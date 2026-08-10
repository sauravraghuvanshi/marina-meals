"""Backend function-tool implementations for Marina Meals.

These are the 5 tools the Voice Live agent calls to ground its answers in
real-time data. Each handler re-reads the underlying JSON file on every call so
edits made during the demo take effect without a restart. Function signatures
are stable so the JSON layer can later be swapped for a DB / POS API without
touching the voice loop.

Grounding rules (see implementation.md §6):
- Match dish / area names case-insensitively against English AND Tamil names.
- If nothing matches, return ``{"found": false}`` — never guess.
- ``place_order`` validates items + availability and computes the total
  server-side. The model is never trusted with arithmetic.
"""

from __future__ import annotations

import json
import time
import unicodedata
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Repo layout: backend/tools.py and data/*.json are siblings under the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_MENU_PATH = _REPO_ROOT / "data" / "menu.json"
_STORES_PATH = _REPO_ROOT / "data" / "stores.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_menu() -> dict[str, Any]:
    with _MENU_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_stores() -> dict[str, Any]:
    with _STORES_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _norm(s: str | None) -> str:
    """Lowercase + NFKC + collapse whitespace. Safe for English and Tamil."""
    if not s:
        return ""
    return " ".join(unicodedata.normalize("NFKC", s).strip().lower().split())


def _iter_items(menu: dict[str, Any]):
    for category in menu.get("categories", []):
        for item in category.get("items", []):
            yield category, item


def _find_item(menu: dict[str, Any], query: str) -> dict[str, Any] | None:
    """Find a menu item by id, English name, or Tamil name (case-insensitive)."""
    q = _norm(query)
    if not q:
        return None
    # 1. Exact id / normalized name / tamil_name match.
    for _, item in _iter_items(menu):
        if q in {
            _norm(item.get("id")),
            _norm(item.get("name")),
            _norm(item.get("tamil_name")),
        }:
            return item
    # 2. Substring fallback on either name.
    for _, item in _iter_items(menu):
        names = (_norm(item.get("name")), _norm(item.get("tamil_name")))
        if any(name and (q in name or name in q) for name in names):
            return item
    return None


def _find_store(stores: dict[str, Any], area: str) -> dict[str, Any] | None:
    q = _norm(area)
    if not q:
        return None
    for store in stores.get("stores", []):
        if q in {
            _norm(store.get("id")),
            _norm(store.get("area")),
            _norm(store.get("tamil_area")),
        }:
            return store
    for store in stores.get("stores", []):
        area_names = (_norm(store.get("area")), _norm(store.get("tamil_area")))
        if any(name and (q in name or name in q) for name in area_names):
            return store
    return None


def _is_open_now(store: dict[str, Any], tz_name: str, now: datetime | None = None) -> bool:
    hours = store.get("hours") or {}
    open_s, close_s = hours.get("open"), hours.get("close")
    if not open_s or not close_s:
        return False
    tz = ZoneInfo(tz_name)
    current = (now.astimezone(tz) if now else datetime.now(tz)).time()
    o = dtime.fromisoformat(open_s)
    c = dtime.fromisoformat(close_s)
    if o <= c:
        return o <= current <= c
    # Overnight window (close past midnight).
    return current >= o or current <= c


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "name": item["name"],
        "tamil_name": item.get("tamil_name"),
        "price": item["price"],
        "veg": item.get("veg", True),
        "available": item.get("available", True),
        "daily_special": item.get("daily_special", False),
        "description": item.get("description"),
        "image": item.get("image"),
    }


def _public_store(store: dict[str, Any], tz_name: str) -> dict[str, Any]:
    return {
        "id": store["id"],
        "name": store["name"],
        "area": store["area"],
        "tamil_area": store.get("tamil_area"),
        "address": store["address"],
        "phone": store["phone"],
        "hours": store["hours"],
        "landmark": store.get("landmark"),
        "lat": store.get("lat"),
        "lng": store.get("lng"),
        "open_now": _is_open_now(store, tz_name),
    }


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def get_menu(category: str | None = None, veg_only: bool | None = None) -> dict[str, Any]:
    """List menu items, optionally filtered by category and/or veg-only."""
    menu = _load_menu()
    cat_q = _norm(category) if category else None
    results: list[dict[str, Any]] = []
    for cat, item in _iter_items(menu):
        if cat_q and cat_q not in {_norm(cat.get("id")), _norm(cat.get("name")), _norm(cat.get("tamil_name"))}:
            continue
        if veg_only and not item.get("veg", True):
            continue
        entry = _public_item(item)
        entry["category"] = cat["id"]
        results.append(entry)
    return {
        "currency": menu.get("currency", "INR"),
        "currency_symbol": menu.get("currency_symbol", "₹"),
        "count": len(results),
        "items": results,
    }


def get_item_price(item_name: str) -> dict[str, Any]:
    """Exact current price + availability for a named dish (EN or Tamil)."""
    menu = _load_menu()
    item = _find_item(menu, item_name)
    if not item:
        return {"found": False, "query": item_name}
    return {
        "found": True,
        "currency": menu.get("currency", "INR"),
        "currency_symbol": menu.get("currency_symbol", "₹"),
        "item": _public_item(item),
    }


def get_todays_specials() -> dict[str, Any]:
    """Today's specials across the menu (real-time)."""
    menu = _load_menu()
    specials = [
        {**_public_item(item), "category": cat["id"]}
        for cat, item in _iter_items(menu)
        if item.get("daily_special") and item.get("available", True)
    ]
    return {
        "currency": menu.get("currency", "INR"),
        "currency_symbol": menu.get("currency_symbol", "₹"),
        "count": len(specials),
        "items": specials,
    }


def find_store(area: str | None = None) -> dict[str, Any]:
    """Find a branch by area; returns address, phone, hours, open_now."""
    stores = _load_stores()
    tz = stores.get("timezone", "Asia/Kolkata")
    if not area:
        return {
            "found": True,
            "count": len(stores.get("stores", [])),
            "stores": [_public_store(s, tz) for s in stores.get("stores", [])],
        }
    store = _find_store(stores, area)
    if not store:
        return {"found": False, "query": area}
    return {"found": True, "store": _public_store(store, tz)}


def place_order(
    items: list[dict[str, Any]],
    store_id: str,
    customer_name: str | None = None,
) -> dict[str, Any]:
    """Validate items + store, compute total server-side, return confirmation."""
    if not items:
        return {"ok": False, "error": "empty_order"}

    menu = _load_menu()
    stores = _load_stores()
    tz = stores.get("timezone", "Asia/Kolkata")

    store = next((s for s in stores.get("stores", []) if s["id"] == store_id), None)
    if not store:
        store = _find_store(stores, store_id)
    if not store:
        return {"ok": False, "error": "store_not_found", "query": store_id}

    line_items: list[dict[str, Any]] = []
    unavailable: list[str] = []
    not_found: list[str] = []
    subtotal = 0

    for entry in items:
        name = entry.get("item_name") or entry.get("name") or ""
        try:
            qty = int(entry.get("qty", 1))
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            not_found.append(name)
            continue
        item = _find_item(menu, name)
        if not item:
            not_found.append(name)
            continue
        if not item.get("available", True):
            unavailable.append(item["name"])
            continue
        price = int(item["price"])
        line_total = price * qty
        subtotal += line_total
        line_items.append({
            "id": item["id"],
            "name": item["name"],
            "tamil_name": item.get("tamil_name"),
            "qty": qty,
            "unit_price": price,
            "line_total": line_total,
        })

    if not line_items:
        return {
            "ok": False,
            "error": "no_valid_items",
            "not_found": not_found,
            "unavailable": unavailable,
        }

    confirmation = f"MM-{int(time.time())}"
    return {
        "ok": True,
        "confirmation_number": confirmation,
        "store": _public_store(store, tz),
        "customer_name": customer_name,
        "items": line_items,
        "subtotal": subtotal,
        "total": subtotal,  # no taxes/fees in the demo
        "currency": menu.get("currency", "INR"),
        "currency_symbol": menu.get("currency_symbol", "₹"),
        "not_found": not_found,
        "unavailable": unavailable,
    }


# Map of tool name -> callable, used by the voice loop to dispatch tool calls.
TOOL_HANDLERS = {
    "get_menu": get_menu,
    "get_item_price": get_item_price,
    "get_todays_specials": get_todays_specials,
    "find_store": find_store,
    "place_order": place_order,
}

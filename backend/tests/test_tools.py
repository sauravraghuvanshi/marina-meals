"""Unit tests for backend.tools — independent of the voice loop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend import tools


@pytest.fixture(autouse=True)
def _isolate_data(tmp_path, monkeypatch):
    """Copy the real data files into a tmp dir so tests can mutate them freely."""
    src_root = Path(__file__).resolve().parents[2]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for name in ("menu.json", "stores.json"):
        (data_dir / name).write_text(
            (src_root / "data" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    monkeypatch.setattr(tools, "_MENU_PATH", data_dir / "menu.json")
    monkeypatch.setattr(tools, "_STORES_PATH", data_dir / "stores.json")
    yield data_dir


def _edit_menu(data_dir: Path, mutate) -> None:
    path = data_dir / "menu.json"
    menu = json.loads(path.read_text(encoding="utf-8"))
    mutate(menu)
    path.write_text(json.dumps(menu), encoding="utf-8")


# ---------------------------------------------------------------------------
# get_menu
# ---------------------------------------------------------------------------

def test_get_menu_returns_all_items():
    result = tools.get_menu()
    assert result["count"] > 20
    assert result["currency"] == "INR"


def test_get_menu_filters_by_category():
    result = tools.get_menu(category="drinks")
    assert result["count"] > 0
    assert all(i["category"] == "drinks" for i in result["items"])


def test_get_menu_veg_only_excludes_non_veg():
    result = tools.get_menu(veg_only=True)
    assert all(i["veg"] for i in result["items"])
    # Sanity: non-veg dish must be excluded.
    assert not any(i["id"] == "chettinad_chicken" for i in result["items"])


def test_get_menu_category_tamil_name():
    result = tools.get_menu(category="பானங்கள்")  # "drinks" in Tamil
    assert result["count"] > 0
    assert all(i["category"] == "drinks" for i in result["items"])


# ---------------------------------------------------------------------------
# get_item_price
# ---------------------------------------------------------------------------

def test_get_item_price_english_name():
    result = tools.get_item_price("Masala Dosa")
    assert result["found"] is True
    assert result["item"]["id"] == "masala_dosa"
    assert result["item"]["price"] == 90


def test_get_item_price_tamil_name():
    result = tools.get_item_price("மசாலா தோசை")
    assert result["found"] is True
    assert result["item"]["id"] == "masala_dosa"


def test_get_item_price_partial_match():
    result = tools.get_item_price("filter coffee")
    assert result["found"] is True
    assert result["item"]["id"] == "filter_coffee"


def test_get_item_price_unknown_returns_not_found():
    result = tools.get_item_price("pizza margherita")
    assert result["found"] is False
    assert result["query"] == "pizza margherita"


def test_get_item_price_reflects_live_edits(_isolate_data):
    _edit_menu(_isolate_data, lambda m: _set_price(m, "masala_dosa", 999))
    result = tools.get_item_price("masala dosa")
    assert result["item"]["price"] == 999


def _set_price(menu, item_id, price):
    for cat in menu["categories"]:
        for item in cat["items"]:
            if item["id"] == item_id:
                item["price"] = price


# ---------------------------------------------------------------------------
# get_todays_specials
# ---------------------------------------------------------------------------

def test_todays_specials_only_specials_and_available():
    result = tools.get_todays_specials()
    assert result["count"] >= 1
    for item in result["items"]:
        assert item["daily_special"] is True
        assert item["available"] is True


# ---------------------------------------------------------------------------
# find_store
# ---------------------------------------------------------------------------

def test_find_store_by_english_area():
    result = tools.find_store(area="Adyar")
    assert result["found"] is True
    assert result["store"]["id"] == "mm-adyar"
    assert "open_now" in result["store"]


def test_find_store_by_tamil_area():
    result = tools.find_store(area="அடையார்")
    assert result["found"] is True
    assert result["store"]["id"] == "mm-adyar"


def test_find_store_unknown_returns_not_found():
    result = tools.find_store(area="Mumbai")
    assert result["found"] is False


def test_find_store_no_area_returns_all():
    result = tools.find_store()
    assert result["found"] is True
    assert result["count"] == 6


# ---------------------------------------------------------------------------
# place_order
# ---------------------------------------------------------------------------

def test_place_order_computes_total_server_side():
    result = tools.place_order(
        items=[
            {"item_name": "Masala Dosa", "qty": 2},
            {"item_name": "Filter Coffee", "qty": 1},
        ],
        store_id="mm-adyar",
        customer_name="Sri",
    )
    assert result["ok"] is True
    # 90*2 + 40 = 220
    assert result["total"] == 220
    assert result["confirmation_number"].startswith("MM-")
    assert result["store"]["id"] == "mm-adyar"
    assert len(result["items"]) == 2


def test_place_order_rejects_unavailable_item():
    result = tools.place_order(
        items=[{"item_name": "Fish Curry Meals", "qty": 1}],
        store_id="mm-adyar",
    )
    assert result["ok"] is False
    assert "Fish Curry Meals" in result["unavailable"]


def test_place_order_rejects_unknown_item():
    result = tools.place_order(
        items=[{"item_name": "pizza", "qty": 1}],
        store_id="mm-adyar",
    )
    assert result["ok"] is False
    assert "pizza" in result["not_found"]


def test_place_order_unknown_store():
    result = tools.place_order(
        items=[{"item_name": "Idli", "qty": 1}],
        store_id="mm-mumbai",
    )
    assert result["ok"] is False
    assert result["error"] == "store_not_found"


def test_place_order_accepts_tamil_item_names():
    result = tools.place_order(
        items=[{"item_name": "மசாலா தோசை", "qty": 1}],
        store_id="mm-adyar",
    )
    assert result["ok"] is True
    assert result["items"][0]["id"] == "masala_dosa"
    assert result["total"] == 90


def test_place_order_empty_items():
    result = tools.place_order(items=[], store_id="mm-adyar")
    assert result["ok"] is False
    assert result["error"] == "empty_order"

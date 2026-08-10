"""FastAPI app entry.

Endpoints:
  GET  /api/health        liveness
  GET  /api/session       validates identity and mints a short-lived proxy ticket
  GET  /api/menu, /price, /specials, /store, POST /order
                          REST mirrors of the tool layer (debug + UI sync)
  POST /api/tool/{name}   server-side dispatch for a tool call from the voice client
  GET  /                  serves the built frontend (single App Service deployment)
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import jwt
import websockets
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from jwt import PyJWKClient, PyJWTError

from .tools import TOOL_HANDLERS, find_store, get_item_price, get_menu, get_todays_specials, place_order
from .voice_config import GREETING_RESPONSE, build_session_update

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

app = FastAPI(title="Marina Meals backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# /api/session — Voice Live credentials minting via managed identity
# ---------------------------------------------------------------------------
# Org policy disables key-based auth on Cognitive Services, so we use Azure AD
# tokens. The backend uses DefaultAzureCredential (App Service managed identity
# in prod, `az login` locally) to authenticate the upstream connection. The
# browser receives only a short-lived, single-use ticket for the local proxy.

_AUDIENCE = "https://cognitiveservices.azure.com/.default"
_credential: DefaultAzureCredential | None = None
_SESSION_TICKET_TTL_SECONDS = 30
_MAX_CLIENT_EVENT_BYTES = 256 * 1024


@dataclass(frozen=True)
class _SessionTicket:
    expires_at: float
    client_ip: str
    principal_id: str | None


_session_tickets: dict[str, _SessionTicket] = {}
_session_ticket_lock = asyncio.Lock()


def _get_credential() -> DefaultAzureCredential:
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return _credential


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=4)
def _jwks_client(tenant_id: str) -> PyJWKClient:
    return PyJWKClient(
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
        cache_keys=True,
    )


def _decode_identity_token(token: str, tenant_id: str, client_id: str) -> dict[str, Any]:
    signing_key = _jwks_client(tenant_id).get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        options={"require": ["exp", "iat", "iss", "aud", "sub"]},
    )


def _client_ip(headers: Any, fallback: str | None) -> str:
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return fallback or "unknown"


def _require_principal(headers: Any) -> str | None:
    if not _env_flag("REQUIRE_APP_AUTH", True):
        return None

    tenant_id = os.getenv("ENTRA_TENANT_ID", "").strip()
    client_id = os.getenv("ENTRA_CLIENT_ID", "").strip()
    if not tenant_id or not client_id:
        raise HTTPException(500, "Application authentication is not configured")

    token = headers.get("x-ms-token-aad-id-token")
    if not token:
        raise HTTPException(401, "Application authentication is required")

    try:
        claims = _decode_identity_token(token, tenant_id, client_id)
    except PyJWTError as exc:
        raise HTTPException(401, "Invalid application identity token") from exc

    principal_id = claims.get("oid") or claims.get("sub")
    if not isinstance(principal_id, str) or not principal_id:
        raise HTTPException(401, "Application identity is missing")

    platform_principal = headers.get("x-ms-client-principal-id")
    if platform_principal and not secrets.compare_digest(platform_principal, principal_id):
        raise HTTPException(401, "Application identity mismatch")
    return principal_id


def _origin_allowed(headers: Any) -> bool:
    origin = headers.get("origin")
    if not origin:
        return False

    configured = {
        entry.strip().rstrip("/")
        for entry in os.getenv("ALLOWED_ORIGINS", "").split(",")
        if entry.strip()
    }
    if origin.rstrip("/") in configured:
        return True

    forwarded_host = headers.get("x-forwarded-host")
    host = (forwarded_host or headers.get("host") or "").split(",", 1)[0].strip()
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc == host


async def _issue_session_ticket(client_ip: str, principal_id: str | None) -> str:
    now = time.monotonic()
    ticket = secrets.token_urlsafe(32)
    async with _session_ticket_lock:
        expired = [key for key, value in _session_tickets.items() if value.expires_at <= now]
        for key in expired:
            del _session_tickets[key]
        _session_tickets[ticket] = _SessionTicket(
            expires_at=now + _SESSION_TICKET_TTL_SECONDS,
            client_ip=client_ip,
            principal_id=principal_id,
        )
    return ticket


async def _consume_session_ticket(
    ticket: str,
    client_ip: str,
    principal_id: str | None,
) -> bool:
    if not ticket:
        return False
    async with _session_ticket_lock:
        session_ticket = _session_tickets.pop(ticket, None)
    if not session_ticket or session_ticket.expires_at <= time.monotonic():
        return False
    return (
        secrets.compare_digest(session_ticket.client_ip, client_ip)
        and session_ticket.principal_id == principal_id
    )


def _client_event_allowed(event: Any, pending_calls: set[str]) -> bool:
    if not isinstance(event, dict):
        return False

    event_type = event.get("type")
    if event_type == "input_audio_buffer.append":
        return set(event) == {"type", "audio"} and isinstance(event.get("audio"), str)

    if event_type == "response.create":
        return set(event) == {"type"}

    if event_type != "conversation.item.create" or set(event) != {"type", "item"}:
        return False

    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "function_call_output":
        return False
    if set(item) != {"type", "call_id", "output"}:
        return False

    call_id = item.get("call_id")
    if not isinstance(call_id, str) or call_id not in pending_calls:
        return False
    if not isinstance(item.get("output"), str):
        return False

    pending_calls.remove(call_id)
    return True


@app.get("/api/session")
async def session(request: Request) -> dict[str, Any]:
    principal_id = _require_principal(request.headers)
    endpoint = os.getenv("AZURE_SPEECH_ENDPOINT", "").rstrip("/")
    model = os.getenv("VOICE_LIVE_MODEL", "")
    api_version = os.getenv("VOICE_LIVE_API_VERSION", "")
    missing = [n for n, v in [
        ("AZURE_SPEECH_ENDPOINT", endpoint),
        ("VOICE_LIVE_MODEL", model),
        ("VOICE_LIVE_API_VERSION", api_version),
    ] if not v]
    if missing:
        raise HTTPException(500, f"Missing env vars: {', '.join(missing)}")

    try:
        _get_credential().get_token(_AUDIENCE)  # validate MI works before handing back config
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Failed to acquire AAD token: {exc}") from exc

    ticket = await _issue_session_ticket(
        _client_ip(request.headers, request.client.host if request.client else None),
        principal_id,
    )
    return {
        "proxy_ws_path": f"/ws/voice?ticket={ticket}",
    }


# ---------------------------------------------------------------------------
# REST mirrors + tool dispatch
# ---------------------------------------------------------------------------
@app.get("/api/menu")
async def api_menu(category: str | None = None, veg_only: bool | None = None):
    return get_menu(category=category, veg_only=veg_only)


@app.get("/api/price")
async def api_price(item_name: str):
    return get_item_price(item_name)


@app.get("/api/specials")
async def api_specials():
    return get_todays_specials()


@app.get("/api/store")
async def api_store(area: str | None = None):
    return find_store(area=area)


@app.post("/api/order")
async def api_order(payload: dict):
    return place_order(
        items=payload.get("items", []),
        store_id=payload.get("store_id", ""),
        customer_name=payload.get("customer_name"),
    )


@app.post("/api/tool/{name}")
async def dispatch_tool(name: str, request: Request) -> Any:
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        raise HTTPException(404, f"Unknown tool: {name}")
    args = await request.json() if (await request.body()) else {}
    try:
        return handler(**args)
    except TypeError as exc:
        raise HTTPException(400, f"Bad arguments for {name}: {exc}") from exc


# ---------------------------------------------------------------------------
# /ws/voice — server-side proxy to Azure Voice Live
# ---------------------------------------------------------------------------
# Browsers cannot set custom headers on a WebSocket constructor, so authenticating
# directly from JS to Voice Live with an AAD bearer token is not possible. We
# bridge: browser ↔ FastAPI WS ↔ Voice Live WS, with the backend attaching the
# Authorization header on the upstream connection. The MI token never reaches
# the client.

async def _voice_live_url() -> str:
    endpoint = os.getenv("AZURE_SPEECH_ENDPOINT", "").rstrip("/")
    model = os.getenv("VOICE_LIVE_MODEL", "")
    api_version = os.getenv("VOICE_LIVE_API_VERSION", "")
    if not (endpoint and model and api_version):
        raise RuntimeError("Voice Live env vars not set")
    ws_base = endpoint.replace("https://", "wss://").replace("http://", "wss://")
    return f"{ws_base}/voice-live/realtime?api-version={api_version}&model={model}"


@app.websocket("/ws/voice")
async def voice_proxy(client_ws: WebSocket) -> None:
    import logging, traceback
    log = logging.getLogger("voice_proxy")

    if not _origin_allowed(client_ws.headers):
        await client_ws.close(code=4403, reason="Origin not allowed")
        return

    try:
        principal_id = _require_principal(client_ws.headers)
    except HTTPException:
        await client_ws.close(code=4401, reason="Authentication required")
        return

    client_ip = _client_ip(
        client_ws.headers,
        client_ws.client.host if client_ws.client else None,
    )
    if not await _consume_session_ticket(
        client_ws.query_params.get("ticket", ""),
        client_ip,
        principal_id,
    ):
        await client_ws.close(code=4401, reason="Invalid or expired session ticket")
        return

    await client_ws.accept()
    log.warning("voice_proxy: client accepted")
    try:
        token = _get_credential().get_token(_AUDIENCE).token
        url = await _voice_live_url()
        log.warning(f"voice_proxy: got token, upstream={url}")
    except Exception as exc:
        log.error(f"voice_proxy: auth/url error: {exc}\n{traceback.format_exc()}")
        try: await client_ws.send_json({"type": "error", "error": {"message": f"auth failed: {exc}"}})
        except Exception: pass
        await client_ws.close(code=1011)
        return

    headers = [("Authorization", f"Bearer {token}")]
    try:
        try:
            upstream = await websockets.connect(url, additional_headers=headers, max_size=20 * 1024 * 1024)
        except TypeError:
            upstream = await websockets.connect(url, extra_headers=headers, max_size=20 * 1024 * 1024)
        log.warning("voice_proxy: upstream connected")
    except Exception as exc:
        log.error(f"voice_proxy: upstream connect failed: {exc}\n{traceback.format_exc()}")
        try: await client_ws.send_json({"type": "error", "error": {"message": f"upstream connect failed: {exc}"}})
        except Exception: pass
        await client_ws.close(code=1011)
        return

    await upstream.send(json.dumps(build_session_update(
        os.getenv("TTS_VOICE", "en-IN-Neerja:DragonHDLatestNeural"),
        os.getenv("STT_LANGUAGES", "en-IN,ta-IN"),
    )))

    pending_calls: set[str] = set()

    async def client_to_upstream() -> None:
        try:
            while True:
                msg = await client_ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    return
                text = msg.get("text")
                if text is None or len(text.encode("utf-8")) > _MAX_CLIENT_EVENT_BYTES:
                    await client_ws.close(code=4400, reason="Unsupported client event")
                    return
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    await client_ws.close(code=4400, reason="Invalid client event")
                    return
                if not _client_event_allowed(event, pending_calls):
                    await client_ws.close(code=4403, reason="Client event not allowed")
                    return
                await upstream.send(text)
        except WebSocketDisconnect:
            return
        except Exception as exc:
            log.error(f"voice_proxy: c2u error: {exc}")
            return

    async def upstream_to_client() -> None:
        greeted = False
        try:
            async for frame in upstream:
                if isinstance(frame, (bytes, bytearray)):
                    await client_ws.send_bytes(frame)
                else:
                    try:
                        event = json.loads(frame)
                    except json.JSONDecodeError:
                        event = {}
                    if event.get("type") == "response.function_call_arguments.done":
                        call_id = event.get("call_id")
                        if isinstance(call_id, str):
                            pending_calls.add(call_id)
                    if event.get("type") == "session.updated" and not greeted:
                        greeted = True
                        await upstream.send(json.dumps(GREETING_RESPONSE))
                    await client_ws.send_text(frame)
        except Exception as exc:
            log.error(f"voice_proxy: u2c error: {exc}")
            return

    proxy_tasks = {
        asyncio.create_task(client_to_upstream()),
        asyncio.create_task(upstream_to_client()),
    }
    try:
        _, pending = await asyncio.wait(proxy_tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        try: await upstream.close()
        except Exception: pass
        try: await client_ws.close()
        except Exception: pass


# ---------------------------------------------------------------------------
# Static frontend (built into ../frontend/dist by `npm run build`)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_FRONTEND_DIST = next(
    (p for p in [_REPO_ROOT / "frontend" / "dist", _REPO_ROOT / "dist"] if p.exists()),
    _REPO_ROOT / "frontend" / "dist",
)


def _resolve_frontend_file(path: str) -> Path | None:
    dist_root = _FRONTEND_DIST.resolve()
    candidate = (dist_root / path).resolve()
    try:
        candidate.relative_to(dist_root)
    except ValueError as exc:
        raise HTTPException(404, "Not found") from exc
    return candidate if candidate.is_file() else None


if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(_FRONTEND_DIST / "index.html")

    @app.get("/{path:path}")
    async def spa_fallback(path: str) -> FileResponse:
        # Serve any other static file if it exists, else fall back to index for SPA routes.
        candidate = _resolve_frontend_file(path)
        if candidate:
            return FileResponse(candidate)
        return FileResponse(_FRONTEND_DIST / "index.html")


# Direct REST debug endpoints — handy while building; the live demo path is
# tool calls coming from the Voice Live session, not these.
@app.get("/api/menu")
async def api_menu(category: str | None = None, veg_only: bool | None = None):
    return get_menu(category=category, veg_only=veg_only)


@app.get("/api/price")
async def api_price(item_name: str):
    return get_item_price(item_name)


@app.get("/api/specials")
async def api_specials():
    return get_todays_specials()


@app.get("/api/store")
async def api_store(area: str | None = None):
    return find_store(area=area)


@app.post("/api/order")
async def api_order(payload: dict):
    return place_order(
        items=payload.get("items", []),
        store_id=payload.get("store_id", ""),
        customer_name=payload.get("customer_name"),
    )

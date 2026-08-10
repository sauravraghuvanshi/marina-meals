"""FastAPI app entry.

Endpoints:
  GET  /api/health        liveness
  GET  /api/session       mints short-lived Voice Live AAD token (managed identity)
  GET  /api/menu, /price, /specials, /store, POST /order
                          REST mirrors of the tool layer (debug + UI sync)
  POST /api/tool/{name}   server-side dispatch for a tool call from the voice client
  GET  /                  serves the built frontend (single App Service deployment)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import websockets
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .tools import TOOL_HANDLERS, find_store, get_item_price, get_menu, get_todays_specials, place_order

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
# in prod, `az login` locally) to acquire a token for the Cognitive Services
# audience, and hands it to the browser. The browser passes it on the Voice
# Live WebSocket via the `bearer.<token>` subprotocol.

_AUDIENCE = "https://cognitiveservices.azure.com/.default"
_credential: DefaultAzureCredential | None = None


def _get_credential() -> DefaultAzureCredential:
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return _credential


@app.get("/api/session")
async def session() -> dict[str, Any]:
    endpoint = os.getenv("AZURE_SPEECH_ENDPOINT", "").rstrip("/")
    model = os.getenv("VOICE_LIVE_MODEL", "")
    api_version = os.getenv("VOICE_LIVE_API_VERSION", "")
    voice = os.getenv("TTS_VOICE", "en-IN-Neerja:DragonHDLatestNeural")
    languages = os.getenv("STT_LANGUAGES", "en-IN,ta-IN")
    region = os.getenv("AZURE_SPEECH_REGION", "eastus2")

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

    return {
        "proxy_ws_path": "/ws/voice",
        "model": model,
        "voice": voice,
        "stt_languages": languages,
        "region": region,
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

    async def client_to_upstream() -> None:
        try:
            while True:
                msg = await client_ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    return
                if msg.get("text") is not None:
                    await upstream.send(msg["text"])
                elif msg.get("bytes") is not None:
                    await upstream.send(msg["bytes"])
        except WebSocketDisconnect:
            return
        except Exception as exc:
            log.error(f"voice_proxy: c2u error: {exc}")
            return

    async def upstream_to_client() -> None:
        try:
            async for frame in upstream:
                if isinstance(frame, (bytes, bytearray)):
                    await client_ws.send_bytes(frame)
                else:
                    await client_ws.send_text(frame)
        except Exception as exc:
            log.error(f"voice_proxy: u2c error: {exc}")
            return

    try:
        await asyncio.gather(client_to_upstream(), upstream_to_client(), return_exceptions=True)
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
if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(_FRONTEND_DIST / "index.html")

    @app.get("/{path:path}")
    async def spa_fallback(path: str) -> FileResponse:
        # Serve any other static file if it exists, else fall back to index for SPA routes.
        candidate = _FRONTEND_DIST / path
        if candidate.is_file():
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

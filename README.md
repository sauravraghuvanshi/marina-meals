# Marina Meals — Chennai Voice Restaurant Demo

A web restaurant app with a **Chennai theme** where customers talk to a **voice agent** (Azure **Voice Live API**) in **English and Tamil**. The agent describes the menu, prices, branches, addresses and phone numbers, and takes orders — all grounded on **real-time backend data** (the model never invents numbers).

> **Building or modifying this?** Read in order:
> 1. [`implementation.md`](./implementation.md) — full build spec (source of truth)
> 2. [`LEARNINGS.md`](./LEARNINGS.md) — bugs we hit, patterns that worked, gotchas to avoid
> 3. [`.github/copilot-instructions.md`](./.github/copilot-instructions.md) — Copilot guardrails

---

## What's built

### Functionality
- 🎙️ **Voice agent** in English and Tamil (and "Tanglish") via Azure Voice Live API + Azure Speech STT (`en-IN,ta-IN`, English-first) + Tamil neural TTS (`ta-IN-PallaviNeural`).
- 🇬🇧 **English-default behavior (strict)** — the agent opens with an English greeting and stays in English unless the customer speaks a *complete* Tamil sentence. A Tamil dish name, the word "vanakkam", an Indian accent, or code-mixed speech inside an English sentence does not flip the language. When in doubt → English.
- 🎯 **Sharp & scoped** — short, professional replies, no over-description of dishes. Out-of-scope questions (general knowledge, coding, weather…) get a one-line refusal + steer back to the menu.
- 🖥️ **Show vs Tell** — *"Show me top 5 dishes"* renders a modal panel silently (one-word ack only). *"Tell me top 5 dishes"* speaks them aloud without opening the panel. The panel auto-closes when the customer asks the next question or any non-show tool fires.
- 🍛 **Grounded answers** — every price/address/availability comes from a backend tool call. The model is forbidden from guessing.
- 🛒 **Place orders by voice** with server-computed totals, read-back, and a confirmation number.
- 🌐 **EN/TA toggle** in the UI (default English; switch to Tamil to swap labels and show Tamil dish names primary).
- 🍽️ **Premium menu UI** — Wikimedia-sourced photos for every dish, today's-special ribbons, veg/non-veg dots, sticky cart, store cards with live open/closed status.

### Architecture
```
┌──────────────────────────┐   /ws/voice (WS proxy)   ┌──────────────────────────┐
│  React + Vite frontend   │ ◄────────────────────────►│  FastAPI backend         │
│  - Mic worklet (24kHz)   │                          │  - DefaultAzureCredential │
│  - Premium UI            │     /api/*  (REST)       │  - Pipes WS to Voice Live │
│  - show_on_screen panel  │                          │  - 5 backend tool handlers│
└────────────┬─────────────┘ ◄────────────────────────►│   (show_on_screen is a   │
             │                                          │    frontend-only tool)   │
             │                                          └────────────┬─────────────┘
             │                                                       │ Authorization: Bearer <AAD>
             ▼                                                       ▼
       (browser audio)                              wss://<foundry-resource>.services.ai.azure.com
                                                       /voice-live/realtime
                                                       (Azure AI Foundry — Voice Live API)
```

**Why a server-side WS proxy?** Browsers can't set the `Authorization` header on a WebSocket. Org policy forces managed identity (no keys). The proxy holds the MI token server-side and pipes frames. See `LEARNINGS.md` for the gory details.

---

## Azure resource requirements

- An Azure AI Foundry project with an AI Services resource that supports Voice Live.
- A latest-GA realtime model deployment.
- An App Service with system-assigned managed identity.
- `Cognitive Services User` and `Foundry User` assigned to the managed identity on the AI Services resource.

---

## Stack

- **Frontend:** React 18 + Vite 5 + TypeScript. Web Audio API `AudioWorklet` for mic capture (PCM16 mono 24 kHz). EN/TA i18n.
- **Backend:** Python 3.12 + FastAPI + `uvicorn`. `websockets==13.1` for the upstream Voice Live connection. `azure-identity` for MI auth. `gunicorn` for production.
- **Data:** `data/menu.json` + `data/stores.json` (read live on every tool call — swap for a DB later without changing the tool contracts).

---

## Repo layout

```
marina-meals/
├── README.md                       # this file
├── implementation.md               # build spec
├── LEARNINGS.md                    # mistakes, bugs, gotchas
├── .github/copilot-instructions.md # Copilot guardrails
├── data/
│   ├── menu.json                   # menu with item images (Wikimedia)
│   └── stores.json                 # branches with hours/phones (demo data)
├── backend/
│   ├── main.py                     # FastAPI app, /api/*, /ws/voice proxy, static mount
│   ├── tools.py                    # 5 tool handlers (server-side totals)
│   ├── requirements.txt
│   ├── .env.example
│   └── tests/test_tools.py         # 20 unit tests
└── frontend/
    ├── public/
    │   ├── favicon.svg             # Marina Meals mark
    │   └── pcm16-worklet.js        # AudioWorklet (must be plain JS, see LEARNINGS)
    └── src/
        ├── audio/{recorder,player,pcm16-worklet}.ts
        ├── voice/voiceLiveClient.ts  # WS client → /ws/voice proxy
        ├── i18n.ts                   # EN/TA strings
        ├── App.tsx                   # main UI (hero, menu grid, cart, store cards)
        └── styles.css                # premium design system
```

---

## Local dev

### Prerequisites
- Node 20+, Python 3.12+, Azure CLI logged in (`az login`).
- Your local principal needs `Cognitive Services User` and `Foundry User` on your AI Services resource.

### Backend
```powershell
cd backend
pip install -r requirements.txt
python -m pytest tests -q                # 20 unit tests
cd ..
uvicorn backend.main:app --reload --port 8000
```

`backend/.env` (copy from `.env.example`):
```
AZURE_SPEECH_ENDPOINT=https://<your-foundry-resource>.services.ai.azure.com
AZURE_SPEECH_REGION=eastus2
VOICE_LIVE_MODEL=<your-ga-model-deployment>
VOICE_LIVE_API_VERSION=<current-ga-api-version>
TTS_VOICE=ta-IN-PallaviNeural
STT_LANGUAGES=en-IN,ta-IN
```
> **Endpoint note:** Voice Live runs on the **new Azure AI Foundry** endpoint (`*.services.ai.azure.com`). Confirm the current GA API version in Microsoft Learn rather than copying a stale value. The managed identity needs both `Cognitive Services User` and `Foundry User` roles on the AI Services resource.
No key needed — `DefaultAzureCredential` picks up your `az login`.

### Frontend
```powershell
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api & /ws → :8000
```

---

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness |
| GET | `/api/session` | mints MI-backed AAD token check, returns `proxy_ws_path` + voice/lang config |
| GET | `/api/menu?category=&veg_only=` | full menu (REST mirror of `get_menu`) |
| GET | `/api/price?item_name=` | exact price for one dish |
| GET | `/api/specials` | today's specials |
| GET | `/api/store?area=` | branch lookup |
| POST | `/api/order` | create order, server-computed total |
| POST | `/api/tool/{name}` | dispatched from the voice loop |
| WS | `/ws/voice` | proxy to Voice Live (server attaches MI bearer token) |
| GET | `/` | serves built frontend |

---

## Demo script (golden path)

1. Open the live URL, allow microphone, click Start.
2. **Greeting:** the agent opens in English with *"Hi, I'm your Marina Meals companion — how can I help you today?"* and then waits. It never opens in Tamil.
3. **EN:** "What's on the menu?" → agent asks one clarifying question (e.g. "Tiffin, meals, drinks, or sweets?"), then reads names only (no dish descriptions).
4. **EN with one Tamil word:** "Masala dosa evlo?" → reply stays in **English**; price comes from `get_item_price`.
5. **Show vs Tell:**
   - *"Show me top 5 dishes"* → modal panel renders 5 items with images + prices; agent says only "Shown on screen." and stops. No reading aloud.
   - *"Tell me top 5 dishes"* → agent speaks the names aloud, no panel.
   - Ask any next question (or pick something) → the panel auto-closes.
6. **TA (full sentence):** "அடையாறுல கடை இருக்கா?" → store lookup with open/closed status, reply in Tamil. Next English question flips the agent back to English.
7. **Order:** "Order 2 masala dosa and a filter coffee for pickup at Adyar." → agent reads back the full order with the server-computed total, asks for confirmation, and on yes returns a confirmation number from `place_order`.
8. **Out-of-scope:** "Who is the PM of India?" / "Write code to add two numbers" → one-line refusal + steer back to the menu (never a partial answer).
9. **Switch language toggle** (top right) — UI flips EN ↔ TA. Default is EN.

To prove real-time grounding live: edit `data/menu.json` (e.g., change masala dosa price to ₹99 or set `available:false`), redeploy, and ask the same question.

---

## Deploy (one command)

From repo root in PowerShell:
```powershell
$resourceGroup = "<your-resource-group>"
$appName = "<your-app-service-name>"
Set-Location <path-to-marina-meals>
cd frontend; npm run build; cd ..
Copy-Item backend\requirements.txt requirements.txt -Force
Get-ChildItem -Recurse backend -Include __pycache__ -Directory | Remove-Item -Recurse -Force
$zip = "$env:TEMP\marina-meals-deploy.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path backend,data,frontend\dist,requirements.txt -DestinationPath $zip
Remove-Item requirements.txt
az webapp deploy -g $resourceGroup -n $appName --src-path $zip --type zip
```

---

## Tweaking without redeploy
```powershell
$resourceGroup = "<your-resource-group>"
$appName = "<your-app-service-name>"

# Change the voice (e.g., Tamil male voice)
az webapp config appsettings set -g $resourceGroup -n $appName `
  --settings TTS_VOICE=ta-IN-ValluvarNeural

# Bump Voice Live api-version
az webapp config appsettings set -g $resourceGroup -n $appName `
  --settings VOICE_LIVE_API_VERSION=<current-ga-api-version>
```
`/api/session` reads these fresh on every page load.

---

## Demo data disclaimer

`data/stores.json` phone numbers and street addresses are illustrative placeholders for the demo, not real businesses.

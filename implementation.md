# Implementation Guide — "Marina Meals" Chennai Voice Restaurant Demo

> **Audience:** GitHub Copilot (and the developer driving it). This file is the single
> source of truth for building the demo end to end. Build strictly to this spec.
>
> **Goal:** A web restaurant app with a **Chennai theme** where a customer talks to a
> **voice agent** (Azure **Voice Live API**) in **English and Tamil**. The agent answers
> questions about the food & drinks menu, prices, store locations, addresses, and phone
> numbers, and takes an order — all grounded on **real-time restaurant data** served by a
> backend (never invented by the model).

---

## 0. Hard requirements (do not deviate)

| # | Requirement | How it is satisfied |
|---|-------------|---------------------|
| 1 | **Azure Voice Live API** for the live conversation | Single managed WebSocket session: STT → GenAI model → TTS, with semantic VAD + barge-in. |
| 2 | **Use the NEW Azure AI Foundry** | Create an **Azure AI Foundry project** (ai.azure.com). All resources (Voice Live, model deployments) live under the Foundry project. Do **not** use the legacy Azure AI Studio or a bare standalone Speech resource flow. |
| 3 | **Latest stable models** | Deploy the **latest GA (generally available)** models from the Foundry model catalog. Do **not** use preview/beta tags. Verify the current GA version string in the catalog at build time (see §3). |
| 4 | **Agent speaks English AND Tamil** | Cascaded language config: Azure Speech STT with `en-IN,ta-IN` (English-first to bias detection in mixed contexts), GenAI text model, Tamil-capable neural TTS voice. The agent **defaults to English** and only switches to Tamil when the customer's current utterance is fully Tamil. See §4 — this is the highest-risk requirement, read it carefully. |
| 5 | **Grounded on real-time restaurant data** | The model is **forbidden** from stating any price / address / availability from memory. It must call backend **function tools** that read live data (`menu.json`/`stores.json` now, swappable for a DB/API later). See §6. |

---

## 1. Architecture

```
┌──────────────────────────┐    WebSocket (PCM16 @ 24kHz)    ┌───────────────────────────┐
│  Frontend (React + Vite) │ ◄──────────────────────────────►│  Azure Voice Live API      │
│  - Chennai-themed UI      │     audio in / audio out         │  (under AI Foundry project)│
│  - mic capture (worklet)  │                                  │  STT → GenAI model → TTS   │
│  - audio playback         │                                  │  semantic VAD + barge-in   │
│  - live menu / cart / map │                                  └────────────┬──────────────┘
└────────────┬──────────────┘                                               │ function-call events
             │ REST (/api/*)                                                 │ (tool calls)
             ▼                                                               ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  Backend (FastAPI, Python)                                                                 │
│  - GET /api/session   → mints ephemeral Voice Live credentials (key stays server-side)     │
│  - Tool handlers invoked from the voice loop:                                              │
│       get_menu · get_item_price · get_todays_specials · find_store · place_order           │
│  - Reads real-time data from data/menu.json + data/stores.json (swap for DB/API later)     │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

**Why this shape:** Voice Live is fully managed, so the backend does **not** orchestrate
STT/LLM/TTS. The backend's only jobs are (a) issuing short-lived session credentials so the
Speech key never reaches the browser, and (b) serving real-time data through tool calls.

---

## 2. Tech stack

- **Frontend:** React + Vite + TypeScript. Web Audio API `AudioWorklet` for mic capture →
  PCM16 mono 24 kHz; a small playback queue for returned audio deltas.
- **Backend:** Python 3.11 + FastAPI + `uvicorn`. `websockets` for the Voice Live connection
  (the voice loop can run client-side **or** be proxied through the backend — see §7 for the
  recommended split).
- **Azure:** Azure AI Foundry project + Azure AI Services (Speech / Voice Live) resource in a
  region near Chennai (**Central India** or **South India**) to minimize round-trip latency.
- **Data:** JSON files now; the tool layer is written so the data source can be swapped for a
  database or live API without touching the voice code.

---

## 3. Azure AI Foundry setup (new Foundry — required)

1. Go to **https://ai.azure.com** and create (or select) an **Azure AI Foundry project**.
2. In the project, attach/create an **Azure AI Services** resource that includes **Speech**
   (this provides Voice Live). Region: **centralindia** or **southindia**.
3. **Deploy the latest STABLE (GA) models** from the project's **Model catalog**:
   - One **realtime/GenAI chat model** for the agent's reasoning (latest GA — confirm the
     current version tag in the catalog; do not pin a preview tag).
   - Voice Live also exposes **Azure Speech STT** and **TTS** — no separate deployment needed,
     they come with the Speech resource.
4. Note these values for `.env` (see §10): project endpoint, Speech resource endpoint,
   API key (server-side only), region, and the deployed model name.
5. **Prototype first in the Voice Live Playground** inside Foundry: paste the system prompt
   (§8), pick a Tamil-capable voice, and confirm an English+Tamil conversation works **before**
   writing client code. This de-risks requirement #4.

> Confirm the exact Voice Live WebSocket URL and `api-version` in the current Microsoft Learn
> Voice Live quickstart at build time. It has the shape:
> `wss://<resource>.cognitiveservices.azure.com/voice-live/realtime?api-version=<GA-version>&model=<deployment>`
> Treat the version string as something to verify, not hard-code blindly.

---

## 4. ⚠️ Language strategy — English + Tamil (highest-risk requirement)

**Key fact (from Microsoft Learn, Voice Live language support):** Tamil (`ta-IN`) is **NOT**
in the default `gpt-realtime` multilingual model's covered language set (that set includes
`en-IN` and `hi-IN`, but not Tamil). If you rely on the default multimodal multilingual mode,
**Tamil recognition quality will be poor.** Do not use the default and hope.

**Required approach — cascaded config:**
- **Input (STT):** configure Azure Speech transcription explicitly with both languages.
  Order matters — first entry is primary. We default to **English first** to bias detection in
  ambiguous/mixed contexts (a Tamil dish name inside an English sentence should not flip the
  agent into Tamil):
  ```jsonc
  "input_audio_transcription": { "model": "azure-speech", "language": "en-IN,ta-IN" }
  ```
  (Alternative if you prefer: `model: "mai-transcribe-1.5"`, which lists Tamil support.)
- **Reasoning:** the latest GA text chat model (handles both languages in text fine).
- **Output (TTS):** a Tamil-capable Azure neural voice, e.g. **`ta-IN-PallaviNeural`** (warm,
  female) or **`ta-IN-ValluvarNeural`** (male). These render both Tamil and English cleanly.

**Language rule** (enforced in the system prompt, §8): the agent **defaults to English** and
replies in English unless the customer's *current* utterance is a full Tamil sentence. A Tamil
dish name or one Tamil word inside an English sentence is still treated as English. Tanglish is
allowed only when the customer's own sentence is genuinely mixed; the moment they switch back to
English, the agent switches back too.

**Testing note for Copilot:** short, mixed-language utterances are where STT quality drops.
Add an automated/manual test list of 8–10 representative Tanglish phrases (e.g.
"Filter coffee evlo?", "Adyar la store irukka?") and validate transcription early.

---

## 5. Repository structure (create exactly this)

```
marina-meals/
├── implementation.md            ← this file
├── README.md
├── .github/
│   └── copilot-instructions.md
├── data/
│   ├── menu.json                ← grounding: menu (provided)
│   └── stores.json              ← grounding: branches (provided)
├── backend/
│   ├── main.py                  ← FastAPI app: /api/session + tool handlers
│   ├── tools.py                 ← function-call implementations (read live data)
│   ├── voice_live.py            ← (optional) server-side proxy for the WS session
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── index.html
    ├── package.json
    ├── vite.config.ts
    └── src/
        ├── App.tsx              ← Chennai-themed UI, menu, cart, store map
        ├── audio/
        │   ├── recorder.ts      ← AudioWorklet mic → PCM16 24kHz
        │   └── player.ts        ← playback queue for audio deltas
        ├── voice/
        │   └── voiceLiveClient.ts ← WebSocket session + event handling
        └── styles.css           ← Chennai theme (see §11 design notes)
```

---

## 6. Data + tool layer (real-time grounding)

Data lives in `data/menu.json` and `data/stores.json` (provided alongside this spec). Each menu
item has `available` and an optional `daily_special` flag so the agent reflects **real-time**
state. The tool handlers in `backend/tools.py` read the files **on each call** (so editing the
JSON during the demo updates answers live). Keep the function signatures stable so the source
can later become a DB or POS API.

**Tool definitions** (declare these in the Voice Live `session.update` `tools` array, and
implement matching handlers in the backend):

```jsonc
[
  {
    "type": "function",
    "name": "get_menu",
    "description": "List menu items, optionally filtered by category or veg-only. Use to answer 'what do you have'.",
    "parameters": {
      "type": "object",
      "properties": {
        "category": { "type": "string", "description": "e.g. tiffin, meals, drinks, sweets" },
        "veg_only": { "type": "boolean" }
      }
    }
  },
  {
    "type": "function",
    "name": "get_item_price",
    "description": "Exact current price (INR) and availability for a named dish. ALWAYS call this before stating any price.",
    "parameters": {
      "type": "object",
      "properties": { "item_name": { "type": "string" } },
      "required": ["item_name"]
    }
  },
  {
    "type": "function",
    "name": "get_todays_specials",
    "description": "Today's specials / offers across the menu (real-time).",
    "parameters": { "type": "object", "properties": {} }
  },
  {
    "type": "function",
    "name": "find_store",
    "description": "Find a branch by area; returns address, phone, hours, and whether it is open now. ALWAYS call this before stating any address or phone number.",
    "parameters": {
      "type": "object",
      "properties": { "area": { "type": "string", "description": "e.g. T. Nagar, Adyar, Velachery, Anna Nagar, Mylapore, OMR" } }
    }
  },
  {
    "type": "function",
    "name": "place_order",
    "description": "Create an order from selected items at a store. Returns a confirmation number. Read the order summary back to the customer before calling.",
    "parameters": {
      "type": "object",
      "properties": {
        "items": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": { "item_name": { "type": "string" }, "qty": { "type": "integer" } },
            "required": ["item_name", "qty"]
          }
        },
        "store_id": { "type": "string" },
        "customer_name": { "type": "string" }
      },
      "required": ["items", "store_id"]
    }
  },
  {
    "type": "function",
    "name": "show_on_screen",
    "description": "Display a curated list of menu items on the customer's screen as a visual panel. Call this AFTER fetching real items via get_menu / get_todays_specials. Use whenever the customer asks to show/list/see items (e.g. 'show me top 5 dishes', 'list the specials'). After calling, ask aloud in ONE short sentence whether to read them out; only read them aloud if the customer says yes.",
    "parameters": {
      "type": "object",
      "properties": {
        "title":    { "type": "string" },
        "item_ids": { "type": "array", "items": { "type": "string" } }
      },
      "required": ["title", "item_ids"]
    }
  }
]
```

**Handler behavior:**
- `get_item_price` / `find_store`: match case-insensitively against English **and** Tamil names
  (the JSON has both). If no match, return `{ "found": false }` so the agent can ask again —
  never let it guess.
- `place_order`: validate items exist + are available, compute total **server-side** (do not let
  the model do arithmetic), generate a confirmation id (e.g. `MM-<timestamp>`), and return the
  summary + total + pickup store.
- `show_on_screen`: **frontend-handled** — does not call the backend. The Voice Live client
  intercepts this tool call, looks up each `item_id` in the cached menu, and renders a modal
  panel on the website. Returns `{ ok: true, shown: N }` to the model. The model must then ask
  the customer aloud whether to read the items out, and only read them if the customer agrees.
- All backend handlers re-read the JSON each call (simulates real-time / live menu).

---

## 7. Voice Live session configuration

Recommended split: run the **WebSocket session in the browser** for lowest latency, but mint
credentials server-side via `GET /api/session` (so the Speech key never ships to the client).
The browser opens the Voice Live WebSocket, then immediately sends `session.update`:

```jsonc
{
  "type": "session.update",
  "session": {
    "instructions": "<SYSTEM PROMPT from §8>",
    "voice": { "name": "ta-IN-PallaviNeural", "type": "azure-standard" },
    "input_audio_format": "pcm16",
    "output_audio_format": "pcm16",
    "input_audio_transcription": { "model": "azure-speech", "language": "en-IN,ta-IN" },
    "turn_detection": {
      "type": "azure_semantic_vad",
      "threshold": 0.5,
      "prefix_padding_ms": 300,
      "silence_duration_ms": 500
    },
    "tools": [ /* the 6 tools from §6 */ ],
    "tool_choice": "auto",
    "temperature": 0.6,
    "max_response_output_tokens": 200
  }
}
```

**Event handling (client `voiceLiveClient.ts`):**

| Incoming event | Action |
|----------------|--------|
| `session.created` / `session.updated` | mark ready, enable mic |
| `input_audio_buffer.speech_started` | stop/duck current playback (barge-in) |
| `response.audio.delta` | enqueue PCM16 chunk to the player |
| `response.audio_transcript.delta` | (optional) show live captions |
| `response.function_call_arguments.done` | parse args → call backend tool → return result (below) |
| `response.done` | finalize turn; update cart/UI if order placed |
| `error` | surface + log; auto-reconnect with backoff |

**Returning a tool result** (after the backend handler responds):
```jsonc
{ "type": "conversation.item.create",
  "item": { "type": "function_call_output", "call_id": "<id>", "output": "<JSON string result>" } }
```
then `{ "type": "response.create" }` to let the agent speak the result.

**Outgoing mic audio:** stream `input_audio_buffer.append` with base64 PCM16; with semantic VAD
on, the service detects turn ends automatically (no manual commit needed in VAD mode).

---

## 8. System prompt (persona + rules)

Use this as the `instructions` value. The agent is **English-default, scope-locked, and
sharp**. Do not soften any of these rules — they exist because the post-demo failure modes were
(a) flipping to Tamil on a single Tamil word, (b) over-explaining dishes, and (c) answering
out-of-scope questions like "who is the PM of India".

```
You are the professional host at "Marina Meals", a Chennai tiffin and meals restaurant. Your
ONLY job: help customers with our menu, prices, branches, and orders. Nothing else.

LANGUAGE (STRICT — this is the most important rule):
- Your DEFAULT and ONLY starting language is English. The very first thing you ever say in a
  conversation MUST be in English. Never open in Tamil.
- Continue replying in English on EVERY turn unless the customer's CURRENT utterance is a
  COMPLETE sentence spoken ENTIRELY in Tamil (Tamil grammar, Tamil verbs, Tamil structure).
- A Tamil dish name (e.g. "idli", "dosa", "pongal", "filter coffee"), a Tamil greeting word
  ("vanakkam"), an Indian accent, or any single Tamil word inside an English sentence is STILL
  ENGLISH. Reply in English.
- Code-mixed sentences (mostly English with some Tamil words, or mostly Tamil with some
  English words) → reply in English.
- Only switch to Tamil when the entire utterance is a full Tamil sentence with Tamil verbs and
  structure. The instant they say even one English sentence after that, switch back to English.
- If you are even slightly unsure what language was spoken, reply in English. When in doubt
  → English.

SCOPE (strict — refuse everything else):
- You ONLY answer questions about Marina Meals: our menu, dish prices, dish availability,
  today's specials, branch info (address, hours, phone), and placing/confirming orders.
- For ANYTHING else — general knowledge (e.g. "who is the PM of India"), coding, math, news,
  weather, opinions, recipes, nutrition advice, comparisons to other restaurants, jokes,
  chit-chat — politely decline in ONE short sentence and steer back: "I can only help with the
  Marina Meals menu, branches, and orders. Would you like to hear today's specials?" Do not
  attempt to answer the out-of-scope question even partially.

SHOW vs TELL (very important — pick the right behavior based on the customer's verb):
- "SHOW me / display / see / can I see / put on screen / list visually" → VISUAL ONLY.
  1) Fetch real item IDs via get_todays_specials and/or get_menu.
  2) Call show_on_screen with a short title and the chosen item_ids. This renders a panel.
  3) Say exactly ONE very short acknowledgement aloud, e.g. "Shown on screen." Nothing more.
     Do NOT read the items. Do NOT ask "should I read them out?". Stop and wait.
- "TELL me / read / say / what are your top N / name them / which are your specials" →
  SPOKEN ONLY.
  1) Fetch real items via the tools.
  2) Speak the names (and prices if asked) aloud, briefly. Do NOT call show_on_screen.
- If the verb is ambiguous (e.g. "give me your top 5"), default to TELL (speak it briefly).
- Never both show AND read aloud in the same turn unless the customer explicitly asks for both.

STYLE (sharp and minimal):
- Be crisp, professional, and brief. This is spoken aloud — short sentences only.
- Do NOT describe, praise, or explain dishes (no "crispy", "soft", "delicious", "made with…",
  no ingredients, no preparation details, no history). Just the name, and price/availability
  when asked.
- Do NOT volunteer information the customer did not ask for. No upselling unless asked for a
  recommendation.
- When the customer asks an open question like "what's on the menu" or "what do you have",
  do NOT dump the list. Ask ONE short clarifying question first (e.g. "Tiffin, meals, drinks,
  or sweets?") and then read only that category, names only.
- One idea per turn. Never more than 1–2 short sentences unless reading back an order.

GROUNDING (critical):
- NEVER state a price, dish availability, address, phone number, or store hours from memory.
- ALWAYS call the appropriate tool first: get_menu / get_item_price / get_todays_specials /
  find_store / place_order. Speak only what the tool returns.
- If a tool returns "found: false", say so briefly and ask the customer to pick from the menu.
  Do not guess.

ORDERING:
- Collect items, quantities, and the pickup branch. Ask only for what is missing — one
  question at a time.
- Call place_order, read back the order and the total exactly as returned, ask for
  confirmation, then give the confirmation number. Nothing extra.
```

---

## 9. Frontend behavior (Chennai theme)

- **Hero:** "Marina Meals" wordmark, tagline in Tamil + English, a Chennai motif (Marina-beach
  / temple-gopuram silhouette). Warm palette (filter-coffee browns, turmeric yellow, banana-leaf
  green). See §11.
- **Talk button:** big mic toggle → starts/stops the Voice Live session. Show a live state:
  *listening* / *speaking* / *thinking*.
- **Live reaction:** when the agent calls a tool, reflect it visually — highlight the dish, show
  its price card, populate the cart on `place_order`, drop a pin / show the branch card on
  `find_store`. When the agent calls `show_on_screen` (a frontend-only tool), render a modal
  panel listing the chosen items by `item_id`. The modal auto-dismisses on: (a) click-outside
  or ×, (b) the customer's next utterance (`input_audio_buffer.speech_started` →
  `onUserSpeechStart` callback), or (c) any non-`show_on_screen` tool call (price lookup, find
  store, place order, etc.). This visual sync is the demo "wow".
- **Proactive greeting:** the Voice Live client sends a `response.create` once on
  `session.updated` so the agent opens with a fixed English single-sentence greeting
  ("Hi, I'm your Marina Meals companion — how can I help you today?") instead of waiting for
  the user to speak first. Guarded by a one-shot flag so it doesn't repeat. This prevents the
  multilingual model from opening in Tamil and sets the language anchor for the rest of the
  session.
- **Captions (optional):** render `response.audio_transcript.delta` so the audience can read
  along — useful for a technical crowd and for Tamil.
- **Reset button:** clear cart + restart session for a clean next run.

---

## 10. Configuration (`backend/.env.example`)

```
AZURE_AI_FOUNDRY_PROJECT_ENDPOINT=
AZURE_SPEECH_ENDPOINT=               # e.g. https://<resource>.cognitiveservices.azure.com
AZURE_SPEECH_KEY=                    # SERVER-SIDE ONLY, never expose to browser
AZURE_SPEECH_REGION=centralindia
VOICE_LIVE_MODEL=                    # latest GA model deployment name
VOICE_LIVE_API_VERSION=              # confirm current GA api-version from docs
TTS_VOICE=ta-IN-PallaviNeural
STT_LANGUAGES=en-IN,ta-IN     # English-first to bias detection in mixed contexts
```

- `GET /api/session` returns a short-lived token/credentials + the WS URL + chosen model/voice
  so the frontend can connect without ever seeing `AZURE_SPEECH_KEY`.
- CORS: allow the Vite dev origin in dev.

---

## 11. Chennai theme — design tokens

```
--coffee:      #4E342E   /* filter coffee brown — primary text/headers */
--turmeric:    #F4A300   /* accent / call-to-action */
--banana-leaf: #2E7D32   /* veg / success */
--chilli:      #C62828   /* non-veg / alerts */
--cream:       #FFF8E1   /* background */
Fonts: a rounded display face for the wordmark; a clean sans for body.
Tamil text must use a font with proper Tamil glyph support (e.g. "Noto Sans Tamil").
```

---

## 12. Build phases (order for Copilot to follow)

1. **Scaffold** the repo structure (§5). Add `data/menu.json` + `data/stores.json` (provided).
2. **Backend tools** (`tools.py`): implement the 5 handlers against the JSON; unit-test them
   independently of voice (English + Tamil name matching, total computation, order ids).
3. **`/api/session`**: mint credentials; return WS URL + model + voice. Verify the key is never
   sent to the client.
4. **Frontend audio**: `recorder.ts` (mic → PCM16 24kHz) and `player.ts` (delta playback queue).
5. **Voice client**: `voiceLiveClient.ts` — open WS, send `session.update` (§7), handle events,
   wire tool calls to the backend, return `function_call_output`.
6. **UI sync**: menu/cart/store cards react to tool calls; live state indicator; captions.
7. **Theme** (§11) + reset button.
8. **Test**: the Tanglish phrase list (§4); barge-in; an end-to-end order; tool-grounded prices.
9. **Rehearse**: scripted golden path (Appendix A) + 2–3 off-script questions.

---

## 13. Acceptance criteria (definition of done)

- [ ] Conversation runs over **Azure Voice Live** via an **AI Foundry** project (req #1, #2).
- [ ] Models deployed are the **latest GA** (no preview tags) (req #3).
- [ ] Agent **defaults to English** and only flips to Tamil on a full Tamil utterance (req #4).
      A Tamil dish name inside an English sentence does NOT trigger a Tamil reply.
- [ ] Every price/address/availability the agent states comes from a **tool call**, verifiable
      in backend logs — nothing invented (req #5).
- [ ] Editing `menu.json` (e.g. mark an item unavailable) changes the agent's answer without a
      restart (real-time).
- [ ] Barge-in works: interrupting the agent mid-sentence stops playback and it adapts.
- [ ] `place_order` returns a server-computed total + confirmation number, reflected in the UI.
- [ ] **Scope is enforced:** out-of-scope questions (e.g. "who is the PM of India?", "write me
      code") get a one-line refusal + steer back to the menu — never a partial answer.
- [ ] **No oversharing:** the agent does not describe, praise, or explain dishes unsolicited.
      Open questions like "what's on the menu" trigger ONE clarifying question, not a dump.
- [ ] **First turn is in English:** the agent opens proactively with the English greeting
      ("Hi, I'm your Marina Meals companion — how can I help you today?") on `session.updated`;
      it never opens in Tamil.
- [ ] **Show vs Tell verb split works:** "Show me top 5 dishes" calls `show_on_screen` and the
      agent says only one short ack ("Shown on screen.") without reading the list aloud.
      "Tell me top 5 dishes" speaks the list aloud and does NOT call `show_on_screen`.
- [ ] **Modal auto-closes** on the customer's next utterance and on any non-`show_on_screen`
      tool call (price lookup, find_store, place_order, etc.) — no manual dismiss required.

---

## 14. Troubleshooting

- **Poor Tamil recognition** → you are likely on the default multilingual mode; switch to the
  explicit `azure-speech` STT with `en-IN,ta-IN` (or `mai-transcribe-1.5`). See §4.
- **Agent invents prices** → tighten the system prompt grounding rules and confirm tools are
  declared in `session.update`; check the model is actually emitting
  `response.function_call_arguments.done`.
- **Agent keeps flipping to Tamil on English questions** → ensure STT language order is
  `en-IN,ta-IN` (English first) **and** the SYSTEM_PROMPT LANGUAGE section starts with
  "DEFAULT to English". Both are required — STT order alone is not enough.
- **Agent overshares (describes dishes, lists everything)** → confirm the SYSTEM_PROMPT STYLE
  section is present and that `temperature` is set low (we use `0.6`) and
  `max_response_output_tokens` is capped (we use `200`).
- **Agent answers out-of-scope questions** → confirm the SYSTEM_PROMPT SCOPE section is
  present verbatim; the explicit "PM of India" example matters as an anchor.
- **Choppy / robotic audio** → ensure PCM16 24 kHz both directions; smooth the playback queue;
  prefer a wired headset on stage.
- **High latency** → use a Central/South India region; run the WS client-side; keep tool
  handlers fast (in-memory JSON).
- **WS auth errors** → key is region-scoped; confirm region matches the resource; confirm the
  `api-version` against the current quickstart.

---

## Appendix A — Demo golden-path script

1. *(English)* "What's good for breakfast?" → agent asks "Tiffin, meals, drinks, or sweets?",
   then calls `get_menu(category="tiffin")` and reads names only — no descriptions.
2. *(English with one Tamil word)* "Masala dosa evlo?" → agent stays in **English** (one Tamil
   word is not a Tamil sentence), calls `get_item_price("masala dosa")`, answers the price.
3. *(barge-in)* Interrupt mid-sentence with a full Tamil sentence "Adyar la store irukka?" →
   playback stops → `find_store(area="Adyar")` → address + phone + open-now, in Tamil. Next
   English question flips the agent back to English immediately.
4. *(English — SHOW)* "Show me your top 5 dishes" → agent calls `get_todays_specials` /
   `get_menu` then `show_on_screen` → modal panel appears with 5 dishes + images + prices →
   agent says only "Shown on screen." and stops (no spoken list).
4b. *(English — TELL)* "Tell me your top 5 dishes" → agent fetches via tools and speaks the
    names briefly aloud, does NOT open the modal.
4c. *(modal auto-close)* While the panel is open, ask any next question ("add masala dosa to
    my order") → the panel disappears as soon as you start speaking; the agent answers the
    new question.
5. *(out-of-scope)* "Who is the PM of India?" → one-line refusal + steer back to the menu.
6. *(English)* "Order 2 masala dosa and 1 filter coffee for pickup at Adyar." → `place_order(...)`
   → agent reads back summary + total, asks to confirm.
7. "Yes, confirm." → confirmation number shown on screen + spoken.

## Appendix B — References

- Voice Live API overview — Microsoft Learn:
  https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live
- Voice Live language support (Tamil nuance) — Microsoft Learn:
  https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-language-support
- Voice Live function calling — Microsoft Learn (search: "how-to-voice-live-function-calling").
- Voice Live Playground — in Azure AI Foundry (ai.azure.com).

> Always confirm exact endpoint URLs, `api-version` strings, and current GA model names against
> the live Microsoft Learn quickstart at build time — these evolve.

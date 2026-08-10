# Learnings — Marina Meals Build

Written by the Copilot agent that built this app, for the Copilot agent that maintains it
next. Captures what worked, what broke, and which mistakes to not repeat.

## TL;DR — checklist before touching this project

- [ ] Confirm AAD/MI auth path is intact — **never** try to use `--keys` for Cognitive Services in this subscription (`disableLocalAuth=true`).
- [ ] Deploy AI Services resources in **`eastus2`** (gpt-realtime GA is not in India regions).
- [ ] Deploy App Service plan wherever F1/B1 quota is available (we landed in `westus2` because eastus had quota=0).
- [ ] **Enable WebSockets on App Service** explicitly (`az webapp config set --web-sockets-enabled true`).
- [ ] When packaging from PowerShell, place `requirements.txt` and the frontend `dist/` so they land at zip root.
- [ ] For Voice Live in the browser: use the server-side `/ws/voice` proxy — never try to authenticate the WebSocket from the browser.
- [ ] **Do not soften the SYSTEM_PROMPT** in `backend/voice_config.py`. The LANGUAGE / SCOPE / STYLE / SHOW vs TELL sections are imperative and example-anchored on purpose — see Bug #10 and #12.
- [ ] Keep `temperature: 0.6` and `max_response_output_tokens: 200` in `session.update`. Removing them brings back the overshare behavior.
- [ ] Keep STT language order as `en-IN,ta-IN` (English first) in both the backend `STT_LANGUAGES` appsetting and the frontend fallback. Required to stop the agent flipping to Tamil on a single Tamil dish name.
- [ ] Keep the proactive English greeting sent by the backend proxy once on `session.updated`. It is what makes the agent open in English instead of waiting for the user.
- [ ] Keep panel auto-close hooks in `App.tsx`: `onUserSpeechStart` and the non-`show_on_screen` clear inside `handleTool`. Without them the modal lingers after the user moves on.

---

## Architectural lessons

### Voice Live with managed identity from a browser → MUST proxy
Browsers cannot set custom headers (including `Authorization`) on the `WebSocket` constructor.
We tried the `bearer.<token>` subprotocol trick first — it does **not** work for Voice Live.
The only viable pattern is a server-side WebSocket proxy that the backend authenticates upstream with the MI token and pipes frames bidirectionally to the browser. Implementation: `backend/main.py` → `voice_proxy()`.

### Voice Live URL & versioning
- URL (current, **new Foundry**): `wss://<resource>.services.ai.azure.com/voice-live/realtime?api-version=2026-04-10&model=<deployment>`
- URL (legacy, still functional): `wss://<resource>.cognitiveservices.azure.com/voice-live/realtime?api-version=<X>&model=<deployment>`
- `model` is the **deployment name** on the AI Services / Foundry account, not the model family.
- AAD token audience: `https://cognitiveservices.azure.com/.default` (works for both legacy and the new Foundry host — we did not need to switch to the `ai.azure.com` scope).
- **RBAC on the new Foundry host:** the MI principal needs **both** `Cognitive Services User` *and* `Foundry User` (role GUID `53ca6127-db72-4b80-b1b0-d745d6d5456d`, formerly "Azure AI User") on the AI Services resource. With only `Cognitive Services User`, the upstream WS handshake fails with 401.
- We currently use `api-version=2026-04-10` on the new Foundry endpoint. Track Microsoft Learn for the active GA api-version.

### Tamil is not in Voice Live's default multilingual set
We had to wire Tamil explicitly:
- `input_audio_transcription`: `{ model: "azure-speech", language: "en-IN,ta-IN" }` — English first, intentionally, to bias detection toward English in mixed/ambiguous utterances.
- `voice`: `{ name: "ta-IN-PallaviNeural", type: "azure-standard" }`
- System prompt enforces English-default; only a full Tamil sentence flips the agent. See Bug #10 for why "mirror the customer's language" is too soft.

Default OpenAI realtime voices (`alloy`, `echo`, …) do **not** speak Tamil — they will produce noise or English approximations if used.

### Grounding rules belong in the system prompt
The model will guess prices if you let it. The system prompt explicitly forbids stating any price/address/availability from memory and lists the exact tool to call for each fact. This is what makes the demo never invent numbers.

---

## Bugs we hit (don't repeat)

### 1. `websockets` library kwarg mismatch — `additional_headers` vs `extra_headers`
- **Symptom:** WS proxy returned 1011 immediately; logs showed `TypeError: create_connection() got an unexpected keyword argument 'additional_headers'`.
- **Cause:** Legacy `websockets.connect` API uses `extra_headers`; the newer asyncio client uses `additional_headers`. Installed version varies.
- **Fix:** Try one, fall back to the other inside a `try/except TypeError`.

### 2. Vite `?url` import on a `.ts` AudioWorklet
- **Symptom:** Production error `Failed to construct 'AudioWorkletNode': node name 'pcm16-worklet' is not defined`. Browser console showed addModule resolving without registering the processor.
- **Cause:** Vite's `import workletUrl from "./pcm16-worklet.ts?url"` emits the raw TS source as a static asset. Browsers cannot parse TS — but `addModule` resolved silently in some cases, leaving the processor unregistered.
- **Fix:** Move worklet processors to `frontend/public/pcm16-worklet.js` (plain JS), reference by absolute path `/pcm16-worklet.js?v=${Date.now()}`. The cache-buster matters because the SPA fallback may have cached an HTML response at that URL pre-fix.

### 3. SPA fallback poisoning the static-asset cache
- **Symptom:** Browser caches `index.html` at e.g. `/pcm16-worklet.js` from an earlier 404→SPA fallback, then refuses to refetch even after the real file is deployed.
- **Fix:** Always cache-bust new static-asset URLs. Long term: have the SPA fallback return 404 for paths matching `*.js|*.css|*.ico|...` so they never get cached as HTML.

### 4. App Service WebSockets disabled by default
- **Symptom:** WS endpoint returns 502/silent close. No useful logs.
- **Fix:** `az webapp config set -g <resource-group> -n <app-name> --web-sockets-enabled true`. Required for every new web app.

### 5. App Service Plan quota=0 in our preferred region
- **Symptom:** Plan creation failed in `eastus` with quota error even on F1/Free.
- **Fix:** Plan goes in `westus2`, AI Services account stays in `eastus2`. Cross-region adds latency but works.

### 6. `Compress-Archive` strips the parent path on Windows
- **Symptom:** Zip lays out `dist/index.html` instead of `frontend/dist/index.html`. FastAPI static mount can't find files.
- **Fix:** In code, check both `frontend/dist` and `dist` candidates. Also: copy `backend/requirements.txt` to repo root before zipping so App Service Python build detects it.

### 7. Unsplash random photo IDs were unreliable
- **Symptom:** Many dish cards showed unrelated photos or blank tiles (404).
- **Fix:** Switched to `https://commons.wikimedia.org/wiki/Special:FilePath/<Filename>?width=640`. Filenames discovered via the MediaWiki search API. Frontend has an `onError` swap to a banana-leaf fallback.

### 8. Wrong AI Foundry account scope in the portal
- **Symptom:** User couldn't see `gpt-realtime` deployment in https://ai.azure.com.
- **Cause:** Foundry portal defaulted to a different AI Services account in the same resource group.
- **Fix:** Use the resource picker top-right in ai.azure.com to switch to the intended AI Services resource, or deep-link via `/resource/deployments?wsid=<resource-id>`.

### 9. We added images to `menu.json` even though spec said "use as-is"
- The original spec said `menu.json` was provided as-is. User later asked for images on every item, so we extended each item with an `image` field and a `tools._public_item()` helper that exposes it. Acceptable since the model-facing tool output is unchanged in shape (just enriched) and the spec's grounding rules still hold.

### 10. Post-demo prompt hardening — the "friendly host" prompt overshares and language-flips
**Symptoms reported live in a manager demo:**
- Agent flipped into Tamil whenever the customer used one Tamil word ("masala dosa") inside an English sentence.
- Agent over-explained dishes: "Dosa is a crispy fermented crepe made with…" when only the price was asked.
- Agent answered out-of-scope questions ("who is the PM of India", "write code to add two numbers") instead of refusing.

**Root cause:** the original prompt said *"reply in the same language the customer used"* and *"warm, welcoming, proud of Chennai food. Suggest today's specials when relevant."* — both are too soft. A realtime model interprets soft style guidance generously and a multilingual model interprets "same language" too literally on a single token.

**Fix (see `backend/voice_config.py` SYSTEM_PROMPT):**
- LANGUAGE section now opens with **"DEFAULT to English"** and explicitly says a single Tamil word does not count as Tamil. Only a full Tamil sentence flips the agent.
- SCOPE section explicitly enumerates the off-topic categories and gives the exact refusal sentence verbatim. The "who is the PM of India" anchor matters — it tested badly without that exact example.
- STYLE section forbids descriptions, ingredients, praise, and dumping the full menu. Requires ONE clarifying question for open prompts.
- Also added `temperature: 0.6` and `max_response_output_tokens: 200` in `session.update` to keep replies short.
- Flipped STT default order from `ta-IN,en-IN` → `en-IN,ta-IN` (frontend fallback **and** backend `STT_LANGUAGES` appsetting). STT order alone is not enough — both layers are needed.

**Lesson:** for a voice realtime model, the system prompt is the primary control surface, but it must be *imperative and example-anchored*, not aspirational. Vague style words ("friendly", "warm") will be honored at the cost of brevity and scope.

### 11. Frontend-only tools — pattern for "render on screen, then ask aloud"
We added a 6th tool `show_on_screen({ title, item_ids })` that does **not** hit the backend. The Voice Live client intercepts it in `dispatchToolCall`, fires an `onShowOnScreen` callback so `App.tsx` renders a modal panel, and returns `{ ok: true, shown: N }` to the model so it can continue the turn.

**Why this matters:**
- Voice agents talking long lists aloud are painful. The right UX is *show then ask*.
- Grounding is preserved by construction: the frontend looks up each `item_id` in the cached menu, so the model can only display real dishes.
- Pattern is reusable for any "display X to the user" intent (e.g. a map pin, an image gallery) — define the tool, intercept by name in the dispatcher, fire a callback, return a tiny ack to the model.

**Gotcha:** the model still needs an explicit prompt section telling it *when* to call `show_on_screen` and *what to say afterwards*. Without that, it will call the tool and then immediately read the whole list anyway, defeating the purpose. See Bug #12 for how we evolved this from "ask before reading" to a hard show/tell verb split.

### 12. Round-2 polish — silent Tamil opener, "show vs tell" confusion, lingering modal

**Symptoms reported in second demo pass:**
- The agent often *opened* in Tamil even when the customer hadn't spoken yet (or spoke English first). The "default to English" wording wasn't strong enough on the *first* turn because there was no first turn — Voice Live waits silently until the user speaks, and the multilingual model's bias surfaces on the very first reply.
- "Show me top 5 dishes" called `show_on_screen` AND immediately read the list aloud, plus tacked on "should I read them out?". The original prompt asked for both, but customers found the follow-up question redundant and the spoken list defeated the visual.
- After the modal opened, asking the next question (e.g. "add masala dosa to my order") left the modal sitting on top of the UI until the user manually dismissed it.

**Fixes:**
1. **Proactive English greeting** — the backend proxy sends one `response.create` after the authoritative `session.updated` event with a fixed English opener. A one-shot flag prevents repetition and anchors the first turn before the customer speaks.
2. **LANGUAGE prompt is now stricter** — opens with "Your DEFAULT and ONLY starting language is English. The very first thing you ever say MUST be in English. Never open in Tamil." Adds explicit anti-examples (Tamil dish names, "vanakkam", accent, code-mixed → still English) and a "when in doubt → English" tiebreaker.
3. **SHOW vs TELL verb split** in both the prompt and the `show_on_screen` tool description:
   - `show / display / see / put on screen` → call `show_on_screen`, say only "Shown on screen." and stop. Do NOT read the items, do NOT ask "should I read them?".
   - `tell / read / say / what are / name them` → speak names aloud, do NOT call `show_on_screen`.
   - Ambiguous verbs ("give me your top 5") default to TELL.
4. **Panel auto-close** — wired two new triggers in `App.tsx`:
   - New `onUserSpeechStart` callback on `VoiceLiveClient` fires on `input_audio_buffer.speech_started` → `setShownPanel(null)`. Dismisses the modal the moment the customer opens their mouth again.
   - Inside `handleTool`, any tool call whose name is **not** `show_on_screen` clears the panel (covers `place_order`, `find_store`, `get_item_price`, etc. — i.e. "the conversation moved on").

**Lesson:** for realtime voice UX, you can't rely on the model to "ask first" — break behaviors apart by verb in the prompt *and* by tool name on the client. And if you want the agent to open the conversation, **send the opening `response.create` yourself** rather than waiting for the user to speak first.

### 13. Migration to the **new Azure AI Foundry** Voice Live endpoint (2026-06)

**Context:** Microsoft moved Voice Live onto the unified AI Foundry surface (`*.services.ai.azure.com`). The legacy `*.cognitiveservices.azure.com` URL still works, but new GA features and api-versions (`2026-04-10` and later) land on the Foundry host first.

**What changed for this project:**
- `AZURE_SPEECH_ENDPOINT` changed from the resource's legacy `*.cognitiveservices.azure.com` host to its `*.services.ai.azure.com` host.
- `VOICE_LIVE_API_VERSION` bumped to `2026-04-10`.
- The App Service managed identity needs **both** `Cognitive Services User` and `Foundry User` on the AI Services resource. Without `Foundry User`, the upstream WS upgrade returns 401 even though the token mints fine.
- **No code change required** — `backend/main.py` reads `AZURE_SPEECH_ENDPOINT` at request time, so the migration is config-only (appsettings + role assignment + restart).

**Gotchas hit during the migration:**
- `az role assignment create` fails with `MissingSubscription` on the `Foundry User` role even with `--subscription`, `--assignee-object-id`, and an explicit role GUID. Worked around with a direct `az rest --method PUT` against ARM (`/providers/Microsoft.Authorization/roleAssignments/<new-guid>?api-version=2022-04-01`) using `roleDefinitionId` path `/subscriptions/<sub>/providers/Microsoft.Authorization/roleDefinitions/53ca6127-db72-4b80-b1b0-d745d6d5456d`.
- Token audience stays as `https://cognitiveservices.azure.com/.default` — do **not** switch to `https://ai.azure.com/.default`, the legacy audience is what the Foundry host accepts today.
- After config + role change, **restart the App Service** (or wait for the cred cache to expire) so the next WS proxy connection picks up the new endpoint.

**Smoke test that proves the migration worked end-to-end (server-side):**
Connect through the application flow, obtain a short-lived ticket from `/api/session`, and open `/ws/voice` with the same authenticated browser session. Expect `session.created` followed by `session.updated`; client-supplied `session.update` events are intentionally rejected.

---

- **Server-side totals on `place_order`.** Never trust the model to compute money — recompute on the backend and return the canonical total. The model parrots whatever we send back.
- **Read JSON fresh on every tool call.** No in-memory caching. Lets you edit `menu.json` mid-demo to prove the grounding is real-time.
- **Unit and security tests.** The backend suite covers matching, totals, authentication, ticket replay, origin checks, event filtering, and path containment. Run `python -m pytest backend/tests -q` before every deploy.
- **Detailed logging on the WS proxy.** When the proxy broke, the only thing that saved us was the per-step log lines (`got token`, `upstream connected`, etc.). Keep them.
- **One env var, one place.** `AZURE_SPEECH_ENDPOINT`, `VOICE_LIVE_MODEL`, `VOICE_LIVE_API_VERSION`, `TTS_VOICE`, `STT_LANGUAGES` are all read at request time, not import time, so flipping voice/language doesn't require a redeploy.

---

## Demo prep notes

- The model emits `response.function_call_arguments.done` events — verified working end-to-end with `e2e_voice_test.py` (in the session files folder). Keep that script as the canary; if browser breaks, run it first to isolate whether Voice Live itself is healthy.
- Allow ~30s for App Service warmup if cold. F1 plan goes cold quickly.
- Mic permission must be granted in the browser. Chrome blocks `getUserMedia` on `http://` — only test against the HTTPS production URL or `localhost`.
- Tamil reply quality is good for tiffin/meal vocabulary. Mixing brand names (`Marina Meals`) may produce mild mispronunciation — that's a TTS limitation, not a config bug.

---

## Future work / parking lot

- Switch hosting plan to **B1** for sustained demos (F1 cold-starts and has WS connection limits).
- Pin `websockets` version explicitly in `requirements.txt` to avoid the kwarg-mismatch class of bugs.
- Make SPA fallback exclude static asset extensions, eliminating the cache-poisoning vector.
- Wire CI: lint + tests + Bicep deploy.
- Real branch data + Maps integration if going beyond demo.

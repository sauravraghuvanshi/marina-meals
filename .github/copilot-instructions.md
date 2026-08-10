# GitHub Copilot — Repository Instructions

You are building **Marina Meals**, a Chennai-themed voice restaurant demo. The authoritative
spec is **`implementation.md`** at the repo root — follow it exactly. These instructions are
the always-on guardrails; when in doubt, defer to `implementation.md`.

## Non-negotiable requirements
1. **Azure Voice Live API** is the conversation engine. Do not substitute a different
   STT/LLM/TTS pipeline or roll your own orchestration — Voice Live is fully managed.
2. **Use the new Azure AI Foundry.** All Azure resources (Voice Live, model deployments) live
   under an **AI Foundry project** (ai.azure.com). Do not use the legacy Azure AI Studio flow
   or a standalone bare Speech setup.
3. **Latest stable (GA) models only.** Never deploy or reference preview/beta model tags.
   Read the current GA version from the Foundry model catalog; do not hard-code a guessed tag.
4. **English-default, Tamil on full Tamil sentences only.** The agent must converse in both
   but **defaults to English** — only a *complete Tamil utterance* triggers a Tamil reply. A
   Tamil dish name inside an English sentence is still English. Tamil is NOT covered by the
   default Voice Live multilingual model — use explicit Azure Speech STT with order
   `en-IN,ta-IN` (English first) and a Tamil TTS voice (`ta-IN-PallaviNeural`). See
   `implementation.md` §4 and §8.
5. **Real-time grounding.** The model must NEVER state a price, address, phone number, hours, or
   availability from memory. It must call a backend tool and speak only what the tool returns.
   Totals are computed server-side, never by the model.
6. **Scope-locked (RAG-only).** The agent answers ONLY menu / price / branch / order questions.
   Out-of-scope prompts (general knowledge, coding, weather, recipes, jokes, "who is the PM of
   India") get a one-line refusal + steer back to the menu — never a partial answer. See the
   SCOPE section of the SYSTEM_PROMPT in `backend/voice_config.py`.
7. **Sharp, no oversharing.** No dish descriptions, ingredients, praise, or unsolicited
   recommendations. Open questions ("what's on the menu") trigger ONE clarifying question
   first. Keep `temperature: 0.6` and `max_response_output_tokens: 200` in `session.update`.
8. **Show-on-screen for visual requests.** "Show / display / see / put on screen" calls
   `show_on_screen`, says one short acknowledgement, and stops. "Tell / read / name" speaks the
   list and does not open the panel. Never read a visual-only result aloud unsolicited.

## Architecture rules
- **Secrets stay server-side.** No keys in this project (org policy: `disableLocalAuth=true`).
  The App Service uses a system-assigned managed identity to mint AAD tokens; the browser opens
  the Voice Live WebSocket through the server-side `/ws/voice` proxy. Never put a token or
  bearer in the browser; never set Authorization on a browser WebSocket — it does not work.
- **The server owns Voice Live policy.** `backend/voice_config.py` defines `session.update`, tools,
  and the greeting. The proxy accepts only allowlisted browser events and function outputs for
  real pending call IDs. Never move session policy back into attacker-controlled frontend code.
- **Production requires Microsoft Entra sign-in.** Keep App Service Authentication enabled and
  `REQUIRE_APP_AUTH=true`. The `/api/health` exclusion is liveness-only. Do not weaken
  authentication to make a demo path easier.
- **Keep identities separate.** Runtime Voice Live access, GitHub deployment, and App Service
  Authentication client assertion use different identities. Never attach the GitHub deployment
  identity to the running app.
- **One worker until shared tickets exist.** Session tickets are process-local and single-use.
  Keep Gunicorn at one worker until the ticket store moves to shared infrastructure.
- **Tools read data live.** `backend/tools.py` re-reads `data/menu.json` / `data/stores.json` on
  each call so demo edits take effect without a restart. Keep tool signatures stable so the data
  source can later become a DB/API.
- **Match English and Tamil names** in tool lookups (the JSON has both `name` and `tamil_name`).
  On no match, return `{ "found": false }` — do not guess.
- **`show_on_screen` is frontend-handled.** Do not add a backend handler for it. The Voice
  Live client intercepts it in `dispatchToolCall`, fires `onShowOnScreen`, and returns
  `{ ok: true, shown: N }` to the model. Keep that interception; do not "fix" it by routing
  through `/api/tool/`.

## Code conventions
- Backend: Python 3.11+, FastAPI, type hints, `async` for I/O. Keep handlers small and pure.
- Frontend: React + TypeScript (strict), functional components + hooks. Audio is PCM16 mono
  24 kHz in both directions.
- Voice client events: handle `session.*`, `input_audio_buffer.speech_started` (barge-in),
  `response.audio.delta`, `response.function_call_arguments.done`, `response.done`, `error`.
  Return tool results via `conversation.item.create` (`function_call_output`) then `response.create`.
- No secrets, no fabricated phone numbers/addresses beyond the provided demo data.
- Keep comments minimal; only explain non-obvious why.

## CI/CD rules
- `.github/workflows/ci-cd.yml` must continue to run CI on every push and pull request and deploy
  only successful pushes to `main`.
- Keep workflow permissions least-privilege: `contents: read` by default and `id-token: write`
  only in the production deployment job.
- Keep actions pinned to immutable commit SHAs. Let Dependabot propose updates.
- Do not add publish profiles, Azure credentials, client secrets, subscription IDs, tenant IDs,
  resource IDs, or concrete Azure endpoints to workflow source.
- Production deploys through the GitHub `production` environment and its exact immutable OIDC
  subject. Do not replace it with a guessed legacy subject format.
- A deployment is complete only after backend tests, production npm audit, frontend build, Azure
  deployment, and `/api/health` verification pass.

## Chennai theme
Warm palette (coffee brown, turmeric, banana-leaf green, cream). Use a font with Tamil glyph
support (e.g. "Noto Sans Tamil") wherever Tamil text appears. See `implementation.md` §11.

## Definition of done
Every item in `implementation.md` §13 (Acceptance criteria) must pass — especially: both
languages work, all stated facts are tool-grounded, editing the JSON changes answers live, and
barge-in works. CI/CD changes must also pass the deployed workflow end to end.

## When unsure about Azure specifics
Endpoint URLs, `api-version` strings, and GA model names evolve. Confirm them against the live
Microsoft Learn Voice Live quickstart rather than guessing. References are in `implementation.md`
Appendix B.

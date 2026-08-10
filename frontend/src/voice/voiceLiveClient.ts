// Voice Live WebSocket client. Opens the session, sends `session.update` with
// the system prompt + tool definitions, streams mic audio in, plays audio out,
// and dispatches function calls back to the backend.

import { Player } from "../audio/player";
import { Recorder } from "../audio/recorder";

export type VoiceState = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "error";

export interface SessionInfo {
  proxy_ws_path: string;
  model: string;
  voice: string;
  stt_languages: string;
}

export interface ToolEvent {
  name: string;
  args: Record<string, unknown>;
  result: unknown;
}

export interface ShownPanel {
  title: string;
  item_ids: string[];
}

export interface VoiceClientCallbacks {
  onState?: (s: VoiceState) => void;
  onTranscriptDelta?: (text: string, role: "user" | "agent") => void;
  onToolCall?: (e: ToolEvent) => void;
  onShowOnScreen?: (panel: ShownPanel) => void;
  onUserSpeechStart?: () => void;
  onError?: (err: string) => void;
}

const SYSTEM_PROMPT = `You are the professional host at "Marina Meals", a Chennai tiffin and meals restaurant. Your ONLY job: help customers with our menu, prices, branches, and orders. Nothing else.

LANGUAGE (STRICT — this is the most important rule):
- Your DEFAULT and ONLY starting language is English. The very first thing you ever say in a conversation MUST be in English. Never open in Tamil.
- Decide the reply language by the GRAMMAR / SENTENCE STRUCTURE of the customer's current utterance, NOT by counting individual words:
  • If the verbs, grammar, and sentence structure are TAMIL → reply in Tamil. This is true even if the sentence contains English loan words like "order", "table", "menu", "bill", "parcel", or English dish names ("dosa", "idli"). Real Chennai Tamil speakers naturally mix English nouns into Tamil sentences — that is still Tamil. Example: "enakku two idli order pannanum" or "bill ready aacha?" → these are Tamil; reply in Tamil.
  • If the verbs, grammar, and sentence structure are ENGLISH → reply in English. A Tamil dish name ("idli", "pongal", "filter coffee") or greeting word ("vanakkam") inside an otherwise English sentence is STILL English. Example: "I'd like two idlis" → English.
- Quick test: ignore food / English loan nouns and look at the verb and sentence skeleton. Tamil verb → Tamil. English verb → English.
- The very first turn of the conversation is always English (rule above). After that, follow the grammar test on each new utterance. Switch freely between English and Tamil based on the customer's current sentence.
- Never translate your own reply into Tamil unless this rule allows Tamil. Never offer a Tamil version proactively.

NUMBERS (read aloud in the SAME language as the rest of your reply):
- When you are replying in English, speak ALL numbers in English: order/confirmation numbers, prices in rupees, phone numbers, quantities, hours. Example: order number 4072 → "four zero seven two", price ₹85 → "eighty-five rupees". Do NOT speak digits in Tamil while the rest of the sentence is English.
- When you are replying in Tamil, speak the numbers in Tamil.
- Never mix: do not read digits in Tamil inside an English sentence, or in English inside a Tamil sentence.

SCOPE (strict — refuse everything else):
- You ONLY answer questions about Marina Meals: our menu, dish prices, dish availability, today's specials, branch info (address, hours, phone), and placing/confirming orders.
- For ANYTHING else — general knowledge (e.g. "who is the PM of India"), coding, math, news, weather, opinions, recipes, nutrition advice, comparisons to other restaurants, jokes, chit-chat — politely decline in ONE short sentence and steer back: "I can only help with the Marina Meals menu, branches, and orders. Would you like to hear today's specials?" Do not attempt to answer the out-of-scope question even partially.

SHOW vs TELL (very important — pick the right behavior based on the customer's verb):
- "SHOW me / display / see / can I see / put on screen / list visually" → VISUAL ONLY.
  1) Fetch real item IDs via get_todays_specials and/or get_menu.
  2) Call show_on_screen with a short title and the chosen item_ids. This renders a panel.
  3) Say exactly ONE very short acknowledgement aloud, e.g. "Shown on screen." Nothing more. Do NOT read the items. Do NOT ask "should I read them out?". Do NOT describe them. Stop and wait.
- "TELL me / read / say / what are your top N / name them / which are your specials" → SPOKEN ONLY.
  1) Fetch real items via the tools.
  2) Speak the names (and prices if asked) aloud, briefly. Do NOT call show_on_screen.
- If the verb is ambiguous (e.g. "give me your top 5"), default to TELL (speak it briefly).
- Never both show AND read aloud in the same turn unless the customer explicitly asks for both ("show me and read them out").

STYLE (sharp and minimal):
- Be crisp, professional, and brief. This is spoken aloud — short sentences only.
- Do NOT describe, praise, or explain dishes (no "crispy", "soft", "delicious", "made with…", no ingredients, no preparation details, no history). Just the name, and price/availability when asked.
- Do NOT volunteer information the customer did not ask for. No upselling unless asked for a recommendation.
- When the customer asks an open question like "what's on the menu" or "what do you have", do NOT dump the list. Ask ONE short clarifying question first (e.g. "Tiffin, meals, drinks, or sweets?") and then read only that category, names only.
- One idea per turn. Never more than 1–2 short sentences unless reading back an order.

GROUNDING (critical):
- NEVER state a price, dish availability, address, phone number, or store hours from memory.
- ALWAYS call the appropriate tool first: get_menu / get_item_price / get_todays_specials / find_store / place_order. Speak only what the tool returns.
- If a tool returns "found: false", say so briefly and ask the customer to pick from the menu. Do not guess.

ORDERING:
- Collect items, quantities, and the pickup branch. Ask only for what is missing — one question at a time.
- Call place_order, read back the order and the total exactly as returned, ask for confirmation, then give the confirmation number. Nothing extra.`;

const TOOLS = [
  {
    type: "function",
    name: "get_menu",
    description: "List menu items, optionally filtered by category or veg-only. Use to answer 'what do you have'.",
    parameters: {
      type: "object",
      properties: {
        category: { type: "string", description: "e.g. tiffin, meals, drinks, sweets" },
        veg_only: { type: "boolean" },
      },
    },
  },
  {
    type: "function",
    name: "get_item_price",
    description: "Exact current price (INR) and availability for a named dish. ALWAYS call this before stating any price.",
    parameters: {
      type: "object",
      properties: { item_name: { type: "string" } },
      required: ["item_name"],
    },
  },
  {
    type: "function",
    name: "get_todays_specials",
    description: "Today's specials / offers across the menu (real-time).",
    parameters: { type: "object", properties: {} },
  },
  {
    type: "function",
    name: "find_store",
    description: "Find a branch by area; returns address, phone, hours, and whether it is open now. ALWAYS call this before stating any address or phone number.",
    parameters: {
      type: "object",
      properties: { area: { type: "string", description: "e.g. T. Nagar, Adyar, Velachery, Anna Nagar, Mylapore, OMR" } },
    },
  },
  {
    type: "function",
    name: "place_order",
    description: "Create an order from selected items at a store. Returns a confirmation number. Read the order summary back to the customer before calling.",
    parameters: {
      type: "object",
      properties: {
        items: {
          type: "array",
          items: {
            type: "object",
            properties: { item_name: { type: "string" }, qty: { type: "integer" } },
            required: ["item_name", "qty"],
          },
        },
        store_id: { type: "string" },
        customer_name: { type: "string" },
      },
      required: ["items", "store_id"],
    },
  },
  {
    type: "function",
    name: "show_on_screen",
    description: "Display a curated list of menu items on the customer's screen as a visual panel. ONLY call this when the customer's verb is 'show / display / see / put on screen' (visual request). Do NOT call it for 'tell / read / say / what are' — those are spoken requests. After calling, say only one tiny acknowledgement like 'Shown on screen.' and stop; do NOT read the items aloud and do NOT ask whether to read them.",
    parameters: {
      type: "object",
      properties: {
        title: { type: "string", description: "Short panel title, e.g. 'Top 5 dishes' or 'Today's specials'" },
        item_ids: {
          type: "array",
          description: "Item IDs from get_menu / get_todays_specials (e.g. ['t01','t02']). Use real IDs only — do not invent.",
          items: { type: "string" },
        },
      },
      required: ["title", "item_ids"],
    },
  },
];

export class VoiceLiveClient {
  private ws: WebSocket | null = null;
  private recorder = new Recorder();
  private player = new Player();

  constructor(private cb: VoiceClientCallbacks = {}) {}

  private setState(s: VoiceState) {
    this.cb.onState?.(s);
  }

  async start(): Promise<void> {
    if (this.ws) return;
    this.setState("connecting");
    try {
      const res = await fetch("/api/session");
      if (!res.ok) throw new Error(`/api/session ${res.status}`);
      const info: SessionInfo = await res.json();

      // Connect to our server-side proxy (which authenticates upstream with MI
      // and pipes frames to Voice Live). No token ever reaches the browser.
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const wsUrl = `${proto}://${location.host}${info.proxy_ws_path}`;
      this.ws = new WebSocket(wsUrl);
      this.ws.binaryType = "arraybuffer";

      this.ws.onopen = () => this.sendSessionUpdate(info);
      this.ws.onmessage = (ev) => this.handleEvent(ev.data);
      this.ws.onerror = (e) => {
        this.cb.onError?.("WebSocket error");
        console.error(e);
        this.setState("error");
      };
      this.ws.onclose = () => this.setState("idle");
    } catch (e: any) {
      this.cb.onError?.(e?.message ?? String(e));
      this.setState("error");
      throw e;
    }
  }

  async stop(): Promise<void> {
    try { this.ws?.close(); } catch { /* noop */ }
    this.ws = null;
    await this.recorder.stop();
    await this.player.close();
    this.setState("idle");
  }

  private sendSessionUpdate(info: SessionInfo) {
    const languages = info.stt_languages || "en-IN,ta-IN";
    const update = {
      type: "session.update",
      session: {
        instructions: SYSTEM_PROMPT,
        voice: { name: info.voice, type: "azure-standard" },
        input_audio_format: "pcm16",
        output_audio_format: "pcm16",
        input_audio_transcription: { model: "azure-speech", language: languages },
        turn_detection: {
          type: "azure_semantic_vad",
          threshold: 0.5,
          prefix_padding_ms: 300,
          silence_duration_ms: 500,
        },
        tools: TOOLS,
        tool_choice: "auto",
        temperature: 0.6,
        max_response_output_tokens: 200,
      },
    };
    this.ws?.send(JSON.stringify(update));
  }

  private greeted = false;
  private sendGreeting() {
    if (this.greeted) return;
    this.greeted = true;
    const create = {
      type: "response.create",
      response: {
        modalities: ["audio", "text"],
        instructions:
          "Greet the customer in ENGLISH ONLY with one short, warm sentence: \"Hi, I'm your Marina Meals companion — how can I help you today?\" Do not say anything else. Do not use Tamil. Do not list the menu. Then stop and wait for them to speak.",
      },
    };
    this.ws?.send(JSON.stringify(create));
  }

  private async startMicWhenReady() {
    if (this.recorder["ctx"]) return; // already running
    try {
      await this.recorder.start((b64) => {
        this.ws?.send(JSON.stringify({ type: "input_audio_buffer.append", audio: b64 }));
      });
      this.setState("listening");
    } catch (e: any) {
      const msg = e?.message ?? String(e);
      console.error("mic start failed", e);
      this.cb.onError?.(`Microphone error: ${msg}`);
      this.setState("error");
    }
  }

  private async handleEvent(data: string | ArrayBuffer) {
    if (typeof data !== "string") return; // Voice Live uses JSON text frames
    let msg: any;
    try { msg = JSON.parse(data); } catch { return; }

    switch (msg.type) {
      case "session.created":
        break;

      case "session.updated":
        await this.startMicWhenReady();
        this.sendGreeting();
        break;

      case "input_audio_buffer.speech_started":
        this.player.stop(); // barge-in
        this.cb.onUserSpeechStart?.();
        this.setState("listening");
        break;

      case "response.created":
        this.setState("thinking");
        break;

      case "response.audio.delta":
        if (msg.delta) {
          this.player.enqueueBase64(msg.delta);
          this.setState("speaking");
        }
        break;

      case "response.audio_transcript.delta":
        if (msg.delta) this.cb.onTranscriptDelta?.(msg.delta, "agent");
        break;

      case "conversation.item.input_audio_transcription.delta":
        if (msg.delta) this.cb.onTranscriptDelta?.(msg.delta, "user");
        break;

      case "response.function_call_arguments.done":
        await this.dispatchToolCall(msg);
        break;

      case "response.done":
        this.setState("listening");
        break;

      case "error":
        this.cb.onError?.(msg.error?.message ?? "Voice Live error");
        console.error(msg);
        break;
    }
  }

  private async dispatchToolCall(msg: any) {
    const name: string = msg.name;
    const callId: string = msg.call_id;
    let args: Record<string, unknown> = {};
    try { args = JSON.parse(msg.arguments ?? "{}"); } catch { /* keep empty */ }

    let result: unknown;
    if (name === "show_on_screen") {
      const title = String((args as any).title ?? "Menu");
      const itemIds = Array.isArray((args as any).item_ids) ? (args as any).item_ids.map(String) : [];
      this.cb.onShowOnScreen?.({ title, item_ids: itemIds });
      result = { ok: true, shown: itemIds.length };
    } else {
      try {
        const r = await fetch(`/api/tool/${encodeURIComponent(name)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(args),
        });
        result = await r.json();
      } catch (e: any) {
        result = { ok: false, error: String(e?.message ?? e) };
      }
    }

    this.cb.onToolCall?.({ name, args, result });

    this.ws?.send(JSON.stringify({
      type: "conversation.item.create",
      item: {
        type: "function_call_output",
        call_id: callId,
        output: JSON.stringify(result),
      },
    }));
    this.ws?.send(JSON.stringify({ type: "response.create" }));
  }
}

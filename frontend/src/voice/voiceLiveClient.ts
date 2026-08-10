// Voice Live WebSocket client. Opens the authenticated server-owned session,
// streams mic audio in, plays audio out, and dispatches function calls.

import { Player } from "../audio/player";
import { Recorder } from "../audio/recorder";

export type VoiceState = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "error";

export interface SessionInfo {
  proxy_ws_path: string;
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

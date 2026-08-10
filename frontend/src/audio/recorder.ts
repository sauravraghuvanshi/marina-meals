// Mic capture: AudioContext + AudioWorklet pipeline that emits base64-encoded
// PCM16 mono 24 kHz frames suitable for Voice Live `input_audio_buffer.append`.

// Worklet is served as a static asset from /public so the browser loads plain JS.
const workletUrl = `/pcm16-worklet.js?v=${Date.now()}`;

export type RecorderOnChunk = (base64Pcm16: string) => void;

export class Recorder {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;

  async start(onChunk: RecorderOnChunk): Promise<void> {
    if (this.ctx) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    this.ctx = new AudioContext({ sampleRate: 48000 });
    try {
      const probe = await fetch(workletUrl);
      const txt = await probe.text();
      console.log("[recorder] worklet fetch status=%d len=%d head=%s", probe.status, txt.length, txt.slice(0, 60));
    } catch (e) { console.warn("[recorder] worklet probe failed", e); }
    await this.ctx.audioWorklet.addModule(workletUrl);
    console.log("[recorder] addModule resolved");
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "pcm16-worklet");
    this.node.port.onmessage = (e) => {
      const buf = e.data as ArrayBuffer;
      onChunk(arrayBufferToBase64(buf));
    };
    this.source.connect(this.node);
    // Worklet doesn't need to reach destination; connecting through a 0-gain
    // node would silence playback if we did connect — so we leave it.
  }

  async stop(): Promise<void> {
    this.node?.disconnect();
    this.source?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    await this.ctx?.close();
    this.node = null;
    this.source = null;
    this.stream = null;
    this.ctx = null;
  }
}

function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

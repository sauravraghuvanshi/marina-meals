// Streaming PCM16 24 kHz mono player. Voice Live sends base64-encoded audio
// deltas; we queue them and schedule each chunk back-to-back so playback is
// gapless. Barge-in (`speech_started`) calls `stop()` to flush instantly.

export class Player {
  private ctx: AudioContext | null = null;
  private nextStart = 0;
  private sources: AudioBufferSourceNode[] = [];
  private gain: GainNode | null = null;

  ensure(): AudioContext {
    if (!this.ctx) {
      this.ctx = new AudioContext({ sampleRate: 24000 });
      this.gain = this.ctx.createGain();
      this.gain.connect(this.ctx.destination);
      this.nextStart = this.ctx.currentTime;
    }
    return this.ctx;
  }

  enqueueBase64(b64: string): void {
    const ctx = this.ensure();
    const bytes = base64ToBytes(b64);
    const samples = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2);
    const float = new Float32Array(samples.length);
    for (let i = 0; i < samples.length; i++) float[i] = samples[i] / 0x8000;

    const buf = ctx.createBuffer(1, float.length, 24000);
    buf.getChannelData(0).set(float);

    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.gain!);
    const start = Math.max(this.nextStart, ctx.currentTime);
    src.start(start);
    this.nextStart = start + buf.duration;
    this.sources.push(src);
    src.onended = () => {
      this.sources = this.sources.filter((s) => s !== src);
    };
  }

  stop(): void {
    // Barge-in: kill pending audio so the user's turn isn't talked over.
    for (const s of this.sources) {
      try { s.stop(); } catch { /* already stopped */ }
    }
    this.sources = [];
    if (this.ctx) this.nextStart = this.ctx.currentTime;
  }

  async close(): Promise<void> {
    this.stop();
    await this.ctx?.close();
    this.ctx = null;
    this.gain = null;
  }
}

function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

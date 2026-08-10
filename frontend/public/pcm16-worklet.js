// AudioWorklet that downsamples mic Float32 frames to PCM16 mono 24 kHz and
// posts ArrayBuffers back to the main thread.
class PCM16Worklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 24000;
    this.inputRate = sampleRate;
    this.ratio = this.inputRate / this.targetRate;
    this.carry = new Float32Array(0);
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel || channel.length === 0) return true;

    const merged = new Float32Array(this.carry.length + channel.length);
    merged.set(this.carry, 0);
    merged.set(channel, this.carry.length);

    const outLen = Math.floor(merged.length / this.ratio);
    const out = new Int16Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const idx = i * this.ratio;
      const i0 = Math.floor(idx);
      const i1 = Math.min(i0 + 1, merged.length - 1);
      const frac = idx - i0;
      const sample = merged[i0] * (1 - frac) + merged[i1] * frac;
      const clamped = Math.max(-1, Math.min(1, sample));
      out[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    }

    const consumed = Math.floor(outLen * this.ratio);
    this.carry = merged.slice(consumed);
    this.port.postMessage(out.buffer, [out.buffer]);
    return true;
  }
}

registerProcessor("pcm16-worklet", PCM16Worklet);

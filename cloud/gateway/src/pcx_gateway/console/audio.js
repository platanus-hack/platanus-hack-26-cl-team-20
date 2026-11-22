// Espectro del micrófono del computador para los widgets con script (wui.onAudio). El audio no sale de esta página:
// un AnalyserNode calcula el espectro y solo eso (128 bandas y el nivel) se les pasa a los widgets. Nunca audio crudo.
export const AUDIO = { bands: 128, fft: 1024, everyMs: 33, smoothing: 0.6 };

export class Spectrum {
  /** onFrame({freq: Uint8Array(128), level: 0..1, t, hz_per_band}) ~30 veces por segundo mientras esté prendido. */
  constructor(onFrame, onState = () => {}) {
    Object.assign(this, { onFrame, onState, running: false, error: null });
  }

  async start() {
    if (this.running || this.starting) return;
    this.starting = true;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
      this.ctx = new AudioContext();
      await this.ctx.resume().catch(() => {});
      const an = (this.analyser = this.ctx.createAnalyser());
      an.fftSize = AUDIO.fft;
      an.smoothingTimeConstant = AUDIO.smoothing;
      this.ctx.createMediaStreamSource(this.stream).connect(an); // no se conecta a la salida: no suena por los parlantes
      const bins = new Uint8Array(an.frequencyBinCount);
      const wave = new Uint8Array(an.fftSize);
      const per = bins.length / AUDIO.bands;
      this.timer = setInterval(() => {
        an.getByteFrequencyData(bins);
        an.getByteTimeDomainData(wave);
        const freq = new Uint8Array(AUDIO.bands);
        for (let b = 0; b < AUDIO.bands; b++) {
          let sum = 0;
          for (let k = Math.floor(b * per); k < Math.floor((b + 1) * per); k++) sum += bins[k];
          freq[b] = sum / per;
        }
        let sq = 0;
        for (const v of wave) sq += ((v - 128) / 128) ** 2;
        const level = Math.min(1, Math.sqrt(sq / wave.length) * 3); // RMS con algo de ganancia: hablar normal ronda 0,3
        this.onFrame({ freq, level: Math.round(level * 1000) / 1000, t: Date.now(), hz_per_band: Math.round(this.ctx.sampleRate / 2 / AUDIO.bands) });
      }, AUDIO.everyMs);
      this.running = true;
      this.error = null;
    } catch (err) {
      this.error = err.name === "NotAllowedError" ? "falta el permiso del micrófono" : err.message;
    }
    this.starting = false;
    this.onState();
  }

  stop() {
    if (!this.running) return;
    clearInterval(this.timer);
    this.stream.getTracks().forEach((t) => t.stop());
    this.ctx.close().catch(() => {});
    this.running = false;
    this.onState();
  }
}

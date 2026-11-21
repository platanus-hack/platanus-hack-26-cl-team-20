// Botón de voz de la mesa: escucha mientras la mano está encima, sin llaves ni servicios propios.
// Usa el reconocimiento de voz del navegador (SpeechRecognition, es-CL, con resultados parciales) y el micrófono del
// computador de la consola. Si el navegador no lo trae, graba el audio y lo deja pendiente de transcribir.

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

export class Voice {
  /** onText(textoParcial) mientras escucha. `forceAudio` salta el reconocimiento (para probar la grabación). */
  constructor(onText, { lang = "es-CL", forceAudio = false } = {}) {
    Object.assign(this, { onText, lang, transcribes: !!SR && !forceAudio, listening: false });
  }

  async start() {
    if (this.listening) return;
    Object.assign(this, { listening: true, final: "", interim: "", error: null, chunks: [], t0: performance.now() });
    if (this.transcribes) return this.listen();
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.recorder = new MediaRecorder(this.stream);
      this.recorder.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
      this.recorder.start();
      this.onText("Grabando (este navegador no transcribe)");
    } catch (err) {
      this.error = err.name === "NotAllowedError" ? "Falta el permiso del micrófono en la consola" : `Micrófono: ${err.message}`;
      this.onText(this.error);
    }
  }

  listen() {
    const rec = (this.rec = new SR());
    Object.assign(rec, { lang: this.lang, interimResults: true, continuous: true });
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) this.final += `${e.results[i][0].transcript} `;
        else interim += e.results[i][0].transcript;
      }
      this.interim = interim;
      this.onText(`${this.final}${interim}`.trim());
    };
    rec.onerror = (e) => {
      if (e.error === "no-speech" || e.error === "aborted") return;
      this.error = e.error === "not-allowed" || e.error === "service-not-allowed" ? "Falta el permiso del micrófono en la consola" : `Reconocimiento de voz: ${e.error}`;
      this.onText(this.error);
    };
    // Chrome corta solo tras un silencio: mientras la mano siga encima, se vuelve a escuchar sin perder lo dicho.
    rec.onend = () => (this.listening && !this.error && this.rec === rec ? this.listen() : this.ended?.());
    try {
      rec.start();
    } catch (err) {
      this.error = `Reconocimiento de voz: ${err.message}`;
    }
  }

  /** Deja de escuchar. Devuelve {text, seconds, audio?, error?}; `audio` (Blob) solo si no hubo transcripción. */
  async stop() {
    if (!this.listening) return null;
    this.listening = false;
    const seconds = Math.round((performance.now() - this.t0) / 100) / 10;
    if (this.rec) {
      const rec = this.rec;
      await new Promise((done) => {
        this.ended = done;
        setTimeout(done, 1500); // los últimos resultados llegan justo después de stop()
        try {
          rec.stop();
        } catch {
          done();
        }
      });
      this.rec = this.ended = null;
      return { text: `${this.final}${this.interim}`.trim(), seconds, error: this.error };
    }
    if (!this.recorder) return { text: "", seconds, error: this.error };
    const recorder = this.recorder;
    await new Promise((done) => ((recorder.onstop = done), recorder.stop()));
    this.stream.getTracks().forEach((t) => t.stop());
    this.recorder = this.stream = null;
    return { text: "", seconds, audio: new Blob(this.chunks, { type: recorder.mimeType }) };
  }
}

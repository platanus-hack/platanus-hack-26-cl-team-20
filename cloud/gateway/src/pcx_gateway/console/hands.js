// Manos en el navegador: MediaPipe Hands (tasks-vision, WASM) sobre el video de la cámara cenital.
// Entrega 21 puntos normalizados (0..1) por mano en cada cuadro nuevo; el resto (mm, toques, dibujo) va en app.js.

const CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";
const MODEL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";

export class HandTracker {
  /** getVideo() → <video> de la cámara cenital; onHands([{lm, handed}]); onStatus(texto) */
  constructor(getVideo, onHands, onStatus) {
    Object.assign(this, { getVideo, onHands, onStatus, running: false, model: null, last: -1, fps: 0 });
  }

  async load() {
    const vision = await import(`${CDN}/vision_bundle.mjs`);
    const files = await vision.FilesetResolver.forVisionTasks(`${CDN}/wasm`);
    const make = (delegate) =>
      vision.HandLandmarker.createFromOptions(files, {
        baseOptions: { modelAssetPath: MODEL, delegate },
        runningMode: "VIDEO",
        numHands: 2,
        minHandDetectionConfidence: 0.5,
        minTrackingConfidence: 0.5,
      });
    this.model = await make("GPU").catch(() => make("CPU"));
  }

  async start() {
    if (this.running) return;
    this.running = true;
    try {
      if (!this.model) {
        this.onStatus("Cargando el modelo de manos…");
        await this.load();
      }
    } catch (err) {
      this.running = false;
      this.onStatus(`No se pudo cargar el modelo de manos: ${err.message}`);
      return;
    }
    let frames = 0;
    let since = performance.now();
    const tick = () => {
      if (!this.running) return;
      const video = this.getVideo();
      if (video && video.readyState >= 2 && video.currentTime !== this.last) {
        this.last = video.currentTime;
        try {
          const out = this.model.detectForVideo(video, performance.now());
          this.onHands(out.landmarks.map((lm, i) => ({ lm, handed: out.handedness?.[i]?.[0]?.categoryName ?? "unknown" })));
        } catch (err) {
          console.warn("manos", err);
        }
        frames++;
      }
      const now = performance.now();
      if (now - since > 1000) {
        this.fps = Math.round((frames * 1000) / (now - since));
        frames = 0;
        since = now;
        this.onStatus(video ? null : "Manos: no hay cámara cenital.");
      }
      setTimeout(tick, 12);
    };
    tick();
  }

  stop() {
    this.running = false;
    this.onHands([]);
  }
}

"""Capa de personas en vivo: lee cuadros de una fuente, corre DAViD (normal + recorte [+ profundidad]) y las manos
(MediaPipe), y deja el último resultado listo para los efectos.

Corre en un **proceso aparte** (por defecto): el loop del runtime no compite por el GIL con el modelo (en un hilo el
runtime caía de ~25 a ~17 fps) y si el modelo se cae no tumba el runtime. `mode="thread"` queda para pruebas.
Vive fuera de playground/effects a propósito: el hot reload de un efecto no reinicia el modelo (cargarlo en el
Neural Engine toma 10-60 s). Se apaga sola si nadie pide resultados por `idle_s` segundos.

Fuentes (`fuente` en los efectos):
    video:captures/david_test/bharatanatyam_960.mp4    archivo en bucle a su fps (pruebas sin cámara)
    url:http://192.168.1.233:8794/frame.jpg?w=960      JPEG por HTTP (la Brio del PC: hands_service / camserve)
    cam:0                                              cámara local (cv2.VideoCapture)

    layer = get_layer("video:...", size=384)          # compartida por (fuente, tamaño, profundidad)
    b = layer.latest()                                 # dict o None: frame, normal, alpha, depth, hands, seq, size
"""

from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import time
import traceback
import urllib.request

import cv2
import numpy as np

_LAYERS: dict[tuple, "PeopleLayer"] = {}
_LOCK = threading.Lock()
RETRY_S = 30.0


class _Source:
    def __init__(self, spec: str, work_w: int):
        self.spec, self.work_w = spec, work_w
        kind, _, arg = spec.partition(":")
        self.kind, self.arg = kind, arg
        self.cap = None
        self.fps = 25.0
        self._t_next = 0.0
        if kind == "video":
            self.cap = cv2.VideoCapture(arg)
            if not self.cap.isOpened():
                raise FileNotFoundError(f"no abre el video {arg}")
            self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 25.0
        elif kind == "cam":
            self.cap = cv2.VideoCapture(int(arg))
            if not self.cap.isOpened():
                raise RuntimeError(f"no abre la cámara {arg}")
        elif kind != "url":
            raise ValueError(f"fuente desconocida: {spec} (video:… | url:… | cam:N)")

    def read(self) -> np.ndarray | None:
        f = None
        if self.kind == "video":
            now = time.time()                       # a ritmo de video: salta cuadros si el modelo va más lento
            if self._t_next == 0:
                self._t_next = now
            skip = max(0, int((now - self._t_next) * self.fps))
            for _ in range(skip):
                self.cap.grab()
            ok, f = self.cap.read()
            if not ok:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0); ok, f = self.cap.read()
            self._t_next = max(self._t_next + (skip + 1) / self.fps, now - 1.0)
            wait = self._t_next - time.time()
            if wait > 0:
                time.sleep(wait)
        elif self.kind == "cam":
            ok, f = self.cap.read()
        else:
            with urllib.request.urlopen(self.arg, timeout=2.0) as r:
                f = cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_COLOR)
        if f is None:
            return None
        h, w = f.shape[:2]
        if w != self.work_w:
            f = cv2.resize(f, (self.work_w, int(h * self.work_w / w) // 2 * 2), interpolation=cv2.INTER_AREA)
        return f

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()


def _loop(cfg: dict, publish, status, should_stop) -> None:
    """Fuente -> DAViD -> manos -> publish(bundle). Lo corre el proceso hijo (o un hilo en mode=thread)."""
    src = hands = None
    try:
        from . import david
        from .hands_mp import Hands
        tasks = ("normal", "alpha") + (("depth",) if cfg["depth"] else ())
        model = david.David(tasks, "coreml", cfg["size"])
        hands = Hands(num_hands=4) if cfg["hands"] else None
        src = _Source(cfg["source"], cfg["work_w"])
        status({"status": "corriendo"})
        i, prev, t0, n = 0, None, time.time(), 0
        every, smooth = max(1, int(cfg["alpha_every"])), float(cfg["smooth"])
        stats = {"fps": 0.0, "ms_david": 0.0, "ms_hands": 0.0, "frames": 0}
        while not should_stop():
            try:
                f = src.read()
            except Exception as e:  # noqa: BLE001  (cámara o red caída: reintenta)
                status({"status": f"sin cuadros: {str(e)[:80]}"}); time.sleep(0.5); continue
            if f is None:
                time.sleep(0.05); continue
            h, w = f.shape[:2]
            ta = time.perf_counter()
            full = prev is None or i % every == 0
            res = model.infer(f, only=None if full else ("normal",))
            tb = time.perf_counter()
            hs = hands(f, int(time.time() * 1000)) if hands is not None else []
            tc = time.perf_counter()
            nrm = res["normal"]
            al = res["alpha"] if "alpha" in res else prev["alpha"]
            dp = res["depth"] if "depth" in res else (prev["depth"] if prev else None)
            if prev is not None and smooth > 0 and prev["normal"].shape == nrm.shape:   # la normal no parpadea
                nrm = nrm * (1 - smooth) + prev["normal"] * smooth
                nrm /= np.linalg.norm(nrm, axis=-1, keepdims=True) + 1e-6
                if "alpha" in res:
                    al = al * (1 - smooth) + prev["alpha"] * smooth
            prev = {"normal": nrm, "alpha": al, "depth": dp}
            publish({"frame": f, "normal": nrm.astype(np.float16), "alpha": al.astype(np.float16),
                     "depth": dp, "hands": [{"handed": x["handed"], "landmarks": x["landmarks"]} for x in hs],
                     "seq": i, "t": time.time(), "size": (w, h)})
            i += 1; n += 1
            stats.update(ms_david=(tb - ta) * 1000, ms_hands=(tc - tb) * 1000, frames=i)
            if time.time() - t0 >= 2:
                stats["fps"] = n / (time.time() - t0); n, t0 = 0, time.time()
                status({"stats": dict(stats)})
        status({"status": "detenida", "stats": dict(stats)})
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        status({"status": "error", "error": f"{type(e).__name__}: {e}"})
    finally:
        if src is not None:
            src.close()
        if hands is not None:
            hands.close()


def _child(cfg: dict, out_q, ctl_q, stop_ev) -> None:
    def publish(b):
        try:
            out_q.put_nowait(b)
        except queue.Full:                          # el lector va atrasado: se descarta el más viejo
            try:
                out_q.get_nowait()
            except queue.Empty:
                pass
            try:
                out_q.put_nowait(b)
            except queue.Full:
                pass
    _loop(cfg, publish, ctl_q.put, stop_ev.is_set)


class PeopleLayer:
    def __init__(self, source: str, size: int = 384, depth: bool = False, work_w: int = 640,
                 alpha_every: int = 3, smooth: float = 0.35, idle_s: float = 15.0, hands: bool = True,
                 mode: str = "process"):
        self.cfg = {"source": source, "size": int(size), "depth": bool(depth), "work_w": int(work_w),
                    "alpha_every": int(alpha_every), "smooth": float(smooth), "hands": bool(hands)}
        self.source, self.size, self.mode, self.idle_s = source, int(size), mode, float(idle_s)
        self.status = "cargando DAViD"
        self.error: str | None = None
        self.error_t = 0.0
        self.stats = {"fps": 0.0, "ms_david": 0.0, "ms_hands": 0.0, "frames": 0}
        self._latest: dict | None = None
        self._last_req = time.time()
        self._stop = False
        self.proc = None
        if mode == "process":
            ctx = mp.get_context("spawn")
            self._out, self._ctl, self._stop_ev = ctx.Queue(maxsize=2), ctx.Queue(), ctx.Event()
            self.proc = ctx.Process(target=_child, args=(self.cfg, self._out, self._ctl, self._stop_ev),
                                    name=f"people:{source}", daemon=True)
            self.proc.start()
            self.thread = threading.Thread(target=self._pump, name=f"people-pump:{source}", daemon=True)
        else:
            self.thread = threading.Thread(target=_loop, name=f"people:{source}", daemon=True,
                                           args=(self.cfg, self._set_latest, self._on_status, self._should_stop))
        self.thread.start()

    # ---- consumo
    def latest(self) -> dict | None:
        self._last_req = time.time()
        return self._latest

    def alive(self) -> bool:
        running = self.thread.is_alive() and (self.proc is None or self.proc.is_alive())
        return running and not self._stop and self.error is None

    def stop(self) -> None:
        self._stop = True
        if self.proc is not None:
            self._stop_ev.set()

    def info(self) -> dict:
        return {"source": self.source, "size": self.size, "mode": self.mode, "status": self.status,
                "error": self.error, "pid": self.proc.pid if self.proc else None,
                **{k: round(v, 1) if isinstance(v, float) else v for k, v in self.stats.items()}}

    # ---- internos
    def _set_latest(self, b: dict) -> None:
        self._latest = b

    def _on_status(self, m: dict) -> None:
        if "status" in m:
            self.status = m["status"]
        if "stats" in m:
            self.stats.update(m["stats"])
        if m.get("error"):
            self.error, self.error_t = m["error"], time.time()

    def _should_stop(self) -> bool:
        if time.time() - self._last_req > self.idle_s:
            self._stop = True
        return self._stop

    def _pump(self) -> None:
        """Proceso: trae resultados y estado al runtime, y apaga al hijo si nadie mira."""
        while True:
            if self._should_stop():
                self._stop_ev.set()
            try:
                self._set_latest(self._out.get(timeout=0.2))
            except queue.Empty:
                pass
            except Exception:  # noqa: BLE001
                pass
            while True:
                try:
                    self._on_status(self._ctl.get_nowait())
                except queue.Empty:
                    break
            if not self.proc.is_alive():
                time.sleep(0.2)                     # el último mensaje (p. ej. el error) puede venir atrasado
                while True:
                    try:
                        self._on_status(self._ctl.get_nowait())
                    except queue.Empty:
                        break
                if self.error is None and not self._stop:
                    self.error, self.error_t = f"el proceso terminó (código {self.proc.exitcode})", time.time()
                    self.status = "error"
                break
            if self._stop and self._stop_ev.is_set():
                self.proc.join(timeout=5)
                if self.proc.is_alive():
                    self.proc.terminate()
                self.status = "detenida (sin uso)"
                break


def get_layer(source: str, size: int = 384, depth: bool = False, **kw) -> PeopleLayer:
    """La capa compartida para (fuente, tamaño, profundidad); la crea si no hay una viva. Una capa con error se
    deja ver `RETRY_S` segundos antes de reintentar (no recarga el modelo en cada cuadro)."""
    key = (source, int(size), bool(depth))
    with _LOCK:
        lay = _LAYERS.get(key)
        if lay is not None and not lay.alive():
            if lay.error and time.time() - lay.error_t < RETRY_S:
                return lay
            lay = None
        if lay is None:
            lay = PeopleLayer(source, size, depth, **kw)
            _LAYERS[key] = lay
        return lay


def layers_info() -> list[dict]:
    with _LOCK:
        return [v.info() for v in _LAYERS.values()]

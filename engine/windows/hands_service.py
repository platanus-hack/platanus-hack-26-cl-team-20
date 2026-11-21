"""Servicio de manos del PC Windows: webcam de pared (Logitech Brio 100) → MediaPipe Hand Landmarker → WebSocket.

Corre en el PC, en C:\\sl-scanner con su venv (Python 3.12):

    python windows/hands_service.py                    # Brio índice 0 (DirectShow) a 1920x1080, WS :8793, HTTP :8794
    python windows/hands_service.py --camera 1 --hz 20
    python windows/hands_service.py --fake             # sin cámara ni modelo: cuadros y una mano sintéticos

Salidas (solo LAN y tailnet: cualquier otra IP se rechaza):

- WebSocket ws://<pc>:8793/ — un mensaje JSON por cuadro procesado (15–30 Hz):
    {"type": "hands", "t": <epoch s de la captura, reloj del PC>, "seq": n, "size": [1920, 1080], "fps": 29.9,
     "det_ms": 14.2, "detector": "mediapipe-tasks",
     "hands": [{"id": 3, "handed": "Right", "score": 0.97,
                "landmarks": [[x, y, z], ...21],  # px de la Brio; z relativo a la muñeca, misma escala que x
                "tip_index": [x, y],              # landmark 8
                "pinch": false, "pinch_ratio": 0.83}]}
  El cliente puede mandar {"type": "ping", "t0": <su reloj>} y recibe {"type": "pong", "t0": ..., "t": <reloj PC>}
  (desfase de relojes y latencia, como NTP).
- HTTP http://<pc>:8794/
    GET /health        estado (cámara, detector, fps, clientes) en JSON; incluye las claves de windows/camserve.py
                       (camera_open, frame, seq, fps, mean, age_s, hint) para reemplazarlo en el mismo puerto
    GET /frame.jpg     cuadro actual (?q=80 calidad, ?w=960 ancho); cabeceras X-Frame-Seq y X-Frame-Time
    GET /frame.png     cuadro sin pérdida para calibrar (?fresh=3 espera 3 cuadros nuevos, ?gray=1 en grises)
    GET /hands.json    último mensaje de manos

Detector: MediaPipe Tasks HandLandmarker (`pip install mediapipe`; la 1.x solo trae la API Tasks y pide el modelo
`hand_landmarker.task`, que se baja solo a C:\\sl-scanner\\models la primera vez). Si hay una mediapipe vieja con
`mp.solutions.hands`, también sirve. Si mediapipe no carga, el servicio sigue sirviendo cuadros (para calibrar) con
`hands: []` y /health dice por qué. Instalación sin romper OpenCV: ver playground/hands/README.md.

MediaPipe supone imagen espejada (selfie) para decidir izquierda/derecha; la Brio entrega la imagen sin espejar, así
que se invierte la etiqueta (`--handed-as-is` para no hacerlo).

Autocontenido a propósito (cv2, numpy, websockets ≥ 13 y mediapipe; nada del repo) para poder correrlo solo.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

VERSION = "hands-service/1"
ROOT = Path(__file__).resolve().parent.parent
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/"
             "hand_landmarker.task")
WRIST, THUMB_TIP, INDEX_MCP, INDEX_TIP, MIDDLE_MCP, PINKY_MCP = 0, 4, 5, 8, 9, 17
TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


# ---------------------------------------------------------------------- red: solo LAN

def is_lan(ip: str, extra: tuple = ()) -> bool:
    """Loopback, privadas (192.168/16, 10/8, 172.16/12, fd00::/8), link-local o tailnet (100.64/10)."""
    try:
        a = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    if a.version == 6 and a.ipv4_mapped is not None:
        a = a.ipv4_mapped
    return (a.is_loopback or a.is_private or a.is_link_local or (a.version == 4 and a in TAILSCALE_V4)
            or any(a in n for n in extra))


# ---------------------------------------------------------------------- geometría de la mano

def palm_size(lm: np.ndarray) -> float:
    p = np.asarray(lm, np.float64)[:, :2]
    return float((np.linalg.norm(p[WRIST] - p[MIDDLE_MCP]) + np.linalg.norm(p[INDEX_MCP] - p[PINKY_MCP])) / 2)


class HandTracker:
    """Ids estables entre cuadros (vecino más cercano de la muñeca) y pinch con histéresis por mano."""

    def __init__(self, max_jump: float = 0.2, pinch_on: float = 0.30, pinch_off: float = 0.42, ttl: float = 0.5):
        self.max_jump = max_jump            # fracción de la diagonal del cuadro
        self.pinch_on, self.pinch_off = pinch_on, pinch_off
        self.ttl = ttl
        self.tracks: dict[int, dict] = {}
        self.next_id = 0

    def update(self, dets: list[tuple[np.ndarray, str, float]], size: tuple[int, int], t: float) -> list[dict]:
        diag = math.hypot(*size)
        self.tracks = {i: tr for i, tr in self.tracks.items() if t - tr["t"] <= self.ttl}
        pairs = []
        for di, (lm, handed, _) in enumerate(dets):
            for tid, tr in self.tracks.items():
                d = float(np.linalg.norm(lm[WRIST, :2] - tr["wrist"]))
                pairs.append((d + (0.0 if tr["handed"] == handed else 0.05 * diag), di, tid, d))
        assign, used = {}, set()
        for _, di, tid, d in sorted(pairs):
            if di in assign or tid in used or d > self.max_jump * diag:
                continue
            assign[di] = tid
            used.add(tid)
        out = []
        for di, (lm, handed, score) in enumerate(dets):
            tid = assign.get(di)
            if tid is None:
                tid = self.next_id
                self.next_id += 1
                self.tracks[tid] = {"pinch": False}
            tr = self.tracks[tid]
            ratio = float(np.linalg.norm(lm[THUMB_TIP, :2] - lm[INDEX_TIP, :2]) / max(palm_size(lm), 1e-6))
            pinch = ratio < (self.pinch_off if tr.get("pinch") else self.pinch_on)
            tr.update(wrist=lm[WRIST, :2].copy(), handed=handed, t=t, pinch=pinch)
            out.append({"id": tid, "handed": handed, "score": round(float(score), 3),
                        "landmarks": np.round(lm, 1).tolist(),
                        "tip_index": [round(float(lm[INDEX_TIP, 0]), 1), round(float(lm[INDEX_TIP, 1]), 1)],
                        "pinch": bool(pinch), "pinch_ratio": round(ratio, 3)})
        return out


# ---------------------------------------------------------------------- detectores

def ensure_model(path: Path) -> Path:
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    log(f"bajando el modelo de MediaPipe a {path} ({MODEL_URL})")
    tmp = path.with_suffix(".part")
    urllib.request.urlretrieve(MODEL_URL, tmp)
    tmp.replace(path)
    return path


class MediaPipeTasks:
    name = "mediapipe-tasks"

    def __init__(self, model: Path, num_hands: int = 2, det_conf: float = 0.5, track_conf: float = 0.5):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
        self.mp = mp
        opts = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(ensure_model(model))),
            running_mode=vision.RunningMode.VIDEO, num_hands=num_hands, min_hand_detection_confidence=det_conf,
            min_hand_presence_confidence=det_conf, min_tracking_confidence=track_conf)
        self.lm = vision.HandLandmarker.create_from_options(opts)
        self.version = getattr(mp, "__version__", "?")
        self._last_ts = -1

    def detect(self, rgb: np.ndarray, ts_ms: int) -> list[tuple[np.ndarray, str, float]]:
        ts_ms = max(int(ts_ms), self._last_ts + 1)          # el modo VIDEO exige marcas crecientes
        self._last_ts = ts_ms
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        res = self.lm.detect_for_video(img, ts_ms)
        out = []
        for lms, hd in zip(res.hand_landmarks, res.handedness):
            arr = np.array([[p.x, p.y, p.z] for p in lms], np.float64)
            cat = hd[0] if hd else None
            out.append((arr, (cat.category_name if cat else None) or "?", float(cat.score or 0.0) if cat else 0.0))
        return out


class MediaPipeLegacy:
    name = "mediapipe-solutions"

    def __init__(self, num_hands: int = 2, det_conf: float = 0.5, track_conf: float = 0.5, complexity: int = 1):
        import mediapipe as mp
        self.h = mp.solutions.hands.Hands(static_image_mode=False, max_num_hands=num_hands,
                                          model_complexity=complexity, min_detection_confidence=det_conf,
                                          min_tracking_confidence=track_conf)
        self.version = getattr(mp, "__version__", "?")

    def detect(self, rgb: np.ndarray, ts_ms: int) -> list[tuple[np.ndarray, str, float]]:
        r = self.h.process(rgb)
        out = []
        for lms, hd in zip(r.multi_hand_landmarks or [], r.multi_handedness or []):
            arr = np.array([[p.x, p.y, p.z] for p in lms.landmark], np.float64)
            c = hd.classification[0]
            out.append((arr, c.label, float(c.score)))
        return out


class NoDetector:
    name = "ninguno"
    version = "-"

    def __init__(self, reason: str):
        self.reason = reason

    def detect(self, rgb, ts_ms):
        return []


# Mano derecha abierta (muñeca→nudillo medio = 1), para --fake. Misma plantilla que playground/hands/model.py.
TEMPLATE = np.array([
    [0.00, 0.00], [-0.35, -0.15], [-0.60, -0.35], [-0.80, -0.55], [-0.95, -0.70],
    [-0.25, -0.95], [-0.30, -1.35], [-0.32, -1.60], [-0.34, -1.80],
    [0.00, -1.00], [0.00, -1.45], [0.00, -1.72], [0.00, -1.95],
    [0.22, -0.93], [0.27, -1.33], [0.30, -1.57], [0.32, -1.77],
    [0.42, -0.80], [0.52, -1.10], [0.58, -1.30], [0.62, -1.47]], np.float64)


class FakeDetector:
    """Una mano que da vueltas, baja y sube (cambia de tamaño) y hace pinch cada tanto. Coordenadas normalizadas."""
    name = "fake"
    version = "-"

    def detect(self, rgb, ts_ms):
        s = ts_ms / 1000.0
        cx, cy = 0.5 + 0.2 * math.cos(s * 0.8), 0.55 + 0.15 * math.sin(s * 0.8)
        scale = 0.07 * (1.0 + 0.15 * max(0.0, math.sin(s * 0.5)))         # "levantada" la mitad del tiempo
        t = TEMPLATE.copy()
        if math.sin(s * 1.3) > 0.6:
            t[THUMB_TIP] = t[INDEX_TIP] + (0.05, 0.05)
        h, w = rgb.shape[:2]
        pts = t * scale * np.array([1.0, w / h]) + (cx, cy)              # escala isótropa en px
        arr = np.zeros((21, 3))
        arr[:, :2] = pts
        return [(arr, "Left", 0.99)]                                      # "Left" espejado = derecha real


def make_detector(kind: str, model: Path, num_hands: int, det_conf: float, track_conf: float):
    if kind == "fake":
        return FakeDetector()
    if kind == "none":
        return NoDetector("desactivado (--detector none)")
    errors = []
    if kind in ("auto", "tasks"):
        try:
            return MediaPipeTasks(model, num_hands, det_conf, track_conf)
        except Exception as e:  # noqa: BLE001
            errors.append(f"tasks: {type(e).__name__}: {e}")
    if kind in ("auto", "legacy"):
        try:
            return MediaPipeLegacy(num_hands, det_conf, track_conf)
        except Exception as e:  # noqa: BLE001
            errors.append(f"legacy: {type(e).__name__}: {e}")
    reason = "; ".join(errors) or kind
    log(f"sin detector de manos ({reason}); sigo sirviendo cuadros")
    return NoDetector(reason)


# ---------------------------------------------------------------------- captura

class FakeCamera:
    def __init__(self, size: tuple[int, int]):
        self.size = size

    def read(self):
        w, h = self.size
        s = time.time()
        img = np.zeros((h, w, 3), np.uint8)
        img[:] = (40, 40, 40)
        cv2.circle(img, (int(w * (0.5 + 0.3 * math.cos(s))), int(h * 0.5)), 40, (0, 200, 255), -1)
        cv2.putText(img, time.strftime("%H:%M:%S"), (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 2)
        time.sleep(1 / 30)
        return True, img

    def isOpened(self):
        return True

    def release(self):
        pass


class Capture:
    """Lee la cámara en un hilo y guarda el último cuadro; si se cae, reabre sola cada segundo."""

    def __init__(self, index: int, size: tuple[int, int], fps: float, rotate: int = 0, fake: bool = False):
        self.index, self.req_size, self.req_fps, self.rotate, self.fake = index, size, fps, rotate % 360, fake
        self.cond = threading.Condition()
        self.frame: np.ndarray | None = None
        self.t = 0.0
        self.seq = 0
        self.status = "abriendo"
        self.fps = 0.0
        self.size = size
        self.backend = "fake" if fake else ""
        self.running = False
        self.cap = None

    def _open(self):
        if self.fake:
            return FakeCamera(self.req_size)
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            cap.release()
            return None
        # la Brio 100 es USB 2.0: 1080p30 solo en MJPG (en YUY2 da ~5 fps). FOURCC antes que el tamaño.
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.req_size[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.req_size[1])
        cap.set(cv2.CAP_PROP_FPS, self.req_fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.backend = cap.getBackendName()
        return cap

    def run(self) -> None:
        self.running = True
        stamps: list[float] = []
        while self.running:
            if self.cap is None:
                self.cap = self._open()
                if self.cap is None:
                    self.status = f"no abre la cámara {self.index}"
                    time.sleep(1.0)
                    continue
                self.status = "ok"
                log(f"cámara {self.index} abierta ({self.backend})")
            ok, f = self.cap.read()
            t = time.time()
            if not ok or f is None:
                self.status = "sin cuadros: reabriendo"
                self.cap.release()
                self.cap = None
                time.sleep(1.0)
                continue
            if self.rotate == 90:
                f = cv2.rotate(f, cv2.ROTATE_90_CLOCKWISE)
            elif self.rotate == 180:
                f = cv2.rotate(f, cv2.ROTATE_180)
            elif self.rotate == 270:
                f = cv2.rotate(f, cv2.ROTATE_90_COUNTERCLOCKWISE)
            stamps.append(t)
            stamps = stamps[-30:]
            with self.cond:
                self.frame, self.t, self.seq = f, t, self.seq + 1
                self.size = (f.shape[1], f.shape[0])
                self.fps = (len(stamps) - 1) / (stamps[-1] - stamps[0]) if len(stamps) > 2 else 0.0
                self.status = "ok"
                self.cond.notify_all()
        if self.cap is not None:
            self.cap.release()

    def wait_new(self, after_seq: int, timeout: float = 1.0):
        with self.cond:
            if not self.cond.wait_for(lambda: self.seq != after_seq and self.frame is not None, timeout=timeout):
                return None
            return self.frame, self.t, self.seq

    def fresh(self, n: int, timeout: float = 3.0):
        """Cuadro capturado al menos `n` cuadros después de ahora (para calibrar: después de cambiar el patrón)."""
        with self.cond:
            target = self.seq + max(0, n)
            if not self.cond.wait_for(lambda: self.seq >= target and self.frame is not None, timeout=timeout):
                return None
            return self.frame, self.t, self.seq


# ---------------------------------------------------------------------- servicio

class Hub:
    """Último mensaje de manos; los clientes WS esperan aquí (cada uno manda solo lo más nuevo)."""

    def __init__(self):
        self.cond = threading.Condition()
        self.seq = 0
        self.json = ""
        self.msg: dict = {}

    def publish(self, msg: dict) -> None:
        with self.cond:
            self.seq += 1
            msg["seq"] = self.seq
            self.msg = msg
            self.json = json.dumps(msg, separators=(",", ":"))
            self.cond.notify_all()

    def wait(self, last: int, timeout: float):
        with self.cond:
            self.cond.wait_for(lambda: self.seq != last, timeout=timeout)
            return self.seq, self.json


class Service:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.extra = tuple(ipaddress.ip_network(n, strict=False) for n in (a.allow or []))
        self.cap = Capture(a.camera, tuple(int(v) for v in a.size.split("x")), a.fps, a.rotate, fake=a.fake)
        self.detector = make_detector("fake" if a.fake else a.detector, Path(a.model), a.num_hands,
                                      a.det_conf, a.track_conf)
        self.tracker = HandTracker(pinch_on=a.pinch_on, pinch_off=a.pinch_off)
        self.hub = Hub()
        self.running = False
        self.clients = 0
        self.det_ms = 0.0
        self.started = time.time()
        self.threads: list[threading.Thread] = []
        self.ws_server = None
        self.http_server = None
        self.ws_port = a.ws_port
        self.http_port = a.http_port

    # ---- detección
    def detect_loop(self) -> None:
        period = 1.0 / max(1.0, self.a.hz)
        last = -1
        while self.running:
            got = self.cap.wait_new(last, 1.0)
            if got is None:
                continue
            frame, t, last = got
            t0 = time.perf_counter()
            h, w = frame.shape[:2]
            dw = min(w, self.a.det_width)
            small = cv2.resize(frame, (dw, int(round(h * dw / w))), interpolation=cv2.INTER_AREA) if dw < w else frame
            try:
                dets = self.detector.detect(cv2.cvtColor(small, cv2.COLOR_BGR2RGB), int(t * 1000))
            except Exception as e:  # noqa: BLE001  un cuadro malo no tumba el servicio
                log(f"detector falló: {e!r}")
                dets = []
            scale = np.array([w, h, w], np.float64)              # normalizado → px; z en la escala de x
            dets_px = []
            for lm, handed, score in dets:
                if not self.a.handed_as_is:
                    handed = {"Left": "Right", "Right": "Left"}.get(handed, handed)
                dets_px.append((lm * scale, handed, score))
            hands = self.tracker.update(dets_px, (w, h), t)
            self.det_ms = 1000 * (time.perf_counter() - t0)
            self.hub.publish({"type": "hands", "t": round(t, 4), "size": [w, h], "fps": round(self.cap.fps, 1),
                              "det_ms": round(self.det_ms, 1), "detector": self.detector.name, "hands": hands})
            rest = period - (time.perf_counter() - t0)
            if rest > 0:
                time.sleep(rest)

    # ---- WebSocket
    def ws_handler(self, ws) -> None:
        from websockets.exceptions import ConnectionClosed
        ip = ws.remote_address[0] if ws.remote_address else ""
        if not is_lan(ip, self.extra):
            log(f"WS rechazado: {ip} no es LAN/tailnet")
            ws.close(1008, "solo LAN")
            return
        self.clients += 1
        log(f"WS cliente {ip} ({self.clients} conectados)")
        lock = threading.Lock()
        alive = threading.Event()
        alive.set()

        def receiver():
            try:
                for raw in ws:
                    try:
                        m = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(m, dict) and m.get("type") == "ping":
                        with lock:
                            ws.send(json.dumps({"type": "pong", "t0": m.get("t0"), "t": time.time()}))
            except ConnectionClosed:
                pass
            finally:
                alive.clear()

        rx = threading.Thread(target=receiver, daemon=True)
        rx.start()
        last = -1
        try:
            while self.running and alive.is_set():
                seq, js = self.hub.wait(last, 0.5)
                if seq != last and js:
                    with lock:
                        ws.send(js)
                    last = seq
        except ConnectionClosed:
            pass
        finally:
            self.clients -= 1
            log(f"WS cliente {ip} se fue ({self.clients} conectados)")

    # ---- HTTP
    def health(self) -> dict:
        det = self.detector
        frame, now = self.cap.frame, time.time()
        mean = float(frame[::8, ::8].mean()) if frame is not None else 0.0
        return {"ok": self.cap.status == "ok" and not isinstance(det, NoDetector), "service": VERSION,
                "t": now, "uptime_s": round(now - self.started, 1),
                # mismas claves que windows/camserve.py (lo reemplaza en el 8794)
                "camera_open": self.cap.status == "ok", "frame": None if frame is None else list(self.cap.size),
                "seq": self.cap.seq, "fps": round(self.cap.fps, 1), "mean": round(mean, 1),
                "age_s": round(now - self.cap.t, 2) if self.cap.t else None,
                "hint": "tapa de privacidad cerrada o cámara sin luz" if frame is not None and mean < 2 else "ok",
                "camera": {"index": self.a.camera, "status": self.cap.status, "size": list(self.cap.size),
                           "fps": round(self.cap.fps, 1), "frames": self.cap.seq, "backend": self.cap.backend},
                "detector": {"name": det.name, "version": getattr(det, "version", "?"),
                             "error": getattr(det, "reason", None), "det_ms": round(self.det_ms, 1)},
                "hands": len(self.hub.msg.get("hands", [])), "msg_seq": self.hub.seq, "clients": self.clients,
                "ws_port": self.ws_port, "http_port": self.http_port}

    def make_http_handler(self):
        svc = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):          # sin una línea por request
                pass

            def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (extra or {}).items():
                    self.send_header(k, str(v))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not is_lan(self.client_address[0], svc.extra):
                    return self._send(403, b"solo LAN", "text/plain")
                u = urlparse(self.path)
                q = {k: v[-1] for k, v in parse_qs(u.query).items()}
                try:
                    return self._route(u, q)
                except ValueError as e:
                    return self._send(400, f"parámetro inválido: {e}".encode(), "text/plain; charset=utf-8")

            def _route(self, u, q):
                if u.path in ("/", "/health"):
                    return self._send(200, json.dumps(svc.health()).encode(), "application/json")
                if u.path == "/hands.json":
                    return self._send(200, (svc.hub.json or "{}").encode(), "application/json")
                if u.path in ("/frame.jpg", "/frame.png"):
                    n = int(q.get("fresh", 0) or 0)
                    got = svc.cap.fresh(n) if n else svc.cap.wait_new(-1, 2.0)
                    if got is None:
                        return self._send(503, f"sin cuadros: {svc.cap.status}".encode(), "text/plain")
                    img = got[0]
                    if q.get("gray") in ("1", "true"):
                        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                    if u.path == "/frame.jpg":
                        w = int(q.get("w", 0) or 0)
                        if 0 < w < img.shape[1]:
                            img = cv2.resize(img, (w, int(img.shape[0] * w / img.shape[1])), interpolation=cv2.INTER_AREA)
                        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(q.get("q", 80))])
                        ctype = "image/jpeg"
                    else:
                        ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
                        ctype = "image/png"
                    return self._send(200 if ok else 500, buf.tobytes() if ok else b"", ctype,
                                      {"X-Frame-Seq": got[2], "X-Frame-Time": f"{got[1]:.4f}"})
                return self._send(404, b"rutas: /health /frame.jpg /frame.png /hands.json", "text/plain")

        return Handler

    # ---- ciclo de vida
    def start(self) -> "Service":
        from websockets.sync.server import serve
        self.running = True
        for fn, name in ((self.cap.run, "captura"), (self.detect_loop, "deteccion")):
            th = threading.Thread(target=fn, name=name, daemon=True)
            th.start()
            self.threads.append(th)
        self.ws_server = serve(self.ws_handler, self.a.host, self.a.ws_port, compression=None)
        self.ws_port = self.ws_server.socket.getsockname()[1]
        th = threading.Thread(target=self.ws_server.serve_forever, name="ws", daemon=True)
        th.start()
        self.threads.append(th)
        self.http_server = ThreadingHTTPServer((self.a.host, self.a.http_port), self.make_http_handler())
        self.http_server.daemon_threads = True
        self.http_port = self.http_server.server_address[1]
        th = threading.Thread(target=self.http_server.serve_forever, name="http", daemon=True)
        th.start()
        self.threads.append(th)
        log(f"manos: WS ws://{self.a.host}:{self.ws_port}/  HTTP http://{self.a.host}:{self.http_port}/health  "
            f"detector={self.detector.name} cámara={'fake' if self.a.fake else self.a.camera}")
        return self

    def stop(self) -> None:
        self.running = False
        self.cap.running = False
        if self.ws_server is not None:
            self.ws_server.shutdown()
        if self.http_server is not None:
            self.http_server.shutdown()
            self.http_server.server_close()
        for th in self.threads:
            th.join(timeout=2)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="manos de la Brio por WebSocket (PC Windows)")
    p.add_argument("--camera", type=int, default=0, help="índice DirectShow de la Brio")
    p.add_argument("--size", default="1920x1080")
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270])
    p.add_argument("--hz", type=float, default=30.0, help="tope de mensajes por segundo")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--ws-port", type=int, default=8793)
    p.add_argument("--http-port", type=int, default=8794)
    p.add_argument("--allow", action="append", help="red extra permitida (CIDR); por defecto LAN, loopback y tailnet")
    p.add_argument("--detector", default="auto", choices=["auto", "tasks", "legacy", "none", "fake"])
    p.add_argument("--model", default=str(ROOT / "models" / "hand_landmarker.task"))
    p.add_argument("--det-width", type=int, default=960, help="ancho al que se baja el cuadro para detectar")
    p.add_argument("--num-hands", type=int, default=2)
    p.add_argument("--det-conf", type=float, default=0.5)
    p.add_argument("--track-conf", type=float, default=0.5)
    p.add_argument("--pinch-on", type=float, default=0.30)
    p.add_argument("--pinch-off", type=float, default=0.42)
    p.add_argument("--handed-as-is", action="store_true", help="no invertir izquierda/derecha de MediaPipe")
    p.add_argument("--fake", action="store_true", help="cuadros y mano sintéticos (sin cámara ni mediapipe)")
    return p


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):              # consola de Windows (cp1252) por SSH: nunca caerse por un tilde
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass
    a = build_parser().parse_args(argv)
    svc = Service(a).start()
    try:
        while True:
            time.sleep(5)
            h = svc.health()
            if svc.clients or h["camera"]["status"] != "ok":
                log(f"cámara {h['camera']['status']} {h['camera']['fps']} fps · det {h['detector']['det_ms']} ms · "
                    f"manos {h['hands']} · clientes {h['clients']}")
    except KeyboardInterrupt:
        pass
    finally:
        svc.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

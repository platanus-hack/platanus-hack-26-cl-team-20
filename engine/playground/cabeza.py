"""cabeza.py: posición 3D del ojo de la persona en el marco de la MESA, desde la Osmo cenital (por HTTP, sin tocar la cámara).

Marco de la mesa (mm): origen en la esquina TL del lienzo proyectable, x a lo largo del lienzo, y hacia abajo del lienzo
(como en la imagen del lienzo), z hacia ARRIBA desde la mesa.

Cómo:
  * K aproximada de la Osmo por FOV horizontal (--hfov 84°), centro en el medio del cuadro.
  * Pose del plano de la mesa en cámara: cv2.solvePnP (IPPE) con las 4 esquinas del lienzo en píxeles de cámara
    (inversa de H_cam_to_lienzo de superficie_feed.json) y su tamaño métrico (lienzo_px · mm_por_px de superficie.json).
    El json se relee si cambia en disco (si el feed se recalibra porque la Osmo se movió).
  * Cabeza: MediaPipe FaceLandmarker (VIDEO). Punto medio entre los iris (landmarks 468/473); la profundidad sale de la
    distancia interpupilar (--ipd 63 mm) corregida por el giro de la cabeza (matriz de transformación facial).
    Respaldo 1: PoseLandmarker (ojos/nariz/orejas) con altura de ojo asumida (--alto-ojo): rayo ∩ plano z=alto.
    Respaldo 2: coronilla = parte más alta (más cerca de la persona) de la mancha de piel en la franja superior del cuadro,
    también con altura asumida. Cada medida dice su `fuente`.
  * EMA y servidor HTTP: /cabeza.json {ojo_mm:[x,y,z], mirada:[dx,dy,dz], ok, hz, fuente, ...}, /debug.jpg, /status.

Uso:
  .venv/bin/python -m playground.cabeza --scan captures/dale-20261102-1511 --port 8803
  .venv/bin/python -m playground.cabeza --scan captures/dale-20261102-1511 --seconds 10 --log reports/pozo/cabeza_log.jsonl
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

MODELS = Path("/Volumes/Externo/models/mediapipe")
CAM_REF_W = 3840                                   # las homografías del feed están en píxeles 4K
D_GL_CV = np.diag([1.0, -1.0, -1.0])               # OpenGL (MediaPipe) → OpenCV: y abajo, z adelante


# ----------------------------------------------------------------------------------------------------------------- cámara
def K_de_fov(w: int, h: int, hfov_deg: float) -> np.ndarray:
    f = 0.5 * w / math.tan(math.radians(hfov_deg) / 2)
    return np.array([[f, 0, w / 2.0], [0, f, h / 2.0], [0, 0, 1.0]])


def fetch_jpg(url: str, timeout: float = 3.0) -> np.ndarray | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            buf = np.frombuffer(r.read(), np.uint8)
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001
        return None


class Mesa:
    """Pose del plano de la mesa en la cámara: P_cam = R·P_F + t, con P_F = (x, y, z_F) y z_F = -z_arriba.
    Todo en píxeles del cuadro de CALIBRACIÓN (camera_size de superficie_feed.json); los cuadros que llegan se
    reescalan a ese tamaño. La imagen útil puede venir con franjas negras (Osmo en 1080x1920 con la foto 16:9 al
    medio): K se arma con el ancho útil y el centro de la banda útil."""

    def __init__(self, scan: Path, hfov: float):
        self.scan, self.hfov = scan, hfov
        self.sup = json.loads((scan / "superficie.json").read_text())
        self.mm_px = float(self.sup["mm_por_px"])
        self.lw, self.lh = self.sup["lienzo_px"]
        self.feed_path = scan / "superficie_feed.json"
        self.mtime = 0.0; self.H_cl = None; self.K = None; self.R = None; self.t = None
        self.reproj_px = None; self.banda = None; self.esq = None
        self._load()

    def _load(self):
        self.mtime = self.feed_path.stat().st_mtime
        feed = json.loads(self.feed_path.read_text())
        self.H_cl = np.array(feed["H_cam_to_lienzo"], np.float64)
        self.cam_size = [int(v) for v in feed.get("camera_size", [3840, 2160])]
        if feed.get("osmo_tl_tr_br_bl"):
            self.esq = np.array(feed["osmo_tl_tr_br_bl"], np.float64)
        else:
            Hi = np.linalg.inv(self.H_cl)
            c = np.array([[0, 0], [self.lw, 0], [self.lw, self.lh], [0, self.lh]], np.float64).reshape(-1, 1, 2)
            self.esq = cv2.perspectiveTransform(c, Hi).reshape(4, 2)
        self.R = None                                              # fuerza re-solvePnP

    def maybe_reload(self):
        try:
            if self.feed_path.stat().st_mtime != self.mtime:
                self._load()
        except OSError:
            pass

    def esquinas_cam(self, w: int | None = None) -> np.ndarray:
        """Esquinas TL TR BR BL del lienzo en px de calibración (o escaladas a un cuadro de ancho w)."""
        if w is None:
            return self.esq.copy()
        return self.esq * (w / float(self.cam_size[0]))

    def set_banda(self, r0: int, r1: int):
        """Filas [r0, r1) con imagen (sin franjas negras), en px de calibración."""
        if self.banda != (r0, r1):
            self.banda = (r0, r1); self.R = None

    def pose(self, *_):
        if self.R is not None:
            return
        w, h = self.cam_size
        r0, r1 = self.banda if self.banda else (0, h)
        f = 0.5 * w / math.tan(math.radians(self.hfov) / 2)
        self.K = np.array([[f, 0, w / 2.0], [0, f, (r0 + r1) / 2.0], [0, 0, 1.0]])
        obj = np.array([[0, 0, 0], [self.lw * self.mm_px, 0, 0], [self.lw * self.mm_px, self.lh * self.mm_px, 0],
                        [0, self.lh * self.mm_px, 0]], np.float64)
        img = self.esq
        ok, rvec, tvec = cv2.solvePnP(obj, img, self.K, None, flags=cv2.SOLVEPNP_IPPE)
        if not ok:
            raise RuntimeError("solvePnP falló con las esquinas del lienzo")
        self.R = cv2.Rodrigues(rvec)[0]; self.t = tvec.reshape(3)
        proj = cv2.projectPoints(obj, rvec, tvec, self.K, None)[0].reshape(4, 2)
        self.reproj_px = float(np.abs(proj - img).max())

    # --- conversiones
    def cam_to_mesa(self, p_cam: np.ndarray) -> np.ndarray:
        pf = self.R.T @ (np.asarray(p_cam, np.float64) - self.t)
        return np.array([pf[0], pf[1], -pf[2]])

    def dir_cam_to_mesa(self, v: np.ndarray) -> np.ndarray:
        vf = self.R.T @ np.asarray(v, np.float64)
        return np.array([vf[0], vf[1], -vf[2]])

    def rayo(self, u: float, v: float):
        """Origen (centro de la cámara) y dirección del rayo por el píxel (u,v), en el marco de la mesa (z arriba)."""
        C = self.cam_to_mesa(np.zeros(3))
        d = self.dir_cam_to_mesa(np.linalg.inv(self.K) @ np.array([u, v, 1.0]))
        return C, d

    def rayo_a_altura(self, u: float, v: float, z_mm: float) -> np.ndarray:
        C, d = self.rayo(u, v)
        if abs(d[2]) < 1e-9:
            return np.array([np.nan] * 3)
        s = (z_mm - C[2]) / d[2]
        return C + s * d

    def punto_por_profundidad(self, u: float, v: float, Z_cam_mm: float) -> np.ndarray:
        p_cam = (np.linalg.inv(self.K) @ np.array([u, v, 1.0])) * Z_cam_mm
        return self.cam_to_mesa(p_cam)

    def altura_camara(self) -> float:
        return float(self.cam_to_mesa(np.zeros(3))[2])


def banda_util(frame: np.ndarray, umbral: float = 8.0) -> tuple[int, int]:
    """Filas con imagen (la Osmo puede entregar la foto 16:9 con franjas negras arriba y abajo)."""
    rows = frame[:, ::4].mean(axis=(1, 2))
    nz = np.where(rows > umbral)[0]
    if len(nz) < 10:
        return 0, frame.shape[0]
    return int(nz.min()), int(nz.max()) + 1


# ---------------------------------------------------------------------------------------------------------------- cabeza
class Detector:
    def __init__(self, ipd_mm: float, alto_ojo: float, pose_model: bool = True, up_px: int = 720):
        import mediapipe as mp
        from mediapipe.tasks import python as mpp
        from mediapipe.tasks.python import vision
        self.mp, self.vision = mp, vision
        self.ipd, self.alto_ojo, self.up_px = ipd_mm, alto_ojo, up_px
        self.face = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=mpp.BaseOptions(model_asset_path=str(MODELS / "face_landmarker.task")),
            running_mode=vision.RunningMode.VIDEO, num_faces=1, output_facial_transformation_matrixes=True,
            min_face_detection_confidence=0.3, min_face_presence_confidence=0.3, min_tracking_confidence=0.3))
        self.pose = None
        pm = MODELS / "pose_landmarker_lite.task"
        if pose_model and pm.exists():
            self.pose = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
                base_options=mpp.BaseOptions(model_asset_path=str(pm)), running_mode=vision.RunningMode.VIDEO,
                num_poses=1, min_pose_detection_confidence=0.3, min_tracking_confidence=0.3))
        self.t0 = time.monotonic(); self.last_ts = -1

    def _ts(self) -> int:
        ts = int((time.monotonic() - self.t0) * 1000)
        if ts <= self.last_ts:
            ts = self.last_ts + 1
        self.last_ts = ts
        return ts

    def roi(self, frame: np.ndarray, mesa: Mesa, sx: float = 1.0, sy: float = 1.0) -> tuple[int, int, int, int]:
        """Zona donde está la persona (px de calibración): frente al borde cercano del lienzo (el lado más cercano al
        borde superior del cuadro), desde arriba de la banda útil hasta un poco más allá de ese borde; de ancho, el borde
        cercano más 25 % por lado. Así la cara ocupa una fracción razonable del recorte (el detector la necesita > ~8 %)."""
        h, w = int(frame.shape[0] * sy), int(frame.shape[1] * sx)
        r0, r1 = mesa.banda if mesa.banda else (0, h)
        e = mesa.esq
        y_lienzo = float(e[:, 1].min())
        y1 = int(min(r1, max(r0 + 0.25 * (r1 - r0), y_lienzo + 0.12 * (r1 - r0))))
        xa, xb = float(min(e[0, 0], e[1, 0])), float(max(e[0, 0], e[1, 0]))
        m = 0.25 * (xb - xa)
        return int(max(0, xa - m)), int(r0), int(min(w, xb + m)), y1

    def detectar(self, frame: np.ndarray, mesa: Mesa, sx: float = 1.0, sy: float = 1.0) -> dict:
        """frame tal como llega; (sx, sy) = px de calibración por px del cuadro. Devuelve {fuente, ok, px:[u,v] (px de
        calibración), ojo_mm, mirada, ...}. fuente ∈ cara | pose-ojos | coronilla | nada."""
        x0, y0, x1, y1 = self.roi(frame, mesa, sx, sy)                    # en px de calibración
        crop = frame[int(y0 / sy):int(y1 / sy), int(x0 / sx):int(x1 / sx)]
        if crop.size == 0:
            return {"fuente": "nada", "ok": False, "roi": [x0, y0, x1, y1]}
        k = self.up_px / max(crop.shape[1], 1)                            # llevar el recorte a --up-px de ancho
        if abs(k - 1) > 0.01:
            crop = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC if k > 1 else cv2.INTER_AREA)
        h, w = crop.shape[:2]

        def back(u, v):
            return float(x0 + u / k * sx), float(y0 + v / k * sy)

        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        ts = self._ts()
        r = self.face.detect_for_video(img, ts)
        if r.face_landmarks:
            lm = r.face_landmarks[0]
            if len(lm) > 473:
                a, b = lm[468], lm[473]
            else:
                a, b = lm[33], lm[263]
            pa = np.array(back(a.x * w, a.y * h)); pb = np.array(back(b.x * w, b.y * h))
            u, v = (pa + pb) / 2
            d_px = float(np.linalg.norm(pa - pb))
            M = np.array(r.facial_transformation_matrixes[0], np.float64) if r.facial_transformation_matrixes else np.eye(4)
            R_cv = D_GL_CV @ M[:3, :3]
            ex = R_cv[:, 0]; ex /= (np.linalg.norm(ex) + 1e-9)       # eje ojo-ojo en cámara (OpenCV)
            ray = np.linalg.inv(mesa.K) @ np.array([u, v, 1.0]); ray /= np.linalg.norm(ray)
            fores = math.sqrt(max(0.05, 1.0 - float(ex @ ray) ** 2))  # acortamiento por giro de la cabeza
            fx = mesa.K[0, 0]
            Z = fx * self.ipd * fores / max(d_px, 1e-3)
            ojo = mesa.punto_por_profundidad(u, v, Z)
            fwd = D_GL_CV @ M[:3, 2]; fwd /= (np.linalg.norm(fwd) + 1e-9)
            mirada = mesa.dir_cam_to_mesa(fwd)
            z_mp = float(np.linalg.norm(M[:3, 3]) * 10.0)             # traslación MediaPipe (cm → mm), solo informativa
            return {"fuente": "cara", "ok": True, "px": [float(u), float(v)], "d_px": d_px, "Z_cam_mm": float(Z),
                    "ojo_mm": ojo.tolist(), "mirada": mirada.tolist(), "dist_mp_mm": z_mp, "roi": [x0, y0, x1, y1], "k": k,
                    "ojo_alto_asumido_mm": mesa.rayo_a_altura(u, v, self.alto_ojo).tolist()}
        if self.pose is not None:
            p = self.pose.detect_for_video(img, ts)
            if p.pose_landmarks:
                lm = p.pose_landmarks[0]
                ojos = [lm[i] for i in (2, 5)]
                self.pose_vis = [round(ojos[0].visibility, 2), round(ojos[1].visibility, 2), round(lm[0].visibility, 2)]
                if all(o.visibility > 0.45 for o in ojos) and lm[0].visibility > 0.45 and abs(ojos[0].x - ojos[1].x) < 0.15 \
                        and 0 <= ojos[0].y < 1 and 0 <= ojos[1].y < 1:
                    u, v = back((ojos[0].x + ojos[1].x) / 2 * w, (ojos[0].y + ojos[1].y) / 2 * h)
                    ojo = mesa.rayo_a_altura(u, v, self.alto_ojo)
                    return {"fuente": "pose-ojos", "ok": True, "px": [u, v], "ojo_mm": ojo.tolist(),
                            "mirada": self._mirada_hacia_lienzo(ojo, mesa), "alto_asumido": True, "roi": [x0, y0, x1, y1]}
        d = self._coronilla(crop, mesa, back, lambda xcal: (xcal - x0) / sx * k)
        d["roi"] = [x0, y0, x1, y1]; d["pose_vis"] = getattr(self, "pose_vis", None); self.pose_vis = None
        return d

    def _mirada_hacia_lienzo(self, ojo: np.ndarray, mesa: Mesa) -> list:
        """Sin pose de cabeza: se asume que mira al centro del lienzo."""
        c = np.array([mesa.lw * mesa.mm_px / 2, mesa.lh * mesa.mm_px / 2, 0.0])
        d = c - ojo; n = np.linalg.norm(d)
        return (d / n).tolist() if n > 1e-6 else [0.0, 0.0, -1.0]

    def _coronilla(self, crop: np.ndarray, mesa: Mesa, back, back_inv_x) -> dict:
        """Mancha de piel más alta (más lejos de la mesa) en la zona de la persona: la cara vista desde arriba."""
        h, w = crop.shape[:2]
        ycc = cv2.cvtColor(crop, cv2.COLOR_BGR2YCrCb)
        m = cv2.inRange(ycc, (40, 135, 85), (255, 175, 135))
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
        n, lab, st, cen = cv2.connectedComponentsWithStats(m)
        # la persona está frente al borde cercano del lienzo: manchas cerca de esa columna (±15 % del ancho) y, entre
        # ellas, la más grande (la cara desde arriba es más grande que una mano)
        xc = back_inv_x(float((mesa.esq[0, 0] + mesa.esq[1, 0]) / 2))
        cands = [i for i in range(1, n) if st[i, cv2.CC_STAT_AREA] > 0.004 * w * h and abs(cen[i][0] - xc) < 0.15 * w]
        if not cands:
            return {"fuente": "nada", "ok": False}
        i = max(cands, key=lambda j: st[j, cv2.CC_STAT_AREA])          # la más grande: la cara vista desde arriba
        u, v = back(float(cen[i][0]), float(cen[i][1]))
        # vista desde arriba el centro de la mancha es ~ la frente/nariz: ojo ≈ rayo ∩ plano z = alto_ojo
        ojo = mesa.rayo_a_altura(u, v, self.alto_ojo)
        return {"fuente": "coronilla", "ok": True, "px": [u, v], "ojo_mm": ojo.tolist(),
                "mirada": self._mirada_hacia_lienzo(ojo, mesa), "alto_asumido": True, "area_px": int(st[i, cv2.CC_STAT_AREA])}


# ----------------------------------------------------------------------------------------------------------------- lazo
class Seguidor:
    def __init__(self, a):
        self.a = a
        self.mesa = Mesa(Path(a.scan), a.hfov)
        self.det = Detector(a.ipd, a.alto_ojo, pose_model=not a.sin_pose, up_px=a.up_px)
        self.lock = threading.Lock()
        self.ojo = None; self.mirada = None; self.t_ok = 0.0; self.fuente = "nada"; self.raw = {}
        self.n = 0; self.n_fuente = {}; self.hz = 0.0; self.debug = None; self.t_start = time.time()
        self.log = open(a.log, "a") if a.log else None

    def url_cuadro(self) -> list[str]:
        return [self.a.feed + "/raw_hd.jpg", self.a.feed + "/raw.jpg"]

    def paso(self) -> dict | None:
        frame = None
        for u in self.url_cuadro():
            frame = fetch_jpg(u)
            if frame is not None:
                break
        if frame is None:
            return None
        self.mesa.maybe_reload()
        cw, ch = self.mesa.cam_size
        sx, sy = cw / frame.shape[1], ch / frame.shape[0]              # /raw_hd.jpg viene reescalado (y a veces aplastado)
        b0, b1 = banda_util(frame)
        self.mesa.set_banda(int(b0 * sy), int(b1 * sy)); self.mesa.pose()
        d = self.det.detectar(frame, self.mesa, sx, sy)
        d["t"] = time.time(); d["frame"] = [frame.shape[1], frame.shape[0]]; d["banda"] = list(self.mesa.banda)
        self._last_scale = (sx, sy)
        with self.lock:
            self.n += 1; self.n_fuente[d["fuente"]] = self.n_fuente.get(d["fuente"], 0) + 1
            if d.get("ok"):
                ojo = np.array(d["ojo_mm"]); mir = np.array(d["mirada"])
                al = self.a.ema
                if self.ojo is None or time.time() - self.t_ok > 2.0 or np.linalg.norm(ojo - self.ojo) > self.a.salto:
                    self.ojo, self.mirada = ojo, mir
                else:
                    self.ojo = (1 - al) * self.ojo + al * ojo; self.mirada = (1 - al) * self.mirada + al * mir
                self.t_ok = time.time(); self.fuente = d["fuente"]; self.raw = d
        if self.log:
            self.log.write(json.dumps({k: v for k, v in d.items()}, default=float) + "\n"); self.log.flush()
        if self.a.debug_cada and self.n % self.a.debug_cada == 0:
            self._debug(frame, d)
        return d

    def _debug(self, frame, d):
        vis = frame.copy(); sx, sy = getattr(self, "_last_scale", (1.0, 1.0)); S = np.array([1 / sx, 1 / sy])
        cv2.polylines(vis, [(self.mesa.esquinas_cam() * S).astype(np.int32).reshape(-1, 1, 2)], True, (0, 255, 0), 2)
        if d.get("roi"):
            x0, y0, x1, y1 = d["roi"]; cv2.rectangle(vis, (int(x0 / sx), int(y0 / sy)), (int(x1 / sx) - 1, int(y1 / sy) - 1), (255, 200, 0), 2)
        if d.get("px"):
            cv2.circle(vis, (int(d["px"][0] / sx), int(d["px"][1] / sy)), 8, (0, 0, 255), 2)
        if self.mesa.banda:
            vis = vis[int(self.mesa.banda[0] / sy):int(self.mesa.banda[1] / sy)]
        txt = f'{d["fuente"]} ' + (f'ojo {np.round(d["ojo_mm"]).astype(int).tolist()} mm' if d.get("ok") else "sin cabeza")
        cv2.putText(vis, txt, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        ok, buf = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            self.debug = buf.tobytes()
            if self.a.debug_png:
                Path(self.a.debug_png).write_bytes(self.debug)

    def estado(self) -> dict:
        with self.lock:
            hace = time.time() - self.t_ok if self.t_ok else None
            ok = self.ojo is not None and hace is not None and hace < self.a.vigencia
            return {"ojo_mm": None if self.ojo is None else np.round(self.ojo, 1).tolist(),
                    "mirada": None if self.mirada is None else np.round(self.mirada, 3).tolist(),
                    "ok": bool(ok), "hz": round(self.hz, 1), "fuente": self.fuente, "hace_s": None if hace is None else round(hace, 2),
                    "alto_asumido": bool(self.raw.get("alto_asumido", False)), "n": self.n, "n_fuente": self.n_fuente,
                    "camara_alto_mm": round(self.mesa.altura_camara(), 0) if self.mesa.R is not None else None,
                    "reproj_px": self.mesa.reproj_px, "ipd_mm": self.a.ipd, "alto_ojo_mm": self.a.alto_ojo, "hfov": self.a.hfov,
                    "raw": {k: self.raw.get(k) for k in ("px", "d_px", "Z_cam_mm", "dist_mp_mm", "ojo_alto_asumido_mm") if k in self.raw}}

    def correr(self):
        periodo = 1.0 / max(self.a.hz, 0.5); n0, t0 = 0, time.time()
        while True:
            t = time.time()
            if self.a.seconds and t - self.t_start > self.a.seconds:
                return
            d = self.paso()
            if d is None:
                time.sleep(0.5)
            n0 += 1
            if time.time() - t0 >= 2:
                self.hz, n0, t0 = n0 / (time.time() - t0), 0, time.time()
            dt = periodo - (time.time() - t)
            if dt > 0:
                time.sleep(dt)


def servir(seg: Seguidor, port: int):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/cabeza.json", "/status", "/"):
                body = json.dumps(seg.estado(), ensure_ascii=False).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
                self.end_headers(); self.wfile.write(body); return
            if path == "/debug.jpg":
                data = seg.debug
                if data is None:
                    self.send_error(503); return
                self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.send_header("Content-Length", str(len(data)))
                self.end_headers(); self.wfile.write(data); return
            self.send_error(404)

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scan", default="captures/dale-20261102-1511")
    p.add_argument("--feed", default="http://127.0.0.1:8798")
    p.add_argument("--port", type=int, default=8803)
    p.add_argument("--hz", type=float, default=8.0)
    p.add_argument("--hfov", type=float, default=84.0, help="FOV horizontal de la Osmo (grados)")
    p.add_argument("--ipd", type=float, default=63.0, help="distancia interpupilar (mm)")
    p.add_argument("--alto-ojo", type=float, default=450.0, help="altura del ojo sobre la mesa cuando no se ve la cara (mm)")
    p.add_argument("--ema", type=float, default=0.35)
    p.add_argument("--salto", type=float, default=400.0, help="salto (mm) sobre el que se reinicia el EMA")
    p.add_argument("--vigencia", type=float, default=1.5, help="s tras la última detección en que ok sigue true")
    p.add_argument("--sin-pose", action="store_true", help="no usar PoseLandmarker como respaldo")
    p.add_argument("--up-px", type=int, default=720, help="ancho al que se agranda la zona de la persona para los detectores")
    p.add_argument("--seconds", type=float, default=0.0, help="0 = para siempre")
    p.add_argument("--log", default=None, help="jsonl con cada medida")
    p.add_argument("--debug-cada", type=int, default=4, help="cada cuántos cuadros se arma /debug.jpg (0 = nunca)")
    p.add_argument("--debug-png", default=None, help="también guardar el último debug aquí")
    a = p.parse_args(argv)
    seg = Seguidor(a)
    seg.mesa.pose()
    print(json.dumps({"cabeza": f"http://127.0.0.1:{a.port}/cabeza.json", "camara_alto_mm": round(seg.mesa.altura_camara()),
                      "reproj_px": seg.mesa.reproj_px, "camara_xy_mm": np.round(seg.mesa.cam_to_mesa(np.zeros(3))[:2]).tolist(),
                      "pose_respaldo": seg.det.pose is not None}), flush=True)
    servir(seg, a.port)
    try:
        seg.correr()
    except KeyboardInterrupt:
        pass
    print(json.dumps(seg.estado(), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

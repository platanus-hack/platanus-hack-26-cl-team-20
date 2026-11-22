"""Visión sobre el lienzo rectificado de la mesa (feed de `superficie_feed`): manos con MediaPipe (21 puntos, gestos),
rig de huesos calibrado a las manos de Max, malla 2.5D para enmascarar/dibujar, toque por pellizco o reposo, y
objetos pequeños (mouse blanco, lápiz, grinder, vape…) seguidos por color/forma desde una semilla.

Todo en px del lienzo (mm = px · mm_por_px) y, cuando se pide, en px del proyector por H_lienzo_to_proj. Corre a
media resolución (960x563) para ir suave; los puntos se devuelven en px del lienzo completo.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import cv2
import numpy as np

MODELS = Path("/Volumes/Externo/models/mediapipe")
BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
         (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]
FINGERS = {"pulgar": [1, 2, 3, 4], "indice": [5, 6, 7, 8], "medio": [9, 10, 11, 12], "anular": [13, 14, 15, 16], "menique": [17, 18, 19, 20]}
TIPS = {"pulgar": 4, "indice": 8, "medio": 12, "anular": 16, "menique": 20}


class OneEuro:
    """Filtro One Euro por punto: poco retardo al moverse, nada de jitter quieto."""

    def __init__(self, min_cutoff: float = 1.2, beta: float = 0.02, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = None; self.dx = None; self.t = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff); return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: np.ndarray, t: float) -> np.ndarray:
        if self.x is None:
            self.x, self.dx, self.t = x.copy(), np.zeros_like(x), t; return x
        dt = max(1e-3, t - self.t); self.t = t
        dx = (x - self.x) / dt
        a_d = self._alpha(self.d_cutoff, dt); self.dx = a_d * dx + (1 - a_d) * self.dx
        cutoff = self.min_cutoff + self.beta * np.linalg.norm(self.dx, axis=-1, keepdims=True)
        a = self._alpha(cutoff, dt); self.x = a * x + (1 - a) * self.x
        return self.x


class HandRig:
    """Largo de cada hueso (mm) de las manos de Max, aprendido cuando la mano está plana y abierta. Con eso la malla
    2.5D (palma + dedos como cápsulas) calza con sus manos y no con una mano genérica."""

    def __init__(self, path: Path | None, mmpx: float):
        self.path = path; self.mmpx = mmpx
        self.samples: dict[str, list[np.ndarray]] = {"Left": [], "Right": []}
        self.bones_mm: dict[str, list[float] | None] = {"Left": None, "Right": None}
        self.n = {"Left": 0, "Right": 0}
        if path and path.exists():
            try:
                d = json.loads(path.read_text()); self.bones_mm.update(d.get("bones_mm", {})); self.n.update(d.get("n", {}))
            except Exception:  # noqa: BLE001
                pass

    def observe(self, handed: str, pts: np.ndarray, pts3d: np.ndarray | None, gesture: str) -> None:
        """Acumula largos cuando la mano está abierta y plana (Open_Palm y poca profundidad relativa)."""
        if gesture != "Open_Palm" or pts3d is None:
            return
        if float(np.ptp(pts3d[:, 2])) > 0.06:                           # z normalizada: la mano inclinada no sirve
            return
        L = np.array([np.linalg.norm(pts[b] - pts[a]) for a, b in BONES]) * self.mmpx
        self.samples[handed].append(L); self.n[handed] += 1
        if len(self.samples[handed]) >= 15:
            med = np.median(np.stack(self.samples[handed]), axis=0)
            prev = self.bones_mm[handed]
            self.bones_mm[handed] = (0.7 * np.array(prev) + 0.3 * med).round(2).tolist() if prev else med.round(2).tolist()
            self.samples[handed] = self.samples[handed][-30:]
            self.save()

    def save(self) -> None:
        if self.path:
            self.path.write_text(json.dumps({"bones_mm": self.bones_mm, "n": self.n, "bones": BONES, "t": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=1))

    def widths_px(self, handed: str, pts: np.ndarray) -> dict[int, float]:
        """Ancho (px lienzo) de cada hueso de dedo: proporción fija del largo calibrado (o del observado)."""
        L = self.bones_mm.get(handed)
        out = {}
        for k, (a, b) in enumerate(BONES):
            l_px = (L[k] / self.mmpx) if L else float(np.linalg.norm(pts[b] - pts[a]))
            if a == 0 or b == 17 and a == 0:
                continue
            frac = 0.62 if a in (1, 5, 9, 13, 17) else 0.55 if a in (2, 6, 10, 14, 18) else 0.5
            out[k] = max(6.0, frac * l_px)
        return out

    def mesh(self, handed: str, pts: np.ndarray) -> np.ndarray:
        """Máscara 2.5D de la mano (uint8 del tamaño que se pida después): palma = casco de muñeca+MCPs ensanchado;
        dedos = cápsulas por hueso. Devuelve lista de polígonos (px lienzo) para pintar con fillPoly."""
        polys = []
        palm_idx = [0, 1, 5, 9, 13, 17]
        palm = pts[palm_idx].astype(np.float32)
        c = palm.mean(0); palm = c + (palm - c) * 1.18                   # la palma es más ancha que los nudillos
        hull = cv2.convexHull(palm.reshape(-1, 1, 2)).reshape(-1, 2)
        polys.append(hull)
        W = self.widths_px(handed, pts)
        for k, (a, b) in enumerate(BONES):
            if k not in W:
                continue
            p, q = pts[a], pts[b]; d = q - p; n = np.linalg.norm(d)
            if n < 1:
                continue
            d /= n; nv = np.array([-d[1], d[0]]) * (W[k] / 2)
            tip_ext = d * (W[k] * 0.55) if b in TIPS.values() else 0.0  # la yema sobresale del último punto
            polys.append(np.array([p + nv, q + nv + tip_ext, q - nv + tip_ext, p - nv], np.float32))
        return polys


class Hands:
    def __init__(self, mmpx: float, Hlp: np.ndarray | None, rig_path: Path | None, scale: float = 0.5, num_hands: int = 2):
        import mediapipe as mp
        from mediapipe.tasks import python as mpt
        from mediapipe.tasks.python import vision
        self.mp, self.vision = mp, vision
        opts = vision.GestureRecognizerOptions(base_options=mpt.BaseOptions(model_asset_path=str(MODELS / "gesture_recognizer.task")),
                                               running_mode=vision.RunningMode.VIDEO, num_hands=num_hands,
                                               min_hand_detection_confidence=0.4, min_tracking_confidence=0.4)
        self.det = vision.GestureRecognizer.create_from_options(opts)
        self.scale = scale; self.mmpx = mmpx; self.Hlp = Hlp
        self.rig = HandRig(rig_path, mmpx)
        self.filters: dict[str, OneEuro] = {}
        self.touch: dict[str, dict] = {}                                 # por mano: estado del toque
        self.last_ts = 0; self.hands: list[dict] = []; self.ms = 0.0; self.t = 0.0

    def update(self, lienzo: np.ndarray, t: float | None = None) -> list[dict]:
        t = time.time() if t is None else t
        t0 = time.perf_counter()
        small = cv2.resize(lienzo, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        ts = max(self.last_ts + 1, int(t * 1000)); self.last_ts = ts
        r = self.det.recognize_for_video(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb), ts)
        out = []
        for i, lm in enumerate(r.hand_landmarks):
            handed = r.handedness[i][0].category_name if r.handedness else "?"
            gest = r.gestures[i][0].category_name if r.gestures and r.gestures[i] else "None"
            gscore = float(r.gestures[i][0].score) if r.gestures and r.gestures[i] else 0.0
            pts = np.array([[p.x * small.shape[1], p.y * small.shape[0]] for p in lm], np.float64) / self.scale
            pts3d = np.array([[p.x, p.y, p.z] for p in lm], np.float64)
            hid = f"{handed}{i}"
            f = self.filters.setdefault(hid, OneEuro())
            pts = f(pts, t)
            self.rig.observe(handed, pts, pts3d, gest)
            palm = float(np.linalg.norm(pts[9] - pts[0]))
            pinch_d = float(np.linalg.norm(pts[4] - pts[8])) / max(palm, 1)
            st = self.touch.setdefault(hid, {"pinch": False, "down": False, "still_since": None, "last_tip": None})
            st["pinch"] = pinch_d < (0.28 if st["pinch"] else 0.22)      # histéresis
            tip = pts[8]
            if st["last_tip"] is not None and np.linalg.norm(tip - st["last_tip"]) < 6:
                st["still_since"] = st["still_since"] or t
            else:
                st["still_since"] = None
            st["last_tip"] = tip.copy()
            dwell = st["still_since"] is not None and t - st["still_since"] > 0.45 and gest in ("Pointing_Up", "None")
            touching = st["pinch"] or dwell
            tips_px = {k: pts[v].round(1).tolist() for k, v in TIPS.items()}
            h = {"id": hid, "handed": handed, "gesture": gest, "gesture_score": round(gscore, 2), "pts": pts.round(1).tolist(),
                 "tips": tips_px, "tip_mm": (tip * self.mmpx).round(1).tolist() if self.mmpx else None,
                 "palm_px": round(palm, 1), "pinch": round(pinch_d, 3), "touching": bool(touching),
                 "trigger": "pinch" if st["pinch"] else ("reposo" if dwell else ""), "t": t,
                 "mesh": [p.round(1).tolist() for p in self.rig.mesh(handed, pts)]}
            if self.Hlp is not None:
                h["tip_proj"] = cv2.perspectiveTransform(np.array([[tip]], np.float64), self.Hlp).reshape(2).round(1).tolist()
                h["pts_proj"] = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), self.Hlp).reshape(-1, 2).round(1).tolist()
            out.append(h)
        seen = {h["id"] for h in out}
        for hid in list(self.touch):
            if hid not in seen:
                self.touch.pop(hid, None); self.filters.pop(hid, None)
        self.hands = out; self.ms = (time.perf_counter() - t0) * 1000; self.t = t
        return out

    def draw(self, img: np.ndarray, scale: float = 1.0) -> None:
        for h in self.hands:
            pts = np.array(h["pts"]) * scale
            for poly in h["mesh"]:
                cv2.polylines(img, [(np.array(poly) * scale).astype(np.int32).reshape(-1, 1, 2)], True, (90, 90, 90), 1, cv2.LINE_AA)
            for a, b in BONES:
                cv2.line(img, tuple(pts[a].astype(int)), tuple(pts[b].astype(int)), (255, 200, 60), 2, cv2.LINE_AA)
            for p in pts:
                cv2.circle(img, tuple(p.astype(int)), 3, (0, 220, 255), -1, cv2.LINE_AA)
            tip = pts[8].astype(int)
            cv2.circle(img, tuple(tip), 12, (0, 255, 0) if h["touching"] else (0, 160, 255), 2, cv2.LINE_AA)
            cv2.putText(img, f"{h['handed']} {h['gesture']}{' TOCA' if h['touching'] else ''}", (int(pts[0][0]) - 20, int(pts[0][1]) + 24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

    def status(self) -> dict:
        return {"n": len(self.hands), "ms": round(self.ms, 1), "rig": {k: (len(v) if v else 0) for k, v in self.rig.bones_mm.items()},
                "rig_muestras": self.rig.n,
                "manos": [{k: v for k, v in h.items() if k not in ("pts", "mesh", "pts_proj")} for h in self.hands]}


class ObjectTracker:
    """Objeto chico seguido por histograma de color (CamShift) desde una semilla (caja o máscara) en el lienzo, con
    re-detección por plantilla cuando se pierde. Barato: corre en el lienzo reducido."""

    def __init__(self, name: str, lienzo: np.ndarray, box: tuple[int, int, int, int], scale: float = 0.5, mmpx: float = 0.0):
        self.name = name; self.scale = scale; self.mmpx = mmpx
        x, y, w, h = box
        self.box = (int(x * scale), int(y * scale), int(w * scale), int(h * scale))
        small = cv2.resize(lienzo, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        bx, by, bw, bh = self.box
        roi = hsv[by:by + bh, bx:bx + bw]
        mask = cv2.inRange(roi, (0, 0, 20), (180, 255, 255))
        self.hist = cv2.calcHist([roi], [0, 1], mask, [16, 16], [0, 180, 0, 256]); cv2.normalize(self.hist, self.hist, 0, 255, cv2.NORM_MINMAX)
        self.template = cv2.cvtColor(small[by:by + bh, bx:bx + bw], cv2.COLOR_BGR2GRAY)
        self.area0 = bw * bh; self.ok = True; self.lost = 0; self.center = None; self.angle = 0.0; self.t = 0.0

    def update(self, lienzo: np.ndarray, t: float) -> dict:
        small = cv2.resize(lienzo, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        back = cv2.calcBackProject([hsv], [0, 1], self.hist, [0, 180, 0, 256], 1)
        back &= cv2.inRange(hsv, (0, 0, 20), (180, 255, 255))
        rect, box = cv2.CamShift(back, self.box, (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 12, 1))
        (cx, cy), (rw, rh), ang = rect
        area = rw * rh
        if area < 0.25 * self.area0 or area > 4 * self.area0 or rw < 4 or rh < 4:
            self.lost += 1
            if self.lost > 8:                                           # re-detección por plantilla
                g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                res = cv2.matchTemplate(g, self.template, cv2.TM_CCOEFF_NORMED)
                _, mx, _, loc = cv2.minMaxLoc(res)
                if mx > 0.55:
                    self.box = (loc[0], loc[1], self.template.shape[1], self.template.shape[0]); self.lost = 0; self.ok = True
                else:
                    self.ok = False
        else:
            self.box = box; self.lost = 0; self.ok = True
            self.center = (cx / self.scale, cy / self.scale); self.angle = float(ang); self.t = t
        return self.info()

    def info(self) -> dict:
        x, y, w, h = self.box
        return {"nombre": self.name, "ok": self.ok, "centro": [round(v, 1) for v in self.center] if self.center else None,
                "centro_mm": [round(v * self.mmpx, 1) for v in self.center] if (self.center and self.mmpx) else None,
                "box": [int(x / self.scale), int(y / self.scale), int(w / self.scale), int(h / self.scale)], "angle": round(self.angle, 1),
                "hace_s": round(time.time() - self.t, 2) if self.t else None}


class MouseTracker:
    """Mouse blanco (Logitech G203: 116,6 x 62,2 mm) seguido por su mancha clara en el lienzo: centro, giro y modelo
    con zonas de clic izquierdo, derecho y rueda. El frente (botones) es el extremo más lejano del usuario (Max está
    en el borde `user_edge` del lienzo: 'top' con rot 180)."""

    L_MM, W_MM = 116.6, 62.2

    def __init__(self, mmpx: float, user_edge: str = "top", alpha: float = 0.4):
        self.mmpx = mmpx; self.user_edge = user_edge; self.alpha = alpha
        self.center = None; self.dir = None; self.t = 0.0; self.ok = False; self.size_mm = None
        # umbrales de la mancha (Osmo: blanco de verdad). Con el Quest (croma corrida, menos luz) el feed pone
        # v_min=None (relativo: percentil 99,5 − 40, mínimo 150), s_max más alto y un rango de tamaño más estrecho
        # (el teléfono blanco, 153x72 mm, no debe pasar por mouse)
        self.v_min: float | None = 222; self.s_max = 20; self.tex_max = 1.2
        self.largo_mm = (85.0, 150.0); self.corto_mm = (45.0, 85.0)

    def update(self, lienzo: np.ndarray, exclude_quads: list[np.ndarray], t: float, scale: float = 0.5) -> dict | None:
        small = cv2.resize(lienzo, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        tex = cv2.blur(np.abs(cv2.Laplacian(cv2.GaussianBlur(g, (3, 3), 0), cv2.CV_32F)), (11, 11))
        # blanco de verdad (S≈0), muy claro y liso: la madera clara tiene S≈34 y está rayada; el pad es verde
        v_min = self.v_min if self.v_min is not None else max(150.0, float(np.percentile(hsv[..., 2], 99.5)) - 40.0)
        m = ((hsv[..., 2] > v_min) & (hsv[..., 1] < self.s_max) & (tex < self.tex_max)).astype(np.uint8)
        for q in exclude_quads:                                         # las hojas no son el mouse
            c = q.mean(0); qq = (c + (q - c) * 1.03) * scale
            cv2.fillPoly(m, [qq.astype(np.int32).reshape(-1, 1, 2)], 0)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        n, lab, st, _ = cv2.connectedComponentsWithStats(m)
        best = None
        for i in range(1, n):
            if st[i, cv2.CC_STAT_AREA] < 200:
                continue
            ys, xs = np.nonzero(lab == i)
            (cx, cy), (w, h), ang = cv2.minAreaRect(np.stack([xs, ys], 1).astype(np.float32))
            long_, short_ = max(w, h) / scale * self.mmpx, min(w, h) / scale * self.mmpx
            if not (self.largo_mm[0] <= long_ <= self.largo_mm[1] and self.corto_mm[0] <= short_ <= self.corto_mm[1]):
                continue
            fill = st[i, cv2.CC_STAT_AREA] / max(w * h, 1)
            score = abs(long_ - self.L_MM) / self.L_MM + abs(short_ - self.W_MM) / self.W_MM + (0.2 if fill < 0.7 else 0)
            if best is None or score < best[0]:
                best = (score, (cx / scale, cy / scale), (w, h, ang), (long_, short_))
        if best is None:
            self.ok = self.ok and (t - self.t) < 0.6
            return self.info()
        _, (cx, cy), (w, h, ang), (long_, short_) = best
        # eje largo del rectángulo
        a = math.radians(ang if w >= h else ang + 90)
        d = np.array([math.cos(a), math.sin(a)])
        # el frente apunta lejos del usuario
        away = np.array({"top": [0, 1], "bottom": [0, -1], "left": [1, 0], "right": [-1, 0]}[self.user_edge], float)
        if d @ away < 0:
            d = -d
        c = np.array([cx, cy])
        if self.center is None or np.linalg.norm(c - self.center) > 60:
            self.center, self.dir = c, d
        else:
            self.center = self.center + self.alpha * (c - self.center)
            dd = self.dir + self.alpha * (d - self.dir); self.dir = dd / max(np.linalg.norm(dd), 1e-6)
        self.size_mm = (round(long_, 1), round(short_, 1)); self.t = t; self.ok = True
        return self.info()

    def _local(self, x_mm: float, y_mm: float) -> np.ndarray:
        """Coordenadas del mouse (x a lo ancho, y desde el centro hacia el frente, mm) → px del lienzo."""
        d = self.dir; nv = np.array([-d[1], d[0]])
        return self.center + (d * y_mm + nv * x_mm) / self.mmpx

    def model(self) -> dict | None:
        """Polígonos (px lienzo): contorno (superelipse), botón izquierdo, derecho y rueda."""
        if not self.ok or self.center is None:
            return None
        L, Wd = self.L_MM / 2, self.W_MM / 2
        ts = np.linspace(0, 2 * math.pi, 48, endpoint=False)
        outline = np.array([self._local(Wd * np.sign(math.cos(t)) * abs(math.cos(t)) ** 0.7, L * np.sign(math.sin(t)) * abs(math.sin(t)) ** 0.8) for t in ts])
        def rect(x0, x1, y0, y1):
            return np.array([self._local(x0, y0), self._local(x1, y0), self._local(x1, y1), self._local(x0, y1)])
        # vista desde arriba con el frente hacia +y: izquierdo a la izquierda del usuario (x negativo mirando al frente)
        left = rect(-Wd * 0.95, -4, L * 0.05, L * 0.95); right = rect(4, Wd * 0.95, L * 0.05, L * 0.95)
        wheel = rect(-5, 5, L * 0.45, L * 0.85)
        return {"outline": outline, "izquierdo": left, "derecho": right, "rueda": wheel}

    def info(self) -> dict:
        m = self.model()
        return {"ok": bool(self.ok), "centro": None if self.center is None else self.center.round(1).tolist(),
                "centro_mm": None if self.center is None else (self.center * self.mmpx).round(1).tolist(),
                "dir": None if self.dir is None else self.dir.round(3).tolist(), "size_mm": self.size_mm,
                "angulo_deg": None if self.dir is None else round(math.degrees(math.atan2(self.dir[1], self.dir[0])), 1),
                "zonas": None if m is None else {k: v.round(1).tolist() for k, v in m.items()}, "hace_s": round(time.time() - self.t, 2) if self.t else None}


class KeyboardTracker:
    """Teclado seguido por plantilla: una referencia (recorte del lienzo con el teclado y su cuadrilátero) y, por
    cuadro, matchTemplate (traslación, a escala 1/4) + findTransformECC euclidiano (giro fino). El cuadrilátero
    de referencia se transforma y la grilla de teclas se recalcula con `teclado_grid.fit`."""

    def __init__(self, ref_img: np.ndarray, quad: np.ndarray, scale: float = 0.25):
        self.scale = scale
        self.quad0 = np.asarray(quad, np.float64)
        g = cv2.cvtColor(ref_img, cv2.COLOR_BGR2GRAY)
        x0, y0 = np.floor(self.quad0.min(0)).astype(int); x1, y1 = np.ceil(self.quad0.max(0)).astype(int)
        pad = 10
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad); x1, y1 = min(g.shape[1], x1 + pad), min(g.shape[0], y1 + pad)
        self.origin = np.array([x0, y0], float)
        self.tpl = cv2.resize(g[y0:y1, x0:x1], None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        self.tpl = cv2.equalizeHist(self.tpl)
        self.warp = np.array([[1, 0, 0], [0, 1, 0]], np.float32)      # lienzo(ref) → lienzo(actual), escala 1
        self.quad = self.quad0.copy(); self.score = 0.0; self.t = 0.0; self.ok = False

    def update(self, lienzo: np.ndarray, t: float) -> dict:
        g = cv2.equalizeHist(cv2.resize(cv2.cvtColor(lienzo, cv2.COLOR_BGR2GRAY), None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA))
        if g.shape[0] <= self.tpl.shape[0] or g.shape[1] <= self.tpl.shape[1]:
            return self.info()
        res = cv2.matchTemplate(g, self.tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        self.score = float(mx)
        if mx < 0.35:
            self.ok = self.ok and (t - self.t) < 2.0
            return self.info()
        tx = loc[0] / self.scale - self.origin[0]; ty = loc[1] / self.scale - self.origin[1]
        q = self.quad0 + np.array([tx, ty])
        # giro fino: ECC euclidiano plantilla → zona encontrada (imagen reducida). findTransformECC(template, input, W)
        # devuelve W tal que input(W·x) ≈ template(x): con template = tpl e input = roi, W lleva px de la plantilla
        # a px de la roi.
        try:
            h, w = self.tpl.shape
            x, y = loc
            ox, oy = max(0, x - 8), max(0, y - 8)
            roi = g[oy:y + h + 8, ox:x + w + 8]
            init = np.array([[1, 0, x - ox], [0, 1, y - oy]], np.float32)
            crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 1e-4)
            _, wm = cv2.findTransformECC(self.tpl, roi, init, cv2.MOTION_EUCLIDEAN, crit, None, 5)
            A = wm[:, :2].astype(np.float64); b = (wm[:, 2] + np.array([ox, oy], np.float32)).astype(np.float64)
            # p_lienzo = A·(p_ref − origen) + (b + [ox, oy]) / escala   (la plantilla está a escala `scale`)
            q2 = (self.quad0 - self.origin) @ A.T + b / self.scale
            if np.all(np.isfinite(q2)) and np.linalg.norm(q2 - q, axis=1).max() < 60:
                q = q2
            self.warp = wm
        except cv2.error:
            pass
        if np.all(np.isfinite(q)):
            self.quad = 0.5 * self.quad + 0.5 * q if np.linalg.norm(q - self.quad, axis=1).max() < 40 else q
        self.t = t; self.ok = True
        return self.info()

    def info(self) -> dict:
        return {"ok": bool(self.ok), "score": round(self.score, 3), "quad_lienzo": self.quad.round(1).tolist(), "hace_s": round(time.time() - self.t, 2) if self.t else None}


def draw_objects(img: np.ndarray, objs: list[dict], scale: float = 1.0) -> None:
    for o in objs:
        if not o.get("ok"):
            continue
        x, y, w, h = (int(v * scale) for v in o["box"])
        cv2.rectangle(img, (x, y), (x + w, y + h), (255, 120, 255), 2)
        cv2.putText(img, o["nombre"], (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 120, 255), 1, cv2.LINE_AA)

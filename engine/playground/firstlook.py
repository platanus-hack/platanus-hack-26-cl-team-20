"""Visual v3: shaders por objeto en el espacio del proyector + tracking de OpenCV en vivo.

Entradas (captures/scan2/): scene_pov.png, proj_to_cam.npz (mapa corregido con el ChArUco proyectado),
height_proj.npy, segments.json. La Osmo se lee en el mismo loop: trackers CSRT sobre el cocodrilo y el cuadro
del Buda; si se mueven, sus efectos los siguen (desplazamiento cámara → proyector por la escala local del mapa).
El Buda ya no viaja: respira en su lugar con un shader basado en los bordes de su textura (Canny).

    python -m playground.firstlook --seconds 120            # en vivo con tracking
    python -m playground.firstlook --preview reports/x.png  # cuadros sin hardware
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np

SCAN = Path(os.environ.get("SLSCAN_SCAN", "captures/scan5"))
W, H = 1920, 1080
RW, RH = 960, 540
S = RW / W


def poly_mask(shape, pts):
    m = np.zeros(shape, np.uint8); cv2.fillPoly(m, [np.array(pts, np.int32).reshape(-1, 1, 2)], 255); return m > 0


def rect_pts(bbox):
    x, y, w, h = bbox; return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def order_quad(q):
    q = np.asarray(q, np.float32); s = q.sum(axis=1); d = q[:, 0] - q[:, 1]
    return np.float32([q[np.argmin(s)], q[np.argmax(d)], q[np.argmax(s)], q[np.argmin(d)]])


class Scene:
    def __init__(self, scan: Path = SCAN):
        self.seg = json.load(open(scan / "segments.json"))
        self.height = np.load(scan / "height_proj.npy")
        self.pov = cv2.imread(str(scan / "scene_pov.png"))
        maps = np.load(scan / "proj_to_cam.npz"); self.mapx, self.mapy, self.valid = maps["mapx"], maps["mapy"], maps["valid"]
        gray = cv2.cvtColor(self.pov, cv2.COLOR_BGR2GRAY); seg = self.seg
        # Buda: textura, bordes (Canny) y máscara de la pintura (sin el marco)
        self.buda_quad = order_quad(seg["buda"].get("quad", rect_pts(seg["buda"]["bbox"])))
        bx, by, bw, bh = seg["buda"]["bbox"]
        self.buda_box = (bx, by, bw, bh)
        tex = self.pov[by:by + bh, bx:bx + bw]
        edges = cv2.Canny(cv2.GaussianBlur(tex, (0, 0), 1.2), 60, 140)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
        self.buda_edges = cv2.GaussianBlur(edges.astype(np.float32) / 255, (0, 0), 1.5)
        self.buda_tex = tex.astype(np.float32) / 255
        # halo: centro en la cabeza (zona más saturada/clara del tercio superior)
        hsv = cv2.cvtColor(tex, cv2.COLOR_BGR2HSV)
        top = hsv[: bh // 2, :, 2].astype(np.float32) * (hsv[: bh // 2, :, 1] > 40)
        ys, xs = np.nonzero(top > np.percentile(top, 97))
        self.buda_head = (float(xs.mean()) if len(xs) else bw / 2, float(ys.mean()) if len(ys) else bh * 0.35)
        # torre: cara superior / frontal
        tx, ty, tw, th = seg["torre"]["bbox"]
        hgt = cv2.GaussianBlur(self.height[ty:ty + th, tx:tx + tw], (0, 0), 3)
        prof = np.abs(cv2.Sobel(hgt, cv2.CV_32F, 0, 1, ksize=5)).mean(axis=1) + np.abs(cv2.Sobel(cv2.GaussianBlur(gray[ty:ty + th, tx:tx + tw].astype(np.float32), (0, 0), 3), cv2.CV_32F, 0, 1, ksize=5)).mean(axis=1) / 50
        prof[: int(th * 0.05)] = 0; prof[int(th * 0.45):] = 0
        split = ty + int(np.argmax(prof))
        self.torre_top = [tx, ty, tw, max(10, split - ty)]; self.torre_front = [tx, split, tw, ty + th - split]
        # teclado: largo real y rectángulo rotado
        kx, ky, kw, kh = seg["teclado"]["bbox"]; y_end = ky + kh
        while y_end < H - 1 and (gray[y_end, kx:kx + kw] > 140).mean() > 0.35:
            y_end += 1
        y_end = min(y_end + 2, ky + 130)
        ys, xs = np.nonzero(gray[ky:y_end, kx:kx + kw] > 140)
        (cx, cy), (rw, rh), ang = cv2.minAreaRect(np.column_stack([xs + kx, ys + ky]).astype(np.float32))
        if rw < rh:
            rw, rh = rh, rw; ang += 90
        if abs(ang) > 45:
            ang = ang - 90 * round(ang / 90)
        ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        pitch = seg["teclado"].get("pitch_px", 34.0)
        first = seg["teclado"]["teclas_blancas"][0][0] if seg["teclado"].get("teclas_blancas") else kx
        self.white_keys, self.black_keys = [], []
        x_left = cx - rw / 2; off = (first - kx) % pitch; n = int(rw / pitch + 0.5)
        for i in range(n):
            u0 = x_left + off + i * pitch - cx; u1 = u0 + pitch * 0.9
            self.white_keys.append([[cx + u * ca - v * sa, cy + u * sa + v * ca] for u, v in [(u0, -rh / 2), (u1, -rh / 2), (u1, rh / 2), (u0, rh / 2)]])
        for (bx_, by_, bw_, bh_) in seg["teclado"].get("teclas_negras", []):
            u0, u1 = bx_ - cx, bx_ + bw_ - cx
            self.black_keys.append([[cx + u * ca - v * sa, cy + u * sa + v * ca] for u, v in [(u0, -rh / 2), (u1, -rh / 2), (u1, rh * 0.1), (u0, rh * 0.1)]])
        self.key_info = {"n": n, "angle": round(ang, 2), "len": round(rh, 1)}
        # DDJ / circuit / papel / cocodrilo
        jogs = seg.get("ddj", {}).get("jogs", [])
        self.jogs = [(j[0], j[1], j[2]) for j in sorted(jogs)[:2]] if len(jogs) == 2 else [(1200, 860, 90), (1560, 880, 100)]
        self.ddj_bbox = seg.get("ddj", {}).get("bbox", seg["controladores"]["bbox"])
        self.circuit_bbox = seg.get("circuit", {}).get("bbox")
        self.papel_quad = order_quad(seg["papel"].get("quad", rect_pts(seg["papel"]["bbox"]))) if "papel" in seg else None
        self.croc = np.array(seg["cocodrilo"]["contour"], np.float32) if "cocodrilo" in seg else None
        self.croc_box = seg["cocodrilo"]["bbox"] if "cocodrilo" in seg else None

        def sm(m): return cv2.resize(m.astype(np.float32), (RW, RH), interpolation=cv2.INTER_AREA)
        self.m = {"torre_top": sm(poly_mask((H, W), rect_pts(self.torre_top))), "torre_front": sm(poly_mask((H, W), rect_pts(self.torre_front))),
                  "papel": sm(poly_mask((H, W), self.papel_quad)) if self.papel_quad is not None else np.zeros((RH, RW), np.float32),
                  "ddj": sm(poly_mask((H, W), rect_pts(self.ddj_bbox))),
                  "circuit": sm(poly_mask((H, W), rect_pts(self.circuit_bbox))) if self.circuit_bbox else np.zeros((RH, RW), np.float32)}
        gx = cv2.Sobel(self.height, cv2.CV_32F, 1, 0, ksize=5); gyy = cv2.Sobel(self.height, cv2.CV_32F, 0, 1, ksize=5)
        edge = np.sqrt(gx * gx + gyy * gyy); self.m["bordes"] = sm(np.clip(edge / (np.percentile(edge, 99) + 1e-6), 0, 1))
        hn = (self.height - np.percentile(self.height, 5)) / (np.percentile(self.height, 95) - np.percentile(self.height, 5) + 1e-6)
        self.m["altura"] = sm(np.clip(hn, 0, 1))

    # --- proyector <-> cámara (para el tracking)
    def proj_to_cam(self, x, y):
        xi, yi = int(np.clip(x, 0, W - 1)), int(np.clip(y, 0, H - 1))
        return float(self.mapx[yi, xi]), float(self.mapy[yi, xi])

    def local_scale(self, bbox):
        """px de cámara por px de proyector alrededor de un bbox (para convertir desplazamientos)."""
        x, y, w, h = bbox
        (ax, ay), (bx, by) = self.proj_to_cam(x, y + h / 2), self.proj_to_cam(x + w, y + h / 2)
        (cx, cy), (dx, dy) = self.proj_to_cam(x + w / 2, y), self.proj_to_cam(x + w / 2, y + h)
        sx = (bx - ax) / max(1, w); sy = (dy - cy) / max(1, h)
        return (sx if abs(sx) > 1e-3 else 0.3), (sy if abs(sy) > 1e-3 else 0.3)

    def cam_bbox(self, bbox):
        x, y, w, h = bbox
        pts = [self.proj_to_cam(px, py) for px, py in rect_pts(bbox)]
        xs = [p[0] for p in pts if p[0] >= 0]; ys = [p[1] for p in pts if p[1] >= 0]
        if len(xs) < 3:
            return None
        return (int(min(xs)), int(min(ys)), int(max(xs) - min(xs)), int(max(ys) - min(ys)))


class Tracker:
    """CSRT sobre la cámara para un objeto; entrega el desplazamiento en px de proyector."""

    def __init__(self, name, scene: Scene, bbox_proj, frame):
        self.name = name; self.sc = scene; self.bbox_proj = bbox_proj
        self.cam_box0 = scene.cam_bbox(bbox_proj); self.ok = False; self.dx = self.dy = 0.0
        self.sx, self.sy = scene.local_scale(bbox_proj)
        if self.cam_box0 and self.cam_box0[2] > 10 and self.cam_box0[3] > 10:
            ctor = getattr(cv2, "TrackerCSRT_create", None) or getattr(cv2, "TrackerKCF_create", None) or getattr(cv2, "TrackerMIL_create", None)
            self.kind = ctor.__name__ if ctor else "template"
            if ctor:
                self.tr = ctor(); self.tr.init(frame, self.cam_box0); self.ok = True
            else:                                   # respaldo: template matching
                x, y, w, h = self.cam_box0; self.tpl = frame[y:y + h, x:x + w].copy(); self.ok = True
        self.last_box = self.cam_box0

    def update(self, frame):
        if not self.ok:
            return
        if self.kind == "template":
            x, y, w, h = self.cam_box0; sx0, sy0 = max(0, x - 80), max(0, y - 80)
            roi = frame[sy0:y + h + 80, sx0:x + w + 80]
            if roi.shape[0] < h or roi.shape[1] < w:
                return
            r = cv2.matchTemplate(roi, self.tpl, cv2.TM_CCOEFF_NORMED); _, mv, _, ml = cv2.minMaxLoc(r)
            ok, box = mv > 0.5, (ml[0] + sx0, ml[1] + sy0, w, h)
        else:
            ok, box = self.tr.update(frame)
        if ok:
            self.last_box = box
            self.dx = (box[0] + box[2] / 2 - (self.cam_box0[0] + self.cam_box0[2] / 2)) / self.sx
            self.dy = (box[1] + box[3] / 2 - (self.cam_box0[1] + self.cam_box0[3] / 2)) / self.sy

    def shift(self):
        return (self.dx, self.dy)


class FirstLook:
    def __init__(self):
        self.sc = Scene()
        ys, xs = np.mgrid[0:RH, 0:RW].astype(np.float32); self.xs, self.ys = xs / RW, ys / RH
        self.trackers: dict[str, Tracker] = {}

    def init_trackers(self, frame):
        sc = self.sc
        self.trackers["buda"] = Tracker("buda", sc, sc.buda_box, frame)
        if sc.croc_box:
            self.trackers["croc"] = Tracker("croc", sc, sc.croc_box, frame)

    def update_trackers(self, frame):
        for t in self.trackers.values():
            t.update(frame)

    def _shift(self, name):
        t = self.trackers.get(name); return t.shift() if t and t.ok else (0.0, 0.0)

    def frame(self, t: float, show_trackers: bool = True) -> np.ndarray:
        sc, m, xs, ys = self.sc, self.sc.m, self.xs, self.ys
        out = np.zeros((RH, RW, 3), np.float32)
        plasma = 0.5 + 0.5 * np.sin(6 * xs + t * 0.7) * np.cos(5 * ys - t * 0.5)
        out += np.stack([0.03 + 0.10 * plasma, 0.02 + 0.07 * plasma * m["altura"], 0.07 + 0.16 * (1 - plasma)], axis=2)
        out += np.stack([m["bordes"] * 0.7, m["bordes"] * 0.45, m["bordes"] * 0.15], axis=2) * (0.5 + 0.5 * np.sin(t * 2))
        # torre
        hue = (t * 0.07) % 1.0
        top = np.array([0.5 + 0.5 * math.sin(hue * 6.28 + 4.2), 0.5 + 0.5 * math.sin(hue * 6.28 + 2.1), 0.5 + 0.5 * math.sin(hue * 6.28)], np.float32)
        out = out * (1 - m["torre_top"][..., None]) + (np.ones((RH, RW, 3), np.float32) * top * (0.6 + 0.4 * math.sin(t * 1.3))) * m["torre_top"][..., None]
        scan = 0.5 + 0.5 * np.sin(ys * 120 - t * 6)
        front = np.stack([scan * (0.3 + 0.7 * np.sin(xs * 9 + t) ** 2), scan * 0.3, scan * (0.6 + 0.4 * np.cos(xs * 9 - t) ** 2)], axis=2)
        out = out * (1 - m["torre_front"][..., None]) + front * (0.5 + 0.5 * m["altura"][..., None]) * m["torre_front"][..., None]
        # papel: ruido de TV
        noise = cv2.resize(np.random.default_rng(int(t * 12)).random((RH // 8, RW // 8)).astype(np.float32), (RW, RH), interpolation=cv2.INTER_NEAREST)
        out = out * (1 - m["papel"][..., None]) + np.stack([noise] * 3, axis=2) * 0.7 * m["papel"][..., None]
        # circuit + ddj
        if sc.circuit_bbox:
            cx, cy, cw, ch = [v * S for v in sc.circuit_bbox]
            pad = ((np.sin((xs * RW - cx) / max(1, cw) * 50) > 0.6) & (np.sin((ys * RH - cy) / max(1, ch) * 40) > 0.6)).astype(np.float32)
            blink = 0.5 + 0.5 * np.sin(t * 5 + xs * 20)
            out = out * (1 - m["circuit"][..., None]) + np.stack([pad * blink * 0.2, pad * blink * 0.9, pad * blink * 0.6], axis=2) * m["circuit"][..., None]
        out = out * (1 - m["ddj"][..., None]); self.draw_ddj(out, t)
        # teclado
        for i, poly in enumerate(sc.white_keys):
            ph = (i / max(1, len(sc.white_keys)) * 2 - t * 0.6) % 1.0
            col = (0.5 + 0.5 * math.sin(ph * 6.28 + 4.2), 0.5 + 0.5 * math.sin(ph * 6.28 + 2.1), 0.5 + 0.5 * math.sin(ph * 6.28))
            pts = (np.array(poly, np.float32) * S).astype(np.int32).reshape(-1, 1, 2)
            cv2.fillPoly(out, [pts], col); cv2.polylines(out, [pts], True, (0.05, 0.05, 0.05), 1)
        for poly in sc.black_keys:
            v = 0.35 + 0.35 * math.sin(t * 3 + poly[0][0] * 0.02)
            cv2.fillPoly(out, [(np.array(poly, np.float32) * S).astype(np.int32).reshape(-1, 1, 2)], (v, 0.05, 0.1))
        # cocodrilo (sigue al tracker)
        if sc.croc is not None:
            dx, dy = self._shift("croc")
            poly = (sc.croc + np.float32([dx, dy])) * S
            mask = np.zeros((RH, RW), np.uint8); cv2.fillPoly(mask, [poly.astype(np.int32).reshape(-1, 1, 2)], 255); mk = mask.astype(np.float32) / 255
            scales = 0.5 + 0.5 * np.sin(xs * 180 + t * 3) * np.sin(ys * 120 - t * 2)
            croc = np.stack([0.1 + 0.2 * scales, 0.45 + 0.5 * scales, 0.15 * scales], axis=2)
            out = out * (1 - mk[..., None]) + croc * mk[..., None]
            cv2.polylines(out, [poly.astype(np.int32).reshape(-1, 1, 2)], True, (0.2, 1.0, 0.6), 2)
        # Buda: respira en su lugar. Bordes (Canny) con brillo que recorre la figura + textura con pulso cálido + halo
        bx, by, bw, bh = sc.buda_box; dx, dy = self._shift("buda")
        breath = 1.0 + 0.012 * math.sin(t * 1.1)
        Mb = cv2.getRotationMatrix2D((bw / 2, bh / 2), 0, breath)
        tex = cv2.warpAffine(sc.buda_tex, Mb, (bw, bh)); edg = cv2.warpAffine(sc.buda_edges, Mb, (bw, bh))
        yy, xx = np.mgrid[0:bh, 0:bw].astype(np.float32)
        wave = 0.5 + 0.5 * np.sin((yy / bh) * 14 - t * 2.2)                       # luz que sube por los bordes
        hx, hy = sc.buda_head; rr = np.sqrt(((xx - hx) / bw) ** 2 + ((yy - hy) / bh) ** 2)
        halo = np.clip(0.55 - rr * 1.5, 0, 1) ** 1.6 * (0.6 + 0.4 * math.sin(t * 1.1))
        warm = np.stack([0.15, 0.55, 1.0], axis=0).reshape(1, 1, 3).astype(np.float32)
        layer = tex * (0.75 + 0.25 * math.sin(t * 1.1)) + warm * halo[..., None] * 0.6 + np.stack([0.2 * edg * wave, 0.8 * edg * wave, 1.0 * edg * wave], axis=2)
        layer = np.clip(layer, 0, 1)
        X0, Y0 = int((bx + dx) * S), int((by + dy) * S); lw, lh = int(bw * S), int(bh * S)
        small = cv2.resize(layer, (lw, lh)); alpha = cv2.resize(cv2.GaussianBlur(np.ones((bh, bw), np.float32), (0, 0), 2), (lw, lh))
        x0c, y0c = max(0, X0), max(0, Y0); x1c, y1c = min(RW, X0 + lw), min(RH, Y0 + lh)
        if x1c > x0c and y1c > y0c:
            sub = small[y0c - Y0:y1c - Y0, x0c - X0:x1c - X0]; al = alpha[y0c - Y0:y1c - Y0, x0c - X0:x1c - X0][..., None]
            out[y0c:y1c, x0c:x1c] = out[y0c:y1c, x0c:x1c] * (1 - al) + sub * al
        # trackers visibles (trabajo en vivo): caja fina donde el tracker ve el objeto, llevada al proyector
        img = (np.clip(out, 0, 1) * 255).astype(np.uint8)
        img = cv2.resize(img, (W, H), interpolation=cv2.INTER_LINEAR)
        if show_trackers:                      # trabajo en vivo: caja del tracker llevada al proyector (en uint8)
            for name, tr in self.trackers.items():
                if tr.ok:
                    x, y, w, h = tr.bbox_proj; sx, sy = tr.shift()
                    cv2.rectangle(img, (int(x + sx), int(y + sy)), (int(x + w + sx), int(y + h + sy)), (255, 255, 255), 2)
                    cv2.putText(img, f"{name} {tr.kind} dx={sx:+.0f} dy={sy:+.0f}", (int(x + sx), int(y + sy) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        return img

    def draw_ddj(self, out, t):
        (lx, ly, r1), (rx, ry, r2) = self.sc.jogs; r = int((r1 + r2) / 2)
        for (jx, jy), spin in (((lx, ly), t * 1.6), ((rx, ry), -t * 1.3)):
            c = (int(jx * S), int(jy * S)); rr = int(r * S)
            cv2.circle(out, c, rr, (0.9, 0.2, 0.8), 2); cv2.circle(out, c, int(rr * 0.75), (0.3, 0.1, 0.5), 2)
            for k in range(8):
                a = spin + k * math.pi / 4
                cv2.line(out, (int(c[0] + rr * 0.78 * math.cos(a)), int(c[1] + rr * 0.78 * math.sin(a))), (int(c[0] + rr * 0.98 * math.cos(a)), int(c[1] + rr * 0.98 * math.sin(a))), (0.9, 0.9, 1.0), 2)
            for row in range(2):
                for col in range(4):
                    px = int((jx - r * 0.9 + col * r * 0.6) * S); py = int((jy + r * 1.25 + row * r * 0.45) * S)
                    on = 0.25 + 0.75 * (math.sin(t * 4 + col * 1.3 + row * 2 + jx * 0.01) > 0.3)
                    cv2.rectangle(out, (px, py), (px + int(r * 0.5 * S), py + int(r * 0.36 * S)), (0.1 * on, 0.9 * on, 0.9 * on), -1)
        mx = (lx + rx) / 2
        for ddx in (-r * 0.35, r * 0.35):
            x = int((mx + ddx) * S); y0 = int((ly - r * 0.8) * S); y1 = int((ly + r * 0.9) * S)
            cv2.line(out, (x, y0), (x, y1), (0.4, 0.4, 0.4), 2)
            pos = y0 + int((y1 - y0) * (0.5 + 0.45 * math.sin(t * 0.9 + ddx)))
            cv2.rectangle(out, (x - 6, pos - 4), (x + 6, pos + 4), (1.0, 0.8, 0.2), -1)
        y = int((ly + r * 1.3) * S); x0 = int((mx - r * 0.6) * S); x1 = int((mx + r * 0.6) * S)
        cv2.line(out, (x0, y), (x1, y), (0.4, 0.4, 0.4), 2)
        pos = x0 + int((x1 - x0) * (0.5 + 0.5 * math.sin(t * 1.7))); cv2.rectangle(out, (pos - 5, y - 7), (pos + 5, y + 7), (1.0, 0.3, 0.3), -1)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, default=120); p.add_argument("--screen", default="1920x1080")
    p.add_argument("--camera", default="osmo"); p.add_argument("--rotate", type=int, default=270)
    p.add_argument("--no-camera", action="store_true"); p.add_argument("--preview")
    p.add_argument("--track-every", type=int, default=2, help="actualiza los trackers cada N cuadros")
    a = p.parse_args(argv)
    fl = FirstLook()
    print("escena:", json.dumps({"teclas": fl.sc.key_info, "jogs": fl.sc.jogs, "torre_top": fl.sc.torre_top, "croc": fl.sc.croc_box, "buda": fl.sc.buda_box}))
    if a.preview:
        cv2.imwrite(a.preview, fl.frame(3.0, show_trackers=False)); print("preview", a.preview); return 0
    from slscan.capture import PatternWindow, Camera
    win = PatternWindow((W, H), screen=a.screen)
    cam = None if a.no_camera else Camera(a.camera, size="3840x2160", rotate=a.rotate, downscale=2)
    Path("reports").mkdir(exist_ok=True)
    try:
        if cam:
            win.solid(70); time.sleep(1.5); cam.flush(15); fl.init_trackers(cam.read())
            print("trackers:", {k: v.ok for k, v in fl.trackers.items()})
        t0 = time.time(); n = 0; shots = {6: "reports/firstlook3_osmo_a.png", 40: "reports/firstlook3_osmo_b.png"}
        while time.time() - t0 < a.seconds:
            t = time.time() - t0
            win.show(fl.frame(t)); n += 1
            if cam and n % a.track_every == 0:
                f = cam.read(); fl.update_trackers(f)
                for ts, path in list(shots.items()):
                    if t >= ts:
                        cv2.imwrite(path, f); shots.pop(ts)
            if win.wait_key(1) == ord("q"):
                break
        print(f"{n} cuadros en {time.time() - t0:.0f} s ({n / max(1e-6, time.time() - t0):.1f} fps); trackers:",
              {k: (round(v.dx), round(v.dy)) for k, v in fl.trackers.items()})
    finally:
        win.close()
        if cam:
            cam.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

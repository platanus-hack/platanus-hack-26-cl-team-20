"""Banco de trabajo en vivo: proyecta sobre la escena cada paso del análisis (con cursor y texto) para ver
en tiempo real si lo detectado encaja exactamente con lo real. Sin trackers: todo sale del escaneo.

Pasos: foto con luz blanca → bordes → teclado (banda, rectángulo, teclas una por una) → cocodrilo (silueta exacta
por altura del escaneo + color + GrabCut) → torre (caras) → mesa (plano) → Buda → resumen. Cada paso deja una
captura de la Osmo en reports/wb_<paso>.png (privadas) y el resultado en captures/scan2/workbench.json.

    python -m playground.workbench            # en vivo
    python -m playground.workbench --dry      # sin proyector ni cámara (solo imágenes)
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np

import os
SCAN = Path(os.environ.get("SLSCAN_SCAN", "captures/scan5"))
W, H = 1920, 1080


class Bench:
    def __init__(self, live: bool, camera="osmo", rotate=270, screen="1920x1080", pace=1.0):
        self.live = live; self.pace = pace
        self.pov = cv2.imread(str(SCAN / "scene_pov.png")); self.gray = cv2.cvtColor(self.pov, cv2.COLOR_BGR2GRAY)
        self.height = np.load(SCAN / "height_proj.npy"); maps = np.load(SCAN / "proj_to_cam.npz"); self.valid = maps["valid"]
        self.result = {}; self.log = []
        self.win = self.cam = None
        if live:
            from slscan.capture import PatternWindow, Camera
            self.win = PatternWindow((W, H), screen=screen); self.cam = Camera(camera, size="3840x2160", rotate=rotate, downscale=2)
        Path("reports").mkdir(exist_ok=True)

    # ---- mostrar: capa sobre fondo oscuro con texto y cursor
    def show(self, layer: np.ndarray, text: str, cursor=None, hold: float = 2.0, name: str | None = None):
        img = layer.copy()
        cv2.rectangle(img, (0, 0), (W, 46), (0, 0, 0), -1)
        cv2.putText(img, text, (24, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (255, 255, 255), 2)
        if cursor is not None:
            x, y = int(cursor[0]), int(cursor[1])
            cv2.circle(img, (x, y), 18, (0, 255, 255), 3); cv2.line(img, (x - 30, y), (x + 30, y), (0, 255, 255), 2); cv2.line(img, (x, y - 30), (x, y + 30), (0, 255, 255), 2)
        self.log.append(text)
        if self.live:
            self.win.show(img); time.sleep(hold * self.pace)
            if name:
                self.cam.flush(8); cv2.imwrite(f"reports/wb_{name}.png", self.cam.read())
        if name:
            cv2.imwrite(str(SCAN / f"wb_{name}.png"), img)

    def dark(self, alpha=0.25):
        return (self.pov * alpha).astype(np.uint8)

    # ---- pasos
    def step_photo(self):
        self.show(self.pov, "1. Foto con luz blanca vista desde el proyector (remap del escaneo)", hold=3, name="foto")
        edges = cv2.Canny(cv2.GaussianBlur(self.gray, (0, 0), 1.2), 50, 130)
        layer = self.dark(); layer[edges > 0] = (255, 255, 255)
        self.show(layer, "2. Bordes (Canny) de la foto: deben caer sobre los bordes reales", hold=3, name="bordes")

    def step_keyboard(self):
        g = self.gray
        sob = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)); edges = (sob > 60).astype(np.float32)
        score = cv2.blur(edges, (61, 21)); band = (score > np.percentile(score, 97)).astype(np.uint8)
        n, lab, st, _ = cv2.connectedComponentsWithStats(cv2.morphologyEx(band * 255, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8)))
        k = int(np.argsort(-st[1:, 4])[0] + 1); kx, ky, kw, kh = [int(v) for v in st[k, :4]]
        layer = self.dark(); layer[lab == k] = (0, 120, 0)
        self.show(layer, "3. Teclado: banda con bordes verticales densos (alternancia blanco/negro)", cursor=(kx + kw / 2, ky + kh / 2), hold=2.5, name="banda")
        # extensión horizontal y largo real de las teclas blancas
        row = g[ky:ky + kh, :]; prof = (row[kh // 2:, :] > 140).astype(np.float32).mean(axis=0)
        dens = cv2.blur(prof.reshape(1, -1), (1, 101)).ravel(); cols = np.nonzero(dens > 0.3)[0]; cols = cols[(cols > kx - 60) & (cols < kx + kw + 60)]
        kx0, kx1 = (int(cols.min()), int(cols.max())) if len(cols) else (kx, kx + kw)
        y_end = ky + kh
        while y_end < H - 1 and (g[y_end, kx0:kx1] > 140).mean() > 0.35:
            y_end += 1
        y_end = min(y_end + 2, ky + 130)
        ys, xs = np.nonzero(g[ky:y_end, kx0:kx1] > 140)
        rect = cv2.minAreaRect(np.column_stack([xs + kx0, ys + ky]).astype(np.float32)); box = cv2.boxPoints(rect)
        layer = self.dark(); cv2.polylines(layer, [box.astype(np.int32).reshape(-1, 1, 2)], True, (0, 255, 0), 3)
        self.show(layer, f"4. Rectangulo del teclado: {int(rect[1][0])}x{int(rect[1][1])} px, angulo {rect[2]:.1f}", hold=2.5, name="rect_teclado")
        # teclas una por una: blancas por periodicidad, con componentes reales donde se ven
        p = prof[kx0:kx1] - prof[kx0:kx1].mean(); F = np.abs(np.fft.rfft(p)); F[:5] = 0; kk = int(np.argmax(F)); pitch = (kx1 - kx0) / max(1, kk)
        if not (18 < pitch < 70):
            pitch = 34.0
        off = max(range(int(pitch)), key=lambda o: sum(prof[kx0 + int(o + i * pitch):kx0 + int(o + i * pitch + pitch * 0.55)].mean() for i in range(int((kx1 - kx0) / pitch)) if kx0 + o + (i + 0.55) * pitch < kx1))
        whites = []; layer = self.dark()
        i = 0
        while kx0 + off + (i + 0.9) * pitch <= kx1 + 4:
            x = int(kx0 + off + i * pitch); w = int(pitch * 0.9)
            seen = float((g[ky + kh // 2:y_end, x:x + w] > 140).mean())
            whites.append({"i": i, "x": x, "y": ky, "w": w, "h": y_end - ky, "visible": round(seen, 2)})
            col = (0, 255, 0) if seen > 0.5 else (0, 160, 255)         # naranja = tapada (cocodrilo)
            cv2.rectangle(layer, (x, ky), (x + w, y_end), col, 2); cv2.putText(layer, str(i + 1), (x + 4, y_end - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
            self.show(layer, f"5. Tecla blanca {i + 1}: x={x} ancho={w} {'visible' if seen > 0.5 else 'tapada'}", cursor=(x + w / 2, ky + (y_end - ky) / 2), hold=0.25)
            i += 1
        top = g[ky:ky + kh // 2, kx0:kx1]; bk = (top < 75).astype(np.uint8); n2, lab2, st2, _ = cv2.connectedComponentsWithStats(bk)
        blacks = sorted([(int(st2[j, 0] + kx0), ky, int(st2[j, 2]), int(st2[j, 3])) for j in range(1, n2) if st2[j, 3] > kh * 0.25 and 4 < st2[j, 2] < pitch])
        for (x, y, w, h) in blacks:
            cv2.rectangle(layer, (x, y), (x + w, y + int((y_end - ky) * 0.6)), (0, 0, 255), 2)
        self.show(layer, f"5. Teclado: {len(whites)} blancas (paso {pitch:.1f} px), {len(blacks)} negras", hold=3, name="teclas")
        self.result["teclado"] = {"bbox": [kx0, ky, kx1 - kx0, y_end - ky], "rect": [list(rect[0]), list(rect[1]), rect[2]], "pitch_px": round(pitch, 2),
                                  "teclas_blancas": whites, "teclas_negras": blacks}
        return kx0, kx1, ky, y_end

    def step_croc(self, kb):
        kx0, kx1, ky, y_end = kb
        hsv = cv2.cvtColor(self.pov, cv2.COLOR_BGR2HSV); sat, val = hsv[..., 1], hsv[..., 2]
        # 1) altura del escaneo: el cocodrilo sobresale del plano local (mediana de la franja)
        strip = np.zeros((H, W), bool); strip[max(0, ky - 170):ky - 14, max(0, kx0 - 40):min(W, kx1 + 40)] = True
        hz = self.height[strip]; thr = np.median(hz) + 1.0 * np.std(hz)
        high = (self.height > thr) & strip
        layer = self.dark(); layer[strip] = (40, 40, 40); layer[high] = (0, 200, 255)
        self.show(layer, "6. Cocodrilo: lo que sobresale del plano segun el escaneo (altura por paralaje)", hold=2.5, name="croc_altura")
        # 2) color: claro poco saturado
        colr = (sat < 120) & (val > 95) & (val < 245) & strip
        both = cv2.morphologyEx((high & colr).astype(np.uint8) | cv2.erode(colr.astype(np.uint8), np.ones((7, 7), np.uint8)), cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
        n, lab, st, _ = cv2.connectedComponentsWithStats(both)
        cands = [i for i in range(1, n) if st[i, 4] > 3000 and st[i, 2] > 1.6 * st[i, 3]]
        if not cands:
            self.show(layer, "6. Cocodrilo: no encontre un candidato", hold=2); return
        i = max(cands, key=lambda j: st[j, 4]); init = lab == i
        layer = self.dark(); layer[init] = (0, 255, 120)
        self.show(layer, "6. Cocodrilo: candidato (altura + color)", hold=2)
        # 3) GrabCut sobre la foto para el contorno exacto
        gc = np.full((H, W), cv2.GC_BGD, np.uint8); x, y, w, h = [int(v) for v in st[i, :4]]; pad = 30
        gc[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad] = cv2.GC_PR_BGD
        gc[cv2.dilate(init.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0] = cv2.GC_PR_FGD
        gc[cv2.erode(init.astype(np.uint8), np.ones((13, 13), np.uint8)) > 0] = cv2.GC_FGD
        bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        try:
            cv2.grabCut(self.pov, gc, None, bgd, fgd, 5, cv2.GC_INIT_WITH_MASK)
            fg = ((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)) & strip
        except cv2.error:
            fg = init
        fg = fg & (val > 70) & (sat < 140)                      # el cuerpo es claro: fuera lo negro del teclado
        fg = cv2.morphologyEx(fg.astype(np.uint8), cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        n2, lab2, st2, _ = cv2.connectedComponentsWithStats(fg)
        if n2 > 1:
            j = int(np.argsort(-st2[1:, 4])[0] + 1); fg = (lab2 == j).astype(np.uint8)
        cnt = max(cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
        cnt = cv2.approxPolyDP(cnt, 1.5, True).reshape(-1, 2)
        layer = self.dark(); layer[fg > 0] = self.pov[fg > 0]
        cv2.polylines(layer, [cnt.reshape(-1, 1, 2)], True, (0, 255, 255), 2)
        self.show(layer, f"6. Cocodrilo: silueta exacta (GrabCut sobre altura+color), {len(cnt)} vertices", cursor=tuple(cnt.mean(axis=0)), hold=3, name="croc")
        self.result["cocodrilo"] = {"contour": cnt.tolist(), "bbox": [int(v) for v in cv2.boundingRect(cnt)]}

    def step_tower(self):
        hsv = cv2.cvtColor(self.pov, cv2.COLOR_BGR2HSV); sat, val = hsv[..., 1], hsv[..., 2]
        white = (sat < 60) & (val > 170) & self.valid; left = np.zeros_like(white); left[150:, 120: W // 3] = True
        mm = cv2.morphologyEx((white & left).astype(np.uint8) * 255, cv2.MORPH_OPEN, np.ones((15, 15), np.uint8)); mm = cv2.morphologyEx(mm, cv2.MORPH_CLOSE, np.ones((31, 31), np.uint8))
        n, lab, st, _ = cv2.connectedComponentsWithStats(mm)
        cands = [i for i in range(1, n) if st[i, 3] > 350 and st[i, 2] > 120]
        if not cands:
            self.show(self.dark(), "7. Torre: no encontrada", hold=2); return
        i = max(cands, key=lambda j: st[j, 4]); mask = lab == i
        cnt = max(cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
        poly = cv2.approxPolyDP(cnt, 6, True).reshape(-1, 2)
        layer = self.dark(); layer[mask] = (200, 200, 60); cv2.polylines(layer, [poly.reshape(-1, 1, 2)], True, (255, 255, 0), 3)
        self.show(layer, f"7. Torre: region blanca grande, contorno de {len(poly)} vertices", hold=2.5, name="torre")
        # caras: borde horizontal mas fuerte dentro de la mascara (altura + brillo)
        x, y, w, h = [int(v) for v in st[i, :4]]
        hgt = cv2.GaussianBlur(self.height[y:y + h, x:x + w], (0, 0), 3); gg = cv2.GaussianBlur(self.gray[y:y + h, x:x + w].astype(np.float32), (0, 0), 3)
        prof = np.abs(cv2.Sobel(hgt, cv2.CV_32F, 0, 1, ksize=5)).mean(axis=1) / (abs(hgt).max() + 1e-6) + np.abs(cv2.Sobel(gg, cv2.CV_32F, 0, 1, ksize=5)).mean(axis=1) / 255
        prof[: int(h * 0.05)] = 0; prof[int(h * 0.45):] = 0; split = y + int(np.argmax(prof))
        top = mask.copy(); top[split:, :] = False; front = mask.copy(); front[:split, :] = False
        layer = self.dark(); layer[top] = (60, 200, 255); layer[front] = (255, 120, 60); cv2.line(layer, (x, split), (x + w, split), (255, 255, 255), 2)
        self.show(layer, f"7. Torre: cara superior (celeste) y frontal (naranja), corte en y={split}", cursor=(x + w / 2, split), hold=3, name="torre_caras")
        self.result["torre"] = {"bbox": [x, y, w, h], "contour": poly.tolist(), "split_y": split}

    def step_table(self):
        # plano dominante: altura ~0 (inliers), zona inferior/central sin objetos
        flat = (np.abs(self.height) < 2.0) & self.valid
        flat = cv2.morphologyEx(flat.astype(np.uint8), cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
        n, lab, st, _ = cv2.connectedComponentsWithStats(flat)
        layer = self.dark()
        if n > 1:
            i = int(np.argsort(-st[1:, 4])[0] + 1); layer[lab == i] = (90, 60, 160)
            self.result["mesa"] = {"area_px": int(st[i, 4]), "bbox": [int(v) for v in st[i, :4]]}
        self.show(layer, "8. Mesa / plano dominante del escaneo (altura ~0): lo que no sobresale", hold=3, name="mesa")

    def step_buda(self):
        hsv = cv2.cvtColor(self.pov, cv2.COLOR_BGR2HSV); hval, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        warm = ((hval < 25) | (hval > 160)) & (sat > 70) & (val > 60) & self.valid
        mm = cv2.morphologyEx(warm.astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((41, 41), np.uint8)); n, lab, st, _ = cv2.connectedComponentsWithStats(mm)
        i = int(np.argsort(-st[1:, 4])[0] + 1); mask = lab == i
        cnt = max(cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
        quad = cv2.boxPoints(cv2.minAreaRect(cnt))
        layer = self.dark(); layer[mask] = self.pov[mask]; cv2.polylines(layer, [quad.astype(np.int32).reshape(-1, 1, 2)], True, (0, 140, 255), 3)
        self.show(layer, "9. Buda: cuadro (tonos calidos) y su cuadrilatero", cursor=tuple(quad.mean(axis=0)), hold=3, name="buda")
        self.result["buda"] = {"quad": quad.tolist(), "bbox": [int(v) for v in st[i, :4]]}

    def run(self):
        try:
            self.step_photo()
            kb = self.step_keyboard()
            self.step_croc(kb)
            self.step_tower()
            self.step_table()
            self.step_buda()
            # resumen: todo junto sobre fondo negro
            layer = np.zeros((H, W, 3), np.uint8)
            r = self.result
            for k in r.get("teclado", {}).get("teclas_blancas", []):
                cv2.rectangle(layer, (k["x"], k["y"]), (k["x"] + k["w"], k["y"] + k["h"]), (0, 255, 0), 2)
            for (x, y, w, h) in r.get("teclado", {}).get("teclas_negras", []):
                cv2.rectangle(layer, (x, y), (x + w, y + int(r["teclado"]["bbox"][3] * 0.6)), (0, 0, 255), 2)
            if "cocodrilo" in r: cv2.polylines(layer, [np.array(r["cocodrilo"]["contour"], np.int32).reshape(-1, 1, 2)], True, (0, 255, 255), 3)
            if "torre" in r: cv2.polylines(layer, [np.array(r["torre"]["contour"], np.int32).reshape(-1, 1, 2)], True, (255, 255, 0), 3)
            if "buda" in r: cv2.polylines(layer, [np.array(r["buda"]["quad"], np.int32).reshape(-1, 1, 2)], True, (0, 140, 255), 3)
            self.show(layer, "10. Resumen: contornos detectados proyectados sobre lo real (deben coincidir)", hold=8, name="resumen")
            json.dump(self.result, open(SCAN / "workbench.json", "w"), indent=1)
        finally:
            if self.win: self.win.close()
            if self.cam: self.cam.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(); p.add_argument("--dry", action="store_true"); p.add_argument("--pace", type=float, default=1.0)
    p.add_argument("--loop", type=int, default=1)
    a = p.parse_args(argv)
    for _ in range(a.loop):
        b = Bench(live=not a.dry, pace=a.pace); b.run()
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk not in ("teclas_blancas", "teclas_negras", "contour", "quad")} for k, v in b.result.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

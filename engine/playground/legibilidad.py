"""Hoja de legibilidad proyectada sobre el mousepad: grilla 16x9 de textos con distintos tamaños y contrastes,
rectificada al plano del pad (pad_quad.json de pad_adjust/pad_checker). Max va clicando las celdas en una ventanita
de su monitor, de la menos legible a la más legible; cada clic queda registrado con orden, celda, tamaño y contraste.

    python -m playground.legibilidad captures/dale-XXXX [--cols 16 --rows 9] [--rot 180] [--seed 1]

Teclas en la ventanita: clic = siguiente celda · u = deshacer · n = nueva hoja (otro sorteo) · q = terminar.
Salidas en <scan>/legibilidad/: hoja_<n>.png (proyector), hoja_<n>_max.png (como la ve Max), clics_<n>.json y un
resumen.json con el orden de cada celda y sus parámetros. Nada de esto toca los displays ni usa fullscreen.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from . import pad_checker as pc

W, H = 1920, 1080
SIZES = [6, 8, 10, 13, 17, 22, 30, 42]                          # alto del texto en milésimas del alto del pad
CONTRASTS = [(255, 0), (170, 0), (110, 0), (70, 0), (255, 70), (255, 140), (0, 255), (90, 255), (170, 255)]  # (texto, fondo)


def max_to_pad(rot: int, aspect: float):
    """Coordenadas 'como las ve Max' (hoja derecha para él) → plano del pad (TL/TR/BR/BL de la cámara)."""
    if rot % 360 == 0:
        return np.eye(3)
    if rot % 360 == 180:
        return np.array([[-1, 0, aspect], [0, -1, 1], [0, 0, 1]], np.float64)     # en unidades normalizadas (w=aspect, h=1)
    raise SystemExit("--rot solo 0 o 180 por ahora")


def largest_rect(mask: np.ndarray) -> tuple[int, int, int, int]:
    """Rectángulo alineado más grande dentro de una máscara binaria (histograma por filas). (x, y, w, h)."""
    h, w = mask.shape
    heights = np.zeros(w, int); best = (0, 0, 0, 0); best_a = 0
    for y in range(h):
        heights = np.where(mask[y] > 0, heights + 1, 0)
        stack = []
        for x in range(w + 1):
            cur = heights[x] if x < w else 0
            start = x
            while stack and stack[-1][1] >= cur:
                sx, sh = stack.pop()
                area = sh * (x - sx)
                if area > best_a:
                    best_a = area; best = (sx, y - sh + 1, x - sx, sh)
                start = sx
            stack.append((start, cur))
    return best


BANDS = [(255, 0), (0, 255), (110, 0), (255, 140)]              # (texto, fondo) por franja: todas en cada celda
CELL_SIZES = [28, 20, 14, 10, 7, 5]                            # alto del texto en px de hoja (de grande a chico), todos en cada celda


def plan_cells(cols: int, rows: int, seed: int) -> list[dict]:
    """Todas las celdas iguales (mismo texto, tamaños y colores): lo único que cambia es la posición, para rankear foco."""
    return [{"i": i, "col": i % cols, "row": i // cols, "code": f"{chr(65 + i // cols)}{i % cols + 1}"} for i in range(cols * rows)]


def render_sheet(cells: list[dict], cols: int, rows: int, sheet_w: int, sheet_h: int, clicked: dict[int, int]) -> np.ndarray:
    """Hoja en coordenadas de Max (px). Cada celda: 4 franjas de contraste, cada una con 'Hg8' en todos los tamaños
    (de grande a chico); el código de la celda chico en la esquina. Las ya clicadas llevan su orden."""
    img = np.zeros((sheet_h, sheet_w, 3), np.uint8)
    cw, ch = sheet_w / cols, sheet_h / rows
    bh = (ch - 4) / len(BANDS)
    for c in cells:
        x0, y0 = int(c["col"] * cw), int(c["row"] * ch); x1, y1 = int((c["col"] + 1) * cw) - 3, int((c["row"] + 1) * ch) - 3
        for b, (fg, bg) in enumerate(BANDS):
            by0, by1 = int(y0 + b * bh), int(y0 + (b + 1) * bh)
            img[by0:by1, x0:x1] = bg
            x = x0 + 6
            base = int(by1 - max(2, bh * 0.22))
            for s in CELL_SIZES:
                px = min(s, int(bh * 0.72))
                scale = px / 22.0; th = max(1, int(round(scale * 1.5)))
                txt = "Hg8"
                (tw, _), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, scale, th)
                if x + tw > x1 - 4:
                    break
                cv2.putText(img, txt, (x, base), cv2.FONT_HERSHEY_SIMPLEX, scale, (fg,) * 3, th, cv2.LINE_AA)
                x += tw + max(6, px // 2)
        cv2.putText(img, c["code"], (x1 - 34, y0 + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1, cv2.LINE_AA)
        if c["i"] in clicked:
            cv2.rectangle(img, (x0, y0), (x1, y1), (0, 200, 255), 3)
            cv2.putText(img, str(clicked[c["i"]]), (x0 + 6, y0 + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2, cv2.LINE_AA)
    return img


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("scan"); p.add_argument("--cols", type=int, default=6); p.add_argument("--rows", type=int, default=9)
    p.add_argument("--rot", type=int, default=180, help="180: Max sentado frente a la cámara (cabeza arriba de la foto)")
    p.add_argument("--seed", type=int, default=1); p.add_argument("--runtime", default="http://127.0.0.1:8790")
    p.add_argument("--margin", type=float, default=0.02)
    p.add_argument("--dry", action="store_true", help="solo renderizar la hoja (PNG), sin proyectar ni ventana")
    a = p.parse_args(argv)
    scan = Path(a.scan); out = scan / "legibilidad"; out.mkdir(exist_ok=True)
    # superficie rectificada (playground.superficie): lienzo de cuadrados reales sobre la mesa → proyector
    sup = json.loads((scan / "superficie.json").read_text())
    lienzo_w, lienzo_h = (int(v) for v in sup["lienzo_px"])
    Hlp = np.array(sup["H_lienzo_to_proj"], np.float64)
    aspect = float(sup["aspect_pad"])
    m = a.margin
    sheet_h = 1400; sheet_w = int(round(sheet_h * lienzo_w / lienzo_h))
    # hoja (px, derecha para Max) → lienzo (px): margen y, si Max está al otro lado de la cámara, giro de 180°
    S = np.array([[lienzo_w * (1 - 2 * m) / sheet_w, 0, m * lienzo_w], [0, lienzo_h * (1 - 2 * m) / sheet_h, m * lienzo_h], [0, 0, 1]], np.float64)
    if a.rot % 360 == 180:
        R = np.array([[-1, 0, lienzo_w], [0, -1, lienzo_h], [0, 0, 1]], np.float64)
    elif a.rot % 360 == 0:
        R = np.eye(3)
    else:
        raise SystemExit("--rot solo 0 o 180")
    Hsheet = Hlp @ R @ S
    # px del proyector por px de hoja (para registrar el tamaño proyectado de cada texto)
    c0 = cv2.perspectiveTransform(np.array([[[sheet_w / 2, sheet_h / 2]], [[sheet_w / 2, sheet_h / 2 + 100]]], np.float64), Hsheet).reshape(2, 2)
    proj_px_per_sheet_px = float(np.linalg.norm(c0[1] - c0[0]) / 100.0)
    print(json.dumps({"aspect": aspect, "lienzo_px": [lienzo_w, lienzo_h], "rot": a.rot,
                      "hoja_px": [sheet_w, sheet_h], "proj_px_por_px_hoja": round(proj_px_per_sheet_px, 3),
                      "alto_texto_px_proyector": {s: round(s * proj_px_per_sheet_px, 1) for s in CELL_SIZES}}), flush=True)
    if a.dry:
        cells = plan_cells(a.cols, a.rows, a.seed)
        sheet = render_sheet(cells, a.cols, a.rows, sheet_w, sheet_h, {})
        cv2.imwrite(str(out / "hoja_dry_max.png"), sheet)
        cv2.imwrite(str(out / "hoja_dry_proj.png"), cv2.warpPerspective(sheet, Hsheet, (W, H), flags=cv2.INTER_AREA))
        print(json.dumps({"dry": True, "hoja": str(out / "hoja_dry_max.png"), "proyector": str(out / "hoja_dry_proj.png")}), flush=True)
        return 0
    use_rt = pc.runtime_alive(a.runtime)
    win = None
    if not use_rt:
        from slscan.capture import PatternWindow
        win = PatternWindow((W, H), screen="1920x1080", name="legibilidad")

    state = {"n": 0, "cells": [], "clicks": [], "seed": a.seed}

    def new_sheet(seed: int):
        state["n"] += 1; state["seed"] = seed
        state["cells"] = plan_cells(a.cols, a.rows, seed); state["clicks"] = []
        state["t0"] = time.time()

    def clicked_map():
        return {c["i"]: k + 1 for k, c in enumerate(state["clicks"])}

    def project():
        sheet = render_sheet(state["cells"], a.cols, a.rows, sheet_w, sheet_h, clicked_map())
        proj = cv2.warpPerspective(sheet, Hsheet, (W, H), flags=cv2.INTER_AREA)
        path = out / f"hoja_{state['n']}.png"
        cv2.imwrite(str(path), proj)
        if use_rt:
            try:
                pc.runtime_cmd(a.runtime, "overlay", path=str(path.resolve()), seconds=3600)
            except Exception as e:  # noqa: BLE001
                print("overlay falló:", e, flush=True)
        else:
            win.show(proj)
        return sheet

    def save():
        cells = state["cells"]
        rec = {"hoja": state["n"], "seed": state["seed"], "cols": a.cols, "rows": a.rows, "aspect": aspect,
               "proj_px_por_px_hoja": proj_px_per_sheet_px, "hoja_px": [sheet_w, sheet_h],
               "tamanos_px_hoja": CELL_SIZES, "tamanos_px_proyector": [round(s * proj_px_per_sheet_px, 1) for s in CELL_SIZES],
               "franjas_texto_fondo": BANDS,
               "clics": [{"orden": k + 1, **cells[c["i"]], "t": round(c["t"] - state["t0"], 2)} for k, c in enumerate(state["clicks"])],
               "sin_clic": [c["code"] for c in cells if c["i"] not in clicked_map()]}
        (out / f"clics_{state['n']}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False))
        return rec

    new_sheet(a.seed)
    sheet = project()
    cv2.imwrite(str(out / f"hoja_{state['n']}_max.png"), sheet)
    name = "legibilidad: clic = celda (de menos a mas legible) · u deshacer · n nueva hoja · q terminar"
    cv2.namedWindow(name, cv2.WINDOW_AUTOSIZE); cv2.moveWindow(name, 60, 60)
    view_w = 1500; view_h = int(sheet_h * view_w / sheet_w)

    def on_mouse(ev, x, y, flags, _):
        if ev != cv2.EVENT_LBUTTONDOWN:
            return
        col = int(x / view_w * a.cols); row = int(y / view_h * a.rows)
        if not (0 <= col < a.cols and 0 <= row < a.rows):
            return
        i = row * a.cols + col
        if i in clicked_map():
            return
        state["clicks"].append({"i": i, "t": time.time()})
        c = state["cells"][i]
        print(json.dumps({"clic": len(state["clicks"]), "celda": c["code"]}), flush=True)
        save(); state["dirty"] = True

    cv2.setMouseCallback(name, on_mouse)
    state["dirty"] = False
    try:
        while True:
            if state["dirty"]:
                state["dirty"] = False; sheet = project()
            view = cv2.resize(sheet, (view_w, view_h), interpolation=cv2.INTER_AREA)
            cv2.putText(view, f"hoja {state['n']} · clics {len(state['clicks'])}/{a.cols * a.rows}", (10, view_h - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2, cv2.LINE_AA)
            cv2.imshow(name, view)
            k = cv2.waitKey(50)
            if k == -1:
                continue
            ch = chr(k) if 0 <= k < 256 else ""
            if ch == "u" and state["clicks"]:
                state["clicks"].pop(); save(); sheet = project()
            elif ch == "n":
                save(); new_sheet(state["seed"] + 1); sheet = project(); cv2.imwrite(str(out / f"hoja_{state['n']}_max.png"), sheet)
            elif ch == "q" or k == 27:
                rec = save(); print(json.dumps({"guardado": str(out / f"clics_{state['n']}.json"), "clics": len(rec["clics"])}), flush=True)
                break
    finally:
        cv2.destroyAllWindows()
        if win is not None:
            win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

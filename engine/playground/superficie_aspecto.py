"""Proporción real (ancho/alto) del rectángulo de la superficie calibrado a mano, usando el plano del mousepad.

    python -m playground.superficie_aspecto captures/dale-XXXX [--aspect-pad 2.3]

Las 4 esquinas de `superficie.json` (px del proyector) se llevan al plano del pad con la homografía de
`pad_quad.json` (pad = rectángulo de proporción conocida), ahí se mide ancho y alto del rectángulo de Max (promedio de
lados opuestos) y se rehace el lienzo con esa proporción (ancho fijo, alto nuevo), la homografía lienzo→proyector y el
damero de cuadrados. Guarda `aspect_rect` y `rect_en_pad` en superficie.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

W, H = 1920, 1080


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("scan"); p.add_argument("--aspect-pad", type=float, default=None); p.add_argument("--cells", type=int, default=None)
    a = p.parse_args(argv)
    scan = Path(a.scan)
    sup = json.loads((scan / "superficie.json").read_text()); pad = json.loads((scan / "pad_quad.json").read_text())
    aspect_pad = float(a.aspect_pad or sup.get("aspect_pad") or pad.get("aspect_w_over_h") or 2.3)
    quad_pad = np.array(pad.get("quad_proj_adjusted") or pad["quad_proj_tl_tr_br_bl"], np.float32)
    pad_rect = np.array([[0, 0], [aspect_pad, 0], [aspect_pad, 1], [0, 1]], np.float32)
    Hpp = cv2.getPerspectiveTransform(pad_rect, quad_pad); Hinv = np.linalg.inv(Hpp.astype(np.float64))
    corners = np.array(sup["rect_proj_tl_tr_br_bl"], np.float64)
    plane = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), Hinv).reshape(4, 2)
    top, right, bottom, left = (np.linalg.norm(plane[1] - plane[0]), np.linalg.norm(plane[2] - plane[1]),
                                np.linalg.norm(plane[3] - plane[2]), np.linalg.norm(plane[0] - plane[3]))
    width, height = (top + bottom) / 2, (left + right) / 2
    aspect = float(width / height)
    # qué tan rectángulo quedó: diferencia de lados opuestos y ángulos
    def ang(p0, p1, p2):
        v1, v2 = p0 - p1, p2 - p1
        return float(np.degrees(np.arccos(np.clip(v1 @ v2 / (np.linalg.norm(v1) * np.linalg.norm(v2)), -1, 1))))
    angles = [ang(plane[(i - 1) % 4], plane[i], plane[(i + 1) % 4]) for i in range(4)]
    lw = int(sup["lienzo_px"][0]); lh = int(round(lw / aspect)); cells = int(a.cells or sup.get("cuadrados_alto", 9))
    src = np.array([[0, 0], [lw, 0], [lw, lh], [0, lh]], np.float32)
    Hlp = cv2.getPerspectiveTransform(src, corners.astype(np.float32)).astype(np.float64)
    cell = lh / cells
    yy, xx = np.mgrid[0:lh, 0:lw]
    chk = np.zeros((lh, lw, 3), np.uint8); chk[((xx // cell).astype(int) + (yy // cell).astype(int)) % 2 == 0] = 255
    cv2.rectangle(chk, (0, 0), (lw - 1, lh - 1), (0, 200, 255), 8)
    cv2.imwrite(str(scan / "superficie_checker.png"), cv2.warpPerspective(chk, Hlp, (W, H), flags=cv2.INTER_AREA))
    sup.update({"aspect_rect": round(aspect, 4), "rect_en_pad": {"ancho": round(width, 4), "alto": round(height, 4), "lados": [round(v, 4) for v in (top, right, bottom, left)],
                                                                 "angulos_deg": [round(v, 1) for v in angles]},
                "lienzo_px": [lw, lh], "H_lienzo_to_proj": Hlp.tolist(), "cuadrados_alto": cells, "cuadrado_lienzo_px": round(cell, 1),
                "aspecto_actualizado": time.strftime("%Y-%m-%d %H:%M:%S")})
    (scan / "superficie.json").write_text(json.dumps(sup, indent=1))
    print(json.dumps({"aspect_rect": sup["aspect_rect"], "rect_en_pad": sup["rect_en_pad"], "lienzo_px": sup["lienzo_px"]}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

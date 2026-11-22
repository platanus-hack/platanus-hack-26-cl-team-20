"""Medir una lámina nueva: `uv run python -m percepcion.medir_lamina foto-cenital-mm.jpg [salida.png]`.

Imprime las manchas de color dentro de la lámina en coordenadas (u, v) y dibuja la plantilla vigente encima, para
copiar los números a `plantilla_lamina.json` sin programar.
"""

from __future__ import annotations

import sys

import cv2
import numpy as np

from .lamina import manchas_en_lamina, planetas_en_lamina, ubicar_laminas


def main() -> None:
    img = cv2.imread(sys.argv[1])
    if img is None:
        sys.exit(f"No pude leer {sys.argv[1]}")
    laminas = ubicar_laminas(img)
    if not laminas:
        sys.exit("No encontré ninguna lámina (rectángulo oscuro de 12 a 26 cm).")
    for n, lam in enumerate(laminas):
        print(f"Lámina {n}: centro {lam.centro_mm} mm, {lam.tam_mm[0]:.0f}×{lam.tam_mm[1]:.0f} mm, giro {lam.angulo_deg}°")
        for m in manchas_en_lamina(img, lam):
            print(f"  mancha uv=({m['uv'][0]:.3f}, {m['uv'][1]:.3f})  r={m['r']:.3f}")
    if len(sys.argv) > 2:
        out = img.copy()
        for lam in laminas:
            cv2.polylines(out, [np.array(lam.esquinas_mm, np.int32)], True, (0, 255, 0), 1)
            for pid, d in planetas_en_lamina(lam).items():
                c = tuple(int(round(v)) for v in d["centro_mm"])
                cv2.circle(out, c, max(2, int(d["radio_mm"])), (0, 255, 0), 1)
                cv2.putText(out, pid[:3], (c[0] - 6, c[1] - int(d["radio_mm"]) - 2), cv2.FONT_HERSHEY_PLAIN, 0.6, (255, 255, 255), 1)
        cv2.imwrite(sys.argv[2], out)
        print(f"Dibujo en {sys.argv[2]}")


if __name__ == "__main__":
    main()

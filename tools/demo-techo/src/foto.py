"""Foto de evidencia: (promedio de cuadros − referencia negra de la calibración), estirada a 8 bits, en color falso.
uso: python -I foto.py <avg16.png> <ref_negro16.png> <salida.jpg>"""
import sys

import cv2
import numpy as np

a = cv2.imread(sys.argv[1], cv2.IMREAD_UNCHANGED).astype(np.float32) / 64
n = cv2.imread(sys.argv[2], cv2.IMREAD_UNCHANGED).astype(np.float32) / 64
w = cv2.imread(sys.argv[2].replace("negro", "blanco"), cv2.IMREAD_UNCHANGED).astype(np.float32) / 64
mask = cv2.erode(((w - n) > 1.0).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0  # área que el proyector ilumina
d = cv2.GaussianBlur(a - n, (0, 0), 1.0)
lo, hi = np.percentile(d[mask], 2), np.percentile(d[mask], 99.8)
d[~mask] = lo + (d[~mask] - lo) * 0.25  # fuera del área proyectada, atenuado
g = np.clip((d - lo) / max(hi - lo, 1e-3) * 255, 0, 255).astype(np.uint8)
cv2.imwrite(sys.argv[3], cv2.applyColorMap(g, cv2.COLORMAP_INFERNO), [cv2.IMWRITE_JPEG_QUALITY, 88])
print(f"{sys.argv[3]}: rango útil {hi - lo:.2f} niveles")

"""Exporta las fotos de prueba y su verdad para el port a Rust (crate pcx-escena-escritorio).

    uv run python -m percepcion.exportar_fixtures <carpeta>

Son las mismas escenas que usan los tests de Python: libreta sola con tres luces, doble página, girada, mesa vacía,
foto real de la mesa con una libreta pegada, antes y después de escribir un renglón, mano encima y página con luz
proyectada.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

from . import sintetico as s

PRIMERAS = ["como le pongo a", "este endpoint?"]
TERCERA = ["/v1/cobros  o  /pagos"]
RAIZ = Path(__file__).resolve().parents[3]


def exportar(destino: Path):
    destino.mkdir(parents=True, exist_ok=True)
    verdad = {}

    def guardar(nombre, img, **datos):
        cv2.imwrite(str(destino / f"{nombre}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        verdad[nombre] = {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in datos.items()}

    for sem in (0, 1, 2):
        f = s.foto(PRIMERAS, semilla=sem)
        guardar(f"simple_{sem}", f["img"], quad=f["quad"], paginas=1)
    f = s.foto(PRIMERAS, doble_pagina=True, lineas_izq=["notas viejas", "deploy viernes"])
    guardar("doble", f["img"], quad=f["quad"], paginas=2)
    f = s.foto(PRIMERAS, lado=1)
    guardar("girada", f["img"], quad=np.roll(f["quad"], 1, axis=0), girar=3)
    guardar("vacia", s.luz(s.mesa(semilla=3), 3))
    mesa = cv2.imread(str(RAIZ / "data/mesa/2026-11-21/mesa-cenital-mm-1.jpg"))
    hoja = s.pagina()
    s.escribir(hoja, PRIMERAS)
    quad = np.float32([[478, 64], [624, 74], [614, 284], [468, 276]])
    guardar("mesa_real", s.poner(mesa, hoja, quad), quad=quad, cerca_de=[545, 175], area_min=0.01)
    a = s.foto(PRIMERAS, semilla=5)
    d = s.foto(PRIMERAS + TERCERA, semilla=6)
    guardar("antes", a["img"], quad=a["quad"])
    guardar("despues", d["img"], quad=d["quad"], nuevo=d["cajas"][2])
    guardar("mano", s.foto(PRIMERAS + TERCERA, semilla=12, mano=True)["img"])
    da = s.foto(PRIMERAS, doble_pagina=True, lineas_izq=["notas viejas"])
    dd = s.foto(PRIMERAS + TERCERA, doble_pagina=True, lineas_izq=["notas viejas"], semilla=4)
    guardar("doble_antes", da["img"])
    guardar("doble_despues", dd["img"], nuevo=dd["cajas"][2])
    # páginas planas (ya rectificadas): con tinta, y con la respuesta proyectada encima (luz que no es tinta)
    hoja = s.pagina()
    cajas = s.escribir(hoja, PRIMERAS)
    guardar("hoja", hoja, cajas_px=cajas)
    guardar("hoja_luz", s.proyectar_luz(hoja, "usa /v1/cobros", (110, 260)))
    guardar("hoja_blanca", s.pagina())
    (destino / "verdad.json").write_text(json.dumps(verdad, indent=1))
    return verdad


if __name__ == "__main__":
    v = exportar(Path(sys.argv[1]))
    print(len(v), "fotos")

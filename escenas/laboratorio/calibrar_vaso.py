"""Calibra la cámara del vaso: vaso vacío + dos niveles conocidos. Toma un minuto.

En la mesa (la cámara secundaria mirando el vaso de lado):
    uv run python calibrar_vaso.py --camara-vaso cam-2              # niveles 50 y 150 ml
    uv run python calibrar_vaso.py --camara-vaso cam-2 --niveles 40,100

Con fotos ya tomadas (sin mesa):
    uv run python calibrar_vaso.py --desde-fotos vacio.jpg 50:a.jpg 150:b.jpg

Pide, paso a paso, dejar el vaso vacío y llenarlo hasta las marcas impresas. La zona del vaso sale sola (donde cambió
la foto) y cada nivel lo lee el mismo detector que se usa en la escena, así que su sesgo se cancela. Deja
`estado/vaso.json`, `estado/vaso-vacio.jpg` y `estado/vaso-revision.jpg` (la foto con la zona y el nivel dibujados).

Consejo: usa un líquido con colorante. Con agua transparente la zona del vaso no sale sola (hay que darla con
`--roi x,y,ancho,alto`), el fondo detrás del vaso debe tener textura (el agua la deforma) y la luz no debe cambiar
entre la foto vacía y la escena.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

from percepcion import vaso as V

AQUI = Path(__file__).parent
ESTADO = AQUI / "estado"


def guardar(cal: V.Calibracion, vacio, ultima) -> Path:
    ESTADO.mkdir(exist_ok=True)
    cv2.imwrite(str(ESTADO / "vaso-vacio.jpg"), vacio)
    datos = {**cal.a_dict(), "vacio": "vaso-vacio.jpg"}
    ruta = ESTADO / "vaso.json"
    ruta.write_text(json.dumps(datos, indent=2))
    # Foto de revisión: zona del vaso, puntos calibrados y el nivel que se lee ahora.
    rev = ultima.copy()
    x, y, w, h = cal.roi_px
    cv2.rectangle(rev, (x, y), (x + w, y + h), (210, 224, 95), 2)
    for fila, ml in cal.puntos:
        cv2.line(rev, (x - 12, int(fila)), (x + 12, int(fila)), (255, 255, 255), 2)
        cv2.putText(rev, f"{ml:g} ml", (x + w + 6, int(fila) + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    lec = V.nivel_px(ultima, cal)
    if lec.fila_px is not None:
        cv2.line(rev, (x, int(lec.fila_px)), (x + w, int(lec.fila_px)), (92, 106, 255), 2)
    cv2.imwrite(str(ESTADO / "vaso-revision.jpg"), rev)
    return ruta


def desde_fotos(vacio_ruta: str, niveles: list[str], roi=None):
    vacio = cv2.imread(vacio_ruta)
    fotos = {}
    for n in niveles:
        ml, ruta = n.split(":", 1)
        fotos[float(ml.replace(",", "."))] = cv2.imread(ruta)
    cal = V.calibrar_con_lecturas(vacio, fotos, roi_px=roi)
    return cal, vacio, fotos[max(fotos)]


def en_la_mesa(camara: str, niveles: list[float], mesa=None, esperar=input, roi=None):
    from mesa_mcp import Mesa

    mesa = mesa or Mesa()

    def foto():
        return mesa.look(camara, annotate=False)[1]

    esperar("1/{}: deja el vaso VACÍO frente a la cámara y aprieta Enter… ".format(len(niveles) + 1))
    vacio = foto()
    fotos = {}
    for k, ml in enumerate(niveles, 2):
        esperar(f"{k}/{len(niveles) + 1}: llena hasta la marca de {ml:g} ml y aprieta Enter… ")
        fotos[ml] = foto()
    cal = V.calibrar_con_lecturas(vacio, fotos, roi_px=roi)
    return cal, vacio, fotos[max(fotos)]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Calibra la cámara del vaso (vacío + niveles conocidos)")
    ap.add_argument("--camara-vaso", help="camera_id de la cámara que mira el vaso")
    ap.add_argument("--niveles", default="50,150", help="ml de las marcas que vas a llenar, separados por coma")
    ap.add_argument("--desde-fotos", nargs="+", metavar="FOTO", help="vacio.jpg ml:foto.jpg [ml:foto.jpg …]")
    ap.add_argument("--roi", help="x,y,ancho,alto del interior del vaso en px; hace falta con agua transparente")
    args = ap.parse_args(argv)
    roi = [int(v) for v in args.roi.split(",")] if args.roi else None
    try:
        if args.desde_fotos:
            cal, vacio, ultima = desde_fotos(args.desde_fotos[0], args.desde_fotos[1:], roi=roi)
        elif args.camara_vaso:
            niveles = [float(x.replace(",", ".")) for x in args.niveles.split(",") if x.strip()]
            cal, vacio, ultima = en_la_mesa(args.camara_vaso, niveles, roi=roi)
        else:
            raise SystemExit("Indica --camara-vaso (en la mesa) o --desde-fotos.")
    except ValueError as e:
        raise SystemExit(f"No pude calibrar: {e}")
    ruta = guardar(cal, vacio, ultima)
    ahora = cal.ml(V.nivel_px(ultima, cal).fila_px)
    print(f"Listo: {ruta}. Zona del vaso {cal.roi_px}, puntos {[[round(a), b] for a, b in cal.puntos]}.")
    print(f"Ahora leo {ahora:.0f} ml. Revisa estado/vaso-revision.jpg: la línea roja debe quedar en la superficie.")


if __name__ == "__main__":
    main()

"""Detecta cada tecla del teclado en un escaneo y deja keys.json + imágenes de verificación.

    python -m playground.keys captures/scan2
    python -m playground.keys captures/scan2 --n-teclas 61 --primera-nota 36 --sin-altura
    python -m playground.keys captures/scan5 --metodo modelo --salida reports/scan5_keys   # ajuste por modelo (ECC)

Métodos: ``bordes`` (detect.py, tecla por tecla), ``modelo`` (fit.py: el teclado entero como plano, alineado con ECC;
para perspectiva fuerte) y ``auto`` (por defecto): bordes y, si su confianza media queda bajo 0,6, también el modelo;
gana el de mayor confianza.

Entrada: <scan>/scene_pov.png (foto con luz blanca vista desde el proyector), el rectángulo del teclado de
<scan>/workbench.json (teclado.rect) o de segments.json (bbox), o si no hay, se estima solo; <scan>/height_proj.npy
si existe. Salida: <scan>/keys.json, <scan>/keys_verify.png (foto + polígonos + nombres + acercamiento) y
<scan>/keys_project.png (contornos sobre negro, para proyectar con el banco de trabajo).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from .detect import detectar_teclas, estimar_rect
from .fit import ajustar_modelo
from .verify import imagen_proyeccion, imagen_verificacion


def cargar_rect(scan: Path, img: np.ndarray):
    """Rectángulo rotado del teclado: workbench.json → segments.json (bbox) → estimado de la imagen."""
    wb = scan / "workbench.json"
    if wb.exists():
        r = json.loads(wb.read_text()).get("teclado", {}).get("rect")
        if r:
            return (tuple(r[0]), tuple(r[1]), float(r[2]))
    seg = scan / "segments.json"
    if seg.exists():
        bb = json.loads(seg.read_text()).get("teclado", {}).get("bbox")
        if bb:
            x, y, w, h = bb
            return ((x + w / 2, y + h / 2), (w, h), 0.0)
    return estimar_rect(img)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m playground.keys", description=__doc__.splitlines()[0])
    ap.add_argument("scan", nargs="?", default="captures/scan5")
    ap.add_argument("--n-teclas", type=int, default=61)
    ap.add_argument("--primera-nota", type=int, default=36)
    ap.add_argument("--sin-altura", action="store_true", help="no usar height_proj.npy para marcar lo tapado")
    ap.add_argument("--metodo", choices=("auto", "bordes", "modelo"), default="auto")
    ap.add_argument("--salida", help="carpeta para keys.json y las imágenes (por defecto, la del escaneo)")
    a = ap.parse_args(argv)
    scan = Path(a.scan)
    out = Path(a.salida) if a.salida else scan
    out.mkdir(parents=True, exist_ok=True)
    img = cv2.imread(str(scan / "scene_pov.png"))
    if img is None:
        raise SystemExit(f"no encuentro {scan / 'scene_pov.png'}")
    altura = None
    if not a.sin_altura and (scan / "height_proj.npy").exists():
        altura = np.load(scan / "height_proj.npy")
    rect = cargar_rect(scan, img)
    res = None
    if a.metodo in ("auto", "bordes"):
        try:
            res = detectar_teclas(img, rect, altura=altura, n_teclas=a.n_teclas, primera_nota=a.primera_nota)
            res["metodo"] = "bordes"
        except Exception as e:  # noqa: BLE001  (en auto, el modelo todavía puede salir)
            if a.metodo == "bordes":
                raise
            print(f"bordes falló: {e}")
    if a.metodo == "modelo" or (a.metodo == "auto" and (res is None or res["confianza_media"] < 0.6)):
        mod = ajustar_modelo(img, rect, n_teclas=a.n_teclas, primera_nota=a.primera_nota)
        if res is not None:
            print(f"bordes: confianza {res['confianza_media']:.2f} · modelo: correlación {mod['confianza_media']:.2f}")
        if res is None or mod["confianza_media"] > res["confianza_media"]:
            res = mod
    res["fuente"] = str(scan / "scene_pov.png")
    rect_l = rect.tolist() if hasattr(rect, "tolist") else rect
    res["rect_entrada"] = ([list(map(float, rect_l[0])), list(map(float, rect_l[1])), float(rect_l[2])]
                           if len(rect_l) == 3 else [list(map(float, p)) for p in rect_l])
    (out / "keys.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    cv2.imwrite(str(out / "keys_verify.png"), imagen_verificacion(img, res))
    cv2.imwrite(str(out / "keys_project.png"), imagen_proyeccion(res, (img.shape[1], img.shape[0])))
    pv = res["primera_visible"] or {}
    print(f"{res['n_teclas']} teclas ({res['n_blancas']} blancas, {res['n_negras']} negras): "
          f"{res['n_visibles']} visibles, {res['n_ocultas']} ocultas, {res['n_fuera']} fuera de cuadro")
    print(f"primera nota {res['primera_nota']} · primera visible {pv.get('nombre', '-')} ({pv.get('nota', '-')}) · "
          f"paso {res['paso_px']:.2f} px · confianza media {res['confianza_media']:.2f} · "
          f"anclaje {res['patron']['anclaje']} (patrón {res['patron']['correlacion']:.2f})")
    for av in res["avisos"]:
        print("aviso:", av)
    print(f"método {res.get('metodo', 'bordes')} → {out / 'keys.json'}, {out / 'keys_verify.png'}, {out / 'keys_project.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

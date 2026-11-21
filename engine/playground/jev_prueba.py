"""Prueba de Jev sin cámara: toma un catalog_refinado.json real, consulta Jev por cada etiqueta y muestra la tabla
etiqueta → emoji de Jev (y el del diccionario del feed, para comparar) → ruta de efecto → intensidad, más llamadas,
tokens, ms y US$. Escribe jev.json en la carpeta del catálogo y la caché del scan, igual que el feed.

    .venv/bin/python -m playground.jev_prueba                    # catálogo de ejemplo (12 etiquetas, dale-20261102-1511)
    .venv/bin/python -m playground.jev_prueba captures/<scan>/items/<fecha>/catalog_refinado.json
    --forzar       pregunta aunque la etiqueta ya esté en la caché (para medir costo y latencia de nuevo)
    --cache RUTA   caché a usar (por defecto captures/<scan>/jev_cache.json, la misma que lee el feed)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
from pathlib import Path

from . import jev as jv
from .superficie_feed import emoji_dict

REPO = Path(__file__).resolve().parent.parent
EJEMPLO = REPO / "captures/dale-20261102-1511/items/20261102-193425/catalog_refinado.json"


def _ancho(s: str) -> int:
    """Columnas de terminal aproximadas (emoji y CJK ocupan 2; selectores de variante, 0)."""
    n = 0
    for ch in s:
        if unicodedata.combining(ch) or ch in "️‍":
            continue
        n += 2 if unicodedata.east_asian_width(ch) in "WF" else 1
    return n


def _col(s, w: int) -> str:
    s = str(s)
    return s + " " * max(0, w - _ancho(s))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("catalogo", nargs="?", type=Path, default=EJEMPLO)
    p.add_argument("--forzar", action="store_true")
    p.add_argument("--cache", type=Path, default=None)
    p.add_argument("--hilos", type=int, default=4)
    a = p.parse_args(argv)
    cat = a.catalogo if a.catalogo.name.endswith(".json") else a.catalogo / "catalog_refinado.json"
    dir_ = cat.parent
    scan = dir_.parent.parent
    sup = json.loads((scan / "superficie.json").read_text())
    if not jv.disponible():
        print("Jev no disponible: falta AI_GATEWAY_API_KEY (entorno o JEV_ENV_FILE)")
        return 1
    cache = jv.Cache(a.cache or scan / "jev_cache.json")
    print(f"catálogo {cat}  ·  caché {cache.ruta} ({len(cache)} etiquetas)  ·  {jv.MODELO} por {jv.ruta_http()}")
    t0 = time.perf_counter()
    r = jv.consultar_catalogo(dir_, cache, float(sup.get("mm_por_px") or 0), tuple(sup["lienzo_px"]), hilos=a.hilos, forzar=a.forzar)
    pared = time.perf_counter() - t0
    cols = (24, 7, 6, 5, 16, 6, 27)
    print()
    print("".join(_col(h, w) for h, w in zip(("etiqueta", "Jev", "conf", "dict", "ruta", "conf", "intensidad"), cols)) + "fuente  camino del emoji")
    print("-" * 110)
    for it in r["items"]:
        conf = it.get("emoji_conf") or []
        fila = (it["label"], it.get("emoji") or "—", f"{conf[-1]:.2f}" if conf else "", emoji_dict(it["label"]),
                it.get("ruta") or "—", f"{it['ruta_conf']:.2f}" if it.get("ruta_conf") is not None else "",
                f"{it['intensidad']:.2f} {it.get('intensidad_nivel', '')}" if it.get("intensidad") is not None else "—")
        print("".join(_col(v, w) for v, w in zip(fila, cols)) + f"{it['fuente']:<8}" + " > ".join(it.get("emoji_ruta") or []))
    s = jv.stats()
    print()
    print(f"etiquetas {r['etiquetas']} · consultadas {r['consultadas']} · nuevas {r['nuevas']} · fallas {r['fallas']}")
    print(f"llamadas {r['llamadas']} · tokens de entrada {r['tokens_in']} (salida {s['tokens_out']}, no se cobra) · "
          f"ms por llamada {r['ms_por_llamada']} (p50 {s['ms_p50']}, p95 {s['ms_p95']}) · pared {pared:.2f} s con {a.hilos} hilos")
    print(f"costo ≈ US$ {r['costo_usd']:.6f} (US$ {jv.USD_POR_M_ENTRADA}/M tokens de entrada)"
          + (f" · ≈ US$ {r['costo_usd'] / max(1, r['etiquetas']):.7f} por etiqueta" if r["llamadas"] else ""))
    if r.get("error"):
        print(f"error: {r['error']}")
    print(f"jev.json → {dir_ / 'jev.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

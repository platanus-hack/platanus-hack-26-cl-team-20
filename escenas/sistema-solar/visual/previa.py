#!/usr/bin/env python3
"""La capa visual sobre una foto real de la mesa, sin proyector:

    uv run python visual/previa.py <foto-mm.jpg> [salida.html] [--escala 1.4] [--oscuro 0.5] [--marcas] [--luz]

Corre la percepción de la escena sobre la vista en mm (1 px = 1 mm, como `prueba-vivo/*-mm.jpg`), arma los widgets de
`visual` con lo que vio (Sol con el contorno real del engranaje, cada planeta sobre su bola, órbitas por las bolas,
fondo, calma sobre el canasto y tapa sobre la lámina) y los dibuja encima de la foto con el mismo HTML y CSS que
recibe el proyector. `--marcas`: los círculos que vio la percepción y las zonas reservadas. `--luz`: suma la
proyección como luz: las capas se componen como en el proyector y después se suman a la foto (lo negro no agrega nada).
"""

from __future__ import annotations

import argparse
import base64
import html as _html
import sys
from pathlib import Path

import cv2

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

import visual as V  # noqa: E402
from percepcion import asignar_planetas, cargar_catalogo, detectar_cuerpos, ubicar_canasto, ubicar_laminas  # noqa: E402

QUIETO = "*{animation-delay:0s!important;animation-duration:0s!important}"  # la foto, ya llegado todo


def percibir(img_mm):
    canasto = ubicar_canasto(img_mm)
    cuerpos = detectar_cuerpos(img_mm, canasto.zona() if canasto else None)
    laminas = [l.dict() for l in ubicar_laminas(img_mm)]
    if not [c for c in cuerpos if c.tipo == "bola"]:
        return None, {}, cuerpos, canasto, laminas
    a = asignar_planetas(cuerpos, cargar_catalogo())
    sol = None
    if a.sol is not None:
        c = cuerpos[a.sol]
        rel = V.contorno_engranaje(img_mm, c.centro_mm, c.radio_mm)
        sol = {"center_mm": c.centro_mm, "radius_mm": c.radio_mm, "polygon_mm": V.girar_contorno(rel, c.centro_mm) if rel else None}
    planetas = {pid: {"center_mm": cuerpos[i].centro_mm, "radius_mm": cuerpos[i].radio_mm} for pid, i in a.planetas.items()}
    return sol, planetas, cuerpos, canasto, laminas


def iframe(w: dict, centros: dict, s: float) -> str:
    p = w.get("params", {})
    if "rect_mm" in w:
        x, y, rw, rh = w["rect_mm"]
    else:
        rw, rh = p["size_mm"]
        c = centros[w["anchor"]["object_id"]]
        ox, oy = w["anchor"].get("offset_mm", (0, 0))
        x, y = c[0] + ox - rw / 2, c[1] + oy - rh / 2
    giro = p.get("rotate_deg", 0)
    doc = (
        '<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;width:100%;height:100%;overflow:hidden;'
        "background:transparent}*{box-sizing:border-box}</style>"
        f"<style>{p.get('css', '')}{QUIETO}</style></head><body>{p.get('html', '')}</body></html>"
    )
    rot = f" translate({rw / 2}px,{rh / 2}px) rotate({giro}deg) translate({-rw / 2}px,{-rh / 2}px)" if giro else ""
    return (
        f'<iframe sandbox="" srcdoc="{_html.escape(doc, quote=True)}" style="position:absolute;left:0;top:0;border:0;'
        f"width:{rw}px;height:{rh}px;z-index:{w.get('z', 0) + 20};transform-origin:0 0;transform:translate({x * s}px,{y * s}px) scale({s}){rot}\"></iframe>"
    )


def pagina(foto: Path, escala: float, oscuro: float, marcas: bool, luz: bool = False) -> tuple[str, str]:
    img = cv2.imread(str(foto))
    h, w = img.shape[:2]
    sol, planetas, cuerpos, canasto, laminas = percibir(img)
    ws = V.widgets(sol, planetas, (w, h), canasto_mm=canasto.poligono_mm if canasto else None)
    # el negro sobre la lámina lo pone el widget de la lámina de h20 (z 2); acá, solo su tapa
    ws += [{"id": f"solar-lamina-{n}", "z": 2, "rect_mm": [l["centro_mm"][0] - l["tam_mm"][0] / 2 - 4, l["centro_mm"][1] - l["tam_mm"][1] / 2 - 4, l["tam_mm"][0] + 8, l["tam_mm"][1] + 8],
            "params": {"rotate_deg": l["angulo_deg"], "html": "", "css": "body{background:#000}"}} for n, l in enumerate(laminas)]
    centros = {V.E.oid(pid): p["center_mm"] for pid, p in planetas.items()}
    if sol:
        centros[V.E.oid("sol")] = sol["center_mm"]
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
    capas = "".join(iframe(x, centros, escala) for x in ws)
    extra = ""
    if marcas:
        circ = "".join(
            f'<circle cx="{c.centro_mm[0] * escala:.1f}" cy="{c.centro_mm[1] * escala:.1f}" r="{c.radio_mm * escala:.1f}" fill="none" stroke="#0f0" stroke-width="1.5" stroke-dasharray="4 4"/>'
            for c in cuerpos
        )
        circ += "".join(
            f'<rect x="{rx * escala:.1f}" y="{ry * escala:.1f}" width="{rw * escala:.1f}" height="{rh * escala:.1f}" fill="rgba(255,0,60,.18)" stroke="#f03" stroke-width="1.5"/>'
            for rx, ry, rw, rh in V.reservas((w, h))
        )
        extra = f'<svg style="position:absolute;inset:0;width:100%;height:100%;z-index:99">{circ}</svg>'
    resumen = f"sol={'sí' if sol else 'no'}{' (contorno real)' if sol and sol.get('polygon_mm') else ''} planetas={','.join(planetas) or '-'} láminas={len(laminas)} widgets={','.join(x['id'] for x in ws)}"
    doc = (
        '<!doctype html><html><head><meta charset="utf-8"><title>Sistema solar · capa visual</title>'
        f"<style>body{{margin:0;background:#000}}#m{{position:relative;overflow:hidden;isolation:isolate;width:{w * escala}px;height:{h * escala}px}}"
        f"#m img{{position:absolute;inset:0;width:100%;height:100%;filter:brightness({oscuro})}}</style></head><body>"
        f'<div id="m"><img src="data:image/jpeg;base64,{base64.b64encode(buf.tobytes()).decode()}">'
        f'<div style="position:absolute;inset:0;isolation:isolate;{"mix-blend-mode:screen;" if luz else ""}">{capas}</div>{extra}</div></body></html>'
    )
    return doc, resumen


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("foto", type=Path)
    ap.add_argument("salida", type=Path, nargs="?")
    ap.add_argument("--escala", type=float, default=1.4)
    ap.add_argument("--oscuro", type=float, default=0.5)
    ap.add_argument("--marcas", action="store_true")
    ap.add_argument("--luz", action="store_true")
    a = ap.parse_args()
    doc, resumen = pagina(a.foto, a.escala, a.oscuro, a.marcas, a.luz)
    out = a.salida or a.foto.with_suffix(".visual.html")
    out.write_text(doc, encoding="utf-8")
    print(f"{out} · {resumen}")


if __name__ == "__main__":
    main()

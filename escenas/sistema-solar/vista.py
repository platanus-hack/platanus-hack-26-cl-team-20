#!/usr/bin/env python3
"""Ver la escena sin mesa: `uv run python vista.py [--ficha saturno] [--decir saturno "texto"] [--escala 1.6]`.

Arma `estado/vista.html` con la foto de referencia de la mesa y encima los mismos widgets html que recibe el
proyector (mismo HTML y CSS, 1 px de CSS = 1 mm, multiplicado por la escala). Sirve para revisar el diseño o para
mostrar la escena a alguien sin proyector. Necesita haber corrido `conductor.py armar` una vez.
"""

from __future__ import annotations

import argparse
import base64
import html
import math

import cv2

import conductor
import escena
from percepcion import planetas_en_lamina
from percepcion.lamina import Lamina


QUIETO = "*{animation-delay:0s!important;animation-duration:0s!important}"


def _iframe(w: dict, objetos: dict, s: float, quieto: bool = False) -> str:
    p = w.get("params", {})
    if "rect_mm" in w:
        x, y, rw, rh = w["rect_mm"]
    else:
        sw, sh = p.get("size_mm", (300, 200))
        c = objetos[w["anchor"]["object_id"]]["center_mm"]
        o = w["anchor"].get("offset_mm", (0, 0))
        x, y, rw, rh = c[0] + o[0] - sw / 2, c[1] + o[1] - sh / 2, sw, sh
    rot = p.get("rotate_deg", 0)
    doc = (
        '<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;width:100%;height:100%;overflow:hidden;'
        'background:transparent;color:#fff;font:400 24px/1.3 system-ui}*{box-sizing:border-box}</style>'
        f"<style>{p.get('css', '')}{QUIETO if quieto else ''}</style></head><body>{p.get('html', '')}</body></html>"
    )
    return (
        f'<iframe sandbox="" srcdoc="{html.escape(doc, quote=True)}" style="position:absolute;left:0;top:0;border:0;'
        f"width:{rw}px;height:{rh}px;z-index:{w.get('z', 0) + 20};opacity:{p.get('opacity', 1)};transform-origin:0 0;"
        f"transform:translate({x * s}px,{y * s}px) scale({s}) translate({rw / 2}px,{rh / 2}px) rotate({rot}deg) translate({-rw / 2}px,{-rh / 2}px)\"></iframe>"
    )


def armar_vista(ficha: str | None, decir: tuple[str, str] | None, escala: float, oscuro: float, quieto: bool = False, nombres: list[str] | None = None) -> str:
    est = conductor.leer_json(conductor.ESTADO)
    widgets = conductor.widgets_sistema(est, True)
    if est.get("laminas"):
        lam = est["laminas"][0]
        widgets.append(escena.widget_lamina(lam, planetas_en_lamina(Lamina(**conductor._lam(lam))), ficha))
    objetos = {o["id"]: o for o in conductor.objetos_mundo(est)}
    cs = conductor.centros_actuales(est)
    for pid in (nombres or [])[:3]:
        widgets.append(escena.widget_nombre(conductor.planeta_de(pid), conductor.radio_de(est, pid), conductor.hacia_afuera(est, pid)))
    if ficha:
        tam = tuple(conductor.leer_json(conductor.MESA).get("ficha", {}).get("tam_mm") or escena.FICHA_MM)
        rect = est.get("ficha_rect") or conductor.ubicar_ficha(est, tam)
        widgets.append(escena.widget_ficha(conductor.planeta_de(ficha), rect))
        if ficha in cs:
            widgets.append(escena.widget_foco(ficha, radio_mm=conductor.radio_de(est, ficha)))
    if decir:
        pid, texto = decir
        widgets.append(escena.widget_lumo(pid, conductor.radio_de(est, pid), conductor.hacia_afuera(est, pid), conductor.lugar_lumo(est, pid)))
        widgets.append(
            escena.widget_burbuja(conductor.cargar_catalogo().get("guia", {"id": "lumo", "nombre": "Lumo"}), conductor.corto(texto), conductor.radio_de(est, pid), conductor.hacia_afuera(est, pid), cs.get(pid), est.get("surface_mm", (1000, 563)), junto_a=pid)
        )
    img = cv2.imread(str(conductor.ESTADO.parent / "referencia.jpg"))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 72])
    fondo = base64.b64encode(buf.tobytes()).decode()
    w, h = est.get("surface_mm", [1000, 563])
    capas = "".join(_iframe(x, objetos, escala, quieto) for x in widgets if x.get("shader") == "html")
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>Sistema solar · vista</title>'
        "<style>body{margin:0;background:#000}#m{position:relative;overflow:hidden}"
        f"#m img{{position:absolute;inset:0;width:100%;height:100%;filter:brightness({oscuro})}}</style></head><body>"
        f'<div id="m" style="width:{w * escala}px;height:{h * escala}px"><img src="data:image/jpeg;base64,{fondo}">{capas}</div>'
        "</body></html>"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ficha")
    ap.add_argument("--decir", nargs=2, metavar=("PLANETA", "TEXTO"))
    ap.add_argument("--escala", type=float, default=1.6)
    ap.add_argument("--oscuro", type=float, default=0.55, help="brillo de la foto de fondo (la sala con poca luz)")
    ap.add_argument("--quieto", action="store_true", help="sin animaciones de entrada (para capturas)")
    ap.add_argument("--nombres", nargs="*", default=[], help="hasta 3 planetas con su nombre a la vista")
    a = ap.parse_args()
    out = conductor.ESTADO.parent / "vista.html"
    out.write_text(armar_vista(a.ficha, tuple(a.decir) if a.decir else None, a.escala, a.oscuro, a.quieto, a.nombres), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()

"""Vista previa sin mesa: arma una página con la foto cenital de la mesa y encima los widgets html, montados igual que
en la consola (iframe aislado, 1 px de CSS = 1 mm, giro con rotate_deg). La proyección se mezcla con «screen», que es
lo que hace la luz de un proyector sobre la mesa.

    uv run python conductor.py --solo-previa estado/previa     # una página por momento de la escena
"""

from __future__ import annotations

import base64
import html
from pathlib import Path

AQUI = Path(__file__).parent
FOTO = AQUI / "percepcion" / "tests" / "datos" / "mesa-cenital-mm-1.jpg"

# Igual que BASE_CSS de cloud/gateway/src/pcx_gateway/console/html-layer.js (sin el script de toques).
BASE_CSS = """html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; background: transparent; color: #fff;
  font: 400 24px/1.3 system-ui, -apple-system, "Segoe UI", sans-serif; } * { box-sizing: border-box; }"""


def pagina(widgets: list[dict], escala: float = 1.6, foto: Path = FOTO, ancho_mm: int = 1000, alto_mm: int = 563) -> str:
    fondo = base64.b64encode(foto.read_bytes()).decode() if foto and foto.exists() else ""
    capas = []
    for w in sorted(widgets, key=lambda w: w.get("z", 0)):
        if w.get("shader") != "html":
            continue
        x, y, rw, rh = w["rect_mm"]
        p = w.get("params", {})
        doc = f"<!doctype html><html><head><meta charset='utf-8'><style>{BASE_CSS}</style><style>{p.get('css', '')}</style></head><body>{p.get('html', '')}</body></html>"
        rot = float(p.get("rotate_deg") or 0)
        t = f"translate({x * escala}px,{y * escala}px) scale({escala}) translate({rw / 2}px,{rh / 2}px) rotate({rot}deg) translate({-rw / 2}px,{-rh / 2}px)"
        capas.append(
            f'<iframe sandbox="" srcdoc="{html.escape(doc, quote=True)}" '
            f'style="position:absolute;left:0;top:0;border:0;transform-origin:0 0;width:{rw}px;height:{rh}px;transform:{t}"></iframe>'
        )
    W, H = ancho_mm * escala, alto_mm * escala
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Laboratorio · vista previa</title>
<style>html,body{{margin:0;background:#111}} .mesa{{position:relative;width:{W}px;height:{H}px;overflow:hidden}}
.foto{{position:absolute;inset:0;background:url(data:image/jpeg;base64,{fondo}) 0 0/100% 100%;filter:brightness(.55) saturate(.9)}}
.luz{{position:absolute;inset:0;mix-blend-mode:screen;background:#000}}</style></head>
<body><div class="mesa"><div class="foto"></div><div class="luz">{"".join(capas)}</div></div></body></html>"""

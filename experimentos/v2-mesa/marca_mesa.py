"""Recursos locales de WUI para los widgets aislados del proyector."""
import base64
from functools import lru_cache
from pathlib import Path

MARCA = Path(__file__).resolve().parent / 'marca'
FIRMA_X, FIRMA_Y, FIRMA_ANCHO = 120, 38, 64
FIRMA_ALTO = FIRMA_ANCHO * 314 / 632

@lru_cache
def recurso(nombre, mime):
    return f'data:{mime};base64,' + base64.b64encode((MARCA / nombre).read_bytes()).decode('ascii')


def fuente():
    url = recurso('instrument-sans.woff2', 'font/woff2')
    return f'@font-face{{font-family:"WUI Sans";font-weight:400 700;font-stretch:75% 100%;src:url({url}) format("woff2")}}'


def firma():
    return f'<img id="wui" alt="WUI" src="{recurso("wui-firma.svg", "image/svg+xml")}">'


POSICION = f'position:absolute;left:{FIRMA_X}px;top:{FIRMA_Y}px;width:{FIRMA_ANCHO}px;height:{FIRMA_ALTO:.3f}px;pointer-events:none;'
CSS_FIRMA = '#wui{' + POSICION + '}'
CSS_CANVAS = ('html,body{margin:0;background:transparent;overflow:hidden}'
              'canvas{position:absolute;inset:0;width:1000px;height:563px;opacity:0}'
              '#wui{opacity:0}' + CSS_FIRMA)
PISTA_JS = (MARCA / 'pista.js').read_text(encoding='utf-8')


def apertura():
    """Un solo ciclo del máster aprobado. Se entrega como imagen por la CSP del proyector."""
    ancho = 344
    escala = ancho / FIRMA_ANCHO
    dx = (1000 - ancho) / 2 - FIRMA_X
    dy = (563 - escala * FIRMA_ALTO) / 2 - FIRMA_Y
    html = (f'<div class="gesto"><img class="entrada" alt="" src="{recurso("wui-entrada.webp", "image/webp")}">'
            + firma() + '</div>')
    css = ('html,body{margin:0;background:transparent;overflow:hidden}'
           '.gesto{' + POSICION + 'transform-origin:0 0;animation:recoger 680ms cubic-bezier(.16,1,.3,1) 1720ms both}'
           f'@keyframes recoger{{from{{transform:translate({dx:.2f}px,{dy:.2f}px) scale({escala})}}to{{transform:none}}}}'
           '.gesto img{position:absolute;inset:0;width:100%;height:100%}'
           '.entrada{animation:salir 1ms step-end 1720ms both}#wui{animation:entrar 1ms step-end 1720ms both}'
           '@keyframes salir{from{opacity:1}to{opacity:0}}@keyframes entrar{from{opacity:0}to{opacity:1}}'
           '@media(prefers-reduced-motion:reduce){.gesto,#wui{animation:none}.entrada{display:none}}')
    return {'type': 'effect', 'id': 'boot', 'shader': 'html', 'z': 2, 'rect_mm': [0, 0, 1000, 563],
            'params': {'html': html, 'css': css}}

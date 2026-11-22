"""Lo que se proyecta en la escena Sistema solar: funciones puras que arman los widgets para `project`.

Sigue el sistema común de la mesa (`MOTOR.md` y `motor/sistema/base.css` de max/motor): cada widget html lleva
`base.css + tema.css` en `params.css`, 1 px de CSS = 1 mm, texto de 28 mm o más, y los verbos de siempre:

- **Señalar** (`solar-foco`): `halo` nativo anclado al objeto del planeta. Uno a la vez.
- **Nombrar** (`solar-nombre-<planeta>`): `.sd-etiqueta` de 28 mm, hacia afuera del sistema. Máximo 3 a la vez.
- **Explicar** (`solar-ficha`): `.sd-panel` con el nombre, su lugar desde el Sol y un dato. Siempre en zona libre.
- **Tocar** (`solar-lamina`): cada planeta impreso en la lámina es un `data-press` con el id del planeta.
- **Hablar** (`solar-burbuja`): lo que dice el planeta en texto corto (la voz dice la versión larga).

Lo propio de esta escena, que no está en el sistema común:
- `solar-planeta-<id>`: la textura de cada planeta sobre su bola de plumavit, con el lado lejos del Sol en sombra.
- `solar-sol`: el Sol sobre el engranaje, con su corona.
- `solar-orbitas`: órbitas de tiza punteada alrededor del Sol, cada una con un punto que la recorre (la firma visual).

Todo va anclado a objetos del mundo con ids `solar:<planeta>` (`solar:sol`, `solar:jupiter`…). Cuando el motor (o
`conductor.py seguir`) mueve esos objetos, la consola mueve los widgets sola: no hay que volver a proyectar.

Sin scripts dentro de lo proyectado (la consola no los deja correr): todo es HTML + CSS.
"""

from __future__ import annotations

import html as _html
import math
import os
import re
import textwrap
from functools import lru_cache
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PREFIJO = "solar:"
REACCION = (0, 0.13, 0.3, 0.5, 0.73, 1.0, 1.3, 1.62, 1.98, 2.36)  # desfases desparejos (s): un líder y sus reacciones
LETRA_MM = 15.4  # ancho medio de una letra de 28 mm en Instrument Sans (~0,55 em)


def oid(pid: str) -> str:
    """Id del objeto del mundo para un planeta: `solar:saturno`."""
    return PREFIJO + pid


# Interfaz propia de la consola (SAT, 21-nov): barra de pestañas a la izquierda y botón de voz arriba a la derecha.
# La escena no pone nada ahí (`project` lo avisa en `reserved` y la interfaz queda encima).
BARRA_MM = 150
VOZ_MM = 130
FRANJA_MM = float(os.environ.get("SOLAR_FRANJA_MM", 40))


def zonas_reservadas(superficie_mm=(1000, 563), girada: bool = False) -> list[list[float]]:
    """Rectángulos [x, y, w, h] de la interfaz de la mesa. `girada`: la consola con flip (todo rota 180°)."""
    w, h = superficie_mm
    if girada:
        return [[w - BARRA_MM, 0, BARRA_MM, h], [0, h - VOZ_MM, VOZ_MM, VOZ_MM]]
    return [[0, 0, BARRA_MM, h], [w - VOZ_MM, 0, VOZ_MM, VOZ_MM]]


def zona_franja(superficie_mm, girada: bool, alto_mm: float = FRANJA_MM) -> list[float]:
    w, h = superficie_mm
    return [0, 0 if girada else h - alto_mm, w, alto_mm]


def esquivar(cx: float, cy: float, ancho: float, alto: float, rects, superficie_mm=(1000, 563),
             margen: float = 6.0, objetos=()) -> tuple[float, float]:
    """Busca el hueco más cercano. La interfaz tiene prioridad si no hay espacio libre de objetos."""
    w, h = superficie_mm
    rx, ry = ancho / 2, alto / 2

    def limitar(x, y):
        return min(max(x, rx), max(rx, w - rx)), min(max(y, ry), max(ry, h - ry))

    preferido = limitar(cx, cy)
    xs, ys = {preferido[0], rx, max(rx, w - rx)}, {preferido[1], ry, max(ry, h - ry)}
    for x, y, rw, rh in (*rects, *objetos):
        xs.update(limitar(edge, cy)[0] for edge in (x - rx - margen, x + rw + rx + margen))
        ys.update(limitar(cx, edge)[1] for edge in (y - ry - margen, y + rh + ry + margen))

    def solape(px, py, cajas):
        return sum(
            max(0, min(px + rx, x + rw) - max(px - rx, x))
            * max(0, min(py + ry, y + rh) - max(py - ry, y))
            for x, y, rw, rh in cajas
        )

    return min(
        ((x, y) for x in sorted(xs) for y in sorted(ys)),
        key=lambda p: (solape(*p, rects), solape(*p, objetos), math.dist(preferido, p)),
    )


@lru_cache(maxsize=1)
def _css_base() -> str:
    """base.css del sistema común (la del repo si está, si no la copia de la escena) + tema.css de la escena."""
    repo = AQUI.parents[1] / "motor" / "sistema" / "base.css"
    base = (repo if repo.exists() else AQUI / "sistema-base.css").read_text(encoding="utf-8")
    return base + "\n" + (AQUI / "tema.css").read_text(encoding="utf-8")


# Tokens cambiados en vivo (`preset_ajustar` con `aplica: css:--token`); el conductor los carga de mesa.json.
CSS_VIVO: dict[str, str] = {}


def css_sistema() -> str:
    """Sistema común + tema de la escena + lo ajustado en vivo."""
    extra = "".join(f"{k}:{v};" for k, v in CSS_VIVO.items())
    return _css_base() + (f"\n:root{{{extra}}}" if extra else "")


def recargar() -> None:
    """Vuelve a leer base.css y tema.css (después de editarlos)."""
    _css_base.cache_clear()


def tokens() -> dict[str, str]:
    """Tokens del sistema ya con el tema encima (para los widgets nativos, que no leen CSS)."""
    return {k: v.strip() for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", css_sistema())}


def _e(s: str) -> str:
    return _html.escape(str(s), quote=True)


def _mezcla(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def _html_widget(wid: str, html: str, css: str, z: int, **extra) -> dict:
    params = {"html": html, "css": css_sistema() + css}
    params.update(extra.pop("params", {}))
    if extra.get("anchor", {}).get("object_id"):
        params.setdefault("wui_anclado", True)
    if "data-press" not in html:
        params["wui_sin_toque"] = True
    return {"type": "effect", "id": wid, "shader": "html", "z": z, "params": params, **extra}


# ---------- texturas de planeta (CSS puro, giran desplazando el fondo) ----------


def textura(planeta: dict, d: float) -> str:
    """`background` + tamaño de mosaico para un disco de diámetro `d` mm. El mosaico mide 2d de ancho y se repite
    en x, así que desplazar el fondo 2d da una vuelta completa sin costura."""
    c1, c2, c3 = (planeta["colores"] + planeta["colores"][-1:] * 3)[:3]
    tipo = planeta.get("textura", "rocosa")
    w = 2 * d
    if tipo in ("bandas", "anillos"):
        bandas = [c1, c2, c1, c3, c2, c1, c2, c3, c1, c2]
        paso = 100 / len(bandas)
        stops = ", ".join(
            f"{c} {(i + .16) * paso:.1f}% {(i + .78) * paso:.1f}%" for i, c in enumerate(bandas)
        )
        capas = [f"linear-gradient(180deg, {stops})"]
        if planeta["id"] == "jupiter":
            capas.insert(0, f"radial-gradient(ellipse {d * .22:.1f}px {d * .12:.1f}px at {w * .3:.1f}px {d * .66:.1f}px, #b34e3d 24%, #ce8361 62%, transparent 100%)")
        fondo = ", ".join(capas)
    elif tipo == "tierra":
        manchas = [(0.12, 0.35, 0.16, 0.12), (0.3, 0.62, 0.13, 0.18), (0.62, 0.3, 0.2, 0.14), (0.8, 0.66, 0.1, 0.12), (0.47, 0.5, 0.06, 0.08)]
        capas = [f"radial-gradient(ellipse {rx * d:.1f}px {ry * d:.1f}px at {x * w:.1f}px {y * d:.1f}px, {c3} 70%, transparent 100%)" for x, y, rx, ry in manchas]
        capas += [f"radial-gradient(ellipse {d * .5:.1f}px {d * .05:.1f}px at {w * .5:.1f}px {d * .2:.1f}px, rgba(255,255,255,.85) 50%, transparent 100%)"]
        capas += [f"linear-gradient(180deg, #ffffff 0 6%, {c1} 12%, {c2} 88%, #ffffff 94%)"]
        fondo = ", ".join(capas)
    elif tipo == "nubes":
        fondo = f"linear-gradient(180deg, {c1}, {c2} 28%, {c1} 45%, {c2} 62%, {c3} 85%, {c2})"
    elif tipo == "hielo":
        fondo = f"linear-gradient(180deg, {c1} 0%, {c2} 35%, {_mezcla(c2, c1, .3)} 48%, {c2} 60%, {c3} 100%)"
    else:  # rocosa: cráteres
        crat = [(0.15, 0.3, 0.09), (0.4, 0.7, 0.13), (0.62, 0.25, 0.07), (0.85, 0.55, 0.11), (0.3, 0.5, 0.05), (0.72, 0.8, 0.06)]
        capas = [f"radial-gradient(circle {r * d:.1f}px at {x * w:.1f}px {y * d:.1f}px, {c3} 55%, {_mezcla(c3, c2, .5)} 75%, transparent 100%)" for x, y, r in crat]
        fondo = ", ".join(capas + [f"linear-gradient(180deg, {c1}, {c2})"])
    return fondo


# ---------- lo propio: planetas, Sol y órbitas ----------


def widget_planeta(planeta: dict, radio_mm: float, hacia_sol: tuple[float, float], duda: bool = False, retraso: float = 0.0, ajuste: dict | None = None) -> dict:
    """La textura del planeta sobre su bola. `hacia_sol`: dirección (mesa) de la bola al Sol, para la sombra."""
    ajuste = ajuste or {}
    r = radio_mm * ajuste.get("escala_bola", 0.96)
    d = 2 * r
    lado = d * (2.2 if planeta.get("textura") == "anillos" else 1.0) + 8
    o = (lado - d) / 2
    n = math.hypot(*hacia_sol) or 1
    lx, ly = 50 + 32 * hacia_sol[0] / n, 50 + 32 * hacia_sol[1] / n
    pid = planeta["id"]
    vel = ajuste.get("giro_s", 26) * (1 + planeta.get("orden", 3) * 0.12)
    anillo = ""
    if planeta.get("textura") == "anillos":
        anillo = f'<div class="anillo" style="left:{o - r * .95:.1f}px;top:{o + r * .55:.1f}px;width:{r * 3.9:.1f}px;height:{r * .9:.1f}px"></div>'
    html = (
        f'<div class="pl{" duda" if duda else ""}" style="left:{o:.1f}px;top:{o:.1f}px;width:{d:.1f}px;height:{d:.1f}px">'
        f'<div class="tex"></div><div class="luz"></div></div>{anillo}'
    )
    css = (
        f".pl{{position:absolute;border-radius:50%;animation:llega .9s var(--sd-curva) {retraso:.2f}s both}}"
        f".tex,.luz{{position:absolute;inset:0;border-radius:50%;"
        f"-webkit-mask:radial-gradient(circle,#000 86%,transparent 100%);mask:radial-gradient(circle,#000 86%,transparent 100%)}}"
        f".tex{{background:{textura(planeta, d)};background-size:{2 * d:.1f}px {d:.1f}px;animation:gira {vel:.1f}s linear infinite}}"
        f".luz{{background:radial-gradient(circle at {lx:.0f}% {ly:.0f}%,transparent 38%,var(--solar-noche) 80%)}}"
        f".duda{{outline:3px dashed var(--sd-tenue);outline-offset:5px}}"
        f".anillo{{position:absolute;border:4px solid {planeta['colores'][1]};border-radius:50%;transform:rotate(-18deg);opacity:.9;"
        f"animation:llega .9s var(--sd-curva) {retraso + .15:.2f}s both}}"
        f"@keyframes gira{{to{{background-position:-{2 * d:.1f}px 0}}}}"
        f"@keyframes llega{{from{{opacity:0;transform:scale(.6)}}}}"
    )
    return _html_widget(f"solar-planeta-{pid}", html, css, 1, anchor={"object_id": oid(pid)}, params={"size_mm": [round(lado, 1), round(lado, 1)]})


def widget_sol(radio_mm: float) -> dict:
    lado = radio_mm * 2.6
    html = '<div class="corona"></div><div class="nucleo"></div>'
    css = (
        f".corona,.nucleo{{position:absolute;border-radius:50%}}"
        f".corona{{inset:0;background:repeating-conic-gradient(from 0deg,transparent 0 7deg,var(--sd-foco) 9deg,transparent 11deg 18deg);"
        f"-webkit-mask:radial-gradient(circle,transparent 30%,#000 52%,transparent 72%);mask:radial-gradient(circle,transparent 30%,#000 52%,transparent 72%);"
        f"opacity:.6;animation:gira 80s linear infinite,llega 1.2s var(--sd-curva) both}}"
        f".nucleo{{inset:{radio_mm * .3:.1f}px;background:radial-gradient(circle,#fff7d6 0%,var(--sd-foco) 45%,#ff9f1c 85%,rgba(255,140,20,0) 100%);"
        f"animation:llega 1.2s var(--sd-curva) both}}"
        f"@keyframes gira{{to{{transform:rotate(360deg)}}}}"
        f"@keyframes llega{{from{{opacity:0;transform:scale(.5)}}}}"
    )
    return _html_widget("solar-sol", html, css, 1, anchor={"object_id": oid("sol")}, params={"size_mm": [round(lado, 1)] * 2})


def widget_orbitas(orbitas: list[dict], intro: bool = True, lenta: float = 1.0, sol_mm=None, fuera_mm=None, ancla: str = "sol") -> dict:
    """Órbitas alrededor del Sol, como pinceladas que fluyen lento. `orbitas`: [{radio_mm, angulo_deg, color}]
    (distancia y ángulo de cada planeta visto desde el Sol). Son círculos: si el canasto gira no cambian, así que basta
    con anclarlas al Sol. Un punto blanco recorre cada una, partiendo desde su planeta.

    Con `sol_mm` y `fuera_mm` (rectángulos de la mesa, p. ej. `zonas_reservadas`), lo que cae ahí no se pinta. La
    máscara vale para esa posición del Sol: si el canasto se mueve, el conductor vuelve a proyectar las órbitas."""
    rmax = max((o["radio_mm"] for o in orbitas), default=100)
    lado = 2 * (rmax + 12)
    c = lado / 2
    partes = [f'<svg viewBox="0 0 {lado:.0f} {lado:.0f}" width="{lado:.0f}" height="{lado:.0f}">']
    if sol_mm is not None and fuera_mm:
        ox, oy = sol_mm[0] - c, sol_mm[1] - c
        tapas = "".join(f'<rect x="{x - ox:.1f}" y="{y - oy:.1f}" width="{w:.1f}" height="{h:.1f}" fill="#000"/>' for x, y, w, h in fuera_mm)
        partes.append(
            f'<defs><mask id="libre" maskUnits="userSpaceOnUse" x="-40" y="-40" width="{lado + 80:.0f}" height="{lado + 80:.0f}">'
            f'<rect x="-40" y="-40" width="{lado + 80:.0f}" height="{lado + 80:.0f}" fill="#fff"/>{tapas}</mask></defs><g mask="url(#libre)">'
        )
    else:
        partes.append("<g>")
    for i, o in enumerate(sorted(orbitas, key=lambda x: x["radio_mm"])):
        r = o["radio_mm"]
        retraso = (REACCION[min(i, len(REACCION) - 1)] + 0.9) if intro else 0
        periodo = 14 * (1 + i) ** 1.5 * lenta
        partes.append(
            f'<circle class="orb" cx="{c:.1f}" cy="{c:.1f}" r="{r:.1f}" style="stroke:{o.get("color", "var(--solar-orbita)")};animation-delay:{retraso:.2f}s"/>'
            f'<g transform="rotate({o["angulo_deg"]:.1f} {c:.1f} {c:.1f})"><g class="punto" style="animation:orbita {periodo:.1f}s linear {retraso + 1.2:.2f}s infinite">'
            f'<circle cx="{c + r:.1f}" cy="{c:.1f}" r="5"/></g></g>'
        )
    partes.append("</g></svg>")
    css = (
        f"svg{{position:absolute;inset:0;overflow:visible}}"
        f".orb{{fill:none;stroke-width:var(--solar-pincel);stroke-linecap:round;stroke-dasharray:18 12;opacity:var(--solar-orbita-opacidad);"
        f"animation:aparece .01s linear both,fluye 40s linear infinite}}"
        f".punto{{transform-box:view-box;transform-origin:{c:.1f}px {c:.1f}px;fill:var(--sd-tinta)}}"
        f"@keyframes fluye{{to{{stroke-dashoffset:-300}}}}"
        f"@keyframes orbita{{to{{transform:rotate(360deg)}}}}"
        f"@keyframes aparece{{from{{opacity:0}}}}"
    )
    return _html_widget("solar-orbitas", "".join(partes), css, 0, anchor={"object_id": oid(ancla)}, params={"size_mm": [round(lado, 1)] * 2})


# ---------- verbos comunes ----------


def widget_foco(pid: str, escuchando: bool = False, radio_mm: float = 23) -> dict:
    """Contorno abierto de la lámina 07, fuera del disco del planeta."""
    r = radio_mm + 7
    c = r + 4
    x, y = c - r * .65, r * .76
    lado = round(2 * c, 1)
    html = (
        f'<svg class="foco-solar{" escucha" if escuchando else ""}" viewBox="0 0 {lado} {lado}">'
        f'<path pathLength="1" d="M{x:.1f},{c - y:.1f} A{r:.1f},{r:.1f} 0 1 1 {x:.1f},{c + y:.1f}"/></svg>'
    )
    css = """
      .foco-solar { position:absolute; inset:0; width:100%; height:100%; overflow:visible; }
      .foco-solar path { fill:none; stroke:var(--sd-tinta); stroke-width:2; opacity:.57;
        stroke-linecap:round; stroke-dasharray:1; animation:solar-contorno 560ms var(--wui-curva) both; }
      .foco-solar.escucha { animation:solar-escucha 2.2s ease-in-out infinite alternate; }
      @keyframes solar-contorno { from { stroke-dashoffset:1; opacity:.12; } to { stroke-dashoffset:0; } }
      @keyframes solar-escucha { to { opacity:.56; } }
    """
    return _html_widget("solar-foco", html, css, 3, anchor={"object_id": oid(pid)}, params={"size_mm": [lado, lado]})


def caja_texto(texto: str, ancho_mm: float) -> list[str]:
    """Corta el texto en líneas que caben en `ancho_mm` con letra de 28 mm."""
    return textwrap.wrap(texto, max(6, int(ancho_mm / LETRA_MM)))


def widget_nombre(
    planeta: dict, radio_mm: float, hacia: tuple[float, float], duda: bool = False, centro_mm=None, superficie_mm=(1000, 563), reservado=None, objetos=()
) -> dict:
    """Nombrar: etiqueta de 28 mm pegada a la bola, del lado que mira hacia afuera del sistema. Con `centro_mm`, si
    la etiqueta pisaría la interfaz de la mesa (`reservado`, por defecto `zonas_reservadas`), se corre."""
    texto = planeta["nombre"] + ("?" if duda else "")
    w = len(texto) * LETRA_MM + 34
    h = 50
    ux, uy = hacia
    n = math.hypot(ux, uy) or 1
    ux, uy = ux / n, uy / n
    d = radio_mm + 8 + abs(ux) * w / 2 + abs(uy) * h / 2
    if centro_mm is not None:
        rects = [*zonas_reservadas(superficie_mm), zona_franja(superficie_mm, False)] if reservado is None else reservado
        x, y = esquivar(centro_mm[0] + ux * d, centro_mm[1] + uy * d, w, h, rects, superficie_mm, objetos=objetos)
        ux, uy, d = x - centro_mm[0], y - centro_mm[1], 1.0
    html = f'<span class="sd-etiqueta{" duda" if duda else ""}">{_e(texto)}</span>'
    css = (
        "body{display:flex;align-items:center;justify-content:center}"
        ".sd-etiqueta{animation:corta .01s linear both}.sd-etiqueta.duda{border-style:dashed}"
        "@keyframes corta{from{opacity:0}}"
    )
    return _html_widget(
        f"solar-nombre-{planeta['id']}", html, css, 4,
        anchor={"object_id": oid(planeta["id"]), "offset_mm": [round(ux * d, 1), round(uy * d, 1)]},
        params={"size_mm": [round(w, 1), h]},
    )


def widget_lamina(lamina: dict, planetas_lamina: dict[str, dict], elegido: str | None = None) -> dict:
    """Tocar la lámina: cada planeta impreso es un blanco. Son más chicos que los 72 mm del sistema común porque los
    dibujos están a ~20 mm uno de otro; cada blanco toma la mitad de la distancia a su vecino más cercano."""
    largo, ancho = lamina["tam_mm"]
    cx, cy = lamina["centro_mm"]
    a = math.radians(lamina["angulo_deg"])
    u = (math.cos(a), math.sin(a))
    v = (-u[1], u[0])
    local = {}
    for pid, d in planetas_lamina.items():
        rel = (d["centro_mm"][0] - cx, d["centro_mm"][1] - cy)
        local[pid] = (rel[0] * u[0] + rel[1] * u[1] + largo / 2, rel[0] * v[0] + rel[1] * v[1] + ancho / 2, d["radio_mm"])
    # La lámina impresa se ve con la luz de la pieza: encima va negro (proyectar negro es no echar luz), que además
    # tapa el fondo de espacio que pueda haber debajo. Los anillos solo aparecen con la mano encima o al elegir.
    partes = ['<div class="tapa"></div>']
    for i, (pid, (x, y, r)) in enumerate(local.items()):
        vecino = min((math.hypot(x - x2, y - y2) for q, (x2, y2, _) in local.items() if q != pid), default=40)
        toque = max(r + 3, min(vecino / 2, 24))
        anillo = r + 3
        partes.append(
            f'<div class="pl{" sel" if pid == elegido else ""}" data-press="{_e(pid)}" style="left:{x - toque:.1f}px;top:{y - toque:.1f}px;width:{2 * toque:.1f}px;height:{2 * toque:.1f}px">'
            f'<i style="inset:{toque - anillo:.1f}px;animation-delay:{REACCION[min(i, 9)] * .6:.2f}s"></i></div>'
        )
    css = (
        ".tapa{position:absolute;inset:-4px;background:#000}"
        ".pl{position:absolute;border-radius:50%;outline:none!important}"
        ".pl i{position:absolute;border-radius:50%;border:3px solid transparent;animation:corta .2s linear both;"
        "transition:border-color var(--sd-rapido) var(--sd-curva),transform var(--sd-rapido) var(--sd-curva)}"
        ".pl[data-pcx-hover] i{border-color:var(--sd-foco);transform:scale(calc(1 + var(--press,0) * .3))}"
        ".pl.sel i{border:4px solid var(--sd-foco)}"
        "@keyframes corta{from{opacity:0}}"
    )
    return _html_widget(
        "solar-lamina", "".join(partes), css, 2,
        rect_mm=[round(cx - largo / 2, 1), round(cy - ancho / 2, 1), round(largo, 1), round(ancho, 1)],
        params={"rotate_deg": round(lamina["angulo_deg"], 2)},
    )


FICHA_MM = (360, 262)


def lugar_desde_sol(planeta: dict) -> str:
    if planeta["id"] == "sol":
        return "La estrella del centro"
    if planeta["id"] == "luna":
        return "Gira alrededor de la Tierra"
    if planeta["id"] == "pluton":
        return "Planeta enano"
    return f"{int(planeta['orden'])}.º desde el Sol"


def widget_ficha(planeta: dict, rect_mm: list[float], dato: int = 0, rotate_deg: float = 0.0) -> dict:
    """Explicar: nombre, lugar desde el Sol y un dato (el `dato`-ésimo; tocar de nuevo pasa al siguiente)."""
    datos = planeta.get("datos") or [""]
    texto = datos[dato % len(datos)]
    lineas = caja_texto(texto, rect_mm[2] - 40)[:3]
    html = (
        f'<div class="sd-panel"><h1>{_e(planeta["nombre"])}</h1><p class="sd-paso">{_e(lugar_desde_sol(planeta))}</p>'
        f'<p class="dato">{"<br>".join(_e(l) for l in lineas)}</p></div>'
    )
    css = (
        ".sd-panel{animation:abre .45s var(--sd-curva) both}.dato{line-height:1.25}"
        "@keyframes abre{from{opacity:0;transform:translateY(6px)}}"
    )
    return _html_widget("solar-ficha", html, css, 5, rect_mm=[round(v, 1) for v in rect_mm], params={"rotate_deg": round(rotate_deg, 2)})


def _trazo_libre(centro, radio, caja, obstaculos):
    """Ruta al borde más próximo de la pista que no atraviesa otro objeto."""
    cx, cy = centro
    x, y, w, h = caja
    px = min(max(cx, x + 12), x + w - 12)
    puertos = [(x - 3, y + 17, -1, 0), (x + w + 3, y + 17, 1, 0),
               (px, y - 3, 0, -1), (px, y + h + 3, 0, 1)]
    for ex, ey, nx, ny in sorted(puertos, key=lambda p: math.dist(centro, p[:2])):
        if (cx - ex) * nx + (cy - ey) * ny <= 0:
            continue  # entrar desde la cara visible, sin cruzar la propia pista
        distancia = math.dist(centro, (ex, ey))
        if distancia <= radio + 18:
            continue
        dx, dy = (ex - cx) / distancia, (ey - cy) / distancia
        sx, sy = cx + dx * (radio + 11), cy + dy * (radio + 11)
        d = min(38, math.dist((sx, sy), (ex, ey)) * .4)
        curva = d * .43
        puntos = (
            (sx, sy), (sx + dx * d - dy * curva, sy + dy * d + dx * curva),
            (ex + nx * d + ny * curva, ey + ny * d - nx * curva), (ex, ey),
        )
        for i in range(15):
            t = i / 14
            k = 1 - t
            pesos = (k**3, 3*k*k*t, 3*k*t*t, t**3)
            mx, my = (sum(p[eje] * peso for p, peso in zip(puntos, pesos)) for eje in (0, 1))
            if any(rx < mx < rx + rw and ry < my < ry + rh for rx, ry, rw, rh in obstaculos):
                break
        else:
            return puntos
    return None


def widget_burbuja(
    quien: dict, texto: str, radio_mm: float, hacia: tuple[float, float], centro_mm=None,
    superficie_mm=(1000, 563), segundos: float | None = None, junto_a: str | None = None,
    reservado=None, objetos=(),
) -> dict:
    """Pista en luz sobre mesa libre. El punto nace fuera de la bola y se sitúa junto al texto."""
    ancho = 326 if len(texto) < 52 else 386
    lineas = textwrap.wrap(texto, int((ancho - 24) / (LETRA_MM * .88)))
    if len(lineas) == 2 and len(lineas[1]) < len(lineas[0]) * .5 and " " in lineas[0]:
        primera, ultima = lineas[0].rsplit(" ", 1)
        lineas = [primera, f"{ultima} {lineas[1]}"]
    alto = 35 * len(lineas) + 8
    ux, uy = hacia
    n = math.hypot(ux, uy) or 1
    ux, uy = ux / n, uy / n
    d = radio_mm + 24 + abs(ux) * ancho / 2 + abs(uy) * alto / 2
    cx, cy = centro_mm or (0, 0)
    bx, by = cx + ux * d, cy + uy * d
    zonas = [*zonas_reservadas(superficie_mm), zona_franja(superficie_mm, False)] if reservado is None else reservado
    if centro_mm is not None:
        diametro = radio_mm * 2
        bola = (cx - radio_mm, cy - radio_mm, diametro, diametro)
        bx, by = esquivar(bx, by, ancho, alto, zonas, superficie_mm, objetos=(*objetos, bola))
    tx, ty = bx - ancho / 2, by - alto / 2
    filas = "".join(f'<span class="wui-linea">{_e(l)}</span>' for l in lineas)
    enlace = ""
    x0, y0, x1, y1 = tx, ty, tx + ancho, ty + alto

    puntos = _trazo_libre((cx, cy), radio_mm, (tx, ty, ancho, alto), (*zonas, *objetos)) if centro_mm is not None else None
    if puntos:
        xs, ys = zip(*puntos)
        x0, y0, x1, y1 = min(tx, min(xs) - 5), min(ty, min(ys) - 5), max(x1, max(xs) + 5), max(y1, max(ys) + 5)
        coords = [f"{x - x0:.1f},{y - y0:.1f}" for x, y in puntos]
        sx, sy = puntos[0]
        enlace = (
            f'<svg class="solar-enlace" viewBox="0 0 {x1 - x0:.1f} {y1 - y0:.1f}">'
            f'<circle cx="{sx - x0:.1f}" cy="{sy - y0:.1f}" r="3"/>'
            f'<path pathLength="1" d="M{coords[0]} C{" ".join(coords[1:])}"/></svg>'
        )

    html = (
        f'{enlace}<p class="wui-pista solar-pista" style="left:{tx - x0:.1f}px;top:{ty - y0:.1f}px;'
        f'width:{ancho}px">{filas}</p>'
    )
    css = """
      .solar-pista { position:absolute; font-weight:500; line-height:1.25; padding-top:3px;
        text-shadow:0 1px 3px #000, 0 0 9px #000; }
      .solar-pista .wui-linea { white-space:nowrap; }
      .solar-enlace { width:100%; height:100%; position:absolute; inset:0; overflow:visible; }
      .solar-enlace path { fill:none; stroke:currentColor; stroke-width:2.4; stroke-linecap:round; stroke-dasharray:1;
        animation:solar-tender 1720ms var(--wui-curva) both; }
      .solar-enlace circle { fill:currentColor; animation:solar-punto 1720ms var(--wui-curva) both; }
      .solar-enlace + .wui-pista::before { animation-name:solar-guion; }
      @keyframes solar-tender { 0% { stroke-dashoffset:1; opacity:0; } 24.42% { stroke-dashoffset:.9; opacity:.8; }
        51.16% { stroke-dashoffset:.35; opacity:.9; } 74.42% { stroke-dashoffset:0; opacity:.8; }
        100% { stroke-dashoffset:-1; opacity:0; } }
      @keyframes solar-punto { 0% { opacity:0; } 24.42%, 51.16% { opacity:1; } 100% { opacity:0; } }
      @keyframes solar-guion { 0%, 65% { opacity:0; transform:scaleX(.45); } 100% { opacity:1; transform:none; } }
      @media (prefers-reduced-motion:reduce) { .solar-enlace { display:none; } }
    """
    return _html_widget(
        "solar-burbuja", html, css, 6,
        anchor={"object_id": oid(junto_a or quien["id"]),
                "offset_mm": [round((x0 + x1) / 2 - cx, 1), round((y0 + y1) / 2 - cy, 1)]},
        params={"size_mm": [round(x1 - x0, 1), round(y1 - y0, 1)],
                "wui_recuperacion": {"texto": "Pon el planeta a la vista",
                                     "centro_mm": [round(bx, 1), round(by, 1)], "ancho_mm": ancho}},
    )


# ---------- Lumo, la guía ----------

LUMO_MM = 40  # alto del cuadro de Lumo


def _lumo_html() -> str:
    return '<div class="lumo"><div class="cuerpo"><i class="ojo a"></i><i class="ojo b"></i><i class="brillo"></i></div></div>'


def _lumo_css() -> str:
    return (
        ".lumo{position:absolute;width:34px;height:34px}"
        ".cuerpo{position:absolute;inset:0;border-radius:50% 50% 46% 46%;"
        "background:radial-gradient(circle at 38% 32%,var(--solar-lumo-brillo) 0 16%,var(--solar-lumo) 46%,#ff9f1c 100%)}"
        ".ojo{position:absolute;top:13px;width:5px;height:7px;border-radius:50%;background:#14183f;animation:parpadea 4.2s infinite}"
        ".ojo.a{left:10px}.ojo.b{right:10px}"
        ".brillo{position:absolute;left:7px;top:6px;width:6px;height:4px;border-radius:50%;background:#fff;opacity:.85}"
        "@keyframes parpadea{0%,92%,100%{transform:scaleY(1)}95%{transform:scaleY(.1)}}"
    )


def posicion_lumo(radio_mm: float, hacia: tuple[float, float]) -> tuple[float, float]:
    """Dónde se posa Lumo respecto del centro de la bola: hacia afuera y de costado (la burbuja sale derecho hacia
    afuera, así no se tapan)."""
    ux, uy = hacia
    n = math.hypot(ux, uy) or 1
    ux, uy = ux / n, uy / n
    px, py = -uy, ux
    d = radio_mm + 26
    return (round((ux * .5 + px) * d, 1), round((uy * .5 + py) * d, 1))


def widget_lumo(pid: str, radio_mm: float, hacia: tuple[float, float], offset_mm: tuple[float, float] | None = None) -> dict:
    """Lumo posado junto a la bola (en `offset_mm` desde su centro, o en `posicion_lumo`), flotando suave."""
    css = _lumo_css() + (
        ".lumo{left:3px;top:3px;animation:flota 3.2s var(--sd-curva) infinite alternate}"
        "@keyframes flota{to{transform:translateY(-5px) rotate(4deg)}}"
    )
    return _html_widget(
        "solar-lumo", _lumo_html(), css, 7,
        anchor={"object_id": oid(pid), "offset_mm": [round(v, 1) for v in (offset_mm or posicion_lumo(radio_mm, hacia))]},
        params={"size_mm": [LUMO_MM, LUMO_MM]},
    )


def widget_lumo_vuelo(desde_mm: tuple[float, float], hasta_mm: tuple[float, float], segundos: float = 1.4) -> dict:
    """Lumo vuela en curva de `desde` a `hasta` (mm de la mesa) y se acomoda con un resorte al llegar.
    El conductor lo reemplaza por `widget_lumo` (anclado) cuando termina el vuelo."""
    (x1, y1), (x2, y2) = desde_mm, hasta_mm
    m = 40
    x0, y0 = min(x1, x2) - m, min(y1, y2) - m - 60
    w, h = abs(x2 - x1) + 2 * m, abs(y2 - y1) + 2 * m + 60
    a = (x1 - x0, y1 - y0)
    b = (x2 - x0, y2 - y0)
    # Arco hacia arriba: el punto de control queda sobre el punto medio, levantado según la distancia.
    lift = min(140, math.dist(a, b) * 0.35)
    ctrl = ((a[0] + b[0]) / 2, min(a[1], b[1]) - lift + 60)
    camino = f"M {a[0]:.1f} {a[1]:.1f} Q {ctrl[0]:.1f} {ctrl[1]:.1f} {b[0]:.1f} {b[1]:.1f}"
    css = _lumo_css() + (
        f".lumo{{left:0;top:0;offset-anchor:50% 50%;offset-path:path('{camino}');offset-rotate:0deg;"
        f"animation:vuela {segundos:.2f}s var(--sd-curva) both,asienta .5s var(--sd-curva) {segundos:.2f}s both}}"
        "@keyframes vuela{from{offset-distance:0%}to{offset-distance:100%}}"
        "@keyframes asienta{0%{transform:scale(1.15,.85)}100%{transform:scale(1)}}"
    )
    return _html_widget("solar-lumo", _lumo_html(), css, 7, rect_mm=[round(x0, 1), round(y0, 1), round(w, 1), round(h, 1)])


def widget_fondo_sim(src_data_url: str, superficie_mm=(1000, 563)) -> dict:
    """Solo para probar con `?sim=1`: la foto real de la mesa como `image` (la cámara simulada dibuja los image, no
    los html), así la cámara simulada «ve» el canasto."""
    return {"type": "image", "id": "solar-fondo-sim", "src": src_data_url, "rect_mm": [0, 0, *superficie_mm], "z": -10}

"""Capa visual del Sistema solar: óleo nocturno (guion v2) sobre lo que publica la percepción.

Funciones puras: reciben centros, radios y polígonos en mm (lo de `solar:*`) y devuelven widgets para `project`
(`effect` con `shader: "html"`), sobre el sistema común (base.css + tema.css de la escena, vía `escena.css_sistema`).
Mismos ids que los widgets de h20 (`solar-sol`, `solar-orbitas`, `solar-planeta-<id>`): reemplazan a los suyos sin
tocar el seguimiento. Lo nuevo es el fondo (`solar-fondo`, `solar-fondo-2`).

Reglas de la mesa (SAT, 21-nov):
- Ningún rect pisa la barra de pestañas ([0, 0, 150, H]), la esquina de voz ([W-130, 0, 130, 130]) ni la franja de
  abajo (`SOLAR_FRANJA_MM`, 40 por defecto). El fondo va en dos piezas que las rodean; las órbitas se recortan a la
  zona libre y siguen ancladas (con `offset_mm`), centradas donde corresponde.
- Cada esfera va exactamente en el `center_mm` y el `radius_mm` del objeto, sin escala ni desplazamiento propios.
- El Sol usa el contorno real del engranaje (`sol_poligono_mm` de h20, o la silueta de la foto limpia).
- La lámina no lleva fondo ni órbitas encima: el widget de la lámina de h20 (z 2) pone negro sobre ella.
- Sin Sol, las órbitas se anclan a `solar:canasto` (h20 lo publica siempre). Un planeta que no se ve ahora (fantasma
  de un armado) se dibuja atenuado y quieto, con su órbita tenue, sin sacarlo.
- `seguir` compara fotos con proyección dentro del canasto + 60 mm y solo descuenta los discos (planetas 1,15 r, Sol
  1,35 r) y, si hay Sol, las órbitas. Por eso ahí nada más se anima: bajo las órbitas va la «calma» (negro quieto, sin
  luz) que tapa el fondo en esa zona, la corona del Sol no pasa de 1,3 r y sin Sol las órbitas quedan quietas.
- `look clean:true` apaga todo lo proyectado (la consola pone negro también el html): la foto limpia no ve nada de esto.
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

ESCENA = Path(__file__).resolve().parents[1]
if str(ESCENA) not in sys.path:
    sys.path.insert(0, str(ESCENA))

import escena as E  # noqa: E402  (css del sistema, ids, zonas reservadas y texturas de h20)

PLANETAS = {p["id"]: p for p in json.loads((ESCENA / "planetas.json").read_text(encoding="utf-8"))["planetas"]}
BARRA_MM = E.BARRA_MM  # pestañas de la consola, a la izquierda
VOZ_MM = E.VOZ_MM  # esquina del botón Hablar
FRANJA_MM = E.FRANJA_MM  # franja gris de la consola, abajo
MARGEN_CANASTO_MM = 60  # la zona donde `seguir` busca movimiento: la caja del canasto + 60 mm
CALMA_SOLIDA_MM = 30  # la calma es negra pareja hasta 30 mm fuera de esa zona…
CALMA_DIFUSA_MM = 45  # …y se desvanece en 45 mm más
FANTASMA_OPACIDAD = 0.38  # un planeta que no se ve ahora: atenuado

CSS_VISUAL = """
:root{
  --solar-cielo:#1e2e71; --solar-cielo-hondo:#080e29; --solar-nebulosa:#53355e;
  --solar-estrella:#dae4ff; --solar-cadmio:#ffc21a; --solar-fondo-opacidad:.62;
  --solar-sol-1:#fff8d8; --solar-sol-2:#ffd23f; --solar-sol-3:#ff9a1f; --solar-sol-4:#e2531a;
}
"""


def _w(wid: str, html: str, css: str, z: int, **extra) -> dict:
    return E._html_widget(wid, html, CSS_VISUAL + css, z, **extra)


# ------------------------------------------------------------------------------------------ zonas


def reservas(superficie_mm=(1000, 563), girada: bool = False, franja_mm: float = FRANJA_MM) -> list[list[float]]:
    """Barra, esquina de voz y franja de abajo (con la consola girada, todo rota 180°)."""
    franja = E.zona_franja(superficie_mm, girada, franja_mm)
    return E.zonas_reservadas(superficie_mm, girada) + ([franja] if franja_mm > 0 else [])


def zonas_libres(superficie_mm=(1000, 563), girada: bool = False, franja_mm: float = FRANJA_MM) -> list[list[float]]:
    """Dos rects que cubren la zona de la escena sin tocar ninguna reserva: el grande y la columna bajo la voz."""
    W, H = superficie_mm
    if girada:
        return [[VOZ_MM, franja_mm, W - BARRA_MM - VOZ_MM, H - franja_mm], [0, franja_mm, VOZ_MM, H - franja_mm - VOZ_MM]]
    return [[BARRA_MM, 0, W - BARRA_MM - VOZ_MM, H - franja_mm], [W - VOZ_MM, VOZ_MM, VOZ_MM, H - franja_mm - VOZ_MM]]


def recortar_caja(x0: float, y0: float, x1: float, y1: float, superficie_mm=(1000, 563), girada: bool = False, franja_mm: float = FRANJA_MM, holgura: float = 1.0):
    """La caja achicada lo mínimo para que no toque ninguna reserva ni salga de la mesa. None si no queda nada.
    `holgura`: mm de aire contra cada reserva (el tamaño y el offset van redondeados a 0,1 mm y la consola avisa con
    cualquier traslape, por chico que sea)."""
    W, H = superficie_mm
    x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(float(W), x1), min(float(H), y1)
    for rx, ry, rw, rh in reservas(superficie_mm, girada, franja_mm):
        rx, ry, rw, rh = rx - holgura, ry - holgura, rw + 2 * holgura, rh + 2 * holgura
        if x0 < rx + rw and x1 > rx and y0 < ry + rh and y1 > ry:
            opciones = [(rx + rw, y0, x1, y1), (x0, y0, rx, y1), (x0, ry + rh, x1, y1), (x0, y0, x1, ry)]
            opciones = [o for o in opciones if o[2] > o[0] and o[3] > o[1]]
            if not opciones:
                return None
            x0, y0, x1, y1 = max(opciones, key=lambda o: (o[2] - o[0]) * (o[3] - o[1]))
    return (x0, y0, x1, y1) if x1 - x0 > 1 and y1 - y0 > 1 else None


def zona_de(canasto_mm, margen: float = MARGEN_CANASTO_MM) -> list[float] | None:
    """La caja del canasto + margen, [x, y, w, h] (lo mismo que `zona_actual` de h20)."""
    if canasto_mm is None or len(canasto_mm) < 3:
        return None
    xs, ys = [p[0] for p in canasto_mm], [p[1] for p in canasto_mm]
    return [min(xs) - margen, min(ys) - margen, max(xs) - min(xs) + 2 * margen, max(ys) - min(ys) + 2 * margen]


# ------------------------------------------------------------------------------------------ fondo


def _cielo(wid: str, rect, superficie, semilla: int) -> dict:
    x0, y0, w, h = rect
    W, H = superficie
    rng = random.Random(semilla)
    stars = []
    for i in range(48):
        x, y = rng.uniform(W * .19, W * .94), rng.uniform(H * .06, H * .92)
        size = rng.choice((1.4, 2.1, 2.6, 3.5))
        if not (x0 <= x <= x0 + w and y0 <= y <= y0 + h):
            continue
        shape = "e estrella" if i % 13 == 0 else "e"
        stars.append(
            f'<i class="{shape}" style="left:{x:.1f}px;top:{y:.1f}px;--r:{size:.1f}px;'
            f'--inicio:-{rng.uniform(0, 8):.1f}s"></i>'
        )
    html = f'<div class="cielo"><div class="luz"></div>{"".join(stars)}</div>'
    css = f"""
      .cielo {{ position:absolute; left:{-x0:.1f}px; top:{-y0:.1f}px; width:{W:.1f}px; height:{H:.1f}px;
        opacity:var(--solar-fondo-opacidad); animation:solar-alba 1720ms var(--sd-curva) both; }}
      .luz {{ position:absolute; inset:0;
        background: radial-gradient(ellipse 42% 51% at 48% 45%,var(--solar-cielo),transparent 96%),
          radial-gradient(ellipse 33% 42% at 72% 48%,var(--solar-nebulosa),transparent 98%),
          radial-gradient(ellipse 55% 75% at 62% 49%,var(--solar-cielo-hondo),transparent 99%);
        animation:solar-marea 19s ease-in-out infinite alternate; }}
      .e {{ position:absolute; width:var(--r); height:var(--r); border-radius:50%; background:var(--solar-estrella);
        opacity:.48; transform:translate(-50%,-50%); animation:solar-centelleo 8s ease-in-out var(--inicio) infinite; }}
      .estrella {{ width:calc(var(--r) * 2.8); height:calc(var(--r) * 2.8); border-radius:0;
        clip-path:polygon(50% 0,62% 38%,100% 50%,62% 62%,50% 100%,38% 62%,0 50%,38% 38%); }}
      @keyframes solar-alba {{ from {{ opacity:0; }} }}
      @keyframes solar-marea {{ to {{ opacity:.72; }} }}
      @keyframes solar-centelleo {{ 50% {{ opacity:.14; }} }}
    """
    return _w(wid, html, css, -5, rect_mm=[round(v, 1) for v in rect])


def fondo(superficie_mm=(1000, 563), girada: bool = False, franja_mm: float = FRANJA_MM, semilla: int = 7) -> list[dict]:
    """Una sola luz ambiente, recortada en dos ventanas para respetar la consola."""
    grande, columna = zonas_libres(superficie_mm, girada, franja_mm)
    out = [_cielo("solar-fondo", grande, superficie_mm, semilla)]
    if columna[2] > 1 and columna[3] > 1:
        out.append(_cielo("solar-fondo-2", columna, superficie_mm, semilla))
    return out


# ------------------------------------------------------------------------------------------ planetas


def silueta(obj: dict) -> tuple[tuple[float, float], list[list[float]], float] | None:
    """De un objeto del mundo ({center_mm, polygon_mm}): (centro, contorno relativo al centro, radio hasta el punto
    más lejano). None si no trae un polígono usable."""
    c = obj.get("center_mm")
    poly = obj.get("polygon_mm") or []
    if not c or len(poly) < 3:
        return None
    rel = [[round(x - c[0], 1), round(y - c[1], 1)] for x, y in poly]
    r = max(math.hypot(x, y) for x, y in rel)
    return (float(c[0]), float(c[1])), rel, r


def es_circulo(rel: list[list[float]], tolerancia: float = 0.04) -> bool:
    """¿El contorno es un círculo (todos los puntos a la misma distancia del centro), sin dientes?"""
    rs = [math.hypot(x, y) for x, y in rel]
    m = sum(rs) / len(rs)
    return m > 0 and (sum((r - m) ** 2 for r in rs) / len(rs)) ** 0.5 / m < tolerancia


def esfera(planeta: dict, radio_mm: float, hacia_sol: tuple[float, float], duda: bool = False, retraso: float = 0.0, giro_s: float = 26.0, fantasma: bool = False, silueta_rel=None) -> dict:
    """El planeta como esfera sobre su bola, exactamente del radio del objeto: textura saturada que gira, relieve,
    terminador hacia el lado opuesto al Sol y un aro oscuro (sin luz, quieto) justo alrededor, para que la bola blanca
    se lea en 3D con la luz de la sala. El widget va centrado en el objeto, sin desplazamiento.

    `silueta_rel`: el contorno del objeto relativo a su `center_mm` (el que publica el motor): la esfera se recorta a esa
    forma; `radio_mm` es entonces el radio hasta su punto más lejano.
    `fantasma`: la bola no se ve ahora (h20 la deja un armado en su última posición): atenuada y sin girar."""
    r = radio_mm
    d = 2 * r
    anillos = planeta.get("textura") == "anillos"
    aro = r * 0.32  # ancho del aro oscuro alrededor de la bola
    lado = max(d * (2.4 if anillos else 1.0), d + 2 * aro) + 8
    c = lado / 2
    o = c - r
    n = math.hypot(*hacia_sol) or 1.0
    ux, uy = hacia_sol[0] / n, hacia_sol[1] / n  # unitario hacia el Sol (mesa, y hacia abajo)
    lx, ly = 50 + 20 * ux, 50 + 20 * uy  # centro del lado de día
    bx, by = 50 + 26 * ux, 50 + 26 * uy  # brillo especular
    pid = planeta["id"]
    vel = giro_s * (1 + planeta.get("orden", 3) * 0.12)
    c1, c2 = planeta["colores"][0], planeta["colores"][1]
    atm = {"tierra": "#7fd0ff", "venus": "#ffe6a6", "urano": "#bff4ff", "neptuno": "#7f9cff"}.get(pid)
    capas_anillo = ""
    if anillos:
        ra, rb = r * 1.95, r * 0.56
        capas_anillo = f'<div class="anillo atras" style="left:{c - ra:.1f}px;top:{c - rb:.1f}px;width:{2 * ra:.1f}px;height:{2 * rb:.1f}px"></div>'
    frente = capas_anillo.replace("atras", "frente") if anillos else ""
    ra_ext = r + aro
    html = (
        f'<div class="aro" style="left:{c - ra_ext:.1f}px;top:{c - ra_ext:.1f}px;width:{2 * ra_ext:.1f}px;height:{2 * ra_ext:.1f}px"></div>'
        f'{capas_anillo}<div class="pl{" duda" if duda else ""}{" fantasma" if fantasma else ""}" style="left:{o:.1f}px;top:{o:.1f}px;width:{d:.1f}px;height:{d:.1f}px">'
        f'<div class="tex"></div><div class="relieve"></div><div class="noche"></div></div>{frente}'
    )
    mascara = "radial-gradient(circle closest-side,#000 calc(100% - 1.5px),transparent 100%)"
    css = (
        # el aro: negro (sin luz) pegado a la bola que se desvanece hacia afuera; recorta la bola contra el fondo
        f".aro{{position:absolute;border-radius:50%;background:radial-gradient(circle closest-side,#000 0,#000 {r / ra_ext * 100:.1f}%,"
        f"rgba(0,0,0,.85) {(r + aro * .45) / ra_ext * 100:.1f}%,transparent 100%)}}"
        f".pl{{position:absolute;border-radius:50%;animation:llega 1.28s var(--sd-curva) {retraso:.2f}s both;"
        + (f"box-shadow:0 0 {r * .12:.1f}px {r * .03:.1f}px {atm}66;" if atm and not silueta_rel else "")
        + ("clip-path:polygon(" + ",".join(f"{r + x:.1f}px {r + y:.1f}px" for x, y in silueta_rel) + ");" if silueta_rel else "")
        + "}"
        f".tex,.relieve,.noche{{position:absolute;inset:0;border-radius:50%;-webkit-mask:{mascara};mask:{mascara}}}"
        f".tex{{background:{E.textura(planeta, d)};background-size:{2 * d:.1f}px {d:.1f}px;"
        f"filter:saturate(1.18) contrast(1.04) brightness(1.06);animation:gira {vel:.1f}s linear infinite}}"
        f".relieve{{background:radial-gradient(circle at {bx:.0f}% {by:.0f}%,rgba(255,252,236,.44) 0,rgba(255,250,230,.15) 24%,transparent 52%),"
        f"radial-gradient(circle closest-side at 50% 50%,transparent 66%,rgba(16,14,38,.24) 91%,rgba(16,14,38,.47) 100%)}}"
        f".noche{{background:radial-gradient(circle {d * .6:.1f}px at {lx:.0f}% {ly:.0f}%,transparent 0,transparent 55%,rgba(7,9,27,.65) 94%,rgba(0,0,12,.87) 110%)}}"
        f".duda{{outline:3px dashed var(--sd-tenue);outline-offset:5px}}"
        + (f".fantasma{{opacity:{FANTASMA_OPACIDAD};filter:grayscale(.5)}}.fantasma .tex{{animation:none}}.anillo{{opacity:{FANTASMA_OPACIDAD * .9:.2f}!important}}" if fantasma else "")
        + f".anillo{{position:absolute;border-radius:50%;transform:rotate(-16deg);opacity:.90;"
        f"background:radial-gradient(ellipse closest-side,transparent 0 57%,{c2}88 60%,{c1} 66%,transparent 69%,{c2}88 71%,{c2} 78%,transparent 84%);"
        f"animation:llega 1.28s var(--sd-curva) {retraso + .15:.2f}s both}}"
        f".atras{{clip-path:inset(0 0 50% 0)}}.frente{{clip-path:inset(50% 0 0 0)}}"
        f"@keyframes gira{{to{{background-position:-{2 * d:.1f}px 0}}}}"
        f"@keyframes llega{{0%{{opacity:0;transform:scale(.78)}}42%{{opacity:.9;transform:scale(.98)}}100%{{opacity:1}}}}"
    )
    return _w(f"solar-planeta-{pid}", html, css, 1, anchor={"object_id": E.oid(pid)}, params={"size_mm": [round(lado, 1), round(lado, 1)]})


# ------------------------------------------------------------------------------------------ Sol


def _circulo(cx: float, cy: float, r: float, n: int = 48) -> list[list[float]]:
    return [[cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)] for k in range(n)]


def contorno_engranaje(img_mm, centro_mm, radio_mm: float, n: int = 144) -> list[list[float]] | None:
    """La silueta real del engranaje en una foto limpia en mm (la de `armar`, `estado/referencia.jpg`): rayos desde el
    centro hasta el borde de la máscara blanca fina de la percepción de h20 (la que ve los dientes). Devuelve el
    contorno relativo al centro, [[dx, dy], …] en mm, o None si no parece un engranaje (rayos que se escapan a una
    bola vecina o se cortan antes)."""
    try:
        import numpy as np

        from percepcion.cuerpos import PARAMS, _rayos, mascara_blanca
    except Exception:  # noqa: BLE001  (sin la percepción de h20, el círculo)
        return None
    fina = mascara_blanca(img_mm, PARAMS, PARAMS["apertura_fina_mm"])
    largos = _rayos(fina, (float(centro_mm[0]), float(centro_mm[1])), radio_mm * 1.3, n).astype(float)
    malos = (largos < 0.6 * radio_mm) | (largos > 1.15 * radio_mm)
    if malos.mean() > 0.25:
        return None
    largos[malos] = np.median(largos[~malos])
    # mediana de 3 (circular): borra un píxel suelto sin comerse los dientes
    largos = np.median(np.stack([np.roll(largos, -1), largos, np.roll(largos, 1)]), axis=0)
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return [[round(float(math.cos(a) * l), 1), round(float(math.sin(a) * l), 1)] for a, l in zip(ang, largos)]


def girar_contorno(rel: list[list[float]], centro_mm, theta_deg: float = 0.0) -> list[list[float]]:
    """El contorno relativo de `contorno_engranaje` puesto en `centro_mm`, girado con la pose del canasto."""
    t = math.radians(theta_deg)
    c, s = math.cos(t), math.sin(t)
    return [[round(centro_mm[0] + c * x - s * y, 1), round(centro_mm[1] + s * x + c * y, 1)] for x, y in rel]


def sol(radio_mm: float, poligono_mm=None, centro_mm=None) -> dict:
    """Textura solar animada dentro de la silueta del engranaje y su contorno como halo. Todo dentro de 1,3 r (lo
    que `seguir` descuenta como animado alrededor del Sol).

    `poligono_mm`: el contorno del engranaje en la mesa (de `contorno_engranaje` o la máscara de la segmentación); sin
    él, el círculo de `radio_mm`. Se ancla a `solar:sol`, así que el contorno se pasa relativo a `centro_mm`."""
    r = radio_mm
    lado = r * 2.6
    c = lado / 2
    if poligono_mm and centro_mm is not None and len(poligono_mm) >= 3:
        pts = [[x - centro_mm[0] + c, y - centro_mm[1] + c] for x, y in poligono_mm]
    else:
        pts = _circulo(c, c, r)
    poly_css = ",".join(f"{x:.1f}px {y:.1f}px" for x, y in pts)
    poly_svg = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    html = (
        '<div class="corona"></div>'
        f'<div class="disco"><div class="plasma"></div><div class="granos"></div><div class="nucleo"></div></div>'
        f'<svg viewBox="0 0 {lado:.1f} {lado:.1f}"><polygon class="borde" points="{poly_svg}"/><polygon class="borde fino" points="{poly_svg}"/></svg>'
    )
    k = 100 / 1.3  # % del radio del widget (1,3 r) que ocupa 1 r
    mascara_corona = f"radial-gradient(circle closest-side,transparent {.95 * k:.0f}%,#000 {1.03 * k:.0f}%,transparent 100%)"
    css = (
        f"svg{{position:absolute;inset:0;width:100%;height:100%;overflow:hidden}}"
        f".corona{{position:absolute;inset:0;border-radius:50%;"
        f"background:radial-gradient(circle closest-side,transparent 0 {.9 * k:.0f}%,rgba(255,170,30,.6) {k:.0f}%,rgba(255,140,20,.2) {1.15 * k:.0f}%,transparent 100%),"
        f"radial-gradient(ellipse at 46% 46%,rgba(255,165,64,.14),transparent 64%);"
        f"-webkit-mask:{mascara_corona};mask:{mascara_corona};"
        f"animation:gira 70s linear infinite,aparece 1.2s var(--sd-curva) both}}"
        f".disco{{position:absolute;inset:0;clip-path:polygon({poly_css});animation:aparece 880ms var(--sd-curva) both}}"
        f".plasma,.granos,.nucleo{{position:absolute;inset:0}}"
        f".plasma{{background:radial-gradient(circle at 50% 50%,var(--solar-sol-1) 0,var(--solar-sol-2) {r * .55:.0f}px,var(--solar-sol-3) {r * .9:.0f}px,var(--solar-sol-4) {r * 1.05:.0f}px)}}"
        f".granos{{mix-blend-mode:soft-light;opacity:.47;"
        f"background:radial-gradient(ellipse at 59% 30%,#ffefb6,transparent 43%),radial-gradient(ellipse at 27% 71%,#e46f39,transparent 48%),radial-gradient(ellipse at 75% 74%,#ffed94,transparent 45%);"
        f"animation:gira 38s linear infinite}}"
        f".nucleo{{background:radial-gradient(circle at 44% 42%,rgba(255,255,240,.85) 0,rgba(255,248,216,.4) {r * .3:.0f}px,transparent {r * .6:.0f}px);"
        f"animation:late 3.2s ease-in-out infinite}}"
        f".borde{{fill:none;stroke:var(--solar-cadmio);stroke-width:3;stroke-linejoin:round;opacity:.62;"
        f"filter:drop-shadow(0 0 3px #ffc21a) drop-shadow(0 0 7px #ff8a1f);animation:halo 3s ease-in-out infinite}}"
        f".fino{{stroke:#ffdf9d;stroke-width:1.4;filter:none;animation:none}}"
        f"@keyframes gira{{to{{transform:rotate(360deg)}}}}"
        f"@keyframes late{{50%{{opacity:.7;transform:scale(1.04)}}}}"
        f"@keyframes halo{{50%{{opacity:.55;stroke-width:4}}}}"
        f"@keyframes aparece{{from{{opacity:0;transform:scale(.7)}}}}"
    )
    return _w("solar-sol", html, css, 2, anchor={"object_id": E.oid("sol")}, params={"size_mm": [round(lado, 1)] * 2})


# ------------------------------------------------------------------------------------------ órbitas (+ calma)


def orbitas(
    centro,
    planetas_mm: dict[str, tuple[tuple[float, float], float]],
    ancla: dict | None = None,
    ancla_mm=None,
    superficie_mm=(1000, 563),
    girada: bool = False,
    intro: bool = True,
    animadas: bool = True,
    zona_mm=None,
    fantasmas=(),
) -> dict | None:
    """Órbitas de pincel centradas en `centro` (el Sol, o el centro del canasto si no se ve) que pasan por el centro
    de cada bola (`planetas_mm` = {id: (centro, radio)}), con la «calma» debajo: negro quieto sobre `zona_mm` (la
    caja del canasto + 60, donde `seguir` busca movimiento), que baja el fondo cerca de la escena.

    El widget se recorta a la zona libre (no toca la barra, la voz ni la franja) y se ancla a `ancla` (por defecto
    `solar:sol`, que está en `ancla_mm`) con `offset_mm`, así que sigue al canasto. `animadas=False`: trazos quietos
    (sin Sol, `seguir` no descuenta las órbitas como animación). `fantasmas`: ids que no se ven ahora (órbita tenue)."""
    if centro is None or not planetas_mm:
        return None
    ancla = ancla or {"object_id": E.oid("sol")}
    ancla_mm = centro if ancla_mm is None else ancla_mm
    orbs = sorted(((math.dist(centro, c), pid, c, r) for pid, (c, r) in planetas_mm.items()), key=lambda t: t[0])
    medio = orbs[-1][0] + 30
    bx0, by0, bx1, by1 = centro[0] - medio, centro[1] - medio, centro[0] + medio, centro[1] + medio
    if zona_mm is not None:
        borde = CALMA_SOLIDA_MM + CALMA_DIFUSA_MM
        zx, zy, zw, zh = zona_mm
        bx0, by0, bx1, by1 = min(bx0, zx - borde), min(by0, zy - borde), max(bx1, zx + zw + borde), max(by1, zy + zh + borde)
    caja = recortar_caja(bx0, by0, bx1, by1, superficie_mm, girada)
    if caja is None:
        return None
    x0, y0, x1, y1 = caja
    w, h = x1 - x0, y1 - y0
    off = [round((x0 + x1) / 2 - ancla_mm[0], 1), round((y0 + y1) / 2 - ancla_mm[1], 1)]
    anchor = {**ancla, "offset_mm": off} if any(off) else dict(ancla)
    cx, cy = centro[0] - x0, centro[1] - y0  # centro de las órbitas dentro del widget
    calma = ""
    if zona_mm is not None:
        zx, zy, zw, zh = zona_mm
        calma = (
            f'<div class="calma" style="left:{zx - x0:.1f}px;top:{zy - y0:.1f}px;width:{zw:.1f}px;height:{zh:.1f}px;'
            f'box-shadow:0 0 {CALMA_DIFUSA_MM}px {CALMA_SOLIDA_MM}px #000"></div>'
        )
    tapas = "".join(f'<circle cx="{pc[0] - x0:.1f}" cy="{pc[1] - y0:.1f}" r="{pr + 7:.1f}" fill="#000"/>' for _, _, pc, pr in orbs)
    partes = [
        calma,
        f'<svg viewBox="0 0 {w:.1f} {h:.1f}"><defs><mask id="libre" maskUnits="userSpaceOnUse" x="0" y="0" width="{w:.1f}" height="{h:.1f}">'
        f'<rect x="0" y="0" width="{w:.1f}" height="{h:.1f}" fill="#fff"/>{tapas}</mask></defs><g mask="url(#libre)">',
    ]
    for i, (rad, pid, pc, pr) in enumerate(orbs):
        col = E._mezcla(PLANETAS.get(pid, {}).get("colores", ["#9fb0ff"])[0], "#9fb0ff", 0.45)
        retraso = (E.REACCION[min(i, len(E.REACCION) - 1)] + 0.9) if intro else 0
        ang = math.degrees(math.atan2(pc[1] - centro[1], pc[0] - centro[0]))
        estela = ""
        if animadas and pid not in fantasmas:
            periodo = 16 * (1 + i) ** .87
            estela = (
                f'<g transform="rotate({ang:.1f} {cx:.1f} {cy:.1f})">'
                f'<g class="estela" style="animation-duration:{periodo:.1f}s">'
                f'<circle class="brillo" cx="{cx:.1f}" cy="{cy:.1f}" r="{rad:.1f}" pathLength="100"/>'
                f'<circle class="cometa" cx="{cx + rad:.1f}" cy="{cy:.1f}" r="2.4" fill="{col}"/></g></g>'
            )
        partes.append(
            f'<g class="o{" fant" if pid in fantasmas else ""}" style="animation-delay:{retraso:.2f}s;color:{col}">'
            f'<circle class="t1" cx="{cx:.1f}" cy="{cy:.1f}" r="{rad:.1f}" stroke="{col}"/>{estela}</g>'
        )
    partes.append("</g></svg>")

    css = (
        f".calma{{position:absolute;background:#000;border-radius:{MARGEN_CANASTO_MM}px}}"
        "svg{position:absolute;inset:0;width:100%;height:100%;overflow:hidden;"
        "mask:linear-gradient(90deg,transparent,#000 20px,#000 calc(100% - 20px),transparent),"
        "linear-gradient(0deg,transparent,#000 20px,#000 calc(100% - 20px),transparent);mask-composite:intersect}"
        f".fant{{opacity:{FANTASMA_OPACIDAD}}}"
        + (".o{animation:aparece 880ms var(--sd-curva) both}" if intro else "")
        + ".t1{fill:none;stroke-width:var(--solar-pincel);opacity:var(--solar-orbita-opacidad)}"
        + (
            ".brillo{fill:none;stroke:currentColor;stroke-width:2.8;stroke-linecap:round;stroke-dasharray:3 97;stroke-dashoffset:3;opacity:.58}"
            f".estela{{transform-origin:{cx:.1f}px {cy:.1f}px;animation:fluye linear infinite}}"
            "@keyframes fluye{to{transform:rotate(360deg)}}"
            if animadas
            else ""
        )
        + "@keyframes aparece{from{opacity:0}}"
    )
    return _w("solar-orbitas", "".join(partes), css, 0, anchor=anchor, params={"size_mm": [round(w, 1), round(h, 1)]})


# ------------------------------------------------------------------------------------------ todo junto


def _dentro(p, poly, margen: float = 0.0) -> bool:
    """¿El punto cae dentro del polígono (o a menos de `margen` mm de su borde)?"""
    dentro = False
    n = len(poly)
    for i in range(n):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
        if (ay > p[1]) != (by > p[1]) and p[0] < (bx - ax) * (p[1] - ay) / (by - ay) + ax:
            dentro = not dentro
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / ((dx * dx + dy * dy) or 1)))
        if math.dist(p, (ax + t * dx, ay + t * dy)) <= margen:
            return True
    return dentro


def centro_del_sistema(sol_c, planetas: dict[str, dict], canasto_mm=None):
    """Sin el conductor de h20 (la previa): el Sol si cae dentro del canasto; si no, el centro del canasto o el de
    los planetas. → (centro, ¿es el Sol?)"""
    pts = [tuple(p["center_mm"]) for p in planetas.values()]
    if canasto_mm is not None and len(canasto_mm) >= 3:
        respaldo = (sum(p[0] for p in canasto_mm) / len(canasto_mm), sum(p[1] for p in canasto_mm) / len(canasto_mm))
        sol_ok = sol_c is not None and _dentro(sol_c, canasto_mm, 30)
    else:
        respaldo = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)) if pts else None
        lejos = max((math.dist(p, respaldo) for p in pts), default=0) + 40 if respaldo else 0
        sol_ok = sol_c is not None and (respaldo is None or math.dist(sol_c, respaldo) <= lejos)
    return (tuple(sol_c), True) if sol_ok else (respaldo, False)


def widgets(
    sol_cuerpo: dict | None,
    planetas: dict[str, dict],
    superficie_mm=(1000, 563),
    dudas=(),
    intro: bool = True,
    girada: bool = False,
    con_fondo: bool = True,
    canasto_mm=None,
    centro=None,
    zona_mm=None,
    ancla_canasto=None,
    fantasmas=(),
) -> list[dict]:
    """Toda la capa visual. `sol_cuerpo` = {center_mm, radius_mm, polygon_mm?} (None si el Sol no se ve);
    `planetas` = {id: {center_mm, radius_mm}} (ids del catálogo: mercurio, venus…); `fantasmas`: los ids que no se
    ven ahora (se dibujan atenuados, no se sacan).

    `centro`: dónde van las órbitas (el `centro_orbitas` de h20); sin él, el Sol si cae en `canasto_mm` (el contorno
    del canasto en la pose actual) o el centro del canasto. `zona_mm`: la caja del canasto + 60 (sin ella, sale de
    `canasto_mm`). `ancla_canasto`: el `center_mm` de `solar:canasto` (h20 lo publica siempre); sin Sol, las órbitas
    se anclan ahí. Sin él, al planeta más cercano al centro."""
    out = fondo(superficie_mm, girada) if con_fondo else []
    sol_c = tuple(sol_cuerpo["center_mm"]) if sol_cuerpo else None
    if centro is not None:
        centro = tuple(centro)
        es_sol = sol_c is not None and math.dist(centro, sol_c) < 1
    elif sol_c or planetas:
        centro, es_sol = centro_del_sistema(sol_c, planetas, canasto_mm)
    else:
        es_sol = False
    if zona_mm is None:
        zona_mm = zona_de(canasto_mm)
    if es_sol:
        ancla, ancla_mm = {"object_id": E.oid("sol")}, sol_c
    elif ancla_canasto is not None and centro is not None:
        ancla, ancla_mm = {"object_id": E.oid("canasto")}, tuple(ancla_canasto)
    elif planetas and centro is not None:
        pid = min(planetas, key=lambda p: math.dist(planetas[p]["center_mm"], centro))
        ancla, ancla_mm = {"object_id": E.oid(pid)}, tuple(planetas[pid]["center_mm"])
    else:
        ancla = ancla_mm = None
    if sol_cuerpo and es_sol:
        out.append(sol(sol_cuerpo["radius_mm"], sol_cuerpo.get("polygon_mm"), sol_c))
    orden = sorted(planetas, key=lambda p: math.dist(planetas[p]["center_mm"], centro)) if centro else list(planetas)
    for i, pid in enumerate(orden):
        p = planetas[pid]
        x, y = p["center_mm"]
        hacia = (centro[0] - x, centro[1] - y) if centro else (1.0, 0.0)
        retraso = (E.REACCION[min(i, len(E.REACCION) - 1)] + 0.5) if intro else 0.0
        out.append(esfera(PLANETAS[pid], p["radius_mm"], hacia, pid in dudas, retraso, fantasma=pid in fantasmas, silueta_rel=p.get("silueta")))
    if ancla is not None:
        orb = orbitas(
            centro,
            {pid: (tuple(p["center_mm"]), p["radius_mm"]) for pid, p in planetas.items()},
            ancla,
            ancla_mm,
            superficie_mm,
            girada,
            intro,
            animadas=es_sol,
            zona_mm=zona_mm if con_fondo else None,
            fantasmas=fantasmas,
        )
        if orb:
            out.append(orb)
    return out


def desde_mundo(
    objetos: dict[str, dict],
    superficie_mm=(1000, 563),
    intro: bool = True,
    girada: bool = False,
    con_fondo: bool = True,
    sol_dientes_rel=None,
) -> list[dict]:
    """Camino B (motor + escena `solar`): todo desde los objetos que publica `pcx-escena-solar` en el mundo
    ({id: {center_mm, polygon_mm}}), no desde la detección de Python.

    - Cada `solar:<planeta>` del catálogo: esfera anclada a su `object_id`, del tamaño de su silueta y recortada a ella.
      Un planeta que el motor no publica no se dibuja (anclado a un id que no existe caería en el centro de la mesa).
    - `solar:sol`: el Sol con la silueta del objeto. Si es un círculo (el motor publica 24 lados) y viene
      `sol_dientes_rel` (el contorno con dientes de la foto de `armar`, relativo a su centro), se usa ese en el centro
      del objeto.
    - Órbitas: centradas en `solar:sol`; sin Sol, en `solar:canasto` (anclas del motor); por el centro de cada planeta
      dibujado. La calma, sobre la caja del canasto + 60."""
    sol_cuerpo = None
    if "solar:sol" in objetos and (s := silueta(objetos["solar:sol"])):
        c, rel, r = s
        if sol_dientes_rel and es_circulo(rel):
            rel = sol_dientes_rel
            r = max(math.hypot(x, y) for x, y in rel)
        sol_cuerpo = {"center_mm": c, "radius_mm": r, "polygon_mm": [[c[0] + x, c[1] + y] for x, y in rel]}
    planetas = {}
    for oid, o in objetos.items():
        pid = oid.split(":", 1)[1] if oid.startswith(E.PREFIJO) else None
        if pid in PLANETAS and (s := silueta(o)):
            c, rel, r = s
            planetas[pid] = {"center_mm": c, "radius_mm": r, "silueta": rel}
    canasto = silueta(objetos["solar:canasto"]) if "solar:canasto" in objetos else None
    canasto_mm = [[canasto[0][0] + x, canasto[0][1] + y] for x, y in canasto[1]] if canasto else None
    centro = sol_cuerpo["center_mm"] if sol_cuerpo else (canasto[0] if canasto else None)
    return widgets(
        sol_cuerpo,
        planetas,
        superficie_mm,
        (),
        intro,
        girada,
        con_fondo,
        canasto_mm=canasto_mm,
        centro=centro,
        ancla_canasto=canasto[0] if canasto else None,
    )


def fantasmas_de(est: dict) -> set[str]:
    """Los planetas que h20 dejó como fantasma (`est["fantasmas"]`: índice del cuerpo → armados sin verlo)."""
    fant = est.get("fantasmas") or {}
    return {pid for pid, i in est["asignacion"]["planetas"].items() if str(i) in fant}


def desde_estado(est: dict, centros: dict[str, tuple[float, float]], intro: bool = True, dudas=(), sol_poligono_mm=None, girada: bool = False, **kw) -> list[dict]:
    """La capa visual desde el estado del conductor de h20 (`est` de `estado/estado.json` y `centros_actuales(est)`).
    Cada esfera usa el radio del cuerpo tal cual (sin el `escala_bola` de mesa.json) y los fantasmas salen de
    `est["fantasmas"]`. `kw`: `centro`, `zona_mm`, `ancla_canasto`, `canasto_mm`, `con_fondo` (ver `widgets`)."""
    a = est["asignacion"]
    cuerpos = est["cuerpos"]
    sol_c = None
    if a.get("sol") is not None and "sol" in centros:
        sol_c = {"center_mm": centros["sol"], "radius_mm": cuerpos[a["sol"]]["radio_mm"], "polygon_mm": sol_poligono_mm}
    planetas = {pid: {"center_mm": centros[pid], "radius_mm": cuerpos[i]["radio_mm"]} for pid, i in a["planetas"].items() if pid in centros}
    kw.setdefault("fantasmas", fantasmas_de(est))
    return widgets(sol_c, planetas, tuple(est.get("surface_mm", (1000, 563))), dudas, intro, girada, **kw)

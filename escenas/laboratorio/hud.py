"""Lo que se proyecta en la escena Laboratorio: funciones puras estado → widgets de `project`.

Todo es `effect` con `shader: "html"` (1 px de CSS = 1 mm). El estilo es el sistema común de la mesa (`base.css`, de
MOTOR.md) + las piezas de esta escena (`ui/piezas.css`) + el tema (`tema.css`). Regla física: texto ≥ 28 mm, trazos
≥ 4 mm, botones de 72 mm de alto. No hay scripts en la proyección: cada cambio (los ml en vivo, por ejemplo) es un
`project` nuevo del widget que cambió, y nada más.

Ids fijos, para que el conductor y un agente sepan qué reemplazar:
- `lab-grilla`: el pastillero, con la celda marcada y su dirección (girado con la caja).
- `lab-paso`: el panel del paso actual (Explicar + Tocar).
- `lab-guia`: la línea de la celda al vaso (Llevar).
- `lab-medidor`: los ml en vivo (Medir + Avisar).
- `lab-nota`: libre para el agente (una respuesta por voz, por ejemplo). El conductor no lo toca.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import numpy as np

from percepcion.pastillero import FILAS, N_COLUMNAS, N_FILAS, Grilla

AQUI = Path(__file__).parent


def _base_css() -> str:
    """El sistema común: el del repo si ya está integrado (motor/sistema/base.css), si no la copia de ui/."""
    for p in (AQUI.parent.parent / "motor" / "sistema" / "base.css", AQUI / "ui" / "base.css"):
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


def css() -> str:
    """base.css + piezas de la escena + tema.css. Se lee cada vez: cambiar el tema no exige reiniciar nada."""
    return "\n".join([_base_css(), (AQUI / "ui" / "piezas.css").read_text(encoding="utf-8"), (AQUI / "tema.css").read_text(encoding="utf-8")])


REG = '<div class="reg"><i></i><i></i><i></i><i></i></div>'
Z = 20


def _e(t) -> str:
    return html.escape(str(t), quote=True)


def _html(id_: str, rect_mm, cuerpo: str, rotar: float = 0.0, z: int = Z) -> dict:
    x, y, w, h = (round(float(v), 1) for v in rect_mm)
    return {
        "type": "effect",
        "id": id_,
        "shader": "html",
        "rect_mm": [x, y, w, h],
        "z": z,
        "params": {"html": cuerpo, "css": css(), "rotate_deg": round(float(rotar), 2)},
    }


def ml_txt(ml: float | None) -> str:
    """50 → «50», 50.4 → «50,4» (coma decimal)."""
    if ml is None:
        return "–"
    r = round(float(ml), 1)
    return f"{r:.0f}" if r == int(r) else f"{r:.1f}".replace(".", ",")


# --------------------------------------------------------------------------------------------- pastillero


def grilla(
    g: Grilla,
    objetivo: list[str] | None = None,
    hechas: list[str] | None = None,
    mal: str | None = None,
    elegir: bool = False,
    flecha: tuple[str, str] | None = None,
    nombres: dict | None = None,
) -> dict:
    """La gradilla de viales (el pastillero 7×4) como sistema de coordenadas.

    - Borde de la gradilla, viales ya usados con un punto y los viales del paso con marcas de registro.
    - `flecha=("A2", "C5")`: traspaso de un vial a otro. El de origen va hueco, el de destino lleno, y una flecha los une.
    - Solo se rotulan la fila y la columna de los viales del paso (28 mm no caben en viales de 22 mm), con líneas guía
      como al buscar en una tabla; debajo de la gradilla (sobre la tapa abierta), la etiqueta: «B3 anticuerpo» o
      «A2 → C5».
    - `nombres`: {«B3»: «anticuerpo»}; un vial sin nombre se rotula con su color.
    - Con `elegir`, cada vial es tocable (`data-press="B3"`). El conductor usa la punta del dedo para saber cuál es.
    El widget va centrado en la gradilla y girado con ella: adentro, x son las columnas e y las filas.
    """
    objetivo = [d.upper() for d in (objetivo or [])]
    hechas = [d.upper() for d in (hechas or [])]
    mal = (mal or "").upper() or None
    nombres = {str(k).upper(): str(v) for k, v in (nombres or {}).items()}
    origen = flecha[0].upper() if flecha else None
    destino = flecha[1].upper() if flecha and str(flecha[1]).upper() != "VASO" else None
    marcados = [d for d in dict.fromkeys(objetivo + [x for x in (origen, destino) if x])]
    p = g.paso_mm
    m_izq, m_arr, m_der, m_abj = 44.0, 44.0, 12.0, 96.0  # márgenes: rótulos arriba e izquierda, etiqueta abajo (sobre la tapa)
    ancho_g, alto_g = N_COLUMNAS * p, N_FILAS * p
    W, H = m_izq + ancho_g + m_der, m_arr + alto_g + m_abj
    u, v = np.array(g.eje_columnas), np.array(g.eje_filas)
    # Centro del widget en la mesa: el de la grilla, corrido por los márgenes en los ejes de la caja.
    c = np.array(g.centro_mm) - ((m_izq + ancho_g / 2 - W / 2) * u + (m_arr + alto_g / 2 - H / 2) * v)
    rect = [c[0] - W / 2, c[1] - H / 2, W, H]
    voltear = abs(((g.angulo_deg + 180) % 360) - 180) > 100  # la caja quedó de cabeza para quien la mira

    def txt(t):
        return f'<span class="voltear">{t}</span>' if voltear else t

    def centro(d):
        return ((int(d[1:]) - 0.5) * p, (FILAS.index(d[0]) + 0.5) * p)

    s = [f'<div class="lab-grilla" style="left:{m_izq}px;top:{m_arr}px;width:{ancho_g:.1f}px;height:{alto_g:.1f}px">', '<div class="borde"></div>']
    cols = sorted({int(d[1:]) for d in marcados})
    filas = sorted({d[0] for d in marcados}, key=FILAS.index)
    for col in cols:
        s.append(f'<div class="eje" style="left:{(col - 1) * p:.1f}px;top:-42px;width:{p:.1f}px;height:34px">{txt(col)}</div>')
    for f in filas:
        s.append(f'<div class="eje" style="left:-42px;top:{FILAS.index(f) * p:.1f}px;width:34px;height:{p:.1f}px">{txt(f)}</div>')
    for d in marcados:
        cx, cy = centro(d)
        s.append(f'<div class="guia" style="left:{cx - 1.5:.1f}px;top:-8px;width:3px;height:{cy - p / 2 + 8:.1f}px"></div>')
        s.append(f'<div class="guia" style="left:-8px;top:{cy - 1.5:.1f}px;width:{cx - p / 2 + 8:.1f}px;height:3px"></div>')
    for f in range(N_FILAS):
        for col in range(N_COLUMNAS):
            d = f"{FILAS[f]}{col + 1}"
            if d == origen:
                clase = "objetivo origen"
            elif d in marcados:
                clase = "objetivo"
            elif d == mal:
                clase = "mal"
            elif d in hechas:
                clase = "hecha"
            else:
                clase = ""
            dentro = REG + ('<div class="relleno"></div>' if "origen" not in clase else "") if ("objetivo" in clase or clase == "mal") else ""
            press = f' data-press="{d}"' if elegir else ""
            s.append(f'<div class="celda {clase}"{press} style="left:{col * p:.1f}px;top:{f * p:.1f}px;width:{p:.1f}px;height:{p:.1f}px">{dentro}</div>')
    if origen and destino:  # flecha de vial a vial, dentro de la gradilla
        (x0, y0), (x1, y1) = centro(origen), centro(destino)
        d = np.array([x1 - x0, y1 - y0])
        n = d / (np.hypot(*d) or 1)
        a, b = np.array([x0, y0]) + n * 9, np.array([x1, y1]) - n * 12
        ang = float(np.degrees(np.arctan2(n[1], n[0])))
        s.append(
            f'<svg class="lab-flecha" width="{ancho_g:.1f}" height="{alto_g:.1f}" viewBox="0 0 {ancho_g:.1f} {alto_g:.1f}">'
            f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}"/>'
            f'<g transform="translate({b[0]:.1f} {b[1]:.1f}) rotate({ang:.1f})"><path d="M3 0 L-11 -7 L-11 7 Z"/></g></svg>'
        )

    def nombre(d):
        return nombres.get(d) or g.celda(d).color

    if origen:
        hacia = destino or "vaso"
        texto, ancla = f"{_e(origen)} → {_e(hacia)}", origen
    elif objetivo or mal:
        ancla = objetivo[0] if objetivo else mal
        texto = f"{_e(ancla)}<small>{_e(nombre(ancla))}</small>"
    else:
        texto = ancla = None
    if texto:
        extra = " mal" if (mal and not objetivo and not origen) else ""
        s.append(f'<div class="sd-etiqueta{extra}" style="left:-32px;top:{alto_g + 16:.1f}px;width:{W - 24:.1f}px">{txt(texto)}</div>')
    s.append("</div>")
    return _html("lab-grilla", rect, "".join(s), rotar=g.angulo_deg)


# --------------------------------------------------------------------------------------------- panel


def _titulo(texto: str) -> str:
    """Resalta en mono las direcciones de celda (B3) y las cifras antes de «ml»."""
    t = _e(texto)
    t = re.sub(r"\b([A-D][1-7])\b", r"<b>\1</b>", t)
    return re.sub(r"\b(\d+(?:,\d+)?)(?= ml\b)", r"<b>\1</b>", t)


def panel(protocolo: dict, i: int, rect_mm, aviso: str | None = None, tono: str = "peligro", listo_resaltado: bool = False, reloj_s: float | None = None) -> dict:
    """Panel del paso `i` (0-based): «Paso 2 de 4», la instrucción, una línea de ayuda (o el aviso, en su lugar) y los
    botones Atrás / Listo. `listo_resaltado`: el botón se llena (ya se puede seguir). En un paso de «elegir» lo que
    confirma es tocar la celda; el botón Listo queda de respaldo."""
    pasos = protocolo["pasos"]
    paso = pasos[i]
    if aviso:
        ayuda = f'<div class="sd-aviso" data-tono="{_e(tono)}">{_e(aviso)}</div>'
    elif paso.get("detalle"):
        ayuda = f'<p class="lab-detalle">{_e(paso["detalle"])}</p>'
    else:
        ayuda = ""
    if reloj_s:  # mezclar: cuenta regresiva en CSS (un entero animado con @property), sin scripts
        s = int(round(reloj_s))
        ayuda += (
            f'<div class="lab-reloj" style="--s:{s};animation-duration:{s}s">'
            f'<span class="lab-reloj-n"></span><span class="lab-reloj-u">s</span><i style="animation-duration:{s}s"></i></div>'
        )
    listo = "sd-boton lab-listo ya" if listo_resaltado else "sd-boton lab-listo"
    cuerpo = (
        '<div class="sd-panel lab-panel">'
        f'<div class="lab-cabeza"><span class="sd-paso">{i + 1} / {len(pasos)}</span></div>'
        f'<h1 class="wui-pista"><span class="wui-linea">{_titulo(paso["di"])}</span></h1>{ayuda}'
        f'<div class="sd-fila"><div class="sd-boton lab-chico" data-press="atras"><span>Atrás</span></div>'
        f'<div class="{listo}" data-press="listo"><span>Listo</span></div></div>'
        "</div>"
    )
    widget = _html("lab-paso", rect_mm, cuerpo)
    widget["params"]["wui_pista"] = str(i)
    return widget


def cierre(protocolo: dict, rect_mm, registro: dict, hora: str) -> dict:
    filas = "".join(f"<span>{k + 1}</span><span>{_e(registro.get(k, '–'))}</span>" for k in range(len(protocolo["pasos"])))
    cuerpo = (
        '<div class="sd-panel lab-panel">'
        f'<div class="lab-cabeza"><span class="sd-paso">{_e(hora)}</span></div>'
        "<h1>Protocolo completo</h1>"
        f'<div class="lab-registro">{filas}</div>'
        '<div class="sd-fila"><div class="sd-boton lab-listo" data-press="reiniciar"><span>Empezar de nuevo</span></div></div>'
        "</div>"
    )
    return _html("lab-paso", rect_mm, cuerpo)


# --------------------------------------------------------------------------------------------- medidor


def medidor(rect_mm, ml: float | None, objetivo_ml: float, zona: str, capacidad_ml: float = 250, cerca: bool = False, exceso_ml: float | None = None, vivo: bool = True) -> dict:
    """Escala del vaso con el nivel en vivo, la línea del objetivo, el número grande y el aviso con su tono.

    `zona`: «falta», «listo» o «te_pasaste» (de `percepcion.vaso.Umbral`). La escala llega a un poco más que el
    objetivo, para que el movimiento se note. El número va entero: una cámara no mide décimas de ml.
    """
    x, y, w, h = rect_mm
    tope = max(objetivo_ml * 1.4, objetivo_ml + 20)
    if capacidad_ml > objetivo_ml:
        tope = min(tope, capacidad_ml)
    alto = h - 32 - 86 - 16  # el aviso va abajo, a lo ancho (2 líneas de 28 mm)

    def pos(m):
        return max(0.0, min(1.0, m / tope)) * alto

    paso_marca = 10 if tope <= 120 else 25
    marcas = "".join(
        f'<div class="marca" style="bottom:{pos(m):.1f}px;width:{24 if m % (2 * paso_marca) == 0 else 12}px"></div>'
        for m in range(0, int(tope) + 1, paso_marca)
    )
    nivel = pos(ml or 0)
    exceso = ""
    if zona == "te_pasaste" and ml is not None:
        exceso = f'<div class="exceso" style="bottom:{pos(objetivo_ml):.1f}px;height:{max(0.0, nivel - pos(objetivo_ml)):.1f}px"></div>'
        nivel = pos(objetivo_ml)
    if zona == "te_pasaste":
        clase, tono, aviso = "pasado", "peligro", f"Te pasaste: {round(exceso_ml or 0)} ml"
    elif zona == "listo":
        clase, tono, aviso = "listo", "ok", "Justo. Deja de echar"
    elif cerca:
        clase, tono, aviso = "", "alerta", "Despacio, ya casi"
    else:
        clase, tono, aviso = "", "", "Echa de a poco"
    tono_attr = f' data-tono="{tono}"' if tono else ""
    cuerpo = (
        f'<div class="lab-medidor {clase}">'
        f'<div class="lab-escala" style="height:{alto:.1f}px">{marcas}<div class="nivel" style="height:{nivel:.1f}px"></div>{exceso}'
        f'<div class="meta" style="bottom:{pos(objetivo_ml):.1f}px"></div></div>'
        '<div class="lab-lectura">'
        + ('<div class="lab-vivo"><i></i>en vivo</div>' if vivo else '<div class="lab-vivo">sin lectura</div>')
        + f'<div class="sd-dato{" tres" if ml is not None and round(ml) >= 100 else ""}">{"–" if ml is None else round(ml)}<small>ml</small></div>'
        f'<div class="lab-objetivo">de <b>{ml_txt(objetivo_ml)}</b> ml</div>'
        "</div>"
        f'<div class="sd-aviso"{tono_attr}>{_e(aviso)}</div>'
        "</div>"
    )
    return _html("lab-medidor", rect_mm, cuerpo)


# --------------------------------------------------------------------------------------------- guía


def guia(desde_mm, hasta_mm, texto: str = "al vaso") -> dict:
    """Llevar: línea punteada de la celda al vaso, que parte y llega al borde (no al centro)."""
    a, b = np.asarray(desde_mm, float), np.asarray(hasta_mm, float)
    m = 30.0
    x0, y0 = np.minimum(a, b) - m
    x1, y1 = np.maximum(a, b) + m
    pa, pb = a - (x0, y0), b - (x0, y0)
    d = pb - pa
    largo = float(np.hypot(*d)) or 1.0
    un = d / largo
    pa2, pb2 = pa + un * 18, pb - un * 34  # sale del borde de la celda y llega al borde del vaso (radio ~ 30 mm)
    ang = float(np.degrees(np.arctan2(un[1], un[0])))
    svg = (
        f'<svg viewBox="0 0 {x1 - x0:.1f} {y1 - y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}">'
        f'<circle class="lab-origen" cx="{pa2[0]:.1f}" cy="{pa2[1]:.1f}" r="3"/>'
        f'<path class="lab-trayecto" pathLength="1" d="M{pa2[0]:.1f},{pa2[1]:.1f} '
        f'C{pa2[0] + (pb2[0]-pa2[0]) * .55:.1f},{pa2[1]:.1f} {pb2[0] - un[0]*20:.1f},{pb2[1] - un[1]*20:.1f} {pb2[0]:.1f},{pb2[1]:.1f}"/>'
        f'<g transform="translate({pb2[0]:.1f} {pb2[1]:.1f}) rotate({ang:.1f})"><path d="M2 0 L-16 -9 L-16 9 Z"/></g>'
        f'<circle cx="{pb[0]:.1f}" cy="{pb[1]:.1f}" r="30"/>'
        "</svg>"
    )
    return _html("lab-guia", [x0, y0, x1 - x0, y1 - y0], f'<div class="lab-guia">{svg}</div>', z=Z - 1)


# --------------------------------------------------------------------------------------------- ambiente


# Proteínas en trazo, para el espacio libre de la mesa: hélice alfa, anticuerpo (Y), proteína globular y lámina beta.
# Solo decoran: no tocan nada, no dicen nada y se mueven lento.
_PROTEINAS = {
    "helice": '<path d="M0 30 C 10 0, 20 0, 30 30 S 50 60, 60 30 S 80 0, 90 30 S 110 60, 120 30"/>'
    '<path d="M0 40 C 10 10, 20 10, 30 40 S 50 70, 60 40 S 80 10, 90 40 S 110 70, 120 40" class="b"/>',
    "anticuerpo": '<path d="M40 90 L40 50 L12 14 M40 50 L68 14"/><path d="M28 50 L4 22 M52 50 L76 22" class="b"/>'
    '<circle cx="12" cy="10" r="6" class="f"/><circle cx="68" cy="10" r="6" class="f"/>',
    "globular": '<circle cx="30" cy="30" r="14"/><circle cx="56" cy="22" r="11"/><circle cx="52" cy="50" r="13"/>'
    '<circle cx="24" cy="58" r="9" class="b"/><path d="M30 30 L56 22 L52 50 Z M30 30 L24 58" class="b"/>',
    "lamina": '<path d="M0 10 H80 L94 20 L80 30 H0"/><path d="M94 46 H14 L0 56 L14 66 H94" class="b"/>'
    '<path d="M0 82 H80 L94 92 L80 102 H0"/>',
}


def ambiente(id_: str, rect_mm, semilla: int = 0) -> dict:
    """Proteínas que flotan en una zona libre de la mesa (un experimento de investigación, no un fondo de pantalla)."""
    rng = np.random.default_rng(semilla)
    x, y, w, h = rect_mm
    nombres = list(_PROTEINAS)
    rng.shuffle(nombres)
    piezas = []
    n = max(2, min(4, int(w * h // 9000)))
    for k in range(n):
        forma = nombres[k % len(nombres)]
        px, py = rng.uniform(0.05, 0.6) * w, rng.uniform(0.05, 0.55) * h
        rot = rng.uniform(-40, 40)
        dur = rng.uniform(17, 31)  # cada una a su ritmo: nada se mueve al unísono
        dx, dy, dr = rng.uniform(-14, 14), rng.uniform(-10, 10), rng.uniform(-12, 12)
        piezas.append(
            f'<svg class="lab-proteina" viewBox="-6 -6 136 116" width="{min(130, w * 0.45):.0f}" '
            f'style="left:{px:.0f}px;top:{py:.0f}px;--r:{rot:.0f}deg;--dx:{dx:.0f}px;--dy:{dy:.0f}px;--dr:{dr:.0f}deg;'
            f'animation-duration:{dur:.1f}s;animation-delay:-{rng.uniform(0, dur):.1f}s">{_PROTEINAS[forma]}</svg>'
        )
    return _html(id_, rect_mm, f'<div class="lab-ambiente">{"".join(piezas)}</div>', z=1)



# --------------------------------------------------------------------------------------------- protocolo y alarma


def protocolo(prot: dict, i: int, rect_mm, terminado: bool = False) -> dict:
    """La lista del protocolo, como la hoja pegada en el mesón: número y nombre corto de cada paso, el actual en foco y
    los hechos tachados. Dos columnas para que quepa a 28 mm."""
    pasos = prot["pasos"]
    items = []
    for k, p in enumerate(pasos):
        clase = "hecho" if (terminado or k < i) else "ahora" if k == i else ""
        nombre = p.get("corto") or p["di"].split()[0]
        items.append(f'<li class="{clase}"><span class="n">{k + 1}</span><span>{_e(nombre)}</span></li>')
    cuerpo = (
        f'<div class="lab-protocolo"><div class="lab-protocolo-t">{_e(prot.get("nombre", "Protocolo"))}</div>'
        f'<ol>{"".join(items)}</ol></div>'
    )
    return _html("lab-protocolo", rect_mm, cuerpo, z=Z - 2)


def alarma(objetos: list[dict], rect_aviso_mm) -> list[dict]:
    """Objeto contaminado: luz roja encima de cada objeto ajeno y el aviso en grande. Devuelve widgets
    (`lab-alarma` + `lab-alarma-1`, `lab-alarma-2`…). Es lo único que late: es un estado vivo y urgente."""
    ws = []
    for k, o in enumerate(objetos, 1):
        pts = np.asarray(o["poligono_mm"], float)
        x0, y0 = pts.min(axis=0) - 24
        x1, y1 = pts.max(axis=0) + 24
        poly = " ".join(f"{px - x0:.1f},{py - y0:.1f}" for px, py in pts)
        svg = (
            f'<svg class="lab-contaminado" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" viewBox="0 0 {x1 - x0:.1f} {y1 - y0:.1f}">'
            f'<polygon points="{poly}"/></svg>'
        )
        ws.append(_html(f"lab-alarma-{k}", [x0, y0, x1 - x0, y1 - y0], svg, z=90))
    que = "tu celular" if any(o.get("tipo") == "celular" for o in objetos) else "el objeto"
    cuerpo = (
        '<div class="sd-panel lab-alarma">'
        '<div class="sd-paso">Mesa contaminada</div>'
        f'<h1>Objeto contaminado en la mesa</h1><div class="sd-aviso" data-tono="peligro">Retira {que} del espacio, por favor</div>'
        "</div>"
    )
    ws.append(_html("lab-alarma", rect_aviso_mm, cuerpo, z=95))
    return ws

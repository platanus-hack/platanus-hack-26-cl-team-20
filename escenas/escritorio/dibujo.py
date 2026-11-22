"""De datos a widgets proyectables: llena las plantillas html y las ubica sobre la libreta o la mesa.

Cada pieza es un widget `effect` con `shader: "html"`: un iframe en `rect_mm`, girado `rotate_deg` alrededor de su
centro, donde 1 px de CSS = 1 mm. Las piezas que van sobre la libreta se calculan en su marco local (origen en la esquina
de arriba a la izquierda tal como la lee la persona, x hacia la derecha e y hacia abajo, en mm) y después se llevan a la
mesa con la misma rotación de la libreta.
"""

from __future__ import annotations

import html
import json
import math
import re
import time
from pathlib import Path

import numpy as np

from percepcion.geometria import caja_rotada

AQUI = Path(__file__).resolve().parent
PLANTILLAS = AQUI / "plantillas"
BASE = AQUI.parents[1] / "motor" / "sistema" / "base.css"
PREFIJO = "escritorio"

# ------------------------------------------------------------------ plantillas


def llenar(plantilla: str, datos: dict) -> str:
    """Plantillas al estilo mustache: {{campo}} (con escape), {{{campo}}} (tal cual), {{#lista}}…{{/lista}} repite o
    muestra si es verdadero, {{^campo}}…{{/campo}} muestra si es falso. Pensado para que alguien sin programar edite el
    html sin romper nada."""

    def seccion(m):
        neg, clave, cuerpo = m.group(1) == "^", m.group(2), m.group(3)
        valor = datos.get(clave)
        if neg:
            return llenar(cuerpo, datos) if not valor else ""
        if isinstance(valor, list):
            return "".join(llenar(cuerpo, {**datos, **(v if isinstance(v, dict) else {"valor": v})}) for v in valor)
        return llenar(cuerpo, datos) if valor else ""

    texto = re.sub(r"\{\{([#^])(\w+)\}\}(.*?)\{\{/\2\}\}", seccion, plantilla, flags=re.S)
    texto = re.sub(r"\{\{\{(\w+)\}\}\}", lambda m: str(datos.get(m.group(1), "")), texto)
    return re.sub(r"\{\{(\w+)\}\}", lambda m: html.escape(str(datos.get(m.group(1), ""))), texto)


def plantilla(nombre: str) -> str:
    texto = (PLANTILLAS / f"{nombre}.html").read_text()
    return re.sub(r"<!--.*?-->", "", texto, flags=re.S).strip()


def css(colores: dict) -> str:
    variables = ";".join(f"--{k.replace('_', '-')}:{v}" for k, v in colores.items() if not k.startswith("_"))
    base = BASE.read_text(encoding="utf-8") if BASE.exists() else ""
    return f"{base}\n:root{{{variables}}}\n" + (PLANTILLAS / "estilo.css").read_text(encoding="utf-8")


def widget_html(id_: str, contenido: str, colores: dict, ubicacion: dict, z: int = 20) -> dict:
    return {
        "type": "effect",
        "id": f"{PREFIJO}:{id_}",
        "shader": "html",
        "z": z,
        "rect_mm": [round(v, 1) for v in ubicacion["rect_mm"]],
        "params": {"html": contenido, "css": css(colores), "rotate_deg": round(ubicacion.get("rotate_deg", 0), 2)},
    }


# ------------------------------------------------------------------ la libreta sobre la mesa


class LibretaEnMesa:
    """La libreta como rectángulo girado en mm de la mesa, con su marco local para ubicar piezas encima y alrededor."""

    def __init__(self, esquinas_mm):
        caja = caja_rotada(esquinas_mm)
        x, y, self.ancho, self.alto = caja["rect_mm"]
        self.centro = np.array(caja["centro_mm"], dtype=float)
        self.angulo = caja["rotate_deg"]
        a = math.radians(self.angulo)
        self._R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        self._origen = self.centro + self._R @ np.array([-self.ancho / 2, -self.alto / 2])

    def a_mesa(self, punto_local) -> np.ndarray:
        return self._origen + self._R @ np.asarray(punto_local, dtype=float)

    def ubicar(self, rect_local) -> dict:
        """Rectángulo en el marco local de la libreta → rect_mm + rotate_deg de un widget html."""
        x, y, w, h = rect_local
        c = self.a_mesa([x + w / 2, y + h / 2])
        return {"rect_mm": [c[0] - w / 2, c[1] - h / 2, w, h], "rotate_deg": self.angulo}

    def local(self, rect_norm) -> list[float]:
        """Rectángulo en coordenadas de la libreta (0..1) → mm en su marco local."""
        u, v, uw, vh = rect_norm
        return [u * self.ancho, v * self.alto, uw * self.ancho, vh * self.alto]


def esquinas_desde_objeto(objeto: dict) -> list | None:
    """Si el motor publicó la libreta como objeto (polygon_mm), sus 4 esquinas en orden de lectura (lado largo vertical)."""
    import cv2

    poly = np.float32(objeto.get("polygon_mm") or [])
    if len(poly) < 4:
        return None
    caja = cv2.boxPoints(cv2.minAreaRect(poly))
    from percepcion.geometria import ordenar_esquinas

    return ordenar_esquinas(caja).tolist()


# ------------------------------------------------------------------ piezas


BOTON = (34.0, 14.0)  # mm: cabe la yema de un dedo con holgura
BOTON_Y = 12.0  # mm bajo el borde de arriba de la libreta: deja ver la marca de corte de esa esquina


def marco(lib: LibretaEnMesa, cfg: dict) -> dict:
    """Marcas de corte en las 4 esquinas de la libreta y el botón «Responde» a un costado."""
    m = cfg["libreta"].get("margen_mm", 14)
    bw, bh = BOTON
    izquierda = cfg["boton"].get("lado", "izquierda") == "izquierda"
    extra = bw + 6
    x0 = -(m + extra) if izquierda else -m
    rect = [x0, -m, lib.ancho + 2 * m + extra, lib.alto + 2 * m]
    ox, oy = -x0, m  # dónde cae la esquina de la libreta dentro del widget
    largo, sep = 8, 3
    cortes = []
    for cx, cy, sx, sy in [(0, 0, -1, -1), (lib.ancho, 0, 1, -1), (lib.ancho, lib.alto, 1, 1), (0, lib.alto, -1, 1)]:
        px, py = ox + cx, oy + cy
        cortes.append({"d": f"M{px + sx * sep:.1f} {py:.1f} h{sx * largo}"})
        cortes.append({"d": f"M{px:.1f} {py + sy * sep:.1f} v{sy * largo}"})
    boton_x = ox - extra if izquierda else ox + lib.ancho + 6
    datos = {
        "ancho": round(rect[2], 1),
        "alto": round(rect[3], 1),
        "cortes": cortes,
        "boton": cfg["boton"].get("texto", "Responde"),
        "boton_x": round(boton_x, 1),
        "boton_y": round(oy + BOTON_Y, 1),
        "boton_w": bw,
        "boton_h": bh,
    }
    return widget_html("marco", llenar(plantilla("marco"), datos), cfg["colores"], lib.ubicar(rect), z=10)


def zona_estado(lib: LibretaEnMesa, cfg: dict) -> list[float]:
    """Debajo del botón, en el marco local de la libreta."""
    bw, bh = BOTON
    x = -(bw + 6) if cfg["boton"].get("lado", "izquierda") == "izquierda" else lib.ancho + 6
    return [x, BOTON_Y + bh + 3, bw, 7]


def estado(lib: LibretaEnMesa, cfg: dict, texto: str, vivo: bool = True) -> dict:
    """Una palabra junto al botón: qué está haciendo el agente con la libreta ahora."""
    contenido = llenar(plantilla("estado"), {"texto": texto, "vivo": vivo})
    return widget_html("estado", contenido, cfg["colores"], lib.ubicar(zona_estado(lib, cfg)), z=12)


def marca(lib: LibretaEnMesa, cfg: dict, renglones_norm: list) -> dict:
    """Contorno de señal a la izquierda de lo recién escrito: «esto es lo que leí»."""
    xs = [r[0] for r in renglones_norm]
    ys0 = [r[1] for r in renglones_norm]
    ys1 = [r[1] + r[3] for r in renglones_norm]
    x, y0, y1 = min(xs) * lib.ancho, min(ys0) * lib.alto - 1.5, max(ys1) * lib.alto + 1.5
    pad, gancho = 3.0, 2.5
    bx = max(0.0, x - 3.5)  # el corchete va un poco antes de la letra
    rect = [bx - pad, y0 - pad, gancho + 2 * pad, (y1 - y0) + 2 * pad]
    # un corchete con la curva de una mano: no es perfectamente recto
    px, p0, p1 = pad + gancho, pad, pad + (y1 - y0)
    d = (
        f"M{px:.1f} {p0:.1f} Q{pad:.1f} {p0:.1f} {pad + 0.3:.1f} {p0 + 2.2:.1f} "
        f"L{pad - 0.2:.1f} {p1 - 2.2:.1f} Q{pad:.1f} {p1:.1f} {px:.1f} {p1:.1f}"
    )
    largo = round(2 * gancho + (y1 - y0) + 4, 1)
    contenido = llenar(plantilla("marca"), {"ancho": round(rect[2], 1), "alto": round(rect[3], 1), "d": d, "largo": largo})
    return widget_html("marca", contenido, cfg["colores"], lib.ubicar(rect), z=14)


def medida_nota(lib: LibretaEnMesa, cfg: dict, lineas: list[str]) -> tuple[float, float]:
    """Cuánto ocupa la respuesta, en proporción de la libreta (ancho, alto)."""
    letra = cfg["respuesta"].get("letra_mm", 9)
    largo = max((len(t) for t in lineas), default=1)
    ancho = ((largo * 0.53 + 1.1) * letra + 4) / lib.ancho
    alto = (len(lineas) * 1.2 * letra + 2) / lib.alto
    return min(0.95, ancho), min(0.9, alto)


def nota(lib: LibretaEnMesa, cfg: dict, n: int, rect_norm, lineas: list[str], escala: float = 1.0) -> dict:
    """La respuesta se revela con el gesto de tinta de WUI sobre el hueco de la libreta."""
    letra = cfg["respuesta"].get("letra_mm", 9) * escala
    datos = {"letra": round(letra, 2), "interlinea": round(letra * 1.2, 2),
             "lineas": [{"texto": texto} for texto in lineas]}
    contenido = llenar(plantilla("nota"), datos)
    return widget_html(f"nota:{n}", contenido, cfg["colores"], lib.ubicar(lib.local(rect_norm)), z=16)


# ------------------------------------------------------------------ panel de agentes


def leer_agentes(cfg: dict) -> list[dict]:
    ruta = AQUI / cfg["agentes"].get("archivo", "agentes.json")
    try:
        return json.loads(ruta.read_text()).get("agentes", [])
    except (OSError, json.JSONDecodeError):
        return []


def agentes(cfg: dict, lista: list[dict], yo: dict | None = None, anteriores: list[dict] | None = None) -> dict:
    """El panel con lo que hacen los agentes. `yo` es la fila del agente de esta escena, con su estado en vivo."""
    filas = [dict(a) for a in lista] + ([dict(yo)] if yo else [])
    antes = {a.get("nombre"): a for a in anteriores or []}
    for f in filas:
        f["estado"] = f.get("estado", "trabajando") if f.get("estado") in ("trabajando", "esperando", "listo") else "trabajando"
        previo = antes.get(f.get("nombre"))
        f["nuevo"] = anteriores is not None and (previo is None or previo.get("detalle") != f.get("detalle"))
    c = cfg["agentes"]
    contenido = llenar(plantilla("agentes"), {"titulo": c.get("titulo", "Tus agentes"), "hora": time.strftime("%H:%M"), "agentes": filas})
    return widget_html("agentes", contenido, cfg["colores"], {"rect_mm": c["rect_mm"], "rotate_deg": c.get("rotate_deg", 0)}, z=10)

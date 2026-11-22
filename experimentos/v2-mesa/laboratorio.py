#!/usr/bin/env python3
"""Escena «laboratorio» v2 (WUI): instrumento de mesa, preciso y técnico.

Un solo widget html en mm reales de la mesa, armado desde las cajas del agente (out/vlm-solar-lab.json) o desde
out/objetos.json (--objetos). No proyecta nada: escribe out/widget-lab.json (otro agente lo proyecta) o, con
--preview, una página en preview-lab/ con el MISMO svg sobre la foto real.

   laboratorio.py [--paso N] [--buscar arduino|hub|jumpers|sensor|...] [--nivel 0..100] [--alerta]
                  [--objetos out/objetos.json] [--sin-barrido] [--preview] [--capturas]

Diseño: tokens, marcas de registro, grilla A1.., escala del vaso y alarma de la escena de Max
(escenas/laboratorio: tema.css, ui/piezas.css, hud.py); estados latente/guía/activa, las cuatro formas (punto, línea,
arco, contorno), los tiempos de «la tinta toma forma» (0,42 / 0,88 / 1,28 / 1,72 s) y la curva de asentamiento de la
marca WUI de Pablo (wui_tokens.py, wui_motion.py, 04-marca/movimiento-texto-pista.md).
"""
import argparse, heapq, html, json, math, subprocess, sys
from pathlib import Path

import cv2
import numpy as np

AQUI = Path(__file__).resolve().parent

# ====================================================================================================================
# DATOS EDITABLES. Todo en mm reales de mesa (x hacia la derecha de la foto, y hacia abajo). La persona está en el
# borde de ARRIBA de la foto: su izquierda es la derecha de la foto, y «lejos» es hacia abajo.
# ====================================================================================================================

# Rejilla de la caja organizadora, medida sobre skin/entrada-vivo.png (bordes superiores de los tabiques).
# `lineas`: tabiques horizontales, cada uno por dos puntos (la caja se ve en perspectiva: no son paralelos).
# `filas`: cada fila vive entre dos líneas; `cortes` son los tabiques verticales como (x sobre la línea de arriba,
# x sobre la de abajo), de izquierda a derecha de la foto. Los compartimentos se numeran desde la izquierda de la
# persona (derecha de la foto): la fila B va de B1 (derecha de la foto) a B5.
ORGANIZADORA = {
    "lineas": {
        "L0": [(765, 127), (1135, 160)],     # pared del fondo (junto a la bisagra de la tapa)
        "L1": [(762, 159), (1046, 198)],
        "L2": [(757, 207.5), (1038, 243)],
        "L3": [(752, 252.5), (1115, 301)],
        "L4": [(742, 333), (1100, 382)],     # pared del frente
        "LJ": [(1049, 170), (1131, 172)],    # techo de la columna alta de la derecha
    },
    "filas": [
        {"fila": "A", "arriba": "L0", "abajo": "L1", "cortes": [(768, 762), (882, 876), (1049, 1046)]},
        {"fila": "B", "arriba": "L1", "abajo": "L2", "cortes": [(762, 757), (819, 814), (876, 869), (932.5, 926), (989, 982), (1046, 1039)]},
        {"fila": "C", "arriba": "L2", "abajo": "L3", "cortes": [(757, 752), (814, 807), (869, 862), (926, 918), (982, 976), (1039, 1032)]},
        {"fila": "D", "arriba": "L3", "abajo": "L4", "cortes": [(752, 742), (805, 796), (918, 905), (1032, 1015), (1115, 1100)]},
        {"fila": "E", "arriba": "LJ", "abajo": "L3", "cortes": [(1049, 1034), (1131, 1115)]},   # columna alta (2 filas)
    ],
}

# Catálogo: pieza, cantidad y compartimento. `clave` es lo que acepta --buscar (y sus alias).
# Las cantidades son de demo (la cámara no cuenta piezas todavía).
PIEZAS = [
    {"clave": "arduino", "alias": ["uno", "arduinos"], "pieza": "Arduino UNO", "cant": 1, "en": "D3"},
    {"clave": "hub", "alias": ["usb", "usb-c"], "pieza": "Hub USB-C", "cant": 1, "en": "D2"},
    {"clave": "driver", "alias": ["rele", "relé"], "pieza": "Driver / relé", "cant": 1, "en": "D4"},
    {"clave": "sensor", "alias": ["qr"], "pieza": "Sensor con QR", "cant": 1, "en": "C3"},
    {"clave": "modulo-rojo", "alias": ["rojo"], "pieza": "Módulo rojo", "cant": 1, "en": "B3"},
    {"clave": "modulo-negro", "alias": ["negro", "bolsa"], "pieza": "Módulo negro", "cant": 1, "en": "A1"},
    {"clave": "jumpers", "alias": ["jumper", "cables"], "pieza": "Cables jumper", "cant": 20, "en": "E1"},
    {"clave": "piezas-blancas", "alias": ["blancas"], "pieza": "Piezas blancas", "cant": 6, "en": "B5"},
    {"clave": "cables-finos", "alias": ["finos"], "pieza": "Cables finos", "cant": 4, "en": "B4"},
    {"clave": "engranaje", "alias": [], "pieza": "Engranaje", "cant": 1, "en": "C4"},
]

# Caja de viales (origen): rejilla dentro de la caja. Márgenes desde la caja del objeto [izq, arriba, der, abajo].
# Filas A..D desde el borde lejano a la tapa (como en la escena de Max); columnas 1..5 desde la izquierda de la persona.
VIALES = {"cols": 5, "filas": 4, "margen_mm": [19, 2, 3, 3],
          "colores": ["rosado", "magenta", "verde", "amarillo", "transparente"]}   # columna 1..5

# Viales sueltos (destinos): centro de cada vial en mm de la foto.
DESTINOS = {
    "Z": {"objeto": "viales azules", "nombre": "azul", "radio": 12,
          "viales": [(581, 256), (581, 281), (581, 305), (581, 327), (606, 283), (606, 306)]},
    "M": {"objeto": "viales morados", "nombre": "morado", "radio": 12,
          "viales": [(1022, 499), (1049, 498), (1076, 498), (1104, 496), (1013, 525), (1041, 524), (1068, 524)]},
}

# Protocolo de depósito con pipeta: de una celda de la caja a un vial suelto.
PROTOCOLO = [
    {"desde": "A4", "hasta": "M2", "gotas": 2},
    {"desde": "A3", "hasta": "Z1", "gotas": 3},
    {"desde": "B2", "hasta": "M6", "gotas": 1},
    {"desde": "A1", "hasta": "Z5", "gotas": 2},
]

# Vaso: dónde apoya (la boca se ve corrida por la altura) y el objetivo de nivel, en % de la altura (~127 mm).
VASO = {"base_mm": (1152, 368), "radio_mm": 40, "objetivo": 60, "tolerancia": 3, "cerca": 10, "nivel_inicial": 25}

# Huellas afinadas sobre la foto real. Las cajas del VLM quedaron corridas en los objetos altos (caja organizadora
# con la tapa abierta, vaso) y en los viales sueltos. Se aplican solo al armar desde el VLM (--sin-ajuste las ignora).
AJUSTE_FOTO = {
    "caja organizadora": [(747, 140), (784, -10), (1150, -10), (1150, 168), (1140, 300), (1126, 404), (1040, 400), (740, 364), (733, 330)],
    "vaso": "capsula",                         # base + boca (1280, 255) r 58
    "viales azules": [566, 243, 619, 338],
    "viales morados": [998, 486, 1118, 538],
    "caja de viales": [778, 430, 975, 540],
    "tapa de la caja de viales": [778, 541, 972, 662],
    "iPhone XR": [394, 178, 478, 335],
}
BOCA_VASO = ((1280, 255), 58)

# Zonas que la escena no usa: los botones de la consola que se ven en la foto.
RESERVAS = {"pestañas de la consola": [985, 575, 1165, 700], "botón de voz": [10, 592, 168, 700]}

# Lugares preferidos de los paneles (x, y de la esquina; si chocan, el motor busca otro lugar, achica u omite).
PREFERIDOS = {"catalogo": (385, 11), "protocolo": (300, 346), "lectura": (998, 410)}

# ---- tokens (tema.css de Max) + estados (wui_tokens de Pablo) ------------------------------------------------------
TINTA, TENUE, FOCO, OK, ALERTA, PELIGRO = "#eef4f3", "#93a3a0", "#5fe0d2", "#8be59c", "#ffcc66", "#ff6a5c"
LATENTE = "#6f7d7a"            # presencia sin reclamar atención (GRIS_MEDIO de WUI, llevado al frío del laboratorio)
SOBRE_FOCO = "#03110f"         # texto encima de una barra de foco
SANS = "'Instrument Sans','Helvetica Neue',Arial,system-ui,sans-serif"
MONO = "ui-monospace,'SF Mono',Menlo,Consolas,monospace"
T_ENTRA, T_ONDULA, T_FORMA, T_ASIENTA = 0.42, 0.88, 1.28, 1.72     # wui_motion.TIEMPOS_TINTA
CURVA = "cubic-bezier(0.16,1,0.3,1)"                               # ease_tinta_asienta
CURVA_LAB = "cubic-bezier(0.1,0.75,0.15,1)"                        # --sd-curva de Max: llega rápido y se asienta
T0 = 0.25                      # el barrido parte un instante después de proyectar
MIN_TXT = 12.0                 # mm: bajo esto no se lee en esta mesa (0,75 px de proyector por mm)

MX, MY, OFF = 1340, 710, 10    # lienzo: mm −10..1330 × −10..700


# ====================================================================================================================
# Geometría y ocupación
# ====================================================================================================================
def esc(s): return html.escape(str(s), quote=True)


def ancho(s, size, mono=False):
    """Ancho estimado de un texto (mm). Conservador: mono 0,61 em; sans con peso 0,60 em (mayúsculas 0,70)."""
    if mono: return len(s) * size * 0.61
    return sum(0.70 if (c.isupper() or c in "MW%") else 0.32 if c in " .·:,il/" else 0.60 for c in s) * size


def T(x, y, s, size, fill, cls="s", anchor="start", w=700, extra=""):
    """Texto girado 180°: se lee desde el lado de la persona. (x, y) es el ancla sobre la línea base."""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size:g}" fill="{fill}" class="{cls}" font-weight="{w}" '
            f'text-anchor="{anchor}" transform="rotate(180 {x:.1f} {y:.1f})"{extra}>{esc(s)}</text>')


def caja_texto(x, y, s, size, mono, anchor="start"):
    w = ancho(s, size, mono)
    x0 = x - w if anchor == "start" else x if anchor == "end" else x - w / 2
    return (x0, y - 0.24 * size, x0 + w, y + 0.76 * size)


def reg(x0, y0, x1, y1, L=10, col=TINTA, sw=2.6, cls=""):
    """Marcas de registro en las 4 esquinas (la firma de la escena de Max) en vez de una caja completa."""
    d = ""
    for x, y, sx, sy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x1, y1, -1, -1), (x0, y1, 1, -1)):
        d += f"M{x + sx * L:.1f},{y:.1f}L{x:.1f},{y:.1f}L{x:.1f},{y + sy * L:.1f}"
    return f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{sw}" stroke-linecap="square"{cls}/>'


def visto(x, y, col, t=5.5, sw=2.8):
    """Marca de «hecho», dibujada (el glifo ✓ de las monoespaciadas sale flaco). Simétrica: no necesita girarse."""
    return f'<path d="M{x + t:.1f},{y - t * 0.1:.1f}l{-t * 0.65:.1f},{-t * 0.75:.1f}l{-t * 1.2:.1f},{t * 1.5:.1f}" fill="none" stroke="{col}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round"/>'


def bb(p):
    p = np.asarray(p, float); return float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max())


def rect_poly(r): x0, y0, x1, y1 = r; return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


class Mesa:
    """Ocupación de la mesa a 1 mm: huellas de objetos, zonas reservadas, lo que el proyector no alcanza, lo ya
    colocado (paneles y textos) y los caminos. Con esto se decide dónde cabe cada cosa y se audita al final."""

    def __init__(self, objetos, canvas_mm):
        self.obj = {}
        for k, o in objetos.items():
            m = np.zeros((MY, MX), np.uint8); cv2.fillPoly(m, [self._i(o["poligono_mm"])], 1); self.obj[k] = m
        self.todo = np.clip(sum(self.obj.values()), 0, 1).astype(np.uint8) if self.obj else np.zeros((MY, MX), np.uint8)
        self.res = np.zeros((MY, MX), np.uint8)
        for r in RESERVAS.values(): cv2.fillPoly(self.res, [self._i(rect_poly(r))], 1)
        dentro = np.zeros((MY, MX), np.uint8); cv2.fillPoly(dentro, [self._i(canvas_mm)], 1)
        mesa = np.zeros((MY, MX), np.uint8); mesa[OFF + 3:OFF + 687, OFF + 3:OFF + 1317] = 1
        dentro = cv2.erode(dentro & mesa, np.ones((13, 13), np.uint8))
        self.fuera = 1 - dentro
        self.ui = np.zeros((MY, MX), np.uint8); self.cam = np.zeros((MY, MX), np.uint8)
        self.items = []; self.notas = []

    @staticmethod
    def _i(p): return (np.asarray(p, float) + OFF).round().astype(np.int32)

    @staticmethod
    def _s(r, m=0):
        x0, y0, x1, y1 = r
        return (slice(max(int(y0 - m) + OFF, 0), max(int(math.ceil(y1 + m)) + OFF, 0)), slice(max(int(x0 - m) + OFF, 0), max(int(math.ceil(x1 + m)) + OFF, 0)))

    def choque(self, r, sobre=(), m_obj=5, m_ui=4, caminos=True, ui=True):
        """Devuelve con qué choca el rectángulo (None si está libre)."""
        x0, y0, x1, y1 = r
        if x0 < -OFF or y0 < -OFF or x1 > MX - OFF or y1 > MY - OFF: return "borde"
        if self.fuera[self._s(r)].any(): return "fuera de la proyección"
        if self.res[self._s(r, 3)].any(): return "zona reservada"
        if self.todo[self._s(r, m_obj)].any():
            for k, m in self.obj.items():
                if k not in sobre and m[self._s(r, m_obj)].any(): return k
        if ui and self.ui[self._s(r, m_ui)].any(): return "otro elemento"
        if caminos and self.cam[self._s(r, 3)].any(): return "camino"
        return None

    def ocupa(self, nombre, r, sobre=(), tipo="ui"):
        self.ui[self._s(r)] = 1; self.items.append({"n": nombre, "r": tuple(round(v, 1) for v in r), "sobre": tuple(sobre), "tipo": tipo})

    def colocar(self, nombre, w, h, preferidos, ancla=None, radio=260, sobre=(), paso=6, **kw):
        """Primer lugar libre: los preferidos y, si no, el más cercano al ancla. None si no cabe."""
        for (x, y) in preferidos:
            if self.choque((x, y, x + w, y + h), sobre, **kw) is None: return (x, y)
        if ancla is None: return None
        ax, ay = ancla; cand = []
        for dx in range(-radio, radio + 1, paso):
            for dy in range(-radio, radio + 1, paso):
                cand.append((dx * dx + dy * dy, ax + dx - w / 2, ay + dy - h / 2))
        for _, x, y in sorted(cand):
            if self.choque((x, y, x + w, y + h), sobre, **kw) is None:
                self.notas.append(f"reubicado: {nombre} → ({x:.0f}, {y:.0f})"); return (x, y)
        return None

    def camino(self, pts):
        m = np.zeros((MY, MX), np.uint8); cv2.polylines(m, [self._i(pts)], False, 1, 7); self.cam |= m

    def bloqueo(self, m_obj=9, m_ui=7):
        k = lambda n: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * n + 1, 2 * n + 1))
        return (cv2.dilate(self.todo, k(m_obj)) | cv2.dilate(self.ui, k(m_ui)) | cv2.dilate(self.res, k(4)) | self.fuera).astype(bool)


PASO = 4    # mm por celda del ruteo


def celda(p): return (int((p[1] + OFF) // PASO), int((p[0] + OFF) // PASO))


def centro_celda(c): return np.array([c[1] * PASO + PASO / 2 - OFF, c[0] * PASO + PASO / 2 - OFF])


def ajusta(p): return centro_celda(celda(p))


def ruta(bloq, a, b, giro=9):
    """A* ortogonal sobre la mesa (celdas de 4 mm): pocos giros, nunca por encima de un objeto. Devuelve las esquinas
    en mm y el costo, o None."""
    R, C = MY // PASO, MX // PASO
    occ = bloq[:R * PASO, :C * PASO].reshape(R, PASO, C, PASO).any(axis=(1, 3))
    s, g = celda(a), celda(b)
    if not (0 <= s[0] < R and 0 <= s[1] < C and 0 <= g[0] < R and 0 <= g[1] < C) or occ[s] or occ[g]: return None
    D = ((0, 1), (1, 0), (0, -1), (-1, 0)); best = {(s, -1): 0}; q = [(0, 0, s, -1)]; prev = {}; fin = None
    while q:
        f, c, n, d = heapq.heappop(q)
        if n == g: fin = (n, d); break
        if best.get((n, d), 1e9) < c: continue
        for k, (dy, dx) in enumerate(D):
            m = (n[0] + dy, n[1] + dx)
            if not (0 <= m[0] < R and 0 <= m[1] < C) or occ[m]: continue
            if d != -1 and (k + 2) % 4 == d: continue
            nc = c + 1 + (giro if d not in (-1, k) else 0)
            if nc < best.get((m, k), 1e9):
                best[(m, k)] = nc; prev[(m, k)] = (n, d); heapq.heappush(q, (nc + abs(m[0] - g[0]) + abs(m[1] - g[1]), nc, m, k))
    if fin is None: return None
    cam = [fin[0]]; cur = fin
    while cur in prev: cur = prev[cur]; cam.append(cur[0])
    return [centro_celda(c) for c in cam[::-1]], best[fin] * PASO


def esquinas(pts):
    """Quita puntos repetidos y colineales."""
    out = []
    for p in pts:
        p = np.asarray(p, float)
        if out and np.abs(p - out[-1]).max() < 0.05: continue
        while len(out) >= 2:
            u, v = out[-1] - out[-2], p - out[-1]
            if abs(u[0] * v[1] - u[1] * v[0]) < 1e-6 and (u @ v) > 0: out.pop()
            else: break
        out.append(p)
    return out


def trazo(esq, radio=11):
    """Camino ortogonal con esquinas redondeadas."""
    d = f"M{esq[0][0]:.1f},{esq[0][1]:.1f}"
    for i in range(1, len(esq) - 1):
        u, v = esq[i] - esq[i - 1], esq[i + 1] - esq[i]; lu, lv = np.linalg.norm(u), np.linalg.norm(v); r = min(radio, lu / 2, lv / 2)
        p, q = esq[i] - u / lu * r, esq[i] + v / lv * r
        d += f"L{p[0]:.1f},{p[1]:.1f}Q{esq[i][0]:.1f},{esq[i][1]:.1f} {q[0]:.1f},{q[1]:.1f}"
    return d + f"L{esq[-1][0]:.1f},{esq[-1][1]:.1f}"


def largo(esq): return float(sum(np.linalg.norm(esq[i + 1] - esq[i]) for i in range(len(esq) - 1)))


def punta(esq, col, L=13, A=7.5):
    """Flecha al final del camino, orientada con el último tramo."""
    u = esq[-1] - esq[-2]; u = u / np.linalg.norm(u); n = np.array([-u[1], u[0]]); p = esq[-1]
    a, b, c = p + u * 2, p - u * L + n * A, p - u * L - n * A
    return f'<path d="M{a[0]:.1f},{a[1]:.1f}L{b[0]:.1f},{b[1]:.1f}L{c[0]:.1f},{c[1]:.1f}Z" fill="{col}"/>'


# ====================================================================================================================
# Objetos
# ====================================================================================================================
CLAVES = {"org": ("organizadora",), "viales": ("caja de viales",), "tapa": ("tapa",), "Z": ("azul",), "M": ("morad",),
          "vaso": ("vaso",), "fono": ("iphone", "teléfono", "telefono", "celular", "smartphone")}


def cargar_objetos(ruta_json, sin_ajuste):
    """→ ({etiqueta: {poligono_mm}}, {rol: etiqueta}, desde_vlm)."""
    O = {}
    if ruta_json:
        for o in json.load(open(ruta_json)):
            k = o["etiqueta"]; n = 2
            while k in O: k = f"{o['etiqueta']} {n}"; n += 1
            O[k] = {"poligono_mm": [tuple(p) for p in o["poligono_mm"]]}
    else:
        for o in json.load(open(AQUI / "out" / "vlm-solar-lab.json")):
            O[o["etiqueta"]] = {"poligono_mm": rect_poly(o["caja_mm"]), "descripcion": o.get("descripcion")}
        if not sin_ajuste:
            for k, a in AJUSTE_FOTO.items():
                if k not in O: continue
                if a == "capsula":
                    c0, r0 = np.array(VASO["base_mm"], float), VASO["radio_mm"]; c1, r1 = np.array(BOCA_VASO[0], float), BOCA_VASO[1]
                    ang = np.linspace(0, 2 * np.pi, 28, endpoint=False); cs = np.c_[np.cos(ang), np.sin(ang)]
                    hull = cv2.convexHull(np.vstack([c0 + r0 * cs, c1 + r1 * cs]).astype(np.float32))[:, 0, :]
                    O[k]["poligono_mm"] = [tuple(map(float, p)) for p in hull]
                else:
                    O[k]["poligono_mm"] = rect_poly(a) if len(a) == 4 and not isinstance(a[0], tuple) else list(a)
    rol = {}
    for r, subs in CLAVES.items():
        for k in O:
            kl = k.lower()
            if any(s in kl for s in subs) and not (r == "viales" and "tapa" in kl):
                rol[r] = k; break
    return O, rol, not ruta_json


# ====================================================================================================================
# Escena
# ====================================================================================================================
class Escena:
    def __init__(self, a):
        self.a = a
        met = json.load(open(AQUI / "fp-metric.json"))
        self.O, self.rol, self.vlm = cargar_objetos(a.objetos, a.sin_ajuste)
        self.M = Mesa(self.O, met["canvas_mm"])
        self.capas = {}
        self.Hm = np.array(met["mm_to_canvas"]); self.Hm = self.Hm / self.Hm[2, 2]
        self.buscar = self._clave(a.buscar) if a.buscar else None
        self.modo = "buscar" if self.buscar else "deposito"
        self.paso = max(1, min(len(PROTOCOLO), a.paso or 1))
        self.V = 1320 / T_ASIENTA      # mm/s del barrido: cruza la mesa en el tiempo de asentamiento de la marca

    # ---- utilidades -------------------------------------------------------------------------------------------------
    def _clave(self, q):
        q = q.lower().strip()
        for p in PIEZAS:
            if q == p["clave"] or q in p["alias"] or q in p["pieza"].lower(): return p
        sys.exit(f"--buscar {q!r}: no está en el catálogo ({', '.join(p['clave'] for p in PIEZAS)})")

    def d(self, x, extra=0.0):
        """Retardo de aparición: cuando el barrido (de la izquierda de la persona a su derecha) alcanza x."""
        if self.a.sin_barrido: return 0.05 + extra * 0.5
        return T0 + min(max((1320 - x) / self.V, 0), T_ASIENTA) + extra

    def add(self, capa, x, s, extra=0.0, cls="rv", attrs=""):
        self.capas.setdefault(capa, []).append(f'<g class="{cls}" style="--d:{self.d(x, extra):.2f}s"{attrs}>{s}</g>')

    def delta(self, rol, ref):
        """Si los objetos vienen de --objetos, corre las plantillas medidas en la foto al lugar actual del objeto."""
        if self.vlm or rol not in self.rol: return np.zeros(2)
        x0, y0, x1, y1 = bb(self.O[self.rol[rol]]["poligono_mm"]); r = bb(ref)
        return np.array([(x0 + x1) / 2 - (r[0] + r[2]) / 2, (y0 + y1) / 2 - (r[1] + r[3]) / 2])

    # ---- caja organizadora ---------------------------------------------------------------------------------------------
    def compartimentos(self):
        if "org" not in self.rol: return {}
        dl = self.delta("org", AJUSTE_FOTO["caja organizadora"]); L = ORGANIZADORA["lineas"]
        ly = lambda n, x: L[n][0][1] + (x - L[n][0][0]) * (L[n][1][1] - L[n][0][1]) / (L[n][1][0] - L[n][0][0])
        C = {}
        for fi, f in enumerate(ORGANIZADORA["filas"]):
            ct = f["cortes"]; n = len(ct) - 1
            for k in range(n):
                q = np.array([(ct[k][0], ly(f["arriba"], ct[k][0])), (ct[k + 1][0], ly(f["arriba"], ct[k + 1][0])),
                              (ct[k + 1][1], ly(f["abajo"], ct[k + 1][1])), (ct[k][1], ly(f["abajo"], ct[k][1]))], float) + dl
                C[f"{f['fila']}{n - k}"] = {"q": q, "c": q.mean(axis=0), "fila": fi}
        return C

    def dibuja_organizadora(self):
        C = self.comp = self.compartimentos()
        if not C: return
        org = self.rol["org"]; obj = self.buscar["en"] if self.buscar else None
        for cod, c in C.items():
            q = c["q"]; qi = q + (c["c"] - q) / np.linalg.norm(c["c"] - q, axis=1)[:, None] * 2.6
            pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in qi); ex = c["fila"] * 0.05
            if cod == obj: continue
            if self.modo == "buscar":      # atenuados: solo el contorno fino
                s = (f'<polygon points="{pts}" fill="none" stroke="{LATENTE}" stroke-width="1.6" stroke-linejoin="round"/>'
                     + T(c["c"][0], c["c"][1] - 4.5, cod, 12.5, LATENTE, "m", "middle", 700))
            else:
                s = (f'<polygon points="{pts}" fill="{FOCO}" class="ola"/>'
                     f'<polygon points="{pts}" fill="none" stroke="{TENUE}" stroke-width="1.8" stroke-linejoin="round"/>')
            self.add(3, c["c"][0], s, ex)
        if obj and obj in C:
            c = C[obj]; q = c["q"]; qi = q + (c["c"] - q) / np.linalg.norm(c["c"] - q, axis=1)[:, None] * 1.5
            pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in qi); x0, y0, x1, y1 = bb(q)
            s = (f'<polygon points="{pts}" fill="{FOCO}" fill-opacity=".34" class="late"/>'
                 f'<polygon points="{pts}" fill="none" stroke="{FOCO}" stroke-width="4" stroke-linejoin="round"/>'
                 + reg(x0 - 7, y0 - 7, x1 + 7, y1 + 7, 13, TINTA, 3))
            self.add(5, c["c"][0], s, T_ENTRA, cls="rv cae")
            self.M.items.append({"n": f"compartimento {obj}", "r": (x0 - 7, y0 - 7, x1 + 7, y1 + 7), "sobre": (org,), "tipo": "marca"})
            # el código del compartimento, grande, dentro de él
            self.add(6, c["c"][0], T(c["c"][0], c["c"][1] - 7, obj, 20, TINTA, "m", "middle", 800), T_ENTRA + 0.1)

    def guia_buscar(self):
        """Línea guía del renglón del catálogo al compartimento: ortogonal, por fuera de los objetos. Entra a la caja
        por el borde que cruza menos compartimentos ajenos."""
        if not (self.buscar and getattr(self, "fila_cat", None) and self.buscar["en"] in self.comp): return
        c = self.comp[self.buscar["en"]]; org = self.rol["org"]; q = c["q"].astype(np.float32); cen = c["c"]
        bloq = self.M.bloqueo(); ini = ajusta((self.fila_cat[0] + 12, self.fila_cat[1])); mejor = None
        for dv in ((0, 1), (-1, 0), (1, 0), (0, -1)):
            dv = np.array(dv, float); t = 0; borde = None
            while t < 500:
                p = cen + dv * t
                if borde is None and cv2.pointPolygonTest(q, (float(p[0]), float(p[1])), False) < 0: borde = t
                pi = (int(round(p[1])) + OFF, int(round(p[0])) + OFF)
                if not (0 <= pi[0] < MY and 0 <= pi[1] < MX): t = None; break
                if not bloq[pi] and borde is not None: break
                t += 1
            if t is None or t >= 500: continue
            if not self.M.obj[org][int(round((cen + dv * (t - 12))[1])) + OFF, int(round((cen + dv * (t - 12))[0])) + OFF]: continue      # al salir de la caja hay otra cosa: por ahí no
            ap = ajusta(cen + dv * (t + 5))
            r = ruta(bloq, ini, ap)
            if not r: continue
            fin = np.where(dv != 0, cen + dv * (borde - 9), ap)          # 9 mm dentro del compartimento
            ajenos = [k for u in range(borde, t) for k, o in self.comp.items() if k != self.buscar["en"] and cv2.pointPolygonTest(o["q"].astype(np.float32), tuple(map(float, cen + dv * u)), False) >= 0]
            cruza = len(ajenos); costo = r[1] + 4 * cruza + 300 * len(set(ajenos))      # antes menos compartimentos ajenos que menos mm
            if len(set(ajenos)) > 1:      # no se atraviesan dos compartimentos: la línea llega al borde de la caja, alineada, con flecha
                fin = np.where(dv != 0, cen + dv * (t - 4), ap); cruza = -1
            if mejor is None or costo < mejor[0]: mejor = (costo, r[0], fin, cruza)
        if not mejor: self.M.notas.append("omitido: línea guía del catálogo (sin ruta libre)"); return
        esq = esquinas([np.array(self.fila_cat)] + [np.array([p[0], self.fila_cat[1]]) if i == 0 else p for i, p in enumerate(mejor[1])] + [mejor[2]])
        self.M.camino(esq); self.audit_camino("guía del catálogo", esq, (org,))
        d = trazo(esq); x = self.fila_cat[0]
        cabo = punta(esq, FOCO) if mejor[3] < 0 else (f'<circle cx="{esq[-1][0]:.1f}" cy="{esq[-1][1]:.1f}" r="6.5" fill="{FOCO}"/>'
                                                      f'<circle cx="{esq[-1][0]:.1f}" cy="{esq[-1][1]:.1f}" r="6.5" fill="none" stroke="{FOCO}" stroke-width="2.5" class="onda"/>')
        s = (f'<path d="{d}" fill="none" stroke="{FOCO}" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round" pathLength="1" class="tz"/>'
             f'<g class="rv" style="--d:{self.d(x, T_FORMA):.2f}s">{cabo}</g>')
        self.add(4, x, s, T_ENTRA, cls="")
        self.M.notas.append(f"guía del catálogo: {len(esq) - 2} giros, {largo(esq):.0f} mm, " + ("llega al borde de la caja alineada con el compartimento (no cruza compartimentos ajenos)" if mejor[3] < 0 else f"cruza {mejor[3]:.0f} mm de compartimentos ajenos"))

    # ---- catálogo ------------------------------------------------------------------------------------------------------
    def panel_catalogo(self):
        if "org" not in self.rol: return
        for esc_ in (1.0, 0.9, 0.82):
            W, H = 295 * esc_, 156 * esc_
            pos = self.M.colocar("catálogo", W, H, [PREFERIDOS["catalogo"]], ancla=(560, 90), caminos=False)
            if pos: break
        if not pos: self.M.notas.append("omitido: panel de catálogo (no cabe)"); return
        if esc_ < 1: self.M.notas.append(f"achicado: catálogo ×{esc_}")
        X, Y = pos; self.M.ocupa("panel catálogo", (X, Y, X + W, Y + H)); k = esc_
        P = lambda u, v: (X + W - u * k, Y + H - v * k)
        filas = PIEZAS[:8]
        if self.buscar and self.buscar not in filas: filas = filas[:7] + [self.buscar]
        s = [reg(X, Y, X + W, Y + H, 12, TINTA, 2.8)]
        x, y = P(12, 20); s.append(T(x, y, "CATÁLOGO", 16 * k, TINTA, "s esp", "start", 800))
        x, y = P(283, 20); s.append(T(x, y, "CANT · UBIC", max(12.5 * k, MIN_TXT), TENUE, "m", "end", 700))
        (x1, y1), (x2, y2) = P(10, 27), P(285, 27); s.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{TENUE}" stroke-width="2"/>')
        self.add(7, X + W / 2, "".join(s))
        for i, p in enumerate(filas):
            v = 43.5 + i * 15.2; sel = self.buscar is p; col = SOBRE_FOCO if sel else (TENUE if self.buscar else TINTA); r = ""
            if sel:
                x, y = P(291, v + 5); r = f'<rect x="{x:.1f}" y="{y:.1f}" width="{287 * k:.1f}" height="{16.4 * k:.1f}" rx="2" fill="{FOCO}"/>'
                self.fila_cat = (X + W + 5, P(0, v - 3.5)[1])
            nombre = p["pieza"]; tam = 14.5 * k
            while ancho(nombre, tam) > 168 * k and tam > MIN_TXT: tam -= 0.5
            x, y = P(12, v); r += T(x, y, nombre, max(tam, MIN_TXT), col, "s", "start", 700)
            x, y = P(226, v); r += T(x, y, f"×{p['cant']}", max(14 * k, MIN_TXT), col, "m", "end", 700)
            x, y = P(283, v); r += T(x, y, p["en"], max(14.5 * k, MIN_TXT), col if sel or self.buscar else FOCO, "m", "end", 800)
            x, y = P(291, v + 5)
            r += f'<rect x="{x:.1f}" y="{y:.1f}" width="{287 * k:.1f}" height="{15.2 * k:.1f}" fill="#000" fill-opacity="0"/>'
            self.add(7, X + W / 2, r, 0.12 + i * 0.045, attrs=f' data-press="buscar:{p["clave"]}"')
        if len(PIEZAS) > len(filas): self.M.notas.append(f"catálogo: se muestran {len(filas)} de {len(PIEZAS)} piezas (lo que cabe a tamaño legible)")

    # ---- caja de viales (origen) -----------------------------------------------------------------------------------------
    def rejilla_viales(self):
        if "viales" not in self.rol: self.G = None; return
        x0, y0, x1, y1 = bb(self.O[self.rol["viales"]]["poligono_mm"]); ml, mt, mr, mb = VIALES["margen_mm"]
        gx0, gy0, gx1, gy1 = x0 + ml, y0 + mt, x1 - mr, y1 - mb; nc, nf = VIALES["cols"], VIALES["filas"]
        self.G = {"box": (x0, y0, x1, y1), "g": (gx0, gy0, gx1, gy1), "pw": (gx1 - gx0) / nc, "ph": (gy1 - gy0) / nf}

    def celda_vial(self, cod):
        """«A3» → (centro, índice de fila, índice de columna desde la izquierda de la foto)."""
        g = self.G; f = "ABCDEFGH".index(cod[0]); col = int(cod[1:]); k = VIALES["cols"] - col
        return np.array([g["g"][0] + (k + 0.5) * g["pw"], g["g"][1] + (f + 0.5) * g["ph"]]), f, k

    def dibuja_viales(self):
        if not self.G: return
        g = self.G; viales = self.rol["viales"]; tapa = self.rol.get("tapa"); nc, nf = VIALES["cols"], VIALES["filas"]
        gx0, gy0, gx1, gy1 = g["g"]; pw, ph = g["pw"], g["ph"]; activo = self.modo == "deposito"
        paso = PROTOCOLO[self.paso - 1] if activo else None
        usados = {p["desde"]: i + 1 for i, p in enumerate(PROTOCOLO)} if activo else {}
        fa, ca = (paso["desde"][0], int(paso["desde"][1:])) if paso else (None, None)
        for f in range(nf):
            for k in range(nc):
                cod = f"{'ABCD'[f]}{nc - k}"; x, y = gx0 + k * pw + 2.5, gy0 + f * ph + 2.2; w, h = pw - 5, ph - 4.4
                if paso and cod == paso["desde"]: continue
                n = usados.get(cod); hecho = n is not None and n < self.paso
                s = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="5" fill="{FOCO}" class="ola"/>'
                     f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="5" fill="none" stroke="{OK if hecho else TINTA if n else TENUE}" stroke-width="{2.4 if n else 2}"/>')
                if n: s += visto(x + w / 2, y + h / 2, OK) if hecho else T(x + w / 2, y + h / 2 - 5, str(n), 14.5, TINTA, "m", "middle", 800)
                self.add(3, x + w / 2, s, f * 0.07)
        # ejes: letras de fila en la franja libre de la caja, números de columna sobre la tapa (como la grilla de Max)
        for f in range(nf):
            on = "ABCD"[f] == fa; x = gx0 - VIALES["margen_mm"][0] / 2 - 0.5; y = gy0 + (f + 0.5) * ph - 5
            self.add(6, x, T(x, y, "ABCD"[f], 15 if on else 13, FOCO if on else TENUE, "m", "middle", 800), 0.15)
        yn = g["box"][3] + 8
        for k in range(nc):
            on = (nc - k) == ca; x = gx0 + (k + 0.5) * pw
            self.add(6, x, T(x, yn, str(nc - k), 15 if on else 13, FOCO if on else TENUE, "m", "middle", 800), 0.2)
        self.M.ocupa("ejes de la rejilla", (gx0, yn - 4, gx1, yn + 12), sobre=(viales, tapa) if tapa else (viales,), tipo="marca")
        if paso:
            c, f, k = self.celda_vial(paso["desde"]); x, y = gx0 + k * pw + 1.5, gy0 + f * ph + 1.2; w, h = pw - 3, ph - 2.4
            s = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="5" fill="{FOCO}" fill-opacity=".36" class="late"/>'
                 f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="5" fill="none" stroke="{FOCO}" stroke-width="3.6"/>')
            # guías de fila y columna hasta los ejes, como al buscar en una tabla
            s += (f'<path d="M{gx0 - 3:.1f},{c[1]:.1f}H{x - 2:.1f}M{c[0]:.1f},{y + h + 2:.1f}V{g["box"][3] + 2:.1f}" stroke="{FOCO}" stroke-width="2.2" opacity=".75"/>')
            self.add(5, c[0], s, 0.3, cls="rv cae")
            if tapa and not self.a.alerta:      # el rótulo del origen, sobre la tapa abierta
                col = VIALES["colores"][ca - 1]; t1 = f"{paso['desde']} · {col}"; tx = gx1 - 2; ty = yn + 26
                r = caja_texto(tx, ty, t1, 17, True)
                if self.M.choque(r, sobre=(tapa,)) is None:
                    self.add(6, tx, T(tx, ty, t1, 17, FOCO, "m", "start", 800), 0.4); self.M.ocupa("rótulo del origen", r, sobre=(tapa,))
                else: self.M.notas.append("omitido: rótulo del origen sobre la tapa")

    # ---- viales sueltos (destinos) y camino de la pipeta -------------------------------------------------------------------
    def destinos(self):
        D = {}
        for g, d in DESTINOS.items():
            if g not in self.rol: continue
            dl = self.delta(g, rect_poly(AJUSTE_FOTO[d["objeto"]]))
            for i, c in enumerate(d["viales"]): D[f"{g}{i + 1}"] = {"c": np.array(c, float) + dl, "g": g, "r": d["radio"]}
        return D

    def dibuja_destinos(self):
        D = self.D = self.destinos(); activo = self.modo == "deposito"
        paso = PROTOCOLO[self.paso - 1] if activo else None
        usados = {p["hasta"]: i + 1 for i, p in enumerate(PROTOCOLO)} if activo else {}
        for cod, v in D.items():
            n = usados.get(cod); hecho = n is not None and n < self.paso; x, y = v["c"]; r = v["r"]
            if paso and cod == paso["hasta"]: continue
            i = int(cod[1:])
            s = (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r - 1.5:.1f}" fill="{FOCO}" class="ola"/>'
                 f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r - 1.5:.1f}" fill="none" stroke="{OK if hecho else TINTA if n else TENUE}" stroke-width="{2.4 if n else 2}"/>')
            if n: s += visto(x, y, OK) if hecho else T(x, y - 5, str(n), 14.5, TINTA, "m", "middle", 800)
            self.add(3, x, s, i * 0.06)
        for g, d in DESTINOS.items():      # rótulo del grupo, donde quepa
            if g not in self.rol: continue
            o = self.rol[g]; x0, y0, x1, y1 = bb(self.O[o]["poligono_mm"]); t = f"DESTINO {g} · {d['nombre']}"; w = ancho(t, 13, True)
            cands = [((x0 + x1) / 2 + w / 2, y1 + 10), ((x0 + x1) / 2 + w / 2, y0 - 20), (x1 + w + 10, (y0 + y1) / 2 - 5), (x0 - 10, (y0 + y1) / 2 - 5), (x1, y1 + 10), (x0 + w, y1 + 10)]
            self.rotulo(f"rótulo destino {g}", t, 13, TENUE, cands, x0)

    def rotulo(self, nombre, t, size, col, cands, x, mono=True, w=700, extra=0.3, sobre=(), capa=6):
        """Coloca un texto en el primer candidato libre (ancla «start»); si ninguno sirve, se omite."""
        for (tx, ty) in cands:
            r = caja_texto(tx, ty, t, size, mono)
            if self.M.choque((r[0] - 1, r[1] - 1, r[2] + 1, r[3] + 1), sobre) is None:
                self.add(capa, x, T(tx, ty, t, size, col, "m" if mono else "s", "start", w), extra); self.M.ocupa(nombre, r, sobre); return True
        self.M.notas.append(f"omitido: {nombre} (sin lugar libre)"); return False

    def audit_camino(self, nombre, esq, propios):
        """Cuenta los mm de camino que pasan por encima de objetos que no son su origen ni su destino."""
        m = np.zeros((MY, MX), np.uint8); cv2.polylines(m, [self.M._i(esq)], False, 1, 3); mal = []
        for k, om in self.M.obj.items():
            if k in propios: continue
            n = int((m & om).sum())
            if n > 6: mal.append(f"{k} ({n / 3:.0f} mm)")
        if int((m & self.M.fuera).sum()) > 6: mal.append("fuera de la proyección")
        self.M.items.append({"n": nombre, "tipo": "camino", "cruces": mal})

    def camino_pipeta(self):
        if self.modo != "deposito" or not self.G: return
        paso = PROTOCOLO[self.paso - 1]
        if paso["hasta"] not in self.D: self.M.notas.append(f"omitido: camino del paso {self.paso} (no está el destino {paso['hasta']})"); return
        g = self.G; c, f, k = self.celda_vial(paso["desde"]); dst = self.D[paso["hasta"]]; nc, nf = VIALES["cols"], VIALES["filas"]
        bx0, by0, bx1, by1 = g["box"]; pw, ph = g["pw"], g["ph"]; bloq = self.M.bloqueo(); S = 14
        sx = 1 if dst["c"][0] > c[0] else -1
        # salidas de la caja: recto si la celda está en el borde; si no, por el canal entre viales
        gx0, gx1 = g["g"][0], g["g"][2]; gapx = c[0] + sx * pw / 2
        if not (gx0 + 2 < gapx < gx1 - 2): gapx = c[0] - sx * pw / 2
        lado = (gapx - c[0]) / (pw / 2); canal = [(c[0] + lado * (pw / 2 - 3), c[1])]
        sal = []
        for borde, yb, sy in ((f == 0, by0 - S, -1), (f == nf - 1, by1 + S, 1)):      # hacia arriba y hacia abajo
            e = ajusta((c[0] if borde else gapx, yb))
            sal.append(([(e[0], c[1] + sy * (ph / 2 - 1))] if borde else canal + [(e[0], c[1])], e))
        for borde, xb, sx2 in ((k == 0, bx0 - S, -1), (k == nc - 1, bx1 + S, 1)):      # hacia los lados
            if borde: e = ajusta((xb, c[1])); sal.append(([(c[0] + sx2 * (pw / 2 - 1), e[1])], e))
            elif f > 0: e = ajusta((xb, c[1] - ph / 2)); sal.append(([(c[0], c[1] - ph / 2 + 3), (c[0], e[1])], e))
        # llegadas al vial: por el lado del grupo que no tapa otro vial
        otros = [v["c"] for q, v in self.D.items() if v["g"] == dst["g"] and q != paso["hasta"]]
        ox0, oy0, ox1, oy1 = bb(self.O[self.rol[dst["g"]]]["poligono_mm"]); lle = []
        for dv in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            dv = np.array(dv, float); n = np.array([-dv[1], dv[0]])
            if any((o - dst["c"]) @ dv > 0 and abs((o - dst["c"]) @ n) < dst["r"] * 1.7 for o in otros): continue
            t = (ox1 - dst["c"][0]) if dv[0] > 0 else (dst["c"][0] - ox0) if dv[0] < 0 else (oy1 - dst["c"][1]) if dv[1] > 0 else (dst["c"][1] - oy0)
            ap = ajusta(dst["c"] + dv * (max(t, dst["r"]) + S + 2))
            fin = np.where(dv != 0, dst["c"] + dv * (dst["r"] + 3), ap)
            lle.append((ap, fin))
        mejor = None
        for m_obj in (9, 6):      # primero con holgura; si no hay paso, más ceñido a los objetos
            for pre, e in sal:
                for ap, fin in lle:
                    r = ruta(bloq, e, ap)
                    if not r: continue
                    esq = esquinas([np.array(p, float) for p in pre] + r[0] + [fin]); costo = r[1] + 30 * len(esq)
                    if mejor is None or costo < mejor[0]: mejor = (costo, esq)
            if mejor: break
            bloq = self.M.bloqueo(m_obj=6, m_ui=5); self.M.notas.append(f"camino del paso {self.paso}: ruteado con holgura mínima (6 mm)")
        if not mejor: self.M.notas.append(f"omitido: camino del paso {self.paso} (sin ruta libre)"); return
        esq = mejor[1]; self.M.camino(esq); self.audit_camino(f"camino del paso {self.paso}", esq, (self.rol["viales"], self.rol[dst["g"]]))
        d = trazo(esq); L = largo(esq); x = c[0]; pausa = self.a.alerta; col = TENUE if pausa else FOCO
        dur = T_FORMA                                # el trazo avanza en el tiempo de «se forma»
        s = f'<path d="{d}" fill="none" stroke="{col}" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round" pathLength="1" class="tz"/>'
        fin_s = [punta(esq, col)]
        if not pausa:
            s += (f'<path d="{d}" fill="none" stroke="{SOBRE_FOCO}" stroke-width="1.5" stroke-linecap="round" stroke-dasharray="1 15" class="rv fluye" style="--d:{self.d(x, T_ENTRA + dur):.2f}s"/>')
            fin_s.append(f'<circle r="5.5" fill="{TINTA}"><animateMotion dur="{max(2.2, L / 260):.1f}s" begin="{self.d(x, T_ENTRA + dur):.2f}s" repeatCount="indefinite" path="{d}" calcMode="linear"/></circle>')
        # destino: lleno (origen hueco, destino lleno, como el traspaso de Max)
        vx, vy = dst["c"]; r = dst["r"]
        fin_s.append(f'<circle cx="{vx:.1f}" cy="{vy:.1f}" r="{r:.1f}" fill="{col}" fill-opacity=".42" class="{"" if pausa else "late"}"/>'
                     f'<circle cx="{vx:.1f}" cy="{vy:.1f}" r="{r:.1f}" fill="none" stroke="{col}" stroke-width="3.6"/>'
                     + ("" if pausa else f'<circle cx="{vx:.1f}" cy="{vy:.1f}" r="{r:.1f}" fill="none" stroke="{FOCO}" stroke-width="2.5" class="onda"/>'))
        s += f'<g class="rv" style="--d:{self.d(x, T_ENTRA + dur - 0.1):.2f}s">{"".join(fin_s)}</g>'
        self.add(4, x, s, T_ENTRA, cls="")
        # número del paso sobre el camino, al salir de la caja
        fuera_caja = [p for p in esq if not (bx0 - 2 <= p[0] <= bx1 + 2 and by0 - 2 <= p[1] <= by1 + 2)]
        cand = []
        for a_, b_ in zip(fuera_caja[:-1], fuera_caja[1:]):
            n_ = int(np.linalg.norm(b_ - a_) // 5)
            cand += [a_ + (b_ - a_) * i / max(n_, 1) for i in range(n_ + 1)]
        for bp in cand:
            r = (bp[0] - 11, bp[1] - 11, bp[0] + 11, bp[1] + 11)
            if self.M.choque(r, caminos=False, m_obj=2, m_ui=5) is None:
                b = (f'<circle cx="{bp[0]:.1f}" cy="{bp[1]:.1f}" r="10.5" fill="{col}"/>' + T(bp[0], bp[1] - 5.6, str(self.paso), 16, SOBRE_FOCO, "m", "middle", 800))
                self.add(5, bp[0], b, T_ENTRA + 0.1, cls="rv cae"); self.M.ocupa("número del paso", r); break
        else: self.M.notas.append("omitido: número del paso sobre el camino")
        # rótulo del destino («Z1»), junto al vial y fuera del grupo
        t = paso["hasta"]; w = ancho(t, 18, True)
        cands = [(ox1 + w + 12, vy - 6), (ox0 - 12, vy - 6), (vx + w / 2, oy1 + 12), (vx + w / 2, oy0 - 26), (ox1 + w + 12, oy0 - 22), (ox1 + w + 12, oy1 + 8), (ox0 - 12, oy1 + 8), (ox0 - 12, oy0 - 22)]
        self.rotulo("rótulo del vial de destino", t, 18, col, cands, vx, w=800, extra=T_ENTRA + dur)
        self.M.notas.append(f"camino del paso {self.paso}: {paso['desde']} → {paso['hasta']}, {len(esq) - 2} giros, {L:.0f} mm")

    # ---- panel del protocolo / aviso de pausa ---------------------------------------------------------------------------
    def panel_protocolo(self):
        if not self.G and not self.a.alerta: return
        W, H = 325, 78; fono = self.rol.get("fono"); ancla = (460, 385)
        pref = [PREFERIDOS["protocolo"]]; dim = self.modo == "buscar" and not self.a.alerta
        if dim: W = 262; pref = [(pref[0][0] + 63, pref[0][1])]      # sin botones: el panel queda en reposo, más angosto
        if self.a.alerta and fono:      # el aviso va pegado al teléfono, dejando lugar a su marco
            x0, y0, x1, y1 = bb(self.O[fono]["poligono_mm"]); ancla = ((x0 + x1) / 2, y1 + 60); H = 76
            pref = [(pref[0][0], max(pref[0][1], y1 + 14))] if pref[0][1] < y1 + 60 and pref[0][0] < x1 and pref[0][0] + W > x0 else pref
        pos = self.M.colocar("protocolo", W, H, pref, ancla=ancla, caminos=False)
        if not pos:
            W, H = 280, 70; pos = self.M.colocar("protocolo", W, H, [], ancla=ancla, caminos=False)
            if pos: self.M.notas.append("achicado: panel del protocolo")
        if not pos: self.M.notas.append("omitido: panel del protocolo (no cabe)"); return
        X, Y = pos; k = W / (262 if dim else 325); self.M.ocupa("panel protocolo" if not self.a.alerta else "aviso de pausa", (X, Y, X + W, Y + H))
        self.slot = (X, Y, W, H); P = lambda u, v: (X + W - u * k, Y + H - v * k)
        if self.a.alerta:
            x, y = P(325, 78)
            s = (f'<rect x="{x + 1.5:.1f}" y="{y + 1.5:.1f}" width="{W - 3:.1f}" height="{H - 3:.1f}" rx="2" fill="none" stroke="{PELIGRO}" stroke-width="3"/>'
                 f'<rect x="{X + W - 13 * k:.1f}" y="{Y:.1f}" width="{13 * k:.1f}" height="{H:.1f}" fill="{PELIGRO}" class="alarma"/>')
            x, y = P(24, 30); s += T(x, y, "OBJETO AJENO", 25 * k, PELIGRO, "s esp", "start", 800)
            x, y = P(24, 51); s += T(x, y, "protocolo en pausa", 18 * k, TINTA, "s", "start", 800)
            x, y = P(24, 69); s += T(x, y, "retira el teléfono de la mesa", max(13.5 * k, MIN_TXT), TENUE, "s", "start", 700)
            self.add(8, X + W / 2, s, cls="rv cae"); return
        col = TENUE if dim else TINTA; n = len(PROTOCOLO); paso = PROTOCOLO[self.paso - 1]
        s = [reg(X, Y, X + W, Y + H, 12, col, 2.8)]
        x, y = P(12, 19); s.append(T(x, y, "PIPETA" if dim else f"PASO {self.paso} / {n}", 13.5 * k, TENUE if dim else FOCO, "m", "start", 800))
        for i in range(n):      # avance: una pastilla por paso (lab-avance de Max)
            x, y = P(96 + i * 16 + 13, 19)
            s.append(f'<rect x="{x:.1f}" y="{y + 2:.1f}" width="{13 * k:.1f}" height="{5 * k:.1f}" rx="1" fill="{TENUE if dim else TINTA if i < self.paso - 1 else FOCO if i == self.paso - 1 else LATENTE}"/>')
        x, y = P(12, 49); s.append(T(x, y, f"{paso['desde']} → {paso['hasta']}", 28 * k, col, "m", "start", 800))
        cn = VIALES["colores"][int(paso["desde"][1:]) - 1]; dn = DESTINOS[paso["hasta"][0]]["nombre"]
        det = f"{paso['gotas']} gota{'s' if paso['gotas'] != 1 else ''} · {cn} → {dn}"; tam = 13.5 * k
        while ancho(det, tam) > 152 * k and tam > MIN_TXT: tam -= 0.5
        x, y = P(12, 68); s.append(T(x, y, det, max(tam, MIN_TXT), TENUE, "s", "start", 700))
        self.add(7, X + W / 2, "".join(s))
        for i, p in enumerate(PROTOCOLO):      # la lista del protocolo
            v = 18 + i * 17.2; hecho = i < self.paso - 1; ahora = i == self.paso - 1 and not dim; r = ""
            if ahora:
                x, y = P(256, v + 4.5); r = f'<rect x="{x:.1f}" y="{y:.1f}" width="{84 * k:.1f}" height="{16.4 * k:.1f}" rx="2" fill="{FOCO}"/>'
            c2 = SOBRE_FOCO if ahora else TENUE if (hecho or dim) else TINTA
            x, y = P(176, v)
            r += visto(x - 4.5 * k, y + 5 * k, c2, 4.2 * k, 2.4) if hecho and not dim else T(x, y, str(i + 1), 13.5 * k, c2, "m", "start", 800)
            x, y = P(190, v); r += T(x, y, f"{p['desde']}→{p['hasta']}", 13.5 * k, c2, "m", "start", 700)
            self.add(7, X + W / 2, r, 0.12 + i * 0.06)
        if not dim:      # botones de toque
            for (u, v, w, h, t, pr, c3) in ((264, 6, 55, 36, "LISTO", "listo", FOCO), (264, 47, 55, 25, "ATRÁS", "atras", TENUE)):
                x, y = P(u + w, v + h); cx, cy = P(u + w / 2, v + h / 2 + 4.6)
                b = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w * k:.1f}" height="{h * k:.1f}" rx="3" fill="{c3}" fill-opacity=".12" stroke="{c3}" stroke-width="2.6"/>'
                     + T(cx, cy, t, 13.5 * k, c3, "s esp", "middle", 800))
                self.add(7, X + W / 2, b, 0.3, attrs=f' data-press="{pr}"')

    # ---- vaso ----------------------------------------------------------------------------------------------------------
    def vaso(self):
        if "vaso" not in self.rol: return
        vk = self.rol["vaso"]; dl = self.delta("vaso", AJUSTE_FOTO["vaso"] if AJUSTE_FOTO["vaso"] != "capsula" else self.O[vk]["poligono_mm"])
        if self.vlm: base = np.array(VASO["base_mm"], float)
        else:        # objeto alto: se ve inclinado hacia afuera; la base es el extremo más cercano al centro de la mesa
            p = np.array(self.O[vk]["poligono_mm"], float); cm = np.array([660, 345.0]); q = p[np.argmin(np.linalg.norm(p - cm, axis=1))]
            base = q + (p.mean(axis=0) - q) / np.linalg.norm(p.mean(axis=0) - q) * VASO["radio_mm"]
        rb = VASO["radio_mm"]; n = float(np.clip(self.a.nivel if self.a.nivel is not None else VASO["nivel_inicial"], 0, 100))
        obj, tol = VASO["objetivo"], VASO["tolerancia"]
        if n > obj + tol: est, col, msg = "pasaste", PELIGRO, f"TE PASASTE +{n - obj:.0f}"
        elif n >= obj - tol: est, col, msg = "listo", OK, "LISTO · JUSTO"
        elif n >= obj - VASO["cerca"]: est, col, msg = "falta", ALERTA, f"YA CASI · {obj - n:.0f}"
        else: est, col, msg = "falta", TINTA, f"FALTA {obj - n:.0f} %"
        # escala: una regla junto a la base; el 0 hacia la persona y sube alejándose de ella
        Ls = [146, 130, 110]; pos = None
        for L in Ls:
            pos = self.M.colocar("escala del vaso", 26, L + 8, [(base[0] - 28, base[1] + rb + 6), (base[0] - rb - 36, base[1] - L / 2), (base[0] + rb + 10, base[1] - L / 2)], ancla=(base[0], base[1] + rb + L / 2), radio=150, paso=4, caminos=False)
            if pos: break
        if not pos: self.M.notas.append("omitido: escala del vaso (sin lugar junto al vaso)"); return
        if L != Ls[0]: self.M.notas.append(f"achicado: escala del vaso a {L} mm")
        X, Y = pos; self.M.ocupa("escala del vaso", (X, Y, X + 26, Y + L + 8)); bx, y0 = X + 3, Y + 4; yy = lambda v: y0 + L * v / 100
        s = [f'<rect x="{bx:.1f}" y="{y0:.1f}" width="10" height="{L}" fill="none" stroke="{TENUE}" stroke-width="2"/>']
        t = "".join(f'M{bx + 10:.1f},{yy(v):.1f}h{9 if v % 50 == 0 else 5}' for v in range(0, 101, 10))
        s.append(f'<path d="{t}" stroke="{TENUE}" stroke-width="2"/>')
        dd = self.d(X, T_ENTRA)
        nv = min(n, obj) if est == "pasaste" else n
        s.append(f'<rect x="{bx + 1:.1f}" y="{y0:.1f}" width="8" height="{L * nv / 100:.1f}" fill="{OK if est == "listo" else TINTA}" class="sube" style="--d:{dd:.2f}s"/>')
        if est == "pasaste":
            s.append(f'<rect x="{bx + 1:.1f}" y="{yy(obj):.1f}" width="8" height="{L * (n - obj) / 100:.1f}" fill="{PELIGRO}" class="sube" style="--d:{dd + 0.5:.2f}s"/>')
        s.append(f'<path d="M{bx - 3:.1f},{yy(obj):.1f}h{24}" stroke="{FOCO}" stroke-width="4"/>')      # la meta
        s.append(f'<g class="sube2" style="--d:{dd:.2f}s;--y:{-L * n / 100:.1f}px"><path d="M{bx + 11:.1f},{yy(n):.1f}l11,-6.5v13z" fill="{col}"/></g>')      # el nivel
        self.add(5, X, "".join(s))
        # lectura
        W, H = 122, 49
        pos = self.M.colocar("lectura del vaso", W, H, [PREFERIDOS["lectura"], (X - W - 6, Y), (X - W - 6, Y + 40)], ancla=(X - W / 2 - 8, Y + 30), radio=160, caminos=False)
        if not pos: self.M.notas.append("omitido: lectura del vaso (no cabe)"); return
        X2, Y2 = pos; self.M.ocupa("lectura del vaso", (X2, Y2, X2 + W, Y2 + H)); P = lambda u, v: (X2 + W - u, Y2 + H - v)
        num = f"{n:.0f}"; x, y = P(4, 27); s = T(x, y, num, 31, col if est != "falta" else TINTA, "m", "start", 800)
        u = 4 + ancho(num, 31, True) + 3; x, y = P(u, 27); s += T(x, y, "%", 17, TENUE, "m", "start", 800)
        x, y = P(W - 3, 27); s += T(x, y, f"de {obj}", 14, FOCO, "m", "end", 800)
        tam = 14.5
        while ancho(msg, tam) > W - 22 and tam > MIN_TXT: tam -= 0.5
        x, y = P(4, 44.5); s += f'<rect x="{x - 9:.1f}" y="{y - 1:.1f}" width="9" height="11" fill="{col}" class="{'alarma' if est == 'pasaste' else ''}"/>'
        x, y = P(18, 44.5); s += T(x, y, msg, tam, col, "s esp", "start", 800)
        self.add(7, X2 + W / 2, s, T_ENTRA)

    # ---- alerta de contaminación ---------------------------------------------------------------------------------------
    def alerta(self):
        if not self.a.alerta: return
        fono = self.rol.get("fono")
        if not fono: self.M.notas.append("alerta: no hay teléfono entre los objetos; solo se muestra el aviso"); return
        p = np.array(self.O[fono]["poligono_mm"], float); x0, y0, x1, y1 = bb(p); m = 6
        # el teléfono es negro: la luz va alrededor, no encima. Los lados laten; las esquinas quedan fijas.
        s = (f'<rect x="{x0 - m:.1f}" y="{y0 - m:.1f}" width="{x1 - x0 + 2 * m:.1f}" height="{y1 - y0 + 2 * m:.1f}" fill="none" stroke="{PELIGRO}" stroke-width="3.2" class="alarma"/>'
             + reg(x0 - m, y0 - m, x1 + m, y1 + m, 20, PELIGRO, 5))
        self.add(8, (x0 + x1) / 2, s, cls="rv cae")
        self.M.items.append({"n": "marco de alerta", "r": (x0 - m - 2.5, y0 - m - 2.5, x1 + m + 2.5, y1 + m + 2.5), "sobre": (fono,), "tipo": "marca"})

    # ---- huellas, retícula y barrido ---------------------------------------------------------------------------------------
    def huellas(self):
        """Destello del barrido sobre cada objeto del laboratorio y sus marcas de registro."""
        for r in ("org", "viales", "tapa", "Z", "M", "vaso"):
            if r not in self.rol: continue
            p = self.O[self.rol[r]]["poligono_mm"]; x0, y0, x1, y1 = bb(p); pts = " ".join(f"{x:.0f},{y:.0f}" for x, y in p)
            if not self.a.sin_barrido:
                self.capas.setdefault(1, []).append(f'<polygon points="{pts}" fill="{FOCO}" class="dest" style="--d:{self.d((x0 + x1) / 2) - 0.12:.2f}s"/>')
            if r in ("Z", "M"):
                self.add(2, (x0 + x1) / 2, reg(x0 - 6, y0 - 6, x1 + 6, y1 + 6, 9, TENUE, 2.4))

    def reticula(self):
        """Cruces de réseau cada 100 mm en la mesa libre: la mesa es un instrumento que mide en mm reales."""
        for gx in range(100, 1300, 100):
            for gy in range(100, 690, 100):
                if self.M.choque((gx - 14, gy - 14, gx + 14, gy + 14), m_obj=6, m_ui=6) is None:
                    self.add(0, gx, f'<path d="M{gx - 6},{gy}h12M{gx},{gy - 6}v12" stroke="{LATENTE}" stroke-width="2"/>')

    def barrido(self):
        if self.a.sin_barrido: return ""
        dur = (MX + 100) / self.V
        return (f'<defs><linearGradient id="gb" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="{FOCO}" stop-opacity=".3"/><stop offset="1" stop-color="{FOCO}" stop-opacity="0"/></linearGradient></defs>'
                f'<g class="barre" style="animation-duration:{dur:.2f}s;animation-delay:{T0 - 10 / self.V:.2f}s"><rect x="0" y="-10" width="90" height="{MY}" fill="url(#gb)"/><rect x="-2" y="-10" width="4" height="{MY}" fill="{TINTA}"/></g>')

    # ---- armado -------------------------------------------------------------------------------------------------------
    def armar(self):
        self.rejilla_viales()
        # 1) lo grande y fijo; 2) los caminos, rodeándolo; 3) rótulos, donde quede lugar; 4) retícula, en lo que sobre
        self.panel_catalogo(); self.panel_protocolo()
        self.dibuja_organizadora(); self.dibuja_viales()
        self.vaso(); self.alerta()
        self.D = self.destinos(); self.camino_pipeta(); self.guia_buscar()
        self.dibuja_destinos(); self.huellas(); self.reticula()
        cuerpo = "".join("".join(self.capas[k]) for k in sorted(self.capas)) + self.barrido()
        return f'<svg viewBox="-10 -10 {MX} {MY}" xmlns="http://www.w3.org/2000/svg">{cuerpo}</svg>'

    def css(self, previa=False):
        Hm = self.Hm
        m3d = "matrix3d(%s)" % ",".join("%.9g" % v for v in [Hm[0, 0], Hm[1, 0], 0, Hm[2, 0], Hm[0, 1], Hm[1, 1], 0, Hm[2, 1], 0, 0, 1, 0, Hm[0, 2], Hm[1, 2], 0, Hm[2, 2]])
        tr = "none" if previa else m3d
        return f"""html,body{{margin:0;background:transparent;overflow:hidden}}#w{{position:absolute;left:0;top:0;width:1400px;height:720px;transform-origin:0 0;transform:{tr}}}
svg{{position:absolute;left:-10px;top:-10px;width:{MX}px;height:{MY}px;overflow:hidden}}
.s{{font-family:{SANS}}}.m{{font-family:{MONO};font-variant-numeric:tabular-nums}}.esp{{letter-spacing:.05em}}
.rv{{opacity:0;animation:rv {T_ENTRA}s {CURVA} var(--d,0s) forwards}}@keyframes rv{{to{{opacity:1}}}}
.cae{{transform-box:fill-box;transform-origin:center;animation:cae {T_ENTRA}s {CURVA_LAB} var(--d,0s) forwards}}@keyframes cae{{from{{opacity:0;transform:scale(1.5)}}to{{opacity:1;transform:scale(1)}}}}
.ola{{fill-opacity:0;animation:ola {T_ONDULA}s ease-out var(--d,0s) both}}@keyframes ola{{0%{{fill-opacity:0}}18%{{fill-opacity:.75}}100%{{fill-opacity:0}}}}
.dest{{opacity:0;animation:dest {T_ONDULA}s ease-out var(--d,0s) both}}@keyframes dest{{0%{{opacity:0}}15%{{opacity:.3}}100%{{opacity:0}}}}
.tz{{stroke-dasharray:1;stroke-dashoffset:1;animation:tz {T_FORMA}s cubic-bezier(.33,0,.67,1) var(--d,0s) forwards}}@keyframes tz{{to{{stroke-dashoffset:0}}}}
.fluye{{animation:rv {T_ENTRA}s linear var(--d,0s) forwards,fl 1.1s linear infinite}}@keyframes fl{{to{{stroke-dashoffset:-16}}}}
.late{{animation:late 1.5s ease-in-out infinite alternate}}@keyframes late{{to{{fill-opacity:.12}}}}
.onda{{transform-box:fill-box;transform-origin:center;animation:onda 1.9s ease-out infinite}}@keyframes onda{{from{{transform:scale(1);opacity:.9}}to{{transform:scale(2.3);opacity:0}}}}
.sube{{transform-box:fill-box;transform-origin:50% 0;transform:scaleY(0);animation:sube {T_FORMA}s {CURVA} var(--d,0s) forwards}}@keyframes sube{{to{{transform:scaleY(1)}}}}
.sube2{{transform:translateY(var(--y));animation:sube2 {T_FORMA}s {CURVA} var(--d,0s) forwards}}@keyframes sube2{{to{{transform:translateY(0)}}}}
.alarma{{animation:al .9s steps(2,jump-none) infinite}}@keyframes al{{50%{{opacity:.35}}}}
.barre{{transform:translateX({MX}px);animation:barre linear forwards}}@keyframes barre{{from{{transform:translateX({MX - 10}px)}}to{{transform:translateX(-110px)}}}}
[data-press]{{cursor:pointer}}
@media (prefers-reduced-motion:reduce){{*{{animation-duration:.01s!important;animation-delay:0s!important}}}}
"""

    def auditar(self):
        """Segunda pasada, independiente de la colocación: todo contra todo y contra las huellas."""
        it = [i for i in self.M.items if i["tipo"] in ("ui", "marca")]; mal = []
        for i, a in enumerate(it):
            r = a["r"]
            for k, om in self.M.obj.items():
                if k not in a["sobre"] and k is not None and om[self.M._s(r)].any(): mal.append(f"{a['n']} ✕ objeto «{k}»")
            if self.M.fuera[self.M._s(r)].any(): mal.append(f"{a['n']} ✕ fuera de la proyección")
            if self.M.res[self.M._s(r)].any(): mal.append(f"{a['n']} ✕ zona reservada")
            for b in it[i + 1:]:
                q = b["r"]
                if "ui" in (a["tipo"], b["tipo"]) and r[0] < q[2] and q[0] < r[2] and r[1] < q[3] and q[1] < r[3]: mal.append(f"{a['n']} ✕ {b['n']}")
        for c in [i for i in self.M.items if i["tipo"] == "camino"]:
            mal += [f"{c['n']} ✕ {x}" for x in c["cruces"]]
            # caminos contra paneles y textos
        cam = self.M.cam
        for a in it:
            if a["tipo"] == "ui" and a["n"] != "número del paso" and cam[self.M._s(a["r"])].any(): mal.append(f"{a['n']} ✕ camino")
        return mal


# ====================================================================================================================
PREVIA = """<!doctype html><meta charset="utf-8"><title>{titulo}</title>
<style>body{{margin:0;background:#000}}#mesa{{position:relative;width:1340px;height:710px;overflow:hidden;background:url(../skin/entrada-vivo.png) 0 0/1340px 710px}}
#sombra{{position:absolute;inset:0;background:rgba(0,0,0,.35)}}#luz{{position:absolute;left:10px;top:10px;mix-blend-mode:screen}}
{css}</style>
<div id="mesa"><div id="sombra"></div><div id="luz">{html}</div></div>
<script>/* ?t=segundos congela todas las animaciones en ese instante (para capturas) */
const t=new URLSearchParams(location.search).get('t');
if(t!==null){{const ms=parseFloat(t)*1000;const f=()=>{{document.getAnimations().forEach(a=>{{a.pause();a.currentTime=ms}});const s=document.querySelector('svg');s.pauseAnimations();s.setCurrentTime(ms/1000)}};f();requestAnimationFrame(f)}}
</script>"""

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CAPTURAS = [  # nombre, argumentos, instante (s)
    ("1-arranque-barrido", [], 1.05), ("2-arranque-fin", [], 6.0), ("3-buscar-arduino", ["--buscar", "arduino"], 6.0),
    ("4-deposito-paso-2", ["--paso", "2"], 6.0), ("5-alerta", ["--paso", "2", "--alerta"], 6.0),
    ("6-buscar-sensor", ["--buscar", "sensor"], 6.0), ("7-paso-3-pasaste", ["--paso", "3", "--nivel", "78"], 6.0),
    ("8-paso-4-listo", ["--paso", "4", "--nivel", "61"], 6.0),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--objetos", help="json con el formato de out/objetos.json; si falta, se usan las cajas de out/vlm-solar-lab.json")
    ap.add_argument("--sin-ajuste", action="store_true", help="usar las cajas del VLM tal cual, sin las huellas afinadas sobre la foto")
    ap.add_argument("--buscar", help="pieza a ubicar: " + ", ".join(p["clave"] for p in PIEZAS))
    ap.add_argument("--paso", type=int, help=f"paso actual del depósito (1..{len(PROTOCOLO)})")
    ap.add_argument("--nivel", type=float, help="nivel del vaso, 0..100 %% (en la mesa lo mide otra parte del sistema)")
    ap.add_argument("--alerta", action="store_true", help="objeto ajeno (el teléfono) en la mesa: protocolo en pausa")
    ap.add_argument("--sin-barrido", action="store_true", help="sin la animación de arranque (para cambios de estado)")
    ap.add_argument("--preview", action="store_true", help="escribe preview-lab/<nombre>.html y no toca out/")
    ap.add_argument("--nombre", default="previa", help="nombre de la página de vista previa")
    ap.add_argument("--capturas", action="store_true", help="genera todas las vistas previas y sus capturas con Chrome headless")
    a = ap.parse_args(argv)
    if a.capturas:
        base = [x for x in (argv or sys.argv[1:]) if x != "--capturas"]
        for nombre, extra, t in CAPTURAS:
            main(base + extra + ["--preview", "--nombre", nombre])
            png = AQUI / "preview-lab" / f"{nombre}.png"
            subprocess.run([CHROME, "--headless=new", "--hide-scrollbars", f"--screenshot={png}", "--window-size=1340,710", "--virtual-time-budget=6000",
                            f"file://{AQUI / 'preview-lab' / (nombre + '.html')}?t={t}"], capture_output=True)
            print("captura:", png)
        return
    E = Escena(a); svg = E.armar(); cuerpo = f'<div id="w">{svg}</div>'
    mal = E.auditar()
    if a.preview:
        d = AQUI / "preview-lab"; d.mkdir(exist_ok=True); f = d / f"{a.nombre}.html"
        f.write_text(PREVIA.format(titulo=a.nombre, css=E.css(previa=True), html=cuerpo), encoding="utf-8"); print("vista previa:", f)
    else:
        w = {"type": "effect", "id": "laboratorio", "shader": "html", "rect_mm": [0, 0, 1000, 563], "params": {"html": cuerpo, "css": E.css()}}
        f = AQUI / "out" / "widget-lab.json"; f.write_text(json.dumps(w, ensure_ascii=False), encoding="utf-8")
        print(f"widget: {f} ({f.stat().st_size / 1000:.1f} kB) · SIN proyectar")
    print(f"  modo {E.modo}" + (f" · {E.buscar['pieza']} en {E.buscar['en']}" if E.buscar else f" · paso {E.paso}") + (" · ALERTA" if a.alerta else ""))
    for n in E.M.notas: print("  ·", n)
    print("  solapamientos:", "0" if not mal else f"{len(mal)} → " + "; ".join(mal))


if __name__ == "__main__":
    main()

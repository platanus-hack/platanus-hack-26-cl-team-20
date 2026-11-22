#!/usr/bin/env python3
"""Escena «sistema solar» v2 (WUI): óleo nocturno sobre el montaje real, en mm de mesa, en UN widget html.

Lenguaje visual: el de Max (escenas/sistema-solar: tema.css «óleo nocturno», planetas.json, visual/__init__.py:
fondo de pinceladas, órbitas de tres trazos, terminador del lado opuesto al Sol, fichas, Lumo) y los tiempos y la
curva de Pablo (wui_motion.py: TIEMPOS_TINTA y cubic-bezier(.16,1,.3,1); wui_tokens.py: grosor mínimo del wordmark,
Niebla, pesos de etiqueta).

   solar.py                       -> escribe out/widget-solar.json (NO proyecta)
   solar.py --preview             -> escribe preview-solar/<estado>.html (foto + svg en modo aditivo), no proyecta
   solar.py --planeta saturno     -> estado «ficha» de ese planeta (sin barrido; --intro lo fuerza)
   solar.py --objetos out/x.json  -> objetos con formato de out/objetos.json (si no, cajas de out/vlm-solar-lab.json)
   solar.py --platos-duros        -> los nombres tampoco pueden ir sobre los platos (van afuera, con hilo)
   solar.py --sin-intro | --intro | --salida <ruta> | --semilla <n>

Capas (de abajo hacia arriba, ver CAPAS): lo negro no se proyecta, así que las «tapas» negras recortan gratis.
"""
import json, math, random, sys
from pathlib import Path
import numpy as np

AQUI = Path(__file__).resolve().parent
ARG = sys.argv[1:]


def arg(nombre, defecto=None):
    return ARG[ARG.index(nombre) + 1] if nombre in ARG and ARG.index(nombre) + 1 < len(ARG) else defecto


SEMILLA = int(arg('--semilla', 26))
rnd = random.Random(SEMILLA)
MESA_W, MESA_H = 1320, 690

# ------------------------------------------------------------------ tokens
# Max · tema.css (óleo nocturno) y visual/__init__.py (CSS_VISUAL)
TINTA, TENUE, FOCO, PLACA = "#fbf6e8", "#9fb0ff", "#ffc21a", "#10164e"
REMOLINO, REMOLINO_CLARO, AZUL_MEDIO, ESTRELLA = "#2f4bd8", "#7f9cff", "#4f6cf0", "#fff3b0"
SOL_C = ("#fff8d8", "#ffd23f", "#ff9a1f", "#e2531a")
LUMO, LUMO_BRILLO = "#ffd23f", "#fff6c9"
FUENTE = "'Arial Rounded MT Bold','Avenir Next Rounded','Avenir Next','Nunito',system-ui,sans-serif"
CURVA = "cubic-bezier(.22,1,.36,1)"          # --sd-curva de Max: llega y se asienta
RESORTE = "cubic-bezier(.34,1.56,.64,1)"     # --solar-resorte: Lumo
# Pablo · wui_motion.py / wui_tokens.py
T_ENTRA, T_ONDULA, T_FORMA, T_ASIENTA = 0.42, 0.88, 1.28, 1.72
TINTA_ASIENTA = "cubic-bezier(.16,1,.3,1)"
TRAZO_MIN = 2.6                               # ANCHO_LINEA_WORDMARK_MM: nada importante más fino que esto
RESPIRA_S = 1 / 0.35                          # envolvente «activo»: respiración de 0,35 Hz entre 0,75 y 1
# barrido: un frente de luz que sale del Sol y cruza la mesa
T0, VEL = 0.55, 430.0                         # s de espera tras encender el Sol, mm/s del frente

# Max · planetas.json (nombre, orden desde el Sol, textura, colores claro→oscuro, datos)
PLANETAS = {
    "mercurio": ("Mercurio", 1, "rocosa", ["#d9d4cc", "#a39d94", "#6e6862"], ["Es el más cercano al Sol", "Un año allá dura solo 88 días", "No tiene lunas"]),
    "venus": ("Venus", 2, "nubes", ["#fff1c9", "#f2c879", "#c98f3c"], ["Es el planeta más caliente", "Sus nubes no dejan ver el suelo", "Gira al revés que casi todos"]),
    "tierra": ("Tierra", 3, "tierra", ["#3fa7ff", "#1b5fbf", "#5fd06b"], ["Es nuestra casa", "Tres cuartas partes son agua", "Tiene una luna"]),
    "marte": ("Marte", 4, "rocosa", ["#ff8a5c", "#d2552b", "#8a2e14"], ["Le dicen el planeta rojo por su polvo", "Tiene el volcán más alto que se conoce", "Tiene dos lunas chiquitas"]),
    "jupiter": ("Júpiter", 5, "bandas", ["#f6e3c4", "#d9a36b", "#a8673a"], ["Es el más grande de todos", "Su mancha roja es una tormenta más grande que la Tierra", "Tiene decenas de lunas"]),
    "saturno": ("Saturno", 6, "anillos", ["#fbe9b7", "#e2c27d", "#b38b4a"], ["Sus anillos son de hielo y roca", "Es tan liviano que flotaría en una piscina gigante", "Un año allá dura 29 de los nuestros"]),
    "urano": ("Urano", 7, "hielo", ["#d4fbff", "#8fe3ef", "#4fb8c9"], ["Gira acostado, de lado", "Es un gigante de hielo", "Es muy, muy frío"]),
    "neptuno": ("Neptuno", 8, "hielo", ["#9fc3ff", "#3d6fe0", "#1d3a8f"], ["Es el más lejano del Sol", "Tiene los vientos más fuertes", "Un año allá dura 165 de los nuestros"]),
    "pluton": ("Plutón", 9, "rocosa", ["#f1e2d0", "#c7a88a", "#8a6d55"], ["Es un planeta enano", "Tiene un corazón dibujado de hielo", "Vive más allá de Neptuno"]),
    "luna": ("Luna", 3.5, "rocosa", ["#f2f2f2", "#bdbdbd", "#7d7d7d"], ["Da una vuelta a la Tierra cada 27 días", "Siempre nos muestra la misma cara", "Doce personas han caminado en ella"]),
}
# reparto por tamaño: la bola más grande es Júpiter, la segunda Saturno… (escena.md de Max); si sobran, Plutón y lunas
POR_TAMANO = ["jupiter", "saturno", "urano", "neptuno", "tierra", "venus", "marte", "mercurio", "pluton"]
ORDINAL = {1: "1.er", 2: "2.º", 3: "3.er", 4: "4.º", 5: "5.º", 6: "6.º", 7: "7.º", 8: "8.º"}


def lugar(pid):
    o = PLANETAS[pid][1]
    return "satélite de la Tierra" if pid == "luna" else "planeta enano, más allá de Neptuno" if pid == "pluton" else f"{ORDINAL[o]} planeta desde el Sol"


# ------------------------------------------------------------------ entrada
def cargar_objetos():
    ruta = arg('--objetos')
    if ruta:
        out = []
        for o in json.load(open(ruta)):
            p = np.array(o['poligono_mm'], float)
            out.append({"etiqueta": o['etiqueta'], "caja": (p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max()), "poligono": p.tolist()})
        return out
    out = []
    for o in json.load(open(AQUI / 'out/vlm-solar-lab.json')):
        x0, y0, x1, y1 = o['caja_mm']
        out.append({"etiqueta": o['etiqueta'], "caja": (x0, y0, x1, y1), "poligono": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]})
    return out


OBJ = cargar_objetos()
for o in OBJ:
    x0, y0, x1, y1 = o['caja']; o['c'] = ((x0 + x1) / 2, (y0 + y1) / 2); o['wh'] = (x1 - x0, y1 - y0)
    e = o['etiqueta'].lower()
    o['tipo'] = ('nucleo' if e.startswith('bola') and 'sol' in e.split() else 'bola' if e.startswith('bola') else 'sol' if ('engranaje' in e or e in ('sol', 'el sol'))
                 else 'plato' if e.startswith('plato') else 'lamina' if e.startswith(('lámina', 'lamina')) else 'otro')
BOLAS = [o for o in OBJ if o['tipo'] == 'bola']
PLATOS = [o for o in OBJ if o['tipo'] == 'plato']
LAMINA = next((o for o in OBJ if o['tipo'] == 'lamina' and min(o['wh']) > 60), None)
OTROS = [o for o in OBJ if o['tipo'] == 'otro' or (o['tipo'] == 'lamina' and o is not LAMINA)]
_sol = next((o for o in OBJ if o['tipo'] == 'sol'), None)
if _sol is None: sys.exit("solar.py: no hay engranaje (Sol) entre los objetos")
SOL = np.array(_sol['c']); R_SOL = min(_sol['wh']) / 2
SOL_POLI = _sol['poligono'] if len(_sol['poligono']) > 8 else None      # silueta real (máscara) si la hay
for o in BOLAS: o['r'] = min(o['wh']) / 2                               # círculo inscrito en su caja
for o in PLATOS: o['r'] = sum(o['wh']) / 4
BOLAS.sort(key=lambda o: (-o['r'], o['etiqueta']))
for i, o in enumerate(BOLAS): o['pid'] = POR_TAMANO[i] if i < len(POR_TAMANO) else 'luna'
BOLAS = [o for i, o in enumerate(BOLAS) if o['pid'] != 'luna' or i < len(POR_TAMANO) + 1]
DE = {o['pid']: o for o in BOLAS}
ANILLOS = [(o['c'][0] - 1.74 * o['r'], o['c'][1] - .72 * o['r'], o['c'][0] + 1.74 * o['r'], o['c'][1] + .72 * o['r']) for o in BOLAS if PLANETAS[o['pid']][2] == 'anillos']

SEL = arg('--planeta')
if SEL and SEL not in DE: sys.exit(f"solar.py: «{SEL}» no está en la mesa. Hay: {', '.join(DE)}")
INTRO = ('--intro' in ARG) or (not SEL and '--sin-intro' not in ARG)

# proyector: homografía mm -> canvas, y zonas reservadas de la consola (pestañas y esquina de voz, en canvas)
met = json.load(open(AQUI / 'fp-metric.json')); Hm = np.array(met['mm_to_canvas']); Hm = Hm / Hm[2, 2]
M3D = "matrix3d(%s)" % ",".join("%.9g" % v for v in [Hm[0, 0], Hm[1, 0], 0, Hm[2, 0], Hm[0, 1], Hm[1, 1], 0, Hm[2, 1], 0, 0, 1, 0, Hm[0, 2], Hm[1, 2], 0, Hm[2, 2]])


def canvas(x, y):
    w = Hm[2, 0] * x + Hm[2, 1] * y + 1
    return (Hm[0, 0] * x + Hm[0, 1] * y + Hm[0, 2]) / w, (Hm[1, 0] * x + Hm[1, 1] * y + Hm[1, 2]) / w


def proyectable(x, y, m=0.0):
    """Dentro de la mesa, dentro del canvas del proyector y fuera de lo reservado por la consola."""
    if not (m <= x <= MESA_W - m and m <= y <= MESA_H - m): return False
    u, v = canvas(x, y); k = m * 0.76
    if not (k <= u <= 1000 - k and k <= v <= 563 - k): return False
    if u < 150 + k: return False                          # barra de pestañas
    if u > 870 - k and v < 130 + k: return False          # esquina de voz («Hablar»)
    return True


# ------------------------------------------------------------------ colisiones
# huellas duras: ('c', cx, cy, r) o ('r', x0, y0, x1, y1). Los platos van aparte: son superficie (ver --platos-duros).
DUROS = [('c', *o['c'], o['r']) for o in BOLAS] + [('c', *SOL, R_SOL)] + [('r', *o['caja']) for o in OTROS] + ([('r', *LAMINA['caja'])] if LAMINA else [])
DUROS += [('r', *a) for a in ANILLOS]
DISCOS = [(*o['c'], o['r']) for o in PLATOS]
PLATOS_DUROS = '--platos-duros' in ARG
TEXTOS = []      # cajas ya ocupadas por textos, paneles, Lumo y estrellas grandes: ('r', x0, y0, x1, y1)
AVISOS = []


def toca(caja, f, m=0.0):
    x0, y0, x1, y1 = caja
    if f[0] == 'r': return x0 < f[3] + m and x1 > f[1] - m and y0 < f[4] + m and y1 > f[2] - m
    dx = max(x0 - f[1], 0, f[1] - x1); dy = max(y0 - f[2], 0, f[2] - y1)
    return dx * dx + dy * dy < (f[3] + m) ** 2


def dentro_de_disco(caja, d, m):
    return all(math.hypot(x - d[0], y - d[1]) <= d[2] - m for x in (caja[0], caja[2]) for y in (caja[1], caja[3]))


def libre(caja, m=4.0, sobre_plato=True, textos=True, salvo=None):
    """¿La caja no monta nada? Objetos duros con margen m, otros textos, borde de mesa, consola y, para los platos:
    o entera sobre el plato (lejos del borde) o entera fuera."""
    x0, y0, x1, y1 = caja
    if not all(proyectable(x, y, 6) for x in (x0, (x0 + x1) / 2, x1) for y in (y0, (y0 + y1) / 2, y1)): return False
    for f in DUROS:
        if f is not salvo and toca(caja, f, m): return False
    if textos and any(toca(caja, t, 3.0) for t in TEXTOS): return False
    for d in DISCOS:
        if toca(caja, ('c', *d), m):
            if PLATOS_DUROS or not sobre_plato or not dentro_de_disco(caja, d, 9): return False
    return True


def punto_libre(x, y, m):
    return libre((x, y, x, y), m, sobre_plato=False)


ANCHOS = {**{c: .31 for c in "iltjI.,·:;'!¡ "}, **{c: .38 for c in "frº"}, **{c: .9 for c in "mwMW"}}


def ancho(s, size):
    """Ancho aproximado (con holgura) en Arial Rounded Bold."""
    return sum(ANCHOS.get(c, .7 if c.isupper() or c.isdigit() else .58) for c in s) * size * 1.05


def envolver(s, size, w):
    lin, cur = [], ""
    for p in s.split():
        if cur and ancho(cur + " " + p, size) > w: lin.append(cur); cur = p
        else: cur = (cur + " " + p).strip()
    return lin + [cur]


def esc(s): return s.replace("&", "&amp;").replace("<", "&lt;")


def ret(p):
    """Retardo del barrido: cuándo llega el frente (que sale del Sol) al punto p."""
    return T0 + math.hypot(p[0] - SOL[0], p[1] - SOL[1]) / VEL


def dl(t): return f' style="animation-delay:{t:.2f}s"'


def mezcla(a, b, t):
    A = [int(a[i:i + 2], 16) for i in (1, 3, 5)]; B = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(A, B))


def vivo(c, k=1.5):
    """Sube la saturación: el proyector tenue con luz de sala lava los colores (Max usa saturate(1.8))."""
    r, g, b = [int(c[i:i + 2], 16) for i in (1, 3, 5)]; m = (r + g + b) / 3
    return "#%02x%02x%02x" % tuple(int(min(255, max(0, m + (v - m) * k))) for v in (r, g, b))


def f1(v): return ("%.1f" % v).rstrip('0').rstrip('.')


# ------------------------------------------------------------------ capas
CAPAS = ["fondo", "estrellas", "orbitas", "tapas", "sol", "tapas_bolas", "planetas", "seleccion", "hilos", "nombres", "panel", "lumo", "barrido"]
K = {k: [] for k in CAPAS}
DEFS = []


def texto(x, y, s, size, fill=TINTA, anchor="middle", cls="", borde=4.0):
    """Texto girado 180° (se lee desde el lado opuesto a la cámara) con borde negro que recorta lo que quede debajo."""
    return (f'<text x="{f1(x)}" y="{f1(y)}" font-size="{f1(size)}" fill="{fill}" text-anchor="{anchor}" class="{cls}" stroke="#000" stroke-width="{borde}" '
            f'paint-order="stroke" stroke-linejoin="round" transform="rotate(180 {f1(x)} {f1(y)})">{esc(s)}</text>')


def caja_texto(cx, cy, s, size):
    w = ancho(s, size); return (cx - w / 2, cy - size * .52, cx + w / 2, cy + size * .52)


# ---------- 1. panel (primero: es lo más grande y manda sobre lo demás)
def buscar_sitio(w, h, ancla, paso=10):
    mejor = None
    for y in np.arange(8, MESA_H - h - 8 + 1, paso):
        for x in np.arange(8, MESA_W - w - 8 + 1, paso):
            c = (x, y, x + w, y + h)
            if not libre(c, 12, sobre_plato=False): continue
            d = math.hypot(x + w / 2 - ancla[0], y + h / 2 - ancla[1])
            if mejor is None or d < mejor[0]: mejor = (d, c)
    return mejor[1] if mejor else None


def lumo_svg(escala=1.0):
    """Lumo (Max, escena.py): lucecita amarilla con ojos. Dibujado al derecho en coordenadas locales."""
    return (f'<g transform="scale({escala})"><circle r="27" fill="url(#gLumoHalo)" class="late"/>'
            f'<g class="flota"><path d="M-21,-9 q-9,-3 -15,3 M-22,2 q-10,1 -14,9 M-18,12 q-7,4 -8,12" class="cola"/>'
            f'<circle r="15.5" fill="url(#gLumo)"/><ellipse cx="-5.5" cy="-1" rx="2.6" ry="3.8" fill="#14183f" class="ojo"/><ellipse cx="5.5" cy="-1" rx="2.6" ry="3.8" fill="#14183f" class="ojo"/>'
            f'<path d="M-4,6.5 q4,3.4 8,0" fill="none" stroke="#14183f" stroke-width="1.8" stroke-linecap="round"/><ellipse cx="-6" cy="-8.5" rx="3.6" ry="2.2" fill="#fff" opacity=".9"/></g></g>')


def marco_pincel(w, h, col=REMOLINO_CLARO):
    """Placa ultramar de Max con marco pintado: tres trazos de pincel, como las órbitas."""
    return (f'<rect x="-5" y="-5" width="{w + 10}" height="{h + 10}" rx="24" fill="#000"/><rect width="{w}" height="{h}" rx="20" fill="{PLACA}" opacity=".9"/>'
            f'<rect x="2" y="2" width="{w - 4}" height="{h - 4}" rx="18" fill="none" stroke="{col}" stroke-width="3.6" stroke-linecap="round" stroke-dasharray="46 7 22 9 70 11"/>'
            f'<rect x="6.5" y="6.5" width="{w - 13}" height="{h - 13}" rx="14" fill="none" stroke="{TINTA}" stroke-width="1.6" stroke-linecap="round" stroke-dasharray="12 30 26 44" opacity=".55"/>')


ANCLA = np.array(LAMINA['c']) if LAMINA else np.array([np.mean([o['c'][0] for o in BOLAS]) + 60, 120.0])
PANEL = None; LUMO_CASA = None
if SEL:
    nombre, orden, textura, colores, datos = PLANETAS[SEL]
    # se prueba de más a menos: achicar letra o quitar datos antes que montar algo
    for w, fs, nd in ((300, 13, 3), (300, 12, 3), (300, 13, 2), (270, 12.5, 2), (240, 12, 2), (220, 11.5, 2), (210, 11.5, 1)):
        x_txt = 22; lineas = [envolver(d, fs, w - x_txt - 30) for d in datos[:nd]]
        h = 80 + sum(len(l) for l in lineas) * fs * 1.24 + (nd - 1) * 5 + 12
        c = buscar_sitio(w, h, ANCLA)
        if c: PANEL = c; break
    if PANEL:
        x0, y0, x1, y1 = PANEL; w, h = x1 - x0, y1 - y0; TEXTOS.append(('r', *PANEL)); col = vivo(colores[0], 1.2); xt = 98 if textura == 'anillos' else 80
        g = [marco_pincel(w, h), f'<g transform="translate(44,40)">__MEDALLA__</g>',
             f'<text x="{xt}" y="45" font-size="31" fill="{TINTA}">{esc(nombre)}</text>',
             f'<text x="{xt + 1}" y="63" font-size="{f1(max(11.5, fs - 1))}" fill="{FOCO}">{esc(lugar(SEL))}</text>',
             f'<path d="M20,75 q{(w - 40) / 4:.0f},-5 {(w - 40) / 2:.0f},0 t{(w - 40) / 2:.0f},0" fill="none" stroke="{col}" stroke-width="3" stroke-linecap="round" stroke-dasharray="60 8 30 10"/>']
        y = 80 + fs
        for k, l in enumerate(lineas):
            g.append(f'<circle cx="{x_txt + 4}" cy="{y - fs * .33:.1f}" r="3.4" fill="{FOCO}"/>')
            for s in l: g.append(f'<text x="{x_txt + 15}" y="{y:.1f}" font-size="{fs}" fill="{TINTA}">{esc(s)}</text>'); y += fs * 1.24
            y += 5
        K['panel'].append(f'<g transform="rotate(180 {(x0 + x1) / 2:.1f} {(y0 + y1) / 2:.1f}) translate({x0:.1f},{y0:.1f})"><g class="abre"{dl(ret(((x0 + x1) / 2, (y0 + y1) / 2)) if INTRO else 0.15)}>{"".join(g)}</g></g>')
        LUMO_CASA = ((x0 + x1) / 2, y0 - 2)
    else: AVISOS.append("ficha omitida: no hay espacio libre suficiente")
else:
    for w, h, fs in ((300, 98, 25), (270, 90, 22), (240, 82, 19.5), (210, 74, 17)):
        c = buscar_sitio(w, h, ANCLA)
        if c: PANEL = c; break
    if PANEL:
        x0, y0, x1, y1 = PANEL; TEXTOS.append(('r', *PANEL)); xt = 84 * h / 98 + (w - 84 * h / 98) / 2 - 6
        g = [marco_pincel(w, h), f'<g transform="translate({h * .5:.1f},{h * .5:.1f})">{lumo_svg(h / 98)}</g>',
             f'<text x="{xt:.1f}" y="{h * .44:.1f}" font-size="{fs}" fill="{TINTA}" text-anchor="middle">Toca un planeta</text>',
             f'<text x="{xt:.1f}" y="{h * .44 + fs * 1.12:.1f}" font-size="{fs}" fill="{FOCO}" text-anchor="middle">en la lámina</text>',
             f'<g transform="translate({xt:.1f},{h * .44 + fs * 1.12 + 9:.1f})"><path d="M{-fs * 3.1:.0f},0 q{fs * 1.55:.0f},7 {fs * 3.1:.0f},0 t{fs * 3.1:.0f},0" fill="none" stroke="{FOCO}" stroke-width="3" stroke-linecap="round" class="subraya"/></g>']
        K['panel'].append(f'<g transform="rotate(180 {(x0 + x1) / 2:.1f} {(y0 + y1) / 2:.1f}) translate({x0:.1f},{y0:.1f})"><g class="abre"{dl(ret(((x0 + x1) / 2, (y0 + y1) / 2)))}>{"".join(g)}</g></g>')
    else: AVISOS.append("aviso «toca un planeta» omitido: no hay espacio libre")
if LAMINA:   # la lámina es el control: marco pintado que respira (no se proyecta nada encima de ella)
    x0, y0, x1, y1 = LAMINA['caja']
    K['seleccion'].append(f'<g class="rv"{dl(ret(LAMINA["c"]))}><rect x="{x0 - 9:.0f}" y="{y0 - 9:.0f}" width="{x1 - x0 + 18:.0f}" height="{y1 - y0 + 18:.0f}" rx="12" fill="none" stroke="{FOCO}" stroke-width="3.4" '
                          f'stroke-linecap="round" stroke-dasharray="40 9 18 11" class="{"respira" if not SEL else ""}"/></g>')

# ---------- 2. nombres: junto a su bola; si no cabe, más chico; si no, afuera con hilo; si no, se omite
ETIQ = {}


def poner_nombre(clave, c, r, s, sel=False, tamanos=(17, 15.5, 14, 12.5)):
    f_propia = next((f for f in DUROS if f[0] == 'c' and abs(f[1] - c[0]) < .01 and abs(f[2] - c[1]) < .01), None)
    for size in ((21, 19) + tamanos if sel else tamanos):
        w, h = ancho(s, size), size * 1.04; cands = []
        # hacia la persona (y menor) primero; después los costados y al final el lado de la cámara
        for ang, pref in ((-90, 0), (-55, 4), (-125, 4), (0, 6), (180, 6), (-25, 7), (-155, 7), (90, 9), (55, 11), (125, 11)):
            ux, uy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
            for hol in (5, 9):
                k = r + hol + abs(ux) * w / 2 + abs(uy) * h / 2
                cx, cy = c[0] + ux * k, c[1] + uy * k
                cands.append((pref + hol * .2, cx, cy))
        for _, cx, cy in sorted(cands):
            cj = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
            if libre(cj, 3.5):
                TEXTOS.append(('r', *cj)); ETIQ[clave] = (cx, cy, size, None); return True
    # afuera, con hilo: una fila hacia la persona, por fuera de los platos
    size = 14; w, h = ancho(s, size), size * 1.04
    for dy in range(20, 160, 6):
        for dx in (0, -20, 20, -40, 40, -60, 60, -80, 80):
            cx, cy = c[0] + dx, c[1] - r - dy; cj = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
            if libre(cj, 5, sobre_plato=False):
                TEXTOS.append(('r', *cj)); ETIQ[clave] = (cx, cy, size, (cx, cy + h / 2 + 2)); return True
    AVISOS.append(f"nombre «{s}» omitido: no cabe sin montar algo"); return False


if SEL: poner_nombre(SEL, DE[SEL]['c'], DE[SEL]['r'], PLANETAS[SEL][0], sel=True)
poner_nombre('sol', tuple(SOL), R_SOL, "Sol", tamanos=(21, 19, 17, 15))
for o in BOLAS:
    if o['pid'] != SEL: poner_nombre(o['pid'], o['c'], o['r'], PLANETAS[o['pid']][0])
for clave, (cx, cy, size, hilo) in ETIQ.items():
    o = DE.get(clave); c = o['c'] if o else tuple(SOL); r = o['r'] if o else R_SOL
    col = FOCO if clave in (SEL, 'sol') else TINTA
    t = (ret(c) + .45) if INTRO else 0
    if hilo:
        ux, uy = hilo[0] - c[0], hilo[1] - c[1]; n = math.hypot(ux, uy); a = (c[0] + ux / n * (r + 4), c[1] + uy / n * (r + 4))
        K['hilos'].append(f'<g class="rv"{dl(t)}><path d="M{a[0]:.1f},{a[1]:.1f} L{hilo[0]:.1f},{hilo[1]:.1f}" stroke="#000" stroke-width="7"/><path d="M{a[0]:.1f},{a[1]:.1f} L{hilo[0]:.1f},{hilo[1]:.1f}" stroke="{TENUE}" stroke-width="{TRAZO_MIN}" stroke-linecap="round" stroke-dasharray="7 5"/></g>')
    K['nombres'].append(f'<g class="rv"{dl(t)}>{texto(cx, cy - size * .34, PLANETAS[clave][0] if o else "Sol", size, col)}</g>')

# ---------- 3. Lumo (en el estado ficha vuela desde la ficha hasta su planeta y se posa)
if SEL:
    o = DE[SEL]; c, r = o['c'], o['r']; pos = None
    for ang in (-35, -145, 20, 160, -90, 60, 120, 90):
        for hol in (27, 34, 42):
            ux, uy = math.cos(math.radians(ang)), math.sin(math.radians(ang)); p = (c[0] + ux * (r + hol), c[1] + uy * (r + hol)); cj = (p[0] - 17, p[1] - 17, p[0] + 17, p[1] + 17)
            if libre(cj, 2): pos = p; break
        if pos: break
    if pos:
        TEXTOS.append(('r', pos[0] - 17, pos[1] - 17, pos[0] + 17, pos[1] + 17))
        a = LUMO_CASA or (pos[0], pos[1] - 120); b = pos; ctrl = ((a[0] + b[0]) / 2 + (b[1] - a[1]) * .22, (a[1] + b[1]) / 2 - (b[0] - a[0]) * .22)
        kf = []
        for i in range(11):   # vuelo en curva (Max: widget_lumo_vuelo) con la curva suave ya aplicada
            u = i / 10; e = 1 - (1 - u) ** 3
            x = (1 - e) ** 2 * a[0] + 2 * (1 - e) * e * ctrl[0] + e * e * b[0]; y = (1 - e) ** 2 * a[1] + 2 * (1 - e) * e * ctrl[1] + e * e * b[1]
            kf.append(f"{i * 10}%{{transform:translate({x - b[0]:.1f}px,{y - b[1]:.1f}px)}}")
        VUELO = "@keyframes vuela{%s}" % "".join(kf)
        K['lumo'].append(f'<g transform="translate({b[0]:.1f},{b[1]:.1f})"><g class="vuela"><g class="asienta"><g transform="rotate(180)">{lumo_svg(.86)}</g></g></g></g>')
    else: AVISOS.append("Lumo omitido: no hay hueco junto al planeta"); VUELO = ""
else: VUELO = ""

# ---------- 4. estrellas grandes (remolinos de pincel, como las de Max) y chicas
GRANDES = []
for _ in range(4000):
    if len(GRANDES) >= 7: break
    x, y, r = rnd.uniform(30, MESA_W - 30), rnd.uniform(30, MESA_H - 30), rnd.uniform(9, 14)
    if math.hypot(x - SOL[0], y - SOL[1]) < 260 or any(math.hypot(x - g[0], y - g[1]) < 190 for g in GRANDES): continue
    cj = (x - r - 9, y - r - 9, x + r + 9, y + r + 9)
    if libre(cj, 22, sobre_plato=False): GRANDES.append((x, y, r)); TEXTOS.append(('r', *cj))
for i, (x, y, r) in enumerate(GRANDES):
    K['estrellas'].append(f'<g transform="translate({x:.0f},{y:.0f})"><g class="rv"{dl(ret((x, y)))}><g class="late" style="animation-delay:{-i * 1.3:.1f}s">'
                          f'<circle r="{r * 1.9:.1f}" fill="none" stroke="{REMOLINO_CLARO}" stroke-width="3" stroke-dasharray="{r * .9:.0f} {r * .7:.0f} {r * .4:.0f} {r:.0f}" stroke-linecap="round" opacity=".7" class="gira{"" if i % 2 else " rev"}"/>'
                          f'<circle r="{r * 1.3:.1f}" fill="none" stroke="{FOCO}" stroke-width="3.2" stroke-dasharray="{r * .7:.0f} {r * .5:.0f}" stroke-linecap="round" opacity=".85" class="gira{" rev" if i % 2 else ""}"/>'
                          f'<circle r="{r * .62:.1f}" fill="{FOCO}"/><circle r="{r * .34:.1f}" fill="#fffbe6"/></g></g></g>')
CHICAS = []
for _ in range(9000):
    if len(CHICAS) >= 95: break
    x, y = rnd.uniform(8, MESA_W - 8), rnd.uniform(8, MESA_H - 8)
    if punto_libre(x, y, 11) and all(math.hypot(x - a, y - b) > 30 for a, b, _ in CHICAS): CHICAS.append((x, y, rnd.choice((1.5, 1.9, 2.3, 2.9))))
for q in range(6):   # seis grupos de titileo desfasados; cada estrella se revela con el barrido
    sub = [s for i, s in enumerate(CHICAS) if i % 6 == q]
    K['estrellas'].append(f'<g class="titila" style="animation-delay:{-q * .9:.1f}s;animation-duration:{4.6 + q * .5:.1f}s">' + "".join(
        f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r}" class="rv"{dl(ret((x, y)))}/>' for x, y, r in sub) + '</g>')

# ---------- 5. fondo: pinceladas ultramar que siguen el giro alrededor del Sol y se arremolinan en las estrellas
def campo(x, y):
    dx, dy = x - SOL[0], y - SOL[1]; n = math.hypot(dx, dy) or 1; vx, vy = -dy / n, dx / n
    for i, (gx, gy, gr) in enumerate(GRANDES):
        ex, ey = x - gx, y - gy; d = math.hypot(ex, ey) or 1; w = math.exp(-(d / 95) ** 2); s = 1 if i % 2 else -1
        vx, vy = vx * (1 - w) + w * (-s * ey / d - .25 * ex / d), vy * (1 - w) + w * (s * ex / d - .25 * ey / d)
    a = .45 * math.sin(x / 83 + 1.3) * math.cos(y / 61 - .4); ca, sa = math.cos(a), math.sin(a)
    vx, vy = vx * ca - vy * sa, vx * sa + vy * ca; n = math.hypot(vx, vy) or 1
    return vx / n, vy / n


ESTILOS = [(REMOLINO, 8.5, .62), (REMOLINO, 6, .7), (AZUL_MEDIO, 5, .6), (REMOLINO_CLARO, 3.4, .55), (TINTA, 2.6, .3), ("#ff9a1f", 5, .5), (FOCO, 3.4, .5)]
grupos = {}
PASO = 21
for gy in np.arange(PASO / 2, MESA_H, PASO):
    for gx in np.arange(PASO / 2, MESA_W, PASO):
        x, y = gx + rnd.uniform(-8, 8), gy + rnd.uniform(-8, 8); d = math.hypot(x - SOL[0], y - SOL[1])
        calido = d < 250 and rnd.random() < (1 - d / 250) * .75
        e = rnd.choice((5, 5, 6)) if calido else rnd.choices(range(5), (3, 4, 4, 4, 2))[0]
        L = rnd.uniform(20, 40) * (1.25 if e == 0 else 1); m = ESTILOS[e][1] / 2 + 4
        pts = [(x, y)]
        for _ in range(2):
            vx, vy = campo(*pts[-1]); mx, my = pts[-1][0] + vx * L / 4, pts[-1][1] + vy * L / 4; vx, vy = campo(mx, my)
            pts.append((pts[-1][0] + vx * L / 2, pts[-1][1] + vy * L / 2))
        if not all(punto_libre(px, py, m) for px, py in pts): continue
        if any(toca((min(p[0] for p in pts) - m, min(p[1] for p in pts) - m, max(p[0] for p in pts) + m, max(p[1] for p in pts) + m), t, 0) for t in TEXTOS): continue
        (ax, ay), (bx, by), (cx, cy) = pts; qx, qy = 2 * bx - (ax + cx) / 2, 2 * by - (ay + cy) / 2
        cubo = round(ret(((ax + cx) / 2, (ay + cy) / 2)) / .14)
        grupos.setdefault(cubo, {}).setdefault(e, []).append(f"M{ax:.0f},{ay:.0f}Q{qx:.0f},{qy:.0f} {cx:.0f},{cy:.0f}")
N_PINCEL = sum(len(v) for g in grupos.values() for v in g.values())
for cubo in sorted(grupos):
    K['fondo'].append(f'<g class="rv"{dl(cubo * .14)}>' + "".join(f'<path class="p{e}" d="{"".join(ds)}"/>' for e, ds in sorted(grupos[cubo].items())) + '</g>')

# ---------- 6. órbitas pintadas alrededor del Sol, por el centro de cada bola (tres trazos de pincel, como las de Max)
for i, o in enumerate(sorted(BOLAS, key=lambda o: math.hypot(o['c'][0] - SOL[0], o['c'][1] - SOL[1]))):
    rad = math.hypot(o['c'][0] - SOL[0], o['c'][1] - SOL[1]); ang = math.degrees(math.atan2(o['c'][1] - SOL[1], o['c'][0] - SOL[0]))
    sel = o['pid'] == SEL; col = FOCO if sel else vivo(mezcla(PLANETAS[o['pid']][3][0], TENUE, .4), 1.25)
    K['orbitas'].append(
        f'<g class="o{" osel" if sel else ""}"><circle class="t1" r="{rad:.1f}" stroke="{col}" style="animation-duration:{50 + i * 9}s"/>'
        f'<circle class="t2" r="{rad + 4.2:.1f}" stroke="{col}"/><circle class="t3" r="{rad - 3.8:.1f}" stroke="{TINTA}"/>'
        + (f'<circle class="cub" r="{rad:.1f}" pathLength="100" transform="rotate({ang:.1f})"{dl(ret(o["c"]) + .15)}/>' if INTRO else '') + '</g>')
K['orbitas'] = [f'<g transform="translate({SOL[0]:.1f},{SOL[1]:.1f})">' + "".join(K['orbitas']) + '</g>']

# ---------- 7. tapas: negro (sin luz) sobre lo que no es de la escena, para que órbitas y fondo no lo pisen
for o in OTROS + ([LAMINA] if LAMINA else []):
    x0, y0, x1, y1 = o['caja']; K['tapas'].append(f'<rect x="{x0 - 7:.0f}" y="{y0 - 7:.0f}" width="{x1 - x0 + 14:.0f}" height="{y1 - y0 + 14:.0f}" rx="9"/>')

# ---------- 8. el Sol sobre el engranaje: plasma de pinceladas que gira, rayos y resplandor
R = R_SOL; g = []
for k, rho in enumerate((.18, .34, .5, .66, .8, .93)):
    n = 3 + k
    for j in range(n):
        a0 = rnd.uniform(0, 360); la = rnd.uniform(.45, .8) * 360 / n; col = rnd.choice((SOL_C[min(3, k // 2)], SOL_C[min(3, k // 2 + 1)], SOL_C[max(0, k // 2 - 1)]))
        a0 = j * 360 / n + rnd.uniform(-8, 8); p0 = (rho * R * math.cos(math.radians(a0)), rho * R * math.sin(math.radians(a0))); p1 = (rho * R * math.cos(math.radians(a0 + la)), rho * R * math.sin(math.radians(a0 + la)))
        g.append(f'<path d="M{p0[0]:.1f},{p0[1]:.1f}A{rho * R:.1f},{rho * R:.1f} 0 0 1 {p1[0]:.1f},{p1[1]:.1f}" stroke="{col}" stroke-width="{R * rnd.uniform(.1, .16):.1f}"/>')
rayos = []
for j in range(20):
    a = math.radians(j * 18 + rnd.uniform(-4, 4)); r0, r1 = R * 1.08, R * rnd.uniform(1.22, 1.42); t = a + rnd.uniform(-.12, .12)
    rayos.append(f'<path d="M{r0 * math.cos(a):.1f},{r0 * math.sin(a):.1f}L{r1 * math.cos(t):.1f},{r1 * math.sin(t):.1f}" stroke="{rnd.choice((FOCO, FOCO, SOL_C[2], SOL_C[0]))}" stroke-width="{rnd.choice((3, 4, 5))}"/>')
if SOL_POLI: DEFS.append('<clipPath id="kSol"><polygon points="%s"/></clipPath>' % " ".join(f"{x - SOL[0]:.1f},{y - SOL[1]:.1f}" for x, y in SOL_POLI))
else: DEFS.append(f'<clipPath id="kSol"><circle r="{R:.1f}"/></clipPath>')
K['sol'].append(
    f'<g transform="translate({SOL[0]:.1f},{SOL[1]:.1f})"><g class="solin">'
    f'<circle r="{R * 1.75:.1f}" fill="url(#gResplandor)" class="late"/><g class="rayos gira rev lento">{"".join(rayos)}</g>'
    f'<g clip-path="url(#kSol)"><circle r="{R * 1.1:.1f}" fill="url(#gSol)"/><g class="plasma gira">{"".join(g)}</g><circle r="{R * .42:.1f}" fill="url(#gNucleo)" class="late"/></g>'
    f'<circle r="{R:.1f}" fill="none" stroke="{FOCO}" stroke-width="3.4" stroke-dasharray="30 6 14 8" stroke-linecap="round" class="respira"/></g></g>')


# ---------- 9. planetas: textura de pinceladas que gira, sombra del lado opuesto al Sol, brillo hacia el Sol
def textura(pid, r, P):
    """Pinceladas de la textura en un período [-r, -r+P] (se dibuja dos veces y se desliza: la esfera «gira»)."""
    _, _, tipo, col, _ = PLANETAS[pid]; c0, c1, c2 = [vivo(c) for c in col]; x0 = -r; s = []
    def onda(y, a, color, w, op=1.0):
        q = P / 4
        s.append(f'<path d="M{x0 - 2:.1f},{y:.1f}q{q / 2:.1f},{a:.1f} {q:.1f},0t{q:.1f},0t{q:.1f},0t{q + 4:.1f},0" stroke="{color}" stroke-width="{w:.1f}"{"" if op == 1 else f" opacity=\"{op}\""}/>')
    def mancha(color, w, op=1.0, y=None, largo=None):
        x = x0 + rnd.uniform(0, P); y = rnd.uniform(-.8, .8) * r if y is None else y; L = (largo or rnd.uniform(.2, .5)) * r; a = rnd.uniform(-.6, .6)
        s.append(f'<path d="M{x:.1f},{y:.1f}q{L / 2 * math.cos(a):.1f},{L / 2 * math.sin(a) - L * .2:.1f} {L * math.cos(a):.1f},{L * math.sin(a):.1f}" stroke="{color}" stroke-width="{w:.1f}"{"" if op == 1 else f" opacity=\"{op}\""}/>')
    if tipo in ('bandas', 'anillos'):
        n = 7 if tipo == 'bandas' else 6; paleta = [c0, c1, c2, c0, c1, c2, c0] if tipo == 'bandas' else [c0, c1, c0, c2, c1, c0]
        for i in range(n): onda(-r + (i + .5) * 2 * r / n, rnd.uniform(-.09, .09) * r, paleta[i], 2 * r / n * 1.2)
        for i in range(n - 1): onda(-r + (i + 1) * 2 * r / n, rnd.uniform(-.1, .1) * r, mezcla(paleta[i], "#ffffff", .35), r * .05, .7)
        if tipo == 'bandas': s.append(f'<ellipse cx="{x0 + P * .38:.1f}" cy="{r * .3:.1f}" rx="{r * .3:.1f}" ry="{r * .17:.1f}" fill="#e2401f" stroke="#ffb27a" stroke-width="{r * .05:.1f}"/>')
    elif tipo == 'hielo':
        for i in range(5): onda(-r + (i + .5) * 2 * r / 5, rnd.uniform(-.06, .06) * r, [c1, c0, c1, c2, c1][i], 2 * r / 5 * 1.2)
        for _ in range(5): mancha(mezcla(c0, "#ffffff", .5), r * .09, .8, largo=.7)
    elif tipo == 'rocosa':
        s.append(f'<rect x="{x0 - 2:.1f}" y="{-r:.1f}" width="{P + 4:.1f}" height="{2 * r:.1f}" fill="{c1}"/>')
        for _ in range(9): mancha(c2, r * rnd.uniform(.2, .34))
        for _ in range(7): mancha(c0, r * rnd.uniform(.14, .26), .9)
    elif tipo == 'nubes':
        s.append(f'<rect x="{x0 - 2:.1f}" y="{-r:.1f}" width="{P + 4:.1f}" height="{2 * r:.1f}" fill="{c1}"/>')
        for i in range(6): onda(-r + (i + .5) * 2 * r / 6, rnd.choice((-1, 1)) * .22 * r, [c0, c2, c0, c1, c0, c2][i], r * .2, .9)
    elif tipo == 'tierra':
        s.append(f'<rect x="{x0 - 2:.1f}" y="{-r:.1f}" width="{P + 4:.1f}" height="{2 * r:.1f}" fill="{c1}"/>')
        for _ in range(5): mancha(c0, r * .2, .8, largo=.6)
        for _ in range(5):
            y = rnd.uniform(-.55, .55) * r
            for _ in range(2): mancha(rnd.choice((c2, "#2f9e46")), r * rnd.uniform(.3, .42), y=y + rnd.uniform(-.15, .15) * r, largo=rnd.uniform(.25, .5))
        for _ in range(4): mancha("#ffffff", r * .13, .85, largo=.7)
        onda(-r * .93, 0, "#ffffff", r * .2); onda(r * .93, 0, "#ffffff", r * .2)
    return "".join(s)


def planeta(pid, r, hacia_sol_deg, uid, gira_s):
    P = 2.6 * r; col = PLANETAS[pid][3]; atm = {"tierra": "#7fd0ff", "venus": "#ffe6a6", "urano": "#bff4ff", "neptuno": "#7f9cff"}.get(pid, vivo(col[0]))
    DEFS.append(f'<clipPath id="k{uid}"><circle r="{r:.1f}"/></clipPath><g id="t{uid}" class="tx">{textura(pid, r, P)}</g>')
    atras = frente = ""
    if PLANETAS[pid][2] == 'anillos':   # anillo de Saturno: mitad por detrás, mitad por delante (Max: .anillo.atras/.frente)
        c0, c1 = vivo(col[0]), vivo(col[1]); el = (f'<ellipse rx="{r * 1.78:.1f}" ry="{r * .52:.1f}" fill="none" stroke="{c1}" stroke-width="{r * .2:.1f}"/>'
                                                  f'<ellipse rx="{r * 1.5:.1f}" ry="{r * .4:.1f}" fill="none" stroke="{c0}" stroke-width="{r * .1:.1f}"/>')
        DEFS.append(f'<clipPath id="ka{uid}"><rect x="{-r * 2:.1f}" y="{-r:.1f}" width="{r * 4:.1f}" height="{r:.1f}"/></clipPath><clipPath id="kf{uid}"><rect x="{-r * 2:.1f}" y="0" width="{r * 4:.1f}" height="{r:.1f}"/></clipPath>')
        atras = f'<g transform="rotate(-16)" clip-path="url(#ka{uid})">{el}</g>'; frente = f'<g transform="rotate(-16)" clip-path="url(#kf{uid})">{el}</g>'
    return (f'{atras}<g clip-path="url(#k{uid})"><g class="rueda" style="--p:{-P:.1f}px;animation-duration:{gira_s:.0f}s"><use href="#t{uid}"/><use href="#t{uid}" x="{P:.1f}"/></g>'
            f'<g transform="rotate({hacia_sol_deg:.1f})"><circle r="{r:.1f}" fill="url(#gNoche)"/><circle r="{r:.1f}" fill="url(#gBrillo)"/></g></g>'
            f'<circle r="{r - .8:.1f}" fill="none" stroke="{atm}" stroke-width="1.6" opacity=".75"/>{frente}')


for i, o in enumerate(BOLAS):
    c, r, pid = o['c'], o['r'], o['pid']; hs = math.degrees(math.atan2(SOL[1] - c[1], SOL[0] - c[0])); t = ret(c)
    K['tapas_bolas'].append(f'<circle cx="{c[0]:.1f}" cy="{c[1]:.1f}" r="{r + 5:.1f}"/>')
    K['planetas'].append(f'<g transform="translate({c[0]:.1f},{c[1]:.1f})"><g class="enc"{dl(t)}>{planeta(pid, r, hs, i, 26 * (1 + PLANETAS[pid][1] * .12))}</g>'
                         + (f'<circle r="{r:.1f}" class="fl"{dl(t)}/>' if INTRO else '') + '</g>')
    if pid == SEL:   # halo cadmio (un solo halo a la vez) con la respiración «activo» de Pablo y un pulso que sale
        K['seleccion'].append(f'<g transform="translate({c[0]:.1f},{c[1]:.1f})"><circle r="{r + 7:.1f}" class="halo respira"/><circle r="{r + 7:.1f}" class="halo pulso"/></g>')
if SEL and PANEL:   # medalla del planeta en la ficha
    K['panel'][0] = K['panel'][0].replace("__MEDALLA__", f'<circle r="28" fill="#000"/>{planeta(SEL, 24, 205, "m", 30)}')

# ---------- 10. el barrido: frente de luz que sale del Sol y cruza la mesa
if INTRO:
    lejos = max(math.hypot(x - SOL[0], y - SOL[1]) for x in (0, MESA_W) for y in (0, MESA_H)); DUR = lejos / VEL
    K['barrido'].append(f'<g transform="translate({SOL[0]:.1f},{SOL[1]:.1f})"><g class="onda" style="animation-duration:{DUR:.2f}s;animation-delay:{T0:.2f}s">'
                        f'<circle r="99.2" class="o3"/><circle r="99.7" class="o2"/><circle r="100" class="o1"/></g></g>')
    ESC = lejos / 100
else: DUR = ESC = 0

# ------------------------------------------------------------------ armado
DEFS += [
    f'<radialGradient id="gNoche" cx=".5" cy=".5" r=".5" fx=".86" fy=".5"><stop offset=".38" stop-color="#03041a" stop-opacity="0"/><stop offset=".74" stop-color="#03041a" stop-opacity=".66"/><stop offset="1" stop-color="#00000c" stop-opacity=".95"/></radialGradient>',
    f'<radialGradient id="gBrillo" cx=".76" cy=".5" r=".34"><stop offset="0" stop-color="#fffcec" stop-opacity=".75"/><stop offset=".5" stop-color="#fffae6" stop-opacity=".2"/><stop offset="1" stop-color="#fffae6" stop-opacity="0"/></radialGradient>',
    f'<radialGradient id="gSol"><stop offset="0" stop-color="{SOL_C[0]}"/><stop offset=".5" stop-color="{SOL_C[1]}"/><stop offset=".82" stop-color="{SOL_C[2]}"/><stop offset="1" stop-color="{SOL_C[3]}"/></radialGradient>',
    f'<radialGradient id="gNucleo"><stop offset="0" stop-color="#fffff0" stop-opacity=".95"/><stop offset="1" stop-color="{SOL_C[0]}" stop-opacity="0"/></radialGradient>',
    f'<radialGradient id="gResplandor"><stop offset=".5" stop-color="#ffaa1e" stop-opacity="0"/><stop offset=".58" stop-color="#ffaa1e" stop-opacity=".7"/><stop offset=".76" stop-color="#ff8c14" stop-opacity=".26"/><stop offset="1" stop-color="#ff8c14" stop-opacity="0"/></radialGradient>',
    f'<radialGradient id="gLumo" cx=".38" cy=".32" r=".75"><stop offset="0" stop-color="{LUMO_BRILLO}"/><stop offset=".5" stop-color="{LUMO}"/><stop offset="1" stop-color="#ff9f1c"/></radialGradient>',
    f'<radialGradient id="gLumoHalo"><stop offset=".5" stop-color="{FOCO}" stop-opacity=".55"/><stop offset="1" stop-color="{FOCO}" stop-opacity="0"/></radialGradient>',
]
VB = 'viewBox="-10 -10 1340 710" xmlns="http://www.w3.org/2000/svg"'
SVGS = [("fondo",), ("estrellas", "orbitas", "tapas"), ("sol", "tapas_bolas", "planetas", "seleccion", "hilos", "nombres", "panel", "lumo", "barrido")]
html = ""
for n, capas in enumerate(SVGS):
    html += f'<svg {VB} class="s{n}">' + (f'<defs>{"".join(DEFS)}</defs>' if n == 2 else '') + "".join(f'<g class="c-{k}">{"".join(K[k])}</g>' for k in capas) + '</svg>'
HTML = f'<div id="w">{html}</div>'

CSS = f"""html,body{{margin:0;background:transparent;overflow:hidden}}#w{{position:absolute;left:0;top:0;width:1400px;height:720px;transform-origin:0 0;transform:{M3D}}}
svg{{position:absolute;left:-10px;top:-10px;width:1340px;height:710px;will-change:transform}}
text{{font-family:{FUENTE};font-weight:800;letter-spacing:.2px}}
.c-fondo path{{fill:none;stroke-linecap:round}}{"".join(f".p{i}{{stroke:{c};stroke-width:{w};opacity:{o}}}" for i, (c, w, o) in enumerate(ESTILOS))}
.c-estrellas .titila circle{{fill:{ESTRELLA}}}.titila{{animation:titila 5s ease-in-out infinite}}@keyframes titila{{50%{{opacity:.38}}}}
.late{{animation:late 3.4s ease-in-out infinite;transform-box:fill-box;transform-origin:center}}@keyframes late{{50%{{transform:scale(1.07);opacity:.78}}}}
.gira{{animation:gira 44s linear infinite}}.gira.rev{{animation-direction:reverse}}.gira.lento{{animation-duration:80s}}@keyframes gira{{to{{transform:rotate(360deg)}}}}
.c-orbitas circle{{fill:none;stroke-linecap:round}}.t1{{stroke-width:5.6;stroke-dasharray:26 9 14 11 40 13;opacity:.92;animation:fluye 60s linear infinite}}
.t2{{stroke-width:{TRAZO_MIN};stroke-dasharray:9 17 30 21;opacity:.6}}.t3{{stroke-width:2.2;stroke-dasharray:4 26 12 30;opacity:.32}}@keyframes fluye{{to{{stroke-dashoffset:-600}}}}
.osel .t1{{stroke-width:6.5;opacity:1}}.osel .t2{{opacity:.85}}
.c-tapas rect,.c-tapas_bolas circle{{fill:#000}}
.plasma path,.rayos path,.tx path{{fill:none;stroke-linecap:round}}.rayos{{opacity:.9}}
.rueda{{animation:rueda 30s linear infinite}}@keyframes rueda{{to{{transform:translateX(var(--p))}}}}
.respira{{animation:respira {RESPIRA_S:.2f}s ease-in-out infinite}}@keyframes respira{{50%{{opacity:.6}}}}
.halo{{fill:none;stroke:{FOCO};stroke-width:4}}.pulso{{animation:pulso 2.4s ease-out infinite;transform-box:fill-box;transform-origin:center}}@keyframes pulso{{from{{transform:scale(1);opacity:.9}}to{{transform:scale(1.45);opacity:0}}}}
.flota{{animation:flota 3.2s ease-in-out infinite alternate}}@keyframes flota{{to{{transform:translateY(-4px) rotate(4deg)}}}}
.ojo{{animation:parpadea 4.2s infinite;transform-box:fill-box;transform-origin:center}}@keyframes parpadea{{0%,92%,100%{{transform:scaleY(1)}}95%{{transform:scaleY(.1)}}}}
.cola{{fill:none;stroke:{FOCO};stroke-width:2.6;stroke-linecap:round;opacity:.8}}
.subraya{{animation:respira {RESPIRA_S:.2f}s ease-in-out infinite}}
.abre{{animation:abre {T_ASIENTA}s {TINTA_ASIENTA} both}}@keyframes abre{{from{{opacity:0;transform:translateY(-10px)}}}}
.vuela{{animation:vuela 1.4s linear .2s both}}.asienta{{animation:asienta .5s {RESORTE} 1.6s both;transform-box:fill-box;transform-origin:center}}@keyframes asienta{{from{{transform:scale(1.2,.82)}}}}{VUELO}
""" + (f""".rv{{animation:rv {T_ONDULA}s {TINTA_ASIENTA} both}}@keyframes rv{{from{{opacity:0}}}}
.enc{{animation:enc {T_FORMA}s {CURVA} both}}@keyframes enc{{from{{opacity:0;transform:scale(.45)}}}}
.fl{{fill:#fff8d8;opacity:0;animation:fl {T_ONDULA}s ease-out both}}@keyframes fl{{0%{{opacity:0}}18%{{opacity:.95}}100%{{opacity:0}}}}
.cub{{stroke:#000;stroke-width:15;stroke-dasharray:100 100;stroke-linecap:butt!important;animation:cub {T_ASIENTA}s cubic-bezier(.33,0,.67,1) both}}@keyframes cub{{from{{stroke-dashoffset:0}}to{{stroke-dashoffset:100}}}}
.solin{{animation:solin {T_FORMA}s {CURVA} both}}@keyframes solin{{from{{opacity:0;transform:scale(.5)}}}}
.onda{{animation:onda linear both}}@keyframes onda{{0%{{transform:scale(.3);opacity:0}}6%{{opacity:1}}80%{{opacity:.9}}100%{{transform:scale({ESC:.2f});opacity:0}}}}
.onda circle{{fill:none;vector-effect:non-scaling-stroke;stroke-linecap:round}}.o1{{stroke:#fff8d8;stroke-width:3.5}}.o2{{stroke:{FOCO};stroke-width:9;opacity:.6;stroke-dasharray:90 14 40 18 130 22}}.o3{{stroke:#ff9a1f;stroke-width:30;opacity:.2}}
""" if INTRO else "")

WIDGET = {"type": "effect", "id": "solar", "shader": "html", "rect_mm": [0, 0, 1000, 563], "params": {"html": HTML, "css": CSS}}
nombre_estado = (f"ficha-{SEL}" if SEL else "espera")
resumen = {"estado": nombre_estado, "intro": INTRO, "planetas": {o['pid']: o['etiqueta'] for o in BOLAS}, "nombres": sorted(ETIQ), "con_hilo": [k for k, v in ETIQ.items() if v[3]],
           "panel_mm": [round(v) for v in PANEL] if PANEL else None, "pinceladas": N_PINCEL, "estrellas": [len(CHICAS), len(GRANDES)], "avisos": AVISOS,
           "kB": round(len(json.dumps(WIDGET, ensure_ascii=False).encode()) / 1024, 1)}

if '--preview' in ARG:
    # Vista previa sin mesa: la foto cenital (oscurecida 35 %) a 1 px = 1 mm y encima el MISMO svg, en modo aditivo y sin matrix3d.
    # ?t=<s> congela todas las animaciones en ese instante (para capturas con Chrome headless).
    d = AQUI / 'preview-solar'; d.mkdir(exist_ok=True); sal = Path(arg('--salida', d / f"{nombre_estado}.html"))
    pagina = f"""<!doctype html><meta charset="utf-8"><title>solar · {nombre_estado}</title><style>{CSS}
html,body{{background:#000;width:1340px;height:710px}}#foto{{position:absolute;left:0;top:0;width:1340px;height:710px;filter:brightness(.65)}}
#w{{transform:none;left:10px;top:10px;mix-blend-mode:screen}}</style><img id="foto" src="../skin/entrada-vivo.png">{HTML}
<script>const t=new URLSearchParams(location.search).get('t');if(t!==null)addEventListener('load',()=>document.getAnimations().forEach(a=>{{a.pause();a.currentTime=parseFloat(t)*1000}}));</script>"""
    sal.write_text(pagina, encoding="utf-8"); resumen["preview"] = str(sal)
else:
    sal = Path(arg('--salida', AQUI / 'out/widget-solar.json')); sal.write_text(json.dumps(WIDGET, ensure_ascii=False), encoding="utf-8"); resumen["widget"] = str(sal)
print(json.dumps(resumen, ensure_ascii=False, indent=1))

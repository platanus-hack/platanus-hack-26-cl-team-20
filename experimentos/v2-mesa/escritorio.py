#!/usr/bin/env python3
"""Escena de escritorio de WUI (World UI) sobre la mesa real.

Lenguaje visual: «tinta y papel» de Max (escenas/escritorio/tema.css) + marca y movimiento de WUI de Pablo
(wui_tokens.py, wui_motion.py, landing global.css): Instrument Sans con peso, cuatro tiempos de la tinta.

Una sola proyección en mm reales, armada desde out/objetos.json (máscaras SAM). Sin seguimiento continuo: nada se
apaga ni se prende cuando pasan manos. Se vuelve a correr si se reordena la mesa.
   escritorio.py [--estado pregunta|aprobado|rechazado] [--cambiados i,j,k] [--objetos ruta.json]
     --cambiados: índices (en out/objetos.json) de lo que se movió o es nuevo. Solo eso (y lo anclado a eso) se
                  anima, con un escaneo local sobre su máscara; el resto queda quieto. Lista vacía: nada se anima.
     --objetos:   otro objetos.json (pruebas, p. ej. con un computador que hoy no está en la mesa).

Reglas de composición (nada se dibuja encima de otra cosa):
  1. Orden de dibujo explícito por capas: libres → contornos → rutas → paneles → botones → textos.
  2. Cada caja de texto o panel se comprueba contra las huellas de los objetos (bbox con margen), contra las cajas ya
     colocadas y contra el área que el proyector alcanza; si choca prueba otras posiciones, se achica o se omite.
  3. El recorrido ortogonal evita objetos, paneles y textos.
  4. El menú radial del mouse elige los ángulos libres.
Sin --estado corre el barrido de arranque; con --estado solo cambia la respuesta (sin repetir el barrido).
Sobre cada objeto conocido va una capa propia encajada en su máscara: taza, lata, mouse, vaso y teclado.
"""
import heapq, json, os, re, subprocess, sys, time
from pathlib import Path
import numpy as np
import marca_mesa as marca
try: import cv2
except Exception: cv2 = None

S = '/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'
met = json.load(open('fp-metric.json')); Hm = np.array(met['mm_to_canvas']); Hm = Hm / Hm[2, 2]
M3D = "matrix3d(%s)" % ",".join("%.9g" % v for v in [Hm[0, 0], Hm[1, 0], 0, Hm[2, 0], Hm[0, 1], Hm[1, 1], 0, Hm[2, 1], 0, 0, 1, 0, Hm[0, 2], Hm[1, 2], 0, Hm[2, 2]])
ANIMAR = '--estado' not in sys.argv
EST = sys.argv[sys.argv.index('--estado') + 1] if '--estado' in sys.argv else 'pregunta'
AVISOS = []   # lo que se omitió o se achicó por falta de espacio (sale por stdout)
def _arg(n): return sys.argv[sys.argv.index(n) + 1] if n in sys.argv and sys.argv.index(n) + 1 < len(sys.argv) else None
IDX_CAMB = {int(v) for v in (_arg('--cambiados') or '').split(',') if v.strip().lstrip('-').isdigit()}
F_OBJ = _arg('--objetos') or 'out/objetos.json'; F_MEM = Path('out/escritorio-cajas.json')


# ------------------------------------------------------------------ geometría
def bb(o):
    p = np.array(o['poligono_mm']); return float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max())


def crece(b, m): return b[0] - m, b[1] - m, b[2] + m, b[3] + m


def toca(a, b): return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def area(b): return max(b[2] - b[0], 0) * max(b[3] - b[1], 0)


def comun(a, b): return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


# Área que el proyector alcanza de verdad (esquinas del canvas en mm): fuera de ella el texto sale cortado.
QUAD = np.array(met.get('canvas_mm') or [[1320, 690], [0, 690], [0, 0], [1320, 0]], float)
_sg = np.sign(sum((QUAD[(i + 1) % 4] - QUAD[i])[0] * (QUAD[(i + 2) % 4] - QUAD[(i + 1) % 4])[1] - (QUAD[(i + 1) % 4] - QUAD[i])[1] * (QUAD[(i + 2) % 4] - QUAD[(i + 1) % 4])[0] for i in range(4)))
MESA = (6.0, 8.0, 1314.0, 680.0)


def dentro(b, m=10):
    """La caja completa cae dentro de la mesa y del cuadrilátero proyectable, con margen m."""
    if b[0] < MESA[0] or b[1] < MESA[1] or b[2] > MESA[2] or b[3] > MESA[3]: return False
    for i in range(4):
        p, q = QUAD[i], QUAD[(i + 1) % 4]; e = (q - p) / np.linalg.norm(q - p)
        for x, y in ((b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])):
            if _sg * (e[0] * (y - p[1]) - e[1] * (x - p[0])) < m: return False
    return True


# ------------------------------------------------------------------ objetos
# Mejor puntaje primero; una detección que cae casi entera dentro de otra mejor es un duplicado (p. ej. «cargador»
# sobre el teléfono, «lata» dentro del vaso) y se descarta.
# La detección corre con la proyección encendida: a veces «ve» nuestros propios botones, paneles y recuadros. Lo que
# calza con una caja que proyectamos la vez anterior (y no tiene puntaje alto) es luz nuestra, no un objeto.
try: MEM = json.load(open(F_MEM))
except Exception: MEM = []


def es_nuestro(o):
    if o.get('score', 1) >= 0.6: return False
    b = bb(o); a = max(area(b), 1)
    for m in MEM:
        c = m['b']; i = comun(b, c)
        if i / (a + area(c) - i) > 0.4 or (m['k'] != 'libre' and i / a > 0.7): return True
    return False


crudos = json.load(open(F_OBJ))
for i, o in enumerate(crudos): o['_i'] = i
vistos = []
for o in sorted(crudos, key=lambda o: -o.get('score', 0)):
    b = bb(o)
    if es_nuestro(o) or any(comun(b, bb(k)) / max(area(b), 1) > 0.6 for k in vistos): continue
    vistos.append(o)
O = {}
for o in vistos:
    k = o['etiqueta']; n = 2
    while k in O: k = f"{o['etiqueta']} {n}"; n += 1
    O[k] = o


def renombra(k, base):
    n = 1; nk = base
    while nk in O: n += 1; nk = f"{base} {n}"
    O[nk] = O.pop(k)


for k in [k for k in O if k.startswith('cargador') and O[k].get('area_mm2', 0) > 6000]: renombra(k, 'cable USB')
cg = sorted([k for k in O if k.startswith('cargador')], key=lambda k: O[k].get('area_mm2', 0))
if cg and cg[0] != 'cargador': renombra(cg[0], 'cargador')
for k in [k for k in O if k.startswith('computador ')]: renombra(k, 'pantalla')      # la tapa abierta: huella, sin rótulo
SIN_ROTULO = {k for k in O if k.startswith('pantalla') or (k.split(' ')[0] in ('lámina', 'sticker') and O[k].get('score', 1) < 0.4)}
CAMB = {k for k, o in O.items() if o['_i'] in IDX_CAMB}   # objetos que se movieron o son nuevos
LBC = None; ROTC = ''; KBR = None; RA, RC = 0.0, (0.0, 0.0); ZONAS = []


def gira(pts):
    """Puntos del marco del computador (derecho) -> mm de mesa, con su giro."""
    a = np.radians(RA); R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]]); return ((np.asarray(pts, float) - RC) @ R.T + RC).round(1).tolist()


if 'computador' in O:   # trackpad: al centro del borde que mira a la persona (medido en este MacBook)
    LBC = bb(O['computador'])
    if cv2 is not None:   # caja orientada: el computador casi nunca queda derecho
        (rcx, rcy), (rw, rh), ra = cv2.minAreaRect(np.array(O['computador']['poligono_mm'], np.float32))
        if rw < rh: rw, rh, ra = rh, rw, ra + 90
        ra = (ra + 90) % 180 - 90
        if abs(ra) < 40: LBC = (rcx - rw / 2, rcy - rh / 2, rcx + rw / 2, rcy + rh / 2); ROTC = f'rotate({ra:.2f} {rcx:.1f} {rcy:.1f})'; RA, RC = ra, (rcx, rcy)
        try:
            _n = cv2.imread('out/norm-full.png') if F_OBJ == 'out/objetos.json' else None; _E = 2.5
            if _n is not None:
                _b = bb(O['computador']); _x0, _y0 = int((_b[0] + 10) * _E), int((_b[1] + 10) * _E); _x1, _y1 = int((_b[2] + 10) * _E), int((_b[3] + 10) * _E)
                _g = cv2.cvtColor(_n[max(_y0, 0):_y1, max(_x0, 0):_x1], cv2.COLOR_BGR2GRAY); _m = np.zeros(_g.shape, np.uint8)
                cv2.fillPoly(_m, [np.round((np.array(O['computador']['poligono_mm'], float) + 10) * _E - [max(_x0, 0), max(_y0, 0)]).astype(np.int32)], 1)
                _osc = ((_g < min(95, float(np.median(_g[_m > 0])) - 22)) & (_m > 0)).astype(np.uint8)
                _osc = cv2.morphologyEx(cv2.morphologyEx(_osc, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (19, 19))), cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
                _cs, _ = cv2.findContours(_osc, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE); _cand = []
                for _c in _cs:
                    (_cx, _cy), (_w, _h), _a = cv2.minAreaRect(_c)
                    if _w < _h: _w, _h, _a = _h, _w, _a + 90
                    _a = (_a + 90) % 180 - 90
                    if _h > 1 and 1.9 < _w / _h < 3.7 and 0.70 * rw < _w / _E < 1.0 * rw and abs(_a) < 40: _cand.append((_cy, _cx, _w / _E, _h / _E, _a))
                if _cand:   # el más cercano a la persona (y menor): la pantalla abierta queda del otro lado
                    _cy, _cx, kw_, kh_, ka_ = min(_cand); kcx, kcy = (_cx + max(_x0, 0)) / _E - 10, (_cy + max(_y0, 0)) / _E - 10; sk = kw_ / 274.0
                    KBR = (kcx - kw_ / 2, kcy - kh_ / 2, kcx + kw_ / 2, kcy + kh_ / 2); LBC = (kcx - 156 * sk, kcy - kh_ / 2 - 97 * sk, kcx + 156 * sk, kcy + kh_ / 2 + 14 * sk)
                    ROTC = f'rotate({ka_:.2f} {kcx:.1f} {kcy:.1f})'; RA, RC = ka_, (kcx, kcy); AVISOS.append(f'teclado detectado: {kw_:.0f} x {kh_:.0f} mm, giro {ka_:.1f} grados')
        except Exception as ex: AVISOS.append(f'teclado no detectado: {type(ex).__name__}: {ex}')
    x0, y0, x1, y1 = LBC; w, h = x1 - x0, y1 - y0
    tx0, tx1, ty0, ty1 = x0 + 0.30 * w, x0 + 0.70 * w, y0 + 0.05 * h, y0 + 0.31 * h
    TRACK = (tx0, ty0, tx1, ty1)

# ------------------------------------------------------------------ tokens
# Max (tema.css): papel cálido --sd-tinta, lápiz rojo --sd-foco, alerta, tinta azul del agente, radio 3, trazo 3.
# Pablo (wui_tokens.py / global.css): LUZ y NIEBLA para la señal activa, Instrument Sans, tiempos y curva de la tinta.
LUZ, PAPEL, PAPEL2, LAPIZ, ALERTA, TINTA = "#ffffff", "#F4F4F2", "#a9a9a6", "#ffffff", "#f2b15c", "#F4F4F2"
# Tintes de zona (uno por función, no por adorno): verde = estación de recarga de la persona (café + Monster; sale de
# --sd-ok de Max, más saturado para el proyector); ámbar = carga del teléfono (--sd-alerta); azul tinta = herramientas del mouse.
VERDE = "#86f5a0"
TRAZO, FINO, RADIO = 3.0, 2.2, 3                       # mm: el trazo principal nunca baja de 3
T_ENTRA, T_ONDULA, T_FORMA, T_ASIENTA = 0.42, 0.88, 1.28, 1.72   # lámina 09: entra, ondula, forma, asienta
C_TINTA = "cubic-bezier(.16,1,.3,1)"                   # asentamiento de la tinta (Pablo)
C_LLEGA = "cubic-bezier(.1,.75,.15,1)"                 # trazo que llega rápido y se asienta (Max)
C_ESCRIBE = "cubic-bezier(.35,.1,.45,1)"               # mano que escribe (Max)
ETIQ, CONF, INSTR = 12, 14, 24                         # mm de mesa: etiqueta, confirmación, instrucción

# ------------------------------------------------------------------ composición: capas, cajas y colisiones
CAPAS = {k: [] for k in ('libres', 'contornos', 'sobre', 'rutas', 'paneles', 'botones', 'textos')}; DEFS = []; LIBRES = []
OBST = [(e, crece(bb(o), 7) if e in ('libreta', 'computador') else bb(o)) for e, o in O.items()]   # huellas (con sus marcas de corte)
# Reservar el rincón de la firma en las mismas coordenadas que las huellas de los objetos.
fx, fy, fw, fh = marca.FIRMA_X, marca.FIRMA_Y, marca.FIRMA_ANCHO, marca.FIRMA_ALTO
firma_px = np.array([[fx, fy, 1], [fx + fw, fy, 1], [fx, fy + fh, 1], [fx + fw, fy + fh, 1]])
firma_mm = firma_px @ np.linalg.inv(Hm).T
firma_mm = firma_mm[:, :2] / firma_mm[:, 2:]
cajas = [(*firma_mm.min(axis=0), *firma_mm.max(axis=0))]
X_INI, X_FIN = 1250.0, -30.0                           # el barrido entra por la izquierda de la persona (x grande)


def retardo(x, extra=0.0): return max(0.0, T_ASIENTA * (X_INI - x) / (X_INI - X_FIN)) + extra


def vivo(de=None):
    """¿Este componente se anima? Todo en el arranque; después, solo lo anclado a un objeto que cambió."""
    return ANIMAR or (de is not None and bool(CAMB & ({de} if isinstance(de, str) else set(de))))


def pon(capa, x, cuerpo, boot='sube', extra=0.0, de=None):
    """Agrega un componente a su capa. En el arranque aparece cuando el barrido pasa por su x; en un rearme solo se
    anima si está anclado (de) a un objeto que cambió, tras el escaneo local; si no, queda quieto."""
    if not vivo(de): CAPAS[capa].append(f'<g>{cuerpo}</g>'); return
    d = retardo(x, extra) if ANIMAR else 0.45 + extra
    CAPAS[capa].append(f'<g class="b {boot}" style="--d:{d:.2f}s">{cuerpo}</g>')


def choca(b, m_obj=11, m_caja=6):
    if not dentro(b): return True
    return any(toca(b, crece(o, m_obj)) for _, o in OBST) or any(toca(b, crece(c, m_caja)) for c in cajas)


def colocar(candidatos, **kw):
    """Primera caja candidata que no choca con nada; queda reservada. None si no hay lugar."""
    for b in candidatos:
        if not choca(b, **kw): cajas.append(b); return b
    return None


def ancho(s, size, k=0.54): return len(s) * size * k   # ancho estimado con ~8 % de holgura (Instrument Sans 600–700 mide ≈0,50 em por letra)


def txt(x, y, s, size=ETIQ, col=PAPEL, cls="s", anchor="middle", peso=700, extra=""):
    """Texto girado 180°: se lee desde el lado de la persona (el lado lejano a la cámara)."""
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size:g}" font-weight="{peso}" fill="{col}" class="{cls}" text-anchor="{anchor}" transform="rotate(180 {x:.1f} {y:.1f})" {extra}>{s}</text>'


def renglon(x, ytop, s, size, col=PAPEL, cls="s", lado="izq", peso=700, extra=""):
    """Un renglón en el marco de la persona: x es su borde (izq = x mayor) e ytop su tope (y mayor)."""
    return txt(x, ytop - 0.76 * size, s, size, col, cls, {"izq": "start", "der": "end", "centro": "middle"}[lado], peso, extra)


def alrededor(b, w, h, g=14):
    """Cajas w×h pegadas a los cuatro lados de b: primero el lado de la persona, luego el opuesto y los costados."""
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2; out = []
    for dx in (0, -30, 30, -60, 60):
        out.append((cx + dx - w / 2, b[1] - g - h, cx + dx + w / 2, b[1] - g)); out.append((cx + dx - w / 2, b[3] + g, cx + dx + w / 2, b[3] + g + h))
    for dy in (0, -30, 30):
        out.append((b[0] - g - w, cy + dy - h / 2, b[0] - g, cy + dy + h / 2)); out.append((b[2] + g, cy + dy - h / 2, b[2] + g + w, cy + dy + h / 2))
    return out


def rotulo(b, s, sizes=(CONF, ETIQ), col=PAPEL, cls="s", capa='textos', que=None, g=14, de=None):
    """Texto de un renglón junto a la caja b. Prueba posiciones y tamaños; si no cabe, se omite."""
    for size in sizes:
        w, h = ancho(s, size, 0.62 if cls == 'm' else 0.54) + 4, size * 1.15
        c = colocar(alrededor(b, w, h, g))
        if c:
            pon(capa, (c[0] + c[2]) / 2, renglon((c[0] + c[2]) / 2, c[3] - 0.08 * size, s, size, col, cls, "centro"), 'sube', 0.25, de); return c
    AVISOS.append(f"omitido por falta de espacio: {que or s}"); return None


def rejilla(paso, m_obj, m_caja):
    """Ocupación de la mesa: objetos, cajas ya colocadas y todo lo que el proyector no alcanza."""
    W, H = 1320 // paso, 690 // paso; occ = np.zeros((H, W), bool)
    def marca(b, m): occ[max(int((b[1] - m) // paso), 0):max(int((b[3] + m) // paso) + 1, 0), max(int((b[0] - m) // paso), 0):max(int((b[2] + m) // paso) + 1, 0)] = True
    for _, b in OBST: marca(b, m_obj)
    for b in cajas: marca(b, m_caja)
    for r in range(H):
        for c in range(W):
            if not occ[r, c] and not dentro((c * paso, r * paso, (c + 1) * paso, (r + 1) * paso), 4): occ[r, c] = True
    return occ


# ------------------------------------------------------------------ 0. máscara, textura y altura de cada objeto
# Se arman desde la máscara de SAM y, si está, desde la textura real (out/norm-full.png: vista cenital a 2,5 px/mm).
# Cámara y proyector están casi alineados: lo que se dibuja sobre la silueta vista cae sobre el objeto.
E = 2.5; NORM = cv2.imread('out/norm-full.png') if cv2 is not None and Path('out/norm-full.png').exists() and F_OBJ == 'out/objetos.json' else None
CAM = np.array([660.0, 1100.0])   # pie de la cámara en mm de mesa (aprox.): los objetos altos se inclinan alejándose de ahí
ROT = {}                          # rótulos enriquecidos por las capas (p. ej. nivel del vaso)


def recorte(o, m=4):
    """Recorte de la vista normalizada sobre el objeto, su máscara y la función px del recorte → mm de mesa."""
    b = bb(o); x0, y0 = b[0] - m, b[1] - m; w, h = int((b[2] - b[0] + 2 * m) * E), int((b[3] - b[1] + 2 * m) * E)
    mask = np.zeros((h, w), np.uint8); cv2.fillPoly(mask, [np.round((np.array(o['poligono_mm']) - [x0, y0]) * E).astype(np.int32)], 255)
    img = None
    if NORM is not None:
        px, py = int(round((x0 + 10) * E)), int(round((y0 + 10) * E)); a0, b0, a1, b1 = max(px, 0), max(py, 0), min(px + w, NORM.shape[1]), min(py + h, NORM.shape[0])
        if a1 > a0 and b1 > b0: img = np.zeros((h, w, 3), np.uint8); img[b0 - py:b1 - py, a0 - px:a1 - px] = NORM[b0:b1, a0:a1]
    return img, mask, (lambda q: np.asarray(q, float) / E + [x0, y0])


def forma(mask, amm, abrir=0):
    """Centro, eje largo u (apuntando lejos de la cámara: hacia «arriba» del objeto), eje corto v y extensiones en mm."""
    if abrir:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(abrir * E) | 1, int(abrir * E) | 1)); ab = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
        if ab.any(): mask = ab
    ys, xs = np.nonzero(mask); P = amm(np.stack([xs, ys], 1)); c = P.mean(0); _, V = np.linalg.eigh(np.cov((P - c).T)); u = V[:, 1]
    if np.dot(u, c - CAM) < 0: u = -u
    v = np.array([-u[1], u[0]]); pu, pv = (P - c) @ u, (P - c) @ v
    return c, u, v, (np.percentile(pu, 1), np.percentile(pu, 99), np.percentile(pv, 1), np.percentile(pv, 99)), mask


def camino(pts, cerrar=False): return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + ("Z" if cerrar else "")


def arco(c, a, b, v, u, t0=0.0, t1=np.pi, n=14):
    """Media elipse (o la que se pida) con semieje a sobre v y b sobre −u: el lado de la banda que mira a la cámara."""
    return np.array([c + a * np.cos(t) * v - b * np.sin(t) * u for t in np.linspace(t0, t1, n)])


def mayor_blob(binaria):
    n, lab, st, cen = cv2.connectedComponentsWithStats(binaria.astype(np.uint8), 8)
    if n < 2: return None, 0
    k = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA])); return (lab == k), int(st[k, cv2.CC_STAT_AREA])




# Paralaje: cámara y proyector no están en el mismo punto, así que la luz apuntada a un punto de la mesa cae corrida
# sobre lo que tiene altura. Medido en esta mesa con discos de prueba (proyectar, fotografiar, restar): lo que cae a
# altura h se ve desplazado ≈ h·PX_K. Cada objeto lleva su corrección: traslación si es bajo y parejo, o una matriz
# que crece con la altura a lo largo de su eje si está de pie (base en la mesa, borde arriba; TOPE = su plano superior).
PX_K = np.array([0.0, 0.28])   # medido en la mesa final con luz estructurada (mouse, 32 mm)
ALTO = {'taza': 95, 'lata': 158, 'vaso': 145, 'mouse': 32, 'teléfono': 8, 'cargador': 28, 'computador': 14, 'libreta': 10}   # mm
DE_PIE = ('taza', 'lata', 'vaso'); TR = {}; TOPE = {}
for e, o in O.items():
    base = e.split(' ')[0]; H = ALTO.get(base)
    if not H or cv2 is None: continue
    try:
        _, mask, amm = recorte(o); c, u, v, (u0, u1, v0, v1), _ = forma(mask, amm, 10); TOPE[e] = f'translate({-H * PX_K[0]:.1f},{-H * PX_K[1]:.1f})'
        if base in DE_PIE and u1 - u0 > 20:
            k = H / (u1 - u0); q = float(u @ c + u0); d = PX_K
            TR[e] = f'matrix({1 - k * d[0] * u[0]:.5f},{-k * d[1] * u[0]:.5f},{-k * d[0] * u[1]:.5f},{1 - k * d[1] * u[1]:.5f},{k * d[0] * q:.2f},{k * d[1] * q:.2f})'
        else: TR[e] = TOPE[e]
    except Exception: pass


def erosion(mask, mm): return cv2.erode(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(mm * E) | 1, int(mm * E) | 1)))


def proyector(o):
    """Para objetos altos (traen poligono_proy_mm): función puntos de la vista de cámara → puntos para el proyector.
    Es la misma corrección de altura.py (la base queda igual, la parte elevada se acorta por k hacia la cámara), para que
    lo que se dibuja DENTRO del objeto (logo, calco, aros) caiga donde corresponde y no se derrame hacia atrás."""
    if 'afin_proy' in o:   # encaje medido con luz estructurada (altura.afin_sl): es exacto, no una estimación
        A_ = np.array(o['afin_proy'], float); return lambda x: np.asarray(x, float).reshape(-1, 2) @ A_[:, :2].T + A_[:, 2]
    try: import altura; C = np.array(altura.CAM[:2], float)
    except Exception: C = np.array([563.0, 857.0])
    q = np.array(o['poligono_mm'], float); r = q.mean(0) - C; r /= np.linalg.norm(r); tg = np.array([-r[1], r[0]]); t = (q - C) @ r
    t0 = float(t.min()); d = min(float(min(cv2.minAreaRect(q.astype(np.float32))[1])), float(t.max() - t0)); k = float(o.get('k_proyector', 1))

    def M(x):
        x = np.asarray(x, float).reshape(-1, 2); tq = (x - C) @ r - t0
        return C + np.outer(t0 + np.where(tq <= d, tq, d + (tq - d) * k), r) + np.outer((x - C) @ tg, tg)
    return M


def adentro(poly, mm):
    """El polígono encogido mm hacia adentro (para recortar capas sin que se escape luz por el borde)."""
    q = np.array(poly, float)
    if cv2 is None: return q
    x0, y0 = q.min(0) - 2; m = np.zeros((int((q[:, 1].max() - y0 + 2) * E) + 1, int((q[:, 0].max() - x0 + 2) * E) + 1), np.uint8); cv2.fillPoly(m, [np.round((q - [x0, y0]) * E).astype(np.int32)], 255)
    cs, _ = cv2.findContours(erosion(m, 2 * mm), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max(cs, key=cv2.contourArea)[:, 0] / E + [x0, y0] if cs else q


_ident = lambda x: np.asarray(x, float).reshape(-1, 2)
PM = {}   # objeto alto → su función cámara → proyector
for e, o in O.items():
    if 'poligono_proy_mm' in o and cv2 is not None:
        try: PM[e] = proyector(o); TR.pop(e, None); TOPE.pop(e, None)
        except Exception: pass


def silueta(o): return o.get('poligono_proy_mm') or o['poligono_mm']   # lo que se dibuja SOBRE el objeto usa la silueta del proyector


def sobre(e, cuerpo):
    """Envuelve lo que se dibuja SOBRE el objeto e con su corrección de paralaje."""
    if isinstance(cuerpo, tuple): return sobre(e, cuerpo[0]) + (f'<g transform="{TOPE[e]}">{cuerpo[1]}</g>' if e in TOPE else cuerpo[1])   # (cuerpo, tope)
    return f'<g transform="{TR[e]}">{cuerpo}</g>' if e in TR else cuerpo


# ------------------------------------------------------------------ 1. contornos de objetos
def cortes(b, L=22, m=7):
    """Marcas de corte en las 4 esquinas (como las de la libreta de Max) en vez de un contorno completo."""
    x0, y0, x1, y1 = crece(b, m); d = ""
    for x, y, sx, sy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x1, y1, -1, -1), (x0, y1, 1, -1)):
        d += f"M{x + sx * L:.0f},{y:.0f} L{x:.0f},{y:.0f} L{x:.0f},{y + sy * L:.0f} "
    return f'<path d="{d}" pathLength="1"'


def contorno(forma, col, grosor, fase=0.0):
    """Contorno vivo: aura suave que respira + trazo nítido + una luz que viaja por el borde. forma = inicio del elemento."""
    return (f'<g class="tras">{forma} fill="none" stroke="{col}" stroke-width="{grosor + 5}" stroke-opacity=".55" stroke-linejoin="round" filter="url(#gl)" class="aura" style="animation-delay:-{fase:.1f}s"/></g>'
            f'{forma} fill="none" stroke="{col}" stroke-width="{grosor}" stroke-linejoin="round" stroke-linecap="square" class="tz"/>'
            f'<g class="tras">{forma} fill="none" stroke="{LUZ}" stroke-width="{grosor + 1.2}" stroke-linecap="round" stroke-dasharray=".07 .93" class="corre" style="animation-delay:-{fase * 2.1:.1f}s"/></g>')


DEFS.append('<filter id="gl" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="2.4"/></filter>'
            f'<linearGradient id="sc" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="{LUZ}" stop-opacity="0"/><stop offset=".45" stop-color="{LUZ}" stop-opacity=".75"/>'
            f'<stop offset=".55" stop-color="{LUZ}" stop-opacity=".75"/><stop offset="1" stop-color="{LUZ}" stop-opacity="0"/></linearGradient>')
REC = []   # sin zona de recarga
TINTE = {**{e: VERDE for e in REC}, **{e: TINTA for e in O if e.split(' ')[0] == 'mouse'}}
for n, (e, o) in enumerate(O.items()):
    b = bb(o); cx = (b[0] + b[2]) / 2
    if e.startswith('pantalla'): continue
    p = " ".join(f"{x:.1f},{y:.1f}" for x, y in adentro(silueta(o), 2.5)); pc = " ".join(f"{x:.1f},{y:.1f}" for x, y in adentro(silueta(o), 2.5 if e in PM else 1.2))
    DEFS.append(f'<clipPath id="cp{o["_i"]}"><polygon points="{pc}"/></clipPath>')
    if e.split(' ')[0] in ('taza', 'lata') and cv2 is not None:
        hq = cv2.convexHull(np.array(adentro(silueta(o), 2.5), np.float32))[:, 0]; p = " ".join(f"{x:.1f},{y:.1f}" for x, y in hq)
    if e == 'computador' and LBC: pon('contornos', cx, sobre(e, f'<g transform="{ROTC}">' + contorno(cortes(LBC), PAPEL, TRAZO, n * 1.3) + '</g>'), 'traza', 0, e)
    elif e in ('libreta', 'computador'): pon('contornos', cx, sobre(e, contorno(cortes(b), PAPEL, TRAZO, n * 1.3)), 'traza', 0, e)
    else: pon('contornos', cx, sobre(e, contorno(f'<polygon points="{p}" pathLength="1"', TINTE.get(e, PAPEL2), FINO + 0.3, n * 1.3)), 'traza', 0, e)
    if not ANIMAR and e in CAMB:   # escaneo local: un haz recorre la máscara del objeto que cambió y enciende su contorno
        CAPAS['sobre'].append(sobre(e, f'<g clip-path="url(#cp{o["_i"]})"><rect class="scan" x="{b[0] - 46:.0f}" y="{b[1] - 4:.0f}" width="46" height="{b[3] - b[1] + 8:.0f}" fill="url(#sc)" style="--w:{b[2] - b[0] + 50:.0f}px"/></g>'))
# Estación de recarga: un área verde que envuelve la taza y la lata (huella de cámara: lo que ocupan a la vista), con
# un rayo que pulsa y un medidor que se llena, en un espacio libre junto a ellas.
RAYO = "M10,0 L1,15 H8 L5,28 L16,11 H9 Z"
if REC and cv2 is not None:
    try:
        todos = np.concatenate([np.array(O[e]['poligono_mm'], float) for e in REC]); zx, zy = todos.min(0) - 20
        zm = np.zeros((int((todos[:, 1].max() - zy + 20) * E), int((todos[:, 0].max() - zx + 20) * E)), np.uint8)
        for e in REC: cv2.fillPoly(zm, [np.round((np.array(O[e]['poligono_mm'], float) - [zx, zy]) * E).astype(np.int32)], 255)
        zm = cv2.dilate(zm, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(18 * E) | 1, int(18 * E) | 1)))
        cs, _ = cv2.findContours(zm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE); casco = cv2.convexHull(np.concatenate(cs))
        partes = [casco] if cv2.contourArea(casco) < 1.7 * (zm > 0).sum() else [cv2.convexHull(k) for k in cs]   # lejos una de otra: un halo por objeto
        zb = None
        for k in partes:
            q = cv2.approxPolyDP(k, 1.5, True)[:, 0] / E + [zx, zy]; kb = (max(q[:, 0].min(), MESA[0] + 11), max(q[:, 1].min(), MESA[1] + 11), q[:, 0].max(), q[:, 1].max())
            cajas.append(kb); zb = kb if zb is None or area(kb) > area(zb) else zb
            pon('libres', (kb[0] + kb[2]) / 2, f'<path d="{camino(q, True)}" fill="{VERDE}" fill-opacity=".1" stroke="none"/>' + contorno(f'<path d="{camino(q, True)}" pathLength="1"', VERDE, TRAZO, 2.0), 'traza', 0, tuple(REC))
        bw, bh = 104, 44; c = colocar(alrededor(zb, bw, bh, 9))
        if c:
            xl, yt = c[2], c[3]; seg = "".join(f'<rect x="{xl - 30 - 13 * (k + 1) + 2:.1f}" y="{yt - 40:.1f}" width="11" height="8" rx="1.5" fill="{VERDE}" class="seg" style="animation-delay:{0.34 * k:.2f}s"/>' for k in range(5))
            pon('textos', (c[0] + c[2]) / 2, f'<path d="{RAYO}" transform="translate({xl - 3:.1f},{yt - 6:.1f}) rotate(180) scale(1.25)" fill="{VERDE}" class="rayo"/>'
                + renglon(xl - 30, yt - 4, "Recarga", 18, VERDE) + seg, 'sube', 0.3, tuple(REC))
        else: AVISOS.append("omitido por falta de espacio: rayo y medidor de la zona de recarga")
    except Exception as ex: AVISOS.append(f"zona de recarga omitida: {type(ex).__name__}: {ex}")
if 'libreta' in O:   # franja del marcador del gato (otro widget) frente a la libreta: nadie más escribe ahí
    b = bb(O['libreta']); cajas.append(((b[0] + b[2]) / 2 - 150, b[1] - 46, (b[0] + b[2]) / 2 + 150, b[1] - 5))

# ------------------------------------------------------------------ 1c. las capas de cada objeto
def capa_taza(e, o):
    """Taza: aro de luz en el borde, nivel dentro, bandas que bajan envolviendo el cuerpo y el logo encendido."""
    img, mask, amm = recorte(o); c, u, v, (u0, u1, v0, v1), mk = forma(mask, amm, 16); cp = f'clip-path="url(#cp{o["_i"]})"'; out = ""; M = PM.get(e, _ident)
    a, b = (v1 - v0) / 2 * 0.86, (v1 - v0) / 2 * 0.52; rc = c + u * (u1 - b); boca = None
    if img is not None:   # la boca es lo oscuro/azul del extremo alto: su elipse real
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV); H, S_, V_ = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        boca, ar = mayor_blob(((V_ < 75) | ((H > 95) & (H < 135) & (S_ > 90) & (V_ < 175))) & (mk > 0))
        if boca is not None and ar > 0.1 * (mk > 0).sum():
            cs, _ = cv2.findContours(boca.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE); (ex, ey), (d1, d2), _ = cv2.fitEllipse(max(cs, key=len))
            rc = amm((ex, ey)); a, b = max(d1, d2) / 2 / E * 1.06, min(d1, d2) / 2 / E * 1.06
        else: boca = None
    largo = max(np.dot(rc - c, u) - u0 - b, 10)
    tope = f'<path d="{camino(M(arco(rc, a, b, v, u, 0, 2 * np.pi, 28)), True)}" fill="none" stroke="{LUZ}" stroke-width="{TRAZO}" pathLength="1" class="tz"/>'
    tope += f'<path d="{camino(M(arco(rc, a * 0.72, b * 0.72, v, u, 0, 2 * np.pi, 24)), True)}" fill="{ALERTA}" fill-opacity=".3" stroke="{ALERTA}" stroke-width="{FINO}" class="respira"/>'   # nivel
    bandas = "".join(f'<path d="{camino(M(arco(rc - u * largo * t, a, b, v, u)))}" fill="none" stroke="{PAPEL}" stroke-width="{TRAZO}" stroke-linecap="round" class="banda" style="animation-delay:-{3.6 - 0.45 * k:.2f}s"/>' for k, t in enumerate((0.22, 0.44, 0.66, 0.88)))
    out += f'<g {cp}>{bandas}</g>'
    if img is not None:   # logo: tinta oscura sobre el cuerpo claro, lejos del borde y de la boca
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY); cuerpo = erosion(mk, 9) > 0
        if boca is not None: cuerpo &= cv2.dilate(boca.astype(np.uint8), np.ones((int(8 * E), int(8 * E)), np.uint8)) == 0
        ys, xs = np.nonzero(cuerpo)
        if len(ys):
            cuerpo &= ((amm(np.stack([np.arange(mask.shape[1])[None, :].repeat(mask.shape[0], 0), np.arange(mask.shape[0])[:, None].repeat(mask.shape[1], 1)], -1)) - rc) @ u) < -b
            tinta = ((g < min(105, np.percentile(g[cuerpo], 50) - 45 if cuerpo.any() else 105)) & (hsv[..., 1] < 110) & cuerpo).astype(np.uint8)
            cs, _ = cv2.findContours(tinta, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE); cs = [k for k in cs if 6 < cv2.contourArea(k) < 0.05 * mask.size]
            if cs and sum(cv2.contourArea(k) for k in cs) > 40:
                letras = "".join(camino(M(amm(cv2.approxPolyDP(k, 0.8, True)[:, 0])), True) for k in cs); (qx, qy), (qw, qh), qa = cv2.minAreaRect(np.concatenate(cs)); caja = M(amm(cv2.boxPoints(((qx, qy), (qw + 6 * E, qh + 6 * E), qa))))
                lc = caja.mean(0); marcas = ""
                for k in range(4):   # cantoneras del logo (mismas marcas de corte del sistema)
                    p0, p1, p2 = caja[k], caja[(k + 1) % 4], caja[(k - 1) % 4]; marcas += camino([p0 + (p1 - p0) / np.linalg.norm(p1 - p0) * 5, p0, p0 + (p2 - p0) / np.linalg.norm(p2 - p0) * 5]) + " "
                r = np.linalg.norm(caja[0] - lc) + 6; DEFS.append(f'<clipPath id="lg{o["_i"]}"><path d="{letras}"/></clipPath>')
                out += (f'<path d="{letras}" fill="{LUZ}" fill-opacity=".5"/><g clip-path="url(#lg{o["_i"]})"><rect class="destello" x="{lc[0] - r - 14:.1f}" y="{lc[1] - r:.1f}" width="14" height="{2 * r:.1f}" fill="{LUZ}" style="--w:{2 * r + 14:.0f}px"/></g>'
                        f'<path d="{marcas}" fill="none" stroke="{LAPIZ}" stroke-width="{FINO}" stroke-linecap="square"/>')
    return out, tope


def capa_cilindro(e, o):
    """Vaso (o cualquier cilindro de pie): aro en el borde y nivel estimado por la oscuridad del contenido."""
    img, mask, amm = recorte(o); c, u, v, (u0, u1, v0, v1), mk = forma(mask, amm, 10); a = (v1 - v0) / 2 * 0.9; b = a * 0.55; L = u1 - u0; nivel = None; M = PM.get(e, _ident)
    if img is not None and L > 2.5 * b:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY); ys, xs = np.nonzero(erosion(mk, 5))
        if len(ys) > 50:
            pu = (amm(np.stack([xs, ys], 1)) - c) @ u; bins = np.clip(((pu - u0) / L * 12).astype(int), 0, 11); med = np.array([np.median(g[ys, xs][bins == k]) if (bins == k).any() else 255 for k in range(12)])
            osc = med < np.median(g[ys, xs]) - 14; n = 0
            while n < 12 and osc[n]: n += 1
            if n: nivel = min(n / 12 * L / max(L - 2 * b, 1), 0.95)
    tope = f'<path d="{camino(M(arco(c + u * (u1 - b), a, b, v, u, 0, 2 * np.pi, 28)), True)}" fill="none" stroke="{LUZ}" stroke-width="{TRAZO}" pathLength="1" class="tz"/>'; out = ""
    if nivel:
        cn = c + u * (u0 + b + nivel * (L - 2 * b)); out += f'<g clip-path="url(#cp{o["_i"]})"><path d="{camino(M(arco(cn, a, b, v, u, 0, 2 * np.pi, 28)), True)}" fill="{ALERTA}" fill-opacity=".22" stroke="{ALERTA}" stroke-width="{TRAZO}" class="respira"/></g>'
        ROT[e] = f"{e.rstrip(' 0123456789').capitalize()} · nivel ≈ {round(nivel * 20) * 5} %"
    return out, tope


def capa_lata(e, o):
    """Lata: calco de su propia gráfica. Se vectoriza la tinta oscura de la textura real y se proyecta encima como trazos."""
    img, mask, amm = recorte(o); M = PM.get(e, _ident)
    if img is None: return ""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY); dentro_m = erosion(mask, 6) > 0
    if not dentro_m.any(): return ""
    suave = cv2.GaussianBlur(g, (0, 0), 5 * E / 2.5 * 2); tinta = ((suave.astype(int) - g > 12) & (g < np.percentile(g[dentro_m], 55)) & dentro_m).astype(np.uint8)
    tinta = cv2.morphologyEx(tinta, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    cs, _ = cv2.findContours(tinta, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE); cs = sorted([k for k in cs if cv2.arcLength(k, True) > 9 * E], key=lambda k: -cv2.arcLength(k, True))[:70]
    out = ""; total = 0
    for n, k in enumerate(cs):
        q = cv2.approxPolyDP(k, 0.9, True)[:, 0]
        if len(q) < 3 or total + len(q) > 2600: continue
        total += len(q); out += f'<path d="{camino(M(amm(q)), True)}" pathLength="1" style="--i:{n}"/>'
    if not out: return ""
    return f'<g fill="{VERDE}" fill-opacity=".16" stroke="{VERDE}" stroke-width="1.6" stroke-linejoin="round" class="calco">{out}</g>'


def capa_mouse(e, o):
    """Mouse: cada parte con su zona de luz (botón izquierdo, botón derecho, rueda, palma), recortadas por la máscara."""
    img, mask, amm = recorte(o); c, u, v, (u0, u1, v0, v1), mk = forma(mask, amm, 22)   # apertura grande: descarta rebabas de luz pegadas a la máscara
    if u[1] < 0: u, v, u0, u1, v0, v1 = -u, -v, -u1, -u0, -v1, -v0   # el frente (botones, cable) mira al lado lejano a la persona
    f = u; rueda = (u0 + 0.5 * (u1 - u0), u0 + 0.88 * (u1 - u0), -4.5, 4.5)
    if img is not None:   # la rueda es la mancha oscura y alargada del frente; si nuestra propia luz la tapa, queda la proporción
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY); blob, ar = mayor_blob((g < 85) & (erosion(mk, 5) > 0))
        if blob is not None and ar > 12 * E * E:
            ys, xs = np.nonzero(blob); P = amm(np.stack([xs, ys], 1)); ps = (P - c) @ u; pt = (P - c) @ v; r = (np.percentile(ps, 3), np.percentile(ps, 97), np.percentile(pt, 5), np.percentile(pt, 95))
            if r[1] - r[0] > 2.2 * (r[3] - r[2]) and abs(pt.mean()) < 6 and ps.mean() > u0 + 0.4 * (u1 - u0): rueda = r
    r0, r1, t0, t1 = rueda; t0, t1 = min(t0, -3.5), max(t1, 3.5); parte = float(np.clip(r0 - 1, u0 + 0.3 * (u1 - u0), u0 + 0.62 * (u1 - u0))); ang = np.degrees(np.arctan2(f[1], f[0]))
    R = lambda s0, s1, a0, a1, extra: f'<rect x="{s0:.1f}" y="{a0:.1f}" width="{s1 - s0:.1f}" height="{a1 - a0:.1f}" {extra}/>'
    zonas = (R(u0 - 6, parte - 1.6, v0 - 6, v1 + 6, f'fill="{PAPEL}" fill-opacity=".1"')                                                    # palma
             + R(parte + 1.6, u1 + 6, v0 - 6, t0 - 2.2, f'fill="{LUZ}" class="clic" style="animation-delay:-2.2s"')                        # un botón
             + R(parte + 1.6, u1 + 6, t1 + 2.2, v1 + 6, f'fill="{LUZ}" class="clic"')                                                       # el otro
             + f'<line x1="{parte:.1f}" y1="{v0 - 6:.1f}" x2="{parte:.1f}" y2="{v1 + 6:.1f}" stroke="{PAPEL}" stroke-width="{FINO}"/>'
             + R(max(r0, parte + 3), min(r1, u1 - 2), t0, t1, f'rx="{(t1 - t0) / 2:.1f}" fill="{ALERTA}" fill-opacity=".4" stroke="{ALERTA}" stroke-width="{FINO}" class="respira"'))   # rueda
    return f'<g clip-path="url(#cp{o["_i"]})"><g transform="translate({c[0]:.1f},{c[1]:.1f}) rotate({ang:.1f})">{zonas}</g></g>'


def capa_teclado(e, o):
    """Teclado del MacBook: rejilla de teclas estimada desde la huella (entre el trackpad y la bisagra) con una ola de luz."""
    x0, y0, x1, y1 = LBC or bb(o); w, h = x1 - x0, y1 - y0; kx0, kx1, ky0, ky1 = x0 + 0.045 * w, x0 + 0.965 * w, y0 + 0.395 * h, y0 + 0.945 * h
    if KBR: kx0, ky0, kx1, ky1 = KBR   # el teclado real, detectado
    # filas desde la persona hacia la bisagra: anchos de tecla en unidades (distribución de MacBook, aproximada)
    filas = ([1, 1, 1, 1.25, 5, 1.25, 1, 1, 1, 1], [2.25] + [1] * 10 + [2.25], [1.75] + [1] * 11 + [1.75], [1.5] + [1] * 13, [1] * 13 + [1.5], [1] * 14)
    altos = [1, 1, 1, 1, 1, 0.55]; uh = (ky1 - ky0) / sum(altos); out = ""; y = ky0; hueco = 1.3
    for r, fila in enumerate(filas):
        ux = (kx1 - kx0) / sum(fila); x = kx1   # la izquierda de la persona es el x mayor
        for a in fila:
            kw = a * ux; d = (kx1 - x) / (kx1 - kx0) * 1.5 + r * 0.14
            out += f'<rect x="{x - kw + hueco / 2:.1f}" y="{y + hueco / 2:.1f}" width="{kw - hueco:.1f}" height="{altos[r] * uh - hueco:.1f}" rx="1.4" style="animation-delay:{d - 3.8:.2f}s"/>'; x -= kw
        y += altos[r] * uh
    return f'<g fill="{LUZ}" class="teclas" transform="{ROTC}">{out}</g>'


for e, o in O.items():
    base = e.split(' ')[0]; fn = {'lata': capa_lata, 'mouse': capa_mouse, 'vaso': capa_cilindro, 'computador': capa_teclado}.get(base)
    if fn is None or cv2 is None or (e != base and base == 'computador'): continue
    try:
        g = fn(e, o)
        if g and e in PM: g = f'<g clip-path="url(#cp{o["_i"]})">{"".join(g) if isinstance(g, tuple) else g}</g>'
        if g: pon('sobre', (bb(o)[0] + bb(o)[2]) / 2, sobre(e, g), 'capa', 0.3, e)
    except Exception as ex: AVISOS.append(f"capa de «{e}» omitida: {type(ex).__name__}: {ex}")


# ------------------------------------------------------------------ 1d. orbes: Puntos que giran en un Arco alrededor de latas y tazas
for e, o in O.items():
    base = e.split(' ')[0]
    if base != 'lata' or cv2 is None: continue
    b = bb(o); (cx, cy), (ow, oh), oa = cv2.minAreaRect(np.array(silueta(o), np.float32)); rx, ry = ow / 2 + 4, oh / 2 + 4; col = VERDE if base == 'lata' else LUZ; n = 3 if base == 'lata' else 2; dur = 5.2 if base == 'lata' else 7.5
    d = f"M{cx - rx:.1f},{cy:.1f} a{rx:.1f},{ry:.1f} 0 1,0 {2 * rx:.1f},0 a{rx:.1f},{ry:.1f} 0 1,0 {-2 * rx:.1f},0"
    g = f'<path id="ob{o["_i"]}" d="{d}" fill="none" stroke="{col}" stroke-opacity=".3" stroke-width="1.2" stroke-dasharray="2 9"/>'
    for k in range(n):
        giro_ = f'<animateMotion dur="{dur}s" begin="-{dur * k / n:.2f}s" repeatCount="indefinite"><mpath href="#ob{o["_i"]}"/></animateMotion>'
        g += f'<circle r="4.5" fill="{col}" fill-opacity=".4" filter="url(#gl)">{giro_}</circle><circle r="2" fill="{LUZ}">{giro_}</circle>'
    pon('contornos', cx, f'<g transform="rotate({oa:.1f} {cx:.1f} {cy:.1f})">{g}</g>', 'asoma', 0.5, e)

# ------------------------------------------------------------------ 1e. plátano: brillo amarillo y el logo de Platanus envuelto en su forma
AMARILLO = "#FFEC40"
DEFS.append('<filter id="gl2" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="7"/></filter>')


def aplana(d, pasos=6):
    """Path SVG (M L H V C Z absolutos) -> lista de polilíneas en coordenadas del logo."""
    tk = re.findall(r'[MLHVCZ]|-?\d*\.?\d+(?:e-?\d+)?', d); i = 0; subs = []; cur = None; cmd = None
    num = lambda: float(tk[i])
    while i < len(tk):
        if tk[i] in 'MLHVCZ': cmd = tk[i]; i += 1
        if cmd == 'Z': subs[-1].append(subs[-1][0]); cur = subs[-1][0]; cmd = None; continue
        if cmd is None: i += 1; continue
        if cmd == 'M': cur = (float(tk[i]), float(tk[i + 1])); i += 2; subs.append([cur]); cmd = 'L'
        elif cmd == 'L': cur = (float(tk[i]), float(tk[i + 1])); i += 2; subs[-1].append(cur)
        elif cmd == 'H': cur = (float(tk[i]), cur[1]); i += 1; subs[-1].append(cur)
        elif cmd == 'V': cur = (cur[0], float(tk[i])); i += 1; subs[-1].append(cur)
        elif cmd == 'C':
            p0 = np.array(cur); p1, p2, p3 = (np.array([float(tk[i + 2 * j]), float(tk[i + 2 * j + 1])]) for j in range(3)); i += 6
            for t in np.linspace(0, 1, pasos + 1)[1:]: subs[-1].append(tuple((1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t * t * p2 + t ** 3 * p3))
            cur = tuple(p3)
    return subs


for e, o in O.items():
    if e.split(' ')[0] != 'plátano' or cv2 is None: continue
    try:
        Mp = (lambda x, pl=o['poli_proy']: __import__('altura').aplica_poli(pl, x)) if 'poli_proy' in o else PM.get(e, _ident)
        q = np.array(o['poligono_mm'], np.float32); bx0, by0 = q.min(0) - 2; mk = np.zeros((int(q[:, 1].max() - by0) + 3, int(q[:, 0].max() - bx0) + 3), np.uint8); cv2.fillPoly(mk, [np.round(q - [bx0, by0]).astype(np.int32)], 1)
        ys, xs = np.nonzero(mk); P = np.stack([xs, ys], 1) + [bx0, by0]; cm = P.mean(0); _, Vv = np.linalg.eigh(np.cov((P - cm).T)); u = Vv[:, 1]
        if u[0] > 0: u = -u                       # sentido de lectura de la persona: de x mayor a x menor
        v = np.array([-u[1], u[0]]); pu, pv = (P - cm) @ u, (P - cm) @ v; lo, hi = np.percentile(pu, [5, 95]); bu, bv, gr = [], [], []
        for k in range(14):                        # línea media del plátano, por tramos a lo largo de su eje
            sel = (pu >= lo + (hi - lo) * k / 14) & (pu < lo + (hi - lo) * (k + 1) / 14)
            if sel.sum() > 8: bu.append(pu[sel].mean()); bv.append(np.median(pv[sel])); gr.append(np.percentile(pv[sel], 95) - np.percentile(pv[sel], 5))
        coef = np.polyfit(bu, bv, 2); grosor = float(np.median(gr)); kl = min((hi - lo) * 0.9 / 576, grosor * 0.62 / 112)

        def envuelve(pts):   # logo (px) -> sobre la línea media curva -> corrección medida del proyector
            pts = np.asarray(pts, float); uu = (pts[:, 0] - 288) * kl + (lo + hi) / 2; nn = (pts[:, 1] - 56) * kl; dv = np.polyval(np.polyder(coef), uu); nr = np.sqrt(1 + dv * dv)
            pu_ = uu - nn * dv / nr; pv_ = np.polyval(coef, uu) + nn / nr
            return Mp(cm + np.outer(pu_, u) + np.outer(pv_, v))
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in Mp(adentro(q, 1.5)))
        g = (f'<polygon points="{pts}" fill="{AMARILLO}" fill-opacity=".5" stroke="{AMARILLO}" stroke-width="9" stroke-linejoin="round" filter="url(#gl2)" class="glowp"/>'
             f'<polygon points="{pts}" fill="none" stroke="{AMARILLO}" stroke-width="{FINO}" stroke-linejoin="round" class="glowl"/>')
        logo = Path(__file__).parent / 'platanus-logo.svg'
        if logo.exists():
            for dpath, fill in re.findall(r'<path d="([^"]+)"[^>]*?fill="([^"]+)"', logo.read_text()):
                dd = "".join(camino(envuelve(sub), False) + "Z" for sub in aplana(dpath) if len(sub) > 2)
                g += f'<path d="{dd}" fill="{LUZ if fill.lower() == "white" else fill}" fill-rule="evenodd"/>'
        pon('sobre', float(cm[0]), g, 'asoma', 0.3, e)
    except Exception as ex: AVISOS.append(f"plátano omitido: {type(ex).__name__}: {ex}")

# ------------------------------------------------------------------ 2. computador: botones en los reposamanos y la pregunta
if 'computador' in O:
    x0, y0, x1, y1 = LBC; tx0, ty0, tx1, ty1 = TRACK; cxc = (x0 + x1) / 2
    for ax, bx, lab, pr, lleno in ((tx1 + 8, x1 - 8, "Aprobar", "aprobar", True), (x0 + 8, tx0 - 8, "Rechazar", "rechazar", False)):
        aw, ah = bx - ax, ty1 - ty0 - 8; mx, my = (ax + bx) / 2, (ty0 + ty1) / 2; fs = min(16, (aw - 12) / (len(lab) * 0.54))
        # Aprobar es la acción principal: placa de luz con la palabra calada (lo negro no se proyecta). Rechazar, solo contorno.
        g = (f'<rect x="{ax:.0f}" y="{ty0 + 4:.0f}" width="{aw:.0f}" height="{ah:.0f}" rx="{RADIO}" fill="{PAPEL if lleno else "#000"}" stroke="{PAPEL}" stroke-width="{TRAZO}"/>'
             + txt(mx, my + 3 - 0.3 * fs, lab, fs, "#000" if lleno else LUZ, "s", "middle", 700)
             + f'<rect x="{mx - aw * 0.28:.0f}" y="{my - 0.3 * fs - 9:.0f}" width="{aw * 0.56:.0f}" height="{TRAZO}" fill="{LAPIZ}"/>')   # raya de lápiz bajo la palabra
        pon('botones', mx, f'<g transform="{ROTC}">{g}</g>', 'crece', 0.1, 'computador')
        ZONAS.append({"id": pr, "poly": gira([(ax, ty0 + 4), (bx, ty0 + 4), (bx, ty0 + 4 + ah), (ax, ty0 + 4 + ah)])})
    x0, y0, x1, y1 = bb(O['computador']); cxc = (x0 + x1) / 2
    # La pregunta, hacia la persona. Regla de Pablo (movimiento-texto-pista): entra el trazo, el texto surge de él,
    # el trazo se retrae y queda como guion corto anclado al inicio del texto.
    TIT = {"pregunta": "El PR 482 espera tu OK", "aprobado": "Listo, lo mergeo ahora", "rechazado": "Ok, lo dejo en espera"}
    META, PISTA = "Revisor · hace 4 min", "Mantén el dedo en Aprobar o Rechazar"
    GUION, hecho = 16, False
    for esc in (1.0, 0.86, 0.74):
        st, sm = INSTR * esc, max(CONF * esc, 10.5)
        wt = max(max(ancho(t, st) for t in TIT.values()), ancho(META, sm), ancho(PISTA, sm)); w = wt + GUION + 8; h = sm + 7 + st + 8 + sm + 2
        cand = [(cxc + dx - w / 2, y0 - 14 - dy - h, cxc + dx + w / 2, y0 - 14 - dy) for dy in (0, 10, 22) for dx in sorted(range(-260, 261, 5), key=abs)]
        c = colocar(cand)
        if not c: continue
        izq, top = c[2] - GUION - 8, c[3]; yt = top - sm - 7; ymid = yt - 0.5 * st; va = vivo('computador'); d0 = retardo((c[0] + c[2]) / 2) if ANIMAR else 0.45
        pon('textos', (c[0] + c[2]) / 2, renglon(izq, top, META, sm, PAPEL2, "s", "izq"), 'sube', 0, 'computador')
        k = GUION / w
        CAPAS['rutas'].append(f'<rect class="guion{" go" if va else ""}" style="--d:{d0:.2f}s;--k:{k:.3f}" x="{c[0]:.1f}" y="{ymid - TRAZO / 2:.1f}" width="{w:.1f}" height="{TRAZO}" fill="{LAPIZ}"/>')
        if EST == 'pregunta':
            CAPAS['textos'].append(renglon(izq, yt, TIT[EST], st, LUZ, "s surge" if va else "s", "izq", 700, f'style="--d:{d0 + T_ENTRA:.2f}s"'))
            pon('textos', (c[0] + c[2]) / 2, renglon(izq, yt - st - 8, PISTA, sm, PAPEL, "s", "izq", 600), 'sube', T_FORMA, 'computador')
        else:   # la respuesta del agente: escrita a mano con tinta azul (pieza propia de la escena de Max)
            CAPAS['textos'].append(renglon(izq, yt + 2, TIT[EST], st * 1.12, TINTA, "h escribe", "izq", 700))
        if esc < 1: AVISOS.append(f"pregunta achicada a {esc:.0%} para no pisar objetos")
        hecho = True; break
    if not hecho: AVISOS.append("omitido por falta de espacio: pregunta frente al computador")

# ------------------------------------------------------------------ 3. mouse: menú radial en los ángulos libres
if 'mouse' in O:
    b = bb(O['mouse']); c = np.array([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]); a0, b0 = (b[2] - b[0]) / 2 + 6, (b[3] - b[1]) / 2 + 6
    ACC = (("Copiar", "copiar"), ("Pegar", "pegar"), ("Deshacer", "deshacer"), ("Captura", "captura"))
    bw, bh = ancho("Deshacer", CONF) + 18, 30; mejor = []
    # ángulos del lado de la persona primero (-90°), luego los costados; el lado lejano (cable del mouse) al final
    giro = lambda a: abs((a + 90 + 180) % 360 - 180)
    angs = sorted(range(-180, 180, 5), key=lambda a: (giro(a) > 115, giro(a)))
    for R in (10, 22, 36):
        base = len(cajas); el = []
        for ang in angs:
            u = np.array([np.cos(np.radians(ang)), np.sin(np.radians(ang))]); p = c + u * np.array([a0 + R + bw / 2, b0 + R + bh / 2])
            if colocar([(p[0] - bw / 2, p[1] - bh / 2, p[0] + bw / 2, p[1] + bh / 2)]): el.append((p, u))
            if len(el) == len(ACC): break
        if len(el) > len(mejor): mejor = el
        del cajas[base:]
        if len(mejor) == len(ACC): break
    mejor.sort(key=lambda e: -e[0][0])   # de izquierda a derecha para la persona
    if len(mejor) < len(ACC): AVISOS.append(f"menú del mouse: solo caben {len(mejor)} de {len(ACC)} acciones ({', '.join(l for l, _ in ACC[len(mejor):])} omitidas)")
    for (p, u), (lab, pr) in zip(mejor, ACC):
        cajas.append((p[0] - bw / 2, p[1] - bh / 2, p[0] + bw / 2, p[1] + bh / 2)); q = c + u * np.array([a0, b0])
        pon('rutas', p[0], f'<line x1="{q[0]:.0f}" y1="{q[1]:.0f}" x2="{p[0]:.0f}" y2="{p[1]:.0f}" stroke="{TINTA}" stroke-width="{FINO}"/>', 'asoma', 0, 'mouse')
        pon('botones', p[0], f'<g data-press="{pr}"><rect x="{p[0] - bw / 2:.0f}" y="{p[1] - bh / 2:.0f}" width="{bw:.0f}" height="{bh}" rx="{RADIO}" fill="#000" stroke="{TINTA}" stroke-width="{FINO + 0.6}"/>{txt(p[0], p[1] - 0.3 * CONF + 1, lab, CONF, LUZ)}</g>', 'crece', 0.08, 'mouse')

# ------------------------------------------------------------------ 4. teléfono → cargador: aviso y recorrido ortogonal
def ruta(a, b, occ, paso=10, radio=14):
    """Recorrido ortogonal (solo giros de 90° redondeados) por las celdas libres. Devuelve (path, esquinas) o None."""
    H, W = occ.shape; occ = occ.copy()
    cel = lambda p: (int(np.clip(p[1] // paso, 0, H - 1)), int(np.clip(p[0] // paso, 0, W - 1))); s, g = cel(a), cel(b)
    for r in (s, g): occ[max(r[0] - 1, 0):r[0] + 2, max(r[1] - 1, 0):r[1] + 2] = False   # los extremos nacen pegados a su objeto
    best = {(s, -1): 0}; q = [(0, 0, s, -1)]; prev = {}; fin = None
    D = ((0, 1), (1, 0), (0, -1), (-1, 0))
    while q:
        f, c, n, d = heapq.heappop(q)
        if n == g: fin = (n, d); break
        if best.get((n, d), 1e9) < c: continue
        for k, (dy, dx) in enumerate(D):
            m = (n[0] + dy, n[1] + dx)
            if not (0 <= m[0] < H and 0 <= m[1] < W) or occ[m]: continue
            nc = c + 1 + (6 if d not in (-1, k) else 0)   # cada giro cuesta: pocas esquinas
            if nc < best.get((m, k), 1e9):
                best[(m, k)] = nc; prev[(m, k)] = (n, d); heapq.heappush(q, (nc + abs(m[0] - g[0]) + abs(m[1] - g[1]), nc, m, k))
    if fin is None: return None
    cam = []; cur = fin
    while True:
        cam.append(cur[0])
        if cur[0] == s or cur not in prev: break
        cur = prev[cur]
    cam = [np.array([c[1] * paso + paso / 2, c[0] * paso + paso / 2]) for c in cam[::-1]]; cam[0], cam[-1] = np.array(a, float), np.array(b, float)
    if len(cam) > 2:   # alinear los extremos con el primer y último tramo
        cam[1] = np.array([cam[0][0], cam[1][1]]) if abs(cam[1][0] - cam[0][0]) < paso else np.array([cam[1][0], cam[0][1]])
        cam[-2] = np.array([cam[-1][0], cam[-2][1]]) if abs(cam[-2][0] - cam[-1][0]) < paso else np.array([cam[-2][0], cam[-1][1]])
    esq = [cam[0]] + [cam[i] for i in range(1, len(cam) - 1) if abs((cam[i] - cam[i - 1])[0] * (cam[i + 1] - cam[i])[1] - (cam[i] - cam[i - 1])[1] * (cam[i + 1] - cam[i])[0]) > 1e-6] + [cam[-1]]
    d = f"M{esq[0][0]:.0f},{esq[0][1]:.0f}"
    for i in range(1, len(esq) - 1):
        u = (esq[i] - esq[i - 1]); v = (esq[i + 1] - esq[i]); r = min(radio, np.linalg.norm(u) / 2, np.linalg.norm(v) / 2); p = esq[i] - u / np.linalg.norm(u) * r; q2 = esq[i] + v / np.linalg.norm(v) * r
        d += f" L{p[0]:.0f},{p[1]:.0f} Q{esq[i][0]:.0f},{esq[i][1]:.0f} {q2[0]:.0f},{q2[1]:.0f}"
    return d + f" L{esq[-1][0]:.0f},{esq[-1][1]:.0f}", esq


if 'teléfono' in O:
    bt = bb(O['teléfono']); rotulo(bt, "Batería 23 % · conéctalo al cargador", (CONF, ETIQ), ALERTA, cls="s titila-aviso", que="aviso de batería", de='teléfono')
    if 'cargador' in O:
        bc = bb(O['cargador']); halo = crece(bc, 12); cajas.append(halo)
        pon('contornos', (bc[0] + bc[2]) / 2, f'<rect x="{halo[0]:.0f}" y="{halo[1]:.0f}" width="{halo[2] - halo[0]:.0f}" height="{halo[3] - halo[1]:.0f}" rx="12" fill="none" stroke="{ALERTA}" stroke-width="{TRAZO}" class="respira"/>', 'asoma', 0, ('teléfono', 'cargador'))
        P = crece(bt, 10); cc = ((bc[0] + bc[2]) / 2, (bc[1] + bc[3]) / 2)
        ct = ((bt[0] + bt[2]) / 2, (bt[1] + bt[3]) / 2); pega = lambda v: (v // 10) * 10 + 5
        # sale del medio del lado del teléfono que mira al cargador y entra al halo de frente (una sola esquina si se puede)
        if abs(cc[0] - ct[0]) > abs(cc[1] - ct[1]):
            a = np.array([P[0] if cc[0] < ct[0] else P[2], pega(ct[1])])
            e = np.array([halo[2] if a[0] > halo[2] else halo[0], a[1]]) if halo[1] + 8 <= a[1] <= halo[3] - 8 else np.array([pega(cc[0]), halo[3] if a[1] > halo[3] else halo[1]])
        else:
            a = np.array([pega(ct[0]), P[1] if cc[1] < ct[1] else P[3]])
            e = np.array([a[0], halo[3] if a[1] > halo[3] else halo[1]]) if halo[0] + 8 <= a[0] <= halo[2] - 8 else np.array([halo[2] if a[0] > halo[2] else halo[0], pega(cc[1])])
        r = ruta(a, e, rejilla(10, 12, 8))
        if r:
            d, esq = r
            for p, q in zip(esq[:-1], esq[1:]): cajas.append((min(p[0], q[0]) - 5, min(p[1], q[1]) - 5, max(p[0], q[0]) + 5, max(p[1], q[1]) + 5))
            pon('rutas', (a[0] + e[0]) / 2, f'<path d="{d}" fill="none" stroke="{ALERTA}" stroke-width="4.2" stroke-dasharray="0.1 13" stroke-linecap="round" stroke-linejoin="round" class="flujo"/>', 'asoma', 0.15, ('teléfono', 'cargador'))
        else: AVISOS.append("omitido: recorrido teléfono → cargador (no hay paso libre entre objetos y textos)")

# ------------------------------------------------------------------ 5. panel «Tus agentes» (datos de escenas/escritorio/agentes.json de Max)
AG = [("api-cobros", "escribe los tests de /v1/cobros", "12 min", "trabajando"), ("revisor", "el PR 482 espera tu OK", "4 min", "esperando"), ("migración", "copió 1,2 M filas a pagos_v2", "08:52", "listo")]
ancla = np.array([(bb(O['computador'])[0] + bb(O['computador'])[2]) / 2, bb(O['computador'])[1]]) if 'computador' in O else np.array([660.0, 300.0])
def parte(t, n):
    """Corta t en renglones de a lo más n letras, por palabras."""
    out = [""]
    for p in t.split():
        if out[-1] and len(out[-1]) + 1 + len(p) > n: out.append(p)
        else: out[-1] = (out[-1] + " " + p).strip()
    return out


panel = None; CAB = TRAZO + 10 + 20 + 14
# variantes, de más a menos: detalle en un renglón, detalle en dos renglones (panel angosto y alto), solo nombres
for pw, nl in ((max(ancho(d, 12) for _, d, _, _ in AG) + 46, 1), (208, 2), (232, 0)):
    nmax = int((pw - 44) / (0.54 * 12)); DET = [parte(d, nmax)[:nl] for _, d, _, _ in AG]; ALT = [20 + 16 * len(dl) + 10 for dl in DET]; ph = CAB + sum(ALT) + 2
    cand = [(x, y, x + pw, y + ph) for y in range(10, int(690 - ph), 10) for x in range(10, int(1320 - pw), 10)]
    cand.sort(key=lambda b: abs((b[0] + b[2]) / 2 - ancla[0]) + 1.4 * abs((b[1] + b[3]) / 2 - ancla[1]))
    panel = next((b for b in cand if not choca(crece(b, 8))), None)
    if panel: cajas.append(panel); break
if panel:
    x, y, x2, y2 = panel; cxp = (x + x2) / 2; izq, der = x2 - 12, x + 12; yt = y2 - TRAZO - 10
    pon('paneles', cxp, f'<rect x="{x:.0f}" y="{y:.0f}" width="{x2 - x:.0f}" height="{y2 - y:.0f}" rx="{RADIO}" fill="{LUZ}" fill-opacity=".07"/>', 'asoma')
    pon('paneles', cxp, f'<rect x="{x:.0f}" y="{y2 - TRAZO:.0f}" width="{x2 - x:.0f}" height="{TRAZO}" fill="{PAPEL}" class="regla"/>', 'asoma')
    pon('textos', cxp, renglon(izq, yt, "Tus agentes", 20, LUZ), 'sube', 0.1)
    for k, (n, d, t, est) in enumerate(AG):
        top = y2 - CAB - sum(ALT[:k]); mx, my = izq - 5, top - 7.5
        mk = {"trabajando": f'<rect x="{mx - 2:.1f}" y="{top - 14:.1f}" width="4.5" height="14" fill="{LUZ}" class="titila"/>',
              "esperando": txt(mx, top - 0.76 * 15, "?", 15, LAPIZ, "s", "middle", 700),
              "listo": f'<path d="M-5,0 L-1.5,4 L5,-4" transform="translate({mx:.1f},{my:.1f}) rotate(180)" fill="none" stroke="{PAPEL2}" stroke-width="{TRAZO}" stroke-linecap="round" stroke-linejoin="round"/>'}[est]
        fila = mk + renglon(izq - 20, top, n, 15, PAPEL2 if est == "listo" else LUZ) + renglon(der, top - 2, t, ETIQ, PAPEL2, "m", "der")
        for j, dl in enumerate(DET[k]): fila += renglon(izq - 20, top - 20 - 16 * j, dl, 12, PAPEL if est == "esperando" else PAPEL2, "s", "izq", 600)
        pon('textos', cxp, fila, 'sube', 0.2 + 0.08 * k)
    if 'computador' in O:   # una línea viva sale del computador y llega al panel: de ahí vienen los agentes
        try:
            cb = crece(bb(O['computador']), 10); ccx, ccy = (cb[0] + cb[2]) / 2, (cb[1] + cb[3]) / 2; pcx_, pcy_ = (x + x2) / 2, (y + y2) / 2; pg = lambda v: (v // 10) * 10 + 5
            if abs(pcx_ - ccx) > abs(pcy_ - ccy): a_ = np.array([cb[0] if pcx_ < ccx else cb[2], pg(ccy)]); e_ = np.array([x2 if pcx_ < ccx else x, pg(pcy_)])
            else: a_ = np.array([pg(ccx), cb[1] if pcy_ < ccy else cb[3]]); e_ = np.array([pg(pcx_), y2 if pcy_ < ccy else y])
            r_ = ruta(a_, e_, rejilla(10, 12, 8)) or ruta(a_, e_, rejilla(10, 5, 2)) or ruta(a_, e_, rejilla(10, -4, -6))
            if r_:
                d_, esq_ = r_
                for p_, q_ in zip(esq_[:-1], esq_[1:]): cajas.append((min(p_[0], q_[0]) - 5, min(p_[1], q_[1]) - 5, max(p_[0], q_[0]) + 5, max(p_[1], q_[1]) + 5))
                pon('rutas', (a_[0] + e_[0]) / 2, f'<path d="{d_}" fill="none" stroke="{PAPEL2}" stroke-width="{FINO}" stroke-linejoin="round"/><path d="{d_}" fill="none" stroke="{LUZ}" stroke-width="4.2" stroke-dasharray="0.1 13" stroke-linecap="round" stroke-linejoin="round" class="flujo"/>'
                    + "".join(f'<circle cx="{q[0]:.0f}" cy="{q[1]:.0f}" r="11" fill="{LUZ}" fill-opacity=".3" filter="url(#gl)" class="respira"/><circle cx="{q[0]:.0f}" cy="{q[1]:.0f}" r="5" fill="{LUZ}"/>' for q in (a_, e_)), 'asoma', 0.3)
            else: AVISOS.append("omitido: línea computador → panel de agentes (sin paso libre)")
        except Exception as ex: AVISOS.append(f"línea computador → panel omitida: {type(ex).__name__}: {ex}")
    if nl == 0: AVISOS.append("panel «Tus agentes» en versión compacta (sin detalle) por falta de espacio")
    elif nl == 2: AVISOS.append("panel «Tus agentes» angosto (detalle en dos renglones) por falta de espacio")
else:
    AVISOS.append("omitido por falta de espacio: panel «Tus agentes»")

# ------------------------------------------------------------------ 6. rótulos de objetos (lo último en elegir lugar: ceden ante todo lo demás)
for e, o in O.items():
    if e in SIN_ROTULO or e in ('computador', 'mouse', 'teléfono') or e in REC: continue   # esos ya llevan su propio componente
    nombre = e.rstrip(' 0123456789'); con_halo = e == 'cargador' and 'teléfono' in O   # el cargador lleva halo: el rótulo va por fuera
    rotulo(crece(bb(o), 12) if con_halo else dict(OBST)[e], ROT.get(e, nombre[0].upper() + nombre[1:]), (ETIQ,), PAPEL2, que=f"rótulo «{e}»", g=8 if con_halo else 14, de=e)
# ------------------------------------------------------------------ 7. espacios libres: lo que queda, al fondo
G = 30; occ = rejilla(G, 14, 10)


def mayor(occ):
    best = (0, None); h, w = occ.shape; alt = np.zeros(w, int)
    for r in range(h):
        alt = np.where(occ[r], 0, alt + 1)
        for c in range(w):
            if alt[c] == 0: continue
            mn = alt[c]
            for c2 in range(c, w):
                if alt[c2] == 0: break
                mn = min(mn, alt[c2]); a = mn * (c2 - c + 1)
                if a > best[0] and mn >= 3 and c2 - c + 1 >= 5: best = (a, (c, r - mn + 1, c2, r))
    return best


for _ in range(2):
    a, r = mayor(occ)
    if not r or a < 24: break
    c0, r0, c1, r1 = r; occ[max(r0 - 1, 0):r1 + 2, max(c0 - 1, 0):c1 + 2] = True; b = (c0 * G + 6, r0 * G + 6, (c1 + 1) * G - 6, (r1 + 1) * G - 6)
    if choca(b, 4, 4): continue
    if not any(z['id'] == 'voz' for z in ZONAS) and b[2] - b[0] >= 95 and b[3] - b[1] >= 95:
        cajas.append(b); vx, vy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2; kv = min((b[2] - b[0]) / 150, (b[3] - b[1]) / 215, 1.0)
        dedos = ''.join(f'<rect x="{dx}" y="{dy}" width="19" height="{dh}" rx="9.5"/>' for dx, dy, dh in ((-44, -78, 70), (-21, -92, 84), (2, -86, 78), (25, -68, 60)))
        mano_ = (f'<g transform="translate({vx:.1f},{vy + 6:.1f}) rotate(180) scale({kv:.2f})" fill="none" stroke="{PAPEL}" stroke-width="{TRAZO}" stroke-linejoin="round" class="respira">'
                 f'{dedos}<rect x="-46" y="-12" width="92" height="84" rx="26"/><rect x="-70" y="-4" width="22" height="56" rx="11" transform="rotate(-28 -59 24)"/></g>')
        pon('libres', vx, mano_ + txt(vx, vy - 96 * kv, 'Posa tu mano · nota de voz', ETIQ, PAPEL2), 'asoma', 0.2)
        ZONAS.append({'id': 'voz', 'poly': [[vx - 60 * kv, vy - 80 * kv], [vx + 60 * kv, vy - 80 * kv], [vx + 60 * kv, vy + 100 * kv], [vx - 60 * kv, vy + 100 * kv]], 'c': [vx, vy]}); continue
    cajas.append(b); LIBRES.append(b)

# ------------------------------------------------------------------ barrido de arranque + estilos
haz = ""
if ANIMAR:   # un haz de luz cruza la mesa (de la izquierda de la persona a su derecha) y cada cosa aparece a su paso
    haz = (f'<defs><linearGradient id="hz" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="{LUZ}" stop-opacity=".95"/><stop offset=".07" stop-color="{LUZ}" stop-opacity=".95"/>'
           f'<stop offset=".09" stop-color="{PAPEL}" stop-opacity=".34"/><stop offset="1" stop-color="{PAPEL}" stop-opacity="0"/></linearGradient></defs>'
           f'<rect class="haz" x="0" y="-10" width="150" height="710" fill="url(#hz)"/>')
fuente = marca.fuente() + marca.CSS_FIRMA
if ANIMAR and os.environ.get('ESPERA'):
    fuente += "#wui{animation:firma-inicial .85s step-end both}@keyframes firma-inicial{from{opacity:0}to{opacity:1}}"
css = fuente + ".titila-aviso{animation:tav 1.1s ease-in-out infinite}@keyframes tav{0%,100%{opacity:1}50%{opacity:.25}}" + f"""html,body{{margin:0;background:transparent;overflow:hidden}}#w{{position:absolute;left:0;top:0;width:1400px;height:720px;transform-origin:0 0;transform:{M3D}}}
svg{{position:absolute;left:-10px;top:-10px;width:1340px;height:710px}}
text{{font-kerning:normal;text-rendering:geometricPrecision}}
.s{{font-family:'WUI Sans','Instrument Sans','Helvetica Neue',Arial,sans-serif}}.m{{font-family:'Menlo','Consolas',ui-monospace,monospace;font-variant-numeric:tabular-nums}}.h{{font-family:'WUI Sans','Instrument Sans',Arial,sans-serif;font-weight:500}}
.titila{{animation:ti 1s steps(1) infinite}}@keyframes ti{{50%{{opacity:0}}}}
.escribe{{animation:es 1.4s {C_ESCRIBE} both}}@keyframes es{{from{{clip-path:inset(-20% 100% -20% 0)}}to{{clip-path:inset(-20% -2% -20% 0)}}}}
.onda{{transform-box:view-box;animation:on 2.4s ease-out infinite}}@keyframes on{{0%{{transform:scale(1);opacity:.9}}100%{{transform:scale(1.35);opacity:0}}}}
.baila{{transform-box:view-box;animation:ba 1.1s ease-in-out infinite}}@keyframes ba{{0%,100%{{fill-opacity:.12;stroke-opacity:.7}}50%{{fill-opacity:.4;stroke-opacity:1}}}}
.chispa{{transform-box:fill-box;transform-origin:50% 50%;animation:ch 2.2s ease-in-out infinite}}@keyframes ch{{0%,100%{{opacity:0;scale:.3}}50%{{opacity:1;scale:1.2}}}}
.glowp{{animation:gp 2.4s ease-in-out infinite}}@keyframes gp{{0%,100%{{opacity:.3}}50%{{opacity:1}}}}.glowl{{animation:gq 2.4s ease-in-out infinite}}@keyframes gq{{0%,100%{{stroke-opacity:.55}}50%{{stroke-opacity:1}}}}
.wm-trazo{{transform-box:fill-box;transform-origin:100% 50%;animation:wmt 14s linear infinite}}@keyframes wmt{{0%{{transform:scaleX(0);opacity:1;animation-timing-function:cubic-bezier(.33,0,.67,1)}}3%{{transform:scaleX(1) translateY(0)}}4.1%{{transform:scaleX(1) translateY(-2.2px)}}5.2%{{transform:scaleX(1) translateY(2.2px)}}6.3%{{transform:scaleX(1) translateY(0);opacity:1}}9.1%,100%{{transform:scaleX(1);opacity:0}}}}.wm-letras{{transform-box:fill-box;transform-origin:50% 50%;animation:wml 14s linear infinite}}@keyframes wml{{0%,6.3%{{clip-path:inset(47% 0 47% 0);opacity:0}}6.4%{{clip-path:inset(47% 0 47% 0);opacity:1;transform:scale(1.035);animation-timing-function:cubic-bezier(.16,1,.3,1)}}9.1%{{clip-path:inset(-4% -2% -4% -2%);transform:scale(1.035);animation-timing-function:cubic-bezier(.16,1,.3,1)}}12.3%,100%{{clip-path:inset(-4% -2% -4% -2%);opacity:1;transform:scale(1)}}}}
.flujo{{animation:fl 1.6s linear infinite}}@keyframes fl{{to{{stroke-dashoffset:-26.2}}}}
.respira{{animation:re 2.86s ease-in-out infinite}}@keyframes re{{50%{{opacity:.55}}}}
.guion{{transform-box:fill-box;transform-origin:100% 50%;transform:scaleX(var(--k))}}
.aura{{animation:au 3.4s ease-in-out infinite}}@keyframes au{{50%{{stroke-opacity:.16}}}}
.corre{{animation:co 7s linear infinite}}@keyframes co{{to{{stroke-dashoffset:-1}}}}
.banda{{opacity:.14;animation:bn 3.6s ease-in-out infinite}}@keyframes bn{{0%,30%,100%{{opacity:.14}}12%{{opacity:.95}}}}
.destello{{animation:ds 3.4s cubic-bezier(.33,0,.67,1) infinite}}@keyframes ds{{0%,55%{{transform:translateX(0)}}100%{{transform:translateX(var(--w))}}}}
.rayo{{animation:ry 1.8s ease-in-out infinite}}@keyframes ry{{0%,100%{{opacity:.55}}45%{{opacity:1}}}}
.seg{{fill-opacity:.16;animation:sg5 2.6s ease-in-out infinite}}@keyframes sg5{{0%{{fill-opacity:.16}}14%,78%{{fill-opacity:1}}92%,100%{{fill-opacity:.16}}}}
.clic{{fill-opacity:.14;animation:cl 4.4s ease-in-out infinite}}@keyframes cl{{0%,60%,100%{{fill-opacity:.14}}78%{{fill-opacity:.42}}}}
.teclas rect{{opacity:.1;animation:tk 3.8s ease-in-out infinite}}@keyframes tk{{0%,72%,100%{{opacity:.1}}84%{{opacity:.62}}}}
.b{{transform-box:fill-box;transform-origin:50% 50%}}
.b.sube{{animation:su {T_ENTRA}s {C_TINTA} var(--d) both}}@keyframes su{{from{{opacity:0;transform:translateY(-9px)}}}}
.b.crece{{animation:cr {T_ENTRA}s {C_TINTA} var(--d) both}}@keyframes cr{{from{{opacity:0;transform:scale(.86)}}}}
.b.asoma,.b.capa{{animation:as {T_ONDULA}s {C_TINTA} var(--d) both}}@keyframes as{{from{{opacity:0}}}}
.b .tz{{stroke-dasharray:1;animation:tz {T_ONDULA}s {C_LLEGA} var(--d) both}}@keyframes tz{{0%{{stroke-dashoffset:1;opacity:0}}12%{{opacity:1}}100%{{stroke-dashoffset:0}}}}
.b .tras{{animation:as {T_ONDULA}s {C_TINTA} calc(var(--d) + .5s) both}}
.b .calco path{{stroke-dasharray:1;animation:ca {T_FORMA}s {C_ESCRIBE} calc(var(--d) + var(--i) * 28ms) both,ig 2.8s ease-in-out calc(var(--d) + 1.9s + var(--i) * 45ms) infinite}}@keyframes ca{{0%{{stroke-dashoffset:1;fill-opacity:0}}70%{{stroke-dashoffset:0;fill-opacity:0}}100%{{stroke-dashoffset:0}}}}
.calco path{{animation:ig 2.8s ease-in-out calc(1.9s + var(--i) * 45ms) infinite}}@keyframes ig{{0%,62%,100%{{fill-opacity:.14;stroke-opacity:.55}}18%{{fill-opacity:.75;stroke-opacity:1}}24%{{fill-opacity:.3}}30%{{fill-opacity:.7}}}}
.b .regla{{transform-box:fill-box;transform-origin:100% 50%;animation:rg {T_ENTRA}s {C_LLEGA} var(--d) both}}@keyframes rg{{from{{transform:scaleX(0)}}}}
.guion.go{{animation:gu {T_ASIENTA}s linear var(--d) both}}
@keyframes gu{{0%{{transform:scaleX(0);animation-timing-function:cubic-bezier(.33,0,.67,1)}}24%{{transform:scaleX(1)}}74%{{transform:scaleX(1);animation-timing-function:{C_TINTA}}}100%{{transform:scaleX(var(--k))}}}}
.surge{{animation:sg {T_FORMA - T_ENTRA:.2f}s {C_TINTA} var(--d) both}}@keyframes sg{{from{{clip-path:inset(-20% 100% -20% 0);opacity:.2}}to{{clip-path:inset(-20% -2% -20% 0);opacity:1}}}}
.scan{{opacity:0;animation:sn .95s cubic-bezier(.33,0,.67,1) .05s both}}@keyframes sn{{0%{{transform:translateX(var(--w));opacity:1}}88%{{opacity:1}}100%{{transform:translateX(0);opacity:0}}}}
.haz{{opacity:0;animation:hz {T_ASIENTA}s linear both}}@keyframes hz{{0%{{transform:translateX({X_INI:.0f}px);opacity:1}}90%{{opacity:1}}100%{{transform:translateX({X_FIN - 60:.0f}px);opacity:0}}}}
"""
if 'libreta' in O and cv2 is not None:   # botón «Comenzar juego» del gato (lo dibuja gato.py): misma cuenta que allá
    (lcx, lcy), (lw_, lh_), _la = cv2.minAreaRect(np.array(O['libreta']['poligono_mm'], np.float32)); lh_ = min(lw_, lh_); ly = lcy - lh_ / 2 - 16
    ZONAS.append({'id': 'gato', 'poly': [[lcx - 80, ly - 26], [lcx + 80, ly - 26], [lcx + 80, ly + 8], [lcx - 80, ly + 8]]})
if F_OBJ == 'out/objetos.json': Path('out/zonas.json').write_text(json.dumps(ZONAS))
if F_OBJ == 'out/objetos.json':   # memoria de lo proyectado, para reconocer nuestras propias cajas en la próxima detección
    try: F_MEM.write_text(json.dumps([{"k": "libre" if b in LIBRES else "caja", "b": [round(float(v), 1) for v in b]} for b in cajas]))
    except Exception: pass
cuerpo = f'<defs>{"".join(DEFS)}</defs>' + "".join(f'<g id="capa-{k}">{"".join(v)}</g>' for k, v in CAPAS.items()) + haz
w = {"type": "effect", "id": os.environ.get("ESC_ID") or (Path("out/esc-id.txt").read_text().strip() if Path("out/esc-id.txt").exists() else "escritorio"), "shader": "html", "rect_mm": [0, 0, 1000, 563],
     "params": {"html": f'<div id="w"><svg viewBox="-10 -10 1340 710" xmlns="http://www.w3.org/2000/svg">{cuerpo}</svg></div>' + marca.firma(), "css": css}}
manos = {"type": "effect", "id": "manos", "shader": "hands", "params": {"skeleton": False, "trail": False}}
r = subprocess.run(["/usr/bin/python3", S, "project", json.dumps({"request_id": "esc-%d" % time.time(), "widgets": [manos, w]})], capture_output=True, text=True).stdout
print(r[:120])
for a in AVISOS: print("aviso:", a)
if ANIMAR: time.sleep(2.6)   # escritorio_auto toma su referencia al volver: que el barrido ya haya terminado

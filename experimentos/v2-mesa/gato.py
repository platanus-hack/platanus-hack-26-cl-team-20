#!/usr/bin/env python3
"""Gato (tic-tac-toe) sobre la libreta: si hay una rejilla # dibujada a mano, la mesa juega.

- Lee la libreta desde la cámara 4K (pose de out/objetos.json), la endereza y busca tinta oscura.
- Rejilla: 2 rayas largas horizontales + 2 verticales. Celdas: 3×3 alrededor.
- Jugada de la persona: tinta nueva dentro de una celda (X u O, da lo mismo). La mesa responde proyectando su marca
  (círculo de tinta azul, el color del agente en el diseño de Max) y lleva el marcador junto a la libreta.
- La tinta se lee igual con proyección encima: lo proyectado aclara, la tinta queda oscura.
"""
import cv2, json, numpy as np, subprocess, sys, time, threading
from pathlib import Path

H = Path(__file__).parent
S = '/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'
sys.path.insert(0, str(H))
from fp_rect import mapa  # noqa: E402

met = json.load(open(H / 'fp-metric.json')); Hm = np.array(met['mm_to_canvas']); Hm = Hm / Hm[2, 2]
M3D = "matrix3d(%s)" % ",".join("%.9g" % v for v in [Hm[0, 0], Hm[1, 0], 0, Hm[2, 0], Hm[0, 1], Hm[1, 1], 0, Hm[2, 1], 0, 0, 1, 0, Hm[0, 2], Hm[1, 2], 0, Hm[2, 2]])
PAPEL, PAPEL2, LAPIZ, TINTA, OK = "#F4F4F2", "#a9a9a6", "#ffffff", "#7aa8ff", "#9ed8a8"
PP = 3.0   # px por mm de la libreta enderezada


def pcx(t, a): return subprocess.run(['/usr/bin/python3', S, t, json.dumps(a)], capture_output=True, text=True).stdout
def log(*a): print(time.strftime('%H:%M:%S'), *a, flush=True)


cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION); cap.set(cv2.CAP_PROP_FRAME_WIDTH, 3840); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160); F = [None]
def _loop():
    while True:
        ok, f = cap.read()
        if ok: F[0] = f
        else: time.sleep(0.03)
threading.Thread(target=_loop, daemon=True).start()


def libreta():
    """Pose de la libreta según la última segmentación: centro, (ancho, alto), ángulo; None si no está."""
    try: o = [x for x in json.load(open(H / 'out/objetos.json')) if x['etiqueta'].startswith('libreta')]
    except Exception: return None
    if not o: return None
    (cx, cy), (w, h), ang = cv2.minAreaRect(np.array(o[0]['poligono_mm'], np.float32))
    if w < h: w, h = h, w; ang += 90
    return np.array([cx, cy]), (w, h), ang


def enderezar(pose):
    """Imagen de la libreta enderezada (PP px/mm) y la matriz libreta(px) -> mesa(mm)."""
    c, (w, h), ang = pose
    while F[0] is None: time.sleep(0.05)
    r = max(w, h) * 0.75; x0, y0 = c[0] - r, c[1] - r
    f = F[0].copy(); mx, my = mapa(x0, y0, c[0] + r, c[1] + r, PP, f.shape[1] / 1280.0); im = cv2.remap(f, mx, my, cv2.INTER_LINEAR)
    M = cv2.getRotationMatrix2D((r * PP, r * PP), ang, 1.0); M[0, 2] += w * PP / 2 - r * PP; M[1, 2] += h * PP / 2 - r * PP
    out = cv2.warpAffine(im, M, (int(w * PP), int(h * PP)), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))
    Mi = cv2.invertAffineTransform(M)   # px de la libreta -> px del recorte; después /PP + (x0, y0) = mm
    A = np.vstack([Mi, [0, 0, 1]]); T = np.array([[1 / PP, 0, x0], [0, 1 / PP, y0], [0, 0, 1]]) @ A
    return out, T


def tinta(im):
    """Tinta de lápiz o lápiz pasta, aunque sea tenue: más oscura que su entorno, o con color (azul, rojo)."""
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY); b = cv2.GaussianBlur(g, (0, 0), 9)
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV); Sb = cv2.GaussianBlur(hsv[..., 1], (0, 0), 9)
    osc = b.astype(np.int16) - g                      # cuánto más oscuro que su entorno; lo proyectado es MÁS claro y queda fuera
    m = ((osc > 10) | (((hsv[..., 1].astype(np.int16) - Sb) > 22) & (osc > 3))).astype(np.uint8)
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))


def rejilla(m):
    """Busca el # con rayas largas (Hough, tolera inclinación). Devuelve las 4 intersecciones I[fila][col] en px, o None."""
    h, w = m.shape; mm = m.copy()
    mm[:, int(w / 2 - 24 * PP):int(w / 2 + 24 * PP)] = 0; e = int(9 * PP); mm[:e] = 0; mm[-e:] = 0; mm[:, :e] = 0; mm[:, -e:] = 0   # fuera el espiral y los bordes de la hoja
    L = cv2.HoughLinesP(mm * 255, 1, np.pi / 180, threshold=int(22 * PP), minLineLength=int(30 * PP), maxLineGap=int(9 * PP))
    if L is None: return None
    hor, ver = [], []
    for x1, y1, x2, y2 in np.asarray(L).reshape(-1, 4):
        a = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1))); a = min(a, 180 - a); largo = float(np.hypot(x2 - x1, y2 - y1))
        if a < 22: hor.append(((x1 + x2) / 2, (y1 + y2) / 2, np.array([x2 - x1, y2 - y1], float) / largo, largo))
        elif a > 68: ver.append(((x1 + x2) / 2, (y1 + y2) / 2, np.array([x2 - x1, y2 - y1], float) / largo, largo))
    if len(hor) < 2 or len(ver) < 2: return None
    def racimos(segs, k, ref):
        """Agrupa rayas por su posición evaluada en una coordenada común `ref` (así dos trazos de la misma raya, o una
        raya inclinada partida en dos, caen juntos). k=0: verticales (posición x en y=ref); k=1: horizontales."""
        pos = []
        for s in segs:
            d = s[2]; den = d[1 - k] if abs(d[1 - k]) > 1e-6 else 1e-6; pos.append((s[k] + d[k] / den * (ref - s[1 - k]), s))
        pos.sort(key=lambda t: t[0]); out = []
        for p0, s in pos:
            if out and p0 - out[-1]['pos'][-1] < 8 * PP: out[-1]['pos'].append(p0); out[-1]['segs'].append(s)
            else: out.append({'pos': [p0], 'segs': [s]})
        res = []
        for o in out:
            peso = sum(s[3] for s in o['segs']); lg = max(o['segs'], key=lambda s: s[3]); lo = min(s[1 - k] - s[3] / 2 for s in o['segs']); hi = max(s[1 - k] + s[3] / 2 for s in o['segs'])
            c = float(np.average(o['pos'], weights=[s[3] for s in o['segs']])); p = np.array([c, ref]) if k == 0 else np.array([ref, c])
            if hi - lo > 34 * PP: res.append({'c': c, 'p': p, 'd': lg[2], 'peso': peso, 'lo': lo, 'hi': hi})
        return res
    def par(rs, lo_, hi_):
        best = None
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                d = rs[j]['c'] - rs[i]['c']
                if lo_ < d < hi_ and (best is None or rs[i]['peso'] + rs[j]['peso'] > best[0]): best = (rs[i]['peso'] + rs[j]['peso'], rs[i], rs[j])
        return best
    yc = float(np.median([s[1] for s in hor])); pv = par(racimos(ver, 0, yc), 14 * PP, 65 * PP)
    if not pv: return None
    dx = pv[2]['c'] - pv[1]['c']; xc = (pv[1]['c'] + pv[2]['c']) / 2
    cerca = [s for s in hor if abs(s[0] - xc) < dx * 1.6]        # solo rayas horizontales que pasan por la rejilla
    ph = par(racimos(cerca, 1, xc), dx * 0.55, dx * 1.8)
    if not ph: return None
    def cruce(a, b):
        try: t = np.linalg.solve(np.array([a['d'], -b['d']]).T, b['p'] - a['p'])
        except np.linalg.LinAlgError: return None
        return a['p'] + a['d'] * t[0]
    I = [[cruce(ph[1], pv[1]), cruce(ph[1], pv[2])], [cruce(ph[2], pv[1]), cruce(ph[2], pv[2])]]
    return None if any(q is None for r in I for q in r) else I


def celdas(I):
    """9 celdas como cuadriláteros (4 esquinas en px), extrapolando una celda hacia afuera por cada lado."""
    o = I[0][0]; u = ((I[0][1] - I[0][0]) + (I[1][1] - I[1][0])) / 2; v = ((I[1][0] - I[0][0]) + (I[1][1] - I[0][1])) / 2
    G = lambda r, c: o + (c - 1) * u + (r - 1) * v
    return [np.array([G(r, c), G(r, c + 1), G(r + 1, c + 1), G(r + 1, c)]) for r in range(3) for c in range(3)]


def ocupadas(m, cs):
    """Por celda: None si está vacía, 'O' si la marca encierra un hueco (círculo), 'X' si no."""
    out = []
    for q in cs:
        c = q.mean(axis=0); qi = (c + (q - c) * 0.72).astype(np.int32); x, y, w, h = cv2.boundingRect(qi)
        if x < 0 or y < 0 or x + w > m.shape[1] or y + h > m.shape[0]: out.append(None); continue
        mk = np.zeros((h, w), np.uint8); cv2.fillPoly(mk, [qi - [x, y]], 1); sub = m[y:y + h, x:x + w] & mk
        if sub.sum() < 0.022 * mk.sum(): out.append(None); continue
        cer = cv2.morphologyEx(sub, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)); cont, jer = cv2.findContours(cer, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        hueco = max([cv2.contourArea(cont[k]) for k in range(len(cont)) if jer[0][k][3] >= 0] or [0])
        out.append('O' if hueco > 0.10 * mk.sum() else 'X')
    return out


LINEAS = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
def gana(t, j): return next((l for l in LINEAS if all(t[i] == j for i in l)), None)   # t usa 'X' = persona, 'O' = mesa
def mejor(t):
    """Jugada de la mesa ('O') por minimax: gana si puede, si no bloquea, si no lo mejor."""
    def mm(t, turno):
        if gana(t, 'O'): return 1
        if gana(t, 'X'): return -1
        libres = [i for i in range(9) if t[i] is None]
        if not libres: return 0
        vals = []
        for i in libres:
            t[i] = turno; vals.append(mm(t, 'X' if turno == 'O' else 'O')); t[i] = None
        return max(vals) if turno == 'O' else min(vals)
    libres = [i for i in range(9) if t[i] is None]; best = (-2, None)
    for i in libres:
        t[i] = 'O'; v = mm(t, 'X'); t[i] = None
        if v > best[0]: best = (v, i)
    return best[1]


def a_mesa(T, p): q = T @ np.array([p[0], p[1], 1.0]); return q[:2]


JUEGO = [False]   # Jev no se consulta hasta que la persona deja el dedo sobre «Comenzar juego»


def _eventos():
    try: cur = json.loads(pcx('events_wait', {"cursor": 0, "timeout_s": 1})).get('cursor', 0)
    except Exception: cur = 0
    while True:
        try: d = json.loads(pcx('events_wait', {"cursor": cur, "timeout_s": 20}))
        except Exception: time.sleep(2); continue
        cur = d.get('cursor', cur)
        for e in d.get('events', []):
            if 'gato:start' in json.dumps(e) and not JUEGO[0]: JUEGO[0] = True; log('comenzar juego: Jev encendido')


def guia(T, pose):
    """Página del gato (mitad derecha de la libreta enderezada): título, instrucciones y un lienzo punteado donde dibujar."""
    w, h = pose[1][0] * PP, pose[1][1] * PP; x0, x1, y0, y1 = 0.11 * w, 0.41 * w, 0.17 * h, 0.64 * h; out = []   # con margen: nada se sale de la hoja
    tx = lambda x, y, s, size, col: '<text x="%.1f" y="%.1f" font-size="%g" fill="%s" font-weight="700" font-family="Helvetica Neue,Arial,sans-serif" text-anchor="middle" transform="rotate(%.1f %.1f %.1f)">%s</text>' % (x, y, size, col, pose[2] + 180, x, y, s)
    q = [a_mesa(T, p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    out.append('<polygon points="%s" fill="none" stroke="%s" stroke-width="1.6" stroke-dasharray="4 7"/>' % (" ".join("%.1f,%.1f" % tuple(p) for p in q), PAPEL2))
    for k, (s, size, col) in enumerate((("Gato", 13, PAPEL), ("1 · dibuja un # en el lienzo", 7.5, PAPEL2), ("2 · marca tu X u O", 7.5, PAPEL2), ("3 · la mesa responde", 7.5, PAPEL2))):
        c = a_mesa(T, ((x0 + x1) / 2, 0.86 * h - k * 0.052 * h)); out.append(tx(c[0], c[1], s, size, col))
    c = a_mesa(T, ((x0 + x1) / 2, y0 + 0.035 * h)); out.append(tx(c[0], c[1], "lienzo", 8, PAPEL2))
    return out


def proyectar(T, cs, t, marcador, estado, pose, figura_mesa='O'):
    svg = guia(T, pose)
    for i, q in enumerate(cs):
        c = a_mesa(T, q.mean(axis=0)); r = float(np.linalg.norm(q[1] - q[0])) / PP * 0.30
        if t[i] == 'O' and figura_mesa == 'O': svg.append(f'<circle cx="{c[0]:.1f}" cy="{c[1]:.1f}" r="{r:.1f}" fill="none" stroke="{TINTA}" stroke-width="3" stroke-linecap="round" pathLength="100" stroke-dasharray="100" class="dib"/>')
        elif t[i] == 'O': svg.append(f'<path d="M{c[0]-r:.1f},{c[1]-r:.1f} L{c[0]+r:.1f},{c[1]+r:.1f} M{c[0]+r:.1f},{c[1]-r:.1f} L{c[0]-r:.1f},{c[1]+r:.1f}" fill="none" stroke="{TINTA}" stroke-width="3" stroke-linecap="round" pathLength="100" stroke-dasharray="100" class="dib"/>')
    g = (gana(t, 'O') or gana(t, 'X')) if t else None
    if g:
        a = a_mesa(T, cs[g[0]].mean(axis=0)); b = a_mesa(T, cs[g[2]].mean(axis=0))
        svg.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" stroke="{LAPIZ}" stroke-width="3" stroke-linecap="round"/>')
    # marcador y estado: junto al borde de la libreta que mira a la persona (hacia arriba en la imagen)
    c, (w, h), ang = pose; y = c[1] - h / 2 - 16
    tx = lambda x, y, s, size, col, cls: f'<text x="{x:.0f}" y="{y:.0f}" font-size="{size}" fill="{col}" class="{cls}" text-anchor="middle" transform="rotate(180 {x:.0f} {y:.0f})">{s}</text>'
    if JUEGO[0]: svg.append(tx(c[0], y, f"tú {marcador['X']} · mesa {marcador['O']} · empates {marcador['E']}", 13, PAPEL, "mono")); svg.append(tx(c[0], y - 18, estado, 15, TINTA if 'mesa' in estado or 'Gan' in estado else PAPEL, "mano"))
    else:   # botón bajo la libreta (del lado de la persona): con el dedo encima ~0,4 s parte el juego
        svg.append(f'<g data-press="gato:start"><rect x="{c[0] - 80:.0f}" y="{y - 26:.0f}" width="160" height="34" rx="3" fill="{PAPEL}"/>' + f'<text x="{c[0]:.0f}" y="{y - 4:.0f}" font-size="15" font-weight="700" fill="#000" font-family="Helvetica Neue,Arial,sans-serif" text-anchor="middle" transform="rotate(180 {c[0]:.0f} {y - 9:.0f})">Comenzar juego</text></g>')
    css = (f"html,body{{margin:0;background:transparent;overflow:hidden}}#w{{position:absolute;left:0;top:0;width:1400px;height:720px;transform-origin:0 0;transform:{M3D}}}svg{{position:absolute;left:-10px;top:-10px;width:1340px;height:710px}}"
           ".mono{font-family:'Menlo',ui-monospace,monospace}.mano{font-family:'Bradley Hand','Noteworthy',cursive}.dib{animation:d .5s cubic-bezier(.35,.1,.45,1) both}@keyframes d{from{stroke-dashoffset:100}to{stroke-dashoffset:0}}")
    pcx('project', {"request_id": "gato-%d" % (time.time() * 10), "widgets": [{"type": "effect", "id": "gato", "shader": "html", "rect_mm": [0, 0, 1000, 563], "params": {"html": f'<div id="w"><svg viewBox="-10 -10 1340 710" xmlns="http://www.w3.org/2000/svg">{"".join(svg)}</svg></div>', "css": css}}]})


if __name__ == '__main__':
    # Versión con Jev (modelo de decisión rápido): el recorte de la libreta va a Jev, que dice si hay rejilla, dónde,
    # y qué hay en cada una de las 9 celdas. Lanzar con: doppler run -p platanus-hack-26 -c dev -- python gato.py
    import jevc, sonido
    NOM = ["top-left", "top-center", "top-right", "middle-left", "center", "middle-right", "bottom-left", "bottom-center", "bottom-right"]
    CRIT = {"vacia": "no pen ink in the cell (ignore grid lines and any glowing projected light)", "X": "an X or cross drawn with pen ink", "O": "a circle or O drawn with pen ink"}
    marcador = {"X": 0, "O": 0, "E": 0}; mesa = set(); fin = False; caja = None; t_caja = 0; previo = None; humano = None; sin = 0; activo = False; t_fin = None
    threading.Thread(target=_eventos, daemon=True).start()
    while True:
        time.sleep(0.25); pose = libreta()
        if pose is None: continue
        im, T = enderezar(pose); h, w = im.shape[:2]
        if fin and t_fin and time.time() - t_fin > 7: JUEGO[0] = False; t_fin = None; log('partida terminada: Jev apagado')
        if not fin: t_fin = None
        elif t_fin is None: t_fin = time.time()
        if not JUEGO[0] and (H / 'out/gato-juego.flag').exists():
            (H / 'out/gato-juego.flag').unlink(); JUEGO[0] = True; log('comenzar juego (mano): Jev encendido')
        if not JUEGO[0]:   # en espera: solo la guía y el botón, sin llamar a Jev
            fg = (tuple(np.round(pose[0] / 4)), round(pose[2] / 2))
            if fg != globals().get('_fesp'): globals()['_fesp'] = fg; proyectar(T, [], [], marcador, '', pose)
            mesa = set(); fin = False; caja = None; previo = None; humano = None; globals()['_firma'] = None; globals()['_fguia'] = None; time.sleep(0.4); continue
        globals()['_fesp'] = None
        if caja is None or time.time() - t_caja > 6:     # buscar / reconfirmar la rejilla cada 6 s
            r = jevc.decide([{"type": "text", "text": "Open spiral notebook seen from above. Someone may have hand-drawn a tic-tac-toe grid (#) with a pen on one page."}, jevc.imagen(im)],
                            {"hay": {"type": "boolean", "instructions": "Is there a hand-drawn tic-tac-toe grid (two vertical and two horizontal pen lines) on the notebook?"},
                             "rejilla": {"type": "box", "instructions": "The full 3x3 tic-tac-toe board: the square region covered by the hand-drawn grid, including the outer cells."}})
            if 'answers' not in r: log('jev:', r.get('error')); time.sleep(2); continue
            if (r['answers'].get('hay') or 0) < 0.6 or not r['answers'].get('rejilla'):
                sin += 1
                if activo and sin >= 2: proyectar(T, [], [], marcador, "dibuja un # para jugar", pose); activo = False; caja = None; mesa = set(); fin = False; humano = None; previo = None; globals()['_firma'] = None; sonido.play('recuperacion'); log('sin rejilla: juego en pausa')
                fg = (tuple(np.round(pose[0] / 4)), round(pose[2] / 2))
                if not activo and fg != globals().get('_fguia'): globals()['_fguia'] = fg; proyectar(T, [], [], marcador, "dibuja un # para jugar", pose)
                time.sleep(1); continue
            sin = 0; b = r['answers']['rejilla']; nueva = (b['left'] * w, b['top'] * h, b['right'] * w, b['bottom'] * h)
            if caja is None or max(abs(a - c) for a, c in zip(caja, nueva)) > 14 * PP:
                if caja is not None: mesa = set(); fin = False; previo = None; log('la rejilla cambió de lugar: partida nueva')
                caja = nueva
            t_caja = time.time()
        x0, y0, x1, y1 = caja; mg = 4 * PP; crop = im[int(max(y0 - mg, 0)):int(y1 + mg), int(max(x0 - mg, 0)):int(x1 + mg)]
        q = {f"c{i}": {"type": "choice", "instructions": f"What is drawn with pen ink inside the {n} cell of this tic-tac-toe grid?", "criteria": CRIT} for i, n in enumerate(NOM)}
        q["mano"] = {"type": "boolean", "instructions": "Is a hand, finger or pen covering part of the grid right now?"}
        r = jevc.decide([{"type": "text", "text": "A hand-drawn tic-tac-toe board on paper, cropped. Bright glowing blue or red marks are projected light, not ink: ignore them."}, jevc.imagen(crop)], q)
        if 'answers' not in r: log('jev:', r.get('error')); time.sleep(1); continue
        a = r['answers']
        if (a.get('mano') or 0) > 0.5: previo = None; continue      # con la mano encima no se lee
        lec = [None if a[f"c{i}"]['choice'] == 'vacia' else a[f"c{i}"]['choice'] for i in range(9)]
        for i in mesa: lec[i] = None                                 # las celdas de la mesa son luz, no tinta
        if lec != previo: previo = lec; continue                     # dos lecturas iguales seguidas antes de actuar
        marcas = [x for x in lec if x]
        if humano is None and marcas: humano = max(set(marcas), key=marcas.count); log('la persona juega con', humano)
        fig_mesa = 'X' if humano == 'O' else 'O'
        if not marcas and (fin or mesa): mesa = set(); fin = False   # hoja limpia: partida nueva
        t = [('O' if i in mesa else ('X' if lec[i] else None)) for i in range(9)]   # interno: X = persona, O = mesa
        estado = "tu turno: dibuja tu marca" if marcas else "rejilla lista: parte tú"
        if not fin:
            if gana(t, 'X'): marcador['X'] += 1; fin = True; estado = "Ganaste. Dibuja otra rejilla."
            elif t.count('X') > t.count('O') and None in t:
                j = mejor(t); mesa.add(j); t[j] = 'O'; sonido.play('confirmacion'); log(f"jugada vista ({r['ms']} ms); la mesa juega en {NOM[j]}")
                if gana(t, 'O'): marcador['O'] += 1; fin = True; estado = "Gana la mesa. Dibuja otra rejilla."
                elif None not in t: marcador['E'] += 1; fin = True; estado = "Empate. Dibuja otra rejilla."
            elif None not in t: marcador['E'] += 1; fin = True; estado = "Empate. Dibuja otra rejilla."
        else: estado = "Partida terminada. Dibuja otra rejilla."
        cw, ch = (x1 - x0) / 3, (y1 - y0) / 3
        cs = [np.array([[x0 + c * cw, y0 + f * ch], [x0 + (c + 1) * cw, y0 + f * ch], [x0 + (c + 1) * cw, y0 + (f + 1) * ch], [x0 + c * cw, y0 + (f + 1) * ch]]) for f in range(3) for c in range(3)]
        firma = (tuple(t), estado, tuple(marcador.values()), tuple(np.round(pose[0] / 4)), round(pose[2] / 2), tuple(round(v / (6 * PP)) for v in caja))   # también se redibuja si se mueve la libreta o la rejilla
        if firma != globals().get('_firma'):
            globals()['_firma'] = firma; activo = True; proyectar(T, cs, t, marcador, estado, pose, fig_mesa); log('tablero', ''.join(x or '.' for x in t), '·', estado)

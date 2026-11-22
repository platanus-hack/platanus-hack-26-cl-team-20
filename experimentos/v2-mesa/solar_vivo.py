#!/usr/bin/env python3
"""Sistema solar en vivo: sigue los tres platos en tiempo real y los planetas van pegados a su plato.

Idea: no se detecta cada bola (blanco sobre blanco es frágil). Se detectan los tres platos, que son círculos grandes y
nítidos, y su punto negro, que da el giro. Cada bola tiene una posición fija dentro de su plato (solar-modelo.json), así
que con el centro y el giro del plato se sabe dónde está cada planeta aunque muevas o gires los platos.

   solar_vivo.py --modelo     mide platos, puntos y bolas en la foto limpia actual y escribe solar-modelo.json
   solar_vivo.py              rastrea (≈10 veces por segundo), sirve las poses en http://127.0.0.1:9302/poses y proyecta
                              el widget vivo «solar-vivo», que las lee y dibuja Sol, órbitas, planetas y nombres

Tocar un planeta físico (dedo quieto ~0,3 s): se ilumina ese planeta en la lámina impresa, la libreta muestra sus
datos y Lumo, que vive en la libreta, los cuenta en voz alta (ElevenLabs; sin llave, la voz del Mac).
Lanzar con: doppler run -p platanus-hack-26 -c dev -- ../sl-scanner/.venv/bin/python solar_vivo.py
"""
import cv2, json, math, os, subprocess, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import marca_mesa as marca
import numpy as np

H = Path(__file__).parent; PY = sys.executable
S = '/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'
sys.path.insert(0, str(H)); from fp_rect import mapa  # noqa: E402
met = json.load(open(H / 'fp-metric.json')); Hm = np.array(met['mm_to_canvas']); Hm = Hm / Hm[2, 2]; L = met['lens']
PP = 1.0; X0 = Y0 = -10            # vista rectificada de trabajo: 1 px por mm
KP = np.array([float(v) for v in os.environ.get('SOLAR_KP', '0.13,0.28').split(',')])   # mm de corrección del proyector por mm de altura
DATOS = json.load(open(H / 'planetas.json'))
PLAN = {p['id']: p for p in DATOS['planetas']}


def pcx(t, a): return subprocess.run(['/usr/bin/python3', S, t, json.dumps(a)], capture_output=True, text=True).stdout
def log(*a): print(time.strftime('%H:%M:%S'), *a, flush=True)
def canvas(p):
    q = np.c_[np.asarray(p, float).reshape(-1, 2), np.ones(len(np.asarray(p).reshape(-1, 2)))] @ Hm.T; return q[:, :2] / q[:, 2:]


def camara():
    """Posición de la cámara (mm de mesa) desde la homografía mm->px y la focal del modelo de lente."""
    try:
        Hi = np.linalg.inv(np.array(met['px_to_mm'])); K = np.array([[L['f'], 0, L['cx']], [0, L['f'], L['cy']], [0, 0, 1]]); B = np.linalg.inv(K) @ Hi
        lam = 2 / (np.linalg.norm(B[:, 0]) + np.linalg.norm(B[:, 1])); r1, r2, t = lam * B[:, 0], lam * B[:, 1], lam * B[:, 2]; R = np.stack([r1, r2, np.cross(r1, r2)], 1); C = -R.T @ t
        if 300 < abs(C[2]) < 3000: return np.array([C[0], C[1], abs(C[2])])
    except Exception: pass
    return np.array([660.0, 1100.0, 800.0])


CAM = camara()
def suelo(a, h): return CAM[:2] + (np.asarray(a, float) - CAM[:2]) * (CAM[2] - h) / CAM[2]        # dónde está de verdad lo que la cámara ve en a, a altura h
def aparente(g, h): return CAM[:2] + (np.asarray(g, float) - CAM[:2]) * CAM[2] / (CAM[2] - h)     # y al revés
def rot(v, a): c, s = math.cos(a), math.sin(a); return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1]])


# ------------------------------------------------------------------ cámara
F = [None]
def _cam():
    c = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION); c.set(cv2.CAP_PROP_FRAME_WIDTH, 3840); c.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160)
    while True:
        ok, f = c.read()
        if ok: F[0] = f
        else: time.sleep(0.03)
_M = {}
def vista():
    while F[0] is None: time.sleep(0.05)
    f = F[0]; w = f.shape[1]
    if w not in _M: _M[w] = mapa(X0, Y0, 1330, 700, PP, w / 1280.0)
    return cv2.cvtColor(cv2.remap(f, _M[w][0], _M[w][1], cv2.INTER_LINEAR), cv2.COLOR_BGR2GRAY)
def a_mm(px): return np.asarray(px, float) / PP + [X0, Y0]
def a_px(mm): return (np.asarray(mm, float) - [X0, Y0]) * PP


def punto(g, c, R, previo=None):
    """Ángulo del punto negro del plato: la manchita oscura más marcada en el anillo del borde."""
    cx, cy = a_px(c); r = R * PP; x0, y0 = int(cx - r - 4), int(cy - r - 4); roi = g[max(y0, 0):int(cy + r + 4), max(x0, 0):int(cx + r + 4)]
    if roi.size == 0: return previo
    bh = cv2.morphologyEx(roi, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(9 * PP) | 1, int(9 * PP) | 1)))
    yy, xx = np.mgrid[0:roi.shape[0], 0:roi.shape[1]]; d = np.hypot(xx - (cx - max(x0, 0)), yy - (cy - max(y0, 0))); bh = np.where((d > 0.80 * r) & (d < 0.96 * r), bh, 0)
    bh = cv2.GaussianBlur(bh.astype(np.float32), (0, 0), 1.2); cand = []
    for _ in range(4):
        _, v, _, (mx_, my_) = cv2.minMaxLoc(bh)
        if v < 14: break
        cand.append((v, math.atan2(my_ - (cy - max(y0, 0)), mx_ - (cx - max(x0, 0))))); cv2.circle(bh, (mx_, my_), int(10 * PP), 0, -1)
    if not cand: return previo
    if previo is None: return cand[0][1]
    dif = lambda a: abs((a - previo + math.pi) % (2 * math.pi) - math.pi)
    cerca = [c_ for c_ in cand if dif(c_[1]) < 0.6 and c_[0] > 0.5 * cand[0][0]]      # continuidad: el punto no salta
    return min(cerca, key=lambda c_: dif(c_[1]))[1] if cerca else (cand[0][1] if cand[0][0] > 30 else previo)


def platos(g, previos=None, R=112):
    """Círculos de los platos. Con `previos` se busca cada uno cerca de donde estaba."""
    if previos is None:
        cs = cv2.HoughCircles(cv2.GaussianBlur(g, (0, 0), 1.2), cv2.HOUGH_GRADIENT, dp=1.5, minDist=150 * PP, param1=80, param2=38, minRadius=int(98 * PP), maxRadius=int(126 * PP))
        vistos = []
        for c in ([] if cs is None else cs[0]):      # un plato es un disco más claro que la mesa que lo rodea: se descartan otros círculos
            yy, xx = np.mgrid[0:g.shape[0], 0:g.shape[1]]; d = np.hypot(xx - c[0], yy - c[1]); dentro_, fuera = g[d < c[2] * 0.8], g[(d > c[2] * 1.12) & (d < c[2] * 1.35)]
            if dentro_.size and fuera.size and float(dentro_.mean()) - float(fuera.mean()) > 18: vistos.append((a_mm(c[:2]), c[2] / PP))
        return vistos[:3]
    out = []
    for c, r in previos:
        cx, cy = a_px(c); m = int((r + 70) * PP); x0, y0 = max(int(cx - m), 0), max(int(cy - m), 0); roi = g[y0:int(cy + m), x0:int(cx + m)]
        cs = cv2.HoughCircles(cv2.GaussianBlur(roi, (0, 0), 1.2), cv2.HOUGH_GRADIENT, dp=1.5, minDist=400, param1=80, param2=30, minRadius=int((r - 5) * PP), maxRadius=int((r + 5) * PP)) if roi.size else None
        ok = None
        if cs is not None:
            px, py = cs[0][0][:2]; yy, xx = np.mgrid[0:roi.shape[0], 0:roi.shape[1]]; d = np.hypot(xx - px, yy - py); dentro_, fuera = roi[d < r * PP * 0.8], roi[(d > r * PP * 1.12) & (d < r * PP * 1.35)]
            if dentro_.size and fuera.size and float(dentro_.mean()) - float(fuera.mean()) > 10: ok = (a_mm(cs[0][0][:2] + [x0, y0]), r)
        out.append(ok)
    return out


# ------------------------------------------------------------------ modelo (una vez, con la mesa limpia)
# Bolas anotadas sobre la foto limpia del montaje (mm de mesa, radio): cuál es cada planeta lo decide su tamaño y su plato.
ANOT = [("sol", 435, 265, 55), ("mercurio", 506, 260, 15), ("venus", 546, 324, 16), ("tierra", 640, 321, 16), ("luna", 664, 324, 16), ("jupiter", 760, 362, 52),
        ("marte", 656, 435, 17), ("urano", 802, 517, 31), ("saturno", 956, 475, 37), ("neptuno", 933, 588, 24)]
# planetas impresos en la lámina (px de la ampliación girada 1170×810 de la foto limpia): x, y, radio
LAMINA = {"sol": (135, 440, 160), "mercurio": (328, 370, 32), "tierra": (515, 285, 60), "luna": (557, 430, 24), "venus": (430, 520, 42), "marte": (610, 597, 52),
          "jupiter": (742, 335, 82), "urano": (962, 335, 58), "saturno": (858, 553, 68), "neptuno": (1068, 553, 45)}


def hacer_modelo():
    g = vista(); ps = platos(g)
    assert len(ps) == 3, f"se esperaban 3 platos y hay {len(ps)}"
    mod = {"platos": [], "lamina": {k: [(870 + (1170 - x) / 3) / 2 - 10, (990 + (810 - y) / 3) / 2 - 10, r / 6] for k, (x, y, r) in LAMINA.items()}}
    for c, r in ps:
        th = punto(g, c, r); gp = suelo(c, 18); bolas = []
        for n, x, y, rb in ANOT:
            if min(ps, key=lambda q: np.hypot(x - q[0][0], y - q[0][1]))[0] is not c: continue      # cada bola, a su plato más cercano
            h = rb * 0.95 + 8 if n != 'sol' else 30; loc = rot(suelo((x, y), h) - gp, -(th or 0)); bolas.append({"id": n, "local": loc.round(1).tolist(), "r": rb, "h": round(h, 1)})
        mod["platos"].append({"c0": np.round(c, 1).tolist(), "r": round(float(r), 1), "th0": th, "bolas": bolas}); log('plato', np.round(c).tolist(), 'r', round(float(r)), 'punto', None if th is None else round(math.degrees(th)), [b['id'] for b in bolas])
    (H / 'solar-modelo.json').write_text(json.dumps(mod, indent=1)); return mod


# ------------------------------------------------------------------ qué plato es cuál (aunque los cambies de lugar)
def parche(g, c, r, grados=0.0, n=96):
    """Recorte cuadrado del plato (1 px/mm), girado `grados`, normalizado y con máscara circular."""
    cx, cy = a_px(c); m = int(r + 14); x0, y0 = int(cx - m), int(cy - m)
    if x0 < 0 or y0 < 0 or y0 + 2 * m > g.shape[0] or x0 + 2 * m > g.shape[1]: g = cv2.copyMakeBorder(g, m, m, m, m, cv2.BORDER_REPLICATE); x0 += m; y0 += m
    q = g[y0:y0 + 2 * m, x0:x0 + 2 * m].astype(np.float32)
    if grados: q = cv2.warpAffine(q, cv2.getRotationMatrix2D((m, m), grados, 1.0), (2 * m, 2 * m), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    q = cv2.resize(q, (n, n), interpolation=cv2.INTER_AREA); yy, xx = np.mgrid[0:n, 0:n]; mk = np.hypot(xx - n / 2, yy - n / 2) < n / 2 * (r / m)
    q = q - q[mk].mean(); q[~mk] = 0; return q / (np.linalg.norm(q) + 1e-6)


def emparejar(g, enc, mod):
    """Empareja los platos vistos con los del modelo comparando su aspecto en todos los giros (cada plato tiene su
    propio arreglo de bolas). Devuelve centros y giros iniciales; así da lo mismo dónde quedó cada plato."""
    from itertools import permutations
    ref = cv2.imread(str(H / 'out/solar-norm.png'), cv2.IMREAD_GRAYSCALE)
    if ref is None or len(enc) < len(mod['platos']): return None
    ref = cv2.resize(ref, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)                      # la foto del modelo está a 2 px/mm
    T = [parche(ref, pl['c0'], pl['r'], math.degrees(pl['th0'] or 0)) for pl in mod['platos']]     # cada plantilla, con su punto negro en ángulo 0
    P = np.zeros((len(T), len(enc))); A = np.zeros_like(P)
    for j, (c, r) in enumerate(enc):
        for a in range(0, 360, 4):
            q = parche(g, c, r, a)
            for k, t in enumerate(T):
                v = float((q * t).sum())
                if v > P[k, j]: P[k, j] = v; A[k, j] = a
    mejor = max(permutations(range(len(enc)), len(T)), key=lambda pm: sum(P[k, j] for k, j in enumerate(pm)))
    log('emparejado por aspecto:', [(k, j, round(P[k, j], 2), int(A[k, j])) for k, j in enumerate(mejor)])
    mejor = list(mejor); dud = [k for k, j in enumerate(mejor) if P[k, j] < 0.45]
    if len(dud) >= 2:      # aspecto poco claro (cambió la luz o se movieron bolas): el plato del engranaje es el de más textura
        def textura(j):
            c, r = enc[j]; q = parche(cv2.Laplacian(g, cv2.CV_32F, ksize=3).__abs__().astype(np.float32) + 1, c, r); cx, cy = a_px(c); m = int(r * 0.85)
            return float(np.abs(cv2.Laplacian(g[int(cy - m):int(cy + m), int(cx - m):int(cx + m)], cv2.CV_32F, ksize=3)).mean())
        libres = sorted([mejor[k] for k in dud], key=textura, reverse=True); ksol = next((k for k in dud if any(b['id'] == 'sol' for b in mod['platos'][k]['bolas'])), None)
        if ksol is not None:
            resto = [k for k in dud if k != ksol]; mejor[ksol] = libres[0]
            for k, j in zip(resto, libres[1:]): mejor[k] = j
            log('platos dudosos resueltos por textura:', [(k, mejor[k]) for k in dud])
    ang = []
    for k, j in enumerate(mejor):      # giro: el del aspecto si fue claro; si no, el punto negro
        th = punto(g, enc[j][0], enc[j][1]) if P[k, j] < 0.45 else None; ang.append(th if th is not None else math.radians(A[k, j]))
    return [(np.array(enc[j][0], float), mod['platos'][k]['r']) for k, j in enumerate(mejor)], ang


# ------------------------------------------------------------------ estado que se sirve al widget
E = {"t": 0, "planetas": [], "sel": None, "lam": None, "nota": None, "lumo": None, "cohete": None, "conv": False, "trans": None, "visible": not os.environ.get('ESPERA'), "t_vis": 0, "ok": False}


def libreta():
    try: o = next(x for x in json.load(open(H / 'out/objetos.json')) if x['etiqueta'].startswith('libreta')); return np.array(o['centro_mm'], float), o['tam_mm']
    except Exception: return np.array([904.0, 160.0]), [308, 236]


def cohete():
    """El plátano de la mesa, listo para volverse cohete: silueta en canvas y su eje CURVO, de la cola (el tallo, el
    extremo fino) a la punta, con el ancho en cada tramo. Todo lo que se dibuja sigue esa curva."""
    try: o = next(x for x in json.load(open(H / 'out/objetos.json')) if x['etiqueta'].startswith('plátano'))
    except Exception: return None
    q = np.array(o['poligono_mm'], np.float32); x0, y0 = q.min(0) - 2; m = np.zeros((int(q[:, 1].max() - y0) + 3, int(q[:, 0].max() - x0) + 3), np.uint8); cv2.fillPoly(m, [np.round(q - [x0, y0]).astype(np.int32)], 1)
    ys, xs = np.nonzero(m); P = np.stack([xs, ys], 1) + [x0, y0]; c = P.mean(0); _, V = np.linalg.eigh(np.cov((P - c).T)); u = V[:, 1]; v = np.array([-u[1], u[0]]); pu, pv = (P - c) @ u, (P - c) @ v
    lo, hi = np.percentile(pu, [1.5, 98.5]); N = 14; bu, bv, bw = [], [], []
    for k in range(N):
        sel = (pu >= lo + (hi - lo) * k / N) & (pu <= lo + (hi - lo) * (k + 1) / N)
        if sel.sum() < 6: continue
        bu.append(pu[sel].mean()); bv.append(np.median(pv[sel])); bw.append(np.percentile(pv[sel], 96) - np.percentile(pv[sel], 4))
    bu, bw = np.array(bu), np.array(bw); bv = np.polyval(np.polyfit(bu, bv, 2), bu); bw = np.convolve(np.r_[bw[0], bw, bw[-1]], [0.25, 0.5, 0.25], 'valid')
    if bw[:3].mean() > bw[-3:].mean(): bu, bv, bw = bu[::-1], bv[::-1], bw[::-1]      # la cola es el extremo fino (el tallo)
    eje = []
    for a_, b_, w_ in zip(bu, bv, bw):
        pt = c + u * a_ + v * b_; qq = canvas(pt)[0]; eje.append([round(float(qq[0]), 1), round(float(qq[1]), 1), round(float(w_) * escala(pt), 1)])
    return {"poly": canvas(q).round(1).tolist(), "eje": eje}


def musica():
    """Fondo sonoro suave (un acorde espacial generado, sin archivos de terceros), en bucle y a bajo volumen."""
    f = H / 'cues/espacio.wav'
    if not f.exists():
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                        "sine=f=110:d=24,volume=0.5[a];sine=f=164.8:d=24,volume=0.35[b];sine=f=220.5:d=24,volume=0.3[c];sine=f=329.6:d=24,volume=0.16[d];[a][b][c][d]amix=inputs=4,tremolo=f=0.12:d=0.5,aecho=0.8:0.7:900|1400:0.35|0.25,lowpass=f=1400,afade=t=in:d=3,afade=t=out:st=21:d=3,volume=2.2",
                        '-ac', '2', str(f)], capture_output=True)
    def _b():
        while f.exists(): subprocess.run(['afplay', '-v', '0.35', str(f)])
    threading.Thread(target=_b, daemon=True).start()


def ver_libreta(g, est):
    """Centro y tamaño (mm) de la libreta abierta, o None. Lo más claro de la mesa, sin los platos, con forma de rectángulo."""
    m = g.copy()
    for c, r in est: cv2.circle(m, tuple(int(v) for v in a_px(c)), int((r + 10) * PP), 0, -1)
    b = (m > np.percentile(m, 94)).astype(np.uint8); b = cv2.morphologyEx(b, cv2.MORPH_OPEN, np.ones((13, 13), np.uint8)); b = cv2.morphologyEx(b, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    cs, _ = cv2.findContours(b, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE); mejor = None
    for c in cs:
        a = cv2.contourArea(c)
        if not 35000 * PP * PP < a < 110000 * PP * PP: continue
        (cx, cy), (w, h), _a = cv2.minAreaRect(c)
        if a / (w * h + 1) > 0.72 and (mejor is None or a > mejor[0]): mejor = (a, a_mm((cx, cy)), (max(w, h) / PP, min(w, h) / PP))
    return None if mejor is None else (mejor[1], mejor[2])


def poner_libreta(c, tam):
    q = canvas(c)[0]; sc = escala(c); d = {"x": float(q[0]), "y": float(q[1]), "w": tam[0] * sc, "h": tam[1] * sc}
    E['lumo'] = {**(E.get('lumo') or {}), **d}
    if E.get('nota'): E['nota'] = {**E['nota'], **d}


def escala(p):   # px de canvas por mm en el punto p
    a, b = canvas([p, np.asarray(p) + [10, 0]]); return float(np.linalg.norm(b - a)) / 10


def publicar(mod, poses):
    out = []
    for pl, pose in zip(mod['platos'], poses):
        if pose is None: continue
        c, th = pose; gp = suelo(c, 18)
        for b in pl['bolas']:
            g = gp + rot(np.array(b['local']), th); a = aparente(g, b['h']); aim = a + b['h'] * KP; q = canvas(aim)[0]; d = PLAN.get(b['id'], {})
            out.append({"id": b['id'], "n": d.get('nombre', 'Sol' if b['id'] == 'sol' else b['id']), "x": round(float(q[0]), 1), "y": round(float(q[1]), 1), "r": round(b['r'] * 1.04 * escala(aim), 1),
                        "c": d.get('colores') or DATOS['sol']['colores'], "tx": d.get('textura', 'sol' if b['id'] == 'sol' else 'rocosa'), "mm": np.round(a, 1).tolist(), "pl": [round(float(v), 1) for v in (*canvas(c)[0], pl['r'] * escala(c))]})
    E['planetas'] = out; E['t'] = time.time(); E['ok'] = True


def hablar(texto):
    return      # sin voz propia: la única voz de la escena es el agente de ElevenLabs (evita acoples)
    def _h():
        mp3 = H / 'out/lumo.mp3'; k = os.environ.get('ELEVENLABS_API_KEY', '')
        try:
            assert k
            r = subprocess.run(['curl', '-s', '-m', '25', '-o', str(mp3), '-w', '%{http_code}', 'https://api.elevenlabs.io/v1/text-to-speech/' + os.environ.get('LUMO_VOZ', 'EXAVITQu4vr4xnSDxMaL') + '?output_format=mp3_44100_64',
                                '-H', 'xi-api-key: ' + k, '-H', 'Content-Type: application/json', '-d', json.dumps({"text": texto, "model_id": "eleven_multilingual_v2"})], capture_output=True, text=True)
            assert r.stdout.strip() == '200', r.stdout
            subprocess.run(['pkill', '-x', 'afplay']); subprocess.Popen(['afplay', str(mp3)])
        except Exception as ex:
            log('voz ElevenLabs no disponible (%s): uso la voz del Mac' % str(ex)[:40]); subprocess.Popen(['say', '-v', 'Paulina', '-r', '185', texto])
    threading.Thread(target=_h, daemon=True).start()


def pagina_lumo(js):
    """Corre JS en la pestaña de la voz de Lumo (http://127.0.0.1:9325). Devuelve el texto que entregue."""
    scr = '''tell application "Google Chrome"
repeat with w in windows
repeat with t in tabs of w
if (URL of t) starts with "http://127.0.0.1:9325" then return (execute t javascript %s)
end repeat
end repeat
return "sin-pestaña"
end tell''' % json.dumps(js)
    return subprocess.run(['osascript', '-e', scr], capture_output=True, text=True).stdout.strip()


def lumo_activa(): return bool(E.get('conv'))


def conversar():
    """Botón de la mesa: parte al tiro (o corta) la conversación de voz con Lumo en su página."""
    # el script que corre AppleScript no ve las funciones de la página: se hace clic en su botón (parte o corta)
    r = pagina_lumo("(function(){ var b = document.getElementById('b'); if (!b) return 'sin-boton'; var c = b.className === 'parar' ? 'cortando' : 'partiendo'; b.click(); return c; })()")
    log('hablar con Lumo:', r)
    if r in ('sin-pestaña', 'sin-boton', ''): abrir_pagina(); time.sleep(3.5); pagina_lumo("document.getElementById('b') && document.getElementById('b').click(); 1")


def abrir_pagina():
    subprocess.run(['osascript', '-e', 'tell application "Google Chrome"\nset nw to make new window\nset URL of active tab of nw to "http://127.0.0.1:9325/"\nset bounds of nw to {60, 60, 700, 620}\nend tell'], capture_output=True)


def renglones(t, n=21):
    out = [""]; t = str(t)
    for p in t.split():
        if out[-1] and len(out[-1]) + 1 + len(p) > n: out.append(p)
        else: out[-1] = (out[-1] + " " + p).strip()
    return out


def elegir(pid, mod):
    d = PLAN.get(pid) or (DATOS['sol'] if pid == 'sol' else None)
    if not d or E['sel'] == pid: return
    E['sel'] = pid; lc, lt = libreta(); q = canvas(lc)[0]; s = escala(lc); lin = []
    for dato in d.get('datos', [])[:2]: lin += renglones(dato) + [""]
    E['nota'] = {"x": float(q[0]), "y": float(q[1]), "w": lt[0] * s, "h": lt[1] * s, "titulo": d['nombre'], "lineas": lin[:8], "c": d['colores'][1]}
    lam = mod['lamina'].get(pid)
    if lam: ql = canvas(lam[:2])[0]; E['lam'] = {"x": float(ql[0]), "y": float(ql[1]), "r": lam[2] * escala(lam[:2]) + 4}
    log('planeta tocado:', pid)
    if E.get('conv'): PEND.append(f'(La persona acaba de tocar {d["nombre"]} en la mesa.)')
    else: hablar(d['nombre'] + '. ' + '. '.join(d.get('datos', [])[:2]) + '.')


def eventos(mod):
    try: cur = json.loads(pcx('events_wait', {"cursor": 0, "timeout_s": 1})).get('cursor', 0)
    except Exception: cur = 0
    while True:
        try: d = json.loads(pcx('events_wait', {"cursor": cur, "timeout_s": 20}))
        except Exception: time.sleep(2); continue
        cur = d.get('cursor', cur)
        for e in d.get('events', []):
            s = json.dumps(e, ensure_ascii=False)
            if 'lumo:hablar' in s: threading.Thread(target=conversar, daemon=True).start()
            if 'planeta:' in s: elegir(s.split('planeta:')[1].split('"')[0].split('\\')[0], mod)


# ------------------------------------------------------------------ Lumo conversa: agente propio de esta mesa (lumo_agente.py)
KM = {"sol": 696000, "mercurio": 2440, "venus": 6052, "tierra": 6371, "luna": 1737, "marte": 3390, "jupiter": 69911, "saturno": 58232, "urano": 25362, "neptuno": 24622}
UA = {"mercurio": 0.39, "venus": 0.72, "tierra": 1.0, "luna": 1.0, "marte": 1.52, "jupiter": 5.2, "saturno": 9.5, "urano": 19.2, "neptuno": 30.1}
AGENTE = ['out/lumo-agente.json', 'Lumo']      # archivo con el id del agente y nombre que se muestra (otra escena los cambia)
TOOLS_VOZ = ['ver_mesa', 'senalar', 'escribir_libreta', 'dibujar_libreta', 'borrar_libreta']
RESOLVER = [None]      # función(nombre, params) -> texto; por defecto, herramienta()
PEND = []      # avisos para Lumo (la persona tocó un planeta…): la página los recoge y se los pasa como contexto
MOD = [None]


def _pid(n):
    import unicodedata
    n = ''.join(c for c in unicodedata.normalize('NFD', str(n).lower().strip()) if unicodedata.category(c) != 'Mn'); return n if n in KM else None


def _col(pid): return (PLAN.get(pid) or {}).get('colores', DATOS['sol']['colores'])[1]
def _nom(pid): return (PLAN.get(pid) or {}).get('nombre', 'Sol')


def _nota(**kw):
    lc = E.get('lumo') or {}; base = {k: lc.get(k) for k in ('x', 'y', 'w', 'h')}; E['nota'] = {**base, "titulo": "Lumo", "lineas": [], "c": "#ffd34d", **kw}


def herramienta(nombre, a):
    """Las herramientas de Lumo, resueltas con lo que el rastreador sabe de la mesa."""
    if nombre == 'ver_mesa':
        ps = E.get('planetas') or []; sol = next((q for q in ps if q['id'] == 'sol'), None); platos = {}
        for q in ps: platos.setdefault(tuple(q['pl'][:2]), []).append(q['n'])
        dist = lambda q: round(float(np.hypot(q['mm'][0] - sol['mm'][0], q['mm'][1] - sol['mm'][1])) / 10) if sol else None
        return json.dumps({"hay_sistema_solar": bool(ps), "platos": [{"plato": i + 1, "contiene": v} for i, v in enumerate(platos.values())],
                           "planetas": sorted([{"nombre": q['n'], "cm_al_sol_en_la_mesa": dist(q)} for q in ps if q['id'] != 'sol'], key=lambda d: d['cm_al_sol_en_la_mesa'] or 0),
                           "seleccionado": _nom(E['sel']) if E.get('sel') else None, "libreta": bool(E.get('lumo')), "cohete_platano": bool(E.get('cohete')), "lamina_impresa": True}, ensure_ascii=False)
    if nombre == 'senalar':
        pid = _pid(a.get('planeta'))
        if not pid: return 'No conozco ese planeta. Usa: ' + ', '.join(KM)
        E['sel'] = pid; lam = MOD[0]['lamina'].get(pid)
        if lam: ql = canvas(lam[:2])[0]; E['lam'] = {"x": float(ql[0]), "y": float(ql[1]), "r": lam[2] * escala(lam[:2]) + 4}
        return f'{_nom(pid)} quedó iluminado en la mesa y marcado en la lámina.'
    if nombre == 'escribir_libreta':
        lin = []
        for r in (a.get('renglones') or [])[:6]: lin += renglones(str(r))
        _nota(titulo=str(a.get('titulo') or 'Lumo')[:22], lineas=lin[:8], c=_col(E['sel']) if E.get('sel') else '#ffd34d'); return 'Escrito en la libreta.'
    if nombre == 'dibujar_libreta':
        ids = [q for q in (_pid(x) for x in (a.get('planetas') or [])) if q][:5]; tipo = 'distancias' if 'dist' in str(a.get('tipo', '')).lower() else 'tamanos'
        if len(ids) < 2: return 'Necesito al menos dos planetas.'
        if tipo == 'tamanos': mx = max(KM[i] for i in ids); items = [{"n": _nom(i), "v": (KM[i] / mx) ** 0.5, "c": _col(i), "t": f"{KM[i] * 2:,} km".replace(',', '.')} for i in ids]
        else:
            ids = [i for i in ids if i in UA]; mx = max(UA[i] for i in ids); items = [{"n": _nom(i), "v": UA[i] / mx, "c": _col(i), "t": f"{UA[i] * 150:.0f} mill. km"} for i in sorted(ids, key=lambda i: UA[i])]
        _nota(titulo=str(a.get('titulo') or ('Tamaños' if tipo == 'tamanos' else 'Distancias'))[:22], lineas=[], dib={"tipo": tipo, "items": items}); return 'Dibujado en la libreta.'
    if nombre == 'borrar_libreta': E['nota'] = None; return 'Libreta en blanco.'
    return 'Herramienta desconocida.'


PAGINA = r"""<!doctype html><meta charset="utf-8"><title>Lumo · WUI</title>
<style>body{font:15px system-ui;background:#111;color:#f4f4f2;margin:24px}button{font:600 16px system-ui;padding:12px 18px;border-radius:8px;border:0;cursor:pointer}
.partir{background:#ffd34d}.parar{background:#ff5a3c;color:#fff}#log{margin-top:16px;white-space:pre-wrap;font:13px ui-monospace,monospace;color:#a9a9a6}</style>
<h2 id="tit">Voz de la mesa</h2><p>Deja esta ventana abierta. La conversación parte y se corta desde el botón de la mesa (o aquí).</p>
<button id="b" class="partir">Hablar con Lumo</button><div id="log"></div>
<script type="module">
import { Conversation } from "https://cdn.jsdelivr.net/npm/@elevenlabs/client@0.12.2/+esm";
const b = document.getElementById('b'), L = document.getElementById('log'); let conv = null, partiendo = false;
const log = (...a) => { L.textContent = (new Date().toLocaleTimeString() + ' ' + a.join(' ') + '\n' + L.textContent).slice(0, 4000); };
const post = (r, d) => fetch(r, { method: 'POST', body: JSON.stringify(d) }).then(x => x.text()).catch(e => String(e));
const tool = n => async p => { const r = await post('/tool', { name: n, params: p || {} }); log('herramienta', n, JSON.stringify(p || {}), '→', r.slice(0, 120)); return r; };
const CFG = await (await fetch('/config')).json(); document.getElementById('tit').textContent = CFG.nombre + ' · WUI'; document.title = CFG.nombre + ' · WUI';
function pinta() { b.className = conv ? 'parar' : 'partir'; b.textContent = conv ? 'Cortar' : 'Hablar con ' + CFG.nombre; post('/estado', { activa: !!conv }); }
window.partir = async () => { if (conv || partiendo) return 'ya'; partiendo = true;
  try { const { signed_url, error } = await (await fetch('/signed')).json(); if (!signed_url) throw new Error(error || 'sin URL firmada');
    conv = await Conversation.startSession({ signedUrl: signed_url, connectionType: 'websocket',
      clientTools: Object.fromEntries(CFG.herramientas.map(n => [n, tool(n)])),
      onMessage: m => { post('/trans', { quien: m.source, texto: m.message }); log(m.source + ':', m.message); },
      onDisconnect: () => { conv = null; pinta(); log('conversación terminada'); }, onError: e => log('error', String(e && e.message || e)) });
    log('conectado'); } catch (e) { log('no partió:', String(e && e.message || e)); conv = null; }
  partiendo = false; pinta(); return conv ? 'ok' : 'fallo'; };
window.cortar = async () => { if (conv) { const c = conv; conv = null; try { await c.endSession(); } catch (e) {} } pinta(); return 'cortado'; };
b.onclick = () => conv ? cortar() : partir();
setInterval(async () => { if (!conv) return; try { const d = await (await fetch('/avisos')).json(); for (const a of d) { conv.sendUserMessage ? conv.sendUserMessage(a) : conv.sendContextualUpdate(a); log('aviso:', a); } } catch (e) {} }, 600);
setInterval(() => post('/estado', { activa: !!conv }), 2000);      // el botón de la mesa siempre refleja el estado real
pinta();
</script>"""


class Voz(BaseHTTPRequestHandler):
    def _r(self, cuerpo, tipo='application/json'):
        b = cuerpo if isinstance(cuerpo, bytes) else str(cuerpo).encode(); self.send_response(200); self.send_header('Content-Type', tipo + '; charset=utf-8'); self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        ruta = self.path.split('?')[0]
        if ruta == '/signed':
            try:
                aid = json.load(open(H / AGENTE[0]))['agent_id']
                r = urllib.request.urlopen(urllib.request.Request('https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?agent_id=' + aid, headers={'xi-api-key': os.environ['ELEVENLABS_API_KEY']}), timeout=15)
                self._r(r.read())
            except Exception as ex: self._r(json.dumps({"error": type(ex).__name__}))
        elif ruta == '/avisos': d = PEND[:]; del PEND[:]; self._r(json.dumps(d, ensure_ascii=False))
        elif ruta == '/config': self._r(json.dumps({"nombre": AGENTE[1], "herramientas": TOOLS_VOZ}, ensure_ascii=False))
        else: self._r(PAGINA, 'text/html')

    def do_POST(self):
        try: d = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0)) or 0) or b'{}')
        except Exception: d = {}
        ruta = self.path.split('?')[0]
        if ruta == '/tool':
            try: r = (RESOLVER[0] or herramienta)(d.get('name'), d.get('params') or {})
            except Exception as ex: r = 'No se pudo: ' + type(ex).__name__
            log('lumo usa', d.get('name'), json.dumps(d.get('params') or {}, ensure_ascii=False)[:90]); self._r(r, 'text/plain')
        elif ruta == '/trans':
            if d.get('texto'): E['trans'] = {"quien": 'lumo' if d.get('quien') == 'ai' else 'tu', "lineas": renglones(str(d['texto']), 58)[-3:], "t": time.time()}
            self._r('ok', 'text/plain')
        elif ruta == '/estado': E['conv'] = bool(d.get('activa')); self._r('ok', 'text/plain')
        else: self._r('?', 'text/plain')

    def log_message(self, *a): pass


class Poses(BaseHTTPRequestHandler):
    def do_GET(self):
        b = json.dumps(E).encode(); self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Access-Control-Allow-Origin', '*'); self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass


JS = marca.PISTA_JS + r"""
const c = document.getElementById('c'), x = c.getContext('2d'); const W = 1000, H = 563, K = 2; c.width = W * K; c.height = H * K; x.scale(K, K);
let E = { planetas: [] }; const P = {}; let manos = [], tM = 0; const toque = {}; let fallos = 0;
async function leer() { try { const r = await fetch('http://127.0.0.1:9302/poses', { cache: 'no-store' }); E = await r.json(); fallos = 0; } catch (e) { fallos++; } setTimeout(leer, 60); }
let nivel = 0, nivelS = 0; if (wui.onAudio) wui.onAudio(a => { nivel = a.level || 0; });
leer(); wui.onHands(h => { manos = (h || []).filter(m => m.tip); tM = performance.now(); });
const ESTR = Array.from({ length: 90 }, (_, i) => [(i * 137.5) % W, (i * 89.3 + 40 * Math.sin(i)) % H, 0.6 + (i % 5) * 0.28, i]);
function esfera(p, t, sol) {
  const [luz, tono, oscuro = tono] = p.c;
  const r = p.r;
  if (!(r > 0)) return;
  const anillos = p.tx === 'anillos';
  x.save();
  x.translate(p.x, p.y);
  x.scale(r, r);
  function anillo(desde, hasta) {
    x.save();
    x.rotate(-.34);
    for (const [radio, grosor, color] of [[1.48, .13, luz], [1.68, .045, tono]]) {
      x.beginPath(); x.ellipse(0, 0, radio, radio * .32, 0, desde, hasta);
      x.strokeStyle = color; x.lineWidth = grosor; x.stroke();
    }
    x.restore();
  }
  if (anillos) anillo(Math.PI, 2 * Math.PI);
  x.save();
  x.beginPath(); x.arc(0, 0, 1, 0, 2 * Math.PI); x.clip();
  x.fillStyle = tono; x.fillRect(-1, -1, 2, 2);
  const giro = Math.sin(t / 12000) * .14;
  if (['bandas', 'anillos', 'nubes', 'hielo'].includes(p.tx)) {
    const bandas = [[-.79,.15,luz],[-.53,.10,oscuro],[-.32,.20,luz],[-.02,.11,oscuro],[.19,.23,luz],[.57,.12,oscuro],[.75,.10,luz]];
    x.globalAlpha = p.tx === 'hielo' ? .25 : .78;
    for (const [y,h,col] of bandas) {
      x.fillStyle = col; x.beginPath(); x.moveTo(-1.2, y);
      x.bezierCurveTo(-.72+giro,y+.17, .40+giro,y-.21, 1.2,y+.02);
      x.lineTo(1.2,y+h); x.bezierCurveTo(.34+giro,y+h-.21,-.68+giro,y+h+.12,-1.2,y+h); x.fill();
    }
    if (p.id === 'jupiter') {
      x.globalAlpha=1; x.fillStyle='#bc5540'; x.beginPath(); x.ellipse(.23+giro,.32,.29,.12,-.1,0,2*Math.PI); x.fill();
      x.strokeStyle='#f2b084'; x.lineWidth=.035; x.stroke();
    }
  } else if (p.tx === 'tierra') {
    x.fillStyle='#59c69c';
    x.beginPath(); x.moveTo(-.95+giro,-.52); x.bezierCurveTo(-.6,-.93,.1,-.65,-.17+giro,-.35);
    x.quadraticCurveTo(-.53,-.2,-.25,-.05); x.bezierCurveTo(.05,.06,-.1,.40,-.43,.78); x.lineTo(-.54,.18); x.quadraticCurveTo(-.96,.01,-.95+giro,-.52); x.fill();
    x.beginPath(); x.moveTo(.34+giro,-.48); x.bezierCurveTo(.76,-.78,1.28,-.57,1.08,-.16); x.lineTo(.77,.03); x.quadraticCurveTo(.66,.60,.47,.48); x.lineTo(.28,.02); x.fill();
    x.strokeStyle='rgba(250,252,247,.74)'; x.lineWidth=.07; x.beginPath(); x.moveTo(-.94,.31); x.bezierCurveTo(-.65,.05,-.1,.48,.22,.13); x.stroke();
  } else {
    for (const [cx,cy,rad] of [[-.45,-.34,.23],[.15,.27,.24],[.58,-.42,.09],[-.35,.54,.13],[.54,.53,.075],[-.03,-.66,.065]]) {
      x.beginPath(); x.ellipse(cx+giro,cy,rad,rad*.8,-.25,0,2*Math.PI); x.fillStyle=oscuro; x.globalAlpha=.58; x.fill();
      x.beginPath(); x.ellipse(cx+giro,cy,rad,rad*.8,-.25,.22,2.6); x.strokeStyle=luz; x.lineWidth=.035; x.stroke();
    }
  }
  x.globalAlpha=1;
  const ang = sol ? Math.atan2(sol.y-p.y,sol.x-p.x) : -2.2;
  const dx=Math.cos(ang), dy=Math.sin(ang);
  const luzDia=x.createRadialGradient(dx*.5,dy*.5,.04,dx*.42,dy*.42,1.52);
  luzDia.addColorStop(0,'rgba(255,253,232,.47)'); luzDia.addColorStop(.53,'rgba(255,255,255,0)');
  luzDia.addColorStop(1,'rgba(10,12,34,.78)'); x.fillStyle=luzDia; x.fillRect(-1,-1,2,2);
  x.restore();
  if (anillos) anillo(0, Math.PI);
  x.restore();
}
function tick(t) {
  const vis = E.visible ? Math.min(1, (Date.now() / 1000 - (E.t_vis || 0)) / 0.9) : 0; c.style.opacity = vis; const lg = document.getElementById('wui'); if (lg) lg.style.opacity = vis >= 1 ? 1 : 0;
  x.clearRect(0, 0, W, H);
  if (!E.visible) { pistasWui.clear(); requestAnimationFrame(tick); return; }
  for (const s of ESTR) { x.globalAlpha = 0.35 + 0.35 * Math.sin(t / 700 + s[3]); x.fillStyle = '#dfe8ff'; x.beginPath(); x.arc(s[0], s[1], s[2], 0, 6.283); x.fill(); } x.globalAlpha = 1;
  for (const p of E.planetas || []) { const a = P[p.id] || (P[p.id] = { ...p }); a.x += (p.x - a.x) * 0.35; a.y += (p.y - a.y) * 0.35; a.r = p.r; a.c = p.c; a.tx = p.tx; a.n = p.n; a.pl = p.pl; a.v = t; }
  const vivos = Object.values(P).filter(a => t - a.v < 1500); const sol = vivos.find(a => a.id === 'sol');
  if (sol) {
    for (const a of vivos) if (a.id !== 'sol' && a.id !== 'luna') { const R = Math.hypot(a.x - sol.x, a.y - sol.y); x.beginPath(); x.arc(sol.x, sol.y, R, 0, 6.283); x.lineWidth = E.sel === a.id ? 1.3 : .85; x.strokeStyle = E.sel === a.id ? 'rgba(225,229,255,.55)' : 'rgba(179,190,224,.30)'; x.stroke(); x.setLineDash([]); }
    nivelS += (Math.min(1, nivel * 3.5) - nivelS) * 0.3; const br = 1 + 1.1 * nivelS;      // el brillo del Sol sigue al sonido
    const g = x.createRadialGradient(sol.x, sol.y, sol.r * 0.2, sol.x, sol.y, sol.r * 1.9 * br); g.addColorStop(0, '#fff4c2'); g.addColorStop(0.45, '#ffd34d'); g.addColorStop(0.75, 'rgba(255,159,28,.55)'); g.addColorStop(1, 'rgba(255,159,28,0)');
    x.globalAlpha = Math.min(1, 0.55 + 0.9 * nivelS); x.fillStyle = g; x.beginPath(); x.arc(sol.x, sol.y, sol.r * 1.9 * br, 0, 6.283); x.fill(); x.globalAlpha = 1;
    x.save(); x.translate(sol.x, sol.y); x.rotate(t / 5000); x.strokeStyle = '#ffd34d'; x.lineWidth = 3; x.lineCap = 'round'; for (let k = 0; k < 14; k++) { x.rotate(6.283 / 14); const l = sol.r * (1.25 + 0.18 * Math.sin(t / 400 + k) + 0.9 * nivelS); x.beginPath(); x.moveTo(sol.r * 1.05, 0); x.lineTo(l, 0); x.stroke(); } x.restore();
  }
  for (const a of vivos) if (a.id !== 'sol') {
    esfera(a, quietudWui.matches ? 0 : t, sol);
    if (E.sel === a.id) {
      const ang = sol ? Math.atan2(sol.y-a.y, sol.x-a.x) : -Math.PI / 2;
      x.beginPath(); x.arc(a.x, a.y, a.r + 5, ang + .38, ang + Math.PI * 2 - .38);
      x.lineWidth = 1.8; x.lineCap = 'round'; x.strokeStyle = '#f4f4f2'; x.stroke();
    }
  }
  // etiquetas: una línea sale del planeta hasta fuera de su plato y el nombre va sobre la mesa
  const platos = []; for (const a of vivos) if (a.pl && !platos.some(q => q[0] === a.pl[0] && q[1] === a.pl[1])) platos.push(a.pl);
  const puestas = [];
  for (const a of vivos) { if (!a.pl) continue; a.pl = (E.planetas.find(q => q.id === a.id) || a).pl; const [cx, cy, R] = a.pl; let dx = a.x - cx, dy = a.y - cy; const d0 = Math.hypot(dx, dy) || 1; dx /= d0; dy /= d0;
    let mejor = null;
    for (const giro of [0, 0.35, -0.35, 0.7, -0.7, 1.05, -1.05, 1.5, -1.5]) { const ux = dx * Math.cos(giro) - dy * Math.sin(giro), uy = dx * Math.sin(giro) + dy * Math.cos(giro);
      for (const extra of [20, 34, 50]) { const lx = cx + ux * (R + extra), ly = cy + uy * (R + extra);
        if (lx < 30 || lx > W - 30 || ly < 14 || ly > H - 8) continue;
        if (platos.some(q => Math.hypot(lx - q[0], ly - q[1]) < q[2] + 12)) continue;
        if (puestas.some(q => Math.abs(lx - q[0]) < 46 && Math.abs(ly - q[1]) < 15)) continue;
        mejor = [lx, ly]; break; }
      if (mejor) break; }
    if (!mejor) continue; puestas.push(mejor); const m = a.et || (a.et = mejor.slice()); m[0] += (mejor[0] - m[0]) * 0.2; m[1] += (mejor[1] - m[1]) * 0.2;
    const ex = m[0] - a.x, ey = m[1] - a.y, el = Math.hypot(ex, ey) || 1; x.beginPath(); x.moveTo(a.x + ex / el * (a.r + 3), a.y + ey / el * (a.r + 3)); x.lineTo(m[0] - ex / el * 4, m[1] - ey / el * 4); x.lineWidth = 1.6; x.strokeStyle = 'rgba(244,244,242,.8)'; x.stroke();
    x.beginPath(); x.arc(m[0] - ex / el * 4, m[1] - ey / el * 4, 2.2, 0, 6.283); x.fillStyle = '#f4f4f2'; x.fill();
    x.font = '600 12.5px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center'; x.fillStyle = E.sel === a.id ? '#ffe98a' : '#f4f4f2'; x.fillText(a.n, m[0] + ex / el * 22, m[1] + ey / el * 9 + 4);
  }
  // tocar un planeta físico: la punta del dedo quieta sobre él
  const ahora = performance.now(); const vm = ahora - tM < 400 ? manos : [];
  for (const a of vivos) { const en = vm.some(m => Math.hypot(m.tip[0] - a.x, m.tip[1] - a.y) < a.r + 12); const s = toque[a.id] || (toque[a.id] = { t0: 0, hecho: false });
    if (en) { if (!s.t0) s.t0 = ahora; const f = Math.min(1, (ahora - s.t0) / 300); x.beginPath(); x.arc(a.x, a.y, a.r + 14, -1.57, -1.57 + 6.283 * f); x.lineWidth = 4; x.strokeStyle = '#fff'; x.stroke(); if (!s.hecho && f >= 1) { s.hecho = true; wui.emit('planeta:' + a.id); } } else { s.t0 = 0; s.hecho = false; } }
  if (E.cohete && E.cohete.eje && E.cohete.eje.length > 5) { const k = E.cohete, A = k.eje, n = A.length, T = (t / 1000) % 20, cl = v => Math.max(0, Math.min(1, v));
    const tg = i => { const a = A[Math.max(0, i - 1)], b = A[Math.min(n - 1, i + 1)], dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy) || 1; return [dx / l, dy / l]; };
    const lado = (i, s, f) => { const d = tg(i); return [A[i][0] - d[1] * s * A[i][2] * f, A[i][1] + d[0] * s * A[i][2] * f]; };
    const silueta = () => { x.beginPath(); k.poly.forEach((q, i) => i ? x.lineTo(q[0], q[1]) : x.moveTo(q[0], q[1])); x.closePath(); };
    const f1 = cl(T / 2.8), f2 = cl((T - 2.8) / 1.2), f3 = cl((T - 4.0) / 1.0), e2 = 1 - Math.pow(1 - f2, 3), lim = Math.round(f1 * (n - 1)), ojiva = Math.round(0.78 * (n - 1));
    // aletas (debajo del casco): nacen de la cola y se abren hacia atrás
    if (f2 > 0) for (const sg of [-1, 1]) { const b0 = lado(0, sg, 0.4), b1 = lado(3, sg, 0.46), d = tg(1), w = A[2][2], tip = [A[0][0] - d[0] * w * 0.55 * e2 - d[1] * sg * w * (0.45 + 0.5 * e2), A[0][1] - d[1] * w * 0.55 * e2 + d[0] * sg * w * (0.45 + 0.5 * e2)];
      x.beginPath(); x.moveTo(b1[0], b1[1]); x.quadraticCurveTo((b1[0] + tip[0]) / 2 - d[1] * sg * w * 0.18, (b1[1] + tip[1]) / 2 + d[0] * sg * w * 0.18, tip[0], tip[1]); x.lineTo(b0[0], b0[1]); x.closePath();
      const gf = x.createLinearGradient(b1[0], b1[1], tip[0], tip[1]); gf.addColorStop(0, '#ff3d1f'); gf.addColorStop(1, '#ff7a3c'); x.fillStyle = gf; x.globalAlpha = e2; x.fill(); x.lineWidth = 2; x.strokeStyle = '#ffffff'; x.stroke(); x.globalAlpha = 1; }
    // casco: paneles metálicos que siguen la curva, de la cola a la punta
    x.save(); silueta(); x.clip();
    for (let i = 0; i < lim; i++) { const a0 = lado(i, -1, 0.75), a1 = lado(i, 1, 0.75), b0 = lado(i + 1, -1, 0.75), b1 = lado(i + 1, 1, 0.75), roja = f1 >= 1 && i >= ojiva;
      const g = x.createLinearGradient(a0[0], a0[1], a1[0], a1[1]); if (roja) { g.addColorStop(0, '#ff2a12'); g.addColorStop(0.45, '#ff7a55'); g.addColorStop(1, '#ff2a12'); } else { const al = i % 2 ? 0.62 : 0.34; g.addColorStop(0, 'rgba(150,205,255,' + al * 0.7 + ')'); g.addColorStop(0.35, 'rgba(255,255,255,' + Math.min(1, al + 0.3) + ')'); g.addColorStop(1, 'rgba(150,205,255,' + al * 0.7 + ')'); }
      x.fillStyle = g; x.beginPath(); x.moveTo(a0[0], a0[1]); x.lineTo(b0[0], b0[1]); x.lineTo(b1[0], b1[1]); x.lineTo(a1[0], a1[1]); x.closePath(); x.fill(); x.lineWidth = 0.8; x.strokeStyle = g; x.stroke(); }
    x.lineWidth = 1.8; x.strokeStyle = 'rgba(255,255,255,.95)'; for (let i = 1; i < lim; i += 1) { const a0 = lado(i, -1, 0.8), a1 = lado(i, 1, 0.8); x.beginPath(); x.moveTo(a0[0], a0[1]); x.lineTo(a1[0], a1[1]); x.stroke(); }
    if (f1 >= 1) { x.beginPath(); for (let i = 1; i < ojiva; i++) { const q = lado(i, 1, 0.16); i === 1 ? x.moveTo(q[0], q[1]) : x.lineTo(q[0], q[1]); } x.lineWidth = 3; x.strokeStyle = '#ff3d1f'; x.stroke();      // franja de carrera a lo largo del casco
      const u = (t / 1700) % 1.3, gi = Math.min(n - 2, Math.floor(u * (n - 1))); if (u < 1) { const a0 = lado(gi, -1, 0.8), a1 = lado(gi, 1, 0.8); x.lineWidth = 9; x.strokeStyle = 'rgba(255,255,255,.95)'; x.beginPath(); x.moveTo(a0[0], a0[1]); x.lineTo(a1[0], a1[1]); x.stroke(); } }
    x.restore(); silueta(); x.lineWidth = 2; x.strokeStyle = f1 >= 1 ? '#e8eef8' : '#FFEC40'; x.shadowColor = x.strokeStyle; x.shadowBlur = 8; x.stroke(); x.shadowBlur = 0;
    // escotillas
    if (f2 > 0) for (const fr of [0.5, 0.64]) { const i = Math.round(fr * (n - 1)), r = A[i][2] * 0.27 * e2; const g = x.createRadialGradient(A[i][0] - r * 0.3, A[i][1] - r * 0.3, r * 0.1, A[i][0], A[i][1], r); g.addColorStop(0, '#ffffff'); g.addColorStop(0.45, '#38d4ff'); g.addColorStop(1, '#0a8fe0');
      x.beginPath(); x.arc(A[i][0], A[i][1], r, 0, 6.283); x.fillStyle = g; x.fill(); x.lineWidth = 2.6; x.strokeStyle = '#ffffff'; x.stroke(); }
    // tobera y llama en tres capas, con chispas
    if (f3 > 0) { const d = tg(0), w = A[1][2], n0 = lado(0, -1, 0.3), n1 = lado(0, 1, 0.3), ex = [A[0][0] - d[0] * w * 0.42, A[0][1] - d[1] * w * 0.42];
      x.beginPath(); x.moveTo(n0[0], n0[1]); x.lineTo(ex[0] + d[1] * w * 0.44, ex[1] - d[0] * w * 0.44); x.lineTo(ex[0] - d[1] * w * 0.44, ex[1] + d[0] * w * 0.44); x.lineTo(n1[0], n1[1]); x.closePath(); x.fillStyle = '#2f3649'; x.fill(); x.lineWidth = 1.4; x.strokeStyle = '#9aa7c0'; x.stroke();
      const fl = (0.82 + 0.12 * Math.sin(t / 43) + 0.08 * Math.sin(t / 19)) * f3;
      for (const [L, an, c0, c1] of [[3.4, 0.46, 'rgba(255,120,40,.85)', 'rgba(255,60,30,0)'], [2.4, 0.32, '#ffd34d', 'rgba(255,160,40,0)'], [1.3, 0.18, '#ffffff', 'rgba(255,240,170,0)']]) { const l = w * L * fl, px = ex[0] - d[0] * l, py = ex[1] - d[1] * l;
        const g = x.createLinearGradient(ex[0], ex[1], px, py); g.addColorStop(0, c0); g.addColorStop(1, c1); x.beginPath(); x.moveTo(ex[0] + d[1] * w * an, ex[1] - d[0] * w * an); x.quadraticCurveTo(ex[0] - d[0] * l * 0.5 + d[1] * w * an * 1.3, ex[1] - d[1] * l * 0.5 - d[0] * w * an * 1.3, px, py); x.quadraticCurveTo(ex[0] - d[0] * l * 0.5 - d[1] * w * an * 1.3, ex[1] - d[1] * l * 0.5 + d[0] * w * an * 1.3, ex[0] - d[1] * w * an, ex[1] + d[0] * w * an); x.closePath(); x.fillStyle = g; x.shadowColor = '#ff9f1c'; x.shadowBlur = 16; x.fill(); x.shadowBlur = 0; }
      for (let i = 0; i < 9; i++) { const a = ((t / 700 + i / 9) % 1), sx = Math.sin(i * 7.3) * w * 0.9 * a, dist = w * (2.6 + 3.2 * a); x.globalAlpha = (1 - a) * f3 * 0.9; x.fillStyle = i % 3 ? '#ffd34d' : '#ffffff'; x.beginPath(); x.arc(ex[0] - d[0] * dist + d[1] * sx, ex[1] - d[1] * dist - d[0] * sx, 1.9 * (1 - a) + 0.6, 0, 6.283); x.fill(); } x.globalAlpha = 1;
      const m = Math.round(0.35 * (n - 1)), dd = tg(m), lx = A[m][0] + dd[1] * A[m][2] * 1.5, ly = A[m][1] - dd[0] * A[m][2] * 1.5; x.font = '700 10.5px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center'; x.fillStyle = '#f4f4f2'; x.fillText('Plátano 1', lx, ly + 4); } }
  if (E.lam) { x.beginPath(); x.arc(E.lam.x, E.lam.y, E.lam.r + 2 * Math.sin(t / 220), 0, 6.283); x.lineWidth = 4; x.strokeStyle = '#ffe98a'; x.shadowColor = '#ffd34d'; x.shadowBlur = 16; x.stroke(); x.shadowBlur = 0; }
  // la libreta: Lumo y lo que cuenta, dentro de una sola página (nada cruza el espiral)
  const nb = E.nota || E.lumo;
  if (nb) { const w = nb.w || 200, h = nb.h || 150, px0 = nb.x - w * 0.46, px1 = nb.x - w * 0.05, top = nb.y - h * 0.36; const lx = px0 + 16, ly = top + 16 + 2 * Math.sin(t / 320);
    const g = x.createRadialGradient(lx, ly, 2, lx, ly, 19); g.addColorStop(0, '#fff7c9'); g.addColorStop(0.5, '#ffd34d'); g.addColorStop(1, 'rgba(255,211,77,0)'); x.fillStyle = g; x.beginPath(); x.arc(lx, ly, 19, 0, 6.283); x.fill();
    x.fillStyle = '#14183f'; const pe = (Math.sin(t / 1700) > 0.985) ? 0.6 : 2.8; x.beginPath(); x.ellipse(lx - 4, ly - 1, 1.9, pe, 0, 0, 6.283); x.ellipse(lx + 4, ly - 1, 1.9, pe, 0, 0, 6.283); x.fill();
    x.save(); x.beginPath(); x.rect(px0, nb.y - h * 0.46, px1 - px0, h * 0.92); x.clip();
    x.textAlign = 'left'; x.fillStyle = nb.c || '#ffe59b'; x.font = '600 13px "WUI Sans", Arial, sans-serif';
    const titulo = nb.titulo || 'Soy Lumo';
    x.fillText(titulo, lx + 20, top + 19, Math.max(24, px1 - lx - 22));
    const lineas = nb.lineas || ['Toca un planeta', 'y te cuento', 'su secreto.'];
    if (lineas.length) {
      x.fillStyle = '#f4f4f2'; x.font = '500 10.5px "WUI Sans", Arial, sans-serif';
      pistaWui(x, 'libreta', JSON.stringify([titulo, lineas]), lineas, px0 + 3, top + 43, 12, Math.max(35, px1 - px0 - 5), t);
    } else pistasWui.delete('libreta');
    if (nb.dib) { const it = nb.dib.items, aw = px1 - px0 - 12, y0 = ly + 34; x.font = '600 8px "WUI Sans", Arial, sans-serif';
      if (nb.dib.tipo === 'tamanos') { const rmax = Math.min(26, aw / (it.length * 1.9)); let cx0 = px0 + 8; it.forEach((d, i) => { const r = Math.max(2.2, rmax * d.v), cxx = cx0 + rmax; x.beginPath(); x.arc(cxx, y0 + rmax, r, 0, 6.283); x.fillStyle = d.c; x.fill(); x.fillStyle = '#f4f4f2'; x.textAlign = 'center'; x.fillText(d.n, cxx, y0 + rmax * 2 + 11); x.fillStyle = '#a9a9a6'; x.fillText(d.t, cxx, y0 + rmax * 2 + 21); cx0 += aw / it.length; }); }
      else { x.strokeStyle = '#a9a9a6'; x.lineWidth = 1.4; x.beginPath(); x.moveTo(px0 + 8, y0 + 8); x.lineTo(px0 + 8 + aw - 6, y0 + 8); x.stroke(); x.fillStyle = '#ffd34d'; x.beginPath(); x.arc(px0 + 8, y0 + 8, 5, 0, 6.283); x.fill();
        it.forEach((d, i) => { const px = px0 + 14 + (aw - 16) * d.v; x.beginPath(); x.arc(px, y0 + 8, 3.6, 0, 6.283); x.fillStyle = d.c; x.fill(); x.save(); x.translate(px, y0 + 20); x.rotate(0.9); x.textAlign = 'left'; x.fillStyle = '#f4f4f2'; x.fillText(d.n + ' · ' + d.t, 0, 0); x.restore(); }); } }
    x.restore();
    // botón bajo la libreta, del lado de la persona
    const bw = 132, bh = 22; let bx = nb.x - bw / 2, by = nb.y + h / 2 + 5; if (by + bh > 468) by = nb.y - h / 2 - bh - 8; const en = vm.some(m => m.tip[0] > bx && m.tip[0] < bx + bw && m.tip[1] > by - 4 && m.tip[1] < by + bh + 4); const sb = toque.__lumo || (toque.__lumo = { t0: 0, hecho: false });
    if (en) { if (!sb.t0) sb.t0 = ahora; if (!sb.hecho && ahora - sb.t0 > 300) { sb.hecho = true; wui.emit('lumo:hablar'); } } else { sb.t0 = 0; sb.hecho = false; }
    x.beginPath(); x.roundRect(bx, by, bw, bh, 5); x.fillStyle = E.conv ? '#ffd34d' : (en ? '#ffffff' : '#f4f4f2'); x.fill(); x.fillStyle = '#000'; x.font = '700 12px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center'; x.fillText(E.conv ? 'Lumo te escucha · cortar' : 'Hablar con Lumo', bx + bw / 2, by + 15.5);
    if (E.conv) { x.beginPath(); x.arc(lx, ly, 23 + 4 * Math.sin(t / 180), 0, 6.283); x.lineWidth = 2; x.strokeStyle = '#ffd34d'; x.stroke(); } }
  // transcripción: lo último que dijo Lumo (o tú), en una franja del lado de la persona
  if (E.trans && E.conv && (Date.now() / 1000 - E.trans.t) < 14) { const tl = E.trans.lineas, ty = 472 - tl.length * 14; x.font = '600 11.5px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center';
    const tw = Math.max(...tl.map(l => x.measureText(l).width)) + 22; x.fillStyle = 'rgba(0,0,0,.82)'; x.beginPath(); x.roundRect(540 - tw / 2, ty - 13, tw, tl.length * 14 + 9, 6); x.fill();
    x.fillStyle = E.trans.quien === 'lumo' ? '#ffe98a' : '#f4f4f2'; tl.forEach((l, i) => x.fillText(l, 540, ty + i * 14)); }
  if (!nb) pistasWui.delete('libreta');
  if (fallos > 20) { x.fillStyle = '#ff8a5c'; x.font = '600 12px Arial'; x.textAlign = 'left'; x.fillText('sin rastreador', 12, H - 12); }
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
"""


def proyectar_widget():
    w = {"type": "effect", "id": "solar-vivo", "shader": "html", "rect_mm": [0, 0, 1000, 563],
         "params": {"html": '<canvas id="c"></canvas>' + marca.firma(), "css": marca.fuente() + marca.CSS_CANVAS, "js": JS}}
    manos = {"type": "effect", "id": "manos", "shader": "hands", "params": {"skeleton": False, "trail": False}}
    log('widget:', pcx('project', {"request_id": "sv-%d" % time.time(), "widgets": [manos, w]})[:70])


if __name__ == '__main__':
    threading.Thread(target=_cam, daemon=True).start(); log('cámara en', CAM.round().tolist())
    if '--modelo' in sys.argv: hacer_modelo(); sys.exit(0)
    mod = json.load(open(H / 'solar-modelo.json'))
    # arranque (mesa sin proyección): ubicar los tres platos y emparejarlos con el modelo por cercanía a donde estaban
    g = vista(); enc = platos(g); log('platos al arrancar:', [np.round(c).tolist() for c, _ in enc])
    est = []
    for pl in mod['platos']:
        mejor = min(enc, key=lambda e: np.hypot(*(e[0] - pl['c0']))) if enc else (np.array(pl['c0']), pl['r']); est.append((np.array(mejor[0], float), pl['r']))
    ths = [pl['th0'] for pl in mod['platos']]
    par = None
    try:      # si cada plato está donde quedó la última vez, es el mismo plato (lo más confiable)
        mem = json.load(open(H / 'out/solar-poses.json')); usados = set(); e2 = []; t2 = []
        for k, pl in enumerate(mod['platos']):
            j = min((j for j in range(len(enc)) if j not in usados), key=lambda j: np.hypot(*(enc[j][0] - mem[k]['c'])))
            assert np.hypot(*(enc[j][0] - mem[k]['c'])) < 45; usados.add(j); e2.append((np.array(enc[j][0], float), pl['r'])); t2.append(punto(g, enc[j][0], enc[j][1], mem[k]['th']))
        par = (e2, t2); log('platos reconocidos por su última posición')
    except Exception: par = emparejar(g, enc, mod)
    if par: est, ths = par
    ths = [((t + math.pi) % (2 * math.pi) - math.pi) if t is not None else None for t in ths]
    threading.Thread(target=lambda: ThreadingHTTPServer(('127.0.0.1', 9302), Poses).serve_forever(), daemon=True).start()
    MOD[0] = mod; threading.Thread(target=lambda: ThreadingHTTPServer(('127.0.0.1', 9325), Voz).serve_forever(), daemon=True).start()
    if pagina_lumo('1') == 'sin-pestaña': abrir_pagina()
    elif pagina_lumo("document.getElementById('b') ? 'ok' : 'vieja'") != 'ok': pagina_lumo('location.reload()')      # no se recarga si ya es la página nueva: cortaría una conversación en curso
    lc, lt = libreta(); q = canvas(lc)[0]; s = escala(lc); E['lumo'] = {"x": float(q[0]), "y": float(q[1]), "w": lt[0] * s, "h": lt[1] * s}
    E['cohete'] = cohete()      # sin música de fondo: se acopla con el micrófono de la conversación
    publicar(mod, [(c, th or 0) for (c, _), th in zip(est, ths)]); proyectar_widget(); threading.Thread(target=eventos, args=(mod,), daemon=True).start()
    (H / 'out/lista.flag').write_text('1'); log('lista'); n = 0; t0 = time.time()
    from collections import deque
    LIB = [None]
    # un plato solo existe si se vio al encender (mesa sin proyección): así el brillo del propio Sol proyectado no se confunde con un plato
    perdido = [0 if any(np.hypot(*(e[0] - c)) < 45 for e in enc) else 10 ** 6 for c, _ in est]; buf = [deque(maxlen=30) for _ in est]; fijo = [(np.array(c, float), th or 0.0) for (c, _), th in zip(est, ths)]; UMB_MM, UMB_G = 3.0, math.radians(3.0)
    dang = lambda a, b: (a - b + math.pi) % (2 * math.pi) - math.pi
    while True:
        if not E.get('visible') and (H / 'out/visible.flag').exists(): E['visible'] = True; E['t_vis'] = time.time(); log('dale: escena visible')
        g = vista(); nuevos = platos(g, est); poses = []
        for k, (nv, (c, r)) in enumerate(zip(nuevos, est)):
            if nv is not None and np.hypot(*(nv[0] - c)) < 90 and not any(m != k and np.hypot(*(nv[0] - est[m][0])) < 70 for m in range(len(est))): c = np.array(nv[0], float); est[k] = (c, r)      # medida cruda (para buscar cerca en el próximo cuadro)
            perdido[k] = (0 if nv is not None else perdido[k] + 1) if perdido[k] < 10 ** 6 else perdido[k]
            if perdido[k] > 50: poses.append(None); continue      # el plato ya no está (≈1 s sin verlo): sus planetas se apagan
            th = punto(g, c, r, ths[k])
            if th is not None: ths[k] = th if ths[k] is None else ths[k] + dang(th, ths[k])
            buf[k].append((c[0], c[1], ths[k] or 0.0)); B = np.array(buf[k]); med = np.median(B[-5:], axis=0); fc, fth = fijo[k]
            if np.hypot(med[0] - fc[0], med[1] - fc[1]) > UMB_MM or abs(dang(med[2], fth)) > UMB_G: fijo[k] = (med[:2].copy(), float(med[2]))       # se está moviendo: seguirlo
            elif len(B) == 30 and B[:, :2].std(axis=0).max() < 0.7 and B[:, 2].std() < 0.012:                                                     # quieto medio segundo: asentar una vez
                m = B.mean(axis=0)
                if np.hypot(m[0] - fc[0], m[1] - fc[1]) > 0.8 or abs(dang(m[2], fth)) > 0.014: fijo[k] = (m[:2].copy(), float(m[2]))
            poses.append(fijo[k])
        publicar(mod, poses); n += 1
        if n % 60 == 0:      # la libreta, un par de veces por segundo (con el mismo criterio de quietud: solo si se movió más de 6 mm)
            lb = ver_libreta(g, est)
            if lb is not None and (LIB[0] is None or np.hypot(*(lb[0] - LIB[0])) > 6): LIB[0] = lb[0]; poner_libreta(*lb)
        if n % 100 == 0 and all(q is not None for q in poses): (H / 'out/solar-poses.json').write_text(json.dumps([{'c': [float(c[0]), float(c[1])], 'th': float(th)} for c, th in poses]))
        if n % 100 == 0: log('%.1f lecturas/s ·' % (100 / (time.time() - t0)), [None if q is None else (np.round(q[0]).tolist(), round(math.degrees(q[1]))) for q in poses]); t0 = time.time()
        time.sleep(0.01)

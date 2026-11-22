#!/usr/bin/env python3
"""Escena 3 · Laboratorio en vivo: la mesa conoce el inventario de la caja organizadora, alumbra lo que se le pide y un
agente de voz formal guía el trabajo (ElevenLabs, «WUI · Laboratorio (mesa v2.1)», ver lab_agente.py).

Usa la misma base que el sistema solar (solar_vivo.py): cámara, vista rectificada, servidor de poses (:9302), página de
voz (:9325) y widget vivo. Aquí cambia qué se detecta y qué herramientas tiene la voz.

   lab_vivo.py            foto limpia -> detección (pipeline.py con prompts-lab.json) -> proyecta y queda escuchando
Qué espera en la mesa (ver LAB.md): caja organizadora abierta, vaso con líquido, caja de viales, libreta abierta.
El inventario y la rejilla de la caja están en lab-modelo.json (códigos A1, A2… por fila y columna).
Lanzar con: doppler run -p platanus-hack-26 -c dev -- ../sl-scanner/.venv/bin/python lab_vivo.py   (o tecla L del dashboard)
"""
import json, subprocess, sys, threading, time, unicodedata
from http.server import ThreadingHTTPServer
from pathlib import Path
import marca_mesa as marca
import cv2, numpy as np
import solar_vivo as sv

H = Path(__file__).parent; PY = sys.executable; E = sv.E; log = sv.log
MOD = json.load(open(H / 'lab-modelo.json'))
OBJ = []          # objetos detectados (de out/objetos.json)
LUZ = [None]      # lo alumbrado: {"tipo": "celda"|"objeto", "id": …}


def norm(t): return ''.join(c for c in unicodedata.normalize('NFD', str(t).lower().strip()) if unicodedata.category(c) != 'Mn')


DET = {}      # lo que el detector encontró al encender: etiqueta -> caja en mm
REF_CAJA = [855, 380, 1217, 610]      # rectángulo (mm) de la caja organizadora en la foto donde se anotaron sus compartimentos
IM0 = [None]      # la foto rectificada del arranque (mesa sin luz)
VIALES = [None]                       # cuadritos de la caja de viales hallados por color: [{id, c, poly}]


def detectar_rapido():
    """Dónde está cada cosa AHORA: una foto rectificada y una llamada al detector (Grounding DINO, ya cargado en
    127.0.0.1:8805). Devuelve {etiqueta: [x0, y0, x1, y1] en mm} o None si el detector no responde."""
    import urllib.request
    sv.vista()
    if not DET and IM0[0] is None: time.sleep(1.0)      # primera vez: la cámara recién abrió; se espera a que asiente la exposición (si no, los colores salen mal)
    f = sv.F[0]; w = f.shape[1]; im = cv2.remap(f, sv._M[w][0], sv._M[w][1], cv2.INTER_LINEAR); ruta = str(H / 'out/lab-det.jpg'); cv2.imwrite(ruta, im, [cv2.IMWRITE_JPEG_QUALITY, 90])
    pr = json.load(open(H / 'prompts-lab.json'))
    try:
        r = urllib.request.Request('http://127.0.0.1:8805/detect', data=json.dumps({"image": ruta, "prompts": list(pr), "box_threshold": 0.22}).encode(), headers={"Content-Type": "application/json"})
        dets = json.loads(urllib.request.urlopen(r, timeout=25).read())["detections"]
    except Exception as ex: log('detector no disponible:', type(ex).__name__); return None, im
    mm = lambda b: [b[0] / sv.PP - 10, b[1] / sv.PP - 10, b[2] / sv.PP - 10, b[3] / sv.PP - 10]; ar = lambda b: (b[2] - b[0]) * (b[3] - b[1]); out = {}
    por = {}
    for d in sorted(dets, key=lambda d: -d['score']): por.setdefault(pr.get(d['prompt'], d['prompt']), []).append(mm(d['box']))
    vasos = sorted([b for k in ('vaso precipitado', 'vaso') for b in por.get(k, [])], key=ar, reverse=True); unicos = []
    for b in vasos:      # el mismo vaso puede salir con los dos nombres: se queda una caja por vaso
        if all(max(0, min(b[2], u[2]) - max(b[0], u[0])) * max(0, min(b[3], u[3]) - max(b[1], u[1])) < 0.6 * ar(b) for u in unicos): unicos.append(b)
    if unicos: out['vaso precipitado'] = unicos[0]      # el más grande es el precipitado
    if len(unicos) > 1: out['vaso'] = unicos[1]
    cajas = sorted(por.get('caja organizadora', []), key=ar, reverse=True)
    if cajas: out['caja organizadora'] = cajas[0]
    for k in ('placa con pantalla', 'libreta', 'plátano'):
        if por.get(k): out[k] = por[k][0]
    return out, im


def viales_por_color(im, zona):
    """Los cuadritos de la caja de viales: cada tira es una mancha de un color; se parte en 4 a lo largo. Si dos tiras del
    mismo tono están pegadas, la mancha sale del doble de ancho y se divide en dos."""
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV); Hh, S_, V_ = hsv[..., 0], hsv[..., 1], hsv[..., 2]; z = np.zeros(Hh.shape, bool)
    z[:] = True
    if zona is not None: x0, y0, x1, y1 = [int(v) for v in sv.a_px([[zona[0] - 6, zona[1] - 6], [zona[2] + 6, zona[3] + 6]]).ravel()]; z[max(y0, 0):y1, max(x0, 0):x1] = False      # zona = la organizadora: ahí adentro no se busca
    base_s = float(np.median(S_)); sat = (S_ > base_s + 16) & (V_ > 105) & z; out = []; k = 0
    TONOS = (("amarilla", (Hh > 20) & (Hh < 38) & (S_ > base_s + 30)), ("verde", (Hh >= 38) & (Hh < 86)), ("rosada", ((Hh >= 150) | (Hh < 8))))
    manchas = []
    for nombre, tono in TONOS:
        m = cv2.morphologyEx((sat & tono).astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)); cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        manchas += [c for c in cs if 900 < cv2.contourArea(c) / sv.PP ** 2 < 12000]
    for c in sorted(manchas, key=lambda c: cv2.boundingRect(c)[0]):
        (cx, cy), (rw, rh), ang = cv2.minAreaRect(c); L, A = max(rw, rh) / sv.PP, min(rw, rh) / sv.PP
        if not 70 < L < 140 or A < 16: continue      # una tira mide unos 105 mm de largo
        a = np.radians(ang if rw >= rh else ang + 90); u = np.array([np.cos(a), np.sin(a)]); v = np.array([-u[1], u[0]]); ctr = sv.a_mm((cx, cy)); mk = np.zeros(Hh.shape, np.uint8); cv2.drawContours(mk, [c], -1, 1, -1); bgr = cv2.mean(im, mask=mk)[:3]
        col = '#%02x%02x%02x' % tuple(int(min(255, q * 1.3)) for q in bgr[::-1]); nt = max(1, int(round(A / 27.0)))      # una tira mide unos 26 mm de ancho
        for t in range(nt):
            k += 1; v0, v1 = -A / 2 + A * t / nt, -A / 2 + A * (t + 1) / nt
            for j in range(4): out.append({"id": f"V{k}{j + 1}", "c": col, "poly": [(ctr + u * x + v * y).tolist() for x, y in ((-L / 2 + L * j / 4, v0), (-L / 2 + L * (j + 1) / 4, v0), (-L / 2 + L * (j + 1) / 4, v1), (-L / 2 + L * j / 4, v1))]})
    return out


def tiras_en(im):
    """Tiras sueltas (morada = P, celeste = T) en una imagen rectificada: letra -> (centro mm, polígonos de sus cajitas)."""
    RANGOS = {'P': ((119, 45, 60), (150, 255, 255)), 'T': ((88, 38, 95), (118, 255, 255))}; hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV); out = {}      # morada y celeste; la pipeta también es azul, pero es mucho más delgada y la descarta la proporción
    for letra, (lo, hi) in RANGOS.items():
        m = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        for o in OBJ:
            if o['etiqueta'].startswith('caja') or o['etiqueta'].startswith('vaso'): cv2.fillPoly(m, [np.round(sv.a_px(np.array(o['poligono_mm']))).astype(np.int32)], 0)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8)); m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE); mejor = None
        for c in cs:
            ar = cv2.contourArea(c) / (sv.PP ** 2)
            if not 1300 < ar < 7500: continue
            (cx, cy), (rw, rh), ang = cv2.minAreaRect(c); L, A = max(rw, rh) / sv.PP, min(rw, rh) / sv.PP
            if not (75 < L < 150 and 2.2 < L / max(A, 1) < 5.5) or ar / (L * A) < 0.55: continue
            if mejor is None or ar > mejor[0]: mejor = (ar, cx, cy, rw, rh, ang, L, A)
        if mejor is None: continue
        _, cx, cy, rw, rh, ang, L, A = mejor; a = np.radians(ang if rw >= rh else ang + 90); u = np.array([np.cos(a), np.sin(a)]); v = np.array([-u[1], u[0]]); c = sv.a_mm((cx, cy))
        if (abs(u[0]) > abs(u[1]) and u[0] < 0) or (abs(u[0]) <= abs(u[1]) and u[1] < 0): u = -u
        A = min(A, 34); n = sum(1 for q in MOD['tira']['celdas'] if q['id'][0] == letra) or 4
        out[letra] = (c, [[(c + u * x + v * y).tolist() for x, y in ((-L / 2 + L * k / n, -A / 2), (-L / 2 + L * (k + 1) / n, -A / 2), (-L / 2 + L * (k + 1) / n, A / 2), (-L / 2 + L * k / n, A / 2))] for k in range(n)])
    return out


def detectar(primera=False):
    """Foto actual -> pipeline -> objetos. Lo que sigue en su lugar conserva su máscara anterior (más limpia)."""
    global OBJ
    if MOD.get('fijos'):      # montaje anotado: no hace falta detectar (parte al instante y sin errores con lo transparente)
        t_ = time.time(); det, im = detectar_rapido(); DET.clear(); DET.update(det or {})
        VIALES[0] = viales_por_color(im, (det or {}).get('caja organizadora')) or None
        if VIALES[0]:      # la caja de viales es lo que encierra a sus tiras
            pv = np.array([q for v in VIALES[0] for q in v['poly']]); DET['caja de viales'] = [float(pv[:, 0].min()) - 6, float(pv[:, 1].min()) - 6, float(pv[:, 0].max()) + 6, float(pv[:, 1].max()) + 6]
        else: DET.pop('caja de viales', None)
        IM0[0] = im
        log('detección rápida: %.1f s ·' % (time.time() - t_), 'encontró', sorted(DET), '· viales', 0 if not VIALES[0] else len(VIALES[0]), '· anotados:', [f['n'] for f in MOD['fijos'] if f['n'] not in DET and not f['n'].startswith('tira') and f['n'] != 'pipeta'])
        OBJ = []
        for f in MOD['fijos']:
            if f['n'].startswith('tira'): continue
            q = np.array(f.get('poly') or [[f['rect'][0], f['rect'][1]], [f['rect'][2], f['rect'][1]], [f['rect'][2], f['rect'][3]], [f['rect'][0], f['rect'][3]]], float)
            if DET.get(f['n']):      # detectado ahora: manda su posición real
                b = DET[f['n']]; e = 0.08 if f['n'].startswith('vaso') else 0.0; dx, dy = (b[2] - b[0]) * e, (b[3] - b[1]) * e; q = np.array([[b[0] + dx, b[1] + dy], [b[2] - dx, b[1] + dy], [b[2] - dx, b[3] - dy], [b[0] + dx, b[3] - dy]], float)
            OBJ.append({"etiqueta": f['n'], "rol": f.get('rol'), "detectado": bool(DET.get(f['n'])), "poligono_mm": q.tolist(), "centro_mm": q.mean(0).tolist(), "tam_mm": [float(np.ptp(q[:, 0])), float(np.ptp(q[:, 1]))], "area_mm2": float(cv2.contourArea(q.astype(np.float32))), "score": 1})
        TIRAS.clear(); TIRAS.update({k: v[1] for k, v in tiras_en(IM0[0]).items()}) if IM0[0] is not None else None; log('tiras al arrancar:', sorted(TIRAS))
        publicar(); return
    sv.vista(); cv2.imwrite(str(H / 'out/limpia-4k.jpg'), sv.F[0], [cv2.IMWRITE_JPEG_QUALITY, 95])
    r = subprocess.run([PY, 'pipeline.py', 'una', '--dino', '--prompts', 'prompts-lab.json', '--foto', 'out/limpia-4k.jpg'], cwd=H, capture_output=True, text=True)
    try: nuevos = json.load(open(H / 'out/objetos.json'))
    except Exception: nuevos = []
    if not nuevos: log('detección sin objetos:', (r.stderr or '')[-120:]); return
    if not primera:
        for i, o in enumerate(nuevos):
            for q in OBJ:
                if q['etiqueta'] == o['etiqueta'] and abs(q['centro_mm'][0] - o['centro_mm'][0]) < 18 and abs(q['centro_mm'][1] - o['centro_mm'][1]) < 18: nuevos[i] = q; break
    OBJ = [o for o in nuevos if o.get('score', 1) >= 0.35]; publicar()


def caja():
    """La caja organizadora y su marco: centro, ejes y medias medidas (mm). None si no está."""
    o = next((x for x in OBJ if x['etiqueta'].startswith('caja organizadora')), None)
    if not o: return None
    (cx, cy), (w, h), a = cv2.minAreaRect(np.array(o['poligono_mm'], np.float32))
    if w < h: w, h, a = h, w, a + 90
    a = np.radians((a + 90) % 180 - 90); u = np.array([np.cos(a), np.sin(a)]); v = np.array([-u[1], u[0]]); m = MOD['caja'].get('margen', 0.04)
    return np.array([cx, cy]), u * w / 2 * (1 - m), v * h / 2 * (1 - m)


def celdas():
    """Polígonos (mm) de cada compartimento según lab-modelo.json. Fila A = la más lejana a la persona (y mayor)."""
    if MOD['caja'].get('celdas'):
        b = DET.get('caja organizadora') or REF_CAJA; sx, sy = (b[2] - b[0]) / (REF_CAJA[2] - REF_CAJA[0]), (b[3] - b[1]) / (REF_CAJA[3] - REF_CAJA[1])
        T_ = lambda x, y: [b[0] + (x - REF_CAJA[0]) * sx, b[1] + (y - REF_CAJA[1]) * sy]
        return [{"id": c['id'], "item": c.get('item', ''), "n": c.get('n', 1), "alias": c.get('alias', []), "poly": [T_(c['rect'][0], c['rect'][1]), T_(c['rect'][2], c['rect'][1]), T_(c['rect'][2], c['rect'][3]), T_(c['rect'][0], c['rect'][3])]} for c in MOD['caja']['celdas']]
    k = caja()
    if not k: return []
    c, U, V = k; filas = MOD['caja']['filas']; out = []
    if V[1] < 0: V = -V                      # +V hacia y mayor
    if U[0] > 0: U = -U                      # columna 1 a la izquierda de la persona (x mayor)… se voltea con "espejo"
    if MOD['caja'].get('espejo'): U = -U
    for i, fila in enumerate(filas):
        v0, v1 = 1 - 2 * i / len(filas), 1 - 2 * (i + 1) / len(filas)
        for j, cel in enumerate(fila):
            u0, u1 = -1 + 2 * j / len(fila), -1 + 2 * (j + 1) / len(fila)
            out.append({"id": f"{chr(65 + i)}{j + 1}", "item": cel.get('item', ''), "n": cel.get('n', 1), "alias": cel.get('alias', []),
                        "poly": [(c + U * a + V * b).tolist() for a, b in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]})
    return out


def tira():
    """Compartimentos de la tira de viales que está fuera de su caja: T1…Tn a lo largo de su eje (T1 a la izquierda de la persona)."""
    if MOD.get('tira', {}).get('celdas'):
        out = []; cuenta = {}
        for c in MOD['tira']['celdas']:
            k = cuenta.get(c['id'][0], 0); cuenta[c['id'][0]] = k + 1; vivos_ = TIRAS.get(c['id'][0])
            poly = vivos_[k] if vivos_ and k < len(vivos_) else [[c['rect'][0], c['rect'][1]], [c['rect'][2], c['rect'][1]], [c['rect'][2], c['rect'][3]], [c['rect'][0], c['rect'][3]]]
            out.append({"id": c['id'], "abierto": ABIERTO.get(c['id'], bool(c.get('abierto'))), "poly": poly})
        return out
    o = next((x for x in OBJ if x['etiqueta'].startswith('tira')), None)
    if not o: return []
    (cx, cy), (w, h), a = cv2.minAreaRect(np.array(o['poligono_mm'], np.float32))
    if w < h: w, h, a = h, w, a + 90
    a = np.radians((a + 90) % 180 - 90); u = np.array([np.cos(a), np.sin(a)]); v = np.array([-u[1], u[0]]); n = int(MOD.get('tira', {}).get('n', 8)); c = np.array([cx, cy])
    if u[0] > 0: u = -u
    if MOD.get('tira', {}).get('espejo'): u = -u
    out = []
    for k in range(n):
        a0, a1 = -w / 2 + w * k / n, -w / 2 + w * (k + 1) / n
        out.append({"id": f"T{k + 1}", "poly": [(c + u * x + v * y).tolist() for x, y in ((a0, -h / 2), (a1, -h / 2), (a1, h / 2), (a0, h / 2))], "abierto": (k + 1) in MOD.get('tira', {}).get('abiertos', [])})
    return out


PASO = [None]      # paso actual del protocolo: {n, total, texto, x, y} (x, y: dónde va el número, en canvas)
ERR = [None]       # error visible: {texto, t}
BUSCA = [None]     # búsqueda animada: {id, t}
PIP = [None]       # transferencia en curso: {"desde": "T1", "hasta": "T2", "ul": 50}
META = [None]      # meta de nivel del vaso (porcentaje) y última lectura
NIVEL = [None]
TEL = {"poly": None, "n": 0}


def telefono(g):
    """Un teléfono sobre la mesa: mancha oscura, rectangular, de unos 15 × 7,5 cm, fuera de la caja. Se mira en cada cuadro."""
    b = cv2.morphologyEx((g < 70).astype(np.uint8), cv2.MORPH_OPEN, np.ones((9, 9), np.uint8)); cs, _ = cv2.findContours(b, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE); k = caja()
    for c in cs:
        a = cv2.contourArea(c) / (sv.PP * sv.PP)
        if not 7500 < a < 15500: continue
        (cx, cy), (w, h), _a = cv2.minAreaRect(c); lado, corto = max(w, h) / sv.PP, min(w, h) / sv.PP
        if a / (lado * corto + 1) < 0.8 or not 1.6 < lado / max(corto, 1) < 2.4: continue
        ctr = sv.a_mm((cx, cy))
        if k is not None and abs((ctr - k[0]) @ (k[1] / np.linalg.norm(k[1]))) < np.linalg.norm(k[1]) and abs((ctr - k[0]) @ (k[2] / np.linalg.norm(k[2]))) < np.linalg.norm(k[2]): continue
        return sv.a_mm(cv2.boxPoints(((cx, cy), (w + 16, h + 16), _a)))
    return None


def publicar():
    cs = celdas(); E['objetos'] = [{"n": o['etiqueta'], "rol": o.get('rol'), "p": sv.canvas(o.get('poligono_proy_mm') or o['poligono_mm']).round(1).tolist()} for o in OBJ if o.get('area_mm2', 0) < 200000]
    E['celdas'] = [{"id": c['id'], "item": c['item'], "p": sv.canvas(c['poly']).round(1).tolist()} for c in cs]
    lib = next((o for o in OBJ if o['etiqueta'].startswith('libreta')), None)
    if lib: q = sv.canvas(lib['centro_mm'])[0]; s = sv.escala(lib['centro_mm']); E['lumo'] = {"x": float(q[0]), "y": float(q[1]), "w": max(lib['tam_mm']) * s, "h": min(lib['tam_mm']) * s}
    E['fichas'] = []
    for f in MOD.get('fichas', []):
        o = next((o for o in OBJ if o['etiqueta'] == f['de']), None)
        if o: b = np.array(o['poligono_mm']); q = sv.canvas([b[:, 0].max() + 14, (b[:, 1].min() + b[:, 1].max()) / 2])[0]; E['fichas'].append({"x": float(q[0]), "y": float(q[1]), "titulo": f['titulo'], "lineas": f['lineas']})
    ts = tira(); E['tira'] = [{"id": c['id'], "abierto": c['abierto'], "p": sv.canvas(c['poly']).round(1).tolist()} for c in ts]
    E['pip'] = PIP[0]; vs = next((o for o in OBJ if o['etiqueta'] == 'vaso'), None)
    if vs:
        b = np.array(vs.get('poligono_proy_mm') or vs['poligono_mm']); q = sv.canvas([[b[:, 0].max() + 16, b[:, 1].max() - 12], [b[:, 0].max() + 16, b[:, 1].min() + 40]])      # medidor al costado libre del vaso: de la base (cerca de la cámara) hacia arriba
        E['vaso'] = {"a": q[0].round(1).tolist(), "b": q[1].round(1).tolist(), "nivel": NIVEL[0], "meta": META[0]}
    else: E['vaso'] = None
    E['alerta'] = None if TEL['poly'] is None else {"p": sv.canvas(TEL['poly']).round(1).tolist(), "texto": "Objeto ajeno al experimento · retire el teléfono"}
    E['pipeta'] = None if PIPETA['ab'] is None or time.time() - PIPETA['t'] > 1.2 else [sv.canvas(q)[0].round(1).tolist() for q in PIPETA['ab']]
    E['hecho'] = dict(HECHO); E['pasos'] = len(MOD.get('protocolo', []))
    E['viales'] = ([{"id": v['id'], "c": v['c'], "p": sv.canvas(v['poly']).round(1).tolist()} for v in VIALES[0]] if VIALES[0]
                   else [{"id": v['id'], "c": v['c'], "p": sv.canvas([[v['rect'][0], v['rect'][1]], [v['rect'][2], v['rect'][1]], [v['rect'][2], v['rect'][3]], [v['rect'][0], v['rect'][3]]]).round(1).tolist()} for v in MOD.get('viales', [])])
    cj = next((o for o in OBJ if o['etiqueta'] == 'caja organizadora'), None)
    if cj:      # lista de inventario al lado de la caja (sobre su tapa, que queda del lado de la cámara)
        b = np.array(cj['poligono_mm']); q = sv.canvas([[b[:, 0].min() + 4, b[:, 1].max() + 14]])[0]; E['inv'] = {"x": float(q[0]), "y": float(q[1]), "items": [[c['id'], c['item'], c['n']] for c in cs if c['item']]}
    E['paso'] = PASO[0]; E['error'] = ERR[0] if ERR[0] and time.time() - ERR[0]['t'] < 5 else None; E['busca'] = BUSCA[0]
    E['luz'] = LUZ[0]; E['t'] = time.time(); E['ok'] = True; E['escena'] = 'lab'


def nivel_vaso():
    """Nivel aproximado del líquido: fracción de la silueta del vaso que se ve oscura, desde la base."""
    o = next((x for x in OBJ if x['etiqueta'] == 'vaso'), None)
    if not o: return None
    g = sv.vista(); q = np.array(o['poligono_mm'], np.float32); m = np.zeros(g.shape, np.uint8); cv2.fillPoly(m, [np.round(sv.a_px(q)).astype(np.int32)], 1); m = cv2.erode(m, np.ones((7, 7), np.uint8))
    ys, xs = np.nonzero(m)
    if len(ys) < 200: return None
    P = sv.a_mm(np.stack([xs, ys], 1)); d = np.hypot(*(P - sv.CAM[:2]).T); val = g[ys, xs].astype(float); bins = np.clip(((d - d.min()) / (np.ptp(d) + 1e-6) * 12).astype(int), 0, 11)
    med = np.array([np.median(val[bins == k]) if (bins == k).any() else 255 for k in range(12)]); osc = med < np.median(val) - 12; n = 0
    while n < 12 and osc[n]: n += 1
    return round(n / 12 * 100 / 5) * 5


def recorte_camara(P, margen):
    """Recorte de la foto de cámara que cubre el rectángulo de mesa que contiene los puntos P (mm), con margen."""
    from fp_rect import mapa
    f = sv.F[0]; mx, my = mapa(P[:, 0].min() - margen, P[:, 1].min() - margen, P[:, 0].max() + margen, P[:, 1].max() + margen, 0.2, f.shape[1] / 1280.0)
    x0, x1, y0, y1 = int(max(mx.min(), 0)), int(min(mx.max(), f.shape[1] - 1)), int(max(my.min(), 0)), int(min(my.max(), f.shape[0] - 1))
    return f[y0:y1, x0:x1].copy() if x1 - x0 > 20 and y1 - y0 > 20 else None


BASE = {"g": None, "m": None, "d": None}      # el vaso vacío al partir: contra eso se mide
JEV_HAY = [None]


def nivel_altura(g):
    """Altura del líquido sobre la altura del vaso. El vaso parte vacío (0): se compara cada tramo del vaso, desde su
    base, con cómo se veía vacío; el líquido oscuro lo oscurece. Devuelve porcentaje (de 5 en 5) o None."""
    o = next((x for x in OBJ if x['etiqueta'] == 'vaso'), None)
    if not o: return None
    if BASE['m'] is None:
        q = np.array(o['poligono_mm'], np.float32); m = np.zeros(g.shape, np.uint8); cv2.fillPoly(m, [np.round(sv.a_px(q)).astype(np.int32)], 1); m = cv2.erode(m, np.ones((15, 15), np.uint8))
        ys, xs = np.nonzero(m); P = sv.a_mm(np.stack([xs, ys], 1)); d = np.hypot(*(P - sv.CAM[:2]).T); BASE['m'] = (ys, xs); BASE['d'] = np.clip(((d - d.min()) / (np.ptp(d) + 1e-6) * 20).astype(int), 0, 19)
    ys, xs = BASE['m']; v = cv2.GaussianBlur(g, (0, 0), 2)[ys, xs].astype(float); med = np.array([np.median(v[BASE['d'] == k]) if (BASE['d'] == k).any() else 0 for k in range(20)])
    if BASE['g'] is None: BASE['g'] = med; return 0
    osc = (BASE['g'] - med) > 20; n = 0; hueco = 0
    for k in range(20):      # tramos oscuros seguidos desde la base (se tolera un tramo claro: brillos del vidrio)
        if osc[k]: n = k + 1; hueco = 0
        else:
            hueco += 1
            if hueco > 1: break
    return int(round(n / 20 * 100 / 5) * 5)


def verificar_jev():
    """Jev vigila el vaso: cada 2 s mira el recorte y dice si hay líquido. Si dice que no, una lectura baja vuelve a 0
    (evita que una sombra o una mano se lean como líquido)."""
    import jevc
    while True:
        time.sleep(2.0)
        try:
            o = next((x for x in OBJ if x['etiqueta'] == 'vaso'), None)
            if not o: continue
            im = recorte_camara(np.array(o['poligono_mm']), 4)
            if im is None: continue
            cv2.imwrite(str(H / 'out/jev-vaso.jpg'), im)
            r = jevc.decide([{"type": "text", "text": "A transparent drinking glass on a table, photographed from above at an angle. Glass walls, reflections and projected light are not liquid."}, jevc.imagen(im)],
                            {"hay": {"type": "boolean", "instructions": "Is there a clearly visible amount of dark liquid inside this glass (more than a thin film at the bottom)?"},
                             "mano": {"type": "boolean", "instructions": "Is a hand or another container covering the glass right now?"}}, timeout=5)
            a = r.get('answers') or {}
            if 'hay' in a and (a.get('mano') or 0) < 0.5: JEV_HAY[0] = a['hay'] > 0.5; E['jev_vaso'] = round(float(a['hay']), 2)
        except Exception as ex: log('jev (vaso):', type(ex).__name__); time.sleep(3)


ABIERTO = {}      # id de compartimento de tira -> True/False, según Jev
PREV = {}
PIPETA = {"ab": None, "t": 0}      # extremos de la pipeta (mm) y cuándo se vio
HECHO = {"desde": False, "hasta": False}


def mirar_tiras():
    """Cada 3 s Jev mira cada tira que está fuera de su caja y dice qué cajitas tienen la tapa abierta."""
    import jevc
    from fp_rect import mapa
    ORD = ["first", "second", "third", "fourth", "fifth", "sixth"]
    while True:
        time.sleep(3.0)
        try:
            grupos = {}
            for c in tira(): grupos.setdefault(c['id'][0], []).append(c)
            for letra, cs in grupos.items():
                P = np.array([q for c in cs for q in c['poly']]); vertical = np.ptp(P[:, 1]) > np.ptp(P[:, 0]); f = sv.F[0]
                im = recorte_camara(P, 34)
                if im is None: continue
                im = cv2.resize(im, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC); cv2.imwrite(str(H / f'out/jev-tira-{letra}.jpg'), im)
                q = {f"c{k}": {"type": "boolean", "instructions": f"Is the lid of the {ORD[k]} box (counting from the {'top' if vertical else 'left'}) flipped open?"} for k in range(len(cs))}
                r = jevc.decide([{"type": "text", "text": f"A strip of {len(cs)} small translucent plastic boxes joined in a {'column' if vertical else 'row'}, seen from above. Each box has a hinged lid. A closed lid sits flat on top of its box, so the strip outline stays a clean rectangle. An open lid is flipped out sideways and lies NEXT to its box as an extra translucent square sticking out of the strip outline. Ignore projected light and outlines."}, jevc.imagen(im)], q, timeout=6)
                a = r.get('answers') or {}; cambio = False
                for k, c in enumerate(cs):
                    v = a.get(f"c{k}")
                    if v is None: continue
                    ab = v > 0.45; previo = PREV.get(c['id']); PREV[c['id']] = ab
                    if previo == ab and ABIERTO.get(c['id']) != ab: ABIERTO[c['id']] = ab; cambio = True
                if cambio: publicar(); log('tapas', letra, {c['id']: ABIERTO.get(c['id']) for c in cs})
        except Exception as ex: log('jev (tapas):', type(ex).__name__); time.sleep(4)


def ver_pipeta():
    """La pipeta (el lápiz azul): mancha azul, larga y delgada. Devuelve sus dos extremos en mm, o None."""
    f = sv.F[0]; w = f.shape[1]
    if w not in sv._M: return None
    im = cv2.remap(f, sv._M[w][0], sv._M[w][1], cv2.INTER_LINEAR); hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 0] > 98) & (hsv[..., 0] < 126) & (hsv[..., 1] > 110) & (hsv[..., 2] > 45) & (hsv[..., 2] < 235)).astype(np.uint8)
    for o in OBJ:      # lo azul que no es la pipeta (tiras y caja de viales) no cuenta
        if o['etiqueta'].startswith('tira') or o['etiqueta'].startswith('caja de viales'): cv2.fillPoly(m, [np.round(sv.a_px(np.array(o['poligono_mm']))).astype(np.int32)], 0)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)); cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE); mejor = None
    for c in cs:
        if cv2.contourArea(c) < 350: continue
        (cx, cy), (rw, rh), ang = cv2.minAreaRect(c); L, A = max(rw, rh), min(rw, rh)
        if 45 < L / sv.PP < 190 and L > 3.2 * max(A, 1) and (mejor is None or L > mejor[0]):
            a = np.radians(ang if rw >= rh else ang + 90); d = np.array([np.cos(a), np.sin(a)]) * L / 2; mejor = (L, sv.a_mm((cx - d[0], cy - d[1])), sv.a_mm((cx + d[0], cy + d[1])))
    return None if mejor is None else (mejor[1], mejor[2])


TIRAS = {}      # letra -> lista de polígonos (mm) de sus compartimentos, seguidos por color


def seguir_tiras():
    """Las tiras sueltas se siguen por color, unas tres veces por segundo. Parten de donde se vieron con la mesa sin luz
    y cada lectura nueva solo vale si está a menos de 15 cm de la anterior."""
    while True:
        time.sleep(0.3)
        try:
            f = sv.F[0]
            if f is None or f.shape[1] not in sv._M: continue
            vistas = tiras_en(cv2.remap(f, sv._M[f.shape[1]][0], sv._M[f.shape[1]][1], cv2.INTER_LINEAR)); cambio = False
            for letra, (c, polys) in vistas.items():
                viejo = TIRAS.get(letra)
                if viejo is None: continue
                d = float(np.hypot(*(np.mean(viejo, axis=(0, 1)) - c)))
                if 4 < d < 150: TIRAS[letra] = polys; cambio = True
            if cambio: publicar()
        except Exception as ex: log('tiras:', type(ex).__name__, ex); time.sleep(2)


def seguir_pipeta():
    """Hilo propio: la pipeta se busca en cada cuadro de la cámara (≈25 veces por segundo)."""
    ult = None
    while True:
        f = sv.F[0]
        if f is None or f is ult: time.sleep(0.005); continue
        ult = f
        try: pp = ver_pipeta()
        except Exception: pp = None
        if pp is not None: PIPETA['ab'] = pp; PIPETA['t'] = time.time()
        E['pipeta'] = None if PIPETA['ab'] is None or time.time() - PIPETA['t'] > 0.8 else [sv.canvas(q)[0].round(1).tolist() for q in PIPETA['ab']]


def buscar(que):
    q = norm(que); mejor = None
    for c in celdas():
        campos = [norm(c['item'])] + [norm(a) for a in c['alias']]
        if any(q in f or f in q or any(p and p in f for p in q.split()) for f in campos if f): mejor = c; break
    return mejor


def herramienta(nombre, a):
    if nombre == 'ver_mesa':
        return json.dumps({"objetos": [{"nombre": o['etiqueta'], "posicion_cm": [round(o['centro_mm'][0] / 10), round(o['centro_mm'][1] / 10)]} for o in OBJ], "caja_organizadora": caja() is not None,
                           "compartimentos": len(celdas()), "cajitas_abiertas": [c['id'] for c in tira() if c['abierto']], "cajitas_cerradas": [c['id'] for c in tira() if not c['abierto']], "alumbrado": LUZ[0], "protocolo_pasos": len(MOD.get('protocolo', []))}, ensure_ascii=False)
    if nombre == 'inventario':
        return json.dumps([{"codigo": c['id'], "contiene": c['item'] or 'vacío', "unidades": c['n']} for c in celdas()] or MOD['caja']['filas'], ensure_ascii=False)
    if nombre == 'buscar':
        c = buscar(a.get('que', ''))
        if not c and 'tel' in norm(a.get('que', '')): return 'El teléfono no es parte del experimento.'
        if not c: return f"No hay «{a.get('que')}» en el inventario."
        LUZ[0] = {"tipo": "celda", "id": c['id']}; BUSCA[0] = {"id": c['id'], "t": time.time()}; publicar(); return f"Está en el compartimento {c['id']}: {c['item']}, {c['n']} unidad(es). Ya está alumbrado."
    if nombre == 'alumbrar':
        t = norm(a.get('objetivo', '')); c = next((c for c in celdas() if norm(c['id']) == t), None)
        tc = next((c for c in tira() if norm(c['id']) == t), None)
        if tc: LUZ[0] = {"tipo": "tira", "id": tc['id']}; publicar(); return f"Compartimento {tc['id']} de la tira alumbrado."
        if c: LUZ[0] = {"tipo": "celda", "id": c['id']}; publicar(); return f"Compartimento {c['id']} alumbrado ({c['item'] or 'vacío'})."
        o = next((o for o in OBJ if t == norm(o['etiqueta'])), None) or next((o for o in OBJ if t and (t in norm(o['etiqueta']) or norm(o['etiqueta']) in t)), None)
        if o: LUZ[0] = {"tipo": "objeto", "id": o['etiqueta']}; publicar(); return f"{o['etiqueta']} alumbrado."
        return 'No encuentro eso en la mesa. Objetos: ' + ', '.join(o['etiqueta'] for o in OBJ)
    if nombre == 'apagar_luz': LUZ[0] = None; PIP[0] = None; publicar(); return 'Luz apagada.'
    if nombre == 'transferir':
        ids = [c['id'] for c in tira()]; d, h = norm(a.get('desde', '')).upper(), norm(a.get('hasta', '')).upper()
        if not ids: return 'No veo la tira de viales fuera de su caja.'
        if d not in ids or h not in ids: return 'Compartimentos de la tira: ' + ', '.join(ids)
        PIP[0] = {"desde": d, "hasta": h, "ul": a.get('microlitros')}; HECHO.update(desde=False, hasta=False); LUZ[0] = None; publicar(); return f"Transferencia marcada en la mesa: de {d} a {h}" + (f", {a.get('microlitros')} microlitros." if a.get('microlitros') else '.')
    if nombre == 'meta_vaso': META[0] = int(a.get('porcentaje', 50)); publicar(); return f'Meta del vaso: {META[0]} por ciento. La mesa muestra el nivel en vivo junto al vaso.'
    if nombre == 'nivel_vaso':
        n = NIVEL[0]; return 'No veo el vaso o no puedo leerlo.' if n is None else f'Aproximadamente {n} por ciento.' + (f' La meta es {META[0]} por ciento.' if META[0] else '')
    if nombre == 'protocolo': return json.dumps([{"paso": i + 1, "instruccion": p['texto']} for i, p in enumerate(MOD.get('protocolo', []))], ensure_ascii=False)
    if nombre == 'paso':
        ps = MOD.get('protocolo', []); n = int(a.get('numero', 1))
        if not 1 <= n <= len(ps): return f'El protocolo tiene {len(ps)} pasos.'
        p = ps[n - 1]; r = herramienta('alumbrar', {"objetivo": p['objetivo']}) if p.get('objetivo') else ''
        if p.get('transferir'): r = herramienta('transferir', p['transferir'])
        if p.get('meta_vaso'): r = herramienta('meta_vaso', {"porcentaje": p['meta_vaso']}) + ' ' + herramienta('alumbrar', {"objetivo": 'vaso'})
        obj = (p.get('transferir') or {}).get('desde') or ('vaso' if p.get('meta_vaso') else p.get('objetivo')); pos = None
        for cnd in tira() + celdas():
            if obj and cnd['id'] == obj: pos = np.array(cnd['poly']).mean(0)
        o_ = next((o for o in OBJ if obj and o['etiqueta'] == obj), None)
        if o_ is not None: b_ = np.array(o_['poligono_mm']); pos = np.array([b_[:, 0].max() + 18, b_[:, 1].mean()])
        qp = sv.canvas(pos)[0] if pos is not None else None
        PASO[0] = {"n": n, "total": len(ps), "texto": p['texto'], "x": None if qp is None else float(qp[0]), "y": None if qp is None else float(qp[1])}; publicar()
        lc = E.get('lumo') or {}; E['nota'] = {**{k: lc.get(k) for k in ('x', 'y', 'w', 'h')}, "titulo": f"Paso {n} de {len(ps)}", "lineas": sv.renglones(p['texto'])[:8], "c": "#f4f4f2"}
        return f"Paso {n}: {p['texto']} {r}"
    if nombre == 'escribir_libreta':
        lin = []
        for r in (a.get('renglones') or [])[:6]: lin += sv.renglones(str(r))
        lc = E.get('lumo') or {}; E['nota'] = {**{k: lc.get(k) for k in ('x', 'y', 'w', 'h')}, "titulo": str(a.get('titulo') or 'Nota')[:22], "lineas": lin[:8], "c": "#f4f4f2"}; return 'Escrito en la libreta.'
    if nombre == 'borrar_libreta': E['nota'] = None; return 'Libreta en blanco.'
    return 'Herramienta desconocida.'


JS = marca.PISTA_JS + r"""
const c = document.getElementById('c'), x = c.getContext('2d'); const W = 1000, H = 563, K = 2; c.width = W * K; c.height = H * K; x.scale(K, K);
let E = {}; let manos = [], tM = 0; const toque = {}; let fallos = 0;
async function leer() { try { E = await (await fetch('http://127.0.0.1:9302/poses', { cache: 'no-store' })).json(); fallos = 0; } catch (e) { fallos++; } setTimeout(leer, 40); }
leer(); wui.onHands(h => { manos = (h || []).filter(m => m.tip); tM = performance.now(); });
const PAL = ['#7aa8ff', '#86f5a0', '#ffd34d', '#ff8a5c', '#d58cff', '#5fe0e0', '#ff7ab8', '#c8f06a'];
const cam = p => { x.beginPath(); p.forEach((q, i) => i ? x.lineTo(q[0], q[1]) : x.moveTo(q[0], q[1])); x.closePath(); };
const cen = p => [p.reduce((s, q) => s + q[0], 0) / p.length, p.reduce((s, q) => s + q[1], 0) / p.length];
function dentro(p, poly) { let d = false; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) { const a = poly[i], b = poly[j]; if ((a[1] > p[1]) !== (b[1] > p[1]) && p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]) d = !d; } return d; }
function tick(t) {
  const vis = E.visible ? Math.min(1, (Date.now() / 1000 - (E.t_vis || 0)) / 0.9) : 0; c.style.opacity = vis; const lg = document.getElementById('wui'); if (lg) lg.style.opacity = vis >= 1 ? 1 : 0; const ch = document.getElementById('chip'); if (ch) ch.style.opacity = vis;
  x.clearRect(0, 0, W, H);
  if (!E.visible) { pistasWui.clear(); requestAnimationFrame(tick); return; }
  const ahora = performance.now(), vm = ahora - tM < 400 ? manos : []; const luz = E.luz;
  // La referencia de mesa se queda en los bordes; el centro recibe la luz del objeto activo.
  x.strokeStyle = 'rgba(244,244,242,.33)'; x.lineWidth = 1;
  for (const [px, py, sx, sy] of [[16,30,1,1],[984,30,-1,1],[16,466,1,-1],[984,466,-1,-1]]) {
    x.beginPath(); x.moveTo(px + 14*sx, py); x.lineTo(px, py); x.lineTo(px, py + 14*sy); x.stroke();
  }
  const estado = E.paso ? 'Paso ' + E.paso.n + ' / ' + E.paso.total : 'Laboratorio listo';
  x.textAlign = 'left'; x.font = '500 12px "WUI Sans", Arial, sans-serif';
  x.fillStyle = '#f4f4f2'; x.beginPath(); x.arc(27, 478, 2.5, 0, Math.PI*2); x.fill(); x.fillText(estado, 38, 482);
  const tri = (cx_, cy_, r) => { x.beginPath(); x.moveTo(cx_, cy_ - r); x.lineTo(cx_ + r * 0.92, cy_ + r * 0.7); x.lineTo(cx_ - r * 0.92, cy_ + r * 0.7); x.closePath(); x.lineWidth = 2.2; x.strokeStyle = '#ffae80'; x.stroke(); x.fillStyle = '#ffae80'; x.fillRect(cx_ - 1.1, cy_ - r * 0.35, 2.2, r * 0.6); x.beginPath(); x.arc(cx_, cy_ + r * 0.48, 1.4, 0, 6.283); x.fill(); };
  for (const o of E.objetos || []) { const on = luz && luz.tipo === 'objeto' && luz.id === o.n; cam(o.p); x.lineWidth = on ? 4 : 1.8; x.strokeStyle = on ? '#ffffff' : 'rgba(244,244,242,.75)'; x.setLineDash(on ? [] : [7, 5]); x.lineDashOffset = 0; if (on) { x.shadowColor = '#fff'; x.shadowBlur = 16 + 8 * Math.sin(t / 200); x.fillStyle = 'rgba(255,255,255,.25)'; x.fill(); } x.stroke(); x.setLineDash([]); x.shadowBlur = 0;
    if (on || !(o.n.startsWith('caja organizadora') || o.n.startsWith('tira'))) { const q = cen(o.p); x.font = '700 10.5px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center'; x.fillStyle = on ? '#fff' : '#a9a9a6'; x.fillText(o.n.charAt(0).toUpperCase() + o.n.slice(1) + (o.rol ? ' · ' + o.rol : ''), q[0], Math.max(...o.p.map(v => v[1])) + 14); } }
  let ci = 0;
  for (const k of E.celdas || []) { const on = luz && luz.tipo === 'celda' && luz.id === k.id, mano = vm.some(m => dentro(m.tip, k.p)), col = PAL[ci++ % PAL.length]; cam(k.p);
    if (!on && k.item) { x.globalAlpha = (luz && luz.tipo === 'celda') ? 0.12 : 0.16; x.fillStyle = col; x.fill(); x.globalAlpha = 1; }
    if (on) { x.fillStyle = 'rgba(255,255,255,' + (0.55 + 0.25 * Math.sin(t / 180)) + ')'; x.shadowColor = '#fff'; x.shadowBlur = 22; x.fill(); x.shadowBlur = 0; x.lineWidth = 3.5; x.strokeStyle = '#fff'; }
    else { x.lineWidth = mano ? 3 : 1.6; x.strokeStyle = k.item ? col : 'rgba(244,244,242,.3)'; x.globalAlpha = (luz && luz.tipo === 'celda') ? 0.35 : 1; }
    x.stroke(); x.globalAlpha = 1; cam(k.p); x.lineWidth = 0;
    x.stroke(); const q = cen(k.p);
    if (E.busca && Date.now() / 1000 - E.busca.t < 1.5) { const pr = (Date.now() / 1000 - E.busca.t) / 1.5, tot = (E.celdas || []).length; if (Math.floor(pr * tot * 1.6) % tot === ci - 1) { cam(k.p); x.fillStyle = 'rgba(255,255,255,.85)'; x.fill(); } }
    if (on) { x.font = '700 12px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center'; x.fillStyle = '#000'; x.fillText(k.id, q[0], q[1] + 4); }
    if ((on || mano) && k.item) { x.font = '700 11px "WUI Sans", Arial, sans-serif'; k.item = k.id + ' · ' + k.item.replace(/^[A-D]\d · /, ''); const w = x.measureText(k.item).width + 14, yy = Math.max(...k.p.map(v => v[1])) + 20; x.fillStyle = '#f4f4f2'; x.fillRect(q[0] - w / 2, yy - 13, w, 18); x.fillStyle = '#000'; x.fillText(k.item, q[0], yy); } }
  const T = {}; for (const k of E.tira || []) { T[k.id] = k; const on = luz && luz.tipo === 'tira' && luz.id === k.id, pd = E.pip && E.pip.desde === k.id, ph = E.pip && E.pip.hasta === k.id; cam(k.p);
    if (pd || ph || on) { const pl_ = 0.45 + 0.4 * Math.sin(t / 170); x.fillStyle = pd ? 'rgba(122,168,255,' + pl_ + ')' : ph ? 'rgba(134,245,160,' + pl_ + ')' : 'rgba(255,255,255,' + pl_ + ')'; x.shadowColor = x.fillStyle; x.shadowBlur = 16; x.fill(); x.shadowBlur = 0; }
    if (!(pd || ph || on) && E.error && E.error.id === k.id) { x.fillStyle = 'rgba(255,90,60,' + (0.5 + 0.4 * Math.sin(t / 90)) + ')'; x.fill(); }
    x.lineWidth = k.abierto ? 2.6 : 1.2; x.strokeStyle = k.abierto ? '#86f5a0' : 'rgba(244,244,242,.5)'; if (k.abierto) { x.shadowColor = '#86f5a0'; x.shadowBlur = 8; } x.stroke(); x.shadowBlur = 0;
    if (k.abierto) { const qa = cen(k.p); x.font = '700 7.5px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center'; x.fillStyle = '#86f5a0'; if (k.id[0] === 'T') x.fillText('abierta', qa[0], Math.min(...k.p.map(v => v[1])) - 15); else x.fillText('abierta', Math.min(...k.p.map(v => v[0])) - 14, qa[1] + 13); } const q = cen(k.p), xs_ = k.p.map(v => v[0]), ys_ = k.p.map(v => v[1]), horiz = k.id[0] === 'T'; x.font = '700 9.5px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center'; x.fillStyle = (pd || ph || on) ? '#ffffff' : '#a9a9a6';
    if (horiz) x.fillText(k.id, q[0], Math.min(...ys_) - 5); else x.fillText(k.id, Math.min(...xs_) - 11, q[1] + 3); }
  if (E.pip && T[E.pip.desde] && T[E.pip.hasta]) { const a = cen(T[E.pip.desde].p), b = cen(T[E.pip.hasta].p), mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2, dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1, nx = -dy / L, ny = dx / L, alto = Math.max(26, L * 0.6), cx_ = mx + nx * alto, cy_ = my + ny * alto;
    x.beginPath(); x.moveTo(a[0], a[1]); x.quadraticCurveTo(cx_, cy_, b[0], b[1]); x.lineWidth = 2.6; x.setLineDash([2, 7]); x.lineCap = 'round'; x.strokeStyle = '#f4f4f2'; x.lineDashOffset = -t / 40; x.stroke(); x.setLineDash([]);
    for (let i = 0; i < 3; i++) { const u = ((t / 1400 + i / 3) % 1), px = (1 - u) * (1 - u) * a[0] + 2 * (1 - u) * u * cx_ + u * u * b[0], py = (1 - u) * (1 - u) * a[1] + 2 * (1 - u) * u * cy_ + u * u * b[1]; x.beginPath(); x.arc(px, py, 3.4, 0, 6.283); x.fillStyle = '#7aa8ff'; x.fill(); }
    x.font = '700 11px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center'; x.fillStyle = '#f4f4f2'; x.fillText(E.pip.desde + ' → ' + E.pip.hasta + (E.pip.ul ? ' · ' + E.pip.ul + ' µL' : ''), cx_, cy_ + (ny > 0 ? 16 : -8)); }
  if (E.vaso) { const a = E.vaso.a, b = E.vaso.b, lv = E.vaso.nivel, mt = E.vaso.meta; x.lineWidth = 6; x.lineCap = 'round'; x.strokeStyle = 'rgba(244,244,242,.25)'; x.beginPath(); x.moveTo(a[0], a[1]); x.lineTo(b[0], b[1]); x.stroke();
    const pt = f => [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
    if (lv != null) { const q = pt(lv / 100), ok = mt != null && Math.abs(lv - mt) <= 5, pas = mt != null && lv > mt + 5; x.strokeStyle = ok ? '#86f5a0' : pas ? '#ff5a3c' : '#7aa8ff'; x.beginPath(); x.moveTo(a[0], a[1]); x.lineTo(q[0], q[1]); x.stroke(); x.font = '700 12px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center'; x.fillStyle = x.strokeStyle; x.fillText(lv + ' %' + (ok ? ' · listo' : pas ? ' · se pasó' : ''), q[0] - 26, q[1] + 4); }
    if (mt != null) { const q = pt(mt / 100); x.lineWidth = 2; x.strokeStyle = '#f4f4f2'; x.beginPath(); x.moveTo(q[0] - 9, q[1]); x.lineTo(q[0] + 9, q[1]); x.stroke(); x.font = '600 9px "WUI Sans", Arial, sans-serif'; x.fillStyle = '#f4f4f2'; x.fillText('meta ' + mt + ' %', q[0] + 30, q[1] + 3); } x.lineCap = 'butt'; }
  if (E.alerta) { const al = E.alerta, pu = 0.55 + 0.45 * Math.sin(t / 130); cam(al.p); x.lineWidth = 5; x.strokeStyle = 'rgba(255,90,60,' + pu + ')'; x.shadowColor = '#ff5a3c'; x.shadowBlur = 22; x.stroke(); x.shadowBlur = 0; x.fillStyle = 'rgba(255,90,60,' + (0.18 * pu) + ')'; x.fill();
    const q = cen(al.p); x.font = '700 12.5px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center'; const w = x.measureText(al.texto).width + 18, yy = Math.max(...al.p.map(v => v[1])) + 24; x.fillStyle = '#ff5a3c'; x.fillRect(q[0] - w / 2, yy - 14, w, 20); x.fillStyle = '#000'; x.fillText(al.texto, q[0], yy); }
  (E.viales || []).forEach((v, i) => { cam(v.p); x.globalAlpha = 0.23; x.fillStyle = v.c; x.fill(); x.globalAlpha = 0.9; x.lineWidth = 1.3; x.strokeStyle = v.c; x.stroke(); x.globalAlpha = 1; });
  if (E.inv) { const it = E.inv.items, col = Math.ceil(it.length / 2); x.textAlign = 'left'; x.font = '700 10px "WUI Sans", Arial, sans-serif'; x.fillStyle = '#f4f4f2'; x.fillText('Inventario', E.inv.x - 150, E.inv.y); x.font = '600 8.5px "WUI Sans", Arial, sans-serif';
    it.forEach((d, i) => { const cx_ = E.inv.x - 150 + (i >= col ? 128 : 0), cy_ = E.inv.y + 13 + (i % col) * 11, on = luz && luz.tipo === 'celda' && luz.id === d[0]; x.fillStyle = on ? '#ffffff' : PAL[i % PAL.length]; x.fillText(d[0], cx_, cy_); x.fillStyle = on ? '#ffffff' : '#a9a9a6'; x.fillText(d[1] + ' ×' + d[2], cx_ + 17, cy_); }); }
  if (E.paso) { const ps = E.paso; if (ps.x != null) { x.beginPath(); x.arc(ps.x, ps.y, 13 + 1.5 * Math.sin(t / 220), 0, 6.283); x.fillStyle = '#ffffff'; x.shadowColor = '#fff'; x.shadowBlur = 12; x.fill(); x.shadowBlur = 0; x.fillStyle = '#000'; x.font = '800 16px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center'; x.fillText(ps.n, ps.x, ps.y + 5.5); }
    x.font = '500 15px "WUI Sans", Arial, sans-serif'; x.fillStyle = '#f4f4f2';
    const ancho = Math.min(640, x.measureText(ps.texto).width + 28);
    pistaWui(x, 'protocolo', ps.n + '/' + ps.texto, [ps.texto], 520 - ancho/2, 449, 18, ancho, t);
    for (let i = 1; i <= ps.total; i++) { x.beginPath(); x.arc(520 - (ps.total - 1) * 9 + (i - 1) * 18, 424, 4, 0, 6.283); x.fillStyle = i < ps.n ? '#b4b4b0' : i === ps.n ? '#ffffff' : 'rgba(244,244,242,.3)'; x.fill(); } }
  if (!E.paso) pistasWui.delete('protocolo');
  if (E.error) { x.font = '800 15px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center'; const ew = x.measureText(E.error.texto).width + 28, pu = 0.7 + 0.3 * Math.sin(t / 110); x.fillStyle = 'rgba(255,90,60,' + pu + ')'; x.beginPath(); x.roundRect(520 - ew / 2, 388, ew, 28, 6); x.fill(); x.fillStyle = '#000'; x.fillText(E.error.texto, 520, 407); tri(520 - ew / 2 - 16, 402, 11); tri(520 + ew / 2 + 16, 402, 11); }
  for (const f of E.fichas || []) { const fw = 104, fh = 22 + f.lineas.length * 12, fx = f.x - fw, fy = f.y - fh / 2; x.strokeStyle = '#f4f4f2'; x.lineWidth = 1.6; x.beginPath(); x.roundRect(fx, fy, fw, fh, 4); x.stroke(); x.fillStyle = '#FFEC40'; x.fillRect(fx, fy, 4, fh);
    x.textAlign = 'left'; x.font = '700 12px "WUI Sans", Arial, sans-serif'; x.fillStyle = '#f4f4f2'; x.fillText(f.titulo, fx + 10, fy + 15); x.font = '600 9px "WUI Sans", Arial, sans-serif'; x.fillStyle = '#a9a9a6'; f.lineas.forEach((l, i) => x.fillText(l, fx + 10, fy + 29 + i * 12)); }
  if (E.pipeta) { const [pa, pb] = E.pipeta; let punta = pb.slice(), cola = pa;      // la punta es el extremo más lejano a la mano que la sostiene
    if (vm.length) { const mn = vm[0].palm || vm[0].tip; if (Math.hypot(pa[0] - mn[0], pa[1] - mn[1]) > Math.hypot(pb[0] - mn[0], pb[1] - mn[1])) { punta = pa; cola = pb; } } else if (window.__punta) { if (Math.hypot(pa[0] - window.__punta[0], pa[1] - window.__punta[1]) < Math.hypot(pb[0] - window.__punta[0], pb[1] - window.__punta[1])) { punta = pa; cola = pb; } }
    const sp0 = window.__ps || (window.__ps = punta.slice()); if (Math.hypot(punta[0] - sp0[0], punta[1] - sp0[1]) > 60) { sp0[0] = punta[0]; sp0[1] = punta[1]; } else { sp0[0] += (punta[0] - sp0[0]) * 0.55; sp0[1] += (punta[1] - sp0[1]) * 0.55; } punta = sp0;
    window.__punta = punta; x.beginPath(); x.moveTo(cola[0], cola[1]); x.lineTo(punta[0], punta[1]); x.lineWidth = 2; x.strokeStyle = 'rgba(244,244,242,.7)'; x.stroke();
    x.beginPath(); x.arc(punta[0], punta[1], 7 + 2 * Math.sin(t / 160), 0, 6.283); x.lineWidth = 2.6; x.strokeStyle = '#ffffff'; x.shadowColor = '#fff'; x.shadowBlur = 10; x.stroke(); x.shadowBlur = 0; x.beginPath(); x.arc(punta[0], punta[1], 2.2, 0, 6.283); x.fillStyle = '#fff'; x.fill();
    const dx = punta[0] - cola[0], dy = punta[1] - cola[1], dl = Math.hypot(dx, dy) || 1; x.setLineDash([2, 6]); x.beginPath(); x.moveTo(punta[0], punta[1]); x.lineTo(punta[0] + dx / dl * 60, punta[1] + dy / dl * 60); x.lineWidth = 1.4; x.strokeStyle = 'rgba(255,255,255,.6)'; x.stroke(); x.setLineDash([]);
    let bajo = null, dmin = 1e9; for (const k of E.tira || []) { const q = cen(k.p), rr = Math.hypot(k.p[0][0] - q[0], k.p[0][1] - q[1]) + 5, d = Math.hypot(punta[0] - q[0], punta[1] - q[1]); if ((dentro(punta, k.p) || d < rr) && d < dmin) { dmin = d; bajo = k.id; } }
    const sp = toque.p || (toque.p = { id: null, t0: 0, hecho: null }); if (bajo !== sp.id) { sp.id = bajo; sp.t0 = ahora; } else if (bajo && sp.hecho !== bajo && ahora - sp.t0 > 150) { sp.hecho = bajo; wui.emit('punta:' + bajo); }
    if (bajo) { x.font = '700 11px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center'; x.fillStyle = '#fff'; x.fillText('pipeta en ' + bajo, punta[0], punta[1] - 14); } }
  if (E.pip && E.hecho) { for (const [cl, ok] of [[E.pip.desde, E.hecho.desde], [E.pip.hasta, E.hecho.hasta]]) if (ok && T[cl]) { const q = cen(T[cl].p); x.beginPath(); x.moveTo(q[0] - 5, q[1]); x.lineTo(q[0] - 1, q[1] + 4); x.lineTo(q[0] + 6, q[1] - 5); x.lineWidth = 3; x.strokeStyle = '#000'; x.stroke(); } }
  const nb = E.nota || E.lumo;
  if (nb && nb.x != null) { const w = nb.w || 200, h = nb.h || 150, px0 = nb.x - w * 0.46, px1 = nb.x - w * 0.05, top = nb.y - h * 0.36;
    x.save(); x.beginPath(); x.rect(px0, nb.y - h * 0.46, px1 - px0, h * 0.92); x.clip(); x.textAlign = 'left'; x.fillStyle = '#f4f4f2'; x.font = '700 13px "WUI Sans", Arial, sans-serif'; x.fillText(nb.titulo || 'Laboratorio', px0 + 6, top + 14);
    x.fillRect(px0 + 6, top + 20, 26, 2); x.font = '600 9.5px "WUI Sans", Arial, sans-serif'; (nb.lineas || ['Pregunte por un', 'componente o pida', 'el protocolo.']).forEach((l, i) => x.fillText(l, px0 + 6, top + 38 + i * 12)); x.restore();
    const pts_ = vm.map(m => m.tip).concat(E.pipeta && window.__punta ? [window.__punta] : []);
    const bw = 150, bh = 22; let bx = nb.x - bw / 2, by = nb.y + h / 2 + 5; if (by + bh > 468) by = nb.y - h / 2 - bh - 8;
    const en = pts_.some(q => q[0] > bx && q[0] < bx + bw && q[1] > by - 4 && q[1] < by + bh + 4); const sb = toque.b || (toque.b = { t0: 0, hecho: false });
    if (en) { if (!sb.t0) sb.t0 = ahora; if (!sb.hecho && ahora - sb.t0 > 220) { sb.hecho = true; wui.emit('voz:hablar'); } } else { sb.t0 = 0; sb.hecho = false; }
    const b2y = by + (by > nb.y ? bh + 6 : -(bh + 6)), en2 = pts_.some(q => q[0] > bx && q[0] < bx + bw && q[1] > b2y - 3 && q[1] < b2y + bh + 3), s2 = toque.e || (toque.e = { t0: 0, hecho: false });
    if (en2) { if (!s2.t0) s2.t0 = ahora; if (!s2.hecho && ahora - s2.t0 > 220) { s2.hecho = true; wui.emit('exp:iniciar'); } } else { s2.t0 = 0; s2.hecho = false; }
    x.beginPath(); x.roundRect(bx, b2y, bw, bh, 5); x.fillStyle = en2 ? '#ffffff' : '#d6d6d1'; x.fill(); x.fillStyle = '#000'; x.font = '700 11.5px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center'; x.fillText('Iniciar experimento', bx + bw / 2, b2y + 15.5);
    x.beginPath(); x.roundRect(bx, by, bw, bh, 5); x.fillStyle = E.conv ? '#ffffff' : (en ? '#ffffff' : '#f4f4f2'); x.fill(); x.fillStyle = '#000'; x.font = '700 11.5px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center'; x.fillText(E.conv ? 'Escuchando · cortar' : 'Hablar con el laboratorio', bx + bw / 2, by + 15.5); }
  if (E.trans && E.conv && (Date.now() / 1000 - E.trans.t) < 14) { const tl = E.trans.lineas, ty = 472 - tl.length * 14; x.font = '600 11.5px "WUI Sans", Arial, sans-serif'; x.textAlign = 'center';
    const tw = Math.max(...tl.map(l => x.measureText(l).width)) + 22; x.fillStyle = 'rgba(0,0,0,.82)'; x.beginPath(); x.roundRect(540 - tw / 2, ty - 13, tw, tl.length * 14 + 9, 6); x.fill(); x.fillStyle = '#f4f4f2'; tl.forEach((l, i) => x.fillText(l, 540, ty + i * 14)); }
  if (fallos > 20) { x.fillStyle = '#ff8a5c'; x.font = '600 12px Arial'; x.textAlign = 'left'; x.fillText('sin rastreador', 12, H - 12); }
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
"""


def iniciar_experimento():
    """Botón de la mesa: parte el protocolo en el paso 1 y abre la conversación con la voz para que guíe."""
    log('iniciar experimento'); herramienta('paso', {"numero": 1})
    if not E.get('conv'):
        sv.conversar()
        for _ in range(40):
            if E.get('conv'): break
            time.sleep(0.25)
    sv.PEND.append('(Pablo apretó «Iniciar experimento» en la mesa. Guíela desde el paso 1 del protocolo: llame a `protocolo`, diga la primera instrucción y avance con `paso` cuando ella confirme.)')


def eventos():
    try: cur = json.loads(sv.pcx('events_wait', {"cursor": 0, "timeout_s": 1})).get('cursor', 0)
    except Exception: cur = 0
    while True:
        try: d = json.loads(sv.pcx('events_wait', {"cursor": cur, "timeout_s": 20}))
        except Exception: time.sleep(2); continue
        cur = d.get('cursor', cur)
        for e in d.get('events', []):
            s_ = json.dumps(e, ensure_ascii=False)
            if 'voz:hablar' in s_: threading.Thread(target=sv.conversar, daemon=True).start()
            if 'exp:iniciar' in s_: threading.Thread(target=iniciar_experimento, daemon=True).start()
            if 'punta:' in s_ and PIP[0]:
                cid = s_.split('punta:')[1].split('"')[0].split('\\')[0]
                if cid not in (PIP[0]['desde'], PIP[0]['hasta']) or (cid == PIP[0]['hasta'] and not HECHO['desde']):
                    esp = PIP[0]['hasta'] if HECHO['desde'] else PIP[0]['desde']; ERR[0] = {"texto": f"Vial incorrecto ({cid}) · use el {esp}", "t": time.time(), "id": cid}; publicar(); log('error: vial incorrecto', cid)
                    subprocess.Popen(['afplay', str(H / 'cues/recuperacion.wav')])
                    if E.get('conv'): sv.PEND.append(f'(Error: Pablo puso la pipeta en el vial {cid}, que no corresponde. Dígale con calma que es el vial incorrecto y que use el {esp}.)')
                elif cid == PIP[0]['desde'] and not HECHO['desde']: HECHO['desde'] = True; publicar(); log('pipeta en el origen', cid); subprocess.Popen(['afplay', str(H / 'cues/guia.wav')])
                elif cid == PIP[0]['hasta'] and HECHO['desde'] and not HECHO['hasta']:
                    HECHO['hasta'] = True; ERR[0] = None; publicar(); log('pipeta en el destino', cid); subprocess.Popen(['afplay', str(H / 'cues/confirmacion.wav')])
                    if E.get('conv'): sv.PEND.append(f"(Pablo llevó la pipeta de {PIP[0]['desde']} a {PIP[0]['hasta']}: la transferencia está hecha. Confírmelo y siga con el paso siguiente.)")


if __name__ == '__main__':
    threading.Thread(target=sv._cam, daemon=True).start()
    sv.AGENTE[:] = ['out/lab-agente.json', 'Laboratorio']; sv.TOOLS_VOZ[:] = ['ver_mesa', 'inventario', 'buscar', 'alumbrar', 'apagar_luz', 'transferir', 'meta_vaso', 'nivel_vaso', 'protocolo', 'paso', 'escribir_libreta', 'borrar_libreta']; sv.RESOLVER[0] = herramienta
    E.clear(); E.update({"t": 0, "objetos": [], "celdas": [], "luz": None, "nota": None, "lumo": None, "conv": False, "trans": None, "ok": False, "visible": not __import__('os').environ.get('ESPERA'), "t_vis": 0})
    detectar(primera=True); log('objetos:', [o['etiqueta'] for o in OBJ], '· compartimentos:', len(celdas()))
    threading.Thread(target=lambda: ThreadingHTTPServer(('127.0.0.1', 9302), sv.Poses).serve_forever(), daemon=True).start()
    threading.Thread(target=lambda: ThreadingHTTPServer(('127.0.0.1', 9325), sv.Voz).serve_forever(), daemon=True).start()
    if sv.pagina_lumo('1') == 'sin-pestaña': sv.abrir_pagina()
    else: sv.pagina_lumo('location.reload()')
    chip = ''
    try:
        import base64 as b64m
        pl = next(o for o in OBJ if o['etiqueta'] == 'placa con pantalla'); b = np.array(pl['poligono_mm']); from fp_rect import mapa as mapa_
        f_ = sv.F[0]; mx_, my_ = mapa_(b[:, 0].min(), b[:, 1].min(), b[:, 0].max(), b[:, 1].max(), 3.0, f_.shape[1] / 1280.0); rec = cv2.remap(f_, mx_, my_, cv2.INTER_LINEAR)
        rec = cv2.convertScaleAbs(rec, alpha=1.5, beta=10); ok, buf = cv2.imencode('.jpg', rec, [cv2.IMWRITE_JPEG_QUALITY, 88])
        w_mm = b[:, 0].max() - b[:, 0].min(); h_mm = b[:, 1].max() - b[:, 1].min(); q = sv.canvas([[b[:, 0].min(), b[:, 1].min()], [b[:, 0].max(), b[:, 1].max()]])      # superpuesta: la foto cae sobre la propia placa; sc = sv.escala([b[:, 0].min(), b[:, 1].mean()])
        lx, ty = float(min(q[0][0], q[1][0])), float(min(q[0][1], q[1][1])); sc = sv.escala([b[:, 0].mean(), b[:, 1].mean()])
        chip = '<img id="chip" style="position:absolute;left:%.0fpx;top:%.0fpx;width:%.0fpx;height:%.0fpx;transform:rotate(180deg);border-radius:3px;opacity:0" src="data:image/jpeg;base64,%s">' % (lx, ty, w_mm * sc, h_mm * sc, b64m.b64encode(buf.tobytes()).decode())
    except Exception as ex: log('foto del chip:', type(ex).__name__, ex)
    w = {"type": "effect", "id": "lab-vivo", "shader": "html", "rect_mm": [0, 0, 1000, 563],
         "params": {"html": '<canvas id="c"></canvas>' + marca.firma() + chip, "css": marca.fuente() + marca.CSS_CANVAS, "js": JS}}
    log('widget:', sv.pcx('project', {"request_id": "lab-%d" % time.time(), "widgets": [{"type": "effect", "id": "manos", "shader": "hands", "params": {"skeleton": False, "trail": False}}, w]})[:70])
    threading.Thread(target=eventos, daemon=True).start(); NIVEL[0] = 0; threading.Thread(target=verificar_jev, daemon=True).start(); threading.Thread(target=seguir_pipeta, daemon=True).start();  (H / 'out/lista.flag').write_text('1'); threading.Thread(target=mirar_tiras, daemon=True).start()
    n = 0; ocupado = [False]; LEC = []
    def _det():
        ocupado[0] = True
        try: detectar()
        except Exception as ex: log('detección:', type(ex).__name__, ex)
        ocupado[0] = False
    while True:
        time.sleep(0.12); n += 1; g = sv.vista()
        if not E.get('visible') and (H / 'out/visible.flag').exists(): E['visible'] = True; E['t_vis'] = time.time(); log('dale: escena visible')
        t = telefono(g)      # el teléfono se ve en cada cuadro: tres seguidos (≈0,4 s) y salta la alerta
        if t is not None:
            TEL['n'] = min(TEL['n'] + 1, 3)
            if TEL['n'] == 3 and TEL['poly'] is None:
                TEL['poly'] = t; publicar(); log('alerta: teléfono en la mesa'); subprocess.Popen(['afplay', str(H / 'cues/recuperacion.wav')])
                if E.get('conv'): sv.PEND.append('(Pablo acaba de dejar su teléfono sobre la mesa de trabajo. Es un objeto ajeno al experimento: pídale con cortesía que lo retire.)')
            elif TEL['poly'] is not None and n % 8 == 0: TEL['poly'] = t; publicar()
        else:
            TEL['n'] = max(TEL['n'] - 1, 0)
            if TEL['n'] == 0 and TEL['poly'] is not None:
                TEL['poly'] = None; publicar(); log('teléfono retirado')
                if E.get('conv'): sv.PEND.append('(Pablo retiró el teléfono. Puede continuar.)')
        if n == 20: BASE['g'] = None; BASE['m'] = None      # referencia del vaso vacío, ya con la proyección puesta
        if n > 20 and n % 3 == 0:
            nv = nivel_altura(g); LEC.append(nv if nv is not None else 0); del LEC[:-5]; mv = sorted(LEC)[len(LEC) // 2]
            if JEV_HAY[0] is False and mv <= 25: mv = 0
            if mv != NIVEL[0]: NIVEL[0] = mv; publicar(); log('vaso:', mv, '%')
        if n % 200 == 0 and not ocupado[0]: threading.Thread(target=_det, daemon=True).start()

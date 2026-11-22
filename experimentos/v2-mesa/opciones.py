#!/usr/bin/env python3
"""(cabecera compartida con gato.py) Gato (tic-tac-toe) sobre la libreta: si hay una rejilla # dibujada a mano, la mesa juega.

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



# ---------------------------------------------------------------------------------------------------------------
# Opciones en la libreta: la mesa proyecta opciones sobre la página izquierda; la persona encierra una con el lápiz
# y Jev (modelo de decisión) dice cuál quedó encerrada. Lanzar con: doppler run -p platanus-hack-26 -c dev -- python opciones.py
OPC = [("Aprobar", "aprobado"), ("Rechazar", "rechazado"), ("Después", "pregunta")]


def a_mesa(T, p): q = T @ np.array([p[0], p[1], 1.0]); return q[:2]


def proyectar(T, pose, cajas, elegido, pregunta):
    svg = []
    tx = lambda x, y, s, size, col, cls: f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{col}" class="{cls}" text-anchor="middle" transform="rotate({pose[2] + 180:.1f} {x:.1f} {y:.1f})">{s}</text>'
    for k, (cx, cy, w, h) in enumerate(cajas):
        c = a_mesa(T, (cx, cy)); col = TINTA if elegido == k else PAPEL
        svg.append(tx(c[0], c[1] + 4, OPC[k][0], 14, col, "sans"))
        if elegido == k: svg.append(f'<circle cx="{c[0]:.1f}" cy="{c[1]:.1f}" r="{w / PP * 0.62:.1f}" fill="none" stroke="{TINTA}" stroke-width="2.6" pathLength="100" stroke-dasharray="100" class="dib"/>')
    alto_ = cajas[0][3]; ct = a_mesa(T, (cajas[0][0], cajas[-1][1] + alto_ * 1.25)); svg.append(tx(ct[0], ct[1], "¿Qué hago con el PR 482?", 9, PAPEL, "sans"))
    c0 = a_mesa(T, (cajas[0][0], cajas[0][1] - alto_ * 1.1)); svg.append(tx(c0[0], c0[1], pregunta, 7.5, PAPEL2, "sans"))
    for k, (cx, cy, w, h) in enumerate(cajas):   # casilla punteada alrededor de cada opción: ahí se encierra
        q = [a_mesa(T, p) for p in ((cx - w / 2, cy - h * .42), (cx + w / 2, cy - h * .42), (cx + w / 2, cy + h * .42), (cx - w / 2, cy + h * .42))]
        svg.append('<polygon points="%s" fill="none" stroke="%s" stroke-width="1.4" stroke-dasharray="3 6"/>' % (" ".join("%.1f,%.1f" % tuple(p) for p in q), PAPEL2))
    css = (f"html,body{{margin:0;background:transparent;overflow:hidden}}#w{{position:absolute;left:0;top:0;width:1400px;height:720px;transform-origin:0 0;transform:{M3D}}}svg{{position:absolute;left:-10px;top:-10px;width:1340px;height:710px}}"
           ".sans{font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;font-weight:700}.dib{animation:d .5s cubic-bezier(.35,.1,.45,1) both}@keyframes d{from{stroke-dashoffset:100}to{stroke-dashoffset:0}}")
    pcx('project', {"request_id": "opc-%d" % (time.time() * 10), "widgets": [{"type": "effect", "id": "opciones", "shader": "html", "rect_mm": [0, 0, 1000, 563], "params": {"html": f'<div id="w"><svg viewBox="-10 -10 1340 710" xmlns="http://www.w3.org/2000/svg">{"".join(svg)}</svg></div>', "css": css}}]})


if __name__ == '__main__':
    import jevc, sonido
    elegido = None; previo = None; firma = None
    while True:
        time.sleep(0.4); pose = libreta()
        if pose is None: continue
        im, T = enderezar(pose); h, w = im.shape[:2]
        # página izquierda (en la imagen enderezada): tres opciones apiladas
        px0, px1 = 0.61 * w, 0.87 * w; alto = 0.13 * h; cajas = [((px0 + px1) / 2, (0.32 + 0.16 * k) * h, (px1 - px0) * 0.9, alto) for k in range(3)]   # con margen: nada se sale de la hoja
        f = (tuple(np.round(pose[0] / 4)), round(pose[2] / 2), elegido)
        if f != firma: firma = f; proyectar(T, pose, cajas, elegido, "encierra una con el lápiz"); time.sleep(0.8); continue
        pag = cv2.rotate(im[int(0.15 * h):int(0.90 * h), int(0.53 * w):int(0.96 * w)], cv2.ROTATE_180)   # al derecho para leer
        r = jevc.decide([{"type": "text", "text": "One page of a paper notebook seen from above. Three words are projected on it with light: Aprobar, Rechazar, Después. A person may draw a loop or circle around ONE of them with a pen (dark or colored ink line going around the word)."}, jevc.imagen(pag)],
                        {"cual": {"type": "choice", "instructions": "Which projected word has a hand-drawn pen circle or loop drawn around it?", "criteria": {"ninguna": "no word is circled with pen ink", "aprobar": "the word Aprobar is circled with pen", "rechazar": "the word Rechazar is circled with pen", "despues": "the word Después is circled with pen"}},
                         "mano": {"type": "boolean", "instructions": "Is a hand or pen covering part of the page right now?"}})
        if 'answers' not in r: log('jev:', r.get('error')); time.sleep(2); continue
        a = r['answers']
        if (a.get('mano') or 0) > 0.5: previo = None; continue
        c = a['cual']['choice'] if (a['cual'].get('p') or 0) > 0.92 else 'ninguna'
        if c != previo: previo = c; globals()['iguales'] = 0; continue
        iguales = globals().get('iguales', 0) + 1; globals()['iguales'] = iguales
        if iguales < 3: continue                       # tres lecturas iguales seguidas antes de actuar
        k = {"aprobar": 0, "rechazar": 1, "despues": 2}.get(c)
        if k is not None and k != elegido:
            elegido = k; sonido.play('confirmacion'); log(f"opción encerrada: {OPC[k][0]} ({r['ms']} ms)"); subprocess.run([sys.executable, 'escritorio.py', '--estado', OPC[k][1]], cwd=H, capture_output=True)
        elif k is None and elegido is not None: elegido = None; log('página sin opción encerrada: vuelve la pregunta')

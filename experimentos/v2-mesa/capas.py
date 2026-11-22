#!/usr/bin/env python3
"""Capas que siguen a los objetos de la mesa (experimento v2).

Cada objeto seguido tiene su propia capa proyectada en mm reales de la mesa:
  libreta       -> recorte del skin generado (o un marco si no hay skin)
  lámina solar  -> su propia foto re-proyectada encima + marco animado alrededor
  caja de viales-> modelo 5×4 de celdas con una animación por cubito
  organizadora  -> modelo de compartimentos con animación lenta

Seguimiento sin parpadeo:
  - mientras algo se mueve (manos, sombras) NO se toca nada;
  - cuando la mesa vuelve a quedar quieta se compara con la foto de referencia: si la zona de un objeto sigue distinta,
    recién ahí se mide, y la medición usa un «foco de luz negra» solo sobre esa zona (el resto de la mesa sigue prendido);
  - si el objeto no aparece en su zona, se hace una medición de mesa completa (un negro corto) para buscarlo.

uso: capas.py [--skin skin/salidas/google-cyberlab.png --entrada skin/entrada-vivo.png]
"""
import base64, json, subprocess, sys, threading, time
from pathlib import Path

import cv2
import numpy as np

H = Path(__file__).parent
PCX = '/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'
sys.path.insert(0, str(H))
from fp_rect import mapa  # noqa: E402

X0, Y0, X1, Y1 = -10, -10, 1330, 700
met = json.load(open(H / 'fp-metric.json'))
Hm = np.array(met['mm_to_canvas']); Hm = Hm / Hm[2, 2]
M3D = "matrix3d(%s)" % ",".join("%.9g" % v for v in [Hm[0, 0], Hm[1, 0], 0, Hm[2, 0], Hm[0, 1], Hm[1, 1], 0, Hm[2, 1], 0, 0, 1, 0, Hm[0, 2], Hm[1, 2], 0, Hm[2, 2]])
BASE = f"html,body{{margin:0;background:transparent;overflow:hidden}}#w{{position:absolute;left:0;top:0;width:1400px;height:720px;transform-origin:0 0;transform:{M3D}}}"


def pcx(tool, args):
    return subprocess.run(['/usr/bin/python3', PCX, tool, json.dumps(args)], capture_output=True, text=True).stdout


def b64(img, ext, params):
    return base64.b64encode(cv2.imencode(ext, img, params)[1].tobytes()).decode()


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


class Cam:
    def __init__(self):
        self.c = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
        self.c.set(cv2.CAP_PROP_FRAME_WIDTH, 3840); self.c.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160)
        self.f = None
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            ok, f = self.c.read()
            if ok: self.f = f
            else: time.sleep(0.03)

    def norm(self, x0=X0, y0=Y0, x1=X1, y1=Y1, pp=1.0):
        while self.f is None: time.sleep(0.05)
        f = self.f.copy()
        mx, my = mapa(x0, y0, x1, y1, pp, f.shape[1] / 1280.0)
        return cv2.remap(f, mx, my, cv2.INTER_LINEAR)


ANG_VIALES = [0.0]   # último giro conocido de la caja de viales


# ---------- detección sobre foto limpia (1 px = 1 mm) ----------
def rect(c, org):
    (cx, cy), (w, h), ang = cv2.minAreaRect(c)
    if w < h: w, h = h, w; ang += 90
    return dict(c=np.array([cx, cy]) + org, tam=(float(w), float(h)), ang=((ang + 90) % 180) - 90, area=float(cv2.contourArea(c)),
                rectitud=float(cv2.contourArea(cv2.convexHull(c)) / (w * h + 1e-6)), poly=cv2.convexHull(c).reshape(-1, 2) + org)


def blobs(mask, org, amin):
    return [rect(c, org) for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0] if cv2.contourArea(c) > amin]


def detectar(im, org, cuales=("libreta", "lamina", "viales")):
    """Devuelve {nombre: pose} con lo que encuentre en la foto limpia `im` (origen `org` en mm)."""
    lab = cv2.cvtColor(im, cv2.COLOR_BGR2LAB); hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    L = cv2.GaussianBlur(lab[..., 0], (0, 0), 2); S = hsv[..., 1]
    k = lambda n: np.ones((n, n), np.uint8)
    out = {}
    if "libreta" in cuales:      # papel: claro, poco saturado, rectangular
        Sb = cv2.GaussianBlur(S, (0, 0), 2)   # el papel casi no tiene color (S≈17) y la mesa sí (S≈54)
        m = cv2.morphologyEx(((Sb < 36) & (L > 150)).astype(np.uint8), cv2.MORPH_OPEN, k(11)); m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k(25))
        c = [b for b in blobs(m, org, 30000) if b['area'] < 110000 and b['rectitud'] > 0.85 and b['tam'][1] > 150]
        if c: out["libreta"] = max(c, key=lambda b: b['rectitud'])
    if "lamina" in cuales:       # lámina del sistema solar: rectángulo oscuro de ~165×107 mm
        m = cv2.morphologyEx((L < 95).astype(np.uint8), cv2.MORPH_CLOSE, k(15)); m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k(15))
        c = [b for b in blobs(m, org, 9000) if b['area'] < 30000 and b['rectitud'] > 0.88 and 1.2 < b['tam'][0] / b['tam'][1] < 2.0 and 120 < b['tam'][0] < 240]
        if c: out["lamina"] = max(c, key=lambda b: b['rectitud'])
    if "viales" in cuales:       # caja de viales: bloque de celdas de colores saturados
        # las columnas amarilla, verde y magenta son lo más saturado de la mesa y quedan al centro de la caja 5×4:
        # de ahí salen centro y giro; el tamaño es el real de la caja (190 × 110 mm)
        m = cv2.morphologyEx(((S > 80) & (L > 110)).astype(np.uint8), cv2.MORPH_OPEN, k(5)); m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k(31))
        c = [b for b in blobs(m, org, 3000) if b['area'] < 12000 and b['rectitud'] > 0.75 and 80 < b['tam'][0] < 150 and 40 < b['tam'][1] < 90]
        if c:
            v = dict(max(c, key=lambda b: b['area'])); v['tam'] = (190.0, 110.0); v['ang'] = min((v['ang'] + d for d in (-180, -90, 0, 90, 180)), key=lambda a: abs(a - ANG_VIALES[0])); ANG_VIALES[0] = v['ang']; out["viales"] = v
    # la caja organizadora es transparente y todavía no hay un detector confiable: se coloca a mano (ver ORG_FIJA)
    return out


# ---------- capas ----------
def w(id_, html, css, js=None):
    p = {"html": f'<div id="w">{html}</div>', "css": BASE + css}
    if js: p["js"] = js
    return {"type": "effect", "id": id_, "shader": "html", "rect_mm": [0, 0, 1000, 563], "params": p}


def caja(p, extra=0):
    """CSS para un div centrado en la pose p (mm), girado, con su tamaño (+extra por lado)."""
    wd, hd = p['tam'][0] + 2 * extra, p['tam'][1] + 2 * extra
    return f"position:absolute;left:0;top:0;width:{wd:.1f}px;height:{hd:.1f}px;transform-origin:0 0;transform:translate({p['c'][0]:.1f}px,{p['c'][1]:.1f}px) rotate({p['ang']:.2f}deg) translate({-wd/2:.1f}px,{-hd/2:.1f}px)"


def capa_libreta(p, skin):
    if skin is None:
        return w("capa-libreta", '<div id="o"></div>', f"#o{{{caja(p, 6)};box-sizing:border-box;border:3px solid #00E5FF;border-radius:6px;box-shadow:0 0 18px #00E5FF}}")
    img, ref, _ = skin
    rot = ((p['ang'] - ref['ang'] + 90) % 180) - 90
    wd, hd = img.shape[1] / 2.0, img.shape[0] / 2.0
    css = f"#o{{position:absolute;left:0;top:0;width:{wd:.1f}px;height:{hd:.1f}px;transform-origin:0 0;transform:translate({p['c'][0]:.1f}px,{p['c'][1]:.1f}px) rotate({rot:.2f}deg) translate({-wd/2:.1f}px,{-hd/2:.1f}px)}}"
    return w("capa-libreta", f'<img id="o" src="data:image/webp;base64,{skin[2]}">', css)


def capa_lamina(p, foto):
    """La propia foto de la lámina, más viva, proyectada encima; alrededor un marco con órbita de luz."""
    f = cv2.convertScaleAbs(foto, alpha=1.5, beta=10)
    css = (f"#o{{{caja(p)}}}#o img{{width:100%;height:100%;display:block}}"
           f"#m{{{caja(p, 14)};box-sizing:border-box;border-radius:12px;padding:3px;background:conic-gradient(from var(--a),#FFD400,#FF7A1A,#00E5FF,#B6FF3C,#FFD400);"
           "-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude;animation:g 3s linear infinite}"
           "@property --a{syntax:'<angle>';inherits:false;initial-value:0deg}@keyframes g{to{--a:360deg}}"
           f"#n{{{caja(p, 30)};border-radius:20px;box-shadow:0 0 40px 6px rgba(255,180,40,.55)}}")
    return w("capa-lamina", f'<div id="n"></div><div id="m"></div><div id="o"><img src="data:image/jpeg;base64,{b64(f, ".jpg", [cv2.IMWRITE_JPEG_QUALITY, 90])}"></div>', css)


def rejilla(id_, p, cols, filas, colores, seg, margen=3):
    cel = "".join(f'<i style="animation-delay:{(c * 0.18 + r * 0.09) % seg:.2f}s;--c:{colores[c % len(colores)]}"></i>' for r in range(filas) for c in range(cols))
    css = (f"#o{{{caja(p)};display:grid;grid-template-columns:repeat({cols},1fr);grid-template-rows:repeat({filas},1fr);gap:{margen}px;box-sizing:border-box;padding:{margen}px}}"
           f"#o i{{border-radius:4px;background:var(--c);opacity:.12;animation:p {seg}s ease-in-out infinite;box-shadow:0 0 6px var(--c)}}"
           "@keyframes p{0%,100%{opacity:.10;transform:scale(.82)}18%{opacity:1;transform:scale(1)}40%{opacity:.25;transform:scale(.9)}}"
           f"#b{{{caja(p, 5)};box-sizing:border-box;border:2px solid rgba(255,255,255,.7);border-radius:6px}}")
    return w(id_, f'<div id="b"></div><div id="o">{cel}</div>', css)


def capa_viales(p):
    return rejilla("capa-viales", p, 5, 4, ["#FFFFFF", "#FFE600", "#5CFF5C", "#FF2BD6", "#FF8AC8"], 2.4)


def capa_organizadora(p):
    return rejilla("capa-organizadora", p, 5, 3, ["#00E5FF", "#7C9CFF", "#B6FF3C", "#FFD400", "#FF7A1A"], 4.0, 6)


def foco(c, r):
    """Foco de luz negra: disco negro con borde suave solo sobre la zona que se va a medir."""
    css = f"#o{{position:absolute;left:{X0}px;top:{Y0}px;width:{X1-X0}px;height:{Y1-Y0}px;background:radial-gradient(circle at {c[0]-X0:.0f}px {c[1]-Y0:.0f}px,#000 0,#000 {r:.0f}px,rgba(0,0,0,0) {r+45:.0f}px)}}"
    return w("capa-foco", '<div id="o"></div>', css)


NEGRO = {"type": "effect", "id": "capa-foco", "shader": "html", "rect_mm": [0, 0, 1000, 563], "params": {"html": "<div></div>", "css": "html,body{margin:0;background:#000}"}}

if __name__ == '__main__':
    cam = Cam()
    skin = None
    if '--skin' in sys.argv:
        gen = cv2.imread(sys.argv[sys.argv.index('--skin') + 1]); ent = cv2.imread(sys.argv[sys.argv.index('--entrada') + 1])
        gen = cv2.resize(gen, (ent.shape[1], ent.shape[0]), interpolation=cv2.INTER_AREA)
        r0 = detectar(cv2.resize(ent, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA), np.array([X0, Y0], float), ("libreta",)).get("libreta")
        if r0 is not None:
            mk = np.zeros(gen.shape[:2], np.uint8); cv2.fillPoly(mk, [((r0['poly'] - [X0, Y0]) * 2).astype(np.int32)], 255)
            R = int(max(r0['tam']) * 2 * 0.62); cx, cy = ((r0['c'] - [X0, Y0]) * 2).astype(int); x0, y0 = max(cx - R, 0), max(cy - R, 0)
            sub = gen[y0:cy + R, x0:cx + R]; a = cv2.GaussianBlur(mk[y0:cy + R, x0:cx + R], (0, 0), 1.2)
            # el recorte queda centrado en el centro de su propia imagen: se corrige el centro de referencia
            r0 = dict(r0); r0['c0'] = np.array([(x0 + sub.shape[1] / 2) / 2 + X0, (y0 + sub.shape[0] / 2) / 2 + Y0]); r0['off'] = r0['c0'] - r0['c']
            skin = (np.dstack([sub, a]), r0, b64(np.dstack([sub, a]), '.webp', [cv2.IMWRITE_WEBP_QUALITY, 88]))
            log('skin de la libreta listo', [round(v) for v in r0['tam']])

    def medir(c=None, r=None, cuales=("libreta", "lamina", "viales")):
        """Foto limpia de una zona (foco negro) o de toda la mesa (negro corto) y detección."""
        pcx('project', {"request_id": "cf-%d" % (time.time() * 10), "widgets": [NEGRO if c is None else foco(c, r)]}); time.sleep(0.42)
        if c is None: im, org = cam.norm(), np.array([X0, Y0], float)
        else:
            x0, y0 = max(c[0] - r, X0), max(c[1] - r, Y0); im, org = cam.norm(x0, y0, min(c[0] + r, X1), min(c[1] + r, Y1)), np.array([x0, y0], float)
        pcx('remove', {"request_id": "cfr-%d" % (time.time() * 10), "ids": ["capa-foco"]})
        return detectar(im, org, cuales), im, org

    def poner(nombre, p, im=None, org=None):
        if nombre == "libreta":
            q = dict(p)
            if skin is not None:   # el recorte se dibuja centrado en su imagen: aplicar el desfase girado
                th = np.radians(((p['ang'] - skin[1]['ang'] + 90) % 180) - 90); R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]]); q['c'] = p['c'] + R @ skin[1]['off']
            wd = capa_libreta(q, skin)
        elif nombre == "lamina":
            M = cv2.getRotationMatrix2D(tuple((p['c'] - org).tolist()), p['ang'], 1.0); rot = cv2.warpAffine(im, M, (im.shape[1], im.shape[0]))
            cx, cy = (p['c'] - org); hw, hh = p['tam'][0] / 2, p['tam'][1] / 2; foto = rot[int(max(cy - hh, 0)):int(cy + hh), int(max(cx - hw, 0)):int(cx + hw)]
            if foto.size == 0: return
            cv2.imwrite(str(H / 'out/lamina-foto.jpg'), foto); wd = capa_lamina(p, cv2.resize(foto, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
        elif nombre == "viales": wd = capa_viales(p)
        else: wd = capa_organizadora(p)
        pcx('project', {"request_id": "cp-%s-%d" % (nombre, time.time() * 10), "widgets": [wd]})

    def gris(): return cv2.GaussianBlur(cv2.cvtColor(cam.norm(pp=0.5), cv2.COLOR_BGR2GRAY), (0, 0), 1.5).astype(np.int16)
    def zona(p, g):   # recorte de la imagen a 0,5 px/mm alrededor del objeto
        r = max(p['tam']) * 0.75 + 30; x0, y0, x1, y1 = [int(max((v - o) * 0.5, 0)) for v, o in ((p['c'][0] - r, X0), (p['c'][1] - r, Y0), (p['c'][0] + r, X0), (p['c'][1] + r, Y0))]
        return g[y0:y1, x0:x1]

    time.sleep(1.0)
    obj, im, org = medir()
    if '--org' in sys.argv:   # --org cx,cy,ancho,alto,angulo  (mm): pose fija de la caja organizadora, no se sigue
        v = [float(x) for x in sys.argv[sys.argv.index('--org') + 1].split(',')]; fija = dict(c=np.array(v[:2]), tam=(v[2], v[3]), ang=v[4], area=v[2] * v[3]); poner('organizadora', fija)
    log('objetos:', {k: (v['c'].round(0).tolist(), [round(t) for t in v['tam']], round(v['ang'], 1)) for k, v in obj.items()})
    for k, p in obj.items(): poner(k, p, im, org)
    (H / 'out/capas.json').write_text(json.dumps({k: {"centro_mm": v['c'].round(1).tolist(), "tam_mm": [round(t, 1) for t in v['tam']], "angulo": round(v['ang'], 1)} for k, v in obj.items()}, ensure_ascii=False))
    ANIM = {"lamina": 45, "viales": 14, "organizadora": 14}   # mm que ocupa la animación por fuera del objeto
    def mascara(p, extra):
        m = np.zeros(((Y1 - Y0) // 2, (X1 - X0) // 2), np.uint8); box = cv2.boxPoints(((float(p['c'][0] - X0) * 0.5, float(p['c'][1] - Y0) * 0.5), ((p['tam'][0] + 2 * extra) * 0.5, (p['tam'][1] + 2 * extra) * 0.5), float(p['ang'])))
        cv2.fillPoly(m, [box.astype(np.int32)], 1); return m
    def mascaras():
        excl = np.zeros(((Y1 - Y0) // 2, (X1 - X0) // 2), np.uint8); zonas = {}
        for k, p in list(obj.items()) + ([("organizadora", fija)] if 'fija' in globals() else []):
            if k in ANIM:
                dentro = mascara(p, ANIM[k]); excl |= dentro
                if k in obj: zonas[k] = (mascara(p, ANIM[k] + 45) & ~dentro & 1).astype(bool)   # anillo: si el objeto se corre, entra al anillo
            else: zonas[k] = mascara(p, 20).astype(bool)
        return ~excl.astype(bool), zonas
    def guardar(): (H / 'out/capas.json').write_text(json.dumps({k: {"centro_mm": v['c'].round(1).tolist(), "tam_mm": [round(t, 1) for t in v['tam']], "angulo": round(v['ang'], 1)} for k, v in obj.items()}, ensure_ascii=False))
    time.sleep(1.0); ref = gris(); prev = ref; quieto = 0; pendiente = False; t_mov = None; libre, zonas = mascaras()
    while True:
        time.sleep(0.12); g = gris()
        mov = float((np.abs(g - prev) > 16)[libre].mean()); prev = g
        if mov > 0.004: quieto = 0; pendiente = True; t_mov = t_mov or time.time(); continue
        quieto += 1
        if not pendiente or quieto < 4: continue          # mesa quieta ~0,5 s después de un movimiento
        pendiente = False; dif = np.abs(g - ref) > 24
        cambiados = [k for k in obj if zonas[k].any() and float(dif[zonas[k]].mean()) > (0.10 if k in ANIM else 0.06)]
        if not cambiados: t_mov = None; continue           # solo pasó una mano o una sombra: no se toca nada
        for k in cambiados:
            p = obj[k]; r = max(p['tam']) * 0.9 + 140
            nuevo, im, org = medir(p['c'], r, (k,))
            if k not in nuevo: nuevo, im, org = medir(None, None, (k,))
            if k in nuevo:
                obj[k] = nuevo[k]; poner(k, nuevo[k], im, org); log(f"{k} reubicada en {nuevo[k]['c'].round(0).tolist()} giro {nuevo[k]['ang']:.0f}° · {time.time()-t_mov:.1f} s desde que empezó el movimiento")
            else:
                pcx('remove', {"request_id": "cx-%d" % (time.time() * 10), "ids": ["capa-" + k]}); log(k, 'no la encuentro: capa apagada (se reintenta cuando algo se mueva)')
        guardar(); time.sleep(0.9); ref = gris(); prev = ref; t_mov = None; libre, zonas = mascaras()

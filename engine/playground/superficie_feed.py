"""Feed de video de la superficie proyectable (rectificada) desde la Osmo, y detector barato de orientación del
monitor del brazo (landscape / portrait) con auto-rotate opcional. Pedidos de Max, 2-nov.

    python -m playground.superficie_feed captures/dale-XXXX --calib            # con el damero de superficie proyectado
    python -m playground.superficie_feed captures/dale-XXXX                    # usa la calibración guardada
    python -m playground.superficie_feed captures/dale-XXXX --rotate-display A428…  # además rota ese display

Calibración: el damero de `playground.superficie` (cuadrados reales en el lienzo) está proyectado; se buscan sus
esquinas interiores en la Osmo (findChessboardCornersSB) y, como se sabe dónde están en el lienzo, sale la homografía
cámara→lienzo. El damero es simétrico a 180°: la ambigüedad se resuelve con el mapa del escaneo (orientación gruesa).
Queda en <scan>/superficie_feed.json. Sirve aunque la Osmo se haya movido después del escaneo: basta recalibrar.

HTTP en --port (8791): /  (página) · /feed.mjpg (lienzo rectificado, MJPEG) · /frame.jpg · /raw.mjpg (cuadro
completo reducido) · /status (fps, calibración, monitor). El lienzo es la entrada del pipeline de visión: mismos px
que `superficie.json` (H_lienzo_to_proj lleva cualquier cosa dibujada ahí al proyector).

Jev (playground/jev.py, --jev 1): tras cada catalog_refinado.json, un hilo pregunta a Jev por las etiquetas nuevas
(emoji, ruta de efecto, intensidad) y escribe jev.json en esa carpeta; la capa catálogo usa ese emoji. /jev.json · /jev?on=0|1.
Agente de visión del refinado: --agente sonnet|jev|ambos y /agente?modo= (jev = playground/vision_jev.py, Jev 1.14 con imagen).

Quest 3 como cámara (desde el 9-nov-2026; la webcam USB ya no está):

    python -m playground.superficie_feed captures/mesa-quest --calib captures/quest-cal-fija --rojo \\
        [--camera http://127.0.0.1:8787/cam.mjpg?w=864&fps=15&q=80] [--salida http://127.0.0.1:8793]

Con --calib <carpeta> (lienzo_quest.json + .npz de playground/lienzo_quest.py) el lienzo es el plano de la mesa a
0,4 mm/px; la cámara es el MJPEG de la app del Quest (playground/quest_cam.MjpegCam; 864x864 a ~12,7 fps, el tope de la
app) y los momentos que importan (segmentación base, /recalibrar) usan /cam_full.jpg (1728x1728). La salida se compone
en el CONTENIDO de la ventana del proyector (1900x1040) con H_lienzo→contenido + lazo_corr.npz y va por POST /imagen
a playground/ventana_proyector.py (:8793) a ≤5 Hz, JPEG q90. Nada del runtime :8790. /recalibrar proyecta 12 cuadrados
(par patrón/invertido, binaria por signo de la diferencia) y mide con la cámara el residuo en mm; ?ajustar=1 corrige
H cámara→lienzo si pasa el umbral. El stream trae croma (color, con tinte) y /cam_full.jpg solo luma: la segmentación
base combina la luma de 1728 con la croma del stream; si la cámara entrega gris, lo que depende de color (piel) se apaga
solo y MediaPipe corre igual sobre el gris.

Monitor: dentro de --monitor-roi (fracciones del cuadro: x0,y0,x1,y1) se busca la pantalla encendida (brillante,
grande), minAreaRect → el lado largo horizontal = landscape, vertical = portrait; 3 s estable para cambiar de estado.
Con --rotate-display <id persistente de displayplacer> gira ese display (degree 0 / 90) cuando cambia; sin él, solo
informa. Nunca toca otros displays.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

from . import catalogo_anim as ca
from . import jev as jv
from . import lienzo_quest as lq
from . import quest_cam as qc

W, H = 1920, 1080

# emoji de la capa catálogo por palabra de la etiqueta (respaldo: primero manda la caché de Jev, ver emoji_de en main)
EMOJI = {"hoja": "📄", "papel": "📄", "teclado": "⌨️", "mouse": "🖱️", "libreta": "📒", "cuaderno": "📒", "laptop": "💻", "notebook": "💻",
         "cable": "🔌", "mano": "✋", "moledor": "🌿", "tapa": "🌿", "taza": "☕", "vape": "💨", "lápiz": "✏️", "lapiz": "✏️", "lapicera": "🖊️",
         "teléfono": "📱", "telefono": "📱", "celular": "📱", "control": "🎮", "mesa": "🪵", "pad": "🟩", "mousepad": "🟩", "objeto": "📦",
         "botella": "🍶", "audífonos": "🎧", "audifonos": "🎧", "libro": "📖", "revista": "📰", "lentes": "👓", "llaves": "🔑", "reloj": "⌚"}


def emoji_dict(label: str) -> str:
    l_ = label.lower()
    for k_, e_ in EMOJI.items():
        if k_ in l_:
            return e_
    return "📦"

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Superficie</title>
<style>html,body{margin:0;height:100%%;background:#000;color:#ccc;font:13px -apple-system,sans-serif}
#wrap{display:flex;height:100vh;gap:4px}#wrap img{display:block;object-fit:contain;max-height:100vh}
#feed{flex:3;min-width:0}#papel{flex:1;min-width:0;background:#111}
#s{position:fixed;left:8px;bottom:6px;background:#0008;padding:4px 8px;border-radius:6px}
#b{position:fixed;right:8px;top:6px;display:flex;gap:6px}button{font:inherit;background:#222;color:#eee;border:1px solid #444;border-radius:6px;padding:4px 8px}</style></head>
<body><div id="wrap"><img id="feed" src="/feed.mjpg"><img id="papel" src="/papel.mjpg"></div>
<div id="b"><button onclick="window.open('/papel.jpg')">captura hoja</button><button onclick="window.open('/frame.jpg')">captura superficie</button></div>
<div id="s">…</div>
<script>setInterval(async()=>{try{const j=await (await fetch('/status')).json();const h=j.hoja;
document.getElementById("s").textContent=`${j.fps} fps · lienzo ${j.lienzo[0]}x${j.lienzo[1]} (${(j.superficie_mm||[]).join("x")} mm) · hojas: ${(j.hojas||[]).map(h=>h.formato+" "+h.size_mm.join("x")+"mm").join(", ")||"no"} · rojo ${j.rojo&&j.rojo.on?"on":"off"} · monitor: ${j.monitor.estado||"?"}`}catch(e){}},1000)</script>
</body></html>"""


MON_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Monitor superficie</title>
<style>html,body{margin:0;height:100%;background:#000;color:#ddd;font:13px -apple-system,sans-serif;overflow:hidden}
#stage{position:relative;width:100vw;height:100vh}
#stage img{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:contain}
#ovl{opacity:.5;mix-blend-mode:normal}
#bar{position:fixed;left:10px;bottom:8px;background:#000a;padding:6px 10px;border-radius:8px;display:flex;gap:10px;align-items:center;z-index:3}
input[type=range]{width:220px}button{font:inherit;background:#222;color:#eee;border:1px solid #444;border-radius:6px;padding:3px 8px}</style></head>
<body><div id="stage"><img id="cam" src="/limpio.mjpg"><img id="ovl" src="/overlay.mjpg"></div>
<div id="bar"><span>overlay</span><input id="op" type="range" min="0" max="100" value="50"><span id="opv">50%</span>
<button id="modo">cámara limpia</button><button onclick="window.open('/frame.jpg')">captura</button><span id="st"></span></div>
<div id="bar2" style="position:fixed;right:10px;bottom:8px;background:#000a;padding:6px 10px;border-radius:8px;display:grid;grid-template-columns:auto 160px auto;gap:4px 8px;align-items:center;z-index:3">
<span>saturación</span><input class="v" data-k="sat" type="range" min="0" max="200" value="100"><span id="v_sat">1.00</span>
<span>brillo</span><input class="v" data-k="brillo" type="range" min="-80" max="80" value="0"><span id="v_brillo">0</span>
<span>contraste</span><input class="v" data-k="contraste" type="range" min="50" max="200" value="100"><span id="v_contraste">1.00</span>
<span>gamma</span><input class="v" data-k="gamma" type="range" min="40" max="250" value="100"><span id="v_gamma">1.00</span>
<span>luz área</span><input class="v" data-k="luz" type="range" min="0" max="255" value="150"><span id="v_luz">150</span>
<span>pantalla virtual</span><button id="vd">apagada</button><span id="vdr"></span>
</div>
<script>
const op=document.getElementById('op'),ovl=document.getElementById('ovl'),opv=document.getElementById('opv'),cam=document.getElementById('cam'),modo=document.getElementById('modo');
op.oninput=()=>{ovl.style.opacity=op.value/100;opv.textContent=op.value+'%'};
const vdb=document.getElementById('vd');let vdon=false;
async function vdSync(){try{const j=await (await fetch('/vd')).json();vdon=j.vd.on;vdb.textContent=vdon?'encendida':'apagada';document.getElementById('vdr').textContent='zona '+j.vd.rect.join(',')}catch(e){}}
vdb.onclick=async()=>{await fetch('/vd?on='+(vdon?0:1));vdSync()};vdSync();
document.querySelectorAll('.v').forEach(el=>{el.oninput=()=>{const k=el.dataset.k;let v=+el.value;
  if(k==='luz'){document.getElementById('v_luz').textContent=v;fetch('/rojo?luz='+v);return}
  const val=(k==='brillo')?v:v/100;document.getElementById('v_'+k).textContent=(k==='brillo')?v:val.toFixed(2);fetch('/video?'+k+'='+val)}});
let limpio=true;modo.onclick=()=>{limpio=!limpio;cam.src=limpio?'/limpio.mjpg':'/feed.mjpg';modo.textContent=limpio?'cámara limpia':'con anotaciones'};
setInterval(async()=>{try{const j=await (await fetch('/status')).json();document.getElementById('st').textContent=`${j.fps} fps · hojas ${j.n_hojas}`}catch(e){}},1000);
// si el feed se relanzó, los <img> MJPEG quedan colgados: se reabren solos al cambiar el inicio del servidor
let _ini=null;setInterval(async()=>{try{const j=await (await fetch('/status')).json();if(_ini&&j.inicio&&j.inicio!==_ini){document.querySelectorAll('img').forEach(im=>{im.src=im.src.split('?')[0]+'?r='+Date.now()})}_ini=j.inicio||_ini}catch(e){}},3000);
</script></body></html>"""


PG_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Playground mesa</title>
<style>html,body{margin:0;height:100%;background:#000;color:#ddd;font:13px -apple-system,sans-serif;overflow:hidden}
#g{display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:6px;height:100vh;padding:6px;box-sizing:border-box}
.p{position:relative;background:#111;border-radius:8px;overflow:hidden}
.p img{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:contain}
.t{position:absolute;left:10px;top:8px;background:#0008;padding:3px 8px;border-radius:6px;z-index:2}
#ovl{opacity:.5}
#bar{position:absolute;right:10px;bottom:8px;background:#0008;padding:6px 10px;border-radius:8px;display:flex;gap:8px;align-items:center;z-index:2}
input[type=range]{width:200px}</style></head>
<body><div id="g">
<div class="p"><span class="t">cámara</span><img src="/raw.mjpg"></div>
<div class="p"><span class="t">área proyectable</span><img src="/limpio.mjpg"><img id="ovl" src="/overlay.mjpg">
<div id="bar"><span>overlay</span><input id="op" type="range" min="0" max="100" value="50"><span id="opv">50%</span></div></div>
<div class="p"><span class="t">segmentación SAM <span id="sgst"></span></span><img src="/limpio.mjpg"><img id="sgo" src="/seg.mjpg">
<div class="c"><span>opacidad</span><input id="sop" type="range" min="0" max="100" value="60"><span id="sopv">60%</span>
<span>cada (s)</span><input id="scada" type="range" min="5" max="150" value="40"><span id="scadav">4.0</span>
<span>grilla</span><input id="sgrid" type="range" min="4" max="32" value="16"><span id="sgridv">16x9</span>
<span>resolución</span><input id="sancho" type="range" min="480" max="1920" step="160" value="960"><span id="sanchov">960</span>
<span>estado</span><button id="sgon">…</button><span></span></div></div>
<div class="p"><span class="t">hoja <span id="hjst"></span></span><img src="/papel.mjpg"></div>
</div>
<div id="vin" style="position:absolute;left:16px;bottom:14px;background:#0008;padding:6px 10px;border-radius:8px;display:grid;grid-template-columns:auto 180px auto;gap:4px 8px;align-items:center;z-index:3">
<span>brillo</span><input class="v" data-k="brillo" type="range" min="-100" max="100" value="0"><span id="v_brillo">0</span>
<span>contraste</span><input class="v" data-k="contraste" type="range" min="30" max="250" value="100"><span id="v_contraste">1.00</span>
<span>saturación</span><input class="v" data-k="sat" type="range" min="0" max="250" value="60"><span id="v_sat">0.60</span>
<span>luminancia</span><input class="v" data-k="lum" type="range" min="30" max="250" value="100"><span id="v_lum">1.00</span>
</div>
<script>const op=document.getElementById('op'),ovl=document.getElementById('ovl'),opv=document.getElementById('opv');
op.oninput=()=>{ovl.style.opacity=op.value/100;opv.textContent=op.value+'%'};
document.querySelectorAll('.v').forEach(el=>{el.oninput=()=>{const k=el.dataset.k,v=+el.value,val=(k==='brillo')?v:v/100;
  document.getElementById('v_'+k).textContent=(k==='brillo')?v:val.toFixed(2);fetch('/video?'+k+'='+val)}});
fetch('/video').then(r=>r.json()).then(j=>{for(const k in j.video){const el=document.querySelector('.v[data-k='+k+']');if(!el)continue;
  el.value=(k==='brillo')?j.video[k]:Math.round(j.video[k]*100);document.getElementById('v_'+k).textContent=(k==='brillo')?j.video[k]:(+j.video[k]).toFixed(2)}});
const sgo=document.getElementById('sgo'),sop=document.getElementById('sop'),sgon=document.getElementById('sgon');let segOn=false;
sop.oninput=()=>{sgo.style.opacity=sop.value/100;document.getElementById('sopv').textContent=sop.value+'%'};
document.getElementById('scada').oninput=e=>{const v=e.target.value/10;document.getElementById('scadav').textContent=v.toFixed(1);fetch('/seg?cada='+v)};
document.getElementById('sgrid').oninput=e=>{const c=+e.target.value,r=Math.max(2,Math.round(c*9/16));document.getElementById('sgridv').textContent=c+'x'+r;fetch('/seg?grid='+c+'x'+r)};
document.getElementById('sancho').oninput=e=>{document.getElementById('sanchov').textContent=e.target.value;fetch('/seg?ancho='+e.target.value)};
async function segSync(){try{const j=await (await fetch('/seg.json')).json();segOn=!!j.on;sgon.textContent=segOn?'encendida':'apagada';
  document.getElementById('sgst').textContent=j.error?('· '+j.error):(j.n?`· ${j.ms} ms · ${j.n} seg · ${(j.segmentos||[]).length} regiones`:'')}catch(e){}}
sgon.onclick=async()=>{await fetch('/seg?on='+(segOn?0:1));segSync()};segSync();setInterval(segSync,2000);
// si el feed se relanzó, los <img> MJPEG quedan colgados: se reabren solos al cambiar el inicio del servidor
let _ini=null;setInterval(async()=>{try{const j=await (await fetch('/status')).json();if(_ini&&j.inicio&&j.inicio!==_ini){document.querySelectorAll('img').forEach(im=>{im.src=im.src.split('?')[0]+'?r='+Date.now()})}_ini=j.inicio||_ini}catch(e){}},3000);
async function hjSync(){try{const j=await (await fetch('/papel.json')).json();const h=j.hoja||(j.hojas&&j.hojas[0]);document.getElementById('hjst').textContent=h?`· ${h.formato||''} ${h.mm?h.mm[0]+'x'+h.mm[1]+' mm':''}`:'· sin hoja'}catch(e){}}hjSync();setInterval(hjSync,1500);</script></body></html>"""


class Sticky:
    """Poses pegajosas para lo proyectado: una capa solo cambia cuando la medida nueva se aleja de la mostrada más
    que `thresh` (px) y, como máximo, una vez por `min_dt` segundos; las variaciones chicas se castigan (se ignoran)."""

    def __init__(self, min_dt: float = 1.0):
        self.min_dt = min_dt; self.shown: dict[str, np.ndarray] = {}; self.t: dict[str, float] = {}; self.pend: dict[str, int] = {}

    def __call__(self, key: str, value, thresh: float, need: int = 2):
        if value is None:
            return self.shown.get(key)
        v = np.asarray(value, np.float64)
        cur = self.shown.get(key)
        now = time.time()
        if cur is None or cur.shape != v.shape:
            self.shown[key] = v; self.t[key] = now; self.pend[key] = 0; return v
        dev = float(np.max(np.linalg.norm((v - cur).reshape(-1, v.shape[-1]) if v.ndim > 1 else (v - cur).reshape(-1, 1), axis=1)))
        if dev > thresh:
            self.pend[key] = self.pend.get(key, 0) + 1
            if self.pend[key] >= need and now - self.t.get(key, 0) >= self.min_dt:
                self.shown[key] = v; self.t[key] = now; self.pend[key] = 0
        else:
            self.pend[key] = 0
        return self.shown[key]

    def drop(self, key: str) -> None:
        self.shown.pop(key, None); self.t.pop(key, None); self.pend.pop(key, None)


class Perf:
    """Rendimiento por etapa: ms (media móvil, ~los últimos 7 cuadros) y Hz por lazo (feed, visión, proyección).
    Lo publican /status y /vision.json en "perf". Las etapas intermitentes (p. ej. teclado cada 0,3 s) muestran el
    costo de cada corrida, no el promedio por cuadro."""

    def __init__(self):
        self.ms: dict[str, dict[str, float]] = {}; self.hz: dict[str, float] = {}
        self._n: dict[str, int] = {}; self._t0: dict[str, float] = {}

    def lap(self, grupo: str, etapa: str, t0: float) -> float:
        """Registra perf_counter() - t0 en grupo/etapa y devuelve el perf_counter() actual (para encadenar)."""
        now = time.perf_counter(); ms = (now - t0) * 1000.0
        g = self.ms.setdefault(grupo, {}); v = g.get(etapa)
        g[etapa] = ms if v is None else v + 0.25 * (ms - v)
        return now

    def vuelta(self, grupo: str) -> None:
        now = time.time(); t0 = self._t0.setdefault(grupo, now); n = self._n.get(grupo, 0) + 1
        if now - t0 >= 2.0:
            self.hz[grupo] = n / (now - t0); n = 0; self._t0[grupo] = now
        self._n[grupo] = n

    def snapshot(self) -> dict:
        out = {}
        for g in sorted(set(self.ms) | set(self.hz)):
            d = {k: round(v, 1) for k, v in sorted(self.ms.get(g, {}).items())}
            d["hz"] = round(self.hz.get(g, 0.0), 1)
            out[g] = d
        return out


def componer_premult(img: np.ndarray, pm: np.ndarray, inv: np.ndarray) -> np.ndarray:
    """img·(255 − α)/255 + color premultiplicado, todo en uint8 con OpenCV (paralelo, sin pasar a float)."""
    return cv2.add(cv2.multiply(img, inv, scale=1 / 255.0), pm)


def capa_a_premult(lay: np.ndarray, brillo: float, premult: bool) -> tuple[np.ndarray, np.ndarray]:
    """Capa BGRA → (color premultiplicado y atenuado por `brillo`, 255 − α en 3 canales) para `componer_premult`."""
    al = cv2.extractChannel(lay, 3)
    a3 = cv2.merge([al, al, al])
    bgr = cv2.cvtColor(lay, cv2.COLOR_BGRA2BGR)
    if premult:
        pm = bgr if brillo >= 0.999 else cv2.convertScaleAbs(bgr, alpha=float(brillo))
    else:
        pm = cv2.multiply(bgr, a3, scale=float(brillo) / 255.0)
    return pm, cv2.bitwise_not(a3)


class CamReader:
    def __init__(self, cam):
        self.cam = cam; self.frame = None; self.seq = 0; self.t = 0.0; self.fps = 0.0
        self._cond = threading.Condition(); self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        n, t0 = 0, time.time(); fails = 0
        while not self._stop.is_set():
            try:
                f = self.cam.read(); fails = 0
            except Exception:  # noqa: BLE001
                fails += 1; time.sleep(0.3)
                if fails >= 10 and getattr(self, "reabrir", None):      # la Osmo se fue: cerrar y esperar a que vuelva
                    try:
                        self.cam.close()
                    except Exception:  # noqa: BLE001
                        pass
                    print("cámara perdida: esperando a que vuelva…", flush=True)
                    self.cam = self.reabrir(); fails = 0
                    print("cámara de vuelta", flush=True)
                continue
            with self._cond:
                self.frame, self.t, self.seq = f, time.time(), self.seq + 1
                self._cond.notify_all()
            n += 1
            if time.time() - t0 >= 2:
                self.fps, n, t0 = n / (time.time() - t0), 0, time.time()

    def wait_new(self, after: int, timeout: float = 1.0):
        with self._cond:
            self._cond.wait_for(lambda: self.seq > after, timeout)
            return (self.frame, self.t, self.seq) if self.seq > after else (None, 0.0, after)

    def close(self):
        self._stop.set(); time.sleep(0.2)
        try:
            self.cam.close()
        except Exception:  # noqa: BLE001
            pass


def calibrate_checker(frame: np.ndarray, sup: dict, scan: Path, cam_name: str) -> dict:
    """Esquinas interiores del damero proyectado → H cámara→lienzo."""
    lw, lh = (int(v) for v in sup["lienzo_px"]); cell = float(sup["cuadrado_lienzo_px"])
    cols, rows = int(round(lw / cell)), int(sup["cuadrados_alto"])
    inner = (cols - 1, rows - 1)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    scale = 1.0
    if gray.shape[1] > 2000:
        scale = 1920.0 / gray.shape[1]
        g = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        g = gray
    ok, corners = cv2.findChessboardCornersSB(g, inner, flags=cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY)
    if not ok:
        ok, corners = cv2.findChessboardCorners(g, inner, flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE)
        if ok:
            corners = cv2.cornerSubPix(g, corners, (7, 7), (-1, -1), (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.01))
    if not ok:
        raise RuntimeError(f"no encontré el damero {inner} en la Osmo: ¿está proyectado superficie_checker.png y sin manos?")
    c = corners.reshape(-1, 2) / scale                                  # px de cámara (resolución completa)
    # esquinas del lienzo en el orden del detector (fila por fila); dos candidatos (180°)
    obj = np.array([[(i + 1) * cell, (j + 1) * cell] for j in range(inner[1]) for i in range(inner[0])], np.float64)
    cands = []
    for o in (obj, obj[::-1]):
        Hm, inl = cv2.findHomography(c, o, cv2.RANSAC, 3.0)
        err = np.linalg.norm(cv2.perspectiveTransform(c.reshape(-1, 1, 2), Hm).reshape(-1, 2) - o, axis=1)
        cands.append((Hm, float(np.median(err)), int(inl.sum())))
    # desempate con el mapa del escaneo: dónde caía en la cámara la esquina (0,0) del lienzo (orientación gruesa)
    pick = 0
    try:
        with np.load(scan / "proj_to_cam.npz") as z:
            mapx, mapy, valid = z["mapx"], z["mapy"], z["valid"]
        Hlp = np.array(sup["H_lienzo_to_proj"], np.float64)
        ref_l = np.array([[[lw * 0.15, lh * 0.15]], [[lw * 0.85, lh * 0.85]]], np.float64)
        ref_p = cv2.perspectiveTransform(ref_l, Hlp).reshape(2, 2)
        ref_c = []
        for x, y in ref_p:
            xi, yi = int(np.clip(x, 0, W - 1)), int(np.clip(y, 0, H - 1))
            if not valid[yi, xi]:
                raise ValueError("referencia fuera del mapa")
            ref_c.append([mapx[yi, xi], mapy[yi, xi]])
        ref_c = np.array(ref_c, np.float64)                              # px de cámara según el escaneo (cámara vieja)
        # el candidato correcto manda ref_c (aprox) cerca de ref_l; el otro, al lado opuesto del lienzo
        d = [np.linalg.norm(cv2.perspectiveTransform(ref_c.reshape(-1, 1, 2), Hm).reshape(2, 2) - ref_l.reshape(2, 2), axis=1).sum()
             for Hm, _, _ in cands]
        pick = int(np.argmin(d))
        tie = {"distancias": [round(v, 1) for v in d]}
    except Exception as e:  # noqa: BLE001
        tie = {"sin_desempate": str(e)}
    Hm, med, nin = cands[pick]
    return {"H_cam_to_lienzo": Hm.tolist(), "camera": cam_name, "camera_size": [frame.shape[1], frame.shape[0]],
            "inner_corners": list(inner), "found": int(len(c)), "inliers": nin, "mediana_px_lienzo": round(med, 2),
            "orientacion": pick, "desempate": tie, "lienzo_px": [lw, lh], "calibrado": time.strftime("%Y-%m-%d %H:%M:%S")}


class MonitorWatch:
    """Orientación del monitor del brazo en el cuadro de la Osmo (barato: cada ~0,5 s, imagen reducida)."""

    def __init__(self, roi, stable_s: float = 3.0, rotate_id: str | None = None, dry: bool = True):
        self.roi = roi; self.stable_s = stable_s; self.rotate_id = rotate_id; self.dry = dry
        self.estado = None; self.candidato = None; self.t_cand = 0.0; self.razon = ""; self.ultimo = {}
        self.rotado = None; self.log = []

    def medir(self, frame_small: np.ndarray) -> dict:
        h, w = frame_small.shape[:2]
        x0, y0, x1, y1 = (int(self.roi[0] * w), int(self.roi[1] * h), int(self.roi[2] * w), int(self.roi[3] * h))
        sub = frame_small[y0:y1, x0:x1]
        hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
        bright = ((hsv[..., 2] > 110) & (hsv[..., 1] > 40)).astype(np.uint8) * 255      # pantalla encendida (color)
        bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        n, lab, st, _ = cv2.connectedComponentsWithStats(bright)
        if n < 2:
            return {"estado": None, "razon": "sin pantalla brillante en el ROI"}
        i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
        if st[i, cv2.CC_STAT_AREA] < 0.02 * sub.shape[0] * sub.shape[1]:
            return {"estado": None, "razon": "mancha demasiado chica"}
        ys, xs = np.nonzero(lab == i)
        (cx, cy), (rw, rh), ang = cv2.minAreaRect(np.stack([xs, ys], 1).astype(np.float32))
        if rw >= rh:
            long_, short_, ang_long = rw, rh, ang
        else:
            long_, short_, ang_long = rh, rw, ang + 90
        ang_long = ((ang_long + 90) % 180) - 90                                       # -90..90, 0 = horizontal
        ratio = long_ / max(short_, 1)
        estado = "landscape" if abs(ang_long) < 45 else "portrait"
        return {"estado": estado, "razon": f"lado largo {ang_long:+.0f}°, razón {ratio:.2f}", "centro": [round(cx + x0, 1), round(cy + y0, 1)],
                "ratio": round(ratio, 2), "ang": round(ang_long, 1), "area": int(st[i, cv2.CC_STAT_AREA])}

    def step(self, frame_small: np.ndarray) -> None:
        m = self.medir(frame_small); self.ultimo = m; self.razon = m.get("razon", "")
        est = m.get("estado")
        if est is None:
            return
        now = time.time()
        if est != self.candidato:
            self.candidato, self.t_cand = est, now
        if est != self.estado and now - self.t_cand >= self.stable_s:
            prev, self.estado = self.estado, est
            self.log.append({"t": time.strftime("%H:%M:%S"), "de": prev, "a": est})
            if prev is not None or True:
                self.rotar(est)

    def rotar(self, est: str) -> None:
        deg = 0 if est == "landscape" else 90
        if not self.rotate_id or self.dry:
            self.rotado = f"(sin rotar) {est} → degree:{deg}"; return
        try:
            r = subprocess.run(["displayplacer", f"id:{self.rotate_id} degree:{deg}"], capture_output=True, text=True, timeout=30)
            self.rotado = f"displayplacer degree:{deg} rc={r.returncode} {r.stderr.strip()[:80]}"
        except Exception as e:  # noqa: BLE001
            self.rotado = f"displayplacer falló: {e}"

    def status(self) -> dict:
        return {"estado": self.estado, "candidato": self.candidato, "razon": self.razon, "rotado": self.rotado,
                "rotate_display": self.rotate_id, "dry": self.dry, "medida": self.ultimo, "cambios": self.log[-5:]}


def largest_rect(mask: np.ndarray) -> tuple[int, int, int, int]:
    """Rectángulo alineado más grande dentro de una máscara binaria (histograma por filas). (x, y, w, h)."""
    h, w = mask.shape
    heights = np.zeros(w, int); best = (0, 0, 0, 0); best_a = 0
    for y in range(h):
        heights = np.where(mask[y] > 0, heights + 1, 0)
        stack = []
        for x in range(w + 1):
            cur = heights[x] if x < w else 0
            start = x
            while stack and stack[-1][1] >= cur:
                sx, sh = stack.pop()
                if sh * (x - sx) > best_a:
                    best_a = sh * (x - sx); best = (sx, y - sh + 1, x - sx, sh)
                start = sx
            stack.append((start, cur))
    return best


def find_paper(lienzo: np.ndarray, min_frac: float = 0.03) -> tuple[int, int, int, int] | None:
    """Hoja blanca en el lienzo rectificado: el rectángulo blanco inscrito más grande, extendido mientras los bordes
    sigan blancos (>= 90 %). Devuelve (x0, y0, x1, y1) en px del lienzo o None."""
    lh, lw = lienzo.shape[:2]
    hsv = cv2.cvtColor(lienzo, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 2] > 205) & (hsv[..., 1] < 55)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    s = 4
    small = cv2.resize(m, (lw // s, lh // s), interpolation=cv2.INTER_AREA)
    x, y, w, h = largest_rect((small > 0).astype(np.uint8))
    if w * h * s * s < min_frac * lw * lh:
        return None
    x0, y0, x1, y1 = x * s, y * s, (x + w) * s, (y + h) * s
    def fc(xx): return m[y0:y1, xx].mean() if 0 <= xx < lw else 0
    def fr(yy): return m[yy, x0:x1].mean() if 0 <= yy < lh else 0
    while fc(x0 - 1) > 0.9: x0 -= 1
    while fc(x1) > 0.9: x1 += 1
    while fr(y0 - 1) > 0.9: y0 -= 1
    while fr(y1) > 0.9: y1 += 1
    return (int(x0), int(y0), int(x1), int(y1))


class Paper:
    """Hoja seguida en el lienzo: rect suavizado, recorte como feed plano, y pintura encima por el proyector."""

    def __init__(self, sup: dict, rot: int, out_w: int = 864):
        self.sup = sup; self.rot = rot % 360; self.rect = None; self.t = 0.0; self.out_w = out_w
        self.Hlp = np.array(sup["H_lienzo_to_proj"], np.float64)
        self.mmpx = float(sup.get("mm_por_px") or 0.0)

    def update(self, lienzo: np.ndarray) -> None:
        r = find_paper(lienzo)
        if r is None:
            return
        if self.rect is None or max(abs(a - b) for a, b in zip(r, self.rect)) > 25:
            self.rect = r                                   # salto: hoja movida
        else:
            self.rect = tuple(int(round(0.7 * a + 0.3 * b)) for a, b in zip(self.rect, r))
        self.t = time.time()

    def size(self) -> tuple[int, int]:
        x0, y0, x1, y1 = self.rect
        w, h = x1 - x0, y1 - y0
        out_h = int(round(self.out_w * h / w))
        return (self.out_w, out_h) if self.rot in (0, 180) else (out_h, self.out_w)

    def crop(self, lienzo: np.ndarray) -> np.ndarray | None:
        if self.rect is None:
            return None
        x0, y0, x1, y1 = self.rect
        img = lienzo[y0:y1, x0:x1]
        w, h = x1 - x0, y1 - y0
        img = cv2.resize(img, (self.out_w, int(round(self.out_w * h / w))), interpolation=cv2.INTER_AREA)
        if self.rot == 180:
            img = cv2.rotate(img, cv2.ROTATE_180)
        elif self.rot == 90:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        elif self.rot == 270:
            img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return img

    def info(self) -> dict:
        if self.rect is None:
            return {"hoja": None}
        x0, y0, x1, y1 = self.rect
        quad_l = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float64)
        quad_p = cv2.perspectiveTransform(quad_l.reshape(-1, 1, 2), self.Hlp).reshape(4, 2)
        return {"hoja": {"lienzo_px": [x0, y0, x1, y1], "tamano_px": [x1 - x0, y1 - y0],
                         "tamano_mm": [round((x1 - x0) * self.mmpx, 1), round((y1 - y0) * self.mmpx, 1)] if self.mmpx else None,
                         "proj_tl_tr_br_bl": quad_p.round(1).tolist(), "feed_px": list(self.size()), "rot": self.rot,
                         "hace_s": round(time.time() - self.t, 1)}}

    def to_projector(self, canvas: np.ndarray) -> np.ndarray:
        """Imagen en coordenadas del feed de la hoja (como la ve Max) → cuadro 1920x1080 del proyector."""
        if self.rot == 180:
            canvas = cv2.rotate(canvas, cv2.ROTATE_180)
        elif self.rot == 90:
            canvas = cv2.rotate(canvas, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif self.rot == 270:
            canvas = cv2.rotate(canvas, cv2.ROTATE_90_CLOCKWISE)
        x0, y0, x1, y1 = self.rect
        ch, cw = canvas.shape[:2]
        src = np.array([[0, 0], [cw, 0], [cw, ch], [0, ch]], np.float32)
        dst = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)
        Hcanvas = self.Hlp @ cv2.getPerspectiveTransform(src, dst).astype(np.float64)
        return cv2.warpPerspective(canvas, Hcanvas, (W, H), flags=cv2.INTER_AREA)


FORMATOS = {"carta": (215.9, 279.4), "A4": (210.0, 297.0), "oficio": (215.9, 330.2), "A5": (148.0, 210.0),
            "cuaderno": (165.0, 215.0), "libro": (140.0, 210.0), "revista": (205.0, 275.0), "tarjeta": (85.6, 54.0)}


def formato_de(short_mm: float, long_mm: float, tol: float = 0.07) -> str:
    best, err = "hoja", None
    for name, (a, b) in FORMATOS.items():
        s, l = min(a, b), max(a, b)
        e = max(abs(short_mm - s) / s, abs(long_mm - l) / l)
        if e <= tol and (err is None or e < err):
            best, err = name, e
    return best


def pc_intersect(l1, l2) -> np.ndarray:
    c1, d1 = l1; c2, d2 = l2
    t = np.linalg.solve(np.array([d1, -d2]).T, c2 - c1)
    return c1 + d1 * t[0]


def order_quad4(pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, np.float64).reshape(-1, 2)
    s, d = pts.sum(1), pts[:, 0] - pts[:, 1]
    return np.array([pts[s.argmin()], pts[d.argmax()], pts[s.argmax()], pts[d.argmin()]], np.float64)


def find_sheets(lienzo: np.ndarray, scale: float = 0.5, min_frac: float = 0.012, exclude: list | None = None) -> list[dict]:
    """Hojas / páginas claras en el lienzo rectificado: componentes claras, rectángulo rotado mínimo, y si el relleno
    es bajo (un mouse o una mano pegada) se erosiona para separar. Devuelve [{quad (4x2 px lienzo, TL TR BR BL),
    w_px, h_px, angle, fill}]."""
    lh, lw = lienzo.shape[:2]
    small = cv2.resize(lienzo, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    # solo por brillo (sin tono): la hoja sigue siendo lo más claro aunque el proyector la pinte de rojo
    # umbral relativo: la hoja es lo más claro de la escena sea cual sea la luz del proyector (blanco pleno o gris)
    V = hsv[..., 2]
    thr = float(max(120.0, min(200.0, np.percentile(V, 96) - 12)))
    m = (V > thr).astype(np.uint8)
    for q in (exclude or []):                                           # el mouse blanco no es parte de la hoja
        cv2.fillPoly(m, [(np.asarray(q, np.float64) * scale).astype(np.int32).reshape(-1, 1, 2)], 0)
    # apertura moderada: limpia ruido sin borrar el marco claro que queda alrededor de la pantalla virtual
    # (el relleno se mide con el contorno exterior, así que un anillo claro cuenta como la hoja entera)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (int(4 * scale * 2) | 1, int(4 * scale * 2) | 1)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    out = []
    min_area = min_frac * small.shape[0] * small.shape[1]
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] < min_area:
            continue
        comp = (lab == i).astype(np.uint8)
        x, y, w, h = st[i, :4]
        sub = comp[y:y + h, x:x + w]
        cnt = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
        if not cnt:
            continue
        c = max(cnt, key=cv2.contourArea)
        rect = cv2.minAreaRect(c)
        area = float(cv2.contourArea(c)); fill = area / max(rect[1][0] * rect[1][1], 1)
        if fill < 0.85:                                   # algo pegado a la hoja: erosionar y quedarse con el núcleo
            k = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
            er = cv2.erode(sub, k, iterations=2)
            n2, lab2, st2, _ = cv2.connectedComponentsWithStats(er)
            if n2 < 2:
                continue
            j = 1 + int(np.argmax(st2[1:, cv2.CC_STAT_AREA]))
            c2 = max(cv2.findContours((lab2 == j).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
            (cx, cy), (rw, rh), ang = cv2.minAreaRect(c2)
            rect = ((cx, cy), (rw + 4 * 15, rh + 4 * 15), ang)      # deshacer la erosión
            fill = cv2.contourArea(c2) / max(rw * rh, 1)
            if fill < 0.8:
                continue
        (cx, cy), (rw, rh), ang = rect
        if min(rw, rh) < 12:
            continue
        box = cv2.boxPoints(((cx + x, cy + y), (rw, rh), ang))
        q = order_quad4(box)
        # afinado de bordes: recta por mínimos cuadrados sobre los puntos del contorno cercanos a cada lado (sin las
        # puntas, donde se meten mouse/cables) y esquinas por intersección
        pts = c.reshape(-1, 2).astype(np.float64) + (x, y)
        lines = []
        for k in range(4):
            p0, p1 = q[k], q[(k + 1) % 4]
            d = p1 - p0; L = np.linalg.norm(d); d = d / max(L, 1e-6); nv = np.array([-d[1], d[0]])
            t = (pts - p0) @ d; dist = np.abs((pts - p0) @ nv)
            sel = (t > 0.1 * L) & (t < 0.9 * L) & (dist < 6)
            if sel.sum() >= 12:
                cm = pts[sel].mean(0); _, _, vt = np.linalg.svd(pts[sel] - cm, full_matrices=False); lines.append((cm, vt[0]))
            else:
                lines.append((p0, d))
        try:
            ref = np.array([pc_intersect(lines[(k - 1) % 4], lines[k]) for k in range(4)])
            if np.all(np.isfinite(ref)) and np.linalg.norm(ref - q, axis=1).max() < 25:
                q = order_quad4(ref)
        except Exception:  # noqa: BLE001
            pass
        q = q / scale
        out.append({"quad": q, "w_px": float(np.linalg.norm(q[1] - q[0])), "h_px": float(np.linalg.norm(q[2] - q[1])),
                    "angle": float(ang), "fill": round(float(fill), 3)})
    return out


class Papers:
    """Varias hojas seguidas en tiempo real: ids estables, esquinas suavizadas (EMA + banda muerta contra el jitter),
    tamaño en mm y formato; recorte plano de cada una; overlay rojo con borde difuminado hacia afuera."""

    def __init__(self, sup: dict, rot: int, out_w: int = 864, feather_px: int = 18, alpha: float = 0.35, dead_px: float = 1.5):
        self.sup = sup; self.rot = rot % 360; self.out_w = out_w
        self.Hlp = np.array(sup["H_lienzo_to_proj"], np.float64)
        self.mmpx = float(sup.get("mm_por_px") or 0.0)
        self.feather = feather_px; self.alpha = alpha; self.dead = dead_px
        self.min_short_mm = 70.0                                       # lado corto mínimo de una hoja/tarjeta grande
        self.grow = 3; self.inset_px = 2.0                              # feather: crece 3 px y se retrae 2 px del borde
        self.det_scale = 0.75                                           # resolución de detección (bordes más finos)
        self.keep_s = 6.0                                               # cuánto sobrevive una hoja sin verse
        self.sticky = Sticky(min_dt=1.0); self.sticky_px = 18.0         # la hoja proyectada solo salta con cambios grandes
        self.sheets: list[dict] = []; self._next = 1; self.t = 0.0
        self.lock = threading.Lock()

    @property
    def rect(self):
        """Compatibilidad: caja alineada de la primera hoja (x0, y0, x1, y1) o None."""
        with self.lock:
            if not self.sheets:
                return None
            q = self.sheets[0]["quad"]
        return (int(q[:, 0].min()), int(q[:, 1].min()), int(q[:, 0].max()), int(q[:, 1].max()))

    def _find_dark_near(self, lienzo: np.ndarray, s: dict) -> dict | None:
        """La misma hoja pero vista oscura (le estamos proyectando contenido oscuro): rectángulo oscuro del mismo
        tamaño cerca de donde estaba, dentro del área blanca que la rodea."""
        q = s["quad"]; c = q.mean(0)
        lh, lw = lienzo.shape[:2]
        r = 0.75 * max(s["w_px"], s["h_px"])
        x0, y0 = int(max(0, c[0] - r)), int(max(0, c[1] - r)); x1, y1 = int(min(lw, c[0] + r)), int(min(lh, c[1] + r))
        if x1 - x0 < 40 or y1 - y0 < 40:
            return None
        roi = lienzo[y0:y1, x0:x1]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        m = (hsv[..., 2] < 150).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8)); m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
        n, lab, st, _ = cv2.connectedComponentsWithStats(m)
        best = None
        for i in range(1, n):
            if st[i, cv2.CC_STAT_AREA] < 0.3 * s["w_px"] * s["h_px"]:
                continue
            ys, xs = np.nonzero(lab == i)
            (cx, cy), (w, h), ang = cv2.minAreaRect(np.stack([xs, ys], 1).astype(np.float32))
            if min(w, h) < 10:
                continue
            ok_size = abs(max(w, h) - max(s["w_px"], s["h_px"])) < 0.06 * max(s["w_px"], s["h_px"]) and abs(min(w, h) - min(s["w_px"], s["h_px"])) < 0.06 * min(s["w_px"], s["h_px"])
            if not ok_size:
                continue
            box = cv2.boxPoints(((cx + x0, cy + y0), (w, h), ang)); qq = order_quad4(box)
            d = float(np.linalg.norm(qq.mean(0) - c))
            if best is None or d < best[0]:
                best = (d, {"quad": qq, "w_px": float(np.linalg.norm(qq[1] - qq[0])), "h_px": float(np.linalg.norm(qq[2] - qq[1])), "angle": float(ang), "fill": 0.95})
        return best[1] if best else None

    def update(self, lienzo: np.ndarray) -> None:
        excl = []
        mo = getattr(self, "mouse_outline", None)
        if mo is not None:
            c = mo.mean(0); excl.append(c + (mo - c) * 1.15)
        found = find_sheets(lienzo, scale=self.det_scale, exclude=excl)
        now = time.time()
        with self.lock:
            prev = self.sheets
            # hojas con contenido proyectado oscuro: no salen claras; se buscan como rectángulo oscuro cerca de donde estaban
            for s in prev:
                if not any(np.linalg.norm(f["quad"].mean(0) - s["quad"].mean(0)) < 120 for f in found):
                    try:
                        dk = self._find_dark_near(lienzo, s)
                    except Exception:  # noqa: BLE001
                        dk = None
                    if dk is not None:
                        found.append(dk)
            used = set(); new = []
            lh_, lw_ = lienzo.shape[:2]
            for f in found:
                c = f["quad"].mean(0)
                # hojas de verdad: enteras dentro del lienzo (la madera iluminada toca el borde) y con relleno alto
                qx0, qy0 = f["quad"].min(0); qx1, qy1 = f["quad"].max(0)
                if qx0 < 3 or qy0 < 3 or qx1 > lw_ - 3 or qy1 > lh_ - 3:
                    continue
                if f["fill"] < 0.75:                                   # (una hoja nueva exige 0,85 más abajo)
                    continue
                best, bd = None, 1e9
                for k, s in enumerate(prev):
                    if k in used:
                        continue
                    d = float(np.linalg.norm(s["quad"].mean(0) - c))
                    if d < bd:
                        best, bd = k, d
                if best is not None and bd < 120:
                    used.add(best); s = prev[best]
                    delta = f["quad"] - s["quad"]
                    # ¿la hoja está tapada? una mano encima, o la detección salió incompleta (tamaño distinto al estable)
                    tapada = any(self._overlap(s["quad"], o) for o in getattr(self, "occluders", []))
                    w0, h0 = s.get("w0", s["w_px"]), s.get("h0", s["h_px"])          # tamaño de referencia (sin deriva)
                    incompleta = abs(f["w_px"] - w0) > 0.04 * w0 or abs(f["h_px"] - h0) > 0.04 * h0 or f["fill"] < 0.9
                    if tapada or incompleta:
                        q = s["quad"]                                 # no se mueve: solo cambia cuando la hoja entera se mueve
                        f = {**f, "w_px": s["w_px"], "h_px": s["h_px"], "angle": s["angle"], "fill": s["fill"]}
                    elif np.abs(delta).max() > 40:                   # salto real: la hoja se movió
                        q = f["quad"]
                    else:
                        step = np.where(np.abs(delta) < self.dead, 0.0, delta)   # banda muerta: el jitter chico no mueve nada
                        q = s["quad"] + self.alpha * step
                    f["w0"], f["h0"] = w0, h0
                    sid = s["id"]
                else:
                    if f["fill"] < 0.85:                               # hoja nueva: tiene que verse casi entera
                        continue
                    q = f["quad"]; sid = self._next; self._next += 1
                    f["w0"], f["h0"] = f["w_px"], f["h_px"]
                w = float(np.linalg.norm(q[1] - q[0])); h = float(np.linalg.norm(q[2] - q[1]))
                short_mm, long_mm = (min(w, h) * self.mmpx, max(w, h) * self.mmpx) if self.mmpx else (0.0, 0.0)
                if self.mmpx and short_mm < self.min_short_mm:         # manchas claras chicas (teclas, reflejos) no son hojas
                    continue
                fmt = formato_de(short_mm, long_mm) if self.mmpx else "hoja"
                if fmt == "hoja" and (short_mm < 90 or long_mm < 125):   # sin formato conocido: al menos tamaño de libreta
                    continue
                new.append({"id": sid, "quad": q, "w_px": w, "h_px": h, "w0": f["w0"], "h0": f["h0"], "angle": f["angle"], "fill": f["fill"], "t": now,
                            "size_mm": [round(w * self.mmpx, 1), round(h * self.mmpx, 1)] if self.mmpx else None,
                            "formato": formato_de(short_mm, long_mm) if self.mmpx else "hoja",
                            "orientacion": "vertical" if h > w else "horizontal"})
            # las que desaparecieron se conservan un rato (manos encima, contenido oscuro, parpadeos): tracking pegajoso
            for k, s in enumerate(prev):
                if k not in used and now - s["t"] < self.keep_s:
                    new.append(s)
            # prioridad: formato conocido primero, luego la más antigua (id menor): la principal no salta entre manchas
            new.sort(key=lambda s: (s["formato"] == "hoja", s["id"]))
            self.sheets = new; self.t = now

    @staticmethod
    def _overlap(quad: np.ndarray, poly: np.ndarray) -> bool:
        """¿El polígono (mano) toca el cuadrilátero? Por cajas y por puntos dentro."""
        if poly is None or len(poly) < 3:
            return False
        qx0, qy0 = quad.min(0); qx1, qy1 = quad.max(0); px0, py0 = poly.min(0); px1, py1 = poly.max(0)
        if px1 < qx0 or px0 > qx1 or py1 < qy0 or py0 > qy1:
            return False
        q32 = quad.astype(np.float32)
        return any(cv2.pointPolygonTest(q32, (float(x), float(y)), False) >= 0 for x, y in poly[::3])

    def _upright_size(self, s: dict) -> tuple[int, int]:
        w, h = s["w_px"], s["h_px"]
        ow = self.out_w; oh = int(round(ow * h / max(w, 1)))
        return ow, oh

    def crop(self, lienzo: np.ndarray, i: int = 0) -> np.ndarray | None:
        with self.lock:
            if i >= len(self.sheets):
                return None
            s = dict(self.sheets[i])
        ow, oh = self._upright_size(s)
        dst = np.array([[0, 0], [ow, 0], [ow, oh], [0, oh]], np.float32)
        Hq = cv2.getPerspectiveTransform(s["quad"].astype(np.float32), dst)
        img = cv2.warpPerspective(lienzo, Hq, (ow, oh), flags=cv2.INTER_AREA)
        if self.rot == 180:
            img = cv2.rotate(img, cv2.ROTATE_180)
        elif self.rot == 90:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        elif self.rot == 270:
            img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return img

    def info(self) -> dict:
        with self.lock:
            sheets = [dict(s) for s in self.sheets]
        out = []
        for s in sheets:
            qp = cv2.perspectiveTransform(s["quad"].reshape(-1, 1, 2), self.Hlp).reshape(4, 2)
            out.append({"id": s["id"], "formato": s["formato"], "orientacion": s["orientacion"], "size_mm": s["size_mm"],
                        "size_px": [round(s["w_px"], 1), round(s["h_px"], 1)], "angle": round(s["angle"], 1),
                        "centro_lienzo": s["quad"].mean(0).round(1).tolist(), "quad_lienzo": s["quad"].round(1).tolist(),
                        "quad_proj": qp.round(1).tolist(), "hace_s": round(time.time() - s["t"], 2)})
        first = out[0] if out else None
        return {"hojas": out, "n_hojas": len(out), "hoja": first}

    def shown_quads(self) -> list[tuple[int, np.ndarray]]:
        """Cuadriláteros pegajosos (id, quad) de las hojas: solo cambian con movimientos grandes (Sticky)."""
        with self.lock:
            cur = [(s["id"], s["quad"].copy()) for s in self.sheets]
        st = getattr(self, "sticky", None)
        if st is None:
            return cur
        ids = {i for i, _ in cur}
        for k in [k for k in list(st.shown) if k.startswith("hoja:") and int(k.split(":")[1]) not in ids]:
            st.drop(k)
        return [(i, st(f"hoja:{i}", q, self.sticky_px)) for i, q in cur]

    def overlay_image(self, color=(0, 0, 255), base: np.ndarray | None = None) -> np.ndarray | None:
        """Cuadro 1920x1080 para el proyector: `base` (p. ej. el área proyectable en blanco) y encima cada hoja
        pintada, con el borde difuminado hacia afuera (la hoja entera queda cubierta y el jitter chico cae dentro
        del degradado)."""
        quads = [q for _, q in self.shown_quads()]
        if not quads:
            return None if base is None else base.copy()
        qps = []
        for q in quads:
            c = q.mean(0); q2 = c + (q - c) * (1 - self.inset_px / max(np.linalg.norm(q[1] - q[0]), 1))   # retracción hacia adentro
            qps.append(cv2.perspectiveTransform(q2.reshape(-1, 1, 2), self.Hlp).reshape(4, 2))
        img = np.zeros((H, W, 3), np.uint8) if base is None else base.copy()
        f = int(self.feather); g = max(1, int(self.grow))
        # todo se hace en la caja de las hojas con holgura para el difuminado (antes: cuadro completo y en float)
        m_ = (g + 2 * f + 4) if f > 0 else 2
        allp = np.vstack(qps)
        x0, y0 = int(max(0, np.floor(allp[:, 0].min()) - m_)), int(max(0, np.floor(allp[:, 1].min()) - m_))
        x1, y1 = int(min(W, np.ceil(allp[:, 0].max()) + m_)), int(min(H, np.ceil(allp[:, 1].max()) + m_))
        if x1 <= x0 or y1 <= y0:
            return img
        mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
        for qp in qps:
            cv2.fillPoly(mask, [np.round(qp - (x0, y0)).astype(np.int32).reshape(-1, 1, 2)], 255, cv2.LINE_AA)
        if f > 0:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * g + 1, 2 * g + 1))
            grown = cv2.dilate(mask, k)                                 # crece un poco hacia afuera…
            soft = cv2.GaussianBlur(grown, (0, 0), f / 2.0)             # …y se difumina (ancho ≈ feather)
            mask = cv2.max(mask, soft)                                  # el interior sigue sólido
        a3 = cv2.merge([mask, mask, mask])
        col = np.empty_like(a3); col[:] = np.array(color, np.uint8)
        roi = img[y0:y1, x0:x1]
        img[y0:y1, x0:x1] = cv2.add(cv2.multiply(roi, cv2.bitwise_not(a3), scale=1 / 255.0), cv2.multiply(col, a3, scale=1 / 255.0))
        return img

    def to_projector(self, canvas: np.ndarray, i: int = 0, inset_px: float = 0.0) -> np.ndarray:
        with self.lock:
            s = dict(self.sheets[i])
        sq = dict(self.shown_quads()).get(s["id"])
        if sq is not None:
            s["quad"] = sq
        if inset_px:
            q = s["quad"]; c = q.mean(0)
            fx = 1 - 2 * inset_px / max(s["w_px"], 1); fy = 1 - 2 * inset_px / max(s["h_px"], 1)
            d = q - c; u = (q[1] - q[0]) / max(np.linalg.norm(q[1] - q[0]), 1e-6); v = (q[3] - q[0]) / max(np.linalg.norm(q[3] - q[0]), 1e-6)
            s["quad"] = c + np.outer(d @ u, u) * fx + np.outer(d @ v, v) * fy
        if self.rot == 180:
            canvas = cv2.rotate(canvas, cv2.ROTATE_180)
        elif self.rot == 90:
            canvas = cv2.rotate(canvas, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif self.rot == 270:
            canvas = cv2.rotate(canvas, cv2.ROTATE_90_CLOCKWISE)
        ch, cw = canvas.shape[:2]
        src = np.array([[0, 0], [cw, 0], [cw, ch], [0, ch]], np.float32)
        Hc = self.Hlp @ cv2.getPerspectiveTransform(src, s["quad"].astype(np.float32)).astype(np.float64)
        return cv2.warpPerspective(canvas, Hc, (W, H), flags=cv2.INTER_AREA)


def main(argv=None) -> int:
    global W, H                                                        # con --calib <carpeta>: el contenido de la ventana (1900x1040)
    p = argparse.ArgumentParser()
    p.add_argument("scan")
    p.add_argument("--camera", default=None,
                   help="osmo | índice | URL MJPEG (http://127.0.0.1:8787/cam.mjpg?w=864&fps=15&q=80 = Quest por adb forward). "
                        "Sin valor: el Quest por USB con --calib <carpeta>, si no osmo")
    p.add_argument("--cam-full-url", default=None, help="cuadro completo del Quest para segmentación base y /recalibrar (por "
                                                         "defecto <base de --camera>/cam_full.jpg)")
    p.add_argument("--cam-size", default="3840x2160")
    p.add_argument("--rojo", action="store_true", help="proyectar overlay rojo sobre las hojas en tiempo real")
    p.add_argument("--feather", type=int, default=8, help="borde difuminado, px del proyector")
    p.add_argument("--overlay-every", type=float, default=None, help="período de la salida (s); con la ventana, mínimo 0,2 (≤5 Hz)")
    p.add_argument("--salida", default=None, help="URL de playground/ventana_proyector.py (POST /imagen). Por defecto "
                                                   "http://127.0.0.1:8793 con --calib <carpeta>; sin ella, el runtime (--runtime)")
    p.add_argument("--salida-q", type=int, default=90, help="calidad JPEG de lo que se manda a la ventana")
    p.add_argument("--sin-lazo", action="store_true", help="no aplicar lazo_corr.npz de la calibración a la salida")
    p.add_argument("--luz", type=int, default=0, help="gris del área proyectable que queda prendida bajo las hojas (0 = apagada)")
    p.add_argument("--vd-margen", type=float, default=14.0, help="marco blanco (px de lienzo) alrededor de la pantalla virtual sobre la hoja")
    p.add_argument("--seg", action="store_true", help="segmentación semántica periódica con MobileSAM (CPU)")
    p.add_argument("--seg-cada", type=float, default=30.0, help="segundos entre segmentaciones")
    p.add_argument("--rot", type=int, default=180, help="giro del feed de la hoja para que se vea derecho para Max")
    p.add_argument("--papel-every", type=float, default=0.2)
    p.add_argument("--port", type=int, default=8791)
    p.add_argument("--calib", nargs="?", const="damero", default=None,
                   help="carpeta de calibración del Quest (captures/quest-cal-fija: lienzo_quest.json/.npz + lazo_corr.npz) "
                        "o, sin valor, recalibrar con el damero proyectado (modo Osmo viejo)")
    p.add_argument("--quality", type=int, default=80); p.add_argument("--feed-fps", type=float, default=30)
    p.add_argument("--rebase-cada", type=float, default=45.0,
                   help="re-segmentación base automática (SAM + agente) cada N s, sin solaparse; 0 = apagada. Con ella "
                        "encendida el SAM periódico (--seg) parte apagado (/seg?on=1 lo prende)")
    p.add_argument("--rebase-agente", type=int, default=1, help="1 = la re-segmentación automática también refina con el agente (ver --agente)")
    p.add_argument("--agente", default="sonnet", choices=["sonnet", "jev", "ambos"],
                   help="agente de visión del refinado: sonnet (Claude CLI, ~20 s), jev (Jev 1.14 con imagen por el AI Gateway, ~2 s; "
                        "playground/vision_jev.py) o ambos (manda Sonnet; Jev deja catalog_refinado_jev.json para comparar). /agente?modo=")
    p.add_argument("--rebase-grid", default="12x7",
                   help="grilla de puntos del SAM automático en la re-segmentación automática (la manual usa 24x14). Con el "
                        "agente, lo que se proyecta es el refinado (cajas del agente → SAM): la grilla densa solo es respaldo")
    p.add_argument("--catalogo-hz", type=float, default=6.0, help="cuadros por segundo de la capa catálogo animada (se reusa entre medio)")
    p.add_argument("--jev", type=int, default=1,
                   help="1 = consulta Jev (TypeSafe, por el AI Gateway; playground/jev.py) por cada etiqueta nueva del catálogo refinado: "
                        "emoji, ruta de efecto e intensidad → jev.json y captures/<scan>/jev_cache.json. Sin llave queda apagado")
    p.add_argument("--overlay-fmt", default="jpg", choices=["jpg", "png"],
                   help="formato del cuadro que se le pasa al runtime (jpg q95: ~3x más barato de escribir y leer que png)")
    p.add_argument("--moledor-cada", type=float, default=0.5, help="segundos entre detecciones Hough de moledores")
    p.add_argument("--manos-cada", type=int, default=1, help="MediaPipe manos cada N cuadros de visión (1 = todos)")
    p.add_argument("--vision-hz", type=float, default=15.0, help="tope de Hz del lazo de visión (manos, teclado, mouse); 0 = sin tope")
    p.add_argument("--monitor-roi", default="0.5,0.4,1.0,1.0"); p.add_argument("--monitor-every", type=float, default=0.5)
    p.add_argument("--rotate-display", default=None, help="id persistente (displayplacer list) del monitor del brazo")
    p.add_argument("--rotate", action="store_true", help="rotar de verdad (sin esto solo informa)")
    p.add_argument("--no-monitor", action="store_true")
    p.add_argument("--runtime", default=None, help="runtime viejo (http://127.0.0.1:8790, modo Osmo); con --calib <carpeta> no se usa")
    p.add_argument("--no-manos", action="store_true"); p.add_argument("--manos-scale", type=float, default=0.5)
    p.add_argument("--objetos-auto", action="store_true", help="sembrar trackers desde la última segmentación SAM (reports/scene_regions)")
    p.add_argument("--cam2", default=None, help="segunda cámara (lateral, iPad por Iriun): índice AVFoundation o 'iriun'")
    p.add_argument("--cam2-size", default="1920x1080")
    a = p.parse_args(argv)
    scan = Path(a.scan)
    quest = bool(a.calib and a.calib != "damero")                    # Quest como cámara + ventana como salida
    LQ: dict = {}
    if quest:
        LQ = lq.cargar(Path(a.calib)); lq_info = LQ["info"]
        sup = lq.superficie_compat(lq_info)
        scan.mkdir(parents=True, exist_ok=True)
        try:                                                           # para teclado_fit, teclado_manual y compañía
            (scan / "superficie.json").write_text(json.dumps(sup, indent=1, ensure_ascii=False))
        except OSError as e:
            print(f"superficie.json no escrito: {e}", flush=True)
        W, H = (int(v) for v in lq_info["salida_px"])
        a.camera = a.camera or f"{qc.USB}/cam.mjpg?w=864&fps=15&q=80"
        a.salida = (a.salida or "http://127.0.0.1:8793").rstrip("/")
        a.runtime = None; a.no_monitor = True
        a.overlay_every = max(0.2, a.overlay_every or 0.2)
    else:
        sup = json.loads((scan / "superficie.json").read_text())
        a.camera = a.camera or "osmo"
        a.runtime = a.runtime or (None if a.salida else "http://127.0.0.1:8790")
        a.overlay_every = a.overlay_every or 0.15
        if a.salida:
            a.salida = a.salida.rstrip("/"); a.overlay_every = max(0.2, a.overlay_every)
    http_cam = str(a.camera).startswith("http")
    if http_cam and not a.cam_full_url:
        a.cam_full_url = qc.base_de(a.camera) + "/cam_full.jpg"
    lw, lh = (int(v) for v in sup["lienzo_px"])
    from slscan.capture import Camera

    def abrir_camara(max_s: float = 90.0):   # tras 90 s sale: el lazo externo lo relanza y la enumeración se refresca
        """La Osmo en modo webcam se desconecta sola cuando nadie la lee: esperar a que vuelva (hasta 10 min). Con una URL
        (MJPEG del Quest) se reconecta solo; aquí solo se espera el primer cuadro."""
        t0 = time.time(); k = 0
        want = tuple(int(v) for v in a.cam_size.lower().split("x"))
        while True:
            try:
                if http_cam:
                    cam_ = qc.MjpegCam(a.camera, timeout=4.0)
                    try:
                        cam_.read()
                    except Exception:
                        cam_.close(); raise
                    return cam_
                cam_ = Camera(a.camera, size=a.cam_size, rotate=0, downscale=1, name=a.camera)
                f_ = cam_.read()
                if (f_.shape[1], f_.shape[0]) != want:
                    # la Osmo con la pantalla girada entrega 1080x1920 (imagen apaisada encajonada): no sirve. Esperar a
                    # que Max la ponga horizontal; se vuelve a intentar.
                    cam_.close()
                    raise RuntimeError(f"la cámara entrega {f_.shape[1]}x{f_.shape[0]} y no {a.cam_size}: ¿Osmo con la pantalla en vertical?")
                return cam_
            except Exception as e:  # noqa: BLE001
                k += 1
                if k == 1 or k % 10 == 0:
                    print(f"cámara '{a.camera}' no disponible ({str(e)[:220]}); reintento cada 3 s", flush=True)
                if time.time() - t0 > max_s:
                    raise
                time.sleep(3)
    # la cámara 2 (Iriun) se ubica ANTES de abrir la Osmo: abrir y cerrar otros índices con la Osmo en uso le cambia el
    # formato a 1080x1920 en pleno stream (visto el 2-nov)
    cam2_index = None
    if a.cam2 and a.cam2 not in ("iriun", "ipad"):
        cam2_index = int(a.cam2)
    elif a.cam2:
        for i in range(6):
            cap = cv2.VideoCapture(i, cv2.CAP_AVFOUNDATION)
            if cap.isOpened():
                w, h, fps = cap.get(3), cap.get(4), cap.get(5); cap.release()
                if abs(fps - 15) < 0.6 and (w, h) == (1920, 1080):
                    cam2_index = i; break
        print(f"cámara 2 (Iriun): índice {cam2_index}", flush=True)
    cam = abrir_camara()
    reader = CamReader(cam)
    reader.reabrir = None if http_cam else abrir_camara                # el MJPEG se reconecta solo (y cae a la Wi-Fi)
    f, _, seq = reader.wait_new(0, 5.0)
    if f is None:
        raise SystemExit("la Osmo no entrega cuadros")
    jpath = scan / "superficie_feed.json"
    if quest:
        # cámara (cuadro completo 1728) → lienzo por geometría (lienzo_quest.json); el remap denso del .npz manda si existe
        k_ = f.shape[1] / float(lq.LADO_CAM)
        cal = {k: v for k, v in lq_info.items() if k not in ("H_lienzo_to_proj", "H_proj_to_lienzo", "T_lienzo_to_mesa", "H_cam_to_mesa")}
        cal.update({"camera": a.camera, "camera_size": [f.shape[1], f.shape[0]], "cam_full_url": a.cam_full_url})
        Hcl = np.array(lq_info["H_cam_to_lienzo"], np.float64) @ np.diag([1 / k_, 1 / k_, 1.0])
        calib = {"H": Hcl, "info": cal, "busy": False, "last": None, "remap": None, "remap_w": None,
                 "full": (LQ.get("mapx"), LQ.get("mapy")), "H_full": np.array(lq_info["H_cam_to_lienzo"], np.float64)}
        if LQ.get("mapx") is not None:
            calib["remap"] = lq.mapa_para(LQ["mapx"], LQ["mapy"], f.shape[1]); calib["remap_w"] = f.shape[1]
        print(f"lienzo Quest: {lw}x{lh} px a {lq_info['mm_por_px']} mm/px desde {a.calib} (cámara {f.shape[1]}x{f.shape[0]}, "
              f"salida {W}x{H} → {a.salida})", flush=True)
    else:
        if a.calib or not jpath.exists():
            for _ in range(6):
                f, _, seq = reader.wait_new(seq, 2.0)
            cal = calibrate_checker(f, sup, scan, a.camera)
            jpath.write_text(json.dumps(cal, indent=1))
            print(json.dumps({k: v for k, v in cal.items() if k != "H_cam_to_lienzo"}, ensure_ascii=False), flush=True)
        cal = json.loads(jpath.read_text())
        Hcl = np.array(cal["H_cam_to_lienzo"], np.float64)
        calib = {"H": Hcl, "info": cal, "busy": False, "last": None, "remap": None, "remap_w": 2560, "full": (None, None)}
        # remap denso cámara→lienzo con la distorsión de la lente ya corregida (modelo de división de la herramienta de mesa de
        # h1, k=-0,27 para la webcam 2K): si existe y calza con el lienzo, reemplaza al warp por homografía (bordes rectos).
        try:
            _rp = scan / "lienzo_remap_webcam.npz"
            if _rp.exists():
                with np.load(_rp) as _z:
                    _mx, _my = _z["mapx"].astype(np.float32), _z["mapy"].astype(np.float32)
                if _mx.shape == (lh, lw) and tuple(cal.get("camera_size", [])) == (2560, 1440):
                    calib["remap"] = (_mx, _my); print("lienzo: remap con corrección de lente cargado", flush=True)
        except Exception as _e:  # noqa: BLE001
            print(f"lienzo: remap no cargado ({_e})", flush=True)

    def salida_enviar(img: np.ndarray, png: bool = False) -> None:
        """Un cuadro del contenido (W x H) a la ventana del proyector: POST /imagen (JPEG q`--salida-q`, o PNG sin pérdida
        para patrones de calibración)."""
        ok_, buf_ = cv2.imencode(".png" if png else ".jpg", img, [] if png else [cv2.IMWRITE_JPEG_QUALITY, int(a.salida_q)])
        if not ok_:
            raise RuntimeError("no pude codificar el cuadro de salida")
        req_ = urllib.request.Request(a.salida + "/imagen", data=buf_.tobytes(), method="POST",
                                      headers={"Content-Type": "image/png" if png else "image/jpeg"})
        with urllib.request.urlopen(req_, timeout=3.0) as r_:
            r_.read()

    def mostrar(img: np.ndarray | None, nombre: str = "patron", seconds: float = 30.0) -> None:
        """Imagen completa por la salida (None = negro / apagar): la ventana (POST) o el runtime viejo (overlay)."""
        if a.salida:
            if img is None:
                with urllib.request.urlopen(a.salida + "/negro", timeout=3.0) as r_:
                    r_.read()
            else:
                salida_enviar(img, png=True)
            return
        from . import pad_checker as pc
        if img is None:
            pc.runtime_cmd(a.runtime, "overlay", off=True); return
        p_ = scan / f"_salida_{nombre}.png"; cv2.imwrite(str(p_), img)
        pc.runtime_cmd(a.runtime, "overlay", path=str(p_.resolve()), seconds=seconds)

    def lienzo_full(color: bool = True) -> np.ndarray:
        """Lienzo desde el cuadro completo del Quest (1728x1728, /cam_full.jpg ~0,2-0,3 s por USB): para la segmentación
        base y la recalibración. El cuadro completo es solo luma; el stream (864) trae croma: con `color`, la luma de 1728
        se combina con la croma del último cuadro del stream (YCrCb, croma reescalada), así SAM tiene resolución y el
        agente ve color. Sin --cam-full-url, el lienzo del stream."""
        if not (quest and a.cam_full_url):
            return vis["lienzo"].copy()
        fr_, _ = qc.cuadro_full(qc.base_de(a.cam_full_url))
        cs_ = state.get("fr")
        if color and cs_ is not None and not vis.get("gris") and fr_.shape[0] == fr_.shape[1] and cs_.shape[0] == cs_.shape[1]:
            ycc_ = cv2.cvtColor(cv2.resize(cs_, (fr_.shape[1], fr_.shape[0]), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2YCrCb)
            ycc_[..., 0] = cv2.cvtColor(fr_, cv2.COLOR_BGR2GRAY)
            fr_ = cv2.cvtColor(ycc_, cv2.COLOR_YCrCb2BGR)
        mx_, my_ = calib.get("full") or (None, None)
        if mx_ is not None and fr_.shape[1] == lq.LADO_CAM:
            return cv2.remap(fr_, mx_, my_, cv2.INTER_LINEAR)
        k2_ = fr_.shape[1] / float(lq.LADO_CAM)
        return cv2.warpPerspective(fr_, calib["H_full"] @ np.diag([1 / k2_, 1 / k2_, 1.0]), (lw, lh), flags=cv2.INTER_LINEAR)

    def recalibrar_quest(ajustar: bool = False, umbral_mm: float = 1.5, lado_mm: float = 36.0) -> dict:
        """Verificación (y ajuste opcional) de cámara→lienzo con el Quest, por la ventana: 12 cuadrados de `lado_mm` en una
        grilla 4x3 dentro de la huella del proyector, como par patrón (blancos sobre negro) / invertido. La diferencia de
        los dos cuadros completos (1728) se binariza por SIGNO (la textura de la mesa bajo el negro se cancela), los
        centroides van al lienzo con la H vigente y se comparan con donde se dibujaron → residuo en mm. Con `ajustar` y
        mediana > `umbral_mm`, corrige H cámara→lienzo con una homografía (observado → previsto, RANSAC) y reescribe
        lienzo_quest.json/.npz (respaldo .prev). Proyecta ~3 s; las capas vuelven solas."""
        mmpx_ = float(sup["mm_por_px"])
        hue = np.array(lq_info["huella_lienzo_tl_tr_br_bl"], np.float64)

        def en_huella(u, v):                                           # bilineal dentro del cuadrilátero TL TR BR BL
            top = hue[0] + (hue[1] - hue[0]) * u; bot = hue[3] + (hue[2] - hue[3]) * u
            return top + (bot - top) * v
        pts_l = np.array([en_huella(u, v) for v in (0.2, 0.5, 0.8) for u in (0.14, 0.38, 0.62, 0.86)], np.float64)
        half = lado_mm / mmpx_ / 2
        pat_l = np.zeros((lh, lw, 3), np.uint8)
        for x_, y_ in pts_l:
            cv2.rectangle(pat_l, (int(round(x_ - half)), int(round(y_ - half))), (int(round(x_ + half)), int(round(y_ + half))), (255, 255, 255), -1)
        pat = cv2.warpPerspective(pat_l, paper.Hlp, (W, H), flags=cv2.INTER_LINEAR)
        if state.get("corr") is not None:
            pat = cv2.remap(pat, state["corr"][0], state["corr"][1], cv2.INTER_LINEAR)
        inv = 255 - pat
        rojo_antes = state.get("rojo", False); state["rojo"] = False
        base_ = qc.base_de(a.cam_full_url)
        try:
            time.sleep(0.3)
            mostrar(pat, "recal_patron"); time.sleep(1.0); fP, iP = qc.cuadro_full(base_)
            mostrar(inv, "recal_invertido"); time.sleep(1.0); fI, iI = qc.cuadro_full(base_)
        finally:
            state["rojo"] = rojo_antes
            if not rojo_antes:
                try:
                    mostrar(None)
                except Exception:  # noqa: BLE001
                    pass
        gP = cv2.GaussianBlur(cv2.cvtColor(fP, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), 1.2)
        gI = cv2.GaussianBlur(cv2.cvtColor(fI, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), 1.2)
        d = gP - gI
        m = ((d > 0) & (np.abs(d) > 6)).astype(np.uint8)              # binaria por signo (con piso de ruido)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n_, lab_, st_, cen_ = cv2.connectedComponentsWithStats(m)
        Hc = calib["H_full"]
        area_esp = (lado_mm / 1.05) ** 2                                # ~1 mm por px de cámara en la mesa
        obs = []
        for i in range(1, n_):
            if 0.3 * area_esp <= st_[i, cv2.CC_STAT_AREA] <= 3.0 * area_esp:
                obs.append(cv2.perspectiveTransform(cen_[i].reshape(1, 1, 2).astype(np.float64), Hc).reshape(2))
        obs = np.array(obs, np.float64).reshape(-1, 2)
        pares = []
        for p_ in pts_l:
            if not len(obs):
                break
            dd = np.linalg.norm(obs - p_, axis=1); k = int(np.argmin(dd))
            if dd[k] * mmpx_ < 25:
                pares.append((p_, obs[k]))
        dbg = cv2.cvtColor(np.clip(d * 2 + 128, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        info = {"ok": len(pares) >= 6, "puntos": len(pares), "de": len(pts_l), "blobs": int(len(obs)), "lado_mm": lado_mm,
                "cuadros": {"patron_ms": iP["ms"], "invertido_ms": iI["ms"]}, "t": time.strftime("%Y-%m-%d %H:%M:%S")}
        if pares:
            P_ = np.array([p for p, _ in pares]); O_ = np.array([o for _, o in pares]); r_ = np.linalg.norm(O_ - P_, axis=1) * mmpx_
            info["residuo_mm"] = {"mediana": round(float(np.median(r_)), 2), "p90": round(float(np.percentile(r_, 90)), 2),
                                  "max": round(float(r_.max()), 2), "por_punto": [round(float(v), 2) for v in r_]}
            info["desplazamiento_medio_mm"] = ((O_ - P_).mean(0) * mmpx_).round(2).tolist()
            Hlc_ = np.linalg.inv(Hc)
            for p_, o_ in pares:
                cp = cv2.perspectiveTransform(p_.reshape(1, 1, 2), Hlc_).reshape(2); co = cv2.perspectiveTransform(o_.reshape(1, 1, 2), Hlc_).reshape(2)
                cv2.circle(dbg, (int(cp[0]), int(cp[1])), 6, (0, 255, 0), 1, cv2.LINE_AA)
                cv2.line(dbg, (int(cp[0]), int(cp[1])), (int(co[0] + (co[0] - cp[0]) * 20), int(co[1] + (co[1] - cp[1]) * 20)), (0, 0, 255), 1, cv2.LINE_AA)
            info["ajustado"] = False
            if ajustar and len(pares) >= 6 and info["residuo_mm"]["mediana"] > umbral_mm:
                C, inl = cv2.findHomography(O_, P_, cv2.RANSAC, 2.0 / mmpx_)
                if C is not None and int(inl.sum()) >= 6:
                    Hn = C @ Hc
                    cdir = Path(a.calib); jq = cdir / "lienzo_quest.json"
                    import shutil
                    shutil.copy2(jq, cdir / "lienzo_quest.prev.json")
                    if (cdir / "lienzo_quest.npz").exists():
                        shutil.copy2(cdir / "lienzo_quest.npz", cdir / "lienzo_quest.prev.npz")
                    lq_info["H_cam_to_lienzo"] = Hn.tolist()
                    lq_info["ajuste_recalibrar"] = {"t": info["t"], "residuo_antes_mm": info["residuo_mm"], "inliers": int(inl.sum())}
                    jq.write_text(json.dumps(lq_info, indent=1, ensure_ascii=False))
                    gx_, gy_ = np.meshgrid(np.arange(lw, dtype=np.float32), np.arange(lh, dtype=np.float32))
                    cxy = cv2.perspectiveTransform(np.stack([gx_, gy_], -1).reshape(-1, 1, 2).astype(np.float64), np.linalg.inv(Hn)).reshape(lh, lw, 2)
                    mx_, my_ = cxy[..., 0].astype(np.float32), cxy[..., 1].astype(np.float32)
                    np.savez_compressed(cdir / "lienzo_quest.npz", mapx=mx_, mapy=my_)
                    calib["H_full"] = Hn; calib["full"] = (mx_, my_)
                    w_ = calib.get("remap_w") or lq.LADO_CAM
                    calib["remap"] = lq.mapa_para(mx_, my_, w_)
                    calib["H"] = Hn @ np.diag([lq.LADO_CAM / w_, lq.LADO_CAM / w_, 1.0])
                    info["ajustado"] = True; info["inliers"] = int(inl.sum())
        cdir = Path(a.calib)
        cv2.imwrite(str(cdir / "recalibrar_quest.jpg"), dbg)
        with (cdir / "recalibraciones.jsonl").open("a") as fh:
            fh.write(json.dumps({k: v for k, v in info.items()}, ensure_ascii=False) + "\n")
        calib["last"] = info["t"]; calib["ultima"] = info
        return info

    def recalibrar(ajustar: bool = False) -> dict:
        """Negro / rectángulo blanco por el runtime → cuadrilátero en la cámara → H cámara→lienzo nueva (la Osmo se
        mueve). Tarda ~5 s; el feed sigue con la H vieja mientras tanto. Con el Quest: recalibrar_quest."""
        if quest:
            return recalibrar_quest(ajustar)
        from . import pad_checker as pc
        from .superficie_captura import quad_from_pair
        corners = np.array(sup["rect_proj_tl_tr_br_bl"], np.float64)
        white = np.zeros((H, W, 3), np.uint8); cv2.fillPoly(white, [corners.astype(np.int32).reshape(-1, 1, 2)], (255, 255, 255))
        pw, pb = scan / "superficie_blanco.png", scan / "superficie_negro.png"
        cv2.imwrite(str(pw), white); cv2.imwrite(str(pb), np.zeros((H, W, 3), np.uint8))
        rojo_antes = state.get("rojo", False); state["rojo"] = False
        try:
            pc.runtime_cmd(a.runtime, "overlay", path=str(pb.resolve()), seconds=30); time.sleep(2.2)
            f0, _, s0 = reader.wait_new(reader.seq, 2.0); f0, _, s0 = reader.wait_new(s0, 2.0)
            pc.runtime_cmd(a.runtime, "overlay", path=str(pw.resolve()), seconds=30); time.sleep(2.2)
            f1, _, s1 = reader.wait_new(reader.seq, 2.0); f1, _, s1 = reader.wait_new(s1, 2.0)
            quad, thr, area = quad_from_pair(f0, f1)
            src = quad.astype(np.float32); dst = np.array([[0, 0], [lw, 0], [lw, lh], [0, lh]], np.float32)
            Hn = cv2.getPerspectiveTransform(src, dst).astype(np.float64)
            info = {"H_cam_to_lienzo": Hn.tolist(), "camera": a.camera, "camera_size": [f1.shape[1], f1.shape[0]], "lienzo_px": [lw, lh],
                    "osmo_tl_tr_br_bl": quad.round(1).tolist(), "umbral": round(thr, 1), "area_px": area,
                    "source": "recalibración en vivo (negro/blanco)", "calibrado": time.strftime("%Y-%m-%d %H:%M:%S")}
            jpath.write_text(json.dumps(info, indent=1))
            calib["H"] = Hn; calib["info"] = info; calib["last"] = info["calibrado"]; calib["remap"] = None
            cv2.imwrite(str(scan / "superficie_osmo.png"), f1)
            return {"ok": True, **{k: v for k, v in info.items() if k != "H_cam_to_lienzo"}}
        finally:
            try:
                mostrar(None)
            except Exception:  # noqa: BLE001
                pass
            state["rojo"] = rojo_antes
    if tuple(cal["camera_size"]) != (f.shape[1], f.shape[0]):
        s = f.shape[1] / cal["camera_size"][0]
        Hcl = Hcl @ np.diag([1 / s, 1 / s, 1.0])
    roi = tuple(float(v) for v in a.monitor_roi.split(","))
    mon = None if a.no_monitor else MonitorWatch(roi, rotate_id=a.rotate_display, dry=not a.rotate)
    paper = Papers(sup, a.rot, feather_px=a.feather)
    state = {"jpg": None, "raw": None, "papel": None, "limpio": None, "ovl": None, "n": 0, "fps": 0.0, "t_mon": 0.0, "t_papel": 0.0, "cond": threading.Condition(),
             "rojo": bool(a.rojo), "rojo_n": 0, "rojo_err": None, "catalogo_hz": float(a.catalogo_hz)}
    if quest:
        # capas por defecto como las tenía Max (9-nov): fondo negro, catálogo animado a brillo 0,5; hoja, mouse, moledor y
        # teclado apagados (se prenden con /capas?hoja=1&…)
        state.update(capas={"hoja": False, "mouse": False, "teclado": False, "moledor": False, "catalogo": True},
                     luz=0, autoluz=False, catalogo_brillo=0.5, catalogo_anim=True)
    perf = Perf()
    INICIO = time.strftime("%Y-%m-%d %H:%M:%S")                      # las páginas reabren sus streams si cambia (relanzo)
    # clientes conectados por stream MJPEG: lo que nadie mira se codifica solo cada `REFRESCO_S` (para /frame.jpg etc.)
    clientes = {"jpg": 0, "limpio": 0, "raw": 0, "papel": 0, "ovl": 0, "segjpg": 0}; clientes_lock = threading.Lock()
    REFRESCO_S = 0.5

    def hace_falta(key: str, t_key: str) -> bool:
        """¿Codificar este stream ahora? Sí si alguien lo mira, o si la última versión tiene más de REFRESCO_S."""
        if clientes.get(key, 0) > 0 or time.time() - state.get(t_key, 0.0) >= REFRESCO_S:
            state[t_key] = time.time(); return True
        return False

    def rojo_loop():
        """Overlay rojo con feather sobre las hojas, al runtime (expira solo si este proceso muere)."""
        from . import pad_checker as pc
        out = scan / ("papel_rojo.jpg" if a.overlay_fmt == "jpg" else "papel_rojo.png")
        out_prm = [cv2.IMWRITE_JPEG_QUALITY, 95] if a.overlay_fmt == "jpg" else []
        # el área proyectable queda prendida (blanco) y el rojo de las hojas va encima
        state.setdefault("luz", int(a.luz)); luz_base = None; base = None
        vd_cap = {"img": None, "t": 0.0}
        t_it = time.time()
        while True:
            # período fijo: el trabajo del cuadro se descuenta de la espera (antes se sumaba: 0,15 s + trabajo)
            time.sleep(max(0.01, a.overlay_every - (time.time() - t_it)))
            t_it = time.time()
            if not state["rojo"]:
                continue
            tp = time.perf_counter(); tp0 = tp
            # luz automática: cada 1 s mira el lienzo crudo y mueve el gris para que no haya quemado y haya contraste
            if state.get("autoluz", True) and time.time() - state.get("t_autoluz", 0) >= 1.0 and vis.get("lienzo") is not None:
                state["t_autoluz"] = time.time()
                try:
                    g_ = cv2.cvtColor(cv2.resize(vis["lienzo"], (480, 281), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
                    quemado = float((g_ >= 250).mean()); p99 = float(np.percentile(g_, 99)); med = float(np.median(g_))
                    luz = int(state["luz"])
                    if quemado > 0.005 or p99 >= 235 or med > 120:
                        luz -= 8
                    elif p99 < 185 and med < 90:
                        luz += 4
                    state["luz"] = max(int(state.get("luz_min", 0)), min(int(state.get("luz_max", 60)), luz))
                    state["autoluz_info"] = {"quemado": round(quemado, 4), "p99": round(p99), "mediana": round(med), "luz": state["luz"]}
                except Exception as e:  # noqa: BLE001
                    state["autoluz_info"] = {"error": str(e)[:80]}
            if base is None or luz_base != state["luz"]:                # luz del área ajustable en caliente (/rojo?luz=N)
                luz_base = int(state["luz"]); base = np.zeros((H, W, 3), np.uint8)
                cv2.fillPoly(base, [np.array(sup["rect_proj_tl_tr_br_bl"], np.int32).reshape(-1, 1, 2)], (luz_base, luz_base, luz_base))
            # pantalla virtual sobre la hoja: captura de una zona del monitor principal (Quartz) → warp a la hoja
            vd = state.get("vd")
            if vd and vd.get("on"):
                try:
                    import Quartz
                    x, y, w_, h_ = vd["rect"]
                    cg = Quartz.CGDisplayCreateImageForRect(Quartz.CGMainDisplayID(), Quartz.CGRectMake(x, y, w_, h_))
                    cw, chh, bpr = Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg), Quartz.CGImageGetBytesPerRow(cg)
                    buf = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(cg))
                    arr = np.frombuffer(buf, np.uint8).reshape(-1, bpr // 4, 4)[:chh, :cw, :3].copy()   # BGRA → BGR (el buffer trae filas de relleno)
                    vd_cap["img"] = arr; vd_cap["t"] = time.time()
                    if time.time() - vd_cap.get("t_dbg", 0) > 2.0:          # muestra de lo capturado, para depurar permisos
                        vd_cap["t_dbg"] = time.time(); cv2.imwrite(str(scan / "vd_captura.jpg"), arr)
                        state["vd_media"] = round(float(arr.mean()), 1)
                except Exception as e:  # noqa: BLE001
                    state["vd_err"] = str(e)[:120]
            capas = state.get("capas", {})
            img = (paper.overlay_image(base=base) if capas.get("hoja", True) else base.copy()) if not (vd and vd.get("on") and vd_cap["img"] is not None and paper.sheets and capas.get("hoja", True)) else base.copy()
            if img is None:
                continue
            if vd and vd.get("on") and vd_cap["img"] is not None and paper.sheets and capas.get("hoja", True):
                try:
                    # la pantalla va con un marco blanco de `vd_margen` px de lienzo: la hoja sigue viéndose clara en
                    # su borde y el tracking no la pierde aunque el contenido sea oscuro
                    pantalla = paper.to_projector(vd_cap["img"], 0, inset_px=a.vd_margen)
                    maskp = np.zeros((H, W), np.uint8)
                    with paper.lock:
                        s0 = dict(paper.sheets[0]); q0 = s0["quad"].copy()
                    q0 = dict(paper.shown_quads()).get(s0["id"], q0)
                    c0 = q0.mean(0); fx = 1 - 2 * a.vd_margen / max(s0["w_px"], 1); fy = 1 - 2 * a.vd_margen / max(s0["h_px"], 1)
                    u = (q0[1] - q0[0]) / max(np.linalg.norm(q0[1] - q0[0]), 1e-6); v = (q0[3] - q0[0]) / max(np.linalg.norm(q0[3] - q0[0]), 1e-6)
                    d0 = q0 - c0; qi = c0 + np.outer(d0 @ u, u) * fx + np.outer(d0 @ v, v) * fy
                    qp = cv2.perspectiveTransform(qi.reshape(-1, 1, 2), paper.Hlp).reshape(4, 2)
                    cv2.fillPoly(maskp, [np.round(qp).astype(np.int32).reshape(-1, 1, 2)], 255, cv2.LINE_AA)
                    img = np.where(maskp[..., None] > 0, pantalla, img)
                except Exception as e:  # noqa: BLE001
                    state["vd_err"] = str(e)[:120]
            # imagen pintada sobre la hoja por POST /papel/pintar (con la ventana no hay overlays aparte: va en el cuadro)
            pin_ = state.get("pintura")
            if pin_ is not None and paper.sheets and time.time() < pin_["hasta"]:
                try:
                    pj_ = paper.to_projector(pin_["img"], 0)
                    mk_ = paper.to_projector(np.full_like(pin_["img"], 255), 0)
                    img = np.where(mk_ > 127, pj_, img)
                except Exception as e:  # noqa: BLE001
                    state["pintura_err"] = str(e)[:120]
            tp = perf.lap("proyeccion", "base_hoja", tp)
            # mouse: zonas de clic sobre el mouse real (izquierdo azul, derecho verde, rueda naranja, contorno)
            try:
                mz = (vis.get("mouse") or {}).get("zonas")
            except NameError:                                           # el lazo arranca antes de que exista `vis`
                mz = None
            sticky = paper.sticky
            if mz:
                # pegajoso: el conjunto de polígonos del mouse salta solo con cambios > 15 px (una vez por segundo)
                keys_ = list(mz.keys()); flat = np.vstack([np.array(mz[k_], np.float64) for k_ in keys_])
                flat_s = sticky("mouse", flat, 15.0)
                off = 0; mz_s = {}
                for k_ in keys_:
                    n_ = len(mz[k_]); mz_s[k_] = flat_s[off:off + n_]; off += n_
                mz = mz_s
            else:
                sticky.drop("mouse")
            if mz and capas.get("mouse", True):
                cols = {"izquierdo": (255, 140, 0), "derecho": (60, 200, 60), "rueda": (0, 160, 255)}
                pps = {k_: cv2.perspectiveTransform(np.array(poly, np.float64).reshape(-1, 1, 2), paper.Hlp).reshape(-1, 2) for k_, poly in mz.items()}
                allp = np.vstack(list(pps.values()))
                # solo en la caja del mouse (antes: copia y mezcla del cuadro completo)
                x0m, y0m = int(max(0, allp[:, 0].min() - 4)), int(max(0, allp[:, 1].min() - 4))
                x1m, y1m = int(min(W, allp[:, 0].max() + 5)), int(min(H, allp[:, 1].max() + 5))
                if x1m > x0m and y1m > y0m:
                    roi_ = img[y0m:y1m, x0m:x1m]; cap_ = roi_.copy()
                    for k_, pp in pps.items():
                        pi_ = np.round(pp - (x0m, y0m)).astype(np.int32).reshape(-1, 1, 2)
                        if k_ == "outline":
                            cv2.polylines(cap_, [pi_], True, (255, 255, 255), 2, cv2.LINE_AA)
                        else:
                            cv2.fillPoly(cap_, [pi_], cols[k_], cv2.LINE_AA)
                    img[y0m:y1m, x0m:x1m] = cv2.addWeighted(cap_, 0.75, roi_, 0.25, 0)
            tp = perf.lap("proyeccion", "mouse", tp)
            # teclas: contorno fino de cada tecla y relleno de las que tienen un dedo encima
            try:
                grid_ = vis.get("grid"); hot = set(vis["info"].get("teclas_bajo_dedos", []))
            except Exception:  # noqa: BLE001
                grid_, hot = None, set()
            # moledor: aura dorada en remolino alrededor de su circunferencia (las dos mitades si están separadas)
            try:
                grinders = vis.get("grinders") or []
            except NameError:
                grinders = []
            # pegajoso: cada moledor salta solo si se movió > 15 px (una vez por segundo)
            g_sticky = []
            for gi, gcirc in enumerate(sorted(grinders, key=lambda g: (round(g["cx"] / 150), round(g["cy"] / 150)))):
                v = sticky(f"moledor:{gi}", np.array([gcirc["cx"], gcirc["cy"], gcirc["r"]]), 15.0)
                g_sticky.append({"cx": float(v[0]), "cy": float(v[1]), "r": float(v[2])})
            for k_ in [k for k in list(sticky.shown) if k.startswith("moledor:") and int(k.split(":")[1]) >= len(grinders)]:
                sticky.drop(k_)
            grinders = g_sticky
            if grinders and capas.get("moledor", True):
                tt = time.time()
                # todo en la caja de los discos (antes: capas de cuadro completo, desenfoque y mezcla en float)
                discos = []
                for gcirc in grinders:
                    pts_c = cv2.perspectiveTransform(np.array([[[gcirc["cx"] + gcirc["r"] * 0.97 * math.cos(t_), gcirc["cy"] + gcirc["r"] * 0.97 * math.sin(t_)]] for t_ in np.linspace(0, 2 * math.pi, 48)], np.float64), paper.Hlp).reshape(-1, 2)
                    discos.append(pts_c)
                allp = np.vstack(discos)
                gx0, gy0 = int(max(0, allp[:, 0].min() - 10)), int(max(0, allp[:, 1].min() - 10))
                gx1, gy1 = int(min(W, allp[:, 0].max() + 11)), int(min(H, allp[:, 1].max() + 11))
                if gx1 > gx0 and gy1 > gy0:
                    off_ = np.array([gx0, gy0], np.float64)
                    glow = np.zeros((gy1 - gy0, gx1 - gx0, 3), np.uint8)
                    for gcirc in grinders:
                        cxl, cyl, rl = gcirc["cx"], gcirc["cy"], gcirc["r"]
                        # remolino INTERIOR: brazos en espiral que giran dentro del disco, sin aro exterior ni sombra
                        for arm in range(3):
                            s_ = np.linspace(0.0, 1.0, 40)
                            ang_ = tt * 1.6 + arm * (2 * math.pi / 3) + s_ * 2.4 * math.pi
                            rr = rl * (0.08 + 0.84 * s_)
                            pts_a = np.stack([cxl + rr * np.cos(ang_), cyl + rr * np.sin(ang_)], 1)
                            pp_ = cv2.perspectiveTransform(pts_a.reshape(-1, 1, 2), paper.Hlp).reshape(-1, 2) - off_
                            cv2.polylines(glow, [np.round(pp_).astype(np.int32).reshape(-1, 1, 2)], False, (40, 200, 255), 5, cv2.LINE_AA)
                        j_ = np.arange(16)                                # chispas que orbitan por dentro
                        ang_ = -tt * 2.4 + j_ * (2 * math.pi / 16)
                        rr = rl * (0.55 + 0.30 * np.sin(3 * ang_ + tt * 2.0))
                        ps_ = cv2.perspectiveTransform(np.stack([cxl + rr * np.cos(ang_), cyl + rr * np.sin(ang_)], 1).reshape(-1, 1, 2), paper.Hlp).reshape(-1, 2) - off_
                        for j, p in enumerate(ps_):
                            cv2.circle(glow, (int(p[0]), int(p[1])), 3 + (j % 3), (90, 225, 255), -1, cv2.LINE_AA)
                    glow = cv2.GaussianBlur(glow, (0, 0), 2.0)
                    # recortado al disco: nada fuera de la circunferencia
                    mdisc = np.zeros(glow.shape[:2], np.uint8)
                    for pts_c in discos:
                        cv2.fillPoly(mdisc, [np.round(pts_c - off_).astype(np.int32).reshape(-1, 1, 2)], 255, cv2.LINE_AA)
                    glow = cv2.bitwise_and(glow, glow, mask=(mdisc > 0).astype(np.uint8))
                    al = cv2.max(cv2.max(glow[..., 0], glow[..., 1]), glow[..., 2]); a3 = cv2.merge([al, al, al])
                    roi_ = img[gy0:gy1, gx0:gx1]
                    img[gy0:gy1, gx0:gx1] = cv2.add(cv2.multiply(roi_, cv2.bitwise_not(a3), scale=1 / 255.0), cv2.multiply(glow, a3, scale=1 / 255.0))
            tp = perf.lap("proyeccion", "moledor", tp)
            if grid_ and capas.get("teclado", True):
                # pegajoso: la grilla entera se mueve solo si el teclado se movió > 12 px (una vez por segundo)
                qk = sticky("teclado", np.array(grid_["quad_lienzo"], np.float64), 12.0)
                if np.max(np.abs(qk - np.array(grid_["quad_lienzo"]))) > 0.5:
                    gk = state.get("grid_shown")
                    if gk is None or np.max(np.abs(np.array(gk["quad_lienzo"]) - qk)) > 0.5:
                        from . import teclado_grid as tg
                        gk = tg.fit(qk.astype(np.float32), paper.Hlp, float(sup.get("mm_por_px") or 0), rot=int(grid_.get("rot", 180)),
                                    inset=float(grid_.get("inset", 0.012)))
                        state["grid_shown"] = gk
                    grid_ = gk
                else:
                    state["grid_shown"] = grid_
                # teclas sólidas con borde rojo (así se ve de inmediato si el fit está corrido); en 3 llamadas, no 2 por tecla
                fr_n, fr_h = [], []
                for k_ in grid_["keys"]:
                    if "poly_proj" in k_:
                        (fr_h if k_["name"] in hot else fr_n).append(np.round(np.array(k_["poly_proj"])).astype(np.int32).reshape(-1, 1, 2))
                if fr_n:
                    cv2.fillPoly(img, fr_n, (190, 190, 190), cv2.LINE_AA)
                if fr_h:
                    cv2.fillPoly(img, fr_h, (0, 255, 120), cv2.LINE_AA)
                if fr_n or fr_h:
                    cv2.polylines(img, fr_n + fr_h, True, (0, 0, 255), 2, cv2.LINE_AA)
            tp = perf.lap("proyeccion", "teclado", tp)
            # capa catálogo (máscaras SAM con emoji + nombre animados) desde la carpeta de catálogo vigente; se arma en
            # segundo plano al cambiar y se rinde a `catalogo_hz` (entre medio se reusa la última, ya premultiplicada)
            try:
                cp_ = catalogo_capa()
                if cp_ is not None:
                    # sobre negro (luz 0 y ninguna otra capa dibujó) la composición es el color premultiplicado tal cual
                    img = cp_[0] if cv2.countNonZero(img.reshape(H, -1)) == 0 else componer_premult(img, cp_[0], cp_[1])
            except Exception as e:  # noqa: BLE001
                state["catalogo_err"] = str(e)[:120]
            tp = perf.lap("proyeccion", "catalogo", tp)
            # corrección de lazo cerrado (remap denso en el espacio del proyector)
            if state.get("corr") is not None:
                try:
                    img = cv2.remap(img, state["corr"][0], state["corr"][1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
                except Exception as e:  # noqa: BLE001
                    state["lazo_err"] = str(e)[:100]
            tp = perf.lap("proyeccion", "lazo", tp)
            try:
                if a.salida:                                            # ventana del proyector: POST /imagen (JPEG q90)
                    salida_enviar(img)
                    tp = perf.lap("proyeccion", "ventana", tp)
                else:
                    cv2.imwrite(str(out), img, out_prm)
                    tp = perf.lap("proyeccion", "escribir", tp)
                    pc.runtime_cmd(a.runtime, "overlay", path=str(out.resolve()), seconds=max(2.0, 6 * a.overlay_every))
                    tp = perf.lap("proyeccion", "runtime", tp)
                state["rojo_n"] += 1; state["rojo_err"] = None; state["t_salida"] = time.time(); state["salida_img"] = img
                # lo proyectado, llevado al lienzo (para /mon y /pg): cada ~0,3 s y solo si alguien mira /overlay.mjpg
                if clientes["ovl"] > 0 and time.time() - state.get("t_ovl", 0) > 0.3:
                    state["t_ovl"] = time.time()
                    ovl = cv2.warpPerspective(img, np.linalg.inv(paper.Hlp), (lw, lh), flags=cv2.INTER_LINEAR)
                    okk, bufo = cv2.imencode(".jpg", ovl, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if okk:
                        with state["cond"]:
                            state["ovl"] = bufo.tobytes(); state["cond"].notify_all()
                    tp = perf.lap("proyeccion", "ovl_jpeg", tp)
            except Exception as e:  # noqa: BLE001
                state["rojo_err"] = str(e)
            perf.lap("proyeccion", "total", tp0); perf.vuelta("proyeccion")

    # ---- Jev (TypeSafe «System One», playground/jev.py): emoji, ruta de efecto e intensidad por etiqueta NUEVA del
    # catálogo refinado. Solo por evento (al escribir catalog_refinado.json o con /jev?on=1), en su propio hilo: nada de
    # Jev en rojo_loop ni en el camino de 6 Hz (ahí solo se lee jev_st["mtime"], un dict en memoria).
    jev_cache = jv.Cache(scan / "jev_cache.json")
    jev_st = {"on": bool(a.jev) and jv.disponible(), "thread": None, "pendiente": None, "lock": threading.Lock(), "mtime": {},
              "ultimo": None, "err": None if (not a.jev or jv.disponible()) else "sin AI_GATEWAY_API_KEY (entorno o JEV_ENV_FILE)"}

    def jev_hilo() -> None:
        while True:
            with jev_st["lock"]:
                dir_ = jev_st["pendiente"]; jev_st["pendiente"] = None
                if dir_ is None or not jev_st["on"]:
                    jev_st["thread"] = None; return
            try:
                r_ = jv.consultar_catalogo(dir_, jev_cache, float(sup.get("mm_por_px") or 0), (lw, lh))
                if r_["nuevas"]:                               # la capa catálogo se rearma (su clave lleva este mtime)
                    jev_st["mtime"][str(dir_)] = (dir_ / "jev.json").stat().st_mtime
                jev_st["ultimo"] = {k: v for k, v in r_.items() if k != "items"} | {
                    "items": [{k: it.get(k) for k in ("label", "emoji", "ruta", "intensidad", "fuente")} for it in r_["items"]]}
                jev_st["err"] = r_.get("error")
            except Exception as e:  # noqa: BLE001
                jev_st["err"] = f"{time.strftime('%H:%M:%S')} {str(e)[:160]}"

    def jev_lanzar(dir_) -> None:
        """Encola un catálogo refinado para Jev; un hilo a la vez (si llegan varios mientras, queda el último)."""
        if not jev_st["on"] or dir_ is None or not (Path(dir_) / "catalog_refinado.json").exists():
            return
        with jev_st["lock"]:
            jev_st["pendiente"] = Path(dir_)
            if jev_st["thread"] is None:
                jev_st["thread"] = threading.Thread(target=jev_hilo, daemon=True); jev_st["thread"].start()

    def jev_status() -> dict:
        return {"on": jev_st["on"], "ocupado": jev_st["thread"] is not None, "cache": str(jev_cache.ruta), "etiquetas_en_cache": len(jev_cache),
                "error": jev_st["err"], "ultimo": jev_st["ultimo"], "stats": jv.stats()}

    # ---- capa catálogo: objeto animado (o capa estática) por carpeta, armado en segundo plano
    cat_st = {"obj": None, "static": None, "key": None, "building": None, "pm": None, "inv": None, "t": 0.0, "br": None, "dir": None,
              "n": 0, "t_hz": time.time(), "hz": 0.0}

    def catalogo_fuente() -> Path | None:
        """Carpeta cuyo catálogo se proyecta: la que fijó la última segmentación (con agente: cuando el refinado está
        listo, para no pasar por el catálogo crudo de SAM) o, al arrancar, la última guardada."""
        d_ = state.get("catalogo_dir") or (state.get("ultima_base") or {}).get("dir")
        return Path(d_) if d_ else None

    def catalogo_capa():
        """(color premultiplicado, 255 − α) de la capa catálogo en px del proyector, o None."""
        if not state.get("capas", {}).get("catalogo"):
            return None
        cdir = catalogo_fuente()
        if cdir is None:
            return None
        anim = state.get("catalogo_anim", True)
        # jev: mtime del jev.json de esa carpeta cuando llegaron respuestas nuevas (en memoria, sin E/S aquí), u "off"
        jev_k = jev_st["mtime"].get(str(cdir), 0.0) if jev_st["on"] else "off"
        key_ = f"{cdir}|{(cdir / 'catalog_refinado.json').exists()}|{'anim' if anim else 'est'}|jev:{jev_k}"
        fallo_ = cat_st.get("fallo")
        if key_ != cat_st["key"] and cat_st["building"] is None and not (fallo_ and fallo_[0] == key_ and time.time() - fallo_[1] < 10):
            cat_st["building"] = key_

            def _armar(k=key_, d_=cdir, an=anim):
                t0_ = time.time()
                try:
                    if an:
                        obj_ = ca.CatalogoAnimado(d_, lw, lh, a.rot, emoji_de, SEG_COLORS); lay_ = None
                    else:
                        obj_ = None; lay_ = render_catalogo(d_)
                    cat_st.update(obj=obj_, static=lay_, key=k, pm=None, dir=str(d_))
                    state["catalogo_key"] = k; state["catalogo_err"] = None
                    state["catalogo_init_ms"] = int((time.time() - t0_) * 1000)
                except Exception as e:  # noqa: BLE001                   # se reintenta en 10 s; mientras, sigue el anterior
                    cat_st["fallo"] = (k, time.time()); state["catalogo_err"] = f"armando {d_.name}: {str(e)[:100]}"
                finally:
                    cat_st["building"] = None
            threading.Thread(target=_armar, daemon=True).start()
        if cat_st["key"] is None:
            return None                                                # todavía no hay ninguno armado
        br_ = float(state.get("catalogo_brillo", 0.5))                 # la Osmo se satura con blancos: atenúa la capa
        now = time.time()
        # 0,75/hz: con el lazo de proyección a ~6,7 Hz y 6 Hz pedidos, rinde en cada vuelta (no en una de cada dos)
        due = cat_st["obj"] is not None and now - cat_st["t"] >= 0.75 / max(0.5, float(state.get("catalogo_hz", 6.0)))
        if cat_st["pm"] is None or cat_st["br"] != br_ or due:
            t0_ = time.perf_counter()
            if cat_st["obj"] is not None:
                lay_ = cv2.warpPerspective(cat_st["obj"].frame(now), paper.Hlp, (W, H), flags=cv2.INTER_LINEAR); pre_ = True
            else:
                lay_ = cat_st["static"]; pre_ = False
            if lay_ is None:
                cat_st["pm"] = None; return None
            cat_st["pm"], cat_st["inv"] = capa_a_premult(lay_, br_, pre_)
            cat_st["t"] = now; cat_st["br"] = br_
            state["catalogo_ms"] = int((time.perf_counter() - t0_) * 1000)
            perf.lap("proyeccion", "catalogo_render", t0_)
            cat_st["n"] += 1
            if now - cat_st["t_hz"] >= 2.0:
                cat_st["hz"] = cat_st["n"] / (now - cat_st["t_hz"]); cat_st["n"] = 0; cat_st["t_hz"] = now
        return cat_st["pm"], cat_st["inv"]

    def catalogo_status() -> dict:
        obj_ = cat_st["obj"]
        fuente_ = getattr(obj_, "fuente", None) if obj_ is not None else ("estática" if cat_st["static"] is not None else None)
        return {"on": bool(state.get("capas", {}).get("catalogo")), "dir": cat_st["dir"], "fuente": fuente_,
                "items": len(obj_.items) if obj_ is not None else None, "armando": cat_st["building"] is not None,
                "render_ms": state.get("catalogo_ms"), "hz": round(cat_st["hz"], 1), "hz_objetivo": state.get("catalogo_hz"),
                "init_ms": state.get("catalogo_init_ms"), "brillo": state.get("catalogo_brillo", 0.5), "error": state.get("catalogo_err")}
    threading.Thread(target=rojo_loop, daemon=True).start()

    # ---- visión: manos (MediaPipe) y objetos (CamShift) sobre el lienzo, en su propio hilo
    vis = {"hands": None, "objs": [], "info": {}, "lienzo": None, "seq": 0, "cond": threading.Condition(), "err": None, "hz": 0.0}
    Hlp_arr = np.array(sup["H_lienzo_to_proj"], np.float64)

    def objetos_desde_sam() -> list[str]:
        """Semillas desde la última escena SAM: máscaras en px del proyector → lienzo → cajas."""
        from . import vision_mesa as vm
        base = Path("reports/scene_regions") / scan.name
        runs = sorted(base.glob("auto-*/objects.json"), key=lambda p: p.stat().st_mtime)
        if not runs or vis["lienzo"] is None:
            return []
        d = json.loads(runs[-1].read_text()); objs = d.get("objects", d) if isinstance(d, dict) else d
        objs = objs if isinstance(objs, list) else list(objs.values())
        Hinv = np.linalg.inv(Hlp_arr); out = []
        for o in objs:
            mp_ = runs[-1].parent / o.get("mask", "")
            m = cv2.imread(str(mp_), cv2.IMREAD_GRAYSCALE) if mp_.exists() else None
            if m is None:
                continue
            ml = cv2.warpPerspective(m, Hinv, (lw, lh), flags=cv2.INTER_NEAREST)
            ys, xs = np.nonzero(ml > 127)
            if len(xs) < 400:
                continue
            box = (int(xs.min()), int(ys.min()), int(xs.max() - xs.min()), int(ys.max() - ys.min()))
            if box[2] * box[3] > 0.25 * lw * lh:                       # el teclado y cosas grandes no van por CamShift
                continue
            name = o.get("name") or o.get("label") or o["id"]
            vis["objs"].append(vm.ObjectTracker(name, vis["lienzo"], box, mmpx=float(sup.get("mm_por_px") or 0)))
            out.append(name)
        return out

    SEG_COLORS = {"mano": (80, 200, 255), "hoja": (60, 60, 255), "mouse": (255, 120, 255), "teclado": (255, 200, 60), "moledor": (0, 215, 255),
                  "pad": (120, 220, 120), "mesa": (200, 200, 200), "objeto": (255, 255, 255)}

    # ---- teclado: grilla (teclado.json) + tracker de plantilla, compartidos entre la visión y /teclado/recargar
    tpath = scan / "teclado.json"; ref_path = scan / "teclado_ref.jpg"
    tk = {"grid": None, "grid_track": None, "mtime": 0.0, "kb": None, "base_q": None, "ref_mtime": None, "ref": None,
          "ref_fuente": None, "t_kb": 0.0, "cargado": None}
    tk_lock = threading.Lock()

    def recargar_teclado(ref: str = "auto") -> dict:
        """Relee teclado.json (si solo trae quad_lienzo, ajusta la plantilla de teclas) y rehace el tracker con ese
        cuadrilátero, sin relanzar el feed. La referencia del tracker (imagen de la que se corta la plantilla) según
        `ref`: archivo = teclado_ref.jpg tal cual · base = lienzo limpio (sin proyección) de la última segmentación ·
        actual = lienzo de ahora · no = sin tracker (grilla fija) · auto = archivo si teclado_ref.jpg es tan nuevo como
        teclado.json (lo guardó la misma herramienta), si no base, y si no hay base, actual. Con base/actual la imagen
        queda como teclado_ref.jpg para que un relanzo use la misma referencia."""
        from . import vision_mesa as vm
        from . import teclado_grid as tg
        with tk_lock:
            mt = tpath.stat().st_mtime
            g0 = json.loads(tpath.read_text())
            q = np.array(g0["quad_lienzo"], np.float64).reshape(4, 2)
            if not g0.get("keys") or "poly_proj" not in (g0["keys"][0] or {}):
                g0 = {**g0, **tg.fit(q.astype(np.float32), Hlp_arr, float(sup.get("mm_por_px") or 0), rot=int(g0.get("rot", 180)),
                                     inset=float(g0.get("inset", 0.012)))}
            base_jpg = None
            ub = state.get("ultima_base") or {}
            if ub.get("dir") and (Path(ub["dir"]) / "lienzo.jpg").exists():
                base_jpg = Path(ub["dir"]) / "lienzo.jpg"
            modo = ref
            if modo == "auto":
                if ref_path.exists() and ref_path.stat().st_mtime >= mt - 10:
                    modo = "archivo"
                else:
                    modo = "base" if base_jpg is not None else "actual"
            img_ref, fuente = None, None
            if modo == "archivo":
                img_ref, fuente = cv2.imread(str(ref_path)), str(ref_path)
            elif modo == "base":
                img_ref, fuente = (cv2.imread(str(base_jpg)) if base_jpg else None), str(base_jpg)
            elif modo == "actual":
                img_ref, fuente = (None if vis["lienzo"] is None else vis["lienzo"].copy()), "lienzo actual"
            elif modo != "no":
                raise ValueError(f"ref desconocida: {modo} (auto, archivo, base, actual, no)")
            kb = None
            if modo != "no":
                if img_ref is None:
                    raise RuntimeError(f"sin imagen de referencia para el tracker ({modo})")
                kb = vm.KeyboardTracker(img_ref, q)
                if modo != "archivo":
                    resp_ = scan / "teclado_ref.orig.jpg"                  # respaldo de la referencia previa (una vez)
                    if ref_path.exists() and not resp_.exists():
                        import shutil
                        shutil.copy2(ref_path, resp_)
                    cv2.imwrite(str(ref_path), img_ref)
            tk.update(grid=g0, grid_track=None, mtime=mt, kb=kb, base_q=q, ref=modo, ref_fuente=fuente, t_kb=0.0,
                      ref_mtime=ref_path.stat().st_mtime if ref_path.exists() else None, cargado=time.strftime("%H:%M:%S"))
        vis["grid"] = g0; vis["kb_info"] = None
        state.pop("grid_shown", None); paper.sticky.drop("teclado")     # la grilla nueva se proyecta ya, sin esperar al pegajoso
        return {"ok": True, "quad": q.round(1).tolist(), "teclas": len(g0.get("keys") or []), "rot": g0.get("rot"), "inset": g0.get("inset"),
                "ref": modo, "ref_fuente": fuente, "tracker": kb is not None, "archivo": str(tpath), "cargado": tk["cargado"]}

    def vision_loop():
        from . import vision_mesa as vm
        hands = None
        if not a.no_manos:
            try:
                hands = vm.Hands(float(sup.get("mm_por_px") or 0), Hlp_arr, scan / "hand_rig.json", scale=a.manos_scale)
                vis["hands"] = hands
            except Exception as e:  # noqa: BLE001
                vis["err"] = f"manos: {e}"; print(vis["err"], flush=True)
        seeded = False; last = 0; n, t0 = 0, time.time(); it_vis = 0
        altura_malla = None
        if quest:                                                       # 3D barato: rayo cámara → malla MRUK (estática)
            try:
                altura_malla = lq.AlturaMalla(Path(a.calib))
            except Exception as e:  # noqa: BLE001
                vis["alturas"] = {"error": f"malla: {str(e)[:100]}"}
        cat_centros = {"key": None, "items": []}

        def objetos_catalogo() -> list[dict]:
            """Centros (px del lienzo) de los ítems del catálogo vigente (refinado si existe), cacheados por archivo."""
            d_ = catalogo_fuente()
            if d_ is None:
                return []
            f_ = d_ / "catalog_refinado.json"
            f_ = f_ if f_.exists() else d_ / "catalog.json"
            if not f_.exists():
                return []
            k_ = (str(f_), f_.stat().st_mtime)
            if cat_centros["key"] != k_:
                its = json.loads(f_.read_text()).get("items") or []
                cat_centros["items"] = [{"id": it.get("id"), "label": it.get("label"),
                                         "centro_lienzo": [round((it["bbox"][0] + it["bbox"][2]) / 2, 1), round((it["bbox"][1] + it["bbox"][3]) / 2, 1)]}
                                        for it in its if it.get("bbox") and it.get("label") not in ("mesa", "pad")]
                cat_centros["key"] = k_
            return cat_centros["items"]
        from . import teclado_grid as tg
        mmpx_ = float(sup.get("mm_por_px") or 0)
        mouse = vm.MouseTracker(mmpx_, user_edge="top" if a.rot % 360 == 180 else "bottom") if mmpx_ else None
        if mouse is not None and quest:                                 # Quest: croma corrida y menos luz (ver MouseTracker)
            mouse.v_min = None; mouse.s_max = 95; mouse.largo_mm = (95.0, 140.0); mouse.corto_mm = (48.0, 80.0)
        try:                                                            # grilla + tracker como estaban guardados
            if tpath.exists():
                recargar_teclado("archivo" if ref_path.exists() else "no")
        except Exception as e:  # noqa: BLE001
            print("teclado:", e, flush=True)
        bridge = None; prev_touch: dict[str, bool] = {}
        if a.runtime:                                                   # toques al runtime viejo; con la ventana no hay a quién
            from .hands.bridge import TouchBridge
            bridge = TouchBridge(a.runtime, source="osmo")
        t_vis = 0.0
        while True:
            if a.vision_hz > 0:                                         # tope de Hz: deja CPU para el resto (la Mac va justa)
                dt_ = 1.0 / a.vision_hz - (time.time() - t_vis)
                if dt_ > 0:
                    time.sleep(dt_)
            with vis["cond"]:
                vis["cond"].wait_for(lambda: vis["seq"] > last, 1.0)
                if vis["seq"] <= last:
                    continue
                last = vis["seq"]; lienzo = vis["lienzo"]
            t = time.time(); it_vis += 1; t_vis = t
            tp = time.perf_counter(); tp0 = tp
            info = {}
            try:                                                        # teclado.json / teclado_ref.jpg cambiados por fuera
                if tpath.exists() and tpath.stat().st_mtime != tk["mtime"]:
                    recargar_teclado("auto")
                elif tk["kb"] is not None and ref_path.exists() and ref_path.stat().st_mtime != tk["ref_mtime"]:
                    recargar_teclado("archivo")
            except Exception as e:  # noqa: BLE001
                vis["kb_err"] = str(e)[:120]
                tk["mtime"] = tpath.stat().st_mtime if tpath.exists() else 0.0     # no reintentar en cada cuadro
                tk["ref_mtime"] = ref_path.stat().st_mtime if ref_path.exists() else None
            kb = tk["kb"]
            grid = tk["grid_track"] or tk["grid"]
            # teclado seguido (traslación + giro) cada 0,3 s → grilla recalculada
            if kb is not None and t - tk["t_kb"] >= 0.3:
                tk["t_kb"] = t
                try:
                    r = kb.update(lienzo, t)
                    # el tracker solo manda si está seguro y el salto respecto al ajuste guardado es moderado; si no, queda
                    # la grilla del archivo (ajuste fino SAM + keycaps). Evita que una correlación floja corra las teclas.
                    base_q = tk["base_q"]
                    salto = float(np.linalg.norm(np.array(r["quad_lienzo"]) - base_q, axis=1).max()) if (r["ok"] and base_q is not None) else None
                    aplica = bool(r["ok"] and grid and r["score"] >= 0.55 and salto is not None and salto < 60)
                    if aplica:
                        g_t = tg.fit(np.array(r["quad_lienzo"], np.float32), Hlp_arr, mmpx_, rot=int(grid.get("rot", 180)), inset=float(grid.get("inset", 0.012)))
                        g_t["tracking"] = {"score": r["score"], "hace_s": r["hace_s"], "salto_px": round(salto, 1)}
                        with tk_lock:
                            if tk["kb"] is kb:                          # no pisar una recarga que llegó mientras tanto
                                tk["grid_track"] = g_t; grid = g_t
                    info_kb = {**r, "aplicado": aplica, "salto_px": None if salto is None else round(salto, 1)}
                except Exception as e:  # noqa: BLE001
                    info_kb = {"ok": False, "error": str(e)[:100]}
                vis["kb_info"] = info_kb
                tp = perf.lap("vision", "teclado", tp)
            vis["grid"] = grid
            tp = time.perf_counter()
            # mouse (forma + zonas) cada cuadro, excluyendo las hojas
            if mouse is not None:
                try:
                    with paper.lock:
                        qs = [s["quad"].copy() for s in paper.sheets]
                    vis["mouse"] = mouse.update(lienzo, qs, t)
                    mdl = mouse.model()
                    paper.mouse_outline = mdl["outline"] if mdl else None
                except Exception as e:  # noqa: BLE001
                    vis["mouse"] = {"ok": False, "error": str(e)[:100]}
                tp = perf.lap("vision", "mouse", tp)
            info["teclado"] = vis.get("kb_info"); info["mouse"] = vis.get("mouse")
            # moledor(es): círculos oscuros/metálicos de 50-70 mm de diámetro (Hough a media resolución), fuera de las hojas
            if mmpx_ and t - vis.get("t_grind", 0.0) >= a.moledor_cada:
                vis["t_grind"] = t; tp = time.perf_counter()
                try:
                    sc = 0.5
                    g_ = cv2.cvtColor(cv2.resize(lienzo, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
                    g_ = cv2.medianBlur(g_, 5)
                    rmin, rmax = int(18 / mmpx_ * sc), int(36 / mmpx_ * sc)
                    circles = cv2.HoughCircles(g_, cv2.HOUGH_GRADIENT, dp=1.2, minDist=rmin * 1.6, param1=110, param2=34, minRadius=rmin, maxRadius=rmax)
                    found = []
                    if circles is not None:
                        with paper.lock:
                            qs = [s["quad"].copy() for s in paper.sheets]
                        # ni dentro de las hojas, ni sobre el teclado (sus teclas dan círculos falsos), ni sobre el mouse
                        excl = list(qs)
                        if vis.get("grid"):
                            excl.append(np.array(vis["grid"]["quad_lienzo"], np.float64))
                        mz_ = (vis.get("mouse") or {}).get("zonas")
                        if mz_:
                            excl.append(np.array(mz_["outline"], np.float64))
                        for cx_, cy_, r_ in circles[0][:8]:
                            cxl, cyl, rl = float(cx_ / sc), float(cy_ / sc), float(r_ / sc)
                            if any(cv2.pointPolygonTest(q.astype(np.float32), (cxl, cyl), False) >= 0 for q in excl):
                                continue
                            # un objeto real: el disco es más oscuro o más texturado que el pad liso alrededor
                            yy0, yy1 = int(max(0, cyl - rl)), int(min(lh, cyl + rl)); xx0, xx1 = int(max(0, cxl - rl)), int(min(lw, cxl + rl))
                            disco = cv2.cvtColor(lienzo[yy0:yy1, xx0:xx1], cv2.COLOR_BGR2GRAY)
                            if not disco.size:
                                continue
                            oscuro = float(disco.mean()); textura = float(disco.std())
                            if oscuro < 200 and (oscuro < 150 or textura > 28):
                                if any(math.hypot(f_["cx"] - cxl, f_["cy"] - cyl) < 1.2 * max(f_["r"], rl) for f_ in found):
                                    continue                              # el mismo círculo detectado dos veces
                                found.append({"cx": cxl, "cy": cyl, "r": rl, "d_mm": round(2 * rl * mmpx_, 1), "oscuro": round(oscuro, 1), "textura": round(textura, 1)})
                            if len(found) >= 3:
                                break
                    prev = vis.get("grinders") or []
                    out_g = []
                    for f_ in found:                                   # suavizado con el anterior más cercano
                        near = [p_ for p_ in prev if math.hypot(p_["cx"] - f_["cx"], p_["cy"] - f_["cy"]) < 40]
                        if near:
                            p_ = near[0]; f_ = {**f_, "cx": 0.6 * p_["cx"] + 0.4 * f_["cx"], "cy": 0.6 * p_["cy"] + 0.4 * f_["cy"], "r": 0.6 * p_["r"] + 0.4 * f_["r"]}
                        f_["t"] = t; out_g.append(f_)
                    for p_ in prev:                                    # memoria corta si parpadea
                        if t - p_.get("t", 0) < 0.8 and not any(math.hypot(p_["cx"] - f_["cx"], p_["cy"] - f_["cy"]) < 40 for f_ in out_g):
                            out_g.append(p_)
                    vis["grinders"] = out_g
                except Exception as e:  # noqa: BLE001
                    vis["grinders_err"] = str(e)[:100]
                perf.lap("vision", "moledores", tp)
            info["moledores"] = [{k_: (round(v_, 1) if isinstance(v_, float) else v_) for k_, v_ in g_.items()} for g_ in (vis.get("grinders") or [])]
            # segmentos SAM seguidos por flujo óptico (Lucas-Kanade) entre corridas; render del overlay por cuadro
            sg_ = state.get("seg") or {}
            if sg_.get("on") and sg_.get("tracks") is not None and sg_.get("gray") is not None:   # solo con el SAM periódico prendido
                tp = time.perf_counter()
                try:
                    tsc = sg_["tsc"]
                    gcur = cv2.cvtColor(cv2.resize(lienzo, None, fx=tsc, fy=tsc, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
                    gprev = sg_["gray"]
                    for tr in sg_["tracks"]:
                        p0 = tr["pts"]
                        if p0 is None or len(p0) < 4:
                            continue
                        p1, st_, _ = cv2.calcOpticalFlowPyrLK(gprev, gcur, p0.reshape(-1, 1, 2), None, winSize=(21, 21), maxLevel=3)
                        ok_ = st_.ravel() == 1
                        if ok_.sum() < 4:
                            continue
                        d = (p1.reshape(-1, 2) - p0)[ok_]
                        dx, dy = float(np.median(d[:, 0])), float(np.median(d[:, 1]))
                        tr["pts"] = p1.reshape(-1, 2)[ok_].astype(np.float32)
                        tr["dx"] += dx; tr["dy"] += dy
                        if abs(dx) > 0.3 or abs(dy) > 0.3:           # mover la máscara solo si hubo desplazamiento real
                            M = np.float32([[1, 0, dx], [0, 1, dy]])
                            tr["mask"] = cv2.warpAffine(tr["mask"].astype(np.uint8), M, (gcur.shape[1], gcur.shape[0]), flags=cv2.INTER_NEAREST).astype(bool)
                    sg_["gray"] = gcur
                    # render a ~10 Hz
                    if t - sg_.get("t_render", 0) >= 0.1 or sg_.get("render"):
                        sg_["t_render"] = t; sg_["render"] = False
                        ov = np.zeros((gcur.shape[0], gcur.shape[1], 3), np.uint8)
                        for tr in sg_["tracks"]:
                            col = SEG_COLORS.get(tr["label"], (255, 255, 255)); ov[tr["mask"]] = col
                            cnts, _ = cv2.findContours(tr["mask"].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                            cv2.drawContours(ov, cnts, -1, (255, 255, 255), 1)
                        for tr in sg_["tracks"]:
                            cx_, cy_ = int((tr["centro"][0] + tr["dx"] / tsc) * tsc), int((tr["centro"][1] + tr["dy"] / tsc) * tsc)
                            cv2.putText(ov, tr["label"], (cx_ - 16, cy_), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
                            cv2.putText(ov, tr["label"], (cx_ - 16, cy_), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
                        ov = cv2.resize(ov, (lw, lh), interpolation=cv2.INTER_NEAREST)
                        okx, bufx = cv2.imencode(".jpg", ov, [cv2.IMWRITE_JPEG_QUALITY, 75])
                        if okx:
                            with state["cond"]:
                                state["segjpg"] = bufx.tobytes(); state["cond"].notify_all()
                    info["segmentos_seguidos"] = [{"label": tr["label"], "dx": round(tr["dx"] / tsc, 1), "dy": round(tr["dy"] / tsc, 1), "pts": 0 if tr["pts"] is None else int(len(tr["pts"]))} for tr in sg_["tracks"]]
                except Exception as e:  # noqa: BLE001
                    sg_["err"] = f"flujo: {str(e)[:120]}"
                perf.lap("vision", "flujo", tp)
            # las manos tapan hojas: el tracking de la hoja se congela bajo una mano
            try:
                paper.occluders = [np.array(h["mesh"][0], np.float64) for h in (hands.hands if hands else [])]
            except Exception:  # noqa: BLE001
                paper.occluders = []
            if hands is not None and it_vis % max(1, a.manos_cada) == 0:
                tp = time.perf_counter()
                try:
                    hs = hands.update(lienzo, t)
                    tp = perf.lap("vision", "manos", tp)
                    try:
                        altura_dedos(hs)
                    except Exception as e:  # noqa: BLE001
                        cam2["err"] = f"altura: {e}"
                    if grid:
                        for h in hs:
                            h["teclas"] = {f: tg.key_at(grid, *h["tips"][f]) for f in h["tips"]}
                            h["tecla"] = h["teclas"]["indice"]
                    info["manos"] = hands.status()
                    info["teclas_bajo_dedos"] = sorted({k for h in hs for k in (h.get("teclas") or {}).values() if k})
                    vis["manos_info"] = (info["manos"], info["teclas_bajo_dedos"])
                    # al runtime: manos (linterna, máscara) y toques (down/move/up) en px del proyector
                    try:
                        if not a.runtime:
                            raise LookupError("sin runtime")
                        from . import pad_checker as pc
                        pc.runtime_cmd(a.runtime, "hands", hands=[{"id": h["id"], "x": h["tip_proj"][0], "y": h["tip_proj"][1], "tip": h["tip_proj"],
                                                                 "touching": h["touching"], "gesture": h["gesture"], "handed": h["handed"],
                                                                 "polygon": [list(p) for p in np.array(cv2.perspectiveTransform(np.array(h["mesh"][0], np.float64).reshape(-1, 1, 2), Hlp_arr).reshape(-1, 2).round(1))],
                                                                 "t": h["t"]} for h in hs], source="osmo")
                    except Exception:  # noqa: BLE001
                        pass
                    for h in hs:
                        was = prev_touch.get(h["id"], False)
                        if bridge is not None and (h["touching"] != was or h["touching"]):
                            st = "down" if (h["touching"] and not was) else ("up" if (was and not h["touching"]) else "move")
                            bridge.send({"id": "o" + h["id"], "state": st, "x": h["tip_proj"][0], "y": h["tip_proj"][1], "space": "proj",
                                         "projector": "proj0", "hand": h["id"], "handed": h["handed"], "trigger": h["trigger"], "t": h["t"], "source": "osmo"})
                        prev_touch[h["id"]] = h["touching"]
                    for hid in [k for k in prev_touch if k not in {h["id"] for h in hs}]:
                        if prev_touch[hid] and bridge is not None:
                            bridge.send({"id": "o" + hid, "state": "up", "x": 0, "y": 0, "space": "proj", "projector": "proj0", "t": t, "source": "osmo", "reason": "mano perdida"})
                        prev_touch.pop(hid, None)
                except Exception as e:  # noqa: BLE001
                    vis["err"] = f"manos: {e}"
                perf.lap("vision", "manos_envio", tp)                  # teclas bajo dedos + toques y manos al runtime
            elif hands is not None and vis.get("manos_info"):           # cuadro sin MediaPipe: lo último
                info["manos"], info["teclas_bajo_dedos"] = vis["manos_info"]
            if altura_malla is not None and t - vis.get("t_alt", 0.0) >= 1.0:   # 1 vez/s: ~4 ms por rayo
                vis["t_alt"] = t; tp = time.perf_counter()
                try:
                    tips_ = [(h["id"], h["tips"]["indice"]) for h in (hands.hands if hands is not None else [])]
                    objs_ = objetos_catalogo()[:16]
                    hs_ = altura_malla.altura([p_ for _, p_ in tips_] + [o_["centro_lienzo"] for o_ in objs_])
                    vis["alturas"] = {"fuente": "malla MRUK estática (Space Setup), rayo desde la cámara del Quest; no ve manos ni objetos nuevos",
                                      "triangulos": altura_malla.n,
                                      "indice": [{"mano": hid_, "lienzo": p_, "mm": h_} for (hid_, p_), h_ in zip(tips_, hs_[:len(tips_)])],
                                      "objetos": [{**o_, "mm": h_} for o_, h_ in zip(objs_, hs_[len(tips_):])]}
                except Exception as e:  # noqa: BLE001
                    vis["alturas"] = {"error": str(e)[:120]}
                perf.lap("vision", "alturas", tp)
            if vis.get("alturas") is not None:
                info["alturas"] = vis["alturas"]
            if a.objetos_auto and not seeded:
                try:
                    print("objetos sembrados:", objetos_desde_sam(), flush=True)
                except Exception as e:  # noqa: BLE001
                    print("objetos:", e, flush=True)
                seeded = True
            tp = time.perf_counter()
            try:
                info["objetos"] = [o.update(lienzo, t) for o in vis["objs"]]
            except Exception as e:  # noqa: BLE001
                vis["err"] = f"objetos: {e}"
            if vis["objs"]:
                perf.lap("vision", "objetos", tp)
            vis["info"] = info
            perf.lap("vision", "total", tp0); perf.vuelta("vision")
            n += 1
            if time.time() - t0 >= 2:
                vis["hz"], n, t0 = n / (time.time() - t0), 0, time.time()

    threading.Thread(target=vision_loop, daemon=True).start()

    # ---- segmentación semántica periódica (MobileSAM ONNX en CPU + etiquetas por lo que ya se sigue)
    # con la re-segmentación base automática encendida, el SAM periódico (y su flujo óptico) parte apagado: libera CPU
    seg = {"on": bool(a.seg) and not (a.rebase_cada > 0), "cada": float(a.seg_cada), "grid": (10, 6), "ancho": 640, "jpg": None, "info": {}, "err": None, "n": 0, "ms": 0.0}
    state["seg"] = seg
    SEG_COLORS = {"mano": (80, 200, 255), "hoja": (60, 60, 255), "mouse": (255, 120, 255), "teclado": (255, 200, 60), "moledor": (0, 215, 255),
                  "pad": (120, 220, 120), "mesa": (200, 200, 200), "objeto": (255, 255, 255)}

    def seg_loop():
        import sys as _sys
        model_dir = Path.home() / "hardware/models/slscan-segmentation"
        try:
            try:
                import onnxruntime  # noqa: F401
            except ImportError:
                _sys.path.insert(0, str(model_dir / "python")); import onnxruntime  # noqa: F401
            from .appearance_prepare import MobileSAM
            from .automatic_masks import automatic_masks
            model = MobileSAM(model_dir, "cpu", threads=2)
            seg["model"] = model; seg["lock"] = threading.Lock(); seg["automatic_masks"] = automatic_masks
        except Exception as e:  # noqa: BLE001
            seg["err"] = f"MobileSAM no disponible: {e}"; print(seg["err"], flush=True); return
        pad_l = None
        try:
            pq = json.loads((scan / "pad_quad.json").read_text())
            qp = np.array(pq.get("quad_proj_adjusted") or pq["quad_proj_tl_tr_br_bl"], np.float64)
            pad_l = cv2.perspectiveTransform(qp.reshape(-1, 1, 2), np.linalg.inv(Hlp_arr)).reshape(4, 2)
        except Exception:  # noqa: BLE001
            pass
        while True:
            if not seg["on"] or vis["lienzo"] is None:
                time.sleep(0.3); continue
            t0 = time.time()
            try:
                lz = vis["lienzo"]; sc = seg["ancho"] / lz.shape[1]
                small = cv2.resize(lz, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
                with seg["lock"]:
                    props, info = automatic_masks(model, small, columns=seg["grid"][0], rows=seg["grid"][1], min_area=300)
                # etiquetas: por solape con lo que ya se sigue; piel → mano
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                skin = (((hsv[..., 0] <= 18) | (hsv[..., 0] >= 172)) & (hsv[..., 1] >= 40) & (hsv[..., 1] <= 180) & (hsv[..., 2] >= 90))
                if vis.get("gris"):                                     # cámara en luma (Quest): sin piel por color
                    skin[:] = False
                def poly_mask(poly):
                    m = np.zeros(small.shape[:2], np.uint8)
                    cv2.fillPoly(m, [(np.asarray(poly, np.float64) * sc).astype(np.int32).reshape(-1, 1, 2)], 1); return m.astype(bool)
                refs = []
                with paper.lock:
                    for s in paper.sheets:
                        refs.append(("hoja", poly_mask(s["quad"])))
                mz_ = (vis.get("mouse") or {}).get("zonas")
                if mz_:
                    refs.append(("mouse", poly_mask(mz_["outline"])))
                if vis.get("grid"):
                    refs.append(("teclado", poly_mask(vis["grid"]["quad_lienzo"])))
                for g_ in vis.get("grinders") or []:
                    m = np.zeros(small.shape[:2], np.uint8); cv2.circle(m, (int(g_["cx"] * sc), int(g_["cy"] * sc)), int(g_["r"] * sc), 1, -1); refs.append(("moledor", m.astype(bool)))
                if vis["hands"] is not None:
                    for h in vis["hands"].hands:
                        for poly in h["mesh"]:
                            refs.append(("mano", poly_mask(poly)))
                padm = poly_mask(pad_l) if pad_l is not None else None
                out = []
                for p in props:
                    m = p["mask"]; area = max(1, int(m.sum()))
                    label, best = None, 0.0
                    for name, rm in refs:
                        ov = float((m & rm).sum()) / area
                        if ov > best and ov > 0.5:
                            label, best = name, ov
                    if label is None and float((m & skin).sum()) / area > 0.45:
                        label = "mano"
                    if label is None:
                        inside = float((m & padm).sum()) / area if padm is not None else 0.0
                        label = "pad" if inside > 0.7 and area > 0.08 * m.size else ("mesa" if area > 0.08 * m.size else "objeto")
                    out.append({"label": label, "area_px": int(area / (sc * sc)), "score": round(float(p["score"]), 3),
                                "centro": [round(float(v) / sc, 1) for v in p["point"]], "mask": m})
                # tracks para el flujo óptico entre corridas: máscara + puntos buenos dentro, en la escala de seguimiento
                tsc = 0.5
                gray_t = cv2.cvtColor(cv2.resize(lz, None, fx=tsc, fy=tsc, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
                tracks = []
                for o in out:
                    mt = cv2.resize(o["mask"].astype(np.uint8), (gray_t.shape[1], gray_t.shape[0]), interpolation=cv2.INTER_NEAREST)
                    pts = cv2.goodFeaturesToTrack(gray_t, maxCorners=60, qualityLevel=0.01, minDistance=6, mask=mt)
                    tracks.append({"label": o["label"], "mask": mt.astype(bool), "pts": None if pts is None else pts.reshape(-1, 2).astype(np.float32),
                                   "dx": 0.0, "dy": 0.0, "area_px": o["area_px"], "centro": o["centro"]})
                with state["cond"]:
                    seg["tracks"] = tracks; seg["gray"] = gray_t; seg["tsc"] = tsc; seg["render"] = True
                seg["info"] = {"n": len(out), "segmentos": [{k: v for k, v in o.items() if k != "mask"} for o in out], "sam": {k: v for k, v in info.items() if k != "proposals"},
                               "ms": round((time.time() - t0) * 1000), "t": time.strftime("%H:%M:%S")}
                seg["n"] += 1; seg["ms"] = round((time.time() - t0) * 1000); seg["err"] = None
            except Exception as e:  # noqa: BLE001
                seg["err"] = str(e)[:160]
            time.sleep(max(0.2, seg["cada"] - (time.time() - t0)))

    threading.Thread(target=seg_loop, daemon=True).start()

    state["capas"] = state.get("capas") or {"hoja": True, "mouse": True, "teclado": True, "moledor": True, "catalogo": False}
    try:                                                                # última segmentación base guardada (sobrevive reinicios)
        prev_items = sorted((scan / "items").glob("*/catalog.json"), key=lambda p_: p_.stat().st_mtime)
        if prev_items:
            state["ultima_base"] = json.loads(prev_items[-1].read_text())
            # la capa catálogo arranca en la última carpeta; si esa fue automática y su agente no alcanzó a refinar
            # (relanzo a medias), en la última que sí quedó refinada
            cdir_ = prev_items[-1].parent
            if not (cdir_ / "catalog_refinado.json").exists() and state["ultima_base"].get("auto"):
                ref_ = [p_.parent for p_ in prev_items if (p_.parent / "catalog_refinado.json").exists()]
                cdir_ = ref_[-1] if ref_ else cdir_
            state["catalogo_dir"] = str(cdir_)
            jev_lanzar(cdir_)                                   # al arrancar: solo pregunta lo que no esté en la caché
    except Exception:  # noqa: BLE001
        pass

    def emoji_de(label: str) -> str:
        """Primero la caché de Jev (en memoria; solo si /jev está prendido), si no, el diccionario EMOJI."""
        if jev_st["on"]:
            e_ = jev_cache.emoji(label)
            if e_:
                return e_
        return emoji_dict(label)

    def render_catalogo(dir_: Path) -> np.ndarray | None:
        """Capa estática 1920x1080 (BGRA) con las máscaras del catálogo (refinado si existe) coloreadas, su contorno y
        una etiqueta 'emoji nombre' en el centroide; dibujada en el lienzo y llevada al proyector."""
        cat = None
        for name in ("catalog_refinado.json", "catalog.json"):
            p_ = dir_ / name
            if p_.exists():
                cat = json.loads(p_.read_text()); break
        if not cat:
            return None
        from PIL import Image, ImageDraw, ImageFont
        layer = np.zeros((lh, lw, 4), np.uint8)
        rng = np.random.default_rng(7)
        labels = []
        for it in sorted(cat["items"], key=lambda x: x.get("z", 0)):
            m = cv2.imread(str(dir_ / it["mask"]), cv2.IMREAD_GRAYSCALE)
            if m is None:
                continue
            m = m > 127
            col = SEG_COLORS.get(it["label"], None) or tuple(int(v) for v in rng.integers(80, 255, 3))
            layer[m, :3] = col; layer[m, 3] = 110
            cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(layer, cnts, -1, (255, 255, 255, 255), 2)
            ys_, xs_ = np.nonzero(m)
            labels.append((it["label"], float(xs_.mean()), float(ys_.mean())))
        pil = Image.fromarray(cv2.cvtColor(layer, cv2.COLOR_BGRA2RGBA))
        d_ = ImageDraw.Draw(pil)
        try:
            f_e = ImageFont.truetype("/System/Library/Fonts/Apple Color Emoji.ttc", 64)
        except Exception:  # noqa: BLE001
            f_e = None
        try:
            f_t = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 34)
        except Exception:  # noqa: BLE001
            f_t = ImageFont.load_default()
        for label, cx_, cy_ in labels:
            txt = label
            # Max está al otro lado: la etiqueta se dibuja girada 180° (rot) para que la lea derecha
            tmp = Image.new("RGBA", (520, 110), (0, 0, 0, 0)); dt = ImageDraw.Draw(tmp)
            if f_e is not None:
                try:
                    dt.text((6, 10), emoji_de(label), font=f_e, embedded_color=True)
                except Exception:  # noqa: BLE001
                    pass
            dt.text((90, 34), txt, font=f_t, fill=(0, 0, 0, 255), stroke_width=4, stroke_fill=(0, 0, 0, 255))
            dt.text((90, 34), txt, font=f_t, fill=(255, 255, 255, 255))
            if a.rot % 360 == 180:
                tmp = tmp.rotate(180)
            bw, bh = tmp.size
            pil.alpha_composite(tmp, (int(max(0, min(lw - bw, cx_ - bw / 2))), int(max(0, min(lh - bh, cy_ - bh / 2)))))
        layer = cv2.cvtColor(np.array(pil), cv2.COLOR_RGBA2BGRA)
        return cv2.warpPerspective(layer, paper.Hlp, (W, H), flags=cv2.INTER_LINEAR)

    def z_index(label: str, area_frac: float) -> int:
        """Capa de apilamiento: mesa/pad abajo, soportes (teclado, hoja) encima, objetos chicos encima de ellos, manos arriba."""
        if label == "mano":
            return 10
        if label in ("mesa", "pad"):
            return 0
        if label in ("teclado", "hoja", "libreta", "cuaderno"):
            return 2
        return 5 if area_frac < 0.03 else 3

    base_lock = threading.Lock()                                       # una segmentación base a la vez (manual o automática)

    def segmentacion_base(con_agente: bool = False, auto: bool = False, grid: tuple[int, int] = (24, 14)) -> dict:
        """Apaga las capas proyectadas (confunden a SAM), espera a que la cámara vea la escena limpia, la captura y
        vuelve a prender las capas (solo ~2 s a oscuras: la capa catálogo anterior sigue mientras SAM trabaja); corre
        MobileSAM (grilla densa) sobre esa captura, suma las manos de MediaPipe, asigna z-index y guarda por ítem:
        máscara PNG, recorte RGBA, SVG del contorno, más catalog.json. Con `con_agente`, lanza en segundo plano la
        refinación por el agente de visión (Sonnet por CLI) que corrige nombres y cajas y rehace SAM con prompts de
        caja; la capa catálogo pasa a la carpeta nueva recién cuando el refinado está listo (o si el agente falla)."""
        if seg.get("model") is None:
            raise RuntimeError("MobileSAM no está cargado todavía")
        if not base_lock.acquire(blocking=False):
            raise RuntimeError("ya hay una segmentación base en curso")
        prev = dict(state["capas"]); prev_luz, prev_auto = state.get("luz"), state.get("autoluz", True)
        restaurado = [False]

        def restaurar():
            if not restaurado[0]:
                state["capas"].update(prev); state["luz"] = prev_luz; state["autoluz"] = prev_auto; restaurado[0] = True
        out_dir = scan / "items" / time.strftime("%Y%m%d-%H%M%S")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            for k in state["capas"]:
                state["capas"][k] = False
            state["autoluz"] = False; state["luz"] = 0
            time.sleep(1.2 if quest else 2.0)                              # latencia AirPlay (~0,3 s con la ventana) + auto-exposición
            lz = vis["lienzo"].copy()
            if quest and a.cam_full_url:                                   # el cuadro completo (1728) para SAM y el agente
                try:
                    lz = lienzo_full(); state["base_fuente"] = "cam_full 1728"
                except Exception as e:  # noqa: BLE001
                    state["base_fuente"] = f"stream (cam_full falló: {str(e)[:80]})"
            else:
                state["base_fuente"] = "stream"
            # lo que siguen los trackers, en el mismo instante de la captura (SAM tarda ~20 s y las cosas se mueven)
            mz_cap = (vis.get("mouse") or {}).get("zonas")
            grid_cap = vis.get("grid")
            grinders_cap = [dict(g_) for g_ in (vis.get("grinders") or [])]
            hands_cap = [{"id": h["id"], "mesh": [np.asarray(p_, np.float64).copy() for p_ in h["mesh"]]} for h in (vis["hands"].hands if vis["hands"] is not None else [])]
            sheet_quads_cap = [q for _, q in paper.shown_quads()]
            with paper.lock:
                sheet_quads_cap += [s["quad"].copy() for s in paper.sheets]
            restaurar()                                                    # escena limpia capturada: las capas vuelven ya
            cv2.imwrite(str(out_dir / "lienzo.jpg"), lz)
            sc = 960.0 / lz.shape[1]
            small = cv2.resize(lz, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            # piel: tono rojizo estrecho (una libreta amarilla tiene H≈25-30 y no es mano); con la cámara en luma, nada
            skin = (((hsv[..., 0] <= 18) | (hsv[..., 0] >= 172)) & (hsv[..., 1] >= 40) & (hsv[..., 1] <= 180) & (hsv[..., 2] >= 90))
            if vis.get("gris"):
                skin[:] = False
            def poly_mask(poly):
                m = np.zeros(small.shape[:2], np.uint8)
                cv2.fillPoly(m, [(np.asarray(poly, np.float64) * sc).astype(np.int32).reshape(-1, 1, 2)], 1); return m.astype(bool)
            # referencias de los trackers en el instante de la captura
            refs = []
            for q in sheet_quads_cap:
                refs.append(("hoja", poly_mask(q)))
            t0 = time.time()
            with seg["lock"]:
                props, info = seg["automatic_masks"](seg["model"], small, columns=int(grid[0]), rows=int(grid[1]), min_area=250)
            t_sam = time.time() - t0
            if mz_cap:
                refs.append(("mouse", poly_mask(mz_cap["outline"])))
            if grid_cap:
                refs.append(("teclado", poly_mask(grid_cap["quad_lienzo"])))
            for g_ in grinders_cap:
                m = np.zeros(small.shape[:2], np.uint8); cv2.circle(m, (int(g_["cx"] * sc), int(g_["cy"] * sc)), int(g_["r"] * sc), 1, -1); refs.append(("moledor", m.astype(bool)))
            hand_masks = []
            for h in hands_cap:
                hm = np.zeros(small.shape[:2], np.uint8)
                for poly in h["mesh"]:
                    cv2.fillPoly(hm, [(np.asarray(poly, np.float64) * sc).astype(np.int32).reshape(-1, 1, 2)], 1)
                hand_masks.append((h["id"], hm.astype(bool)))
                refs.append(("mano", hm.astype(bool)))
            items = []
            total = small.shape[0] * small.shape[1]
            for p in props:
                m = p["mask"]; area = max(1, int(m.sum()))
                label, best = None, 0.0
                for name, rm in refs:
                    ov = float((m & rm).sum()) / area
                    if ov > best and ov > 0.5:
                        label, best = name, ov
                if label is None and float((m & skin).sum()) / area > 0.45:
                    label = "mano"
                if label is None:                                          # rectángulo claro grande y lleno = hoja
                    ys_, xs_ = np.nonzero(m)
                    (_, _), (rw_, rh_), _ = cv2.minAreaRect(np.stack([xs_, ys_], 1).astype(np.float32))
                    fill_ = area / max(rw_ * rh_, 1); bright_ = float(np.median(hsv[..., 2][m]))
                    if fill_ > 0.85 and bright_ > 170 and min(rw_, rh_) > 0.08 * small.shape[1]:
                        label = "hoja"
                if label is None:
                    label = "mesa" if area > 0.12 * total else "objeto"
                items.append({"label": label, "mask_small": m, "score": float(p["score"]), "stability": float(p["stability"]), "area_frac": area / total})
            for hid, hm in hand_masks:                                     # las manos entran siempre, con su malla
                if not any(it["label"] == "mano" and float((it["mask_small"] & hm).sum()) / max(1, hm.sum()) > 0.5 for it in items):
                    items.append({"label": "mano", "mask_small": hm, "score": 1.0, "stability": 1.0, "area_frac": float(hm.sum()) / total})
            # guardar cada ítem a resolución del lienzo
            lh_, lw_ = lz.shape[:2]
            catalog = []
            for i, it in enumerate(sorted(items, key=lambda x: (z_index(x["label"], x["area_frac"]), -x["area_frac"]))):
                mfull = cv2.resize(it["mask_small"].astype(np.uint8) * 255, (lw_, lh_), interpolation=cv2.INTER_NEAREST)
                mfull = cv2.morphologyEx(mfull, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
                ys, xs = np.nonzero(mfull)
                if len(xs) < 50:
                    continue
                x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
                z = z_index(it["label"], it["area_frac"])
                name = f"{i:02d}_{it['label']}"
                cv2.imwrite(str(out_dir / f"{name}_mask.png"), mfull)
                rgba = np.dstack([lz[y0:y1, x0:x1], mfull[y0:y1, x0:x1]])
                cv2.imwrite(str(out_dir / f"{name}_recorte.png"), rgba)
                paths = ca.path_svg(ca.contornos_suaves(mfull))        # contorno suavizado en vector (sin el dentado de SAM)
                svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {lw_} {lh_}" width="{lw_}" height="{lh_}" data-label="{it["label"]}" data-z="{z}">'
                       f'<path d="{paths}" fill="none" stroke="#ffffff" stroke-width="2"/></svg>')
                (out_dir / f"{name}.svg").write_text(svg)
                catalog.append({"id": name, "label": it["label"], "z": z, "bbox": [x0, y0, x1, y1], "area_px": int(len(xs)), "area_mm2": round(len(xs) * float(sup.get("mm_por_px") or 0) ** 2),
                                "score": round(it["score"], 3), "paths_svg": len(paths), "mask": f"{name}_mask.png", "recorte": f"{name}_recorte.png", "svg": f"{name}.svg"})
            # SVG compuesto con todas las capas en orden z
            comp = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {lw_} {lh_}" width="{lw_}" height="{lh_}">']
            for c in catalog:
                comp.append(f'<g data-id="{c["id"]}" data-label="{c["label"]}" data-z="{c["z"]}">' + (out_dir / c["svg"]).read_text().split(">", 1)[1].rsplit("</svg>", 1)[0] + "</g>")
            comp.append("</svg>")
            (out_dir / "composicion.svg").write_text("".join(comp))
            # vista rápida: recortes coloreados por z
            prev_img = lz.copy()
            for c in catalog:
                m = cv2.imread(str(out_dir / c["mask"]), cv2.IMREAD_GRAYSCALE) > 127
                col = SEG_COLORS.get(c["label"], (255, 255, 255))
                prev_img[m] = (prev_img[m] * 0.45 + np.array(col) * 0.55).astype(np.uint8)
                x0, y0 = c["bbox"][:2]
                cv2.putText(prev_img, f'{c["label"]} z{c["z"]}', (x0 + 4, y0 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
                cv2.putText(prev_img, f'{c["label"]} z{c["z"]}', (x0 + 4, y0 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.imwrite(str(out_dir / "vista.jpg"), prev_img)
            result = {"ok": True, "dir": str(out_dir), "items": catalog, "sam_s": round(t_sam, 1), "sam": {k: v for k, v in info.items() if k != "proposals"},
                      "auto": bool(auto)}
            (out_dir / "catalog.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
            if con_agente and not state.get("catalogo_dir") and state.get("ultima_base"):
                state["catalogo_dir"] = state["ultima_base"]["dir"]       # la capa sigue en la anterior hasta el refinado
            state["ultima_base"] = result
            if con_agente:
                th_ = threading.Thread(target=refinar_con_agente, args=(out_dir, lz), daemon=True)
                state["agente_thread"] = th_; th_.start()
                result["agente"] = "refinando en segundo plano (catalog_refinado.json); la capa catálogo cambia cuando esté"
            else:
                state["catalogo_dir"] = str(out_dir)
            return result
        finally:
            restaurar(); base_lock.release()

    def calibrar_lazo(cols: int = 12, rows: int = 7) -> dict:
        """Lazo cerrado lienzo → proyector → mesa → Osmo → lienzo: proyecta puntos en coordenadas del lienzo, mide dónde
        vuelven y construye una corrección densa (remap en el espacio del proyector) que anula el residuo. Guarda
        lazo_corr.npz y lazo.json; la composición aplica el remap antes de enviar cada cuadro. Con el Quest, la corrección
        fina es la de calib_quest_proyector.py lazo (cuadro completo); esta, con el stream de 864, la refina encima."""
        prev = dict(state["capas"]); prev_luz, prev_auto, prev_rojo = state.get("luz"), state.get("autoluz", True), state.get("rojo")
        try:
            state["rojo"] = False                                       # el lazo de composición no manda overlays mientras tanto
            time.sleep(0.4)
            mx = int(0.06 * lw); my = int(0.08 * lh)
            pts_l = np.array([[mx + i * (lw - 2 * mx) / (cols - 1), my + j * (lh - 2 * my) / (rows - 1)] for j in range(rows) for i in range(cols)], np.float64)
            dots = np.zeros((lh, lw, 3), np.uint8)
            for x, y in pts_l:
                cv2.circle(dots, (int(round(x)), int(round(y))), 9, (255, 255, 255), -1, cv2.LINE_AA)
            proj_dots = cv2.warpPerspective(dots, paper.Hlp, (W, H), flags=cv2.INTER_LINEAR)
            if state.get("corr") is not None:                              # medir sobre la corrección vigente (iterativo)
                proj_dots = cv2.remap(proj_dots, state["corr"][0], state["corr"][1], cv2.INTER_LINEAR)
            pb, pd = scan / "lazo_negro.png", scan / "lazo_puntos.png"
            cv2.imwrite(str(pb), np.zeros((H, W, 3), np.uint8)); cv2.imwrite(str(pd), proj_dots)
            mostrar(np.zeros((H, W, 3), np.uint8), "lazo_negro"); time.sleep(1.4 if a.salida else 2.4)
            f0 = vis["lienzo"].copy()
            mostrar(proj_dots, "lazo_puntos"); time.sleep(1.4 if a.salida else 2.4)
            f1 = vis["lienzo"].copy()
            g0 = cv2.cvtColor(f0, cv2.COLOR_BGR2GRAY).astype(np.float32); g1 = cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY).astype(np.float32)
            d = cv2.GaussianBlur(g1 - g0, (0, 0), 1.5)
            thr = max(20.0, 0.4 * float(np.percentile(d, 99.7)))
            m = (d > thr).astype(np.uint8)
            n, lab, st, cen = cv2.connectedComponentsWithStats(m)
            obs = []
            for i in range(1, n):
                if 20 <= st[i, cv2.CC_STAT_AREA] <= 2500:
                    obs.append(cen[i])
            obs = np.array(obs, np.float64) if obs else np.zeros((0, 2))
            pares = []
            for p_ in pts_l:                                              # cada punto previsto → el observado más cercano (< 45 px)
                if len(obs) == 0:
                    break
                dd = np.linalg.norm(obs - p_, axis=1); k = int(np.argmin(dd))
                if dd[k] < 45:
                    pares.append((p_, obs[k]))
            if len(pares) < 12:
                raise RuntimeError(f"solo {len(pares)} puntos emparejados de {len(pts_l)} (observados {len(obs)}): ¿la Osmo ve el área?")
            P_int = np.array([p for p, _ in pares]); P_obs = np.array([o for _, o in pares])
            res = P_obs - P_int
            # campo de corrección en el espacio del proyector: el contenido previsto en L_int tiene que dibujarse donde
            # haga falta para aterrizar en L_int. Observado: lo dibujado en L_int aterriza en L_obs. Pre-distorsión:
            # dibujar lo de L_int en L_int - (L_obs - L_int). En proj: desplazamiento d_p = H(L_int - res) - H(L_int).
            Pp_int = cv2.perspectiveTransform(P_int.reshape(-1, 1, 2), paper.Hlp).reshape(-1, 2)
            Pp_pre = cv2.perspectiveTransform((P_int - res).reshape(-1, 1, 2), paper.Hlp).reshape(-1, 2)
            # remap: final(x, y) = img(mapx(x, y), mapy(x, y)). El contenido previsto para Pp_int debe DIBUJARSE en Pp_pre
            # (así aterriza en L_int): final(Pp_pre) = img(Pp_int) → en Pp_pre, map = Pp_int → disp = Pp_int - Pp_pre.
            disp = Pp_int - Pp_pre
            from scipy.interpolate import griddata
            gx, gy = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
            dx = griddata(Pp_pre, disp[:, 0], (gx, gy), method="linear"); dy = griddata(Pp_pre, disp[:, 1], (gx, gy), method="linear")
            dxn = griddata(Pp_pre, disp[:, 0], (gx, gy), method="nearest"); dyn = griddata(Pp_pre, disp[:, 1], (gx, gy), method="nearest")
            dx = np.where(np.isnan(dx), dxn, dx).astype(np.float32); dy = np.where(np.isnan(dy), dyn, dy).astype(np.float32)
            dx = cv2.GaussianBlur(dx, (0, 0), 25); dy = cv2.GaussianBlur(dy, (0, 0), 25)
            if state.get("corr") is not None:                              # componer con la corrección anterior
                pmx, pmy = state["corr"]
                mapx = cv2.remap(pmx, (gx + dx), (gy + dy), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                mapy = cv2.remap(pmy, (gx + dx), (gy + dy), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            else:
                mapx, mapy = gx + dx, gy + dy
            state["corr"] = (mapx.astype(np.float32), mapy.astype(np.float32))
            np.savez_compressed(scan / "lazo_corr.npz", mapx=state["corr"][0], mapy=state["corr"][1])
            info = {"ok": True, "puntos": len(pares), "de": len(pts_l), "residuo_px_lienzo": {"mediana": round(float(np.median(np.linalg.norm(res, axis=1))), 2),
                    "p90": round(float(np.percentile(np.linalg.norm(res, axis=1), 90)), 2), "max": round(float(np.linalg.norm(res, axis=1).max()), 2)},
                    "residuo_mm": round(float(np.median(np.linalg.norm(res, axis=1))) * float(sup.get("mm_por_px") or 0), 2),
                    "desplazamiento_proj_px": {"mediana": round(float(np.median(np.linalg.norm(disp, axis=1))), 2)}, "t": time.strftime("%Y-%m-%d %H:%M:%S")}
            (scan / "lazo.json").write_text(json.dumps({**info, "pares": [[p.tolist(), o.tolist()] for p, o in pares]}, indent=1))
            dbg = f1.copy()
            for p_, o_ in pares:
                cv2.circle(dbg, (int(p_[0]), int(p_[1])), 6, (0, 255, 0), 1); cv2.line(dbg, (int(p_[0]), int(p_[1])), (int(o_[0]), int(o_[1])), (0, 0, 255), 2)
            cv2.imwrite(str(scan / "lazo_debug.jpg"), dbg)
            state["lazo_info"] = info
            return info
        finally:
            try:
                mostrar(None)
            except Exception:  # noqa: BLE001
                pass
            state["capas"].update(prev); state["luz"] = prev_luz; state["autoluz"] = prev_auto; state["rojo"] = prev_rojo

    try:                                                                # corrección de lazo guardada
        if quest:                                                       # la de la calibración del Quest (contenido 1900x1040)
            state["corr"] = None if a.sin_lazo else LQ.get("lazo")
            state["corr_fuente"] = None if a.sin_lazo else f"{a.calib}/lazo_corr.npz"
        if (scan / "lazo_corr.npz").exists() and not a.sin_lazo:       # la refinada por /calibrar/lazo del feed (compuesta)
            with np.load(scan / "lazo_corr.npz") as z_:
                mx_, my_ = z_["mapx"].astype(np.float32), z_["mapy"].astype(np.float32)
            if mx_.shape == (H, W):
                state["corr"] = (mx_, my_); state["corr_fuente"] = str(scan / "lazo_corr.npz")
    except Exception:  # noqa: BLE001
        state["corr"] = None

    agente_st = {"modo": a.agente}                                     # agente del refinado: sonnet | jev | ambos (/agente?modo=)

    def _catalogo_desde(out_dir: Path, lz: np.ndarray, small: np.ndarray, objs: list[dict], pref: str) -> list[dict]:
        """SAM con prompts de caja (px de `small`) por objeto → máscara, recorte y SVG a resolución del lienzo, en orden z."""
        lh_, lw_ = lz.shape[:2]
        with seg["lock"]:
            seg["model"].set_image(small)
            masks = []
            for o in objs:
                m, info = seg["model"].predict([float(v) for v in o["caja"]])
                masks.append((o, m, info))
        catalog = []
        total = small.shape[0] * small.shape[1]
        for i, (o, m, info) in enumerate(sorted(masks, key=lambda t: (z_index(t[0]["nombre"], float(t[1].sum()) / total), -float(t[1].sum())))):
            mfull = cv2.resize(m.astype(np.uint8) * 255, (lw_, lh_), interpolation=cv2.INTER_NEAREST)
            ys, xs = np.nonzero(mfull)
            if len(xs) < 50:
                continue
            x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
            label = o["nombre"].replace(" ", "_")[:30]; z = z_index(o["nombre"], len(xs) / (lw_ * lh_))
            name = f"{pref}{i:02d}_{label}"
            cv2.imwrite(str(out_dir / f"{name}_mask.png"), mfull)
            cv2.imwrite(str(out_dir / f"{name}_recorte.png"), np.dstack([lz[y0:y1, x0:x1], mfull[y0:y1, x0:x1]]))
            paths = ca.path_svg(ca.contornos_suaves(mfull))        # contorno suavizado en vector
            (out_dir / f"{name}.svg").write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {lw_} {lh_}" data-label="{o["nombre"]}" data-z="{z}"><path d="{paths}" fill="none" stroke="#fff" stroke-width="2"/></svg>')
            catalog.append({"id": name, "label": o["nombre"], "z": z, "bbox": [x0, y0, x1, y1], "caja_agente": o["caja"], "confianza_agente": o.get("confianza"),
                            "sam_score": round(float(info["score"]), 3), "area_px": int(len(xs)), "mask": f"{name}_mask.png", "recorte": f"{name}_recorte.png", "svg": f"{name}.svg"})
        return catalog

    def _refinar_jev(out_dir: Path, lz: np.ndarray, small_path: Path, small: np.ndarray, archivo: str) -> dict:
        """Agente con Jev (playground/vision_jev.py: cajas nativas de Jev 1.14 sobre el lienzo + regiones del SAM base
        nombradas por recorte, ~2 s) → agente_jev.json y `archivo` con el mismo formato que el refinado de Sonnet."""
        from . import vision_jev as vj
        d = vj.describir(small_path, size=(small.shape[1], small.shape[0]), catalogo_dir=out_dir)
        (out_dir / "agente_jev.json").write_text(json.dumps(d, indent=1, ensure_ascii=False))
        if d.get("error") and not d.get("objetos"):
            raise RuntimeError(f"jev: {d['error']}")
        objs = d.get("objetos") or []
        catalog = _catalogo_desde(out_dir, lz, small, objs, "jv") if objs else []
        resumen = {k: v for k, v in (d.get("jev") or {}).items() if k not in ("crudas", "detalle")}
        if catalog:
            (out_dir / archivo).write_text(json.dumps({"ok": True, "agente": "jev", "agente_s": d.get("segundos"), "jev": resumen, "items": catalog},
                                                      indent=1, ensure_ascii=False))
        state["ultima_refinada_jev"] = {"dir": str(out_dir), "archivo": archivo, "n": len(catalog), "agente_s": d.get("segundos"),
                                        "costo_usd": resumen.get("costo_gateway_usd") or resumen.get("costo_usd")}
        return {"n": len(catalog), "segundos": d.get("segundos")}

    def refinar_con_agente(out_dir: Path, lz: np.ndarray) -> None:
        """El agente de visión mira el lienzo limpio y devuelve objetos con cajas; SAM los segmenta con prompts de caja
        (recortes afinados) y se guardan como catálogo refinado con z-index. El agente sale de /agente?modo= (--agente):
        sonnet (Claude CLI, ~20 s), jev (Jev 1.14 con imagen por el AI Gateway, ~2 s) o ambos (manda Sonnet y Jev deja
        catalog_refinado_jev.json al lado, para comparar)."""
        from . import vision_vlm as vv
        modo = agente_st["modo"]
        try:
            small_path = out_dir / "lienzo_960.jpg"
            sc = 960.0 / lz.shape[1]
            small = cv2.resize(lz, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(small_path), small)
            if modo == "jev":
                r_ = _refinar_jev(out_dir, lz, small_path, small, "catalog_refinado.json")
                if not r_["n"]:
                    return
                state["ultima_refinada"] = {"dir": str(out_dir), "n": r_["n"], "agente_s": r_["segundos"], "agente": "jev"}
                jev_lanzar(out_dir)                            # Jev por las etiquetas nuevas, en su hilo (escribe jev.json)
                return
            if modo == "ambos":
                def _jev_al_lado():
                    try:
                        _refinar_jev(out_dir, lz, small_path, small, "catalog_refinado_jev.json")
                    except Exception as e:  # noqa: BLE001
                        (out_dir / "agente_jev_error.txt").write_text(str(e))
                        state["ultima_refinada_jev"] = {"dir": str(out_dir), "error": str(e)[:200]}
                threading.Thread(target=_jev_al_lado, daemon=True).start()
            d = vv.describir(small_path, "sonnet", 150.0, size=(small.shape[1], small.shape[0]))
            (out_dir / "agente.json").write_text(json.dumps(d, indent=1, ensure_ascii=False))
            objs = d.get("objetos") or []
            if not objs:
                return
            catalog = _catalogo_desde(out_dir, lz, small, objs, "ag")
            (out_dir / "catalog_refinado.json").write_text(json.dumps({"ok": True, "agente": "sonnet", "agente_s": d.get("segundos"), "items": catalog},
                                                                      indent=1, ensure_ascii=False))
            state["ultima_refinada"] = {"dir": str(out_dir), "n": len(catalog), "agente_s": d.get("segundos"), "agente": "sonnet"}
            jev_lanzar(out_dir)                                # Jev por las etiquetas nuevas, en su hilo (escribe jev.json)
        except Exception as e:  # noqa: BLE001
            (out_dir / "agente_error.txt").write_text(str(e))
            state["ultima_refinada"] = {"dir": str(out_dir), "error": str(e)[:200]}
        finally:
            # la capa catálogo pasa a esta carpeta (refinado si salió; si no, el catálogo de SAM), salvo que ya haya otra
            # segmentación más nueva
            if (state.get("ultima_base") or {}).get("dir") == str(out_dir):
                state["catalogo_dir"] = str(out_dir)

    # ---- re-segmentación base automática: cada `cada` s (de inicio a inicio), nunca dos a la vez ni mientras el agente
    # de la anterior sigue trabajando; espera hasta 10 s a que no haya manos en el área y si no, corre igual
    rebase = {"on": a.rebase_cada > 0, "cada": float(a.rebase_cada) if a.rebase_cada > 0 else 45.0, "agente": bool(a.rebase_agente),
              "grid": tuple(max(2, min(48, int(v))) for v in a.rebase_grid.lower().split("x")),
              "n": 0, "t_prox": time.time() + (float(a.rebase_cada) if a.rebase_cada > 0 else 45.0), "fase": "espera",
              "ultimo": None, "err": None, "espera_manos_s": None, "con_manos": None}

    def rebase_loop():
        while True:
            time.sleep(0.5)
            if not rebase["on"]:
                rebase["fase"] = "apagado"; continue
            if seg.get("model") is None or vis["lienzo"] is None:
                rebase["fase"] = "esperando SAM"; continue
            th_ = state.get("agente_thread")
            if th_ is not None and th_.is_alive():
                rebase["fase"] = "esperando al agente"; continue
            if time.time() < rebase["t_prox"] or base_lock.locked():
                rebase["fase"] = "espera"; continue
            rebase["fase"] = "esperando que no haya manos"; t_w = time.time()
            while time.time() - t_w < 10.0 and vis["hands"] is not None and vis["hands"].hands:
                time.sleep(0.25)
            rebase["espera_manos_s"] = round(time.time() - t_w, 1)
            rebase["con_manos"] = bool(vis["hands"] is not None and vis["hands"].hands)
            rebase["fase"] = "segmentando"; t0_ = time.time()
            try:
                r_ = segmentacion_base(con_agente=rebase["agente"], auto=True, grid=rebase["grid"])
                rebase["ultimo"] = {"dir": r_["dir"], "items": len(r_["items"]), "sam_s": r_["sam_s"], "s": round(time.time() - t0_, 1),
                                    "t": time.strftime("%H:%M:%S"), "agente": rebase["agente"]}
                rebase["n"] += 1; rebase["err"] = None
            except Exception as e:  # noqa: BLE001
                rebase["err"] = f"{time.strftime('%H:%M:%S')} {str(e)[:160]}"
            finally:
                rebase["t_prox"] = max(t0_ + rebase["cada"], time.time() + 5.0)
                rebase["fase"] = "espera"

    def rebase_status() -> dict:
        th_ = state.get("agente_thread")
        return {"on": rebase["on"], "cada": rebase["cada"], "agente": rebase["agente"], "grid": "%dx%d" % rebase["grid"], "fase": rebase["fase"], "n": rebase["n"],
                "busy": base_lock.locked() or bool(th_ is not None and th_.is_alive()),
                "proxima_en_s": round(max(0.0, rebase["t_prox"] - time.time()), 1) if rebase["on"] else None,
                "espera_manos_s": rebase["espera_manos_s"], "con_manos": rebase["con_manos"], "ultimo": rebase["ultimo"], "error": rebase["err"],
                "catalogo_dir": state.get("catalogo_dir") or (state.get("ultima_base") or {}).get("dir")}
    threading.Thread(target=rebase_loop, daemon=True).start()

    # ---- segunda cámara lateral (iPad / Iriun): altura del dedo sobre la mesa
    cam2 = {"reader": None, "H": None, "info": {}, "hands": [], "err": None, "busy": False, "hz": 0.0, "index": cam2_index}
    c2path = scan / "cam2_calib.json"
    if c2path.exists():
        try:
            d2 = json.loads(c2path.read_text()); cam2["H"] = np.array(d2["H_cam2_to_lienzo"], np.float64); cam2["info"] = d2
        except Exception:  # noqa: BLE001
            pass

    def abrir_cam2():
        spec = cam2.get("index")
        if spec is None:
            raise RuntimeError("cámara 2 no resuelta al arrancar (Iriun 1920x1080 @ 15 fps): abre la app en el iPad y se toma en el próximo relanzo")
        return Camera(int(spec), size=a.cam2_size, rotate=0, downscale=1, name="cam2")

    def cam2_loop():
        from . import vision_mesa as vm
        import mediapipe as mp
        from mediapipe.tasks import python as mpt
        from mediapipe.tasks.python import vision as mpv
        while cam2["reader"] is None:
            try:
                cam2["reader"] = CamReader(abrir_cam2())
            except Exception as e:  # noqa: BLE001
                cam2["err"] = str(e)[:120]; time.sleep(5)
        det = mpv.HandLandmarker.create_from_options(mpv.HandLandmarkerOptions(
            base_options=mpt.BaseOptions(model_asset_path=str(vm.MODELS / "hand_landmarker.task")), running_mode=mpv.RunningMode.VIDEO, num_hands=2))
        last = 0; last_ts = 0; n, t0 = 0, time.time()
        while True:
            f, t, last = cam2["reader"].wait_new(last, 1.0)
            if f is None:
                continue
            small = cv2.resize(f, (960, 540), interpolation=cv2.INTER_AREA)
            ts = max(last_ts + 1, int(t * 1000)); last_ts = ts
            try:
                r = det.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(small, cv2.COLOR_BGR2RGB)), ts)
                hs = []
                for i, lm in enumerate(r.hand_landmarks):
                    pts = np.array([[p.x * f.shape[1], p.y * f.shape[0]] for p in lm])
                    hs.append({"handed": r.handedness[i][0].category_name if r.handedness else "?", "tip": pts[8].round(1).tolist(),
                               "wrist": pts[0].round(1).tolist(), "t": t})
                cam2["hands"] = hs; cam2["err"] = None
            except Exception as e:  # noqa: BLE001
                cam2["err"] = str(e)[:120]
            n += 1
            if time.time() - t0 >= 2:
                cam2["hz"], n, t0 = n / (time.time() - t0), 0, time.time()

    if a.cam2:
        threading.Thread(target=cam2_loop, daemon=True).start()

    def cam2_calibrar() -> dict:
        """Negro / rectángulo blanco → cuadrilátero en la cámara 2 → H cam2→lienzo (puntos sobre la mesa)."""
        from .superficie_captura import quad_from_pair
        if cam2["reader"] is None:
            raise RuntimeError("cámara 2 no abierta")
        corners = np.array(sup["rect_proj_tl_tr_br_bl"], np.float64)
        white = np.zeros((H, W, 3), np.uint8); cv2.fillPoly(white, [corners.astype(np.int32).reshape(-1, 1, 2)], (255, 255, 255))
        pw, pb = scan / "superficie_blanco.png", scan / "superficie_negro.png"
        cv2.imwrite(str(pw), white); cv2.imwrite(str(pb), np.zeros((H, W, 3), np.uint8))
        rojo_antes = state.get("rojo", False); state["rojo"] = False
        rd = cam2["reader"]
        try:
            mostrar(np.zeros((H, W, 3), np.uint8), "cam2_negro"); time.sleep(2.2)
            f0, _, s0 = rd.wait_new(rd.seq, 2.0); f0, _, s0 = rd.wait_new(s0, 2.0)
            mostrar(white, "cam2_blanco"); time.sleep(2.2)
            f1, _, s1 = rd.wait_new(rd.seq, 2.0); f1, _, s1 = rd.wait_new(s1, 2.0)
            quad, thr, area = quad_from_pair(f0, f1)
            # desde el costado el orden TL/TR/BR/BL de la cámara no es el del lienzo: se prueban las 4 rotaciones y se
            # elige la que deja la mesa 'derecha' (el lado largo del rectángulo es el ancho del lienzo)
            dst = np.array([[0, 0], [lw, 0], [lw, lh], [0, lh]], np.float32)
            best = None
            for k in range(4):
                q = np.roll(quad, -k, axis=0).astype(np.float32)
                Hk = cv2.getPerspectiveTransform(q, dst)
                top = np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3]); side = np.linalg.norm(q[2] - q[1]) + np.linalg.norm(q[3] - q[0])
                score = top / max(side, 1e-6)
                if best is None or score > best[0]:
                    best = (score, Hk, q)
            Hk, q = best[1].astype(np.float64), best[2]
            info = {"H_cam2_to_lienzo": Hk.tolist(), "cam2_quad": q.round(1).tolist(), "camera_size": [f1.shape[1], f1.shape[0]],
                    "umbral": round(thr, 1), "area_px": area, "calibrado": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "nota": "homografía solo para puntos sobre la mesa; un dedo levantado cae desplazado: esa es la señal de altura"}
            c2path.write_text(json.dumps(info, indent=1)); cam2["H"] = Hk; cam2["info"] = info
            cv2.imwrite(str(scan / "cam2_blanco.png"), f1)
            return {"ok": True, **{k: v for k, v in info.items() if k != "H_cam2_to_lienzo"}}
        finally:
            try:
                mostrar(None)
            except Exception:  # noqa: BLE001
                pass
            state["rojo"] = rojo_antes

    def altura_dedos(hs: list[dict]) -> None:
        """Para cada mano de la Osmo: su punta (lienzo) → dónde caería en cam2 si tocara la mesa → desplazamiento
        contra la punta que cam2 ve de verdad → altura (px de cam2, ~proporcional a mm) y 'apoyado'."""
        if cam2["H"] is None or not cam2["hands"]:
            return
        Hinv = np.linalg.inv(cam2["H"])
        for h in hs:
            tip_l = np.array(h["tips"]["indice"], np.float64)
            exp = cv2.perspectiveTransform(tip_l.reshape(1, 1, 2), Hinv).reshape(2)
            best = None
            for c in cam2["hands"]:
                if time.time() - c["t"] > 0.5:
                    continue
                d = abs(c["tip"][0] - exp[0])
                if d < 160 and (best is None or d < best[0]):
                    best = (d, c)
            if best is None:
                h["altura_px"] = None; continue
            dy = float(exp[1] - best[1]["tip"][1])                      # positivo = la punta real está más arriba que la mesa
            h["altura_px"] = round(dy, 1)
            h["apoyado"] = dy < 12
            h["touching"] = bool(h["apoyado"]); h["trigger"] = "cam2" if h["apoyado"] else h.get("trigger", "")

    def loop():
        last = seq; n, t0 = 0, time.time(); period = 1.0 / max(a.feed_fps, 1)
        while True:
            fr, t, last = reader.wait_new(last, 1.0)
            if fr is None:
                continue
            if time.time() - t0 < n * period:          # limitar el feed a feed_fps
                continue
            tp = time.perf_counter(); tp0 = tp
            if quest and calib.get("full", (None,))[0] is not None and calib.get("remap_w") != fr.shape[1]:
                # el stream cambió de ancho (p. ej. 864 → 1728): remap y H para ese ancho
                calib["remap"] = lq.mapa_para(calib["full"][0], calib["full"][1], fr.shape[1]); calib["remap_w"] = fr.shape[1]
                calib["H"] = calib["H_full"] @ np.diag([lq.LADO_CAM / fr.shape[1], lq.LADO_CAM / fr.shape[1], 1.0])
            if calib.get("remap") is not None and fr.shape[1] == calib.get("remap_w"):
                lienzo = cv2.remap(fr, calib["remap"][0], calib["remap"][1], cv2.INTER_LINEAR)
            else:
                lienzo = cv2.warpPerspective(fr, calib["H"], (lw, lh), flags=cv2.INTER_LINEAR)
            if state["n"] % 30 == 0:                                    # ¿cámara en luma? apaga lo que depende de color (piel)
                hsv_ = cv2.cvtColor(np.ascontiguousarray(fr[::8, ::8]), cv2.COLOR_BGR2HSV)
                sat_ = hsv_[..., 1][hsv_[..., 2] > 50]
                vis["gris"] = bool(sat_.size < 50 or float(np.percentile(sat_, 95)) < 30)
            state["fr"] = fr
            with vis["cond"]:
                vis["lienzo"] = lienzo; vis["seq"] += 1; vis["cond"].notify_all()
            tp = perf.lap("feed", "warp", tp)
            # se codifica solo lo que alguien mira (o cada REFRESCO_S para /frame.jpg, /raw.jpg, /papel.jpg)
            need_jpg = hace_falta("jpg", "t_enc_jpg"); need_limpio = hace_falta("limpio", "t_enc_limpio")
            need_papel = hace_falta("papel", "t_enc_papel")
            mon_due = mon is not None and time.time() - state["t_mon"] >= a.monitor_every
            need_raw = hace_falta("raw", "t_enc_raw") or mon_due
            shown = lienzo
            if need_jpg and (vis["hands"] is not None or vis["objs"]):
                shown = lienzo.copy()
                try:
                    from . import vision_mesa as vm
                    if vis.get("grid"):
                        from . import teclado_grid as tg
                        tg.draw(shown, vis["grid"], highlight=set(vis["info"].get("teclas_bajo_dedos", [])))
                    mz = (vis.get("mouse") or {}).get("zonas")
                    if mz:
                        for k_, poly in mz.items():
                            cv2.polylines(shown, [np.array(poly, np.int32).reshape(-1, 1, 2)], True,
                                          (255, 0, 255) if k_ == "outline" else (0, 255, 255), 2, cv2.LINE_AA)
                    for g_ in vis.get("grinders") or []:
                        cv2.circle(shown, (int(g_["cx"]), int(g_["cy"])), int(g_["r"]), (0, 200, 255), 2, cv2.LINE_AA)
                        cv2.putText(shown, f"moledor {g_['d_mm']:.0f}mm", (int(g_["cx"] - g_["r"]), int(g_["cy"] - g_["r"] - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1, cv2.LINE_AA)
                    if vis["hands"] is not None:
                        vis["hands"].draw(shown)
                    vm.draw_objects(shown, vis["info"].get("objetos", []))
                except Exception:  # noqa: BLE001
                    pass
            # ajustes de video del feed (solo para lo que se muestra; la visión trabaja con el cuadro crudo)
            vj = state.get("video") or {}
            if abs(vj.get("brillo", 0.0)) > 0.5 or any(abs(vj.get(k, 1.0) - 1.0) > 1e-3 for k in ("sat", "contraste", "gamma", "lum")):
                def _ajusta(im_):
                    out_ = im_
                    s_ = float(vj.get("sat", 1.0))
                    if abs(s_ - 1.0) > 1e-3:
                        hsv_ = cv2.cvtColor(out_, cv2.COLOR_BGR2HSV)
                        hsv_[..., 1] = np.clip(hsv_[..., 1].astype(np.float32) * s_, 0, 255).astype(np.uint8)
                        out_ = cv2.cvtColor(hsv_, cv2.COLOR_HSV2BGR)
                    c_, b_, l_ = float(vj.get("contraste", 1.0)), float(vj.get("brillo", 0.0)), float(vj.get("lum", 1.0))
                    if abs(c_ - 1.0) > 1e-3 or abs(b_) > 0.5 or abs(l_ - 1.0) > 1e-3:
                        # contraste alrededor del gris medio, luminancia como ganancia global, brillo como desplazamiento
                        out_ = cv2.convertScaleAbs(out_, alpha=c_ * l_, beta=b_ + 128.0 * (1.0 - c_) * l_)
                    g_ = float(vj.get("gamma", 1.0))
                    if abs(g_ - 1.0) > 1e-3:
                        lut = (np.clip((np.arange(256) / 255.0) ** (1.0 / g_), 0, 1) * 255).astype(np.uint8)
                        out_ = cv2.LUT(out_, lut)
                    return out_
                lienzo_v = _ajusta(lienzo) if need_limpio else lienzo
                shown = (_ajusta(shown) if shown is not lienzo else lienzo_v) if need_jpg else shown
                ajusta_fn = _ajusta                                     # el video de entrada (Osmo) se ajusta más abajo
            else:
                lienzo_v = lienzo; ajusta_fn = None
            tp = perf.lap("feed", "anotar", tp)
            ok = okl = ok2 = False
            if need_jpg:
                ok, buf = cv2.imencode(".jpg", shown, [cv2.IMWRITE_JPEG_QUALITY, a.quality])
            if need_limpio:
                okl, bufl = cv2.imencode(".jpg", lienzo_v, [cv2.IMWRITE_JPEG_QUALITY, a.quality]) if (shown is not lienzo_v or not ok) else (ok, buf)
            tp = perf.lap("feed", "jpeg", tp)
            if need_raw:
                sr_ = 960.0 / max(fr.shape[1], fr.shape[0])             # lado mayor a 960 sin aplastar (el Quest es cuadrado)
                small = cv2.resize(fr, (int(round(fr.shape[1] * sr_)), int(round(fr.shape[0] * sr_))), interpolation=cv2.INTER_AREA)
                if ajusta_fn is not None:
                    small = ajusta_fn(small)
                if mon_due:
                    state["t_mon"] = time.time()
                    try:
                        mon.step(small)
                    except Exception as e:  # noqa: BLE001
                        mon.razon = f"error {e}"
                    m = mon.ultimo
                    if m.get("centro"):
                        cv2.circle(small, (int(m["centro"][0]), int(m["centro"][1])), 12, (0, 255, 255), 2)
                ok2, buf2 = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
                tp = perf.lap("feed", "raw", tp)
            if time.time() - state["t_papel"] >= a.papel_every and not state.get("papel_busy"):
                state["t_papel"] = time.time(); state["papel_busy"] = True
                snap = lienzo.copy()

                def _buscar():                                  # la búsqueda de la hoja es lenta: fuera del lazo del feed
                    tq = time.perf_counter()
                    try:
                        paper.update(snap)
                    except Exception as e:  # noqa: BLE001
                        print("hoja:", e, flush=True)
                    finally:
                        state["papel_busy"] = False
                        perf.lap("vision", "hojas", tq)
                threading.Thread(target=_buscar, daemon=True).start()
            pj = None
            if need_papel:
                crop = paper.crop(lienzo)
                if crop is not None:
                    okp, bufp = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, a.quality])
                    pj = bufp.tobytes() if okp else None
                tp = perf.lap("feed", "papel", tp)
            with state["cond"]:
                if ok:
                    state["jpg"] = buf.tobytes()
                if okl:
                    state["limpio"] = bufl.tobytes()
                if ok2:
                    state["raw"] = buf2.tobytes()
                if pj is not None:
                    state["papel"] = pj
                state["n"] += 1
                state["cond"].notify_all()
            perf.lap("feed", "total", tp0); perf.vuelta("feed")
            n += 1
            if time.time() - t0 >= 2:
                state["fps"], n, t0 = n / (time.time() - t0), 0, time.time()

    threading.Thread(target=loop, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _mjpeg(self, key):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame"); self.send_header("Cache-Control", "no-store")
            self.end_headers()
            n = -1
            with clientes_lock:
                clientes[key] = clientes.get(key, 0) + 1
            try:
                while True:
                    with state["cond"]:
                        state["cond"].wait_for(lambda: state["n"] > n, 2.0)
                        if state["n"] <= n:
                            continue
                        n = state["n"]; data = state.get(key)
                    if data is None:
                        continue
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            finally:
                with clientes_lock:
                    clientes[key] = max(0, clientes.get(key, 0) - 1)

        def _json(self, obj, code=200):
            def _np(o):
                if isinstance(o, np.floating):
                    return float(o)
                if isinstance(o, np.integer):
                    return int(o)
                if isinstance(o, np.ndarray):
                    return o.tolist()
                return str(o)
            body = json.dumps(obj, ensure_ascii=False, default=_np).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

        def do_POST(self):
            """POST /papel/pintar  {"png": "<ruta de imagen en coords del feed de la hoja>", "seconds": 3600}
            → la proyecta sobre la hoja (overlay del runtime). {"off": true} la quita."""
            path = self.path.split("?")[0]
            n = int(self.headers.get("Content-Length") or 0)
            try:
                args = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._json({"ok": False, "error": "JSON inválido"}, 400)
            if path == "/objetos/seguir":                   # {"nombre": "vape", "box": [x, y, w, h]} en px del lienzo
                try:
                    from . import vision_mesa as vm
                    if vis["lienzo"] is None:
                        return self._json({"ok": False, "error": "sin lienzo todavía"}, 409)
                    vis["objs"] = [o for o in vis["objs"] if o.name != args["nombre"]]
                    vis["objs"].append(vm.ObjectTracker(args["nombre"], vis["lienzo"], tuple(int(v) for v in args["box"]), mmpx=float(sup.get("mm_por_px") or 0)))
                    return self._json({"ok": True, "objetos": [o.name for o in vis["objs"]]})
                except Exception as e:  # noqa: BLE001
                    return self._json({"ok": False, "error": str(e)}, 500)
            if path == "/objetos/borrar":
                vis["objs"] = [o for o in vis["objs"] if o.name != args.get("nombre")] if args.get("nombre") else []
                return self._json({"ok": True, "objetos": [o.name for o in vis["objs"]]})
            if path != "/papel/pintar":
                return self._json({"ok": False, "error": "ruta desconocida"}, 404)
            try:
                from . import pad_checker as pc
                if args.get("off"):
                    if a.salida:
                        state.pop("pintura", None); return self._json({"ok": True, "pintura": None})
                    pc.runtime_cmd(a.runtime, "overlay", off=True); return self._json({"ok": True, "overlay": None})
                if not paper.sheets:
                    return self._json({"ok": False, "error": "todavía no veo ninguna hoja"}, 409)
                img = cv2.imread(str(args["png"]), cv2.IMREAD_COLOR)
                if img is None:
                    return self._json({"ok": False, "error": f"no pude leer {args.get('png')}"}, 400)
                if a.salida:                                    # con la ventana: va dentro del cuadro que compone rojo_loop
                    state["pintura"] = {"img": img, "hasta": time.time() + float(args.get("seconds", 3600))}
                    return self._json({"ok": True, "pintura": str(args["png"]), "nota": "se compone sobre la hoja en cada cuadro (necesita /rojo?on=1)"})
                out = scan / "papel_overlay.png"
                cv2.imwrite(str(out), paper.to_projector(img))
                r = pc.runtime_cmd(a.runtime, "overlay", path=str(out.resolve()), seconds=float(args.get("seconds", 3600)))
                return self._json({"ok": True, "overlay": str(out), "runtime": r.get("result") or r})
            except Exception as e:  # noqa: BLE001
                return self._json({"ok": False, "error": str(e)}, 500)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/feed.mjpg":
                return self._mjpeg("jpg")
            if path == "/raw.mjpg":
                return self._mjpeg("raw")
            if path == "/papel.mjpg":
                return self._mjpeg("papel")
            if path == "/overlay.mjpg":
                return self._mjpeg("ovl")
            if path == "/seg.mjpg":
                return self._mjpeg("segjpg")
            if path == "/seg/base":                              # /seg/base?agente=1 → carpeta items/<fecha>/ con máscaras, recortes, SVG, z-index
                q = self.path.split("?", 1)[1] if "?" in self.path else ""
                if base_lock.locked():
                    return self._json({"ok": False, "error": "ya hay una segmentación base en curso", "rebase": rebase_status()}, 409)
                try:
                    return self._json(segmentacion_base(con_agente="agente=1" in q))
                except Exception as e:  # noqa: BLE001
                    return self._json({"ok": False, "error": str(e)}, 500)
            if path == "/rebase":                                # /rebase?on=1&cada=45&agente=1&grid=12x7&ahora=1
                q = self.path.split("?", 1)[1] if "?" in self.path else ""
                kv = dict(p_.split("=", 1) for p_ in q.split("&") if "=" in p_)
                try:
                    if "cada" in kv:
                        rebase["cada"] = max(10.0, float(kv["cada"]))
                        if rebase["on"]:
                            rebase["t_prox"] = min(rebase["t_prox"], time.time() + rebase["cada"])
                    if "agente" in kv:
                        rebase["agente"] = kv["agente"] == "1"
                    if "grid" in kv:
                        gx_, gy_ = kv["grid"].lower().split("x"); rebase["grid"] = (max(2, min(48, int(gx_))), max(2, min(32, int(gy_))))
                    if "on" in kv:
                        on_ = kv["on"] == "1"
                        if on_ and not rebase["on"]:
                            rebase["t_prox"] = time.time() + rebase["cada"]
                            seg["on"] = False                    # con rebase, el SAM periódico se apaga (/seg?on=1 lo prende)
                        rebase["on"] = on_
                    if kv.get("ahora") == "1":
                        rebase["t_prox"] = 0.0
                except ValueError:
                    pass
                return self._json({"rebase": rebase_status(), "seg_periodico": seg.get("on")})
            if path == "/calibrar/lazo":                      # /calibrar/lazo?reset=1 para empezar de cero
                q = self.path.split("?", 1)[1] if "?" in self.path else ""
                if "reset=1" in q:
                    state["corr"] = None if (a.sin_lazo or not quest) else LQ.get("lazo")
                    try:
                        (scan / "lazo_corr.npz").unlink()
                    except OSError:
                        pass
                try:
                    return self._json(calibrar_lazo())
                except Exception as e:  # noqa: BLE001
                    return self._json({"ok": False, "error": str(e)}, 500)
            if path == "/lazo":
                return self._json({"activo": state.get("corr") is not None, "info": state.get("lazo_info"), "error": state.get("lazo_err")})
            if path == "/agente":                                # /agente?modo=sonnet|jev|ambos (agente de visión del refinado)
                q = self.path.split("?", 1)[1] if "?" in self.path else ""
                kv = dict(p_.split("=", 1) for p_ in q.split("&") if "=" in p_)
                if "modo" in kv:
                    if kv["modo"] not in ("sonnet", "jev", "ambos"):
                        return self._json({"ok": False, "error": "modo: sonnet | jev | ambos"}, 400)
                    if kv["modo"] != "sonnet" and not jv.disponible():
                        return self._json({"ok": False, "error": "sin AI_GATEWAY_API_KEY (entorno o JEV_ENV_FILE)"}, 409)
                    agente_st["modo"] = kv["modo"]
                return self._json({"ok": True, "modo": agente_st["modo"], "jev_disponible": jv.disponible(),
                                   "ultima_refinada": state.get("ultima_refinada"), "ultima_refinada_jev": state.get("ultima_refinada_jev")})
            if path == "/jev.json":                              # lo último de Jev (por etiqueta) + llamadas, tokens, ms, US$
                return self._json(jev_status())
            if path == "/jev":                                   # /jev?on=0|1 (al prender, consulta el catálogo vigente si le faltan etiquetas)
                q = self.path.split("?", 1)[1] if "?" in self.path else ""
                kv = dict(p_.split("=", 1) for p_ in q.split("&") if "=" in p_)
                if "on" in kv:
                    if kv["on"] == "1" and not jv.disponible():
                        return self._json({"ok": False, "error": "sin AI_GATEWAY_API_KEY (entorno o JEV_ENV_FILE)", **jev_status()}, 409)
                    jev_st["on"] = kv["on"] == "1"
                    if jev_st["on"]:
                        jev_lanzar(catalogo_fuente())
                return self._json({"ok": True, **jev_status()})
            if path == "/seg/ultima":
                return self._json({"base": state.get("ultima_base"), "refinada": state.get("ultima_refinada"),
                                   "catalogo_dir": str(catalogo_fuente() or "")})
            if path == "/capas":                                 # /capas?teclado=0&moledor=0&mouse=1&hoja=1
                q = self.path.split("?", 1)[1] if "?" in self.path else ""
                for kv in q.split("&"):
                    if "=" in kv:
                        k_, v_ = kv.split("=", 1)
                        if k_ in state["capas"]:
                            state["capas"][k_] = v_ == "1"
                        elif k_ == "anim":                          # catálogo animado (patrón de emojis + texto alrededor) o estático
                            state["catalogo_anim"] = v_ == "1"
                        elif k_ == "brillo":                        # /capas?brillo=0.5 atenúa la capa catálogo (0-1)
                            try:
                                state["catalogo_brillo"] = max(0.05, min(1.0, float(v_)))
                            except ValueError:
                                pass
                        elif k_ == "hz":                            # /capas?hz=6 cuadros/s de la capa catálogo animada
                            try:
                                state["catalogo_hz"] = max(0.5, min(30.0, float(v_)))
                            except ValueError:
                                pass
                return self._json({"capas": state["capas"], "anim": state.get("catalogo_anim", True), "catalogo_ms": state.get("catalogo_ms"),
                                   "catalogo_err": state.get("catalogo_err"), "catalogo": catalogo_status()})
            if path == "/seg.json":
                sg = state.get("seg") or {}
                return self._json({"on": sg.get("on"), "cada": sg.get("cada"), "grid": sg.get("grid"), "ancho": sg.get("ancho"), "n": sg.get("n"), "ms": sg.get("ms"), "error": sg.get("err"), **(sg.get("info") or {})})
            if path == "/seg":                                   # /seg?on=1&cada=4&grid=16x9&ancho=960
                q = self.path.split("?", 1)[1] if "?" in self.path else ""
                kv = dict(p_.split("=", 1) for p_ in q.split("&") if "=" in p_)
                sg = state.get("seg")
                if sg is not None:
                    try:
                        if "on" in kv: sg["on"] = kv["on"] == "1"
                        if "cada" in kv: sg["cada"] = max(0.5, float(kv["cada"]))
                        if "grid" in kv: cx_, ry_ = kv["grid"].lower().split("x"); sg["grid"] = (max(2, min(48, int(cx_))), max(2, min(32, int(ry_))))
                        if "ancho" in kv: sg["ancho"] = max(320, min(1920, int(kv["ancho"])))
                    except ValueError:
                        pass
                return self._json({"on": sg.get("on") if sg else None, "cada": sg.get("cada") if sg else None, "grid": sg.get("grid") if sg else None, "ancho": sg.get("ancho") if sg else None, "error": sg.get("err") if sg else None})
            if path == "/limpio.mjpg":
                return self._mjpeg("limpio")
            if path == "/pg":
                body = PG_PAGE.encode()
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body); return
            if path == "/mon":
                body = MON_PAGE.encode()
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body); return
            if path == "/papel.json":
                return self._json(paper.info())
            if path == "/vd":                                # pantalla virtual: /vd?on=1&x=1387&y=200&w=493&h=641
                q = self.path.split("?", 1)[1] if "?" in self.path else ""
                kv = dict(p.split("=", 1) for p in q.split("&") if "=" in p)
                vd = state.setdefault("vd", {"on": False, "rect": [1920 - 493 - 40, 200, 493, 641]})
                try:
                    if all(k in kv for k in ("x", "y", "w", "h")):
                        vd["rect"] = [int(kv["x"]), int(kv["y"]), int(kv["w"]), int(kv["h"])]
                    if "on" in kv:
                        vd["on"] = kv["on"] == "1"
                except ValueError:
                    pass
                return self._json({"vd": vd, "error": state.get("vd_err"), "media_captura": state.get("vd_media")})
            if path == "/video":                              # /video?sat=0.6&brillo=-10&contraste=1.1&gamma=1.2
                q = self.path.split("?", 1)[1] if "?" in self.path else ""
                vj = state.setdefault("video", {"sat": 1.0, "brillo": 0.0, "contraste": 1.0, "gamma": 1.0, "lum": 1.0})
                for kv in q.split("&"):
                    if "=" in kv:
                        k_, v_ = kv.split("=", 1)
                        if k_ in vj:
                            try:
                                vj[k_] = float(v_)
                            except ValueError:
                                pass
                return self._json({"video": vj})
            if path == "/mouse.json":
                return self._json(vis.get("mouse") or {"ok": False})
            if path == "/teclado.json":
                return self._json({"grid": vis.get("grid"), "tracking": vis.get("kb_info")})
            if path == "/teclado/ref":                       # fija la referencia del tracker: lienzo actual + grilla actual
                try:
                    if vis["lienzo"] is None or not vis.get("grid"):
                        return self._json({"ok": False, "error": "sin lienzo o sin grilla"}, 409)
                    cv2.imwrite(str(ref_path), vis["lienzo"])
                    tpath.write_text(json.dumps({k_: v_ for k_, v_ in vis["grid"].items() if k_ != "tracking"}, indent=1))
                    r_ = recargar_teclado("archivo")
                    return self._json({"ok": True, "quad": r_["quad"]})
                except Exception as e:  # noqa: BLE001
                    return self._json({"ok": False, "error": str(e)}, 500)
            if path == "/teclado/recargar":                  # /teclado/recargar?ref=auto|archivo|base|actual|no
                q = self.path.split("?", 1)[1] if "?" in self.path else ""
                kv = dict(p_.split("=", 1) for p_ in q.split("&") if "=" in p_)
                try:
                    if not tpath.exists():
                        return self._json({"ok": False, "error": f"no existe {tpath}"}, 404)
                    return self._json(recargar_teclado(kv.get("ref", "auto")))
                except Exception as e:  # noqa: BLE001
                    return self._json({"ok": False, "error": str(e)}, 500)
            if path == "/vision.json":
                return self._json({"hz": round(vis["hz"], 1), "error": vis["err"], **vis["info"], "perf": perf.snapshot(),
                                   "catalogo": catalogo_status(), "rebase": rebase_status(),
                                   "cam2": {"activa": cam2["reader"] is not None, "hz": round(cam2["hz"], 1), "calibrada": cam2["H"] is not None,
                                            "manos": cam2["hands"], "error": cam2["err"]}})
            if path == "/cam2/calibrar":
                if cam2["busy"]:
                    return self._json({"ok": False, "error": "ya calibrando"}, 409)
                cam2["busy"] = True
                try:
                    return self._json(cam2_calibrar())
                except Exception as e:  # noqa: BLE001
                    return self._json({"ok": False, "error": str(e)}, 500)
                finally:
                    cam2["busy"] = False
            if path == "/cam2.jpg":
                rd = cam2["reader"]
                if rd is None or rd.frame is None:
                    self.send_error(503); return
                fr2 = rd.frame.copy()
                for c in cam2["hands"]:
                    cv2.circle(fr2, (int(c["tip"][0]), int(c["tip"][1])), 10, (0, 255, 0), 2)
                ok, buf = cv2.imencode(".jpg", cv2.resize(fr2, (960, 540)), [cv2.IMWRITE_JPEG_QUALITY, 80]); data = buf.tobytes()
                self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data); return
            if path == "/objetos/auto":
                try:
                    return self._json({"ok": True, "sembrados": objetos_desde_sam()})
                except Exception as e:  # noqa: BLE001
                    return self._json({"ok": False, "error": str(e)}, 500)
            if path == "/rojo":
                q = self.path.split("?", 1)[1] if "?" in self.path else ""
                for kv in q.split("&"):                                 # ajustes en vivo: feather=8&grow=3&inset=2
                    if "=" in kv:
                        k_, v_ = kv.split("=", 1)
                        try:
                            if k_ == "feather": paper.feather = int(v_)
                            elif k_ == "grow": paper.grow = int(v_)
                            elif k_ == "inset": paper.inset_px = float(v_)
                            elif k_ == "luz": state["luz"] = max(0, min(255, int(v_))); state["autoluz"] = False
                            elif k_ == "autoluz": state["autoluz"] = v_ == "1"
                            elif k_ == "luz_max": state["luz_max"] = max(0, min(255, int(v_)))
                            elif k_ == "luz_min": state["luz_min"] = max(0, min(255, int(v_)))
                        except ValueError:
                            pass
                if "on=1" in q:
                    state["rojo"] = True
                elif "on=0" in q:
                    state["rojo"] = False
                    try:
                        time.sleep(0.25)                               # que no llegue un cuadro en vuelo después del negro
                        mostrar(None)
                    except Exception:  # noqa: BLE001
                        pass
                return self._json({"rojo": state["rojo"], "enviados": state["rojo_n"], "error": state["rojo_err"], "luz": state.get("luz"),
                                   "autoluz": state.get("autoluz", True), "autoluz_info": state.get("autoluz_info")})
            if path == "/papel.jpg":
                data = state["papel"]
                if data is None:
                    self.send_error(503); return
                self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data); return
            if path == "/raw_full.jpg":                       # cuadro completo a resolución nativa (4K), para leer detalle
                fr = state.get("fr")
                if fr is None:
                    self.send_error(503); return
                ok, buf = cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 92]); data = buf.tobytes()
                self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data); return
            if path == "/raw_hd.jpg":
                fr = state.get("fr")
                if fr is None:
                    self.send_error(503); return
                sc = 1920.0 / max(fr.shape[1], fr.shape[0])          # lado mayor a 1920, sin aplastar cuadros verticales
                hd = cv2.resize(fr, (int(round(fr.shape[1] * sc)), int(round(fr.shape[0] * sc))), interpolation=cv2.INTER_AREA) if sc < 1 else fr
                ok, buf = cv2.imencode(".jpg", hd, [cv2.IMWRITE_JPEG_QUALITY, 85])
                data = buf.tobytes()
                self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data); return
            if path == "/recalibrar":                             # Quest: /recalibrar?ajustar=1 corrige si pasa el umbral
                if calib["busy"]:
                    return self._json({"ok": False, "error": "ya hay una recalibración en curso"}, 409)
                calib["busy"] = True
                try:
                    return self._json(recalibrar(ajustar="ajustar=1" in self.path))
                except Exception as e:  # noqa: BLE001
                    return self._json({"ok": False, "error": str(e)}, 500)
                finally:
                    calib["busy"] = False
            if path == "/salida.jpg":                            # el último cuadro mandado a la ventana (contenido 1900x1040)
                im_ = state.get("salida_img")
                if im_ is None:
                    self.send_error(503); return
                ok, buf = cv2.imencode(".jpg", im_, [cv2.IMWRITE_JPEG_QUALITY, 85]); data = buf.tobytes()
                self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data); return
            if path == "/camara.json":
                cam_ = reader.cam
                return self._json({"camara": cam_.status() if hasattr(cam_, "status") else {"fps": round(reader.fps, 1)},
                                   "lector_fps": round(reader.fps, 1), "gris": vis.get("gris"), "cam_full_url": a.cam_full_url})
            if path in ("/frame.jpg", "/raw.jpg"):
                data = state["jpg" if path == "/frame.jpg" else "raw"]
                if data is None:
                    self.send_error(503); return
                self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data); return
            if path == "/status":
                cam_ = reader.cam
                body = json.dumps({"fps": round(state["fps"], 1), "camara_fps": round(reader.fps, 1), "lienzo": [lw, lh], "inicio": INICIO,
                                   "calibracion": {k: v for k, v in calib["info"].items() if k != "H_cam_to_lienzo"},
                                   "mm_por_px": sup.get("mm_por_px"), "superficie_mm": sup.get("superficie_mm"), **paper.info(),
                                   "modo": "quest" if quest else "osmo", "camara": cam_.status() if hasattr(cam_, "status") else {"spec": a.camera},
                                   "gris": vis.get("gris"), "ultima_recalibracion": calib.get("ultima"),
                                   "salida": {"url": a.salida or a.runtime, "px": [W, H], "periodo_s": a.overlay_every, "jpeg_q": a.salida_q if a.salida else None,
                                              "lazo": state.get("corr_fuente") if state.get("corr") is not None else None,
                                              "hace_s": round(time.time() - state["t_salida"], 2) if state.get("t_salida") else None},
                                   "capas": state.get("capas"), "luz": state.get("luz"), "base_fuente": state.get("base_fuente"),
                                   "rojo": {"on": state["rojo"], "enviados": state["rojo_n"], "error": state["rojo_err"], "feather_px": a.feather},
                                   "monitor": mon.status() if mon else {}, "perf": perf.snapshot(), "catalogo": catalogo_status(),
                                   "rebase": rebase_status(), "seg_periodico": seg.get("on"), "clientes": dict(clientes),
                                   "jev": {k: v for k, v in jev_status().items() if k != "ultimo"}, "agente": agente_st["modo"],
                                   "teclado": {"cargado": tk["cargado"], "ref": tk["ref"], "ref_fuente": tk["ref_fuente"], "tracker": tk["kb"] is not None,
                                               "error": vis.get("kb_err")}},
                                  ensure_ascii=False, default=str).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body); return
            if path == "/":
                body = (PAGE % ()).encode()
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body); return
            self.send_error(404)

    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(json.dumps({"feed": f"http://127.0.0.1:{a.port}/", "lienzo": [lw, lh], "monitor": None if mon is None else {"roi": roi, "rotate": a.rotate, "display": a.rotate_display}}), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

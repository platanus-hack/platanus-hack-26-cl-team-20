#!/usr/bin/env python3
"""Capa de manos sobre la escena (widget aparte, transparente): la mesa reacciona a donde están las manos.

- Cursor: un anillo sigue la punta del índice de CADA mano (hasta dos), interpolado a 60 cuadros/s.
- Pasar la mano sobre un objeto: su silueta se enciende y aparece su nombre; al salir se apaga suave.
- Zonas sensibles (out/zonas.json, las escribe escritorio.py): Aprobar, Rechazar, «Comenzar juego» y la silueta de la
  nota de voz. Basta POSAR la mano (cualquier parte, no solo el índice) ~0,2 s: se llena un aro y se emite el toque.
- No deja trazos dibujados en la mesa.
- Eventos al agente (events_wait, widget_pressed con value): «toque:<id>» y «sobre:<objeto>».

Lee out/objetos.json (polígonos en mm reales) y los lleva al canvas con fp-metric.json. Se vuelve a correr cuando la
escena se rearma.
"""
import json, subprocess, time
from pathlib import Path
import numpy as np

S = '/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'
met = json.load(open('fp-metric.json')); Hm = np.array(met['mm_to_canvas']); Hm = Hm / Hm[2, 2]


def canvas(p):
    q = np.c_[np.asarray(p, float), np.ones(len(p))] @ Hm.T
    return (q[:, :2] / q[:, 2:]).round(1).tolist()


objs = []
for o in json.load(open('out/objetos.json')):
    if o.get('area_mm2', 0) > 120000: continue
    poly = o.get('poligono_proy_mm') or o['poligono_mm']        # objetos altos: la silueta que ve el proyector
    objs.append({"n": o['etiqueta'], "p": canvas(poly), "h": canvas(o['poligono_mm'])})   # p: se dibuja; h: zona sensible
zonas = []
try: zonas = [{"id": z['id'], "p": canvas(z['poly'])} for z in json.load(open('out/zonas.json'))]
except Exception: pass

JS = r"""
const OBJ = __OBJ__, ZON = __ZON__, T_TOQUE = 200;
const c = document.getElementById('c'), x = c.getContext('2d'); const W = 1000, H = 563, K = 2; c.width = W * K; c.height = H * K; x.scale(K, K);
const VERDE = '#7dffa8', LUZ = '#ffffff';
let manos = [], tUlt = 0; const cur = {}; const calor = OBJ.map(() => 0); let ultimoSobre = null, tEmit = 0;
const zs = ZON.map(() => ({ t0: 0, hecho: false, flash: 0 }));
wui.onHands(h => { manos = (h || []).filter(m => m.tip); tUlt = performance.now(); });
function dentro(p, poly) { let d = false; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) { const a = poly[i], b = poly[j]; if ((a[1] > p[1]) !== (b[1] > p[1]) && p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]) d = !d; } return d; }
function centro(poly) { let sx = 0, sy = 0; for (const q of poly) { sx += q[0]; sy += q[1]; } return [sx / poly.length, sy / poly.length]; }
function camino(poly) { x.beginPath(); poly.forEach((q, i) => i ? x.lineTo(q[0], q[1]) : x.moveTo(q[0], q[1])); x.closePath(); }
function tick() {
  const ahora = performance.now(); x.clearRect(0, 0, W, H);
  const vivas = ahora - tUlt < 400 ? manos : []; const vistos = {}; let sobre = null;
  vivas.forEach((m, i) => {                      // una entrada por mano (por índice: dos manos = dos cursores)
    vistos[i] = 1; const a = cur[i] || (cur[i] = { x: m.tip[0], y: m.tip[1] });
    a.x += (m.tip[0] - a.x) * 0.5; a.y += (m.tip[1] - a.y) * 0.5;
    OBJ.forEach((o, j) => { if (dentro([a.x, a.y], o.h)) { calor[j] = Math.min(1, calor[j] + 0.12); sobre = o.n; } });
  });
  for (const k in cur) if (!vistos[k]) delete cur[k];
  // zonas sensibles: cualquier punto de cualquier mano (dedos, palma) dentro de la zona
  ZON.forEach((z, j) => {
    const s = zs[j]; const pts = []; vivas.forEach(m => { (m.points || [m.tip]).forEach(p => pts.push(p)); if (m.palm) pts.push(m.palm); });
    const en = pts.some(p => dentro(p, z.p));
    if (en) { if (!s.t0) s.t0 = ahora; if (!s.hecho && ahora - s.t0 >= T_TOQUE) { s.hecho = true; s.flash = 1; wui.emit('toque:' + z.id); } }
    else { s.t0 = 0; s.hecho = false; }
    const q = centro(z.p);
    if (en && !s.hecho) { const f = Math.min(1, (ahora - s.t0) / T_TOQUE); x.beginPath(); x.arc(q[0], q[1], 16, -1.57, -1.57 + 6.283 * f); x.lineWidth = 4; x.strokeStyle = LUZ; x.stroke(); }
    if (s.flash > 0.02) { x.save(); camino(z.p); x.fillStyle = 'rgba(255,255,255,' + (0.55 * s.flash) + ')'; x.fill(); x.lineWidth = 3; x.strokeStyle = LUZ; x.globalAlpha = s.flash; x.stroke(); x.restore(); s.flash *= 0.93; }
  });
  // objetos bajo la mano: silueta encendida y nombre
  OBJ.forEach((o, j) => {
    if (calor[j] > 0.01) {
      x.save(); camino(o.p); x.fillStyle = 'rgba(125,255,168,' + (0.18 * calor[j]) + ')'; x.fill(); x.lineWidth = 2 + 2.5 * calor[j]; x.strokeStyle = VERDE; x.shadowColor = VERDE; x.shadowBlur = 14 * calor[j]; x.globalAlpha = calor[j]; x.stroke(); x.restore();
      const q = centro(o.p); x.save(); x.translate(q[0], q[1]); x.globalAlpha = calor[j]; x.font = '700 15px Helvetica Neue, Arial'; x.textAlign = 'center';
      const w = x.measureText(o.n).width + 18; x.fillStyle = VERDE; x.fillRect(-w / 2, -12, w, 22); x.fillStyle = '#04140a'; x.fillText(o.n, 0, 4); x.restore();
    }
    calor[j] = Math.max(0, calor[j] - 0.045);
  });
  for (const k in cur) { const a = cur[k]; x.beginPath(); x.arc(a.x, a.y, 12, 0, 6.283); x.lineWidth = 3; x.strokeStyle = LUZ; x.shadowColor = LUZ; x.shadowBlur = 8; x.stroke(); x.shadowBlur = 0; }
  if (sobre !== ultimoSobre && ahora - tEmit > 400) { ultimoSobre = sobre; tEmit = ahora; if (sobre) wui.emit('sobre:' + sobre); }
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
"""
w = {"type": "effect", "id": "manos-capa", "shader": "html", "rect_mm": [0, 0, 1000, 563],
     "params": {"html": '<canvas id="c"></canvas>', "css": "html,body{margin:0;background:transparent;overflow:hidden}canvas{position:absolute;left:0;top:0;width:1000px;height:563px}",
                "js": JS.replace('__OBJ__', json.dumps(objs, ensure_ascii=False)).replace('__ZON__', json.dumps(zonas, ensure_ascii=False))}}
manos = {"type": "effect", "id": "manos", "shader": "hands", "params": {"skeleton": False, "trail": False}}
r = subprocess.run(["/usr/bin/python3", S, "project", json.dumps({"request_id": "mc-%d" % time.time(), "widgets": [manos, w]})], capture_output=True, text=True).stdout
print(len(objs), 'objetos sensibles ·', len(zonas), 'zonas ·', r[:80])

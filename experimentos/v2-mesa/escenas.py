#!/usr/bin/env python3
"""Barra lateral de escenas (siempre visible, a la izquierda de la persona) y controlador que cambia de escena.

Cada escena es una «ventana» en la barra. Dejar la mano quieta sobre una (≈0,4 s, toque de la consola) cambia a esa escena.
  escritorio  -> procesos vivos: escritorio_auto.py (escena + capa de manos), gato.py y opciones.py
  laboratorio -> out/widget-lab.json   (estática por ahora)
  solar       -> out/widget-solar.json (estática por ahora)
  cielo       -> estrellas.py (las estrellas sobre Santiago, en vivo)
Agregar una escena = sumar una entrada a ESCENAS.  Lanzar con: doppler run -p platanus-hack-26 -c dev -- python escenas.py [escena]
"""
import json, os, subprocess, sys, time
from pathlib import Path

H = Path(__file__).parent; PY = sys.executable
S = '/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'
PAPEL, PAPEL3, VERDE = "#efe6d6", "#6f675b", "#86f5a0"
ESCENAS = [("escritorio", "Escritorio"), ("laboratorio", "Laboratorio"), ("solar", "Sistema solar"), ("cielo", "Cielo")]
PROPIOS = ["escritorio", "manos-capa", "gato", "opciones", "laboratorio", "solar", "estrellas", "auto-negro", "capa-foco"]   # widgets de escena (la barra no)
VIVOS = ["escritorio_auto.py", "gato.py", "opciones.py"]


def pcx(t, a): return subprocess.run(['/usr/bin/python3', S, t, json.dumps(a)], capture_output=True, text=True).stdout
def log(*a): print(time.strftime('%H:%M:%S'), *a, flush=True)


def barra(activa):
    """Barra compacta en el borde izquierdo de la persona (x chico del canvas). Texto girado 180°."""
    w, h, gap = 78, 54, 8; alto = len(ESCENAS) * h + (len(ESCENAS) - 1) * gap; y0 = (563 - alto) / 2; it = []
    for k, (eid, nom) in enumerate(ESCENAS):     # canvas derecho para la persona: la primera queda arriba
        y = y0 + k * (h + gap); on = eid == activa
        it.append(f'<g data-press="esc:{eid}"><rect x="6" y="{y:.0f}" width="{w}" height="{h}" rx="5" fill="{VERDE if on else "#000"}" fill-opacity="{0.9 if on else 0}" stroke="{VERDE if on else PAPEL}" stroke-width="{2.6 if on else 1.6}"/>'
                  f'<text x="{6 + w / 2}" y="{y + h / 2 + 4:.0f}" font-size="{11 if len(nom) > 10 else 12.5}" font-weight="700" fill="{"#06140b" if on else PAPEL}" text-anchor="middle" font-family="Helvetica Neue, Arial">{nom}</text></g>')
    it.append(f'<text x="{6 + w / 2}" y="{y0 - 10:.0f}" font-size="9" font-weight="700" fill="{PAPEL3}" text-anchor="middle" font-family="Helvetica Neue, Arial">ESCENAS</text>')
    svg = f'<svg viewBox="0 0 1000 563" xmlns="http://www.w3.org/2000/svg" style="position:absolute;left:0;top:0;width:1000px;height:563px">{"".join(it)}</svg>'
    manos = {"type": "effect", "id": "manos", "shader": "hands", "params": {"skeleton": False}}
    pcx('project', {"request_id": "barra-%d" % (time.time() * 10), "widgets": [manos, {"type": "effect", "id": "barra", "shader": "html", "rect_mm": [0, 0, 1000, 563], "params": {"html": svg, "css": "html,body{margin:0;background:transparent;overflow:hidden}"}}]})


def apagar():
    for p in VIVOS: subprocess.run(['pkill', '-f', p])
    time.sleep(0.5); pcx('remove', {"request_id": "esc-rm-%d" % (time.time() * 10), "ids": PROPIOS})


def lanzar(script): subprocess.Popen([PY, script], cwd=H, stdout=open(H / 'out' / (script.replace('.py', '') + '.log'), 'a'), stderr=subprocess.STDOUT, start_new_session=True)


def activar(eid, primera=False):
    log('escena ->', eid)
    if not (primera and eid == 'escritorio'): apagar()
    try: subprocess.Popen(['afplay', str(H / 'cues/aparicion.wav')])
    except Exception: pass
    if eid == 'escritorio':
        if not primera or subprocess.run(['pgrep', '-f', 'escritorio_auto.py'], capture_output=True).returncode != 0:
            for s in VIVOS: lanzar(s)
    elif eid in ('laboratorio', 'solar'):
        w = json.load(open(H / f'out/widget-{"lab" if eid == "laboratorio" else "solar"}.json')); pcx('project', {"request_id": "esc-%d" % time.time(), "widgets": [w]})
    elif eid == 'cielo':
        subprocess.run([PY, 'estrellas.py', '--proyectar'], cwd=H, capture_output=True)
    barra(eid); (H / 'out/escena.json').write_text(json.dumps({"escena": eid, "t": time.time()}))


if __name__ == '__main__':
    actual = sys.argv[1] if len(sys.argv) > 1 else 'escritorio'; activar(actual, primera=True)
    cur = json.loads(pcx('events_wait', {"cursor": 0, "timeout_s": 1})).get('cursor', 0); t_ult = 0
    while True:
        try: d = json.loads(pcx('events_wait', {"cursor": cur, "timeout_s": 20}))
        except Exception: time.sleep(2); continue
        cur = d.get('cursor', cur)
        for e in d.get('events', []):
            s = json.dumps(e, ensure_ascii=False)
            if '"barra"' not in s or 'esc:' not in s: continue
            eid = s.split('esc:')[1].split('"')[0]
            if eid != actual and eid in dict(ESCENAS) and time.time() - t_ult > 2.5: actual = eid; t_ult = time.time(); activar(eid)

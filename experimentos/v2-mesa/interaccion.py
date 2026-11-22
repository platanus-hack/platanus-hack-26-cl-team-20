#!/usr/bin/env python3
"""Reacciones de la escena de escritorio a los toques de la capa de manos (manos_capa.py emite «toque:<id>»).

  toque:aprobar / toque:rechazar -> la respuesta del agente (escritorio.py --estado) y vuelve a la pregunta a los 6 s
  toque:gato                     -> enciende el gato de la libreta (gato.py deja de esperar y empieza a consultar a Jev)
  toque:voz                      -> nota de voz: graba el micrófono mientras gira una espiral, transcribe (ElevenLabs)
                                    y deja la nota escrita junto a la silueta de la mano
Lanzar con: doppler run -p platanus-hack-26 -c dev -- python interaccion.py   (necesita ELEVENLABS_API_KEY para la voz)
"""
import json, os, subprocess, sys, time, threading
from pathlib import Path
import numpy as np

H = Path(__file__).parent; PY = sys.executable
S = '/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'
met = json.load(open(H / 'fp-metric.json')); Hm = np.array(met['mm_to_canvas']); Hm = Hm / Hm[2, 2]
M3D = "matrix3d(%s)" % ",".join("%.9g" % v for v in [Hm[0, 0], Hm[1, 0], 0, Hm[2, 0], Hm[0, 1], Hm[1, 1], 0, Hm[2, 1], 0, 0, 1, 0, Hm[0, 2], Hm[1, 2], 0, Hm[2, 2]])
SEG = 6          # segundos de grabación


def pcx(t, a): return subprocess.run(['/usr/bin/python3', S, t, json.dumps(a)], capture_output=True, text=True).stdout
def log(*a): print(time.strftime('%H:%M:%S'), *a, flush=True)
def son(n): subprocess.Popen(['afplay', str(H / f'cues/{n}.wav')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def widget_voz(cuerpo):
    css = (f"html,body{{margin:0;background:transparent;overflow:hidden}}#w{{position:absolute;left:0;top:0;width:1400px;height:720px;transform-origin:0 0;transform:{M3D}}}svg{{position:absolute;left:-10px;top:-10px;width:1340px;height:710px}}"
           ".gira{transform-box:fill-box;transform-origin:50% 50%;animation:g 2.4s linear infinite}@keyframes g{to{transform:rotate(360deg)}}"
           ".traza{stroke-dasharray:1;animation:t 6s linear both}@keyframes t{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}"
           ".nota{font-family:'Bradley Hand','Noteworthy',cursive;font-weight:700}.s{font-family:'Helvetica Neue',Arial,sans-serif;font-weight:700}")
    pcx('project', {"request_id": "voz-%d" % (time.time() * 10), "widgets": [{"type": "effect", "id": "voz", "shader": "html", "rect_mm": [0, 0, 1000, 563], "params": {"html": f'<div id="w"><svg viewBox="-10 -10 1340 710" xmlns="http://www.w3.org/2000/svg">{cuerpo}</svg></div>', "css": css}}]})


def zona_voz():
    try: return next(z for z in json.load(open(H / 'out/zonas.json')) if z['id'] == 'voz')
    except Exception: return None


def renglones(t, n=26):
    out = [""]
    for p in t.split():
        if out[-1] and len(out[-1]) + 1 + len(p) > n: out.append(p)
        else: out[-1] = (out[-1] + " " + p).strip()
    return out[:5]


def nota_de_voz():
    z = zona_voz()
    if not z: log('voz: no hay silueta en la escena'); return
    cx, cy = z['c']; son('aparicion')
    # espiral que se va trazando mientras graba (el tiempo que queda se ve en cuánto falta por trazarse)
    pts = [(cx + (8 + 9.5 * a) * np.cos(a * 1.9), cy + (8 + 9.5 * a) * np.sin(a * 1.9)) for a in np.linspace(0, 9, 140)]
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    tx = lambda x, y, s, size, col, cls="s": f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{col}" class="{cls}" text-anchor="middle" transform="rotate(180 {x:.1f} {y:.1f})">{s}</text>'
    widget_voz(f'<g class="gira"><path d="{d}" fill="none" stroke="#ffffff" stroke-width="3.2" stroke-linecap="round" pathLength="1" class="traza"/></g>' + tx(cx, cy - 112, "Grabando… habla ahora", 15, "#ffffff"))
    wav = H / 'out/nota.wav'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'avfoundation', '-i', ':default', '-t', str(SEG), '-ac', '1', '-ar', '16000', str(wav)], capture_output=True)
    widget_voz(tx(cx, cy - 112, "Transcribiendo…", 15, "#a9a9a6")); texto = ''
    try:
        r = subprocess.run(['curl', '-s', '-m', '30', 'https://api.elevenlabs.io/v1/speech-to-text', '-H', 'xi-api-key: ' + os.environ['ELEVENLABS_API_KEY'], '--form-string', 'model_id=scribe_v1', '--form-string', 'language_code=spa', '-F', f'file=@{wav}'], capture_output=True, text=True)
        texto = (json.loads(r.stdout).get('text') or '').strip()
    except Exception as ex: log('voz: transcripción falló', type(ex).__name__)
    if not texto: widget_voz(tx(cx, cy - 112, "No te escuché. Posa la mano de nuevo.", 13, "#a9a9a6")); son('recuperacion'); time.sleep(4); pcx('remove', {"request_id": "voz-rm-%d" % time.time(), "ids": ["voz"]}); return
    log('nota de voz:', texto); son('confirmacion'); (H / 'out/notas.txt').open('a').write(time.strftime('%H:%M ') + texto + '\n')
    rs = renglones(texto); cuerpo = tx(cx, cy - 112, "Nota guardada", 12, "#a9a9a6")
    for k, rr in enumerate(rs): cuerpo += tx(cx, cy - 136 - 22 * k, rr, 19, "#7aa8ff", "nota")
    widget_voz(cuerpo); time.sleep(14); pcx('remove', {"request_id": "voz-rm-%d" % time.time(), "ids": ["voz"]})


def respuesta(est):
    subprocess.run([PY, 'escritorio.py', '--estado', est], cwd=H, capture_output=True); son('confirmacion')
    time.sleep(6); subprocess.run([PY, 'escritorio.py', '--estado', 'pregunta'], cwd=H, capture_output=True); subprocess.run([PY, 'manos_capa.py'], cwd=H, capture_output=True)


if __name__ == '__main__':
    ocupado = {}
    def lanza(k, f, *a):
        if ocupado.get(k) and ocupado[k].is_alive(): return
        ocupado[k] = threading.Thread(target=f, args=a, daemon=True); ocupado[k].start()
    try: cur = json.loads(pcx('events_wait', {"cursor": 0, "timeout_s": 1})).get('cursor', 0)
    except Exception: cur = 0
    log('escuchando toques')
    while True:
        try: d = json.loads(pcx('events_wait', {"cursor": cur, "timeout_s": 20}))
        except Exception: time.sleep(2); continue
        cur = d.get('cursor', cur)
        for e in d.get('events', []):
            s = json.dumps(e, ensure_ascii=False)
            if 'toque:aprobar' in s: log('toque: aprobar'); lanza('resp', respuesta, 'aprobado')
            elif 'toque:rechazar' in s: log('toque: rechazar'); lanza('resp', respuesta, 'rechazado')
            elif 'toque:gato' in s: log('toque: comenzar juego'); (H / 'out/gato-juego.flag').write_text('1'); son('confirmacion')
            elif 'toque:voz' in s: log('toque: nota de voz'); lanza('voz', nota_de_voz)

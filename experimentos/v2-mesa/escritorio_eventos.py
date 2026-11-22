#!/usr/bin/env python3
"""Escucha los toques (dedo quieto) sobre Aprobar / Rechazar de la escena de escritorio y actualiza la respuesta."""
import json, subprocess, sys, time
S='/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'
def pcx(t,a): return subprocess.run(["/usr/bin/python3",S,t,json.dumps(a)],capture_output=True,text=True).stdout
cur=json.loads(pcx('events_wait',{"cursor":0,"timeout_s":1})).get('cursor',0)
while True:
    try: d=json.loads(pcx('events_wait',{"cursor":cur,"timeout_s":25}))
    except Exception: time.sleep(2); continue
    cur=d.get('cursor',cur)
    for e in d.get('events',[]):
        p=e.get('payload') or {}; s=json.dumps(p,ensure_ascii=False)
        if 'escritorio' in s and ('aprobar' in s or 'rechazar' in s):
            est='aprobado' if 'aprobar' in s else 'rechazado'; print(time.strftime('%H:%M:%S'),'toque:',est,flush=True)
            subprocess.run([sys.executable,'escritorio.py','--estado',est],capture_output=True); time.sleep(6); subprocess.run([sys.executable,'escritorio.py','--estado','pregunta'],capture_output=True)

#!/usr/bin/env python3
"""Toda la secuencia de la mesa de una vez: calibración -> checkerboard -> segmentación con los contornos prendiéndose -> modelos 3D -> planetas."""
import subprocess, sys, time, json
from pathlib import Path
import numpy as np
H=Path(__file__).parent; PY=sys.executable; PCX='/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'
def pcx(t,a): return subprocess.run(['/usr/bin/python3',PCX,t,json.dumps(a)],capture_output=True,text=True).stdout
def est(e): (H/'out/estado.json').write_text(json.dumps({"etapa":e,"t":time.time(),"etapas":[]}))
def limpiar():
    ids=[w['id'] for w in json.loads(pcx('scene',{})).get('widgets',[])]
    if ids: pcx('remove',{'request_id':'todo-rm-%d'%time.time(),'ids':ids})
def run(args,**kw):
    t=time.time(); r=subprocess.run([PY]+args,cwd=H,capture_output=True,text=True); print(f'[{time.time()-t:5.1f} s] {" ".join(args)}'); out=[l for l in (r.stdout+r.stderr).splitlines() if 'OpenCV' not in l and 'WARN' not in l]; print('    '+'\n    '.join(out[-kw.get("n",4):])); return r
t0=time.time(); subprocess.run(['pkill','-f','pipeline.py vivo'])
est('calibrando (calibrate + iPhone + luz estructurada)'); run(['calibrar.py','--settle','0.3'],n=8)
est('checkerboard de 50 mm en el plano real'); m=json.load(open(H/'fp-metric.json')); Hm=np.array(m['mm_to_canvas']); Hm=Hm/Hm[2,2]
m3d="matrix3d(%s)"%",".join("%.9g"%v for v in [Hm[0,0],Hm[1,0],0,Hm[2,0],Hm[0,1],Hm[1,1],0,Hm[2,1],0,0,1,0,Hm[0,2],Hm[1,2],0,Hm[2,2]])
css=f"html,body{{margin:0;background:#000;overflow:hidden}}#w{{position:absolute;left:0;top:0;width:1400px;height:700px;transform-origin:0 0;transform:{m3d};background:conic-gradient(#cfcfcf 25%,#000 0 50%,#cfcfcf 0 75%,#000 0) 0 0/100px 100px}}#t{{position:absolute;left:0;top:0;width:{m['W']:.1f}px;height:{m['H']:.1f}px;box-sizing:border-box;border:4px solid #ff3b00}}"
pcx('project',{"request_id":"todo-chk-%d"%time.time(),"widgets":[{"type":"effect","id":"v2-checker","shader":"html","rect_mm":[0,0,1000,563],"params":{"html":'<div id="w"><div id="t"></div></div>',"css":css}}]}); time.sleep(7)
limpiar(); subprocess.run([str(H/'frente.sh')]); time.sleep(1.2)
run(['pipeline.py','una','--progresivo'],n=3); time.sleep(3)
est('modelos 3D'); run(['modelos.py'],n=2); limpiar(); est('planetas 3D'); run(['proyectar3d.py'],n=1)
print(f'TODO: {time.time()-t0:.0f} s'); subprocess.Popen([PY,'pipeline.py','vivo'],cwd=H,stdout=open(H/'out/pipeline.log','a'),stderr=subprocess.STDOUT,start_new_session=True)

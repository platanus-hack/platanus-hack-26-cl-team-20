#!/usr/bin/env python3
"""Calibración completa de la mesa en un comando:  .venv-python calibrar.py [--sin-sl] [--settle 0.3]
1) ventana(s) de proyección a pantalla completa  2) calibrate precise de la consola (cámara<->canvas)
3) foto 4K -> bordes del tablero + iPhone XR -> marco métrico real (fp-metric.json)
4) luz estructurada (gray code) con lo que haya en la mesa -> relieve (fp-parallax.npz, out/relieve.jpg)"""
import subprocess, sys, time, json, shutil
from pathlib import Path
import cv2
H=Path(__file__).parent; PY=sys.executable; PCX='/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'; SL=Path.home()/'code/platanus-hack-26/sl-scanner'
settle=float(sys.argv[sys.argv.index('--settle')+1]) if '--settle' in sys.argv else 0.3
T={}; t0=time.time()
def paso(n,f):
    t=time.time(); r=f(); T[n]=round(time.time()-t,1); print(f'[{T[n]:5.1f} s] {n}',flush=True); return r
def pcx(tool,args): return subprocess.run(['/usr/bin/python3',PCX,tool,json.dumps(args)],capture_output=True,text=True).stdout
def full():
    subprocess.run(['osascript','-e','tell application "System Events" to tell process "Google Chrome"\nrepeat with w in windows\nif (name of w) starts with "WUI · proyector" then\nif (value of attribute "AXFullScreen" of w) is false then set value of attribute "AXFullScreen" of w to true\nend if\nend repeat\nend tell'],capture_output=True); time.sleep(2.5)
def limpiar():
    sc=json.loads(pcx('scene',{})); ids=[w['id'] for w in sc.get('widgets',[])]
    if ids: pcx('remove',{'request_id':'cal-rm-%d'%time.time(),'ids':ids})
def calib():
    out=pcx('calibrate',{'request_id':'cal-%d'%time.time(),'mode':'precise'}); d=json.loads(out[out.index('{'):out.rindex('}')+1])
    assert d.get('saved'),out[:300]; (H/'cal-local.json').write_text(json.dumps(d)); print('   ',d['points_found'],'/',d['points_total'],d['error_mm'])
def foto():
    c=cv2.VideoCapture(0,cv2.CAP_AVFOUNDATION); c.set(cv2.CAP_PROP_FRAME_WIDTH,3840); c.set(cv2.CAP_PROP_FRAME_HEIGHT,2160)
    for _ in range(8): ok,f=c.read()
    c.release(); cv2.imwrite(str(H/'hires-0.jpg'),f,[cv2.IMWRITE_JPEG_QUALITY,93])
def geo():
    for s in ('fp_geo.py','fp_metric.py'):
        r=subprocess.run([PY,s],cwd=H,capture_output=True,text=True); print('   '+r.stdout.strip().replace('\n','\n   ')); assert r.returncode==0,r.stderr[-400:]
def sl():
    shutil.rmtree(H/'sl',ignore_errors=True) if False else None
    r=subprocess.run([str(SL/'.venv/bin/python'),'-m','slscan','scan','--camera','0','--cam-size','3840x2160','--downscale','1','--rotate','0','--screen','1','--skip-bits','1','--settle',str(settle),'--out',str(H/'sl')],cwd=SL,capture_output=True,text=True)
    print('   '+[l for l in r.stdout.splitlines() if 'decodificados' in l][-1])
    r=subprocess.run([PY,'fp_height.py'],cwd=H,capture_output=True,text=True); print('   '+r.stdout.strip().splitlines()[-2]); shutil.copy(H/'fp-parallax.jpg',H/'out/relieve.jpg')
paso('pantalla completa',full); paso('limpiar la proyección',limpiar); paso('calibrate precise (cámara <-> canvas)',calib); paso('foto 4K',foto); paso('bordes + iPhone -> mm reales',geo)
if '--sin-sl' not in sys.argv: paso(f'luz estructurada (44 patrones, {settle} s c/u) + relieve',sl)
paso('proyección de la mesa al frente',lambda: subprocess.run([str(H/'frente.sh')]))
print(f'TOTAL {time.time()-t0:.1f} s'); (H/'out/calibracion.json').write_text(json.dumps({'t':time.time(),'pasos':T,'total':round(time.time()-t0,1)}))

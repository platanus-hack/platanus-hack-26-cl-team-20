#!/usr/bin/env python3
"""Escritorio que se rearma solo: cuando la mesa cambia de verdad (no una mano ni una sombra) y vuelve a quedar quieta,
apaga la escena, toma una foto limpia, detecta (Grounding DINO) + segmenta (SAM por recorte) y vuelve a proyectar."""
import cv2, json, numpy as np, subprocess, sys, time, threading
from pathlib import Path
H=Path(__file__).parent; PY=sys.executable; S='/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'
sys.path.insert(0,str(H)); from fp_rect import mapa
import sonido
def pcx(t,a): return subprocess.run(['/usr/bin/python3',S,t,json.dumps(a)],capture_output=True,text=True).stdout
def log(*a): print(time.strftime('%H:%M:%S'),*a,flush=True)
c=cv2.VideoCapture(0,cv2.CAP_AVFOUNDATION); c.set(cv2.CAP_PROP_FRAME_WIDTH,3840); c.set(cv2.CAP_PROP_FRAME_HEIGHT,2160); F=[None]
def loop():
    while True:
        ok,f=c.read()
        if ok: F[0]=f
        else: time.sleep(0.03)
threading.Thread(target=loop,daemon=True).start()
_M={}
def gris():
    while F[0] is None: time.sleep(0.05)
    f=F[0]; w=f.shape[1]
    if w not in _M: _M[w]=mapa(-10,-10,1330,700,0.5,w/1280.0)
    mx,my=_M[w]; return cv2.GaussianBlur(cv2.cvtColor(cv2.remap(f,mx,my,cv2.INTER_LINEAR),cv2.COLOR_BGR2GRAY),(0,0),1.5).astype(np.int16)
def hay_mano():
    """Jev (≈0,5 s): ¿hay una mano o un brazo sobre la mesa ahora? Sin llave o sin respuesta: se asume que no."""
    try:
        import jevc
        f=F[0]; mx,my=mapa(-10,-10,1330,700,0.8,f.shape[1]/1280.0); im=cv2.remap(f,mx,my,cv2.INTER_LINEAR)
        r=jevc.decide([{"type":"text","text":"Top-down view of a table with objects and projected light."},jevc.imagen(im)],{"mano":{"type":"boolean","instructions":"Is a human hand, arm or person visibly over the table surface right now?"}},timeout=4)
        p=(r.get('answers') or {}).get('mano'); 
        if p is not None and p>0.5: log('mano sobre la mesa (%.2f): no se rearma'%p); return True
    except Exception: pass
    return False
def centros():
    # solo objetos confiables: con la luz encendida el detector a veces «ve» nuestros propios botones como láminas o cables
    try: return [(o['etiqueta'],o['centro_mm'],o['tam_mm']) for o in json.load(open(H/'out/objetos.json')) if o.get('score',1)>=0.55 or o['etiqueta'].split(' ')[0] in ('taza','plátano','lata','bola','plato','engranaje')]
    except Exception: return []
import urllib.request, re
_TOK=open(str(Path(S).parent/'local-token')).read().strip()
def rapido(tool,args):
    """Llamada MCP en el mismo proceso (sin lanzar otro Python): ~20 ms en vez de ~200."""
    try:
        q=urllib.request.Request('http://localhost:9217/mcp',data=json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":tool,"arguments":args}}).encode(),headers={"Authorization":"Bearer "+_TOK,"Content-Type":"application/json","Accept":"application/json, text/event-stream"})
        urllib.request.urlopen(q,timeout=8).read()
    except Exception as e: log('mcp',tool,e)
_met=json.load(open(H/'fp-metric.json')); _Hm=np.array(_met['mm_to_canvas']); _Hm=_Hm/_Hm[2,2]
PP=0.5; X0=Y0=-10          # la rejilla de gris(): 0,5 px por mm, origen (-10,-10) mm
K5=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(7,7))
def zona(g,ref,um=26):
    """Dónde cambió la mesa de verdad. Se descartan trazos finos (líneas proyectadas, lápiz): solo cuentan manchas con cuerpo.
    Devuelve (píxeles con cuerpo, caja en la rejilla x0,y0,x1,y1 con margen) o (0,None)."""
    m=cv2.morphologyEx((np.abs(g-ref)>um).astype(np.uint8),cv2.MORPH_OPEN,K5); n=int(m.sum())
    if n==0: return 0,None
    ys,xs=np.nonzero(m); mg=int(35*PP); h,w=m.shape
    return n,(max(0,xs.min()-mg),max(0,ys.min()-mg),min(w-1,xs.max()+mg),min(h-1,ys.max()+mg))
def foto_limpia(caja):
    """Foto sin proyección apagando SOLO la zona que cambió, y solo lo que tarda la cámara en verla a oscuras (~0,2 s).
    El resto de la foto sale de la foto limpia anterior. Devuelve True si en la zona cambió algo de verdad."""
    while F[0] is None: time.sleep(0.05)
    f0=F[0]; esc=f0.shape[1]/1280.0; mx,my=mapa(-10,-10,1330,700,PP,esc); h,w=mx.shape
    ant=cv2.imread(str(H/'out/limpia-4k.jpg')) if caja else None
    if ant is None or ant.shape!=f0.shape or (caja[2]-caja[0])*(caja[3]-caja[1])>0.45*w*h: caja=(0,0,w-1,h-1); ant=None
    x0,y0,x1,y1=caja; mm=np.array([[X0+x0/PP,Y0+y0/PP],[X0+x1/PP,Y0+y0/PP],[X0+x1/PP,Y0+y1/PP],[X0+x0/PP,Y0+y1/PP]])
    q=np.c_[mm,np.ones(4)]@_Hm.T; cv=(q[:,:2]/q[:,2:]).round(1)
    if ant is None: html='<div style="position:absolute;inset:0;background:#000"></div>'
    else: html='<svg viewBox="0 0 1000 563" style="position:absolute;left:0;top:0;width:1000px;height:563px"><polygon points="%s" fill="#000"/></svg>'%' '.join('%.1f,%.1f'%tuple(p) for p in cv)
    t=time.time(); rapido('project',{"request_id":"an-%d"%(t*100),"widgets":[{"type":"effect","id":"auto-negro","shader":"html","rect_mm":[0,0,1000,563],"params":{"html":html,"css":"html,body{margin:0;background:transparent;overflow:hidden}"}}]})
    # esperar a que la cámara vea la zona a oscuras: el brillo deja de bajar (mínimo 0,12 s, máximo 0,6 s)
    def brillo(): return float(gris()[y0:y1+1,x0:x1+1].mean())
    b0=brillo(); ult=b0; ok=0
    while time.time()-t<0.6:
        time.sleep(0.04); b=brillo()
        ok=ok+1 if (abs(b-ult)<0.6 and time.time()-t>0.12) else 0; ult=b
        if ok>=2: break
    f=F[0].copy(); rapido('remove',{"request_id":"anr-%d"%(time.time()*100),"ids":["auto-negro"]}); dt=time.time()-t
    if ant is None: cv2.imwrite(str(H/'out/limpia-4k.jpg'),f,[cv2.IMWRITE_JPEG_QUALITY,95]); return True,dt
    px=mx[y0:y1+1,x0:x1+1]; py=my[y0:y1+1,x0:x1+1]; a0,a1,b0_,b1=int(max(0,px.min())),int(min(f.shape[1],px.max()+1)),int(max(0,py.min())),int(min(f.shape[0],py.max()+1))
    v=cv2.GaussianBlur(cv2.cvtColor(f[b0_:b1,a0:a1],cv2.COLOR_BGR2GRAY),(0,0),3).astype(np.int16); u=cv2.GaussianBlur(cv2.cvtColor(ant[b0_:b1,a0:a1],cv2.COLOR_BGR2GRAY),(0,0),3).astype(np.int16)
    kk=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(int(14*esc)|1,int(14*esc)|1)); real=int(cv2.morphologyEx((np.abs(v-u)>22).astype(np.uint8),cv2.MORPH_OPEN,kk).sum())
    ant[b0_:b1,a0:a1]=f[b0_:b1,a0:a1]; cv2.imwrite(str(H/'out/limpia-4k.jpg'),ant,[cv2.IMWRITE_JPEG_QUALITY,95])
    return real>int(600*esc*esc),dt
import os
ESC=os.environ.get('ESCENA','escritorio')      # qué escena se compone con lo detectado: escritorio | solar
PROMPTS='prompts-%s.json'%ESC
ID=[ESC]
def componer(args_esc,wid,primera):
    """Compone y proyecta la escena con el id de widget dado. Devuelve False si la escena no se pudo armar."""
    if ESC=='escritorio':
        subprocess.run([PY,'escritorio.py']+args_esc,cwd=H,capture_output=True,env={**os.environ,'ESC_ID':wid}); return True
    if ESC=='solar':      # solar.py arma el widget en un archivo; aquí se proyecta con el id de relevo
        r=subprocess.run([PY,'solar.py','--objetos','out/objetos.json','--intro' if primera else '--sin-intro','--salida','out/widget-solar.json'],cwd=H,capture_output=True,text=True)
        if r.returncode!=0: log('solar.py:',(r.stderr or r.stdout).strip()[-160:]); return False
        w=json.load(open(H/'out/widget-solar.json')); w=w.get('widget',w) if isinstance(w,dict) else w; w['id']=wid
        rapido('project',{"request_id":"sol-%d"%(time.time()*100),"widgets":[w]}); return True
    return False
def armar(primera=False,caja=None):
    """Rearma SIN apagar nada: detecta con la proyección encendida y releva la escena (la nueva entra con otro id y
    recién entonces se quita la vieja), así no hay ni un cuadro a oscuras. Solo se anima lo que cambió."""
    gris(); t=time.time(); antes=centros()
    try: previos=[] if primera else json.load(open(H/'out/objetos.json'))
    except Exception: previos=[]
    cv2.imwrite(str(H/'out/limpia-4k.jpg'),F[0],[cv2.IMWRITE_JPEG_QUALITY,95])
    r=subprocess.run([PY,'pipeline.py','una','--dino','--prompts',PROMPTS,'--foto','out/limpia-4k.jpg'],cwd=H,capture_output=True,text=True)
    if previos:   # lo que sigue en su lugar conserva su máscara anterior (tomada en limpio); solo lo movido o nuevo usa la nueva
        try:
            nuevos=json.load(open(H/'out/objetos.json')); usados=set()
            for i,o in enumerate(nuevos):
                for j,q in enumerate(previos):
                    if j in usados or q['etiqueta']!=o['etiqueta']: continue
                    if abs(q['centro_mm'][0]-o['centro_mm'][0])<18 and abs(q['centro_mm'][1]-o['centro_mm'][1])<18 and 0.75<(q.get('area_mm2',1)/max(o.get('area_mm2',1),1))<1.33: nuevos[i]=q; usados.add(j); break
            json.dump(nuevos,open(H/'out/objetos.json','w'),ensure_ascii=False)
        except Exception as ex: log('conservar máscaras:',ex)
    n=[l for l in r.stdout.splitlines() if l.endswith('objetos')]; ahora=centros(); camb=[]
    for k,(e,c,tm) in enumerate(ahora):
        igual=[1 for (e2,c2,t2) in antes if e2==e and abs(c2[0]-c[0])<25 and abs(c2[1]-c[1])<25]
        if not igual: camb.append(str(k))
    if not ahora and not json.load(open(H/'out/objetos.json')):      # la detección falló (cero objetos): no se arma una mesa vacía
        if previos: json.dump(previos,open(H/'out/objetos.json','w'),ensure_ascii=False)
        log('detección sin objetos: se ignora'); return False
    if not primera and not camb and len(ahora)==len(antes): log('nada cambió de lugar: no se toca la escena · %.1f s'%(time.time()-t)); return False
    args=[] if primera else ['--estado','pregunta','--cambiados',','.join(camb)]
    if primera and os.environ.get('ESPERA'):      # preparada: ya está todo segmentado; se proyecta cuando llegue «dale»
        log('preparada: %d objetos segmentados, esperando dale'%len(ahora)); (H/'out/lista.flag').write_text('1')
        while not (H/'out/visible.flag').exists(): time.sleep(0.1)
    viejo=ID[0]; nuevo_id=ESC+'-b' if viejo==ESC else ESC
    if not componer(args,nuevo_id,primera): return False
    sonido.play('aparicion'); ID[0]=nuevo_id
    time.sleep(0.35); rapido('remove',{"request_id":"rel-%d"%(time.time()*100),"ids":[viejo]})
    (H/'out/esc-id.txt').write_text(ID[0])
    if ESC!='escritorio': (H/'out/zonas.json').write_text('[]')
    subprocess.run([PY,'manos_capa.py'],cwd=H,capture_output=True)
    log('escena actualizada sin apagar:',n[-1] if n else r.stderr[-200:],'· cambiaron',len(camb),'· %.1f s'%(time.time()-t)); return True
def asentar():
    """Referencia nueva cuando la proyección ya terminó de animarse (si no, la propia animación parece un cambio)."""
    time.sleep(3.0); return gris()
armar(primera=True); ref=asentar(); prev=ref; quieto=0; pend=False
while True:
    time.sleep(0.15); g=gris(); mov=float((np.abs(g-prev)>16).mean()); prev=g
    if mov>0.012: quieto=0; pend=True; continue      # solo un movimiento grande (una mano que entra) arma la revisión; las animaciones propias no
    if mov>0.004: quieto=0; continue
    quieto+=1
    if not pend or quieto<5: continue                       # ~0,8 s quieta después de un movimiento
    pend=False
    n,caja=zona(g,ref)
    if n<600: continue                                       # mano o sombra que ya se fue, o solo cambió la proyección: nada
    if hay_mano(): pend=True; quieto=0; time.sleep(0.6); continue   # hay una mano o un brazo quieto sobre la mesa: esperar a que salga
    if armar(caja=caja): ref=asentar()
    else: ref=gris()
    prev=ref

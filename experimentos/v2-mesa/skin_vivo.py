#!/usr/bin/env python3
"""Skin generado en vivo sobre la mesa: foto limpia 4K -> cuenta regresiva 10 s -> cronómetro mientras genera -> imagen proyectada
con el tiempo final. La libreta va recortada con su máscara SAM como capa aparte y se sigue en tiempo real; debajo queda metal."""
import cv2, json, numpy as np, subprocess, sys, time, threading, base64, os
from pathlib import Path
H=Path(__file__).parent; SK=H/'skin'; PCX='/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'
sys.path.insert(0,str(H)); sys.path.insert(0,str(Path.home()/'code/platanus-hack-26/sl-scanner'))
from fp_rect import mapa
from playground.appearance_prepare import MobileSAM
ESTILO=sys.argv[1] if len(sys.argv)>1 else 'cyberlab'; PP=2.0; X0,Y0,X1,Y1=-10,-10,1330,700
met=json.load(open(H/'fp-metric.json')); Hm=np.array(met['mm_to_canvas']); Hm=Hm/Hm[2,2]
M3D="matrix3d(%s)"%",".join("%.9g"%v for v in [Hm[0,0],Hm[1,0],0,Hm[2,0],Hm[0,1],Hm[1,1],0,Hm[2,1],0,0,1,0,Hm[0,2],Hm[1,2],0,Hm[2,2]])
def pcx(t,a): return subprocess.run(['/usr/bin/python3',PCX,t,json.dumps(a)],capture_output=True,text=True).stdout
def b64(img,ext,params): ok,buf=cv2.imencode(ext,img,params); return base64.b64encode(buf.tobytes()).decode()
class Cam:
    def __init__(s):
        s.c=cv2.VideoCapture(0,cv2.CAP_AVFOUNDATION); s.c.set(cv2.CAP_PROP_FRAME_WIDTH,3840); s.c.set(cv2.CAP_PROP_FRAME_HEIGHT,2160); s.f=None; threading.Thread(target=s.loop,daemon=True).start()
    def loop(s):
        while True:
            ok,f=s.c.read()
            if ok: s.f=f
            else: time.sleep(0.03)
    def norm(s,x0=X0,y0=Y0,x1=X1,y1=Y1,pp=PP):
        while s.f is None: time.sleep(0.05)
        f=s.f.copy(); mx,my=mapa(x0,y0,x1,y1,pp,f.shape[1]/1280.0); return cv2.remap(f,mx,my,cv2.INTER_LINEAR)
def papel(im,pp,org,area_ref=None,cerca=None):
    """Papel blanco en una imagen normalizada (pp px/mm, origen org mm): mancha clara, grande y rectangular. -> pose en mm."""
    L=cv2.GaussianBlur(cv2.cvtColor(im,cv2.COLOR_BGR2LAB)[...,0],(0,0),2); best=None
    for th in sorted({int(cv2.threshold(L,0,255,cv2.THRESH_OTSU)[0]),int(np.percentile(L,80)),int(np.percentile(L,88)),int(np.percentile(L,70))}):
        m=cv2.morphologyEx((L>th).astype(np.uint8),cv2.MORPH_OPEN,np.ones((int(9*pp)|1,int(9*pp)|1),np.uint8)); m=cv2.morphologyEx(m,cv2.MORPH_CLOSE,np.ones((int(15*pp)|1,int(15*pp)|1),np.uint8))
        for c in cv2.findContours(m,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]:
            a=cv2.contourArea(c)/pp**2
            if a<20000 or a>130000: continue
            hull=cv2.convexHull(c); (cx,cy),(w,h),ang=cv2.minAreaRect(hull); rect=cv2.contourArea(hull)/(w*h+1e-6)
            if rect<0.86 or min(w,h)/pp<120: continue
            if w<h: w,h=h,w; ang+=90
            cand=dict(poly=hull.reshape(-1,2)/pp+org,c=np.array([cx,cy])/pp+org,tam=(w/pp,h/pp),ang=((ang+90)%180)-90,area=a)
            sc=rect-(abs(a/area_ref-1) if area_ref else 0)-(np.linalg.norm(cand['c']-cerca)/600 if cerca is not None else 0)
            if area_ref and abs(a/area_ref-1)>0.22: continue
            if best is None or sc>best[0]: best=(sc,cand)
    return best[1] if best else None
TIMER_JS="""const e=document.getElementById('t'),s=document.getElementById('s'),t0=Date.now();
setInterval(()=>{const d=(Date.now()-t0)/1000;if(d<10){e.textContent=Math.ceil(10-d);s.textContent='comienza la generación en';}else{e.textContent=(d-10).toFixed(1).replace('.',',')+' s';s.textContent='generando la imagen…';}},50);"""
def timer():
    css="html,body{margin:0;background:#000;overflow:hidden;font-family:ui-sans-serif,system-ui}#b{position:absolute;left:300px;top:150px;width:500px;height:260px;transform:rotate(180deg);text-align:center;color:#fff}#t{font-size:150px;font-weight:800;color:#00E5FF;line-height:1}#s{font-size:30px;margin-bottom:14px}"
    pcx('project',{"request_id":"sk-t-%d"%time.time(),"widgets":[{"type":"effect","id":"skin-timer","shader":"html","rect_mm":[0,0,1000,563],"params":{"html":'<div id="b"><div id="s"></div><div id="t"></div></div>',"css":css,"js":TIMER_JS}}]})
def w_fondo(img,txt=""):
    css=f"html,body{{margin:0;background:#000;overflow:hidden}}#w{{position:absolute;left:0;top:0;width:1400px;height:720px;transform-origin:0 0;transform:{M3D}}}#w img{{position:absolute;left:{X0}px;top:{Y0}px;width:{X1-X0}px;height:{Y1-Y0}px}}#tt{{position:absolute;left:440px;top:345px;width:260px;transform:rotate(180deg);text-align:center;font:700 26px ui-sans-serif,system-ui;color:#00E5FF;text-shadow:0 0 8px #000,0 0 3px #000}}"
    return {"type":"effect","id":"skin","shader":"html","rect_mm":[0,0,1000,563],"params":{"html":f'<div id="w"><img src="data:image/jpeg;base64,{b64(img,".jpg",[cv2.IMWRITE_JPEG_QUALITY,86])}"><div id="tt">{txt}</div></div>',"css":css}}
def w_libreta(rgba,ref,pose):
    """rgba: recorte de la libreta (2 px/mm) alrededor de ref['c'] sin girar; pose: dónde está ahora."""
    h,w=rgba.shape[:2]; wm,hm=w/PP,h/PP; d=pose['c']; rot=pose['ang']-ref['ang']
    css=f"html,body{{margin:0;background:transparent;overflow:hidden}}#w{{position:absolute;left:0;top:0;width:1400px;height:720px;transform-origin:0 0;transform:{M3D}}}#w img{{position:absolute;left:0;top:0;width:{wm:.1f}px;height:{hm:.1f}px;transform-origin:0 0;transform:translate({d[0]:.1f}px,{d[1]:.1f}px) rotate({rot:.2f}deg) translate({-wm/2:.1f}px,{-hm/2:.1f}px)}}"
    return {"type":"effect","id":"skin-libreta","shader":"html","rect_mm":[0,0,1000,563],"params":{"html":f'<div id="w"><img src="data:image/webp;base64,{LIB64}"></div>',"css":css}}
if __name__=='__main__':
    subprocess.run(['pkill','-f','pipeline.py vivo']); pcx('remove',{"request_id":"sk-rm-%d"%time.time(),"ids":["skin","skin-libreta","skin-timer","skin-tiempo","v2-planetas3d","v2-checker"]})
    subprocess.run([str(H/'frente.sh')]); cam=Cam(); time.sleep(1.5)
    limpia=cam.norm()
    if '--reusar' not in sys.argv: cv2.imwrite(str(SK/'entrada-vivo.png'),limpia)
    caja=[d for d in json.load(open(H/'out/vlm.json')) if d['etiqueta']=='libreta'][0]['caja_mm']
    ref=papel(limpia[0:int(480*PP),0:int(580*PP)],PP,np.array([X0,Y0],float)) if '--reusar' not in sys.argv else papel(cv2.imread(str(SK/'entrada-vivo.png'))[0:int(480*PP),0:int(580*PP)],PP,np.array([X0,Y0],float)); print('libreta: centro',ref['c'].round(1),'tam',[round(v) for v in ref['tam']],'ángulo',round(ref['ang'],1),flush=True)
    REUSAR='--reusar' in sys.argv
    if not REUSAR: timer(); time.sleep(10)
    t0=time.time()
    r=subprocess.run(['true'] if REUSAR else [sys.executable,'gen_google.py',ESTILO,'gemini-3-pro-image','entrada-vivo.png'],cwd=SK,capture_output=True,text=True,env={**os.environ,"IMG_SIZE":os.environ.get("IMG_SIZE","4K")}); seg=json.load(open(H/'out/skin.json'))['segundos'] if REUSAR else time.time()-t0
    print(r.stdout[-300:],r.stderr[-300:],f'generación: {seg:.1f} s',flush=True)
    gen=cv2.imread(str(SK/'salidas'/f'google-{ESTILO}.png')); assert gen is not None,'sin imagen'
    gen=cv2.resize(gen,(limpia.shape[1],limpia.shape[0]),interpolation=cv2.INTER_AREA)
    mask=np.zeros(gen.shape[:2],np.uint8); pp=((ref['poly']-[X0,Y0])*PP).astype(np.int32); cv2.fillPoly(mask,[pp],255)
    # capa de la libreta: recorte RGBA centrado en la libreta
    R=int(max(ref['tam'])*PP*0.62); cx,cy=((ref['c']-[X0,Y0])*PP).astype(int); x0,y0=max(cx-R,0),max(cy-R,0); sub=gen[y0:cy+R,x0:cx+R]; a=cv2.GaussianBlur(mask[y0:cy+R,x0:cx+R],(0,0),1.2)
    rgba=np.dstack([sub,a]); LIB64=b64(rgba,'.webp',[cv2.IMWRITE_WEBP_QUALITY,88]); ref0c=ref['c'].copy(); ref['c']=np.array([(x0+sub.shape[1]/2)/PP+X0,(y0+sub.shape[0]/2)/PP+Y0]); ref0=dict(ref)
    # fondo: bajo la libreta, piso de metal (se rellena con la textura de mesa generada alrededor)
    # piso de metal: se copia una franja de mesa generada (libre de objetos) en mosaico espejado, algo más oscura
    def px(mmx,mmy): return int((mmx-X0)*PP),int((mmy-Y0)*PP)
    (sx0,sy0),(sx1,sy1)=px(330,305),px(560,392); pat=gen[sy0:sy1,sx0:sx1]; fila=np.hstack([pat,pat[:,::-1]]); tile=np.vstack([fila,fila[::-1]])
    md=cv2.dilate(mask,np.ones((31,31),np.uint8)); bx,by,bw,bh=cv2.boundingRect(md); rep=np.tile(tile,(bh//tile.shape[0]+1,bw//tile.shape[1]+1,1))[:bh,:bw]
    fondo=gen.copy(); al=(cv2.GaussianBlur(md[by:by+bh,bx:bx+bw],(0,0),6).astype(np.float32)/255)[...,None]
    fondo[by:by+bh,bx:bx+bw]=(fondo[by:by+bh,bx:bx+bw]*(1-al)+rep*0.8*al).astype(np.uint8); cv2.imwrite(str(SK/'salidas'/f'vivo-{ESTILO}-fondo.jpg'),fondo)
    off=ref0['c']-libreta(sam,cam,caja)['c'] if False else np.zeros(2); pose={'c':ref0['c'].copy(),'ang':ref0['ang']}
    txt=f"generada en {seg:.1f} s".replace('.',',')
    pcx('remove',{"request_id":"sk-rmt-%d"%time.time(),"ids":["skin-timer"]})
    print(pcx('project',{"request_id":"sk-%d"%time.time(),"widgets":[w_fondo(fondo,txt),w_libreta(rgba,ref0,pose)]})[:100],flush=True)
    (H/'out/skin.json').write_text(json.dumps({"estilo":ESTILO,"segundos":round(seg,1),"modelo":"gemini-3-pro-image","t":time.time()}))
    # Seguimiento de la libreta sin realimentación de la propia proyección:
    #  - quieta: solo se compara contra una foto de referencia (no se corre SAM con la capa encima, que la hacía derivar);
    #  - si algo cambia: se apaga la capa de la libreta, se espera a que quede quieta, se mide con SAM y se vuelve a prender ahí.
    def roi(c,r=260): return cam.norm(c[0]-r,c[1]-r,c[0]+r,c[1]+r,0.5)
    def gris(im): return cv2.GaussianBlur(cv2.cvtColor(im,cv2.COLOR_BGR2GRAY),(0,0),1.5).astype(np.int16)
    NEGRO={"type":"effect","id":"skin-negro","shader":"html","rect_mm":[0,0,1000,563],"params":{"html":"<div></div>","css":"html,body{margin:0;background:#000}"}}
    def medir(c):
        """Medición limpia: un instante de negro sobre todo, foto, y se detecta el papel como en la foto inicial."""
        pcx('project',{"request_id":"sk-n-%d"%(time.time()*10),"widgets":[NEGRO]}); time.sleep(0.45)
        im=cam.norm(X0,Y0,720,560,1.0); pcx('remove',{"request_id":"sk-nr-%d"%(time.time()*10),"ids":["skin-negro"]})
        return papel(im,1.0,np.array([X0,Y0],float),ref0['area'],c)
    pcx('remove',{"request_id":"sk-h0-%d"%time.time(),"ids":["skin-libreta"]}); time.sleep(0.9)
    base=None
    for _ in range(6):
        base=medir(ref0['c'])
        if base: break
        time.sleep(0.4)
    assert base is not None,'no pude medir la libreta para empezar a seguirla'
    centro0=ref0c.copy(); ang0=ref0['ang']; pose={'c':ref0['c'].copy(),'ang':ref0['ang']}; actual=base
    def poner(cur):
        d=cur['c']-centro0; da=((cur['ang']-ang0+90)%180)-90
        pcx('project',{"request_id":"sk-l-%d"%(time.time()*10),"widgets":[w_libreta(rgba,ref0,{'c':ref0['c']+d,'ang':ref0['ang']+da})]}); return d,da
    poner(base); time.sleep(0.8); refg=gris(roi(actual['c'])); print('seguimiento listo: libreta',[round(v) for v in ref0['tam']],'mm · ahora en',base['c'].round(0),flush=True)
    while True:
        g=gris(roi(actual['c'])); cambio=float((np.abs(g-refg)>22).mean())
        if cambio>0.012:
            t=time.time(); pcx('remove',{"request_id":"sk-h-%d"%(time.time()*10),"ids":["skin-libreta"]}); prev=None; quieto=0
            while quieto<2 and time.time()-t<20:
                time.sleep(0.12); gg=gris(roi(actual['c']))
                quieto=quieto+1 if prev is not None and float((np.abs(gg-prev)>18).mean())<0.004 else 0; prev=gg
            cur=None
            for _ in range(8):
                cur=medir(actual['c'])
                if cur: break
                time.sleep(0.3)
            if cur is None: print(time.strftime('%H:%M:%S'),'no encuentro la libreta; la capa queda apagada',flush=True); time.sleep(1); refg=gris(roi(actual['c'])); continue
            actual=cur; d,da=poner(cur); time.sleep(0.7); refg=gris(roi(actual['c']))
            print(time.strftime('%H:%M:%S'),'libreta reubicada: d=(%.0f,%.0f) mm giro %.1f° · %.1f s desde que se movió'%(d[0],d[1],da,time.time()-t),flush=True)
        time.sleep(0.1)

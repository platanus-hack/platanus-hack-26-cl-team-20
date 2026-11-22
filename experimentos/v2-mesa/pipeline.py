"""Pipeline v2 de la mesa (experimento): cámara 4K -> vista normalizada (mm reales) -> detección -> SAM por recorte -> objetos.
Publica cada etapa en out/ para el dashboard (dashboard.html). uso: pipeline.py [una|vivo]"""
import cv2, json, numpy as np, sys, time, threading, urllib.request, subprocess
from pathlib import Path
HERE=Path(__file__).parent; OUT=HERE/'out'; sys.path.insert(0,str(Path.home()/'code/platanus-hack-26/sl-scanner'))
from fp_rect import mapa
import altura
from playground.appearance_prepare import MobileSAM
M=json.load(open(HERE/'fp-metric.json')); T2C=np.array(M['mm_to_canvas']); PROG='--progresivo' in sys.argv; PCX='/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'; PP=2.5; X0,Y0,X1,Y1=-10,-10,1330,700   # zona normalizada (mm): solo la mesa
PROMPTS=["a notebook","a smartphone","a plastic organizer box","a drinking glass","a white plate","a white ball","a white gear","a pill organizer","small colored plastic cubes","a sticker","an arduino board","a usb hub","a cable"]
ES={"a notebook":"libreta","a smartphone":"teléfono","a plastic organizer box":"caja organizadora","a drinking glass":"vaso","a white plate":"plato","a white ball":"bola","a white gear":"engranaje","a pill organizer":"caja de viales","small colored plastic cubes":"cubos de viales","a sticker":"sticker","an arduino board":"arduino","a usb hub":"hub usb","a cable":"cable"}
if '--prompts' in sys.argv:   # --prompts archivo.json: {"prompt en inglés": "etiqueta"}
    ES=json.load(open(sys.argv[sys.argv.index('--prompts')+1])); PROMPTS=list(ES)
estado={"etapa":"iniciando","t":time.time(),"etapas":[],"fps_vivo":0}
def pub(etapa,ms=None,**kw):
    estado.update(etapa=etapa,t=time.time(),**kw)
    if ms is not None: estado["etapas"]=[e for e in estado["etapas"] if e["n"]!=etapa]+[{"n":etapa,"ms":round(ms)}]
    (OUT/'estado.json').write_text(json.dumps(estado,ensure_ascii=False))
def wimg(name,img,q=82):
    tmp=OUT/(name+'.tmp.jpg'); cv2.imwrite(str(tmp),img,[cv2.IMWRITE_JPEG_QUALITY,q]); tmp.replace(OUT/name)
class Foto:
    """Una foto ya tomada (4K) en vez de la cámara en vivo: `--foto ruta`."""
    def __init__(self,ruta): self.f=cv2.imread(ruta); self.n=1
    def get(self): return self.f
class Cam:
    def __init__(self):
        self.c=cv2.VideoCapture(0,cv2.CAP_AVFOUNDATION); self.c.set(cv2.CAP_PROP_FRAME_WIDTH,3840); self.c.set(cv2.CAP_PROP_FRAME_HEIGHT,2160)
        self.f=None; self.n=0; threading.Thread(target=self.loop,daemon=True).start()
        print('cámara:',self.c.get(cv2.CAP_PROP_FRAME_WIDTH),'x',self.c.get(cv2.CAP_PROP_FRAME_HEIGHT),flush=True)
    def loop(self):
        while True:
            ok,f=self.c.read()
            if ok: self.f=f; self.n+=1
            else: time.sleep(0.05)
    def get(self):
        while self.f is None: time.sleep(0.05)
        return self.f
def dino(path,th=0.22):
    req=urllib.request.Request("http://127.0.0.1:8805/detect",data=json.dumps({"image":str(path),"prompts":PROMPTS,"box_threshold":th}).encode(),headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req,timeout=120).read())["detections"]
def iou(a,b):
    x0,y0,x1,y1=max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3]); i=max(0,x1-x0)*max(0,y1-y0); return i/((a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-i+1e-6)
COL=[(255,122,26),(0,229,255),(180,255,60),(255,79,216),(124,156,255),(255,212,0),(255,90,90),(120,255,200)]
_MAPAS={}
def mapas(w):
    """Mapa de rectificación para el ancho real del cuadro (la cámara puede entregar 4K o 1080p según quién más la tenga abierta)."""
    if w not in _MAPAS: _MAPAS[w]=mapa(X0,Y0,X1,Y1,PP,w/1280.0)
    return _MAPAS[w]
def ciclo(cam,sam,mx=None,my=None):
    t0=time.time(); f=cam.get().copy(); mx,my=mapas(f.shape[1]); norm=cv2.remap(f,mx,my,cv2.INTER_LINEAR); t1=time.time(); estado['camara_px']=[f.shape[1],f.shape[0]]
    wimg('1-camara.jpg',cv2.resize(f,(1280,720))); wimg('2-normalizada.jpg',norm); cv2.imwrite(str(OUT/'norm-full.png'),norm)
    pub('normalizar',(t1-t0)*1e3)
    small=cv2.resize(norm,None,fx=0.5,fy=0.5,interpolation=cv2.INTER_AREA); cv2.imwrite(str(OUT/'norm-small.jpg'),small,[cv2.IMWRITE_JPEG_QUALITY,90])
    H,W=norm.shape[:2]; keep=[]; vlm=OUT/('vlm.json' if '--dino' not in sys.argv else 'no-existe.json')
    if vlm.exists():   # el agente (modelo de visión) ya dijo qué hay y dónde: SAM solo dentro de sus cajas
        pub('cajas del agente (modelo de visión)')
        for d in json.loads(vlm.read_text()):
            x0,y0,x1,y1=d["caja_mm"]; keep.append({"box":[(x0-X0)*PP,(y0-Y0)*PP,(x1-X0)*PP,(y1-Y0)*PP],"prompt":d["etiqueta"],"score":1.0,"desc":d.get("descripcion"),"grupo":d.get("grupo")})
        t2=time.time()
    else:
        pub('detectar (Grounding DINO)'); dets=dino(OUT/'norm-small.jpg'); t2=time.time()
        for d in sorted(dets,key=lambda x:-x["score"]):
            b=[v*2 for v in d["box"]]; a=(b[2]-b[0])*(b[3]-b[1])
            if a<900 or a>0.30*H*W: continue
            if any(iou(b,o["box"])>0.45 for o in keep): continue
            if d["score"]<0.30: continue
            keep.append({"box":b,"prompt":d["prompt"],"score":d["score"]})
    dbg=norm.copy()
    for k,o in enumerate(keep): b=[int(v) for v in o["box"]]; cv2.rectangle(dbg,(b[0],b[1]),(b[2],b[3]),COL[k%8],3); cv2.putText(dbg,f'{ES.get(o["prompt"],o["prompt"])} {o["score"]:.2f}',(b[0],max(22,b[1]-6)),0,0.8,COL[k%8],2)
    wimg('3-deteccion.jpg',dbg); pub('cajas del agente (modelo de visión)' if vlm.exists() else 'detectar (Grounding DINO)',(t2-t1)*1e3,n_cajas=len(keep))
    pub('segmentar (SAM por recorte)'); objs=[]; ov=norm.copy(); mask_all=np.zeros((H,W),np.uint8); tiles=[]
    for k,o in enumerate(keep):
        b=o["box"]; bw,bh=b[2]-b[0],b[3]-b[1]; mgn=0.18*max(bw,bh)+12
        cx0,cy0,cx1,cy1=int(max(0,b[0]-mgn)),int(max(0,b[1]-mgn)),int(min(W,b[2]+mgn)),int(min(H,b[3]+mgn))
        crop=norm[cy0:cy1,cx0:cx1]; s=1024.0/max(crop.shape[:2]); big=cv2.resize(crop,None,fx=s,fy=s,interpolation=cv2.INTER_CUBIC if s>1 else cv2.INTER_AREA)
        sam.set_image(big); bb=np.array([(b[0]-cx0)*s,(b[1]-cy0)*s,(b[2]-cx0)*s,(b[3]-cy0)*s],float)
        pos=((bb[0]+bb[2])/2,(bb[1]+bb[3])/2); curvo=o["prompt"]=="a banana"
        if curvo:      # objeto curvo: el centro de su caja puede caer fuera de él; el punto de apoyo es su píxel amarillo más central
            hv=cv2.cvtColor(big,cv2.COLOR_BGR2HSV); am=((hv[...,0]>15)&(hv[...,0]<38)&(hv[...,1]>90)&(hv[...,2]>90)).astype(np.uint8); am[:int(max(bb[1],0))]=0; am[int(bb[3]):]=0; am[:,:int(max(bb[0],0))]=0; am[:,int(bb[2]):]=0
            dt=cv2.distanceTransform(am,cv2.DIST_L2,5)
            if dt.max()>4: yy,xx=np.unravel_index(int(np.argmax(dt)),dt.shape); pos=(float(xx),float(yy))
        mk,_=sam.predict(bb,positive=[pos]); mk=mk.astype(np.uint8)
        mk=cv2.morphologyEx(mk,cv2.MORPH_OPEN,np.ones((5,5),np.uint8)); cs,_=cv2.findContours(mk,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)
        if not cs: continue
        c=max(cs,key=cv2.contourArea)
        if cv2.contourArea(c)<400: continue
        if '--dino' in sys.argv:
            hull=cv2.convexHull(c); sol=cv2.contourArea(c)/(cv2.contourArea(hull)+1e-6); caja_a=max((bb[2]-bb[0])*(bb[3]-bb[1]),1.0); lleno=cv2.contourArea(c)/caja_a
            if sol<(0.45 if curvo else 0.72) or lleno<(0.18 if curvo else 0.30) or lleno>1.6: continue      # dentada, o no llena su caja, o se desbordó: no es un objeto
        cn=(c.reshape(-1,2)/s+[cx0,cy0])                      # px de la vista normalizada
        poly=cv2.approxPolyDP(cn.astype(np.float32).reshape(-1,1,2),0.8,True).reshape(-1,2)
        mm=(poly/PP+[X0,Y0]); area=cv2.contourArea(poly.astype(np.float32))/PP**2
        (ecx,ecy),(ea,eb),ang=cv2.minAreaRect(poly.astype(np.float32))
        col=COL[k%8]; cv2.fillPoly(mask_all,[poly.astype(np.int32)],k+1)
        cv2.polylines(ov,[poly.astype(np.int32)],True,col,3)
        tile=cv2.resize(big,(256,int(256*big.shape[0]/big.shape[1]))); tm=cv2.resize(mk*255,(tile.shape[1],tile.shape[0])); tile[tm>0]=(0.55*tile[tm>0]+0.45*np.array(col)).astype(np.uint8)
        wimg(f'obj-{k}.jpg',tile)
        if PROG:   # prender el contorno en la mesa apenas sale la máscara
            cm=np.c_[mm,np.ones(len(mm))]@T2C.T; cm=(cm[:,:2]/cm[:,2:]).round(1).tolist()
            subprocess.run(['/usr/bin/python3',PCX,'project',json.dumps({"request_id":"prog-%d-%d"%(k,time.time()),"widgets":[{"type":"halo","id":f"v2-{k}","polygon_mm":cm,"color":{"lab":"#00E5FF","solar":"#FFD400","ref":"#FF4FD8"}.get(o.get("grupo"),"#B6FF3C"),"width_mm":2.5,"pulse_hz":0},{"type":"label","id":f"v2-t{k}","text":ES.get(o["prompt"],o["prompt"]),"anchor":{"point_mm":[float(np.mean([p[0] for p in cm])),float(min(p[1] for p in cm))-10]},"size_mm":13,"color":"#FFFFFF"}]})],capture_output=True)
            estado['n_objetos']=len(objs)+1; pub('segmentar (SAM por recorte)')

        objs.append({"id":k,"etiqueta":ES.get(o["prompt"],o["prompt"]),"prompt":o["prompt"],"score":round(o["score"],2),"centro_mm":[round(ecx/PP+X0,1),round(ecy/PP+Y0,1)],"tam_mm":[round(max(ea,eb)/PP,1),round(min(ea,eb)/PP,1)],"area_mm2":round(area),"puntos":len(poly),"poligono_mm":mm.round(1).tolist(),"descripcion":o.get("desc"),"grupo":o.get("grupo")})
        pp_,h_,k_=altura.corregir(mm,ES.get(o["prompt"],o["prompt"]))
        if h_>0: objs[-1].update(poligono_proy_mm=np.round(pp_,1).tolist(),altura_mm=round(h_),k_proyector=round(k_,2))
    try: altura.aplicar_sl(objs)      # encaje medido con luz estructurada, si hay escaneo con relieve
    except Exception as ex: print("sl:",ex)
    t3=time.time()
    tint=ov.copy()
    for k,o in enumerate(keep): tint[mask_all==k+1]=(0.6*tint[mask_all==k+1]+0.4*np.array(COL[k%8])).astype(np.uint8)
    for ob in objs: p=((np.array(ob["centro_mm"])-[X0,Y0])*PP).astype(int); cv2.putText(tint,f'{ob["id"]} {ob["etiqueta"]}',(p[0]-40,p[1]),0,0.75,(255,255,255),4); cv2.putText(tint,f'{ob["id"]} {ob["etiqueta"]}',(p[0]-40,p[1]),0,0.75,(0,0,0),2)
    wimg('4-mascaras.jpg',tint,88); (OUT/'objetos.json').write_text(json.dumps(objs,ensure_ascii=False))
    pub('segmentar (SAM por recorte)',(t3-t2)*1e3,n_objetos=len(objs)); pub('listo',(t3-t0)*1e3,ciclo=estado.get('ciclo',0)+1)
    return norm,objs
if __name__=='__main__':
    modo=sys.argv[1] if len(sys.argv)>1 else 'una'
    pub('abriendo cámara 4K'); cam=Foto(sys.argv[sys.argv.index('--foto')+1]) if '--foto' in sys.argv else Cam(); mx,my=mapa(X0,Y0,X1,Y1,PP,3.0); pub('cargando SAM'); sam=MobileSAM(Path.home()/"hardware/models/slscan-segmentation")
    norm,objs=ciclo(cam,sam,mx,my); print(len(objs),'objetos'); [print(o['id'],o['etiqueta'],o['score'],o['centro_mm'],o['tam_mm'],o['puntos']) for o in objs]
    if modo=='vivo':
        ref=cv2.cvtColor(cv2.resize(norm,None,fx=0.25,fy=0.25),cv2.COLOR_BGR2GRAY).astype(np.int16); quieto=0; n=0; tl=time.time()
        while True:
            f=cam.get(); mx,my=mapas(f.shape[1]); cur=cv2.remap(f,mx,my,cv2.INTER_LINEAR); wimg('vivo.jpg',cv2.resize(cur,None,fx=0.5,fy=0.5),75); n+=1
            if time.time()-tl>2: estado['fps_vivo']=round(n/(time.time()-tl),1); n=0; tl=time.time()
            g=cv2.cvtColor(cv2.resize(cur,None,fx=0.25,fy=0.25),cv2.COLOR_BGR2GRAY).astype(np.int16); d=np.abs(g-ref); cambio=float((d>28).mean())
            estado['cambio']=round(cambio*100,2)
            if cambio>0.004: quieto=0; pub('movimiento: esperando que la mesa quede quieta'); ref_pend=True; prev=g
            else: quieto+=1
            if cambio>0.004 or estado['etapa'].startswith('movimiento'):
                # quieta = dos cuadros seguidos casi iguales entre sí
                if 'prevg' in globals() and float((np.abs(g-prevg)>18).mean())<0.002: quieto2=globals().get('quieto2',0)+1
                else: quieto2=0
                globals()['quieto2']=quieto2
                if quieto2>=3:
                    norm,objs=ciclo(cam,sam,mx,my); ref=cv2.cvtColor(cv2.resize(norm,None,fx=0.25,fy=0.25),cv2.COLOR_BGR2GRAY).astype(np.int16); globals()['quieto2']=0
            globals()['prevg']=g
            (OUT/'estado.json').write_text(json.dumps(estado,ensure_ascii=False)); time.sleep(0.15)

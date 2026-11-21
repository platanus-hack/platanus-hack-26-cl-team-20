"""Contornos reales: foto -> Grounding DINO (servicio local :8805) -> MobileSAM por caja -> contorno en px de cámara -> halo polygon_px."""
import json, subprocess, time, sys, urllib.request, cv2, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path.home()/"code/platanus-hack-26/sl-scanner"))
from playground.appearance_prepare import MobileSAM
sam=MobileSAM(Path.home()/"hardware/models/slscan-segmentation")
P={"a bowl":("bowl","#FF7A1A"),"a coffee mug":("taza","#00E5FF"),"a wicker basket":("canasto","#FFD400"),"a white plastic box":("caja","#B6FF3C"),
   "a drinking glass":("vaso","#7C9CFF"),"a spoon":("cuchara","#FFFFFF"),"a pill organizer":("pastillero","#FF4FD8"),"a white ball":("planeta","#FF5A5A")}
def call(tool,args): return subprocess.run(["/usr/bin/python3","pcx.py",tool,json.dumps(args)],capture_output=True,text=True).stdout
def foto():
    out=call("look",{"camera_id":"cam-2","annotate":False}); p=[l.split(" ",1)[1] for l in out.splitlines() if l.startswith("IMAGE")]
    return (p[0], cv2.imread(p[0])) if p else (None,None)
def dino(path):
    req=urllib.request.Request("http://127.0.0.1:8805/detect",data=json.dumps({"image":path,"prompts":list(P),"box_threshold":float(sys.argv[2]) if len(sys.argv)>2 else 0.3}).encode(),headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req,timeout=60).read())
def iou(a,b):
    x0,y0,x1,y1=max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3]); i=max(0,x1-x0)*max(0,y1-y0)
    return i/((a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-i+1e-6)
t0=time.time(); c=0; prev=[]; previd=[]
while time.time()-t0<float(sys.argv[1]):
    c+=1; path,img=foto()
    if img is None: print("sin cuadro",flush=True); time.sleep(2); continue
    try: d=dino(path)
    except Exception as e: print("error dino: "+str(e)[:100],flush=True); time.sleep(3); continue
    H,Wd=img.shape[:2]; dets=[]; MESA=np.int32([[771,640],[300,560],[330,290],[820,370]])
    for det in sorted(d["detections"],key=lambda x:-x["score"]):
        b=det["box"]; a=(b[2]-b[0])*(b[3]-b[1])
        if a<500 or a>0.12*H*Wd or b[0]>820 and b[1]<420: continue          # muy chico, muy grande, o la lámpara
        if any(iou(b,o["box"])>0.5 for o in dets): continue
        if cv2.pointPolygonTest(MESA,((b[0]+b[2])/2,(b[1]+b[3])/2),False)<0: continue
        dets.append(det)
    sam.set_image(img); W=[]; ids=[]; res=[]
    for k,det in enumerate(dets[:12]):
        b=det["box"]; m,info=sam.predict(np.array(b,float),positive=[((b[0]+b[2])/2,(b[1]+b[3])/2)])
        m=cv2.morphologyEx(m.astype(np.uint8),cv2.MORPH_OPEN,np.ones((3,3),np.uint8)); cs,_=cv2.findContours(m,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        if not cs: continue
        cc=max(cs,key=cv2.contourArea)
        if cv2.contourArea(cc)<300: continue
        poly=cv2.approxPolyDP(cc,1.5,True).reshape(-1,2).astype(float).tolist()
        name,col=P.get(det["prompt"],(det["prompt"],"#FFFFFF")); cx=sum(p[0] for p in poly)/len(poly); top=min(p[1] for p in poly)
        W.append({"type":"halo","id":f"s{k}","polygon_px":poly,"camera_id":"cam-2","color":col,"width_mm":4,"pulse_hz":0}); ids.append(f"s{k}"); res.append(f"{name}({det['score']:.2f})")
    quitar=[i for i in previd if i not in ids]
    if W: r=call("project",{"request_id":f"sam-{c}-{time.time():.0f}","widgets":W})
    if quitar: call("remove",{"request_id":f"samrm-{c}-{time.time():.0f}","ids":quitar})
    if c==1:
        print("primera pasada: "+", ".join(res)+f" | dino {d.get('seconds')}s | "+(r[:160] if W else ""),flush=True); cv2.imwrite("sam-debug.jpg",cv2.polylines(img.copy(),[np.int32(w["polygon_px"]) for w in W],True,(0,255,0),2))
    elif sorted(x.split("(")[0] for x in res)!=sorted(prev): print(f"ciclo {c}: "+", ".join(res),flush=True)
    prev=[x.split("(")[0] for x in res]; previd=ids
print("fin",flush=True)

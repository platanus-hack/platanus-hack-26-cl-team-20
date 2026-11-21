"""Sigue objetos registrados por el agente (caja en mm sobre top-ref.png) con template matching en la vista cenital."""
import json, subprocess, time, cv2, numpy as np, sys, math
H=np.load("H.npy")
OBJ=[("maní",(75,270,300,500),"#FF7A1A","circ"),("taza",(360,170,485,298),"#00E5FF","circ"),("canasto",(198,4,468,168),"#FFD400","rect"),
     ("caja",(662,338,872,560),"#B6FF3C","rect")]
def call(tool,args): return subprocess.run(["/usr/bin/python3","pcx.py",tool,json.dumps(args)],capture_output=True,text=True).stdout
def top():
    out=call("look",{"camera_id":"cam-2"}); p=[l.split(" ",1)[1] for l in out.splitlines() if l.startswith("IMAGE")]
    if not p: return None
    return cv2.cvtColor(cv2.warpPerspective(cv2.imread(p[0]),H,(1000,563)),cv2.COLOR_BGR2GRAY)
ref=cv2.cvtColor(cv2.imread("top-ref.png"),cv2.COLOR_BGR2GRAY)
st=[{"name":n,"tpl":ref[y0:y1,x0:x1].copy(),"pos":(x0,y0),"w":x1-x0,"h":y1-y0,"col":c,"shape":s,"ok":True} for n,(x0,y0,x1,y1),c,s in OBJ]
def busca(img,o,margen):
    x,y=o["pos"]; x0=max(0,x-margen); y0=max(0,y-margen); x1=min(1000,x+o["w"]+margen); y1=min(563,y+o["h"]+margen)
    r=cv2.matchTemplate(img[y0:y1,x0:x1],o["tpl"],cv2.TM_CCOEFF_NORMED); _,s,_,loc=cv2.minMaxLoc(r); return s,(x0+loc[0],y0+loc[1])
def widgets(o,i):
    x,y=o["pos"]; w,h=o["w"],o["h"]; cx,cy=x+w/2,y+h/2; m=14
    if o["shape"]=="circ":
        r=max(w,h)/2+m; poly=[[round(cx+r*math.cos(2*math.pi*k/36),1),round(cy+r*math.sin(2*math.pi*k/36),1)] for k in range(36)]
    else: poly=[[x-m,y-m],[x+w+m,y-m],[x+w+m,y+h+m],[x-m,y+h+m]]
    big=w>60
    return [{"type":"halo","id":f"o{i}","polygon_mm":poly,"color":o["col"],"width_mm":6 if big else 4,"pulse_hz":0},
            {"type":"label","id":f"n{i}","anchor":{"point_mm":[cx+(0 if big else w/2+70),cy-(h/2+m+26 if big else 0)]},"text":o["name"],"size_mm":26 if big else 20,"color":o["col"]}]
t0=time.time(); c=0
while time.time()-t0<float(sys.argv[1]):
    c+=1; img=top()
    if img is None: print("sin cuadro",flush=True); time.sleep(2); continue
    W=[]; quitar=[]; ev=[]
    for i,o in enumerate(st):
        s,p=busca(img,o,140)
        if s<0.6:
            s2,p2=busca(img,o,1000)
            if s2>s: s,p=s2,p2
        if s>=0.55:
            if math.dist(p,o["pos"])>18: ev.append(f"{o['name']} se movió a ({int(p[0]+o['w']/2)},{int(p[1]+o['h']/2)}) mm")
            if not o["ok"]: ev.append(f"{o['name']} reapareció")
            o["pos"]=p; o["ok"]=True; W+=widgets(o,i)
        else:
            if o["ok"]: ev.append(f"{o['name']} perdido (score {s:.2f})")
            o["ok"]=False; quitar+=[f"o{i}",f"n{i}"]
    if W: call("project",{"request_id":f"sg-{c}-{time.time():.0f}","widgets":W})
    if quitar: call("remove",{"request_id":f"rq-{c}-{time.time():.0f}","ids":quitar})
    if c==1: print("inicio: "+", ".join(f"{o['name']}={'ok' if o['ok'] else 'no'}" for o in st),flush=True)
    for e in ev: print(e,flush=True)
print("fin",flush=True)

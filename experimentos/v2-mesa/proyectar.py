"""Proyecta sobre la mesa los contornos de out/objetos.json, en mm reales -> canvas del proyector."""
import json, numpy as np, subprocess, sys, time
S='/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/pcxl.py'
H=np.array(json.load(open('fp-metric.json'))['mm_to_canvas'])
def c(p): q=np.c_[np.asarray(p,float),np.ones(len(p))]@H.T; return (q[:,:2]/q[:,2:]).round(1).tolist()
COL={"lab":"#00E5FF","solar":"#FFD400","ref":"#FF4FD8","otro":"#B6FF3C"}
W=[]
for o in json.load(open('out/objetos.json')):
    if o["area_mm2"]>90000: continue
    W.append({"type":"halo","id":f"v2-{o['id']}","polygon_mm":c(o["poligono_mm"]),"color":COL.get(o.get("grupo"),"#FFFFFF"),"width_mm":2.5,"pulse_hz":0})
r=subprocess.run(["/usr/bin/python3",S,"project",json.dumps({"request_id":"v2-%d"%time.time(),"widgets":W})],capture_output=True,text=True).stdout; print(len(W),r[:90])

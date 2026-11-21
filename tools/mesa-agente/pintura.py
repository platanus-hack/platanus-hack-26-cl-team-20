"""Pintura viva: foto limpia -> DINO -> MobileSAM -> un solo effect html (SVG en mm) con rellenos por objeto, grilla viva y cartel."""
import json, subprocess, time, sys, urllib.request, cv2, numpy as np, random, math
from pathlib import Path
sys.path.insert(0, str(Path.home()/"code/platanus-hack-26/sl-scanner"))
from playground.appearance_prepare import MobileSAM
sam=MobileSAM(Path.home()/"hardware/models/slscan-segmentation"); Hm=np.load("H.npy")
DY=float(sys.argv[2]) if len(sys.argv)>2 else -18.0
P={"a bowl":"bowl","a coffee mug":"taza","a wicker basket":"canasto","a white plastic box":"caja","a drinking glass":"vaso","a spoon":"cuchara","a pill organizer":"pastillero","a white ball":"planeta","a white foam sphere":"planeta"}
PLANETAS=[("#ffb347","#c1440e"),("#6fb1ff","#1b3a8a"),("#e8d9a8","#b08d57"),("#ff6b4a","#7a1f0e"),("#9be7ff","#2a7f9e"),("#d9c2ff","#5b3fa8"),("#ffe27a","#c98a00"),("#a8ffb0","#1f7a3a")]
def call(tool,args): return subprocess.run(["/usr/bin/python3","pcx.py",tool,json.dumps(args)],capture_output=True,text=True).stdout
def mm(pts):
    p=cv2.perspectiveTransform(np.float32(pts).reshape(-1,1,2),Hm).reshape(-1,2); p[:,1]+=DY; return p
def foto():
    out=call("look",{"camera_id":"cam-2","annotate":False}); p=[l.split(" ",1)[1] for l in out.splitlines() if l.startswith("IMAGE")]
    return (p[0], cv2.imread(p[0])) if p else (None,None)
def dino(path):
    req=urllib.request.Request("http://127.0.0.1:8805/detect",data=json.dumps({"image":path,"prompts":list(P),"box_threshold":0.24}).encode(),headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req,timeout=60).read())["detections"]
def iou(a,b):
    x0,y0,x1,y1=max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3]); i=max(0,x1-x0)*max(0,y1-y0)
    return i/((a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-i+1e-6)
MESA=np.int32([[817,669],[248,579],[310,250],[863,357]])
CSS="""svg{position:absolute;inset:0;width:100%;height:100%}
@keyframes vida{0%,100%{opacity:.05}50%{opacity:.95}} .cel{animation:vida 2.4s infinite}
@keyframes gira{to{transform:rotate(360deg)}} .gira{animation:gira 9s linear infinite;transform-box:fill-box;transform-origin:center}
@keyframes late{0%,100%{stroke-width:2;opacity:.6}50%{stroke-width:6;opacity:1}} .late{animation:late 1.6s infinite}
@keyframes corre{from{transform:translateX(1000px)}to{transform:translateX(-1400px)}} .corre{animation:corre 16s linear infinite}
@keyframes raya{to{stroke-dashoffset:-60}} .raya{stroke-dasharray:14 10;animation:raya 1.2s linear infinite}"""
def escena(objs,Hs):
    s=[f'<svg viewBox="0 0 1000 {Hs}" xmlns="http://www.w3.org/2000/svg"><defs>']
    for i,(a,b) in enumerate(PLANETAS): s.append(f'<radialGradient id="g{i}" cx="35%" cy="35%"><stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></radialGradient>')
    s.append('</defs>'); np_=0; nombres=[]
    for o in objs:
        pts=" ".join(f"{x:.0f},{y:.0f}" for x,y in o["mm"]); n=o["name"]; nombres.append(n); (cx,cy),r=cv2.minEnclosingCircle(np.float32(o["mm"]))
        if n=="planeta":
            g=np_%len(PLANETAS); np_+=1
            s.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r*0.92:.0f}" fill="url(#g{g})"/>')
            if g==2: s.append(f'<ellipse cx="{cx:.0f}" cy="{cy:.0f}" rx="{r*1.7:.0f}" ry="{r*0.45:.0f}" fill="none" stroke="#e8d9a8" stroke-width="3"/>')
        elif n=="pastillero":
            (rx,ry),(rw,rh),ang=cv2.minAreaRect(np.float32(o["mm"]))
            if rw<rh: rw,rh,ang=rh,rw,ang+90
            cols,rows=7,4; cw,ch=rw/cols,rh/rows; s.append(f'<g transform="translate({rx:.0f} {ry:.0f}) rotate({ang:.0f})">')
            for j in range(rows):
                for i in range(cols):
                    hue=(i*360/cols+j*18)%360
                    s.append(f'<rect class="cel" style="animation-delay:{random.random()*2.4:.2f}s" x="{-rw/2+i*cw+1.5:.1f}" y="{-rh/2+j*ch+1.5:.1f}" width="{cw-3:.1f}" height="{ch-3:.1f}" rx="3" fill="hsl({hue:.0f} 95% 60%)"/>')
            s.append(f'<rect x="{-rw/2:.0f}" y="{-rh/2:.0f}" width="{rw:.0f}" height="{rh:.0f}" fill="none" stroke="#fff" stroke-width="2"/></g>')
        elif n=="taza":
            s.append(f'<polygon points="{pts}" fill="#00E5FF" fill-opacity=".25" stroke="#00E5FF" class="late"/>')
            s.append(f'<circle class="gira raya" cx="{cx:.0f}" cy="{cy:.0f}" r="{r+16:.0f}" fill="none" stroke="#00E5FF" stroke-width="3"/>')
            s.append(f'<text transform="translate({cx:.0f} {cy-r-30:.0f}) rotate(180)" text-anchor="middle" font-size="24" font-weight="700" fill="#00E5FF" font-family="system-ui">café ☕ 62°</text>')
        elif n=="bowl":
            s.append(f'<polygon points="{pts}" fill="#FF7A1A" fill-opacity=".18" stroke="#FF7A1A" class="late"/>')
            for k in range(3): s.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r+14+k*14:.0f}" fill="none" stroke="#FF7A1A" stroke-width="2" class="cel" style="animation-delay:{k*0.5}s"/>')
            s.append(f'<text transform="translate({cx:.0f} {cy-r-62:.0f}) rotate(180)" text-anchor="middle" font-size="24" font-weight="700" fill="#FF7A1A" font-family="system-ui">maní</text>')
        elif n=="canasto":
            s.append(f'<polygon points="{pts}" fill="#FFD400" fill-opacity=".10" stroke="#FFD400" stroke-width="4" class="raya"/>')
        elif n=="caja":
            s.append(f'<polygon points="{pts}" fill="#B6FF3C" fill-opacity=".22" stroke="#B6FF3C" stroke-width="4" class="late"/>')
        elif n=="vaso":
            s.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r*0.8:.0f}" fill="#7C9CFF" fill-opacity=".45" class="cel"/><polygon points="{pts}" fill="none" stroke="#7C9CFF" stroke-width="3"/>')
        else:
            s.append(f'<polygon points="{pts}" fill="#fff" fill-opacity=".35" stroke="#fff" stroke-width="2" class="late"/>')
    s.append(f'<g transform="translate(0 {Hs-34:.0f})"><g class="corre"><text transform="rotate(180 700 0)" x="700" y="0" text-anchor="middle" font-size="40" font-weight="800" fill="#FF7A1A" font-family="system-ui" letter-spacing="6">EQUIPO 20 · pcx · los agentes ya tienen pinceles · EQUIPO 20</text></g></g></svg>')
    return "".join(s), nombres
t0=time.time(); c=0; visible=False
while time.time()-t0<float(sys.argv[1]):
    c+=1
    if visible: call("remove",{"request_id":f"pin-rm-{c}-{time.time():.0f}","ids":["pintura"]}); time.sleep(0.7)
    path,img=foto()
    if visible: call("project",last)            # vuelve la pintura anterior mientras se calcula la nueva
    if img is None: print("sin cuadro",flush=True); time.sleep(2); continue
    try: D=dino(path)
    except Exception as e: print("error dino: "+str(e)[:100],flush=True); time.sleep(3); continue
    dets=[]
    for det in sorted(D,key=lambda x:-x["score"]):
        b=det["box"]; a=(b[2]-b[0])*(b[3]-b[1])
        if a<350 or a>0.12*img.shape[0]*img.shape[1]: continue
        if any(iou(b,o["box"])>0.5 for o in dets): continue
        if cv2.pointPolygonTest(MESA,((b[0]+b[2])/2,(b[1]+b[3])/2),False)<0: continue
        if det["prompt"]=="a pill organizer" and any(o["prompt"]=="a pill organizer" for o in dets): continue
        dets.append(det)
    sam.set_image(img); objs=[]
    for det in dets[:16]:
        b=det["box"]; m,_=sam.predict(np.array(b,float),positive=[((b[0]+b[2])/2,(b[1]+b[3])/2)])
        m=cv2.morphologyEx(m.astype(np.uint8),cv2.MORPH_OPEN,np.ones((3,3),np.uint8)); cs,_=cv2.findContours(m,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        if not cs: continue
        cc=max(cs,key=cv2.contourArea)
        if cv2.contourArea(cc)<200: continue
        objs.append({"name":P[det["prompt"]],"mm":mm(cv2.approxPolyDP(cc,1.5,True).reshape(-1,2)).tolist()})
    sc=json.loads(call("scene",{})); Hs=sc["surface"]["height_mm"]; html,nombres=escena(objs,Hs)
    last={"request_id":f"pin-{c}-{time.time():.0f}","widgets":[{"type":"effect","id":"pintura","shader":"html","rect_mm":[0,0,1000,Hs],"z":-5,"params":{"html":html,"css":CSS}}]}
    r=call("project",last); visible=True
    if c==1: print(f"primera pasada (superficie {Hs} mm, dy {DY}): "+", ".join(nombres)+" | "+r[:70],flush=True)
    time.sleep(float(sys.argv[3]) if len(sys.argv)>3 else 6)
print("fin",flush=True)

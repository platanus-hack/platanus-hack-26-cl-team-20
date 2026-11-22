"""Calibración de lente desde afuera: proyecta una grilla densa de puntos verdes, los ubica por diferencia y ajusta f + k1,k2 con una vista plana."""
import json, subprocess, time, cv2, numpy as np, sys
def call(tool,args): return subprocess.run(["/usr/bin/python3","pcx.py",tool,json.dumps(args)],capture_output=True,text=True).stdout
def foto():
    out=call("look",{"camera_id":"cam-2","annotate":False}); p=[l.split(" ",1)[1] for l in out.splitlines() if l.startswith("IMAGE")]
    return cv2.imread(p[0]).astype(np.float32)
Hq=np.array(json.loads(sys.argv[1])).reshape(3,3)                      # px -> mm (calibrate quick)
XS=list(range(50,960,60)); YS=list(range(40,540,60)); R=7
dots="".join(f'<circle cx="{x}" cy="{y}" r="{R}" fill="#00ff00"/>' for y in YS for x in XS)
W={"type":"effect","id":"grilla","shader":"html","rect_mm":[0,0,1000,563],"z":50,"params":{"html":f'<svg viewBox="0 0 1000 563" xmlns="http://www.w3.org/2000/svg" style="position:absolute;inset:0;width:100%;height:100%;background:#000">{dots}</svg>',"css":""}}
def verde(im): return im[:,:,1]-(im[:,:,2]+im[:,:,0])/2
acc=None
for k in range(3):
    call("project",{"request_id":f"gr-on-{k}-{time.time():.0f}","widgets":[W]}); time.sleep(1.2); on=verde(foto())
    call("remove",{"request_id":f"gr-off-{k}-{time.time():.0f}","ids":["grilla"]}); time.sleep(1.2); off=verde(foto())
    d=on-off; acc=d if acc is None else acc+d
d=cv2.GaussianBlur(acc/3,(5,5),0); cv2.imwrite("lente-diff.png",np.clip(d*4,0,255).astype(np.uint8))
th=max(12,float(np.percentile(d,99.5))*0.5); m=(d>th).astype(np.uint8); n,lab,st,cen=cv2.connectedComponentsWithStats(m)
blobs=[cen[i] for i in range(1,n) if 6<=st[i,cv2.CC_STAT_AREA]<=600]
Hinv=np.linalg.inv(Hq); obj=[]; img=[]
for y in YS:
    for x in XS:
        p=Hinv@[x,y,1]; p=p[:2]/p[2]
        if not blobs: continue
        j=min(range(len(blobs)),key=lambda i: np.hypot(*(blobs[i]-p)))
        if np.hypot(*(blobs[j]-p))<22: obj.append([x,y,0]); img.append(blobs[j])
obj=np.float32(obj); img=np.float32(img); print(f"puntos: {len(XS)*len(YS)} proyectados, {len(blobs)} manchas, {len(obj)} emparejados (umbral {th:.0f})")
w,h=1280,720
def homo_res(ip,op):
    Hh,_=cv2.findHomography(ip,op[:,:2],0); pr=cv2.perspectiveTransform(ip.reshape(-1,1,2),Hh).reshape(-1,2); return np.hypot(*(pr-op[:,:2]).T),Hh
keep=np.ones(len(obj),bool)
for it in range(4):                                                   # saca puntos que cayeron sobre objetos altos
    K0=np.array([[1000,0,w/2],[0,1000,h/2],[0,0,1]],float)
    fl=cv2.CALIB_USE_INTRINSIC_GUESS|cv2.CALIB_FIX_PRINCIPAL_POINT|cv2.CALIB_FIX_ASPECT_RATIO|cv2.CALIB_ZERO_TANGENT_DIST|cv2.CALIB_FIX_K3
    rms,K,dist,rv,tv=cv2.calibrateCamera([obj[keep]],[img[keep]],(w,h),K0,None,flags=fl)
    pr,_=cv2.projectPoints(obj,rv[0],tv[0],K,dist); e=np.hypot(*(pr.reshape(-1,2)-img).T)
    nk=e<max(2.5,3*np.median(e[keep]))
    if nk.sum()==keep.sum(): break
    keep=nk
und=cv2.undistortPoints(img[keep].reshape(-1,1,2),K,dist,P=K).reshape(-1,2)
e_d,_=homo_res(und,obj[keep]); e_h,_=homo_res(img[keep],obj[keep])
f=K[0,0]; fov_h=2*np.degrees(np.arctan(w/2/f)); fov_v=2*np.degrees(np.arctan(h/2/f)); fov_d=2*np.degrees(np.arctan(np.hypot(w,h)/2/f))
out={"puntos_usados":int(keep.sum()),"descartados":int((~keep).sum()),"f_px":round(float(f),1),"fov_h":round(fov_h,1),"fov_v":round(fov_v,1),"fov_diag":round(fov_d,1),
 "k1":round(float(dist[0,0]),4),"k2":round(float(dist[0,1]),4),"rms_reproy_px":round(float(rms),2),
 "mm_solo_homografia":{"mediana":round(float(np.median(e_h)),1),"p95":round(float(np.percentile(e_h,95)),1),"max":round(float(e_h.max()),1)},
 "mm_con_distorsion":{"mediana":round(float(np.median(e_d)),1),"p95":round(float(np.percentile(e_d,95)),1),"max":round(float(e_d.max()),1)},"imagen":[w,h]}
print(json.dumps(out)); np.savez("lente.npz",K=K,dist=dist,obj=obj[keep],img=img[keep])

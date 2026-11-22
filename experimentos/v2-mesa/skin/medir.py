"""Mide que tan 'calco' es cada salida respecto de entrada-norm.png. Todo se trabaja a 1 px = 1 mm (1340x710).
uso: medir.py salidas/a.png [salidas/b.png ...]  -> metricas.json + comparaciones/<nombre>-{bordes,lado}.jpg
Metricas:
 - recall/precision/F1 de bordes Canny con tolerancia 3 mm; chamfer (mm) entrada->salida y salida->entrada
 - control: mismas metricas con la salida desplazada 15 mm (si no empeoran, la metrica no discrimina)
 - global: afin ECC entre mapas de bordes suavizados (escala y traslacion en mm)
 - por objeto: desplazamiento (mm) que mejor calza los bordes del objeto en una ventana de +-40 mm (NCC).
   Solo es confiable si ncc>=0.3 y el pico no cae en el borde de la ventana; mediana/max usan solo los confiables."""
import cv2, json, numpy as np, sys
from pathlib import Path
D=Path(__file__).resolve().parent; W,H=1340,710; TOL=3
# ROIs a mano sobre entrada-norm.png, en px de 1 mm (mm = px-10). objetos.json NO sirve hoy (ver LEEME).
ROI={"libreta":(55,95,375,340),"telefono":(400,175,495,350),"viales azules":(545,205,640,380),"caja organizadora":(700,0,1150,445),
     "vaso":(1095,230,1300,435),"plato 1 + engranaje":(15,415,295,655),"plato 2":(290,435,528,685),"plato 3":(530,450,775,695),
     "caja de viales":(768,425,985,552),"tapa caja viales":(768,548,978,695)}
def prep(p):
    im=cv2.imread(str(p)); im=cv2.resize(im,(W,H),interpolation=cv2.INTER_AREA); g=cv2.GaussianBlur(cv2.cvtColor(im,cv2.COLOR_BGR2GRAY),(0,0),1.5)
    e=cv2.Canny(g,40,110); return im,e
def soft(e): return cv2.GaussianBlur(e.astype(np.float32)/255,(0,0),3)
def edge_metrics(a,b):
    k=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*TOL+1,2*TOL+1)); A,B=a>0,b>0
    rec=(A&(cv2.dilate(b,k)>0)).sum()/max(A.sum(),1); pre=(B&(cv2.dilate(a,k)>0)).sum()/max(B.sum(),1)
    da=cv2.distanceTransform(255-a,cv2.DIST_L2,3); db=cv2.distanceTransform(255-b,cv2.DIST_L2,3)
    return dict(recall=round(float(rec),3),precision=round(float(pre),3),f1=round(float(2*rec*pre/(rec+pre+1e-9)),3),
                chamfer_in_a_out_mm=round(float(db[A].mean()),2),chamfer_out_a_in_mm=round(float(da[B].mean()),2))
im0,e0=prep(D/"entrada-norm.png"); s0=soft(e0); (D/"comparaciones").mkdir(exist_ok=True); res={}
for p in sys.argv[1:]:
    p=Path(p); im,e=prep(p); s=soft(e); r=edge_metrics(e0,e)
    r["control_desplazada_15mm"]=edge_metrics(e0,np.roll(e,15,1))
    try:
        _,M=cv2.findTransformECC(s0,s,np.eye(2,3,dtype=np.float32),cv2.MOTION_AFFINE,(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT,300,1e-6),None,5)
        r["global_afin"]=dict(escala_x=round(float(np.hypot(M[0,0],M[1,0])),4),escala_y=round(float(np.hypot(M[0,1],M[1,1])),4),
                              rot_grados=round(float(np.degrees(np.arctan2(M[1,0],M[0,0]))),2),tx_mm=round(float(M[0,2]),1),ty_mm=round(float(M[1,2]),1))
    except cv2.error as ex: r["global_afin"]="ECC no convergio"
    objs={}; S=40; sp=cv2.copyMakeBorder(s,S,S,S,S,cv2.BORDER_CONSTANT,value=0)
    for n,(x0,y0,x1,y1) in ROI.items():
        y1=min(y1,H); x1=min(x1,W); t=s0[y0:y1,x0:x1]; win=sp[y0:y1+2*S,x0:x1+2*S]
        c=cv2.matchTemplate(win,t,cv2.TM_CCOEFF_NORMED); _,mx,_,loc=cv2.minMaxLoc(c)
        objs[n]=dict(dx_mm=loc[0]-S,dy_mm=loc[1]-S,ncc=round(float(mx),3),ncc_sin_mover=round(float(c[S,S]),3),confiable=bool(mx>=0.3 and abs(loc[0]-S)<S and abs(loc[1]-S)<S))
    r["objetos"]=objs; d=[np.hypot(o["dx_mm"],o["dy_mm"]) for o in objs.values() if o["confiable"]] or [float("nan")]
    r["desplazamiento_objetos_mm"]=dict(n_confiables=sum(o["confiable"] for o in objs.values()),mediana=round(float(np.median(d)),1),max=round(float(max(d)),1)); res[p.name]=r
    ov=im.copy(); ov[e0>0]=(0,255,0)
    for n,(x0,y0,x1,y1) in ROI.items(): cv2.rectangle(ov,(x0,y0),(x1,y1),(0,200,255),1)
    cv2.imwrite(str(D/"comparaciones"/(p.stem+"-bordes.jpg")),ov,[cv2.IMWRITE_JPEG_QUALITY,88])
    cv2.imwrite(str(D/"comparaciones"/(p.stem+"-lado.jpg")),np.vstack([im0,im]),[cv2.IMWRITE_JPEG_QUALITY,85])
    print(f"{p.name:22s} F1={r['f1']:.3f} rec={r['recall']:.3f} (ctrl {r['control_desplazada_15mm']['recall']:.3f}) chamfer in->out={r['chamfer_in_a_out_mm']}mm  global={r['global_afin']}  obj mediana={r['desplazamiento_objetos_mm']['mediana']}mm max={r['desplazamiento_objetos_mm']['max']}mm")
    for n,o in objs.items(): print(f"    {n:22s} dx={o['dx_mm']:+4d} dy={o['dy_mm']:+4d} mm  ncc={o['ncc']:.2f} (sin mover {o['ncc_sin_mover']:.2f}){'' if o['confiable'] else '  [no confiable: la silueta cambio demasiado o el pico es espurio]'}")
old=json.loads((D/"metricas.json").read_text()) if (D/"metricas.json").exists() else {}
old.update(res); (D/"metricas.json").write_text(json.dumps(old,indent=1,ensure_ascii=False))

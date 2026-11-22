"""Skill general: objeto segmentado -> modelo 3D paramétrico en mm reales (marco de la mesa).
Entrada: out/objetos.json (huella en mm), relieve de luz estructurada (fp-parallax.npz) y centro de cámara.
Primitivas: esfera, disco, cilindro, prisma (huella extruida). La altura sale, en orden: de la forma (esfera: 2r),
de la perspectiva (lo alto se «acuesta» alejándose de la cámara), o de un valor típico de la clase (marcado como supuesto)."""
import json, numpy as np, cv2
CC=np.array([563.,857.,785.])     # centro óptico de la cámara (mm), de la homografía con f≈669 px@720p: aproximado
ALT={"iPhone XR":8.3,"libreta":12,"caja de viales":28,"tapa de la caja de viales":12,"viales azules":25,"viales morados":25,"caja organizadora":45,"sticker PH26":0.3,"sticker PH26 amarillo":0.3,"engranaje (Sol)":25}
def relieve():
    Z=np.load('fp-parallax.npz'); A=Z['A']; d=np.linalg.norm(Z['B']-A,axis=1); return A,d-np.median(d)
def modelar(o,A=None,rel=None):
    poly=np.array(o['poligono_mm'],np.float32); e=o['etiqueta']; m={"id":o['id'],"etiqueta":e,"grupo":o.get('grupo'),"huella_mm":o['poligono_mm']}
    (ex,ey),(ea,eb),ang=cv2.fitEllipse(poly) if len(poly)>=5 else ((0,0),(0,0),0); area=cv2.contourArea(poly); circ=4*np.pi*area/(cv2.arcLength(poly,True)**2+1e-6)
    if A is not None:
        ins=cv2.pointPolygonTest(poly,(0,0),False); bx,by,bw,bh=cv2.boundingRect(poly); sel=(A[:,0]>bx)&(A[:,0]<bx+bw)&(A[:,1]>by)&(A[:,1]<by+bh)
        m["relieve_mm"]=round(float(np.percentile(rel[sel],80)),1) if sel.sum()>30 else None; m["puntos_sl"]=int(sel.sum())
    if e.startswith('bola'):
        rs=min(ea,eb)/2; r=rs
        for _ in range(6): cz=r+4; r=rs*(CC[2]-cz)/CC[2]
        cxy=CC[:2]+(np.array([ex,ey])-CC[:2])*(CC[2]-cz)/CC[2]
        m.update(tipo="esfera",centro=[round(float(cxy[0]),1),round(float(cxy[1]),1),round(float(cz),1)],radio=round(float(r),1),altura=round(float(cz+r),1),altura_fuente="forma (esfera sobre el plato)")
    elif e.startswith('plato'):
        m.update(tipo="disco",centro=[round(ex,1),round(ey,1),6.0],radio=round((ea+eb)/4,1),altura=12.0,altura_fuente="supuesto de clase")
    elif e=='vaso':
        # eje del vaso = dirección que se aleja de la cámara; base = extremo más cercano a la cámara
        ax=np.array([np.cos(np.radians(ang-90)),np.sin(np.radians(ang-90))]); t=(poly-[ex,ey])@ax; lado=np.sign((np.array([ex,ey])-CC[:2])@ax) or 1
        ancho=min(ea,eb); L=t.max()-t.min(); base=np.array([ex,ey])-lado*ax*(L/2-ancho*0.33); D=np.linalg.norm(base-CC[:2])
        rb=ancho*0.34; disp=L-(rb+ancho/2); h=disp*CC[2]/(D+disp)
        m.update(tipo="cilindro",centro=[round(float(base[0]),1),round(float(base[1]),1),0],radio=round(float(rb),1),altura=round(float(h),1),altura_fuente="perspectiva (largo de la silueta acostada)",eje=[round(float(lado*ax[0]),3),round(float(lado*ax[1]),3)])
    else:
        h=ALT.get(e); m.update(tipo="prisma",altura=h if h is not None else 20,altura_fuente="supuesto de clase" if h is not None else "sin dato: 20 mm por defecto",centro=[round(ex,1),round(ey,1),0])
    return m
if __name__=='__main__':
    A,rel=relieve(); objs=json.load(open('out/objetos.json')); M=[modelar(o,A,rel) for o in objs]
    json.dump({"camara":CC.tolist(),"modelos":M},open('out/modelos.json','w'),ensure_ascii=False)
    for m in M: print(m['id'],m['etiqueta'],m['tipo'],'altura',m['altura'],'·',m['altura_fuente'],'· relieve',m.get('relieve_mm'), '· radio' if 'radio' in m else '', m.get('radio',''))

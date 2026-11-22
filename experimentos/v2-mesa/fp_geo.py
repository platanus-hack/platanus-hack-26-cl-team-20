"""Geometría real de la mesa desde una foto 4K: bordes del tablero izquierdo (rectángulo) + iPhone XR (escala)."""
import cv2, numpy as np, json, sys
im=cv2.imread(sys.argv[1] if len(sys.argv)>1 else 'hires-0.jpg'); g=cv2.GaussianBlur(cv2.cvtColor(im,cv2.COLOR_BGR2GRAY),(0,0),2).astype(np.float32)
# esquinas aproximadas del tablero izquierdo en 4K (a ojo, se refinan con los bordes): TL, TR, BR, BL
A=np.array([[564,246],[3204,216],[3735,1470],[126,1545]],float)   # mesa final (una sola mesa, completa en cuadro)
def borde(p,q,n=60,r=45):
    d=(q-p)/np.linalg.norm(q-p); nrm=np.array([-d[1],d[0]]); pts=[]
    for t in np.linspace(0.12,0.88,n):
        c=p+(q-p)*t; s=np.arange(-r,r+1)
        xs=c[0]+nrm[0]*s; ys=c[1]+nrm[1]*s
        ok=(xs>=1)&(xs<g.shape[1]-1)&(ys>=1)&(ys<g.shape[0]-1)
        if ok.sum()<len(s)*0.8: continue
        v=cv2.remap(g,xs[ok].astype(np.float32).reshape(1,-1),ys[ok].astype(np.float32).reshape(1,-1),cv2.INTER_LINEAR)[0]
        gr=np.abs(np.gradient(v)); k=int(np.argmax(gr))
        if gr[k]>4: pts.append([xs[ok][k],ys[ok][k]])
    pts=np.array(pts,np.float32)
    for _ in range(3):  # ajuste robusto
        vx,vy,x0,y0=cv2.fitLine(pts,cv2.DIST_HUBER,0,0.01,0.01).ravel()
        dist=np.abs((pts[:,0]-x0)*vy-(pts[:,1]-y0)*vx); keep=dist<max(2.0,np.percentile(dist,75))
        pts=pts[keep]
    return (vx,vy,x0,y0),len(pts),float(np.median(dist))
def cruce(l1,l2):
    vx1,vy1,x1,y1=l1; vx2,vy2,x2,y2=l2
    M=np.array([[vx1,-vx2],[vy1,-vy2]]); t=np.linalg.solve(M,[x2-x1,y2-y1]); return np.array([x1+vx1*t[0],y1+vy1*t[0]])
L=[]
for i in range(4):
    l,n,e=borde(A[i],A[(i+1)%4]); L.append(l); print('borde',i,'pts',n,'resid',round(e,2))
C=np.array([cruce(L[3],L[0]),cruce(L[0],L[1]),cruce(L[1],L[2]),cruce(L[2],L[3])]); print('esquinas tablero',C.round(1).tolist())
# iPhone: contorno oscuro -> cuadrilátero por ajuste de 4 rectas
x0,y0=800,760; roi=g[y0:1200,x0:1200]; pm=(roi<65).astype(np.uint8)*255; pm=cv2.morphologyEx(pm,cv2.MORPH_OPEN,np.ones((9,9),np.uint8))
pc=(max(cv2.findContours(pm,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)[0],key=cv2.contourArea).reshape(-1,2)+np.array([x0,y0])).astype(np.float32)
bx=cv2.boxPoints(cv2.minAreaRect(pc)); bx=bx[np.argsort(np.arctan2(bx[:,1]-bx[:,1].mean(),bx[:,0]-bx[:,0].mean()))]  # orden angular
PL=[]
for i in range(4):
    p,q=bx[i],bx[(i+1)%4]; d=(q-p)/np.linalg.norm(q-p); t=(pc-p)@d; dist=np.abs((pc-p)@np.array([-d[1],d[0]]))
    sel=pc[(t>0.2*np.linalg.norm(q-p))&(t<0.8*np.linalg.norm(q-p))&(dist<14)]
    PL.append(cv2.fitLine(sel,cv2.DIST_HUBER,0,0.01,0.01).ravel())
P=np.array([cruce(PL[i-1],PL[i]) for i in range(4)]); print('esquinas fono',P.round(1).tolist())
json.dump({'tablero':C.tolist(),'fono':P.tolist()},open('fp-geo.json','w'))
dbg=im.copy(); cv2.polylines(dbg,[np.int32(C)],True,(0,0,255),4); cv2.polylines(dbg,[np.int32(P)],True,(0,255,0),3)
for k,c in enumerate(C): cv2.circle(dbg,tuple(np.int32(c)),14,(0,255,255),3)
cv2.imwrite('fp-geo.jpg',cv2.resize(dbg,(1920,1080)),[cv2.IMWRITE_JPEG_QUALITY,85])

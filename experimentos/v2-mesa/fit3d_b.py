"""Variante: cámara fija en la pose de la homografía; esferas fijas desde su silueta; se ajusta solo el centro del proyector."""
import json, numpy as np, cv2
from scipy.optimize import least_squares
from fit3d import tri, A, B, balls, sel, Cc0
Cc=Cc0.copy(); Zc=Cc[2]; esf=[]
for (idx,cx,cy,r),o in zip(sel,balls):
    poly=np.array(o['poligono_mm'],np.float32); (ex,ey),(ea,eb),_=cv2.fitEllipse(poly); rs=min(ea,eb)/2
    sr=rs
    for _ in range(5): sz=sr+8; sr=rs*(Zc-sz)/Zc
    sxy=Cc[:2]+(np.array([ex,ey])-Cc[:2])*(Zc-sz)/Zc; esf.append((sxy[0],sxy[1],sz,sr)); print(o['etiqueta'],'radio %.1f centro (%.0f,%.0f,%.0f)'%(sr,sxy[0],sxy[1],sz))
def res(cp):
    out=[]
    for (idx,*_),(sx,sy,sz,sr) in zip(sel,esf):
        P,gap=tri(A[idx],B[idx],Cc,cp); out.append((np.linalg.norm(P-[sx,sy,sz],axis=1)-sr)*np.sqrt(600/len(idx)))
    return np.concatenate(out)
for cp0 in ([585,1700,2000],[585,1200,1200],[585,2600,3000],[0,1700,2000],[1200,1700,2000]):
    r=least_squares(res,cp0,loss='soft_l1',f_scale=3,max_nfev=400); print('inicio',cp0,'-> Cp',r.x.round(0).tolist(),'costo %.0f'%r.cost)
cp=r.x
for ((idx,*_),(sx,sy,sz,sr),o) in zip(sel,esf,balls):
    P,gap=tri(A[idx],B[idx],Cc,cp); print(' ',o['etiqueta'],'alturas trianguladas p50 %.0f p90 %.0f (esperado tope %.0f) · error a la esfera %.1f mm'%(np.percentile(P[:,2],50),np.percentile(P[:,2],90),sz+sr,np.median(np.abs(np.linalg.norm(P-[sx,sy,sz],axis=1)-sr))))

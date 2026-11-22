"""Calibración 3D con las bolas: encuentra el centro óptico de la cámara y del proyector (mm, marco de la mesa) haciendo que
los puntos triangulados del escaneo caigan sobre esferas. Después triangula todo -> nube de puntos métrica."""
import json, numpy as np, cv2, sys
from scipy.optimize import least_squares
Z=np.load('fp-parallax.npz'); A=Z['A'].astype(float); B=Z['B'].astype(float)
objs=json.load(open('out/objetos.json'))
def tri(A,B,Cc,Cp):
    a=np.c_[A,np.zeros(len(A))]; b=np.c_[B,np.zeros(len(B))]; u=Cc-a; v=Cp-b; w=a-b
    uu=(u*u).sum(1); vv=(v*v).sum(1); uv=(u*v).sum(1); uw=(u*w).sum(1); vw=(v*w).sum(1); den=uu*vv-uv**2+1e-9
    s=(uv*vw-vv*uw)/den; t=(uu*vw-uv*uw)/den; p=a+u*s[:,None]; q=b+v*t[:,None]; return (p+q)/2, np.linalg.norm(p-q,axis=1)
balls=[o for o in objs if o['etiqueta'].startswith('bola')]
sel=[]
for o in balls:
    poly=np.array(o['poligono_mm'],np.float32); (cx,cy),r=cv2.minEnclosingCircle(poly)
    d=np.hypot(A[:,0]-cx,A[:,1]-cy); idx=np.nonzero(d<r*0.8)[0]
    if len(idx)>4000: idx=np.random.default_rng(0).choice(idx,4000,replace=False)
    sel.append((idx,cx,cy,r)); print(o['etiqueta'],'pts',len(idx),'r silueta',round(r,1))
pose=json.load(open('/private/tmp/claude-501/-Users-sat/676a50ed-7238-4b78-9411-17db147f1404/scratchpad/fp-pose.json'))['cam']
Cc0=np.array(pose['C']); Cc0[2]=abs(Cc0[2]); Cp0=np.array([585.,1700.,2000.])
x0=list(Cc0)+list(Cp0); lo=[Cc0[0]-250,Cc0[1]-300,450]+[-400,700,900]; hi=[Cc0[0]+250,Cc0[1]+400,1300]+[1600,3200,3800]
for idx,cx,cy,r in sel:
    r0=r*0.90; x0+=[cx,cy,r0+8,r0]; lo+=[cx-0.5*r,cy-0.5*r,r0*0.8+2,r0*0.82]; hi+=[cx+0.5*r,cy+0.5*r,r0*1.15+25,r0*1.08]
x0=np.array(x0); lo=np.array(lo); hi=np.array(hi)
def res(x):
    Cc,Cp=x[:3],x[3:6]; out=[]
    for k,(idx,cx,cy,r) in enumerate(sel):
        sx,sy,sz,sr=x[6+4*k:10+4*k]; P,gap=tri(A[idx],B[idx],Cc,Cp); w=np.sqrt(600.0/len(idx))   # cada bola pesa parecido
        out.append((np.linalg.norm(P-[sx,sy,sz],axis=1)-sr)*w); out.append(gap*0.3*w)
        out.append([(sz-sr-8)*1.0])              # descansa sobre el plato (~8 mm sobre la mesa)
    out.append((Cc-Cc0)*0.02)
    return np.concatenate([np.atleast_1d(o).ravel() for o in out])
best=None
for cp0 in ([585,1700,2000],[585,1300,1500],[585,2200,2600],[300,1700,2000],[900,1700,2000]):
    xx=x0.copy(); xx[3:6]=cp0; r=least_squares(res,xx,bounds=(lo,hi),loss='soft_l1',f_scale=3.0,max_nfev=300)
    print('   inicio proyector',cp0,'-> costo %.1f'%r.cost,'Cc',r.x[:3].round(0).tolist(),'Cp',r.x[3:6].round(0).tolist())
    if best is None or r.cost<best.cost: best=r
r=best; x=r.x; Cc,Cp=x[:3],x[3:6]
print('cámara  centro (mm):',Cc.round(0).tolist(),' (inicial',Cc0.round(0).tolist(),')'); print('proyector centro (mm):',Cp.round(0).tolist())
for k,(idx,cx,cy,rr) in enumerate(sel):
    sx,sy,sz,sr=x[6+4*k:10+4*k]; P,gap=tri(A[idx],B[idx],Cc,Cp); e=np.abs(np.linalg.norm(P-[sx,sy,sz],axis=1)-sr)
    print(f"  {balls[k]['etiqueta']}: centro ({sx:.0f},{sy:.0f},{sz:.0f}) radio {sr:.1f} mm (silueta {rr:.1f}) · error mediano {np.median(e):.1f} mm · tope a {sz+sr:.0f} mm")
json.dump({'Cc':Cc.tolist(),'Cp':Cp.tolist(),'esferas':[{'etiqueta':balls[k]['etiqueta'],'centro':x[6+4*k:9+4*k].tolist(),'radio':float(x[9+4*k])} for k in range(len(sel))]},open('fp-3d.json','w'),indent=1)
# nube completa
P,gap=tri(A,B,Cc,Cp); ok=(gap<6)&(P[:,2]>-15)&(P[:,2]<400)&(A[:,0]>0)&(A[:,0]<1320)&(A[:,1]>0)&(A[:,1]<690)
print('nube:',ok.sum(),'puntos · altura mediana de la mesa %.1f mm · p99 %.0f mm'%(np.median(P[ok,2]),np.percentile(P[ok,2],99)))
np.savez_compressed('nube.npz',P=P[ok].astype(np.float32),us=Z['us'][ok],vs=Z['vs'][ok])
img=np.full((700,1340),np.nan,np.float32); xi=np.clip((A[ok,0]+10).astype(int),0,1339); yi=np.clip((A[ok,1]+10).astype(int),0,699); img[yi,xi]=P[ok,2]
vis=cv2.applyColorMap(np.clip(np.nan_to_num(img,nan=0)*2.2,0,255).astype(np.uint8),cv2.COLORMAP_TURBO); vis[np.isnan(img)]=(25,25,25); cv2.imwrite('out/alturas.jpg',vis)

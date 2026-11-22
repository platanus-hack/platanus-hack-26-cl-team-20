"""Marco métrico de la mesa: (px de cámara 720p corregidos de lente) -> mm reales, y canvas del proyector <- mm reales."""
import json, numpy as np, cv2
cal=json.loads(open('cal-local.json').read()[open('cal-local.json').read().index('{'):])
Lz=dict(f=cal['f_px'],cx=cal['cx'],cy=cal['cy'],k1=cal['k1'],k2=cal['k2'])
M=np.array(cal['matrix_undistorted_px_to_mm']).reshape(3,3)   # px corregido -> "mm" del canvas
def undist(p):
    xd,yd=(p[0]-Lz['cx'])/Lz['f'],(p[1]-Lz['cy'])/Lz['f']; x,y=xd,yd
    for _ in range(12):
        r2=x*x+y*y; k=1+Lz['k1']*r2+Lz['k2']*r2*r2; x,y=xd/k,yd/k
    return [x*Lz['f']+Lz['cx'],y*Lz['f']+Lz['cy']]
def ap(H,p):
    p=np.asarray(p,float).reshape(-1,2); q=np.c_[p,np.ones(len(p))]@H.T; return q[:,:2]/q[:,2:]
g=json.load(open('fp-geo.json'))
T=np.array([undist(np.array(p)/3) for p in g['tablero']]); F=np.array([undist(np.array(p)/3) for p in g['fono']])
Hu=cv2.getPerspectiveTransform(T.astype(np.float32),np.float32([[0,0],[1,0],[1,1],[0,1]])).astype(float)
f=ap(Hu,F)                                   # fono en coordenadas del cuadrado unidad
lados=[f[(i+1)%4]-f[i] for i in range(4)]
largo=[(lados[1],lados[3]),(lados[0],lados[2])]   # según orden: 0-1 es el lado corto de arriba
# resolver W,H: lados cortos miden 75.7, largos 150.9
A=[];b=[]
for v in (lados[0],lados[2]): A.append([v[0]**2,v[1]**2]); b.append(75.7**2)
for v in (lados[1],lados[3]): A.append([v[0]**2,v[1]**2]); b.append(150.9**2)
W2,H2=np.linalg.lstsq(np.array(A),np.array(b),rcond=None)[0]; W,H=float(np.sqrt(W2)),float(np.sqrt(H2))
print('tablero izquierdo: %.1f x %.1f mm'%(W,H))
S=np.diag([W,H,1.0]); Hmm=S@Hu               # px corregido -> mm reales (origen: esquina TL del tablero izq; x hacia la derecha de la foto, y hacia abajo)
fm=ap(Hmm,F); print('fono medido (mm):',[round(float(np.linalg.norm(fm[(i+1)%4]-fm[i])),1) for i in range(4)])
C2T=Hmm@np.linalg.inv(M)                     # canvas "mm" -> mm reales
T2C=np.linalg.inv(C2T)
cc=ap(C2T,[[0,0],[1000,0],[1000,563],[0,563]]); print('canvas en mm reales:',cc.round(0).tolist())
print('lados del canvas (mm reales): arriba %.0f abajo %.0f izq %.0f der %.0f'%tuple(np.linalg.norm(cc[j]-cc[i]) for i,j in ((0,1),(3,2),(0,3),(1,2))))
json.dump({'W':W,'H':H,'px_to_mm':Hmm.tolist(),'canvas_to_mm':C2T.tolist(),'mm_to_canvas':(T2C/T2C[2,2]).tolist(),'canvas_mm':cc.tolist(),'lens':Lz},open('fp-metric.json','w'),indent=1)

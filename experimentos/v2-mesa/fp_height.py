"""Paralaje del escaneo gray code contra el plano de la mesa -> mapa de 'altura' (mm de desplazamiento en el plano)."""
import cv2, numpy as np, json
z=np.load('sl/osmo/map.npz'); print({k:(z[k].shape,str(z[k].dtype)) for k in z.files})
px,py=z[z.files[0]].astype(np.float32),z[z.files[1]].astype(np.float32)
m=json.load(open('fp-metric.json')); Hmm=np.array(m['px_to_mm']); C2T=np.array(m['canvas_to_mm']); L=m['lens']
valid=(px<60000)&(py<60000); print('válidos',valid.sum(), 'rango proy x',px[valid].min(),px[valid].max(),'y',py[valid].min(),py[valid].max())
h,w=px.shape; vs,us=np.nonzero(valid); esc=w/1280.0
# cámara: px 1080p -> 720p -> corregir lente -> mm (A)
u=us/esc; v=vs/esc; xd=(u-L['cx'])/L['f']; yd=(v-L['cy'])/L['f']; x,y=xd.copy(),yd.copy()
for _ in range(12):
    r2=x*x+y*y; k=1+L['k1']*r2+L['k2']*r2*r2; x,y=xd/k,yd/k
q=np.stack([x*L['f']+L['cx'],y*L['f']+L['cy'],np.ones_like(x)],1)@Hmm.T; A=q[:,:2]/q[:,2:]
# proyector: px 1920x1080 -> canvas (÷1.92) -> mm (B)
q=np.stack([px[valid]/1.92,py[valid]/1.92,np.ones(valid.sum())],1)@C2T.T; B=q[:,:2]/q[:,2:]
d=B-A; mag=np.linalg.norm(d,axis=1)
enmesa=(A[:,0]>20)&(A[:,0]<1300)&(A[:,1]>20)&(A[:,1]<670)
print('en la mesa:',enmesa.sum(),'· paralaje mediana %.2f mm, p25 %.2f, p75 %.2f, p95 %.1f'%tuple(np.percentile(mag[enmesa],[50,25,75,95])))
base=mag[enmesa]<np.percentile(mag[enmesa],40); print('sesgo del plano (zonas bajas): dx %.2f dy %.2f mm'%tuple(np.median(d[enmesa][base],axis=0)))
np.savez_compressed('fp-parallax.npz',A=A.astype(np.float32),B=B.astype(np.float32),us=us.astype(np.int16),vs=vs.astype(np.int16))
# mapa en la vista normalizada (1 px = 1 mm)
img=np.zeros((720,1440),np.float32); cnt=np.zeros_like(img); xi=np.clip((A[:,0]+20).astype(int),0,1439); yi=np.clip((A[:,1]+20).astype(int),0,719); ok=enmesa
np.add.at(img,(yi[ok],xi[ok]),mag[ok]); np.add.at(cnt,(yi[ok],xi[ok]),1); img=np.where(cnt>0,img/np.maximum(cnt,1),0)
vis=cv2.applyColorMap(np.clip(img*6,0,255).astype(np.uint8),cv2.COLORMAP_TURBO); vis[cnt==0]=(30,30,30); cv2.imwrite('fp-parallax.jpg',vis)

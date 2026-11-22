"""Reconstruye la vista normalizada (2 px/mm, x[-10,1330] y[-10,700] mm) desde una foto de camara,
igual que fp_rect.mapa/pipeline.py. uso: normalizar.py foto.jpg salida.png"""
import cv2, json, numpy as np, sys
from pathlib import Path
m=json.load(open(Path(__file__).resolve().parent.parent/'fp-metric.json')); Hi=np.linalg.inv(np.array(m['px_to_mm'])); L=m['lens']
X0,Y0,X1,Y1,PP=-10,-10,1330,700,2.0
def mapa(esc):
    xs,ys=np.meshgrid(np.arange(X0,X1,1/PP),np.arange(Y0,Y1,1/PP)); q=np.stack([xs,ys,np.ones_like(xs)],-1)@Hi.T
    u,v=q[...,0]/q[...,2],q[...,1]/q[...,2]; x,y=(u-L['cx'])/L['f'],(v-L['cy'])/L['f']; r2=x*x+y*y; k=1+L['k1']*r2+L['k2']*r2*r2
    return ((x*k*L['f']+L['cx'])*esc).astype(np.float32),((y*k*L['f']+L['cy'])*esc).astype(np.float32)
im=cv2.imread(sys.argv[1]); mx,my=mapa(im.shape[1]/1280)
out=cv2.remap(im,mx,my,cv2.INTER_CUBIC); cv2.imwrite(sys.argv[2],out); print(out.shape)

"""Foto 4K -> vista normalizada de la mesa (mm reales, 2 px/mm). uso: fp_rect.py entrada.jpg salida.jpg [ppmm]"""
import cv2, json, numpy as np, sys
m=json.load(open('fp-metric.json')); Hi=np.linalg.inv(np.array(m['px_to_mm'])); L=m['lens']
def mapa(x0,y0,x1,y1,pp,esc=3.0):
    xs,ys=np.meshgrid(np.arange(x0,x1,1/pp),np.arange(y0,y1,1/pp)); q=np.stack([xs,ys,np.ones_like(xs)],-1)@Hi.T
    u,v=q[...,0]/q[...,2],q[...,1]/q[...,2]; x,y=(u-L['cx'])/L['f'],(v-L['cy'])/L['f']; r2=x*x+y*y; k=1+L['k1']*r2+L['k2']*r2*r2
    return ((x*k*L['f']+L['cx'])*esc).astype(np.float32),((y*k*L['f']+L['cy'])*esc).astype(np.float32)
if __name__=='__main__':
    im=cv2.imread(sys.argv[1]); pp=float(sys.argv[3]) if len(sys.argv)>3 else 2
    mx,my=mapa(-20,-20,1420,720,pp,im.shape[1]/1280); out=cv2.remap(im,mx,my,cv2.INTER_AREA if pp<2 else cv2.INTER_LINEAR); cv2.imwrite(sys.argv[2],out,[cv2.IMWRITE_JPEG_QUALITY,88]); print(out.shape)

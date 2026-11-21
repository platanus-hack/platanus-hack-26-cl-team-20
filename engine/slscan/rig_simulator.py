"""Small ray-cast calibration room, with explicit synthetic optical assumptions.

Images use the effective Osmo frame, not a measured model of its 4K ISP. The
projector samples its own native raster. No camera or display APIs are imported.
"""
from dataclasses import dataclass, asdict
import cv2
import numpy as np

from .joint import project, rays
from .rig import Device, Rig


@dataclass
class SensorModel:
    read_noise_dn: float = 1.2
    shot_noise_dn: float = .6
    camera_blur_px: float = .65
    projector_blur_px: float = .5
    exposure_jitter: float = .008
    vignette: float = .15
    gamma: float = 2.0
    latency_ms: float = 90.
    throw_ratio: float = 1.1
    seed: int = 23


def surface(name, corners, color):
    q=np.asarray(corners,float)
    u=q[1]-q[0]; v=q[3]-q[0]
    return {'name':name,'corners':q,'color':np.asarray(color,float),
            'u':u/np.linalg.norm(u),'v':v/np.linalg.norm(v),
            'size':np.array([np.linalg.norm(u),np.linalg.norm(v)]),
            'normal':np.cross(u,v)/np.linalg.norm(np.cross(u,v))}


class SyntheticRoom:
    def __init__(self, model=None, scale=1.):
        self.model=model or SensorModel(); self.rng=np.random.default_rng(self.model.seed)
        self.scale=scale
        K=np.array([[1450.,0,530],[0,1470,945],[0,0,1]])
        K[:2]*=scale
        cam=Device('osmo','camera',(round(1080*scale),round(1920*scale)),K,
                   np.array([-.065,.015,.001,-.0007,.002]))
        focal=1920*self.model.throw_ratio
        pro=Device('proj','projector',(1920,1080),
            np.array([[focal,0,970],[0,focal*1.01,565],[0,0,1.]]),
            np.array([-.075,.018,-.0005,.001,.003]),
            cv2.Rodrigues(np.array([.02,-.12,.01]))[0],np.array([240.,-40.,30.]))
        self.rig=Rig({'osmo':cam,'proj':pro},world='osmo',extra={'evidence':'synthetic'})
        self.paper_mm=np.array([[0.,0,0],[210,0,0],[210,297,0],[0,297,0]])
        self.room=[
            surface('Muro', [[-1400,-1100,1900],[1400,-1100,1900],[1400,600,1900],[-1400,600,1900]],[.16,.20,.27]),
            surface('Suelo', [[-1400,440,400],[1400,440,400],[1400,440,1900],[-1400,440,1900]],[.18,.21,.22]),
            surface('Cubo frontal', [[-410,30,1180],[-240,30,1180],[-240,260,1180],[-410,260,1180]],[.35,.26,.16]),
            surface('Cubo lateral', [[-240,30,1180],[-240,30,1390],[-240,260,1390],[-240,260,1180]],[.25,.18,.10]),
            surface('Panel lateral', [[290,-130,1480],[520,-130,1360],[520,250,1360],[290,250,1480]],[.14,.30,.29]),
        ]
        self.poses=[]
        rng=np.random.default_rng(12)
        for i in range(13):
            # Like placing the real sheet: all four corners must lie inside
            # camera AND projector. Otherwise a clipped sheet is not a pose.
            for attempt in range(500):
                R=cv2.Rodrigues(rng.uniform([-.45,-.45,-.10],[.45,.45,.10]))[0]
                t=np.array([rng.uniform(-100,100),rng.uniform(-20,140),rng.uniform(850,1200)])-R@self.paper_mm.mean(0)
                X=self.paper_mm@R.T+t
                pc,pp=project(cam,X),project(pro,X)
                if np.all(pc>30*scale) and np.all(pc<np.array(cam.size)-30*scale) and np.all(pp>45) and np.all(pp<np.array(pro.size)-45):
                    self.poses.append((R,t));break
            else: raise ValueError('el tiro no deja espacio para una A4 completa en este volumen')
        self.current=None

    def describe(self):
        return {'model':asdict(self.model),'camera_size':list(self.rig['osmo'].size),
                'projector_size':list(self.rig.projector().size),
                'assumptions':['Parámetros ópticos y de sensor ilustrativos, no medidos en T749/Osmo.',
                    'Rayos 3D, oclusión, raster del proyector, distorsión Brown, blur, ruido, exposición y 8 bits.',
                    'Sensor efectivo; sin Bayer/ISP 4K, rolling shutter, compresión, rebotes ni respuesta espectral real.',
                    'Latencia es un retardo modelado con cuadros viejos si no se espera; no una medición.']}

    def set_pose(self,index):
        R,t=self.poses[index]
        paper=self.paper_mm@R.T+t
        support=np.array([[-12,-12,1],[222,-12,1],[222,309,1],[-12,309,1]])@R.T+t
        surfaces=self.room+[surface('Soporte',support,[.12,.12,.14]),surface('A4',paper,[.86,.86,.84])]
        self.prepare_surfaces(surfaces,index)
        return paper

    def prepare_surfaces(self,surfaces,index):
        cam=self.rig['osmo']; pro=self.rig.projector(); w,h=cam.size
        self.mapx=np.full((h,w),-1,np.float32); self.mapy=self.mapx.copy()
        self.albedo=np.zeros((h,w,3),np.float32); self.illum=np.zeros((h,w),np.float32)
        self.labels=np.full((h,w),-1,np.int16)
        self.xyz=np.zeros((h,w,3),np.float32)
        center=cam.center_world()
        for y in range(0,h,96):
            yy,xx=np.mgrid[y:min(y+96,h),0:w]
            direction=rays(cam,np.column_stack((xx.ravel(),yy.ravel())))
            best=np.full(len(direction),np.inf); label=np.full(len(direction),-1)
            for k,s in enumerate(surfaces):
                denom=direction@s['normal']; valid=abs(denom)>1e-8
                depth=np.divide((s['corners'][0]-center)@s['normal'],denom,out=np.zeros(len(denom)),where=valid)
                X=center+depth[:,None]*direction; local=X-s['corners'][0]
                uv=np.column_stack((local@s['u'],local@s['v']))
                valid &= (depth>0)&(depth<best)&np.all(uv>=0,axis=1)&np.all(uv<=s['size'],axis=1)
                best[valid]=depth[valid]; label[valid]=k
            valid=label>=0
            X=center+np.where(valid,best,0)[:,None]*direction
            self.xyz[y:y+xx.shape[0]]=X.reshape((*xx.shape,3))
            pp=project(pro,X); pp[~valid]=-1
            colors=np.zeros((len(X),3)); light=np.zeros(len(X))
            L=pro.center_world()-X; norm=np.linalg.norm(L,axis=1)
            for k,s in enumerate(surfaces):
                keep=label==k; colors[keep]=s['color']
                if s.get('material')=='wood':
                    local=X[keep]-s['corners'][0]
                    grain=.88+.12*np.sin(local@s['u']*.07+2*np.sin(local@s['v']*.015))
                    colors[keep]*=grain[:,None]
                if 'texture' in s and np.any(keep):
                    local=X[keep]-s['corners'][0]
                    tx=local@s['u']*2;ty=local@s['v']*2
                    ix=np.floor(tx).astype(int);iy=np.floor(ty).astype(int)
                    dx=tx-ix;dy=ty-iy;tex=s['texture'];th,tw=tex.shape
                    value=((1-dx)*(1-dy)*tex[iy%th,ix%tw]+dx*(1-dy)*tex[iy%th,(ix+1)%tw]+
                           (1-dx)*dy*tex[(iy+1)%th,ix%tw]+dx*dy*tex[(iy+1)%th,(ix+1)%tw])
                    colors[keep]*=value[:,None]
                light[keep]=np.abs(L[keep]@s['normal'])/np.maximum(1,norm[keep])
            # Visibility from projector: surfaces between lens and visible point.
            D=X-pro.center_world()
            shadow=np.zeros(len(X),bool)
            for s in surfaces:
                denom=D@s['normal']
                depth=np.divide((s['corners'][0]-pro.center_world())@s['normal'],denom,
                    out=np.zeros(len(denom)),where=abs(denom)>1e-8)
                local=pro.center_world()+depth[:,None]*D-s['corners'][0]
                uv=np.column_stack((local@s['u'],local@s['v']))
                shadow|=(depth>1e-6)&(depth<.99999)&np.all(uv>=0,axis=1)&np.all(uv<=s['size'],axis=1)
            light[shadow]=0
            light*=np.clip((1000/np.maximum(norm,100))**2,.1,2)
            shape=xx.shape
            self.mapx[y:y+shape[0]]=pp[:,0].reshape(shape)
            self.mapy[y:y+shape[0]]=pp[:,1].reshape(shape)
            self.albedo[y:y+shape[0]]=colors.reshape((*shape,3))
            self.illum[y:y+shape[0]]=light.reshape(shape)
            self.labels[y:y+shape[0]]=label.reshape(shape)
        yy,xx=np.mgrid[:h,:w]
        self.vignette=1-self.model.vignette*((xx-w/2)**2+(yy-h/2)**2)/((w/2)**2+(h/2)**2)
        self.current=index; self.surfaces=surfaces; self.previous=None

    def render(self,pattern,*,wait_ms=200.,roi=None):
        """Capture native effective camera pixels, optionally cropping AFTER optics.

        Gray frames may retain a crop to bound memory; coordinates remain full
        sensor pixels after adding the declared crop origin at decoding.
        """
        if self.current is None: raise ValueError('set_pose first')
        delivered=pattern if self.previous is None or wait_ms>=self.model.latency_ms else self.previous
        self.previous=pattern.copy()
        light=delivered.astype(np.float32)/255
        if self.model.projector_blur_px>0:
            light=cv2.GaussianBlur(light,(0,0),self.model.projector_blur_px)
        slices=(slice(None),slice(None)) if roi is None else (slice(roi[1],roi[1]+roi[3]),slice(roi[0],roi[0]+roi[2]))
        mx,my=self.mapx[slices],self.mapy[slices]
        projected=cv2.remap(light,mx,my,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
        if projected.ndim==2: projected=projected[...,None]
        linear=self.albedo[slices]*(.07+.65*self.illum[slices][...,None]*projected)
        linear*=self.vignette[slices][...,None]
        gain=1+self.rng.normal(0,self.model.exposure_jitter)
        signal=255*np.power(np.clip(linear*gain,0,1),1/self.model.gamma)
        if self.model.camera_blur_px>0:
            signal=cv2.GaussianBlur(signal,(0,0),self.model.camera_blur_px*self.scale)
        std=np.sqrt(self.model.read_noise_dn**2+self.model.shot_noise_dn**2*np.maximum(signal,0)/255)
        signal+=self.rng.normal(0,1,signal.shape)*std
        return np.clip(np.rint(signal),0,255).astype(np.uint8)

    def paper_roi(self):
        cam=self.rig['osmo']; R,t=self.poses[self.current]
        q=project(cam,self.paper_mm@R.T+t)
        lo=np.maximum(0,np.floor(q.min(0)-20)).astype(int)
        hi=np.minimum(cam.size,np.ceil(q.max(0)+20)).astype(int)
        return (*lo,*(hi-lo))

    def scene_json(self):
        return [{'name':s['name'],'corners':s['corners'].tolist(),'color':s['color'].tolist()}
                for s in self.surfaces]

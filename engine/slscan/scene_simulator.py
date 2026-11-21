"""A furnished room for board-free structured-light experiments (all synthetic)."""
from dataclasses import asdict
import cv2
import numpy as np
from .rig import Device,Rig
from .rig_simulator import SyntheticRoom,SensorModel,surface


def box(name,lo,hi,color):
    x,y,z=lo; X,Y,Z=hi
    faces=[[[x,y,z],[X,y,z],[X,Y,z],[x,Y,z]],
           [[x,y,z],[x,y,Z],[X,y,Z],[X,y,z]],
           [[x,y,z],[x,Y,z],[x,Y,Z],[x,y,Z]],
           [[X,y,z],[X,y,Z],[X,Y,Z],[X,Y,z]],
           [[x,y,Z],[x,Y,Z],[X,Y,Z],[X,y,Z]]]
    return [surface(name+' '+str(i),q,np.asarray(color)*(1-.06*i)) for i,q in enumerate(faces)]


def room_surfaces():
    room=[surface('Pared fondo',[[-1000,-900,1800],[1000,-900,1800],[1000,400,1800],[-1000,400,1800]],[.68,.64,.57]),
          surface('Pared lateral',[[-650,-900,300],[-650,-900,1800],[-650,400,1800],[-650,400,300]],[.60,.63,.65])]
    desk=box('Mesa de madera',[-600,280,650],[600,330,1710],[.48,.30,.17])
    for s in desk: s['material']='wood'
    room+=desk
    for x in (-535,495): room+=box('Pata mesa',[x,330,700],[x+45,900,745],[.12,.13,.14])
    room+=box('Teclado base',[-185,258,820],[185,280,955],[.16,.17,.19])
    for row in range(5):
        for col in range(14):
            x=-173+col*25; z=829+row*24
            room+=box(f'Tecla {row}-{col}',[x,250,z],[x+21,258,z+19],[.65,.67,.66])[:2]
    room+=box('Barra espaciadora',[-70,249,949],[70,258,968],[.63,.65,.64])[:2]
    for i,c in enumerate(([.20,.34,.42],[.52,.26,.18],[.28,.38,.23])):
        room+=box('Libro '+str(i),[240-i*8,250-i*30,1210+i*8],[445-i*8,278-i*30,1485+i*8],c)
    room+=box('Caja alta',[-480,50,1220],[-290,280,1430],[.52,.45,.34])
    # Mug with faceted cylindrical relief and open dark interior.
    for i in range(24):
        a,b=np.array([i,i+1])*2*np.pi/24
        room.append(surface('Taza',[[285+48*np.cos(a),160,1000+48*np.sin(a)],
            [285+48*np.cos(b),160,1000+48*np.sin(b)],
            [285+48*np.cos(b),279,1000+48*np.sin(b)],
            [285+48*np.cos(a),279,1000+48*np.sin(a)]],[.25,.42,.40]))
    room+=box('Asa taza',[330,185,987],[354,243,1010],[.22,.37,.36])
    for i,(x,y,w,h) in enumerate([(-390,-350,250,300),(-60,-290,290,210),(290,-390,190,270)]):
        room+=box('Marco '+str(i),[x-12,y-12,1765],[x+w+12,y+h+12,1795],[.16,.12,.09])
        room.append(surface('Cuadro '+str(i),[[x,y,1762],[x+w,y,1762],[x+w,y+h,1762],[x,y+h,1762]],[.70,.64,.48]))
        for j in range(4):
            a=x+15+j*w*.21;b=y+40+(j%2)*h*.22
            room.append(surface('Arte geometrico',[[a,b,1761],[a+w*.16,b,1761],[a+w*.16,y+h-20,1761],[a,y+h-20,1761]],
                                [.25+.08*j,.30+.05*i,.34]))
    return room


class FurnishedRoom(SyntheticRoom):
    def __init__(self,model=None,scale=1.,motion='camera'):
        if motion not in ('camera','rigid'): raise ValueError('movimiento inválido')
        self.motion=motion
        self.model=model or SensorModel();self.rng=np.random.default_rng(self.model.seed);self.scale=scale
        K=np.array([[1200.,0,532],[0,1200.,953],[0,0,1.]])
        K[:2]*=scale
        camera=Device('osmo','camera',(round(1080*scale),round(1920*scale)),K,
                      np.array([-.045,.009,0,0,0]))
        f=1920*self.model.throw_ratio
        projector=Device('proj','projector',(1920,1080),np.array([[f,0,975],[0,f,555],[0,0,1.]]),
                         np.array([-.055,.012,0,0,0]),cv2.Rodrigues(np.array([.025,-.16,.015]))[0],
                         np.array([290.,-65.,55.]))
        self.rig=Rig({'osmo':camera,'proj':projector},world='osmo',extra={'evidence':'synthetic'})
        self.relative_R=projector.R.copy();self.relative_t=projector.t.copy()
        self.room=room_surfaces();self.current=None
        if motion=='rigid':
            # Physical, view-independent mottled surfaces. Only RGB pixels go
            # to SIFT; texture coordinates/geometry never go to the estimator.
            texture_rng=np.random.default_rng(109)
            for s in self.room:
                fine=texture_rng.uniform(0,1,(64,64)).astype(np.float32)
                coarse=texture_rng.uniform(0,1,(16,16)).astype(np.float32)
                tex=.25*cv2.resize(fine,(512,512))+.75*cv2.resize(coarse,(512,512))
                s['texture']=.15+.85*tex
        self.camera_poses=[(np.eye(3),np.zeros(3))]
        for center in (np.array([-230.,-65.,50.]),np.array([240.,-100.,90.])):
            forward=np.array([0.,100.,1350.])-center;forward/=np.linalg.norm(forward)
            right=np.cross([0,1,0],forward);right/=np.linalg.norm(right)
            R=np.stack([right,np.cross(forward,right),forward])
            self.camera_poses.append((R,-R@center))

    def set_view(self,index):
        self.rig['osmo'].R,self.rig['osmo'].t=self.camera_poses[index]
        if self.motion=='rigid':
            cam=self.rig['osmo'];pro=self.rig.projector()
            pro.R=self.relative_R@cam.R;pro.t=self.relative_R@cam.t+self.relative_t
        self.prepare_surfaces(self.room,index)

    def describe(self):
        result=super().describe()
        result.update(mode='scene',scene='Mesa, teclado con teclas en relieve, libros, taza, cuadros y paredes.',
                      motion=self.motion,
                      camera_motion=('Tres posiciones del conjunto rígido; escena fija, superficies con textura procedural.'
                                     if self.motion=='rigid' else 'Tres posiciones; proyector y objetos fijos. Sin hoja ni tablero.'))
        return result


def grid_pattern(size=(1920,1080),spacing=64):
    w,h=size;img=np.full((h,w,3),180,np.uint8)
    for x in range(0,w,spacing): cv2.line(img,(x,0),(x,h-1),(20,170,245),3)
    for y in range(0,h,spacing): cv2.line(img,(0,y),(w-1,y),(220,130,25),3)
    return img

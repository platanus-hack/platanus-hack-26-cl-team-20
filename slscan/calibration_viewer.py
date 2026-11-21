"""Loopback-only calibration observer, shared by simulation and real acquisition.

The server never opens hardware. Producers publish bounded previews and an
atomic state file; SSE consumers can reconnect without losing current state.
"""
import asyncio
import copy
import json
from pathlib import Path
import threading
import time
import uuid
import cv2
import numpy as np

from .metric_session import write_json
from .joint import rays

STEPS=['Preparar pose','Blanco','Gray code','ChArUco','Detectar esquinas','Ajustar rig','Control reservado']


def device_json(device):
    d=device.to_json(); w,h=device.size
    border=np.array([[-.5,-.5],[w-.5,-.5],[w-.5,h-.5],[-.5,h-.5]],float)
    center=device.center_world(); directions=rays(device,border)
    d['center_world']=center.tolist()
    d['frustum_world']=(center+directions*550/np.linalg.norm(directions,axis=1)[:,None]).tolist()
    return d


class Journal:
    def __init__(self,root,evidence='live',total=13,resume=False):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.state={'run_id':uuid.uuid4().hex,'seq':0,'evidence':evidence,'status':'running',
            'stage':STEPS[0],'stage_index':0,'pose':0,'total':total,'progress':0,
            'message':'Esperando observaciones','images':{},'views':[], 'planes':[],
            'devices':{},'truth_devices':{},'truth_scene':[], 'history':[],
            'physical_verified':False,'steps':STEPS,'model':None}
        if resume and (self.root/'state.json').exists():
            self.state=json.loads((self.root/'state.json').read_text())
            if self.state['evidence']!=evidence:
                raise ValueError('el visor no puede mezclar evidencia sintética y real')
        self.lock=threading.Lock(); self.emit()

    def emit(self,stage=None,**fields):
        with self.lock:
            self.state.update(fields)
            if stage:
                self.state.update(stage=stage,stage_index=self.state['steps'].index(stage))
            self.state['seq']+=1; self.state['updated_at']=time.time()
            if stage or 'message' in fields:
                self.state['history'].append({'seq':self.state['seq'],'time':time.time(),
                    'pose':self.state['pose'],'stage':self.state['stage'],'message':self.state['message']})
                self.state['history']=self.state['history'][-100:]
            write_json(self.root/'state.json',self.state)

    def frame(self,kind,image,points=None,paper=None):
        preview=image.copy()
        if preview.ndim==2: preview=cv2.cvtColor(preview,cv2.COLOR_GRAY2BGR)
        if paper is not None:
            cv2.polylines(preview,[np.round(paper).astype(np.int32)],True,(45,190,250),2,cv2.LINE_AA)
        if points is not None:
            for i,p in enumerate(points):
                cv2.circle(preview,tuple(np.round(p).astype(int)),4,(220,210,25),-1,cv2.LINE_AA)
        h,w=preview.shape[:2]; factor=min(1,1000/max(w,h))
        if factor<1: preview=cv2.resize(preview,(round(w*factor),round(h*factor)),interpolation=cv2.INTER_AREA)
        filename=f'{kind}.jpg'
        ok,jpeg=cv2.imencode('.jpg',preview,[cv2.IMWRITE_JPEG_QUALITY,85])
        if not ok: raise ValueError('preview JPEG failed')
        tmp=self.root/(filename+'.tmp'); tmp.write_bytes(jpeg.tobytes()); tmp.replace(self.root/filename)
        self.state['images'][kind]={'url':f'/images/{filename}','size':[w,h]}

    def estimate(self,rig,views):
        report=rig.extra['metric_report']; indexed={v.name:v for v in views}
        planes=[]
        for row in report['per_view']:
            v=indexed[row['name']]; R=np.asarray(row['paper_pose']['R']); t=np.asarray(row['paper_pose']['t'])
            planes.append({'name':v.name,'corners':(v.paper_mm@R.T+t).tolist(),
                           'residual_camera_px':row['projected_camera_px']['rms'],
                           'residual_projector_px':row['projector_px']['rms']})
        self.emit('Ajustar rig',devices={k:device_json(d) for k,d in rig.devices.items()},planes=planes,
                  calibration={'status':report['status'],'issues':report['issues'],
                    'rms_px':report['rms_px'],'baseline_mm':report['baseline_mm'],
                    'observability':report['observability']},
                  message=f"{len(planes)} planos estimados; {report['status']}. Precisión física sin verificar.")


class Simulation:
    def __init__(self,root):
        self.root=Path(root); self.thread=None; self.paused=threading.Event(); self.cancel=threading.Event()

    def checkpoint(self):
        while self.paused.is_set() and not self.cancel.is_set(): time.sleep(.1)
        if self.cancel.is_set(): raise InterruptedError('simulación detenida')

    def start(self,options=None):
        if self.thread and self.thread.is_alive(): return False
        self.cancel.clear(); self.paused.clear()
        self.thread=threading.Thread(target=self.run,args=(options or {},),daemon=True)
        self.thread.start(); return True

    def run(self,options):
        from .rig_simulator import SensorModel,SyntheticRoom
        from .calib import CharucoBoard
        from . import graycode,metric,joint
        from .metric_session import adaptive_board,initialize,save_view,evaluate_reserved
        model=SensorModel(**options)
        journal=Journal(self.root,'synthetic',11)
        try:
            room=SyntheticRoom(model)
            journal.emit(model=room.describe(),truth_devices={k:device_json(d) for k,d in room.rig.devices.items()},
                         message='Sala sintética. Parámetros de lente y sensor supuestos, sin hardware.')
            board=CharucoBoard(); views=[]; reserved=[]; rig=None
            for i in range(11):
                self.checkpoint()
                name=f'pose_{i:02d}'; role='train' if i<8 else 'holdout'
                journal.emit('Preparar pose',pose=i+1,progress=i/11,message=f'Colocando A4: {name}, {role}')
                room.set_pose(i)
                journal.emit(truth_scene=room.scene_json())
                white_pattern=np.full((1080,1920),200,np.uint8)
                white=room.render(white_pattern)
                quad=metric.detect_paper(white,aspect=297/210)
                if quad is None: raise ValueError(f'{name}: hoja no detectada en imagen renderizada')
                journal.frame('pattern',white_pattern); journal.frame('camera',white,paper=quad)
                journal.emit('Blanco',message='Esquinas físicas detectadas en la imagen con ruido.')
                time.sleep(.3)
                # Actual raster images of all 44 patterns are decoded. Retain a
                # crop only to avoid 44 full-resolution sensor buffers on disk.
                roi=room.paper_roi(); x,y,w,h=roi; items=[]
                specs=graycode.sequence((1920,1080))
                for k,s in enumerate(specs):
                    self.checkpoint()
                    pattern=graycode.pattern((1920,1080),s.axis,s.bit,s.inverse)
                    image=room.render(pattern)
                    items.append((s,image[y:y+h,x:x+w].copy()))
                    journal.frame('pattern',pattern); journal.frame('camera',image,paper=quad)
                    journal.emit('Gray code',message=f'Patrón {k+1}/44; eje {"xy"[s.axis]}, bit {s.bit}',
                                 pattern_count=k+1,pattern_total=44,progress=(i+(k+1)/44*.5)/11)
                decoded=graycode.decode(items,skip_bits=2); del items
                c,p,_=decoded.points(); c=c+np.array([x,y])
                keep=np.array([cv2.pointPolygonTest(quad.astype(np.float32),tuple(map(float,z)),True)>4 for z in c])
                c,p=c[keep][::10],p[keep][::10]
                plane=metric.paper_scale(quad,size_mm=(210,297),cam_proj=(c,p))
                if plane.quad_proj is None: raise ValueError('Gray code sin homografía inicial')
                canvas,px=adaptive_board(board,(1920,1080),plane.quad_proj)
                image=room.render(canvas)
                journal.frame('pattern',canvas); journal.frame('camera',image,paper=quad)
                journal.emit('ChArUco',message='Patrón dentro de la hoja, muestreado con la lente del proyector.')
                ch=metric.charuco_on_plane(image,board,px)
                pose=metric.PaperPose(plane,ch,name)
                view=joint.PlaneView.from_paper_pose(pose,room.rig['osmo'].size,(1920,1080))
                if len(view.projected_cam)<6: raise ValueError(f'{name}: solo {len(view.projected_cam)} esquinas detectadas')
                journal.frame('camera',image,points=view.projected_cam,paper=view.paper_cam)
                camera_name=f'{name}_camera'; pattern_name=f'{name}_pattern'
                journal.frame(camera_name,image,points=view.projected_cam,paper=view.paper_cam)
                journal.frame(pattern_name,canvas)
                entry={'name':name,'role':role,'corners':len(view.projected_cam),
                    'camera':journal.state['images'][camera_name],'pattern':journal.state['images'][pattern_name],
                    'truth_scene':room.scene_json()}
                journal.state['views'].append(entry)
                journal.emit('Detectar esquinas',message=f'{len(view.projected_cam)} esquinas reales del detector; sin coordenadas ideales.')
                save_view(self.root/'runs'/journal.state['run_id']/'observations'/name/'observation.json',
                          view,role,evidence='synthetic',status='accepted')
                (views if role=='train' else reserved).append(view)
                if role=='train' and len(views)>=4:
                    self.checkpoint()
                    journal.emit('Ajustar rig',message=f'Ajustando {len(views)} vistas; geometría verdadera excluida del solver.')
                    seed=initialize(views,'osmo') if rig is None else rig
                    rig=joint.calibrate(views,seed,distortion='k1',restarts=1,max_nfev=100,evidence='synthetic')
                    journal.estimate(rig,views)
                elif role=='holdout':
                    validation=evaluate_reserved(rig,reserved,[v.name for v in views])
                    journal.emit('Control reservado',validation=validation,
                                 message='Pose excluida del ajuste. Máximos métricos sobre la hoja reservada.')
                else:
                    journal.emit(message='Acumulando vistas: se necesitan cuatro para una primera estimación.')
                time.sleep(.3)
            journal.emit(status='completed',progress=1.,message='Simulación terminada. Revisar residuos y controles; no acredita 2 mm físicos.')
            if rig is not None: rig.save(self.root/'synthetic-rig.json')
        except Exception as exc:
            journal.emit(status='failed',message=str(exc))


def create_app(root,simulation=None):
    from fastapi import FastAPI,Request,HTTPException
    from fastapi.responses import FileResponse,StreamingResponse,JSONResponse
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    root=Path(root); static=Path(__file__).parent.parent/'playground'/'calibration_web'
    def state():
        try: return json.loads((root/'state.json').read_text())
        except FileNotFoundError: return {'status':'waiting','seq':0,'evidence':'live','message':'Esperando captura con --viewer-state.'}
    @app.middleware('http')
    async def local_only(request:Request,call_next):
        host=request.headers.get('host','').split(':')[0]
        if host not in ('127.0.0.1','localhost','testserver'):
            return JSONResponse({'error':'loopback only'},status_code=403)
        if request.method!='GET':
            origin=request.headers.get('origin')
            if origin and origin!=f'http://{request.headers.get("host")}':
                return JSONResponse({'error':'same origin required'},status_code=403)
        response=await call_next(request); response.headers['Cache-Control']='no-store'
        return response
    @app.get('/')
    async def index(): return FileResponse(static/'index.html')
    @app.get('/assets/{name}')
    async def asset(name:str):
        if name not in ('app.js','style.css'): raise HTTPException(404)
        return FileResponse(static/name)
    @app.get('/vendor/{name}')
    async def vendor(name:str):
        if name not in ('three.module.js','OrbitControls.js'): raise HTTPException(404)
        path=root/'vendor'/name
        if not path.exists(): raise HTTPException(404)
        return FileResponse(path,media_type='text/javascript')
    @app.get('/api/state')
    async def get_state(): return state()
    @app.get('/data/{name}')
    async def geometry(name:str):
        if name!='mesh.json' or not (root/name).is_file():raise HTTPException(404)
        return FileResponse(root/name,media_type='application/json')
    @app.get('/images/{name}')
    async def image(name:str):
        import re
        if not re.fullmatch(r'[a-z0-9_]+\.jpg',name) or not (root/name).exists(): raise HTTPException(404)
        return FileResponse(root/name,media_type='image/jpeg')
    @app.get('/api/events')
    async def events(request:Request):
        async def stream():
            last=None
            while not await request.is_disconnected():
                data=state(); key=(data.get('run_id'),data.get('seq'))
                if key!=last:
                    yield f'data: {json.dumps(data,allow_nan=False)}\n\n'; last=key
                else: yield ': heartbeat\n\n'
                await asyncio.sleep(.25)
        return StreamingResponse(stream(),media_type='text/event-stream')
    @app.post('/api/simulation/{action}')
    async def control(action:str,request:Request):
        if simulation is None: raise HTTPException(403,'visor de captura: solo lectura')
        if state().get('evidence')!='synthetic': raise HTTPException(409,'captura real activa: simulación deshabilitada')
        if action=='pause': simulation.paused.set()
        elif action=='resume': simulation.paused.clear()
        elif action=='start':
            data=await request.json(); allowed={'read_noise_dn':(0,8),'camera_blur_px':(0,2),'exposure_jitter':(0,.08),'throw_ratio':(.8,1.6)}
            if not isinstance(data,dict) or any(k not in allowed or not isinstance(v,(int,float)) or not allowed[k][0]<=v<=allowed[k][1] for k,v in data.items()):
                raise HTTPException(400,'parámetros fuera de rango')
            if not simulation.start(data): raise HTTPException(409,'ya hay simulación en curso')
        else: raise HTTPException(404)
        return {'ok':True,'paused':simulation.paused.is_set()}
    return app


def prepare_assets(root):
    """Fetch only pinned public library code, once; browser then stays offline."""
    import urllib.request
    directory=Path(root)/'vendor'; directory.mkdir(parents=True,exist_ok=True)
    for name,path in [('three.module.js','build/three.module.js'),('OrbitControls.js','examples/jsm/controls/OrbitControls.js')]:
        out=directory/name
        if not out.exists():
            with urllib.request.urlopen('https://cdn.jsdelivr.net/npm/three@0.160.0/'+path,timeout=30) as response:
                data=response.read()
            out.write_bytes(data)


def serve_command(args):
    import uvicorn
    root=Path(args.state)
    prepare_assets(root)
    if getattr(args,'scene',False):
        from .scene_viewer import SceneSimulation
        simulation=SceneSimulation(root) if args.simulate else None
    else:
        simulation=Simulation(root) if args.simulate else None
    if simulation: simulation.start()
    print(f'Visor local: http://127.0.0.1:{args.port}/',flush=True)
    uvicorn.run(create_app(root,simulation),host='127.0.0.1',port=args.port,log_level='warning')
    return 0

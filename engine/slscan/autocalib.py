"""Pipeline entry point for scene self-calibration; hardware is opt-in.

stdout is JSONL. Replay uses the exact same estimator as a live capture.
Metric scale and its source are separate from optical self-calibration.
"""
import contextlib
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import cv2
import numpy as np
from . import graycode,joint,selfcalib
from .metric_session import write_json,check_frame


def event(step,progress,msg,ok=True,**fields):
    print(json.dumps(dict(stage='autocalib',step=step,progress=float(progress),msg=msg,ok=ok,**fields),
                     ensure_ascii=False,allow_nan=False),flush=True)


def add_parser(subparsers):
    p=subparsers.add_parser('autocalib',help='autocalibración de escena sin hoja; captura explícita o replay')
    p.add_argument('session');p.add_argument('--out',required=True)
    p.add_argument('--scale',help='huincha:MM (baseline óptico) o teclas:MM (segmento identificado)')
    p.add_argument('--scale-pixels',help='x1,y1,x2,y2 del segmento medido en la primera imagen de cámara')
    p.add_argument('--scale-uncertainty-mm',type=float)
    p.add_argument('--display-confirmed',action='store_true',help='turno físico, pantalla 1:1 y óptica fija autorizados')
    p.add_argument('--replay',action='store_true',help='usar view_XX_map.npz de la sesión, sin hardware')
    p.add_argument('--views',type=int,choices=[1,2,3],default=1)
    p.add_argument('--motion',choices=['camera','rigid'],
                   help='live: rígido por defecto; replay: usar modelo registrado en sesión')
    p.add_argument('--settle',type=float,default=2.)
    p.add_argument('--cam-size',default='3840x2160',help='tamaño pedido a la Osmo')
    p.add_argument('--rotate',type=int,default=270,choices=[0,90,180,270],help='rotación del cuadro (270: Osmo vertical; 0: horizontal/cenital)')
    p.add_argument('--downscale',type=int,default=2,help='reducción del cuadro (1 = 4K completo)')
    p.add_argument('--viewer-state',help='carpeta servida por el visor local 8797')
    p.add_argument('--camera-focal-prior',type=float)
    p.add_argument('--projector-focal-prior',type=float)
    p.add_argument('--max-nfev',type=int,default=650)
    p.set_defaults(fn=command)


def capture(args,root,observer):
    # Import only after command validates explicit hardware permission.
    from .capture import Camera,PatternWindow,list_displays,resolve_screen,capture_sequence
    if root.exists() and any(root.iterdir()): raise ValueError('usar sesión nueva para captura')
    root.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(root).free<2*1024**3: raise ValueError('reservar 2 GiB libres antes de capturar')
    target=next(d for d in list_displays() if d['index']==resolve_screen('1920x1080'))
    if (target['w'],target['h'])!=(1920,1080) or (target.get('pixel_w',1920),target.get('pixel_h',1080))!=(1920,1080):
        raise ValueError('el proyector debe estar a 1920x1080 físico/lógico 1:1')
    cam=win=None;maps=[]
    motion=getattr(args,'motion',None) or 'rigid'
    from .metric_session import frame_size
    raw_size=tuple(int(v) for v in args.cam_size.lower().split('x'))
    expected=frame_size(raw_size,args.rotate,args.downscale)
    metadata={'evidence':'live','camera_size':list(expected),'projector_size':[1920,1080],
              'camera_requested_size':list(raw_size),'rotate':args.rotate,'downscale':args.downscale,'views':args.views,
              'display':target,'status':'running','scene_static_required':True,
              'projector_fixed_required':motion=='camera','motion':motion,
              'fixed_camera_projector_transform_required':motion=='rigid',
              'fixed_intrinsics_required':True}
    try:
        with contextlib.redirect_stdout(sys.stderr):
            win=PatternWindow((1920,1080),screen='1920x1080',fullscreen=True)
            cam=Camera('osmo',size=args.cam_size,rotate=args.rotate,downscale=args.downscale,name='osmo')
        check_frame(cam.read(),expected);metadata['camera']=cam.metadata()
        write_json(root/'autocalib-session.json',metadata)
        for v in range(args.views):
            if v:
                # Explicit human motion, never pretend a second static scan is
                # an independent camera view. Launcher may send "continue".
                guide=np.full((1080,1920),180,np.uint8)
                instruction=('Mueve camara Y proyector juntos 20-30 cm.' if motion=='rigid'
                             else 'Mueve solo la camara hacia un lado.')
                message=('Mueve el conjunto completo cámara y proyector 20 a 30 centímetros hacia un lado; '
                         'cambia un poco el ángulo y la altura. Mantén la misma escena visible. '
                         'No cambies zoom ni muevas objetos. Déjalo quieto y confirma.' if motion=='rigid'
                         else 'Mueve solo la cámara hacia un lado. Cambia el ángulo. Déjala quieta y confirma.')
                if motion=='rigid' and v==2:
                    instruction='Lleva TODO el rig al otro lado del inicio.'
                    message=('Mueve el conjunto completo cámara y proyector al otro lado de la posición inicial, '
                             'unos 40 a 60 centímetros desde donde está ahora. Varía algo la altura y apunta '
                             'a los mismos objetos. No cambies zoom. Esta será la posición de trabajo; '
                             'déjalo quieto y confirma.')
                for row,text in enumerate([instruction,
                                          'Cambia un poco el angulo.',
                                          'Dejala quieta. Confirma para seguir.']):
                    cv2.putText(guide,text,(40,80+row*65),cv2.FONT_HERSHEY_SIMPLEX,1.2,0,2,cv2.LINE_AA)
                win.show(guide)
                if observer:
                    observer.frame('pattern',guide)
                    observer.emit('Preparar escena',pose=v+1,message=message)
                event('move_camera',v/args.views,message,
                      view=v+1,motion=motion,final_view=v==args.views-1,stdin_action='continue')
                # Keep SDL responsive while the launcher waits for Max.
                import queue,threading
                replies=queue.Queue(maxsize=1)
                threading.Thread(target=lambda:replies.put(sys.stdin.readline()),daemon=True).start()
                while True:
                    try: answer=replies.get(timeout=.1).strip();break
                    except queue.Empty:
                        if hasattr(win,'pg'):
                            for e in win.pg.event.get():
                                if e.type==win.pg.QUIT: raise ValueError('ventana cerrada por operador')
                if answer not in ('continue','{"action":"continue"}'):
                    raise ValueError('movimiento de cámara no confirmado; captura detenida')
            folder=root/f'view_{v:02d}'
            folder.mkdir(parents=True,exist_ok=True)
            win.solid(180);time.sleep(args.settle);cam.flush(5)
            white=check_frame(cam.read(),expected)
            if not cv2.imwrite(str(folder/'color.png'),white): raise IOError('no se guardó referencia de color')
            if observer:
                observer.frame(f'view_{v:02d}_camera',white)
                observer.frame(f'view_{v:02d}_pattern',np.full((1080,1920),180,np.uint8))
                observer.state['views'].append({'name':f'view_{v:02d}','role':'scene','corners':0,
                    'camera':observer.state['images'][f'view_{v:02d}_camera'],
                    'pattern':observer.state['images'][f'view_{v:02d}_pattern']})
            specs=graycode.sequence((1920,1080))
            def observed(spec,camera,image):
                check_frame(image,expected)
                progress=(v+(spec.index+1)/len(specs))/args.views*.65
                event('capture',progress,f'Vista {v+1}: patrón {spec.index+1}/{len(specs)}',view=v+1)
                if observer:
                    observer.frame('camera',image)
                    observer.frame('pattern',graycode.pattern((1920,1080),spec.axis,spec.bit,spec.inverse))
                    observer.emit('Gray code',pose=v+1,progress=progress,
                                  pattern_count=spec.index+1,pattern_total=len(specs),message=f'Capturando vista {v+1}. No muevas nada.')
            capture_sequence(win,[cam],specs,lambda s:graycode.pattern((1920,1080),s.axis,s.bit,s.inverse),
                             folder,args.settle,flush=5,observe=observed)
            items=graycode.load_sequence(folder/'osmo');dec=graycode.decode(items,skip_bits=2)
            dec.save(root/f'view_{v:02d}_map.npz');maps.append(dec)
            for _,path in items: Path(path).unlink()
        metadata['status']='captured'
    except BaseException:
        metadata['status']='failed';raise
    finally:
        if cam is not None: cam.close()
        if win is not None: win.close()
        write_json(root/'autocalib-session.json',metadata)
    return maps,metadata


def metric_scale(args,rig,maps):
    if not args.scale: raise ValueError('falta escala externa: --scale huincha:MM o teclas:MM con --scale-pixels')
    kind,value=args.scale.split(':',1);length=float(value)
    if kind not in ('huincha','teclas') or not np.isfinite(length) or length<=0: raise ValueError('escala inválida')
    if args.scale_pixels:
        if not maps: raise ValueError('la escala por segmento requiere mapas Gray de la sesión')
        xy=np.asarray([float(s) for s in args.scale_pixels.split(',')]).reshape(2,2)
        ij=np.rint(xy).astype(int);dec=maps[0]
        if np.any(ij<0) or np.any(ij>=dec.size) or not dec.valid[ij[:,1],ij[:,0]].all():
            raise ValueError('extremos de referencia sin correspondencia Gray válida')
        p=np.column_stack((dec.px[ij[:,1],ij[:,0]],dec.py[ij[:,1],ij[:,0]])).astype(float)
        if dec.stats.get('skip_bits',0): p-=.5
        points=joint.triangulate(rig['osmo'],rig.projector(),ij,p)
        if not np.isfinite(points).all() or any(np.any((points@d.R.T+d.t)[:,2]<=0)
                                               for d in (rig['osmo'],rig.projector())):
            raise ValueError('referencia con profundidad inválida')
        unit_length=np.linalg.norm(points[0]-points[1]);source='measured_scene_segment'
    else:
        if kind=='teclas': raise ValueError('teclas:MM requiere --scale-pixels; ancho de tecla y pitch no son intercambiables')
        unit_length=np.linalg.norm(rig.projector().center_world());source='optical_center_baseline'
    if not np.isfinite(unit_length) or unit_length<=0: raise ValueError('referencia no triangulable')
    bound=args.scale_uncertainty_mm
    if bound is not None and (not np.isfinite(bound) or bound<0): raise ValueError('incertidumbre de referencia inválida')
    return length/unit_length,{'source':source,'method':kind,'length_mm':length,
        'uncertainty_bound_mm':bound,'pixels':args.scale_pixels,
        'budget':selfcalib.scale_budget(length,bound) if bound is not None else None,
        'verified':False}


def command(args):
    root=Path(args.session);out=Path(args.out);observer=None
    event('preflight',0,'Autocalibración de escena. Precisión métrica sin verificar.')
    try:
        if out.exists(): raise ValueError('la salida ya existe; usar un rig nuevo, sin sobrescribir el anterior')
        if args.settle<0 or args.max_nfev<1: raise ValueError('settle/max-nfev inválidos')
        if not args.replay and not args.display_confirmed:
            raise ValueError('falta --display-confirmed: esperar confirmación q21 antes de abrir hardware')
        if not args.replay and (args.motion or 'rigid')=='rigid' and args.views<2:
            raise ValueError('rig rígido requiere --views 2 o 3; recomendado 3. Una vista no determina este modelo óptico')
        if args.viewer_state:
            from .calibration_viewer import Journal
            from .scene_viewer import SCENE_STEPS
            observer=Journal(args.viewer_state,'recorded' if args.replay else 'live',args.views)
            observer.emit(steps=SCENE_STEPS,mode='scene',stage=SCENE_STEPS[0])
        if args.replay:
            paths=sorted(root.glob('view_*_map.npz'));maps=[graycode.Decoded.load(path) for path in paths]
            meta_path=root/'autocalib-session.json'
            if not meta_path.exists(): raise ValueError('replay requiere autocalib-session.json con tamaño y procedencia')
            metadata=json.loads(meta_path.read_text())
            if metadata.get('evidence') not in ('synthetic','recorded','live'): raise ValueError('procedencia de sesión inválida')
        else:
            maps,metadata=capture(args,root,observer)
            paths=sorted(root.glob('view_*_map.npz'))
        if not maps: raise ValueError('sesión sin mapas Gray')
        motion=metadata.get('motion','camera')
        if motion not in ('camera','rigid'): raise ValueError('modelo de movimiento de sesión inválido')
        if args.motion is not None and args.motion!=motion:
            raise ValueError('--motion no coincide con la sesión; no reinterpretar un proyector fijo como rig móvil')
        if observer: observer.emit(evidence=metadata['evidence'],total=len(maps))
        if any(tuple(m.size)!=tuple(metadata['camera_size']) for m in maps): raise ValueError('resolución de sesión incompatible')
        if motion=='rigid':
            from .scene_tracks import rigid_tracks
            photo_paths=[root/path.name.removesuffix('_map.npz')/'color.png' for path in paths]
            event('tracks',.68,'Siguiendo puntos físicos en las fotos; Gray distinto en cada posición del rig.')
            c,p,tracking=rigid_tracks(maps,[cv2.imread(str(path)) for path in photo_paths])
        else:
            photo_paths=[]
            event('tracks',.68,'Enlazando celdas del proyector fijo entre vistas.')
            c,p,tracking=selfcalib.dense_tracks(maps)
        kind='puntos físicos comunes' if motion=='rigid' else 'celdas comunes'
        event('coverage',.70,f"{tracking['common_cells']} {kind}; {tracking['sampling']}.",
              coverage=tracking)
        train=np.arange(c.shape[1])%5!=0
        event('bundle',.72,f'Ajustando {len(maps)} vista(s) y {int(train.sum())} tracks. Priors explícitos.')
        rig,X,report,good=selfcalib.fit(c[:,train],p[:,train] if motion=='rigid' else p[train],tuple(metadata['camera_size']),
             tuple(metadata['projector_size']),camera_focal=args.camera_focal_prior,
             projector_focal=args.projector_focal_prior,max_nfev=args.max_nfev,motion=motion)
        report.update(evidence=metadata['evidence'],tracking=tracking)
        reserved=joint.triangulate(rig['osmo'],rig.projector(),c[0,~train],p[0,~train] if motion=='rigid' else p[~train])
        heldout=[];heldout_projector=[];heldout_positive=True
        for i in range(len(maps)):
            device=rig['osmo' if i==0 else f'osmo_{i}']
            heldout.append(joint.stats(np.linalg.norm(joint.project(device,reserved)-c[i,~train],axis=1)))
            pro=selfcalib.projector_at(rig.projector(),device) if motion=='rigid' else rig.projector()
            heldout_projector.append(joint.stats(np.linalg.norm(joint.project(pro,reserved)-
                                (p[i,~train] if motion=='rigid' else p[~train]),axis=1)))
            heldout_positive &= all(np.all((reserved@d.R.T+d.t)[:,2]>0) for d in (device,pro))
        report['heldout']={'tracks':int((~train).sum()),'camera_reprojection_px':heldout,
                           'projector_reprojection_px':heldout_projector,
                           'physical_metric_validation':'unverified'}
        if not np.isfinite(reserved).all() or not heldout_positive or any(r['rms']>2 for r in [*heldout,*heldout_projector]):
            report['issues'].append('heldout_reprojection_above_2px')
        write_json(root/'autocalib-report.json',report)
        fatal={'optimizer_not_converged','negative_depth','nearly_planar_scene','data_rank_deficient','reprojection_above_1px','heldout_reprojection_above_2px'}
        if fatal.intersection(report['issues']):
            raise ValueError('rig no publicable: '+', '.join(sorted(fatal.intersection(report['issues']))))
        factor,scale=metric_scale(args,rig,maps)
        scale['evidence']=metadata['evidence']
        if metadata.get('scale_source'): scale['session_declared_source']=metadata['scale_source']
        for d in rig.devices.values(): d.t*=factor
        for i in range(len(maps)):
            rig['osmo' if i==0 else f'osmo_{i}'].rms=report['per_camera'][i]['rms']
        rig.projector().rms=report['projector_residual']['rms']
        if motion=='rigid':
            rig.devices['proj']=selfcalib.projector_at(rig.projector(),rig[f'osmo_{len(maps)-1}'])
            report['operating_view']=len(maps)-1
        # The scan immediately after calibration uses the camera where Max
        # left it. Keep world at the first view, but name the active last view
        # "osmo" so downstream default selection cannot use the wrong pose.
        if len(maps)>1:
            first=rig.devices.pop('osmo');first.name='osmo_reference'
            active=rig.devices.pop(f'osmo_{len(maps)-1}');active.name='osmo'
            rig.devices[first.name]=first;rig.devices['osmo']=active;rig.world='osmo_reference'
        rig.units='mm';report['scale']=scale;report['units']='mm'
        report['issues'].remove('scale_unmeasured')
        report['issues'].append('external_scale_not_physically_validated')
        hashes={str(path.resolve()):hashlib.sha256(path.read_bytes()).hexdigest() for path in [*paths,*photo_paths]}
        data={'format':'slscan-rig/2','schema_version':'1.0','artifact':'rig','evidence':metadata['evidence'],
            'units':'mm','world':rig.world,'pixel_convention':'opencv_integer_centers',
            'metric_status':'unverified','validated_range':None,'input_hashes':hashes,'devices':{},
            'extra':{'selfcalibration_report':report,'scale_reference':scale}}
        for name,d in rig.devices.items():
            row=d.to_json();row.update(pose_estimated=name!=rig.world,
                model={'free':report['model']['estimated'],'fixed':report['model']['fixed']},
                uncertainty={'observability':report['observability'],'scale_reference':scale},
                residuals_by_view=[{'camera':i,'reprojection_px':r} for i,r in enumerate(report['per_camera'])]
                                 if d.kind=='camera' else [{'reprojection_px':report['projector_residual']}])
            data['devices'][name]=row
        write_json(out,data);write_json(root/'autocalib-report.json',report)
        if observer:
            from .calibration_viewer import device_json
            observer.emit('Ajuste conjunto',status='completed',progress=1,
                active_camera='osmo',pose=len(maps),total=len(maps),
                devices={k:device_json(d) for k,d in rig.devices.items()},cloud={'points':(X*factor).tolist()},
                calibration={'status':'unverified','issues':report['issues'],'rms_px':report['rms_px'],
                             'baseline_mm':float(np.linalg.norm(rig.projector().center_world()-rig['osmo'].center_world())),
                             'observability':report['observability']},
                message='Rig candidato con escala externa. Validación física pendiente.')
        event('rig',1,'Rig candidato guardado. No acredita ≤2 mm.',out=str(out),metric_status='unverified')
        return 0
    except (Exception,KeyboardInterrupt) as exc:
        if observer: observer.emit(status='failed',message=str(exc))
        event('failed',1,str(exc) or 'Captura interrumpida.',False)
        return 2

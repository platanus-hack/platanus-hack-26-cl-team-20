"""Versioned acquisition and offline calibration for the joint metric rig.

No hardware is opened by calibration. Session files contain observations and
roles assigned before fitting; private images stay under the requested reports
directory. The original paper-only solver remains an explicit legacy option.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import time
import cv2
import numpy as np

from . import joint, metric
from .rig import Rig


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False))
    temporary.replace(path)


def frame_size(raw_size, rotate, downscale):
    if downscale < 1 or rotate not in (0,90,180,270):
        raise ValueError('rotate debe ser 0/90/180/270 y downscale >= 1')
    w,h = raw_size
    if w % downscale or h % downscale:
        raise ValueError('la reducción debe dividir el tamaño solicitado')
    size = (w//downscale,h//downscale)
    return size[::-1] if rotate in (90,270) else size


def check_frame(image, expected):
    actual = (image.shape[1],image.shape[0])
    if actual != tuple(expected):
        raise ValueError(f'resolución entregada {actual}, esperada {expected}; no reutilizar K')
    return image


def adaptive_board(board, size, quad):
    """Fit a rectangular ChArUco inside the mapped paper, without resampling it."""
    q = np.asarray(quad, np.float32)
    center = q.mean(0)
    # Grow a centred rectangle while all four corners remain inside the sheet.
    for square in range(int(min(np.ptp(q[:,0])/board.cols,np.ptp(q[:,1])/board.rows)), 5, -1):
        width,height = board.cols*square+4,board.rows*square+4
        x,y = np.floor(center-[width/2,height/2]).astype(int)
        corners = [(x,y),(x+width,y),(x+width,y+height),(x,y+height)]
        if all(cv2.pointPolygonTest(q, (float(a),float(b)), True) > 5 for a,b in corners):
            if x < 0 or y < 0 or x+width > size[0] or y+height > size[1]:
                continue
            canvas = np.full((size[1],size[0]),255,np.uint8)
            canvas[y:y+height,x:x+width] = board.board.generateImage((width,height),marginSize=2)
            # Chessboard junction is at the boundary between neighbouring pixel
            # centres. OpenCV's centre coordinates put that boundary at k-0.5.
            px = board.corners_mm()[:,:2]/board.square_mm*square+[x+1.5,y+1.5]
            return canvas,px
    raise ValueError('hoja demasiado pequeña para ChArUco: acercar soporte o cambiar pose')


def save_view(path, view, role, **metadata):
    write_json(path, {'schema_version':'1.0','artifact':'rig-plane-observation','units':'mm',
        'evidence':metadata.pop('evidence','live'),'pixel_convention':'opencv_integer_centers',
        'name':view.name,'role':role,'cam_size':list(view.cam_size),'proj_size':list(view.proj_size),
        'paper_mm':view.paper_mm.tolist(),'paper_cam':view.paper_cam.tolist(),
        'projected_cam':view.projected_cam.tolist(),'projected_px':view.projected_px.tolist(),
        'sigma_paper_px':view.sigma_paper_px,'sigma_pattern_px':view.sigma_pattern_px, **metadata})


def load_views(folder):
    train, reserved, records = [], [], []
    for path in sorted(Path(folder).glob('pose_*/observation.json')):
        d = json.loads(path.read_text())
        if d['schema_version'] != '1.0' or d['pixel_convention'] != 'opencv_integer_centers':
            raise ValueError(f'{path}: contrato o convención incompatible')
        if d.get('status') != 'accepted':
            raise ValueError(f'{path}: pose rechazada; adquirir una sesión válida')
        view = joint.PlaneView(d['name'],*[np.array(d[k],float) for k in
            ('paper_mm','paper_cam','projected_cam','projected_px')],tuple(d['cam_size']),tuple(d['proj_size']),
            d.get('sigma_paper_px',.25),d.get('sigma_pattern_px',.25))
        if d['role'] not in ('train','holdout'):
            raise ValueError('pose sin rol train/holdout')
        (train if d['role']=='train' else reserved).append(view)
        records.append({'path':str(path.relative_to(folder)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                        'evidence':d['evidence']})
    if not train:
        raise ValueError('sin observaciones v1: usar metric capture; el formato legado requiere --solver legacy')
    sizes = {(v.cam_size,v.proj_size) for v in train+reserved}
    if len(sizes) != 1:
        raise ValueError('resolución diferente entre poses')
    if len({v.name for v in train+reserved}) != len(train+reserved):
        raise ValueError('identificadores de poses duplicados')
    return train,reserved,records


def initialize(views, name):
    """Old homographies only seed the joint model, never enter its residuals."""
    poses = []
    for v in views:
        pl = metric.paper_scale(v.paper_cam, size_mm=(v.paper_mm[1,0],v.paper_mm[2,1]),
                                cam_proj=(v.projected_cam,v.projected_px))
        xy = pl.cam_to_mm(v.projected_cam)
        ch = {'cam':v.projected_cam,'proj':v.projected_px,'obj':np.column_stack((xy,np.zeros(len(xy))))}
        poses.append(metric.PaperPose(pl,ch,v.name))
    rig = metric.calibrate_from_paper(poses, views[0].cam_size, views[0].proj_size,
                                      cam_name=name,repeat=0)
    # Match physical corner ordering chosen by the initializer; no evaluation
    # observations are ever used to select this ordering or any lens parameters.
    turned = set(rig.extra['metric_report']['reoriented'])
    for v,p in zip(views,poses):
        if v.name in turned:
            p = metric.turn_pose(p)
        v.paper_cam = p.plane.quad_cam.copy()
    return rig


def evaluate_reserved(rig, views, training_ids=(), uncertainty_mm=None):
    """Independent sheet dimensions and planarity, with no fitted scale/offset.

    Fit an unconstrained plane to triangulated projected points, then intersect
    observed physical corners with it. The nominal dimensions are used ONLY
    for evaluation. No homography-derived projector paper corners are accepted.
    This does not evaluate projected object outlines or certify a room volume.
    """
    cam,pro = rig[rig.world],rig.projector()
    results = []
    for v in views:
        if v.name in training_ids:
            raise ValueError('fuga de validación: pose usada para entrenar')
        if v.cam_size != cam.size or v.proj_size != pro.size:
            raise ValueError('resolución de evaluación incompatible')
        X = joint.triangulate(cam,pro,v.projected_cam,v.projected_px)
        valid = np.isfinite(X).all(1) & (X[:,2]>0) & ((X@pro.R.T+pro.t)[:,2]>0)
        if not valid.all() or len(X)<6:
            results.append({'name':v.name,'status':'failed','reason':'invalid_triangulation',
                            'input_count':len(X),'invalid_count':int((~valid).sum())}); continue
        center = X.mean(0)
        _,_,vt = np.linalg.svd(X-center,full_matrices=False)
        normal = vt[-1]
        # intersect only needs column 2 (the normal), not a full plane frame.
        R = np.eye(3); R[:,2] = normal
        corners,depth = joint.intersect(cam,v.paper_cam,R,center)
        pairs = [(0,1),(1,2),(2,3),(3,0),(0,2),(1,3)]
        measured = np.array([np.linalg.norm(corners[a]-corners[b]) for a,b in pairs])
        nominal = np.array([np.linalg.norm(v.paper_mm[a]-v.paper_mm[b]) for a,b in pairs])
        # A blank rectangle can be ordered short-side-first or long-side-first.
        # The orientation is selected by the physical observations at capture.
        err = abs(measured-nominal)
        plane = abs((X-center)@normal)
        worst = max(float(err.max()),float(plane.max()))
        status = 'failed' if worst>2 or (depth<=0).any() else 'inconclusive'
        if uncertainty_mm is not None and uncertainty_mm >= 0 and worst+uncertainty_mm <= 2 and (depth>0).all():
            status = 'passed_local_geometry'
        results.append({'name':v.name,'status':status,'sides_and_diagonals_mm':measured.tolist(),
                        'dimension_error_mm':joint.stats(err),'planarity_mm':joint.stats(plane),
                        'before_filters':{'count':len(X),'planarity_mm':joint.stats(plane)},
                        'after_filters':{'count':len(X),'planarity_mm':joint.stats(plane)},
                        'rejected_fraction':0.,'uncertainty_bound_mm':uncertainty_mm,
                        'xyz_bounds_mm':[X.min(0).tolist(),X.max(0).tolist()]})
    return {'schema_version':'1.0','artifact':'rig-heldout-validation','units':'mm',
            'evidence':rig.extra.get('evidence','recorded'),'views':results,
            'status':'failed' if any(v['status']=='failed' for v in results) else 'unverified',
            'reason':'three repeats, second independent calibration and physical outline checks required',
            'reserved_poses_required':3,'reserved_poses_available':len(views),'tolerance_mm':2.,
            'scope':'sampled sheets only; no whole-scene absolute 3D certificate'}


def calibrate_command(args):
    train,reserved,records = load_views(Path(args.folder))
    initial = Rig.load(args.initial_rig) if args.initial_rig else initialize(train,args.name)
    evidence = {r['evidence'] for r in records}
    if len(evidence) != 1:
        raise ValueError('no mezclar evidencia sintética y física en una calibración')
    rig = joint.calibrate(train,initial,distortion=args.distortion,restarts=args.restarts,
                          bootstrap=args.repeat,evidence=evidence.pop())
    rig.extra['provenance'] = {'observations':records,'session':str(Path(args.folder).resolve())}
    rep = rig.extra['metric_report']
    validation = evaluate_reserved(rig,reserved,rep['training_view_ids'],args.uncertainty_mm)
    rep['heldout'] = validation
    if validation['status']=='failed':
        rep['status']='rejected'; rep['issues'].append('heldout_geometry_failed')
    if getattr(args,'viewer_state',None):
        from .calibration_viewer import Journal
        observer=Journal(args.viewer_state,evidence=rig.extra['evidence'],total=len(train)+len(reserved),resume=True)
        observer.estimate(rig,train)
        observer.emit('Control reservado',validation=validation,status='completed',progress=1,
                      message='Ajuste final publicado en el visor. Precisión física sin verificar.')
    if args.report:
        write_json(args.report,rep)
    if rep['status']=='rejected':
        print('Rig rechazado: '+', '.join(rep['issues'])+'. No se publica rig.json.',flush=True)
        return 2
    Path(args.out).parent.mkdir(parents=True,exist_ok=True)
    rig.save(args.out)
    if args.html:
        from .recon import write_ply
        from .viewer import write_html
        points = [joint.triangulate(rig[rig.world],rig.projector(),v.projected_cam,v.projected_px) for v in train]
        colors = [np.tile(np.array([40+17*i%200,180,220]),(len(x),1)) for i,x in enumerate(points)]
        ply = Path(args.html).with_suffix('.ply'); ply.parent.mkdir(parents=True,exist_ok=True)
        write_ply(ply,np.concatenate(points),np.concatenate(colors))
        write_html(args.html,[('poses de calibración',ply)],rig,title='Rig candidato; ≤2 mm sin verificar')
    print(f"Rig candidato: {args.out}; RMS cámara {rep['rms_px']['cam']:.4f} px, "
          f"proyector {rep['rms_px']['proj']:.4f} px; validación física pendiente.",flush=True)
    return 0


def capture_command(args):
    from .capture import Camera, PatternWindow, capture_sequence, list_displays, resolve_screen
    from .calib import CharucoBoard
    from . import graycode
    size = tuple(map(int,args.size.split('x')))
    raw_size = tuple(map(int,args.cam_size.split('x')))
    expected = frame_size(raw_size,args.rotate,args.downscale)
    paper_size = tuple(map(float,args.paper_mm.split('x')))
    if args.windowed:
        raise ValueError('calibración métrica exige display 1:1; --windowed no permitido')
    if args.poses < 4 or args.holdout < 3:
        raise ValueError('se requieren >=4 poses train y >=3 holdout; sugeridas 10+3')
    if not args.display_confirmed:
        raise ValueError('confirmar T749 1:1, keystone/zoom fijo y turno libre con --display-confirmed')
    displays = list_displays()
    index = resolve_screen(args.screen)
    target = next(d for d in displays if d['index']==index)
    if (target['w'],target['h']) != size:
        raise ValueError('display y patrón deben tener idéntica resolución')
    if (target.get('pixel_w',size[0]),target.get('pixel_h',size[1])) != size:
        raise ValueError('display escalado: píxeles físicos y patrón difieren')
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        raise ValueError('usar carpeta nueva: no mezclar ni sobrescribir sesiones')
    out.mkdir(parents=True,exist_ok=True)
    minimum_free = (2 if args.keep_images else 1)*1024**3
    if shutil.disk_usage(out).free < minimum_free:
        raise ValueError('disco insuficiente: reservar >=1 GiB libre (2 GiB con --keep-images) antes de capturar')
    session = {'schema_version':'1.0','artifact':'rig-capture-session','units':'mm','evidence':'live',
               'pixel_convention':'opencv_integer_centers','paper_size_mm':list(paper_size),
               'paper_support':'flat, confirmed by operator','reference_uncertainty_mm':args.uncertainty_mm,
               'train_count':args.poses,'holdout_count':args.holdout,'projector_size':list(size),
               'camera_expected_size':list(expected),'display':target,'status':'running','poses':[]}
    board = CharucoBoard.from_args(args)
    observer=None; observed_views=[]; observed_reserved=[]; preview_rig=None
    if getattr(args,'viewer_state',None):
        from .calibration_viewer import Journal
        observer=Journal(args.viewer_state,'live',args.poses+args.holdout)
    win = cam = None
    try:
        win = PatternWindow(size,screen=args.screen,fullscreen=True)
        cam = Camera(args.camera,size=args.cam_size,rotate=args.rotate,downscale=args.downscale,name=args.name)
        # Validate a real delivered frame, not CAP_PROP_FRAME_* alone.
        check_frame(cam.read(),expected)
        session['camera'] = cam.metadata()
        write_json(out/'session.json',session)
        for i in range(args.poses+args.holdout):
            name = f'pose_{i:02d}'
            role = 'train' if i<args.poses else 'holdout'
            if observer:
                observer.emit('Preparar pose',pose=i+1,progress=i/(args.poses+args.holdout),
                              message=f'{name}: esperando hoja quieta; {role}.')
            guide = np.full((size[1],size[0]),args.white_level,np.uint8)
            directions = ['Centro, inclinacion hacia arriba', 'Centro, inclinacion hacia abajo',
                          'Izquierda, inclinacion lateral', 'Derecha, inclinacion lateral opuesta',
                          'Mas cerca, inclinacion en ambos ejes', 'Mas lejos, inclinacion en ambos ejes',
                          'Borde superior, hoja completa visible', 'Borde inferior, hoja completa visible',
                          'Otra zona cerca, inclinacion distinta', 'Otra zona lejos, inclinacion distinta']
            instruction = directions[i%len(directions)] if role=='train' else [
                'CONTROL: cerca y centro', 'CONTROL: lejos y borde', 'CONTROL: otra inclinacion y zona'][(i-args.poses)%3]
            for row,text in enumerate([f'{name} / {role}',instruction,
                                        'Hoja plana sobre soporte. Dejar quieta y confirmar al agente.']):
                cv2.putText(guide,text,(35,50+row*38),cv2.FONT_HERSHEY_SIMPLEX,.8,0,2,cv2.LINE_AA)
            win.show(guide)
            reply = input(f'{name} [{role}]: hoja {paper_size} mm en soporte plano; variar inclinación, '
                          'zona y distancia. Dejar quieta. ENTER captura, q termina: ')
            if reply.strip().lower()=='q':
                session['status']='incomplete'; break
            win.solid(args.white_level)  # guide must not contaminate observations
            folder = out/name/args.name; folder.mkdir(parents=True)
            def snap(filename):
                time.sleep(args.settle); cam.flush(args.flush)
                img = check_frame(cam.read(),expected)
                if not cv2.imwrite(str(folder/filename),img):
                    raise IOError(f'no se pudo guardar {filename}')
                return img
            white = snap('white.png')
            quad = metric.detect_paper(white,aspect=max(paper_size)/min(paper_size))
            if quad is None:
                raise ValueError(f'{name}: no se ve la hoja; recapturar en sesión nueva')
            if observer:
                observer.frame('pattern',np.full((size[1],size[0]),args.white_level,np.uint8))
                observer.frame('camera',white,paper=quad)
                observer.emit('Blanco',message='Hoja física detectada en la cámara.')
            specs = graycode.sequence(size)
            def observed_pattern(spec,camera,image):
                if observer:
                    observer.frame('pattern',graycode.pattern(size,spec.axis,spec.bit,spec.inverse))
                    observer.frame('camera',image,paper=quad)
                    observer.emit('Gray code',pattern_count=spec.index+1,pattern_total=len(specs),
                                  message=f'Patrón {spec.index+1}/{len(specs)} capturado.')
            capture_sequence(win,[cam],specs,lambda s:graycode.pattern(size,s.axis,s.bit,s.inverse),
                             folder.parent,settle_s=args.settle,flush=args.flush,observe=observed_pattern)
            items = graycode.load_sequence(folder)
            dec = graycode.decode(items,threshold=args.threshold,skip_bits=args.skip_bits)
            if tuple(dec.size) != expected:
                raise ValueError('resolución cambió durante Gray code')
            dec.save(folder/'map.npz')
            plane = metric.paper_scale(quad,dec,size_mm=paper_size)
            if plane.quad_proj is None:
                raise ValueError('Gray code sin correspondencias suficientes en la hoja')
            canvas,px = adaptive_board(board,size,plane.quad_proj)
            write_json(folder/'pattern.json',{'schema_version':'1.0','artifact':'projected-pattern','evidence':'live',
                'units':'px','pixel_convention':'opencv_integer_centers','corners_px':px.tolist(),
                'board':args.board,'dict':args.dict,'size':list(size)})
            win.show(canvas)
            image = snap('board.png')
            if observer:
                observer.frame('pattern',canvas); observer.frame('camera',image,paper=quad)
                observer.emit('ChArUco',message='Patrón proyectado sobre la hoja física.')
            # No homography residual gate: lens distortion is precisely what we
            # need to estimate. Membership is determined by the physical sheet.
            ch = metric.charuco_on_plane(image,board,px)
            pose = metric.PaperPose(plane,ch,name)
            view = joint.PlaneView.from_paper_pose(pose,expected,size)
            if observer:
                observer.frame('camera',image,points=view.projected_cam,paper=view.paper_cam)
                observer.frame(f'{name}_camera',image,points=view.projected_cam,paper=view.paper_cam)
                observer.frame(f'{name}_pattern',canvas)
                observer.state['views'].append({'name':name,'role':role,'corners':len(view.projected_cam),
                    'camera':observer.state['images'][f'{name}_camera'],
                    'pattern':observer.state['images'][f'{name}_pattern']})
                observer.emit('Detectar esquinas',message=f'{len(view.projected_cam)} esquinas detectadas.')
            win.solid(args.white_level)
            after = snap('white_after.png')
            after_quad = metric.detect_paper(after,aspect=max(paper_size)/min(paper_size))
            if after_quad is None:
                raise ValueError('hoja perdida al final de la pose')
            motion = float(np.max(np.linalg.norm(metric.order_quad(quad)-metric.order_quad(after_quad),axis=1)))
            mask = np.zeros(white.shape[:2],np.uint8)
            cv2.fillConvexPoly(mask,quad.astype(np.int32),255)
            mask = cv2.erode(mask,np.ones((9,9),np.uint8))>0
            luminance = cv2.cvtColor(white,cv2.COLOR_BGR2GRAY)[mask].astype(float)
            last = cv2.cvtColor(after,cv2.COLOR_BGR2GRAY)[mask].astype(float)
            drift = float(abs(np.median(last)-np.median(luminance))/max(1,np.median(luminance)))
            saturated = float(np.mean(luminance>=250))
            issues = []
            if motion>.5: issues.append('paper_moved')
            if drift>.03: issues.append('photometric_drift')
            if saturated>.05: issues.append('paper_saturated')
            if len(view.projected_cam)<6: issues.append('too_few_projected_corners')
            status = 'rejected' if issues else 'accepted'
            save_view(out/name/'observation.json',view,role,status=status,issues=issues,
                checks={'max_corner_motion_px':motion,'white_median_drift_fraction':drift,
                        'white_saturated_fraction':saturated,
                        'limits':{'motion_px':.5,'drift_fraction':.03,'saturation_fraction':.05},
                        'optical_locks':'unverified; start/end check only'})
            session['poses'].append({'name':name,'role':role,'status':status})
            write_json(out/'session.json',session)
            # Only temporary frames produced in this newly created session.
            if not args.keep_images:
                for _,f in items:
                    Path(f).unlink()
            print(f'{name}: {status}; {len(view.projected_cam)} esquinas; movimiento {motion:.3f} px',flush=True)
            if issues:
                raise ValueError('pose rechazada: '+', '.join(issues))
            if observer:
                (observed_views if role=='train' else observed_reserved).append(view)
                if len(observed_views)>=4:
                    try:
                        if role=='train':
                            observer.emit('Ajustar rig',message='Estimación provisional con las vistas observadas.')
                            seed=initialize(observed_views,args.name) if preview_rig is None else preview_rig
                            preview_rig=joint.calibrate(observed_views,seed,distortion='k1',restarts=1,max_nfev=100,evidence='live')
                            observer.estimate(preview_rig,observed_views)
                        elif preview_rig is not None:
                            observer.emit('Control reservado',validation=evaluate_reserved(preview_rig,observed_reserved,
                                          [v.name for v in observed_views]),message='Evaluando pose fuera del ajuste.')
                    except (ValueError,cv2.error) as exc:
                        observer.emit(message=f'Vista guardada; estimación provisional no disponible: {exc}')
        else:
            session['status']='captured'
    except Exception as exc:
        session['status']='failed'; session['reason']=str(exc)
        if observer: observer.emit(status='failed',message=str(exc))
        raise
    finally:
        if cam is not None: cam.close()
        if win is not None: win.close()
        write_json(out/'session.json',session)
    if observer:
        observer.emit(status='completed' if session['status']=='captured' else 'incomplete',
                      progress=1 if session['status']=='captured' else observer.state['progress'],
                      message='Captura terminada. Falta ajuste final y validación física.')
    return 0 if session['status']=='captured' else 2

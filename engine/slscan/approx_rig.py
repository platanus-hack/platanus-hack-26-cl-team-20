"""One-view approximate rig with fixed projector optics and centered camera.

This is an explicitly assumed optical model for visualization, not calibration
of the hardware. Unknown scale stays in projector-baseline units.
"""
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np
from scipy.optimize import least_squares,minimize_scalar
from scipy.sparse import lil_matrix
from .rig import Device,Rig
from .joint import project,triangulate,stats
from .selfcalib import intrinsic,dense_tracks
from .graycode import Decoded
from .metric_session import write_json
from .autocalib import event


def fixed_projector(size,throw,offset_percent=100.):
    """100% means the lens axis is at bottom edge of an upright desktop image."""
    w,h=size
    K=intrinsic(w*throw,size,((w-1)/2,(h-1)/2+h*offset_percent/200))
    return Device('proj','projector',tuple(size),K,np.zeros(5))


def fit(camera,projector,camera_size,pro,max_nfev=650):
    c=np.asarray(camera,float);p=np.asarray(projector,float)
    F,mask=cv2.findFundamentalMat(c,p,cv2.FM_RANSAC,1.5,.999)
    if F is None or F.shape!=(3,3): raise ValueError('fundamental no estimable')
    inlier=mask.ravel().astype(bool)
    if inlier.sum()<40: raise ValueError('menos de 40 correspondencias epipolares consistentes')
    c,p=c[inlier],p[inlier]
    focal_range=np.array([.25,5.])*max(camera_size)
    def essential_cost(logf):
        E=pro.K.T@F@intrinsic(np.exp(logf),camera_size)
        s=np.linalg.svd(E,compute_uv=False)
        return ((s[0]-s[1])/max(s[0]+s[1],1e-15))**2
    initial=minimize_scalar(essential_cost,bounds=np.log(focal_range),method='bounded')
    fc=np.exp(initial.x);Kc=intrinsic(fc,camera_size)
    E=pro.K.T@F@Kc
    cn=cv2.undistortPoints(c[:,None],Kc,None).reshape(-1,2)
    pn=cv2.undistortPoints(p[:,None],pro.K,None).reshape(-1,2)
    _,R,t,_=cv2.recoverPose(E,cn,pn,np.eye(3));t=t.ravel()
    cam=Device('osmo','camera',tuple(camera_size),Kc,np.zeros(5))
    initial_pro=Device('proj','projector',pro.size,pro.K,pro.dist,R,t)
    X=triangulate(cam,initial_pro,c,p)
    positive=np.isfinite(X).all(1)&(X[:,2]>.01)&(X[:,2]<300)&((X@R.T+t)[:,2]>.01)
    if positive.sum()<40: raise ValueError('menos de 40 puntos con profundidad positiva')
    c,p,X=c[positive],p[positive],X[positive];n=len(X)
    start=np.r_[np.log(fc),cv2.Rodrigues(R)[0].ravel(),np.arctan2(t[1],t[0]),
                np.arcsin(np.clip(t[2],-1,1)),X.ravel()]
    def unpack(v):
        camera=Device('osmo','camera',tuple(camera_size),intrinsic(np.exp(v[0]),camera_size),np.zeros(5))
        trans=np.array([np.cos(v[5])*np.cos(v[4]),np.cos(v[5])*np.sin(v[4]),np.sin(v[5])])
        projector=Device('proj','projector',pro.size,pro.K,pro.dist,cv2.Rodrigues(v[1:4])[0],trans)
        return camera,projector,v[6:].reshape(-1,3)
    def residual(v):
        camera,projector,points=unpack(v)
        return np.r_[(project(camera,points)-c).ravel(),(project(projector,points)-p).ravel()]
    sparse=lil_matrix((4*n,len(start)),dtype=np.int8)
    for block in range(2):
        for i in range(n):
            rows=slice(2*(block*n+i),2*(block*n+i)+2)
            sparse[rows,:6]=1;sparse[rows,6+3*i:9+3*i]=1
    lower=np.full(len(start),-1000.);upper=np.full(len(start),1000.)
    lower[0],upper[0]=np.log(focal_range);lower[8::3]=.001
    # First eliminate XYZ through triangulation. Refining six global parameters
    # avoids hundreds of tiny sparse bundle steps along the focal/pose valley.
    def profile(v):
        camera,projector,_=unpack(np.r_[v,start[6:]])
        points=triangulate(camera,projector,c,p)
        return np.r_[(project(camera,points)-c).ravel(),(project(projector,points)-p).ravel()]
    preliminary=least_squares(profile,start[:6],bounds=(lower[:6],upper[:6]),
                              loss='soft_l1',f_scale=1.,max_nfev=max_nfev,x_scale='jac')
    pc,pp,_=unpack(np.r_[preliminary.x,start[6:]])
    initial_points=triangulate(pc,pp,c,p)
    if np.isfinite(initial_points).all() and np.all(initial_points[:,2]>.001):
        start=np.r_[preliminary.x,initial_points.ravel()]
    result=least_squares(residual,start,jac_sparsity=sparse.tocsr(),bounds=(lower,upper),
                         loss='soft_l1',f_scale=1.,x_scale='jac',max_nfev=max_nfev,
                         ftol=1e-5,xtol=1e-6,gtol=1e-5,
                         tr_options={'atol':1e-9,'btol':1e-9,'maxiter':200})
    cam,pro,X=unpack(result.x)
    errors=[stats(np.linalg.norm(project(d,X)-v,axis=1)) for d,v in [(cam,c),(pro,p)]]
    J=result.jac.toarray();reduced=[]
    for i in range(n):
        rows=[2*i,2*i+1,2*(n+i),2*(n+i)+1]
        A=J[rows,:6];B=J[rows,6+3*i:9+3*i]
        reduced.append(A-B@np.linalg.lstsq(B,A,rcond=None)[0])
    reduced=np.vstack(reduced);s=np.linalg.svd(reduced/np.maximum(np.linalg.norm(reduced,axis=0),1e-12),compute_uv=False)
    rank=int(np.sum(s>s[0]*1e-5))
    report={'optimized':bool(result.success),'evaluations':result.nfev,'input_points':len(camera),
        'fundamental_inliers':int(inlier.sum()),'positive_initialized':int(positive.sum()),
        'profile_evaluations':preliminary.nfev,
        'camera_focal_px':float(cam.K[0,0]),'camera_focal_initial_px':float(fc),
        'focal_bounds_px':focal_range.tolist(),'focal_at_bound':bool(min(result.x[0]-lower[0],upper[0]-result.x[0])<.01),
        'rms_px':{'cam':errors[0]['rms'],'proj':errors[1]['rms']},
        'data_rank':rank,'global_parameters':6,'singular_values':s.tolist(),
        'positive_final':bool(np.all(X[:,2]>0)&np.all((X@pro.R.T+pro.t)[:,2]>0))}
    return Rig({'osmo':cam,'proj':pro},world='osmo',units='projector_baseline'),report


def validation(rig,c,p):
    X=triangulate(rig['osmo'],rig.projector(),c,p)
    positive=np.isfinite(X).all(1)&(X[:,2]>0)&((X@rig.projector().R.T+rig.projector().t)[:,2]>0)
    errors=np.concatenate([np.linalg.norm(project(rig['osmo'],X)-c,axis=1),
                           np.linalg.norm(project(rig.projector(),X)-p,axis=1)])
    return {'reprojection_px':stats(errors),'positive_fraction':float(np.mean(positive))}


def add_parser(subparsers):
    p=subparsers.add_parser('approx-rig',help='rig aproximado de una vista existente; no abre hardware')
    p.add_argument('session');p.add_argument('--out',required=True)
    p.add_argument('--throws',default='1.2,1.3,1.4,1.5')
    p.add_argument('--offset-percent',type=float,default=100.)
    p.add_argument('--baseline-mm',type=float,help='distancia externa aproximada; omitida = escala relativa')
    p.add_argument('--max-nfev',type=int,default=650)
    p.add_argument('--points',type=int,default=650)
    p.set_defaults(fn=command)


def command(args):
    root=Path(args.session);out=Path(args.out)
    event('approx_preflight',0,'Rig aproximado de una vista. Parámetros de proyector supuestos; sin certificación métrica.')
    try:
        if out.exists(): raise ValueError('la salida ya existe; no sobrescribir')
        metadata=json.loads((root/'autocalib-session.json').read_text())
        path=root/'view_00_map.npz';dec=Decoded.load(path)
        if tuple(metadata['camera_size'])!=tuple(dec.size): raise ValueError('tamaño de cámara incompatible')
        c,p,tracking=dense_tracks([dec],limit=args.points);c=c[0]
        part=np.arange(len(c))%5;train=part>=2;select=part==1;audit=part==0
        throws=[float(x) for x in args.throws.split(',')]
        if not throws or not all(np.isfinite(x) and x>0 for x in throws): raise ValueError('throw inválido')
        if not np.isfinite(args.offset_percent): raise ValueError('offset inválido')
        candidates=[];rigs=[]
        for i,throw in enumerate(throws):
            event('approx_bundle',.1+.7*i/len(throws),f'Ajustando throw supuesto {throw}; focal Osmo libre.')
            pro=fixed_projector(tuple(metadata['projector_size']),throw,args.offset_percent)
            rig,report=fit(c[train],p[train],dec.size,pro,args.max_nfev)
            report.update(throw_ratio=throw,offset_percent=args.offset_percent,
                          selection=validation(rig,c[select],p[select]))
            candidates.append(report);rigs.append(rig)
            write_json(out.with_name(out.stem+'-candidates.json'),{'candidates':candidates})
        eligible=[i for i,r in enumerate(candidates) if r['optimized'] and r['positive_final']
                  and not r['focal_at_bound'] and r['selection']['positive_fraction']>.95]
        if not eligible: raise ValueError('ningún modelo aproximado converge con profundidad positiva y focal interior')
        best=min(eligible,key=lambda i:candidates[i]['selection']['reprojection_px']['rms'])
        rig=rigs[best];chosen=candidates[best]
        report={'mode':'approximate_single_view','status':'approximate_unverified','metric_status':'unverified',
            'evidence':metadata['evidence'],'tracking':tracking,'candidates':candidates,'selected_index':best,
            'selection_rule':'minimum selection-set reprojection RMS; audit set unused',
            'audit':validation(rig,c[audit],p[audit]),'scale':{'status':'unknown','baseline_units':1.},
            'model':{'fixed':['projector_K_from_assumed_throw_and_offset','camera_pp_at_raster_center',
                'square_pixels','zero_skew','zero_distortion_both','baseline_norm=1'],
                'free':['camera_focal','relative_rotation','translation_direction','scene_points']},
            'projector_prior_source':'assumed sweep authorized by operator; no T749 datasheet',
            'issues':['projector_intrinsics_assumed','camera_distortion_ignored','metric_accuracy_unverified']}
        report['prior_sensitivity']={
            'selection_rms_range_px':[min(r['selection']['reprojection_px']['rms'] for r in candidates),
                                      max(r['selection']['reprojection_px']['rms'] for r in candidates)],
            'camera_focal_range_px':[min(r['camera_focal_px'] for r in candidates),
                                     max(r['camera_focal_px'] for r in candidates)],
            'selected_throw_is_measured':False}
        if chosen['data_rank']<6: report['issues'].append('conditionally_rank_deficient')
        if args.baseline_mm is not None:
            if not np.isfinite(args.baseline_mm) or args.baseline_mm<=0: raise ValueError('baseline inválido')
            rig.projector().t*=args.baseline_mm;rig.units='mm'
            report['scale']={'status':'approximate_external_measurement','baseline_mm':args.baseline_mm,
                             'optical_centers_verified':False}
        else: report['issues'].append('scale_unknown')
        report['units']=rig.units
        rig.extra={'evidence':metadata['evidence'],'approximate':True,'metric_status':'unverified',
                   'pixel_convention':'opencv_integer_centers','approximate_report':report}
        data={'format':'slscan-rig/1','units':rig.units,'world':rig.world,
              'metric_status':'unverified','approximate':True,'evidence':metadata['evidence'],
              'input_hashes':{str(path.resolve()):hashlib.sha256(path.read_bytes()).hexdigest()},
              'devices':{name:d.to_json() for name,d in rig.devices.items()},'extra':rig.extra}
        write_json(out,data);write_json(out.with_name(out.stem+'-report.json'),report)
        event('rig',1,'Rig aproximado guardado; escala y precisión sin verificar.',out=str(out),units=rig.units,
              camera_focal_px=chosen['camera_focal_px'],throw_ratio=chosen['throw_ratio'],audit=report['audit'])
        return 0
    except (Exception,KeyboardInterrupt) as exc:
        event('failed',1,str(exc) or 'Interrumpido',False)
        return 2

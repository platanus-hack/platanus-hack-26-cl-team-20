"""Board-free multi-view camera/projector bundle with explicitly assumed priors.

Input is measured image correspondence, never room geometry. World is camera 0;
projector baseline is one (scale gauge). All cameras share optics. No result is
a metric certificate; scale comes from a separately documented measurement.
"""
import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
from .rig import Device,Rig
from .joint import project,triangulate,stats


def dense_tracks(maps,limit=450):
    """Join projector code cells across views with projector AND scene fixed.

    Cell centroids approximate a common surface patch, not subpixel metrology.
    Reject broad/disconnected-looking cells; reserve tracks before fitting.
    Prefer multi-pixel patches. Sparse maps/undersampled cameras can supply
    single-pixel cells, explicitly reported with the same quantization caveat.
    """
    if not maps: raise ValueError('sin mapas para enlazar celdas Gray')
    skips={int(m.stats.get('skip_bits',0)) for m in maps}
    if len(skips)!=1: raise ValueError('Gray skip_bits debe coincidir entre vistas')
    groups=[];coverage=[]
    for dec in maps:
        c,p,_=dec.points()
        code=p[:,1].astype(np.int64)*65536+p[:,0]
        ids,inverse,count=np.unique(code,return_inverse=True,return_counts=True)
        if len(ids):
            means=np.column_stack([np.bincount(inverse,weights=c[:,j])/count for j in range(2)])
            var=sum(np.bincount(inverse,weights=(c[:,j]-means[inverse,j])**2)/count for j in range(2))
        else:
            means=np.empty((0,2));var=np.empty(0)
        good=var<25
        groups.append((ids[good],means[good],count[good]))
        coverage.append({'valid_pixels':len(c),'cells':len(ids),
                         'single_pixel_cells':int(np.sum(count==1)),
                         'multi_pixel_cells':int(np.sum(good&(count>=2))),
                         'rejected_spread_cells':int(np.sum(~good))})
    common=groups[0][0][groups[0][2]>=2]
    for ids,_,count in groups[1:]: common=np.intersect1d(common,ids[count>=2])
    multi_count=len(common);sampling='multi_pixel_cells'
    if multi_count<100:
        common=groups[0][0]
        for ids,_,_ in groups[1:]: common=np.intersect1d(common,ids)
        sampling='includes_single_pixel_cells'
    if len(common)<100:
        detail='; '.join(f"vista {i+1}: {r['valid_pixels']} píxeles, {r['cells']} celdas" for i,r in enumerate(coverage))
        raise ValueError(f'menos de 100 celdas Gray comunes ({len(common)}; multipíxel {multi_count}); '
                         f'{detail}. Aumentar solape/contraste; repetir puntos no crea correspondencias.')
    rng=np.random.default_rng(37)
    chosen=np.sort(rng.choice(common,min(limit,len(common)),replace=False))
    camera=np.stack([xy[np.searchsorted(ids,chosen)] for ids,xy,_ in groups])
    selected_counts=[count[np.searchsorted(ids,chosen)] for ids,_,count in groups]
    projector=np.column_stack((chosen%65536,chosen//65536)).astype(float)
    if skips.pop()>0: projector-=.5  # integer pixel centres: mean of [0,1,2,3] = 1.5, not 2
    return camera,projector,{'common_cells':len(common),'selected':len(chosen),
        'common_multi_pixel_cells':multi_count,'sampling':sampling,'per_view':coverage,
        'selected_single_pixel_cells_per_view':[int(np.sum(count==1)) for count in selected_counts],
        'cell_centroid_assumption':True,'cell_variance_limit_px2':25}


def intrinsic(f,size,pp=None):
    cx,cy=pp if pp is not None else (np.array(size)-1)/2
    return np.array([[f,0,cx],[0,f,cy],[0,0,1.]],float)


def projector_at(relative,camera):
    """Compose the single camera->projector transform with a rig world pose."""
    return Device('proj','projector',relative.size,relative.K,relative.dist,
                  relative.R@camera.R,relative.R@camera.t+relative.t,
                  rms=relative.rms)


def fit(camera,projector,cam_size,proj_size=(1920,1080),*,camera_focal=None,
        projector_focal=None,max_nfev=180,prior_strength=1.,motion='camera'):
    """Bundle physical tracks; rigid mode needs projector pixels (views,N,2).

    The returned projector is relative to camera 0. Compose it with the last
    camera pose before publishing the operating rig. Legacy camera-only motion
    takes (N,2) projector pixels and keeps the projector fixed in world space.
    """
    camera=np.asarray(camera,float);projector=np.asarray(projector,float)
    nv,n,_=camera.shape
    if motion not in ('camera','rigid'): raise ValueError('modelo de movimiento inválido')
    rigid=motion=='rigid'
    if nv<1 or n<30 or projector.shape!=((nv,n,2) if rigid else (n,2)):
        raise ValueError('correspondencias insuficientes o incompatibles')
    if not np.isfinite(camera).all() or not np.isfinite(projector).all(): raise ValueError('coordenadas no finitas')
    if np.any(camera<0) or np.any(camera>=np.array(cam_size)) or np.any(projector<0) or np.any(projector>=proj_size):
        raise ValueError('coordenadas fuera del raster')
    fc=camera_focal or max(cam_size)*20/36  # nominal 20 mm equivalent, crop NOT calibrated
    fp=projector_focal or proj_size[0]*1.1
    if not all(np.isfinite(x) and x>0 for x in (fc,fp,prior_strength)):
        raise ValueError('focales y fuerza de priors deben ser positivas y finitas')
    Kc=intrinsic(fc,cam_size);Kp=intrinsic(fp,proj_size)
    first_projector=projector[0] if rigid else projector
    F,mask=cv2.findFundamentalMat(camera[0],first_projector,cv2.FM_RANSAC,1.5,.999)
    if F is None or F.shape!=(3,3): raise ValueError('fundamental no estimable')
    E=Kp.T@F@Kc
    c=cv2.undistortPoints(camera[0,None].reshape(-1,1,2),Kc,None).reshape(-1,2)
    p=cv2.undistortPoints(first_projector.reshape(-1,1,2),Kp,None).reshape(-1,2)
    _,R,t,_=cv2.recoverPose(E,c,p,np.eye(3))
    t=t.ravel();r=cv2.Rodrigues(R)[0].ravel()
    cam=Device('osmo','camera',tuple(cam_size),Kc,np.zeros(5))
    pro=Device('proj','projector',tuple(proj_size),Kp,np.zeros(5),R,t)
    X=triangulate(cam,pro,camera[0],first_projector)
    # Bad initial depths are reported; do not use true depths to repair them.
    good=np.isfinite(X).all(1)&(X[:,2]>.1)&(X[:,2]<80)
    if good.sum()<30: raise ValueError('inicialización sin profundidad positiva suficiente')
    initial_invalid=int((~good).sum())
    X=X[good];camera=camera[:,good];projector=projector[:,good] if rigid else projector[good];n=len(X)
    theta=np.arctan2(t[1],t[0]);phi=np.arcsin(np.clip(t[2],-1,1))
    global0=[np.log(fc),np.log(fp),0,0,0,0,0,0,0,0,*r,theta,phi]
    for view in camera[1:]:
        ok,rv,tv=cv2.solvePnP(np.ascontiguousarray(X),np.ascontiguousarray(view),Kc,None,flags=cv2.SOLVEPNP_EPNP)
        if not ok: raise ValueError('no se inicializó la cámara adicional')
        global0.extend([*rv.ravel(),*tv.ravel()])
    ng=len(global0);start=np.r_[global0,X.ravel()]
    lower=np.full(len(start),-100.);upper=np.full(len(start),100.)
    lower[:2]=np.log([fc*.45,fp*.45]);upper[:2]=np.log([fc*2.2,fp*2.2])
    lower[2:6]=-.12;upper[2:6]=.12
    lower[6:10]=-.35;upper[6:10]=.35
    lower[ng+2::3]=.01
    sigmas=np.array([.30,.30,.04,.04,.04,.04,.15,.10,.15,.10])/prior_strength
    def unpack(x):
        Kc=intrinsic(np.exp(x[0]),cam_size,(np.array(cam_size)-1)/2+x[2:4]*cam_size)
        Kp=intrinsic(np.exp(x[1]),proj_size,(np.array(proj_size)-1)/2+x[4:6]*proj_size)
        dc=np.array([x[6],x[7],0,0,0]);dp=np.array([x[8],x[9],0,0,0])
        trans=np.array([np.cos(x[14])*np.cos(x[13]),np.cos(x[14])*np.sin(x[13]),np.sin(x[14])])
        cams=[Device('osmo','camera',tuple(cam_size),Kc,dc)]
        for j in range(nv-1):
            a=15+j*6
            cams.append(Device(f'osmo_{j+1}','camera',tuple(cam_size),Kc,dc,cv2.Rodrigues(x[a:a+3])[0],x[a+3:a+6]))
        pro=Device('proj','projector',tuple(proj_size),Kp,dp,cv2.Rodrigues(x[10:13])[0],trans)
        return cams,pro,x[ng:].reshape(-1,3)
    def residual(x):
        cams,pro,points=unpack(x)
        observations=[(project(d,points)-v).ravel() for d,v in zip(cams,camera)]
        if rigid:
            observations.extend((project(projector_at(pro,d),points)-v).ravel() for d,v in zip(cams,projector))
        else:
            observations.append((project(pro,points)-projector).ravel())
        priors=(x[:10]-np.asarray(global0[:10]))/sigmas
        return np.r_[np.concatenate(observations),priors]
    blocks=2*nv if rigid else nv+1
    rows=2*n*blocks
    sparse=lil_matrix((rows+10,len(start)),dtype=np.int8)
    for v in range(blocks):
        for i in range(n):
            a=2*(v*n+i)
            sparse[a:a+2,:10]=1
            if v>=nv: sparse[a:a+2,10:15]=1
            pose=v-nv if v>=nv else v
            if pose>0: sparse[a:a+2,15+(pose-1)*6:15+pose*6]=1
            sparse[a:a+2,ng+3*i:ng+3*i+3]=1
    for i in range(10): sparse[rows+i,i]=1
    result=least_squares(residual,start,jac_sparsity=sparse.tocsr(),bounds=(lower,upper),
        loss='soft_l1',f_scale=1.,x_scale='jac',max_nfev=max_nfev,
        ftol=1e-5,xtol=1e-6,gtol=1e-5,
        tr_options={'atol':1e-9,'btol':1e-9,'maxiter':300})
    cams,pro,points=unpack(result.x)
    # Eliminate point columns from the data-only Jacobian. Priors do not count
    # as observations; rank/conditioning is conditional on fixed square pixels.
    J=result.jac[:rows].toarray();reduced=[]
    for i in range(n):
        rr=np.array([[2*(v*n+i),2*(v*n+i)+1] for v in range(blocks)]).ravel()
        A=J[rr,:ng];B=J[rr,ng+3*i:ng+3*i+3]
        reduced.append(A-B@np.linalg.lstsq(B,A,rcond=None)[0])
    Jg=np.vstack(reduced);norm=np.linalg.norm(Jg,axis=0)
    singular=np.linalg.svd(Jg/np.maximum(norm,1e-12),compute_uv=False)
    rank=int(np.sum(singular>singular[0]*1e-5))
    errors=[stats(np.linalg.norm(project(d,points)-v,axis=1)) for d,v in zip(cams,camera)]
    pros=[projector_at(pro,d) for d in cams] if rigid else [pro]
    pobs=projector if rigid else [projector]
    per_projector=[stats(np.linalg.norm(project(d,points)-v,axis=1)) for d,v in zip(pros,pobs)]
    perr=stats(np.concatenate([np.linalg.norm(project(d,points)-v,axis=1) for d,v in zip(pros,pobs)]))
    all_depth=np.column_stack([(points@d.R.T+d.t)[:,2] for d in [*cams,*pros]])
    _,sv,_=np.linalg.svd(points-points.mean(0),full_matrices=False)
    issues=['scale_unmeasured','priors_assumed_not_calibrated','gray_cells_are_surface_patches']
    if rank<ng: issues.append('data_rank_deficient')
    if sv[-1]/sv[0]<.015: issues.append('nearly_planar_scene')
    if not result.success: issues.append('optimizer_not_converged')
    if np.any(all_depth<=0): issues.append('negative_depth')
    if any(e['rms']>1 for e in [*errors,*per_projector]): issues.append('reprojection_above_1px')
    report={'status':'unverified','mode':'scene_selfcalibration','evidence':'unspecified',
        'views':nv,'points':n,'initial_invalid_points':initial_invalid,'optimized':bool(result.success),
        'evaluations':result.nfev,'message':str(result.message),'rms_px':{'cam':errors[0]['rms'],'proj':perr['rms']},
        'optimizer':{'loss':'soft_l1','f_scale_px':1.,'max_nfev':max_nfev,
                     'ftol':1e-5,'xtol':1e-6,'gtol':1e-5},
        'per_camera':errors,'projector_residual':perr,'per_projector':per_projector,
        'motion':motion,'issues':issues,'metric_status':'unverified',
        'units':'projector_baseline','scale':{'status':'unmeasured','baseline':1.},
        'model':{'fixed':['square_pixels','skew_zero','p1=0','p2=0','k3=0','camera0_pose','baseline_norm=1'],
                 'estimated':['fc','fp','principal_points','k1_k2_both','device_poses','scene_points'],
                 'focal_priors_px':[fc,fp],'prior_strength':prior_strength},
        'observability':{'data_rank':rank,'global_parameters':ng,'singular_values':singular.tolist(),
            'condition':float(singular[0]/max(singular[-1],1e-15)),'shape_thickness_ratio':float(sv[-1]/sv[0]),
            'conditional_on_model':True,'prior_rows_excluded':True}}
    if rigid:
        report['model']['fixed'].append('single_camera_to_projector_transform')
        report['relative_projector']={'R':pro.R.tolist(),'t_baseline_units':pro.t.tolist()}
    rig=Rig({d.name:d for d in [*cams,pro]},world='osmo',units='projector_baseline')
    return rig,points,report,good


def scale_budget(length_mm,uncertainty_mm,distance_mm=1000.):
    if length_mm<=0 or uncertainty_mm<0: raise ValueError('medida e incertidumbre inválidas')
    return {'length_mm':length_mm,'uncertainty_bound_mm':uncertainty_mm,
            'relative_bound':uncertainty_mm/length_mm,
            'scale_only_error_at_distance_mm':distance_mm*uncertainty_mm/length_mm,
            'distance_mm':distance_mm,'includes_geometry_error':False}

"""Approximate joint fit of two known rectangles, projected corners and Gray.

The paper dimensions constrain scale. Errors on those fitted references are not
independent metric validation. All optical priors and rank are reported.
"""
import argparse
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np
from scipy.optimize import least_squares
from . import joint,metric
from .graycode import Decoded
from .selfcalib import intrinsic,dense_tracks
from .rig import Device,Rig
from .metric_session import write_json


def fit(observations,scene_c,scene_p,initial,max_nfev=600,*,paper_sigma=1.,marker_sigma=.5,gray_effective=16):
    if len(observations)!=2:raise ValueError('se requieren exactamente dos hojas')
    if min(paper_sigma,marker_sigma,gray_effective)<=0:raise ValueError('pesos deben ser positivos')
    cam0=initial['osmo'];pro0=initial.projector();size=cam0.size;psize=pro0.size
    lengths=[]
    for row in observations:
        X=joint.triangulate(cam0,pro0,np.array(row['paper_cam']),np.array(row['paper_proj_gray']))
        sides=np.linalg.norm(np.roll(X,-1,axis=0)-X,axis=1)
        lengths.extend(np.array(row['size_mm']*2)/sides)
    scale=float(np.median(lengths))
    start=[np.log(cam0.K[0,0]),np.log(pro0.K[0,0]),pro0.K[1,2]/psize[1],
           *cv2.Rodrigues(pro0.R)[0].ravel(),*(pro0.t*scale)]
    for row in observations:
        obj=np.column_stack((metric.paper_corners_mm(row['size_mm']),np.zeros(4)))
        ok,r,t=cv2.solvePnP(obj,np.array(row['paper_cam']),cam0.K,np.zeros(5),flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:raise ValueError('no se pudo inicializar hoja')
        start.extend([*r.ravel(),*t.ravel()])
    start=np.array(start);lo=np.full(len(start),-10000.);hi=-lo
    lo[:2]=np.log([max(size)*.25,psize[0]*.4]);hi[:2]=np.log([max(size)*5,psize[0]*3])
    lo[2]=-.5;hi[2]=1.5
    for i in range(len(observations)):lo[9+6*i+5]=50
    prior_sigma=np.array([.35,.35,.35])
    def unpack(x):
        c=Device('osmo','camera',size,intrinsic(np.exp(x[0]),size),np.zeros(5))
        p=Device('proj','projector',psize,intrinsic(np.exp(x[1]),psize,((psize[0]-1)/2,x[2]*psize[1])),
                 np.zeros(5),cv2.Rodrigues(x[3:6])[0],x[6:9])
        poses=[(cv2.Rodrigues(x[9+6*i:12+6*i])[0],x[12+6*i:15+6*i]) for i in range(len(observations))]
        return c,p,poses
    def residual(x,priors=True):
        c,p,poses=unpack(x);errors=[]
        for row,(R,t) in zip(observations,poses):
            paper=np.column_stack((metric.paper_corners_mm(row['size_mm']),np.zeros(4)))@R.T+t
            errors.extend(((joint.project(c,paper)-row['paper_cam'])/paper_sigma).ravel())
            for key,sigma in [('projected',marker_sigma),('gray',.75)]:
                pixels=np.array(row[key+'_cam']);target=np.array(row[key+'_px'])
                if len(pixels):
                    # Camera localization uncertainty is in CAMERA pixels.
                    # Dense correlated Gray samples must not overwhelm 4 paper corners.
                    points,_=joint.intersect(p,target,R,t)
                    weight=sigma*np.sqrt(max(1,len(pixels)/gray_effective))
                    errors.extend(((joint.project(c,points)-pixels)/weight).ravel())
        if len(scene_c):
            points=joint.triangulate(c,p,scene_c,scene_p)
            errors.extend(((joint.project(c,points)-scene_c)/1.5).ravel())
            errors.extend(((joint.project(p,points)-scene_p)/1.5).ravel())
        if priors:errors.extend((x[:3]-start[:3])/prior_sigma)
        return np.array(errors)
    result=least_squares(residual,start,bounds=(lo,hi),loss='soft_l1',f_scale=1.,x_scale='jac',
                         max_nfev=max_nfev,ftol=1e-7,xtol=1e-7,gtol=1e-7)
    cam,pro,poses=unpack(result.x);J=result.jac[:-3]
    singular=np.linalg.svd(J/np.maximum(np.linalg.norm(J,axis=0),1e-12),compute_uv=False)
    rank=int(np.sum(singular>singular[0]*1e-5));sheets=[]
    for row,(R,t) in zip(observations,poses):
        X=joint.triangulate(cam,pro,np.array(row['paper_cam']),np.array(row['paper_proj_gray']))
        sides=np.linalg.norm(np.roll(X,-1,axis=0)-X,axis=1);reference=np.array(row['size_mm']*2)
        expected=np.column_stack((metric.paper_corners_mm(row['size_mm']),np.zeros(4)))@R.T+t
        corners=joint.stats(np.linalg.norm(X-expected,axis=1))
        sheets.append({'name':row['name'],'side_lengths_mm':sides.tolist(),'side_errors_mm':(sides-reference).tolist(),
            'corner_triangulation_vs_fitted_plane_mm':corners,
            'camera_corner_residual_px':joint.stats(np.linalg.norm(joint.project(cam,expected)-row['paper_cam'],axis=1)),
            'normal':R[:,2].tolist(),'t_mm':t.tolist()})
    angle=float(np.degrees(np.arccos(np.clip(abs(poses[0][0][:,2]@poses[1][0][:,2]),-1,1))))
    report={'mode':'approximate_two_oficio_sheets','optimized':bool(result.success),'evaluations':result.nfev,
        'message':str(result.message),'metric_status':'unverified','units':'mm','evidence':'recorded',
        'camera_focal_px':float(cam.K[0,0]),'projector_throw_ratio':float(pro.K[0,0]/psize[0]),
        'projector_cy_px':float(pro.K[1,2]),'baseline_mm':float(np.linalg.norm(pro.t)),
        'sheet_normal_angle_deg':angle,'sheets':sheets,
        'observability':{'data_rank':rank,'parameters':len(start),'singular_values':singular.tolist(),'prior_rows_excluded':True},
        'optical_priors':{'initial':[float(cam0.K[0,0]),float(pro0.K[0,0]),float(pro0.K[1,2])],
                          'sigma_log_fc_log_fp_cy_fraction':prior_sigma.tolist()},
        'fixed':['square_pixels','zero_distortion','camera_pp_center','projector_cx_center'],
        'reference':{'paper_size_mm':observations[0]['size_mm'],'physically_measured':False,
                     'corners':'visually seeded and edge-refined; couch contact'},
        'validation':'residual on fitted paper dimensions, NOT independent metric accuracy',
        'residual_weights':{'paper_camera_sigma_px':paper_sigma,'marker_camera_sigma_px':marker_sigma,
                            'gray_camera_sigma_px':.75,'gray_effective_max_samples_per_sheet':gray_effective},
        'side_errors_mm':joint.stats([abs(e) for s in sheets for e in s['side_errors_mm']]),
        'issues':['approximate_optical_model','paper_dimensions_assumed_from_oficio','independent_validation_missing']}
    if rank<len(start):report['issues'].append('data_rank_deficient')
    if angle<5:report['issues'].append('nearly_parallel_sheets')
    if not result.success:report['issues'].append('optimizer_not_converged')
    scene_X=joint.triangulate(cam,pro,scene_c,scene_p)
    report['positive_depth_fraction']=float(np.mean((scene_X[:,2]>0)&((scene_X@pro.R.T+pro.t)[:,2]>0))) if len(scene_X) else None
    report['scene_reprojection_px']={'camera':joint.stats(np.linalg.norm(joint.project(cam,scene_X)-scene_c,axis=1)),
                                    'projector':joint.stats(np.linalg.norm(joint.project(pro,scene_X)-scene_p,axis=1))} if len(scene_X) else {}
    report['at_optical_bound']=bool(np.any(np.minimum(result.x[:3]-lo[:3],hi[:3]-result.x[:3])<.001))
    if report['at_optical_bound']:report['issues'].append('optical_parameter_at_bound')
    if report['positive_depth_fraction'] is not None and report['positive_depth_fraction']<.99:report['issues'].append('negative_depth')
    return Rig({'osmo':cam,'proj':pro},world='osmo',units='mm'),report


def command(args):
    root=Path(args.session);out=Path(args.out)
    if out.exists():raise ValueError('no sobrescribir rig')
    rows=[json.loads(p.read_text()) for p in sorted(root.glob('sheet_?.json'))]
    if len(rows)!=2:raise ValueError('se requieren dos hojas observadas')
    dec=Decoded.load(root/'view_00_map.npz');c,p,tracking=dense_tracks([dec],limit=100000)
    c=c[0];rng=np.random.default_rng(32);on_paper=np.zeros(len(c),bool)
    for row in rows:
        # Legacy metric.paper_scale uses projector edge coordinates. Camera
        # corners and this solver use OpenCV centers; retain the raw capture.
        if row.get('gray_pixel_convention') is None:
            row['paper_proj_gray']=(np.array(row['paper_proj_gray'])-.5).tolist()
            row['gray_homography']=(np.array([[1,0,-.5],[0,1,-.5],[0,0,1.]])@np.array(row['gray_homography'])).tolist()
            row['gray_pixel_convention']='opencv_centers'
        row['homography_cam_to_projector_markers']=cv2.getPerspectiveTransform(np.array(row['projected_cam'],np.float32),np.array(row['projected_px'],np.float32)).tolist()
        row['homography_cam_to_paper_mm']=cv2.getPerspectiveTransform(np.array(row['paper_cam'],np.float32),metric.paper_corners_mm(row['size_mm']).astype(np.float32)).tolist()
        write_json(out.parent/(row['name']+'-homographies.json'),row)
        q=np.array(row['paper_cam'],np.float32)
        inside=np.array([cv2.pointPolygonTest(q,tuple(pt),True)>3 for pt in c])
        on_paper|=np.array([cv2.pointPolygonTest(q,tuple(pt),True)>-3 for pt in c])
        ids=np.flatnonzero(inside)
        if len(ids)<20:raise ValueError('Gray insuficiente dentro de hoja')
        selected=rng.choice(ids,min(len(ids),100),replace=False)
        row['gray_cam']=c[selected].tolist();row['gray_px']=p[selected].tolist()
    split=rng.permutation(np.flatnonzero(~on_paper));selected=split[:240];held=split[240:440]
    rig,report=fit(rows,c[selected],p[selected],Rig.load(args.initial),paper_sigma=args.paper_sigma,
                   marker_sigma=args.marker_sigma,gray_effective=args.gray_effective)
    report['capture_corner_motion_px']=json.loads((root/'autocalib-session.json').read_text()).get('corner_motion_px')
    report['input_hashes']={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [root/'view_00_map.npz',root/'autocalib-session.json',Path(args.initial),*sorted(root.glob('sheet_?.json'))]}
    if report['capture_corner_motion_px'] and np.max(report['capture_corner_motion_px'])>1:
        report['issues'].append('white_photo_corner_drift_above_1px')
    if len(held):
        points=joint.triangulate(rig['osmo'],rig.projector(),c[held],p[held])
        report['heldout_reprojection_px']={key:joint.stats(np.linalg.norm(joint.project(dev,points)-pixels,axis=1))
            for key,dev,pixels in [('camera',rig['osmo'],c[held]),('projector',rig.projector(),p[held])]}
        report['heldout_reprojection_px']['validation']='unused Gray scene correspondences; not independent metric accuracy'
    rig.extra={'evidence':'recorded','approximate':True,'metric_status':'unverified','oficio_report':report}
    write_json(out.with_name(out.stem+'-report.json'),report)
    if not report['optimized'] or 'negative_depth' in report['issues']:raise ValueError('ajuste inválido; reporte guardado')
    rig.save(out)
    print(json.dumps(report,indent=2,allow_nan=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('session')
    p.add_argument('--initial',required=True);p.add_argument('--out',required=True)
    p.add_argument('--paper-sigma',type=float,default=1.)
    p.add_argument('--marker-sigma',type=float,default=.5)
    p.add_argument('--gray-effective',type=float,default=16.)
    command(p.parse_args())

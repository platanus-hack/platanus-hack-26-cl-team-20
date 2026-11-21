"""Evaluate a synthetic rigid replay AFTER estimation, never supply its truth.

Usage: python -m slscan.rigid_validation SESSION --rig SESSION/rig.json
"""
import argparse
import json
from pathlib import Path
import cv2
import numpy as np
from .graycode import Decoded
from .joint import rays,triangulate,stats
from .rig import Device,Rig
from .scene_simulator import room_surfaces
from .scene_tracks import rigid_tracks
from .metric_session import write_json


def intersect_room(camera,pixels):
    directions=rays(camera,pixels);center=camera.center_world()
    nearest=np.full(len(pixels),np.inf)
    for surface in room_surfaces():
        denominator=directions@surface['normal']
        with np.errstate(divide='ignore',invalid='ignore'):
            distance=((surface['corners'][0]-center)@surface['normal'])/denominator
        local=center+distance[:,None]*directions-surface['corners'][0]
        uv=np.column_stack((local@surface['u'],local@surface['v']))
        hit=(distance>0)&np.all((uv>=0)&(uv<=surface['size']),axis=1)
        nearest[hit]=np.minimum(nearest[hit],distance[hit])
    if not np.isfinite(nearest).all(): raise ValueError('un punto de evaluación no intersecta la sala')
    return center+nearest[:,None]*directions


def evaluate(root,rig_path):
    root=Path(root);truth=json.loads((root/'simulation-truth.json').read_text())
    metadata=json.loads((root/'autocalib-session.json').read_text())
    if metadata.get('evidence')!='synthetic' or metadata.get('motion')!='rigid':
        raise ValueError('evaluador exclusivo de simulación rígida')
    paths=sorted(root.glob('view_*_map.npz'))
    c,p,tracking=rigid_tracks([Decoded.load(path) for path in paths],
        [cv2.imread(str(root/path.name.removesuffix('_map.npz')/'color.png')) for path in paths])
    estimated=Rig.load(rig_path);report=estimated.extra['selfcalibration_report']
    if report['motion']!='rigid' or estimated.extra['evidence']!='synthetic':
        raise ValueError('el rig no corresponde a una simulación rígida')
    if tracking['selected']!=report['tracking']['selected']:
        raise ValueError('los tracks actuales no coinciden con el reporte del ajuste')
    active=estimated['osmo'];pro=estimated.projector();first=estimated[estimated.world]
    relative_R=pro.R@active.R.T;relative_t=pro.t-relative_R@active.t
    first_pro=Device('proj','projector',pro.size,pro.K,pro.dist,relative_R,relative_t)
    reconstructed=triangulate(first,first_pro,c[0],p[0])
    true_cam=Device.from_json(truth['devices']['osmo'])
    expected=intersect_room(true_cam,c[0])
    error=np.linalg.norm(reconstructed-expected,axis=1)
    reserved=np.arange(len(error))%5==0
    R=np.asarray(truth['camera_poses'][-1]['R']);t=np.asarray(truth['camera_poses'][-1]['t'])
    true_center=-R.T@t
    angle=np.degrees(np.arccos(np.clip((np.trace(active.R@R.T)-1)/2,-1,1)))
    result={'evidence':'synthetic','alignment':'none; camera0 world; external scale only',
            'camera_size':metadata['camera_size'],'tracks':tracking['selected'],
            'all_triangulated_xyz_mm':stats(error),'reserved_xyz_mm':stats(error[reserved]),
            'baseline_reference_mm':truth['baseline_mm'],
            'last_camera_center_error_mm':float(np.linalg.norm(active.center_world()-true_center)),
            'last_camera_rotation_error_deg':float(angle),'rms_px':report['rms_px'],
            'data_rank':report['observability']['data_rank'],
            'reserved_xyz_all_within_2mm':bool(np.all(error[reserved]<=2)),
            'physical_metric_status':'unverified'}
    write_json(root/'rigid-truth-evaluation.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session');parser.add_argument('--rig',required=True)
    args=parser.parse_args()
    print(json.dumps(evaluate(args.session,args.rig),indent=2,allow_nan=False))

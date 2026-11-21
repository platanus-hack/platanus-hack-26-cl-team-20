"""Run integrated P3 offline with explicitly approximate, cross-session optics."""
import argparse,json,time
from pathlib import Path
import cv2,numpy as np
from slscan.graycode import decode,load_sequence
from slscan.rig import Rig
from slscan import metric_recon as p3

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--scan',required=True);p.add_argument('--rig',required=True);p.add_argument('--out',required=True)
p.add_argument('--paper-audit',required=True);p.add_argument('--allow-unverified-session',action='store_true')
p.add_argument('--rotate',type=int,choices=[0,270],default=270)
p.add_argument('--max-edge-mm',type=float,default=150);p.add_argument('--max-depth-jump-mm',type=float,default=150)
p.add_argument('--max-normal-angle-deg',type=float,default=140)
args=p.parse_args()
if not args.allow_unverified_session:raise ValueError('requiere aceptación explícita de vista previa sin compatibilidad estática verificada')
source=Path(args.scan);out=Path(args.out);root=out.parent;root.mkdir(parents=True,exist_ok=True)
rig_path=Path(args.rig);rig=Rig.load(rig_path)
if out.exists():raise FileExistsError(out)
start=time.monotonic();items=load_sequence(source)
print('Decode',len(items),flush=True)
dec=decode(items,skip_bits=2,projector_size=rig.projector().size,retain_contrast=True)
# This raw capture records rotate=0. The calibrated frame records rotate=270.
# Rotate measured arrays exactly, without interpolation or changing projector codes.
if args.rotate==270:
 dec.px=np.rot90(dec.px).copy();dec.py=np.rot90(dec.py).copy();dec.color=np.rot90(dec.color).copy()
 dec.bit_contrast=np.rot90(dec.bit_contrast,axes=(1,2)).copy();dec.size=dec.size[::-1]
dec.stats['camera_frame_transform']=f'rotate_{args.rotate}_from_raw; exact pixel permutation'
print('Measured grouping',int(dec.valid.sum()),flush=True)
m=p3.measured_matches(dec,rig.projector().size)
texture=root/(out.name+'-appearance.png');color=cv2.imread(str(source/'color.png'))
if color is None:raise ValueError('falta color.png de adquisición')
if args.rotate==270:color=cv2.rotate(color,cv2.ROTATE_90_COUNTERCLOCKWISE)
cv2.imwrite(str(texture),color)
if tuple(color.shape[1::-1])!=rig['osmo'].size:raise ValueError('texture size mismatch')
m.color=np.concatenate([cv2.remap(color,xy[:,0].astype(np.float32).reshape(-1,1),xy[:,1].astype(np.float32).reshape(-1,1),cv2.INTER_LINEAR).reshape(-1,3) for xy in np.array_split(m.cam,max(1,int(np.ceil(len(m.cam)/16000))))])
filters=p3.Filters(min_confidence=.02,max_reprojection_px=2.5,min_ray_angle_deg=.5,
 max_ray_gap_mm=40,min_depth_mm=100,max_depth_mm=8000,max_sigma_mm=120)
limits=p3.MeshLimits(max_edge_mm=args.max_edge_mm,max_depth_jump_mm=args.max_depth_jump_mm,max_normal_angle_deg=args.max_normal_angle_deg)
print('Triangulation',len(m.cam),flush=True)
q=p3.triangulate_measured(m,rig['osmo'],rig.projector(),filters)
mesh=p3.grid_mesh(m,q,rig['osmo'],limits)
inputs={str(p):p3.sha256(p) for _,p in items};inputs[str(rig_path)]=p3.sha256(rig_path)
report=p3.export_reconstruction(out,m,q,mesh,evidence='recorded',world=rig.world,
 rig_sha256=p3.sha256(rig_path),texture_path=texture,filters=filters,limits=limits,
 inputs=inputs,elapsed_seconds=time.monotonic()-start,camera_name='osmo',projector_name='proj')
audit=json.loads(Path(args.paper_audit).read_text())
approx={'approximate':True,'metric_status':'unverified','physical_verified':False,
 'calibration_source':str(rig_path),'scan_source':str(source),
 'cross_session_static_compatibility':'not_independently_verified',
 'authorization':'explicit --allow-unverified-session',
 'paper_reference_size_mm':[216,330],
 'sheet_max_side_errors_mm':[r['max_abs_mm'] for r in audit['sheets']],
 'paper_error_audit':str(Path(args.paper_audit).resolve()),
 'note':'Appearance under acquisition lighting. No PBR/albedo claim. Broad geometry filters for approximate preview; not 2mm acceptance.'}
(out/'approximation.json').write_text(json.dumps(approx,indent=2)+'\n')
print(json.dumps({'points':report['points'],'triangles':report['triangles'],'seconds':report['seconds'],'out':str(out)},indent=2),flush=True)

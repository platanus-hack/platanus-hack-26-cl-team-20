"""Publish an explicitly approximate P3 reconstruction to the local rig viewer."""
import argparse,json
from pathlib import Path
import cv2,numpy as np
from .calibration_viewer import Journal,device_json
from .rig import Rig
from .metric_session import write_json


def command(args):
    root=Path(args.state);source=Path(args.reconstruction);rig=Rig.load(args.rig)
    meta=json.loads((source/'approximation.json').read_text())
    report=json.loads((source/'reconstruction.json').read_text())
    mesh=np.load(source/args.mesh_name);cloud=np.load(source/'cloud_quality.npz')
    xyz=cloud['xyz_mm'];colors=cloud['color_rgb'].astype(float)/255
    take=np.linspace(0,len(xyz)-1,min(16000,len(xyz)),dtype=int)
    colors=np.where(colors<=.04045,colors/12.92,((colors+.055)/1.055)**2.4)
    root.mkdir(parents=True,exist_ok=True)
    white=cv2.imread(str(source/'appearance.png'))
    cv2.imwrite(str(root/'appearance.jpg'),white,[cv2.IMWRITE_JPEG_QUALITY,95])
    # JSON geometry is separate from small SSE progress updates.
    write_json(root/'mesh.json',{'vertices':np.round(mesh['vertices_mm'],4).tolist(),
        'faces':mesh['faces'].tolist(),'uv':np.round(mesh['uv'],7).tolist(),
        'texture':'/images/appearance.jpg','metric_status':'unverified','approximate':True})
    journal=Journal(root,'recorded',1)
    journal.frame('camera',white)
    journal.frame('pattern',np.full((1080,1920),200,np.uint8))
    devices={k:device_json(d) for k,d in rig.devices.items()}
    for dev in devices.values():
        center=np.array(dev['center_world']);dev['frustum_world']=(center+(np.array(dev['frustum_world'])-center)*2.4).tolist()
    residual=report['quality']['reprojection_px']['after']['rms']
    sheet_errors=meta['sheet_max_side_errors_mm']
    errors_text=' / '.join(f'{x:.2f}'.replace('.',',') for x in sheet_errors)
    journal.emit(steps=['Gray medido','Triangulación P3','Malla con apariencia','Inspección aproximada'],
        stage='Inspección aproximada',stage_index=3,status='completed',progress=1,pose=1,total=1,
        mode='reconstruction',approximate=True,devices=devices,physical_verified=False,
        initial_view={'position':[1300,-1000,1800],'target':[0,-300,-2800],'up':[-1,0,0]},
        cloud={'points':np.round(xyz[take],3).tolist(),'colors':np.round(colors[take],5).tolist()},
        mesh={'url':'/data/mesh.json','vertices':len(mesh['vertices_mm']),'faces':len(mesh['faces'])},
        approximation_note=f"{len(xyz):,} puntos ({len(take):,} en vista) · {len(mesh['faces']):,} triángulos. Error en hojas: {errors_text} mm. Escala nominal; no cumple ≤2 mm.",
        calibration={'status':'unverified','issues':['approximate','cross_session_not_verified'],
            'rms_px':{'cam':None,'proj':None},'baseline_mm':float(np.linalg.norm(rig.projector().t))},
        message='Captura real del dale + primer rig de dos hojas. Apariencia de cámara, sin PBR. Malla de vista previa: límites amplios de continuidad, sin rellenar huecos. Inmovilidad entre sesiones sin verificar.',
        reconstruction={'source':str(source.resolve()),'points':len(xyz),'faces':len(mesh['faces']),
                        'joint_reprojection_rms_px':residual,'approximation':meta})
    print(json.dumps({'state':str(root),'points':len(xyz),'faces':len(mesh['faces'])}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--rig',required=True)
    p.add_argument('--reconstruction',required=True);p.add_argument('--state',required=True)
    p.add_argument('--mesh-name',default='mesh.npz')
    command(p.parse_args())

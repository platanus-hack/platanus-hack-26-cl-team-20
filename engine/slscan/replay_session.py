"""Generate genuine Gray image -> decode -> NPZ sessions, without hardware.

Truth is stored separately for evaluation; the replay estimator reads only the
decoded maps and capture metadata. No ideal coordinates are injected into maps.
"""
from pathlib import Path
import shutil
import cv2
import numpy as np
from . import graycode
from .autocalib import event
from .metric_session import write_json
from .rig_simulator import SensorModel
from .scene_simulator import FurnishedRoom


def add_parser(subparsers):
    p=subparsers.add_parser('autocalib-simulate',help='generar sesión replay de imágenes Gray renderizadas, sin hardware')
    p.add_argument('session')
    p.add_argument('--camera-scale',type=float,choices=[.25,.5,1.],default=1.,
                   help='1 = 1080x1920; .5/.25 reducen solo el sensor para pruebas rápidas')
    p.add_argument('--views',type=int,choices=[1,2,3],default=3)
    p.add_argument('--motion',choices=['camera','rigid'],default='camera')
    p.add_argument('--seed',type=int,default=23)
    p.add_argument('--keep-images',action='store_true')
    p.set_defaults(fn=command)


def generate(root,*,camera_scale=1.,views=3,seed=23,keep_images=False,notify=None,motion='camera'):
    root=Path(root)
    if root.exists() and any(root.iterdir()): raise ValueError('usar carpeta nueva para la simulación')
    if camera_scale not in (.25,.5,1.) or views not in (1,2,3): raise ValueError('resolución o vistas inválidas')
    root.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(root).free<512*1024**2: raise ValueError('reservar al menos 512 MiB libres')
    room=FurnishedRoom(SensorModel(seed=seed),scale=camera_scale,motion=motion)
    metadata={'evidence':'synthetic','camera_size':list(room.rig['osmo'].size),
        'projector_size':list(room.rig.projector().size),'views':views,'status':'simulating',
        'generator':'raycast_gray_images_decode_save','model':room.describe(),
        'scale_source':'exact_simulated_baseline_only','skip_bits':2,'motion':motion}
    truth={'evidence':'synthetic','baseline_mm':float(np.linalg.norm(room.rig.projector().t)),
           'devices':{name:d.to_json() for name,d in room.rig.devices.items()},
           'camera_poses':[{'R':R.tolist(),'t':t.tolist()} for R,t in room.camera_poses[:views]]}
    write_json(root/'simulation-truth.json',truth)
    write_json(root/'autocalib-session.json',metadata)
    specs=graycode.sequence(room.rig.projector().size)
    try:
        for view in range(views):
            if notify: notify('raycast',view/views,f'Preparando vista {view+1}/{views}.')
            room.set_view(view)
            directory=root/f'view_{view:02d}';directory.mkdir()
            if motion=='rigid':
                white=room.render(np.full((1080,1920),180,np.uint8))
                if not cv2.imwrite(str(directory/'color.png'),white): raise IOError('no se guardó foto uniforme')
            items=[]
            for spec in specs:
                pattern=graycode.pattern(room.rig.projector().size,spec.axis,spec.bit,spec.inverse)
                path=directory/(spec.tag+'.png')
                if not cv2.imwrite(str(path),room.render(pattern)): raise IOError('no se guardó imagen renderizada')
                items.append((spec,path))
                if notify: notify('render',(view+(spec.index+1)/len(specs)*.9)/views,
                                  f'Vista {view+1}: imagen Gray {spec.index+1}/{len(specs)}.')
            # Reload from disk: the public decoder path sees exactly the same
            # serialized inputs as a captured image sequence.
            decoded=graycode.decode(graycode.load_sequence(directory),skip_bits=2)
            decoded.save(root/f'view_{view:02d}_map.npz')
            if notify: notify('decode',(view+1)/views,f'Vista {view+1}: {decoded.valid.sum()} píxeles válidos.')
            if not keep_images:
                for _,path in items: path.unlink()
        metadata['status']='captured'
    except BaseException:
        metadata['status']='failed';raise
    finally:
        write_json(root/'autocalib-session.json',metadata)
    return metadata


def command(args):
    event('simulate',0,'Generando imágenes Gray y mapas NPZ reales de una escena sintética. Sin hardware.')
    try:
        generate(args.session,camera_scale=args.camera_scale,views=args.views,seed=args.seed,
                 keep_images=args.keep_images,notify=event,motion=args.motion)
        event('simulated_session',1,'Sesión lista para autocalib --replay.',session=args.session)
        return 0
    except (Exception,KeyboardInterrupt) as exc:
        event('failed',1,str(exc) or 'Simulación interrumpida.',False)
        return 2

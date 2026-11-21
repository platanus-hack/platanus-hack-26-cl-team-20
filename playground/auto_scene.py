"""Prepare a generic scene offline, then atomically activate its native-pixel effects.

    python -m playground.auto_scene --activate

After dale, the default scan is the running runtime's scan. No camera, display,
calibration or model download is opened by this command.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid

import cv2
import numpy as np

from .appearance import manifest_path, asset
from .appearance_prepare import MobileSAM, clean_scan, publish_catalog
from .automatic_masks import automatic_masks, render_segments
from slscan.contracts import digest

MODEL_DIR=Path.home()/'hardware/models/slscan-segmentation'
TYPE_NAMES={'picture':'cuadro','wall':'pared','cushion':'cojín','plush':'peluche',
            'mask':'máscara','plant':'planta','sofa':'sofá','column':'columna',
            'book':'libro','table':'mesa','screen':'pantalla','vase':'florero','lamp':'lámpara','unknown':'segmento',
            # escritorio (2-nov): tipos nuevos del vocabulario de grounded_labels
            'keyboard':'teclado','mouse':'mouse','controller':'control','phone':'teléfono','hand':'mano',
            'mousepad':'mousepad','cable':'cable','instrument':'instrumento','tablet':'tablet','headphones':'audífonos','remote':'control remoto'}


def effect_for(kind):
    if kind=='picture':return 'vector_drawing',dict(width=1,speed=.08,intensity=.6,progress=-1)
    if kind=='wall':return 'surface_lines',dict(width=1,speed=.08,intensity=.25,amount=2,resolution=.35)
    if kind=='mask':return 'mask_pulse',dict(intensity=.5,min_area=300,bloom=.3)
    if kind=='column':return 'vector_pencil',dict(width=1,speed=.06,intensity=.5,min_area=1200,max_regions=18)
    color=[100,230,130] if kind=='plant' else [100,190,240]
    return 'contour_breathe',dict(width=2,speed=.2,amount=2,intensity=.4,color=color,resolution=.5)


def select_objects(proposals, *, max_objects=10, min_area=700):
    """Prefer grounded foreground masks; keep unknown coverage without duplicates.

    Confident foreground objects take their visible pixels before backgrounds.
    Unknown proposals never overwrite known objects or inherited names.
    """
    if not 1<=max_objects<=24:raise ValueError('max_objects must be 1..24')
    if not proposals:raise ValueError('SAM produced no usable masks')
    known=sorted((p for p in proposals if p.get('type','unknown')!='unknown'),
                 key=lambda p:(p['type'] in ('wall','sofa'),-p.get('semantic_score',0),-p['area']))
    unknown=sorted((p for p in proposals if p.get('type','unknown')=='unknown'),key=lambda p:-p['area'])
    used=np.zeros_like(proposals[0]['mask'],bool);selected=[]
    for p in known+unknown:
        original=np.asarray(p['mask'],bool);mask=original&~used
        if mask.sum()<min_area or mask.sum()<original.sum()*.35:continue
        selected.append({**p,'mask':mask,'area':int(mask.sum())});used|=mask
        if len(selected)>=max_objects:break
    return selected


def detected_proposals(model, detections, valid):
    result=[];h,w=valid.shape
    for d in detections:
        kind=d.get('type','unknown');score=float(d.get('score',0))
        box=np.asarray(d.get('box',[]),float)
        if kind not in TYPE_NAMES or kind=='unknown' or not np.isfinite(score) or score<.30 or box.shape!=(4,) or not np.isfinite(box).all():continue
        box[[0,2]]=np.clip(box[[0,2]],0,w-1);box[[1,3]]=np.clip(box[[1,3]],0,h-1)
        if box[2]<=box[0] or box[3]<=box[1]:continue
        mask,info=model.predict(box.tolist());mask &= valid
        if mask.sum()<700 or info['score']<.7:continue
        # A detection is only a prompt. The final shape always comes from SAM.
        result.append(dict(mask=mask,area=int(mask.sum()),type=kind,label=TYPE_NAMES[kind],
                           semantic_score=score,score=info['score'],stability=None,box=box.tolist(),
                           method='Grounding DINO box + MobileSAM mask'))
    return result


def detect_labels(image_path, model_dir, python=None):
    """Existing local Grounding DINO only. No image or weights leave the machine."""
    worker=Path(__file__).with_name('grounded_labels.py')
    executable=python or os.environ.get('SL_SCAN_SEMANTIC_PYTHON') or shutil.which('python3')
    try:
        result=subprocess.run([executable or sys.executable,str(worker),str(image_path),'--packages',str(model_dir/'semantic-python')],
                              capture_output=True,text=True,timeout=240)
        if result.returncode:
            return dict(detections=[],status='unavailable',reason=result.stderr[-1500:])
        return json.loads(result.stdout)
    except (OSError,subprocess.TimeoutExpired,json.JSONDecodeError) as exc:
        return dict(detections=[],status='unavailable',reason=str(exc)[:1500])


def build_prepared(scan, appearance, proposals, root, run, *, max_objects=10):
    """Build a staged catalog. Never replace the active manifest while preparing."""
    selected=select_objects(proposals,max_objects=max_objects)
    masks=run/'masks';masks.mkdir()
    source_hash=digest(scan/'scene_pov.png');labels={};composition=[];records=[]
    for index,p in enumerate(selected,1):
        identity=hashlib.sha256(source_hash.encode()+str(scan).encode()+np.packbits(p['mask']).tobytes()).hexdigest()[:16]
        oid='obj_'+identity;kind=p.get('type','unknown')
        name=f'{TYPE_NAMES.get(kind,"segmento")} {index}'
        cv2.imwrite(str(masks/f'{oid}.png'),p['mask'].astype('uint8')*255)
        labels[oid]=dict(name=name,kind='frame' if kind=='picture' else 'wall' if kind=='wall' else 'prop',
                         aliases=[],texture='tela' if kind in ('picture','cushion','plush','sofa') else 'mate',
                         method=p.get('method','MobileSAM automatic point grid'),semantic_type=kind,
                         semantic_score=p.get('semantic_score',0),source_scan_sha256=source_hash)
        effect,params=effect_for(kind);composition.append(dict(effect=effect,target=oid,params=params))
        records.append({k:v for k,v in p.items() if k!='mask'}|dict(id=oid,name=name,mask=f'masks/{oid}.png'))
    data=publish_catalog(run,appearance,masks,labels)
    prefix=run.relative_to(root)
    for entry in [data['appearance'],*(v[k] for v in data['objects'].values() for k in ('mask','bundle'))]:
        entry['path']=str(prefix/entry['path'])
    data.update(composition=composition,producer='playground.auto_scene',scan=str(scan),
                max_effects=max_objects,semantic_policy='unknown when no grounded label; no old-scene IDs')
    staged=run/'prepared.json';staged.write_text(json.dumps(data,indent=2,ensure_ascii=False))
    (run/'objects.json').write_text(json.dumps(records,indent=2,ensure_ascii=False))
    preview,overlay=render_segments(cv2.imread(str(appearance)),selected)
    cv2.imwrite(str(run/'objects.png'),preview);cv2.imwrite(str(run/'projection.png'),overlay)
    return staged


def prepare(scan, model_dir=MODEL_DIR, *, max_objects=10, semantic_python=None, grid=(24,14)):
    scan=Path(scan).resolve();model_dir=Path(model_dir).resolve()
    if not (scan/'scene_pov.png').is_file():raise ValueError('Run dale/recalib first: scene_pov.png is missing')
    root=manifest_path(scan).parent;root.mkdir(parents=True,exist_ok=True)
    run=root/('auto-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]);run.mkdir()
    appearance=run/'appearance'
    if all((scan/p).is_file() for p in ('proj_to_cam.npz','osmo/map.npz','osmo/color.png')):
        clean_scan(scan,appearance)
    else:
        appearance.mkdir();shutil.copyfile(scan/'scene_pov.png',appearance/'appearance.png')
        (appearance/'cleanup.json').write_text(json.dumps(dict(method='original_scene_pov',
                        input_hashes={'scene_pov.png':digest(scan/'scene_pov.png')})))
    source=appearance/'appearance.png';image=cv2.imread(str(source))
    if image is None:raise ValueError('Unreadable appearance')
    support=appearance/'supported.png'
    valid=cv2.imread(str(support),0)>0 if support.exists() else image.any(axis=2)
    try:import onnxruntime  # noqa: F401
    except ImportError:sys.path.insert(0,str(model_dir/'python'))
    model=MobileSAM(model_dir)
    proposals,report=automatic_masks(model,image,valid,columns=grid[0],rows=grid[1])
    preview,overlay=render_segments(image,proposals)
    cv2.imwrite(str(run/'all-segments.png'),preview);cv2.imwrite(str(run/'all-projection.png'),overlay)
    automatic=run/'automatic-masks';automatic.mkdir()
    for i,p in enumerate(proposals,1):cv2.imwrite(str(automatic/f'{i:03}.png'),p['mask'].astype('uint8')*255)
    semantic=detect_labels(source,model_dir,semantic_python)
    (run/'semantic.json').write_text(json.dumps(semantic,indent=2,ensure_ascii=False))
    proposals=detected_proposals(model,semantic.get('detections',[]),valid)+proposals
    report.update(semantic_status=semantic.get('status','ready'),grounded_count=sum(p.get('type')!='unknown' for p in proposals),
                  source_sha256=digest(source),backend='CPU',physical_verified=False)
    (run/'segmentation.json').write_text(json.dumps(report,indent=2))
    staged=build_prepared(scan,source,proposals,root,run,max_objects=max_objects)
    return staged


def activate_runtime(runtime, scan, prepared):
    """Single atomic composition swap; malformed bundles leave the old show intact."""
    from .appearance import install_scene_regions, catalog
    from .scene import Scene
    from .effects.base import Canvas
    scan=Path(scan).resolve();prepared=Path(prepared).resolve();root=manifest_path(scan).parent
    if not prepared.is_relative_to(root.resolve()):raise ValueError('Prepared scene must be below its catalog root')
    data=json.loads(prepared.read_text());signature=digest(prepared)
    if data.get('producer')!='playground.auto_scene' or Path(data['scan']).resolve()!=scan:
        raise ValueError('Prepared scene belongs to another scan')
    if data.get('schema_version')!='1.0' or data.get('coordinate_space')!='projector_px':raise ValueError('Unsupported prepared scene')
    for name,expected in data['input_hashes'].items():asset(scan,dict(path=name,sha256=expected))
    for entry in [data['appearance'],*(v[k] for v in data['objects'].values() for k in ('mask','bundle'))]:asset(root,entry)
    composition=data['composition']
    if not 1<=len(composition)<=24:raise ValueError('Composition must have 1..24 effects')
    with runtime.lock:
        if runtime.metric_mode or runtime.scene.coordinate_space=='world_mm':raise ValueError('Prepared 2D scene cannot replace metric rendering')
        if not runtime.scene.scan_dir or Path(runtime.scene.scan_dir).resolve()!=scan:
            raise ValueError('Runtime scan changed or dale has not loaded this scan')
        if tuple(data['projector_size'])!=runtime.canvas_size:raise ValueError('Native projector size mismatch')
        if (getattr(runtime,'prepared_signature',None)==signature and manifest_path(scan).exists()
                and digest(manifest_path(scan))==signature and len(runtime.instances)==len(composition)):
            return dict(unchanged=True,objects=list(runtime.scene.objects),effects=list(runtime.instances))
        if runtime.overlay is not None and time.time()<runtime.overlay_until:raise ValueError('Wait for current calibration/overlay to finish')
        manifest=manifest_path(scan);previous=manifest.read_bytes() if manifest.exists() else None
        temp=manifest.with_suffix('.json.tmp');temp.write_bytes(prepared.read_bytes());temp.replace(manifest)
        try:
            new=Scene(table_mm=tuple(data['projector_size']),projector_px=True,scan_dir=str(scan))
            new.beat,new.audio,new.controls=runtime.scene.beat,runtime.scene.audio,runtime.scene.controls
            new.lights,new.projectors=runtime.scene.lights,runtime.scene.projectors
            install_scene_regions(new);catalog(scan)
            staged={};counter=runtime._n
            canvas=Canvas(np.zeros_like(runtime.frame),1,flip_y=False)
            for item in composition:
                cls=runtime.registry.classes[item['effect']];obj=new.objects[item['target']]
                if '*' not in cls.targets and obj.kind not in cls.targets:raise ValueError('Effect does not support object kind')
                counter+=1;iid=f'e{counter}';inst=cls(obj,item['params'],iid)
                inst.update(0,new);inst.draw(canvas);staged[iid]=inst
        except Exception:
            if previous is None:manifest.unlink(missing_ok=True)
            else:temp.write_bytes(previous);temp.replace(manifest)
            raise
        for inst in runtime.instances.values():inst.kill()
        runtime.scene=new;runtime.instances=staged;runtime._n=counter;runtime.demo=False
        runtime.px_per_mm=1.;runtime.frame=canvas.img;runtime.prepared_signature=signature
        runtime.emit('prepared_scene_activated',scan=str(scan),objects=len(new.objects),effects=len(staged))
        return dict(scan=str(scan),objects=list(new.objects),effects=[i.state() for i in staged.values()],manifest_sha256=signature)


def api(base,cmd=None,args=None):
    url=base.rstrip('/')+('/api/command' if cmd else '/api/state')
    req=urllib.request.Request(url,data=json.dumps(dict(cmd=cmd,args=args or {})).encode() if cmd else None,
                              headers={'Content-Type':'application/json'})
    result=json.load(urllib.request.urlopen(req,timeout=180))
    if cmd and not result.get('ok'):raise RuntimeError(result)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('scan',nargs='?',help='After dale, defaults to the scan currently loaded by runtime')
    p.add_argument('--runtime',default='http://127.0.0.1:8790');p.add_argument('--models',default=str(MODEL_DIR))
    p.add_argument('--semantic-python');p.add_argument('--max-objects',type=int,default=10)
    p.add_argument('--activate',action='store_true');p.add_argument('--prepared',help='Activate an already prepared artifact')
    a=p.parse_args(argv)
    scan=a.scan or api(a.runtime)['scene'].get('scan_dir')
    if not scan:raise ValueError('No scan: run dale first or pass its scan directory')
    staged=Path(a.prepared) if a.prepared else prepare(scan,a.models,max_objects=a.max_objects,semantic_python=a.semantic_python)
    result=dict(prepared=str(staged))
    if a.activate:result['activation']=api(a.runtime,'activate_prepared',dict(scan=str(Path(scan).resolve()),prepared=str(staged.resolve())))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()

"""Brief, overlay-only acquisition of two existing oficio sheets.

Invoke only with an explicitly released hardware turn. Never opens a display
window or stops the runtime. Paper seeds are inspected in the preceding photo.
"""
import argparse
import json
from pathlib import Path
import time
import urllib.request
import cv2
import numpy as np
from . import graycode,metric
from .capture import Camera,list_displays,resolve_screen,is_reserved
from .metric_session import write_json,check_frame


def marker_pattern(planes):
    """One large marker per sheet; save exact pixel-center boundary coordinates."""
    image=np.full((1080,1920),200,np.uint8);known=[]
    dictionary=cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for i,plane in enumerate(planes):
        q=np.asarray(plane.quad_proj,np.float32);center=q.mean(axis=0)
        # Keep extra margin around the first, smaller paper and its dark edge.
        if i==0:center[0]+=8
        for side in range(240,35,-6):
            x,y=np.round(center-side/2).astype(int)
            corners=np.array([[x,y],[x+side,y],[x+side,y+side],[x,y+side]],float)-.5
            if min(cv2.pointPolygonTest(q,tuple(pt),True) for pt in corners)>14:break
        else:raise ValueError(f'hoja {i}: no cabe un marcador con margen')
        marker_id=[2,1][i]
        image[y:y+side,x:x+side]=cv2.aruco.generateImageMarker(dictionary,marker_id,side)
        known.append({'id':marker_id,'corners_px':corners.tolist()})
    return image,known


def detect_markers(photo,known,planes):
    parameters=cv2.aruco.DetectorParameters()
    parameters.cornerRefinementMethod=cv2.aruco.CORNER_REFINE_SUBPIX
    detector=cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),parameters)
    corners,ids,_=detector.detectMarkers(photo)
    found={} if ids is None else {int(i):q.reshape(4,2) for i,q in zip(ids.ravel(),corners)}
    observations=[]
    for i,(marker,plane) in enumerate(zip(known,planes)):
        if marker['id'] not in found:raise ValueError(f'hoja {i}: no se detectó ArUco {marker["id"]}')
        q=found[marker['id']]
        if any(cv2.pointPolygonTest(plane.quad_cam.astype(np.float32),tuple(pt),True)<2 for pt in q):
            raise ValueError(f'hoja {i}: marcador fuera del papel')
        observations.append({'name':f'sheet_{i}','size_mm':[216,330],
            'paper_cam':plane.quad_cam.tolist(),'paper_proj_gray':(plane.quad_proj-.5).tolist(),
            'projected_cam':q.tolist(),'projected_px':marker['corners_px'],
            'gray_homography':(np.array([[1,0,-.5],[0,1,-.5],[0,0,1.]])@plane.H_cam_proj).tolist(),
            'gray_pixel_convention':'opencv_centers','gray_fit':plane.fit,
            'marker_type':'DICT_4X4_50','marker_id':marker['id']})
    return observations


def command(args):
    root=Path(args.out)
    if root.exists() and not args.reuse_gray: raise ValueError('usar carpeta nueva')
    display=next(d for d in list_displays() if d['index']==resolve_screen('1920x1080'))
    if is_reserved(display) or (display['pixel_w'],display['pixel_h'])!=(1920,1080):
        raise ValueError('se requiere T749 externo 1920x1080 físico/lógico')
    root.mkdir(parents=True,exist_ok=args.reuse_gray)
    reference=json.loads(Path(args.corners).read_text());quads=np.array(reference['quads'])
    def api(cmd,**args):
        req=urllib.request.Request('http://127.0.0.1:8790/api/command',
            data=json.dumps({'cmd':cmd,'args':args}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=8) as r:result=json.load(r)
        if not result.get('ok'): raise ValueError(str(result))
        return result['result']
    offset=api('output_offset')
    if offset!={'dx':0.,'dy':0.}: raise ValueError('overlay tiene offset; no es raster 1:1')
    counter=0;cam=None
    def show(image):
        nonlocal counter
        path=root/f'overlay_{counter%2}.png';counter+=1
        if not cv2.imwrite(str(path),image): raise IOError('no se guardó patrón')
        api('overlay',path=str(path.resolve()),seconds=15)
    def snap(path):
        time.sleep(args.settle);cam.flush(5)
        image=check_frame(cam.read(),(1080,1920))
        if not cv2.imwrite(str(path),image): raise IOError('no se guardó foto')
        return image
    metadata={'evidence':'live','camera_size':[1080,1920],'projector_size':[1920,1080],
              'paper_size_mm':reference['size_mm'],'corner_reference':reference,
              'motion':'camera','views':1,'status':'running','display':display,'overlay_offset':offset}
    try:
        show(np.full((1080,1920),200,np.uint8))
        cam=Camera('osmo',size='3840x2160',rotate=270,downscale=2,name='osmo')
        # Device discovery can exceed the overlay timeout; refresh after open.
        show(np.full((1080,1920),200,np.uint8))
        metadata['camera']=cam.metadata();folder=root/'view_00';folder.mkdir(exist_ok=args.reuse_gray)
        white=snap(root/'white_retry.png' if args.reuse_gray else folder/'color.png');g=cv2.cvtColor(white,cv2.COLOR_BGR2GRAY)
        before=np.array([metric.orient_paper_quad(metric.refine_quad_edges(g,q,search=3),
                       size_mm=reference['size_mm'])[0] for q in quads])
        if np.max(np.linalg.norm(before-quads,axis=2))>3:
            raise ValueError('hojas cambiaron desde inspección; revisar esquinas nuevamente')
        if args.reuse_gray:
            dec=graycode.Decoded.load(root/'view_00_map.npz')
            original=cv2.cvtColor(cv2.imread(str(folder/'color.png')),cv2.COLOR_BGR2GRAY)
            original_quads=np.array([metric.orient_paper_quad(metric.refine_quad_edges(original,q,search=3),size_mm=reference['size_mm'])[0] for q in quads])
            drift=float(np.max(np.linalg.norm(before-original_quads,axis=2)))
            metadata['gray_reuse_corner_drift_px']=drift
            if drift>1.5:raise ValueError('hojas movidas desde Gray; no reutilizar')
        else:
            raw=root/'osmo';raw.mkdir();specs=graycode.sequence((1920,1080))
            for spec in specs:
                show(graycode.pattern((1920,1080),spec.axis,spec.bit,spec.inverse))
                snap(raw/(spec.tag+'.png'))
                print(json.dumps({'stage':'oficio','step':'gray','pattern':spec.index+1,'total':len(specs)}),flush=True)
            items=graycode.load_sequence(raw);dec=graycode.decode(items,skip_bits=2)
            dec.save(root/'view_00_map.npz')
            for _,path in items:Path(path).unlink()
        planes=[metric.paper_scale(q,dec,size_mm=reference['size_mm']) for q in before]
        if any(p.quad_proj is None for p in planes):raise ValueError('Gray insuficiente en hoja')
        pattern,known=marker_pattern(planes)
        write_json(root/'markers-pattern.json',known)
        cv2.imwrite(str(root/'markers-pattern.png'),pattern)
        show(pattern);photo=snap(root/'markers-camera.png')
        observations=detect_markers(photo,known,planes)
        for i,observation in enumerate(observations):write_json(root/f'sheet_{i}.json',observation)
        show(np.full((1080,1920),200,np.uint8));after=snap(root/'white_after.png')
        g=cv2.cvtColor(after,cv2.COLOR_BGR2GRAY)
        refined=[metric.orient_paper_quad(metric.refine_quad_edges(g,q,search=3),
                 size_mm=reference['size_mm'])[0] for q in before]
        metadata['corner_motion_px']=np.linalg.norm(np.array(refined)-before,axis=2).tolist()
        metadata['status']='captured';metadata['observations']=observations
    except Exception as error:
        metadata['status']='failed';metadata['error']=str(error)
        raise
    finally:
        if cam is not None:cam.close()
        api('overlay',off=True)
        write_json(root/'autocalib-session.json',metadata)
    print(json.dumps({'stage':'oficio','step':'captured','out':str(root)}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corners',required=True);p.add_argument('--out',required=True)
    p.add_argument('--settle',type=float,default=1.)
    p.add_argument('--reuse-gray',action='store_true',help='retry markers only after checking white-photo paper drift')
    command(p.parse_args())

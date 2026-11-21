"""One-off automatic MobileSAM proposals; no scene names or hand-authored boxes."""
import time

import cv2
import numpy as np


def automatic_masks(model, image, valid=None, *, columns=24, rows=14,
                    min_area=700, score_threshold=.72, stability_threshold=.82):
    if not 2 <= columns <= 64 or not 2 <= rows <= 64:
        raise ValueError('Automatic point grid must be 2..64 per axis')
    h,w=image.shape[:2]
    valid=np.ones((h,w),bool) if valid is None else np.asarray(valid,bool)
    if valid.shape!=(h,w):raise ValueError('Support mask size mismatch')
    model.set_image(image)
    candidates=[];started=time.perf_counter()
    for y in np.linspace(h/(2*rows),h-h/(2*rows),rows):
        for x in np.linspace(w/(2*columns),w-w/(2*columns),columns):
            if not valid[min(h-1,round(y)),min(w-1,round(x))]:continue
            points=np.array([[[x*model.resized[0]/w,y*model.resized[1]/h],[0,0]]],np.float32)
            feed=dict(image_embeddings=model.embedding,point_coords=points,
                      point_labels=np.array([[1,-1]],np.float32),mask_input=np.zeros((1,1,256,256),np.float32),
                      has_mask_input=np.zeros(1,np.float32),orig_im_size=np.array([h,w],np.float32))
            logits,scores,_=model.decoder.run(None,feed)
            logits=logits[0,0];mask=(logits>0)&valid;area=int(mask.sum())
            score=float(scores.ravel()[0]);stability=float((logits>2).sum()/max(1,(logits>-2).sum()))
            if not np.isfinite(score) or score<score_threshold or stability<stability_threshold:
                continue
            if area<min_area or area>h*w*.8:continue
            small=cv2.resize(mask.astype('uint8'),(min(w,240),min(h,135)),interpolation=cv2.INTER_NEAREST)>0
            candidates.append(dict(mask=mask,small=small,area=area,score=score,stability=stability,
                                   point=[float(x),float(y)],type='unknown',label='segmento',semantic_score=0.))
    selected=[]
    for c in sorted(candidates,key=lambda c:c['score']*c['stability'],reverse=True):
        if any((c['small']&p['small']).sum()/max(1,(c['small']|p['small']).sum())>.85 for p in selected):continue
        selected.append(c)
    for c in selected:c.pop('small')
    return sorted(selected,key=lambda c:-c['area']),dict(grid=[columns,rows],proposals=len(candidates),
            count=len(selected),seconds=time.perf_counter()-started,method='MobileSAM automatic point grid')


def render_segments(image, proposals):
    preview=image.copy();overlay=np.zeros_like(image);occupied=np.zeros(image.shape[:2],np.uint8)
    for i,p in enumerate(proposals,1):
        mask=p['mask'];hue=round((i*.61803398875%1)*179)
        color=tuple(map(int,cv2.cvtColor(np.uint8([[[hue,210,255]]]),cv2.COLOR_HSV2BGR)[0,0]))
        preview[mask]=(preview[mask]*.6+np.array(color)*.4).astype('uint8')
        contours,_=cv2.findContours(mask.astype('uint8'),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(preview,contours,-1,color,2);cv2.drawContours(overlay,contours,-1,color,3)
        d=cv2.distanceTransform(mask.astype('uint8'),cv2.DIST_L2,5);d[occupied>0]=0
        _,_,_,xy=cv2.minMaxLoc(d);cv2.circle(occupied,xy,22,255,-1)
        for target in (preview,overlay):
            cv2.putText(target,str(i),xy,cv2.FONT_HERSHEY_SIMPLEX,.65,(0,0,0),5,cv2.LINE_AA)
            cv2.putText(target,str(i),xy,cv2.FONT_HERSHEY_SIMPLEX,.65,(255,255,255),2,cv2.LINE_AA)
    return preview,overlay

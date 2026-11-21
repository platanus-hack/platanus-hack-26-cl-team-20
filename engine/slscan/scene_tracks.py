"""Physical scene tracks for a moving rigid camera/projector pair.

Uniform-light photos provide temporal identity. Gray maps supply a DIFFERENT
projector observation at each view; a projector code is never a temporal ID.
"""
import cv2
import numpy as np


def sample_projector(dec,xy,radius=4):
    """Local affine fit of quantized codes, rejecting occlusion/depth edges."""
    x,y=np.rint(xy).astype(int);w,h=dec.size
    if not (radius<=x<w-radius and radius<=y<h-radius): return None
    yy,xx=np.mgrid[y-radius:y+radius+1,x-radius:x+radius+1]
    valid=dec.valid[yy,xx]
    if valid.sum()<max(15,valid.size*.75): return None
    A=np.column_stack((xx[valid]-xy[0],yy[valid]-xy[1],np.ones(valid.sum())))
    p=np.column_stack((dec.px[yy[valid],xx[valid]],dec.py[yy[valid],xx[valid]])).astype(float)
    if dec.stats.get('skip_bits',0): p-=.5
    coef=np.linalg.lstsq(A,p,rcond=None)[0]
    error=np.linalg.norm(A@coef-p,axis=1)
    # Quantization of 4-pixel Gray cells alone has RMS sqrt(2*16/12).
    if np.sqrt(np.mean(error**2))>2 or np.max(error)>5: return None
    return coef[-1]


def rigid_tracks(maps,images,limit=450):
    if len(maps)!=len(images) or len(maps)<2:
        raise ValueError('rig rígido requiere 2–3 vistas con color.png y escena estática')
    if len({int(m.stats.get('skip_bits',0)) for m in maps})!=1:
        raise ValueError('Gray skip_bits debe coincidir entre vistas')
    sift=cv2.SIFT_create(nfeatures=8000,contrastThreshold=.015)
    features=[]
    for dec,img in zip(maps,images):
        if img is None or tuple(img.shape[1::-1])!=tuple(dec.size):
            raise ValueError('falta color.png o su resolución no coincide con el mapa Gray')
        gray=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY) if img.ndim==3 else img
        # Gray rejection leaves narrow holes at code transitions. Eroding the
        # binary map would erase almost the entire illuminated surface.
        support=cv2.boxFilter(dec.valid.astype(np.float32),-1,(9,9))
        mask=(support>=.75).astype(np.uint8)*255
        kp,desc=sift.detectAndCompute(gray,mask)
        if desc is None or len(kp)<40: raise ValueError('textura insuficiente para seguir la escena entre posiciones del rig')
        features.append((np.array([k.pt for k in kp]),desc))
    matcher=cv2.BFMatcher(cv2.NORM_L2)
    links=[];pair_counts=[]
    for xy,desc in features[1:]:
        def ratio(a,b):
            return {pair[0].queryIdx:pair[0].trainIdx for pair in matcher.knnMatch(a,b,k=2)
                    if len(pair)==2 and pair[0].distance<.75*pair[1].distance}
        forward=ratio(features[0][1],desc);backward=ratio(desc,features[0][1])
        ids=np.array([(i,j) for i,j in forward.items() if backward.get(j)==i],dtype=int)
        if len(ids)<30: raise ValueError('menos de 30 matches físicos mutuos: aumentar textura y solape del rig')
        _,inliers=cv2.findFundamentalMat(features[0][0][ids[:,0]],xy[ids[:,1]],cv2.FM_RANSAC,1.,.999)
        if inliers is None: raise ValueError('no se pudo verificar el movimiento entre fotos')
        ids=ids[inliers.ravel().astype(bool)]
        links.append(dict(ids));pair_counts.append(len(ids))
    common=set(links[0])
    for link in links[1:]: common.intersection_update(link)
    cameras=[];projectors=[];seen=set()
    for i in sorted(common):
        c=np.array([features[0][0][i],*[features[v+1][0][link[i]] for v,link in enumerate(links)]])
        # SIFT can give multiple orientations to one location; don't count it twice.
        key=tuple(np.rint(c[0]).astype(int))
        if key in seen: continue
        p=[sample_projector(dec,xy) for dec,xy in zip(maps,c)]
        if any(q is None for q in p): continue
        seen.add(key);cameras.append(c);projectors.append(p)
    if len(cameras)<40:
        raise ValueError(f'solo {len(cameras)} tracks físicos con Gray válido en todas las vistas; '
                         'mantener objetos con textura y relieve visibles en las 3 posiciones')
    selected=np.sort(np.random.default_rng(37).choice(len(cameras),min(limit,len(cameras)),replace=False))
    return np.array(cameras)[selected].transpose(1,0,2),np.array(projectors)[selected].transpose(1,0,2),{
        'common_cells':len(cameras),'common_physical_tracks':len(cameras),'selected':len(selected),
        'sampling':'sift_mutual_ratio_fundamental_local_gray_affine',
        'features_per_view':[len(xy) for xy,_ in features],'pair_inliers':pair_counts,
        'projector_codes_used_as_temporal_ids':False,'gray_patch_radius_px':4}

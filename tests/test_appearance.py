"""Offline provenance, robust decode filtering and exact runtime clipping."""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from slscan.contracts import digest
from playground.appearance import install_scene_regions, mask_for_object, texture_path
from playground.appearance_prepare import filter_matches
from playground.scene import Scene
from playground.runtime import Runtime
from playground.effects.base import Canvas
from playground.vectortrace import write_bundle


@pytest.fixture
def prepared(tmp_path):
    scan=tmp_path/'captures'/'scan';scan.mkdir(parents=True)
    root=tmp_path/'reports'/'scene_regions'/'scan';root.mkdir(parents=True)
    original=np.full((80,120,3),40,np.uint8)
    clean=np.full_like(original,[0,0,200]);clean[:,60:]=[0,200,0]
    cv2.imwrite(str(scan/'scene_pov.png'),original)
    cv2.imwrite(str(root/'appearance.png'),clean)
    mask=np.zeros((80,120),np.uint8)
    cv2.ellipse(mask,(60,40),(48,32),0,0,360,255,-1)
    mask[25:40,50:65]=0
    mask[2:8,2:8]=255  # disconnected visible part, outside the main contour ROI
    cv2.imwrite(str(root/'mask.png'),mask)
    bundle=write_bundle(root/'appearance.png',root/'mask.png',root/'vectors',target_id='painting',min_area=0)
    asset=lambda p:dict(path=str(p.relative_to(root)),sha256=digest(p))
    data=dict(schema_version='1.0',coordinate_space='projector_px',projector_size=[120,80],
              input_hashes={'scene_pov.png':digest(scan/'scene_pov.png')},appearance=asset(root/'appearance.png'),
              objects={'painting':dict(name='cuadro de prueba',aliases=['pintura'],kind='frame',
                                       mask=asset(root/'mask.png'),bundle=asset(bundle))})
    (root/'manifest.json').write_text(json.dumps(data))
    scene=Scene(table_mm=(120,80),projector_px=True,scan_dir=str(scan))
    return scene,root,mask


def test_decode_rejects_isolated_bad_correspondences():
    yy,xx=np.mgrid[:20,:20];p=np.column_stack([xx.ravel(),yy.ravel()]).astype(float)
    c=p*.1;c[210]=[500,-800]
    a,b,ids=filter_matches(p,c,residual_px=1,passes=1)
    assert 210 not in ids and len(ids)==399
    np.testing.assert_allclose(b,a*.1)
    with pytest.raises(ValueError):filter_matches([[0,0]],[[0,0]])


def test_catalog_preserves_pixel_holes_and_reloads_objects(prepared):
    scene,root,mask=prepared
    assert texture_path(scene.scan_dir)==root/'appearance.png'
    install_scene_regions(scene);obj=scene.find('pintura')
    np.testing.assert_array_equal(mask_for_object(scene,obj),mask>0)
    install_scene_regions(scene);assert scene.objects['painting'] is obj
    scene.objects.clear();install_scene_regions(scene)
    assert scene.find('pintura') and len(scene.find('pintura').contour)>4
    scene.coordinate_space='world_mm';scene.objects.clear()
    assert install_scene_regions(scene)==[]


@pytest.mark.parametrize('change',['source','texture','mask','escape'])
def test_stale_catalog_assets_fail_closed(prepared,change):
    scene,root,mask=prepared
    install_scene_regions(scene)
    if change=='source':Path(scene.scan_dir,'scene_pov.png').write_bytes(b'changed')
    elif change=='texture':(root/'appearance.png').write_bytes(b'changed')
    elif change=='mask':(root/'mask.png').write_bytes(b'changed')
    else:
        data=json.loads((root/'manifest.json').read_text());data['appearance']['path']='../outside.png'
        (root/'manifest.json').write_text(json.dumps(data))
    with pytest.raises((ValueError,OSError)):
        texture_path(scene.scan_dir)
        mask_for_object(scene,scene.objects['painting'])


@pytest.mark.parametrize('name',['vector_blobs','mask_pulse','surface_lines','contour_breathe','rabbit_outline','scene_texture'])
def test_effects_clip_holes_and_exterior(prepared,tmp_path,name):
    scene,root,mask=prepared
    install_scene_regions(scene)
    rt=Runtime(scene=scene,demo=False,canvas_size=(120,80),requests_dir=tmp_path/'requests')
    params=dict(min_area=0,min_saturation=0,audio='bass',bloom=3,bloom_radius=30,intensity=.3)
    st=rt.spawn(name,'painting',params)
    image=rt.tick(.3).copy()
    assert image.any() and not image[mask==0].any()
    if name=='scene_texture':
        assert image[4,4].any()
    inst=rt.instances[st['id']]
    if name!='scene_texture':
        scene.audio['bass']=1;inst.update(.3,scene)
        canvas=Canvas(np.zeros_like(image),1,flip_y=False);inst.draw(canvas)
        assert not canvas.img[mask==0].any()
        assert not np.array_equal(image,canvas.img)
    assert not [e for e in rt.events if e['type']=='effect_error']


def test_blob_area_cutoff_and_hue(prepared,tmp_path):
    scene,root,mask=prepared;install_scene_regions(scene)
    rt=Runtime(scene=scene,demo=False,canvas_size=(120,80),requests_dir=tmp_path/'requests')
    st=rt.spawn('vector_blobs','painting',dict(min_area=1e9))
    assert not rt.tick(.1).any()
    rt.set_param(st['id'],'min_area',0)
    a=rt.tick(.1).copy();b=rt.tick(5).copy()
    assert a.any() and not np.array_equal(a,b)


def test_provider_preserves_existing_nudge_and_vectors_refuse_it(prepared,tmp_path):
    scene,root,mask=prepared
    install_scene_regions(scene)
    obj=scene.objects['painting']
    obj.extra['ajuste']=[5,0]
    obj.extra.pop('appearance_version')
    install_scene_regions(scene)
    shifted=mask_for_object(scene,scene.objects['painting'])
    assert scene.objects['painting'].extra['ajuste']==[5,0]
    np.testing.assert_array_equal(shifted[:,5:],mask[:,:-5]>0)
    rt=Runtime(scene=scene,demo=False,canvas_size=(120,80),requests_dir=tmp_path/'requests')
    st=rt.spawn('vector_blobs','painting')
    assert not rt.tick(.1).any() and st['id'] not in rt.instances


def test_cloth_and_vectors_share_clean_appearance(prepared,tmp_path):
    scene,root,mask=prepared;install_scene_regions(scene)
    rt=Runtime(scene=scene,demo=False,canvas_size=(120,80),requests_dir=tmp_path/'requests')
    st=rt.spawn('tela_viva','painting',dict(fuerza=0))
    image=rt.tick(.02).copy()
    clean=cv2.imread(str(root/'appearance.png'))
    np.testing.assert_array_equal(image[mask>0],clean[mask>0])
    assert not image[mask==0].any()
    assert rt.instances[st['id']].state()['texture_source']==str(root/'appearance.png')
    rt.kill(st['id'])
    st=rt.spawn('vector_fill','painting',dict(progress=1))
    rt.tick(.01)
    assert rt.instances[st['id']].state()['texture_source']==str(root/'appearance.png')


def test_pencil_limits_major_regions_without_dense_hatching(prepared,tmp_path):
    scene,root,mask=prepared;install_scene_regions(scene)
    rt=Runtime(scene=scene,demo=False,canvas_size=(120,80),requests_dir=tmp_path/'requests')
    st=rt.spawn('vector_pencil','painting',dict(progress=1,min_area=500,max_regions=1,jitter=0,width=1))
    sparse=rt.tick(.01).copy()
    assert sparse.any() and rt.instances[st['id']].state()['regions']==1
    assert rt.instances[st['id']].p['hatching'] is False
    rt.set_param(st['id'],'min_area',0);rt.set_param(st['id'],'max_regions',0)
    rt.set_param(st['id'],'hatching',True);rt.set_param(st['id'],'detail',0)
    dense=rt.tick(.01).copy()
    assert np.count_nonzero(dense)>2*np.count_nonzero(sparse)
    assert not dense[mask==0].any()
    rt.set_param(st['id'],'min_area',1e8)
    assert not rt.tick(.01).any()


def test_wall_strokes_follow_light_and_have_no_cross_grid(prepared,tmp_path):
    scene,root,mask=prepared;install_scene_regions(scene)
    rt=Runtime(scene=scene,demo=False,canvas_size=(120,80),requests_dir=tmp_path/'requests')
    st=rt.spawn('surface_lines','painting',dict(width=1,speed=0,spacing=20))
    image=rt.tick(.01).copy()
    # A crossed grid had full-height vertical rails. A single flowing family
    # occupies only a small fraction of each interior column.
    assert np.max((image[15:65,25:95].max(axis=2)>0).mean(axis=0))<.6
    light=rt.instances[st['id']].light.copy()
    photo=cv2.imread(str(root/'appearance.png'));photo[:]=photo[:,::-1].copy()
    cv2.imwrite(str(root/'appearance.png'),photo)
    data=json.loads((root/'manifest.json').read_text());data['appearance']['sha256']=digest(root/'appearance.png')
    (root/'manifest.json').write_text(json.dumps(data))
    changed=rt.tick(.01).copy()
    assert not np.allclose(light,rt.instances[st['id']].light)
    assert not np.array_equal(image,changed)
    assert not changed[mask==0].any()


def test_cached_assets_recheck_replacement_and_symlink_escape(prepared, tmp_path):
    scene, root, mask = prepared
    from playground.appearance import asset
    data = json.loads((root/'manifest.json').read_text())
    entry = data['appearance']
    asset(root,entry)
    outside = tmp_path/'outside.png'
    outside.write_bytes((root/'appearance.png').read_bytes())
    # Same bytes/hash still cannot turn a previously cached asset into an escape.
    (root/'appearance.png').unlink()
    (root/'appearance.png').symlink_to(outside)
    with pytest.raises(ValueError,match='outside'):
        asset(root,entry)


def test_pixel_resolution_change_rebuilds_but_preserves_native_clip(prepared):
    from playground.effects.pixel_surfaces import SurfaceLines
    scene,root,mask=prepared
    install_scene_regions(scene)
    effect=SurfaceLines(scene.objects['painting'],{'resolution':1,'width':4})
    effect.update(.1,scene); full_shape=effect.xx.shape
    effect.p['resolution']=.5;effect.update(.1,scene)
    assert effect.xx.shape[0]<full_shape[0] and effect.xx.shape[1]<full_shape[1]
    canvas=Canvas(np.full((80,120,3),17,np.uint8),1,flip_y=False)
    effect.draw(canvas)
    assert (canvas.img[mask==0]==17).all() and (canvas.img[mask>0]>17).any()

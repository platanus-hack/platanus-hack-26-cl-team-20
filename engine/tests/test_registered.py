"""P4 registration, optical projection, native transport and outline metrology."""
import copy
import json
from pathlib import Path
import shutil
import subprocess

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from slscan import contracts as c
from slscan.registered import (B, RegisteredScene, camera_matrices, optical_warp,
                               register_scene)
from slscan.verify_registered import (surface_points, measure_object, extract_paired_controls,
                                       diagnose_global, validation_report)
from playground.runtime import Runtime, build_app


def device(name='proj', kind='projector', size=(320, 240), distortion=None):
    w, h = size
    R = cv2.Rodrigues(np.array([.05, -.08, .025]))[0]
    T = np.eye(4); T[:3, :3] = R; T[:3, 3] = [-90, 12, 25]
    return dict(name=name, kind=kind, size=list(size),
        K=[[w*.9, 0, w*.43], [0, w*.94, h*.57], [0, 0, 1]],
        dist=distortion if distortion is not None else [-.13, .045, .003, -.004, -.007],
        R=R.tolist(), t=T[:3, 3].tolist(), T_device_from_world=T.tolist(),
        pose_estimated=False, model={'free': [], 'fixed': ['synthetic_truth']},
        uncertainty={'source': 'synthetic'}, residuals_by_view=[])


def fixture_scene(root, size=(320, 240)):
    root = Path(root); source=root/'source'; source.mkdir(parents=True)
    d=device(size=size)
    cam=device('cam','camera',size)
    cam.update(R=np.eye(3).tolist(),t=[0,0,0],T_device_from_world=np.eye(4).tolist())
    rig=dict(**c.header('rig','synthetic'),format='slscan-rig/2',world='fixture',
        pixel_convention=c.PIXELS,devices={'proj':d,'cam':cam},
        metric_status='unverified',validated_range=None)
    c.atomic_json(source/'rig.json',rig)
    # Backplane and occluding central square, specified in projector coordinates.
    pixels=np.array([[30,30],[size[0]-30,30],[size[0]-30,size[1]-30],[30,size[1]-30],
                     [size[0]*.4,size[1]*.4],[size[0]*.6,size[1]*.4],
                     [size[0]*.6,size[1]*.6],[size[0]*.4,size[1]*.6]],float)
    K=np.array(d['K']); z=np.array([1000]*4+[700]*4)
    xyz=np.column_stack([(pixels[:,0]-K[0,2])/K[0,0]*z,(pixels[:,1]-K[1,2])/K[1,1]*z,z])
    vertices=(xyz-np.array(d['t']))@np.array(d['R'])
    faces=np.array([[0,1,2],[0,2,3],[4,5,6],[4,6,7]],np.int32)
    arrays=dict(vertices_mm=vertices,faces=faces,uv=np.array([[0,0],[1,0],[1,1],[0,1]]*2,float),
        sample_index=np.arange(8,dtype=np.int32),measured=np.ones(8,bool),texture_valid=np.ones(8,bool))
    c.write_npz(source/'mesh.npz',dict(**c.header('mesh_arrays','synthetic'),world='fixture',vertex_count=8,face_count=4),arrays)
    from slscan.metric import write_mesh_ply, Mesh
    write_mesh_ply(source/'mesh.ply',Mesh(vertices.astype(np.float32),faces))
    image=np.zeros((32,32,3),np.uint8);image[:16,:16]=[0,0,255];image[:16,16:]=[0,255,0];image[16:,:16]=[255,0,0];image[16:,16:]=255
    cv2.imwrite(str(source/'texture.png'),image)
    asset=lambda name:dict(path=name,sha256=c.digest(source/name))
    mesh=dict(**c.header('mesh','synthetic'),world='fixture',cloud_sha256='0'*64,
        rig_sha256=c.digest(source/'rig.json'),vertex_count=8,face_count=4,
        uv_convention='top_left_0_1',arrays=asset('mesh.npz'),ply=asset('mesh.ply'),
        texture=asset('texture.png'),texture_kind='appearance')
    c.atomic_json(source/'mesh.json',mesh)
    objects=dict(**c.header('objects','synthetic'),mesh_sha256=c.digest(source/'mesh.json'),face_count=4,
        objects=[dict(id='background',label='Plano de fondo',face_indices=[0,1],
          projector_contour_px=[],bbox_mm=[vertices.min(axis=0).tolist(),vertices.max(axis=0).tolist()],
          plane=None,confidence=1,observation='measured')])
    c.atomic_json(source/'objects.json',objects)
    path=register_scene(source/'rig.json',source/'mesh.json',root/'scene',objects_path=source/'objects.json')
    return path


@pytest.mark.parametrize('dist',[
    [-.13,.04,.003,-.004],[-.13,.04,.003,-.004,-.007],
    [-.13,.04,.003,-.004,-.007,.02,-.005,.001],
    [-.13,.04,.003,-.004,-.007,.02,-.005,.001,.002,-.001,.001,-.002],
    [-.13,.04,.003,-.004,-.007,.02,-.005,.001,.002,-.001,.001,-.002,.02,-.015]])
def test_inverse_optics_every_pixel_including_borders(dist):
    d=device(distortion=dist); optics,uv=optical_warp(d);w,h=d['size']
    source=(uv*np.array([1,-1])+[0,1])*optics['size']-.5+optics['offset']
    K=np.array(d['K']); rays=(source-[K[0,2],K[1,2]])/[K[0,0],K[1,1]]
    pts=np.column_stack([rays.reshape(-1,2),np.ones(w*h)])
    projected=cv2.projectPoints(pts,np.zeros(3),np.zeros(3),K,np.array(dist))[0][:,0]
    yy,xx=np.mgrid[:h,:w];expected=np.stack([xx,yy],-1).reshape(-1,2)
    assert np.max(np.linalg.norm(projected-expected,axis=1))<.001
    assert optics['size'][0]>w  # barrel lens needs overscan, including border rays
    assert optics['inverse_max_px']<.01


def test_js_three_projection_against_opencv_4096_points():
    if not shutil.which('node'):pytest.skip('Node unavailable')
    d=device(size=(1920,1080));K=np.array(d['K']);R=np.array(d['R']);t=np.array(d['t'])
    rng=np.random.default_rng(92)
    pixels=rng.uniform([-.5,-.5],[1919.5,1079.5],(4096,2));pixels[:4]=[[-.5,-.5],[1919.5,-.5],[1919.5,1079.5],[-.5,1079.5]]
    z=rng.uniform(100,8000,len(pixels))
    dev=np.column_stack([(pixels-[K[0,2],K[1,2]])/[K[0,0],K[1,1]]*z[:,None],z])
    xyz=(dev-t)@R
    module=(Path(__file__).parents[1]/'playground/web/registered-math.mjs').as_uri()
    script=f"import {{idealPixel}} from '{module}';import fs from 'node:fs';const x=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(x.xyz.map(p=>idealPixel(x.d,p))));"
    out=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(dict(d=d,xyz=xyz.tolist())),text=True,capture_output=True,check=True)
    actual=np.array(json.loads(out.stdout))
    expected=cv2.projectPoints(xyz,R,t,K,np.zeros(5))[0][:,0]
    assert np.linalg.norm(actual-expected,axis=1).max()<1e-8
    matrices=camera_matrices(d)
    center=np.linalg.inv(np.array(matrices['view']))[:3,3]
    np.testing.assert_allclose(center,(-R.T@t)*[1,-1,-1])


def test_registration_assets_hashes_world_and_contract(tmp_path):
    path=fixture_scene(tmp_path);s=RegisteredScene(path);p=s.payload()
    assert s.doc['units']=='mm' and not s.doc['physical_verified']
    assert len(p['geometry']['faces'])==4 and len(p['objects'])==1
    assert 'geometry/texture.png?scene_sha256=' in p['texture']
    assert c.read_json(path,'scene')['rig_sha256']==c.digest(tmp_path/'source/rig.json')
    mesh=tmp_path/'source/mesh.json';data=c.read_json(mesh);data['rig_sha256']='f'*64;c.atomic_json(mesh,data)
    with pytest.raises(ValueError,match='rig_sha256'):register_scene(tmp_path/'source/rig.json',mesh,tmp_path/'stale')
    s.texture.write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='stale asset'):RegisteredScene(path)


def test_reject_unsafe_asset_and_world(tmp_path):
    fixture_scene(tmp_path);mesh=tmp_path/'source/mesh.json';data=c.read_json(mesh)
    data['world']='another';c.atomic_json(mesh,data)
    with pytest.raises(ValueError,match='world'):register_scene(tmp_path/'source/rig.json',mesh,tmp_path/'other')
    data['world']='fixture';data['arrays']['path']='../source/../../outside';c.atomic_json(mesh,data)
    with pytest.raises(ValueError,match='unsafe'):register_scene(tmp_path/'source/rig.json',mesh,tmp_path/'unsafe')


def test_runtime_native_roundtrip_black_timeout_and_legacy_offsets(tmp_path):
    path=fixture_scene(tmp_path,(1920,1080))
    rt=Runtime(requests_dir=tmp_path/'requests');rt.registered_scene=RegisteredScene(path)
    rt.output_offset=(-21,21);rt.overlay=np.full_like(rt.frame,63);rt.overlay_until=1e20
    client=TestClient(build_app(rt))
    img=np.zeros_like(rt.frame);img[0,0]=[5,25,250];img[-1,-1]=[50,150,200];img[201,673]=[91,31,41]
    payload=cv2.imencode('.png',img)[1].tobytes()
    uri='/api/registered/frame?scene_sha256='+rt.registered_scene.sha
    assert client.post(uri,content=payload).status_code==200
    np.testing.assert_array_equal(rt.tick(.01),img)
    decoded=cv2.imdecode(np.frombuffer(client.get('/api/frame.png').content,np.uint8),cv2.IMREAD_COLOR)
    np.testing.assert_array_equal(decoded,img)
    for cmd,args in [('output_offset',{'dx':1}),('nudge',{'object':'background'}),('overlay',{'off':True})]:
        assert client.post('/api/command',json={'cmd':cmd,'args':args}).status_code==400
    rt.metric_until=0;assert not rt.tick(.01).any()
    assert client.post(uri,content=cv2.imencode('.png',img[:100])[1].tobytes()).status_code==409
    assert client.post(uri.replace(rt.registered_scene.sha,'0'*64),content=payload).status_code==409
    assert client.get('/api/registered/assets/rig.json').status_code==404
    assert client.get(rt.registered_scene.payload()['texture']).status_code==200
    assert client.get('/api/registered/warp.bin?scene_sha256='+'0'*64).status_code==409
    assert client.get('/web/registered.html').status_code==200
    assert client.get('/web/registered-math.mjs').status_code==200


def simple_camera():
    d=device('cam','camera',size=(200,200),distortion=[0]*5)
    d.update(K=[[500,0,100],[0,500,100],[0,0,1]],R=np.eye(3).tolist(),t=[0,0,0],T_device_from_world=np.eye(4).tolist())
    return d


def test_metric_residual_local_depth_bidirectional_missing_uncertainty():
    p=np.column_stack([np.full(101,100.),np.linspace(30,170,101)])
    q=p+[1,0];d=simple_camera()
    kw=dict(object_id='panel',sample_lengths_mm=np.ones(101),uncertainty_mm=.2,held_out=True)
    result=measure_object(d,p,q,[0,0,1,-500],**kw)
    assert result['check']['max_mm']==pytest.approx(1) and result['check']['status']=='passed'
    far=measure_object(d,p,q,[0,0,1,-1500],**kw)
    assert far['check']['max_mm']==pytest.approx(3) and far['check']['status']=='failed'
    q[30:40]=np.nan;missing=measure_object(d,p,q,[0,0,1,-500],**kw)
    assert missing['check']['coverage']==pytest.approx(91/101)
    assert missing['check']['status']=='inconclusive' and len(missing['missing_controls'])==10
    assert not validation_report([result],evidence='recorded',input_hashes={})['physical_verified']
    assert measure_object(d,p,p+[1,0],[0,0,1,-500],evidence='synthetic',**kw)['check']['status']=='unverified'
    with pytest.raises(ValueError):measure_object(d,p,q,[0,0,1,-500],**{**kw,'uncertainty_mm':-1})


def test_paired_photographs_white_object_edge_black_projected_line():
    y,x=np.mgrid[:200,:200]
    white=np.where(x<100,130.,210.)
    outlined=white-80*np.exp(-.5*((x-102.)/.8)**2)
    samples=np.column_stack([np.full(30,100.),np.linspace(20,180,30)])
    normals=np.tile([1.,0],(30,1))
    p,q=extract_paired_controls(white,outlined,samples,normals)
    assert np.max(abs(p[:,0]-99.5))<.1 and np.max(abs(q[:,0]-102))<.1
    result=measure_object(simple_camera(),p,q,[0,0,1,-500],object_id='edge',
        sample_lengths_mm=np.ones(30),uncertainty_mm=.2,held_out=True)
    assert result['check']['max_mm']==pytest.approx(2.5,abs=.1)
    assert result['check']['status']=='failed'
    # No projected line must not become a successful zero error measurement.
    _,empty=extract_paired_controls(white,white,samples,normals)
    assert np.isnan(empty).all()


def test_global_diagnostics_do_not_apply_correction():
    p=np.array([[10,20],[30,40],[80,70],[10,90],[85,10]],float)
    r=diagnose_global(p,p*1.05+[3,-4])
    assert r['similarity']['scale']==pytest.approx(1.05)
    assert r['raw']['max_px']>4 and r['similarity']['max_px']<1e-8
    assert not r['corrections_applied'] and 'pose' in r['possible_causes']


def test_zero_skew_and_noninvertible_lens_rejected():
    d=device();d['K'][0][1]=3
    with pytest.raises(ValueError,match='skew'):optical_warp(d)
    d=device(distortion=[-5,0,0,0,0])
    with pytest.raises(ValueError,match='inverse'):optical_warp(d)


def test_p6_adapter_registers_real_contract_and_resumes(tmp_path):
    from slscan.pipeline import Runner
    from slscan.pipeline_adapters import synthetic_adapters
    from slscan.registered import pipeline_adapter
    source=tmp_path/'scan';source.mkdir()
    adapters=synthetic_adapters();adapters['register_render']=pipeline_adapter
    runner=Runner(source,tmp_path/'pipeline',synthetic=True,adapters=adapters)
    report=runner.run()
    stage=report['stages']['register_render']
    assert stage['state']=='unverified',stage['reason']
    path=next(runner.output/p for p in stage['outputs'] if p.endswith('/scene.json'))
    scene=RegisteredScene(path)
    assert len(scene.arrays['vertices_mm'])==96 and scene.doc['materials']
    assert scene.doc['input_hashes'].items()>=stage['inputs'].items()
    assert not scene.doc.get('objects')  # segment is NOT a P6 register_render dependency
    second=Runner(source,tmp_path/'pipeline',synthetic=True,adapters=adapters).run()
    assert second['stages']['register_render']['cached']


def test_verification_packet_stale_scene_and_missing_observations(tmp_path):
    from slscan.verify_registered import verify_packet
    scene_path=fixture_scene(tmp_path)
    scene=RegisteredScene(scene_path)
    # Equal white frames provide no projected line: zero coverage, never passed.
    for name in ('white.png','outlined.png'):cv2.imwrite(str(tmp_path/name),np.full((240,320),200,np.uint8))
    packet=dict(scene='scene/scene.json',scene_sha256=scene.sha,camera='cam',white='white.png',outlined='outlined.png',
        evidence='recorded',stationary=True,exposure_verified=True,controls_prefixed=True,
        objects=[dict(id='background',samples_px=[[100,80],[100,90],[100,100]],normals_px=[[1,0]]*3,
          surface_plane_world=[0,0,1,-1000],sample_lengths_mm=[10]*3,uncertainty_mm=.3,held_out=True)])
    path=tmp_path/'packet.json';path.write_text(json.dumps(packet))
    result=verify_packet(path,tmp_path/'validation.json')
    assert result['evidence']=='synthetic' and result['checks'][0]['coverage']==0
    assert result['checks'][0]['max_mm'] is None and not result['physical_verified']
    packet['scene_sha256']='0'*64;path.write_text(json.dumps(packet))
    with pytest.raises(ValueError,match='stale'):verify_packet(path,tmp_path/'bad-validation.json')

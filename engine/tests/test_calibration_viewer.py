import json
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from slscan.rig_simulator import SyntheticRoom,SensorModel
from slscan.calibration_viewer import Journal,create_app,device_json
from slscan.calib import CharucoBoard
from slscan.metric_session import adaptive_board
from slscan import metric,graycode,joint


@pytest.fixture(scope='module')
def room():
    room=SyntheticRoom(scale=.5)
    room.set_pose(0)
    return room


def test_raycast_image_detection_without_ideal_corner_injection(room):
    white=room.render(np.full((1080,1920),200,np.uint8))
    q=metric.detect_paper(white,aspect=297/210)
    assert q is not None
    R,t=room.poses[0]
    actual=joint.project(room.rig['osmo'],room.paper_mm@R.T+t)
    assert np.max(np.min(np.linalg.norm(q[:,None]-actual[None,:],axis=2),axis=1))<2
    board=CharucoBoard()
    canvas,px=adaptive_board(board,(1920,1080),joint.project(room.rig.projector(),room.paper_mm@R.T+t))
    image=room.render(canvas)
    found=board.detect(image)
    assert found is not None and len(found['ids'])>=12
    X,_=joint.intersect(room.rig.projector(),px[found['ids']],R,t)
    error=np.linalg.norm(joint.project(room.rig['osmo'],X)-found['img'],axis=1)
    assert np.max(error)<1.,error
    assert image.shape==(960,540,3) and image.dtype==np.uint8


def test_gray_code_is_decoded_from_rendered_rasters(room):
    roi=room.paper_roi(); x,y,w,h=roi
    items=[(s,room.render(graycode.pattern((1920,1080),s.axis,s.bit,s.inverse),roi=roi))
           for s in graycode.sequence((1920,1080))]
    decoded=graycode.decode(items,skip_bits=2)
    is_paper=room.labels[y:y+h,x:x+w]==len(room.surfaces)-1
    valid=decoded.valid&is_paper
    assert valid.sum()>5000
    for coord,truth in ((decoded.px,room.mapx),(decoded.py,room.mapy)):
        error=abs(coord[valid].astype(float)-truth[y:y+h,x:x+w][valid])
        assert np.percentile(error,95)<2.5


def test_noise_is_sensor_noise_and_delay_can_deliver_previous_frame():
    clean=SensorModel(read_noise_dn=0,shot_noise_dn=0,exposure_jitter=0,camera_blur_px=0,projector_blur_px=0)
    r=SyntheticRoom(clean,scale=.25);r.set_pose(0)
    black=np.zeros((1080,1920),np.uint8);white=np.full_like(black,255)
    a=r.render(black);late=r.render(white,wait_ms=0);b=r.render(white,wait_ms=200)
    assert np.array_equal(a,late) and b.mean()>a.mean()+10
    noisy=SyntheticRoom(SensorModel(read_noise_dn=4),scale=.25);noisy.set_pose(0)
    a=noisy.render(white).astype(float);b=noisy.render(white).astype(float)
    assert np.std(a-b)>3


def test_all_synthetic_sheets_fit_both_devices():
    room=SyntheticRoom()
    for R,t in room.poses:
        X=room.paper_mm@R.T+t
        for d in room.rig.devices.values():
            p=joint.project(d,X)
            assert np.all(p>=0) and np.all(p<d.size)


def test_journal_and_server_share_live_and_synthetic_contract(tmp_path,room):
    journal=Journal(tmp_path,'synthetic',11)
    journal.frame('camera',np.zeros((1920,1080,3),np.uint8),points=np.array([[540,960]]))
    journal.emit('Detectar esquinas',pose=1,truth_devices={k:device_json(d) for k,d in room.rig.devices.items()})
    with TestClient(create_app(tmp_path)) as client:
        state=client.get('/api/state').json()
        assert state['evidence']=='synthetic' and state['pose']==1
        assert state['physical_verified'] is False
        response=client.get(state['images']['camera']['url'])
        decoded=cv2.imdecode(np.frombuffer(response.content,np.uint8),cv2.IMREAD_COLOR)
        assert decoded.shape[0]==1000
        assert client.get('/').status_code==200
        assert client.get('/assets/app.js').status_code==200
        assert client.get('/assets/calibration_viewer.py').status_code==404
        assert client.get('/images/state.json').status_code==404
        assert client.get('/api/state',headers={'Host':'attacker.example'}).status_code==403
        assert client.post('/api/simulation/start',json={}).status_code==403
    with pytest.raises(ValueError,match='mezclar'):
        Journal(tmp_path,'live',13,resume=True)
    Journal(tmp_path,'live',13)
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/state').json()['evidence']=='live'


def test_simulation_controls_are_bounded_and_same_origin(tmp_path):
    import threading
    class Simulation:
        paused=threading.Event()
        def start(self,options): self.options=options;return True
    sim=Simulation()
    Journal(tmp_path,'synthetic',11)
    with TestClient(create_app(tmp_path,sim)) as client:
        assert client.post('/api/simulation/start',json={'read_noise_dn':2}).status_code==200
        assert sim.options=={'read_noise_dn':2}
        assert client.post('/api/simulation/start',json={'read_noise_dn':99}).status_code==400
        assert client.post('/api/simulation/start',json={},headers={'Origin':'https://elsewhere.example'}).status_code==403
        assert client.post('/api/simulation/pause',json={}).status_code==200 and sim.paused.is_set()
        assert client.post('/api/simulation/resume',json={}).status_code==200 and not sim.paused.is_set()
        Journal(tmp_path,'live',3)
        assert client.post('/api/simulation/start',json={}).status_code==409


def test_real_capture_observer_receives_exact_saved_frames(tmp_path,monkeypatch):
    from slscan.capture import capture_sequence
    from slscan import capture
    class Window:
        image=None
        def show(self,image): self.image=image
    win=Window()
    class Camera:
        name='osmo'
        def flush(self,n): pass
        def read(self): return win.image.copy()
    seen=[]
    monkeypatch.setattr(capture.time,'sleep',lambda _:None)
    specs=graycode.sequence((32,16))[:4]
    def observe(spec,cam,image):
        stored=cv2.imread(str(tmp_path/cam.name/(spec.tag+'.png')),cv2.IMREAD_UNCHANGED)
        assert np.array_equal(stored,image)
        seen.append(spec.index)
    capture_sequence(win,[Camera()],specs,lambda s:graycode.pattern((32,16),s.axis,s.bit,s.inverse),
                     tmp_path,settle_s=0,observe=observe)
    assert seen==[s.index for s in specs]

"""Optional real Three.js/WebGL test. Local headless Chrome, no hardware/windows.

Set SLSCAN_THREE_VENDOR to a folder with pinned three.module.js (0.160.0) and
OrbitControls.js. Test never fetches dependencies or private data from internet.
"""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import urllib.request

import cv2
import numpy as np
import pytest
import uvicorn
from fastapi.responses import HTMLResponse, FileResponse, Response
from websockets.sync.client import connect

from playground.runtime import Runtime, build_app
from slscan.registered import RegisteredScene
from test_registered import fixture_scene


def test_headless_registered_gpu_and_runtime(tmp_path):
    vendor=Path(os.environ.get('SLSCAN_THREE_VENDOR','/nonexistent'))
    chrome=Path(os.environ.get('SLSCAN_CHROME','/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))
    if not chrome.exists() or not (vendor/'three.module.js').exists():
        pytest.skip('requires local Chrome and pinned SLSCAN_THREE_VENDOR')
    scene=fixture_scene(tmp_path,(1920,1080));rt=Runtime(requests_dir=tmp_path/'requests')
    rt.registered_scene=RegisteredScene(scene);rt.metric_mode=True
    app=build_app(rt)
    web=Path(__file__).parents[1]/'playground/web'
    @app.get('/test-scene')
    def page():
        html=(web/'registered.html').read_text().replace('<head>','<head><base href="/web/">')
        return HTMLResponse(html.replace('https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js','/vendor/three.module.js')
            .replace('https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/','/vendor/'))
    @app.get('/vendor/{path:path}')
    def module(path:str):
        name=Path(path).name
        assert name in ('three.module.js','OrbitControls.js')
        return FileResponse(vendor/name,media_type='text/javascript')
    @app.get('/favicon.ico')
    def favicon(): return Response(status_code=204)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    profile=tmp_path/'chrome'
    process=subprocess.Popen([str(chrome),'--headless=new','--use-angle=swiftshader','--enable-unsafe-swiftshader',
        '--remote-debugging-port=0',f'--user-data-dir={profile}','--no-first-run','--no-default-browser-check',
        '--disable-background-networking','--disable-sync','--disable-extensions','--disable-component-update',
        '--window-size=1400,900','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    errors=[]
    try:
        deadline=time.monotonic()+20
        while not (profile/'DevToolsActivePort').exists() or not server.started:
            if time.monotonic()>deadline:raise TimeoutError('headless startup')
            time.sleep(.05)
        debug=(profile/'DevToolsActivePort').read_text().splitlines()[0]
        tabs=json.load(urllib.request.urlopen(f'http://127.0.0.1:{debug}/json'))
        tab=next(t for t in tabs if t['type']=='page' and t['url']=='about:blank')
        with connect(tab['webSocketDebuggerUrl'],max_size=32_000_000) as ws:
            seq=0
            def call(method,params=None):
                nonlocal seq
                seq+=1;ws.send(json.dumps(dict(id=seq,method=method,params=params or {})))
                while True:
                    msg=json.loads(ws.recv(timeout=25))
                    if (msg.get('method')=='Runtime.exceptionThrown'
                            or (msg.get('method')=='Log.entryAdded' and msg['params']['entry']['level']=='error')
                            or (msg.get('method')=='Runtime.consoleAPICalled' and msg['params']['type']=='error')):
                        errors.append(msg['params'])
                    if msg.get('id')==seq:
                        assert 'error' not in msg,msg
                        return msg.get('result',{})
            def evaluate(expression):
                result=call('Runtime.evaluate',dict(expression=expression,awaitPromise=True,returnByValue=True))
                assert 'exceptionDetails' not in result,result
                return result.get('result',{}).get('value')
            call('Runtime.enable');call('Page.enable');call('Log.enable')
            navigation=call('Page.navigate',dict(url=f'http://127.0.0.1:{port}/test-scene'))
            assert not navigation.get('errorText'),navigation
            deadline=time.monotonic()+30
            while not evaluate('Boolean(window.registered?.ready)'):
                error=evaluate('window.registered?.error');assert not error,error
                if time.monotonic()>deadline:raise TimeoutError(str((errors,evaluate('document.documentElement.outerHTML'),evaluate('performance.getEntriesByType("resource").map(x=>x.name)'))))
                time.sleep(.1)
            assert evaluate('[output.width,output.height]')==[1920,1080]
            # Read actual GPU framebuffer; quadrant UVs prove texture origin.
            def output():
                data=evaluate("document.getElementById('output').toDataURL('image/png').split(',')[1]")
                return cv2.imdecode(np.frombuffer(base64.b64decode(data),np.uint8),cv2.IMREAD_COLOR)
            image=output()
            np.testing.assert_allclose(image[220,350],[0,0,255],atol=2)
            np.testing.assert_allclose(image[220,1500],[0,255,0],atol=2)
            np.testing.assert_allclose(image[850,350],[255,0,0],atol=2)
            # Selection mask must retain foreground depth even when it is black.
            evaluate("document.getElementById('mode').value='mask';document.getElementById('object').value='background';document.getElementById('object').onchange()")
            mask=output();assert mask[540,960].max()==0 and mask[220,350].min()==255
            optics,uv=rt.registered_scene.optics,rt.registered_scene.warp
            ideal=(uv*np.array([1,-1])+[0,1])*optics['size']-.5+optics['offset']
            x,y=ideal[:,:,0],ideal[:,:,1]
            expected_mask=(x>30)&(x<1890)&(y>30)&(y<1050)&~((x>768)&(x<1152)&(y>432)&(y<648))
            # Compare all pixels away from the one-texel rasterisation boundary.
            away=np.ones((1080,1920),bool)
            for edge in (30,1890,768,1152):away&=abs(x-edge)>2
            for edge in (30,1050,432,648):away&=abs(y-edge)>2
            assert np.array_equal(mask[:,:,0][away]>127,expected_mask[away])
            evaluate("document.getElementById('mode').value='appearance';registered.draw()")
            selected=output();assert selected[540,960].max()==0 and selected[220,350].max()>250
            evaluate("document.getElementById('mode').value='outlines';registered.draw()")
            outline=output();assert outline[0,0].min()==255 and outline[540,960].min()==255
            assert np.count_nonzero(outline[:,:,0]<128)>1000
            # DPR/CSS navigation do not mutate the projector camera or framebuffer.
            before=evaluate('registered.projector.projectionMatrix.elements')
            nav_position=evaluate('registered.nav.position.toArray()')
            call('Emulation.setDeviceMetricsOverride',dict(width=390,height=844,deviceScaleFactor=2,mobile=True))
            evaluate("registered.nav.position.set(400,500,600);registered.draw()")
            assert evaluate('registered.projector.projectionMatrix.elements')==before
            assert evaluate('[output.width,output.height]')==[1920,1080]
            np.testing.assert_array_equal(output(),outline)
            # GPU PNG -> HTTP runtime -> returned PNG, exact pixels with legacy offset present.
            rt.output_offset=(-21,21)
            result=evaluate("(async()=>{const r=await fetch('/api/registered/frame?scene_sha256='+registered.data.scene_sha256,{method:'POST',body:await registered.blob()});return r.status})()")
            assert result==200
            np.testing.assert_array_equal(rt.tick(.01),outline)
            raw=urllib.request.urlopen(f'http://127.0.0.1:{port}/api/frame.png').read()
            np.testing.assert_array_equal(cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR),outline)
            # Independent actual Three Vector3.project + nonidentity camera pose, 2048 points.
            projected=evaluate("""(async()=>{const T=await import('/vendor/three.module.js'),r=registered,ps=[];
              for(let i=0;i<2048;i++){const p=new T.Vector3((i%64-32)*15,(Math.floor(i/64)-16)*15,-(500+i%13*30));const q=p.clone().project(r.projector);ps.push({world:[p.x,-p.y,-p.z],px:[(q.x+1)*r.data.optics.size[0]/2-.5+r.data.optics.offset[0],(1-q.y)*r.data.optics.size[1]/2-.5+r.data.optics.offset[1]]});}return ps;})()""")
            d=rt.registered_scene.projector
            xyz=np.array([p['world'] for p in projected],float);actual=np.array([p['px'] for p in projected])
            expected=cv2.projectPoints(xyz,np.array(d['R']),np.array(d['t']),np.array(d['K']),np.zeros(5))[0][:,0]
            error=float(np.linalg.norm(actual-expected,axis=1).max());assert error<.1
            # Restore desktop and keep only synthetic review artifacts outside git.
            call('Emulation.setDeviceMetricsOverride',dict(width=1400,height=900,deviceScaleFactor=1,mobile=False))
            evaluate('registered.nav.position.fromArray('+json.dumps(nav_position)+')')
            evaluate("document.getElementById('mode').value='appearance';registered.draw()")
            destination=os.environ.get('SLSCAN_BROWSER_REPORT')
            if destination:
                folder=Path(destination);folder.mkdir(parents=True,exist_ok=True)
                screenshot=call('Page.captureScreenshot',dict(format='png'))
                (folder/'registered-headless.png').write_bytes(base64.b64decode(screenshot['data']))
                (folder/'browser.json').write_text(json.dumps(dict(points=2048,max_px=error,
                    output_size=[1920,1080],roundtrip='pixel_exact',occlusion=True,dpr_invariant=True,errors=errors),indent=2))
            assert not errors
    finally:
        process.terminate();process.wait(timeout=10)
        server.should_exit=True;thread.join(timeout=5)

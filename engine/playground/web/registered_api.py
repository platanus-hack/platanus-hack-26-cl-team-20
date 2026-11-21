"""Metric frame transport; mounts on the existing loopback runtime."""
import struct
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import Request, HTTPException
from fastapi.responses import FileResponse, Response
from slscan import contracts as c


def accept_frame(rt, png, scene_sha256, seconds=1.):
    scene = getattr(rt, 'registered_scene', None)
    if scene is None or scene.sha != scene_sha256:
        raise ValueError('registered scene is absent or stale')
    if tuple(scene.projector['size']) != (1920, 1080) or tuple(rt.canvas_size) != (1920, 1080):
        raise ValueError('metric runtime requires native 1920x1080; scaling is forbidden')
    if len(png) > 12_000_000 or png[:8] != b'\x89PNG\r\n\x1a\n' or len(png) < 33:
        raise ValueError('expected bounded PNG')
    if struct.unpack('>II', png[16:24]) != (1920, 1080):
        raise ValueError('frame must be exactly 1920x1080')
    if not np.isfinite(seconds) or not 0 < seconds <= 10:
        raise ValueError('frame lifetime must be 0..10 seconds')
    frame = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if frame is None or frame.shape != (1080, 1920, 3):
        raise ValueError('invalid PNG')
    with rt.lock:
        rt.metric_mode = True
        rt.metric_frame = frame
        rt.metric_until = time.monotonic()+seconds
        rt.frame = frame.copy()
    return dict(width=1920, height=1080, scene_sha256=scene.sha, offsets_applied=False)


def mount_registered(app, rt):
    def scene():
        value = getattr(rt, 'registered_scene', None)
        if value is None: raise HTTPException(409, 'No registered scene loaded')
        return value

    @app.get('/api/registered')
    def payload(): return scene().payload()

    @app.get('/api/registered/warp.bin')
    def warp(scene_sha256: str):
        s = scene()
        if scene_sha256 != s.sha: raise HTTPException(409, 'stale scene')
        return Response(s.warp.tobytes(), media_type='application/octet-stream',
                        headers={'Cache-Control': 'no-store'})

    @app.get('/api/registered/assets/{rel:path}')
    def asset(rel: str, scene_sha256: str = ''):
        s = scene()
        path = (s.path.parent / rel).resolve()
        if path not in s.allowed or not path.is_relative_to(s.path.parent):
            raise HTTPException(404)
        if scene_sha256 != s.sha or not path.exists() or c.digest(path) != s.asset_hashes[path]:
            raise HTTPException(409, 'stale scene asset')
        return FileResponse(path, headers={'Cache-Control': 'no-store'})

    @app.post('/api/registered/frame')
    async def frame(request: Request, scene_sha256: str, seconds: float = 1.):
        # Stream bounded input, even when Content-Length is absent/untrusted.
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 12_000_000: raise HTTPException(413, 'frame too large')
            chunks.append(chunk)
        try: return accept_frame(rt, b''.join(chunks), scene_sha256, seconds)
        except ValueError as e: raise HTTPException(409, str(e)) from e

    @app.post('/api/registered/stop')
    def stop():
        with rt.lock:
            rt.metric_mode = True  # stay black; never resume legacy projection by expiry
            rt.metric_frame = None
            rt.frame = np.zeros_like(rt.frame)
        return {'state': 'black'}

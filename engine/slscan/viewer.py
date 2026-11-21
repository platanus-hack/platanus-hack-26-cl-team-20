"""Visor: capturas PNG (rasterizador propio, sin dependencias) y visor web Three.js.

El HTML lleva los datos embebidos (PLY en base64 y frustums del rig como JSON), así se
abre con doble clic o se publica como artifact sin servidor.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import cv2
import numpy as np

from .rig import Rig


# ----------------------------------------------------------------------------- PNG

def _rotation(yaw_deg: float, pitch_deg: float) -> np.ndarray:
    y, p = np.radians(yaw_deg), np.radians(pitch_deg)
    Ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    Rx = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
    return Rx @ Ry


def render_png(xyz: np.ndarray, rgb: np.ndarray | None, out: str | Path, size=(1600, 1200),
               yaw: float = 25, pitch: float = -20, point_px: int = 2, title: str = "",
               bg=(24, 24, 24)) -> np.ndarray:
    """Proyección ortográfica con z-buffer por orden de pintado. Devuelve la imagen BGR."""
    W, H = size
    img = np.full((H, W, 3), bg, np.uint8)
    if len(xyz) == 0:
        return img
    p = xyz - np.median(xyz, axis=0)
    p = p @ _rotation(yaw, pitch).T
    lo, hi = np.percentile(p[:, :2], 1, axis=0), np.percentile(p[:, :2], 99, axis=0)
    span = max(float((hi - lo).max()), 1e-6)
    scale = 0.9 * min(W, H) / span
    sx = ((p[:, 0] - (lo[0] + hi[0]) / 2) * scale + W / 2).astype(int)
    sy = (-(p[:, 1] - (lo[1] + hi[1]) / 2) * scale + H / 2).astype(int)
    order = np.argsort(p[:, 2])            # lejos primero, cerca sobrescribe
    col = (rgb[:, ::-1] if rgb is not None else np.full((len(xyz), 3), 200, np.uint8))
    for dy in range(point_px):
        for dx in range(point_px):
            xs, ys = sx[order] + dx, sy[order] + dy
            ok = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H)
            img[ys[ok], xs[ok]] = col[order][ok]
    if title:
        cv2.putText(img, title, (16, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (230, 230, 230), 2, cv2.LINE_AA)
    cv2.imwrite(str(out), img)
    return img


def distortion_map_png(K: np.ndarray, dist: np.ndarray, size: tuple[int, int], out: str | Path,
                       step: int = 40) -> np.ndarray:
    """Mapa de distorsión: magnitud del desplazamiento en px (arcoíris) y grilla deformada."""
    w, h = size
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    pts = np.stack([xs.ravel(), ys.ravel()], axis=1).reshape(-1, 1, 2)
    und = cv2.undistortPoints(pts, K, dist, P=K).reshape(h, w, 2)
    mag = np.linalg.norm(und - np.stack([xs, ys], axis=-1), axis=-1)
    t = np.clip(mag / max(mag.max(), 1e-6), 0, 1)
    hsv = np.zeros((h, w, 3), np.uint8)
    hsv[..., 0] = (120 - t * 120).astype(np.uint8); hsv[..., 1] = 200; hsv[..., 2] = 220
    img = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    # isolíneas cada 1 px y grilla deformada
    for level in np.arange(1, mag.max(), 1.0):
        mask = ((mag >= level) & (mag < level + 0.15)).astype(np.uint8) * 255
        img[mask > 0] = (40, 40, 40)
    for x in range(0, w, step):
        line = und[:, x].astype(np.int32)
        cv2.polylines(img, [line.reshape(-1, 1, 2)], False, (255, 255, 255), 1, cv2.LINE_AA)
    for y in range(0, h, step):
        line = und[y, :].astype(np.int32)
        cv2.polylines(img, [line.reshape(-1, 1, 2)], False, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(img, f"distorsion max {mag.max():.1f} px", (16, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                (0, 0, 0), 2, cv2.LINE_AA)
    scale = min(1.0, 1600 / w)
    if scale < 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out), img)
    return img


# ----------------------------------------------------------------------------- HTML

_HTML = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#0f1115;--fg:#e8e8ea;--muted:#9a9ca3;--panel:#1a1d24}
@media (prefers-color-scheme: light){:root:not([data-theme="dark"]){--bg:#f4f4f6;--fg:#111;--muted:#555;--panel:#fff}}
html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif}
#c{width:100%;height:100%;display:block}
#p{position:fixed;left:16px;top:16px;background:var(--panel);padding:10px 14px;border-radius:8px;max-width:calc(100vw - 32px);opacity:.92}
#p h1{font-size:15px;margin:0 0 4px}#p div{color:var(--muted)}
</style></head><body>
<canvas id="c"></canvas>
<div id="p"><h1>__TITLE__</h1><div id="info">cargando…</div><div>arrastrar: girar · rueda: zoom · clic derecho: mover</div></div>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
<script type="module">
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {PLYLoader} from 'three/addons/loaders/PLYLoader.js';
const DATA = __DATA__;
const canvas=document.getElementById('c');
const renderer=new THREE.WebGLRenderer({canvas,antialias:true});renderer.setPixelRatio(devicePixelRatio);
const scene=new THREE.Scene();
const cam=new THREE.PerspectiveCamera(50,1,1,100000);
const ctl=new OrbitControls(cam,canvas);ctl.enableDamping=true;
const root=new THREE.Group();scene.add(root);
const box=new THREE.Box3();
function b64(s){const b=atob(s);const a=new Uint8Array(b.length);for(let i=0;i<b.length;i++)a[i]=b.charCodeAt(i);return a.buffer;}
let npts=0;
const loader=new PLYLoader();
for(const c of DATA.clouds){
  const g=loader.parse(b64(c.ply));
  const m=new THREE.PointsMaterial({size:c.point_size||1.5,vertexColors:g.hasAttribute('color'),color:g.hasAttribute('color')?0xffffff:0xa0c0ff});
  const pts=new THREE.Points(g,m);pts.name=c.name;root.add(pts);
  g.computeBoundingBox();box.union(g.boundingBox);npts+=g.attributes.position.count;
}
function frustum(d){
  const [w,h]=d.size,K=d.K,R=d.R,t=d.t;
  const Rm=new THREE.Matrix3().set(R[0][0],R[0][1],R[0][2],R[1][0],R[1][1],R[1][2],R[2][0],R[2][1],R[2][2]);
  const Rt=Rm.clone().transpose();
  const C=new THREE.Vector3(-t[0],-t[1],-t[2]).applyMatrix3(Rt);   // centro = -R^T t
  const depth=d.depth||300;
  const corners=[[0,0],[w,0],[w,h],[0,h]].map(([u,v])=>{
    const x=(u-K[0][2])/K[0][0]*depth,y=(v-K[1][2])/K[1][1]*depth;
    return new THREE.Vector3(x,y,depth).applyMatrix3(Rt).add(C);
  });
  const col=new THREE.Color(d.kind==='projector'?0xffb347:0x5ac8fa);
  const segs=[];
  for(let i=0;i<4;i++){segs.push(C,corners[i]);segs.push(corners[i],corners[(i+1)%4]);}
  const g=new THREE.BufferGeometry().setFromPoints(segs);
  const l=new THREE.LineSegments(g,new THREE.LineBasicMaterial({color:col}));root.add(l);
  const s=new THREE.Mesh(new THREE.SphereGeometry(depth*0.02),new THREE.MeshBasicMaterial({color:col}));s.position.copy(C);root.add(s);
  box.expandByPoint(C);corners.forEach(p=>box.expandByPoint(p));
}
for(const d of (DATA.frustums||[]))frustum(d);
const axes=new THREE.AxesHelper(100);root.add(axes);
const size=new THREE.Vector3();box.getSize(size);const center=new THREE.Vector3();box.getCenter(center);
const r=Math.max(size.length(),1);
cam.position.copy(center).add(new THREE.Vector3(r*0.6,r*0.5,r*0.9));cam.near=r/1000;cam.far=r*50;ctl.target.copy(center);
document.getElementById('info').textContent=`${npts.toLocaleString('es-CL')} puntos · ${(DATA.frustums||[]).length} dispositivos · caja ${size.x.toFixed(0)}×${size.y.toFixed(0)}×${size.z.toFixed(0)} mm`;
function resize(){const w=canvas.clientWidth,h=canvas.clientHeight;if(canvas.width!==w*devicePixelRatio||canvas.height!==h*devicePixelRatio){renderer.setSize(w,h,false);cam.aspect=w/h;cam.updateProjectionMatrix();}}
function loop(){resize();ctl.update();renderer.render(scene,cam);requestAnimationFrame(loop);}loop();
</script></body></html>
"""


def write_html(out: str | Path, clouds: list[tuple[str, Path | bytes]] = (), rig: Rig | None = None,
               title: str = "SL Scanner", frustum_depth: float = 300.0, point_size: float = 1.5) -> Path:
    """Visor HTML autocontenido. clouds: [(nombre, ruta PLY o bytes)]; rig: dibuja frustums."""
    data = {"clouds": [], "frustums": []}
    for name, src in clouds:
        raw = Path(src).read_bytes() if not isinstance(src, (bytes, bytearray)) else bytes(src)
        data["clouds"].append({"name": name, "ply": base64.b64encode(raw).decode("ascii"), "point_size": point_size})
    if rig is not None:
        for d in rig.devices.values():
            data["frustums"].append({"name": d.name, "kind": d.kind, "size": list(d.size), "K": d.K.tolist(),
                                     "R": d.R.tolist(), "t": d.t.ravel().tolist(), "depth": frustum_depth})
    html = _HTML.replace("__TITLE__", title).replace("__DATA__", json.dumps(data))
    out = Path(out)
    out.write_text(html)
    return out

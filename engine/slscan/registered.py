"""Registered metric scenes and inverse optical warp. No acquisition or windows.

World and device coordinates are OpenCV, millimetres. Render world = B @ world,
B=diag(1,-1,-1); render view = B @ T_device_from_world @ B. Pixel centres are
integers. The +0.5 below converts pixel centres to framebuffer edges, not rays.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

import cv2
import numpy as np

from . import contracts as c

B = np.diag([1., -1., -1., 1.])


def undistort(pixels, K, dist):
    criteria = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 60, 1e-12)
    if hasattr(cv2, 'undistortPointsIter'):
        return cv2.undistortPointsIter(pixels, K, dist, None, None, criteria)
    return cv2.undistortPoints(pixels, K, dist, criteria=criteria)


def camera_matrices(device, *, near=1., far=20000., size=None, offset=(0., 0.)):
    K = np.asarray(device['K'], float)
    if not np.allclose([K[0, 1], K[1, 0]], 0):
        raise ValueError('OpenCV pinhole requires zero skew')
    if not 0 < near < far:
        raise ValueError('invalid clipping range')
    w, h = size or device['size']
    cx, cy = K[0, 2] - offset[0], K[1, 2] - offset[1]
    P = np.array([[2*K[0, 0]/w, 0, 1-2*(cx+.5)/w, 0],
                  [0, 2*K[1, 1]/h, 2*(cy+.5)/h-1, 0],
                  [0, 0, -(far+near)/(far-near), -2*far*near/(far-near)],
                  [0, 0, -1, 0]])
    T = np.asarray(device['T_device_from_world'], float)
    view = B @ T @ B
    return dict(projection=P.tolist(), view=view.tolist(),
                world=np.linalg.inv(view).tolist(), near_mm=near, far_mm=far)


def optical_warp(device):
    """Destination distorted pixel -> ideal source UV, with overscan at borders.

    OpenCV handles all supported 4/5/8/12/14 coefficient models, including tilt.
    Float32 UV texture rows are top-down; UV values address a bottom-up GL target.
    Reject noninvertible or extreme lenses instead of silently clipping the FOV.
    """
    w, h = device['size']
    K, dist = np.asarray(device['K'], float), np.asarray(device['dist'], float)
    camera_matrices(device)  # reject unsupported K before undistorting
    # Bound OpenCV's implicit projection Jacobian allocation with small row blocks.
    ideal = np.empty((h, w, 2), np.float64)
    residual_max = 0.
    for y in range(0, h, 32):
        yy, xx = np.mgrid[y:min(y+32, h), :w]
        pixels = np.stack([xx, yy], -1).astype(np.float64).reshape(-1, 1, 2)
        rays = undistort(pixels, K, dist).reshape(-1, 2)
        xyz = np.column_stack([rays, np.ones(len(rays))])
        check = cv2.projectPoints(xyz, np.zeros(3), np.zeros(3), K, dist)[0].reshape(-1, 2)
        residual = np.linalg.norm(check - pixels[:, 0], axis=1)
        if not np.isfinite(rays).all() or not np.isfinite(residual).all() or residual.max() > .01:
            raise ValueError('distortion inverse is not reliable over the entire projector')
        residual_max = max(residual_max, float(residual.max()))
        ideal[y:y+32] = (rays * [K[0, 0], K[1, 1]] + [K[0, 2], K[1, 2]]).reshape(-1, w, 2)
    ideal = ideal.reshape(-1, 2)
    origin = np.minimum(np.floor(ideal.min(axis=0))-1, [0, 0]).astype(int)
    end = np.maximum(np.ceil(ideal.max(axis=0))+2, [w, h]).astype(int)
    size = end-origin
    if size.max() > 8192 or np.prod(size) > 4*w*h:
        raise ValueError('optical overscan exceeds the bounded render budget')
    uv = ((ideal-origin+.5)/size).reshape(h, w, 2).astype('<f4')
    uv[..., 1] = 1-uv[..., 1]
    return dict(size=size.tolist(), offset=origin.tolist(),
                inverse_max_px=residual_max,
                **camera_matrices(device, size=size.tolist(), offset=origin)), uv


def resolve_asset(document, ref):
    root = Path(document).resolve().parent
    relative = Path(ref['path'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError(f'unsafe asset path: {ref["path"]}')
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file() or c.digest(target) != ref['sha256']:
        raise ValueError(f'missing, unsafe or stale asset: {ref["path"]}')
    return target


def register_scene(rig_path, mesh_path, out, *, cloud_path=None, materials_path=None, objects_path=None):
    """Publish a new self-contained scene. Inputs are verified and read-only."""
    rig_path, mesh_path, out = map(lambda p: Path(p).resolve(), (rig_path, mesh_path, out))
    if out.exists() or out.is_relative_to(mesh_path.parent) or out.is_relative_to(rig_path.parent):
        raise ValueError('output must be new and outside input folders')
    rig, mesh = c.read_json(rig_path, 'rig'), c.read_json(mesh_path, 'mesh')
    rh, mh = c.digest(rig_path), c.digest(mesh_path)
    if mesh['world'] != rig['world']:
        raise ValueError('mesh and rig use different worlds')
    if mesh.get('rig_sha256') != rh:
        if cloud_path is None:
            raise ValueError('mesh needs rig_sha256 or its hash-linked cloud.json')
        cloud = c.read_json(cloud_path, 'cloud')
        if c.digest(cloud_path) != mesh['cloud_sha256'] or cloud['rig_sha256'] != rh or cloud['world'] != rig['world']:
            raise ValueError('stale mesh/cloud/rig chain')
    evidence = 'synthetic' if 'synthetic' in (rig['evidence'], mesh['evidence']) else mesh['evidence']
    for device in rig['devices'].values():
        camera_matrices(device)
        if evidence != 'synthetic' and not device['pose_estimated']:
            raise ValueError('physical scene requires estimated device poses')
    meta, arrays = c.read_npz(resolve_asset(mesh_path, mesh['arrays']), 'mesh_arrays')
    if meta['world'] != rig['world'] or (meta['vertex_count'], meta['face_count']) != (mesh['vertex_count'], mesh['face_count']):
        raise ValueError('mesh metadata mismatch')
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.registered-', dir=out.parent))
    try:
        def bundle(path, folder):
            path = Path(path).resolve()
            doc = c.read_json(path)
            dest = staging / folder
            dest.mkdir(parents=True, exist_ok=True)
            def walk(value):
                if isinstance(value, dict):
                    if set(value) == {'path', 'sha256'}:
                        src = resolve_asset(path, value)
                        target = dest / value['path']
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(src, target)
                    else:
                        for v in value.values(): walk(v)
                elif isinstance(value, list):
                    for v in value: walk(v)
            walk(doc)
            shutil.copyfile(path, dest / path.name)
            return dict(path=f'{folder}/{path.name}', sha256=c.digest(path))
        mesh_ref = bundle(mesh_path, 'geometry')
        mat_ref = obj_ref = None
        for path, kind in ((materials_path, 'materials'), (objects_path, 'objects')):
            if path is not None:
                doc = c.read_json(path, kind)
                if doc['mesh_sha256'] != mh or (kind == 'objects' and doc['face_count'] != mesh['face_count']):
                    raise ValueError(f'{kind} references a different mesh')
                if kind == 'materials': mat_ref = bundle(path, 'materials')
                else: obj_ref = bundle(path, 'objects')
        shutil.copyfile(rig_path, staging / 'rig.json')
        inputs = {'rig.json': rh, 'mesh.json': mh}
        for path in (cloud_path, materials_path, objects_path):
            if path is not None: inputs[Path(path).name] = c.digest(path)
        scene = dict(**c.header('scene', evidence, input_hashes=inputs), world=rig['world'],
            rig_sha256=rh, mesh=mesh_ref, materials=mat_ref, objects=obj_ref,
            devices=rig['devices'], metric_status='unverified', validated_range=rig['validated_range'],
            rig_metric_status=rig['metric_status'], render_basis_from_opencv=B[:3, :3].tolist(),
            pixel_convention=c.PIXELS, physical_verified=False,
            output_policy='native_pixels_no_offsets', texture_kind=mesh['texture_kind'])
        c.atomic_json(staging / 'scene.json', scene)
        RegisteredScene(staging / 'scene.json')  # validate the actual published bundle
        staging.rename(out)
    except BaseException:
        # Keep failed unpublished staging for inspection; never recursively delete.
        raise
    return out / 'scene.json'


class RegisteredScene:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.doc = c.read_json(self.path, 'scene')
        if self.doc['render_basis_from_opencv'] != B[:3, :3].tolist():
            raise ValueError('unsupported render basis')
        self.sha = c.digest(self.path)
        self.mesh_path = resolve_asset(self.path, self.doc['mesh'])
        self.mesh = c.read_json(self.mesh_path, 'mesh')
        if self.mesh['world'] != self.doc['world']:
            raise ValueError('mesh and scene world differ')
        self.allowed = {self.mesh_path, resolve_asset(self.mesh_path, self.mesh['ply'])}
        ap = resolve_asset(self.mesh_path, self.mesh['arrays'])
        self.allowed.add(ap)
        meta, self.arrays = c.read_npz(ap, 'mesh_arrays')
        if (meta['world'] != self.doc['world'] or meta['face_count'] != self.mesh['face_count']
                or meta['vertex_count'] != self.mesh['vertex_count']):
            raise ValueError('mesh arrays disagree with scene')
        faces = self.arrays['faces']
        if not len(faces) or not np.any(self.arrays['measured'][faces].all(axis=1)):
            raise ValueError('scene has no measured mesh faces')
        if 'texture_valid' in self.arrays:
            valid = self.arrays['texture_valid']
            if valid.dtype != bool or valid.shape != (meta['vertex_count'],):
                raise ValueError('invalid texture visibility mask')
        self.texture = resolve_asset(self.mesh_path, self.mesh['texture']) if self.mesh['texture'] else None
        if self.texture: self.allowed.add(self.texture)
        self.objects = []
        if self.doc.get('objects'):
            op = resolve_asset(self.path, self.doc['objects'])
            objects = c.read_json(op, 'objects')
            if objects['mesh_sha256'] != c.digest(self.mesh_path) or objects['face_count'] != len(self.arrays['faces']):
                raise ValueError('stale objects')
            self.objects = objects['objects']
        self.materials = None
        if self.doc['materials']:
            mp = resolve_asset(self.path, self.doc['materials'])
            self.materials = c.read_json(mp, 'materials')
            if self.materials['mesh_sha256'] != c.digest(self.mesh_path):
                raise ValueError('stale materials')
            for mat in self.materials['materials']:
                for item in mat['maps'].values(): self.allowed.add(resolve_asset(mp, item['asset']))
                if mat['confidence_mask']: self.allowed.add(resolve_asset(mp, mat['confidence_mask']))
            self.materials_path = mp
        self.projector = next(d for d in self.doc['devices'].values() if d['kind'] == 'projector')
        self.asset_hashes = {path: c.digest(path) for path in self.allowed}
        self.optics, self.warp = optical_warp(self.projector)

    def payload(self):
        def url(path): return '/api/registered/assets/' + path.relative_to(self.path.parent).as_posix() + '?scene_sha256=' + self.sha
        mats = []
        if self.materials:
            for mat in self.materials['materials']:
                mats.append({**mat, 'maps': {key: {**item, 'url': url(resolve_asset(self.materials_path, item['asset']))}
                    for key, item in mat['maps'].items()}})
        a = self.arrays
        return dict(scene=self.doc, scene_sha256=self.sha, projector=self.projector['name'],
            optics=self.optics, devices={k: {**d, **camera_matrices(d)} for k, d in self.doc['devices'].items()},
            geometry={k: a[k].tolist() for k in ('vertices_mm', 'faces', 'uv', 'measured')},
            texture_valid=a.get('texture_valid', np.ones(len(a['vertices_mm']), bool)).tolist(),
            colors=a['color_rgb'].tolist() if 'color_rgb' in a else None,
            texture=url(self.texture) if self.texture else None, materials=mats, objects=self.objects,
            warp_url='/api/registered/warp.bin?scene_sha256='+self.sha)


def pipeline_adapter(ctx):
    """Optional P6 register_render adapter; GPU rendering remains a browser step."""
    from .pipeline import Result, Blocked
    def optional(stage, name):
        try: return ctx.artifact(stage, name)
        except Blocked: return None
    ctx.progress(.1, 'Registrando rig y malla por hashes')
    path = register_scene(ctx.artifact('validate_rig', 'rig.json'), ctx.artifact('mesh', 'mesh.json'),
        ctx.work/'registered', cloud_path=optional('triangulate', 'cloud.json'),
        materials_path=optional('radiometry_materials', 'materials.json'))
    scene = c.read_json(path)
    scene['input_hashes'].update(ctx.inputs)
    c.atomic_json(path, scene)
    ctx.progress(1., 'Escena registrada; render GPU y verificación física pendientes')
    files = [str(p.relative_to(ctx.work)) for p in sorted(path.parent.rglob('*')) if p.is_file()]
    return Result(files, state='unverified', reason='Registered scene; GPU output and physical verification require explicit review.',
                  counts={'devices': len(scene['devices'])})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build')
    for key in ('rig', 'mesh', 'out'): build.add_argument('--'+key, required=True, type=Path)
    for key in ('cloud', 'materials', 'objects'): build.add_argument('--'+key, type=Path)
    serve = sub.add_parser('serve')
    serve.add_argument('scene', type=Path)
    serve.add_argument('--port', type=int, default=8798)
    args = parser.parse_args(argv)
    if args.command == 'build':
        print(register_scene(args.rig, args.mesh, args.out, cloud_path=args.cloud,
                             materials_path=args.materials, objects_path=args.objects))
    else:
        import uvicorn
        from playground.runtime import Runtime, build_app
        rt = Runtime(canvas_size=(1920, 1080))
        rt.registered_scene = RegisteredScene(args.scene)
        rt.metric_mode = True
        import threading
        worker = threading.Thread(target=rt.run, daemon=True)
        worker.start()
        try: uvicorn.run(build_app(rt), host='127.0.0.1', port=args.port)
        finally:
            rt.running = False
            worker.join(timeout=2)


if __name__ == '__main__':
    main()

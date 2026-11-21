"""Offline appearance cleanup and prompted MobileSAM. Never changes acquisition.

Dependencies used at runtime remain NumPy/OpenCV/SciPy. ONNX Runtime and its
models are optional preparation tools, installed outside the repository.
"""
from pathlib import Path
import json
import time

import cv2
import numpy as np
from scipy.spatial import cKDTree, Delaunay
from scipy.interpolate import LinearNDInterpolator

from slscan.graycode import Decoded
from slscan.recon import coalesce
from slscan.contracts import digest


def publish_catalog(root, appearance, masks, labels, *, vectors="vectors"):
    """Vectorize reviewed masks and atomically expose a source-bound catalog.

    All assets stay below root. Labels are descriptive metadata supplied after
    reviewing the masks, never inferred from a rectangular prompt.
    """
    from .vectortrace import write_bundle
    root, appearance, masks = Path(root).resolve(), Path(appearance).resolve(), Path(masks).resolve()
    vector_dir = (root/vectors).resolve()
    if not all(p.is_relative_to(root) for p in (appearance,masks,vector_dir)):
        raise ValueError("Prepared assets must be below catalog root")
    if vector_dir.exists():
        raise ValueError("Use a new vector version directory")
    report = json.loads((appearance.parent/"cleanup.json").read_text())
    image = cv2.imread(str(appearance))
    if image is None:raise ValueError("Missing appearance")
    def entry(path):return dict(path=str(path.relative_to(root)),sha256=digest(path))
    objects = {}
    for oid, label in labels.items():
        if not oid or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in oid):
            raise ValueError("Unsafe object id")
        mask = masks/(oid+".png")
        bundle = write_bundle(appearance,mask,vector_dir/oid,target_id=oid,colors=9,
                              max_size=900,min_area=40,simplify=1,max_regions=700,denoise=5)
        objects[oid] = {**label,"mask":entry(mask),"bundle":entry(bundle)}
    data = dict(schema_version="1.0",coordinate_space="projector_px",projector_size=[image.shape[1],image.shape[0]],
                input_hashes=report["input_hashes"],appearance=entry(appearance),objects=objects)
    tmp = root/"manifest.json.tmp"
    tmp.write_text(json.dumps(data,indent=2))
    tmp.replace(root/"manifest.json")
    return data


def filter_matches(projector_xy, camera_xy, *, neighbors=49, residual_px=1.6, radius_px=65., passes=3):
    """Reject isolated/inconsistent decoded samples using local camera consensus.

    This deliberately sacrifices sparse boundaries for an appearance preview.
    It does not relabel interpolated samples as measured or produce metric data.
    """
    p, c = np.asarray(projector_xy, float), np.asarray(camera_xy, float)
    if p.shape != c.shape or p.ndim != 2 or p.shape[1] != 2 or not np.isfinite([p, c]).all():
        raise ValueError("Expected matching finite Nx2 coordinates")
    kept = np.arange(len(p))
    for _ in range(passes):
        if len(p) < 4:
            raise ValueError("Too few reliable correspondences")
        distances, ids = cKDTree(p).query(p, k=min(neighbors, len(p)))
        median = np.median(c[ids[:, 1:]], axis=1)
        good = (np.linalg.norm(c-median, axis=1) <= residual_px) & (distances[:, -1] <= radius_px)
        p, c, kept = p[good], c[good], kept[good]
    if len(p) < 4:
        raise ValueError("Too few reliable correspondences")
    return p, c, kept


def clean_scan(scan, output):
    """Re-sample the original camera photo through robustly filtered matches.

    Inherit the existing dense-map translation, so cleanup does not move the
    aligned legacy view. Larger unsupported regions stay black; convex-hull
    interpolation is explicitly marked. No rig, mesh or decoded file is written.
    """
    scan, output = Path(scan).resolve(), Path(output).resolve()
    if output == scan or output.is_relative_to(scan) or output.exists():
        raise ValueError("Use a new output directory outside the scan")
    started = time.perf_counter()
    dec = Decoded.load(scan / "osmo/map.npz")
    matches = coalesce(dec)
    with np.load(scan / "proj_to_cam.npz", allow_pickle=False) as data:
        mx, my, valid = data["mapx"], data["mapy"], data["valid"]
    h, w = valid.shape
    p, c = matches.pro, matches.cam
    inside = (p[:, 0] >= 0) & (p[:, 0] < w) & (p[:, 1] >= 0) & (p[:, 1] < h)
    p, c = p[inside], c[inside]
    ij = np.rint(p).astype(int)
    mapped = np.column_stack([mx[ij[:, 1], ij[:, 0]], my[ij[:, 1], ij[:, 0]]])
    known = valid[ij[:, 1], ij[:, 0]] & np.isfinite(mapped).all(axis=1)
    if not known.any():
        raise ValueError("Existing map has no aligned samples")
    inherited = np.median(mapped[known] - c[known], axis=0)
    before = len(p)
    p, c, _ = filter_matches(p, c)
    yy, xx = np.mgrid[:h, :w]
    grid = np.stack([xx, yy], axis=-1)
    mapping = LinearNDInterpolator(Delaunay(p), c+inherited)(grid).astype(np.float32)
    distance = cKDTree(p).query(grid.reshape(-1, 2))[0].reshape(h, w)
    camera = cv2.imread(str(scan / "osmo/color.png"))
    if camera is None:
        raise ValueError("Missing original camera texture")
    supported = (np.isfinite(mapping).all(axis=2) & (mapping[..., 0] >= 0) & (mapping[..., 1] >= 0)
                 & (mapping[..., 0] < camera.shape[1]-1) & (mapping[..., 1] < camera.shape[0]-1))
    image = cv2.remap(camera, np.nan_to_num(mapping[..., 0], nan=-1),
                      np.nan_to_num(mapping[..., 1], nan=-1), cv2.INTER_LINEAR)
    image[~supported] = 0
    closed = cv2.morphologyEx(supported.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    holes = (closed > 0) & ~supported
    image = cv2.inpaint(image, holes.astype(np.uint8), 3, cv2.INPAINT_TELEA)
    interpolated = supported & (distance > 8.)
    output.mkdir(parents=True)
    for name, pixels in (("appearance.png", image), ("supported.png", supported.astype(np.uint8)*255),
                         ("inpainted.png", holes.astype(np.uint8)*255),
                         ("interpolated.png", interpolated.astype(np.uint8)*255)):
        if not cv2.imwrite(str(output/name), pixels):
            raise OSError(f"Could not write {name}")
    report = dict(method="local_decode_consensus_then_camera_resampling", original_samples=before,
                  retained_samples=len(p), rejected_samples=before-len(p),
                  inherited_map_offset_camera_px=inherited.tolist(),
                  supported_fraction=float(supported.mean()), inpainted_fraction=float(holes.mean()),
                  long_interpolation_fraction=float(interpolated.mean()),
                  interpolation_scope="appearance_only_not_measured_geometry", physical_verified=False,
                  seconds=time.perf_counter()-started,
                  input_hashes={name:digest(scan/name) for name in
                                ("scene_pov.png", "proj_to_cam.npz", "osmo/map.npz", "osmo/color.png")})
    (output/"cleanup.json").write_text(json.dumps(report, indent=2))
    return report


class MobileSAM:
    """Acly's MobileSAM ONNX export: RGB preprocessing is included in encoder.

    Prompts are points and boxes, never replacement output polygons. A single
    image embedding is reused for every object. CPU is an explicit supported path.
    """
    def __init__(self, model_dir, backend="cpu", threads=4):
        import onnxruntime as ort
        root = Path(model_dir)
        options = ort.SessionOptions()
        options.intra_op_num_threads = int(threads)
        options.log_severity_level = 3
        providers = ["CPUExecutionProvider"]
        if backend == "coreml":
            providers.insert(0, ("CoreMLExecutionProvider", {"ModelFormat":"MLProgram", "MLComputeUnits":"CPUAndGPU"}))
        elif backend != "cpu":
            raise ValueError("backend must be cpu or coreml")
        self.encoder = ort.InferenceSession(str(root/"mobile_sam_image_encoder.onnx"), options, providers=providers)
        self.decoder = ort.InferenceSession(str(root/"sam_mask_decoder_single.onnx"), options, providers=providers)
        self.backend = backend
        self.timings = {}

    def set_image(self, bgr):
        self.shape = bgr.shape[:2]
        h, w = self.shape
        self.scale = 1024 / max(h, w)
        self.resized = (round(w*self.scale), round(h*self.scale))
        rgb = cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), self.resized, interpolation=cv2.INTER_LINEAR)
        t = time.perf_counter()
        self.embedding = self.encoder.run(None, {"input_image":rgb.astype(np.float32)})[0]
        self.timings["encoder_seconds"] = time.perf_counter()-t

    def predict(self, box, positive=(), negative=()):
        pts = [*positive, *negative, box[:2], box[2:]]
        labels = [1]*len(positive) + [0]*len(negative) + [2, 3]
        h, w = self.shape
        coords = np.asarray(pts, np.float32)*[self.resized[0]/w, self.resized[1]/h]
        feed = dict(image_embeddings=self.embedding, point_coords=coords[None].astype(np.float32),
                    point_labels=np.asarray(labels, np.float32)[None], mask_input=np.zeros((1,1,256,256),np.float32),
                    has_mask_input=np.zeros(1,np.float32), orig_im_size=np.asarray(self.shape,np.float32))
        t = time.perf_counter()
        masks, score, _ = self.decoder.run(None, feed)
        return masks[0,0] > 0, dict(score=float(score.ravel()[0]), seconds=time.perf_counter()-t)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Prepare clean local appearance or prompted masks, without hardware")
    subs = parser.add_subparsers(dest="cmd", required=True)
    clean = subs.add_parser("clean");clean.add_argument("scan");clean.add_argument("output")
    pub = subs.add_parser("publish")
    for name in ("root","appearance","masks","labels"):pub.add_argument(name)
    pub.add_argument("--vectors",default="vectors")
    seg = subs.add_parser("segment")
    seg.add_argument("image");seg.add_argument("prompts");seg.add_argument("output")
    seg.add_argument("--models",required=True);seg.add_argument("--backend",choices=["cpu","coreml"],default="cpu")
    args = parser.parse_args(argv)
    if args.cmd == "clean":
        print(json.dumps(clean_scan(args.scan,args.output)));return
    if args.cmd == "publish":
        result = publish_catalog(args.root,args.appearance,args.masks,json.loads(Path(args.labels).read_text()),vectors=args.vectors)
        print(json.dumps({"objects":list(result["objects"])}));return
    output = Path(args.output)
    output.mkdir(parents=True,exist_ok=False)
    prompts = json.loads(Path(args.prompts).read_text())
    model = MobileSAM(args.models,args.backend)
    image = cv2.imread(args.image)
    if image is None:raise ValueError("Unreadable input image")
    model.set_image(image)
    report = dict(backend=args.backend, model="MobileSAM ONNX", image_sha256=digest(args.image), objects={}, **model.timings)
    for name, prompt in prompts.items():
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in name):
            raise ValueError("Unsafe object id")
        mask, info = model.predict(**prompt)
        cv2.imwrite(str(output/(name+".png")),mask.astype(np.uint8)*255)
        report["objects"][name] = dict(**info, pixels=int(mask.sum()), prompt=prompt)
        print(name,info,flush=True)
    (output/"segmentation.json").write_text(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()

"""Source-bound, offline-prepared appearance and pixel masks for legacy scenes."""
from functools import lru_cache
from pathlib import Path
import json
import os

import cv2
import numpy as np

from slscan.contracts import digest


@lru_cache(maxsize=32)
def _scan_root(absolute):
    return Path(absolute).resolve()


def manifest_path(scan):
    scan = _scan_root(os.path.abspath(scan))
    return scan.parent.parent / "reports" / "scene_regions" / scan.name / "manifest.json"


def stamp(path):
    p = Path(path)
    s = p.stat()
    return s.st_mtime_ns, s.st_size, s.st_ino, s.st_dev


@lru_cache(maxsize=128)
def _digest(path, version):
    return digest(path)


@lru_cache(maxsize=128)
def _asset_path(root, relative, version):
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("Appearance asset outside manifest directory")
    return path


def asset(root, entry):
    candidate = root / entry["path"]
    version = stamp(candidate)
    path = _asset_path(str(root), entry["path"], version)
    if _digest(str(path), version) != entry["sha256"]:
        raise ValueError("Stale appearance asset")
    return path


@lru_cache(maxsize=8)
def _manifest(path, version):
    data = json.loads(Path(path).read_text())
    if data.get("schema_version") != "1.0" or data.get("coordinate_space") != "projector_px":
        raise ValueError("Unsupported appearance manifest")
    return data


@lru_cache(maxsize=16)
def _inputs(scan, path, version):
    data = _manifest(path, version)
    sources = []
    for name, expected in data["input_hashes"].items():
        sources.append((name, scan / name, expected))
    return sources


def catalog(scan):
    if not scan:
        return None
    path = manifest_path(scan)
    if not path.exists():
        return None
    version = stamp(path)
    data = _manifest(str(path), version)
    scan_root = _scan_root(os.path.abspath(scan))
    for name, candidate, expected in _inputs(scan_root, str(path), version):
        source_version = stamp(candidate)
        source = _asset_path(str(scan_root), name, source_version)
        if _digest(str(source), source_version) != expected:
            raise ValueError("Appearance belongs to a different scan; prepare again")
    return path.parent, data


def texture_path(scan):
    cat = catalog(scan)
    return asset(cat[0], cat[1]["appearance"]) if cat else Path(scan) / "scene_pov.png"


@lru_cache(maxsize=32)
def _mask(path, version):
    mask = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise ValueError("Unreadable pixel mask")
    mask = mask > 0
    mask.setflags(write=False)
    return mask


def mask_for_object(scene, obj):
    cat = catalog(scene.scan_dir)
    if cat is None or obj.id not in cat[1]["objects"]:
        return None
    root, data = cat
    path = asset(root, data["objects"][obj.id]["mask"])
    mask = _mask(str(path), stamp(path))
    if mask.shape != (int(scene.table_mm[1]), int(scene.table_mm[0])):
        raise ValueError("Pixel mask size mismatch")
    # Nudge remains explicit, and never silently expands a segmentation.
    dx, dy = obj.extra.get("ajuste", [0, 0])
    if dx or dy:
        mask = cv2.warpAffine(mask.astype(np.uint8), np.float32([[1,0,dx],[0,1,dy]]),
                              tuple(map(int, scene.table_mm)), flags=cv2.INTER_NEAREST) > 0
    return mask


def install_scene_regions(scene):
    """Idempotent provider, reapplied after scene reload; no image/model inference."""
    if not scene.projector_px or scene.coordinate_space == "world_mm":
        return []
    cat = catalog(scene.scan_dir)
    if cat is None:
        return []
    root, data = cat
    if tuple(data["projector_size"]) != tuple(scene.table_mm):
        raise ValueError("Appearance projector size mismatch")
    from .scene import SceneObject
    version = str(manifest_path(scene.scan_dir)), stamp(manifest_path(scene.scan_dir))
    for oid, entry in data["objects"].items():
        old = scene.objects.get(oid)
        if old and old.extra.get("appearance_version") == version:
            continue
        path = asset(root, entry["mask"])
        mask = _mask(str(path), stamp(path))
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            raise ValueError("Empty object mask")
        poly = cv2.approxPolyDP(max(contours,key=cv2.contourArea), .8, True).reshape(-1,2).tolist()
        ys, xs = np.nonzero(mask)
        dx,dy = old.extra.get("ajuste", [0,0]) if old else (0,0)
        if dx or dy:
            poly = [[x+dx,y+dy] for x,y in poly]
        extra = dict(aliases=entry.get("aliases", []), outline=poly, appearance_version=version,
                     pixel_mask=str(path), segmentation=entry.get("method", "offline"))
        if dx or dy:
            extra["ajuste"] = [dx,dy]
        if entry.get("bundle"):
            extra["vector_bundle"] = str(asset(root, entry["bundle"]))
        scene.objects[oid] = SceneObject(oid, entry["name"], entry.get("kind","prop"),
            [int(xs.min())+dx,int(ys.min())+dy,0,int(xs.max())+dx,int(ys.max())+dy,0],poly,
            texture=entry.get("texture","mate"),extra=extra)
    return list(data["objects"])

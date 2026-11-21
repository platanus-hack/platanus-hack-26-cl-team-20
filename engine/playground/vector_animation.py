"""Prepared vector animations in native projector pixels; never use object quads."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import json
import math

import cv2
import numpy as np

from slscan.contracts import digest
from .effects.base import Effect, bgr
from .audio.features import audio_level
from .vectortrace import Trace, prefix
from .appearance import texture_path, install_scene_regions


def _stamp(path):
    stat = Path(path).stat()
    return stat.st_mtime_ns, stat.st_size


@lru_cache(maxsize=4)
def _hash(path, stamp):
    return digest(path)


@lru_cache(maxsize=4)
def _read_bundle(path, stamp, mask_stamp):
    root = Path(path).parent
    data = json.loads(Path(path).read_text())
    if data.get("artifact") != "vector_bundle" or data.get("schema_version") != "1.0":
        raise ValueError("Unsupported vector bundle")
    if data["mask"]["path"] != "mask.png":
        raise ValueError("Expected bundle-local mask.png")
    if digest(root / "mask.png") != data["mask"]["sha256"]:
        raise ValueError("Stale vector mask")
    trace = Trace.from_json(data["trace"])
    mask = cv2.imread(str(root / "mask.png"), cv2.IMREAD_GRAYSCALE)
    if mask is None or mask.shape != (trace.height, trace.width):
        raise ValueError("Vector mask size mismatch")
    return data, trace, mask > 0


def region_order(trace, order):
    regions = list(trace.regions)
    if order == "reverse":
        regions.reverse()
    elif order == "layer":
        regions.sort(key=lambda r: (r.layer, -r.area, r.id))
    elif order == "random":
        np.random.default_rng(12).shuffle(regions)
    elif order != "area":
        raise ValueError("order: area, reverse, layer or random")
    return regions


class VectorAnimation(Effect):
    """Base lives outside effects/ so it is not registered as another plugin."""
    needs = ("textures", "audio")
    targets = ("*",)
    params = dict(bundle="", speed=.12, width=2., color=[255, 220, 170], order="area",
                  progress=-1., loop=True, intensity=1., source_colors=True,
                  audio="", audio_gain=.7, hatch_spacing=8., jitter=.5, capa=5)
    mode = "drawing"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prepared = None
        self._key = None
        self.level = 0.
        self.origin = (0, 0)
        self.status = "waiting_for_bundle"

    def _prepare(self, trace, mask):
        regions = region_order(trace, self.p["order"])
        if self.mode == "sketch":
            threshold = max(0.,float(self.p.get("min_area",0)))
            largest = sorted((r for r in regions if r.area >= threshold),key=lambda r:-r.area)
            limit = max(0,int(self.p.get("max_regions",0)))
            keep = {r.id for r in (largest[:limit] if limit else largest)}
            regions = [r for r in regions if r.id in keep]
        rank = np.full(mask.shape, -1, np.int32)
        fill = np.zeros((*mask.shape, 3), np.uint8)
        sketch = np.zeros_like(fill)
        paths, lengths = [], []
        color = bgr(self.p["color"])
        width = max(1, min(32, round(float(self.p["width"]))))
        for index, r in enumerate(regions):
            rings = [np.asarray(trace.rings[i].points, float) for i in [r.outer, *r.holes]]
            rings = [p for p in rings if len(p) >= 3]
            if not rings:
                continue
            pts = [np.rint(p).astype(np.int32) for p in rings]
            x, y, w, h = cv2.boundingRect(pts[0])
            if x < 0 or y < 0 or x+w > trace.width or y+h > trace.height:
                raise ValueError("Vector coordinates outside texture")
            local = np.zeros((h, w), np.uint8)
            cv2.fillPoly(local, [p-[x, y] for p in pts], 255)
            selected = local > 0
            rank[y:y+h, x:x+w][selected] = index
            col = bgr(trace.palette[r.layer]) if self.p["source_colors"] else color
            fill[y:y+h, x:x+w][selected] = col
            # Dark source regions get denser pencil hatching, with exact holes.
            shade = np.mean(trace.palette[r.layer])/255
            step = max(3, round(float(self.p["hatch_spacing"])*(.5+shade)))
            yy, xx = np.mgrid[y:y+h, x:x+w]
            hatch = selected & ((xx+yy) % step == 0)
            if self.p.get("hatching",True):
                sketch[y:y+h, x:x+w][hatch] = [round(c*.55) for c in color]
            for j, p in enumerate(rings):
                if self.mode == "sketch":
                    if abs(cv2.contourArea(p.astype(np.float32))) < threshold:
                        continue
                    detail = max(0.,float(self.p.get("detail",0)))
                    p = cv2.approxPolyDP(p.astype(np.float32),detail,True).reshape(-1,2)
                    if len(p)<3:continue
                paths.append(p)
                lengths.append(np.linalg.norm(np.diff(np.vstack([p, p[:1]]), axis=0), axis=1).sum())
                rng = np.random.default_rng(r.id*31+j)
                for _ in range(2):
                    line = np.rint(p + rng.normal(0, float(self.p["jitter"]), p.shape)).astype(np.int32)
                    cv2.polylines(sketch, [line], True, color, width, cv2.LINE_AA)
        # A region's outside AA/jitter never escapes the authoritative object mask.
        fill[~mask] = 0
        sketch[~mask] = 0
        self.prepared = dict(trace=trace, mask=mask, rank=rank, fill=fill, sketch=sketch,
                             mask8=mask.astype(np.uint8)*255,
                             paths=paths, lengths=lengths, count=len(regions), cumulative=np.cumsum(lengths),
                             closed_paths=[np.rint(p).astype(np.int32) for p in paths])
        self._draw_layer = np.zeros_like(fill)
        self._draw_complete = 0
        self._draw_style = None

    def update(self, dt, scene):
        super().update(dt, scene)
        if not scene.projector_px or scene.coordinate_space == "world_mm":
            raise ValueError("Vector effects require a legacy projector-pixel scene")
        install_scene_regions(scene)
        source = texture_path(scene.scan_dir) if scene.scan_dir else None
        target_id = self.target.id if self.target else "cuadro"
        obj = scene.objects.get(target_id)
        if obj and any(obj.extra.get("ajuste", [0, 0])):
            raise ValueError("Vector bundle has native alignment; prepare again after nudge")
        if self.p["bundle"]:
            path = Path(self.p["bundle"]).resolve()
        elif obj and obj.extra.get("vector_bundle"):
            path = Path(obj.extra["vector_bundle"])
        elif source:
            path = Path(__file__).resolve().parents[1] / "reports" / "vectortrace" / Path(scene.scan_dir).name / target_id / "trace.json"
        else:
            raise ValueError("No scan or vector bundle")
        if source is None:
            raise ValueError("Vector bundle requires source scan for alignment validation")
        mask_path = path.parent / "mask.png"
        key = (str(path), _stamp(path), _stamp(mask_path), str(source.resolve()), _stamp(source), target_id,
               *(repr(self.p[k]) for k in ("order", "color", "width", "source_colors", "hatch_spacing", "jitter")))
        key += tuple(repr(self.p.get(k)) for k in ("min_area","max_regions","hatching","detail"))
        if key != self._key:
            data, trace, mask = _read_bundle(str(path), _stamp(path), _stamp(mask_path))
            if data["target_id"] != target_id:
                raise ValueError("Vector bundle belongs to a different object")
            if _hash(str(source), _stamp(source)) != data["source_sha256"]:
                raise ValueError("Vector bundle belongs to a different scene_pov; prepare again")
            if tuple(data["projector_size"]) != tuple(scene.table_mm):
                raise ValueError("Vector projector size mismatch")
            self.origin = tuple(data["origin_px"])
            self.projector_size = tuple(data["projector_size"])
            x, y = self.origin
            if x < 0 or y < 0 or x+trace.width > self.projector_size[0] or y+trace.height > self.projector_size[1]:
                raise ValueError("Vector crop outside projector")
            self._prepare(trace, mask)
            self.texture_source = str(source)
            self._key = key
            self.status = "ready"
        self.level = audio_level(scene.audio, self.p["audio"]) if self.p["audio"] else 0.

    def draw(self, canvas):
        if self.prepared is None:
            return
        if canvas.size != self.projector_size or canvas.px_per_mm != 1 or canvas.flip_y or canvas.origin_px != (0., 0.):
            raise ValueError("Vector effects require native projector pixels, without transforms")
        p = self.prepared
        progress = float(self.p["progress"])
        if progress < 0:
            progress = max(0., self.t * float(self.p["speed"]))
            progress = progress % 1 if self.p["loop"] else min(1., progress)
        progress = float(np.clip(progress, 0, 1))
        layer = np.zeros_like(p["fill"])
        if self.mode == "drawing":
            remaining = progress * sum(p["lengths"])
            width = max(1, min(32, round(float(self.p["width"])*(1+self.level*float(self.p["audio_gain"])))))
            complete = int(np.searchsorted(p["cumulative"], remaining, side="right"))
            style = (width, bgr(self.p["color"]))
            if style != self._draw_style or complete < self._draw_complete:
                self._draw_layer[:] = 0
                self._draw_complete = 0
                self._draw_style = style
            if complete > self._draw_complete:
                cv2.polylines(self._draw_layer, p["closed_paths"][self._draw_complete:complete],
                              True, style[1], width, cv2.LINE_AA)
            self._draw_complete = complete
            layer = self._draw_layer.copy()
            if complete < len(p["paths"]) and remaining > 0:
                length = p["lengths"][complete]
                before = p["cumulative"][complete-1] if complete else 0
                points = prefix(p["paths"][complete], min(1., (remaining-before)/max(length, 1e-12)))
                if len(points) >= 2:
                    cv2.polylines(layer, [np.rint(points).astype(np.int32)], False, bgr(self.p["color"]), width, cv2.LINE_AA)
        else:
            total = progress * p["count"]
            visible = (p["rank"] >= 0) & (p["rank"] < math.ceil(total))
            source = p["fill"] if self.mode == "fill" else p["sketch"]
            cv2.copyTo(source, visible.astype(np.uint8), layer)
            if total % 1:
                current = (p["rank"] == int(total)).astype(np.uint8)
                cv2.copyTo(cv2.convertScaleAbs(source, alpha=total % 1), current, layer)
        layer = cv2.bitwise_and(layer, layer, mask=p["mask8"])
        gain = np.clip(float(self.p["intensity"]) * (1+self.level*float(self.p["audio_gain"])), 0, 4)
        if gain != 1:
            layer = cv2.convertScaleAbs(layer, alpha=float(gain))
        x, y = self.origin
        h, w = layer.shape[:2]
        cv2.add(canvas.img[y:y+h, x:x+w], layer, dst=canvas.img[y:y+h, x:x+w])

    def state(self):
        return {**super().state(), "status": self.status, "mask_source": "verified_pixel_mask",
                "texture_source": getattr(self,"texture_source",None),
                "regions": self.prepared["count"] if self.prepared else 0,
                "truncated": self.prepared["trace"].truncated if self.prepared else False,
                "origin_px": list(self.origin)}

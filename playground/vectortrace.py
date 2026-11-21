"""Bounded, deterministic texture tracing. BGR images in; RGB vector data out.

Coordinates are local image pixels. Rings retain color-layer hierarchy and holes;
regions have a stable draw order. No image intermediates or external executables.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import json

import cv2
import numpy as np


@dataclass
class Ring:
    id: int
    layer: int
    parent: int | None
    depth: int
    points: list


@dataclass
class Region:
    id: int
    layer: int
    outer: int
    holes: list[int]
    area: float


@dataclass
class Trace:
    width: int
    height: int
    palette: list
    rings: list[Ring]
    regions: list[Region]
    clip: list
    truncated: bool = False

    def to_json(self):
        return dict(schema_version="1.0", artifact="vector_trace", units="px",
                    coordinate_space="texture_local", **asdict(self))

    @classmethod
    def from_json(cls, data):
        if data.get("schema_version") != "1.0" or data.get("artifact") != "vector_trace":
            raise ValueError("Unsupported vector trace")
        return cls(data["width"], data["height"], data["palette"],
                   [Ring(**r) for r in data["rings"]], [Region(**r) for r in data["regions"]],
                   data["clip"], data.get("truncated", False))

    def svg(self):
        def path(rings):
            return " ".join("M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in r) + " Z"
                            for r in rings if len(r) >= 3)
        items = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.width} {self.height}">',
                 '<defs><clipPath id="mask"><path clip-rule="evenodd" d="' + path(self.clip) + '"/></clipPath></defs>',
                 '<g clip-path="url(#mask)">']
        for r in self.regions:
            rgb = self.palette[r.layer]
            d = path([self.rings[i].points for i in [r.outer, *r.holes]])
            items.append(f'<path id="region-{r.id}" data-layer="{r.layer}" fill="rgb({rgb[0]},{rgb[1]},{rgb[2]})" '
                         f'fill-rule="evenodd" d="{d}"/>')
        return "\n".join([*items, "</g></svg>"])


def _palette(pixels, count):
    # Deterministic sample and Lloyd iteration; no process-global OpenCV RNG.
    sample = pixels[np.linspace(0, len(pixels) - 1, min(4096, len(pixels))).astype(int)].astype(np.float32)
    centers = [sample.mean(axis=0)]
    distance = np.full(len(sample), np.inf)
    for _ in range(count - 1):
        distance = np.minimum(distance, ((sample - centers[-1]) ** 2).sum(axis=1))
        if distance.max() < 1:
            break
        centers.append(sample[distance.argmax()])
    centers = np.asarray(centers)
    for _ in range(8):
        labels = ((sample[:, None] - centers) ** 2).sum(axis=2).argmin(axis=1)
        for k in range(len(centers)):
            if np.any(labels == k):
                centers[k] = sample[labels == k].mean(axis=0)
    centers = np.unique(np.rint(centers).astype(np.uint8), axis=0)
    return centers[np.argsort(centers @ np.array([.114, .587, .299]), kind="stable")]


def trace_image(image, mask=None, *, colors=8, max_size=512, simplify=1., min_area=8., max_regions=1500):
    """Approximate filled regions, ordered largest first, preserving nested rings.

    Limits apply before contour extraction; min_area/simplify use original pixels.
    An authoritative mask must still clip raster output after simplification.
    """
    image = np.asarray(image)
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3 or not image.size:
        raise ValueError("Expected nonempty uint8 BGR image")
    if not (1 <= colors <= 16 and 16 <= max_size <= 1024 and 1 <= max_regions <= 5000
            and np.isfinite([simplify, min_area]).all() and simplify >= 0 and min_area >= 0):
        raise ValueError("Invalid trace limits")
    h, w = image.shape[:2]
    mask = np.ones((h, w), bool) if mask is None else np.asarray(mask, bool)
    if mask.shape != (h, w):
        raise ValueError("Mask and image sizes differ")
    clip = [p.reshape(-1, 2).tolist() for p in cv2.findContours(mask.astype(np.uint8), cv2.RETR_TREE,
                                                             cv2.CHAIN_APPROX_SIMPLE)[0]]
    if not mask.any():
        return Trace(w, h, [], [], [], clip)
    scale = min(1., max_size / max(w, h))
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    small = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    m = cv2.resize(mask.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST).astype(bool)
    if not m.any():
        return Trace(w, h, [], [], [], clip)
    palette = _palette(small[m], int(colors))
    labels = np.full(m.shape, -1, np.int16)
    pixels = small[m].astype(np.float32)
    # Chunk the color distances, bounding memory even with max_size=1024.
    chosen = np.empty(len(pixels), np.int16)
    for start in range(0, len(pixels), 8192):
        chosen[start:start+8192] = ((pixels[start:start+8192, None] - palette.astype(float)) ** 2).sum(2).argmin(1)
    labels[m] = chosen
    rings, regions = [], []
    factor = np.array([w / size[0], h / size[1]])
    for layer in range(len(palette)):
        contours, hierarchy = cv2.findContours((labels == layer).astype(np.uint8), cv2.RETR_TREE,
                                               cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            continue
        offset = len(rings)
        for i, contour in enumerate(contours):
            parent = int(hierarchy[0, i, 3])
            depth, p = 0, parent
            while p >= 0:
                depth += 1
                p = int(hierarchy[0, p, 3])
            pts = contour.reshape(-1, 2).astype(np.float32) * factor.astype(np.float32)
            pts = cv2.approxPolyDP(pts, simplify, True).reshape(-1, 2)
            # Never simplify a real hole to a point/line.
            if len(pts) < 3 and len(contour) >= 3:
                pts = contour.reshape(-1, 2) * factor
            rings.append(Ring(offset+i, layer, offset+parent if parent >= 0 else None, depth, pts.tolist()))
        for i, contour in enumerate(contours):
            ring = rings[offset+i]
            if ring.depth % 2 or len(ring.points) < 3:
                continue
            area = abs(cv2.contourArea(contour)) * float(factor.prod())
            if area < min_area:
                continue
            holes = [offset+j for j in range(len(contours)) if hierarchy[0, j, 3] == i]
            regions.append(Region(len(regions), layer, offset+i, holes, area))
    regions.sort(key=lambda r: (-r.area, r.layer, r.id))
    truncated = len(regions) > max_regions
    regions = regions[:max_regions]
    # Keep ancestors for hierarchy, but discard tiny unused paths to bound the result.
    used = {i for r in regions for i in [r.outer, *r.holes]}
    for i in list(used):
        parent = rings[i].parent
        while parent is not None:
            used.add(parent)
            parent = rings[parent].parent
    mapping = {old: new for new, old in enumerate(sorted(used))}
    rings = [Ring(mapping[r.id], r.layer, mapping.get(r.parent), r.depth, r.points)
             for r in rings if r.id in used]
    for r in regions:
        r.outer, r.holes = mapping[r.outer], [mapping[i] for i in r.holes]
    return Trace(w, h, palette[:, ::-1].tolist(), rings, regions, clip, truncated)


def prefix(points, fraction, closed=True):
    """Arc-length prefix, including an interpolated last point (not vertex count)."""
    p = np.asarray(points, float).reshape(-1, 2)
    if len(p) < 2 or fraction <= 0:
        return np.empty((0, 2))
    if closed:
        p = np.vstack([p, p[0]])
    lengths = np.linalg.norm(np.diff(p, axis=0), axis=1)
    distance = np.clip(fraction, 0, 1) * lengths.sum()
    if fraction >= 1:
        return p
    cumulative = np.r_[0., np.cumsum(lengths)]
    end = min(np.searchsorted(cumulative, distance, side="right") - 1, len(p) - 2)
    t = (distance - cumulative[end]) / max(lengths[end], 1e-12)
    return np.vstack([p[:end+1], p[end] + t * (p[end+1] - p[end])])


def write_bundle(image_path, mask_path, output, *, target_id, colors=8, max_size=512,
                 min_area=16., simplify=1., max_regions=700, denoise=0, provenance=None):
    """Prepare a private, source-bound cache; the runtime never segments at 30 fps.

    Mask must already be measured/segmented from the image, in projector pixels.
    A new output directory is required; source captures are always read-only.
    """
    from pathlib import Path
    from slscan.contracts import digest
    output = Path(output).resolve()
    if output.exists() or "captures" in output.parts:
        raise ValueError("Bundle output must be new and outside captures")
    image_path, mask_path = Path(image_path).resolve(), Path(mask_path).resolve()
    image, mask = cv2.imread(str(image_path)), cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    if image is None or mask is None or mask.shape != image.shape[:2] or not mask.any():
        raise ValueError("Expected image and nonempty mask of the same size")
    yy, xx = np.nonzero(mask)
    x, y, right, bottom = int(xx.min()), int(yy.min()), int(xx.max())+1, int(yy.max())+1
    local_mask = mask[y:bottom, x:right] > 0
    if denoise not in (0, 3, 5, 7, 9):
        raise ValueError("denoise must be 0, 3, 5, 7 or 9 pixels")
    texture = image[y:bottom, x:right]
    if denoise:
        texture = cv2.medianBlur(texture, denoise)
    trace = trace_image(texture, local_mask, colors=colors, max_size=max_size,
                        min_area=min_area, simplify=simplify, max_regions=max_regions)
    output.mkdir(parents=True)
    if not cv2.imwrite(str(output / "mask.png"), local_mask.astype(np.uint8)*255):
        raise OSError("Could not write vector mask")
    data = dict(schema_version="1.0", artifact="vector_bundle", target_id=target_id,
                source=str(image_path), source_sha256=digest(image_path),
                projector_size=list(image.shape[1::-1]), origin_px=[x, y],
                mask=dict(path="mask.png", sha256=digest(output / "mask.png")),
                trace=trace.to_json(), denoise_px=denoise,
                provenance=provenance or {"method": "provided_pixel_mask"})
    (output / "trace.json").write_text(json.dumps(data, separators=(",", ":")))
    (output / "trace.svg").write_text(trace.svg())
    return output / "trace.json"


def main(argv=None):
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description="Trace a local texture without hardware")
    parser.add_argument("image")
    parser.add_argument("--mask")
    parser.add_argument("--out", required=True, help="New .svg or .json file, outside captures")
    parser.add_argument("--colors", type=int, default=8)
    args = parser.parse_args(argv)
    source, out = Path(args.image).resolve(), Path(args.out).resolve()
    if out.exists() or out == source or "captures" in out.parts or out.suffix not in (".svg", ".json"):
        parser.error("Use a new .svg/.json output outside captures")
    image = cv2.imread(str(source))
    mask = cv2.imread(args.mask, cv2.IMREAD_GRAYSCALE) if args.mask else None
    if image is None or (args.mask and mask is None):
        parser.error("Unreadable image/mask")
    trace = trace_image(image, mask, colors=args.colors)
    with out.open("x") as f:
        f.write(trace.svg() if out.suffix == ".svg" else json.dumps(trace.to_json()))
    print(json.dumps(dict(regions=len(trace.regions), rings=len(trace.rings), truncated=trace.truncated)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

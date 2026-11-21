"""Versioned interchange contracts for P2–P7 (JSON Schema + numerical checks).

No camera/hardware imports. NPZ metadata is a scalar Unicode JSON string, never
pickle. Unknown major/minor versions fail closed. JSON schemas are exportable.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

import numpy as np

VERSION = "1.0"
PIXELS = "opencv_integer_centers"
STATES = ["pending", "running", "passed", "failed", "blocked", "unverified"]
NUM = {"type": "number"}
TEXT = {"type": "string", "minLength": 1}
BOOL = {"type": "boolean"}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
NAT = {"type": "integer", "minimum": 0}
FRACTION = {"type": "number", "minimum": 0, "maximum": 1}


def enum(*values):
    return {"enum": list(values)}


def array(items, n=None):
    s = {"type": "array", "items": items}
    if n is not None:
        s.update(minItems=n, maxItems=n)
    return s


def obj(properties, required=None, additional=False):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": additional}


def nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


V2, V3 = array(NUM, 2), array(NUM, 3)
M3, M4 = array(array(NUM, 3), 3), array(array(NUM, 4), 4)
SIZE = array({"type": "integer", "minimum": 1}, 2)
HASHES = {"type": "object", "additionalProperties": HASH}
FREE = {"type": "object"}
ASSET = obj({"path": TEXT, "sha256": HASH})
COMMON = {"schema_version": {"const": VERSION}, "artifact": TEXT,
          "evidence": enum("synthetic", "recorded", "live"),
          "units": enum("mm", "px", "relative"), "input_hashes": HASHES}
DEVICE = obj({"name": TEXT, "kind": enum("camera", "projector"), "size": SIZE,
              "K": M3, "dist": array(NUM), "R": M3, "t": V3,
              "T_device_from_world": M4, "pose_estimated": BOOL,
              "model": obj({"free": array(TEXT), "fixed": array(TEXT)}),
              "uncertainty": FREE, "residuals_by_view": array(FREE)}, additional=True)
STAGE = obj({"state": enum(*STATES), "dependencies": array(TEXT),
             "fingerprint": nullable(HASH), "inputs": HASHES,
             "outputs": HASHES, "duration_s": {"type": "number", "minimum": 0},
             "counts": {"type": "object", "additionalProperties": NAT},
             "metrics": FREE, "units": enum("mm", "px", "relative"),
             "evidence": enum("synthetic", "recorded", "live"),
             "reason": {"type": "string"}, "cached": BOOL,
             "progress": FRACTION}, additional=True)
FIELDS = {
    "pbr_fit": {"texel_count": NAT, "atlas_shape": SIZE,
                "confidence_order": array(TEXT, 4), "mesh_sha256": HASH},
    "object_predictions": {"image_sha256": HASH, "image_size": SIZE, "camera": TEXT,
        "backend": TEXT, "model": TEXT, "instances": array(obj({
            "id": TEXT, "mask": ASSET, "score": FRACTION, "semantic": FREE}))},
    "manifest": {"session_id": TEXT, "code_revision": TEXT, "rig_sha256": nullable(HASH),
                 "world": TEXT, "pixel_convention": {"const": PIXELS},
                 "devices": {"type": "object", "additionalProperties": obj({
                     "size": SIZE, "rotation_deg": enum(0, 90, 180, 270),
                     "downscale": {"type": "number", "exclusiveMinimum": 0},
                     "exposure": FREE, "white_balance": FREE, "focus": FREE})},
                 "patterns": array(obj({"path": TEXT, "kind": TEXT,
                                         "timestamp": nullable(NUM)})),
                 "acquisition_known": BOOL},
    "rig": {"format": {"const": "slscan-rig/2"}, "world": TEXT,
            "pixel_convention": {"const": PIXELS},
            "devices": {"type": "object", "minProperties": 2, "additionalProperties": DEVICE},
            "validated_range": nullable(FREE), "metric_status": enum("unverified", "verified", "inconclusive")},
    "matches": {"camera": TEXT, "projector": TEXT, "camera_size": SIZE,
                "projector_size": SIZE, "pixel_convention": {"const": PIXELS},
                "decoder": TEXT, "confidence_kind": enum("measured", "binary_validity_only"),
                "count": NAT},
    "cloud_quality": {"world": TEXT, "point_count": NAT, "sample_count": NAT,
                      "rejection_codes": {"type": "object", "additionalProperties": TEXT}},
    "mesh_arrays": {"world": TEXT, "vertex_count": NAT, "face_count": NAT},
    "cloud": {"world": TEXT, "rig_sha256": HASH, "matches_sha256": HASH,
              "point_count": NAT, "sample_count": NAT, "coverage": FRACTION,
              "ply": ASSET, "quality": ASSET},
    "mesh": {"world": TEXT, "cloud_sha256": HASH, "vertex_count": NAT,
             "face_count": NAT, "uv_convention": {"const": "top_left_0_1"},
             "ply": ASSET, "arrays": ASSET, "texture": nullable(ASSET),
             "texture_kind": enum("appearance", "albedo", "none")},
    "scene": {"world": TEXT, "rig_sha256": HASH, "mesh": ASSET,
              "materials": nullable(ASSET), "devices": {"type": "object", "additionalProperties": DEVICE},
              "metric_status": enum("unverified", "verified", "inconclusive"),
              "validated_range": nullable(FREE), "render_basis_from_opencv": M3},
    "objects": {"mesh_sha256": HASH, "face_count": NAT, "objects": array(obj({
        "id": TEXT, "label": nullable(TEXT), "face_indices": array(NAT),
        "projector_contour_px": array(V2), "bbox_mm": array(V3, 2),
        "plane": nullable(array(NUM, 4)), "confidence": FRACTION,
        "observation": enum("measured", "partial", "unobserved"),
        "capabilities": array(TEXT), "semantic": FREE, "effect_routes": array(FREE),
        "projector_regions_px": array(obj({"outer": array(V2), "holes": array(array(V2))})),
        "projector_mask": ASSET, "geometry": FREE,
    }, required=["id", "label", "face_indices", "projector_contour_px", "bbox_mm",
                 "plane", "confidence", "observation"]))},
    "radiometry": {"response_kind": enum("effective", "linearized", "unobserved"),
                   "states": array(FREE), "channel_response": array(array(NUM)),
                   "mixing_rgb": M3, "saturation_mask": nullable(ASSET),
                   "gamut": FREE, "reference": FREE},
    "lights": {"world": TEXT, "states": array(FREE), "lights": array(obj({
        "id": TEXT, "type": enum("point", "area", "diffuse", "directional", "group"),
        "observation": enum("position_estimated", "direction_only", "unobserved"),
        "position_mm": nullable(V3), "direction": nullable(V3),
        "intensity_rgb_relative": V3, "temperature_k": nullable(NUM),
        "confidence": FRACTION, "uncertainty": FREE}))},
    "materials": {"mesh_sha256": HASH, "uv_convention": {"const": "top_left_0_1"},
                  "materials": array(obj({"id": TEXT, "observation": enum("estimated", "partial", "unobserved"),
                      "maps": {"type": "object", "additionalProperties": obj({
                          "asset": ASSET, "color_space": enum("sRGB", "linear"),
                          "meaning": enum("albedo", "normal_tangent_opengl", "roughness", "specular", "absorption", "appearance")})},
                      "confidence_mask": nullable(ASSET), "reason": {"type": "string"}}))},
    "pipeline": {"run_id": TEXT, "session_id": TEXT, "status": enum(*STATES),
                 "started_at": TEXT, "updated_at": TEXT, "code_revision": TEXT,
                 "source": TEXT, "stages": {"type": "object", "additionalProperties": STAGE},
                 "physical_verified": BOOL, "cancelled": BOOL},
    "validation": {"status": enum("passed", "failed", "unverified", "inconclusive"),
                   "physical_verified": BOOL, "threshold_mm": {"const": 2},
                   "scope": TEXT, "reason": {"type": "string"},
                   "protocol": nullable(obj({"held_out_pose_ids": array(TEXT), "stationary_repeats": NAT,
                       "reference_dimensions_mm": V2, "prefixed_roi": TEXT,
                       "diagnostic_corrections_applied": BOOL})),
                   "checks": array(obj({"name": TEXT, "max_mm": nullable(NUM),
                       "rms_mm": nullable(NUM), "p95_mm": nullable(NUM), "samples": NAT,
                       "coverage": FRACTION, "uncertainty_mm": nullable(NUM),
                       "held_out": BOOL, "status": enum("passed", "failed", "unverified", "inconclusive")}))},
}
SCHEMAS = {}
for _name, _fields in FIELDS.items():
    SCHEMAS[_name] = {"$schema": "https://json-schema.org/draft/2020-12/schema",
                      "$id": f"urn:slscan:{_name}:{VERSION}",
                      **obj({**COMMON, "artifact": {"const": _name}, **_fields}, additional=True)}


class ContractError(ValueError):
    pass


def header(kind, evidence, units="mm", input_hashes=None):
    return dict(schema_version=VERSION, artifact=kind, evidence=evidence,
                units=units, input_hashes=input_hashes or {})


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def validate(data, kind=None):
    from jsonschema import Draft202012Validator
    kind = kind or data.get("artifact")
    if kind not in SCHEMAS:
        raise ContractError(f"unknown artifact: {kind}")
    # JSON Schema's number accepts NaN in Python; reject it everywhere first.
    try:
        json.dumps(data, allow_nan=False)
    except (ValueError, TypeError) as e:
        raise ContractError(str(e)) from e
    error = next(Draft202012Validator(SCHEMAS[kind]).iter_errors(data), None)
    if error:
        raise ContractError(f"{kind}/{'.'.join(map(str, error.absolute_path))}: {error.message}")
    if kind in ("rig", "cloud", "cloud_quality", "mesh", "mesh_arrays", "scene", "objects", "lights", "validation") and data["units"] != "mm":
        raise ContractError(f"{kind} requires mm")
    if kind in ("rig", "scene"):
        if data["evidence"] == "synthetic" and data["metric_status"] == "verified":
            raise ContractError("synthetic devices cannot be physically verified")
        kinds = []
        for name, d in data["devices"].items():
            K, R, T = map(np.asarray, (d["K"], d["R"], d["T_device_from_world"]))
            if name != d["name"] or K[0, 0] <= 0 or K[1, 1] <= 0 or not np.allclose(K[2], [0, 0, 1]):
                raise ContractError(f"invalid intrinsics/device name: {name}")
            if len(d["dist"]) not in (4, 5, 8, 12, 14):
                raise ContractError(f"invalid OpenCV distortion: {name}")
            if not np.allclose(R.T @ R, np.eye(3), atol=1e-6) or not np.isclose(np.linalg.det(R), 1):
                raise ContractError(f"rotation is not SO(3): {name}")
            if not (np.allclose(T[:3, :3], R) and np.allclose(T[:3, 3], d["t"]) and np.allclose(T[3], [0, 0, 0, 1])):
                raise ContractError(f"inconsistent world transform: {name}")
            kinds.append(d["kind"])
        if "camera" not in kinds or kinds.count("projector") != 1:
            raise ContractError("requires cameras and exactly one projector")
    if kind == "objects":
        ids = []
        for o in data["objects"]:
            ids.append(o["id"])
            if any(i >= data["face_count"] for i in o["face_indices"]):
                raise ContractError("object references a missing face")
            if len(set(o["face_indices"])) != len(o["face_indices"]):
                raise ContractError("duplicate face in object")
            if np.any(np.array(o["bbox_mm"])[0] > np.array(o["bbox_mm"])[1]):
                raise ContractError("inverted bounding box")
        if len(set(ids)) != len(ids):
            raise ContractError("duplicate object ids")
    if kind == "lights":
        for light in data["lights"]:
            if light["observation"] == "position_estimated" and light["position_mm"] is None:
                raise ContractError("estimated position is missing")
            if light["observation"] == "direction_only" and (light["position_mm"] is not None or light["direction"] is None):
                raise ContractError("direction-only light cannot claim a position")
    if kind == "validation" and data["physical_verified"]:
        checks = data["checks"]
        if data["evidence"] == "synthetic" or data["status"] != "passed" or not checks:
            raise ContractError("physical verification requires independent physical measurements")
        for c in checks:
            if (c["status"] != "passed" or not c["held_out"] or c["samples"] == 0 or c["coverage"] < 1
                    or c["max_mm"] is None or c["uncertainty_mm"] is None
                    or c["max_mm"] + c["uncertainty_mm"] > 2):
                raise ContractError("physical verification must include maximum, uncertainty and complete declared scope")
        protocol = data.get("protocol") or {}
        if (len(set(protocol.get("held_out_pose_ids", []))) < 3
                or protocol.get("stationary_repeats", 0) < 3
                or not protocol.get("reference_dimensions_mm")
                or not protocol.get("prefixed_roi")
                or protocol.get("diagnostic_corrections_applied", True)):
            raise ContractError("physical verification requires three held-out poses/repeats, reference, ROI and no corrections")
        if not {"sheet_dimensions", "planarity", "object_outlines"}.issubset({x["name"] for x in checks}):
            raise ContractError("physical verification requires dimensions, planarity and independent outlines")
    if kind == "pipeline" and data["physical_verified"] and data["evidence"] == "synthetic":
        raise ContractError("synthetic pipeline cannot verify physical accuracy")
    return data


def read_json(path, kind=None):
    return validate(json.loads(Path(path).read_text()), kind)


def atomic_json(path, data, kind=None):
    if kind or "artifact" in data:
        validate(data, kind)
    _atomic(path, lambda f: f.write(json.dumps(data, indent=2, allow_nan=False).encode()))


def _atomic(path, writer):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            writer(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


ARRAYS = {
    "pbr_fit": {"albedo": ("f", ("N", 3)), "normal": ("f", ("N", 3)),
                "roughness": ("f", ("N",)), "specular": ("f", ("N",)),
                "confidence": ("f", ("N", 4)), "fit_rms": ("f", ("N",)),
                "condition": ("f", ("N",)), "samples": ("i", ("N",)),
                "owner": ("i", ("H", "W")), "normal_tangent": ("f", ("H", "W", 3))},
    "matches": {"camera_xy": ("f", ("N", 2)), "projector_xy": ("f", ("N", 2)),
                "color_rgb": ("u", ("N", 3)), "confidence": ("f", ("N",)),
                "source_code": ("i", ("N", 2)), "source_bit": ("i", ("N",)),
                **{k: ("b", ("N",)) for k in ("measured", "interpolated", "occluded", "invalid")}},
    "cloud_quality": {"xyz_mm": ("f", ("N", 3)), "color_rgb": ("u", ("N", 3)),
                      "sample_index": ("i", ("N",)), "uncertainty_mm": ("f", ("N",)),
                      "reprojection_px": ("f", ("N", 2)), "rejection_reason": ("i", ("S",))},
    "mesh_arrays": {"vertices_mm": ("f", ("N", 3)), "faces": ("i", ("F", 3)),
                    "uv": ("f", ("N", 2)), "sample_index": ("i", ("N",)),
                    "measured": ("b", ("N",))},
}


def validate_arrays(kind, arrays):
    if kind not in ARRAYS:
        raise ContractError(f"unknown array contract {kind}")
    dims = {}
    for key, (dtype, shape) in ARRAYS[kind].items():
        if key not in arrays:
            raise ContractError(f"{kind}: missing array {key}")
        a = np.asarray(arrays[key])
        if a.dtype.kind != dtype or a.ndim != len(shape):
            raise ContractError(f"{kind}.{key}: expected {dtype}, shape {shape}")
        for actual, expected in zip(a.shape, shape):
            if isinstance(expected, str):
                expected = dims.setdefault(expected, actual)
            if actual != expected:
                raise ContractError(f"{kind}.{key}: mismatched shape")
        if not np.isfinite(a).all():
            raise ContractError(f"{kind}.{key}: non-finite values")
    if kind == "matches":
        masks = sum(np.asarray(arrays[k], int) for k in ("measured", "interpolated", "occluded", "invalid"))
        if not np.all(masks == 1):
            raise ContractError("each match needs exactly one provenance mask")
        if np.any((arrays["confidence"] < 0) | (arrays["confidence"] > 1)):
            raise ContractError("confidence outside [0,1]")
    if kind == "mesh_arrays":
        if np.any(arrays["faces"] < 0) or np.any(arrays["faces"] >= dims["N"]):
            raise ContractError("mesh face index out of range")
        if np.any(arrays["uv"] < 0) or np.any(arrays["uv"] > 1):
            raise ContractError("UV outside [0,1]")
    if kind == "cloud_quality":
        if np.any(arrays["sample_index"] < 0) or np.any(arrays["sample_index"] >= dims["S"]):
            raise ContractError("cloud sample index out of range")
        if np.any(arrays["uncertainty_mm"] < 0) or np.any(arrays["reprojection_px"] < 0):
            raise ContractError("uncertainty and residual norms must be nonnegative")
        if np.any(arrays["rejection_reason"][arrays["sample_index"]] != 0):
            raise ContractError("cloud contains rejected samples")
    if "color_rgb" in arrays and arrays["color_rgb"].dtype != np.uint8:
        raise ContractError("color_rgb must be uint8")
    return dims


def write_npz(path, metadata, arrays, kind=None):
    validate(metadata)
    _validate_bundle(metadata, arrays, kind)
    _atomic(path, lambda f: np.savez_compressed(f, metadata=np.array(json.dumps(metadata, allow_nan=False)), **arrays))


def read_npz(path, kind=None):
    with np.load(path, allow_pickle=False) as z:
        metadata = validate(json.loads(str(z["metadata"].item())))
        arrays = {k: z[k] for k in z.files if k != "metadata"}
    _validate_bundle(metadata, arrays, kind)
    return metadata, arrays


def _validate_bundle(metadata, arrays, kind=None):
    dims = validate_arrays(kind or metadata["artifact"], arrays)
    if metadata["artifact"] == "pbr_fit":
        if (metadata["texel_count"] != dims["N"] or dims["N"] != dims["H"]*dims["W"]
                or metadata["atlas_shape"] != [dims["H"], dims["W"]]):
            raise ContractError("PBR atlas/count mismatch")
        for key in ("confidence", "albedo", "roughness", "specular"):
            if np.any((arrays[key] < 0) | (arrays[key] > 1)):
                raise ContractError(f"PBR {key} outside [0,1]")
    if metadata["artifact"] == "matches":
        if metadata["count"] != dims["N"]:
            raise ContractError("match count differs from arrays")
        valid = arrays["measured"] | arrays["interpolated"]
        for key, size in (("camera_xy", "camera_size"), ("projector_xy", "projector_size")):
            xy = arrays[key][valid]
            if np.any(xy < 0) or np.any(xy >= metadata[size]):
                raise ContractError(f"{key}: out of device bounds")
    if metadata["artifact"] == "cloud_quality":
        if metadata["point_count"] != dims["N"] or metadata["sample_count"] != dims["S"]:
            raise ContractError("cloud metadata counts differ from arrays")
    if metadata["artifact"] == "mesh_arrays":
        if metadata["vertex_count"] != dims["N"] or metadata["face_count"] != dims["F"]:
            raise ContractError("mesh metadata counts differ from arrays")


def validate_files(root, paths):
    """Validate complete declared products and every nested asset reference."""
    root = Path(root).resolve()
    declared = {(root / p).resolve() for p in paths}
    for path in declared:
        if not path.is_relative_to(root) or not path.is_file():
            raise ContractError(f"missing/unsafe product: {path.name}")
    def refs(value, parent):
        if isinstance(value, dict):
            if "path" in value and "sha256" in value:
                target = (parent / value["path"]).resolve()
                if target not in declared or digest(target) != value["sha256"]:
                    raise ContractError(f"missing/changed/undeclared asset: {value['path']}")
            for v in value.values():
                refs(v, parent)
        elif isinstance(value, list):
            for v in value:
                refs(v, parent)
    for path in declared:
        if path.suffix == ".json":
            refs(read_json(path), path.parent)
        elif path.suffix == ".npz":
            read_npz(path)

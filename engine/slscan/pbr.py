"""Offline P7 processing and v1 exports. No acquisition imports or device IO."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from . import contracts as c
from .pbr_model import fit_texels, locate_light, render, unit
from .pbr_protocol import capture_plan, isolate
from .pbr_radiometry import fit_response
from .radiometry import plan


def asset(path, root):
    return dict(path=str(Path(path).relative_to(root)), sha256=c.digest(path))


def checked_asset(root, ref):
    path = (Path(root) / ref["path"]).resolve()
    if not path.is_relative_to(Path(root).resolve()) or not path.is_file() or c.digest(path) != ref["sha256"]:
        raise ValueError("Missing, external or modified P7 asset")
    return path


def uv_geometry(vertices, faces, uv, shape):
    """Rasterize top-left UV at texel centers; reject overlapping UV islands.

    Tangent follows +U, bitangent follows -V (OpenGL). Winding is authoritative.
    Uncovered pixels have face=-1 and zero confidence; geometry is never filled.
    """
    height, width = shape
    xyz = np.zeros((height, width, 3))
    normal, tangent, bitangent = [np.zeros_like(xyz) for _ in range(3)]
    owner = np.full(shape, -1, np.int32)
    for index, face in enumerate(faces):
        tri, st = vertices[face], uv[face]
        duv = np.stack((st[1]-st[0], st[2]-st[0]))
        if abs(np.linalg.det(duv)) < 1e-12:
            continue
        lo = np.maximum(np.floor(st.min(axis=0)*[width, height]-.5).astype(int), 0)
        hi = np.minimum(np.ceil(st.max(axis=0)*[width, height]-.5).astype(int), [width-1, height-1])
        yy, xx = np.mgrid[lo[1]:hi[1]+1, lo[0]:hi[0]+1]
        samples = np.stack(((xx+.5)/width, (yy+.5)/height), axis=-1)
        weights = (samples-st[0]) @ np.linalg.inv(duv)
        inside = (weights[..., 0] >= -1e-9) & (weights[..., 1] >= -1e-9) & (weights.sum(axis=-1) <= 1+1e-9)
        y, x, b = yy[inside], xx[inside], weights[inside]
        positions = tri[0] + b @ np.stack((tri[1]-tri[0], tri[2]-tri[0]))
        overlaps = owner[y, x] >= 0
        if np.any(np.linalg.norm(xyz[y[overlaps], x[overlaps]]-positions[overlaps], axis=-1) > 1e-5):
            raise ValueError("Overlapping UV islands with distinct geometry")
        n = unit(np.cross(tri[1]-tri[0], tri[2]-tri[0]))
        dp = np.linalg.solve(duv, np.stack((tri[1]-tri[0], tri[2]-tri[0])))
        t = unit(dp[0]-n*np.dot(dp[0], n))
        bvec = unit(-dp[1] + n*np.dot(dp[1], n) + t*np.dot(dp[1], t))
        xyz[y, x], normal[y, x], tangent[y, x], bitangent[y, x] = positions, n, t, bvec
        owner[y, x] = index
    return xyz, normal, tangent, bitangent, owner


def sample_camera_to_uv(image, device, vertices, faces, uv, shape):
    """Optional P2/P3 bridge: sample linear camera RGB with nearest-face visibility.

    Conservative nearest sampling avoids crossing silhouettes or depth edges.
    Returns UV data and an explicit visibility mask, never inpaints holes.
    """
    from .object_geometry import rasterize
    xyz, _, _, _, face_ids = uv_geometry(vertices, faces, uv, shape)
    owner, _ = rasterize(vertices, faces, device)
    if image.shape != (device.size[1], device.size[0], 3):
        raise ValueError("Camera frame does not match calibrated resolution")
    xy = cv2.projectPoints(xyz.reshape(-1, 3), cv2.Rodrigues(device.R)[0], device.t,
                           device.K, device.dist)[0].reshape(*shape, 2)
    pixels = np.rint(xy).astype(int)
    depth = (xyz @ device.R.T + device.t)[..., 2]
    valid = (face_ids >= 0) & (depth > 0)
    valid &= (pixels[..., 0] >= 0) & (pixels[..., 0] < device.size[0])
    valid &= (pixels[..., 1] >= 0) & (pixels[..., 1] < device.size[1])
    out = np.zeros((*shape, 3))
    y, x = np.where(valid)
    px, py = pixels[y, x].T
    visible = owner[py, px] == face_ids[y, x]
    valid[y, x] = visible
    out[y[visible], x[visible]] = image[py[visible], px[visible]]
    return out, valid


def save_png(path, values, *, srgb=False):
    values = np.clip(values, 0, 1)
    if srgb:
        values = np.where(values <= .0031308, values*12.92, 1.055*values**(1/2.4)-.055)
    data = np.rint(values*65535).astype(np.uint16)
    if data.ndim == 3:
        data = data[..., ::-1]
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), data):
        raise OSError(f"Could not write {path}")


def process(session_path, output, *, input_hashes=None, progress=None):
    """Read a prepared static UV capture bundle; publish into a fresh directory.

    P2/P3 supply registered, linearized UV frames plus separate matte references.
    Reference observations are kept disjoint from evaluation material texels.
    Only declared fit states reach the BRDF solver, including its initialization.
    """
    session_path, output = Path(session_path).resolve(), Path(output).resolve()
    source = session_path.parent
    if output == source or output.is_relative_to(source):
        raise ValueError("Output must be outside read-only input bundle")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output must be new/empty")
    doc = json.loads(session_path.read_text())
    if doc.get("version") != 1 or doc.get("encoding") != "linear_rgb" or not doc.get("controls_validated"):
        raise ValueError("Version 1, validated linearization/exposure/WB/focus required")
    if doc.get("reference_disjoint") is not True or doc.get("alignment_validated") is not True:
        raise ValueError("Disjoint references and validated static alignment required")
    if not np.isfinite(doc.get("noise", .001)) or doc.get("noise", .001) <= 0:
        raise ValueError("Positive finite measured noise required")
    mesh_path = checked_asset(source, doc["mesh"])
    rig_path = checked_asset(source, doc["rig"])
    frame_path = checked_asset(source, doc["frames"])
    mesh, rig = c.read_json(mesh_path, "mesh"), c.read_json(rig_path, "rig")
    if mesh["evidence"] != doc["evidence"] or rig["evidence"] != doc["evidence"] or mesh["world"] != rig["world"]:
        raise ValueError("Mismatched evidence/world")
    if doc["rig"]["sha256"] != mesh.get("rig_sha256"):
        raise ValueError("Mesh must carry matching rig provenance")
    arrays_path = checked_asset(mesh_path.parent, mesh["arrays"])
    metadata, geo = c.read_npz(arrays_path, "mesh_arrays")
    if metadata["world"] != mesh["world"] or metadata["evidence"] != doc["evidence"]:
        raise ValueError("Mesh arrays world/evidence mismatch")
    states = doc["states"]
    ids = [s["id"] for s in states]
    train = np.array([s["role"] == "fit" for s in states])
    reserved = [i for i, s in enumerate(states) if s["role"] == "held_out"]
    if len(ids) != len(set(ids)) or len(reserved) != 1 or not train.any() or any(s["role"] not in ("fit", "held_out") for s in states):
        raise ValueError("Unique lamp IDs, training states and exactly one reserved lamp required")
    hold = reserved[0]
    shape = tuple(doc["atlas_shape"])
    if len(shape) != 2 or min(shape) <= 0 or max(shape) > 4096:
        raise ValueError("Atlas dimensions outside [1,4096]")
    xyz, ng, tangent, bitangent, owner = uv_geometry(geo["vertices_mm"], geo["faces"], geo["uv"], shape)
    measured_faces = np.all(geo["measured"][geo["faces"]], axis=1)
    measured_uv = (owner >= 0) & measured_faces[np.maximum(owner, 0)] if len(measured_faces) else np.zeros(shape, bool)
    with np.load(frame_path, allow_pickle=False) as data:
        frames = {key: data[key] for key in data.files}
    expected = (len(states), *shape, 3)
    for key in ("on", "off_before", "off_after"):
        if frames[key].shape != expected or not np.isfinite(frames[key]).all() or np.any(frames[key] < 0):
            raise ValueError(f"Invalid {key} frames")
    if frames["valid"].shape != expected[:-1] or frames["valid"].dtype != bool:
        raise ValueError("Explicit boolean visibility masks required")
    object_ids = frames.get("object_ids", np.zeros(shape, int))
    if object_ids.shape != shape or object_ids.dtype.kind not in "iu":
        raise ValueError("Integer object ID atlas shape mismatch")
    object_ids = object_ids.ravel()
    reference_count = len(frames["reference_xyz"])
    for key, size in (("reference_xyz", (reference_count, 3)), ("reference_normal", (reference_count, 3)),
                      ("reference_reflectance", (reference_count, 3)),
                      ("reference_signal", (len(states), reference_count, 3)),
                      ("emitter_origin", (len(states), 3)), ("emitter_ray", (len(states), 3))):
        if frames[key].shape != size or not np.isfinite(frames[key]).all():
            raise ValueError(f"Invalid {key}")
    if np.any(np.abs(np.linalg.norm(frames["emitter_ray"], axis=-1)-1) > .001):
        raise ValueError("Unit emitter rays required")
    if (np.any(np.abs(np.linalg.norm(frames["reference_normal"], axis=-1)-1) > .001)
            or np.any(frames["reference_reflectance"] <= 0) or np.any(frames["reference_reflectance"] > 1)):
        raise ValueError("Unit reference normals and reflectance in (0,1] required")
    if doc.get("reference_status") != "linear_exposure_normalized_stable_unsaturated":
        raise ValueError("Reference patches must be independently isolated and exposure validated")
    camera_doc = rig["devices"][doc["camera"]]
    if camera_doc["kind"] != "camera":
        raise ValueError("P7 view must be a calibrated camera")
    camera = -np.asarray(camera_doc["R"]).T @ camera_doc["t"]
    signals, masks, lights, drifts = [], [], [], []
    for i, state in enumerate(states):
        signal, valid, drift = isolate(frames["on"][i], frames["off_before"][i], frames["off_after"][i],
                                       exposure=state["exposure_ratios"])
        signals.append(signal.reshape(-1, 3))
        masks.append((valid & frames["valid"][i] & measured_uv).ravel())
        drifts.append(float(np.max(drift)))
        lights.append(locate_light(state["id"], frames["emitter_origin"][i], frames["emitter_ray"][i],
                                   frames["reference_xyz"], frames["reference_normal"], frames["reference_reflectance"],
                                   frames["reference_signal"][i], noise=doc.get("noise", .001),
                                   calibrated_color=doc.get("color_calibrated", False), source_type=state["type"]))
    positions = np.array([light["position_mm"] if light["position_mm"] else [0, 0, 0] for light in lights])
    powers = np.array([light["intensity_rgb_relative"] for light in lights])
    usable = np.array([light["observation"] == "position_estimated" and light["type"] == "point" for light in lights])
    masks = np.array(masks) & usable[:, None]
    signals = np.array(signals)
    xyz, ng, tangent, bitangent = [x.reshape(-1, 3) for x in (xyz, ng, tangent, bitangent)]
    fit = fit_texels(xyz, ng, tangent, bitangent, camera, positions[train], powers[train],
                     signals[train], masks[train], noise=doc.get("noise", .001), progress=progress)
    rendered = render(xyz, fit["normal"], fit["albedo"], fit["roughness"], fit["specular"], camera,
                      positions[hold:hold+1], powers[hold:hold+1])[0]
    response = fit_response(plan(), frames["ramp_patch"], high_reference=doc["radiometric_range"])
    input_hashes = {**(input_hashes or {}), "session.json": c.digest(session_path),
                    "mesh.json": c.digest(mesh_path), "rig.json": c.digest(rig_path),
                    "frames.npz": c.digest(frame_path), "mesh_arrays.npz": c.digest(arrays_path)}
    header = lambda kind, units="relative": c.header(kind, doc["evidence"], units, input_hashes)
    output.mkdir(parents=True, exist_ok=True)
    maps = {}
    tangent_normal = np.stack([np.sum(fit["normal"]*axis, axis=-1) for axis in (tangent, bitangent, ng)], axis=-1)
    values = dict(albedo=fit["albedo"], normal=tangent_normal*.5+.5,
                  roughness=fit["roughness"], specular=fit["specular"], absorption=np.zeros(len(xyz)))
    for name, value in values.items():
        path = output / "materials" / f"{name}.png"
        save_png(path, value.reshape(*shape, *value.shape[1:]), srgb=name == "albedo")
        maps[name] = dict(asset=asset(path, output), color_space="sRGB" if name == "albedo" else "linear",
                          meaning="normal_tangent_opengl" if name == "normal" else name)
    confidence_assets = {}
    for i, name in enumerate(("albedo", "normal", "roughness", "specular", "absorption")):
        value = fit["confidence"][:, i] if i < 4 else np.zeros(len(xyz))
        path = output / "materials" / f"{name}_confidence.png"
        save_png(path, value.reshape(shape))
        confidence_assets[name] = asset(path, output)
    complete = np.min(fit["confidence"], axis=1)
    save_png(output / "materials" / "confidence.png", complete.reshape(shape))
    fit_meta = {**header("pbr_fit"), "texel_count": len(xyz), "atlas_shape": list(shape),
                "confidence_order": ["albedo", "normal", "roughness", "specular"], "mesh_sha256": c.digest(mesh_path)}
    c.write_npz(output / "materials" / "fit.npz", fit_meta,
                dict(**fit, owner=owner, normal_tangent=tangent_normal.reshape(*shape, 3)))
    coverage = {name: float(np.mean(fit["confidence"][:, i] > 0)) for i, name in enumerate(("albedo", "normal", "roughness", "specular"))}
    coverage["absorption"] = 0.
    mat = {**header("materials"), "mesh_sha256": c.digest(mesh_path), "uv_convention": "top_left_0_1",
           "materials": [dict(id="opaque-dielectric-fit", observation="partial", maps=maps,
                              confidence_mask=asset(output / "materials" / "confidence.png", output),
                              reason="GGX relative opaque dielectric; absorption unobserved. Zero-confidence values are preview defaults/fits.")],
           "confidence_maps": confidence_assets, "coverage": coverage,
           "diagnostics": asset(output / "materials" / "fit.npz", output),
           "absorption": dict(observation="unobserved", placeholder=0, apply=False,
                              reason="RGB reflection alone cannot identify transmission or spectral/volumetric absorption"),
           "normal_convention": "OpenGL tangent +U,-V; UV image origin top-left",
           "brdf": "Lambert*(1-Schlick Fresnel) + GGX Smith; alpha=roughness^2; scalar dielectric F0",
           "physical_verified": False}
    light_doc = {**header("lights", "mm"), "world": mesh["world"], "states": states, "lights": lights,
                 "temperature_method": "McCamy approximate; null without calibrated color or near-white source"}
    rad = {**header("radiometry"), **response, "states": [vars(s) for s in plan()],
           "saturation_mask": None, "scope": "single reference patch effective RGB transfer"}
    evaluation = masks[hold] & (complete > 0)
    linear_error = np.abs(rendered-signals[hold]) / doc["radiometric_range"]
    luminance_error = np.abs((rendered-signals[hold]) @ np.array([.2126, .7152, .0722])) / doc["radiometric_range"]
    def metrics(mask):
        return dict(samples=int(mask.sum()), coverage=float(mask.mean()),
                    mae_rgb=linear_error[mask].mean(axis=0).tolist() if mask.any() else None,
                    mae_luminance=float(luminance_error[mask].mean()) if mask.any() else None,
                    p95_luminance=float(np.percentile(luminance_error[mask], 95)) if mask.any() else None,
                    max_luminance=float(np.max(luminance_error[mask])) if mask.any() else None)
    photo_metrics = metrics(evaluation)
    validation = {**header("validation", "mm"), "status": "unverified", "physical_verified": False,
                  "threshold_mm": 2, "scope": "P7 held-out lamp appearance; no geometric accuracy certification",
                  "reason": "Offline prediction with disjoint reference light fit; physical/Blender validation pending",
                  "protocol": None, "checks": [], "photometry": dict(held_out=ids[hold], independent=True,
                      normalization=doc["radiometric_range"], **photo_metrics,
                      by_object={str(i): metrics(evaluation & (object_ids == i)) for i in np.unique(object_ids)},
                      max_ambient_drift=drifts, observed_light=bool(usable[hold]))}
    for name, value in (("materials", mat), ("lights", light_doc), ("radiometry", rad), ("validation", validation)):
        c.atomic_json(output / f"{name}.json", value)
    save_png(output / "materials" / "held_out_photo.png", signals[hold].reshape(*shape, 3), srgb=True)
    save_png(output / "materials" / "held_out_render.png", rendered.reshape(*shape, 3), srgb=True)
    save_png(output / "materials" / "held_out_error.png", linear_error.reshape(*shape, 3))
    save_png(output / "materials" / "held_out_valid.png", evaluation.reshape(shape))
    validation["photometry"]["assets"] = {name: asset(output / "materials" / f"held_out_{name}.png", output)
                                            for name in ("photo", "render", "error", "valid")}
    c.atomic_json(output / "validation.json", validation)
    if progress:
        progress(1., "Materiales y validación reservada publicados; sin validación física")
    return dict(coverage=coverage, photometry=photo_metrics, lights_estimated=int(usable.sum()),
                physical_verified=False, evidence=doc["evidence"])


def pipeline_adapter(ctx):
    from .pipeline import Blocked, Result
    session = ctx.source / "pbr" / "session.json"
    if not session.exists():
        raise Blocked("P7 requiere pbr/session.json: luces on/off lineales, referencias, UV y barrido RGB.")
    doc = json.loads(session.read_text())
    if doc["mesh"]["sha256"] != c.digest(ctx.artifact("mesh", "mesh.json")) or doc["evidence"] != ctx.evidence:
        raise Blocked("P7 bundle obsoleto: mesh/evidence no coincide con pipeline actual.")
    result = process(session, ctx.work, input_hashes=ctx.inputs, progress=ctx.progress)
    return Result([str(p.relative_to(ctx.work)) for p in sorted(ctx.work.rglob("*")) if p.is_file()],
                  counts={"lights_estimated": result["lights_estimated"]}, metrics=result,
                  state="unverified", reason="PBR parcial: absorción sin observar; validación física pendiente.")


def add_parser(sub):
    parser = sub.add_parser("pbr", help="P7 offline: protocolo, sintético y ajuste de lámparas fijas")
    commands = parser.add_subparsers(dest="pbr_command", required=True)
    s = commands.add_parser("plan"); s.add_argument("--lamps", nargs="+", required=True)
    s.add_argument("--held-out", required=True)
    s.set_defaults(fn=lambda a: print(json.dumps(capture_plan(a.lamps, a.held_out), indent=2, ensure_ascii=False)) or 0)
    s = commands.add_parser("fit"); s.add_argument("session"); s.add_argument("--out", required=True)
    s.set_defaults(fn=lambda a: print(json.dumps(process(a.session, a.out), indent=2)) or 0)
    s = commands.add_parser("synthetic"); s.add_argument("--out", required=True)
    s.add_argument("--width", type=int, default=12); s.add_argument("--height", type=int, default=8)
    def synthetic(a):
        from .pbr_synthetic import run
        print(json.dumps(run(a.out, (a.height, a.width)), indent=2))
        return 0
    s.set_defaults(fn=synthetic)

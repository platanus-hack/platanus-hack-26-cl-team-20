"""Synthetic reference adapters and conservative recorded-data handoffs.

These adapters exercise every contract without pretending to implement P2–P7.
Recorded metric stages require explicitly bound products from those packages.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import cv2
import numpy as np

from . import contracts as c
from .pipeline import Blocked, Result


def asset(path):
    path = Path(path)
    return {"path": path.name, "sha256": c.digest(path)}


def config(ctx):
    path = ctx.source / "synthetic.json"
    return json.loads(path.read_text()) if path.exists() else {}


def synthetic_rig(ctx):
    cfg = config(ctx)
    devices = {}
    for name, kind, x in (("osmo", "camera", 0.), ("proj", "projector", -80.)):
        K = [[300., 0., 160.], [0., 300., 120.], [0., 0., 1.]]
        if name == "proj":
            K[0][0] = cfg.get("projector_fx", 300.)
        T = np.eye(4)
        T[0, 3] = x
        devices[name] = dict(name=name, kind=kind, size=[320, 240], K=K, dist=[0.] * 5,
                             R=np.eye(3).tolist(), t=[x, 0., 0.], T_device_from_world=T.tolist(),
                             pose_estimated=False, model={"free": [], "fixed": ["synthetic_ground_truth"]},
                             uncertainty={"source": "synthetic_ground_truth"}, residuals_by_view=[])
    return ctx.metadata("rig", format="slscan-rig/2", world="synthetic-world", pixel_convention=c.PIXELS,
                        devices=devices, validated_range=None, metric_status="unverified")


def synthetic_points(ctx):
    x, y = np.meshgrid(np.linspace(-100., 100., 12), np.linspace(-70., 70., 8))
    z = float(config(ctx).get("depth_mm", 800.))
    return np.column_stack([x.ravel(), y.ravel(), np.full(x.size, z)])


def preflight(ctx):
    path = ctx.source / "manifest.json"
    if ctx.runner.synthetic:
        rig = synthetic_rig(ctx)
        data = ctx.metadata("manifest", session_id=ctx.source.name, code_revision="synthetic-adapter-v1",
            rig_sha256=None, world=rig["world"], pixel_convention=c.PIXELS,
            devices={k: dict(size=d["size"], rotation_deg=0, downscale=1,
                            exposure={"status": "synthetic"}, white_balance={"status": "synthetic"},
                            focus={"status": "synthetic"}) for k, d in rig["devices"].items()},
            patterns=[], acquisition_known=True)
    elif not ctx.source.is_dir():
        raise Blocked(f"No existe el scan: {ctx.source}")
    elif path.exists():
        data = c.read_json(path, "manifest")
        if data["evidence"] == "synthetic":
            raise Blocked("El manifiesto es sintético; usar --synthetic explícitamente.")
        for rel, sha in data["input_hashes"].items():
            f = (ctx.source / rel).resolve()
            if not f.is_relative_to(ctx.source) or not f.is_file() or c.digest(f) != sha:
                raise Blocked(f"Entrada no coincide con manifiesto: {rel}")
    else:
        folder = ctx.source / ctx.options["camera"]
        if not folder.is_dir():
            raise Blocked(f"Falta la carpeta de cámara {folder.name}.")
        size = [1, 1]
        if (folder / "map.npz").exists():
            with np.load(folder / "map.npz", allow_pickle=False) as z:
                size = [int(v) for v in z["size"]]
        data = ctx.metadata("manifest", units="relative", session_id=ctx.source.name,
            code_revision=ctx.runner.report["code_revision"], rig_sha256=None, world="unknown",
            pixel_convention=c.PIXELS, devices={ctx.options["camera"]: dict(size=size,
                rotation_deg=0, downscale=1, exposure={"status": "unknown"},
                white_balance={"status": "unknown"}, focus={"status": "unknown"})},
            patterns=[dict(path=str(p.relative_to(ctx.source)), kind="legacy_unknown", timestamp=None)
                      for p in sorted(folder.glob("*.png"))], acquisition_known=False,
            warning="Metadatos históricos desconocidos; rotation/downscale son placeholders, no calibración.")
    c.atomic_json(ctx.work / "manifest.json", data)
    return Result(["manifest.json"], units=data["units"], metrics={"acquisition_known": data["acquisition_known"]})


def match_metadata(ctx, count, camera_size, projector_size, *, binary=False):
    return ctx.metadata("matches", units="px", camera=ctx.options["camera"], projector="proj",
                        camera_size=list(camera_size), projector_size=list(projector_size),
                        pixel_convention=c.PIXELS, decoder="synthetic-v1" if ctx.runner.synthetic else "legacy-graycode-v1",
                        confidence_kind="binary_validity_only" if binary else "measured", count=count)


def match_arrays(cam, pro, rgb, measured):
    n = len(cam)
    return dict(camera_xy=np.asarray(cam, np.float64), projector_xy=np.asarray(pro, np.float64),
                color_rgb=np.clip(rgb, 0, 255).astype(np.uint8), confidence=measured.astype(float),
                source_code=np.asarray(pro, np.int64), source_bit=np.full(n, -1, np.int32),
                measured=measured, interpolated=np.zeros(n, bool), occluded=np.zeros(n, bool), invalid=~measured)


def decode_synthetic(ctx):
    xyz, rig = synthetic_points(ctx), synthetic_rig(ctx)
    points = []
    for d in rig["devices"].values():
        p, _ = cv2.projectPoints(xyz, np.zeros(3), np.asarray(d["t"]), np.asarray(d["K"]), np.zeros(5))
        points.append(p.reshape(-1, 2))
    n = len(xyz)
    arrays = match_arrays(*points, np.tile([85, 155, 210], (n, 1)), np.ones(n, bool))
    meta = match_metadata(ctx, n, [320, 240], [320, 240])
    c.write_npz(ctx.work / "matches.npz", meta, arrays)
    preview = np.full((240, 320, 3), 245, np.uint8)
    for x, y in points[0].astype(int):
        cv2.circle(preview, (x, y), 2, (210, 155, 85), -1)
    cv2.imwrite(str(ctx.work / "decode.png"), preview)
    return Result(["matches.npz", "decode.png"], counts={"measured": n, "interpolated": 0}, units="px")


def decode_recorded(ctx):
    from .graycode import Decoded, load_sequence, decode, visualize
    folder = ctx.source / ctx.options["camera"]
    try:
        items = load_sequence(folder)
    except FileNotFoundError:
        items = []
    if items:
        dec = decode(items, threshold=ctx.options["threshold"], skip_bits=ctx.options["skip_bits"],
                     progress=lambda i, n, *args: ctx.progress(i / max(1, n), f"Par {i}/{n}"))
        decoder = "graycode-replay-v1"
    elif (folder / "map.npz").exists():
        dec = Decoded.load(folder / "map.npz")
        decoder = "legacy-cache-unversioned"
    else:
        raise Blocked("Faltan secuencia Gray code y map.npz.")
    cam, pro, bgr = dec.points()
    size = ctx.options["projector_size"]
    measured = (pro >= 0).all(axis=1) & (pro < size).all(axis=1)
    arrays = match_arrays(cam, pro, bgr[:, ::-1], measured)
    meta = match_metadata(ctx, len(cam), dec.size, size, binary=True)
    meta.update(decoder=decoder, skip_bits=dec.stats.get("skip_bits", ctx.options["skip_bits"]),
                quantization_px=2 ** dec.stats.get("skip_bits", ctx.options["skip_bits"]),
                warning="Sin confianza por bit; no convierte caché histórico en correspondencia subpíxel certificada.")
    c.write_npz(ctx.work / "matches.npz", meta, arrays)
    paths = ["matches.npz"]
    for key, im in visualize(dec).items():
        name = f"decode_{key}.png"
        # Local derived preview, never embedded in reports or uploaded.
        scale = min(1., 640 / max(im.shape[:2]))
        cv2.imwrite(str(ctx.work / name), cv2.resize(im, None, fx=scale, fy=scale))
        paths.append(name)
    return Result(paths, counts={"measured": int(measured.sum()), "invalid": int((~measured).sum()),
                                "interpolated": 0, "frames": len(items)}, units="px",
                  metrics={"camera_coverage": float(measured.sum() / np.prod(dec.size)), "decoder": decoder})


def validate_rig(ctx):
    if ctx.runner.synthetic:
        data = c.read_json(ctx.runner.rig, "rig") if ctx.runner.rig.exists() else synthetic_rig(ctx)
        if data["evidence"] != "synthetic":
            raise Blocked("Un ensayo sintético requiere un rig sintético.")
    else:
        if not ctx.runner.rig.exists():
            raise Blocked("Falta rig.json compatible; P2 debe calibrar y vincularlo al manifiesto del scan.")
        try:
            data = c.read_json(ctx.runner.rig, "rig")
        except c.ContractError as e:
            raise Blocked(f"Rig sin contrato v1; P2 debe aportar procedencia y compatibilidad: {e}") from e
        manifest = c.read_json(ctx.artifact("preflight", "manifest.json"))
        if not manifest["acquisition_known"] or manifest["rig_sha256"] != c.digest(ctx.runner.rig):
            raise Blocked("Rig no vinculado a esta adquisición: falta manifest.rig_sha256/configuración efectiva.")
        if data["evidence"] == "synthetic" or data["world"] != manifest["world"]:
            raise Blocked("El rig y la sesión no comparten evidencia/mundo físico.")
        for name, d in data["devices"].items():
            if name not in manifest["devices"] or d["size"] != manifest["devices"][name]["size"]:
                raise Blocked(f"Frame incompatible para {name}.")
        matches, _ = c.read_npz(ctx.artifact("decode", "matches.npz"))
        for key, size in (("camera", "camera_size"), ("projector", "projector_size")):
            name = matches[key]
            if name not in data["devices"] or matches[size] != data["devices"][name]["size"]:
                raise Blocked(f"Resolución decodificada incompatible con el rig: {name}.")
    c.atomic_json(ctx.work / "rig.json", data)
    intrinsics = {k: {f: d[f] for f in ("K", "dist", "size")} for k, d in data["devices"].items()}
    poses = {k: d["T_device_from_world"] for k, d in data["devices"].items()}
    return Result(["rig.json"], counts={"devices": len(data["devices"])},
                  metrics={"metric_status": data["metric_status"], "intrinsics_sha256": c.canonical_hash(intrinsics),
                           "poses_sha256": c.canonical_hash(poses)})


def triangulate_synthetic(ctx):
    from .recon import Matches, triangulate_pair, write_ply
    from .rig import Rig
    rp = ctx.artifact("validate_rig", "rig.json")
    mp = ctx.artifact("decode", "matches.npz")
    _, a = c.read_npz(mp)
    rig = Rig.load(rp)
    m = Matches(a["camera_xy"], a["projector_xy"], a["color_rgb"][:, ::-1])
    xyz = triangulate_pair(m, rig.cameras()[0], rig.projector(), mode="world")
    n = len(xyz)
    err = np.linalg.norm(xyz - synthetic_points(ctx), axis=1)
    if err.max() > .1:
        raise ValueError(f"Synthetic triangulation error {err.max():.4g} mm")
    write_ply(ctx.work / "cloud.ply", xyz, m.color, comment="synthetic; mm; not physically verified")
    meta = ctx.metadata("cloud_quality", world=rig.world, point_count=n, sample_count=n,
                        rejection_codes={"0": "accepted", "1": "invalid", "2": "reprojection"})
    arrays = dict(xyz_mm=xyz.astype(float), color_rgb=a["color_rgb"], sample_index=np.arange(n),
                  uncertainty_mm=np.zeros(n), reprojection_px=np.zeros((n, 2)), rejection_reason=np.zeros(n, np.int32))
    c.write_npz(ctx.work / "cloud_quality.npz", meta, arrays)
    data = ctx.metadata("cloud", world=rig.world, rig_sha256=c.digest(rp), matches_sha256=c.digest(mp),
        point_count=n, sample_count=n, coverage=1., ply=asset(ctx.work / "cloud.ply"),
        quality=asset(ctx.work / "cloud_quality.npz"))
    c.atomic_json(ctx.work / "cloud.json", data)
    return Result(["cloud.ply", "cloud_quality.npz", "cloud.json"], counts={"measured": n, "rejected": 0},
                  metrics={"synthetic_max_mm": float(err.max()), "coverage": 1.})


def mesh_synthetic(ctx):
    _, a = c.read_npz(ctx.artifact("triangulate", "cloud_quality.npz"))
    vertices = a["xyz_mm"]
    faces = []
    for y in range(7):
        for x in range(11):
            i = y * 12 + x
            faces.extend([[i, i + 1, i + 12], [i + 1, i + 13, i + 12]])
    faces = np.array(faces, np.int32)
    uv = (vertices[:, :2] - vertices[:, :2].min(axis=0)) / np.ptp(vertices[:, :2], axis=0)
    meta = ctx.metadata("mesh_arrays", world="synthetic-world", vertex_count=len(vertices), face_count=len(faces))
    c.write_npz(ctx.work / "mesh.npz", meta, dict(vertices_mm=vertices, faces=faces, uv=uv,
                sample_index=np.arange(len(vertices)), measured=np.ones(len(vertices), bool)))
    from .metric import Mesh, write_mesh_ply
    # Keep this fixture independent of production meshing heuristics.
    mesh = Mesh(vertices=vertices, faces=faces, colors=a["color_rgb"][:, ::-1], method="grid")
    write_mesh_ply(ctx.work / "mesh.ply", mesh, comment="synthetic grid; mm")
    data = ctx.metadata("mesh", world="synthetic-world", cloud_sha256=c.digest(ctx.artifact("triangulate", "cloud.json")),
        vertex_count=len(vertices), face_count=len(faces), uv_convention="top_left_0_1",
        ply=asset(ctx.work / "mesh.ply"), arrays=asset(ctx.work / "mesh.npz"), texture=None, texture_kind="none")
    c.atomic_json(ctx.work / "mesh.json", data)
    return Result(["mesh.npz", "mesh.ply", "mesh.json"], counts={"vertices": len(vertices), "faces": len(faces)})


def segment_synthetic(ctx):
    from .objects import segment_pipeline
    return segment_pipeline(ctx)


def materials_synthetic(ctx):
    radiometry = ctx.metadata("radiometry", units="relative", response_kind="unobserved", states=[],
        channel_response=[], mixing_rgb=np.eye(3).tolist(), saturation_mask=None, gamut={"status": "unobserved"},
        reference={"status": "synthetic_placeholder"})
    lights = ctx.metadata("lights", world="synthetic-world", states=[], lights=[dict(id="fixture-light",
        type="directional", observation="direction_only", position_mm=None, direction=[0., 0., -1.],
        intensity_rgb_relative=[1., 1., 1.], temperature_k=None, confidence=1., uncertainty={"synthetic": True})])
    materials = ctx.metadata("materials", mesh_sha256=c.digest(ctx.artifact("mesh", "mesh.json")),
        uv_convention="top_left_0_1", materials=[dict(id="fixture-matte", observation="unobserved", maps={},
            confidence_mask=None, reason="Fixture de contratos; no se estimaron materiales PBR.")])
    for name, data in (("radiometry", radiometry), ("lights", lights), ("materials", materials)):
        c.atomic_json(ctx.work / f"{name}.json", data)
    return Result(["radiometry.json", "lights.json", "materials.json"], counts={"lights": 1, "materials": 1},
                  state="unverified", reason="Adaptador sintético: materiales sin medir.")


def register_synthetic(ctx):
    rp, mp = ctx.artifact("validate_rig", "rig.json"), ctx.artifact("mesh", "mesh.json")
    rig = c.read_json(rp)
    # The tiny fixture scene is self-contained, including all nested asset refs.
    for name in ("mesh.json", "mesh.ply", "mesh.npz"):
        shutil.copyfile(ctx.artifact("mesh", name), ctx.work / name)
    mat = ctx.artifact("radiometry_materials", "materials.json")
    shutil.copyfile(mat, ctx.work / "materials.json")
    data = ctx.metadata("scene", world=rig["world"], rig_sha256=c.digest(rp), mesh=asset(ctx.work / "mesh.json"),
        materials=asset(ctx.work / "materials.json"), devices=rig["devices"], metric_status="unverified",
        validated_range=None, render_basis_from_opencv=np.diag([1, -1, -1]).tolist())
    c.atomic_json(ctx.work / "scene.json", data)
    _, a = c.read_npz(ctx.work / "mesh.npz")
    d = rig["devices"]["proj"]
    projected, _ = cv2.projectPoints(a["vertices_mm"], np.asarray(d["R"]), np.asarray(d["t"]),
                                    np.asarray(d["K"]), np.asarray(d["dist"]))
    frame = np.full((d["size"][1], d["size"][0], 3), 255, np.uint8)
    for face in a["faces"]:
        cv2.polylines(frame, [np.rint(projected[face, 0]).astype(np.int32)], True, (0, 0, 0), 1)
    cv2.imwrite(str(ctx.work / "render.png"), frame)
    return Result(["scene.json", "mesh.json", "mesh.ply", "mesh.npz", "materials.json", "render.png"], counts={"devices": len(rig["devices"])},
                  state="unverified", reason="Escena sintética; render físico pendiente de P4.")


def verify_synthetic(ctx):
    data = ctx.metadata("validation", status="unverified", physical_verified=False, threshold_mm=2,
        scope="synthetic fixture only", reason="No hay hojas reservadas ni fotos de contornos físicos.", protocol=None, checks=[])
    c.atomic_json(ctx.work / "validation.json", data)
    return Result(["validation.json"], state="unverified", reason=data["reason"])


PRODUCTS = {"triangulate": ("cloud.json", "cloud.ply", "cloud_quality.npz"),
            "mesh": ("mesh.json", "mesh.ply", "mesh.npz"), "segment": ("objects.json",),
            "radiometry_materials": ("radiometry.json", "lights.json", "materials.json"),
            "register_render": ("scene.json",), "verify": ("validation.json",)}


def import_product(ctx):
    folder = ctx.source / "products" / ctx.stage
    required = PRODUCTS[ctx.stage]
    if not all((folder / name).is_file() for name in required):
        raise Blocked(f"Falta adaptador/producto de P2–P7 para {ctx.stage}: products/{ctx.stage}/{required[0]}.")
    dependencies = {k: v for k, v in ctx.inputs.items() if "/" in k and k.split("/")[0] in ctx.runner.report["stages"]
                    and not k.endswith("/fingerprint")}
    paths = set(required)
    inspected = set()
    def references(value, parent=folder):
        if isinstance(value, dict):
            if set(value) == {"path", "sha256"}:
                p = (parent / value["path"]).resolve()
                if not p.is_relative_to(folder.resolve()) or not p.is_file() or c.digest(p) != value["sha256"]:
                    raise Blocked(f"Asset faltante, externo o modificado: {value['path']}")
                paths.add(str(p.relative_to(folder)))
                if p.suffix == ".json" and p not in inspected:
                    inspected.add(p)
                    references(c.read_json(p), p.parent)
            for v in value.values():
                references(v, parent)
        elif isinstance(value, list):
            for v in value:
                references(v, parent)
    data = c.read_json(folder / required[0])
    if data["evidence"] == "synthetic":
        raise Blocked("Producto sintético no aceptado para scan grabado.")
    if any(data["input_hashes"].get(k) != v for k, v in dependencies.items()):
        raise Blocked("Producto obsoleto: input_hashes no coincide con las dependencias actuales.")
    for name in required:
        if name.endswith(".json"):
            doc = c.read_json(folder / name)
            if doc["evidence"] == "synthetic":
                raise Blocked(f"Contrato sintético en producto grabado: {name}")
            references(doc)
        elif name.endswith(".npz"):
            c.read_npz(folder / name)
    for rel in sorted(paths):
        target = ctx.work / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(folder / rel, target)
    c.validate_files(ctx.work, paths)
    state, reason = "passed", "Producto grabado validado; no implica precisión física."
    if ctx.stage == "verify":
        state = "passed" if data["physical_verified"] else "unverified"
        reason = data["reason"]
    return Result(sorted(paths), state=state, reason=reason)


def synthetic_adapters():
    return dict(preflight=preflight, decode=decode_synthetic, validate_rig=validate_rig,
                triangulate=triangulate_synthetic, mesh=mesh_synthetic, segment=segment_synthetic,
                radiometry_materials=materials_synthetic, register_render=register_synthetic, verify=verify_synthetic)


def recorded_adapters():
    return dict(preflight=preflight, decode=decode_recorded, validate_rig=validate_rig,
                **{stage: segment_recorded if stage == "segment" else materials_recorded if stage == "radiometry_materials" else import_product for stage in PRODUCTS})


def materials_recorded(ctx):
    if (ctx.source / "products" / "radiometry_materials" / "materials.json").is_file():
        return import_product(ctx)
    from .pbr import pipeline_adapter
    return pipeline_adapter(ctx)


def segment_recorded(ctx):
    if (ctx.source / "products" / "segment" / "objects.json").is_file():
        return import_product(ctx)
    from .objects import segment_pipeline
    return segment_pipeline(ctx)

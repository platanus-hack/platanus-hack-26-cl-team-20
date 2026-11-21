"""P5: calibrated mesh -> generic objects, optional local SAM/VLM predictions."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path

import cv2
import numpy as np

from . import contracts as c
from .object_geometry import Options, geometric_labels, plane_fit, rasterize, regions, split_components
from .object_semantics import describe, routes
from .rig import Rig


def asset_path(root, asset):
    root = Path(root).resolve()
    path = (root / asset["path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file() or c.digest(path) != asset["sha256"]:
        raise ValueError(f"Missing, external or stale asset: {asset['path']}")
    return path


def read_predictions(path, image_path, camera, evidence):
    path = Path(path)
    data = c.read_json(path, "object_predictions")
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError("Prediction image missing")
    if (data["image_sha256"] != c.digest(image_path) or data["camera"] != camera
            or data["image_size"] != [image.shape[1], image.shape[0]]):
        raise ValueError("Predictions do not match image/camera/resolution")
    if data["evidence"] != evidence or (data["backend"] == "stub" and evidence != "synthetic"):
        raise ValueError("Synthetic/stub predictions cannot label a physical scan")
    ids, instances = set(), []
    hashes = {"predictions.json": c.digest(path)}
    for item in data["instances"]:
        if item["id"] in ids:
            raise ValueError("Duplicate prediction id")
        ids.add(item["id"])
        mask_path = asset_path(path.parent, item["mask"])
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is None or mask.shape != image.shape[:2] or not np.isin(mask, [0, 255]).all():
            raise ValueError("Mask must be binary 0/255 at original camera resolution")
        semantic = describe(item["semantic"], source=data.get("semantic_model", "none"))
        instances.append(dict(id=item["id"], mask=mask > 0, score=item["score"], semantic=semantic))
        hashes[f"prediction/{item['mask']['path']}"] = c.digest(mask_path)
    return instances, hashes


def assign_masks(owner, instances, count, fraction):
    labels = np.full(count, -1, np.int32)
    visible = np.bincount(owner[owner >= 0], minlength=count)
    # Small, confident masks win over enclosing SAM masks. Each face belongs once.
    order = sorted(range(len(instances)), key=lambda i: (int(instances[i]["mask"].sum()),
                                                         -instances[i]["score"], instances[i]["id"]))
    for i in order:
        mask = instances[i]["mask"]
        selected = owner[mask & (owner >= 0)]
        covered = np.bincount(selected, minlength=count)
        fit = (visible > 0) & (covered >= fraction * visible) & (labels < 0)
        labels[fit] = i
    return labels


def segment_mesh(mesh_path, rig_path, output, *, camera=None, predictions=None,
                 options=Options(), input_hashes=None, progress=None, cloud_path=None):
    """Write only a fresh output directory. Existing captures/products are read-only."""
    mesh_path, rig_path, output = Path(mesh_path).resolve(), Path(rig_path).resolve(), Path(output).resolve()
    mesh = c.read_json(mesh_path, "mesh")
    rig_doc = c.read_json(rig_path, "rig")
    rig = Rig.load(rig_path)
    if mesh["world"] != rig.world or mesh["evidence"] != rig_doc["evidence"]:
        raise ValueError("Mesh and rig world/evidence mismatch")
    mesh_rig = mesh.get("rig_sha256")
    if mesh_rig is None:
        # P6 fixture/imports trace rig through cloud; never silently accept another rig.
        cloud = Path(cloud_path) if cloud_path else mesh_path.parent / "cloud.json"
        if cloud.exists() and c.digest(cloud) == mesh["cloud_sha256"]:
            mesh_rig = c.read_json(cloud, "cloud")["rig_sha256"]
    if mesh_rig != c.digest(rig_path):
        raise ValueError("Mesh has no matching rig provenance")
    arrays_path = asset_path(mesh_path.parent, mesh["arrays"])
    metadata, arrays = c.read_npz(arrays_path, "mesh_arrays")
    if (metadata["world"] != mesh["world"] or metadata["evidence"] != mesh["evidence"]
            or metadata["face_count"] != mesh["face_count"]
            or metadata["vertex_count"] != mesh["vertex_count"]):
        raise ValueError("Mesh metadata mismatch")
    vertices, faces = arrays["vertices_mm"], arrays["faces"]
    image_path = asset_path(mesh_path.parent, mesh["texture"]) if mesh["texture"] else None
    camera = camera or (rig.cameras()[0].name if len(rig.cameras()) == 1 else None)
    if camera not in rig.devices or rig[camera].kind != "camera":
        raise ValueError("Choose the calibrated texture camera")
    hashes = {**(input_hashes or {}), "mesh.json": c.digest(mesh_path), "rig.json": c.digest(rig_path),
              "mesh.npz": c.digest(arrays_path)}
    if cloud_path:
        hashes["cloud.json"] = c.digest(cloud_path)
    instances = []
    image, colors = None, None
    if image_path:
        image = cv2.imread(str(image_path))
        if image is None or [image.shape[1], image.shape[0]] != list(rig[camera].size):
            raise ValueError("Texture must match calibrated camera resolution")
        hashes["appearance"] = c.digest(image_path)
        uv = arrays["uv"][faces].mean(axis=1)
        xy = np.floor(uv * rig[camera].size).astype(int)
        xy = np.clip(xy, 0, np.array(rig[camera].size) - 1)
        colors = image[xy[:, 1], xy[:, 0]].astype(float)
    if predictions:
        if image_path is None:
            raise ValueError("SAM predictions require the original calibrated texture image")
        instances, extra_hashes = read_predictions(predictions, image_path, camera, mesh["evidence"])
        hashes.update(extra_hashes)
    if output == mesh_path.parent or output.is_relative_to(mesh_path.parent):
        raise ValueError("Output must be outside mesh input directory")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be empty; publish a new generation")
    output.mkdir(parents=True, exist_ok=True)
    def update(fraction, message):
        if progress:
            progress(fraction, message)
    update(.05, "Planos RANSAC y continuidad 3D")
    labels, edges = geometric_labels(vertices, faces, options, colors)
    prediction_labels = np.full(len(faces), -1, np.int32)
    camera_metrics = None
    if instances:
        owner, camera_metrics = rasterize(vertices, faces, rig[camera],
                                          lambda f: update(.1 + .3 * f, "Visibilidad cámara"))
        prediction_labels = assign_masks(owner, instances, len(faces), options.mask_face_fraction)
        # Segmenter masks cut geometry; geometry continuity still splits disconnected parts.
        labels = np.where(prediction_labels >= 0, prediction_labels + options.max_planes, labels)
    groups = split_components(labels, edges)
    owner, projector_metrics = rasterize(vertices, faces, rig.projector(),
                                        lambda f: update(.4 + .3 * f, "Visibilidad proyector"))
    mesh_hash = c.digest(mesh_path)
    objects, products = [], []
    for number, group in enumerate(groups):
        update(.7 + .29 * number / max(1, len(groups)), "Objetos, máscaras y capacidades")
        vertex_ids = np.unique(faces[group])
        points = vertices[vertex_ids]
        plane, residual = plane_fit(points, options.noise_mm * options.plane_sigma)
        pid = prediction_labels[group[0]]
        semantic = instances[pid]["semantic"] if pid >= 0 else describe()
        # IDs contain mesh identity, so a new scene never inherits the old object's label.
        identity = hashlib.sha256((mesh_hash + ":" + ",".join(map(str, group))).encode()).hexdigest()[:16]
        oid = f"object-{identity}"
        mask = np.isin(owner, group).astype(np.uint8) * 255
        polygons = regions(mask)
        mask_name = f"masks/{oid}.png"
        (output / "masks").mkdir(exist_ok=True)
        if not cv2.imwrite(str(output / mask_name), mask):
            raise OSError("Could not write projector mask")
        products.append(mask_name)
        capabilities = ["mesh", "normals"]
        if plane is not None:
            capabilities.append("planar")
        if image is not None:
            capabilities.append("textured")
        visible_faces = np.unique(owner[mask > 0])
        observation = "unobserved" if not len(visible_faces) else "partial"
        objects.append(dict(id=oid, label=semantic["type"] if semantic["status"] != "unknown" else None,
            face_indices=group.tolist(), bbox_mm=[points.min(axis=0).tolist(), points.max(axis=0).tolist()],
            plane=plane, confidence=float(instances[pid]["score"] if pid >= 0 else 0),
            observation=observation, semantic=semantic, capabilities=capabilities,
            effect_routes=routes(semantic, capabilities), projector_regions_px=polygons,
            projector_contour_px=polygons[0]["outer"] if polygons else [],
            projector_mask=dict(path=mask_name, sha256=c.digest(output / mask_name)),
            geometry=dict(method="sam+mesh" if pid >= 0 else "ransac+mesh_components",
                prediction_id=instances[pid]["id"] if pid >= 0 else None,
                plane_max_residual_mm=residual, measured_vertices=int(arrays["measured"][vertex_ids].sum()),
                visible_projector_faces=len(visible_faces), visible_projector_pixels=int(np.count_nonzero(mask)),
                coverage_scope="reconstructed_visible_surface_only; no_complete_object_claim")))
    data = {**c.header("objects", mesh["evidence"], "mm", hashes), "mesh_sha256": mesh_hash,
            "face_count": len(faces), "objects": objects, "world": mesh["world"],
            "rig_sha256": c.digest(rig_path), "projector_size": list(rig.projector().size),
            "camera": camera, "options": asdict(options), "physical_verified": False,
            "visibility": {"camera": camera_metrics, "projector": projector_metrics},
            "semantic_status": "model_predictions" if instances else "unknown_no_model",
            "contour_scope": "largest_island_preview; use_projector_mask_for_clipping"}
    c.atomic_json(output / "objects.json", data)
    products.append("objects.json")
    c.validate_files(output, products)
    return data, products


def segment_pipeline(ctx):
    """P6 adapter; prediction assets live under scan/products/segment and are hashed."""
    from .pipeline import Result
    prediction = ctx.source / "products" / "segment" / "predictions.json"
    mesh_path = ctx.artifact("mesh", "mesh.json")
    rig_path = ctx.artifact("validate_rig", "rig.json")
    cloud_path = ctx.artifact("triangulate", "cloud.json")
    mesh = c.read_json(mesh_path, "mesh")
    cloud = c.read_json(cloud_path, "cloud")
    if mesh["cloud_sha256"] != c.digest(cloud_path) or cloud["rig_sha256"] != c.digest(rig_path):
        raise ValueError("Stale mesh/cloud/rig chain")
    data, paths = segment_mesh(mesh_path, rig_path, ctx.work, camera=ctx.options.get("object_camera"),
        predictions=prediction if prediction.exists() else None, input_hashes=ctx.inputs, cloud_path=cloud_path,
        options=Options(**ctx.options.get("objects", {})), progress=ctx.progress)
    return Result(paths, counts={"objects": len(data["objects"]),
                                "semantic_known": sum(o["label"] is not None for o in data["objects"])},
                  state="unverified", reason="Objetos sobre malla; borde físico y semántica sin verificar.")


def add_parser(subparsers):
    parser = subparsers.add_parser("objects", help="P5: objetos genéricos de malla métrica, sin hardware")
    parser.add_argument("mesh", help="mesh.json de P3")
    parser.add_argument("--rig", required=True)
    parser.add_argument("--out", required=True, help="directorio nuevo fuera de las entradas")
    parser.add_argument("--camera")
    parser.add_argument("--cloud", help="cloud.json si P6 lo guarda en otro directorio")
    parser.add_argument("--predictions", help="predictions.json del worker local SAM/VLM")
    parser.add_argument("--noise-mm", type=float, default=.5)
    parser.set_defaults(fn=command)


def command(args):
    data, _ = segment_mesh(args.mesh, args.rig, args.out, camera=args.camera,
                           predictions=args.predictions, options=Options(noise_mm=args.noise_mm), cloud_path=args.cloud)
    print(f"{len(data['objects'])} objetos; {data['semantic_status']}; sin verificación física")
    return 0

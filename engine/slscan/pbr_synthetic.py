"""Deterministic tabletop-size material scene, separate calibration and holdout.

Truth is written for auditing only; process() never reads it. This is a controlled
direct-light benchmark, not a photorealistic model of an entire house.
"""
from __future__ import annotations

import json
from pathlib import Path
import cv2
import numpy as np

from . import contracts as c
from .pbr import asset, process, uv_geometry
from .pbr_model import emitter_regions, irradiance, render, unit
from .radiometry import plan


def create_bundle(folder, shape=(8, 12), seed=7):
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()):
        raise ValueError("Synthetic bundle directory must be empty")
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    height, width = shape
    if min(shape) < 2 or max(shape) > 128:
        raise ValueError("Synthetic dimensions must be between 2 and 128")
    vertices, uv, faces = [], [], []
    # Four separate textured panels with slightly different geometric orientations.
    for row in range(2):
        for col in range(2):
            index = len(vertices)
            for u, v in ((col/2, row/2), ((col+1)/2, row/2), ((col+1)/2, (row+1)/2), (col/2, (row+1)/2)):
                x, y = (u-.5)*700, (v-.5)*500
                z = (col*2-1)*x*.10 + (row*2-1)*y*.06
                vertices.append([x, y, z]); uv.append([u, v])
            faces.extend([[index, index+2, index+1], [index, index+3, index+2]])
    vertices, uv, faces = np.array(vertices), np.array(uv), np.array(faces, np.int32)
    camera = np.array([0., 0., -1800.])
    K = np.array([[240., 0, 319.5], [0, 240., 239.5], [0, 0, 1.]])
    devices = {}
    for name, kind, center in (("camera", "camera", camera), ("projector", "projector", camera+[150, 0, 0])):
        transform = np.eye(4); transform[:3, 3] = -center
        devices[name] = dict(name=name, kind=kind, size=[640, 480], K=K.tolist(), dist=[0.]*5,
            R=np.eye(3).tolist(), t=(-center).tolist(), T_device_from_world=transform.tolist(), pose_estimated=True,
            model=dict(free=["synthetic_pose"], fixed=[]), uncertainty=dict(synthetic=True), residuals_by_view=[])
    rig = {**c.header("rig", "synthetic", "mm", {}), "format": "slscan-rig/2", "world": "p7-synthetic-world",
           "pixel_convention": c.PIXELS, "devices": devices, "validated_range": None, "metric_status": "unverified"}
    c.atomic_json(folder / "rig.json", rig)
    meta = {**c.header("mesh_arrays", "synthetic", "mm", {}), "world": rig["world"], "vertex_count": len(vertices), "face_count": len(faces)}
    c.write_npz(folder / "mesh.npz", meta, dict(vertices_mm=vertices, faces=faces, uv=uv,
                sample_index=np.arange(len(vertices)), measured=np.ones(len(vertices), bool)))
    # Tiny inspectable PLY, no captured data or external meshes.
    lines = ["ply", "format ascii 1.0", f"element vertex {len(vertices)}", "property float x", "property float y", "property float z",
             f"element face {len(faces)}", "property list uchar int vertex_indices", "end_header"]
    lines += [" ".join(map(str, p)) for p in vertices]
    lines += ["3 " + " ".join(map(str, f)) for f in faces]
    (folder / "mesh.ply").write_text("\n".join(lines)+"\n")
    mesh = {**c.header("mesh", "synthetic", "mm", {}), "world": rig["world"],
            "rig_sha256": c.digest(folder / "rig.json"), "cloud_sha256": c.canonical_hash(vertices.tolist()),
            "vertex_count": len(vertices), "face_count": len(faces), "uv_convention": "top_left_0_1",
            "ply": asset(folder / "mesh.ply", folder), "arrays": asset(folder / "mesh.npz", folder), "texture": None, "texture_kind": "none"}
    c.atomic_json(folder / "mesh.json", mesh)
    xyz, ng, t, b, owner = uv_geometry(vertices, faces, uv, shape)
    xyz, ng, t, b = [a.reshape(-1, 3) for a in (xyz, ng, t, b)]
    yy, xx = np.mgrid[:height, :width]
    object_ids = ((yy >= height/2)*2 + (xx >= width/2)).ravel().astype(int)
    palette = np.array([[.42, .19, .07], [.65, .57, .45], [.12, .25, .42], [.55, .08, .04]])
    albedo = palette[object_ids] * (1 + .05*np.sin(xx.ravel()*2.1)[:, None])
    normals = unit(ng + t*(.035*np.sin(xx.ravel()*1.3))[:, None] + b*(.025*np.cos(yy.ravel()*1.6))[:, None])
    roughness = np.array([.48, .65, .35, .26])[object_ids]
    specular = np.array([.045, .035, .055, .045])[object_ids]
    positions = np.array([[-800, -250, -650], [700, -350, -800], [-550, 650, -650],
                          [600, 550, -750], [-100, -200, -1100], [180, 60, -1150],
                          [-250, 180, -1000], [400, -100, -950]], float)
    powers = np.array([[.7, .55, .36], [.45, .55, .7], [.65, .58, .46], [.55, .56, .6],
                       [.50, .40, .28], [.5, .52, .58], [.6, .51, .4], [.55, .50, .42]])
    signal = render(xyz, normals, albedo, roughness, specular, camera, positions, powers)
    ambient = .008 + .002*np.cos(xx.ravel())[None, :, None]
    on = np.clip(signal + ambient + rng.normal(0, .00015, signal.shape), 0, 1)
    off = np.broadcast_to(ambient, signal.shape).copy()
    # Explicit unusable examples: shadow, saturation, UV sample without visibility.
    valid = np.ones(on.shape[:-1], bool)
    valid[0, 0] = False; on[0, 0] = off[0, 0]
    on[1, -1] = 1.
    # Separate matte patches outside all evaluation panels, varied depth/orientation.
    reference_xyz = np.array([[-520, -400, -80], [480, -420, 160], [-550, 400, 100], [500, 400, -80],
                              [-450, 0, 130], [460, 0, -100], [0, -400, 200], [0, 410, -150]], float)
    reference_normal = unit(np.array([[.1, .1, -1], [-.15, .1, -1], [.1, -.15, -1], [-.1, -.1, -1],
                                      [.3, 0, -1], [-.3, 0, -1], [0, .3, -1], [0, -.3, -1]]))
    reference_reflectance = np.full((8, 3), .6)
    direction, energy = irradiance(reference_xyz, positions, powers)
    reference_signal = reference_reflectance * np.maximum(np.sum(reference_normal*direction, axis=-1), 0)[..., None] * energy / np.pi
    reference_signal += rng.normal(0, .00001, reference_signal.shape)
    # Localize from synthetic short-exposure emitter images, not ground-truth rays.
    rays, emitter_frames, source_types = [], [], []
    gy, gx = np.mgrid[:480, :640]
    for position in positions:
        pixel = cv2.projectPoints(position.reshape(1, 3), np.zeros(3), -camera, K, np.zeros(5))[0].ravel()
        emitter = .85*np.exp(-((gx-pixel[0])**2+(gy-pixel[1])**2)/(2*2.**2))
        image = np.repeat(emitter[..., None], 3, axis=2)
        candidates = emitter_regions(image, np.zeros_like(image), K, np.zeros(5), np.eye(3), -camera, threshold=.2)
        if len(candidates) != 1:
            raise ValueError("Synthetic emitter not detected")
        rays.append(candidates[0]["direction"])
        source_types.append(candidates[0]["inferred_type"])
        emitter_frames.append(image.astype(np.float32))
    patterns = plan()
    rgb = np.array([p.rgb for p in patterns])/255
    mixing = np.array([[.7, .025, .015], [.015, .6, .03], [.02, .025, .55]])
    ramp_patch = .01 + rgb**np.array([2.1, 2.25, 1.9]) @ mixing.T
    np.savez_compressed(folder / "frames.npz", on=on.reshape(8, *shape, 3),
        off_before=off.reshape(8, *shape, 3), off_after=off.reshape(8, *shape, 3), valid=valid.reshape(8, *shape),
        reference_xyz=reference_xyz, reference_normal=reference_normal, reference_reflectance=reference_reflectance,
        reference_signal=reference_signal, emitter_origin=np.tile(camera, (8, 1)), emitter_ray=np.array(rays),
        ramp_patch=ramp_patch, object_ids=object_ids.reshape(shape))
    np.savez_compressed(folder / "emitters.npz", frames=np.array(emitter_frames))
    np.savez_compressed(folder / "truth.npz", albedo=albedo, normal=normals, roughness=roughness,
                        specular=specular, positions=positions, powers=powers)
    states = [dict(id=f"lamp_{i+1}", role="held_out" if i == 7 else "fit", type=source_types[i],
                   exposure_ratios=[1., 1., 1.]) for i in range(8)]
    doc = dict(version=1, evidence="synthetic", encoding="linear_rgb", controls_validated=True,
        alignment_validated=True, reference_disjoint=True, reference_status="linear_exposure_normalized_stable_unsaturated",
        camera="camera", atlas_shape=list(shape), states=states, noise=.0003,
        color_calibrated=True, radiometric_range=1., mesh=asset(folder / "mesh.json", folder),
        rig=asset(folder / "rig.json", folder), frames=asset(folder / "frames.npz", folder))
    (folder / "session.json").write_text(json.dumps(doc, indent=2))
    return folder / "session.json"


def run(output, shape=(8, 12)):
    output = Path(output).resolve()
    session = create_bundle(output / "input", shape)
    summary = process(session, output / "scan")
    with np.load(output / "input" / "truth.npz", allow_pickle=False) as truth, np.load(output / "scan" / "materials" / "fit.npz", allow_pickle=False) as fit:
        observable = fit["confidence"] > 0
        normal_error = np.degrees(np.arccos(np.clip(np.sum(fit["normal"]*truth["normal"], axis=-1), -1, 1)))
        summary["truth_metrics"] = dict(
            normal_median_deg=float(np.median(normal_error[observable[:, 1]])) if observable[:, 1].any() else None,
            roughness_mae=float(np.mean(np.abs(fit["roughness"][observable[:, 2]]-truth["roughness"][observable[:, 2]]))) if observable[:, 2].any() else None,
            albedo_mae=float(np.mean(np.abs(fit["albedo"][observable[:, 0]]-truth["albedo"][observable[:, 0]]))) if observable[:, 0].any() else None)
        lights = c.read_json(output / "scan" / "lights.json")["lights"]
        errors = [np.linalg.norm(np.array(light["position_mm"])-truth["positions"][i])/np.linalg.norm(truth["positions"][i])
                  for i, light in enumerate(lights) if light["position_mm"] is not None]
        summary["truth_metrics"]["light_position_relative_max"] = float(max(errors)) if errors else None
    c.atomic_json(output / "summary.json", summary)
    return summary

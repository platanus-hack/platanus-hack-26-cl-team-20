"""P5 integration and geometry checks, entirely synthetic and without hardware."""
import json

import cv2
import numpy as np
import pytest

from slscan import contracts as c
from slscan.object_geometry import Options, geometric_labels, plane_fit, rasterize, regions, split_components
from slscan.object_semantics import describe, routes
from slscan.object_worker import LocalVLM, StubSegmenter, write_predictions
from slscan.objects import assign_masks, segment_mesh, segment_pipeline
from slscan.rig import Device


def grid(x0, x1, y0, y1, z, n=5):
    x, y = np.meshgrid(np.linspace(x0, x1, n), np.linspace(y0, y1, n))
    vertices = np.column_stack([x.ravel(), y.ravel(), np.full(x.size, z)])
    faces = []
    for i in range(n-1):
        for j in range(n-1):
            k = i*n+j
            faces += [[k, k+1, k+n], [k+1, k+n+1, k+n]]
    return vertices, np.asarray(faces, np.int32)


def device():
    return Device("cam", "camera", (96, 72), np.array([[80., 0, 48], [0, 80, 36], [0, 0, 1]]), np.zeros(5))


@pytest.fixture
def mesh_case(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    devices = {}
    for name, kind in (("cam", "camera"), ("proj", "projector")):
        d = device().to_json()
        d.update(name=name, kind=kind, T_device_from_world=np.eye(4).tolist(),
                 pose_estimated=False, model={"free": [], "fixed": ["fixture"]},
                 uncertainty={}, residuals_by_view=[])
        devices[name] = d
    rig = {**c.header("rig", "synthetic", "mm"), "format": "slscan-rig/2", "world": "fixture",
           "pixel_convention": c.PIXELS, "devices": devices, "validated_range": None, "metric_status": "unverified"}
    c.atomic_json(root / "rig.json", rig)
    v1, f1 = grid(-40, -5, -25, 25, 140)
    v2, f2 = grid(5, 40, -25, 25, 160)
    vertices, faces = np.concatenate([v1, v2]), np.concatenate([f1, f2 + len(v1)])
    xy = cv2.projectPoints(vertices, np.zeros(3), np.zeros(3), device().K, device().dist)[0].reshape(-1, 2)
    metadata = {**c.header("mesh_arrays", "synthetic", "mm"), "world": "fixture",
                "vertex_count": len(vertices), "face_count": len(faces)}
    c.write_npz(root / "mesh.npz", metadata, dict(vertices_mm=vertices, faces=faces,
        uv=(xy+.5)/[96, 72], sample_index=np.arange(len(vertices)), measured=np.ones(len(vertices), bool)))
    cv2.imwrite(str(root / "appearance.png"), np.full((72, 96, 3), 127, np.uint8))
    (root / "mesh.ply").write_text("ply fixture")
    asset = lambda name: dict(path=name, sha256=c.digest(root / name))
    mesh = {**c.header("mesh", "synthetic", "mm"), "world": "fixture", "cloud_sha256": "a"*64,
            "rig_sha256": c.digest(root / "rig.json"), "face_count": len(faces), "vertex_count": len(vertices),
            "uv_convention": "top_left_0_1", "ply": asset("mesh.ply"), "arrays": asset("mesh.npz"),
            "texture": asset("appearance.png"), "texture_kind": "appearance"}
    c.atomic_json(root / "mesh.json", mesh)
    return root, vertices, faces


def test_geometry_two_planes_and_rotation(mesh_case):
    _, vertices, faces = mesh_case
    rotation = cv2.Rodrigues(np.array([.8, -.6, .3]))[0]
    for v in (vertices, vertices @ rotation.T + [200, -500, 40]):
        labels, edges = geometric_labels(v, faces, Options())
        groups = split_components(labels, edges)
        assert sorted(map(len, groups)) == [32, 32]
        for group in groups:
            plane, residual = plane_fit(v[np.unique(faces[group])], 1.5)
            assert plane is not None and residual < 1e-9


def test_generic_unknowns_and_contract_masks(mesh_case, tmp_path):
    root, _, faces = mesh_case
    out = tmp_path / "out"
    data, paths = segment_mesh(root / "mesh.json", root / "rig.json", out)
    assert len(data["objects"]) == 2 and not data["physical_verified"]
    c.validate_files(out, paths)
    assigned = [f for obj in data["objects"] for f in obj["face_indices"]]
    assert sorted(assigned) == list(range(len(faces)))
    for obj in data["objects"]:
        assert obj["label"] is None and obj["semantic"]["status"] == "unknown"
        assert set(obj["capabilities"]) == {"mesh", "planar", "textured", "normals"}
        assert obj["plane"] is not None and obj["observation"] == "partial"
        assert obj["projector_regions_px"] and obj["geometry"]["visible_projector_pixels"] > 0


def test_stub_sam_semantics_routes_and_exact_iou(mesh_case, tmp_path):
    root, vertices, faces = mesh_case
    owner, _ = rasterize(vertices, faces, device())
    masks = [(owner >= 0) & (owner < 32), owner >= 32]
    segmenter = StubSegmenter([dict(mask=m, score=.97) for m in masks])
    answers = iter([dict(type="picture", material="paint", surface="smooth", confidence=.95),
                    dict(type="plant", material="organic", surface="foliage", confidence=.93)])
    pred = tmp_path / "pred"
    write_predictions(root / "appearance.png", pred, segmenter, camera="cam", evidence="synthetic",
                      describer=lambda _: next(answers))
    out = tmp_path / "objects"
    data, _ = segment_mesh(root / "mesh.json", root / "rig.json", out, predictions=pred / "predictions.json")
    assert [o["label"] for o in data["objects"]] == ["picture", "plant"]
    assert "flat_texture" in [r["effect"] for r in data["objects"][0]["effect_routes"]]
    assert "foliage" in [r["effect"] for r in data["objects"][1]["effect_routes"]]
    for item, expected in zip(data["objects"], masks):
        actual = cv2.imread(str(out / item["projector_mask"]["path"]), 0) > 0
        iou = np.count_nonzero(actual & expected) / np.count_nonzero(actual | expected)
        assert iou == 1.


def test_camera_visibility_prevents_labeling_hidden_surface():
    front, ff = grid(-20, 20, -20, 20, 100)
    back, bf = grid(-40, 40, -40, 40, 200)
    vertices, faces = np.concatenate([front, back]), np.concatenate([ff, bf+len(front)])
    owner, _ = rasterize(vertices, faces, device())
    assert set(np.unique(owner)) <= set(range(32)) | {-1}
    labels = assign_masks(owner, [dict(mask=owner >= 0, score=.9, id="front")], len(faces), .6)
    assert np.all(labels[:32] == 0) and np.all(labels[32:] == -1)


def test_distorted_visibility_matches_independent_ray_plane_intersections():
    d = device()
    d.dist = np.array([.2, -.03, .01, -.01, .003])
    vertices = np.array([[-50., -35, 120], [50., -30, 180], [0., 45, 150]])
    owner, _ = rasterize(vertices, np.array([[0, 1, 2]], np.int32), d)
    yy, xx = np.mgrid[:72, :96]
    xy = np.column_stack([xx.ravel(), yy.ravel()]).astype(float)
    uv = cv2.undistortPoints(xy.reshape(-1, 1, 2), d.K, d.dist).reshape(-1, 2)
    rays = np.column_stack([uv, np.ones(len(uv))])
    normal = np.cross(vertices[1]-vertices[0], vertices[2]-vertices[0])
    xyz = rays * ((normal @ vertices[0])/(rays @ normal))[:, None]
    basis = np.column_stack([vertices[1]-vertices[0], vertices[2]-vertices[0]])
    coeff = np.linalg.lstsq(basis, (xyz-vertices[0]).T, rcond=None)[0].T
    expected = (coeff[:, 0] >= -1e-10) & (coeff[:, 1] >= -1e-10) & (coeff.sum(axis=1) <= 1+1e-10)
    assert np.array_equal(owner.ravel() >= 0, expected)


def test_holes_and_islands_are_preserved():
    mask = np.zeros((40, 60), np.uint8)
    mask[2:30, 2:30] = 255
    mask[8:15, 8:15] = 0
    mask[20:35, 40:55] = 255
    result = regions(mask)
    assert len(result) == 2 and len(result[0]["holes"]) == 1


@pytest.mark.parametrize("kind,surface,effect", [("picture", "smooth", "flat_texture"),
    ("plant", "foliage", "foliage"), ("wall", "smooth", "canvas"), ("wall", "textured", "relief"),
    ("vase", "smooth", "ceramic_glaze"), ("book", "smooth", "page_texture"),
    ("cushion", "fabric", "fabric_wave"), ("sofa", "fabric", "fabric_wave")])
def test_routes_are_semantic_and_capability_gated(kind, surface, effect):
    sem = describe(dict(type=kind, surface=surface, confidence=.9))
    full = [r["effect"] for r in routes(sem, ["mesh", "normals", "planar", "textured"])]
    assert effect in full
    assert routes(sem, []) == []
    assert [r["effect"] for r in routes(sem, ["mesh"])] == ["surface_light"]
    sem["status"] = "unknown"
    assert [r["effect"] for r in routes(sem, ["mesh", "normals", "planar", "textured"])] == ["surface_light"]


def test_stale_rig_image_mask_and_stub_rejected(mesh_case, tmp_path):
    root, vertices, faces = mesh_case
    owner, _ = rasterize(vertices, faces, device())
    stub = StubSegmenter([dict(mask=owner >= 0, score=.9)])
    with pytest.raises(ValueError, match="synthetic"):
        write_predictions(root / "appearance.png", tmp_path / "bad", stub, camera="cam")
    pred = tmp_path / "pred"
    write_predictions(root / "appearance.png", pred, stub, camera="cam", evidence="synthetic")
    path = pred / "predictions.json"
    data = c.read_json(path)
    data["image_sha256"] = "0"*64
    c.atomic_json(path, data)
    with pytest.raises(ValueError, match="image/camera"):
        segment_mesh(root / "mesh.json", root / "rig.json", tmp_path / "o", predictions=path)
    data["image_sha256"] = c.digest(root / "appearance.png")
    c.atomic_json(path, data)
    (pred / "mask-0000.png").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="stale asset"):
        segment_mesh(root / "mesh.json", root / "rig.json", tmp_path / "o", predictions=path)
    rig = c.read_json(root / "rig.json")
    rig["devices"]["proj"]["K"][0][0] += 1
    c.atomic_json(root / "rig.json", rig)
    with pytest.raises(ValueError, match="provenance"):
        segment_mesh(root / "mesh.json", root / "rig.json", tmp_path / "o")


def test_unknown_on_malformed_vlm_and_no_download(mesh_case, tmp_path):
    root, _, _ = mesh_case
    stub = StubSegmenter([dict(mask=np.ones((72, 96), bool), score=.9)])
    data = write_predictions(root / "appearance.png", tmp_path / "pred", stub, camera="cam",
        evidence="synthetic", describer=lambda _: {"type": "execute_shell", "confidence": .9})
    assert data["semantic_errors"] and data["instances"][0]["semantic"]["type"] == "unknown"
    with pytest.raises(ValueError, match="local"):
        LocalVLM("not-a-local-model")


def test_scene_loading_no_legacy_ghosts_or_nudges(mesh_case, tmp_path):
    from playground.scene import scene_from_scan, Scene
    from playground.runtime import Runtime
    root, _, _ = mesh_case
    out = tmp_path / "objects"
    data, _ = segment_mesh(root / "mesh.json", root / "rig.json", out)
    (out / "segments.json").write_text(json.dumps({"buda": {"bbox": [1, 1, 10, 10]}}))
    (out / "ajustes.json").write_text(json.dumps({data["objects"][0]["id"]: [20, 30]}))
    scene = scene_from_scan(out)
    assert set(scene.objects) == {o["id"] for o in data["objects"]}
    assert scene.coordinate_space == "world_mm" and not scene.projector_px
    first = next(iter(scene.objects.values()))
    assert first.contour == [] and first.bbox == [-40., -25., 140., -5., 25., 140.]
    (out / "salida.json").write_text('{"dx": 20, "dy": 30}')
    runtime = Runtime(scene, demo=False, requests_dir=tmp_path / "requests")
    assert set(runtime.scene.objects) == {o["id"] for o in data["objects"]}
    assert runtime.output_offset == (0, 0)
    with pytest.raises(ValueError, match="renderer"):
        runtime.spawn("contour_glow", first.id)
    with pytest.raises(ValueError, match="offsets"):
        runtime.command("output_offset", {"dx": 5})
    legacy = Runtime(demo=True, requests_dir=tmp_path / "requests-legacy")
    legacy.spawn("contour_glow", "croc")
    legacy.command("reload_scene", {"scan": str(out)})
    assert not legacy.instances and "croc" not in legacy.scene.objects
    with pytest.raises(ValueError, match="calibración"):
        scene.nudge(first.id, 10, 10)
    scene.save(tmp_path / "scene.json")
    assert Scene.load(tmp_path / "scene.json").coordinate_space == "world_mm"
    data["objects"] = []
    c.atomic_json(out / "objects.json", data)
    assert scene_from_scan(out).objects == {}


def test_pipeline_adapter_runs_and_cache_invalidation(tmp_path):
    from slscan.pipeline import Runner
    from slscan.pipeline_adapters import synthetic_adapters
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir()
    adapters = synthetic_adapters()
    adapters["segment"] = segment_pipeline
    first = Runner(source, out, synthetic=True, adapters=adapters).run()
    assert first["stages"]["segment"]["state"] == "unverified", first["stages"]["segment"]["reason"]
    assert first["stages"]["segment"]["counts"]["objects"] == 1
    second = Runner(source, out, synthetic=True, adapters=adapters).run()
    assert second["stages"]["segment"]["cached"]
    changed = Runner(source, out, synthetic=True, adapters=adapters, options={"objects": {"noise_mm": .8}}).run()
    assert not changed["stages"]["segment"]["cached"]


def test_output_cannot_overwrite_inputs(mesh_case):
    root, _, _ = mesh_case
    before = c.digest(root / "mesh.json")
    with pytest.raises(ValueError, match="outside"):
        segment_mesh(root / "mesh.json", root / "rig.json", root / "new")
    assert c.digest(root / "mesh.json") == before


def test_duplicate_faces_rejected():
    data = {**c.header("objects", "synthetic", "mm"), "mesh_sha256": "a"*64, "face_count": 2,
            "objects": [dict(id="x", label=None, face_indices=[0, 0], bbox_mm=[[0]*3, [1]*3],
                             projector_contour_px=[], plane=None, confidence=0, observation="partial")]}
    with pytest.raises(c.ContractError, match="duplicate face"):
        c.validate(data)


def test_overlapping_masks_choose_small_object_first():
    owner = np.array([[0, 0, 1, 1], [0, 0, 1, 1], [2, 2, 3, 3]])
    broad = dict(id="parent", score=.99, mask=np.ones_like(owner, bool))
    small = dict(id="child", score=.9, mask=owner == 0)
    assert assign_masks(owner, [broad, small], 4, .6).tolist() == [1, 0, 0, 0]


def test_empty_mesh_and_degenerate_plane():
    labels, edges = geometric_labels(np.empty((0, 3)), np.empty((0, 3), np.int32), Options())
    assert split_components(labels, edges) == []
    assert plane_fit(np.array([[0., 0, 0], [1., 0, 0], [2., 0, 0]]), 1)[0] is None
    owner, _ = rasterize(np.empty((0, 3)), np.empty((0, 3), np.int32), device())
    assert np.all(owner == -1)


def test_local_color_edges_split_without_hue_or_position_rules():
    vertices, faces = grid(-20, 20, -20, 20, 100)
    colors = np.zeros((len(faces), 3))
    colors[len(faces)//2:] = [100, 100, 100]
    labels, edges = geometric_labels(vertices, faces, Options(), colors)
    assert sorted(map(len, split_components(labels, edges))) == [16, 16]

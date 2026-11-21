"""P7 numerical, observability, provenance and offline integration regressions."""
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from slscan import contracts as c
from slscan.pbr import process, uv_geometry, sample_camera_to_uv, pipeline_adapter
from slscan.pbr_model import emitter_regions, fit_texels, irradiance, locate_light, render, temperature, unit
from slscan.pbr_protocol import capture_plan, StabilityGate, isolate, normalize_reference
from slscan.pbr_radiometry import fit_response, predict_response, compensate
from slscan.pbr_synthetic import create_bundle, run
from slscan.radiometry import plan


def test_capture_guidance_and_change_stability():
    guide = capture_plan(["floor", "desk", "ceiling"], "desk")
    assert [s["lamp"] for s in guide["lamps"] if s["role"] == "held_out"] == ["desk"]
    assert guide["projector_rgb_during_lamps"] == [0, 0, 0]
    base = np.full((4, 5, 3), .1)
    gate = StabilityGate(base)
    for i in range(5):
        assert not gate.feed(base, i)["capture"]
    assert not gate.feed(base+.2, 5, guides_off=False)["capture"]
    for i in range(3):
        assert not gate.feed(base+.2, 6+i)["capture"]
    assert gate.feed(base+.2, 9)["capture"]
    flicker = base+.2; flicker[0, 0] += .03
    assert not gate.feed(flicker, 10)["capture"]
    assert not gate.feed(base+.2, 11, controls_valid=False)["capture"]
    with pytest.raises(ValueError, match="timestamps"):
        gate.feed(base, 10)


def test_initial_base_and_saturation_do_not_capture_bad_frames():
    base = np.full((2, 2, 3), .1)
    gate = StabilityGate(base, require_change=False)
    for i in range(4):
        result = gate.feed(base, i)
    assert result["capture"]
    for i in range(4, 10):
        assert not gate.feed(np.ones_like(base), i)["capture"]


def test_ambient_bracket_and_reference_gain():
    base = np.full((3, 4, 3), .04)
    signal, valid, drift = isolate((base+.2)*2, base, base)
    assert valid.all()
    signal, valid, drift = isolate((base+.2)*2, base, base, exposure=(2, 1, 1))
    assert np.allclose(signal, .2)
    assert not isolate(base+.2, base, base+.02)[1].any()
    assert not isolate(np.ones_like(base), base, base)[1].any()
    expected = np.array([[.1, .2, .15], [.3, .4, .35]])
    normalized, gain = normalize_reference(base*2, expected*2, expected)
    assert np.allclose(normalized, base) and np.allclose(gain, 2)
    with pytest.raises(ValueError, match="linear exposure"):
        normalize_reference(base, expected**.5, expected)


def test_emitter_rays_types_and_cct():
    on, off = np.zeros((80, 100, 3)), np.zeros((80, 100, 3))
    on[38:42, 48:52] = .8
    K = np.array([[100., 0, 49.5], [0, 100., 39.5], [0, 0, 1]])
    found = emitter_regions(on, off, K, np.zeros(5), np.eye(3), np.zeros(3))
    assert len(found) == 1 and np.allclose(found[0]["direction"], [0, 0, 1])
    assert found[0]["inferred_type"] == "point" and not found[0]["saturated"]
    on[20:60, 30:70] = .8
    assert emitter_regions(on, off, K, np.zeros(5), np.eye(3), np.zeros(3))[0]["inferred_type"] == "area"
    assert temperature([1, 1, 1]) is None
    assert abs(temperature([1, 1, 1], True)-6500) < 100
    assert temperature([1, .01, .01], True) is None


def test_light_depth_ambiguity_and_area_not_claimed_as_points():
    # Repeated identical reference point cannot identify depth vs power.
    xyz = np.tile([0., 0., 0.], (8, 1)); ng = np.tile([0., 0., -1.], (8, 1))
    rho = np.full((8, 3), .6); y = np.full((8, 3), .1)
    for kind in ("point", "area", "group"):
        light = locate_light("lamp", [0, 0, -2000], [0, 0, 1], xyz, ng, rho, y, source_type=kind)
        assert light["position_mm"] is None and light["observation"] == "direction_only"
        assert not light["uncertainty"]["power_observed"]


def test_rgb_transfer_holdout_and_bounded_compensation():
    patterns = plan(); rgb = np.array([p.rgb for p in patterns])/255
    matrix = np.array([[.7, .03, .01], [.01, .6, .04], [.02, .02, .5]])
    measured = .02 + rgb**[2.1, 1.9, 2.3] @ matrix.T
    model = fit_response(patterns, measured)
    assert model["accepted"] and np.max(model["held_out_mae_rgb"]) < .001
    target = [.2, .25, .15]
    adjusted = compensate(model, target)
    before = np.linalg.norm(predict_response(model, target)-target)
    assert adjusted["in_gamut"] and np.linalg.norm(adjusted["residual_rgb"]) < .7*before
    assert not compensate(model, [2, 2, 2])["in_gamut"]
    assert not compensate(model, [0, 0, 0])["in_gamut"]  # Cannot subtract ambient.
    poisoned = measured.copy()
    poisoned[[i for i, p in enumerate(patterns) if p.role == "held_out"]] += .1
    bad = fit_response(patterns, poisoned)
    assert np.allclose(bad["gamma"], model["gamma"]) and not bad["accepted"]
    with pytest.raises(ValueError, match="ambient drift"):
        altered = measured.copy(); altered[-1] += .04
        fit_response(patterns, altered)


def test_uv_opengl_orientation_holes_and_overlaps():
    vertices = np.array([[-1., -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]])
    faces = np.array([[0, 2, 1], [0, 3, 2]])
    uv = np.array([[0., 0], [1, 0], [1, 1], [0, 1]])
    xyz, n, t, b, owner = uv_geometry(vertices, faces, uv, (4, 4))
    assert (owner >= 0).all() and np.allclose(n, [0, 0, -1])
    assert np.allclose(t, [1, 0, 0]) and np.allclose(b, [0, -1, 0])
    assert xyz[0, 0, 0] < 0 and xyz[0, 0, 1] < 0
    assert (uv_geometry(vertices, faces[:1], uv, (4, 4))[-1] < 0).any()
    with pytest.raises(ValueError, match="Overlapping"):
        uv_geometry(np.vstack([vertices, vertices+[0, 0, 1]]), np.vstack([faces, faces+4]), np.vstack([uv, uv]), (4, 4))


def test_camera_to_uv_sampling_matches_calibration():
    from slscan.rig import Device
    vertices = np.array([[-1., -1, 2], [1, -1, 2], [1, 1, 2], [-1, 1, 2]])
    faces = np.array([[0, 2, 1], [0, 3, 2]])
    uv = np.array([[0., 0], [1, 0], [1, 1], [0, 1]])
    camera = Device("cam", "camera", (21, 21), np.array([[10., 0, 10], [0, 10, 10], [0, 0, 1]]), np.zeros(5))
    image = np.full((21, 21, 3), [.2, .4, .6])
    samples, valid = sample_camera_to_uv(image, camera, vertices, faces, uv, (4, 4))
    assert valid.mean() > .5 and np.allclose(samples[valid], [.2, .4, .6])


def test_parallel_lights_and_absent_specular_are_not_full_materials():
    xyz = np.array([[0., 0, 0]]); ng = np.array([[0., 0, -1.]])
    t, b = np.array([[1., 0, 0]]), np.array([[0., -1, 0]])
    positions = np.tile([0., 0, -1000], (6, 1)); powers = np.full((6, 3), .5)
    images = render(xyz, ng, np.array([[.4, .3, .2]]), [.5], [.04], [0, 0, -2000], positions, powers)
    fit = fit_texels(xyz, ng, t, b, [0, 0, -2000], positions, powers, images, np.ones((6, 1), bool))
    assert not fit["confidence"].any()


def test_opaque_brdf_has_no_back_face_radiance():
    xyz = np.array([[0., 0, 0]]); normal = np.array([[0., 0, -1]])
    value = render(xyz, normal, np.array([[.5, .5, .5]]), [.5], [.04],
                   [0, 0, 1000], [[0, 0, -1000]], [[1, 1, 1]])
    assert np.array_equal(value, np.zeros((1, 1, 3)))


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("p7")
    summary = run(root, (4, 6))
    return root, summary


def test_full_synthetic_recovery_contracts_and_independent_lamp(demo):
    root, summary = demo
    assert summary["lights_estimated"] == 8 and not summary["physical_verified"]
    assert summary["truth_metrics"]["normal_median_deg"] < 5
    assert summary["truth_metrics"]["roughness_mae"] < .1
    assert summary["truth_metrics"]["light_position_relative_max"] < .02
    assert summary["photometry"]["mae_luminance"] < .05
    assert summary["photometry"]["coverage"] > .25
    assert summary["coverage"]["absorption"] == 0
    paths = [str(p.relative_to(root / "scan")) for p in (root / "scan").rglob("*") if p.is_file()]
    c.validate_files(root / "scan", paths)
    mat = c.read_json(root / "scan" / "materials.json")
    assert mat["materials"][0]["observation"] == "partial" and not mat["absorption"]["apply"]
    assert mat["materials"][0]["maps"]["albedo"]["color_space"] == "sRGB"
    encoded = cv2.imread(str(root / "scan" / "materials" / "albedo.png"), cv2.IMREAD_UNCHANGED)[..., ::-1]/65535
    with np.load(root / "scan" / "materials" / "fit.npz") as fit:
        decoded = np.where(encoded <= .04045, encoded/12.92, ((encoded+.055)/1.055)**2.4)
        assert np.max(np.abs(decoded.reshape(-1, 3)-fit["albedo"])) < .00003


def test_holdout_material_photos_cannot_leak_into_fit(tmp_path, demo):
    root, _ = demo
    session = create_bundle(tmp_path / "input", (4, 6))
    frames_path = session.parent / "frames.npz"
    with np.load(frames_path) as data:
        arrays = {k: data[k] for k in data.files}
    arrays["on"][-1] += .12
    np.savez_compressed(frames_path, **arrays)
    doc = json.loads(session.read_text()); doc["frames"]["sha256"] = c.digest(frames_path)
    session.write_text(json.dumps(doc))
    summary = process(session, tmp_path / "out")
    with np.load(root / "scan" / "materials" / "fit.npz") as a, np.load(tmp_path / "out" / "materials" / "fit.npz") as b:
        for key in ("albedo", "normal", "roughness", "specular", "confidence"):
            assert np.array_equal(a[key], b[key])
    assert summary["photometry"]["mae_luminance"] > .1


@pytest.mark.parametrize("field,value,match", [
    ("encoding", "srgb", "linearization"), ("reference_disjoint", False, "Disjoint"),
    ("alignment_validated", False, "alignment"), ("controls_validated", False, "validated"),
])
def test_fail_closed_inputs(tmp_path, field, value, match):
    session = create_bundle(tmp_path / "input", (2, 2))
    doc = json.loads(session.read_text()); doc[field] = value; session.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match=match):
        process(session, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_output_and_assets_are_safe(tmp_path):
    session = create_bundle(tmp_path / "input", (2, 2))
    with pytest.raises(ValueError, match="outside"):
        process(session, session.parent / "out")
    doc = json.loads(session.read_text()); doc["frames"]["path"] = "../elsewhere.npz"
    session.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="asset"):
        process(session, tmp_path / "out")


def test_p7_pipeline_hashes_capture_bundle_and_rejects_stale_mesh(tmp_path):
    from slscan.pipeline import Runner, Blocked
    source = tmp_path / "source"; source.mkdir()
    session = create_bundle(source / "pbr", (2, 2))
    runner = Runner(source, tmp_path / "output")
    first = runner._source_hashes("radiometry_materials")
    session.write_text(session.read_text()+"\n")
    second = runner._source_hashes("radiometry_materials")
    assert first["pbr/session.json"] != second["pbr/session.json"]
    stale = tmp_path / "mesh.json"; stale.write_text("stale")
    ctx = SimpleNamespace(source=source, artifact=lambda *_: stale, evidence="recorded")
    with pytest.raises(Blocked, match="obsoleto"):
        pipeline_adapter(ctx)


def test_pipeline_adapter_publishes_complete_partial_products(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    session = create_bundle(source / "pbr", (2, 2))
    work = tmp_path / "work"; work.mkdir()
    progress = []
    ctx = SimpleNamespace(source=source, artifact=lambda *_: session.parent / "mesh.json", evidence="synthetic",
                          work=work, inputs={}, progress=lambda *args: progress.append(args))
    result = pipeline_adapter(ctx)
    assert result.state == "unverified" and result.counts["lights_estimated"] == 8
    assert "materials/albedo.png" in result.paths and progress[-1][0] == 1
    c.validate_files(work, result.paths)


def test_extended_light_and_few_lamps_keep_materials_partial(tmp_path):
    session = create_bundle(tmp_path / "input", (2, 2))
    doc = json.loads(session.read_text())
    # A household with only two independent point directions and extended lights.
    for state in doc["states"][2:7]:
        state["type"] = "area"
    session.write_text(json.dumps(doc))
    summary = process(session, tmp_path / "output")
    assert summary["lights_estimated"] == 3
    assert summary["coverage"]["normal"] == 0 and summary["coverage"]["roughness"] == 0
    assert summary["photometry"]["mae_luminance"] is None
    lights = c.read_json(tmp_path / "output" / "lights.json")["lights"]
    assert lights[2]["type"] == "area" and lights[2]["position_mm"] is None

"""P3: geometría, procedencia, UV y CLI offline, sin dispositivos físicos."""
from dataclasses import replace
import json

import cv2
import numpy as np
import pytest

from slscan import graycode as gc, metric_recon as mr
from slscan.rig import Device, Rig


def rig_pair():
    K = np.array([[120., 0, 32], [0, 120., 24], [0, 0, 1]])
    cam = Device("cam", "camera", (64, 48), K, np.zeros(5))
    pro = Device("proj", "projector", (64, 48), K.copy(), np.zeros(5), t=np.array([-100., 0, 0]))
    return cam, pro


def project(dev, xyz):
    return cv2.projectPoints(np.asarray(xyz, float), cv2.Rodrigues(dev.R)[0], dev.t, dev.K, dev.dist)[0].reshape(-1, 2)


def matches_for(xyz, cam, pro, cells=None, sigma=.01):
    n = len(xyz)
    if cells is None:
        cells = np.column_stack((np.arange(n), np.zeros(n, int)))
    return mr.MeasuredMatches(project(cam, xyz), project(pro, xyz), np.tile([17, 83, 201], (n, 1)),
        np.asarray(cells, np.int32), np.arange(n), np.ones(n, np.int32), np.zeros(n), np.ones(n),
        np.full((n, 4), sigma), {k: np.zeros((cam.size[1], cam.size[0]), bool)
            for k in ("measured", "interpolated", "occluded", "invalid", "rejected", "unobserved")},
        np.full((cam.size[1], cam.size[0]), -1, np.int32), pro.size, cam.size, 1)


def identity(size=(16, 12), skip=0):
    items = [(s, gc.pattern(size, s.axis, s.bit, s.inverse)) for s in gc.sequence(size)]
    return gc.decode(items, skip_bits=skip, projector_size=size, retain_contrast=True)


def test_pixel_centers_block_edges_and_roundtrip(tmp_path):
    dec = identity((15, 11), skip=2)
    m = mr.measured_matches(dec, (15, 11))
    assert np.allclose(m.cam, m.pro)  # sin sesgo 0.5 px entre ambos dispositivos
    assert np.allclose(m.pro[0], [1.5, 1.5])
    assert np.allclose(m.pro[-1], [13, 9])  # bloque parcial al borde
    assert np.allclose(m.sigma_px[0, 2:], 4 / np.sqrt(12))
    assert np.allclose(m.sigma_px[-1, 2:], 3 / np.sqrt(12))
    dec.save(tmp_path / "map.npz")
    back = gc.Decoded.load(tmp_path / "map.npz")
    assert back.stats == json.loads(json.dumps(dec.stats))
    assert np.array_equal(back.bit_contrast, dec.bit_contrast)
    full = identity()
    f = mr.measured_matches(full, (16, 12))
    assert np.array_equal(f.cam, f.pro)
    assert np.array_equal(f.pro[0], [0, 0])


def test_gray_limits_missing_bits_and_legacy():
    items = [(s, gc.pattern((16, 16), s.axis, s.bit, s.inverse)) for s in gc.sequence((16, 16))]
    bounded = gc.decode(items, projector_size=(13, 11), retain_contrast=True)
    assert bounded.valid.sum() == 13 * 11
    assert bounded.stats["out_of_bounds"] == 256 - 143
    with pytest.raises(ValueError, match="faltan bits"):
        gc.decode([p for p in items if p[0].bit != 1], projector_size=(13, 11))
    with pytest.raises(ValueError, match="duplicado"):
        gc.decode(items + items[:1])
    with pytest.raises(ValueError, match="legacy"):
        mr.measured_matches(replace(bounded, stats={}), (13, 11))


def test_grouping_rejects_disconnected_and_spread_keeps_holes():
    dec = identity((16, 12))
    # Dos islas con un mismo código, aunque caigan dentro del umbral de radio.
    dec.px[2, 2] = dec.px[2, 5] = 2
    dec.py[2, 2] = dec.py[2, 5] = 2
    dec.px[4, 4] = gc.INVALID
    m = mr.measured_matches(dec, (16, 12))
    assert m.masks["rejected"][2, 2] and m.masks["rejected"][2, 5]
    assert m.masks["invalid"][4, 4]
    assert not m.masks["interpolated"].any() and not m.masks["occluded"].any()
    assert m.source_match[4, 4] == -1
    assert not ((m.cells == [2, 2]).all(1)).any()
    assert np.array_equal(m.masks["measured"], m.source_match >= 0)
    broad = mr.measured_matches(identity((16, 12), skip=2), (16, 12), max_spread_px=1)
    assert len(broad.cam) == 0 and broad.masks["rejected"].all()


def test_distortion_world_frame_and_noiseless_accuracy():
    cam, pro = rig_pair()
    cam.dist = np.array([-.1, .03, .002, -.001, 0])
    pro.dist = np.array([-.07, .01, -.001, .002, 0])
    R = cv2.Rodrigues(np.array([.2, -.1, .04]))[0]
    offset = np.array([100., -30., -2000.])
    # X_new=R X_old+offset; mundo con z negativo, dispositivos aún ven z positivo.
    cam.R = R.T; cam.t = -R.T @ offset
    pro.R = R.T; pro.t = np.array([-100., 0, 0]) - R.T @ offset
    rng = np.random.default_rng(7)
    xyz = np.column_stack((rng.uniform(-30, 80, 100), rng.uniform(-40, 40, 100), rng.uniform(600, 800, 100)))
    xyz = xyz @ R.T + offset
    m = matches_for(xyz, cam, pro)
    q = mr.triangulate_measured(m, cam, pro, chunk_size=23)
    assert q.accepted.all()
    assert np.linalg.norm(q.xyz - xyz, axis=1).max() < 1e-5
    assert q.reprojection_px.max() < 1e-7
    assert q.depth_mm.min() > 0 and (q.xyz[:, 2] < 0).all()


def test_reject_reprojection_confidence_bounds_and_uncertainty():
    cam, pro = rig_pair()
    xyz = np.tile([50., 0., 600.], (5, 1))
    m = matches_for(xyz, cam, pro)
    m.pro[1, 1] += 8
    m.cam[2, 0] = -2
    m.confidence[3] = np.nan
    m.sigma_px[4] = 4 / np.sqrt(12)
    q = mr.triangulate_measured(m, cam, pro)
    assert q.accepted.tolist() == [True, False, False, False, False]
    assert q.rejection[1] & mr.Reject.REPROJECTION
    assert q.rejection[1] & mr.Reject.RAY_GAP
    assert q.rejection[2] & mr.Reject.OUT_OF_BOUNDS
    assert q.rejection[3] & mr.Reject.CONFIDENCE
    assert q.rejection[4] & mr.Reject.UNCERTAINTY


def test_cheirality_in_projector_and_degenerate_baseline():
    cam, pro = rig_pair()
    pro.R = cv2.Rodrigues(np.array([0., np.pi, 0.]))[0]
    pro.t = -pro.R @ np.array([100., 0, 0])
    m = matches_for([[50., 0, 600]], cam, pro)
    q = mr.triangulate_measured(m, cam, pro)
    assert q.depth_mm[0, 0] > 0 and q.depth_mm[0, 1] < 0
    assert q.rejection[0] & mr.Reject.CHEIRALITY
    with pytest.raises(ValueError, match="baseline"):
        mr.triangulate_measured(m, cam, replace(cam, kind="projector", name="proj"))


def test_parallel_rays_empty_and_nonfinite():
    cam, pro = rig_pair()
    m = matches_for([[50., 0, 600], [50., 0, 600]], cam, pro)
    m.pro[0] = m.cam[0]
    m.pro[1, 0] = np.nan
    q = mr.triangulate_measured(m, cam, pro)
    assert not q.accepted.any()
    assert q.rejection[0] & mr.Reject.RAY_ANGLE
    assert q.rejection[1] & mr.Reject.NONFINITE
    dec = identity((64, 48)); dec.px[:] = gc.INVALID
    empty = mr.measured_matches(dec, pro.size)
    q = mr.triangulate_measured(empty, cam, pro)
    mesh = mr.grid_mesh(empty, q, cam)
    assert mesh.faces.shape == (0, 3) and mesh.vertices.shape == (0, 3)


def grid_fixture(depth_jump=False):
    cam, pro = rig_pair()
    yy, xx = np.mgrid[20:27, 20:29]
    pixels = np.column_stack((xx.ravel(), yy.ravel()))
    z = np.where(pixels[:, 0] >= 24, 800., 600.) if depth_jump else np.full(len(pixels), 600.)
    xyz = np.column_stack(((pixels[:, 0] - 32) / 120 * z + 100, (pixels[:, 1] - 24) / 120 * z, z))
    m = matches_for(xyz, cam, pro, pixels)
    q = mr.triangulate_measured(m, cam, pro)
    assert q.accepted.all()
    return m, q, cam


def test_mesh_holes_discontinuities_uv_and_winding():
    m, q, cam = grid_fixture(True)
    missing = np.flatnonzero((m.cells == [22, 23]).all(1))[0]
    q.accepted[missing] = False
    mesh = mr.grid_mesh(m, q, cam)
    tri = mesh.vertices[mesh.faces]
    assert len(tri) > 0
    assert np.ptp(tri[:, :, 2], axis=1).max() < 1e-5
    cell_tri = m.cells[mesh.sample_index[mesh.faces]]
    # Ni un triángulo toca quads con la muestra faltante como esquina.
    assert not np.any(np.all((cell_tri >= [21, 22]) & (cell_tri <= [22, 23]), axis=(1, 2)))
    assert missing not in mesh.sample_index
    assert np.allclose(mesh.uv[:, 0] * 64 - .5, m.cam[mesh.sample_index, 0])
    assert np.allclose(mesh.uv[:, 1] * 48 - .5, m.cam[mesh.sample_index, 1])
    assert (mesh.normals[np.linalg.norm(mesh.normals, axis=1) > 0, 2] < 0).all()
    assert mesh.grid_sample[23, 22] == -1


def test_ply_independent_consumer_and_contract(tmp_path):
    from slscan.metric import read_mesh_ply  # otro módulo, no writer P3
    from slscan.recon import read_ply
    m, q, cam = grid_fixture()
    mesh = mr.grid_mesh(m, q, cam)
    result = mr.export_reconstruction(tmp_path, m, q, mesh, evidence="synthetic", world="cam", rig_sha256="a" * 64)
    cloud, rgb = read_ply(tmp_path / "cloud.ply")
    loaded = read_mesh_ply(tmp_path / "mesh.ply")
    assert np.allclose(cloud, q.xyz, atol=1e-4)
    assert np.array_equal(rgb, m.color[:, ::-1])
    assert np.allclose(loaded.vertices, mesh.vertices, atol=1e-4)
    assert np.array_equal(loaded.faces, mesh.faces)
    assert np.array_equal(loaded.colors, mesh.color)
    for name in ("mesh", "matches", "cloud_quality"):
        with np.load(tmp_path / f"{name}.npz", allow_pickle=False) as data:
            meta = json.loads(data["metadata"].item())
            assert meta["schema_version"] == "1.0" and meta["units"] == ("px" if name == "matches" else "mm")
            assert meta["evidence"] == "synthetic"
    assert result["metric_validation"] == "unverified" and result["triangles"] == len(mesh.faces)
    assert result["quality"]["reprojection_px"]["before"]["max"] < 1e-7


def test_normal_cut_edge_limit_and_texture_occlusion():
    m, q, cam = grid_fixture()
    q.xyz[:, 2] += np.maximum(m.cells[:, 0] - 24, 0) * 20
    permissive = mr.grid_mesh(m, q, cam, mr.MeshLimits(200, 200, 180))
    creases = mr.grid_mesh(m, q, cam, mr.MeshLimits(200, 200, 30))
    assert 0 < len(creases.faces) < len(permissive.faces)
    assert len(mr.grid_mesh(m, q, cam, mr.MeshLimits(max_edge_mm=1)).faces) == 0
    cam, pro = rig_pair()
    m = matches_for([[0, 0, 500], [0, 0, 700]], cam, pro, cells=[[8, 24], [15, 24]])
    q = mr.triangulate_measured(m, cam, pro)
    mesh = mr.grid_mesh(m, q, cam)
    assert mesh.texture_valid.tolist() == [True, False]


def test_empty_export_and_frame_mismatch(tmp_path):
    cam, pro = rig_pair()
    dec = identity((64, 48)); dec.px[:] = gc.INVALID
    m = mr.measured_matches(dec, pro.size)
    with pytest.raises(ValueError, match="tamaños"):
        mr.triangulate_measured(m, replace(cam, size=(128, 96)), pro)
    q = mr.triangulate_measured(m, cam, pro)
    mesh = mr.grid_mesh(m, q, cam)
    report = mr.export_reconstruction(tmp_path, m, q, mesh, evidence="synthetic", world="cam", rig_sha256="a" * 64)
    assert report["processing_state"] == "blocked" and report["status"] == "unverified"
    assert report["points"] == 0 and report["triangles"] == 0


def test_paper_maximum_missing_and_no_rescaling():
    corners = np.array([[0, 0, 600], [215.9, 0, 600], [215.9, 279.4, 600], [0, 279.4, 600]])
    points = np.tile(corners, (100, 1)).astype(float)
    result = mr.paper_metrics(points, corners, plane=([0, 0, 1], -600))
    assert result["status"] == "passed_local"
    points[0, 2] += 2.1
    result = mr.paper_metrics(points, corners, plane=([0, 0, 1], -600))
    assert result["status"] == "failed"  # aunque p95 y RMS pasen
    assert result["plane_distance_mm"]["p95"] == 0
    corners = corners.astype(float); corners[1] = np.nan
    assert mr.paper_metrics(points, corners)["status"] == "unverified"
    scaled = np.array([[0, 0, 600], [219, 0, 600], [219, 279.4, 600], [0, 279.4, 600]])
    assert mr.paper_metrics(scaled, scaled)["status"] == "failed"


def test_reserved_synthetic_letter_dimensions_after_triangulation():
    cam, pro = rig_pair()
    xx, yy = np.meshgrid(np.linspace(-107.95, 107.95, 9), np.linspace(-139.7, 139.7, 13))
    xyz = np.column_stack((xx.ravel() + 50, yy.ravel(), np.full(xx.size, 1000.)))
    m = matches_for(xyz, cam, pro)
    q = mr.triangulate_measured(m, cam, pro)
    assert q.accepted.all()
    corners = q.xyz[[0, 8, -1, -9]]
    report = mr.paper_metrics(q.xyz, corners, plane=([0, 0, 1], -1000))
    assert report["status"] == "passed_local"
    assert report["dimension_error_mm"]["max"] < 1e-6
    assert report["plane_distance_mm"]["max"] < 1e-6


def make_recorded_fixture(tmp_path):
    cam, pro = rig_pair()
    root = tmp_path / "input"; root.mkdir()
    for spec in gc.sequence(pro.size):
        pat = gc.pattern(pro.size, spec.axis, spec.bit, spec.inverse)
        # Disparidad 20 px => plano Z=600 mm.
        img = np.full_like(pat, 100); img[:, 20:] = (40 + .7 * pat[:, :-20]).astype(np.uint8)
        cv2.imwrite(str(root / f"{spec.tag}.png"), img)
    color = np.zeros((48, 64, 3), np.uint8); color[:] = [17, 83, 201]
    cv2.imwrite(str(root / "color.png"), color)
    rig = tmp_path / "rig.json"; Rig({"cam": cam, "proj": pro}, world="cam").save(rig)
    manifest = dict(schema_version="1.0", artifact="manifest", evidence="synthetic", units="mm",
        rig_sha256=mr.sha256(rig), session_id="test-static", rig_compatibility="verified_static_session",
        acquisition_known=True, code_revision="synthetic-fixture", world="cam",
        pixel_convention="opencv_integer_centers",
        devices={"cam": dict(size=[64, 48], rotation_deg=0, downscale=1, exposure={}, white_balance={}, focus={})},
        patterns=[dict(path=p.name, kind="gray", timestamp=None) for p in root.iterdir() if p.name != "color.png"],
        input_hashes={p.name: mr.sha256(p) for p in root.iterdir()})
    manifest_path = tmp_path / "manifest.json"; manifest_path.write_text(json.dumps(manifest))
    return root, rig, manifest_path


def test_cli_end_to_end_offline_and_rejects_stale_inputs(tmp_path, monkeypatch):
    from slscan.cli import main
    from slscan import capture
    def forbidden(*args, **kwargs):
        pytest.fail("P3 offline intentó abrir hardware")
    monkeypatch.setattr(capture, "Camera", forbidden)
    monkeypatch.setattr(capture, "PatternWindow", forbidden)
    root, rig, manifest = make_recorded_fixture(tmp_path)
    before = {p.name: mr.sha256(p) for p in root.iterdir()}
    out = tmp_path / "output"
    argv = ["reconstruct", str(root), "--rig", str(rig), "--manifest", str(manifest),
            "--camera", "cam", "--out", str(out), "--max-sigma-mm", "20"]
    assert main(argv) == 0
    report = json.loads((out / "reconstruction.json").read_text())
    assert report["points"] == 44 * 48 and report["triangles"] > 0
    assert before == {p.name: mr.sha256(p) for p in root.iterdir()}
    with pytest.raises(FileExistsError):
        main(argv)
    bad = json.loads(manifest.read_text()); bad["rig_sha256"] = "0" * 64      # formato válido (sha256), rig distinto
    manifest.write_text(json.dumps(bad))
    argv[argv.index("--out") + 1] = str(tmp_path / "new")
    with pytest.raises(ValueError, match="vincula"):
        main(argv)
    assert not (tmp_path / "new").exists()


def test_noise_sigma_is_not_accuracy_certificate():
    cam, pro = rig_pair()
    rng = np.random.default_rng(81)
    xyz = np.tile([50., 0, 600.], (1000, 1))
    m = matches_for(xyz, cam, pro, sigma=.1)
    m.cam += rng.normal(0, .1, m.cam.shape)
    m.pro += rng.normal(0, .1, m.pro.shape)
    q = mr.triangulate_measured(m, cam, pro, mr.Filters(max_sigma_mm=10, max_ray_gap_mm=10))
    assert q.accepted.all()
    error = np.linalg.norm(q.xyz - xyz, axis=1)
    assert .7 < np.sqrt(np.mean(error ** 2)) / np.mean(q.sigma_mm) < 1.3
    assert error.max() > 2  # pasar filtros no es aprobar máximo ≤2 mm

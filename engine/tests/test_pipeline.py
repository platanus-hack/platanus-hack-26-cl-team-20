"""Pipeline contract, restart, stale-data and SSE tests. No acquisition."""
import asyncio
import copy
import json
from pathlib import Path
import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from slscan import contracts as c
from slscan.pipeline import Runner, DEPENDENCIES, Result, Blocked
from slscan.pipeline_adapters import synthetic_adapters
from slscan.pipeline_web import build_app, event_stream


@pytest.fixture
def paths(tmp_path):
    source, output = tmp_path / "scan", tmp_path / "out"
    source.mkdir()
    return source, output


def run(paths, **kw):
    return Runner(*paths, synthetic=True, **kw).run()


def artifact(paths, report, stage, name):
    return paths[1] / next(p for p in report["stages"][stage]["outputs"] if Path(p).name == name)


def test_complete_synthetic_contracts_and_cache(paths):
    first = run(paths)
    assert first["status"] == "unverified" and not first["physical_verified"]
    assert set(first["stages"]) == set(DEPENDENCIES)
    for stage in first["stages"].values():
        assert stage["state"] in ("passed", "unverified")
        assert stage["outputs"] and stage["fingerprint"]
        for rel in stage["outputs"]:
            p = paths[1] / rel
            if p.suffix == ".json":
                c.read_json(p)
            elif p.suffix == ".npz":
                c.read_npz(p)
    second = run(paths)
    assert first["run_id"] != second["run_id"]
    assert all(s["cached"] for s in second["stages"].values())
    assert first["stages"]["triangulate"]["metrics"]["synthetic_max_mm"] < .1


def test_rig_change_invalidates_descendants_but_reuses_decode(paths):
    first = run(paths)
    rig = c.read_json(artifact(paths, first, "validate_rig", "rig.json"))
    c.atomic_json(paths[0] / "rig.json", rig)
    second = run(paths)
    assert second["stages"]["decode"]["cached"]
    assert not second["stages"]["validate_rig"]["cached"]
    rig["devices"]["proj"]["K"][0][0] += 5
    c.atomic_json(paths[0] / "rig.json", rig)
    third = run(paths)
    assert third["stages"]["decode"]["cached"]
    assert not third["stages"]["triangulate"]["cached"]
    assert third["stages"]["triangulate"]["state"] == "failed"  # independent known truth catches wrong K
    assert third["stages"]["register_render"]["state"] == "blocked"
    assert third["stages"]["register_render"]["outputs"] == {}


def test_corrupt_output_forces_recompute_and_descendants(paths):
    first = run(paths)
    artifact(paths, first, "mesh", "mesh.ply").write_bytes(b"partial")
    second = run(paths)
    assert second["stages"]["triangulate"]["cached"]
    for stage in ("mesh", "segment", "radiometry_materials", "register_render", "verify"):
        assert not second["stages"][stage]["cached"]
        assert second["stages"][stage]["outputs"]


def test_missing_artifact_and_interrupted_stage_are_not_cached(paths):
    first = run(paths)
    artifact(paths, first, "decode", "matches.npz").unlink()
    first["stages"]["mesh"]["state"] = "running"
    c.atomic_json(paths[1] / "pipeline.json", first)
    second = run(paths)
    assert not second["stages"]["decode"]["cached"]
    assert not second["stages"]["mesh"]["cached"]


def test_cancel_does_not_publish_partial_stage_and_can_resume(paths):
    stop = threading.Event()
    adapters = synthetic_adapters()
    original = adapters["mesh"]
    def cancel_mesh(ctx):
        result = original(ctx)
        stop.set()
        return result
    adapters["mesh"] = cancel_mesh
    first = run(paths, adapters=adapters, cancel=stop)
    assert first["cancelled"] and first["status"] == "blocked"
    assert first["stages"]["mesh"]["outputs"] == {}
    assert first["stages"]["verify"]["state"] == "blocked"
    second = run(paths)
    assert not second["cancelled"] and second["status"] == "unverified"
    assert second["stages"]["triangulate"]["cached"]


def test_failed_adapter_is_atomic(paths):
    adapters = synthetic_adapters()
    def broken(ctx):
        (ctx.work / "cloud.ply").write_text("partial")
        raise RuntimeError("injected failure")
    adapters["triangulate"] = broken
    report = run(paths, adapters=adapters)
    assert report["status"] == "failed"
    assert report["stages"]["triangulate"]["outputs"] == {}
    assert "injected failure" in report["stages"]["triangulate"]["reason"]
    assert not list((paths[1] / "artifacts" / "triangulate").glob(".building-*"))


def test_new_scene_clears_old_outputs_before_any_processing(paths, tmp_path):
    first = run(paths)
    source = tmp_path / "new-scene"
    source.mkdir()
    snapshots = []
    second = Runner(source, paths[1], synthetic=True, on_event=snapshots.append).run()
    assert all(not s["outputs"] for s in snapshots[0]["stages"].values())
    assert second["run_id"] != first["run_id"]
    assert not any(s["cached"] for s in second["stages"].values())


def test_single_writer_lock(paths):
    import fcntl
    paths[1].mkdir()
    with (paths[1] / ".pipeline.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(RuntimeError, match="ejecución"):
            run(paths)


def test_recorded_without_rig_blocks_metric_and_leaves_inputs_unchanged(paths):
    from slscan.graycode import Decoded
    folder = paths[0] / "osmo"
    folder.mkdir()
    dec = Decoded((3, 2), np.array([[1, 2, 9999], [1, 2, 3]], np.uint16),
                  np.ones((2, 3), np.uint16), np.ones((2, 3, 3), np.float32) * 128, {"skip_bits": 2})
    dec.save(folder / "map.npz")
    before = c.digest(folder / "map.npz")
    report = Runner(*paths).run()
    assert report["stages"]["decode"]["state"] == "passed"
    assert report["stages"]["decode"]["counts"]["invalid"] == 1
    assert report["stages"]["validate_rig"]["state"] == "blocked"
    assert "Falta rig.json" in report["stages"]["validate_rig"]["reason"]
    assert report["stages"]["triangulate"]["state"] == "blocked"
    assert c.digest(folder / "map.npz") == before
    assert not (paths[0] / "manifest.json").exists()


def test_source_output_and_camera_paths_are_guarded(paths):
    with pytest.raises(ValueError, match="fuera"):
        Runner(paths[0], paths[0] / "output")
    with pytest.raises(ValueError, match="camera"):
        Runner(*paths, options={"camera": "../../etc"})


@pytest.mark.parametrize("mutation", [
    lambda x: x.update(schema_version="2.0"),
    lambda x: x.update(units="m"),
    lambda x: x["devices"]["proj"]["K"][0].__setitem__(0, -1),
    lambda x: x["devices"]["proj"]["R"][0].__setitem__(0, 2),
    lambda x: x["devices"]["proj"]["T_device_from_world"][0].__setitem__(3, 0),
    lambda x: x["devices"]["proj"]["K"][0].__setitem__(0, float("nan")),
])
def test_bad_rig_rejected(paths, mutation):
    report = run(paths)
    rig = c.read_json(artifact(paths, report, "validate_rig", "rig.json"))
    mutation(rig)
    with pytest.raises(c.ContractError):
        c.validate(rig)


def test_bad_arrays_rejected(paths):
    report = run(paths)
    meta, arrays = c.read_npz(artifact(paths, report, "decode", "matches.npz"))
    bad = copy.deepcopy(arrays)
    bad["interpolated"][0] = True
    with pytest.raises(c.ContractError, match="exactly one"):
        c.write_npz(paths[1] / "bad.npz", meta, bad)
    bad = copy.deepcopy(arrays)
    bad["camera_xy"] = bad["camera_xy"][:-1]
    with pytest.raises(c.ContractError, match="shape"):
        c.validate_arrays("matches", bad)
    _, mesh = c.read_npz(artifact(paths, report, "mesh", "mesh.npz"))
    mesh["faces"][0, 0] = 9999
    with pytest.raises(c.ContractError, match="index"):
        c.validate_arrays("mesh_arrays", mesh)


def test_cannot_claim_physical_from_synthetic_or_rms_only(paths):
    report = run(paths)
    data = c.read_json(artifact(paths, report, "verify", "validation.json"))
    data.update(physical_verified=True, status="passed")
    with pytest.raises(c.ContractError, match="physical"):
        c.validate(data)
    data["evidence"] = "recorded"
    data["checks"] = [dict(name="plane", max_mm=None, rms_mm=.1, p95_mm=.2, samples=100,
                          coverage=1., uncertainty_mm=.1, held_out=True, status="passed")]
    with pytest.raises(c.ContractError, match="maximum"):
        c.validate(data)


def test_web_only_serves_current_intact_artifacts(paths):
    report = run(paths)
    client = TestClient(build_app(paths[1]))
    assert client.get("/").status_code == 200
    assert client.get("/api/pipeline").json()["report"]["run_id"] == report["run_id"]
    preview = client.get("/api/preview").json()
    assert len(preview["points"]) == 96 and len(preview["devices"]) == 2
    assert preview["faces"] and preview["objects"] and preview["materials"]
    rel = next(p for p in report["stages"]["decode"]["outputs"] if p.endswith(".png"))
    assert client.get("/api/artifacts/" + rel).status_code == 200
    assert client.get("/api/artifacts/pipeline.json").status_code == 404
    assert client.get("/api/artifacts/../../etc/passwd").status_code == 404
    assert client.get("/static/contracts.py").status_code == 404
    (paths[1] / rel).write_bytes(b"modified")
    assert client.get("/api/artifacts/" + rel).status_code == 404
    report["stages"]["decode"]["outputs"] = {}
    report["stages"]["decode"]["state"] = "pending"
    c.atomic_json(paths[1] / "pipeline.json", report)
    assert client.get("/api/artifacts/" + rel).status_code == 404


def test_sse_updates_under_one_second_and_reconnect_snapshot(paths):
    report = run(paths)
    async def check():
        async def connected():
            return False
        stream = event_stream(paths[1], connected)
        first = await anext(stream)
        assert report["run_id"] in first and "event: pipeline" in first
        report["stages"]["decode"]["reason"] = "updated"
        c.atomic_json(paths[1] / "pipeline.json", report)
        start = time.monotonic()
        second = await asyncio.wait_for(anext(stream), timeout=1.)
        assert "updated" in second and time.monotonic() - start < 1.
        await stream.aclose()
        reconnect = event_stream(paths[1], connected)
        assert "updated" in await anext(reconnect)
        await reconnect.aclose()
    asyncio.run(check())


def test_cli_registers_pipeline_and_exports_schema(capsys):
    from slscan.cli import main
    assert main(["pipeline", "schema", "rig"]) == 0
    assert json.loads(capsys.readouterr().out)["$id"] == "urn:slscan:rig:1.0"


def test_product_handoff_requires_current_dependencies_and_nested_assets(paths, tmp_path):
    import shutil
    from slscan.pipeline import Context
    from slscan.pipeline_adapters import import_product
    report = run(paths)
    folder = paths[0] / "products" / "triangulate"
    folder.mkdir(parents=True)
    for name in ("cloud.json", "cloud.ply", "cloud_quality.npz"):
        shutil.copyfile(artifact(paths, report, "triangulate", name), folder / name)
    meta, arrays = c.read_npz(folder / "cloud_quality.npz")
    meta["evidence"] = "recorded"
    c.write_npz(folder / "cloud_quality.npz", meta, arrays)
    data = c.read_json(folder / "cloud.json")
    data["evidence"] = "recorded"
    data["quality"]["sha256"] = c.digest(folder / "cloud_quality.npz")
    data["input_hashes"] = {"decode/matches.npz": "a" * 64, "validate_rig/rig.json": "b" * 64}
    c.atomic_json(folder / "cloud.json", data)
    runner = Runner(*paths)
    runner.report = report
    work = tmp_path / "handoff"
    work.mkdir()
    ctx = Context(runner, "triangulate", work, dict(data["input_hashes"]))
    result = import_product(ctx)
    c.validate_files(work, result.paths)
    ctx.inputs["validate_rig/rig.json"] = "c" * 64
    with pytest.raises(Blocked, match="obsoleto"):
        import_product(ctx)
    ctx.inputs = data["input_hashes"]
    (folder / "cloud.ply").write_bytes(b"changed")
    with pytest.raises(Blocked, match="modificado"):
        import_product(ctx)


def test_bad_metadata_count_and_undeclared_asset_fail_closed(paths):
    report = run(paths)
    meta, arrays = c.read_npz(artifact(paths, report, "mesh", "mesh.npz"))
    meta["face_count"] += 1
    with pytest.raises(c.ContractError, match="counts"):
        c.write_npz(paths[1] / "bad.npz", meta, arrays)
    scene = artifact(paths, report, "register_render", "scene.json")
    with pytest.raises(c.ContractError, match="undeclared"):
        c.validate_files(scene.parent, [scene.name])


def test_all_exported_schemas_are_valid():
    from jsonschema import Draft202012Validator
    for schema in c.SCHEMAS.values():
        Draft202012Validator.check_schema(schema)


def test_recorded_rig_requires_actual_decoded_frame_match(paths):
    from slscan.graycode import Decoded
    report = run(paths)
    rig = c.read_json(artifact(paths, report, "validate_rig", "rig.json"))
    rig["evidence"] = "recorded"
    c.atomic_json(paths[0] / "rig.json", rig)
    manifest = c.read_json(artifact(paths, report, "preflight", "manifest.json"))
    manifest.update(evidence="recorded", rig_sha256=c.digest(paths[0] / "rig.json"))
    c.atomic_json(paths[0] / "manifest.json", manifest)
    folder = paths[0] / "osmo"
    folder.mkdir()
    Decoded((3, 2), np.ones((2, 3), np.uint16), np.ones((2, 3), np.uint16),
            np.full((2, 3, 3), 128., np.float32), {"skip_bits": 2}).save(folder / "map.npz")
    report = Runner(*paths, options={"projector_size": [320, 240]}).run()
    assert report["stages"]["validate_rig"]["state"] == "blocked"
    assert "Resolución decodificada" in report["stages"]["validate_rig"]["reason"]


def test_manifest_inputs_rechecked_after_cache(paths):
    report = run(paths)
    source = paths[0] / "frame.bin"
    source.write_bytes(b"first")
    manifest = c.read_json(artifact(paths, report, "preflight", "manifest.json"))
    manifest.update(evidence="recorded", input_hashes={"frame.bin": c.digest(source)})
    c.atomic_json(paths[0] / "manifest.json", manifest)
    first = Runner(*paths).run()
    assert first["stages"]["preflight"]["state"] == "passed"
    source.write_bytes(b"changed")
    second = Runner(*paths).run()
    assert second["stages"]["preflight"]["state"] == "blocked"
    assert "no coincide" in second["stages"]["preflight"]["reason"]

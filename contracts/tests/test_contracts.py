from __future__ import annotations

import json

import jsonschema
import msgspec
import pytest

from pcx_contracts import FIXTURES, FIXTURES_DIR, ROOTS
from pcx_contracts.cloud import ToolCall
from pcx_contracts.commands import Project, RunFlow, check_flow
from pcx_contracts.envelope import Envelope, wrap
from pcx_contracts.events import WidgetPressed
from pcx_contracts.export import bundle_schema, canonical
from pcx_contracts.geometry import PointMM
from pcx_contracts.render import SceneSnapshot, to_legacy
from pcx_contracts.tools import TOOLS, input_schema, mcp_tools, parse_args


def load(name: str):
    return msgspec.json.decode((FIXTURES_DIR / name).read_bytes(), type=FIXTURES[name])


@pytest.mark.parametrize("name", FIXTURES)
def test_roundtrip_json_and_msgpack(name: str) -> None:
    tp = FIXTURES[name]
    obj = load(name)
    assert msgspec.json.decode(msgspec.json.encode(obj), type=tp) == obj
    assert msgspec.msgpack.decode(msgspec.msgpack.encode(obj), type=tp) == obj


@pytest.mark.parametrize("name", FIXTURES)
def test_canonical_up_to_date(name: str) -> None:
    on_disk = json.loads((FIXTURES_DIR / "canonical" / name).read_text(encoding="utf-8"))
    assert on_disk == canonical(name), "regenera con `uv run python -m pcx_contracts.export`"


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_validates_against_exported_schema(name: str) -> None:
    bundle = bundle_schema()
    root = next(k for k, v in ROOTS.items() if v is FIXTURES[name])
    schema = {**bundle["properties"][root], "$defs": bundle["$defs"]}
    raw = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(raw)
    jsonschema.Draft202012Validator(schema).validate(canonical(name))


def test_points_are_arrays() -> None:
    assert msgspec.json.encode(PointMM(1.5, 2.0)) == b"[1.5,2.0]"


def test_flow_is_coherent() -> None:
    cmd = load("flow_guia_3_pasos.json")
    assert isinstance(cmd, RunFlow)
    assert check_flow(cmd.flow) == []
    assert [s.id for s in cmd.flow.states] == ["paso1", "paso2", "paso3", "fin"]


def test_tool_call_args_parse_as_tool_input() -> None:
    call = load("tool_call.json")
    assert isinstance(call, ToolCall)
    cmd = parse_args(call.tool, call.args)
    assert isinstance(cmd, Project) and len(cmd.widgets) == 2
    jsonschema.Draft202012Validator(input_schema(call.tool)).validate(call.args)


def test_tool_schemas_are_flat_objects() -> None:
    tools = mcp_tools()
    assert [t["name"] for t in tools] == [
        "scene", "look", "project", "remove", "ask", "events_wait", "run_flow", "calibrate", "devices", "home"]
    for t in tools:
        s = json.dumps(t["inputSchema"])
        assert t["inputSchema"]["type"] == "object"
        assert "$ref" not in s and "$defs" not in s, t["name"]
        assert "type" not in t["inputSchema"].get("required", [])
    assert len(TOOLS) == len(tools)


def test_envelope_wrap() -> None:
    env = wrap(WidgetPressed(widget_id="b"), seq=7, source="core", node_id="n1")
    assert env.type == "widget_pressed" and env.v == 2
    assert msgspec.json.decode(msgspec.json.encode(env), type=Envelope) == env


def test_legacy_scene_matches_protocolo_v0() -> None:
    snap = load("scene.json")
    assert isinstance(snap, SceneSnapshot)
    legacy = to_legacy(snap, "main")
    assert legacy["homography"] == [2.16, 0, 96, 0, 2.16, 0, 0, 0, 1]
    types = [w["type"] for w in legacy["widgets"]]
    assert types == ["label", "halo", "arrow", "panel", "button"]
    assert legacy["widgets"][0]["anchor"] == {"x_mm": 110.0, "y_mm": 314.0}
    assert legacy["widgets"][1]["polygon_mm"][0] == [155.0, 370.0]

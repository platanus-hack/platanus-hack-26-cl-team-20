"""Pruebas de los cambios v1→v2 (revisión de contratos)."""

from __future__ import annotations

import json

import jsonschema
import msgspec
import pytest

from pcx_contracts import FIXTURES_DIR, ROOTS, SCHEMA_DIR, ns_to_str
from pcx_contracts.cloud import CloudMessage, HistoryGap
from pcx_contracts.commands import Command, Flow, HomeCall, RunFlow, flow_is_destructive
from pcx_contracts.envelope import Envelope
from pcx_contracts.events import Event
from pcx_contracts.export import bundle_schema, to_draft07
from pcx_contracts.geometry import Transform, projector_matrix, select_projector_transform
from pcx_contracts.home import HomePolicy, action_digest, decide
from pcx_contracts.render import FromRenderer, RenderBlank, ToRenderer
from pcx_contracts.tools import input_schema, mcp_tools, parse_args
from pcx_contracts.widgets import Widget

ROOT_TYPES = {"widget": Widget, "transform": Transform, "to_renderer": ToRenderer, "from_renderer": FromRenderer}
INVALID = json.loads((FIXTURES_DIR / "invalid" / "cases.json").read_text(encoding="utf-8"))
SELECTION = json.loads((FIXTURES_DIR / "selection_cases.json").read_text(encoding="utf-8"))
FLOW_RAW = json.loads((FIXTURES_DIR / "flow_guia_3_pasos.json").read_text(encoding="utf-8"))
ENV_RAW = json.loads((FIXTURES_DIR / "envelope_widget_pressed.json").read_text(encoding="utf-8"))


def _schemas(root: str) -> tuple[dict, dict]:
    bundle = bundle_schema()
    s2020 = {**bundle["properties"][root], "$defs": bundle["$defs"]}
    d07 = to_draft07(bundle)
    s07 = {**d07["properties"][root], "definitions": d07["definitions"]}
    return s2020, s07


# ---------------------------------------------------------------- 7. negativos compartidos (Python + 2 schemas; Rust en tests/fixtures.rs)

@pytest.mark.parametrize("case", INVALID, ids=[c["why"] for c in INVALID])
def test_shared_negative_fixtures_are_rejected(case: dict) -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.convert(case["value"], ROOT_TYPES[case["root"]])
    s2020, s07 = _schemas(case["root"])
    assert not jsonschema.Draft202012Validator(s2020).is_valid(case["value"]), "2020-12 lo acepta"
    assert not jsonschema.Draft7Validator(s07).is_valid(case["value"]), "draft-07 lo acepta"


# ---------------------------------------------------------------- 10. selección de transformaciones

@pytest.mark.parametrize("case", SELECTION, ids=[c["name"] for c in SELECTION])
def test_projector_transform_selection(case: dict) -> None:
    ts = msgspec.convert(case["transforms"], list[Transform])
    sel = select_projector_transform(ts, case["projector_id"])
    idx = None if sel is None else next(i for i, t in enumerate(ts) if t is sel)
    assert idx == case["selected_index"]
    assert projector_matrix(ts, case["projector_id"]) == case["matrix"]


# ---------------------------------------------------------------- 1. domótica

def test_home_call_rejects_caller_side_confirmation_flag() -> None:
    with pytest.raises(msgspec.ValidationError):
        parse_args("home", {"request_id": "r1", "domain": "lock", "service": "unlock", "requires_confirmation": False})
    bad = json.loads(json.dumps(FLOW_RAW))
    bad["flow"]["states"][2]["on_enter"][1]["requires_confirmation"] = False
    with pytest.raises(msgspec.ValidationError):
        msgspec.convert(bad, Command)


def test_home_policy_decides_on_node() -> None:
    pol = HomePolicy(allow=["light.*", "lock.unlock"], confirm=["lock.*"])
    call = lambda d, s: HomeCall(request_id="r", domain=d, service=s)  # noqa: E731
    assert decide(pol, call("light", "turn_on")) == "allow"
    assert decide(pol, call("lock", "unlock")) == "confirm"
    assert decide(pol, call("cover", "open")) == "deny"
    assert decide(HomePolicy(allow=["*"]), call("light", "turn_on")) == "confirm"  # por defecto todo se confirma


def test_action_digest_binds_exact_action() -> None:
    a = HomeCall(request_id="r1", domain="lock", service="unlock", data={"entity_id": "lock.puerta"})
    b = HomeCall(request_id="r2", domain="lock", service="unlock", data={"entity_id": "lock.puerta"})
    c = HomeCall(request_id="r1", domain="lock", service="unlock", data={"entity_id": "lock.garage"})
    assert action_digest(a) == action_digest(b) != action_digest(c)
    assert len(action_digest(a)) == 64


def test_run_flow_is_destructive() -> None:
    ann = {t["name"]: t["annotations"] for t in mcp_tools()}
    assert ann["run_flow"]["destructiveHint"] and ann["home"]["destructiveHint"]
    flow = msgspec.convert(FLOW_RAW, RunFlow).flow
    assert flow_is_destructive(flow)


def test_reserved_widget_ids_rejected() -> None:
    with pytest.raises(msgspec.ValidationError):
        parse_args("remove", {"request_id": "r", "ids": ["pcx:confirm:abc"]})


# ---------------------------------------------------------------- 2. nulabilidad en inputSchema

def test_canonical_objects_validate_against_input_schema() -> None:
    cases = {
        "project": {"request_id": "r", "widgets": [
            {"type": "button", "id": "b", "anchor": None, "on_press": None, "rect_mm": [0, 0, 10, 10]}]},
        "run_flow": FLOW_RAW,
        "ask": {"request_id": "r", "question": "¿Sí?", "options": ["sí", "no"], "anchor": None},
        "look": {"camera_id": None},
        "devices": {"kind": None},
        "calibrate": {"request_id": "r", "camera_id": None, "projector_id": None},
    }
    for tool, args in cases.items():
        obj = parse_args(tool, {k: v for k, v in args.items() if k != "type"})
        canon = json.loads(msgspec.json.encode(obj))
        canon.pop("type", None)
        jsonschema.Draft202012Validator(input_schema(tool)).validate(canon)


# ---------------------------------------------------------------- 3. run_flow

def _flow(**over) -> dict:
    f = {"id": "f", "initial": "a", "states": [{"id": "a", "transitions": [{"after_s": 1, "to": "b"}]},
                                               {"id": "b", "final": True}]}
    f.update(over)
    return {"request_id": "r", "flow": f}


@pytest.mark.parametrize("args", [
    _flow(states=[], initial="a"),
    _flow(initial="nope"),
    _flow(states=[{"id": "a", "transitions": [{"after_s": -1, "to": "a"}]}]),
    _flow(states=[{"id": "a", "transitions": [{"after_s": 2, "on": {"event": "x"}, "to": "b"}]}, {"id": "b"}]),
    _flow(states=[{"id": "a", "transitions": [{"after_s": 0, "to": "b"}]},
                  {"id": "b", "transitions": [{"after_s": 0, "to": "a"}]}]),
    _flow(states=[{"id": "a", "final": True, "transitions": [{"after_s": 1, "to": "a"}]}]),
    _flow(timeout_s=-5),
], ids=["vacío", "initial", "negativo", "ambiguo", "ciclo-inmediato", "final-con-transición", "timeout-negativo"])
def test_invalid_flows_rejected(args: dict) -> None:
    with pytest.raises(msgspec.ValidationError):
        parse_args("run_flow", args)


def test_valid_flow_accepted() -> None:
    assert isinstance(parse_args("run_flow", _flow()), RunFlow)
    assert isinstance(parse_args("run_flow", {k: v for k, v in FLOW_RAW.items() if k != "type"}).flow, Flow)


# ---------------------------------------------------------------- 4. envelope

def test_envelope_rejects_mismatched_type_unknown_or_missing_version() -> None:
    for patch in ({"type": "home_call"}, {"v": 999}, {"v": 1}):
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert({**ENV_RAW, **patch}, Envelope)
    with pytest.raises(msgspec.ValidationError):
        msgspec.convert({k: v for k, v in ENV_RAW.items() if k != "v"}, Envelope)


def test_version_handshake() -> None:
    hello = msgspec.convert({"type": "hello", "node_id": "n", "token": "t", "version": "0.2", "contract_versions": [2],
                             "stream_id": "s1", "log_first_cursor": 100, "log_last_cursor": 900}, CloudMessage)
    assert hello.retention.max_events == 10_000
    w = msgspec.convert({"type": "welcome", "session_id": "x", "contract_version": 2, "last_stream_id": "s1",
                         "last_cursor": 50}, CloudMessage)
    assert w.contract_version in hello.contract_versions
    msgspec.convert({"type": "hello_rejected", "reason": "version", "supported_versions": [2]}, CloudMessage)


# ---------------------------------------------------------------- 5. reconexión

def test_history_gap_wire_form() -> None:
    gap = HistoryGap(stream_id="s1", from_=51, to=99, reason="retention")
    raw = json.loads(msgspec.json.encode(gap))
    assert raw == {"type": "history_gap", "stream_id": "s1", "from": 51, "to": 99, "reason": "retention"}
    assert msgspec.convert(raw, CloudMessage) == gap
    done = msgspec.convert({"type": "replay_done", "stream_id": "s1", "cursor": 900, "history_complete": False},
                           CloudMessage)
    assert done.history_complete is False


# ---------------------------------------------------------------- 8. idempotencia

@pytest.mark.parametrize("tool,args", [
    ("project", {"widgets": []}), ("remove", {"ids": ["a"]}), ("ask", {"question": "q", "options": ["a"]}),
    ("run_flow", {"flow": _flow()["flow"]}), ("calibrate", {}), ("home", {"domain": "light", "service": "turn_on"}),
])
def test_request_id_required(tool: str, args: dict) -> None:
    with pytest.raises(msgspec.ValidationError):
        parse_args(tool, args)
    assert "request_id" in input_schema(tool)["required"]


def test_ask_answered_requires_request_id() -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.convert({"type": "ask_answered", "answer": "sí"}, Event)


# ---------------------------------------------------------------- 9. nanosegundos

def test_ns_as_decimal_strings_for_ts() -> None:
    out = ns_to_str(ENV_RAW)
    assert out["t_capture_ns"] == "1795231660101000000" and int(out["t_pub_ns"]) == ENV_RAW["t_pub_ns"]
    assert out["seq"] == ENV_RAW["seq"]
    ts = json.loads((SCHEMA_DIR / "pcx.ts.json").read_text(encoding="utf-8"))
    env = ts["definitions"]["Envelope"]["properties"]
    assert env["t_pub_ns"]["type"] == "string"
    assert "string" in json.dumps(env["t_capture_ns"])


# ---------------------------------------------------------------- 11. blank

def test_blank_command_and_render_messages() -> None:
    cmd = msgspec.convert({"type": "blank", "request_id": "r", "ms": 300, "reason": "clean_photo"}, Command)
    assert cmd.ms == 300
    msg = msgspec.convert({"type": "blank", "request_id": "r", "ms": 300, "reason": "clean_photo"}, ToRenderer)
    assert isinstance(msg, RenderBlank)
    msgspec.convert({"type": "blanked", "request_id": "r", "t_start_ms": 1.0, "t_end_ms": 301.0}, FromRenderer)
    msgspec.convert({"type": "blanked", "request_id": "r", "projector_id": "main", "t_start_ms": 1.0,
                     "t_end_ms": 301.0}, Event)
    assert "transform" in ROOTS


# ---------------------------------------------------------------- 12. v2.1

def test_v21_pattern_resync_and_shown() -> None:
    from pcx_contracts.render import Pattern, PatternClear, PatternShown, Resync

    p = msgspec.convert({"type": "pattern", "pattern_id": "g0", "image": {"data_uri": "data:image/png;base64,AA=="}},
                        ToRenderer)
    assert isinstance(p, Pattern) and p.until_ms is None
    assert isinstance(msgspec.convert({"type": "pattern_clear"}, ToRenderer), PatternClear)
    for bad in ({}, {"path": "/a.png", "data_uri": "data:,"}):
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert({"type": "pattern", "pattern_id": "g0", "image": bad}, ToRenderer)
    assert isinstance(msgspec.convert({"type": "pattern_shown", "pattern_id": "g0", "t_present_ms": 1.0},
                                      FromRenderer), PatternShown)
    r = msgspec.convert({"type": "resync", "have_rev": None}, FromRenderer)
    assert isinstance(r, Resync) and r.have_rev is None


def test_v21_events_and_confirm_action() -> None:
    from pcx_contracts.events import FrameMeta, HandsGone
    from pcx_contracts.home import ConfirmAction

    assert isinstance(msgspec.convert({"type": "hands_gone", "t_capture_ns": 5}, Event), HandsGone)
    fm = msgspec.convert({"type": "frame_meta", "camera_id": "c", "seq": 1, "t_capture_ns": 2, "w": 4, "h": 2}, Event)
    assert isinstance(fm, FrameMeta) and (fm.segment, fm.format, fm.stride) == (None, "bgr8", None)
    with pytest.raises(msgspec.ValidationError):
        msgspec.convert({"type": "frame_meta", "camera_id": "c", "seq": 1, "t_capture_ns": 2, "w": 4, "h": 2,
                         "format": "yuyv"}, Event)
    t = msgspec.convert({"type": "touch_down", "touch": {"id": "t", "phase": "down", "point_mm": [1, 2],
                                                        "trigger": "rest"}}, Event)
    assert t.touch.trigger == "rest"
    ca = msgspec.convert({"type": "confirm_action", "confirmation_id": "c", "approved": False,
                          "action_digest": "a" * 64}, CloudMessage)
    assert isinstance(ca, ConfirmAction) and ca.by == "console" and ca.operator is None
    with pytest.raises(msgspec.ValidationError):  # el nombre v2 `approve` ya no vale
        msgspec.convert({"type": "confirm_action", "confirmation_id": "c", "approve": True,
                         "action_digest": "a" * 64}, CloudMessage)

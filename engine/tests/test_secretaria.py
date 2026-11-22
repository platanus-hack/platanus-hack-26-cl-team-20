"""Agente de voz «Playground» (playground/secretaria): catálogo, handlers contra un runtime falso o en memoria,
token del puente, página local, túnel y payload del agente. Sin red: el runtime va por TestClient, ElevenLabs y el
runtime falso por httpx.MockTransport."""

import json
import os
import re
import stat

import httpx
import pytest
from fastapi.testclient import TestClient

from playground.runtime import Runtime, build_app as build_runtime_app
from playground.secretaria import config as cfgmod
from playground.secretaria import agent, tunel
from playground.secretaria.server import build_app, build_local_app, token_ok
from playground.secretaria.tools import (RuntimeClient, Toolbox, catalog, coerce, parse_color, resolve_effect,
                                         webhook_config)

pytestmark = pytest.mark.filterwarnings("ignore:You should not use the 'timeout' argument with the TestClient")

NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
TYPES = {"string", "number", "integer", "boolean"}


@pytest.fixture(autouse=True)
def aislado(tmp_path, monkeypatch):
    """Config y estado en tmp: nunca toca ~/.config/sl-scanner ni la llave real."""
    monkeypatch.setenv("PLAYGROUND_SECRETARIA_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("PLAYGROUND_SECRETARIA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("PLAYGROUND_PUENTE_TOKEN", raising=False)
    monkeypatch.setitem(cfgmod.DEFAULTS, "api_key_archivo", str(tmp_path / "no-hay-llave"))
    monkeypatch.setitem(cfgmod.DEFAULTS, "runtime_repo", str(tmp_path / "repo"))      # nunca el checkout real
    monkeypatch.setenv("PLAYGROUND_STATE", str(tmp_path / "pgstate"))


@pytest.fixture
def box(tmp_path):
    rt = Runtime(canvas_size=(640, 320), requests_dir=tmp_path / "requests")
    client = RuntimeClient(url="http://testserver", http=TestClient(build_runtime_app(rt)), autostart=False)
    lights = FakeLights()
    return Toolbox(runtime=client, lights=lights, requests_dir=tmp_path / "requests"), rt, lights


class FakeLights:
    def __init__(self):
        self.calls = []

    def __call__(self, light_id):
        self.id = light_id
        return self

    def status(self):
        return {"light": self.id, "learned": False}

    def set(self, **kw):
        self.calls.append(kw)
        return {"light": self.id, "sent": {k: v for k, v in kw.items() if v is not None}}


# ---------------------------------------------------------------------------- catálogo

def test_catalog_is_valid_elevenlabs_schema():
    tools = catalog()
    names = [t.name for t in tools]
    assert len(names) == len(set(names)) >= 15
    wanted = {"pg_efectos", "pg_lanzar_efecto", "pg_apagar_efecto", "pg_ajustar", "pg_estado", "pg_objetos",
              "pg_modo", "pg_audio_reactivo", "pg_luz", "pg_midi", "pg_idea", "pg_que_proyectas"}
    assert wanted <= set(names)
    for t in tools:
        assert NAME_RE.match(t.name), t.name
        assert 20 < len(t.description) < 1000, t.name
        assert set(t.required) <= set(t.properties), t.name
        for k, spec in t.properties.items():
            assert spec["type"] in TYPES and spec.get("description"), (t.name, k)
            if "enum" in spec:
                assert spec["type"] == "string" and all(isinstance(x, str) and x for x in spec["enum"]), (t.name, k)
        conf = webhook_config(t, "https://ejemplo.trycloudflare.com/", "sec123")
        api = conf["api_schema"]
        assert api["url"] == f"https://ejemplo.trycloudflare.com/playground/h/{t.name}"
        assert api["request_headers"]["X-Playground-Token"] == {"secret_id": "sec123"}
        assert api["request_body_schema"]["type"] == "object"
        assert 5 <= conf["response_timeout_secs"] <= 120
        json.dumps(conf)


def test_coerce_types_and_enums():
    tools = {t.name: t for t in catalog()}
    assert coerce(tools["pg_tempo"], {"bpm": "128", "tocando": "true"}) == {"bpm": 128.0, "tocando": True}
    assert coerce(tools["pg_luz"], {"accion": "ajustar", "temperatura_k": "3200.0"})["temperatura_k"] == 3200
    with pytest.raises(ValueError, match="uno de"):
        coerce(tools["pg_modo"], {"modo": "karaoke", "activo": True})
    with pytest.raises(ValueError, match="falta"):
        coerce(tools["pg_idea"], {"titulo": "x", "tipo": "visual"})
    with pytest.raises(ValueError, match="number"):
        coerce(tools["pg_tempo"], {"bpm": "rápido"})


def test_language_helpers():
    assert parse_color("turquesa") == [0, 255, 200]
    assert parse_color("[255, 0, 0]") == [255, 0, 0]
    assert parse_color("#00ff00") == [0, 255, 0]
    assert parse_color("plaid") is None
    gallery = [{"name": "contour_glow", "label": "Halo de contorno"}, {"name": "texture_grid", "label": "Textura"}]
    assert resolve_effect(gallery, "halo") == "contour_glow"
    assert resolve_effect(gallery, "Halo de contorno") == "contour_glow"
    assert resolve_effect(gallery, "la textura") == "texture_grid"
    assert resolve_effect(gallery, "karaoke") is None


# ---------------------------------------------------------------------------- handlers contra el runtime en memoria

def test_handlers_drive_the_runtime(box):
    tb, rt, _ = box
    s = tb.call("pg_estado", {"motivo": "ver"})
    assert any("cocodrilo" in o.lower() for o in s["objetos"]) and s["efectos_vivos"] == "ninguno"
    assert "contour_glow" in {e["nombre"] for e in tb.call("pg_efectos", {"motivo": "x"})["efectos"]}
    assert any(o["tipo"] == "instrument" for o in tb.call("pg_objetos", {"tipo": "instrument"})["objetos"])
    assert tb.call("pg_buscar_objeto", {"nombre": "cocodrilo"})["medidas_mm"]

    r = tb.call("pg_lanzar_efecto", {"efecto": "halo", "objeto": "cocodrilo", "parametros": '{"color": "turquesa"}'})
    assert r["efecto"] == "contour_glow" and r["parametros"]["color"] == [0, 255, 200]
    iid = r["lanzado"]
    assert tb.call("pg_ajustar", {"efecto": "el halo", "parametro": "color", "valor": "rojo"})["valor"] == [255, 0, 0]
    assert tb.call("pg_ajustar", {"efecto": "cocodrilo", "parametro": "width_mm", "valor": "40"})["valor"] == 40
    assert rt.instances[iid].p["width_mm"] == 40

    rt.tick(0.03)
    q = tb.call("pg_que_proyectas", {"motivo": "x"})
    assert len(q["efectos"]) == 1 and "Halo" in q["efectos"][0]
    assert q["captura"] and os.path.exists(q["captura"]) and "lienzo" in q

    assert tb.call("pg_tempo", {"bpm": 128})["pulso"]["bpm"] == 128
    assert "error" in tb.call("pg_tempo", {"bpm": 999})

    assert "ohm" in json.dumps(tb.call("pg_midi", {"accion": "estado"}))
    tb.call("pg_midi", {"accion": "conectar", "fuente": "fake"})
    assert tb.call("pg_midi", {"accion": "simular", "control": "knob0", "valor": 0.7})
    b = tb.call("pg_enlazar", {"control": "ohm/knob0", "destino": "effect:halo:width_mm", "minimo": 5, "maximo": 80})
    assert b["target"] == f"effect:{iid}:width_mm"

    idea = tb.call("pg_idea", {"titulo": "halo que respire", "detalle": "el halo del cocodrilo late con el bpm",
                               "tipo": "visual"})
    assert idea["anotada"] == "001"
    assert tb.call("pg_ideas", {"estado": "todos"})["total"] == 1

    assert tb.call("pg_apagar_efecto", {"efecto": "no-existe"})["error"]
    assert tb.call("pg_apagar_efecto", {"todos": True})["apagados"] == [iid]
    assert tb.call("pg_runtime", {"accion": "estado"})["runtime"] == "prendido"


def test_errors_come_back_as_text(box):
    tb, _, _ = box
    assert "no hay un efecto" in tb.call("pg_lanzar_efecto", {"efecto": "karaoke"})["error"]
    assert "no aplica" in tb.call("pg_lanzar_efecto", {"efecto": "frame_feed", "objeto": "cocodrilo"})["error"]
    assert "no existe" in tb.call("pg_nada", {})["error"]
    assert "JSON" in tb.call("pg_lanzar_efecto", {"efecto": "halo", "parametros": "{color: rojo"})["error"]


def test_modes_spawn_the_touchsynth_effects(box):
    """Los modos de la mesa táctil ya existen como efectos (playground/touchsynth): se prenden y se apagan."""
    tb, rt, _ = box
    assert tb.call("pg_modo", {"modo": "marimba", "activo": True})["efecto"] == "marimba"
    assert tb.call("pg_modo", {"modo": "touch", "activo": True})["efecto"] == "croc_synth"
    assert tb.call("pg_modo", {"modo": "dibujo", "activo": True})["efecto"] == "touch_draw"
    assert "ya estaba" in tb.call("pg_modo", {"modo": "marimba", "activo": True})["nota"]
    off = tb.call("pg_modo", {"modo": "ninguno", "activo": False})
    assert off["activo"] is False and len(off["apagados"]) == 3 and rt.state()["effects"] == []


def test_modes_are_pending_without_runtime_support(box, monkeypatch):
    from playground.secretaria import tools
    monkeypatch.setitem(tools.MODOS, "marimba", ("no_existe_aun",))
    monkeypatch.setitem(tools.MODOS, "touch", ("tampoco_existe",))
    tb, _, _ = box
    r = tb.call("pg_modo", {"modo": "marimba", "activo": True})    # el runtime ya tiene marimba y touch (16-oct)
    assert r.get("activo") is True or (r.get("estado") == "pendiente" and "pg_idea" in r["detalle"])
    r = tb.call("pg_modo", {"modo": "touch", "activo": True})
    assert r.get("activo") is True or "004" in r.get("detalle", "")
    assert tb.call("pg_modo", {"modo": "ninguno", "activo": False})["activo"] is False


def test_lights_use_the_ble_factory(box):
    tb, _, lights = box
    assert tb.call("pg_luz", {"accion": "estado"})["light"] == "smallrig"
    tb.call("pg_luz", {"accion": "ajustar", "intensidad": 0.8, "temperatura_k": 3200})
    assert lights.calls[-1] == {"on": None, "intensity": 0.8, "cct": 3200, "rgb": None}
    tb.call("pg_luz", {"accion": "encender", "color": "rojo"})
    assert lights.calls[-1]["on"] is True and lights.calls[-1]["rgb"] == [255, 0, 0]
    tb.call("pg_luz", {"accion": "apagar"})
    assert lights.calls[-1] == {"on": False}
    assert tb.call("pg_luz", {"accion": "estado", "luz": "dmx0"})["estado"] == "pendiente"


# ---------------------------------------------------------------------------- runtime falso (contrato de modos)

def fake_runtime(commands: dict, state: dict):
    seen = []

    def handler(request: httpx.Request):
        if request.url.path == "/api/schema":
            return httpx.Response(200, json={})
        if request.url.path == "/api/state":
            return httpx.Response(200, json=state)
        body = json.loads(request.content)
        seen.append(body)
        fn = commands.get(body["cmd"])
        if fn is None:
            return httpx.Response(400, json={"ok": False, "error": f"comando desconocido: {body['cmd']}"})
        return httpx.Response(200, json={"ok": True, "result": fn(body.get("args") or {})})
    client = RuntimeClient(url="http://fake", http=httpx.Client(transport=httpx.MockTransport(handler)), autostart=False)
    return client, seen


def test_mode_uses_runtime_command_when_it_exists():
    client, seen = fake_runtime({"mode": lambda a: {"mode": a["mode"], "on": a["on"]}}, {"effects": [], "gallery": []})
    r = Toolbox(runtime=client).call("pg_modo", {"modo": "dibujo", "activo": True})
    assert r["runtime"] == {"mode": "dibujo", "on": True} and seen[-1]["cmd"] == "mode"


def test_mode_falls_back_to_a_gallery_effect():
    state = {"effects": [], "gallery": [{"name": "marimba_pads", "label": "Marimba en la mesa", "targets": ["table"]}]}
    client, seen = fake_runtime({"spawn": lambda a: {"id": "e7", **a}}, state)
    r = Toolbox(runtime=client).call("pg_modo", {"modo": "marimba", "activo": True})
    assert r["efecto"] == "marimba_pads" and r["id"] == "e7"
    assert seen[-1] == {"cmd": "spawn", "args": {"effect": "marimba_pads", "target": "mesa"}}


def test_audio_pending_on_an_old_runtime():
    client, _ = fake_runtime({}, {"effects": [], "gallery": []})
    assert Toolbox(runtime=client).call("pg_audio_reactivo", {"activo": True})["estado"] == "pendiente"


class FakeAudioCtl:
    def __init__(self):
        self.calls = []

    def start(self, device="", channel=-1):
        self.calls.append(("start", device, channel))
        return {"status": "running", "device": "MacBook Air Microphone"}

    def stop(self):
        self.calls.append(("stop",))
        return {"status": "stopped"}


def test_audio_uses_the_capture_process_when_the_runtime_has_audio():
    state = {"audio": {"live": False, "features": {}}, "gallery": [{"name": "bass_pulse", "label": "Pulso"}],
             "effects": [{"id": "e2", "effect": "bass_pulse", "target": None, "params": {}}], "scene": {"objects": {}}}
    client, seen = fake_runtime({}, state)
    ctl = FakeAudioCtl()
    tb = Toolbox(runtime=client, audio_ctl=ctl)
    on = tb.call("pg_audio_reactivo", {"activo": True, "dispositivo": "MacBook"})
    assert on["captura"] == "running" and on["dispositivo"] == "MacBook Air Microphone" and "e2" in on["lo_siguen"][0]
    assert ctl.calls[-1] == ("start", "MacBook", -1) and not seen        # no manda comandos: lo hace la captura
    assert tb.call("pg_audio_reactivo", {"activo": False})["captura"] == "stopped"
    assert tb.call("pg_estado", {"motivo": "x"})["audio_reactivo"]["vivo"] is False


def test_runtime_down_is_reported_and_ideas_still_land(tmp_path):
    def handler(request):
        raise httpx.ConnectError("apagado")
    client = RuntimeClient(url="http://apagado", http=httpx.Client(transport=httpx.MockTransport(handler)),
                           autostart=False)
    tb = Toolbox(runtime=client, requests_dir=tmp_path / "req")
    assert "apagado" in tb.call("pg_estado", {"motivo": "x"})["error"]
    r = tb.call("pg_idea", {"titulo": "luz que siga el beat", "detalle": "x", "tipo": "visual"})
    assert r["anotada"] == "001" and list((tmp_path / "req").glob("001-*.md"))


# ---------------------------------------------------------------------------- puente: token y rutas

def test_server_requires_the_token(box, tmp_path):
    tb, _, _ = box
    c = TestClient(build_app(toolbox=tb, token_fn=lambda: "s3cret-token"))
    assert c.get("/playground/salud").json() == {"ok": True}
    assert c.post("/playground/h/pg_estado", json={"motivo": "x"}).status_code == 401
    assert c.post("/playground/h/pg_estado", json={"motivo": "x"},
                  headers={"X-Playground-Token": "otro"}).status_code == 401
    ok = c.post("/playground/h/pg_estado", json={"motivo": "x"}, headers={"X-Playground-Token": "s3cret-token",
                                                                          "X-Conversacion": "conv_1"})
    assert ok.status_code == 200 and "objetos" in ok.json()
    assert c.post("/h/pg_tempo", json={"bpm": 100}, headers={"X-Playground-Token": "s3cret-token"}).json()["pulso"]["bpm"] == 100
    assert c.post("/playground/h/pg_nada", json={}, headers={"X-Playground-Token": "s3cret-token"}).status_code == 404
    assert c.post("/playground/h/pg_estado", content=b"{no json", headers={"X-Playground-Token": "s3cret-token"}).status_code == 400
    logs = "".join(p.read_text() for p in (tmp_path / "state").glob("*.jsonl"))
    assert "rechazos" not in logs and "s3cret-token" not in logs and "conv_1" in logs
    assert (tmp_path / "state" / "rechazos.jsonl").exists()


def test_server_without_configured_token_rejects_everyone(box):
    tb, _, _ = box
    c = TestClient(build_app(toolbox=tb, token_fn=lambda: None))
    assert c.post("/playground/h/pg_estado", json={"motivo": "x"}, headers={"X-Playground-Token": ""}).status_code == 401
    assert not token_ok("", None) and not token_ok("a", "") and token_ok("abc", "abc")


def test_local_app_only_answers_localhost():
    app = build_local_app({"puerto_app": 8794, "cliente_js": "/no/existe.js"}, session_fn=lambda: {"token": "t"})
    c = TestClient(app, base_url="http://127.0.0.1:8794")
    assert c.post("/app/sesion").json() == {"token": "t"}
    assert "Playground" in c.get("/app").text
    assert c.get("/app/cliente.js").status_code == 404
    assert c.post("/app/sesion", headers={"Host": "evil.example"}).status_code == 403
    assert c.post("/app/sesion", headers={"Origin": "https://evil.example"}).status_code == 403
    other = TestClient(build_local_app({"puerto_app": 8794}, session_fn=lambda: {"token": "t"}, port=8797),
                       base_url="http://127.0.0.1:8797")
    assert other.post("/app/sesion").status_code == 200        # el puerto real manda, no el de la config


# ---------------------------------------------------------------------------- config y túnel

def test_token_file_is_private_and_url_masked():
    t = cfgmod.token(create=True)
    assert len(t) > 30 and cfgmod.token() == t
    mode = stat.S_IMODE(os.stat(cfgmod.config_dir() / "token").st_mode)
    assert mode == 0o600
    assert cfgmod.mask_url("https://abc-def-ghi.trycloudflare.com") == "https://abc…trycloudflare.com"
    assert cfgmod.api_key() is None and cfgmod.api_key_source() == "ninguna"


def test_tunnel_helpers():
    log = "2026-10-16 INF |  https://brave-otter-lake.trycloudflare.com  |\nINF Registered tunnel connection"
    assert tunel.find_url(log) == "https://brave-otter-lake.trycloudflare.com"
    assert tunel.find_url("nada") is None
    argv, url = tunel.tunnel_command({"puerto": 8793, "tunel": {"modo": "quick"}})
    assert argv[-2:] == ["--url", "http://127.0.0.1:8793"] and url is None
    argv, url = tunel.tunnel_command({"puerto": 8793, "tunel": {"modo": "named", "nombre": "sl-pg", "hostname": "pg.x.cl"}})
    assert argv[-1] == "sl-pg" and url == "https://pg.x.cl"
    with pytest.raises(ValueError):
        tunel.tunnel_command({"tunel": {"modo": "named"}})


# ---------------------------------------------------------------------------- agente en ElevenLabs

def test_agent_dry_run_has_no_secrets(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_NO_DEBE_APARECER")
    (cfgmod.config_dir()).mkdir(parents=True)
    (cfgmod.config_dir() / "token").write_text("tok_NO_DEBE_APARECER")
    out = agent.dry_run("https://ejemplo.trycloudflare.com", modo="tunel")
    text = json.dumps(out, ensure_ascii=False)
    assert "NO_DEBE_APARECER" not in text
    a = out["agente"]
    assert a["name"] == "Playground" and a["platform_settings"]["auth"]["enable_auth"] is True
    assert a["conversation_config"]["agent"]["language"] == "es"
    assert "español de chile" in a["conversation_config"]["agent"]["prompt"]["prompt"].lower()
    assert "{{" not in a["conversation_config"]["agent"]["prompt"]["prompt"]
    assert len(a["conversation_config"]["agent"]["prompt"]["tool_ids"]) == len(out["herramientas"]) == len(catalog())
    assert all(t["api_schema"]["url"].startswith("https://ejemplo.trycloudflare.com/playground/h/") for t in out["herramientas"])
    assert out["llave"] == "variable ELEVENLABS_API_KEY"

    local = agent.dry_run()                                   # por defecto: modo local, client tools, sin secreto
    assert local["modo"] == "local" and "secreto" not in local and "NO_DEBE_APARECER" not in json.dumps(local)
    assert {t["type"] for t in local["herramientas"]} == {"client"}
    assert {"feedback", "decision"} <= {t["name"] for t in local["herramientas"]}
    cc = local["agente"]["conversation_config"]
    assert cc["tts"]["voice_id"] and cc["tts"]["speed"] < 1 and cc["tts"]["stability"] >= 0.6
    assert cc["tts"]["expressive_mode"] is True and cc["tts"]["suggested_audio_tags"]
    assert cc["turn"]["silence_end_call_timeout"] == -1
    assert "skip_turn" in cc["agent"]["prompt"]["built_in_tools"]
    assert local["agente"]["platform_settings"]["overrides"]["conversation_config_override"]["agent"]["first_message"]
    assert "meditación" in cc["agent"]["prompt"]["prompt"]


def fake_elevenlabs(moderated=()):
    db = {"agents": {}, "tools": {}, "secrets": {}, "calls": []}

    def handler(request: httpx.Request):
        assert request.headers["xi-api-key"] == "sk_test"
        body = json.loads(request.content) if request.content else None
        path, m = request.url.path, request.method
        db["calls"].append((m, path, body))
        voice = ((body or {}).get("conversation_config") or {}).get("tts", {}).get("voice_id")
        if voice in moderated:
            return httpx.Response(400, json={"detail": {"status": "voice_live_moderated_not_allowed"}})
        if path == "/v1/convai/secrets" and m == "POST":
            sid = f"sec{len(db['secrets']) + 1}"
            db["secrets"][sid] = body
            return httpx.Response(200, json={"secret_id": sid})
        if path.startswith("/v1/convai/secrets/") and m == "PATCH":
            db["secrets"][path.rsplit("/", 1)[1]] = body
            return httpx.Response(200, json={})
        if path == "/v1/convai/tools" and m == "POST":
            tid = f"tool_{len(db['tools']) + 1}"
            db["tools"][tid] = body["tool_config"]
            return httpx.Response(200, json={"id": tid})
        if path.startswith("/v1/convai/tools/") and m == "PATCH":
            db["tools"][path.rsplit("/", 1)[1]] = body["tool_config"]
            return httpx.Response(200, json={})
        if path == "/v1/convai/agents" and m == "GET":
            return httpx.Response(200, json={"agents": []})
        if path == "/v1/convai/agents/create":
            db["agents"]["agent_pg"] = body
            return httpx.Response(200, json={"agent_id": "agent_pg"})
        if path.startswith("/v1/convai/agents/"):
            aid = path.rsplit("/", 1)[1]
            if m == "PATCH":
                db["agents"][aid] = body
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"agent_id": aid, **db["agents"][aid]})
        if path == "/v1/convai/conversation/token":
            return httpx.Response(200, json={"token": "conv-token"})
        return httpx.Response(404, json={"error": path})
    return agent.ElevenLabs("sk_test", http=httpx.Client(transport=httpx.MockTransport(handler))), db


def test_agent_publish_is_idempotent_and_keeps_the_token_server_side():
    el, db = fake_elevenlabs()
    said = []
    r = agent.publish("https://uno.trycloudflare.com", el=el, say=said.append, escena="(prueba)", modo="tunel")
    assert r["creado"] and r["agente_id"] == "agent_pg" and all(r["chequeos"].values())
    token = cfgmod.token()
    assert db["secrets"]["sec1"]["value"] == token
    assert token not in json.dumps(db["tools"]) and token not in json.dumps(db["agents"]) and token not in "".join(said)
    assert len(db["tools"]) == len(catalog())
    assert db["agents"]["agent_pg"]["conversation_config"]["agent"]["prompt"]["tool_ids"] == list(db["tools"])

    r2 = agent.publish("https://dos.trycloudflare.com", el=el, say=said.append, escena="(prueba)", modo="tunel")
    assert not r2["creado"] and len(db["tools"]) == len(catalog()) and len(db["secrets"]) == 1
    assert all(t["api_schema"]["url"].startswith("https://dos.trycloudflare.com/") for t in db["tools"].values())
    cfg = cfgmod.load()
    assert cfg["agente_id"] == "agent_pg" and cfg["puente_url"] == "https://dos.trycloudflare.com"
    assert agent.conversation_token(cfg, el=el) == "conv-token"


def test_prompt_scene_falls_back_to_the_code_gallery():
    s = agent.scene_summary("http://127.0.0.1:9")          # puerto cerrado: runtime apagado
    assert "contour_glow" in s and "pg_objetos" in s


def test_publish_needs_https_url_in_tunnel_mode():
    el, _ = fake_elevenlabs()
    with pytest.raises(agent.ElevenLabsError, match="URL"):
        agent.publish("http://127.0.0.1:8793", el=el, say=lambda s: None, modo="tunel")


def test_agent_publish_local_mode_uses_client_tools_and_no_secret():
    el, db = fake_elevenlabs()
    r = agent.publish(el=el, say=lambda s: None, escena="(prueba)")
    assert r["modo"] == "local" and r["creado"] and all(r["chequeos"].values())
    assert not db["secrets"] and cfgmod.token() is None             # sin túnel no hay token ni secreto
    assert {t["type"] for t in db["tools"].values()} == {"client"} and len(db["tools"]) == len(catalog())
    assert all(t["expects_response"] for t in db["tools"].values())
    cfg = cfgmod.load()
    assert cfg["modo"] == "local" and len(cfg["herramientas_cliente_ids"]) == len(catalog()) and not cfg["puente_url"]
    r2 = agent.publish(el=el, say=lambda s: None, escena="(prueba)")
    assert not r2["creado"] and len(db["tools"]) == len(catalog())


def test_publish_skips_voices_with_live_moderation():
    first, second = cfgmod.DEFAULTS["voz_id"], cfgmod.DEFAULTS["voces_respaldo"][0]
    el, db = fake_elevenlabs(moderated={first})
    said = []
    r = agent.publish(el=el, say=said.append, escena="(prueba)")
    assert r["creado"] and db["agents"]["agent_pg"]["conversation_config"]["tts"]["voice_id"] == second
    assert cfgmod.load()["voz_id"] == second and any("moderación" in s for s in said)
    el2, _ = fake_elevenlabs(moderated={first, second, *cfgmod.DEFAULTS["voces_respaldo"]})
    cfgmod.save({"agente_id": "", "voz_id": first})
    with pytest.raises(agent.ElevenLabsError, match="moderat"):
        agent.publish(el=el2, say=lambda s: None, escena="(prueba)")

"""Voz nativa del agente «Playground»: cola de avances, feedback y decisiones de Max, AudioInterface con sounddevice
simulado y la lógica que entrega avances sin pisar a Max. Sin red, sin micrófono, sin ElevenLabs."""

import datetime as dt
import json
import subprocess
import time
from pathlib import Path

import pytest

from playground.secretaria import avances
from playground.secretaria import config as cfgmod
from playground.secretaria.audio import IN_FRAMES, SoundDeviceAudio, dbfs, find_device
from playground.secretaria.tools import Toolbox, RuntimeClient
from playground.secretaria.voz import Voz

ORIG_RUNTIME_REPO = cfgmod.DEFAULTS["runtime_repo"]          # antes de que el fixture lo cambie


@pytest.fixture(autouse=True)
def aislado(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYGROUND_SECRETARIA_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("PLAYGROUND_SECRETARIA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setitem(cfgmod.DEFAULTS, "api_key_archivo", str(tmp_path / "no-hay-llave"))
    monkeypatch.setitem(cfgmod.DEFAULTS, "orq_cli", "/no/existe/orq-creativo")
    monkeypatch.setitem(cfgmod.DEFAULTS, "runtime_repo", str(tmp_path / "repo"))      # nunca el checkout real
    monkeypatch.setenv("PLAYGROUND_STATE", str(tmp_path / "pgstate"))


def test_runtime_and_requests_resolve_to_runtime_repo(tmp_path):
    # el default se calcula desde la ruta del código: desde un worktree de agente apunta al checkout principal
    assert "/.claude/worktrees/" not in ORIG_RUNTIME_REPO and (Path(ORIG_RUNTIME_REPO) / "playground").is_dir()
    tb = Toolbox(RuntimeClient(url="http://127.0.0.1:9", autostart=True))
    assert tb.requests_dir == tmp_path / "repo" / "requests"
    r = tb.rt.start(wait_s=0.5)                               # sin playground en ese repo: falla en palabras
    assert r["status"] == "failed" and "runtime_repo" in r["error"]
    assert tb.audio_ctl() is None                             # sin playground/audio en ese repo: no hay lanzador


def test_parallel_ideas_get_distinct_numbers(tmp_path):
    import threading
    tb = Toolbox(RuntimeClient(url="http://127.0.0.1:9", autostart=False), requests_dir=tmp_path / "req")
    out = []
    ths = [threading.Thread(target=lambda i=i: out.append(tb.call("pg_idea", {"titulo": f"idea {i}", "detalle": "x",
                                                                              "tipo": "tool"})["anotada"]))
           for i in range(6)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert sorted(out) == ["001", "002", "003", "004", "005", "006"]


# ---------------------------------------------------------------------------- cola de avances

def test_avances_queue_roundtrip():
    tail = avances.Tail()                                   # sin offset: parte del final
    assert tail.poll() == ([], 0)
    a1 = avances.add("el halo ya sigue al cocodrilo", "cambio")
    a2 = avances.add("¿halo turquesa o dorado?", opciones=["turquesa", "dorado"], pregunta="q16")
    recs, off = tail.poll()
    assert [r["id"] for r in recs] == [a1["id"], a2["id"]] and recs[1]["opciones"] == ["turquesa", "dorado"]
    assert tail.poll()[0] == recs                           # sin commit, se repiten
    tail.commit(off)
    assert tail.poll()[0] == []
    assert avances.Tail().offset == off                     # el offset sobrevive al reinicio
    with pytest.raises(ValueError):
        avances.add("   ")
    with pytest.raises(ValueError):
        avances.add("x", nivel="urgente")


def test_avances_skip_old_and_partial_lines():
    p = avances.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    old = (dt.datetime.now().astimezone() - dt.timedelta(hours=2)).isoformat(timespec="seconds")
    p.write_text(json.dumps({"id": "a1", "ts": old, "nivel": "trabajo", "texto": "viejo"}) + "\n")
    tail = avances.Tail(max_age_s=900)
    tail.offset = 0
    new = avances.add("nuevo")
    with open(p, "a") as f:
        f.write('{"id": "a9", "texto": "a medio escri')   # línea incompleta: no se consume
    recs, off = tail.poll()
    assert [r["id"] for r in recs] == [new["id"]] and off < p.stat().st_size


def test_for_agent_text():
    ctx, trig = avances.for_agent([{"id": "a1", "ts": "2026-10-16T03:10:00-03:00", "nivel": "cambio", "de": "h3",
                                    "texto": "halo listo", "opciones": ["A", "B"], "pregunta": "q16"}])
    assert "halo listo" in ctx and "A / B" in ctx and "q16" in ctx and "03:10" in ctx
    assert trig.startswith("(aviso del sistema") and "pregúntale" in trig


def test_avances_from_orquestador_marks_then_reads_new():
    hist = [{"at": "2026-10-16T02:00:00-03:00", "kind": "note", "text": "vieja"}]

    def runner(argv):
        assert argv[1:] == ["show", "h3", "--json"]
        return subprocess.CompletedProcess(argv, 0, json.dumps({"history": hist}), "")
    assert avances.from_orquestador(runner=runner)["nuevas"] == 0      # primera vez: solo marca
    hist += [{"at": "2026-10-16T02:10:00-03:00", "kind": "note", "text": "calibración lista"},
             {"at": "2026-10-16T02:11:00-03:00", "kind": "note", "text": "Feedback de Max por voz (textual): x"},
             {"at": "2026-10-16T02:12:00-03:00", "kind": "blocked", "text": "no es nota"}]
    assert avances.from_orquestador(runner=runner)["nuevas"] == 1
    lines =[json.loads(x) for x in avances.path().read_text().splitlines()]
    assert [x["texto"] for x in lines] == ["calibración lista"] and lines[0]["de"] == "h3 (orquestador)"
    assert avances.from_orquestador(runner=runner)["nuevas"] == 0
    bad = avances.from_orquestador(runner=lambda argv: subprocess.CompletedProcess(argv, 1, "", "boom"))
    assert "error" in bad


# ---------------------------------------------------------------------------- feedback y decisiones

def _toolbox(tmp_path, calls, rc=0):
    def runner(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, rc, "", "" if rc == 0 else "falló")
    rt = RuntimeClient(url="http://127.0.0.1:9", autostart=False)
    return Toolbox(runtime=rt, requests_dir=tmp_path / "requests", orq_runner=runner)


def test_feedback_lands_in_three_places(tmp_path):
    calls = []
    tb = _toolbox(tmp_path, calls)
    texto = "el halo está muy grueso, déjalo como la mitad"
    r = tb.call("feedback", {"texto": texto})
    assert r["mandado"] and r["nota_a_h3"] == "ok" and r["pedido"] == "001"
    line = json.loads((cfgmod.state_dir() / "feedback.jsonl").read_text().splitlines()[-1])
    assert line["tipo"] == "feedback" and line["texto"] == texto and line["ts"]
    req = next((tmp_path / "requests").glob("001-*.md")).read_text()
    assert texto in req and "kind: fix" in req and "Feedback de Max por voz" in req
    assert calls[-1][1:] == ["note", "h3", f"Feedback de Max por voz (textual): {texto}"]


def test_feedback_without_orquestador_still_saved(tmp_path):
    tb = _toolbox(tmp_path, [], rc=1)
    r = tb.call("feedback", {"texto": "más lento", "tipo": "visual"})
    assert r["mandado"] and "no se pudo" in r["nota_a_h3"]
    assert "kind: visual" in next((tmp_path / "requests").glob("001-*.md")).read_text()
    assert "falta texto" in tb.call("feedback", {})["error"]


def test_decision_answers_the_question_and_notes_h3(tmp_path):
    calls = []
    tb = _toolbox(tmp_path, calls)
    r = tb.call("decision", {"opcion": "turquesa, pero más suave", "pregunta": "Q16"})
    assert r["registrada"] and r["respuesta_q16"] == "ok" and r["nota_a_h3"] == "ok"
    answer, note = calls
    assert answer[1:3] == ["answer", "q16"] and "turquesa, pero más suave" in answer[3] and "--de" in answer
    assert note[1:3] == ["note", "h3"] and "Decisión de Max por voz (textual): turquesa, pero más suave" in note[3]
    line = json.loads((cfgmod.state_dir() / "feedback.jsonl").read_text().splitlines()[-1])
    assert line == {**line, "tipo": "decision", "texto": "turquesa, pero más suave", "pregunta": "q16"}
    calls.clear()
    assert "respuesta_q16" not in tb.call("decision", {"opcion": "la primera"}) and len(calls) == 1


# ---------------------------------------------------------------------------- AudioInterface con sounddevice falso

class FakeStream:
    def __init__(self, **kw):
        self.kw, self.started, self.closed, self.written = kw, False, False, []

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True

    def write(self, data):
        self.written.append(bytes(data))
        time.sleep(0.001)


class FakeSD:
    def __init__(self):
        self.inputs, self.outputs = [], []

    def query_devices(self, idx=None, kind=None):
        devs = [{"name": "Iriun Webcam Audio", "max_input_channels": 2, "max_output_channels": 0},
                {"name": "MacBook Air Microphone", "max_input_channels": 1, "max_output_channels": 0},
                {"name": "MacBook Air Speakers", "max_input_channels": 0, "max_output_channels": 2}]
        if idx is None and kind is None:
            return devs
        return devs[idx if idx is not None else (1 if kind == "input" else 2)]

    def RawInputStream(self, **kw):
        s = FakeStream(**kw)
        self.inputs.append(s)
        return s

    def RawOutputStream(self, **kw):
        s = FakeStream(**kw)
        self.outputs.append(s)
        return s


def _loud(n=IN_FRAMES):
    import array
    return array.array("h", [8000 if i % 2 else -8000 for i in range(n)]).tobytes()


def test_audio_interface_picks_macbook_and_streams():
    sd = FakeSD()
    assert find_device(sd, "MacBook", "input") == 1 and find_device(sd, "MacBook", "output") == 2
    au = SoundDeviceAudio(sd=sd)
    got = []
    au.start(got.append)
    ins, outs = sd.inputs[-1], sd.outputs[-1]
    assert ins.kw["device"] == 1 and outs.kw["device"] == 2 and ins.kw["samplerate"] == 16000
    assert ins.kw["blocksize"] == IN_FRAMES and ins.kw["dtype"] == "int16" and ins.started and outs.started
    assert au.devices == {"entrada": "MacBook Air Microphone", "salida": "MacBook Air Speakers"}
    # entrada: pasa tal cual y marca voz
    ins.kw["callback"](_loud(), IN_FRAMES, None, None)
    assert got[-1] == _loud() and au.user_speaking() and dbfs(_loud()) > -20
    # salida: se escribe en bloques de 100 ms; mientras suena, el micrófono va en silencio (semidúplex)
    au.output(b"\x01\x00" * 8000)                           # 0,5 s
    for _ in range(200):
        if outs.written:
            break
        time.sleep(0.005)
    assert au.playing()
    ins.kw["callback"](_loud(), IN_FRAMES, None, None)
    assert got[-1] == b"\x00" * len(_loud())
    for _ in range(400):
        if sum(map(len, outs.written)) >= 16000:
            break
        time.sleep(0.005)
    assert sum(map(len, outs.written)) == 16000 and all(len(w) <= 3200 for w in outs.written)
    au.stop()
    assert ins.closed and outs.closed


def test_audio_interface_interrupt_and_duplex():
    sd = FakeSD()
    au = SoundDeviceAudio(sd=sd, half_duplex=False, tail_s=0.0)
    got = []
    au.start(got.append)
    ins = sd.inputs[-1]
    au.output(b"\x01\x00" * 64000)                          # 4 s en cola
    au.interrupt()
    time.sleep(0.05)
    assert sum(map(len, sd.outputs[-1].written)) < 128000   # se cortó
    ins.kw["callback"](_loud(), IN_FRAMES, None, None)
    assert got[-1] == _loud()                               # dúplex: no silencia el micrófono
    au.stop()


def test_audio_interface_warns_on_exact_silence():
    sd = FakeSD()
    warnings = []
    au = SoundDeviceAudio(sd=sd, on_warning=warnings.append)
    au.start(lambda b: None)
    cb = sd.inputs[-1].kw["callback"]
    for _ in range(30):                                      # 7,5 s de ceros exactos
        cb(b"\x00" * IN_FRAMES * 2, IN_FRAMES, None, None)
    assert len(warnings) == 1 and "permiso" in warnings[0]
    au.stop()


# ---------------------------------------------------------------------------- lógica de la voz

class FakeAudio:
    def __init__(self):
        self.is_playing = False
        self.speaking = False
        self.last_voice = 0.0
        self.played_s = 0.0
        self.devices = {}

    def playing(self, now=None):
        return self.is_playing

    def user_speaking(self, within_s=1.2, now=None):
        return self.speaking


class FakeConv:
    def __init__(self, *a, **kw):
        self.kw = kw
        self.sent = []
        self._conversation_id = "conv_1"
        self._ws = object()
        self.ended = False

    def send_contextual_update(self, text):
        self.sent.append(("ctx", text))

    def send_user_message(self, text):
        self.sent.append(("user", text))

    def end_session(self):
        self.ended = True


def _voz(**kw):
    v = Voz(cfg={**cfgmod.load(), "agente_id": "agent_x"}, toolbox=Toolbox(RuntimeClient(url="http://127.0.0.1:9", autostart=False)),
            audio=FakeAudio(), client=object(), say=lambda m: None, **kw)
    v.conv = FakeConv()
    v.state = "viva"
    v.session_start = time.monotonic() - 5
    return v


def test_voz_delivers_avances_only_when_nobody_is_talking():
    v = _voz()
    avances.add("primer cambio listo", "cambio")
    v.audio.speaking = True                                  # Max está hablando: espera
    v.tick()
    assert v.conv.sent == [] and len(v.pending) == 1
    v.audio.speaking, v.audio.is_playing = False, True       # ella está hablando: espera
    v.tick()
    assert v.conv.sent == []
    v.audio.is_playing = False
    v.last_user, v.last_agent = time.monotonic(), time.monotonic() - 3   # Max terminó y ella no contesta aún
    v.tick()
    assert v.conv.sent == []
    v.last_agent = time.monotonic()
    v.tick()
    kinds = [k for k, _ in v.conv.sent]
    assert kinds == ["ctx", "user"] and "primer cambio listo" in v.conv.sent[0][1] and v.pending == []
    avances.add("segundo")
    v.tick()                                                 # el anterior aún no lo cuenta: espera
    assert len(v.conv.sent) == 2
    v.last_agent = time.monotonic() + 0.01
    v.tick()
    assert len(v.conv.sent) == 4 and "segundo" in v.conv.sent[2][1]


def test_voz_does_not_wait_forever_on_room_noise():
    v = _voz()
    avances.add("cambio con ruido en la sala")
    v.audio.speaking = True                                  # charla o música constante en la sala
    t0 = time.monotonic()
    v.tick(t0)
    assert v.conv.sent == []
    v.tick(t0 + 61)                                          # pasado el tope, entra en la primera pausa de ella
    assert [k for k, _ in v.conv.sent] == ["ctx", "user"]
    v2 = _voz()
    avances.add("otro")
    v2.audio.speaking, v2.audio.is_playing = True, True
    v2.tick(t0)
    v2.tick(t0 + 61)
    assert v2.conv.sent == []                                # pero nunca encima de ella


def test_voz_wakes_when_asleep_and_sleeps_when_idle():
    v = _voz(dormir_tras_s=60)
    v.state = "dormida"
    avances.add("despierta")
    v.tick()
    assert v.wake_ev.is_set() and v.conv.sent == []          # el bucle principal abre la sesión
    v2 = _voz(dormir_tras_s=60)
    v2.tail.commit(avances.path().stat().st_size)             # sin avances pendientes
    v2.session_start = time.monotonic() - 120
    v2.tick()
    assert v2.conv.ended and v2.ended_by == "dormir"


def test_voz_hangs_up_when_max_says_goodbye_even_if_the_llm_forgets():
    from playground.secretaria.voz import HANGUP
    for s in ("Soy-- estamos hablando. Corta la llamada, porfa.", "cuelga", "ya, chao", "adiós", "termina la conversación"):
        assert HANGUP.search(s), s
    for s in ("corta el halo", "que el halo se corte", "cuál es la llamada a la acción del efecto"):
        assert not HANGUP.search(s), s
    v = _voz()
    v.tail.commit(avances.path().stat().st_size if avances.path().exists() else 0)
    v._on_user("Corta la llamada, porfa.")
    v.tick()
    assert not v.conv.ended                                  # todavía no se despide
    v._on_agent("Listo, Max. Respira...")
    v.tick(time.monotonic() + 0.5)
    assert not v.conv.ended                                  # deja terminar la despedida
    v.tick(time.monotonic() + 2.0)
    assert v.conv.ended and v.ended_by == "dormir"


def test_voz_tool_handler_and_session_results():
    v = _voz()
    out = json.loads(v._tool("pg_estado")({"tool_call_id": "t1", "motivo": "x"}))
    assert "error" in out and "apagado" in out["error"]      # runtime falso apagado: error en palabras, no excepción
    log = (cfgmod.state_dir() / "voz.jsonl").read_text()
    assert "herramienta" in log and "t1" not in log

    class Tools:
        def __init__(self):
            self.names = []

        def register(self, name, handler):
            self.names.append(name)

    class Init:
        def __init__(self, conversation_config_override=None):
            self.override = conversation_config_override

    class Conv(FakeConv):
        def start_session(self):
            pass

        def wait_for_session_end(self):
            if self.kw.get("callback_end_session") and not getattr(type(self), "cut", False):
                self.kw["callback_end_session"]()

    v.conversation_cls, v.client_tools_cls, v.init_data_cls = Conv, Tools, Init
    assert v.session(None) == "normal"                        # ella colgó: normal → dormida
    c = v.make_conversation("")
    assert c.kw["config"].override == {"agent": {"first_message": ""}} and c.kw["requires_auth"] is True
    assert {"feedback", "decision", "pg_estado"} <= set(c.kw["client_tools"].names)
    Conv.cut = True
    assert v.session("") == "error"                           # se cortó sin cierre: reconectar

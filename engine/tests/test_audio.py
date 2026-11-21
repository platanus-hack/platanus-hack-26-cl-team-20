"""Audio reactivo sin hardware: análisis con señales sintéticas, AGC, tempo, runtime (`audio`, bindings),
efectos con audio simulado, formas del escenario y el bucle de captura con un sounddevice falso."""

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from playground.audio import AudioAnalyzer, audio_level, audio_mode
from playground.runtime import Runtime, build_app

SR = 48000
RNG = np.random.default_rng(7)


def env_hits(t, hit_times, attack=0.01, decay=0.25):
    env = np.zeros_like(t)
    for h in hit_times:
        m = t >= h
        dt = t[m] - h
        env[m] = np.maximum(env[m], np.minimum(1.0, dt / attack) * np.exp(-dt / decay))
    return env


def bass_hits(secs=8.0, bpm=120.0, freq=55.0, amp=0.5, noise=1e-4, start=0.5):
    t = np.arange(int(SR * secs)) / SR
    hits = np.arange(start, secs - 0.2, 60.0 / bpm)
    x = amp * env_hits(t, hits) * np.sin(2 * np.pi * freq * t) + noise * RNG.standard_normal(len(t))
    return x.astype(np.float32), hits


def analyze(x, block=512, **kw):
    an = AudioAnalyzer(SR, **kw)
    frames = []
    for i in range(0, len(x), block):           # bloques que no calzan con el hop: prueba el buffer interno
        frames += an.process(x[i:i + block])
    return frames


def col(frames, k):
    return np.array([f[k] for f in frames], float)


# ---------------------------------------------------------------------------------------------- análisis

def test_55hz_hits_bands_and_onsets():
    x, hits = bass_hits()
    fr = analyze(x)
    assert len(fr) == len(x) // AudioAnalyzer(SR).hop
    ts = col(fr, "t")
    on = ts[[f["onset"] for f in fr]]
    assert len(on) == len(hits) and fr[-1]["onsets"] == len(hits)
    delays = [(on[on >= h - 1e-3] - h).min() * 1000 for h in hits]
    assert max(delays) <= 45, delays                      # golpe detectado a lo más 2 cuadros después
    sub, bass, mid, high = (col(fr, k) for k in ("sub", "bass", "mid", "high"))
    win = [(ts >= h) & (ts < h + 0.15) for h in hits]
    assert np.mean([sub[w].max() for w in win]) > 0.85   # 55 Hz es sub (y roza el borde del bajo)
    assert np.mean([bass[w].max() for w in win]) > 0.6
    sustain = np.any([(ts >= h + 0.1) & (ts < h + 0.45) for h in hits], axis=0)
    assert mid[sustain].mean() < 0.1 and high.max() < 0.1  # el seno sostenido no se fuga a medios ni agudos
    beat = col(fr, "beat")
    assert beat.max() == 1.0 and beat[sustain].mean() < 0.3  # flash corto


def test_bands_follow_their_content():
    """Bajo en los tiempos, hi-hat (5 kHz) en los contratiempos: cada banda late en su lugar."""
    secs = 8.0
    t = np.arange(int(SR * secs)) / SR
    beats = np.arange(0.5, secs - 0.3, 0.5)
    off = beats + 0.25
    hat = env_hits(t, off, 0.001, 0.03) * RNG.standard_normal(len(t)) * np.sin(2 * np.pi * 5000 * t)
    x = 0.5 * env_hits(t, beats) * np.sin(2 * np.pi * 55 * t) + 0.08 * hat + 1e-4 * RNG.standard_normal(len(t))
    fr = analyze(x.astype(np.float32))
    ts, high, sub = col(fr, "t"), col(fr, "high"), col(fr, "sub")
    at = lambda arr, times: np.mean([arr[(ts >= h) & (ts < h + 0.06)].max() for h in times[2:]])
    assert at(high, off) > 0.5 and at(high, off) > 4 * at(high, beats)
    assert at(sub, beats) > 0.85 and at(sub, beats) > at(sub, off)
    assert fr[-1]["onsets"] == len(beats)                 # los hi-hats no cuentan como golpes del bajo


def test_no_false_onsets_on_steady_tone_or_noise():
    t = np.arange(SR * 6) / SR
    steady = analyze((0.3 * np.sin(2 * np.pi * 55 * t) + 1e-4 * RNG.standard_normal(len(t))).astype(np.float32))
    noise = analyze((0.01 * RNG.standard_normal(len(t))).astype(np.float32))
    assert steady[-1]["onsets"] == 0 and noise[-1]["onsets"] == 0
    assert np.mean(col(noise, "bass")[100:]) < 0.1        # ruido de fondo ≈ 0, no se infla a 1


def test_agc_same_range_without_gain():
    """Micrófono lejano (-46 dB) o línea caliente (-6 dB): mismo rango 0..1 y mismos golpes."""
    out = []
    for amp in (0.005, 0.5):
        x, hits = bass_hits(amp=amp, noise=amp * 2e-4)
        fr = analyze(x)
        ts, sub = col(fr, "t"), col(fr, "sub")
        out.append((np.mean([sub[(ts >= h) & (ts < h + 0.15)].max() for h in hits]), fr[-1]["onsets"], len(hits)))
    (q_peak, q_on, n), (l_peak, l_on, _) = out
    assert q_peak > 0.85 and l_peak > 0.85 and q_on == l_on == n


def test_agc_recovers_after_level_drop():
    loud, hits = bass_hits(secs=6, amp=0.5)
    quiet, _ = bass_hits(secs=20, amp=0.01, noise=2e-6)
    fr = analyze(np.concatenate([loud, quiet]))
    ts, sub = col(fr, "t"), col(fr, "sub")
    late = [h + 6 for h in np.arange(14.0, 19.5, 0.5)]    # 8–13 s después de bajar 34 dB
    assert np.mean([sub[(ts >= h) & (ts < h + 0.15)].max() for h in late]) > 0.7


@pytest.mark.parametrize("bpm", [96.0, 128.0, 150.0])
def test_tempo_estimate(bpm):
    x, _ = bass_hits(secs=12, bpm=bpm, freq=70.0)
    assert abs(analyze(x)[-1]["bpm"] - bpm) < 2.5


def test_audio_level_helpers():
    a = {"sub": 0.2, "bass": 0.6, "beat": 0.9, "mid": 0.1}
    assert audio_mode("") is None and audio_mode("off") is None and audio_mode(False) is None
    assert audio_mode(True) == "bass" and audio_mode("rms") == "rms"
    assert audio_level(a, "") == 0.0 and audio_level(a, "bass") == 0.6 and audio_level(a, "low") == 0.6
    assert audio_level(a, "kick") == 0.9 and audio_level(a, "bass", gain=3) == 1.0 and audio_level(None, "bass") == 0.0


# ---------------------------------------------------------------------------------------------- runtime

def _rt(tmp_path, size=(960, 540)):
    return Runtime(canvas_size=size, requests_dir=tmp_path / "requests")


def _send(rt, **f):
    rt.command("audio", {"features": {"sub": 0.0, "bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0,
                                      "onsets": 0, "bpm": 0.0, "tc": time.time(), **f}})


def test_runtime_audio_state_and_staleness(tmp_path, monkeypatch):
    rt = _rt(tmp_path)
    assert rt.scene.audio["live"] is False and rt.command("audio")["live"] is False
    _send(rt, bass=0.8, beat=1.0, onsets=3, bpm=121.0)
    rt.tick(0.03)
    a = rt.scene.audio
    assert a["live"] and a["bass"] == 0.8 and a["onsets"] == 3 and a["bpm"] == 121.0
    st = rt.state()["audio"]
    assert st["live"] and st["packets"] == 1 and st["lag_ms"] is not None and st["lag_ms"] < 1000
    monkeypatch.setattr(time, "time", lambda t0=time.time(): t0 + 1.0)    # 1 s sin datos
    for _ in range(10):
        rt.tick(0.03)
    a = rt.scene.audio
    assert a["live"] is False and a["bass"] < 0.1 and a["bpm"] == 121.0
    monkeypatch.undo()
    rt.command("audio", {"stop": True}); rt.tick(0.03)
    assert rt.scene.audio["live"] is False


def test_simulated_beat_counts_and_decays(tmp_path):
    rt = _rt(tmp_path)
    rt.command("audio", {"features": {"bass": 0.9, "beat": 1.0}, "hold": 2.0})
    rt.tick(0.03)
    assert rt.scene.audio["onsets"] == 1 and rt.scene.audio["beat"] == 1.0
    time.sleep(0.12)
    for _ in range(5):
        rt.tick(0.03)
    assert rt.scene.audio["live"] and rt.scene.audio["bass"] == 0.9 and rt.scene.audio["beat"] < 0.4


def test_audio_over_http(tmp_path):
    from fastapi.testclient import TestClient
    rt = _rt(tmp_path)
    c = TestClient(build_app(rt))
    r = c.post("/api/command", json={"cmd": "audio", "args": {"features": {"bass": 0.5, "onsets": 2, "tc": time.time()},
                                                              "meta": {"device": "prueba"}}}).json()
    assert r["ok"]
    rt.tick(0.03)
    s = c.post("/api/command", json={"cmd": "audio"}).json()["result"]
    assert s["live"] and s["features"]["bass"] == 0.5 and s["meta"]["device"] == "prueba"
    assert c.get("/api/state").json()["audio"]["features"]["onsets"] == 2
    assert "audio" in c.get("/api/schema").json()


def test_audio_bindings_drive_any_param(tmp_path):
    rt = _rt(tmp_path)
    e = rt.spawn("contour_glow", "cocodrilo")
    rt.command("bind", {"control": "audio/bass", "target": f"effect:{e['id']}:width_mm", "lo": 10, "hi": 90})
    rt.command("bind", {"control": "audio/bpm", "target": "beat:bpm"})
    _send(rt, bass=0.5, bpm=132.0)
    rt.tick(0.03)
    assert rt.instances[e["id"]].p["width_mm"] == pytest.approx(50.0)
    assert rt.scene.beat["bpm"] == 132.0
    assert len(rt.state()["audio"]["binds"]) == 2
    with pytest.raises(KeyError):
        rt.command("bind", {"control": "audio/nada", "target": "beat:bpm"})
    assert rt.command("unbind", {"control": "audio/bass"})["removed"] == 1


# ---------------------------------------------------------------------------------------------- efectos

def _lit(frame):
    return int(frame.astype(np.int32).sum())


def test_bass_pulse_follows_bass_and_flashes(tmp_path):
    rt = _rt(tmp_path)
    rt.spawn("bass_pulse", params={"scan": str(tmp_path / "sin_escaneo")})
    _send(rt, bass=0.0); quiet = _lit(rt.tick(0.03))
    _send(rt, bass=1.0); loud = _lit(rt.tick(0.03))
    _send(rt, bass=1.0, beat=1.0, onsets=1); flash = _lit(rt.tick(0.03))
    assert 0 < quiet < loud < flash
    assert loud > 2 * quiet


def test_bass_pulse_colors_rgb_outside(tmp_path):
    rt = _rt(tmp_path)
    rt.spawn("bass_pulse", params={"halo": False, "key_color": [255, 0, 0], "scan": str(tmp_path / "x")})
    _send(rt, bass=1.0)
    f = rt.tick(0.03)
    lit = f[f.sum(axis=2) > 0]
    assert len(lit) and lit[:, 2].mean() > 5 * max(1.0, lit[:, 0].mean())    # rojo RGB → canal R (BGR[2])


def test_bass_ripple_rings_on_onsets(tmp_path):
    rt = _rt(tmp_path)
    rt.spawn("bass_ripple", params={"scan": str(tmp_path / "x")})
    _send(rt, bass=0.3, onsets=0); rt.tick(0.03)
    base = _lit(rt.tick(0.03))
    inst = next(iter(rt.instances.values()))
    assert inst.rings == []
    _send(rt, bass=0.9, beat=1.0, onsets=1); rt.tick(0.03)
    assert len(inst.rings) == 1
    for _ in range(5):
        _send(rt, bass=0.3, onsets=1); f = rt.tick(0.05)
    assert len(inst.rings) == 1 and _lit(f) > base               # el anillo crece y suma luz
    _send(rt, bass=0.9, onsets=3); rt.tick(0.03)
    assert len(inst.rings) == 2                                    # un disparo por tick con golpes nuevos


def test_bass_ripple_falls_back_to_beat_without_audio(tmp_path):
    rt = _rt(tmp_path)                                             # demo: beat a 120 BPM, sin audio
    rt.spawn("bass_ripple", params={"scan": str(tmp_path / "x")})
    for _ in range(40):
        rt.tick(1 / 30)
    assert len(next(iter(rt.instances.values())).rings) >= 2


def test_contour_glow_audio_param_off_by_default(tmp_path):
    a, b = _rt(tmp_path), _rt(tmp_path)
    a.spawn("contour_glow", "cocodrilo"); b.spawn("contour_glow", "cocodrilo")
    _send(b, bass=1.0, beat=1.0)                                   # audio presente pero el efecto no lo usa
    assert np.array_equal(a.tick(0.03), b.tick(0.03))
    c = _rt(tmp_path)
    e = c.spawn("contour_glow", "cocodrilo", {"audio": "bass", "layers": 3})
    _send(c, bass=0.0); q = _lit(c.tick(0.0))
    _send(c, bass=1.0); l = _lit(c.tick(0.0))
    assert l > 1.5 * q
    c.set_param(e["id"], "audio", "off"); _send(c, bass=1.0)
    assert c.instances[e["id"]].p["audio"] == "off" and _lit(c.tick(0.0)) > 0


def test_control_glow_audio_param(tmp_path):
    rt = _rt(tmp_path)
    rt.connect_devices("fake")
    e = rt.spawn("control_glow", "ohm", {"audio": "bass", "color": [255, 0, 0]})
    pad = next(c for c, v in rt.devices.devices["ohm"].controls.items() if v.type == "pad")
    rt.command("simulate", {"control": pad, "value": 1.0, "state": "down"})
    _send(rt, bass=0.0); dim = rt.tick(0.03)[..., 2].max()
    _send(rt, bass=1.0); bright = rt.tick(0.03)[..., 2].max()
    assert 0 < dim < bright and bright == 255
    rt.set_param(e["id"], "audio", "")
    _send(rt, bass=0.0)
    assert rt.tick(0.03)[..., 2].max() == 255                      # apagado: color pleno como antes


def test_stage_shapes_from_scan_and_fallback(tmp_path):
    import json
    from playground.stage import stage, FALLBACK
    fb = stage(tmp_path / "nada")
    assert fb["source"] == "fallback" and fb["jogs"] == FALLBACK["jogs"] and len(fb["keys"]) == 23
    d = tmp_path / "scan"; d.mkdir()
    (d / "segments.json").write_text(json.dumps({"ddj": {"jogs": [[100, 200, 50]], "bbox": [0, 0, 10, 10]},
                                                 "circuit": {"bbox": [10, 20, 30, 40]}}))
    (d / "workbench.json").write_text(json.dumps({"buda": {"quad": [[0, 0], [10, 0], [10, 10], [0, 10]]}}))
    s = stage(d)
    assert s["jogs"] == [[100.0, 200.0, 50.0]] and s["from"]["jogs"] == "segments" and s["from"]["buda"] == "workbench"
    assert s["circuit"][2] == [40.0, 60.0] and s["from"]["keys"] == "fallback"
    half = stage(d, (960, 540))
    assert half["jogs"] == [[50.0, 100.0, 25.0]] and half["buda"][2] == [5.0, 5.0]


# ---------------------------------------------------------------------------------------------- captura (sin hardware)

def test_device_preference(monkeypatch):
    from playground.audio import capture
    assert capture.rank("USB Audio CODEC")[1] == "mezcladora" and capture.rank("BlackHole 2ch")[1] == "loopback"
    assert capture.rank("MacBook Air Microphone")[1] == "micrófono" and capture.rank("Background Music (UI Sounds)")[0] == 99
    devs = [{"index": 5, "name": "MacBook Air Microphone"}, {"index": 2, "name": "OsmoPocket3"},
            {"index": 7, "name": "BlackHole 2ch"}, {"index": 9, "name": "QX1222USB"}]
    fake = [{**d, "inputs": 2, "samplerate": 48000, "latency_ms": 5.0, "hostapi": "Core Audio",
             "rank": capture.rank(d["name"])[0], "kind": capture.rank(d["name"])[1]} for d in devs]
    monkeypatch.setattr(capture, "input_devices", lambda refresh=False: sorted(fake, key=lambda d: (d["rank"], d["index"])))
    monkeypatch.setattr(capture, "probe", lambda idx, seconds=0.5: 0.0)        # loopback mudo
    assert capture.choose_device()["index"] == 9                                # la mezcladora primero
    fake[:] = [d for d in fake if d["index"] != 9]
    assert capture.choose_device()["index"] == 5                                # BlackHole mudo → micrófono
    monkeypatch.setattr(capture, "probe", lambda idx, seconds=0.5: 0.2)
    assert capture.choose_device()["index"] == 7                                # BlackHole con señal
    assert capture.choose_device("osmo")["index"] == 2 and capture.choose_device(5)["index"] == 5
    with pytest.raises(RuntimeError):
        capture.choose_device("no existe")


class _FakeStream:
    """InputStream falso: entrega bloques de `hop` de una señal sintética al callback, en otro hilo."""

    def __init__(self, signal, device, channels, samplerate, blocksize, dtype, latency, callback):
        self.x, self.cb, self.n, self.ch = signal, callback, blocksize, channels
        self.latency = 0.012
        self.stop = threading.Event()

    def _feed(self):
        for i in range(0, len(self.x) - self.n, self.n):
            if self.stop.is_set():
                return
            blk = self.x[i:i + self.n] if self.x.ndim == 2 else np.repeat(self.x[i:i + self.n, None], self.ch, axis=1)
            self.cb(blk, self.n, SimpleNamespace(currentTime=1.010, inputBufferAdcTime=1.0), None)
            time.sleep(0.004)

    def __enter__(self):
        threading.Thread(target=self._feed, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.stop.set()


def test_capture_loop_publishes_without_hardware(monkeypatch):
    from playground.audio import capture
    x, hits = bass_hits(secs=3.0, start=0.3)
    sd = SimpleNamespace(InputStream=lambda **kw: _FakeStream(x, **kw))
    monkeypatch.setattr(capture, "_sd", lambda: sd)
    monkeypatch.setattr(capture, "choose_device", lambda spec=None: {"index": 1, "name": "QX1222USB", "inputs": 2,
                                                                     "samplerate": SR, "kind": "mezcladora", "why": "test"})
    sent = []

    class Pub:
        def __init__(self, url):
            self.post_ms, self.ok, self.sent, self.last_error = 0.5, True, 0, ""

        def send(self, args):
            sent.append(args); self.sent += 1; return True

        def close(self):
            pass

    monkeypatch.setattr(capture, "Publisher", Pub)
    out = capture.run(None, "http://x", meter=False, seconds=1.2, log_every=99)
    feats = [a["features"] for a in sent if "features" in a]
    assert out["frames"] >= 100 and out["onsets"] >= 4 and out["in_latency_ms"] == 12.0
    assert set(capture.PUBLISH) <= set(feats[-1]) and feats[-1]["onsets"] >= 4 and "tc" in feats[-1]
    assert any("meta" in a and a["meta"]["device"] == "QX1222USB" for a in sent)
    assert sent[-1] == {"stop": True}                                           # avisa al runtime al salir


# ---------------------------------------------------------------------------------------------- multicanal

def _two_channels(secs=8.0):
    """Canal 1: bajo de 55 Hz en los tiempos. Canal 2: hi-hat (5 kHz) en los contratiempos. Como con paneo duro L/R."""
    t = np.arange(int(SR * secs)) / SR
    beats = np.arange(0.5, secs - 0.3, 0.5)
    off = beats + 0.25
    ch1 = 0.5 * env_hits(t, beats) * np.sin(2 * np.pi * 55 * t)
    from scipy.signal import butter, sosfilt
    hiss = sosfilt(butter(4, 3000, btype="high", fs=SR, output="sos"), RNG.standard_normal(len(t)))  # hi-hat real: sin graves
    ch2 = 0.15 * env_hits(t, off, 0.001, 0.03) * hiss
    x = np.stack([ch1, ch2], axis=1) + 1e-4 * RNG.standard_normal((len(t), 2))
    return x.astype(np.float32), beats, off


def test_multichannel_analysis_separates_channels():
    from playground.audio import MultiAnalyzer
    x, beats, off = _two_channels()
    m = MultiAnalyzer(SR, 2)
    fr = []
    for i in range(0, len(x), 512):
        fr += m.process(x[i:i + 512])
    ts = col(fr, "t")
    c1 = {k: np.array([f["ch"][0][k] for f in fr]) for k in ("bass", "high", "onsets")}
    c2 = {k: np.array([f["ch"][1][k] for f in fr]) for k in ("bass", "high", "onsets")}
    at = lambda arr, times: np.mean([arr[(ts >= h) & (ts < h + 0.08)].max() for h in times[2:]])
    assert c1["onsets"][-1] == len(beats) and c2["onsets"][-1] == 0      # golpes del bajo solo en el canal 1
    assert at(c1["bass"], beats) > 0.7 and c2["bass"].max() < 0.2
    assert at(c2["high"], off) > 0.5 and c1["high"].max() < 0.2
    assert fr[-1]["onsets"] == len(beats) and fr[-1]["bpm"] > 0         # la mezcla sigue con golpes y tempo
    mono = MultiAnalyzer(SR, 1).process(x[:960, :1])
    assert len(mono[0]["ch"]) == 1 and mono[0]["ch"][0]["bass"] == mono[0]["bass"]


def test_channel_keys_and_fallback():
    from playground.audio import parse_key, audio_source, audio_value, valid_key
    a = {"bass": 0.1, "rms": 0.2, "onsets": 4, "bpm": 120.0,
         "ch": [{"bass": 0.9, "rms": 0.8, "onsets": 2}, {"bass": 0.0, "rms": 0.4, "high": 0.7, "onsets": 0}]}
    assert parse_key("bass") == ("mix", "bass") and parse_key("ch2.high") == ("ch2", "high")
    assert parse_key("L") == ("ch1", "rms") and parse_key("R.bass") == ("ch2", "bass") and parse_key("off") is None
    assert audio_mode("R.bass") == "ch2.bass" and audio_mode("bass") == "bass"
    assert audio_level(a, "ch1.bass") == 0.9 and audio_level(a, "R.high") == 0.7 and audio_level(a, "ch2") == 0.4
    assert audio_level(a, "ch5.bass") == 0.1 and audio_source(a, "ch9") is a          # canal que no hay: mezcla
    assert audio_value(a, "ch1.onsets") == 2 and audio_value(a, "bpm") == 120.0
    assert valid_key("ch3.rms") and valid_key("bpm") and valid_key("L.kick") and not valid_key("ch1.nada")


def test_runtime_per_channel_state_and_binds(tmp_path, monkeypatch):
    rt = _rt(tmp_path)
    e = rt.spawn("contour_glow", "cocodrilo")
    rt.command("bind", {"control": "audio/ch2.rms", "target": f"effect:{e['id']}:width_mm", "lo": 0, "hi": 100})
    _send(rt, bass=0.3, ch=[{"rms": 0.1, "bass": 0.9, "onsets": 1}, {"rms": 0.6, "onsets": 0}])
    rt.tick(0.03)
    assert rt.scene.audio["ch"][0]["bass"] == 0.9 and len(rt.state()["audio"]["features"]["ch"]) == 2
    assert rt.instances[e["id"]].p["width_mm"] == pytest.approx(60.0)
    monkeypatch.setattr(time, "time", lambda t0=time.time(): t0 + 1.0)
    for _ in range(10):
        rt.tick(0.03)
    assert rt.scene.audio["ch"][0]["bass"] < 0.1 and rt.scene.audio["live"] is False


def test_mapping_file_defaults():
    import json
    from playground.audio.mapping import MAPPING_PATH, load_mapping
    raw = json.loads(MAPPING_PATH.read_text())
    parts, err, _ = load_mapping("")
    assert err is None and len(parts) == len(raw["parts"]) >= 4
    by = {p["part"]: p for p in parts}
    assert by["buda"]["source"] == "ch1" and by["keys"]["source"] == "ch2"
    assert by["jogs"]["feature"] == "bass" and by["jogs"]["ripples"] and by["cocodrilo"]["feature"] == "high"


def test_channel_react_follows_each_channel(tmp_path):
    rt = _rt(tmp_path, (1920, 1080))
    e = rt.spawn("channel_react", params={"scan": str(tmp_path / "x"), "only": "buda,keys"})
    from playground.stage import FALLBACK
    bx = np.asarray(FALLBACK["buda"])
    kx = np.asarray(FALLBACK["keyboard"]) + [[0, 25], [0, 25], [0, 0], [0, 0]]    # sin el borde que toca el halo del Buda
    region = lambda f, p: int(f[int(p[:, 1].min()):int(p[:, 1].max()),
                                int(p[:, 0].min()):int(p[:, 0].max())].astype(np.int32).sum())
    _send(rt, ch=[{"rms": 0.0}, {"rms": 0.0}]); f0 = rt.tick(0.03).copy()
    _send(rt, ch=[{"rms": 1.0}, {"rms": 0.0}]); f1 = rt.tick(0.03).copy()
    _send(rt, ch=[{"rms": 0.0}, {"rms": 1.0}]); f2 = rt.tick(0.03).copy()
    assert region(f1, bx) > 2 * region(f0, bx) and region(f1, kx) == pytest.approx(region(f0, kx), rel=0.05)
    assert region(f2, kx) > 2 * region(f0, kx) and region(f2, bx) == pytest.approx(region(f0, bx), rel=0.05)
    st = rt.instances[e["id"]].state()["mapping"]
    assert st["error"] is None and st["parts"][0]["label"].startswith("canal 1")
    # mapeo inline y onda en cada golpe del canal elegido
    rt.set_param(e["id"], "mapping", {"parts": [{"part": "jogs", "source": "ch1", "feature": "bass", "ripples": True}]})
    rt.set_param(e["id"], "only", "")
    _send(rt, ch=[{"bass": 0.5, "onsets": 0}]); rt.tick(0.03)
    _send(rt, ch=[{"bass": 0.9, "onsets": 1}]); rt.tick(0.03)
    assert len(rt.instances[e["id"]].ripples[0]) == 1


def test_channel_react_survives_broken_mapping(tmp_path):
    import shutil
    from playground.audio.mapping import MAPPING_PATH
    m = tmp_path / "map.json"; shutil.copy(MAPPING_PATH, m)
    rt = _rt(tmp_path)
    e = rt.spawn("channel_react", params={"mapping": str(m), "scan": str(tmp_path / "x")})
    rt.tick(0.03)
    n = len(rt.instances[e["id"]].parts)
    time.sleep(0.02); m.write_text("{ esto no es json")
    rt.tick(0.03)
    inst = rt.instances[e["id"]]
    assert len(inst.parts) == n and "map.json" in inst.state()["mapping"]["error"]   # sigue con el último bueno


def test_capture_multichannel_device(monkeypatch):
    from playground.audio import capture
    x2, beats, _ = _two_channels(secs=3.0)
    x = np.concatenate([x2, x2[:, :1] * 0.0 + 1e-4, x2[:, 1:] * 0.5], axis=1)      # 4 entradas (una casi muda)
    monkeypatch.setattr(capture, "_sd", lambda: SimpleNamespace(InputStream=lambda **kw: _FakeStream(x, **kw)))
    monkeypatch.setattr(capture, "choose_device", lambda spec=None: {"index": 3, "name": "BlackHole 16ch", "inputs": 4,
                                                                     "samplerate": SR, "kind": "loopback", "why": "test"})
    sent = []

    class Pub:
        def __init__(self, url):
            self.post_ms, self.ok, self.sent, self.last_error = 0.5, True, 0, ""

        def send(self, args):
            sent.append(args); self.sent += 1; return True

        def close(self):
            pass

    monkeypatch.setattr(capture, "Publisher", Pub)
    out = capture.run(None, "http://x", meter=False, seconds=1.2, log_every=99)
    feats = [a["features"] for a in sent if "features" in a]
    assert out["channels"] == 4 and len(feats[-1]["ch"]) == 4
    assert out["ch_onsets"][0] >= 3 and out["ch_onsets"][1] == 0 and out["ch_onsets"][2] == 0
    meta = next(a["meta"] for a in sent if "meta" in a)
    assert meta["channels"] == 4

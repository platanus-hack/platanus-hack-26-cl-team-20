"""Captura de audio en el Mac (sounddevice / PortAudio) → `MultiAnalyzer` (mezcla + cada canal) → runtime por HTTP.

Dispositivo por defecto, en orden: mezcladora (Behringer QX1222USB, que macOS suele mostrar como «USB Audio CODEC»),
loopback (BlackHole / Loopback: el master de Ableton; se salta si entrega silencio digital), micrófono interno.
Abre **todos** los canales de entrada del dispositivo (QX1222USB: L y R; BlackHole 16ch: 16) y analiza la mezcla y
cada canal. Publica cada cuadro con `POST /api/command {"cmd": "audio", "args": {"features": {..., "ch": [...]}}}`
(keep-alive, ~1 ms local).
"""

from __future__ import annotations

import os
import queue
import signal
import sys
import threading
import time

import numpy as np

from .analysis import AudioAnalyzer, MultiAnalyzer

DEFAULT_URL = os.environ.get("PLAYGROUND_URL", "http://127.0.0.1:8790")
PREFER: list[tuple[str, tuple[str, ...]]] = [
    ("mezcladora", ("qx1222", "behringer", "xenyx", "usb audio codec")),
    ("loopback", ("blackhole", "loopback")),
    ("micrófono", ("macbook", "built-in", "internal", "micrófono", "microphone")),
]
AVOID = ("ui sounds",)
PUBLISH = ("sub", "bass", "mid", "high", "rms", "beat", "onsets", "bpm")
MAX_CHANNELS = 32


def _sd():
    import sounddevice as sd       # import diferido: el runtime y los tests no necesitan PortAudio
    return sd


def rank(name: str) -> tuple[int, str]:
    n = name.lower()
    if any(a in n for a in AVOID):
        return 99, "evitado"
    for i, (label, keys) in enumerate(PREFER):
        if any(k in n for k in keys):
            return i, label
    return 50, "otro"


def input_devices(refresh: bool = False) -> list[dict]:
    sd = _sd()
    if refresh:                    # PortAudio cachea la lista al iniciar; re-inicializa para ver equipos recién enchufados
        sd._terminate(); sd._initialize()
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] < 1:
            continue
        r, label = rank(d["name"])
        out.append({"index": i, "name": d["name"], "inputs": int(d["max_input_channels"]),
                    "samplerate": int(d["default_samplerate"]), "latency_ms": round(d["default_low_input_latency"] * 1000, 1),
                    "hostapi": sd.query_hostapis(d["hostapi"])["name"], "rank": r, "kind": label,
                    "multichannel": int(d["max_input_channels"]) > 2})
    return sorted(out, key=lambda d: (d["rank"], d["index"]))


def probe(index: int, seconds: float = 0.5) -> float:
    """Pico absoluto de `seconds` de audio (0.0 = silencio digital: loopback sin nada ruteado)."""
    sd = _sd()
    d = sd.query_devices(index)
    x = sd.rec(int(d["default_samplerate"] * seconds), samplerate=d["default_samplerate"],
               channels=min(2, d["max_input_channels"]), device=index, dtype="float32")
    sd.wait()
    return float(np.abs(x).max()) if x.size else 0.0


def choose_device(spec=None, probe_loopback: bool = True) -> dict:
    """`spec`: índice, parte del nombre o None (orden de PREFER; un loopback mudo se salta)."""
    devs = input_devices()
    if not devs:
        raise RuntimeError("no hay dispositivos de entrada de audio")
    if spec not in (None, "", "auto"):
        if isinstance(spec, int) or str(spec).isdigit():
            m = [d for d in devs if d["index"] == int(spec)]
        else:
            m = [d for d in devs if str(spec).lower() in d["name"].lower()]
        if not m:
            raise RuntimeError(f"no hay entrada que calce con {spec!r}: {[d['name'] for d in devs]}")
        return {**m[0], "why": f"pedido: {spec}"}
    for d in devs:
        if d["rank"] >= 50:
            break
        if d["kind"] == "loopback" and probe_loopback:
            try:
                if probe(d["index"]) == 0.0:
                    continue                                   # nada ruteado al loopback
            except Exception:  # noqa: BLE001
                continue
        return {**d, "why": f"preferido ({d['kind']})"}
    d = next((d for d in devs if d["rank"] < 99), devs[0])
    return {**d, "why": "primera entrada disponible"}


class Publisher:
    """POST de rasgos al runtime con keep-alive; si el runtime no está, reintenta cada segundo sin frenar el análisis."""

    def __init__(self, url: str = DEFAULT_URL, timeout: float = 0.25):
        import httpx
        self.url = url.rstrip("/")
        self.client = httpx.Client(timeout=timeout)
        self.ok = False
        self.retry_at = 0.0
        self.post_ms = 0.0
        self.sent = 0
        self.errors = 0
        self.last_error = ""

    def send(self, args: dict) -> bool:
        now = time.time()
        if now < self.retry_at:
            return False
        t0 = time.perf_counter()
        try:
            r = self.client.post(f"{self.url}/api/command", json={"cmd": "audio", "args": args})
            self.ok = r.status_code == 200
            if not self.ok:
                self.last_error = r.text[:200]
        except Exception as e:  # noqa: BLE001  (httpx.HTTPError y afines)
            self.ok = False
            self.last_error = str(e)[:200]
        if self.ok:
            self.sent += 1
            self.post_ms = 0.9 * self.post_ms + 0.1 * (time.perf_counter() - t0) * 1000 if self.sent > 1 else (time.perf_counter() - t0) * 1000
        else:
            self.errors += 1
            self.retry_at = now + 1.0
        return self.ok

    def close(self):
        self.client.close()


def _bar(v: float, n: int = 10) -> str:
    k = int(round(max(0.0, min(1.0, v)) * n))
    return "█" * k + "·" * (n - k)


def run(device=None, url: str = DEFAULT_URL, rate: float = 50.0, channel: int = -1, post: bool = True,
        meter: bool | None = None, seconds: float | None = None, samplerate: int | None = None, tempo: bool = True,
        log_every: float = 5.0, stop: threading.Event | None = None) -> dict:
    """Bucle principal: captura, analiza a `rate` Hz y publica. Devuelve un resumen al terminar."""
    sd = _sd()
    dev = choose_device(device)
    sr = int(samplerate or dev["samplerate"])
    nch = max(1, min(MAX_CHANNELS, int(dev["inputs"])))       # todos los canales: cada uno se analiza aparte
    if channel >= nch:
        raise RuntimeError(f"{dev['name']} tiene {nch} canales; channel={channel} no existe")
    an = MultiAnalyzer(sr, nch, rate, tempo=tempo, mix_channel=channel)
    q: queue.Queue = queue.Queue(maxsize=256)
    st = {"overflows": 0, "dropped": 0, "adc_lag_ms": 0.0, "silent_s": 0.0}
    stop = stop or threading.Event()
    old_handlers = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):                  # SIGTERM / Ctrl-C: cierra limpio y avisa al runtime
            old_handlers[sig] = signal.signal(sig, lambda *_: stop.set())
    meter = sys.stdout.isatty() if meter is None else meter

    def cb(indata, frames, ti, status):
        now = time.time()
        if status and status.input_overflow:
            st["overflows"] += 1
        try:
            lag = max(0.0, float(ti.currentTime - ti.inputBufferAdcTime)) if ti.inputBufferAdcTime > 0 else None
        except Exception:  # noqa: BLE001
            lag = None
        tc = now - (lag if lag is not None else 0.0) + frames / sr     # reloj de pared de la última muestra en el ADC
        if lag is not None:
            st["adc_lag_ms"] = 0.95 * st["adc_lag_ms"] + 0.05 * lag * 1000 if st["adc_lag_ms"] else lag * 1000
        try:
            q.put_nowait((np.array(indata, np.float32), tc))
        except queue.Full:
            st["dropped"] += 1

    pub = Publisher(url) if post else None
    t0 = time.time()
    last_meta = last_log = 0.0
    proc_ms = 0.0
    seq = 0
    feats: dict = {}
    print(f"audio: {dev['name']} (#{dev['index']}, {dev['why']}) {sr} Hz, {nch} canal(es), hop {an.hop} "
          f"→ {url if post else 'sin publicar'}", flush=True)
    stream = sd.InputStream(device=dev["index"], channels=nch, samplerate=sr, blocksize=an.hop, dtype="float32",
                            latency="low", callback=cb)
    with stream:
        in_lat_ms = round(float(stream.latency) * 1000, 1)
        while not stop.is_set():
            if seconds is not None and time.time() - t0 >= seconds:
                break
            try:
                x, tc = q.get(timeout=1.0)
            except queue.Empty:
                continue
            t1 = time.perf_counter()
            frames = an.process(x)
            proc_ms = 0.95 * proc_ms + 0.05 * (time.perf_counter() - t1) * 1000
            st["silent_s"] = st["silent_s"] + len(x) / sr if float(np.abs(x).max()) < 1e-6 else 0.0
            peaks = np.abs(x).max(axis=0) if x.ndim == 2 else np.abs(x).max(keepdims=True)
            st["ch_peak"] = np.maximum(st.get("ch_peak", peaks) * 0.995, peaks)
            if not frames:
                continue
            feats = frames[-1]
            seq += 1
            now = time.time()
            if pub is not None and q.qsize() < 3:                 # atrasado: analiza todo, publica solo lo último
                args = {"features": {**{k: feats[k] for k in PUBLISH}, "ch": feats.get("ch", []),
                                     "tc": round(tc, 4), "seq": seq}}
                if now - last_meta >= 1.0:
                    last_meta = now
                    args["meta"] = {"device": dev["name"], "index": dev["index"], "kind": dev["kind"], "sr": sr,
                                    "hop": an.hop, "rate": rate, "channel": channel, "pid": os.getpid(),
                                    "in_latency_ms": in_lat_ms, "adc_lag_ms": round(st["adc_lag_ms"], 1),
                                    "proc_ms": round(proc_ms, 3), "post_ms": round(pub.post_ms, 2),
                                    "overflows": st["overflows"], "dropped": st["dropped"],
                                    "silent": st["silent_s"] > 3.0, "level_db": feats["level_db"], "channels": nch,
                                    "ch_silent": [bool(v < 1e-6) for v in st.get("ch_peak", [])]}
                pub.send(args)
            if meter:
                print("\r" + " ".join(f"{k} {_bar(feats[k], 8)}" for k in ("sub", "bass", "mid", "high")) +
                      f" rms {feats['rms']:.2f} {'●' if feats['beat'] > 0.5 else '○'} {feats['onsets']:4d} "
                      f"bpm {feats['bpm']:5.1f} {feats['level_db']:6.1f} dB" +
                      ("  " + " ".join(f"{i + 1}:{_bar(c.get('rms', 0), 4)}" for i, c in enumerate(feats["ch"][:8]))
                       if nch > 1 else ""), end="", flush=True)
            elif now - last_log >= log_every:
                last_log = now
                warn = "  ¡SIN SEÑAL! (¿permiso de micrófono o dispositivo mudo?)" if st["silent_s"] > 3.0 else ""
                rt = "" if pub is None else (f" post {pub.post_ms:.1f} ms" if pub.ok else f" runtime no responde ({pub.last_error[:60]})")
                chs = (" · canales " + " ".join(f"{i + 1}:{c.get('rms', 0):.2f}/{c.get('onsets', 0)}"
                                                for i, c in enumerate(feats["ch"][:16]))) if nch > 1 else ""
                print(" ".join(f"{k} {feats[k]:.2f}" for k in ("sub", "bass", "mid", "high", "rms")) +
                      f" golpes {feats['onsets']} bpm {feats['bpm']:.1f} nivel {feats['level_db']:.1f} dB"
                      f" in {in_lat_ms} ms proc {proc_ms:.2f} ms{rt}{chs}{warn}", flush=True)
    if pub is not None:
        pub.retry_at = 0.0
        pub.send({"stop": True})
        pub.close()
    for sig, h in old_handlers.items():
        signal.signal(sig, h)
    if meter:
        print()
    return {"device": dev["name"], "sr": sr, "channels": nch, "seconds": round(time.time() - t0, 2), "frames": an.frames,
            "onsets": an.onsets, "bpm": feats.get("bpm", 0.0), "in_latency_ms": in_lat_ms if seq else None,
            "proc_ms": round(proc_ms, 3), "post_ms": round(pub.post_ms, 2) if pub else None,
            "sent": pub.sent if pub else 0, "overflows": st["overflows"], "dropped": st["dropped"],
            "ch_onsets": [c.get("onsets", 0) for c in feats.get("ch", [])]}


def measure_latency(device=None, clicks: int = 6, freq: float = 140.0, amp: float = 0.12, gap: float = 0.8) -> dict:
    """Lazo acústico: el parlante por defecto toca golpes graves y la entrada los detecta con el mismo analizador.
    `dac_to_onset_ms` = desde que el golpe sale del DAC (hora que da CoreAudio) hasta que el analizador lo marca:
    aire + latencia de entrada + espera del cuadro + análisis. Golpes cortos y bajos de volumen (`amp`)."""
    sd = _sd()
    dev = choose_device(device)
    sr = int(dev["samplerate"])
    an = AudioAnalyzer(sr, 50.0, tempo=False)
    burst_n = int(0.12 * sr)
    tt = np.arange(burst_n) / sr
    burst = (amp * np.sin(2 * np.pi * freq * tt) * np.minimum(1, tt / 0.002) * np.exp(-tt / 0.05)).astype(np.float32)
    plan = [0.6 + i * gap for i in range(clicks)]
    played: list[float] = []
    detected: list[float] = []
    lock = threading.Lock()
    state = {"start": None, "pos": 0}
    total_n = int((plan[-1] + 0.8) * sr)

    def out_cb(outdata, frames, ti, status):
        outdata.fill(0)
        if state["start"] is None:
            state["start"] = time.time()
        pos = state["pos"]
        for t_c in plan:
            s = int(t_c * sr)
            if s < pos + frames and s + burst_n > pos:
                a, b = max(s, pos), min(s + burst_n, pos + frames)
                outdata[a - pos:b - pos, 0] += burst[a - s:b - s]
                if s >= pos:
                    dac = time.time() + max(0.0, float(ti.outputBufferDacTime - ti.currentTime)) + (s - pos) / sr
                    with lock:
                        played.append(dac)
        state["pos"] = pos + frames

    def in_cb(indata, frames, ti, status):
        now = time.time()
        for f in an.process(indata.mean(axis=1)):
            if f["onset"]:
                with lock:
                    detected.append(now)

    with sd.InputStream(device=dev["index"], channels=min(2, dev["inputs"]), samplerate=sr, blocksize=an.hop,
                        dtype="float32", latency="low", callback=in_cb) as ins, \
            sd.OutputStream(samplerate=sr, channels=1, dtype="float32", latency="low", callback=out_cb) as outs:
        time.sleep(total_n / sr + 0.3)
        in_lat, out_lat = float(ins.latency) * 1000, float(outs.latency) * 1000
    pairs = []
    for p in played:
        d = [x - p for x in detected if -0.05 < x - p < 0.5]
        if d:
            pairs.append(min(d) * 1000)
    res = {"device": dev["name"], "clicks": clicks, "played": len(played), "detected": len(pairs),
           "out_latency_ms": round(out_lat, 1), "in_latency_ms": round(in_lat, 1)}
    if pairs:
        med = float(np.median(pairs))
        res.update(dac_to_onset_ms=round(med, 1), all_ms=[round(v, 1) for v in pairs])
    return res

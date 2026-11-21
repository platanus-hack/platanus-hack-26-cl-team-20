"""Análisis de audio a ~50 Hz: energía por bandas, RMS, golpes del bajo y tempo, con normalización automática.

Por cuadro (`hop` = sr / rate muestras, 20 ms a 50 Hz):

- **Bandas** con filtros IIR Butterworth en SOS (con estado entre cuadros, sin ventana FFT: poca latencia):
  sub 25–60 Hz · bajo 60–180 · medios 180–2k · agudos 2k–8k. Energía = media cuadrática del cuadro (sub y bajo
  promedian 2 cuadros: un ciclo de 30 Hz dura 33 ms).
- **AGC lento** por banda en dB (`AGC`): techo que sube al instante y baja ~3 dB/s, piso de ruido que baja rápido y
  sube ~0,5 dB/s. 0..1 = dónde cae el nivel entre `piso + margen` y el techo (ventana máx. 24 dB). Una banda solo
  llega a 1 si su techo está a menos de `boost_db` del techo de la banda más fuerte: así la fuga de un filtro (un
  seno de 55 Hz visto por los medios) no se infla a 1. Sin configurar ganancia: micrófono lejano o línea de la
  mezcladora terminan en el mismo rango tras unos segundos.
- **Golpes (onset) del bajo**: banda 30–180 Hz de orden 2 (≈3 ms de retardo), subida en dB respecto del mínimo de
  los 2 cuadros previos; umbral adaptativo = max(6 dB, mediana + 3·MAD del último segundo), con histéresis, puerta de
  nivel, graves a no más de 24 dB bajo el total (un canal de hi-hats no golpea) y período refractario de
  100 ms. `beat` = 1 en el golpe y cae con τ = 0,12 s.
- **Tempo** (opcional): autocorrelación de la fuerza de golpe de los últimos 8 s, con prior log-normal en 120 BPM,
  interpolación parabólica y mediana de las últimas 5 estimaciones.
- **Por canal** (`MultiAnalyzer`): lo mismo para cada canal de entrada (sin tempo), además de la mezcla.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np

BANDS: dict[str, tuple[float, float]] = {"sub": (25.0, 60.0), "bass": (60.0, 180.0), "mid": (180.0, 2000.0),
                                         "high": (2000.0, 8000.0)}
ORDERS = {"sub": 3, "bass": 4, "mid": 6, "high": 4}       # mid 6: la fuga de 55 Hz queda en -66 dB
SMOOTH_HOPS = {"sub": 2, "bass": 2, "mid": 1, "high": 1}
ONSET_BAND = (30.0, 180.0)
DB_MIN = -120.0


def to_db(mean_square: float) -> float:
    """Media cuadrática → dBFS (un seno de amplitud 1 da -3 dB)."""
    return max(DB_MIN, 10.0 * math.log10(max(float(mean_square), 1e-15)))


class AGC:
    """Normaliza un nivel en dB a 0..1 sin configurar ganancia (ver docstring del módulo)."""

    def __init__(self, rate: float = 50.0, release_db_s: float = 3.0, floor_fall_s: float = 0.25,
                 floor_rise_db_s: float = 0.5, range_db: float = 24.0, margin_db: float = 6.0,
                 min_span_db: float = 9.0):
        self.rate = rate
        self.release = release_db_s / rate
        self.fall = 1.0 - math.exp(-1.0 / (rate * floor_fall_s))
        self.rise = floor_rise_db_s / rate
        self.range_db, self.margin_db, self.min_span_db = range_db, margin_db, min_span_db
        self.peak: float | None = None
        self.floor: float | None = None

    def update(self, level_db: float) -> None:
        if self.peak is None:                    # al partir se asume ruido: el piso baja rápido en el primer valle
            self.peak = self.floor = level_db
            return
        self.peak = level_db if level_db > self.peak else max(level_db, self.peak - self.release)
        if level_db < self.floor:
            self.floor += (level_db - self.floor) * self.fall
        else:
            self.floor = min(level_db, self.floor + self.rise)

    def bounds(self, ref_peak: float | None = None, boost_db: float = 24.0) -> tuple[float, float]:
        hi = self.peak if ref_peak is None else max(self.peak, ref_peak - boost_db)
        lo = max(hi - self.range_db, self.floor + self.margin_db)
        hi = max(hi, lo + self.min_span_db)
        return lo, hi

    def norm(self, level_db: float, ref_peak: float | None = None, boost_db: float = 24.0) -> float:
        if self.peak is None:
            return 0.0
        lo, hi = self.bounds(ref_peak, boost_db)
        return float(min(1.0, max(0.0, (level_db - lo) / (hi - lo))))


class _Filter:
    """Butterworth pasa-banda en SOS con estado; sin scipy cae a un FFT por cuadro (más fuga, misma API)."""

    def __init__(self, sr: int, lo: float, hi: float, order: int):
        self.sr, self.lo, self.hi = sr, lo, min(hi, 0.45 * sr)
        try:
            from scipy.signal import butter, sosfilt
            self.sos = butter(order, [lo, self.hi], btype="band", fs=sr, output="sos")
            self.zi = np.zeros((self.sos.shape[0], 2))
            self._sosfilt = sosfilt
        except ImportError:                                  # pragma: no cover (scipy está en el venv)
            self.sos = None

    def mean_square(self, x: np.ndarray) -> float:
        if self.sos is not None:
            y, self.zi = self._sosfilt(self.sos, x, zi=self.zi)
            return float(np.mean(y * y))
        n = len(x)                                           # pragma: no cover
        spec = np.abs(np.fft.rfft(x * np.hanning(n))) ** 2
        f = np.fft.rfftfreq(n, 1.0 / self.sr)
        return float(spec[(f >= self.lo) & (f < self.hi)].sum() * 2 / (n * n * 0.375))


class OnsetDetector:
    """Golpes por subida de energía en dB con umbral adaptativo (mediana + k·MAD del último segundo)."""

    def __init__(self, rate: float = 50.0, lag: int = 2, k: float = 3.0, min_rise_db: float = 6.0,
                 window_s: float = 1.0, refractory_s: float = 0.10, gate: float = 0.25, min_share_db: float = 24.0):
        self.rate, self.k, self.min_rise, self.refractory, self.gate = rate, k, min_rise_db, refractory_s, gate
        self.min_share = min_share_db          # graves a lo más 24 dB bajo el total: un canal de hi-hats no "golpea"
        self.levels: deque[float] = deque(maxlen=lag + 1)
        self.flux_hist: deque[float] = deque(maxlen=max(4, int(window_s * rate)))
        self.armed = True
        self.last_t = -1e9
        self.threshold = min_rise_db

    def update(self, level_db: float, level_norm: float, t: float, share_db: float = 0.0) -> tuple[float, bool]:
        self.levels.append(level_db)
        flux = 0.0
        if len(self.levels) == self.levels.maxlen:
            prev = list(self.levels)[:-1]
            flux = max(0.0, level_db - min(prev))
        if len(self.flux_hist) >= 4:                         # mediana + k·MAD: los golpes mismos no suben el umbral
            h = np.fromiter(self.flux_hist, float)
            med = float(np.median(h))
            thr = max(self.min_rise, med + self.k * 1.4826 * float(np.median(np.abs(h - med))))
        else:
            thr = self.min_rise * 1.5
        self.threshold = thr
        onset = (self.armed and flux > thr and level_norm >= self.gate and share_db >= -self.min_share
                 and t - self.last_t >= self.refractory)
        if onset:
            self.last_t = t
            self.armed = False
        elif flux < 0.5 * thr:
            self.armed = True
        self.flux_hist.append(flux)
        return flux, onset


class TempoEstimator:
    """BPM por autocorrelación de la fuerza de golpe (flux) de los últimos `window_s` segundos."""

    def __init__(self, rate: float = 50.0, window_s: float = 8.0, every_s: float = 0.5,
                 bpm_range: tuple[float, float] = (70.0, 180.0), prior_bpm: float = 120.0, prior_oct: float = 1.0,
                 min_conf: float = 0.15):
        self.rate = rate
        self.env: deque[float] = deque(maxlen=int(window_s * rate))
        self.every = max(1, int(every_s * rate))
        self.bpm_range, self.prior_bpm, self.prior_oct, self.min_conf = bpm_range, prior_bpm, prior_oct, min_conf
        self.n = 0
        self.history: deque[float] = deque(maxlen=5)
        self.bpm = 0.0
        self.confidence = 0.0

    def update(self, strength: float) -> float:
        self.env.append(strength)
        self.n += 1
        if self.n % self.every == 0 and len(self.env) >= 3 * self.rate:
            est = self._estimate()
            if est:
                self.history.append(est)
                self.bpm = float(np.median(self.history))
        return self.bpm

    def _estimate(self) -> float | None:
        x = np.fromiter(self.env, float)
        x = x - x.mean()
        n = len(x)
        if not np.any(x):
            return None
        spec = np.fft.rfft(x, 2 * n)
        acf = np.fft.irfft(spec * np.conj(spec))[:n]
        if acf[0] <= 0:
            return None
        acf = acf / acf[0]
        lo = max(2, int(math.floor(60.0 * self.rate / self.bpm_range[1])))
        hi = min(n - 2, int(math.ceil(60.0 * self.rate / self.bpm_range[0])))
        if hi <= lo:
            return None
        lags = np.arange(lo, hi + 1)
        bpms = 60.0 * self.rate / lags
        prior = np.exp(-0.5 * (np.log2(bpms / self.prior_bpm) / self.prior_oct) ** 2)
        score = acf[lags] * prior
        i = int(np.argmax(score))
        L = int(lags[i])
        self.confidence = float(acf[L])
        if self.confidence < self.min_conf:
            return None
        a, b, c = acf[L - 1], acf[L], acf[L + 1]
        den = a - 2 * b + c
        frac = 0.5 * (a - c) / den if den < 0 else 0.0
        return 60.0 * self.rate / (L + max(-0.5, min(0.5, frac)))


class AudioAnalyzer:
    """Recibe bloques de muestras (mono, float, cualquier tamaño) y entrega un dict de rasgos por cuadro de `hop`."""

    def __init__(self, sr: int = 48000, rate: float = 50.0, tempo: bool = True, boost_db: float = 24.0,
                 beat_decay_s: float = 0.12, release_s: float = 0.08):
        self.sr = int(sr)
        self.rate = float(rate)
        self.hop = int(round(self.sr / self.rate))
        self.boost_db = boost_db
        self.filters = {b: _Filter(self.sr, lo, hi, ORDERS[b]) for b, (lo, hi) in BANDS.items()}
        self.onset_filter = _Filter(self.sr, *ONSET_BAND, 2)
        self.agc = {b: AGC(self.rate) for b in (*BANDS, "rms", "low")}
        self.prev_ms = {b: None for b in BANDS}
        self.onset = OnsetDetector(self.rate)
        self.tempo = TempoEstimator(self.rate) if tempo else None
        self.beat_decay = math.exp(-1.0 / (self.rate * beat_decay_s))
        self.release = math.exp(-1.0 / (self.rate * release_s))
        self.env = {b: 0.0 for b in (*BANDS, "rms")}
        self.beat = 0.0
        self.onsets = 0
        self.frames = 0
        self._buf = np.zeros(0, np.float32)

    @property
    def t(self) -> float:
        return self.frames / self.rate

    def process(self, x: np.ndarray) -> list[dict]:
        """Agrega muestras y devuelve un dict por cada cuadro completo (normalmente uno por bloque de `hop`)."""
        x = np.asarray(x, np.float64).reshape(-1)
        buf = np.concatenate([self._buf, x]) if len(self._buf) else x
        out = []
        i = 0
        while len(buf) - i >= self.hop:
            out.append(self.frame(buf[i:i + self.hop]))
            i += self.hop
        self._buf = buf[i:].copy()
        return out

    def frame(self, x: np.ndarray) -> dict:
        levels = {}
        for b, f in self.filters.items():
            ms = f.mean_square(x)
            if SMOOTH_HOPS[b] > 1 and self.prev_ms[b] is not None:
                ms_s = 0.5 * (ms + self.prev_ms[b])
            else:
                ms_s = ms
            self.prev_ms[b] = ms
            levels[b] = to_db(ms_s)
        levels["rms"] = to_db(float(np.mean(x * x)))
        low_db = to_db(self.onset_filter.mean_square(x))
        for k, v in levels.items():
            self.agc[k].update(v)
        self.agc["low"].update(low_db)
        ref = max(self.agc[b].peak for b in BANDS)
        out: dict = {}
        for b in BANDS:
            n = self.agc[b].norm(levels[b], ref, self.boost_db)
            self.env[b] = max(n, self.env[b] * self.release)
            out[b] = round(self.env[b], 4)
        n = self.agc["rms"].norm(levels["rms"])
        self.env["rms"] = max(n, self.env["rms"] * self.release)
        out["rms"] = round(self.env["rms"], 4)

        t = self.t
        low_norm = self.agc["low"].norm(low_db)
        flux, onset = self.onset.update(low_db, low_norm, t, low_db - levels["rms"])
        if onset:
            self.onsets += 1
            self.beat = 1.0
        else:
            self.beat *= self.beat_decay
        out["beat"] = round(self.beat, 4)
        out["onsets"] = self.onsets
        out["onset"] = onset
        out["bpm"] = round(self.tempo.update(flux), 2) if self.tempo else 0.0
        out["level_db"] = round(levels["rms"], 1)
        out["t"] = round(t + 1.0 / self.rate, 4)        # fin del cuadro: cuándo se pudo saber
        self.frames += 1
        return out


CH_KEYS = ("sub", "bass", "mid", "high", "rms", "beat", "onsets")


class MultiAnalyzer:
    """La mezcla (con tempo) más un analizador por canal de entrada (bandas, rms, golpes; sin tempo).

    `process(block)` recibe (muestras, canales) y agrega a cada cuadro de la mezcla `ch`: [{sub, bass, …}, …].
    Con un solo canal, ch[0] es la mezcla (no se analiza dos veces). Cada canal tiene su propio AGC: un canal bajito
    (teclas) llega igual a 1, y uno sin nada (ruido) se queda en ~0.
    """

    def __init__(self, sr: int, channels: int, rate: float = 50.0, tempo: bool = True, mix_channel: int = -1):
        self.channels = max(1, int(channels))
        self.mix_channel = mix_channel
        self.mix = AudioAnalyzer(sr, rate, tempo=tempo)
        self.chans = [AudioAnalyzer(sr, rate, tempo=False) for _ in range(self.channels)] if self.channels > 1 else []
        self.hop = self.mix.hop
        self.sr, self.rate = self.mix.sr, self.mix.rate

    @property
    def frames(self) -> int:
        return self.mix.frames

    @property
    def onsets(self) -> int:
        return self.mix.onsets

    def process(self, block: np.ndarray) -> list[dict]:
        b = np.asarray(block, np.float64)
        if b.ndim == 1:
            b = b[:, None]
        x = b[:, self.mix_channel] if 0 <= self.mix_channel < b.shape[1] else b.mean(axis=1)
        frames = self.mix.process(x)
        if self.chans:
            per = [a.process(b[:, i]) if i < b.shape[1] else [] for i, a in enumerate(self.chans)]
            for j, f in enumerate(frames):
                f["ch"] = [{k: p[j][k] for k in CH_KEYS} if j < len(p) else {} for p in per]
        else:
            for f in frames:
                f["ch"] = [{k: f[k] for k in CH_KEYS}]
        return frames

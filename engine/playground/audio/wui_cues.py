"""Sonidos cortos para las interacciones WUI.

Un efecto visual pinta la señal; un cue sonoro puntual la acompaña para que la persona la
perciba sin mirar directo al anclaje. Son sonidos acústicamente sencillos, monocromáticos como
la marca: una paleta de timbres cálidos, sin acordes mayor/menor, sin reverb saturada. Dos usos:

    from playground.audio.wui_cues import play, render_all

    play("aparicion")     # tap suave al aparecer una pista
    play("guia")          # sostenido corto cuando la pista se asienta
    play("confirmacion")  # resultado positivo del paso
    play("recuperacion")  # suave pulso cuando WUI pide que vuelva el objeto

Los WAVs se renderizan la primera vez a `playground/audio/cues/*.wav`. Para regenerarlos a
mano: `python -m playground.audio.wui_cues`.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Callable

import numpy as np

SR: int = 44_100
CUES_DIR: Path = Path(__file__).parent / "cues"


# --- síntesis primitiva ---------------------------------------------------


def _envelope(n: int, attack_ms: float, release_ms: float, sustain: float = 1.0) -> np.ndarray:
    """ADSR simplificada en amplitud lineal."""

    a = max(1, int(attack_ms * SR / 1000))
    r = max(1, int(release_ms * SR / 1000))
    s = max(0, n - a - r)
    env = np.concatenate(
        [
            np.linspace(0.0, 1.0, a, endpoint=False),
            np.full(s, sustain, dtype=np.float32),
            np.linspace(sustain, 0.0, r, endpoint=True),
        ]
    ).astype(np.float32)
    if env.shape[0] < n:
        env = np.pad(env, (0, n - env.shape[0]))
    return env[:n]


def _tone(freq_hz: float, duration_ms: float, *, harmonics: tuple[float, ...] = (1.0,)) -> np.ndarray:
    """Suma de senos con pesos opcionales (armónicos)."""

    n = int(duration_ms * SR / 1000)
    t = np.arange(n, dtype=np.float32) / SR
    wave_out = np.zeros(n, dtype=np.float32)
    for k, amp in enumerate(harmonics, start=1):
        wave_out += amp * np.sin(2 * math.pi * freq_hz * k * t).astype(np.float32)
    wave_out /= max(1.0, float(sum(abs(a) for a in harmonics)))
    return wave_out


def _glide(freq_a: float, freq_b: float, duration_ms: float) -> np.ndarray:
    """Seno con glissando de `freq_a` a `freq_b` (modo ease asienta)."""

    n = int(duration_ms * SR / 1000)
    t = np.arange(n, dtype=np.float32) / SR
    k = np.linspace(0.0, 1.0, n, dtype=np.float32)
    k = 1.0 - (1.0 - k) ** 3   # ease out cúbico, parecido a ease_tinta_asienta
    freq = freq_a + (freq_b - freq_a) * k
    phase = 2 * math.pi * np.cumsum(freq) / SR
    return np.sin(phase).astype(np.float32)


def _noise_breath(duration_ms: float, cutoff_hz: float = 2200.0) -> np.ndarray:
    """Ruido blanco filtrado pasa-bajos simple; da la textura de respiración del aire del proyector."""

    n = int(duration_ms * SR / 1000)
    x = np.random.default_rng(42).standard_normal(n).astype(np.float32)
    alpha = math.exp(-2.0 * math.pi * cutoff_hz / SR)
    y = np.zeros_like(x)
    prev = 0.0
    for i in range(n):
        prev = alpha * prev + (1 - alpha) * x[i]
        y[i] = prev
    return y / (np.max(np.abs(y)) + 1e-9)


# --- cues ----------------------------------------------------------------


def _aparicion() -> np.ndarray:
    """Punto · 0,42 s. Tap suave de aparición de la señal."""

    body = _glide(880.0, 660.0, 420.0) * _envelope(int(0.42 * SR), attack_ms=4, release_ms=360)
    air = _noise_breath(420.0) * _envelope(int(0.42 * SR), attack_ms=8, release_ms=300, sustain=0.25)
    out = 0.85 * body + 0.08 * air
    return out


def _guia() -> np.ndarray:
    """Línea / Arco · 0,88 s. Sostenido corto, cuerpo cálido con ligero vibrato."""

    n = int(0.88 * SR)
    t = np.arange(n, dtype=np.float32) / SR
    vib = 1.0 + 0.004 * np.sin(2 * math.pi * 5.5 * t)
    phase = 2 * math.pi * np.cumsum(440.0 * vib) / SR
    body = (
        0.75 * np.sin(phase)
        + 0.18 * np.sin(2 * phase)
        + 0.06 * np.sin(3 * phase)
    ).astype(np.float32)
    env = _envelope(n, attack_ms=80, release_ms=520, sustain=0.9)
    air = _noise_breath(880.0) * _envelope(n, attack_ms=200, release_ms=600, sustain=0.2)
    return 0.7 * body * env + 0.06 * air


def _confirmacion() -> np.ndarray:
    """Resultado · 0,56 s. Dos tapitos; segundo medio tono más alto (C5 → C#5)."""

    n1 = int(0.18 * SR)
    pause = int(0.06 * SR)
    n2 = int(0.32 * SR)
    a = _tone(523.25, 180.0, harmonics=(1.0, 0.25, 0.1)) * _envelope(n1, attack_ms=3, release_ms=170)
    b = _tone(554.365, 320.0, harmonics=(1.0, 0.3, 0.12)) * _envelope(n2, attack_ms=3, release_ms=290)
    out = np.concatenate([a, np.zeros(pause, dtype=np.float32), b])
    return out


def _recuperacion() -> np.ndarray:
    """Recuperación · 1,20 s. Dos pulsos suaves pidiendo que el objeto vuelva."""

    n = int(1.2 * SR)
    t = np.arange(n, dtype=np.float32) / SR
    pulso = 0.5 * (1 + np.sin(2 * math.pi * 1.6 * t - math.pi / 2))  # dos crestas
    tono = np.sin(2 * math.pi * 330.0 * t).astype(np.float32)
    aire = _noise_breath(1200.0)
    env = _envelope(n, attack_ms=120, release_ms=420, sustain=0.85)
    out = 0.55 * tono * pulso * env + 0.08 * aire * pulso
    return out


CUE_FACTORIES: dict[str, Callable[[], np.ndarray]] = {
    "aparicion": _aparicion,
    "guia": _guia,
    "confirmacion": _confirmacion,
    "recuperacion": _recuperacion,
}


# --- render y playback ---------------------------------------------------


def _to_int16(x: np.ndarray, headroom_db: float = -3.0) -> np.ndarray:
    """Normaliza a 16-bit PCM con headroom contra clipping."""

    peak = float(np.max(np.abs(x))) or 1.0
    gain = 10 ** (headroom_db / 20.0) / peak
    x_i = np.clip(x * gain * 32767.0, -32768.0, 32767.0)
    return x_i.astype(np.int16)


def _write_wav(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = _to_int16(samples)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes(pcm.tobytes())


def path_for(cue: str) -> Path:
    """Ruta absoluta al WAV de un cue (lo renderiza si falta)."""

    if cue not in CUE_FACTORIES:
        raise KeyError(f"Cue desconocido: {cue}. Disponibles: {sorted(CUE_FACTORIES)}")
    dst = CUES_DIR / f"{cue}.wav"
    if not dst.exists():
        _write_wav(dst, CUE_FACTORIES[cue]())
    return dst


def render_all(force: bool = False) -> list[Path]:
    """Renderiza los cuatro WAVs al disco. Si `force`, regenera."""

    out: list[Path] = []
    for cue in CUE_FACTORIES:
        dst = CUES_DIR / f"{cue}.wav"
        if force or not dst.exists():
            _write_wav(dst, CUE_FACTORIES[cue]())
        out.append(dst)
    return out


def play(cue: str, *, blocking: bool = False) -> subprocess.Popen | None:
    """Reproduce un cue. En macOS usa `afplay`; en Linux intenta `aplay` o `paplay`.

    Devuelve el proceso (si no bloqueante) o None cuando se completa.
    """

    wav = path_for(cue)
    cmd: list[str] | None = None
    if sys.platform == "darwin" and shutil.which("afplay"):
        cmd = ["afplay", str(wav)]
    elif shutil.which("paplay"):
        cmd = ["paplay", str(wav)]
    elif shutil.which("aplay"):
        cmd = ["aplay", "-q", str(wav)]
    if cmd is None:
        return None
    if blocking:
        subprocess.run(cmd, check=False)
        return None
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> None:
    paths = render_all(force=True)
    for p in paths:
        print(p)


if __name__ == "__main__":
    main()

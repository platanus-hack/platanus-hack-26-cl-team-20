"""Audio reactivo del playground: captura (Mac) → análisis a ~50 Hz → `scene.audio` en el runtime → efectos.

    python -m playground.audio devices                # entradas, en orden de preferencia, y cuál se elige
    python -m playground.audio run [--device QX1222]  # captura todos los canales y publica al runtime (PLAYGROUND_URL)

`features` no importa nada de audio (lo usan el runtime y los efectos); `analysis` solo numpy/scipy;
`capture` es lo único que necesita sounddevice (PortAudio).
"""

from .features import (AUDIO_KEYS, AUDIO_STALE_S, CH_KEYS, LEVELS, MODES, audio_level, audio_mode, audio_source,
                       audio_value, audio_zero, parse_key, valid_key)
from .analysis import AGC, AudioAnalyzer, BANDS, MultiAnalyzer, OnsetDetector, TempoEstimator

__all__ = ["AUDIO_KEYS", "AUDIO_STALE_S", "CH_KEYS", "LEVELS", "MODES", "audio_level", "audio_mode", "audio_source",
           "audio_value", "audio_zero", "parse_key", "valid_key",
           "AGC", "AudioAnalyzer", "BANDS", "MultiAnalyzer", "OnsetDetector", "TempoEstimator"]

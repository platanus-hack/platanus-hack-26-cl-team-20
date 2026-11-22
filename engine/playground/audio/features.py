"""Rasgos de audio que viajan al runtime (`scene.audio`) y cómo los leen los efectos. Sin dependencias de audio.

    sub · bass · mid · high · rms   0..1 de la mezcla (normalizados con AGC lento: no hay que tocar ganancia)
    beat                            0..1: 1 en cada golpe del bajo y cae en ~0,15 s (flash corto)
    onsets                          contador de golpes (para disparar algo una sola vez por golpe)
    bpm                             tempo estimado de los golpes (0 = todavía sin estimar)
    ch                              lista por canal de entrada: [{sub, bass, mid, high, rms, beat, onsets}, …]
    live                            True si llegaron datos hace menos de `AUDIO_STALE_S`

Un rasgo se nombra `feature` (de la mezcla) o `fuente.feature`: `ch1.rms`, `ch2.bass`, `L.high`, `R` (= R.rms).
Fuentes: `mix`, `ch1`…`chN` (1 = primer canal del dispositivo), `L` = ch1, `R` = ch2. Un canal que el dispositivo
no tiene cae a la mezcla (con el micrófono mono, ch2 = mezcla).
"""

from __future__ import annotations

import re

LEVELS = ("sub", "bass", "mid", "high", "rms", "beat")
AUDIO_KEYS = LEVELS + ("onsets", "bpm")
CH_KEYS = LEVELS + ("onsets",)
MODES = LEVELS + ("low", "kick")          # low = max(sub, bass) · kick = max(bass, beat)
AUDIO_STALE_S = 0.5
_SRC = re.compile(r"^(mix|l|r|ch\d+)$")


def audio_zero() -> dict:
    return {"sub": 0.0, "bass": 0.0, "mid": 0.0, "high": 0.0, "rms": 0.0, "beat": 0.0, "onsets": 0, "bpm": 0.0,
            "ch": [], "live": False}


def parse_key(value) -> tuple[str, str] | None:
    """'bass' → ('mix', 'bass') · 'ch2.rms' → ('ch2', 'rms') · 'R' → ('ch2', 'rms') · '' / 'off' → None."""
    if value is True:
        return "mix", "bass"
    if not value or str(value).strip().lower() in ("off", "no", "false", "0", "none"):
        return None
    v = str(value).strip().lower()
    src, dot, feat = v.partition(".")
    if not dot:
        if v in MODES:
            return "mix", v
        src, feat = (v, "rms") if _SRC.match(v) else ("mix", "bass")
    if not _SRC.match(src):
        src = "mix"
    src = {"l": "ch1", "r": "ch2"}.get(src, src)
    if src == "ch0":
        src = "ch1"
    return src, (feat if feat in MODES else "rms")


def audio_mode(value) -> str | None:
    """Normaliza el parámetro `audio` de un efecto: '' / 'off' / False / None = apagado; True = 'bass'."""
    p = parse_key(value)
    if p is None:
        return None
    return p[1] if p[0] == "mix" else f"{p[0]}.{p[1]}"


def audio_source(audio: dict | None, source: str = "mix") -> dict:
    """El dict de rasgos de una fuente; un canal inexistente cae a la mezcla."""
    if not audio:
        return {}
    if source in ("", "mix", None):
        return audio
    m = re.match(r"^ch(\d+)$", source)
    chs = audio.get("ch") or []
    if m and 1 <= int(m.group(1)) <= len(chs):
        return chs[int(m.group(1)) - 1] or {}
    return audio


def audio_level(audio: dict | None, mode, gain: float = 1.0) -> float:
    """Nivel 0..1 de `mode` ('bass', 'ch1.rms', 'L.high', 'kick'…) en `scene.audio` (0 si está apagado o no hay audio)."""
    p = parse_key(mode)
    if p is None or not audio:
        return 0.0
    src, m = p
    a = audio_source(audio, src)
    if m == "low":
        v = max(float(a.get("sub", 0.0)), float(a.get("bass", 0.0)))
    elif m == "kick":
        v = max(float(a.get("bass", 0.0)), float(a.get("beat", 0.0)))
    else:
        v = float(a.get(m, 0.0))
    return max(0.0, min(1.0, v * float(gain)))


def audio_value(audio: dict | None, key: str) -> float:
    """Valor crudo de un rasgo (sirve también para `onsets` y `bpm`): 'bpm', 'ch2.onsets', 'bass'…"""
    src, _, feat = str(key).lower().partition(".")
    if not feat:
        src, feat = "mix", src
    src = {"l": "ch1", "r": "ch2"}.get(src, src)
    return float(audio_source(audio, src).get(feat, 0.0) or 0.0)


def valid_key(key: str) -> bool:
    src, _, feat = str(key).lower().partition(".")
    if not feat:
        return src in AUDIO_KEYS or src in MODES
    return bool(_SRC.match(src)) and (feat in CH_KEYS or feat in MODES)

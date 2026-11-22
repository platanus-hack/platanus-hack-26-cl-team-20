"""Dispara cues WUI desde eventos de efectos.

Cada efecto llama ``fire(cue, key)`` cuando ocurre un evento perceptual
discreto (aparición de un toque, asentamiento de la pista, resultado positivo
de un paso, objeto fuera de vista). ``key`` identifica de forma estable el
origen del evento (p. ej. ``"touch_marks:instance_123:aparicion"``) y se usa
para dedupear: si la misma ``key`` se dispara dos veces dentro del throttle,
el segundo intento se ignora.

El reproductor ya tolera hosts sin ``afplay``/``paplay``/``aplay`` (devuelve
``None`` sin romper), así que el hook es seguro para CI y tests.
"""

from __future__ import annotations

import time

from .wui_cues import CUE_FACTORIES, play

# throttle por clave: eventos de la misma clave que se repiten dentro de
# esta ventana se descartan. Ajustado para que un spawn de afplay no colisione
# con otro de la misma fuente durante la duración de los cues (hasta 1,2 s).
THROTTLE_MS: float = 300.0

_last_fired_ms: dict[str, float] = {}


def fire(cue: str, key: str) -> bool:
    """Reproduce ``cue`` si la ``key`` no se disparó recientemente.

    Devuelve ``True`` si el cue se despachó, ``False`` si se descartó por
    throttle o por nombre inválido.
    """

    if cue not in CUE_FACTORIES:
        return False
    now_ms = time.time() * 1000.0
    last = _last_fired_ms.get(key, 0.0)
    if now_ms - last < THROTTLE_MS:
        return False
    _last_fired_ms[key] = now_ms
    play(cue)
    return True


def reset() -> None:
    """Limpia la memoria de throttle. Útil en tests."""

    _last_fired_ms.clear()

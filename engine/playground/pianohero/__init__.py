"""Piano Hero proyectado sobre las teclas reales del Korg EK-50 (pedido de Max, 16-oct).

    python -m playground.pianohero --level 1 --song oda              # proyecta y juega (Korg por el puente o local)
    python -m playground.pianohero --level 3 --song ~/x.mid --practice

Partes: ``songs`` (melodías incluidas, .mid, simplificación por nivel), ``game`` (reloj, juicio, puntaje, práctica),
``layout`` (carriles desde keys.json), el efecto ``playground/effects/pianohero.py`` (dibujo, con hot reload) y el CLI.
El MCP lo maneja con ``pg_pianohero``. Ver README.md.
"""

from __future__ import annotations

# juegos vivos por instancia de efecto: sobreviven al hot reload del efecto (el runtime recrea la instancia con los
# mismos parámetros; el juego sigue donde iba). Clave: id de instancia → (firma de parámetros, Game).
LIVE: dict[str, tuple[tuple, object]] = {}
# layouts ya cargados (cargar uno puede ajustar el modelo del teclado: ~2 s). Clave: (keys, lane_px, hud).
LAYOUTS: dict[tuple, object] = {}

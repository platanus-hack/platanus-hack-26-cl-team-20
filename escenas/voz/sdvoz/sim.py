"""Mesa de prueba para la voz: las 3 pestañas y objetos plausibles publicados como lo haría el motor (`scenes
objects`). Solo para la consola simulada (`?sim=1`); en la mesa real los objetos los publica el motor.

    uv run python -m sdvoz sim-mesa [--pestana "Sistema solar"]
"""

from __future__ import annotations

import math

from .mesa import Mesa


def _circulo(cx, cy, r, n=12):
    return [[round(cx + r * math.cos(2 * math.pi * i / n)), round(cy + r * math.sin(2 * math.pi * i / n))]
            for i in range(n)]


def _rect(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


PLANETAS = [("mercurio", "Mercurio", 14), ("venus", "Venus", 20), ("tierra", "Tierra", 21), ("marte", "Marte", 16),
            ("jupiter", "Júpiter", 42), ("saturno", "Saturno", 36), ("urano", "Urano", 27), ("neptuno", "Neptuno", 26)]


def objetos() -> list[dict]:
    objs = [{"id": "solar:sol", "label": "Sol", "polygon_mm": _circulo(250, 330, 55)}]
    for i, (pid, nombre, r) in enumerate(PLANETAS):
        ang = -0.9 + i * 0.42
        d = 95 + i * 34
        objs.append({"id": f"solar:{pid}", "label": nombre,
                     "polygon_mm": _circulo(250 + d * math.cos(ang), 330 + d * math.sin(ang) * 0.75, r)})
    objs += [
        {"id": "lab:pastillero", "label": "Pastillero", "polygon_mm": _rect(660, 90, 280, 160)},
        {"id": "lab:vaso", "label": "Vaso", "polygon_mm": _circulo(800, 420, 45)},
        {"id": "desk:libreta", "label": "Libreta", "polygon_mm": _rect(600, 262, 150, 210)},
    ]
    return objs


async def armar(mesa: Mesa, pestana: str = "Sistema solar") -> dict:
    lista = await mesa.tool("scenes", {"action": "list"})
    hay = [n.get("name") if isinstance(n, dict) else n for n in (lista.get("scenes") or [])]
    for nombre in ("Sistema solar", "Laboratorio", "Escritorio"):
        if nombre not in hay:
            await mesa.tool("scenes", {"action": "create", "name": nombre})
    await mesa.tool("scenes", {"action": "objects", "objects": objetos()})
    await mesa.tool("scenes", {"action": "activate", "name": pestana})
    return await mesa.tool("scenes", {"action": "list"})

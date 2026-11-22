"""Genera wui-card.stl desde las mismas dimensiones que card.scad.

Como OpenSCAD no está disponible (cask desactivado por Gatekeeper), emulamos las
operaciones en shapely + trimesh: shapes 2D, union de huecos, resta del cuerpo,
extrusión de la placa al alto final. El resultado es un STL binario importable
a cualquier slicer.

Uso:
    /tmp/wui-venv/bin/python hardware/3d/wui-card/build_stl.py
"""

from __future__ import annotations

from pathlib import Path

import trimesh
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

CARD_W = 200.0
CARD_H = 150.0
THICKNESS = 1.6
CORNER_R = 4.0

LOGO_W = 120.0
LOGO_TOP = 15.0
SVG_W = 193.0
SVG_H = 97.0
LOGO_SCALE = LOGO_W / SVG_W
LOGO_X_L = (CARD_W - LOGO_W) / 2


def svg_to_card(x_svg: float, y_svg: float) -> tuple[float, float]:
    return LOGO_X_L + x_svg * LOGO_SCALE, CARD_H - LOGO_TOP - y_svg * LOGO_SCALE


def polygon_from_svg(points: list[tuple[float, float]]) -> Polygon:
    return Polygon([svg_to_card(x, y) for x, y in points])


def wordmark_holes() -> list[Polygon]:
    w = polygon_from_svg(
        [(0, 24), (23, 24), (33, 63), (44, 24), (60, 24), (71, 63), (82, 24),
         (104, 24), (84, 88), (63, 88), (52, 56), (41, 88), (20, 88)]
    )
    u = polygon_from_svg(
        [(108, 24), (129, 24), (129, 60),
         (130, 66), (132, 70), (139, 71), (146, 71), (149, 69), (150, 65),
         (150, 59), (150, 24), (170, 24), (170, 88), (151, 88), (151, 82),
         (146, 87), (140, 89), (133, 89), (118, 89), (108, 78), (108, 61)]
    )
    i_stem = polygon_from_svg([(175, 24), (193, 24), (193, 88), (175, 88)])
    i_dot = polygon_from_svg([(175, 1), (193, 1), (193, 17), (175, 17)])
    return [w, u, i_stem, i_dot]


def marker_punto(cx: float, cy: float):
    main = Point(cx, cy).buffer(5.0, resolution=24)
    sats = [
        Point(cx + dx, cy + dy).buffer(1.5, resolution=16)
        for dx, dy in [(0, 8), (0, -8), (8, 0), (-8, 0)]
    ]
    return unary_union([main, *sats])


def marker_linea(cx: float, cy: float):
    bars = [box(cx - 7, cy + dy - 0.75, cx + 7, cy + dy + 0.75) for dy in (-4, 0, 4)]
    return unary_union(bars)


def marker_arco(cx: float, cy: float):
    outer = Point(cx, cy).buffer(9.0, resolution=32)
    inner = Point(cx, cy).buffer(6.0, resolution=32)
    ring = outer.difference(inner)
    top_mask = box(cx - 11, cy, cx + 11, cy + 10)
    return ring.difference(top_mask)


def marker_contorno(cx: float, cy: float):
    outer = box(cx - 8, cy - 8, cx + 8, cy + 8)
    inner = box(cx - 6, cy - 6, cx + 6, cy + 6)
    frame = outer.difference(inner)
    open_right = box(cx + 1, cy - 9, cx + 11, cy + 9)
    return frame.difference(open_right)


def info_bateria(cx: float, cy: float):
    inner = box(cx - 12 + 1.2, cy - 6 + 1.2, cx + 12 - 1.2, cy + 6 - 1.2)
    return inner.buffer(1.2, resolution=16)


def info_tiempo(cx: float, cy: float):
    inner = box(cx - 15 + 1.2, cy - 5 + 1.2, cx + 15 - 1.2, cy + 5 - 1.2)
    return inner.buffer(1.2, resolution=16)


def info_notificacion(cx: float, cy: float):
    return Point(cx, cy).buffer(7.0, resolution=32)


def card_body() -> Polygon:
    inner = box(CORNER_R, CORNER_R, CARD_W - CORNER_R, CARD_H - CORNER_R)
    return inner.buffer(CORNER_R, resolution=24)


def main() -> None:
    body = card_body()

    holes = []
    holes.extend(wordmark_holes())
    holes.append(marker_punto(18, CARD_H - 18))
    holes.append(marker_linea(CARD_W - 18, CARD_H - 18))
    holes.append(marker_arco(18, 18))
    holes.append(marker_contorno(CARD_W - 18, 18))
    holes.append(info_bateria(CARD_W / 2, CARD_H - LOGO_TOP - 60 - 15))
    holes.append(info_notificacion(CARD_W / 2 - 36, CARD_H - LOGO_TOP - 60 - 40))
    holes.append(info_tiempo(CARD_W / 2 + 20, CARD_H - LOGO_TOP - 60 - 40))

    card_2d = body.difference(unary_union(holes))

    if not card_2d.is_valid:
        raise SystemExit(f"Geometría inválida: {card_2d.is_valid} · {card_2d.geom_type}")

    mesh = trimesh.creation.extrude_polygon(card_2d, THICKNESS)
    out = Path(__file__).parent / "wui-card.stl"
    mesh.export(out)
    print(f"ok: {out.name} · {len(mesh.faces)} caras · {len(mesh.vertices)} vértices · {THICKNESS} mm")


if __name__ == "__main__":
    main()

"""Contrato de percepción de `MOTOR.md` para esta escena: lo que el motor llama con cada foto limpia.

Es la referencia del módulo en Rust (`node/crates/pcx-escena-solar`): mismas entradas, mismas salidas.

    al_limpiar(lienzo, ctx) -> {"objetos": [...], "eventos": [...], "datos": {...}}

- `lienzo`: BGR, la mesa vista desde arriba, 1 px = 1 mm (o `ctx["px_por_mm"]`).
- `ctx["memoria"]`: dict propio de la escena que el motor conserva entre llamadas. Aquí vive la referencia (las bolas
  del primer cuadro), la asignación de planetas y la última pose del canasto.
- `ctx["fijos"]` (opcional): {planeta: [x, y]} decididos a mano, en mm de la referencia.

Objetos que publica (ids con prefijo `solar:`): `solar:sol`, uno por planeta (`solar:jupiter`…) con `center_mm` y
`radius_mm` en la pose actual, y `solar:lamina-N` con su polígono. Eventos: `solar_armado` (la primera vez, con las
dudas), `canasto_movido` (cuando la pose cambia más de 3 mm o 1°) y `canasto_perdido` (si deja de verse).
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .canasto import ubicar_canasto
from .cuerpos import detectar_cuerpos
from .lamina import ubicar_laminas
from .planetas import asignar_planetas, cargar_catalogo
from .pose import aplicar_pose, pose_grupo
from .tipos import Cuerpo, Pose


def _escala(lienzo: np.ndarray, ppm: float) -> np.ndarray:
    if abs(ppm - 1.0) < 1e-3:
        return lienzo
    return cv2.resize(lienzo, None, fx=1 / ppm, fy=1 / ppm, interpolation=cv2.INTER_AREA)


def _fijos_a_indices(fijos_mm: dict, cuerpos: list[Cuerpo]) -> dict[str, int]:
    out = {}
    for pid, pos in (fijos_mm or {}).items():
        d = [(math.hypot(c.centro_mm[0] - pos[0], c.centro_mm[1] - pos[1]), i) for i, c in enumerate(cuerpos) if c.tipo == "bola"]
        if d and min(d)[0] < 30:
            out[pid] = min(d)[1]
    return out


def _objetos(ref: list[Cuerpo], asig: dict, pose: Pose, catalogo: dict) -> list[dict]:
    nombres = {p["id"]: p["nombre"] for p in catalogo["planetas"]}
    ids = ([("sol", asig["sol"], "Sol")] if asig.get("sol") is not None else []) + [(pid, i, nombres[pid]) for pid, i in asig["planetas"].items()]
    pts = aplicar_pose([ref[i].centro_mm for _, i, _ in ids], pose) if ids else []
    return [
        {"id": f"solar:{pid}", "label": nombre, "center_mm": [round(float(p[0]), 1), round(float(p[1]), 1)], "radius_mm": ref[i].radio_mm, "confidence": ref[i].puntaje}
        for (pid, i, nombre), p in zip(ids, pts)
    ]


def al_limpiar(lienzo: np.ndarray, ctx: dict) -> dict | None:
    img = _escala(lienzo, float(ctx.get("px_por_mm", 1.0)))
    mem = ctx.setdefault("memoria", {})
    catalogo = mem.get("catalogo") or cargar_catalogo()
    canasto = ubicar_canasto(img)
    cuerpos = detectar_cuerpos(img, canasto.zona() if canasto else None)
    laminas = ubicar_laminas(img)
    lam_obj = [
        {"id": f"solar:lamina-{n}", "label": "Lámina del sistema solar", "polygon_mm": [list(p) for p in l.esquinas_mm], "center_mm": list(l.centro_mm)}
        for n, l in enumerate(laminas)
    ]
    eventos = []
    if "ref" not in mem:
        if not [c for c in cuerpos if c.tipo == "bola"]:
            return {"objetos": lam_obj, "eventos": [], "datos": {"estado": "sin bolas a la vista"}}
        asig = asignar_planetas(cuerpos, catalogo, _fijos_a_indices(ctx.get("fijos"), cuerpos))
        mem["ref"] = [c.dict() for c in cuerpos]
        mem["asignacion"] = asig.dict()
        mem["pose"] = Pose(0.0, (0.0, 0.0), len(cuerpos), len(cuerpos), 0.0, True).dict()
        eventos.append({"event": "solar_armado", "data": {"planetas": list(asig.planetas), "dudas": asig.dudas, "sol": asig.sol is not None}})
    ref = [Cuerpo(c["tipo"], tuple(c["centro_mm"]), c["radio_mm"], c.get("puntaje", 1), c.get("dientes", 0)) for c in mem["ref"]]
    previa = Pose(**{**mem["pose"], "t_mm": tuple(mem["pose"]["t_mm"]), "pares": []})
    pose = pose_grupo(ref, cuerpos, previa=previa) if cuerpos else Pose(0, (0, 0), 0, len(ref), 0, False)
    if pose.ok:
        if abs(pose.theta_deg - previa.theta_deg) > 1.0 or math.dist(pose.t_mm, previa.t_mm) > 3.0:
            eventos.append({"event": "canasto_movido", "data": {"theta_deg": pose.theta_deg, "t_mm": list(pose.t_mm), "bolas": pose.inliers}})
        mem["pose"] = {**pose.dict(), "pares": []}
    else:
        eventos.append({"event": "canasto_perdido", "data": {"bolas_vistas": len(cuerpos)}})
        pose = previa
    return {
        "objetos": _objetos(ref, mem["asignacion"], pose, catalogo) + lam_obj,
        "eventos": eventos,
        "datos": {"pose": {"theta_deg": pose.theta_deg, "t_mm": list(pose.t_mm)}, "dudas": mem["asignacion"].get("dudas", [])},
    }

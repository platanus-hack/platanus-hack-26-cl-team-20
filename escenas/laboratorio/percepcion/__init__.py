"""Percepción de la escena Laboratorio, con el contrato de MOTOR.md (rama max/motor).

El motor llama:
- `al_limpiar(lienzo, ctx)` con cada foto limpia de la cámara cenital (1 px = 1 mm, o `ctx["px_por_mm"]`): ubica el
  pastillero y publica la grilla 7×4. La primera foto buena queda de referencia; después publica los objetos ajenos
  (`lab:contaminado-N`, por ejemplo un celular) y un evento `contaminacion` cuando aparecen o se van.
- `en_vivo(cuadro, ctx)` con cada cuadro de la cámara secundaria (la que mira el vaso de lado): mide los ml y avisa
  «te pasaste». Barata: unos milisegundos por cuadro.

Lo que necesita `en_vivo` en `ctx["memoria"]` (lo deja el conductor o el agente):
- `vaso_cal`: la calibración (`estado/vaso.json`, ver calibrar_vaso.py), como dict o como ruta. Sin ella no mide.
- `objetivo_ml` y, opcional, `tolerancia_ml`: el objetivo del paso. Sin objetivo publica los ml sin zona.

Ids con prefijo `lab:`. Funciones sin cámara, sin red y sin estado global: el estado vive en `ctx["memoria"]`.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2

from . import intrusos as I
from . import pastillero as P
from . import vaso as V

PALETA = P.PALETA_DEMO


def al_limpiar(lienzo, ctx: dict | None = None) -> dict | None:
    ctx = ctx or {}
    mem = ctx.setdefault("memoria", {})
    px_por_mm = float(ctx.get("px_por_mm", 1.0))
    g = P.detectar(lienzo, mm_por_px=1.0 / px_por_mm, paleta=mem.get("paleta") or PALETA)
    if not g.ok:
        # Tapado por una mano o fuera de cuadro: se mantiene la última grilla buena, si hay.
        if "grilla" in mem:
            return {"datos": {"pastillero": {**mem["grilla"], "visto": False, "motivo": g.motivo}}}
        return {"datos": {"pastillero": {"ok": False, "motivo": g.motivo}}}
    grilla = g.a_dict()
    mem["grilla"] = grilla
    if mem.get("referencia") is None:
        mem["referencia"] = lienzo.copy()  # la mesa preparada: contra esto se buscan objetos ajenos
    celdas = {c.dir: {"centro_mm": c.centro_mm, "poligono_mm": c.poligono_mm, "color": c.color} for c in g.celdas}
    intrusos = [
        o
        for o in I.detectar(mem["referencia"], lienzo, ignorar_poligonos_mm=[g.esquinas_mm], mm_por_px=1.0 / px_por_mm)
        if not mem.get("solo_celular", True) or o["tipo"] == "celular"
    ]
    eventos = []
    if bool(intrusos) != bool(mem.get("_contaminada")):
        mem["_contaminada"] = bool(intrusos)
        eventos.append({"event": "contaminacion", "data": {"hay": bool(intrusos), "objetos": [o["tipo"] for o in intrusos]}})
    return {
        "eventos": eventos,
        "objetos": [
            *(
                {"id": f"lab:contaminado-{k}", "label": f"objeto contaminado ({o['tipo']})", "polygon_mm": o["poligono_mm"], "center_mm": o["centro_mm"]}
                for k, o in enumerate(intrusos, 1)
            ),
            {
                "id": "lab:pastillero",
                "label": "pastillero 7x4",
                "polygon_mm": g.esquinas_mm,
                "center_mm": g.centro_mm,
                "confidence": g.confianza,
            }
        ],
        "datos": {
            "pastillero": {
                "ok": True,
                "visto": True,
                "angulo_deg": g.angulo_deg,
                "paso_mm": g.paso_mm,
                "confianza": g.confianza,
                "celdas": celdas,  # «B3» → centro, polígono y color: para un halo con polygon_mm o una flecha
            }
        },
    }


def _calibracion(mem: dict) -> V.Calibracion | None:
    if isinstance(mem.get("_cal"), V.Calibracion):
        return mem["_cal"]
    fuente = mem.get("vaso_cal")
    if not fuente:
        return None
    if isinstance(fuente, (str, Path)):
        ruta = Path(fuente)
        d = json.loads(ruta.read_text())
        vacio = cv2.imread(str(ruta.with_name(d["vacio"]))) if d.get("vacio") else None
    else:
        d, vacio = fuente, None
        if d.get("vacio_ruta"):
            vacio = cv2.imread(str(d["vacio_ruta"]))
    mem["_cal"] = V.Calibracion.desde_dict(d, vacio=vacio)
    return mem["_cal"]


def en_vivo(cuadro, ctx: dict | None = None) -> dict | None:
    ctx = ctx or {}
    mem = ctx.setdefault("memoria", {})
    cal = _calibracion(mem)
    if cal is None:
        return None
    if "_medidor" not in mem or mem.get("_medidor_cal") is not cal:
        mem["_medidor"], mem["_medidor_cal"] = V.Medidor(cal, n=3), cal
    objetivo = mem.get("objetivo_ml")
    clave = (objetivo, mem.get("tolerancia_ml"))
    if objetivo is not None and mem.get("_umbral_clave") != clave:
        mem["_umbral"] = V.Umbral(float(objetivo), tolerancia_ml=float(mem.get("tolerancia_ml", 3)), histeresis_ml=1.0, cuadros=2)
        mem["_umbral_clave"] = clave
    if objetivo is None:
        mem.pop("_umbral", None)
        mem.pop("_umbral_clave", None)

    r = mem["_medidor"].leer(cuadro)
    nivel = {"ml": r["ml"], "estable": r["estable"], "calidad": r["calidad"]}
    if "_umbral" in mem:
        u = mem["_umbral"].actualizar(r["ml"])
        nivel.update({"objetivo_ml": u["objetivo_ml"], "estado": u["zona"], "cerca": u["cerca"], "exceso_ml": u["exceso_ml"]})
    salida = {"datos": {"vaso": nivel}}

    # Un evento solo cuando cambia algo que se ve: 1 ml entero, la zona o «cerca». No uno por cuadro.
    firma = (None if r["ml"] is None else round(r["ml"]), nivel.get("estado"), nivel.get("cerca"))
    if firma != mem.get("_ultima_firma"):
        mem["_ultima_firma"] = firma
        salida["eventos"] = [{"event": "nivel", "data": {"vaso": "lab:vaso", **nivel}}]
    return salida

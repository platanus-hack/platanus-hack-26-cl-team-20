#!/usr/bin/env python3
"""El conductor de h20 con la capa visual nueva (fondo, esferas, Sol en el engranaje, órbitas de pincel).

    cd escenas/sistema-solar
    uv run python visual/conductor_visual.py armar        # en vez de: uv run python conductor.py armar
    uv run python visual/conductor_visual.py seguir       # en vez de: uv run python conductor.py seguir
    uv run python visual/conductor_visual.py redibujar    # vuelve a proyectar con la capa nueva

Para volver a armar, solo `armar` (nunca `limpiar && armar`): los ids `solar:*` se mantienen entre armados.

Mismos comandos, variables y estado que `conductor.py`: solo cambia lo que se dibuja (`widgets_sistema` y
`widget_orbitas_de`). El seguimiento, los objetos `solar:*`, la lámina, Lumo y las fichas siguen siendo los de h20.
Si algo de la capa nueva falla, avisa por stderr y usa los widgets de siempre.

- Centro de las órbitas: `centro_orbitas` de h20 (el Sol si se ve; si no, el centro del canasto en la pose actual,
  anclado a `solar:canasto`).
- Sol: `est["sol_poligono_mm"]` de h20 (la silueta con dientes en la pose actual); si no viene, la silueta de
  `estado/referencia.jpg` girada con la pose.
- Fantasmas (`est["fantasmas"]`, lo que no se ve ahora): atenuados. Cuando `seguir` vuelve a ver uno y republica los
  objetos, su esfera vuelve a la normal en ese momento (solo esa).
- Solo se envía lo que cambió: `Mesa.project` descarta los widgets de esta capa idénticos al último envío (`armar` y
  `redibujar` mandan todo de nuevo; `Mesa.remove` olvida lo borrado).
- Camino B (`PCX_SIN_OBJETOS=1`, motor + pestaña `solar`): al proyectar lee los `solar:*` del mundo y dibuja sobre
  esos (`visual.desde_mundo`): cada planeta y el Sol anclados a su `object_id`, con su silueta; órbitas en el Sol o en
  `solar:canasto` del motor. Lo que el motor no publica no se dibuja. Ahí no se corre `seguir`.

`SOLAR_FRANJA_MM` (40 por defecto): alto de la franja gris de la consola abajo, que la capa no pinta.
`SOLAR_SIN_FONDO=1`: sin el fondo de espacio (ni la calma bajo las órbitas).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

ESCENA = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ESCENA))

import cv2  # noqa: E402

import conductor  # noqa: E402

import visual as V  # noqa: E402

_widgets_h20 = conductor.widgets_sistema
_orbitas_h20 = conductor.widget_orbitas_de
_proyectar_h20 = conductor.proyectar_todo
_publicar_h20 = conductor.publicar_objetos
_project = conductor.Mesa.project
_remove = conductor.Mesa.remove

MIOS = ("solar-fondo", "solar-fondo-2", "solar-sol", "solar-orbitas")
_enviado: dict[str, str] = {}  # id → último widget enviado (json)
_cerrojo = threading.Lock()
_contorno: dict[tuple, list | None] = {}
_fantasmas_dibujados: set[str] | None = None  # los que se proyectaron atenuados la última vez
_dentro_de_proyectar = threading.local()
_mundo: dict[str, dict] | None = None  # camino B: los solar:* del mundo, leídos al proyectar


def _mio(wid: str) -> bool:
    return wid in MIOS or wid.startswith("solar-planeta-")


def _girada() -> bool:
    return bool(conductor.leer_json(conductor.MESA).get("interfaz", {}).get("girada", False))


def _dudas(est: dict) -> set[str]:
    fijos = set(conductor.leer_json(conductor.MESA).get("fijos", {}))
    return {d["asignado"] for d in est["asignacion"].get("dudas", []) if "asignado" in d} - fijos


def contorno_sol(est: dict, cs: dict) -> list | None:
    """El contorno del engranaje donde está el Sol ahora: el que guarda h20 (`sol_poligono_mm`, ya en la pose actual);
    si no viene, la silueta de `estado/referencia.jpg` (una vez por foto) girada con la pose."""
    a = est["asignacion"]
    if a.get("sol") is None or "sol" not in cs:
        return None
    if est.get("sol_poligono_mm"):
        return est["sol_poligono_mm"]
    ref = conductor.ESTADO.parent / "referencia.jpg"
    if not ref.exists():
        return None
    c = est["cuerpos"][a["sol"]]
    clave = (ref.stat().st_mtime_ns, tuple(c["centro_mm"]), c["radio_mm"])
    if clave not in _contorno:
        img = cv2.imread(str(ref))
        _contorno[clave] = V.contorno_engranaje(img, c["centro_mm"], c["radio_mm"]) if img is not None else None
    rel = _contorno[clave]
    return V.girar_contorno(rel, cs["sol"], float((est.get("pose") or {}).get("theta_deg", 0.0))) if rel else None


def _ancla_canasto(est: dict):
    """El `center_mm` con que h20 publica `solar:canasto` (None si no lo publica)."""
    if os.environ.get("PCX_SIN_OBJETOS") == "1" or not hasattr(conductor, "poligono_canasto"):
        return None
    k = conductor.poligono_canasto(est)
    return (round(k[1][0], 1), round(k[1][1], 1)) if k else None


def camino_b() -> bool:
    """Motor + escena `solar` (`PCX_SIN_OBJETOS=1`): los `solar:*` los publica el motor y se dibuja sobre esos."""
    return os.environ.get("PCX_SIN_OBJETOS") == "1"


def leer_mundo(mesa) -> dict[str, dict] | None:
    """Los objetos `solar:*` del mundo (los que publica el motor), {id: objeto}. None si no se pudo leer."""
    global _mundo
    try:
        objs = mesa.llamar("scene", {}, 20)[0].get("objects", [])
    except Exception as e:  # noqa: BLE001
        print(f"visual: no pude leer los objetos del mundo: {e!r}", file=sys.stderr)
        return _mundo
    _mundo = {o["id"]: o for o in objs if str(o.get("id", "")).startswith(conductor.escena.PREFIJO)}
    planetas = [i for i in _mundo if i.split(":", 1)[1] in V.PLANETAS]
    if not planetas:
        print("visual: el motor no publica ningún solar:<planeta> (¿la pestaña se llama exactamente «solar»?); no dibujo planetas", file=sys.stderr)
    return _mundo


def _dientes_rel(est: dict, cs: dict):
    """El contorno con dientes de la foto de `armar` (h20), relativo al centro del Sol de Python."""
    poly = est.get("sol_poligono_mm")
    if not poly or "sol" not in cs:
        return None
    return [[x - cs["sol"][0], y - cs["sol"][1]] for x, y in poly]


def _capa(est: dict, intro: bool) -> list[dict]:
    cs = conductor.centros_actuales(est)
    if camino_b() and _mundo is not None:
        return V.desde_mundo(
            _mundo,
            tuple(est.get("surface_mm", (1000, 563))),
            intro,
            _girada(),
            os.environ.get("SOLAR_SIN_FONDO") != "1",
            _dientes_rel(est, cs) if est["asignacion"].get("sol") is not None else None,
        )
    return V.desde_estado(
        est,
        cs,
        intro,
        _dudas(est),
        contorno_sol(est, cs),
        _girada(),
        centro=conductor.centro_orbitas(est) if hasattr(conductor, "centro_orbitas") else None,
        zona_mm=conductor.zona_actual(est) if est.get("canasto") else None,
        ancla_canasto=_ancla_canasto(est),
        con_fondo=os.environ.get("SOLAR_SIN_FONDO") != "1",
    )


def widgets_sistema(est: dict, intro: bool) -> list[dict]:
    global _fantasmas_dibujados
    if intro:  # armar / redibujar: todo de nuevo, con su entrada
        with _cerrojo:
            _enviado.clear()
    try:
        ws = _capa(est, intro)
        _fantasmas_dibujados = V.fantasmas_de(est)
        return ws
    except Exception as e:  # noqa: BLE001
        print(f"visual: {e!r}; uso los widgets de h20", file=sys.stderr)
        return _widgets_h20(est, intro)


def widget_orbitas_de(est: dict, intro: bool) -> dict | None:
    try:
        return next((w for w in _capa(est, intro) if w["id"] == "solar-orbitas"), None)
    except Exception as e:  # noqa: BLE001
        print(f"visual: {e!r}; uso las órbitas de h20", file=sys.stderr)
        return _orbitas_h20(est, intro)


def proyectar_todo(mesa, est: dict, intro: bool = False) -> None:
    if camino_b():
        leer_mundo(mesa)  # antes de armar los widgets: se dibuja sobre lo que publica el motor
    _dentro_de_proyectar.si = True
    try:
        return _proyectar_h20(mesa, est, intro)
    finally:
        _dentro_de_proyectar.si = False


def publicar_objetos(mesa, est: dict) -> None:
    """Los objetos de h20 y, si `seguir` volvió a ver un fantasma (o dejó de ver uno), solo esas esferas de nuevo."""
    global _fantasmas_dibujados
    r = _publicar_h20(mesa, est)
    if getattr(_dentro_de_proyectar, "si", False) or _fantasmas_dibujados is None or camino_b():
        return r  # proyectar_todo dibuja todo enseguida; en el camino B los objetos son del motor
    try:
        ahora = V.fantasmas_de(est)
        cambian = ahora ^ _fantasmas_dibujados
        if cambian:
            ws = [w for w in _capa(est, False) if w["id"] in {f"solar-planeta-{p}" for p in cambian} or w["id"] == "solar-orbitas"]
            if ws:
                mesa.project(ws)
            _fantasmas_dibujados = ahora
    except Exception as e:  # noqa: BLE001
        print(f"visual: fantasmas: {e!r}", file=sys.stderr)
    return r


def project(self, widgets: list[dict]) -> dict:
    nuevos, firmas = [], {}
    with _cerrojo:
        for w in widgets:
            wid = w.get("id", "")
            if _mio(wid):
                f = json.dumps(w, sort_keys=True, ensure_ascii=False)
                if _enviado.get(wid) == f:
                    continue  # igual a lo que ya está proyectado: no se vuelve a dibujar
                firmas[wid] = f
            nuevos.append(w)
    if not nuevos:
        return {"ok": True, "sin_cambios": True}
    r = _project(self, nuevos)
    with _cerrojo:
        _enviado.update(firmas)
    return r


def remove(self, ids: list[str]) -> dict:
    with _cerrojo:
        for i in ids:
            _enviado.pop(i, None)
    return _remove(self, ids)


conductor.widgets_sistema = widgets_sistema
conductor.widget_orbitas_de = widget_orbitas_de
conductor.proyectar_todo = proyectar_todo
conductor.publicar_objetos = publicar_objetos
conductor.Mesa.project = project
conductor.Mesa.remove = remove

if __name__ == "__main__":
    conductor.main()

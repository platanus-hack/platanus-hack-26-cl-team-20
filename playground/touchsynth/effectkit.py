"""Pegamento efecto ↔ instrumento: lo comparten croc_synth, touch_draw, marimba y linterna (playground/effects/)."""

from __future__ import annotations

import time

from .instrument import Instrument
from .registry import registry_of, touch_state

PROJ_SIZE = (1920, 1080)


def scene_croc_contour(scene):
    """Contorno del cocodrilo de la escena viva si viene del escaneo en px del proyector (scene_from_scan: sigue a
    nudge y reload_scene); None con la escena demo (en mm), y entonces se usa el archivo del escaneo."""
    if not getattr(scene, "projector_px", False):
        return None
    o = getattr(scene, "objects", {}).get("croc")
    if o is None or not o.contour or len(o.contour) < 3:
        return None
    return o.contour


class InstrumentEffectMixin:
    """Para clases `Effect`: `self._run(scene, factory)` en update y `self._draw(canvas)` en draw."""

    _inst: Instrument | None = None
    _reg = None

    def _iid(self) -> str:
        return self.instance_id or f"anon{id(self)}"

    def _run(self, scene, factory) -> Instrument:
        reg = registry_of(scene)
        ts = touch_state(scene)
        inst = reg.get(self._iid(), factory, last_seq=ts.seq)
        inst.configure(self.p)
        now = time.time()                    # después de construir/configurar (armar un rig puede tardar)
        reg.pump(self._iid(), ts.log, now)
        inst.step(now)
        self._inst, self._reg = inst, reg
        return inst

    def _draw(self, canvas) -> None:
        if self._inst is None:
            return
        img = canvas.img
        self._inst.render(img, img.shape[1] / PROJ_SIZE[0], img.shape[0] / PROJ_SIZE[1], time.time())

    def kill(self) -> None:
        super().kill()
        if self._reg is not None:
            self._reg.drop(self._iid())

    def state(self) -> dict:
        st = super().state()
        if self._inst is not None:
            st["instrument"] = self._inst.status()
        return st


class SceneObjectsMixin:
    """Para efectos que miran todos los objetos de la escena: `self._scene_objs(scene)` (cacheado hasta que cambie
    la geometría: nudge, reload_scene) con el param `objetos` (ids/alias separados por coma; vacío = todos)."""

    _objs_key = None
    _objs: list = []

    def _scene_objs(self, scene, exclude: tuple[str, ...] = ()):
        from .objetos import objects_key, parse_ids, scene_objects_px
        want = tuple(parse_ids(self.p.get("objetos")))
        key = (objects_key(scene), want, exclude)
        if key != self._objs_key:
            objs = scene_objects_px(scene, exclude)
            if want:
                ids = set()
                for q in want:
                    o = scene.find(q)
                    if o is not None:
                        ids.add(o.id)
                objs = [o for o in objs if o.id in ids]
            self._objs, self._objs_key = objs, key
        return self._objs

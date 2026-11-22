"""Juego de memoria (Simón con los objetos de la escena): se encienden en secuencia y hay que tocarlos en orden;
cada ronda suma uno. El puntaje va proyectado en el objeto HUD (param hud, o el plano más chico: hoja, cuadro).
Lógica en playground/touchsynth/interactivo.py (Memoria)."""

from __future__ import annotations

import time

from ..touchsynth.effectkit import InstrumentEffectMixin, SceneObjectsMixin
from ..touchsynth.interactivo import Memoria, pick_hud
from ..touchsynth.objetos import scene_objects_px
from .base import Effect, Canvas


class JuegoMemoria(SceneObjectsMixin, InstrumentEffectMixin, Effect):
    name = "juego_memoria"
    label = "Juego de memoria"
    description = ("Simón con los objetos: se encienden en orden (con su nota, canal 4) y hay que tocarlos igual; "
                   "acierto = la secuencia crece en uno, error = parpadeo rojo y vuelve a empezar. Puntaje, nivel y "
                   "récord proyectados en el HUD: param hud (objeto) o el plano más chico de la escena (hoja, "
                   "cuadro). objetos = cuáles juegan (vacío = todos menos el HUD). Tocar el HUD reinicia. Jugar por "
                   "comando: pg_touch target 'memoria' (el que toca ahora) o 'memoria:N'.")
    aliases = ("memoria", "juego", "simon", "simón dice", "juguemos", "juego de memoria", "secuencia")
    needs = ("contours", "touch")
    targets = ("*",)
    params = {**{k: v for k, v in Memoria.DEFAULTS.items()}, "objetos": "", "hud": "", "capa": 8}

    def update(self, dt, scene):
        super().update(dt, scene)
        objs = self._scene_objs(scene)
        inst = self._run(scene, lambda: Memoria(params=self.p))
        hud_q = str(self.p.get("hud") or "")
        if getattr(inst, "_pushed", None) is not objs or getattr(inst, "_hud_q", None) != hud_q:
            hud = pick_hud(scene_objects_px(scene), hud_q, scene)      # el HUD sale de todos, no solo de los que juegan
            players = [o for o in objs if hud is None or o.id != hud.id]
            inst.set_objects(players, hud)
            inst._pushed, inst._hud_q = objs, hud_q
            if inst.state == "pausa" and inst.t_state == 0.0 and players:
                inst.start(time.time())                                # arranca solo al aparecer

    def draw(self, canvas: Canvas):
        self._draw(canvas)

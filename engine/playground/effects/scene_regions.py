"""Expose prepared objects without emitting light or touching acquisition."""
from .base import Effect
import importlib
from .. import appearance
install_scene_regions = importlib.reload(appearance).install_scene_regions


class SceneRegions(Effect):
    name = "scene_regions"
    label = "Objetos segmentados de la escena"
    description = "Carga máscaras preparadas y nombres para seleccionar objetos. No emite luz."
    targets = ("*",)

    def update(self, dt, scene):
        super().update(dt, scene)
        self.object_ids = install_scene_regions(scene)

    def state(self):
        return {**super().state(), "objects":getattr(self,"object_ids",[])}

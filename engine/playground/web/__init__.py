"""Visor 3D web del playground: estáticos en /web y la escena 3D en /api/scene3d (ver scene3d.py)."""

from .scene3d import mount_web, build_scene3d

__all__ = ["mount_web", "build_scene3d"]

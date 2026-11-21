"""Detección tecla por tecla del teclado en el espacio del proyector (pedido 006).

    python -m playground.keys captures/scan2      # → keys.json + keys_verify.png + keys_project.png
    python -m playground.keys.refine captures/scan5   # afinar tecla por tecla con capturas (Osmo + Brio)

detect.py: borde a borde · fit.py: modelo entero por ECC (perspectiva fuerte) · refine.py: proyectar, capturar, corregir.
"""

from .detect import detectar_teclas, estimar_rect, nombre_nota

__all__ = ["detectar_teclas", "estimar_rect", "nombre_nota"]

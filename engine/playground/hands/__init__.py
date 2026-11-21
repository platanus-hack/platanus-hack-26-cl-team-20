"""Manos y mesa táctil: webcam de pared (Brio, en el PC) → manos → toques en px del proyector (y mm) → runtime.

    PC  windows/hands_service.py   Brio + MediaPipe → WS :8793 (manos) · HTTP :8794 (/frame.jpg, /frame.png, /health)
    Mac client.HandsClient         estado más reciente, reconexión sola, desfase de relojes
        calib.Calibration          brio_px → proj_px (plano de la mesa) y proj_px → mm
        touch.TouchTracker         down / move / up con histéresis, EMA, zona muerta de 3 px y zonas excluidas
        bridge.TouchBridge         POST /api/command {"cmd": "touch"} (o rt.emit si el runtime aún no lo tiene)

CLI: `python -m playground.hands --help`. Guía: playground/hands/README.md.
"""

from .calib import Calibration
from .client import HandsClient
from .model import Hand, HandsFrame
from .touch import TouchConfig, TouchEvent, TouchTracker, Zones

__all__ = ["Calibration", "HandsClient", "Hand", "HandsFrame", "TouchConfig", "TouchEvent", "TouchTracker", "Zones"]

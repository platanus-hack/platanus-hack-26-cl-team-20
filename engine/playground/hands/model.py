"""Modelo de mano: los 21 landmarks de MediaPipe, el mensaje del servicio de manos y geometría básica.

Mensaje que publica `windows/hands_service.py` por WebSocket (puerto 8793):

    {"type": "hands", "t": <epoch s de la captura, reloj del PC>, "seq": n, "size": [1920, 1080],
     "detector": "mediapipe-tasks", "hands": [
        {"id": 3, "handed": "Right", "score": 0.97,
         "landmarks": [[x, y, z], ...21],   # px de la Brio; z relativo a la muñeca, misma escala (negativo = más cerca)
         "tip_index": [x, y],               # landmark 8 (punta del índice)
         "pinch": false}]}

Índices de MediaPipe: 0 muñeca; pulgar 1–4; índice 5–8; medio 9–12; anular 13–16; meñique 17–20 (el último de cada
dedo es la punta).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

N_LANDMARKS = 21
WRIST, THUMB_TIP, INDEX_MCP, INDEX_TIP, MIDDLE_MCP, PINKY_MCP = 0, 4, 5, 8, 9, 17


def palm_size(pts: np.ndarray) -> float:
    """Tamaño de la palma en las unidades de `pts` (21 x 2 o 3): media de muñeca→nudillo medio y ancho de nudillos.

    Son los puntos más rígidos de la mano (no cambian al doblar los dedos), así que el tamaño solo cambia con la
    distancia a la cámara y con la inclinación de la palma.
    """
    p = np.asarray(pts, np.float64)[:, :2]
    return float((np.linalg.norm(p[WRIST] - p[MIDDLE_MCP]) + np.linalg.norm(p[INDEX_MCP] - p[PINKY_MCP])) / 2)


def pinch_ratio(pts: np.ndarray) -> float:
    """Distancia pulgar–índice relativa a la palma (≈ 0,1 juntos, > 0,6 abiertos)."""
    p = np.asarray(pts, np.float64)[:, :2]
    return float(np.linalg.norm(p[THUMB_TIP] - p[INDEX_TIP]) / max(palm_size(p), 1e-6))


@dataclass
class Hand:
    id: int | str
    handed: str
    landmarks: np.ndarray                  # (21, 3) px de la Brio
    tip_index: tuple[float, float]
    pinch: bool = False
    score: float = 0.0

    @classmethod
    def from_json(cls, d: dict) -> "Hand":
        lm = np.asarray(d.get("landmarks") or np.zeros((N_LANDMARKS, 3)), np.float64).reshape(-1, 3)
        tip = d.get("tip_index") or (lm[INDEX_TIP, :2].tolist() if len(lm) > INDEX_TIP else [0.0, 0.0])
        return cls(d.get("id", 0), d.get("handed", "?"), lm, (float(tip[0]), float(tip[1])),
                   bool(d.get("pinch", False)), float(d.get("score") or 0.0))

    def to_json(self) -> dict:
        return {"id": self.id, "handed": self.handed, "score": round(self.score, 3),
                "landmarks": np.round(self.landmarks, 2).tolist(), "tip_index": [round(v, 2) for v in self.tip_index],
                "pinch": self.pinch}


@dataclass
class HandsFrame:
    t: float                               # captura en el reloj del PC
    seq: int
    hands: list[Hand]
    size: tuple[int, int] = (1920, 1080)
    detector: str = ""
    received: float = 0.0                  # llegada en el reloj local
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, msg: dict, received: float = 0.0) -> "HandsFrame":
        size = msg.get("size") or (1920, 1080)
        return cls(float(msg.get("t", 0.0)), int(msg.get("seq", 0)), [Hand.from_json(h) for h in msg.get("hands", [])],
                   (int(size[0]), int(size[1])), str(msg.get("detector", "")), received, msg)


# --------------------------------------------------------------------- mano sintética (tests y modo --fake)

# Mano derecha abierta, palma hacia la cámara, en unidades de "muñeca→nudillo medio = 1"; y crece hacia abajo
# (coordenadas de imagen), así que los dedos apuntan hacia -y.
TEMPLATE = np.array([
    [0.00, 0.00],
    [-0.35, -0.15], [-0.60, -0.35], [-0.80, -0.55], [-0.95, -0.70],
    [-0.25, -0.95], [-0.30, -1.35], [-0.32, -1.60], [-0.34, -1.80],
    [0.00, -1.00], [0.00, -1.45], [0.00, -1.72], [0.00, -1.95],
    [0.22, -0.93], [0.27, -1.33], [0.30, -1.57], [0.32, -1.77],
    [0.42, -0.80], [0.52, -1.10], [0.58, -1.30], [0.62, -1.47],
], np.float64)


def synthetic_hand(cx: float, cy: float, scale: float = 80.0, angle_deg: float = 0.0, pinch: bool = False,
                   z: float = 0.0) -> np.ndarray:
    """Landmarks (21, 3) de una mano con la punta del índice en (cx, cy); `scale` = muñeca→nudillo medio en px."""
    t = TEMPLATE.copy()
    if pinch:                                          # el pulgar va a la punta del índice
        t[THUMB_TIP] = t[INDEX_TIP] + (0.05, 0.05)
        t[3] = (t[2] + t[THUMB_TIP]) / 2
    a = math.radians(angle_deg)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    pts = (t - t[INDEX_TIP]) @ rot.T * scale + (cx, cy)
    out = np.zeros((N_LANDMARKS, 3))
    out[:, :2] = pts
    out[:, 2] = z
    return out

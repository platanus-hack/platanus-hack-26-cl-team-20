"""Manos con MediaPipe sobre los mismos cuadros que ve DAViD (modo VIDEO, 2+ manos).

Devuelve landmarks en px del cuadro con z en px (MediaPipe da z relativa a la muñeca en la escala del ancho;
negativa = más cerca). Mismo modelo que el resto del repo: /Volumes/Externo/models/mediapipe/hand_landmarker.task.
Suaviza las puntas con un EMA por mano (la luz no tiembla).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

MODEL = Path("/Volumes/Externo/models/mediapipe/hand_landmarker.task")


class Hands:
    def __init__(self, num_hands: int = 4, det_width: int = 960, ema: float = 0.5, model: Path = MODEL):
        import mediapipe as mp
        from mediapipe.tasks import python as mpt
        from mediapipe.tasks.python import vision as mpv
        self._mp = mp
        self.det = mpv.HandLandmarker.create_from_options(mpv.HandLandmarkerOptions(
            base_options=mpt.BaseOptions(model_asset_path=str(model)), running_mode=mpv.RunningMode.VIDEO,
            num_hands=num_hands, min_hand_detection_confidence=0.4, min_hand_presence_confidence=0.4,
            min_tracking_confidence=0.4))
        self.det_width = det_width
        self.ema = ema
        self._prev: dict[str, np.ndarray] = {}
        self._last_ts = -1

    def __call__(self, frame_bgr: np.ndarray, t_ms: int) -> list[dict]:
        h, w = frame_bgr.shape[:2]
        s = min(1.0, self.det_width / w)
        small = cv2.resize(frame_bgr, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else frame_bgr
        ts = max(self._last_ts + 1, int(t_ms)); self._last_ts = ts
        r = self.det.detect_for_video(self._mp.Image(image_format=self._mp.ImageFormat.SRGB,
                                                     data=np.ascontiguousarray(small[..., ::-1])), ts)
        out, seen = [], set()
        for i, lm in enumerate(r.hand_landmarks):
            pts = np.array([[p.x * w, p.y * h, p.z * w] for p in lm], np.float32)
            handed = r.handedness[i][0].category_name if r.handedness else "?"
            key = f"{handed}{i if handed == '?' else ''}"
            if key in seen:
                key = f"{key}{i}"
            seen.add(key)
            prev = self._prev.get(key)
            if prev is not None and self.ema > 0 and np.linalg.norm(prev[0, :2] - pts[0, :2]) < w * 0.15:
                pts = prev * self.ema + pts * (1 - self.ema)
            self._prev[key] = pts
            out.append({"handed": handed, "landmarks": pts, "score": float(r.handedness[i][0].score) if r.handedness else 0.0})
        for k in list(self._prev):
            if k not in seen:
                del self._prev[k]
        return out

    def close(self) -> None:
        self.det.close()

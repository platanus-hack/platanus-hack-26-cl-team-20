"""Cuadro plano: reconoce un objeto tipo cuadro (plano rectangular) y proyecta sobre él, rectificado,
el feed de la cámara general (o un patrón si no hay cámara)."""

from __future__ import annotations

import numpy as np
import cv2

from .base import Effect, Canvas, bgr


class FrameFeed(Effect):
    name = "frame_feed"
    label = "Feed en el cuadro"
    description = "Sobre un cuadro (plano rectangular) proyecta el feed de la cámara, rectificado a sus 4 esquinas; sin cámara, un test card que respira."
    needs = ("planes", "frames", "camera")
    targets = ("frame", "screen", "table")
    params = {"source": "camera", "border": [255, 255, 255], "opacity": 0.9}

    def update(self, dt, scene):
        super().update(dt, scene)
        self.obj = self.object(scene)
        self.frame = scene.camera_frame if self.p["source"] == "camera" else None

    def _testcard(self, w, h):
        img = np.zeros((h, w, 3), np.uint8)
        bars = [bgr(c) for c in ((255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255))]  # RGB
        for i, c in enumerate(bars):
            cv2.rectangle(img, (i * w // 7, 0), ((i + 1) * w // 7, h), c, -1)
        k = int(20 + 10 * np.sin(self.t * 2))
        cv2.circle(img, (w // 2, h // 2), k, (0, 0, 0), 3)
        return img

    def draw(self, canvas: Canvas):
        if self.obj is None or len(self.obj.contour) < 4:
            return
        quad = np.array([canvas.project(x, y) for x, y in self.obj.contour[:4]], np.float32)
        src = self.frame if self.frame is not None else self._testcard(320, 240)
        h, w = src.shape[:2]
        H = cv2.getPerspectiveTransform(np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32), quad)
        warped = cv2.warpPerspective(src, H, canvas.size)
        mask = np.zeros(canvas.img.shape[:2], np.uint8)
        cv2.fillPoly(mask, [quad.astype(np.int32).reshape(-1, 1, 2)], 255)
        a = float(self.p["opacity"])
        region = mask > 0
        canvas.img[region] = (canvas.img[region] * (1 - a) + warped[region] * a).astype(np.uint8)
        cv2.polylines(canvas.img, [quad.astype(np.int32).reshape(-1, 1, 2)], True, bgr(self.p["border"]), 2, cv2.LINE_AA)

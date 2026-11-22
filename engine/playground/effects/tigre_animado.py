"""Flowing tiger stripes, clipped to the prepared ear pixels."""
import math

import cv2
import numpy as np

from ..pixel_animation import PixelAnimation


class TigreAnimado(PixelAnimation):
    name = "tigre_animado"
    label = "Orejas de tigre"
    aliases = ("orejas de tigre", "rayas de tigre")
    description = "Rayas naranja y negro que fluyen por cada oreja; solo dentro de su máscara real."
    needs = ("textures", "contours", "beat")
    params = {**PixelAnimation.params, "speed": .35, "spacing": 38., "intensity": 1., "capa": 20}

    def update(self, dt, scene):
        super().update(dt, scene)
        if getattr(self, "_pattern_mask", None) is not self._mask:
            self._pattern_mask = self._mask
            ys, xs = np.nonzero(self.mask)
            xy = np.column_stack((xs, ys)).astype(np.float32)
            center = xy.mean(axis=0)
            _, axes = np.linalg.eigh(np.cov(xy.T))
            axis = axes[:, -1].astype(np.float32)
            if axis[1] < 0:
                axis = -axis
            x, y, _, _ = self.roi
            dx, dy = self.xx-x-center[0], self.yy-y-center[1]
            self.along = dx*axis[0]+dy*axis[1]
            self.across = -dx*axis[1]+dy*axis[0]
            self.shade = .72+.28*np.sqrt(np.clip(self.distance/max(1., float(self.distance.max())), 0, 1))

    def draw(self, canvas):
        if not hasattr(self, "mask"):
            return
        if canvas.size != self.size or canvas.px_per_mm != 1 or canvas.flip_y or canvas.origin_px != (0., 0.):
            raise ValueError("Tiger stripes require native projector pixels")
        spacing = float(np.clip(float(self.p["spacing"]), 12, 120))
        t = self.t*float(self.p["speed"])
        # Bent and forked stripes, with alternating tapered ends across the ear.
        u, v = self.along/spacing, self.across/spacing
        key = (id(self.along),spacing)
        if key != getattr(self,"_stripe_key",None):
            self._stripe_key = key
            self.stripe_phase = 2*math.pi*(u+.13*np.sin(v*2.8+u*1.9)+.05*np.sin(v*7-u*3))
            self.stripe_width = .42+.18*np.sin(v*2.3+u*2.1)
            orange = np.empty((*u.shape,3),np.float32)
            orange[:] = (12,116,255)
            orange[...,1] += 18*np.sin(u*1.2-v)
            self.stripe_base = self.shade[...,None]*np.array([5,7,12],np.float32)
            self.stripe_delta = (orange-np.array([5,7,12],np.float32))*self.shade[...,None]
        stripe = np.clip((np.cos(self.stripe_phase-2*math.pi*t)-self.stripe_width)*7,0,1)
        layer = cv2.add(self.stripe_base,cv2.multiply(self.stripe_delta,cv2.merge([1-stripe]*3)))
        gain = float(np.clip(float(self.p["intensity"]), 0, 2))*(1+.12*self.level)
        layer = cv2.convertScaleAbs(layer,alpha=gain)
        x, y, w, h = self.roi
        layer = cv2.resize(layer, (w, h))
        cv2.copyTo(layer, self.mask8, canvas.img[y:y+h, x:x+w])

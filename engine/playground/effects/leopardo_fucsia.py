"""Breathing fuchsia rosettes on the rabbit body, with exact ear subtraction."""
import math

import cv2
import numpy as np

from ..pixel_animation import PixelAnimation
from ..appearance import mask_for_object, texture_path, stamp


class LeopardoFucsia(PixelAnimation):
    name = "leopardo_fucsia"
    label = "Minto leopardo fucsia"
    aliases = ("conejo leopardo", "leopardo fucsia", "minto fucsia")
    needs = ("textures", "contours", "beat")
    description = "Rosetas fucsia y negro con deriva y respiración; cuerpo del conejo menos sus orejas reales."
    params = {**PixelAnimation.params, "speed": .22, "spacing": 48., "intensity": 1., "capa": 20}

    @staticmethod
    def _tile():
        size = 288
        tile = np.full((size, size, 3), (168, 20, 245), np.uint8)
        rng = np.random.default_rng(13)
        for row in range(6):
            for col in range(6):
                cx, cy = (np.array([col, row])+.5)*48+rng.uniform(-9, 9, 2)
                rx, ry = rng.uniform(13, 20, 2)
                angle = np.linspace(0, 2*math.pi, 37)
                radius = 1+.14*np.sin(angle*3+rng.uniform(0, 6))+.09*np.sin(angle*5)
                points = np.column_stack((np.cos(angle)*rx*radius, np.sin(angle)*ry*radius))
                for ox in (-size, 0, size):
                    for oy in (-size, 0, size):
                        center = np.array([cx+ox, cy+oy])
                        inner = np.rint(points*.83+center).astype(np.int32)
                        cv2.fillPoly(tile, [inner], (105, 8, 170), cv2.LINE_AA)
                        ring = np.rint(points+center).astype(np.int32)
                        # Broken irregular rings are rosettes, not uniform polka dots.
                        for start, end in ((1, 11), (14, 23), (26, 34)):
                            cv2.polylines(tile, [ring[start:end]], False, (9, 4, 16), 4, cv2.LINE_AA)
                        cv2.circle(tile, tuple(np.rint(center+[23, 20]).astype(int)), 3, (12, 4, 20), -1, cv2.LINE_AA)
        return tile

    def update(self, dt, scene):
        super().update(dt, scene)
        ears = []
        for oid in ('oreja_izquierda', 'oreja_superior'):
            obj = scene.objects.get(oid)
            ear = mask_for_object(scene, obj) if obj else None
            if ear is None:
                raise ValueError("Leopard body needs both prepared ear masks")
            ears.append(ear)
        path = texture_path(scene.scan_dir)
        key = (id(self._mask), *(id(ear) for ear in ears), str(path), stamp(path))
        if key != getattr(self, '_body_key', None):
            self._body_key = key
            x, y, w, h = self.roi
            body = self.mask.copy()
            for ear in ears: body &= ~ear[y:y+h, x:x+w]
            self.body8 = body.astype(np.uint8)*255
            distance = cv2.distanceTransform(np.pad(body.astype(np.uint8), 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
            size = (self.xx.shape[1], self.xx.shape[0])
            shape = cv2.resize(distance, size)/max(1., float(distance.max()))
            photo = cv2.imread(str(path))
            if photo is None:
                raise ValueError("Unreadable rabbit appearance")
            gray = cv2.cvtColor(photo[y:y+h, x:x+w], cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(cv2.GaussianBlur(gray, (0, 0), 5), size).astype(np.float32)/255
            self.shade = .66+.23*np.sqrt(shape)+.11*gray
            self.tile = self._tile()

    def draw(self, canvas):
        if not hasattr(self, 'body8'):
            return
        if canvas.size != self.size or canvas.px_per_mm != 1 or canvas.flip_y or canvas.origin_px != (0., 0.):
            raise ValueError("Leopard texture requires native projector pixels")
        t = self.t*float(self.p['speed'])
        spacing = float(np.clip(float(self.p['spacing']), 20, 100))
        breath = 1+.025*math.sin(t*2*math.pi)+.015*self.level
        u = ((self.xx-self.xx.mean())/breath*48/spacing+t*9).astype(np.float32)
        v = ((self.yy-self.yy.mean())/breath*48/spacing+t*5).astype(np.float32)
        layer = cv2.remap(self.tile, u, v, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
        gain = self.shade*float(np.clip(float(self.p['intensity']), 0, 2))*(1+.10*self.level)
        layer = cv2.multiply(layer, cv2.merge([gain]*3), dtype=cv2.CV_8U)
        x, y, w, h = self.roi
        cv2.copyTo(cv2.resize(layer, (w, h)), self.body8, canvas.img[y:y+h, x:x+w])

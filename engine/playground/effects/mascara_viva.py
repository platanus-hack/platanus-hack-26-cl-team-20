"""Animated real mask contours and texture-detected eyes, with bass response."""
import math

import cv2
import numpy as np

from .base import bgr
from ..pixel_animation import PixelAnimation
from ..appearance import texture_path, stamp


def detect_eyes(photo, mask):
    """Pick a bilateral pair of compact dark texture components, or no eyes.

    Upper-face/paired priors only rank evidence; they never create coordinates
    when the texture has no contrasting eye components.
    """
    h, w = mask.shape
    gray = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY)
    k = max(13, int(w/6) | 1)
    contrast = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    yy, xx = np.mgrid[:h, :w]
    search = mask & (yy > .09*h) & (yy < .46*h) & (xx > .12*w) & (xx < .88*w)
    selected = ((contrast > 24) & (gray < 95) & search).astype(np.uint8)
    selected = cv2.morphologyEx(selected, cv2.MORPH_OPEN,
                               cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, labels, stats, centers = cv2.connectedComponentsWithStats(selected)
    candidates = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        compact = area/max(1, bw*bh)
        if not (max(7, w*h*.00015) <= area <= w*h*.025 and .35 < bw/max(1, bh) < 4 and compact > .40):
            continue
        strength = float(contrast[labels == i].mean())*math.sqrt(area)*compact
        candidates.append((i, centers[i], (int(x), int(y), int(bw), int(bh)), strength))
    pairs = []
    for left in candidates:
        for right in candidates:
            lx, ly = left[1]; rx, ry = right[1]
            if not (.15*w < lx < .48*w and .52*w < rx < .85*w and abs(ly-ry) < .10*h and rx-lx > .25*w):
                continue
            symmetry = abs((lx+rx)/2-w/2)/w + abs(ly-ry)/h
            score = (left[3]+right[3])/(1+12*symmetry+3*abs((ly+ry)/2/h-.23))
            pairs.append((score, left, right))
    if not pairs:
        return []
    _, left, right = max(pairs, key=lambda p: p[0])
    return [dict(center=tuple(map(float, c[1])), box=c[2], mask=(labels == c[0])) for c in (left, right)]


class MascaraViva(PixelAnimation):
    name = "mascara_viva"
    label = "Máscara azteca viva"
    aliases = ("máscara viva", "ojos de la máscara", "máscara azteca animada")
    needs = ("textures", "contours", "beat")
    description = "Bordes reales con trazo móvil, líneas de la textura y ojos detectados que miran/parpadean al bajo."
    params = {**PixelAnimation.params, "speed": .35, "width": 3., "color": [55, 255, 220],
              "intensity": 1., "textura": .5, "ojos": 1., "capa": 30}

    def update(self, dt, scene):
        super().update(dt, scene)
        path = texture_path(scene.scan_dir)
        key = (id(self._mask), str(path), stamp(path))
        if key != getattr(self, '_face_key', None):
            self._face_key = key
            x, y, w, h = self.roi
            photo = cv2.imread(str(path))
            if photo is None:
                raise ValueError("Unreadable mask appearance")
            self.photo = photo[y:y+h, x:x+w].copy()
            gray = cv2.cvtColor(cv2.GaussianBlur(self.photo, (3, 3), .7), cv2.COLOR_BGR2GRAY)
            self.edges8 = cv2.Canny(gray, 45, 115)
            self.edges8[~self.mask] = 0
            # Pad before tracing: components touching the crop edge remain closed.
            contours, _ = cv2.findContours(np.pad(self.mask8, 1), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
            self.contours = [c.reshape(-1, 2)-1 for c in contours if len(c) >= 6]
            self.eyes = detect_eyes(self.photo, self.mask)
            for eye in self.eyes:
                ex, ey, ew, eh = eye['box']
                eye['clip8'] = eye['mask'][ey:ey+eh, ex:ex+ew].astype(np.uint8)
                eye['rows'] = np.arange(eh, dtype=np.float32)[:, None]+ey
            self.py, self.px = np.mgrid[:h, :w].astype(np.float32)

    def draw(self, canvas):
        if not hasattr(self, 'photo'):
            return
        if canvas.size != self.size or canvas.px_per_mm != 1 or canvas.flip_y or canvas.origin_px != (0., 0.):
            raise ValueError("Animated mask requires native projector pixels")
        t = self.t*float(self.p['speed'])
        pulse = .65+.35*math.sin(t*2*math.pi)**2+.6*self.level
        layer = cv2.convertScaleAbs(self.photo, alpha=float(np.clip(float(self.p['textura']), 0, 1)))
        color = bgr(self.p['color'])
        if color != getattr(self, '_edge_color', None):
            self._edge_color = color
            self.colored_edges = cv2.merge([cv2.convertScaleAbs(self.edges8, alpha=c/255) for c in color])
        wave = (.25+.55*(.5+.5*np.sin(self.px*.045+self.py*.035-t*7)))*pulse
        edge_layer = cv2.multiply(self.colored_edges, cv2.merge([wave]*3), dtype=cv2.CV_8U)
        layer = cv2.add(layer, edge_layer)
        width = max(1, min(10, round(float(self.p['width']))))
        cv2.polylines(layer, self.contours, True, tuple(int(v*.35*pulse) for v in bgr(self.p['color'])), width, cv2.LINE_AA)
        for index, contour in enumerate(self.contours):
            count = len(contour)
            start = int((t*.7+index*.17) % 1*count)
            trail = contour[(start+np.arange(max(3, count//5))) % count]
            cv2.polylines(layer, [trail], False, (100, 235, 255), width+1, cv2.LINE_AA)
        eye_gain = float(np.clip(float(self.p['ojos']), 0, 2))
        if eye_gain:
            openness = 1-.96*math.exp(-((t % 1.7-1.52)/.065)**2)
            for eye in self.eyes:
                cx, cy = eye['center']; ex, ey, ew, eh = eye['box']
                visible = (eye['clip8'] > 0) & (np.abs(eye['rows']-cy) <= eh*.55*openness)
                eye_layer = np.zeros((eh, ew, 3), np.uint8)
                eye_layer[visible] = (190, 235, 255)
                gx, gy = round(cx-ex+ew*.15*math.sin(t*2.1)), round(cy-ey+eh*.08*math.sin(t*1.3))
                radius = max(2, round(min(ew, eh)*.30))
                cv2.circle(eye_layer, (gx, gy), radius+2, (220, 145, 15), -1, cv2.LINE_AA)
                cv2.circle(eye_layer, (gx, gy), radius, (10, 5, 8), -1, cv2.LINE_AA)
                cv2.circle(eye_layer, (gx-1, gy-1), 1, (255, 255, 255), -1, cv2.LINE_AA)
                eye_layer[~visible] = 0
                eye_layer = cv2.convertScaleAbs(eye_layer, alpha=eye_gain*(.85+.3*self.level))
                cv2.copyTo(eye_layer, eye['clip8'], layer[ey:ey+eh, ex:ex+ew])
        layer = cv2.convertScaleAbs(layer, alpha=float(np.clip(float(self.p['intensity']), 0, 2)))
        x, y, w, h = self.roi
        cv2.copyTo(layer, self.mask8, canvas.img[y:y+h, x:x+w])

    def state(self):
        eyes = getattr(self, 'eyes', [])
        x, y = self.roi[:2] if hasattr(self, 'roi') else (0, 0)
        return {**super().state(), 'eyes_detected':len(eyes),
                'eyes_px':[[round(e['center'][0]+x, 1), round(e['center'][1]+y, 1)] for e in eyes],
                'eye_status':'texture_detected' if eyes else 'not_detected'}

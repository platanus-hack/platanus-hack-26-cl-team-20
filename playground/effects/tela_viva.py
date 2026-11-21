"""Pinned PBD cloth, hidden colliders and relit scan texture in projector pixels.

The verified h12 pixel mask is authoritative. No geometry is inferred from the
runtime quad. The renderer is a bounded 2.5D approximation (no folded/overhanging
cloth): invert the projected displacement field to transport material UVs.
"""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from .base import Effect
from ..audio.features import audio_level
from ..vector_animation import _hash, _read_bundle, _stamp
from ..appearance import texture_path, install_scene_regions


def _number(value, lo, hi):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("tela_viva: parameters must be finite")
    return min(hi, max(lo, value))


class TelaViva(Effect):
    name = "tela_viva"
    label = "Algo sale de la tela"
    aliases = ("cara en la tela", "cara saliendo", "tela viva", "cloth")
    needs = ("textures", "contours", "beat")
    targets = ("frame",)
    default_target = "cuadro"
    params = dict(forma="cara", fuerza=1., velocidad=1., sombreado=1.,
                  rigidez=.65, audio="", audio_gain=1., bundle="", capa=10)
    description = ("Tela con bordes fijos en la máscara real del cuadro: cara, mano o esfera "
                   "empujan desde atrás. fuerza 0..2, velocidad 0..3, sombreado 0..2, "
                   "rigidez 0..1, audio='' o 'bass'. Usa la apariencia limpia y su bundle verificado.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._key = None
        self.status = "waiting_for_bundle"
        self.phase = 0.
        self.accumulator = 0.
        self.level = 0.
        self.steps = 0
        self.origin = (0, 0)

    def _prepare(self, source, data, mask):
        self.mask = mask
        self.mask8 = mask.astype(np.uint8) * 255
        h, w = mask.shape
        x, y = self.origin
        photo = cv2.imread(str(source))
        if photo is None or photo.shape[1::-1] != self.projector_size:
            raise ValueError("tela_viva: source image size mismatch")
        self.texture = photo[y:y+h, x:x+w].copy()
        # Include a zero border: a real mask can touch the crop edges.
        distance = cv2.distanceTransform(np.pad(mask.astype(np.uint8), 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
        self.edge = np.minimum(distance / 18., 1.).astype(np.float32)
        self.edge[distance <= 2] = 0
        self.border8 = ((distance <= 2) & mask).astype(np.uint8)*255
        # At most 4,225 particles, independent of full output resolution.
        nx, ny = max(5, min(65, math.ceil(w/16)+1)), max(5, min(65, math.ceil(h/16)+1))
        xx, yy = np.meshgrid(np.linspace(0, w-1, nx, dtype=np.float32),
                             np.linspace(0, h-1, ny, dtype=np.float32))
        self.rest = np.stack((xx, yy, np.zeros_like(xx)), axis=-1)
        self.pos = self.rest.copy()
        self.vel = np.zeros_like(self.pos)
        self.spacing = ((w-1)/(nx-1), (h-1)/(ny-1))
        d = cv2.remap(distance, xx, yy, cv2.INTER_LINEAR)
        self.free = d > 1.5 * max(self.spacing)
        self.free[[0, -1], :] = False
        self.free[:, [0, -1]] = False
        # Disjoint edge sets permit vectorized Gauss-Seidel without scatter races.
        self.edges = []
        weights = self.free.astype(np.float32)[..., None]
        for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
            for parity in (0, 1):
                x0, x1 = max(0, -dx), min(nx, nx-dx)
                if dy == 0:
                    a = (slice(None), slice(parity, nx-1, 2))
                    b = (slice(None), slice(parity+1, nx, 2))
                else:
                    a = (slice(parity, ny-1, 2), slice(x0, x1))
                    b = (slice(parity+1, ny, 2), slice(x0+dx, x1+dx))
                denom = np.maximum(weights[a]+weights[b], 1)
                self.edges.append((a, b, math.hypot(dx*self.spacing[0], dy*self.spacing[1]),
                                   weights[a]/denom, weights[b]/denom))
        self.grid_x, self.grid_y = xx, yy
        # Position comes from actual mask interior, never the invented runtime quad.
        _, radius, _, center = cv2.minMaxLoc(distance)
        self.center = center
        self.radius = radius
        self.scale = max(12., radius * .83)
        # Render inverse UV field at simulation resolution, then interpolate to px.
        # Bound the CPU shading/UV raster, keeping the final clip at native
        # resolution. Source coordinates still sample the original photograph.
        ratio = min(1., 512/max(w, h))
        self.render_size = (max(2, round(w*ratio)), max(2, round(h*ratio)))
        rw, rh = self.render_size
        self.px_x, self.px_y = np.meshgrid(np.linspace(0, w-1, rw, dtype=np.float32),
                                         np.linspace(0, h-1, rh, dtype=np.float32))
        self.render_edge = cv2.resize(self.edge, self.render_size)
        self.render_texture = cv2.remap(self.texture, self.px_x, self.px_y, cv2.INTER_LINEAR)
        self.accumulator = 0.
        self.status = "ready"

    def _collider(self, x, y, phase, shape, strength):
        """Front surface of an animated hidden solid; negative z is behind cloth."""
        scale = self.scale
        cx = self.center[0] + scale*.12*math.sin(phase*.71)
        cy = self.center[1] + scale*.08*math.sin(phase*.53)
        u, v = (x-cx)/scale, (y-cy)/scale
        def bump(a, b, sx, sy):
            return np.exp(-.5*(((u-a)/sx)**2 + ((v-b)/sy)**2))
        if shape == "cara":
            # Forehead, nose bridge/tip, cheeks, lips and chin. Recessed sockets
            # are in the collider; the cloth bridges them through its constraints.
            z = .48*bump(0, -.05, .52, .78)
            z += .16*bump(0, -.48, .38, .25)
            z += .30*bump(0, -.10, .10, .29) + .30*bump(0, .07, .14, .12)
            for side in (-1, 1):
                z += .16*bump(side*.30, .12, .17, .20)
                z += .12*bump(side*.24, -.32, .19, .08)
                z -= .15*bump(side*.24, -.19, .13, .10)
            z += .13*bump(0, .43, .24, .07) + .12*bump(0, .57, .20, .13)
            z -= .10*bump(0, .36, .20, .045)
        elif shape == "mano":
            z = .52*bump(0, .22, .37, .42)
            for a, b, length in ((-.31, -.30, .34), (-.10, -.46, .42), (.12, -.41, .39), (.33, -.25, .31)):
                z = np.maximum(z, .48*bump(a, b, .075, length))
            z = np.maximum(z, .43*bump(-.48, .18, .20, .12))
        else:
            z = .65*np.sqrt(np.maximum(0, 1-(u/.70)**2-(v/.70)**2))
        # Withdraw fully once per cycle; a hit adds penetration, not just brightness.
        push = .5 - .5*math.cos(phase*2*math.pi*.16)
        depth = strength*(.90*push + .35*self.level)
        # Keep the solid behind the plane wherever its profile is zero, even at
        # maximum force/audio. Otherwise the entire hidden plane would collide.
        return ((np.minimum(z, .90)*strength + min(.94, depth) - .95) * scale).astype(np.float32)

    def _step(self, dt, stiffness, shape, strength, speed):
        self.phase += dt*speed
        old = self.pos.copy()
        # Weak elastic return to the undeformed plane plus velocity damping.
        self.vel *= math.exp(-3.3*dt)
        self.vel += (self.rest-self.pos) * (9.*dt)
        self.pos += self.vel*dt
        for _ in range(2):
            for a, b, length, wa, wb in self.edges:
                delta = self.pos[b]-self.pos[a]
                norm = np.sqrt(np.sum(delta*delta, axis=2, keepdims=True))
                correction = delta*((norm-length)/np.maximum(norm, 1e-6))*(.18+.77*stiffness)
                self.pos[a] += correction*wa
                self.pos[b] -= correction*wb
            surface = self._collider(self.pos[..., 0], self.pos[..., 1], self.phase, shape, strength)
            self.pos[..., 2] = np.maximum(self.pos[..., 2], surface)
            self.pos[~self.free] = self.rest[~self.free]
        self.vel = (self.pos-old)/dt
        self.steps += 1

    def update(self, dt, scene):
        dt = _number(dt, 0, .1)  # bounded catch-up after a stalled frame
        super().update(dt, scene)
        if not scene.projector_px or scene.coordinate_space != "legacy" or not scene.scan_dir:
            raise ValueError("tela_viva requires a legacy projector-pixel scan")
        install_scene_regions(scene)
        obj = self.object(scene) or scene.find(self.default_target)
        if obj is None or any(float(v) != 0 for v in obj.extra.get("ajuste", (0, 0))):
            raise ValueError("tela_viva: missing target or changed alignment; prepare a new mask")
        source = texture_path(scene.scan_dir)
        path = (Path(self.p["bundle"]) if self.p["bundle"] else
                Path(obj.extra["vector_bundle"]) if obj.extra.get("vector_bundle") else
                Path(__file__).resolve().parents[2]/"reports"/"vectortrace"/Path(scene.scan_dir).name/obj.id/"trace.json")
        mask_path = path.parent/"mask.png"
        key = (str(path.resolve()), _stamp(path), _stamp(mask_path), str(source.resolve()),
               _stamp(source), obj.id, tuple(scene.table_mm))
        if key != self._key:
            data, trace, mask = _read_bundle(str(path), _stamp(path), _stamp(mask_path))
            if data["target_id"] != obj.id or _hash(str(source), _stamp(source)) != data["source_sha256"]:
                raise ValueError("tela_viva: bundle belongs to another target or scene_pov")
            self.projector_size = tuple(data["projector_size"])
            self.origin = tuple(data["origin_px"])
            x, y = self.origin
            if (self.projector_size != tuple(scene.table_mm) or not mask.any() or
                    x < 0 or y < 0 or x+trace.width > self.projector_size[0] or y+trace.height > self.projector_size[1]):
                raise ValueError("tela_viva: invalid mask/projector crop")
            self._prepare(source, data, mask)
            self.texture_source = str(source)
            self._key = key
        shape = self.p["forma"]
        if shape not in ("cara", "mano", "esfera"):
            raise ValueError("forma must be cara, mano or esfera")
        strength = _number(self.p["fuerza"], 0, 2)
        speed = _number(self.p["velocidad"], 0, 3)
        stiffness = _number(self.p["rigidez"], 0, 1)
        level = audio_level(scene.audio, self.p["audio"], _number(self.p["audio_gain"], 0, 4))
        self.level += (level-self.level)*(1-math.exp(-dt*12))
        self.accumulator += dt
        # Never chase a slow display with six expensive physics steps: that
        # creates a feedback loop (late frame -> more work -> later frame).
        # Drop excess physics time but keep the body's animation clock moving.
        dropped = max(0., self.accumulator-2/60)
        self.phase += dropped*speed
        self.accumulator -= dropped
        while self.accumulator >= 1/60-1e-9:
            self._step(1/60, stiffness, shape, strength, speed)
            self.accumulator -= 1/60

    def draw(self, canvas):
        if self.status != "ready":
            return
        if (canvas.size != self.projector_size or canvas.flip_y or canvas.px_per_mm != 1 or
                canvas.origin_px != (0., 0.)):
            raise ValueError("tela_viva requires native projector pixels without transforms")
        h, w = self.mask.shape
        size = self.render_size
        sx, sy = self.spacing
        displacement = self.pos-self.rest
        if np.max(np.abs(displacement)) < 1e-4:
            x, y = self.origin
            cv2.copyTo(self.texture, self.mask8, canvas.img[y:y+h, x:x+w])
            return
        # Oblique virtual view: apparent relief on the physical flat painting.
        dx = displacement[..., 0] + .22*displacement[..., 2]
        dy = displacement[..., 1] - .12*displacement[..., 2]
        ix, iy = self.grid_x.copy(), self.grid_y.copy()
        for _ in range(4):
            mx, my = ix/sx, iy/sy
            ix = self.grid_x-cv2.remap(dx, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            iy = self.grid_y-cv2.remap(dy, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        mapx = self.px_x + cv2.resize(ix-self.grid_x, size)*self.render_edge
        mapy = self.px_y + cv2.resize(iy-self.grid_y, size)*self.render_edge
        # Never sample scenery outside the verified painting, including mask holes.
        invalid = cv2.compare(cv2.remap(self.mask8, mapx, mapy, cv2.INTER_LINEAR), 255, cv2.CMP_LT)
        out = cv2.remap(self.texture, mapx, mapy, cv2.INTER_LINEAR)
        cv2.copyTo(self.render_texture, invalid, out)
        tangent_y, tangent_x = np.gradient(self.pos, sy, sx, axis=(0, 1))
        normals = np.cross(tangent_x, tangent_y)
        normals /= np.maximum(np.linalg.norm(normals, axis=2, keepdims=True), 1e-6)
        light = np.array([-.48, -.55, .685], np.float32)
        light /= np.linalg.norm(light)
        diffuse = np.maximum(0, normals@light)
        shade = (.20 + .80*diffuse)/(.20+.80*light[2])
        shade = cv2.remap(shade, ix/sx, iy/sy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        amount = _number(self.p["sombreado"], 0, 2)
        shade = np.maximum(.08, 1+(cv2.resize(shade, size)-1)*amount*self.render_edge)
        out = cv2.multiply(out, cv2.merge([shade, shade, shade]), dtype=cv2.CV_8U)
        if size != (w, h):
            out = cv2.resize(out, (w, h))
        cv2.copyTo(self.texture, self.border8, out)
        x, y = self.origin
        # Replace only the mask: shadows must darken prior texture layers.
        cv2.copyTo(out, self.mask8, canvas.img[y:y+h, x:x+w])

    def state(self):
        return {**super().state(), "status": self.status, "mask_source": "verified_pixel_mask",
                "texture_source": getattr(self,"texture_source",None),
                "origin_px": list(self.origin), "simulation_steps": self.steps,
                "max_depth_px": round(float(self.pos[..., 2].max()), 2) if self._key else 0.}

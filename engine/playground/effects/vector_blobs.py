"""Large vector color regions with a clipped, bass-reactive bloom."""
import cv2
import numpy as np
import importlib
import math

from .. import vector_animation
VectorAnimation = importlib.reload(vector_animation).VectorAnimation
region_order = vector_animation.region_order


class VectorBlobs(VectorAnimation):
    name = "vector_blobs"
    label = "Blobs grandes con hue y bloom"
    aliases = ("blobs", "hue bloom", "colores al bajo")
    description = ("Regiones grandes de la textura, hue animado y bloom al bajo, recortados a máscara real. "
                   "min_area en píxeles nativos; hue_speed en vueltas/segundo; bloom y bloom_radius controlan brillo.")
    params = {**VectorAnimation.params, "min_area":1800., "hue_speed":.035,
              "bloom":.8, "bloom_radius":18., "audio":"bass", "audio_gain":1.2, "intensity":.6,
              "min_saturation":90., "min_value":45.}

    def _prepare(self, trace, mask):
        super()._prepare(trace, mask)
        regions = region_order(trace, self.p["order"])
        self.areas = np.asarray([r.area for r in regions])
        h,w = mask.shape
        scale = min(1.,512/max(h,w))
        self.small_size = max(1,round(w*scale)),max(1,round(h*scale))
        self.small_rank = cv2.resize(self.prepared["rank"].astype(np.float32),self.small_size,
                                    interpolation=cv2.INTER_NEAREST).astype(np.int32)
        self.hsv = cv2.cvtColor(cv2.resize(self.prepared["fill"],self.small_size,
                               interpolation=cv2.INTER_NEAREST),cv2.COLOR_BGR2HSV)

    def draw(self, canvas):
        if self.prepared is None:
            return
        if canvas.size != self.projector_size or canvas.px_per_mm != 1 or canvas.flip_y or canvas.origin_px != (0.,0.):
            raise ValueError("Blobs require native projector pixels")
        p = self.prepared
        rank = self.small_rank
        selected = ((rank >= 0) & (self.hsv[...,1] >= float(self.p["min_saturation"]))
                    & (self.hsv[...,2] >= float(self.p["min_value"])))
        if len(self.areas):
            selected &= self.areas[np.maximum(rank,0)] >= max(0.,float(self.p["min_area"]))
        else:
            return
        hsv = self.hsv.copy()
        hsv[...,0] = (hsv[...,0].astype(np.float32) + self.t*float(self.p["hue_speed"])*180) % 180
        hsv[...,1] = np.maximum(hsv[...,1], 150)
        hsv[...,2] = np.maximum(hsv[...,2], 180)
        layer = cv2.cvtColor(hsv,cv2.COLOR_HSV2BGR)
        layer[~selected] = 0
        strength = np.clip(float(self.p["bloom"])*(1+self.level*float(self.p["audio_gain"])),0,4)
        if strength:
            h,w = layer.shape[:2]
            small = cv2.resize(layer,(max(1,w//4),max(1,h//4)),interpolation=cv2.INTER_AREA)
            scale = w / p["mask"].shape[1]
            glow = cv2.GaussianBlur(small,(0,0),max(.1,min(100.,float(self.p["bloom_radius"]))*scale/4))
            glow = cv2.resize(glow,(w,h),interpolation=cv2.INTER_LINEAR)
            layer = cv2.addWeighted(layer,1,glow,float(strength),0)
        gain = np.clip(float(self.p["intensity"])*(1+self.level*float(self.p["audio_gain"])*.3),0,4)
        layer = cv2.convertScaleAbs(layer,alpha=float(gain))
        layer = cv2.resize(layer,(p["mask"].shape[1],p["mask"].shape[0]),interpolation=cv2.INTER_LINEAR)
        layer = cv2.bitwise_and(layer,layer,mask=p["mask8"])
        x,y = self.origin
        h,w = layer.shape[:2]
        cv2.add(canvas.img[y:y+h,x:x+w],layer,dst=canvas.img[y:y+h,x:x+w])


class MaskPulse(VectorBlobs):
    name = "mask_pulse"
    label = "Máscara azteca con color pulsante"
    aliases = ("máscara pulsante", "máscara de colores")
    description = "Rellenos de color que pulsan al bajo en la máscara azteca segmentada."
    params = {**VectorBlobs.params,"min_area":40.,"hue_speed":.06,"pulse_speed":.4,"bloom":.45,
              "min_saturation":0.,"min_value":0.,"intensity":.8}

    def draw(self,canvas):
        original = self.p["intensity"]
        self.p["intensity"] = float(original)*(.45+.55*(.5+.5*math.sin(self.t*float(self.p["pulse_speed"])*2*math.pi)))
        try:
            super().draw(canvas)
        finally:
            self.p["intensity"] = original

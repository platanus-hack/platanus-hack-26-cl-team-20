"""Native-pixel surface animation using only prepared segmentation masks."""
import math
import cv2
import numpy as np

from .effects.base import Effect, bgr
from .appearance import install_scene_regions, mask_for_object, texture_path, stamp
from .audio.features import audio_level


class PixelAnimation(Effect):
    targets = ("*",)
    needs = ("contours", "audio")
    params = dict(color=[80,230,255], width=3., speed=.3, amount=5., spacing=45.,
                  intensity=.7, audio="bass", audio_gain=1., resolution=.5, capa=5)
    mode = "contour"

    def update(self, dt, scene):
        super().update(dt,scene)
        if not scene.projector_px or scene.coordinate_space == "world_mm":
            raise ValueError("Pixel effects require a legacy projector-pixel scene")
        install_scene_regions(scene)
        obj = self.object(scene)
        if obj is None:
            raise ValueError("Select a segmented object")
        mask = mask_for_object(scene,obj)
        if mask is None:
            raise ValueError("Object has no prepared pixel mask")
        resolution = float(np.clip(float(self.p["resolution"]),.25,1.))
        key = (id(mask),resolution)
        if self.mode == "lines":
            source = texture_path(scene.scan_dir)
            key += (str(source),stamp(source))
        if getattr(self,"_prepare_key",None) != key:
            if not mask.any():
                raise ValueError("Object has no prepared pixel mask")
            self._prepare_key = key
            self._mask = mask
            ys,xs = np.nonzero(mask)
            x,y,w,h = int(xs.min()),int(ys.min()),int(xs.max()-xs.min()+1),int(ys.max()-ys.min()+1)
            self.roi = x,y,w,h
            self.mask = mask[y:y+h,x:x+w]
            self.mask8 = self.mask.astype(np.uint8)*255
            # Padding keeps clipped image edges a real contour too.
            pad = np.pad(self.mask.astype(np.uint8),1)
            distance = cv2.distanceTransform(pad,cv2.DIST_L2,5)[1:-1,1:-1]
            scale = min(resolution,384/max(w,h))
            sw,sh = max(1,round(w*scale)),max(1,round(h*scale))
            self.distance = cv2.resize(distance,(sw,sh),interpolation=cv2.INTER_LINEAR)
            self.small_mask = cv2.resize(self.mask.astype(np.uint8),(sw,sh),interpolation=cv2.INTER_NEAREST)>0
            self.yy,self.xx = np.meshgrid(np.linspace(y,y+h-1,sh,dtype=np.float32),
                                         np.linspace(x,x+w-1,sw,dtype=np.float32),indexing="ij")
            ears = np.zeros((h,w),np.uint8)
            for oid in ("oreja_izquierda","oreja_superior"):
                if oid in scene.objects:
                    ear = mask_for_object(scene,scene.objects[oid])
                    if ear is not None:ears |= ear[y:y+h,x:x+w].astype(np.uint8)
            self.ears = (cv2.resize(ears,(sw,sh),interpolation=cv2.INTER_NEAREST)>0)&self.small_mask
            if self.mode == "lines":
                photo = cv2.imread(str(source),cv2.IMREAD_GRAYSCALE)
                if photo is None or photo.shape != mask.shape:
                    raise ValueError("Missing aligned appearance for surface light field")
                light = cv2.resize(photo[y:y+h,x:x+w],(sw,sh)).astype(np.float32)
                # Broad illumination, not individual image texture pixels, bends the strokes.
                weight = cv2.GaussianBlur(self.small_mask.astype(np.float32),(0,0),max(3.,30*scale))
                light = cv2.GaussianBlur(light*self.small_mask,(0,0),max(3.,30*scale))/np.maximum(weight,.001)
                lo,hi = np.percentile(light[self.small_mask],[5,95])
                self.light = np.clip((light-float(lo))/max(10.,float(hi-lo)),0,1)
        self.size = tuple(map(int,scene.table_mm))
        self.level = audio_level(scene.audio,self.p["audio"]) if self.p["audio"] else 0.

    def draw(self,canvas):
        if not hasattr(self,"mask"):
            return
        if canvas.size != self.size or canvas.px_per_mm != 1 or canvas.flip_y or canvas.origin_px != (0.,0.):
            raise ValueError("Pixel effects require native projector pixels")
        phase = self.t*float(self.p["speed"])*2*math.pi
        pulse = .5+.5*math.sin(phase)
        width = float(np.clip(float(self.p["width"]),.5,50))
        amount = float(np.clip(float(self.p["amount"]),0,100))
        if self.mode == "lines":
            spacing = float(np.clip(float(self.p["spacing"]),4,500))
            drift = phase*.18
            wave = (self.yy + .22*self.xx + (12+5*amount)*np.sin(self.xx/210+drift)
                    + 14*np.sin((self.xx+self.yy)/310-drift*.7) + self.light*spacing*.65)
            distance = np.abs(np.mod(wave+drift*spacing+spacing*.5,spacing)-spacing*.5)
            brush = .72+.28*np.sin(self.xx/140-drift+self.light*2)
            alpha = np.clip(width*brush+1-distance,0,1)*self.small_mask
            alpha *= .65+.35*(.5+.5*np.sin(self.xx/250+self.yy/380+drift*.4))
        else:
            moving_width = width+amount*pulse*(1+self.level*float(self.p["audio_gain"]))
            alpha = np.clip(moving_width+1-self.distance,0,1)*self.small_mask
            if self.mode == "rabbit":
                # Ears breathe independently inside their authoritative masks.
                alpha = np.maximum(alpha,self.ears*(.12+.35*(.5+.5*np.sin(self.yy/45-phase))))
                alpha *= .55+.45*(.5+.5*np.sin(self.xx/100-phase))
        gain = float(np.clip(float(self.p["intensity"])*(1+self.level*float(self.p["audio_gain"])*.3),0,4))
        layer = cv2.merge([cv2.convertScaleAbs(alpha,alpha=c*gain) for c in bgr(self.p["color"])])
        x,y,w,h = self.roi
        layer = cv2.resize(layer,(w,h),interpolation=cv2.INTER_LINEAR)
        layer = cv2.bitwise_and(layer,layer,mask=self.mask8)
        cv2.add(canvas.img[y:y+h,x:x+w],layer,dst=canvas.img[y:y+h,x:x+w])

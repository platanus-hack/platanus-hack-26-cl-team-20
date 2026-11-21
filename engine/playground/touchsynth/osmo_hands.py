"""Dedos desde la Osmo cenital, directo en px del proyector.

Todo pasa en la **vista del proyector** (POV): el mapa del gray code (`proj_to_cam.npz`: para cada px del proyector,
dónde cae en la cámara) remapea cada cuadro de la Osmo al lienzo del proyector (a la mitad: 960x540). Así la
proyección, la foto del escaneo y la detección quedan alineadas píxel a píxel y un dedo detectado ya está en
coordenadas del proyector (sin homografías).

Qué es mano (dos modelos):
- **"proyeccion"** (en vivo): se sabe lo que se proyecta, así que se predice la cámara:
  `cámara ≈ B + (W − B) · (proyectado/255)^γ` por píxel y canal (B = proyector en negro, W = en blanco;
  `ProjectionModel.capture` los mide en ~4 s con 4 tableros complementarios, que tienen siempre la mitad blanca
  para que la auto-exposición de la Osmo no se mueva; γ se reajusta en vivo cada `gamma_every` cuadros). Lo que no
  calza con la predicción (para ninguno de los
  últimos cuadros proyectados: cubre la latencia proyector→cámara) es primer plano. Corrige la ganancia global de
  la cámara (auto-exposición de la Osmo en modo webcam) con la mediana cámara/predicción. Así las visuales que
  cambian (pulsos, trazos, linterna) no se confunden con manos.
- **"fondo"**: fondo aprendido (primeros cuadros) que se adapta lento donde no hay mano. Para escena con luz fija
  (o la foto blanca del escaneo como fondo si se proyecta blanco).

Sombra: primer plano que quedó cerca de B (sin luz del proyector) donde la predicción decía luz. La sombra del dedo
en la mesa cae exactamente en el px del proyector del dedo (el proyector "ve" el dedo ahí); el dedo mismo, remapeado
como si estuviera en la mesa, aparece corrido por paralaje. Por eso la distancia punta del dedo ↔ punta de su
sombra (`gap`) es ~0 al tocar y crece con la altura: es la señal de contacto de la Osmo sola.

Punta: cada mancha de mano entra por el borde de la zona vista (el brazo); la punta es el punto del contorno más
lejano a esa entrada. Dirección = entrada → punta (para la linterna); centro de la mano = punta − `hand_back`.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np


class PovMapper:
    """Cuadro de la Osmo → vista del proyector reducida `scale` veces."""

    def __init__(self, mapx: np.ndarray, mapy: np.ndarray, valid: np.ndarray, scale: int = 2):
        self.scale = int(scale)
        self.proj_size = (mapx.shape[1], mapx.shape[0])
        self.mx = np.ascontiguousarray(mapx[::scale, ::scale], np.float32)
        self.my = np.ascontiguousarray(mapy[::scale, ::scale], np.float32)
        self.valid = np.ascontiguousarray(valid[::scale, ::scale]).astype(bool)
        self.size = (self.mx.shape[1], self.mx.shape[0])
        self.mx[~self.valid] = -1
        self.my[~self.valid] = -1

    @classmethod
    def load(cls, scan_dir: str | Path, scale: int = 2) -> "PovMapper":
        with np.load(Path(scan_dir) / "proj_to_cam.npz") as z:
            return cls(z["mapx"], z["mapy"], z["valid"], scale)

    def to_pov(self, cam_frame: np.ndarray) -> np.ndarray:
        return cv2.remap(cam_frame, self.mx, self.my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    def proj_to_pov(self, proj_img: np.ndarray) -> np.ndarray:
        if (proj_img.shape[1], proj_img.shape[0]) == self.size:
            return proj_img
        return cv2.resize(proj_img, self.size, interpolation=cv2.INTER_AREA)


def _lum(img: np.ndarray) -> np.ndarray:
    """Luminancia (BGR, pesos de Rec. 601) en float32; con OpenCV si se puede (mucho más rápido)."""
    if img.ndim == 3 and img.shape[2] == 3 and img.dtype in (np.float32, np.uint8):
        return cv2.cvtColor(np.ascontiguousarray(img, np.float32), cv2.COLOR_BGR2GRAY)
    return img[..., 0] * 0.114 + img[..., 1] * 0.587 + img[..., 2] * 0.299


class ProjectionModel:
    """cámara ≈ B + A · (proyectado/255)^γ por píxel y canal (en la POV reducida)."""

    def __init__(self, black: np.ndarray, white: np.ndarray, gray: np.ndarray | None = None):
        self.B = black.astype(np.float32)
        self.A = np.maximum(white.astype(np.float32) - self.B, 1.0)
        self.gamma = 1.0
        if gray is not None:
            lit = _lum(self.A) > 30
            if lit.sum() > 100:
                ratio = (_lum(gray.astype(np.float32)) - _lum(self.B))[lit] / _lum(self.A)[lit]
                r = float(np.median(np.clip(ratio, 0.02, 0.98)))
                self.gamma = float(np.clip(np.log(r) / np.log(0.5), 0.4, 3.0))
        self._lut = (np.arange(256, dtype=np.float32) / 255.0) ** self.gamma

    def predict(self, proj_pov: np.ndarray) -> np.ndarray:
        return self.B + self.A * self._lut[proj_pov]

    def lit(self, proj_pov: np.ndarray) -> np.ndarray:
        """Luz del proyector que llega (en niveles de la cámara, luminancia)."""
        return _lum(self.A * self._lut[proj_pov])

    def adapt(self, cam: np.ndarray, proj_pov: np.ndarray, bg: np.ndarray, rate: float) -> None:
        """Deriva lenta del ambiente (B) donde no hay mano."""
        if rate <= 0:
            return
        target = cam.astype(np.float32) - self.A * self._lut[proj_pov]
        self.B[bg] += rate * (target[bg] - self.B[bg])

    def set_gamma(self, gamma: float) -> None:
        self.gamma = float(gamma)
        self._lut = (np.arange(256, dtype=np.float32) / 255.0) ** self.gamma

    @classmethod
    def capture(cls, show, read_pov, settle: float = 0.8, frames: int = 2, size=(1920, 1080),
                block: int = 48) -> "ProjectionModel":
        """Mide B y W con 4 tableros complementarios (tablero, inverso, y los dos corridos medio bloque): cada uno
        tiene la mitad blanca, así la auto-exposición de la Osmo no cambia entre tomas (con negro y blanco enteros
        sí cambia, y el modelo sale mal). Por píxel: W = máximo, B = mínimo de las 4 tomas (los bordes borrosos de
        un par quedan al centro de un bloque en el otro). La gamma se ajusta después, en vivo (`OsmoHands`)."""
        w, h = size
        yy, xx = np.mgrid[0:h, 0:w]
        pats = []
        for off in (0, block // 2):
            c = ((((xx + off) // block) + ((yy + off) // block)) % 2).astype(np.uint8) * 255
            pats += [c, 255 - c]

        def grab(p):
            show(np.repeat(p[..., None], 3, axis=2))
            time.sleep(settle)
            acc = [read_pov().astype(np.float32) for _ in range(frames)]
            return np.median(np.stack(acc), axis=0)

        st = np.stack([grab(p) for p in pats])
        return cls(st.min(axis=0), st.max(axis=0))

    def save(self, path: str | Path) -> None:
        np.savez_compressed(path, B=self.B.astype(np.float16), A=self.A.astype(np.float16), gamma=self.gamma)

    @classmethod
    def load(cls, path: str | Path) -> "ProjectionModel":
        with np.load(path) as z:
            m = cls(z["B"].astype(np.float32), z["B"].astype(np.float32) + z["A"].astype(np.float32))
            m.set_gamma(float(z["gamma"]))
        return m

    def fit_gamma(self, cam: np.ndarray, projs: list[np.ndarray], valid: np.ndarray,
                  candidates=(0.5, 0.7, 0.85, 1.0, 1.2, 1.5, 1.9, 2.4), step: int = 6) -> float:
        """Gamma que mejor explica el cuadro actual (submuestreado, con corrección de ganancia). La mayor parte del
        cuadro es mesa (no manos), así que la mediana del residuo manda."""
        sub = (slice(None, None, step), slice(None, None, step))
        c = cam[sub].astype(np.float32)
        B, A, v = self.B[sub], self.A[sub], valid[sub]
        lc = _lum(c)
        best, best_err = self.gamma, None
        for g in candidates:
            lut = (np.arange(256, dtype=np.float32) / 255.0) ** g
            res = None
            for p in projs:
                pred = B + A * lut[p[sub]]
                lp = _lum(pred)
                sel = (lp > 40) & v
                gain = float(np.clip(np.median(lc[sel] / lp[sel]), 0.5, 2.0)) if sel.sum() > 50 else 1.0
                r = np.abs(c - pred * gain).max(axis=2)
                res = r if res is None else np.minimum(res, r)
            err = float(np.percentile(res[v], 90))         # la mediana la explica la ganancia sola
            if best_err is None or err < best_err:
                best, best_err = g, err
        self.set_gamma(best)
        return best


@dataclass
class OsmoTip:
    x: float                              # punta, px del proyector
    y: float
    hand_x: float                         # centro aproximado de la mano
    hand_y: float
    dir: tuple[float, float]              # entrada del brazo → punta (unitario)
    area: float                           # px² del proyector
    gap: float | None = None              # punta ↔ punta de la sombra, px del proyector (None = sin sombra visible)
    lit: float = 0.0                      # luz del proyector alrededor de la punta (niveles de cámara)
    t: float = 0.0
    entry: tuple[float, float] = (0.0, 0.0)


@dataclass
class OsmoConfig:
    model: str = "hibrido"               # "hibrido" (en vivo) | "proyeccion" | "fondo"
    thr: float = 22.0                    # residuo mínimo (niveles de cámara)…
    thr_rel: float = 0.08                # …más esto por el brillo predicho
    min_area: int = 300                  # px de la POV reducida (mano a 960x540 ≈ 1500-4000)
    max_area: float = 150000.0           # px² del proyector: más grande no es una mano (brazo incluido)
    max_hands: int = 4
    open_k: int = 3
    close_k: int = 9
    group_k: int = 25                    # pedazos a menos de esto (px de la POV) son la misma mano
    border_band: int = 8                 # px de la POV: banda del borde por donde entra el brazo
    arm_side: str = "abajo"              # respaldo si la mancha no toca el borde: abajo | arriba | izquierda | derecha
    tip_smooth: float = 2.5              # px de la POV
    hand_back: float = 70.0              # px del proyector de la punta al centro de la mano
    shadow_frac: float = 0.3             # sombra: llega menos de esta fracción de la luz predicha…
    shadow_min_light: float = 30.0       # …donde debía llegar al menos esta luz del proyector (niveles)
    shadow_radius: int = 45              # px de la POV alrededor de la punta
    min_shadow_px: int = 10
    shadow_ratio_bg: float = 0.6         # modo fondo: sombra si Y < ratio · Y_fondo con el mismo color
    bg_alpha: float = 0.03               # adaptación del fondo / de B donde no hay mano
    gain_comp: bool = True
    gamma_every: int = 30                # cada cuántos cuadros se reajusta la gamma del modelo (0 = nunca)
    min_reach: float = 60.0              # px del proyector: la punta tiene que entrar al menos esto desde el borde
    proj_keep: int = 64                  # cuadros proyectados guardados (con su hora)
    proj_compare: int = 2                # contra cuántos de ellos se compara cada cuadro (+ el último)
    lat_min: float = 0.03                # latencia proyector → Osmo → Mac (s): ventana de lo que la cámara…
    lat_max: float = 0.40                # …pudo estar viendo
    warmup: int = 5                      # modos fondo e híbrido: cuadros para aprender el fondo
    reset_fg_frac: float = 0.35          # si el primer plano tapa más que esto…
    reset_frames: int = 20               # …tantos cuadros seguidos, el fondo se reaprende


class OsmoHands:
    def __init__(self, mapper: PovMapper, cfg: OsmoConfig | None = None, model: ProjectionModel | None = None):
        self.mapper = mapper
        self.cfg = cfg or OsmoConfig()
        self.model = model
        self.bg: np.ndarray | None = None
        self._bg_n = 0
        self.proj_hist: deque[tuple[float, np.ndarray]] = deque(maxlen=self.cfg.proj_keep)
        v = mapper.valid.astype(np.uint8)
        b = self.cfg.border_band
        inner = cv2.erode(v, np.ones((2 * b + 1, 2 * b + 1), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0)
        self.band = (v > 0) & (inner == 0)
        self.stats = {"frames": 0, "ms": 0.0, "gain": 1.0, "fg_frac": 0.0}
        self.debug: dict[str, np.ndarray] = {}

    @property
    def scale(self) -> int:
        return self.mapper.scale

    def push_projected(self, proj_img: np.ndarray, t: float | None = None) -> None:
        """Lo que se acaba de proyectar (lienzo del runtime, BGR), con su hora."""
        self.proj_hist.append((time.time() if t is None else t, self.mapper.proj_to_pov(proj_img)))

    def _projected_for(self, t_cap: float, like: np.ndarray) -> list[np.ndarray]:
        """Lo proyectado que la cámara pudo estar viendo en `t_cap`: entre `lat_max` y `lat_min` antes (latencia
        proyector → Osmo → Mac), a lo más `proj_compare` cuadros repartidos. Sin cuadros en esa ventana, los
        últimos."""
        cfg = self.cfg
        hist = list(self.proj_hist)
        if not hist:
            return [np.zeros_like(like)]
        win = [p for tp, p in hist if t_cap - cfg.lat_max <= tp <= t_cap - cfg.lat_min]
        if not win:
            win = [p for _, p in hist[-cfg.proj_compare:]]
        if len(win) > cfg.proj_compare:
            idx = np.linspace(0, len(win) - 1, cfg.proj_compare).round().astype(int)
            win = [win[i] for i in idx]
        if win[-1] is not hist[-1][1]:
            win.append(hist[-1][1])                       # el último siempre (latencia menor a la esperada)
        return win

    def detect_frame(self, cam_frame: np.ndarray, t: float | None = None) -> list[OsmoTip]:
        self.last_pov = self.mapper.to_pov(cam_frame)
        return self.detect_pov(self.last_pov, t)

    # ------------------------------------------------------------------ primer plano y sombra
    def _masks(self, pov: np.ndarray, t: float):
        cfg = self.cfg
        cam = pov.astype(np.float32)
        valid = self.mapper.valid
        if cfg.model == "hibrido" and self.model is not None:
            return self._masks_hybrid(pov, cam, valid, t)
        if cfg.model == "proyeccion" and self.model is not None:
            projs = self._projected_for(t, pov)
            m = self.model
            if cfg.gamma_every and self.stats["frames"] % cfg.gamma_every == 0 and self.stats["fg_frac"] < 0.05:
                self.stats["gamma"] = m.fit_gamma(pov, projs, valid)
            lum_c = _lum(cam)
            sub = (slice(None, None, 4), slice(None, None, 4))
            best_res = best_lp = best_lit = None
            gain = 1.0
            for p in projs:
                litc = m.A * m._lut[p]
                pred = m.B + litc
                lp = _lum(pred)
                if cfg.gain_comp:
                    sel = (lp[sub] > 40) & valid[sub]
                    if sel.sum() > 200:
                        gain = float(np.clip(np.median(lum_c[sub][sel] / lp[sub][sel]), 0.25, 4.0))
                        pred *= gain
                        lp *= gain
                res = np.abs(cam - pred).max(axis=2)
                lit = _lum(litc)
                if best_res is None:
                    best_res, best_lp, best_lit = res, lp, lit
                else:
                    better = res < best_res
                    best_res = np.where(better, res, best_res)
                    best_lp = np.where(better, lp, best_lp)
                    best_lit = np.where(better, lit, best_lit)
            self.stats["gain"] = round(gain, 3)
            fg = (best_res > cfg.thr + cfg.thr_rel * best_lp) & valid
            # sombra: casi no llega luz del proyector (cámara − B < shadow_frac de la luz que debía llegar)
            dark = (lum_c - _lum(m.B) * gain) < cfg.shadow_frac * best_lit * gain
            shadow = fg & dark & (best_lit > cfg.shadow_min_light)
            return fg, shadow, best_lit, projs[-1]
        # modo fondo
        if self.bg is None or self._bg_n < cfg.warmup:
            self.bg = cam if self.bg is None else 0.5 * self.bg + 0.5 * cam
            self._bg_n += 1
            z = np.zeros(pov.shape[:2], bool)
            return z, z, _lum(self.bg), None
        diff = np.abs(cam - self.bg).max(axis=2)
        fg = (diff > cfg.thr + cfg.thr_rel * _lum(self.bg)) & valid
        yb, yc = _lum(self.bg) + 1.0, _lum(cam) + 1.0
        cb = self.bg / yb[..., None]
        cc = cam / yc[..., None]
        same_color = np.abs(cc - cb).max(axis=2) < 0.12
        shadow = fg & (yc < cfg.shadow_ratio_bg * yb) & same_color & (yb > cfg.shadow_min_light)
        return fg, shadow, _lum(self.bg), None

    def _term(self, p: np.ndarray) -> np.ndarray:
        """A·p^γ de un cuadro proyectado (float32), en caché: cada cuadro proyectado se compara con varios de la
        cámara."""
        cache = self.__dict__.setdefault("_term_cache", {})
        key = id(p)
        hit = cache.get(key)
        if hit is not None and hit[0] is p:
            return hit[1]
        term = (self.model.A * self.model._lut[p]).astype(np.float32)
        live = {id(q) for _, q in self.proj_hist}
        for k in [k for k in cache if k not in live]:
            del cache[k]
        cache[key] = (p, term)
        return term

    def _masks_hybrid(self, pov: np.ndarray, cam: np.ndarray, valid: np.ndarray, t: float):
        """Fondo aprendido en vivo + lo que cambió en la proyección desde que se aprendió:
        `cámara ≈ e · (fondo + g·(A·p^γ − A·p_ref^γ))`. El fondo es la cámara tal cual (absorbe la luz ambiente, los
        objetos que no estaban al calibrar y la luz base) y recuerda bajo qué proyección se aprendió (`term_ref`);
        g (luz real del proyector / la medida con los tableros) sale de los cambios entre cuadros, que no dependen
        del fondo, y e (auto-exposición de la Osmo) es un factor global."""
        cfg, m = self.cfg, self.model
        projs = self._projected_for(t, pov)
        terms = [self._term(p) for p in projs]
        g = self.stats.get("gain", 1.0)
        sub = (slice(None, None, 4), slice(None, None, 4))
        raw_l = _lum(cam)[sub]
        if self.bg is None or self._bg_n < cfg.warmup:
            if self.bg is None:
                self.bg, self.term_ref = cam.copy(), terms[-1].copy()
            else:
                self.bg = 0.5 * self.bg + 0.5 * cam
                self.term_ref = 0.5 * self.term_ref + 0.5 * terms[-1]
            self._bg_n += 1
            self._prev_h = (_lum(terms[-1])[sub], raw_l, np.zeros(raw_l.shape, bool))
            z = np.zeros(pov.shape[:2], bool)
            return z, z, _lum(g * terms[-1]), None
        preds = [self.bg + g * (term - self.term_ref) for term in terms]
        lp_last = _lum(preds[-1])[sub]
        sel = (lp_last > 20) & valid[sub]
        e = 1.0
        if sel.sum() > 200:                                                 # auto-exposición (la mano es chica)
            e = float(np.clip(np.median(raw_l[sel] / lp_last[sel]), 0.25, 4.0))
            self.stats["exposicion"] = round(e, 3)
        camn = cam / e
        best_res = best_lp = best_lit = None
        best_k = 0
        for k, (term, pred) in enumerate(zip(terms, preds)):
            res = np.abs(camn - pred).max(axis=2)
            lp, lit = _lum(pred), _lum(g * term)
            if best_res is None:
                best_res, best_lp, best_lit = res, lp, lit
            else:
                better = res < best_res
                best_res = np.where(better, res, best_res)
                best_lp = np.where(better, lp, best_lp)
                best_lit = np.where(better, lit, best_lit)
                if better.mean() > 0.5:
                    best_k = k
        # quemado: un canal en el tope donde ya se esperaba casi el tope (la mano no está quemada) no informa
        saturated = ((cam >= 250) & (preds[-1] * e >= 225)).any(axis=2)
        fg = (best_res > cfg.thr + cfg.thr_rel * best_lp) & valid & ~saturated
        ambient = _lum(self.bg - g * self.term_ref)                         # la cámara sin luz del proyector
        dark = (_lum(camn) - ambient) < cfg.shadow_frac * best_lit
        shadow = fg & dark & (best_lit > cfg.shadow_min_light)
        # g para el próximo cuadro: Δcámara / Δproyectado entre cuadros seguidos (crudos), donde no hay mano
        lt = _lum(terms[best_k])[sub]
        prev = getattr(self, "_prev_h", None)
        if prev is not None:
            dlt, dlc = lt - prev[0], raw_l / e - prev[1]                  # sin el salto de exposición
            sel = (np.abs(dlt) > 20) & ~fg[sub] & ~prev[2] & valid[sub]
            if sel.sum() > 100:
                g_now = float(np.median(dlc[sel] / dlt[sel]))
                self.stats["gain"] = round(float(np.clip(0.7 * g + 0.3 * g_now, 0.2, 5.0)), 3)
        self._prev_h = (lt, raw_l / e, fg[sub])
        # el fondo (y la proyección bajo la que se aprendió) se adapta donde no hay mano
        bgmask = ~cv2.dilate(fg.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool) & valid
        a = cfg.bg_alpha
        mask8 = bgmask.astype(np.uint8)
        cv2.accumulateWeighted(np.ascontiguousarray(camn, np.float32), self.bg, a, mask=mask8)
        cv2.accumulateWeighted(terms[best_k], self.term_ref, a, mask=mask8)
        return fg, shadow, best_lit, None

    # ------------------------------------------------------------------ manos
    def pause(self, seconds: float) -> None:
        """No detecta ni aprende el fondo por `seconds` (p.ej. después de proyectar los tableros: la Osmo todavía
        entrega cuadros viejos) y después aprende el fondo de nuevo."""
        self.skip_until = time.time() + seconds
        self.reset_background()

    def reset_background(self) -> None:
        self.bg, self._bg_n = None, 0
        self.proj_hist.clear()
        self._big_fg = 0
        self._prev_h = None

    def detect_pov(self, pov: np.ndarray, t: float | None = None) -> list[OsmoTip]:
        t0 = time.perf_counter()
        t = time.time() if t is None else t
        cfg, s = self.cfg, self.scale
        if time.time() < getattr(self, "skip_until", 0.0):
            return []
        fg, shadow, lit, proj_now = self._masks(pov, t)
        # una "mano" que tapa más de un tercio de la mesa por mucho rato es un fondo mal aprendido: se reaprende
        fg_frac = float(fg.mean())
        self._big_fg = getattr(self, "_big_fg", 0) + 1 if fg_frac > cfg.reset_fg_frac else 0
        if self._big_fg:                                  # cuadro poco confiable: no se reportan manos
            self.stats["fg_frac"] = round(fg_frac, 4)
            if self._big_fg >= cfg.reset_frames:
                self.reset_background()
                self.stats["reinicios"] = self.stats.get("reinicios", 0) + 1
            return []
        hand = (fg & ~shadow).astype(np.uint8)
        if cfg.open_k > 1:
            hand = cv2.morphologyEx(hand, cv2.MORPH_OPEN, np.ones((cfg.open_k, cfg.open_k), np.uint8))
        if cfg.close_k > 1:
            hand = cv2.morphologyEx(hand, cv2.MORPH_CLOSE, np.ones((cfg.close_k, cfg.close_k), np.uint8))
        # agrupar pedazos cercanos (un brazo bajo luz tenue sale cortado): etiquetas sobre la máscara dilatada,
        # píxeles de la máscara original
        grp = cv2.dilate(hand, np.ones((cfg.group_k, cfg.group_k), np.uint8)) if cfg.group_k > 1 else hand
        n, lab, st, _ = cv2.connectedComponentsWithStats(grp)
        tips = []
        cands = []
        for i in range(1, n):
            x, y, w, h = (int(v) for v in st[i, :4])
            comp = np.zeros_like(hand, dtype=bool)
            comp[y:y + h, x:x + w] = (lab[y:y + h, x:x + w] == i) & (hand[y:y + h, x:x + w] > 0)
            area = int(comp[y:y + h, x:x + w].sum())
            if cfg.min_area <= area and area * s * s <= cfg.max_area:
                cands.append((area, comp, (x, y, w, h)))
        for area, comp, box in sorted(cands, key=lambda c: -c[0])[: cfg.max_hands]:
            tp = self._tip(comp, box, shadow, lit, t)
            if tp is not None:
                tips.append(tp)
        # fondo / ambiente: se adaptan donde no hay mano
        bgmask = ~cv2.dilate(fg.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool)
        if cfg.model == "proyeccion" and self.model is not None and proj_now is not None:
            self.model.adapt(pov, proj_now, bgmask & self.mapper.valid, cfg.bg_alpha * 0.3)
        elif (cfg.model == "fondo" or self.model is None) and self.bg is not None and self._bg_n >= cfg.warmup:
            self.bg[bgmask] += cfg.bg_alpha * (pov.astype(np.float32)[bgmask] - self.bg[bgmask])
        self.stats["frames"] += 1
        self.stats["ms"] = round(1000 * (time.perf_counter() - t0), 1)
        self.stats["fg_frac"] = round(float(fg.mean()), 4)
        self.debug = {"fg": fg, "shadow": shadow, "hand": hand > 0}
        return tips

    def _tip(self, comp: np.ndarray, box, shadow: np.ndarray, lit: np.ndarray, t: float) -> OsmoTip | None:
        cfg, s = self.cfg, self.scale
        x, y, w, h = (int(v) for v in box)
        ys, xs = np.nonzero(comp & self.band)
        if len(xs) >= 3:
            entry = np.array([xs.mean(), ys.mean()])
        else:
            side = cfg.arm_side
            cy, cx = np.nonzero(comp[y:y + h, x:x + w])
            if side in ("arriba", "top"):
                sel = cy == cy.min()
            elif side in ("izquierda", "left"):
                sel = cx == cx.min()
            elif side in ("derecha", "right"):
                sel = cx == cx.max()
            else:
                sel = cy == cy.max()
            entry = np.array([cx[sel].mean() + x, cy[sel].mean() + y])
        cnts, _ = cv2.findContours(comp[y:y + h, x:x + w].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not cnts:
            return None
        pts = np.vstack([c.reshape(-1, 2) for c in cnts]).astype(np.float64) + (x, y)
        d = np.linalg.norm(pts - entry, axis=1)
        far = pts[d >= d.max() - cfg.tip_smooth]
        tip = far.mean(axis=0)
        v = tip - entry
        nv = float(np.linalg.norm(v))
        tx_i, ty_i = int(round(tip[0])), int(round(tip[1]))
        if nv * s < cfg.min_reach or self.band[min(ty_i, self.band.shape[0] - 1), min(tx_i, self.band.shape[1] - 1)]:
            return None                                   # mancha pegada al borde: no es una mano que entra
        direction = (float(v[0] / nv), float(v[1] / nv))
        # sombra cerca de la punta
        r = cfg.shadow_radius
        tx, ty = int(round(tip[0])), int(round(tip[1]))
        x0, y0 = max(0, tx - r), max(0, ty - r)
        x1, y1 = min(shadow.shape[1], tx + r + 1), min(shadow.shape[0], ty + r + 1)
        sy, sx = np.nonzero(shadow[y0:y1, x0:x1])
        gap = None
        if len(sx) >= cfg.min_shadow_px:
            sp = np.stack([sx + x0, sy + y0], axis=1).astype(np.float64)
            ds = np.linalg.norm(sp - entry, axis=1)
            stip = sp[ds >= ds.max() - cfg.tip_smooth].mean(axis=0)
            gap = float(np.linalg.norm(stip - tip)) * s
        lit_here = float(np.median(lit[y0:y1, x0:x1])) if lit is not None else 0.0
        hb = cfg.hand_back / s
        return OsmoTip(float(tip[0] * s), float(tip[1] * s), float((tip[0] - direction[0] * hb) * s),
                       float((tip[1] - direction[1] * hb) * s), direction, float(comp.sum() * s * s), gap, lit_here, t,
                       (float(entry[0] * s), float(entry[1] * s)))

    def status(self) -> dict:
        return {"modelo": self.cfg.model if (self.cfg.model == "fondo" or self.model) else f"{self.cfg.model} (sin medir el proyector: fondo)",
                "gamma": round(self.model.gamma, 2) if self.model else None, **self.stats}

"""Capa de catálogo animada: cada ítem segmentado por SAM se pinta con su emoji repetido como patrón (tresbolillo,
desplazándose) recortado a la máscara, y su nombre corre como texto scrolling alrededor del contorno. Los contornos de
las máscaras se suavizan en vector (remuestreo a paso fijo + gaussiana circular) para que no se vean los escalones de SAM.

Uso: obj = CatalogoAnimado(dir_items, lw, lh, rot=180, emoji_de=fn, colores=dict); capa = obj.frame(time.time())
→ BGRA (lh, lw, 4) PREMULTIPLICADO (color ya multiplicado por alfa) en coordenadas del lienzo; el que llama la lleva
al proyector y compone: out = fondo·(1 − α) + color.

Costo por cuadro (2-nov): lo estático se arma una vez (mosaico del patrón ya replicado al tamaño del ítem, contornos
dibujados, glifos con su máscara); por cuadro solo se recorta el patrón con un offset y se pega con cv2.copyTo. Color
y fase salen del nombre (crc32), y el patrón está anclado al lienzo: al cambiar de catálogo (re-segmentación) un ítem
con el mismo nombre sigue con el mismo color y el patrón no salta.
"""

from __future__ import annotations

import json
import math
import zlib
from pathlib import Path

import cv2
import numpy as np

EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"
TEXT_FONT = "/System/Library/Fonts/Helvetica.ttc"


def suavizar_contorno(cnt: np.ndarray, paso: float = 2.0, sigma: float = 4.0, cerrado: bool = True) -> np.ndarray:
    """Contorno (N,1,2) o (N,2) de OpenCV → polilínea suave (M,2) float32: remuestreo a `paso` px de arco y filtro
    gaussiano circular de `sigma` muestras. Mantiene el área aproximada y borra el dentado de la máscara."""
    p = np.asarray(cnt, np.float64).reshape(-1, 2)
    if len(p) < 4:
        return p.astype(np.float32)
    if cerrado:
        p = np.vstack([p, p[:1]])
    seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
    L = float(seg.sum())
    if L < paso * 4:
        return p[:-1].astype(np.float32) if cerrado else p.astype(np.float32)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    n = max(8, int(L / paso))
    si = np.linspace(0.0, L, n, endpoint=False) if cerrado else np.linspace(0.0, L, n + 1)
    x = np.interp(si, s, p[:, 0]); y = np.interp(si, s, p[:, 1])
    if sigma > 0:
        r = int(3 * sigma); k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2); k /= k.sum()
        if cerrado:
            x = np.convolve(np.concatenate([x[-r:], x, x[:r]]), k, "valid")
            y = np.convolve(np.concatenate([y[-r:], y, y[:r]]), k, "valid")
        else:
            x = np.convolve(np.pad(x, r, "edge"), k, "valid"); y = np.convolve(np.pad(y, r, "edge"), k, "valid")
    return np.stack([x, y], 1).astype(np.float32)


def contornos_suaves(mask: np.ndarray, area_min: float = 40.0, sigma: float = 4.0) -> list[np.ndarray]:
    cnts, _ = cv2.findContours((mask > 127).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return [suavizar_contorno(c, 2.0, sigma) for c in cnts if cv2.contourArea(c) >= area_min]


def mascara_suave(mask: np.ndarray, sigma: float = 4.0) -> tuple[np.ndarray, list[np.ndarray]]:
    """Máscara 0/255 rehecha a partir de sus contornos suavizados (+ la lista de contornos)."""
    cs = contornos_suaves(mask, sigma=sigma)
    out = np.zeros(mask.shape[:2], np.uint8)
    if cs:
        cv2.fillPoly(out, [np.round(c).astype(np.int32) for c in cs], 255, cv2.LINE_AA)
    return out, cs


def path_svg(cs: list[np.ndarray], eps: float = 0.6) -> str:
    """Contornos suaves → atributo d de un <path> (simplificado con approxPolyDP para no escribir miles de puntos)."""
    parts = []
    for c in cs:
        ap = cv2.approxPolyDP(c.reshape(-1, 1, 2), eps, True).reshape(-1, 2)
        parts.append("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in ap) + " Z")
    return " ".join(parts)


def _semilla(label: str) -> int:
    """Semilla estable por nombre (no depende del orden del catálogo)."""
    return zlib.crc32(str(label).encode("utf-8"))


def _clockwise(p: np.ndarray) -> np.ndarray:
    """Orden visualmente horario en coordenadas de imagen (y hacia abajo): shoelace > 0."""
    x, y = p[:, 0], p[:, 1]
    area2 = float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))
    return p if area2 > 0 else p[::-1].copy()


class CatalogoAnimado:
    _GLIFOS: dict[tuple[str, int, int], tuple[np.ndarray, np.ndarray]] = {}

    def __init__(self, dir_: Path, lw: int, lh: int, rot: int = 180, emoji_de=None, colores: dict | None = None,
                 celda: int = 54, texto_px: int = 30, margen: int = 14, vel_texto: float = 45.0, vel_patron: float = 18.0):
        from PIL import Image, ImageDraw, ImageFont
        self.Image, self.ImageDraw = Image, ImageDraw
        self.lw, self.lh, self.rot = int(lw), int(lh), int(rot) % 360
        self.celda, self.texto_px, self.margen = celda, texto_px, margen
        self.vel_texto, self.vel_patron = vel_texto, vel_patron
        self.emoji_de = emoji_de or (lambda s: "📦")
        self.colores = colores or {}
        try:
            self.f_e = ImageFont.truetype(EMOJI_FONT, 64)
        except Exception:  # noqa: BLE001
            self.f_e = None
        try:
            self.f_t = ImageFont.truetype(TEXT_FONT, texto_px)
        except Exception:  # noqa: BLE001
            self.f_t = ImageFont.load_default()
        self._tex: dict[str, np.ndarray] = {}
        self._glifos = CatalogoAnimado._GLIFOS                          # caché compartida entre catálogos (rebase)
        self.items: list[dict] = []
        self.error: str | None = None
        cat = None
        for name in ("catalog_refinado.json", "catalog.json"):
            p_ = Path(dir_) / name
            if p_.exists():
                cat = json.loads(p_.read_text()); break
        if not cat:
            self.error = "sin catálogo"; return
        self.fuente = "catalog_refinado.json" if (Path(dir_) / "catalog_refinado.json").exists() else "catalog.json"
        d = int(self.margen + self.texto_px * 0.55)
        k_dil = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d + 1, 2 * d + 1))
        for it in sorted(cat["items"], key=lambda x: x.get("z", 0)):
            m = cv2.imread(str(Path(dir_) / it["mask"]), cv2.IMREAD_GRAYSCALE)
            if m is None or m.shape[0] != self.lh or m.shape[1] != self.lw:
                if m is not None:
                    m = cv2.resize(m, (self.lw, self.lh), interpolation=cv2.INTER_NEAREST)
                else:
                    continue
            ms, cs = mascara_suave(m, sigma=4.0)
            if not cs:
                continue
            ys, xs = np.nonzero(ms)
            x0, x1 = int(xs.min()), int(xs.max()) + 1; y0, y1 = int(ys.min()), int(ys.max()) + 1
            sem = _semilla(it["label"])
            col = self.colores.get(it["label"]) or tuple(int(v) for v in np.random.default_rng(sem).integers(80, 255, 3))
            # camino del texto: contorno exterior de la máscara dilatada (a `margen` + medio glifo del borde); la
            # dilatación se hace solo en la caja del ítem con holgura (mismo resultado, mucho más barato)
            pad = d + 4
            rx0, ry0, rx1, ry1 = max(0, x0 - pad), max(0, y0 - pad), min(self.lw, x1 + pad), min(self.lh, y1 + pad)
            dil = cv2.dilate(ms[ry0:ry1, rx0:rx1], k_dil)
            cd = [c + np.array([rx0, ry0], np.float32) for c in contornos_suaves(dil, sigma=6.0)]
            camino = None
            if cd:
                c0 = _clockwise(max(cd, key=len))
                seg = np.linalg.norm(np.diff(np.vstack([c0, c0[:1]]), axis=0), axis=1)
                L = float(seg.sum())
                if L > 60:
                    s = np.concatenate([[0.0], np.cumsum(seg)])
                    n = int(L)
                    si = np.arange(n, dtype=np.float64)
                    px = np.interp(si, s, np.append(c0[:, 0], c0[0, 0])); py = np.interp(si, s, np.append(c0[:, 1], c0[0, 1]))
                    tx = np.gradient(np.concatenate([px[-3:], px, px[:3]]))[3:-3]; ty = np.gradient(np.concatenate([py[-3:], py, py[:3]]))[3:-3]
                    nrm = np.hypot(tx, ty) + 1e-6; tx /= nrm; ty /= nrm
                    camino = {"x": px, "y": py, "L": L, "ang": np.degrees(np.arctan2(-tx, ty)) + 90.0}  # normal exterior = (ty, -tx); +90 lleva el "arriba" del glifo a la normal
            texto = f"{it['label']} · "
            adv = [max(4.0, self.f_t.getlength(ch)) for ch in texto]
            # repeticiones del nombre alrededor: 1 a 3 según el perímetro, repartidas parejo (y así el costo por cuadro es acotado)
            reps = 1 if camino is None else int(max(1, min(3, camino["L"] / (sum(adv) * 2.2))))
            emoji = self.emoji_de(it["label"])
            c = self.celda; w_, h_ = x1 - x0, y1 - y0
            tile = self._tile_item(emoji, col)
            # patrón ya replicado al tamaño del ítem + un período: por cuadro solo se recorta con el offset
            big = np.ascontiguousarray(np.tile(tile, (h_ // (2 * c) + 2, w_ // c + 2, 1)))
            self.items.append({"label": it["label"], "emoji": emoji, "msk": (ms[y0:y1, x0:x1] > 0).astype(np.uint8), "cs": cs,
                               "bbox": (x0, y0, x1, y1), "color": col, "camino": camino, "texto": texto, "adv": adv, "reps": reps,
                               "fase": (sem % 100000) / 100.0, "tile": tile, "big": big})
        # contornos (estáticos): una capa premultiplicada dibujada una vez y su máscara, recortadas a su caja
        self._borde = None
        if self.items:
            b = np.zeros((self.lh, self.lw, 4), np.uint8)
            for it in self.items:
                cv2.polylines(b, [np.round(cc).astype(np.int32) for cc in it["cs"]], True, (255, 255, 255, 255), 2, cv2.LINE_AA)
            ys, xs = np.nonzero(b[..., 3])
            if len(xs):
                bx0, by0, bx1, by1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
                bb = np.ascontiguousarray(b[by0:by1, bx0:bx1])
                self._borde = (bb, (bb[..., 3] > 0).astype(np.uint8), (bx0, by0, bx1, by1))
        self._precalentar_glifos()

    # ---- patrón de emojis -------------------------------------------------------------------------------------------
    def _tile_emoji(self, emoji: str):
        """Mosaico RGBA (PIL) de período (celda, 2·celda): dos filas en tresbolillo del emoji, girado para Max."""
        c = self.celda
        tile = self.Image.new("RGBA", (c, 2 * c), (0, 0, 0, 0))
        if self.f_e is not None:
            g = self.Image.new("RGBA", (72, 72), (0, 0, 0, 0))
            try:
                self.ImageDraw.Draw(g).text((4, 2), emoji, font=self.f_e, embedded_color=True)
            except Exception:  # noqa: BLE001
                pass
            g = g.resize((int(c * 0.72), int(c * 0.72)), self.Image.LANCZOS)
            if self.rot == 180:
                g = g.rotate(180)
            off = (c - g.size[0]) // 2
            tile.alpha_composite(g, (off, off))
            tile.alpha_composite(g, (off + c // 2, c + off))        # segunda fila corrida medio paso (partida en el borde
            tile.alpha_composite(g, (off + c // 2 - c, c + off))    # para que el mosaico cierre)
        return tile

    def _tile_item(self, emoji: str, col) -> np.ndarray:
        """Período BGRA premultiplicado ya compuesto: emoji sobre el color del ítem (alfa 235 en el emoji, 95 en el
        fondo); por cuadro solo se recorta con offset (sin aritmética en coma flotante)."""
        t = cv2.cvtColor(np.array(self._tile_emoji(emoji)), cv2.COLOR_RGBA2BGRA)
        a = t[..., 3:4].astype(np.float32) / 255.0 * 0.95
        A = np.where(t[..., 3] > 40, 235, 95).astype(np.float32)
        out = np.empty_like(t)
        rgb = t[..., :3].astype(np.float32) * a + np.array(col, np.float32) * (1 - a)
        out[..., :3] = np.round(rgb * (A[..., None] / 255.0)).astype(np.uint8)
        out[..., 3] = A.astype(np.uint8)
        return out

    # ---- glifos del texto -------------------------------------------------------------------------------------------
    def _glifo(self, ch: str, ang: float) -> tuple[np.ndarray, np.ndarray]:
        """Glifo BGRA premultiplicado del carácter con su 'arriba' girado `ang` grados (horario visual) y su máscara de
        pegado (alfa > 100); caché por paso de 6°."""
        q = int(round(ang / 6.0)) * 6 % 360
        key = (ch, q, self.texto_px)
        g = self._glifos.get(key)
        if g is not None:
            return g
        s = self.texto_px
        im = self.Image.new("RGBA", (int(s * 1.6) + 8, int(s * 1.6) + 8), (0, 0, 0, 0))
        d = self.ImageDraw.Draw(im)
        w = self.f_t.getlength(ch)
        xy = ((im.size[0] - w) / 2, im.size[1] / 2 - s * 0.62)
        d.text(xy, ch, font=self.f_t, fill=(0, 0, 0, 255), stroke_width=3, stroke_fill=(0, 0, 0, 255))
        d.text(xy, ch, font=self.f_t, fill=(255, 255, 255, 255))
        if q:
            im = im.rotate(-q, resample=self.Image.BICUBIC, expand=False)
        g = cv2.cvtColor(np.array(im), cv2.COLOR_RGBA2BGRA)
        m = (g[..., 3] > 100).astype(np.uint8)
        g[..., :3] = (g[..., :3].astype(np.uint16) * g[..., 3:4] // 255).astype(np.uint8)   # premultiplicado
        self._glifos[key] = (g, m)
        return g, m

    def _precalentar_glifos(self) -> None:
        """Rinde de una vez los glifos de todos los nombres en los 60 ángulos (caché compartida entre catálogos): así
        el primer segundo de un catálogo nuevo no paga PIL por cuadro."""
        for ch in {ch for it in self.items for ch in it["texto"] if ch != " "}:
            for q in range(0, 360, 6):
                self._glifo(ch, float(q))

    @staticmethod
    def _pegar(layer: np.ndarray, gm: tuple[np.ndarray, np.ndarray], cx: float, cy: float) -> None:
        """Pega el glifo por máscara dura (alfa > 100): entero puro, sin mezcla, suficiente a la escala proyectada."""
        g, m = gm
        h, w = g.shape[:2]
        x0 = int(cx - w / 2 + 0.5); y0 = int(cy - h / 2 + 0.5)
        X0, Y0 = max(0, x0), max(0, y0); X1, Y1 = min(layer.shape[1], x0 + w), min(layer.shape[0], y0 + h)
        if X1 <= X0 or Y1 <= Y0:
            return
        cv2.copyTo(g[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0], m[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0], layer[Y0:Y1, X0:X1])

    # ---- cuadro -----------------------------------------------------------------------------------------------------
    def frame(self, t: float) -> np.ndarray:
        """Capa BGRA premultiplicada (lh, lw, 4) en el instante t (segundos de reloj: el movimiento es continuo aunque
        el objeto se rehaga)."""
        layer = np.zeros((self.lh, self.lw, 4), np.uint8)
        c = self.celda
        for it in self.items:
            x0, y0, x1, y1 = it["bbox"]
            # patrón: recorte del mosaico precompuesto con un offset que avanza en diagonal; anclado al lienzo (el
            # offset incluye la esquina de la caja), así una caja que se corre unos px no desplaza el patrón
            ox = int((t * self.vel_patron + it["fase"] + x0) % c); oy = int((t * self.vel_patron * 0.6 + it["fase"] + y0) % (2 * c))
            w_, h_ = x1 - x0, y1 - y0
            cv2.copyTo(it["big"][oy:oy + h_, ox:ox + w_], it["msk"], layer[y0:y1, x0:x1])
        if self._borde is not None:
            bb, bm, (bx0, by0, bx1, by1) = self._borde
            cv2.copyTo(bb, bm, layer[by0:by1, bx0:bx1])
        for it in self.items:
            cam = it["camino"]
            if cam is None:
                continue
            L = cam["L"]; n = len(cam["x"]); xs, ys, angs = cam["x"], cam["y"], cam["ang"]
            s0 = (t * self.vel_texto + it["fase"]) % L
            paso_rep = L / it["reps"]
            for r in range(it["reps"]):
                pos = s0 + r * paso_rep
                for ch, adv in zip(it["texto"], it["adv"]):
                    if ch != " ":
                        k = int((pos + adv / 2) % L) % n
                        self._pegar(layer, self._glifo(ch, float(angs[k])), float(xs[k]), float(ys[k]))
                    pos += adv
        return layer

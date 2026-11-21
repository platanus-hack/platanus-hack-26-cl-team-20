"""Gray code: generación de patrones y decodificación de secuencias capturadas.

Esquema (el de sltk): para cada eje (x = columnas, y = filas del proyector) se muestran
`bits` patrones, del bit más significativo al menos, cada uno seguido de su inverso.
Un píxel de cámara decodifica el bit como 1 si el patrón es más brillante que su inverso
por más de `threshold`, 0 si es al revés, e inválido si la diferencia es menor. Un píxel
con cualquier bit inválido queda inválido en ese eje. Al final, gray -> binario da la
coordenada del proyector que ilumina ese píxel de cámara.

Las imágenes se procesan de a pares para no cargar la secuencia entera en memoria.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

INVALID = np.uint16(0xFFFF)
AXIS_X, AXIS_Y = 0, 1

# nombre de archivo de sltk: "<ts>_[AA-BB-CC_NN]_img.jpg" = [eje-bit-inverso_secuencia]
_SLTK_NAME = re.compile(r"\[(\d{2})-(\d{2})-(\d{2})_(\d+)\]")


# ----------------------------------------------------------------------------- patrones

def bin2gray(v: np.ndarray | int):
    return v ^ (v >> 1)


def gray2bin(g: np.ndarray) -> np.ndarray:
    """Gray -> binario para arreglos uint16 (hasta 16 bits)."""
    g = g.astype(np.uint16)
    for shift in (8, 4, 2, 1):
        g = g ^ (g >> shift)
    return g


def bits_for(n: int) -> int:
    """Bits necesarios para codificar n posiciones."""
    return max(1, int(np.ceil(np.log2(n))))


def pattern(size: tuple[int, int], axis: int, bit: int, inverse: bool = False) -> np.ndarray:
    """Imagen uint8 (alto, ancho) del bit `bit` del gray code sobre el eje dado."""
    w, h = size
    n = w if axis == AXIS_X else h
    coord = np.arange(n, dtype=np.uint32)
    line = (((bin2gray(coord) >> bit) & 1) * 255).astype(np.uint8)
    if inverse:
        line = 255 - line
    if axis == AXIS_X:
        return np.tile(line[None, :], (h, 1))
    return np.tile(line[:, None], (1, w))


@dataclass(frozen=True)
class PatternSpec:
    axis: int
    bit: int
    inverse: bool
    index: int

    @property
    def tag(self) -> str:
        return f"[{self.axis:02d}-{self.bit:02d}-{int(self.inverse):02d}_{self.index:02d}]"


def sequence(size: tuple[int, int], axes=(AXIS_X, AXIS_Y), with_inverse: bool = True,
             bits: int | None = None) -> list[PatternSpec]:
    """Orden de captura: por eje, bit MSB->LSB, patrón e inverso (igual que sltk)."""
    w, h = size
    specs = []
    i = 0
    for axis in axes:
        nb = bits if bits is not None else bits_for(w if axis == AXIS_X else h)
        for bit in range(nb - 1, -1, -1):
            specs.append(PatternSpec(axis, bit, False, i)); i += 1
            if with_inverse:
                specs.append(PatternSpec(axis, bit, True, i)); i += 1
    return specs


# ----------------------------------------------------------------------------- listas

def parse_sltk_name(name: str) -> PatternSpec | None:
    m = _SLTK_NAME.search(name)
    if not m:
        return None
    axis, bit, inv, idx = (int(x) for x in m.groups())
    return PatternSpec(axis, bit, bool(inv), idx)


def load_sequence(scan_dir: str | Path) -> list[tuple[PatternSpec, Path]]:
    """Lee `list.yaml` de sltk (o `sequence.yaml`), o toma los archivos con nombre [AA-BB-CC_NN]."""
    scan_dir = Path(scan_dir)
    names: list[str] = []
    for yaml_name in ("list.yaml", "sequence.yaml"):
        f = scan_dir / yaml_name
        if f.exists():
            fs = cv2.FileStorage(str(f), cv2.FILE_STORAGE_READ)
            node = fs.getNode("gcb_images")
            names = [node.at(i).string() for i in range(node.size())]
            fs.release()
            break
    if not names:
        names = sorted(p.name for p in scan_dir.iterdir() if _SLTK_NAME.search(p.name))
    out = []
    for n in names:
        spec = parse_sltk_name(n)
        if spec is None:
            raise ValueError(f"nombre sin patrón [eje-bit-inv_idx]: {n}")
        out.append((spec, scan_dir / n))
    if not out:
        raise FileNotFoundError(f"sin imágenes de secuencia en {scan_dir}")
    return out


# ----------------------------------------------------------------------------- decodificación

def _luma_int(img: np.ndarray) -> np.ndarray:
    """Luma entera como sltk: int(0.299 R + 0.587 G + 0.114 B) con imagen BGR."""
    if img.ndim == 2:
        return img.astype(np.int32)
    b, g, r = img[..., 0].astype(np.float64), img[..., 1].astype(np.float64), img[..., 2].astype(np.float64)
    return (0.299 * r + 0.587 * g + 0.114 * b).astype(np.int32)


@dataclass
class Decoded:
    """Mapa denso cámara -> proyector."""
    size: tuple[int, int]            # tamaño de la cámara (ancho, alto)
    px: np.ndarray                   # (h, w) uint16 columna del proyector, INVALID si no
    py: np.ndarray                   # (h, w) uint16 fila del proyector, INVALID si no
    color: np.ndarray                # (h, w, 3) float32 BGR promedio de los cuadros brillantes
    stats: dict
    bit_contrast: np.ndarray | None = None  # (bits,h,w) uint8 |patrón-inverso|, orden stats.bit_order

    @property
    def valid(self) -> np.ndarray:
        valid = (self.px != INVALID) & (self.py != INVALID)
        if "projector_size" in self.stats:
            w, h = self.stats["projector_size"]
            # El código almacena el representante entero del bloque; el último
            # bloque parcial puede tener su representante fuera de la imagen.
            step = 1 << self.stats.get("skip_bits", 0)
            valid &= (self.px // step * step < w) & (self.py // step * step < h)
        return valid

    def points(self):
        """(cam_xy int32 (n,2), proj_xy int32 (n,2), color float32 (n,3)) en orden de fila."""
        ys, xs = np.nonzero(self.valid)
        cam = np.stack([xs, ys], axis=1).astype(np.int32)
        pro = np.stack([self.px[ys, xs], self.py[ys, xs]], axis=1).astype(np.int32)
        return cam, pro, self.color[ys, xs]

    def save(self, path: str | Path) -> None:
        import json
        extra = {} if self.bit_contrast is None else {"bit_contrast": self.bit_contrast}
        np.savez_compressed(path, px=self.px, py=self.py, color=self.color,
                            size=np.array(self.size), stats=np.frombuffer(json.dumps(self.stats).encode(), np.uint8), **extra)

    @classmethod
    def load(cls, path: str | Path) -> "Decoded":
        import json
        with np.load(path, allow_pickle=False) as z:
            stats = json.loads(z["stats"].tobytes().decode()) if "stats" in z.files else {}
            return cls(tuple(int(v) for v in z["size"]), z["px"], z["py"], z["color"], stats,
                       z["bit_contrast"] if "bit_contrast" in z.files else None)


def decode(items: list[tuple[PatternSpec, Path | np.ndarray]], threshold: int = 5,
           channel: int | None = None, progress=None, skip_bits: int = 0,
           projector_size: tuple[int, int] | None = None, retain_contrast: bool = False) -> Decoded:
    """Decodifica una secuencia gray code (patrón + inverso por bit, ejes x e y).

    items: [(spec, ruta o imagen BGR)]. Se agrupan por (eje, bit); cada grupo necesita
    el patrón y su inverso. `channel` elige un canal BGR en vez de la luma.
    """
    if not 0 <= skip_bits < 16 or threshold < 0:
        raise ValueError("skip_bits debe estar en [0,15] y threshold >= 0")
    if projector_size is not None and any(not 0 < v < 65535 for v in projector_size):
        raise ValueError("projector_size fuera de rango")
    by_key: dict[tuple[int, int], dict[bool, Path | np.ndarray]] = {}
    for spec, src in items:
        if spec.axis not in (0, 1) or not 0 <= spec.bit < 16:
            raise ValueError("eje/bit inválido")
        if spec.inverse in by_key.get((spec.axis, spec.bit), {}):
            raise ValueError("patrón duplicado")
        by_key.setdefault((spec.axis, spec.bit), {})[spec.inverse] = src

    maps = {AXIS_X: None, AXIS_Y: None}
    color_sum = None
    color_n = None
    stats = {"threshold": threshold, "bits": {AXIS_X: 0, AXIS_Y: 0}, "avg_diff": {}}

    def load(src):
        if isinstance(src, np.ndarray):
            return src
        img = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise FileNotFoundError(src)
        return img

    keys = sorted(by_key, key=lambda k: (k[0], -k[1]))   # eje, MSB primero
    # skip_bits: los bits más finos no se resuelven cuando la cámara ve pocos px por px de proyector:
    # se ignoran (coordenada cuantizada a 2^skip_bits px) en vez de invalidar el píxel
    keys = [k for k in keys if k[1] >= skip_bits]
    for axis in (0, 1):
        bits = {bit for ax, bit in keys if ax == axis}
        top = bits_for(projector_size[axis]) if projector_size else max(bits, default=-1) + 1
        if not bits or bits != set(range(skip_bits, top)):
            raise ValueError("faltan bits contiguos para el tamaño del proyector")
    contrasts = []
    for n, (axis, bit) in enumerate(keys):
        pair = by_key[(axis, bit)]
        if False not in pair or True not in pair:
            raise ValueError(f"falta patrón o inverso para eje {axis} bit {bit}")
        img, inv = load(pair[False]), load(pair[True])
        if img.shape != inv.shape:
            raise ValueError("patrón e inverso de distinto tamaño")
        h, w = img.shape[:2]
        if color_sum is not None and color_sum.shape[:2] != (h, w):
            raise ValueError("la secuencia cambia de tamaño")
        if maps[axis] is None:
            maps[axis] = np.zeros((h, w), np.uint16)
            stats["bits"][axis] = 0
        if color_sum is None:
            color_sum = np.zeros((h, w, 3), np.float32)
            color_n = np.zeros((h, w), np.float32)

        if channel is not None and img.ndim == 3:
            g0, g1 = img[..., channel].astype(np.int32), inv[..., channel].astype(np.int32)
        else:
            g0, g1 = _luma_int(img), _luma_int(inv)
        diff = g0 - g1
        if retain_contrast:
            contrasts.append(np.clip(np.abs(diff), 0, 255).astype(np.uint8))
        one = diff > threshold
        zero = diff < -threshold
        bad = ~(one | zero)

        m = maps[axis]
        m |= (one.astype(np.uint16) << np.uint16(bit))
        m[bad] = INVALID
        stats["bits"][axis] += 1
        sig = np.abs(diff)[~bad]
        stats["avg_diff"][f"{'xy'[axis]}{bit}"] = float(sig.mean()) if sig.size else -1.0

        # color: cuadro más brillante de cada par, promediado sobre todos los pares
        c0 = img if img.ndim == 3 else np.repeat(img[..., None], 3, axis=2)
        c1 = inv if inv.ndim == 3 else np.repeat(inv[..., None], 3, axis=2)
        color_sum[one] += c0[one]
        color_sum[zero] += c1[zero]
        color_n[one | zero] += 1
        if progress:
            progress(n + 1, len(keys), axis, bit, stats["avg_diff"][f"{'xy'[axis]}{bit}"])

    if maps[AXIS_X] is None or maps[AXIS_Y] is None:
        raise ValueError("se necesitan secuencias de ambos ejes")
    gx, gy = maps[AXIS_X], maps[AXIS_Y]
    bad = (gx == INVALID) | (gy == INVALID)
    px = gray2bin(gx); py = gray2bin(gy)
    px[bad] = INVALID; py[bad] = INVALID
    color = color_sum / np.maximum(color_n, 1)[..., None]
    h, w = px.shape
    stats["valid"] = int((~bad).sum())
    stats["valid_x"] = int((gx != INVALID).sum())
    stats["valid_y"] = int((gy != INVALID).sum())
    stats["total"] = int(h * w)
    stats["bits"] = dict(stats["bits"]); stats["skip_bits"] = skip_bits
    if skip_bits:
        # sin los bits finos, gray2bin deja los bits bajos en 0 o en 1s (según el bit siguiente): se limpian antes de
        # ir al centro del bloque; si no, la mitad de los bloques queda corrida 1 px (lo encontró el agente de metric)
        half = np.uint16((1 << skip_bits) // 2)
        px[~bad] = ((px[~bad] >> skip_bits) << skip_bits) | half; py[~bad] = ((py[~bad] >> skip_bits) << skip_bits) | half
    stats.update(decoder_version="slscan-gray/2", code_storage="integer_block_representative",
                 pixel_convention="opencv_integer_centers", bit_order=[list(k) for k in keys])
    if projector_size is not None:
        stats["projector_size"] = list(projector_size)
        step = 1 << skip_bits
        outside = ~bad & ((px // step * step >= projector_size[0]) | (py // step * step >= projector_size[1]))
        stats["out_of_bounds"] = int(outside.sum())
        px[outside] = INVALID; py[outside] = INVALID
        stats["valid"] = int((~bad & ~outside).sum())
    return Decoded((w, h), px, py, np.clip(color, 0, 255).astype(np.float32), stats,
                   np.stack(contrasts) if retain_contrast else None)


# ----------------------------------------------------------------------------- visualización

def colormap(t: np.ndarray) -> np.ndarray:
    """Arcoíris rojo->amarillo->verde->cian->azul como `float2Color` de sltk. t en [0,1] -> BGR uint8."""
    t = np.clip(np.asarray(t, np.float32), 0, 1)
    hsv = np.zeros((t.size, 1, 3), np.uint8)
    hsv[:, 0, 0] = (t.ravel() * 120).astype(np.uint8)     # 0 rojo .. 120 azul (OpenCV H en 0..180)
    hsv[:, 0, 1] = 255
    hsv[:, 0, 2] = 255
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR).reshape(t.shape + (3,))


def visualize(dec: Decoded) -> dict[str, np.ndarray]:
    """Imágenes de control: rgb (color promedio), x e y (coordenada del proyector en arcoíris)."""
    valid = dec.valid
    rgb = np.where(valid[..., None], dec.color, 0).astype(np.uint8)
    out = {"rgb": rgb}
    for name, m in (("x", dec.px), ("y", dec.py)):
        vis = np.full(m.shape + (3,), 32, np.uint8)
        if valid.any():
            v = m[valid].astype(np.float32)
            lo, hi = v.min(), max(v.max(), v.min() + 1)
            vis[valid] = colormap((v - lo) / (hi - lo))
        out[name] = vis
    return out

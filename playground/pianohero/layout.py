"""Geometría del Piano Hero en píxeles del proyector: teclas (keys.json), carriles, línea de golpe y HUD.

Cada carril es el polígono exacto de su tecla prolongado hacia atrás: sus dos bordes (izquierdo y derecho) se
extienden como rectas desde el borde trasero de la tecla, en la dirección frente → atrás. Como los bordes salen de un
modelo proyectivo (detect.py o fit.py), las rectas de todas las teclas convergen al mismo punto de fuga y los carriles
quedan pegados, sin huecos ni cruces. ``s`` = distancia en px desde la línea de las teclas, a lo largo del carril.

Fuentes de las teclas: la escena del runtime si trae un objeto con ``extra["keys"]`` (``from_scene``: así corre como
efecto en el runtime que ya proyecta, sin abrir otra ventana); si no, keys.json en este orden: el argumento,
``PIANOHERO_KEYS``, ``captures/scan5/keys.json``, ``captures/scan2/keys.json``. Si el keys.json elegido tiene confianza baja (< 0,5) y al lado está ``scene_pov.png``, se ajusta el
modelo del teclado al vuelo (``playground.keys.fit``, ~2 s) sin escribir nada. Si no hay ninguno, un teclado
sintético recto (para probar sin escaneo). El HUD va sobre la hoja blanca (``papel`` de segments.json), si no sobre
la torre, si no arriba de los carriles.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parent.parent.parent
SIZE = (1920, 1080)
CANDIDATOS = ("captures/scan5/keys.json", "captures/scan2/keys.json")
NOMBRES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def nombre_nota(m: int) -> str:
    return f"{NOMBRES[m % 12]}{m // 12 - 1}"


def _unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else np.array([0.0, -1.0])


@dataclass
class KeyGeom:
    midi: int
    name: str
    black: bool
    poly: np.ndarray            # 4x2: atrás-izq, atrás-der, frente-der, frente-izq
    contour: np.ndarray         # Nx2 (blancas con muescas)
    dir_l: np.ndarray           # hacia atrás, unitario, por el borde izquierdo
    dir_r: np.ndarray
    confidence: float = 1.0

    @property
    def back_l(self) -> np.ndarray:
        return self.poly[0]

    @property
    def back_r(self) -> np.ndarray:
        return self.poly[1]

    def at(self, s: float, frac: float) -> np.ndarray:
        """Punto del carril a distancia s de la línea de teclas y fracción `frac` (0 izq .. 1 der) del ancho."""
        L = self.back_l + self.dir_l * s
        R = self.back_r + self.dir_r * s
        return L + (R - L) * frac

    def lane_quad(self, s0: float, s1: float, inset: float = 0.0) -> np.ndarray:
        """Trozo del carril entre s0 y s1 (px), achicado `inset` (fracción del ancho) por lado."""
        return np.array([self.at(s0, inset), self.at(s0, 1 - inset), self.at(s1, 1 - inset), self.at(s1, inset)])


@dataclass
class Layout:
    keys: dict[int, KeyGeom]
    size: tuple[int, int] = SIZE
    lane_px: float = 260.0
    hud: tuple[float, float, float, float] = (0, 0, 0, 0)     # x, y, w, h
    hud_source: str = ""
    source: str = "sintetico"
    method: str = ""
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)

    @property
    def lo(self) -> int:
        return min(self.keys)

    @property
    def hi(self) -> int:
        return max(self.keys)

    def whites(self) -> list[KeyGeom]:
        return [k for m, k in sorted(self.keys.items()) if not k.black]

    def hit_line(self) -> np.ndarray:
        """Borde trasero de las teclas, de izquierda a derecha."""
        w = self.whites()
        return np.array([w[0].back_l] + [k.back_r for k in w])

    def bar_line(self, s: float) -> np.ndarray:
        w = self.whites()
        return np.array([w[0].at(s, 0.0)] + [k.at(s, 1.0) for k in w])

    def key_len(self) -> float:
        return float(np.median([np.linalg.norm(k.poly[0] - k.poly[3]) for k in self.whites()]))

    def scaled(self, size: tuple[int, int]) -> "Layout":
        """La misma geometría para un lienzo de otro tamaño (tests, vista previa)."""
        if tuple(size) == tuple(self.size):
            return self
        sx, sy = size[0] / self.size[0], size[1] / self.size[1]
        S = np.array([sx, sy])
        keys = {}
        for m, k in self.keys.items():
            keys[m] = KeyGeom(k.midi, k.name, k.black, k.poly * S, k.contour * S, _unit(k.dir_l * S), _unit(k.dir_r * S),
                              k.confidence)
        x, y, w, h = self.hud
        f = (sx + sy) / 2
        return Layout(keys, tuple(size), self.lane_px * f, (x * sx, y * sy, w * sx, h * sy), self.hud_source,
                      self.source, self.method, self.confidence, list(self.warnings))

    def to_json(self) -> dict:
        return {"source": self.source, "method": self.method, "confidence": round(self.confidence, 3),
                "keys": len(self.keys), "range": [nombre_nota(self.lo), nombre_nota(self.hi)],
                "lane_px": round(self.lane_px, 1), "hud": [round(v) for v in self.hud], "hud_source": self.hud_source,
                "size": list(self.size), "warnings": self.warnings}


# ---------------------------------------------------------------------------------------------- construcción

def from_keys(data: dict, size=SIZE, lane_px: float | None = None) -> Layout:
    """Layout desde el contenido de un keys.json (detect.py o fit.py)."""
    keys: dict[int, KeyGeom] = {}
    for t in data["teclas"]:
        if t.get("estado") == "fuera":
            continue
        P = np.asarray(t["poligono"], float)
        # orden esperado: atrás-izq, atrás-der, frente-der, frente-izq
        dl = _unit(P[0] - P[3]); dr = _unit(P[1] - P[2])
        cont = np.asarray(t.get("contorno") or t["poligono"], float)
        keys[int(t["nota"])] = KeyGeom(int(t["nota"]), t.get("nombre") or nombre_nota(int(t["nota"])),
                                       t.get("color") == "negra", P, cont, dl, dr, float(t.get("confianza", 1.0)))
    if not keys:
        raise ValueError("keys.json sin teclas dentro del cuadro")
    lay = Layout(keys, tuple(size), 0.0, source="", method=data.get("metodo", "bordes"),
                 confidence=float(data.get("confianza_media", 1.0)))
    lay.lane_px = float(lane_px) if lane_px else float(np.clip(2.3 * lay.key_len(), 150.0, 420.0))
    return lay


def synthetic(size=SIZE, lo: int = 36, n: int = 61, lane_px: float | None = None) -> Layout:
    """Teclado recto centrado abajo (sin escaneo): para probar y para la vista previa."""
    W, H = size
    blancas = [m for m in range(lo, lo + n) if m % 12 in (0, 2, 4, 5, 7, 9, 11)]
    paso = W * 0.8 / len(blancas)
    x0 = W * 0.1; atras = H * 0.78; frente = atras + paso * 5.5
    ln = paso * 5.5 * 0.62; an = paso * 7 / 12
    keys = {}
    k = 0
    for m in range(lo, lo + n):
        if m % 12 in (0, 2, 4, 5, 7, 9, 11):
            P = np.array([[x0 + k * paso, atras], [x0 + (k + 1) * paso, atras], [x0 + (k + 1) * paso, frente], [x0 + k * paso, frente]])
            black = False; k += 1
        else:
            c = x0 + k * paso
            P = np.array([[c - an / 2, atras], [c + an / 2, atras], [c + an / 2, atras + ln], [c - an / 2, atras + ln]])
            black = True
        keys[m] = KeyGeom(m, nombre_nota(m), black, P, P.copy(), np.array([0.0, -1.0]), np.array([0.0, -1.0]))
    lay = Layout(keys, tuple(size), lane_px or H * 0.45, source="sintetico", method="sintetico")
    lay.hud = (W * 0.1, H * 0.03, W * 0.8, H * 0.16); lay.hud_source = "arriba"
    return lay


def place_hud(lay: Layout, boxes: dict, hud="auto") -> None:
    """HUD sobre la hoja (papel) o la torre si están en `boxes` ({nombre: (x, y, w, h)} en px del proyector); si no,
    arriba de los carriles. `hud` = "auto" | "papel" | "torre" | [x, y, w, h]."""
    if isinstance(hud, (list, tuple)) and len(hud) == 4:
        lay.hud = tuple(float(v) for v in hud); lay.hud_source = "param"; return
    orden = [hud] if hud in ("papel", "torre") else ["papel", "torre"]
    for nombre in orden:
        bb = boxes.get(nombre)
        if not bb:
            continue
        x, y, w, h = [float(v) for v in bb]
        if nombre == "torre":                        # la torre es alta: el HUD en su tercio de arriba
            h = h * 0.4
        m = 0.08
        x, y, w, h = x + w * m, y + h * m, w * (1 - 2 * m), h * (1 - 2 * m)
        # que no tape la punta de los carriles que pasan por debajo
        tops = [k.at(s, f) for k in lay.keys.values() for s in (lay.lane_px * 0.5, lay.lane_px) for f in (0.0, 1.0)]
        ys = [p[1] for p in tops if x - 5 <= p[0] <= x + w + 5 and p[1] > y]
        if ys and min(ys) - 12 < y + h and min(ys) - 12 - y >= 140:
            h = min(ys) - 12 - y
        lay.hud = (x, y, w, h); lay.hud_source = nombre
        return
    # arriba de los carriles, a lo ancho del teclado
    tops = np.array([k.at(lay.lane_px, 0.5) for k in lay.whites()])
    x0, x1 = float(tops[:, 0].min()), float(tops[:, 0].max())
    y1 = float(tops[:, 1].min()) - 20
    h = min(180.0, max(80.0, y1 - 10))
    lay.hud = (x0, max(10.0, y1 - h), x1 - x0, h); lay.hud_source = "arriba"


def _hud_from_scan(lay: Layout, scan: Path | None, hud) -> None:
    seg = {}
    if scan is not None and (scan / "segments.json").exists():
        try:
            seg = json.loads((scan / "segments.json").read_text())
        except (OSError, json.JSONDecodeError):
            seg = {}
    place_hud(lay, {k: (v or {}).get("bbox") for k, v in seg.items() if isinstance(v, dict)}, hud)


def scene_keys(objects: dict):
    """(id, lista de teclas) del objeto de la escena que trae las teclas en extra["keys"] (formato keys.json), o None."""
    orden = sorted(objects.items(), key=lambda kv: kv[0] != "keys")
    for oid, o in orden:
        extra = o.get("extra") if isinstance(o, dict) else getattr(o, "extra", None)
        ks = (extra or {}).get("keys")
        if isinstance(ks, list) and len(ks) >= 12 and isinstance(ks[0], dict) and "poligono" in ks[0]:
            return oid, ks
    return None


def from_scene(objects: dict, lane_px: float | None = None, hud="auto", size=SIZE) -> Layout | None:
    """Layout desde la escena del runtime: teclas de objects["keys"].extra["keys"] y HUD sobre objects["papel"] o
    ["torre"] (bbox [x0, y0, z0, x1, y1, z1] en px del proyector, como las teclas). None si la escena no trae teclas."""
    found = scene_keys(objects)
    if found is None:
        return None
    oid, ks = found
    conf = float(np.mean([float(t.get("confianza", 1.0)) for t in ks]))
    lay = from_keys({"teclas": ks, "metodo": "escena", "confianza_media": conf}, SIZE, lane_px)
    lay.source = f"escena:{oid}"
    boxes = {}
    for nombre in ("papel", "torre"):
        o = objects.get(nombre)
        b = (o.get("bbox") if isinstance(o, dict) else getattr(o, "bbox", None)) if o is not None else None
        if b and len(b) == 6:
            boxes[nombre] = (b[0], b[1], b[3] - b[0], b[4] - b[1])
    place_hud(lay, boxes, hud)
    return lay.scaled(size)


def resolve_keys_path(path: str | Path | None = None) -> Path | None:
    cands = [path, os.environ.get("PIANOHERO_KEYS")] + [PROJECT / c for c in CANDIDATOS]
    for c in cands:
        if not c:
            continue
        p = Path(c).expanduser()
        if not p.is_absolute():
            p = PROJECT / p
        if p.is_dir():
            p = p / "keys.json"
        if p.exists():
            return p
    return None


def load_layout(keys: str | Path | None = None, lane_px: float | None = None, hud="auto", size=SIZE,
                min_confidence: float = 0.5, refit: bool = True) -> Layout:
    """Layout listo para dibujar. Ver el docstring del módulo para el orden de búsqueda y el ajuste al vuelo."""
    p = resolve_keys_path(keys)
    if p is None:
        lay = synthetic(size, lane_px=lane_px)
        lay.warnings.append("sin keys.json: teclado sintético (no cae sobre las teclas reales)")
        return lay
    data = json.loads(p.read_text())
    warns = []
    scan = p.parent
    if refit and float(data.get("confianza_media", 1.0)) < min_confidence and (scan / "scene_pov.png").exists():
        try:
            import cv2
            from ..keys.fit import ajustar_modelo
            from ..keys.__main__ import cargar_rect
            img = cv2.imread(str(scan / "scene_pov.png"))
            fit = ajustar_modelo(img, cargar_rect(scan, img))
            if fit["confianza_media"] > float(data.get("confianza_media", 0)):
                warns.append(f"{p.name} con confianza {data.get('confianza_media')}: uso el ajuste por modelo al vuelo "
                             f"(correlación {fit['confianza_media']:.2f}); para dejarlo fijo: python -m playground.keys {scan}")
                data = fit
        except Exception as e:  # noqa: BLE001  (el juego igual corre con lo que hay)
            warns.append(f"ajuste por modelo falló: {e}")
    if float(data.get("confianza_media", 1.0)) < min_confidence:
        warns.append(f"teclas con confianza baja ({data.get('confianza_media')}): verificar antes de jugar")
    lay = from_keys(data, SIZE, lane_px)
    lay.source = str(p)
    lay.warnings += warns
    _hud_from_scan(lay, scan, hud)
    return lay.scaled(size)

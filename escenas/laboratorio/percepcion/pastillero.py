"""Pastillero 7×4 como sistema de coordenadas.

Entrada: la vista cenital rectificada a la mesa (1 px = `mm_por_px` mm; las de `capturas/` son 1 px = 1 mm).
Salida: la grilla con sus 28 celdas, cada una con dirección (A1…D7), centro y polígono en mm, y color.

Convención (atada al objeto, no a la cámara, para que no cambie si giran la caja):
- Columnas 1 a 7 en el orden de la `paleta` (la del pastillero de la demo: de la blanca a la rosada).
- Filas A a D: A es la del borde y D la que queda junto a la bisagra de la tapa. El eje de filas es el de columnas
  girado +90° en la imagen (y hacia abajo), así la convención sigue igual aunque la caja esté girada.

Funciones puras: numpy + OpenCV, sin estado.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

FILAS = "ABCD"
N_COLUMNAS = 7
N_FILAS = 4

# Colores de las columnas del pastillero de la demo, de la columna 1 a la 7. Se puede cambiar en protocolo.yaml.
PALETA_DEMO = ("blanco", "morado", "amarillo", "azul", "verde", "magenta", "rosado")

# Tono de referencia en OpenCV (0–179) para cada nombre. «blanco» se reconoce por saturación baja.
TONOS = {"rojo": 0, "naranjo": 12, "amarillo": 34, "verde": 68, "celeste": 92, "azul": 105, "morado": 121, "magenta": 160, "rosado": 170}

# Tonos 8–27 son el canasto, la madera, la mesa beige y la luz cálida: se excluyen de la máscara de colores.
_TONO_CALIDO = (8, 27)


@dataclass
class Celda:
    dir: str  # «B3»
    fila: str  # «B»
    col: int  # 3
    centro_mm: list[float]
    poligono_mm: list[list[float]]  # 4 esquinas
    color: str  # nombre en español (el de la paleta para esa columna)
    hex: str  # color medido en la foto


@dataclass
class Grilla:
    ok: bool
    confianza: float = 0.0  # fracción de columnas cuyo color calza con la paleta
    centro_mm: list[float] = field(default_factory=list)
    angulo_deg: float = 0.0  # ángulo del eje de columnas (1→7) en la imagen
    paso_mm: float = 0.0  # distancia entre centros de celdas vecinas
    eje_columnas: list[float] = field(default_factory=list)  # vector unitario 1→7
    eje_filas: list[float] = field(default_factory=list)  # vector unitario A→D
    esquinas_mm: list[list[float]] = field(default_factory=list)  # esquinas externas de A1, A7, D7, D1
    celdas: list[Celda] = field(default_factory=list)
    motivo: str = ""  # por qué falló, si ok es False

    def celda(self, direccion: str) -> Celda:
        d = direccion.strip().upper()
        for c in self.celdas:
            if c.dir == d:
                return c
        raise KeyError(f"No hay celda «{direccion}». Van de A1 a {FILAS[-1]}{N_COLUMNAS}.")

    def a_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def desde_dict(cls, d: dict) -> "Grilla":
        d = dict(d)
        d["celdas"] = [Celda(**c) for c in d.get("celdas", [])]
        return cls(**d)


def direccion_valida(direccion: str) -> bool:
    d = direccion.strip().upper()
    return len(d) >= 2 and d[0] in FILAS and d[1:].isdigit() and 1 <= int(d[1:]) <= N_COLUMNAS


def _hsv(bgr) -> tuple[int, int, int]:
    px = np.uint8([[list(map(int, bgr))]])
    return tuple(int(x) for x in cv2.cvtColor(px, cv2.COLOR_BGR2HSV)[0, 0])


def nombre_color(bgr) -> str:
    """Nombre corto de un color medido, sin paleta (para pastilleros que no son el de la demo)."""
    h, s, v = _hsv(bgr)
    if s < 45:
        return "blanco" if v > 120 else "gris"
    return min(TONOS, key=lambda n: _dist_tono(h, TONOS[n]))


def _dist_tono(a: float, b: float) -> float:
    d = abs(a - b) % 180
    return min(d, 180 - d)


def _calza(hsv, nombre: str) -> bool:
    h, s, v = hsv
    if nombre == "blanco":
        return s < 60 and v > 140
    if nombre not in TONOS:
        return False
    return s >= 30 and _dist_tono(h, TONOS[nombre]) <= 14


def _hex(bgr) -> str:
    b, g, r = (int(round(x)) for x in bgr)
    return f"#{r:02x}{g:02x}{b:02x}"


def _mascara_colores(hsv: np.ndarray) -> np.ndarray:
    h, s, v = cv2.split(hsv)
    m = (s > 70) & (v > 110) & ((h < _TONO_CALIDO[0]) | (h > _TONO_CALIDO[1]))
    m = m.astype(np.uint8) * 255
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))


def _variedad_tonos(tonos: np.ndarray) -> int:
    """Cuántos tonos distintos hay (bins de 15° con al menos 4 % de los píxeles)."""
    if tonos.size == 0:
        return 0
    hist = np.histogram(tonos, bins=12, range=(0, 180))[0]
    return int((hist > 0.04 * hist.sum()).sum())


def _muestra(img: np.ndarray, centro, radio: float):
    """Mediana BGR de un parche cuadrado. None si cae fuera de la imagen."""
    x, y = (int(round(c)) for c in centro)
    r = max(1, int(radio))
    h, w = img.shape[:2]
    if x - r < 0 or y - r < 0 or x + r >= w or y + r >= h:
        return None
    return np.median(img[y - r : y + r + 1, x - r : x + r + 1].reshape(-1, 3), axis=0)


def _medir_columnas(img, origen, u, v, paso):
    medidos = []
    for col in range(N_COLUMNAS):
        muestras = [_muestra(img, origen + u * (col + 0.5) * paso + v * (f + 0.5) * paso, paso * 0.22) for f in range(N_FILAS)]
        muestras = [m for m in muestras if m is not None]
        medidos.append(np.median(muestras, axis=0) if muestras else None)
    return medidos


def _ajustar(img, c, u, largo, corto, paleta):
    """Arma la grilla 7×4 sobre el bloque de color centrado en `c`, con el eje `u` hacia la columna 7.

    Las 4 filas se ven enteras (cada columna es de un solo color), así que el paso sale del lado corto. A lo largo, el
    bloque puede tener menos de 7 columnas (la blanca no satura; una pálida puede perderse en un extremo): se desliza
    la grilla columna a columna y gana el corrimiento que mejor calza con la paleta.
    Devuelve (aciertos, origen, paso, eje de filas, colores medidos por columna).
    """
    paso = corto / N_FILAS
    v = np.array([-u[1], u[0]])
    inicio = c - u * (largo / 2) - v * (corto / 2)  # esquina del bloque de color
    cols_color = int(np.clip(round(largo / paso), 1, N_COLUMNAS))
    mejor = None
    for s in range(0, N_COLUMNAS - cols_color + 1):
        origen = inicio - u * s * paso
        medidos = _medir_columnas(img, origen, u, v, paso)
        if paleta is not None:
            aciertos = sum(1 for col, bgr in enumerate(medidos) if bgr is not None and _calza(_hsv(bgr), paleta[col]))
        else:
            # Sin paleta: la columna 1 es blanca y las otras seis tienen color.
            hsv = [_hsv(b) if b is not None else (0, 0, 0) for b in medidos]
            aciertos = (hsv[0][2] - 1.5 * hsv[0][1]) / 100 + sum(1 for h in hsv[1:] if h[1] >= 40)
        if mejor is None or aciertos > mejor[0]:
            mejor = (aciertos, origen, paso, v, medidos)
    return mejor


def detectar(
    img_mm: np.ndarray, mm_por_px: float = 1.0, region_mm: tuple | None = None, paleta: tuple | list | None = PALETA_DEMO
) -> Grilla:
    """Busca el pastillero en la vista cenital rectificada y arma la grilla 7×4.

    `region_mm` (x, y, ancho, alto) limita la búsqueda si ya se sabe dónde está; si no, busca en toda la foto.
    `paleta`: colores de las columnas 1 a 7. Con ella se elige el bloque y su orientación; sin ella (None), la columna 1
    es la más blanca de los dos extremos.
    """
    if img_mm is None or img_mm.ndim != 3:
        return Grilla(ok=False, motivo="La imagen debe ser BGR de 3 canales.")
    if paleta is not None and len(paleta) != N_COLUMNAS:
        return Grilla(ok=False, motivo=f"La paleta debe tener {N_COLUMNAS} colores, uno por columna.")
    escala = 1.0 / mm_por_px  # px por mm
    img, off = img_mm, np.zeros(2)
    if region_mm is not None:
        x, y, w, h = (int(round(v * escala)) for v in region_mm)
        x, y = max(0, x), max(0, y)
        img, off = img_mm[y : y + h, x : x + w], np.array([x, y], float)

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = _mascara_colores(hsv)
    # Une las celdas en un solo bloque. El plástico entre celdas mide ~3 mm, pero una columna pálida (la verde menta)
    # puede no saturar y dejar un hueco de una celda (~22 mm): la dilatación lo salva. La geometría se mide después
    # solo con los píxeles de color reales.
    k = max(3, int(round(26 * escala)) | 1)
    n, lab, st, _ = cv2.connectedComponentsWithStats(cv2.dilate(m, np.ones((k, k), np.uint8)))

    mejor = None
    for i in range(1, n):
        if not 3000 <= st[i, cv2.CC_STAT_AREA] / escala**2 <= 40000:
            continue
        sel = (lab == i) & (m > 0)
        if _variedad_tonos(hsv[..., 0][sel]) < 3:
            continue
        pts = np.column_stack(np.where(sel))[:, ::-1].astype(np.float32)
        (cx, cy), (rw, rh), ang = cv2.minAreaRect(pts)
        largo, corto = max(rw, rh), min(rw, rh)
        if corto * mm_por_px < 40 or not 1.1 <= largo / corto <= 2.6:
            continue
        t = np.deg2rad(ang)
        ex, ey = np.array([np.cos(t), np.sin(t)]), np.array([-np.sin(t), np.cos(t)])
        u = ex if rw >= rh else ey
        c = np.array([cx, cy])
        for sentido in (u, -u):
            aciertos, origen, paso, v, medidos = _ajustar(img, c, sentido, largo, corto, paleta)
            puntaje = (aciertos, sel.sum())
            if mejor is None or puntaje > mejor[0]:
                mejor = (puntaje, origen, paso, sentido, v, medidos)

    if mejor is None:
        return Grilla(ok=False, motivo="No encontré un bloque de celdas de colores (¿tapado por una mano o fuera de cuadro?).")
    (aciertos, _), origen, paso, u, v, medidos = mejor
    confianza = aciertos / N_COLUMNAS if paleta else 1.0
    if paleta and aciertos < 4:
        return Grilla(ok=False, confianza=round(confianza, 2), motivo=f"Encontré un bloque de colores, pero solo {aciertos} de 7 columnas calzan con la paleta.")

    def to_mm(p):
        return [round(float((p[0] + off[0]) * mm_por_px), 1), round(float((p[1] + off[1]) * mm_por_px), 1)]

    celdas: list[Celda] = []
    for f in range(N_FILAS):
        for col in range(N_COLUMNAS):
            centro = origen + u * (col + 0.5) * paso + v * (f + 0.5) * paso
            esquinas = [origen + u * (col + a) * paso + v * (f + b) * paso for a, b in ((0, 0), (1, 0), (1, 1), (0, 1))]
            bgr = _muestra(img, centro, paso * 0.22)
            if paleta:
                nombre = paleta[col]
            else:
                nombre = nombre_color(medidos[col]) if medidos[col] is not None else "fuera"
            celdas.append(
                Celda(
                    dir=f"{FILAS[f]}{col + 1}",
                    fila=FILAS[f],
                    col=col + 1,
                    centro_mm=to_mm(centro),
                    poligono_mm=[to_mm(p) for p in esquinas],
                    color=nombre,
                    hex=_hex(bgr) if bgr is not None else "#000000",
                )
            )

    W, H = u * N_COLUMNAS * paso, v * N_FILAS * paso
    return Grilla(
        ok=True,
        confianza=round(confianza, 2),
        centro_mm=to_mm(origen + W / 2 + H / 2),
        angulo_deg=round(float(np.degrees(np.arctan2(u[1], u[0]))), 1),
        paso_mm=round(float(paso * mm_por_px), 1),
        eje_columnas=[round(float(x), 4) for x in u],
        eje_filas=[round(float(x), 4) for x in v],
        esquinas_mm=[to_mm(p) for p in (origen, origen + W, origen + W + H, origen + H)],
        celdas=celdas,
    )


def celda_en(grilla: Grilla, punto_mm) -> str | None:
    """Dirección de la celda bajo un punto en mm (la punta del dedo, por ejemplo). None si cae fuera."""
    if not grilla.ok:
        return None
    o = np.array(grilla.esquinas_mm[0])
    u, v = np.array(grilla.eje_columnas), np.array(grilla.eje_filas)
    d = np.asarray(punto_mm, float) - o
    col, fil = int(np.floor(d @ u / grilla.paso_mm)), int(np.floor(d @ v / grilla.paso_mm))
    if 0 <= col < N_COLUMNAS and 0 <= fil < N_FILAS:
        return f"{FILAS[fil]}{col + 1}"
    return None


def rectificar(
    img_cam: np.ndarray, matriz_px_a_mm, ancho_mm: int = 1000, alto_mm: int = 563, lente: dict | None = None, image_px=None
) -> np.ndarray:
    """Foto de la cámara cenital → vista de la mesa a 1 px = 1 mm, con lo que devuelve `look` calibrado.

    - `matriz_px_a_mm`: `matrix_px_to_mm` (9 números).
    - `lente`: el `lens` de `look` ({f, cx, cy, k1, k2}), si la calibración tiene modelo de lente. Entonces la matriz
      lleva píxeles **corregidos** a mm, igual que `pxToMm` de la consola (`calib.js`).
    - `image_px`: tamaño de la foto con que se calibró; si la foto llega de otro tamaño, se escala.
    """
    H = np.asarray(matriz_px_a_mm, float).reshape(3, 3)
    h_img, w_img = img_cam.shape[:2]
    sx, sy = (1.0, 1.0) if not image_px else (w_img / image_px[0], h_img / image_px[1])
    if not lente and (sx, sy) == (1.0, 1.0):
        return cv2.warpPerspective(img_cam, H, (ancho_mm, alto_mm))
    # Para cada mm de la mesa: píxel corregido (homografía inversa) → píxel de la foto (distorsión) → remap.
    X, Y = np.meshgrid(np.arange(ancho_mm, dtype=np.float64), np.arange(alto_mm, dtype=np.float64))
    q = np.linalg.inv(H) @ np.stack([X.ravel(), Y.ravel(), np.ones(X.size)])
    u, v = q[0] / q[2], q[1] / q[2]
    if lente:
        f, cx, cy = lente["f"], lente["cx"], lente["cy"]
        x, y = (u - cx) / f, (v - cy) / f
        r2 = x * x + y * y
        k = 1 + lente["k1"] * r2 + lente["k2"] * r2 * r2
        u, v = x * k * f + cx, y * k * f + cy
    mx = (u * sx).reshape(alto_mm, ancho_mm).astype(np.float32)
    my = (v * sy).reshape(alto_mm, ancho_mm).astype(np.float32)
    return cv2.remap(img_cam, mx, my, cv2.INTER_LINEAR)

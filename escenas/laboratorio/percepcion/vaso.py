"""Vaso precipitado visto de cerca por la cámara secundaria: nivel del líquido → ml → «te pasaste».

Tres piezas, todas puras salvo el estado explícito de `Medidor` y `Umbral`:

1. `nivel_px(cuadro, cal)`: fila (px, en el cuadro) de la superficie del líquido dentro de la zona del vaso.
   - Con foto del vaso vacío (`cal.vacio`): compara cada fila con la misma fila vacía. Sirve para agua transparente
     (el agua deforma el fondo) y para líquidos de color.
   - Sin foto del vaso vacío: busca el líquido por saturación. Sirve solo para líquidos de color.
2. `Calibracion.ml(px)`: px → ml con dos o tres niveles conocidos (o dos marcas impresas del vaso), interpolando por
   tramos y extrapolando con el tramo más cercano.
3. `Umbral`: zonas «falta», «listo» y «te_pasaste» con histéresis y unos cuadros de confirmación, para que el aviso no
   parpadee cuando el nivel tiembla justo en el borde.

La cámara secundaria debe mirar el vaso de lado (el nivel es una línea horizontal en la imagen). Ver
`escenas/laboratorio/README.md`, sección «Calibrar el vaso».
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np


# --------------------------------------------------------------------------------------------- calibración


@dataclass
class Calibracion:
    """Todo lo que hace falta para pasar de un cuadro de la cámara secundaria a ml.

    - `roi_px`: [x, y, ancho, alto] del interior del vaso en el cuadro (sin el borde de vidrio). El fondo del vaso es
      el borde inferior de esta zona.
    - `puntos`: pares [fila_px, ml] medidos (al menos dos). Con un vaso vacío, «fondo del roi = 0 ml» ya es uno.
    - `vacio`: foto del vaso vacío (mismo encuadre), opcional pero recomendada.
    """

    roi_px: list[int]
    puntos: list[list[float]] = field(default_factory=list)
    vacio: np.ndarray | None = None

    def __post_init__(self):
        if len(self.roi_px) != 4 or self.roi_px[2] < 8 or self.roi_px[3] < 16:
            raise ValueError("roi_px debe ser [x, y, ancho, alto] con al menos 8×16 px.")

    @property
    def fondo_px(self) -> int:
        return int(self.roi_px[1] + self.roi_px[3] - 1)

    def ml(self, fila_px: float | None) -> float | None:
        """px → ml. Interpola por tramos entre los puntos y extrapola con el tramo más cercano."""
        if fila_px is None:
            return None
        if len(self.puntos) < 2:
            raise ValueError("La calibración necesita al menos dos niveles conocidos (por ejemplo vacío y una marca).")
        p = np.array(sorted(self.puntos, key=lambda q: -q[0]), float)  # de abajo (fila grande) hacia arriba
        y, ml = p[:, 0], p[:, 1]
        if np.any(np.diff(y) >= 0):
            raise ValueError("Dos niveles calibrados están en la misma fila: usa niveles separados.")
        if fila_px >= y[0]:
            i = 0
        elif fila_px <= y[-1]:
            i = len(y) - 2
        else:
            i = int(np.searchsorted(-y, -fila_px) - 1)
        t = (fila_px - y[i]) / (y[i + 1] - y[i])
        return float(max(0.0, ml[i] + t * (ml[i + 1] - ml[i])))

    def px(self, ml_objetivo: float) -> float:
        """ml → fila px (para dibujar la línea del objetivo en la vista de la cámara)."""
        p = np.array(sorted(self.puntos, key=lambda q: q[1]), float)
        ml, y = p[:, 1], p[:, 0]
        if ml_objetivo <= ml[0]:
            i = 0
        elif ml_objetivo >= ml[-1]:
            i = len(ml) - 2
        else:
            i = int(np.searchsorted(ml, ml_objetivo) - 1)
        t = (ml_objetivo - ml[i]) / (ml[i + 1] - ml[i])
        return float(y[i] + t * (y[i + 1] - y[i]))

    def a_dict(self) -> dict:
        return {"roi_px": [int(v) for v in self.roi_px], "puntos": [[float(a), float(b)] for a, b in self.puntos]}

    @classmethod
    def desde_dict(cls, d: dict, vacio: np.ndarray | None = None) -> "Calibracion":
        return cls(roi_px=list(d["roi_px"]), puntos=[list(p) for p in d.get("puntos", [])], vacio=vacio)


def calibrar_por_marcas(roi_px, marcas: dict[float, float], vacio=None, fondo_es_cero: bool = True) -> Calibracion:
    """Calibración con las marcas impresas del vaso: `{ml: fila_px}`, por ejemplo `{50: 312, 100: 241}`.

    Con `fondo_es_cero`, el fondo del vaso (borde inferior de la zona) cuenta como 0 ml.
    """
    puntos = [[float(px), float(ml)] for ml, px in marcas.items()]
    cal = Calibracion(roi_px=list(roi_px), puntos=puntos, vacio=vacio)
    if fondo_es_cero and not any(ml == 0 for _, ml in puntos):
        cal.puntos.append([float(cal.fondo_px), 0.0])
    return cal


def roi_desde_diferencia(vacio: np.ndarray, con_liquido: np.ndarray, alto_extra: float = 3.0, umbral: float = 14.0) -> list[int] | None:
    """Zona del vaso sin marcar nada a mano: donde cambió la foto al echar líquido.

    El fondo del vaso es el borde inferior de lo que cambió; las columnas, su ancho (sin los bordes); hacia arriba se
    deja `alto_extra` veces la altura del líquido, para que quepa lo que se va a echar. None si no cambió nada.
    """
    a = cv2.cvtColor(cv2.GaussianBlur(vacio, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.float32)
    b = cv2.cvtColor(cv2.GaussianBlur(con_liquido, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.float32)
    d = (np.linalg.norm(a - b, axis=2) > umbral).astype(np.uint8)
    d = cv2.morphologyEx(d, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(d)
    if n < 2:
        return None
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = (int(v) for v in st[i, :4])
    if w < 8 or h < 4:
        return None
    borde = int(w * 0.04)
    tope = max(0, int(y - alto_extra * h))
    return [x + borde, tope, w - 2 * borde, y + h - tope]


def calibrar_con_lecturas(vacio: np.ndarray, niveles: dict[float, np.ndarray], roi_px=None) -> Calibracion:
    """Calibración recomendada: foto del vaso vacío + fotos con dos o más niveles conocidos (`{50: foto, 100: foto}`).

    La zona del vaso sale sola (de la diferencia con la foto del nivel más alto) y cada punto es la lectura del propio
    detector, así que su sesgo (por ejemplo, el grosor del menisco) se cancela al convertir a ml.
    """
    if not niveles:
        raise ValueError("Hace falta al menos una foto con un nivel conocido además del vaso vacío.")
    mayor = max(niveles)
    roi = list(roi_px) if roi_px else roi_desde_diferencia(vacio, niveles[mayor], alto_extra=1.5)
    if roi is None:
        raise ValueError("No vi diferencia entre el vaso vacío y el lleno: ¿el líquido se ve en la cámara?")
    cal = Calibracion(roi_px=roi, puntos=[], vacio=vacio)
    cal.puntos.append([float(cal.fondo_px), 0.0])
    for ml, foto in sorted(niveles.items()):
        lec = nivel_px(foto, cal)
        if lec.vacio or lec.fila_px is None:
            raise ValueError(f"No encontré el nivel en la foto de {ml} ml.")
        cal.puntos.append([float(lec.fila_px), float(ml)])
    return cal


# --------------------------------------------------------------------------------------------- nivel


@dataclass
class Lectura:
    fila_px: float | None  # superficie del líquido en el cuadro (None: no se pudo leer)
    calidad: float  # 0..1: qué tan clara es la diferencia líquido / aire
    vacio: bool = False  # True si el vaso parece vacío
    perfil: np.ndarray | None = None  # señal por fila (para depurar)


def _perfil(cuadro: np.ndarray, cal: Calibracion) -> np.ndarray:
    """Una señal por fila del interior del vaso: alta donde hay líquido, baja donde hay aire."""
    x, y, w, h = cal.roi_px
    # Solo el centro del vaso: las paredes de vidrio reflejan y engañan.
    x0, x1 = x + int(w * 0.2), x + int(w * 0.8)
    zona = cv2.GaussianBlur(cuadro[y : y + h, x0:x1], (5, 5), 0)
    lab = cv2.cvtColor(zona, cv2.COLOR_BGR2LAB).astype(np.float32)
    if cal.vacio is not None:
        ref = cv2.GaussianBlur(cal.vacio[y : y + h, x0:x1], (5, 5), 0)
        ref = cv2.cvtColor(ref, cv2.COLOR_BGR2LAB).astype(np.float32)
        d = np.linalg.norm(lab - ref, axis=2)
    else:
        a, b = lab[..., 1] - 128, lab[..., 2] - 128
        d = np.hypot(a, b)  # croma: el líquido de color resalta sobre el vidrio y el fondo grises
    fila = np.median(d, axis=1)
    return cv2.GaussianBlur(fila.reshape(-1, 1), (1, 7), 0).ravel()


def nivel_px(cuadro: np.ndarray, cal: Calibracion, min_contraste: float = 6.0) -> Lectura:
    """Fila de la superficie del líquido. Busca desde el fondo hacia arriba dónde termina el líquido."""
    if cuadro is None or cuadro.ndim != 3:
        return Lectura(None, 0.0)
    perfil = _perfil(cuadro, cal)
    h = len(perfil)
    abajo = np.median(perfil[h - max(4, int(h * 0.03)) :])  # la señal en el fondo del vaso (las últimas filas)
    arriba = np.percentile(perfil, 10)  # la señal del aire (la parte más «vacía»)
    contraste = abajo - arriba
    y0 = cal.roi_px[1]
    if contraste < min_contraste:
        # No hay líquido en el fondo: vaso vacío (o el líquido no se distingue).
        return Lectura(float(cal.fondo_px), 0.0, vacio=True, perfil=perfil)
    umbral = arriba + contraste / 2
    liquido = perfil > umbral
    # Desde el fondo hacia arriba: la superficie es el primer tramo de 4 filas seguidas sin líquido.
    i = h - 1
    seguidas = 0
    superficie = 0
    while i >= 0:
        if liquido[i]:
            seguidas = 0
        else:
            seguidas += 1
            if seguidas >= 4:
                superficie = i + seguidas  # primera fila con líquido sobre el tramo de aire
                break
        i -= 1
    # Afinar: dónde cruza el umbral, con interpolación lineal entre la fila de aire y la de líquido.
    s = int(np.clip(superficie, 1, h - 1))
    a, b = perfil[s - 1], perfil[s]
    sub = s - 1 + (umbral - a) / (b - a) if b != a else float(s)
    calidad = float(np.clip(contraste / 40.0, 0, 1))
    return Lectura(float(y0 + sub), calidad, perfil=perfil)


# --------------------------------------------------------------------------------------------- en el tiempo


class Medidor:
    """Suaviza las lecturas: mediana de las últimas `n` y aviso de estabilidad."""

    def __init__(self, cal: Calibracion, n: int = 5, estable_ml: float = 1.5):
        self.cal = cal
        self.ventana: deque[float] = deque(maxlen=n)
        self.estable_ml = estable_ml

    def leer(self, cuadro: np.ndarray) -> dict:
        lec = nivel_px(cuadro, self.cal)
        ml = self.cal.ml(lec.fila_px) if lec.fila_px is not None else None
        if ml is not None:
            self.ventana.append(ml)
        if not self.ventana:
            return {"ml": None, "ml_crudo": None, "fila_px": None, "calidad": 0.0, "estable": False, "vacio": lec.vacio}
        suave = float(np.median(self.ventana))
        estable = len(self.ventana) == self.ventana.maxlen and (max(self.ventana) - min(self.ventana)) <= self.estable_ml
        return {
            "ml": round(suave, 1),
            "ml_crudo": None if ml is None else round(ml, 1),
            "fila_px": None if lec.fila_px is None else round(lec.fila_px, 1),
            "calidad": round(lec.calidad, 2),
            "estable": estable,
            "vacio": lec.vacio,
        }


FALTA, LISTO, PASADO = "falta", "listo", "te_pasaste"
_ZONAS = (FALTA, LISTO, PASADO)


class Umbral:
    """Zona del nivel respecto del objetivo, con histéresis.

    - «listo» es objetivo ± tolerancia.
    - Para cambiar de zona hay que cruzar el borde por más de `histeresis_ml` y quedarse ahí `cuadros` lecturas seguidas.
    - `cerca` (sin histéresis, solo para la interfaz): falta menos de `cerca_ml` para el objetivo.
    """

    def __init__(self, objetivo_ml: float, tolerancia_ml: float = 2.0, histeresis_ml: float = 1.0, cuadros: int = 3, cerca_ml: float | None = None):
        if tolerancia_ml < 0 or histeresis_ml < 0:
            raise ValueError("Tolerancia e histéresis no pueden ser negativas.")
        self.objetivo = float(objetivo_ml)
        self.tol = float(tolerancia_ml)
        self.h = float(histeresis_ml)
        self.cuadros = max(1, int(cuadros))
        self.cerca_ml = float(cerca_ml) if cerca_ml is not None else max(5.0, 0.2 * self.objetivo)
        self.zona = FALTA
        self._candidata: str | None = None
        self._cuenta = 0
        self.cambios: list[tuple[str, str]] = []

    def _zona_cruda(self, ml: float) -> str:
        i = _ZONAS.index(self.zona)
        bordes = (self.objetivo - self.tol, self.objetivo + self.tol)  # falta|listo, listo|pasado
        # Subir: cruzar el borde de arriba por más de h. Bajar: cruzar el de abajo por más de h.
        while i < 2 and ml > bordes[i] + self.h:
            i += 1
        while i > 0 and ml < bordes[i - 1] - self.h:
            i -= 1
        return _ZONAS[i]

    def actualizar(self, ml: float | None) -> dict:
        if ml is not None:
            cruda = self._zona_cruda(ml)
            if cruda == self.zona:
                self._candidata, self._cuenta = None, 0
            elif cruda == self._candidata:
                self._cuenta += 1
            else:
                self._candidata, self._cuenta = cruda, 1
            if self._candidata and self._cuenta >= self.cuadros:
                self.cambios.append((self.zona, self._candidata))
                self.zona, self._candidata, self._cuenta = self._candidata, None, 0
        falta = None if ml is None else round(self.objetivo - ml, 1)
        return {
            "zona": self.zona,
            "objetivo_ml": self.objetivo,
            "falta_ml": falta,
            "cerca": bool(self.zona == FALTA and falta is not None and falta <= self.cerca_ml),
            "exceso_ml": None if ml is None or self.zona != PASADO else round(ml - self.objetivo, 1),
        }

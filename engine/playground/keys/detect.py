"""Detección exacta, tecla por tecla, del teclado visto desde el proyector.

Entrada: la foto con luz blanca en el espacio del proyector (``scene_pov.png``) y el rectángulo rotado del teclado
(``cv2.minAreaRect``; p. ej. ``workbench.json → teclado.rect``). Opcional: la altura del escaneo
(``height_proj.npy``) para marcar como tapado lo que sobresale del plano de las teclas.

Pasos
1. Rectificar: homografía del rectángulo (con margen) a una banda recta. Se corrige la orientación (las negras van
   atrás = arriba en la banda, notas graves a la izquierda) y se rehace la homografía con los bordes trasero y
   frontal medidos columna por columna (rectas robustas). Después, la perspectiva: los bordes entre teclas son un
   haz de rectas que converge a un punto de fuga (en scan2 van de casi verticales en el canto izquierdo a ~0,5 en
   el derecho). El haz se mide (cantos del teclado, bordes E|F y B|C a todo lo largo, bordes de las negras) y una
   segunda homografía lo vuelve vertical; se repite hasta que la inclinación residual sea < 0,01.
2. Paso y fase: la señal de las negras (oscuro en la mitad trasera) se pliega por octava (7 pasos de blanca) y se
   correlaciona con la plantilla del teclado (grupos de 2 y 3 negras). El mejor paso y la mejor fase dicen dónde
   está cada Do: eso valida el patrón musical y da el nombre de cada tecla aunque solo se vea una parte.
3. Blancas: perfil de columnas en la franja frontal (solo blanco); cada borde entre teclas es un valle oscuro
   estrecho. Se busca cerca de la posición que predice el paso, se ajusta con subpíxel (gaussiana de 3 puntos) y
   todos los bordes se ajustan a un modelo proyectivo 1D (perspectiva a lo largo del teclado).
4. Negras: bordes izquierdo y derecho medidos fila por fila (cruce a media altura entre su núcleo y el blanco de
   al lado), con la inclinación del haz; el fin frontal se mide siguiendo la tecla. Su posición y ancho son los
   medidos, no inferidos.
5. Ocultas: una blanca sin blanco visible en su zona frontal (o que sobresale según la altura) queda "oculta" y su
   polígono sale del modelo (sus bordes no se miden: el borde de la mancha engaña); igual una negra que no se pudo
   medir. La octava absoluta la fija el canto real del teclado (plástico oscuro más allá, dentro de la imagen).
6. Salida en píxeles del proyector (se deshace la homografía): polígono de 4 puntos, contorno real de la blanca
   (con las muescas de las negras), centro, bbox, cuadrante (tercio frontal = dónde tocar), nota MIDI, nombre,
   estado (visible / oculta / fuera de cuadro) y confianza.

Convención de la banda: ``u`` a lo largo del teclado (graves → agudos), ``v`` de atrás (0) hacia el frente.
Nombres: MIDI 60 = C4, así el teclado de 61 teclas de Max va de C2 (36) a C7 (96).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import SimpleNamespace

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d, grey_closing, map_coordinates

NOMBRES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
PC_BLANCAS = (0, 2, 4, 5, 7, 9, 11)              # clase de altura de las blancas en orden: C D E F G A B
# Distribución estándar "12 iguales atrás": al fondo la octava (7 blancas) se reparte en 12 anchos iguales; la negra
# de clase pc ocupa [pc, pc + 1]·7/12 medido en anchos de blanca desde el borde izquierdo del Do.
ANCHO_NEGRA = 7 / 12
LARGO_NEGRA = 0.62                                # fracción del largo de la blanca (si no se puede medir)
# borde (contado desde el borde izquierdo del Do, en blancas) sobre el que va cada negra: C|D, D|E, F|G, G|A, A|B
NEGRA_DEL_BORDE = {1: 1, 2: 3, 4: 6, 5: 8, 6: 10}


def nombre_nota(midi: int) -> str:
    return f"{NOMBRES[midi % 12]}{midi // 12 - 1}"


def es_negra(midi: int) -> bool:
    return midi % 12 not in PC_BLANCAS


def desfase_negra(pc: int) -> float:
    """Centro de la negra respecto de su borde, en anchos de blanca (C# −0,125 · D# +0,042 · F# −0,208 …)."""
    borde = {v: k for k, v in NEGRA_DEL_BORDE.items()}[pc]
    return (pc + 0.5) * 7 / 12 - borde


# ---------------------------------------------------------------- utilidades geométricas y de ajuste

def _recta(x, y, k=2.5, piso=0.25, iters=8):
    """Recta robusta y = a + b·x (mínimos cuadrados con rechazo iterativo de atípicos).

    Devuelve ((a, b), inliers, sigma) o None si hay menos de 2 puntos."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 2:
        return None
    w = ok.copy(); coef = np.array([np.median(y[ok]), 0.0]); s = piso
    for _ in range(iters):
        if np.ptp(x[w]) < 1e-9:                    # todos en la misma x: solo nivel
            coef = np.array([np.median(y[w]), 0.0])
        else:
            coef = np.linalg.lstsq(np.column_stack([np.ones(w.sum()), x[w]]), y[w], rcond=None)[0]
        r = y - (coef[0] + coef[1] * x)
        s = max(1.4826 * float(np.median(np.abs(r[w]))), piso)
        nw = ok & (np.abs(r) <= k * s)
        if nw.sum() < 2 or (nw == w).all():
            break
        w = nw
    return coef, w, s


@dataclass
class Proyectiva1D:
    """u(k) = (a·k' + b) / (c·k' + 1) con k' = k − k0: posiciones de bordes equiespaciados vistos en perspectiva."""
    a: float
    b: float
    c: float
    k0: float
    sigma: float

    def __call__(self, k):
        kk = np.asarray(k, float) - self.k0
        return (self.a * kk + self.b) / (self.c * kk + 1.0)

    def paso(self, k):
        """Derivada du/dk (ancho local de una blanca, en px de banda)."""
        kk = np.asarray(k, float) - self.k0
        return (self.a - self.b * self.c) / (self.c * kk + 1.0) ** 2


def _ajustar_proyectiva(k, u, k_rech=2.5, piso=0.12, iters=8):
    """Ajuste robusto de u(k) = (a·k' + b)/(c·k' + 1), lineal en (a, b, c): a·k' + b − c·k'·u = u.

    Con menos de 5 puntos, o si el polo cae dentro del rango, se usa una recta (c = 0)."""
    k = np.asarray(k, float); u = np.asarray(u, float)
    k0 = float(np.median(k)); kk = k - k0

    def resolver(w):
        if w.sum() >= 5:
            A = np.column_stack([kk[w], np.ones(w.sum()), -kk[w] * u[w]])
            coef = np.linalg.lstsq(A, u[w], rcond=None)[0]
            if abs(coef[2]) * (np.abs(kk).max() + 1) <= 0.25:
                return coef
        ab = np.linalg.lstsq(np.column_stack([kk[w], np.ones(w.sum())]), u[w], rcond=None)[0]
        return np.array([ab[0], ab[1], 0.0])

    w = np.ones(len(k), bool); s = piso
    coef = resolver(w)
    for _ in range(iters):
        r = u - (coef[0] * kk + coef[1]) / (coef[2] * kk + 1.0)
        s = max(1.4826 * float(np.median(np.abs(r[w]))), piso)
        nw = np.abs(r) <= k_rech * s
        if nw.sum() < 3 or (nw == w).all():
            break
        w = nw
        coef = resolver(w)
    return Proyectiva1D(float(coef[0]), float(coef[1]), float(coef[2]), k0, s), w


def _cruce(a, b, umbral):
    """Fracción entre dos muestras donde la señal cruza el umbral (0 = en a, 1 = en b)."""
    d = b - a
    return 0.5 if abs(d) < 1e-9 else float(np.clip((umbral - a) / d, 0.0, 1.0))


def ordenar_rect(rect) -> np.ndarray:
    """Esquinas del teclado (atrás-izq, atrás-der, frente-der, frente-izq) a partir de un minAreaRect o 4 puntos.

    El lado largo queda horizontal y apuntando a +x de la imagen; la orientación final (dónde están las negras) la
    decide ``detectar_teclas``."""
    try:
        P = np.asarray(rect, float)
    except (TypeError, ValueError):                     # ((cx, cy), (w, h), ángulo): no es una matriz
        P = None
    if P is not None and P.shape == (4, 2):
        c = P.mean(axis=0)
        lados = [P[(i + 1) % 4] - P[i] for i in range(4)]
        i_largo = int(np.argmax([np.linalg.norm(l) for l in lados]))
        eu = lados[i_largo] / np.linalg.norm(lados[i_largo])
        largo = (np.linalg.norm(lados[i_largo]) + np.linalg.norm(lados[(i_largo + 2) % 4])) / 2
        corto = (np.linalg.norm(lados[(i_largo + 1) % 4]) + np.linalg.norm(lados[(i_largo + 3) % 4])) / 2
    else:
        (cx, cy), (w, h), ang = rect
        c = np.array([cx, cy], float)
        t = math.radians(float(ang))
        ew = np.array([math.cos(t), math.sin(t)]); eh = np.array([-math.sin(t), math.cos(t)])
        if w >= h:
            eu, largo, corto = ew, float(w), float(h)
        else:
            eu, largo, corto = eh, float(h), float(w)
    if eu[0] < 0 or (abs(eu[0]) < 1e-9 and eu[1] < 0):
        eu = -eu
    ev = np.array([-eu[1], eu[0]])                      # perpendicular hacia +y de la imagen
    return np.array([c - largo / 2 * eu - corto / 2 * ev, c + largo / 2 * eu - corto / 2 * ev,
                     c + largo / 2 * eu + corto / 2 * ev, c - largo / 2 * eu + corto / 2 * ev])


def estimar_rect(img: np.ndarray):
    """Rectángulo rotado del teclado sin ayuda: banda de bordes verticales densos + región clara de las teclas."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sob = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3))
    score = cv2.blur((sob > 60).astype(np.float32), (61, 21))
    banda = (score > np.percentile(score, 97)).astype(np.uint8) * 255
    banda = cv2.morphologyEx(banda, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(banda)
    if n < 2:
        raise ValueError("no encontré una banda con teclas")
    i = int(np.argmax(st[1:, cv2.CC_STAT_AREA]) + 1); x, y, w, h = [int(v) for v in st[i, :4]]
    x0, x1 = max(0, x - 60), min(g.shape[1], x + w + 60); y0, y1 = max(0, y - h // 2), min(g.shape[0], y + 2 * h)
    roi = g[y0:y1, x0:x1]
    _, claro = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    claro = cv2.morphologyEx(claro, cv2.MORPH_CLOSE, np.ones((5, 15), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(claro)
    i = int(np.argmax(st[1:, cv2.CC_STAT_AREA]) + 1)
    ys, xs = np.nonzero(lab == i)
    return cv2.minAreaRect(np.column_stack([xs + x0, ys + y0]).astype(np.float32))


# ---------------------------------------------------------------- banda rectificada

@dataclass
class Banda:
    H: np.ndarray                 # imagen → banda (3x3)
    ancho: int
    alto: int
    gris: np.ndarray              # float32
    color: np.ndarray             # float32 BGR
    valida: np.ndarray            # bool: dentro de la imagen y con dato
    altura: np.ndarray | None

    def a_imagen(self, pts) -> np.ndarray:
        P = np.asarray(pts, np.float64).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(P, np.linalg.inv(self.H)).reshape(-1, 2)


def _homografia(quad, ancho, alto, mu, mv) -> np.ndarray:
    dst = np.array([[mu, mv], [mu + ancho, mv], [mu + ancho, mv + alto], [mu, mv + alto]], np.float64)
    return cv2.getPerspectiveTransform(np.asarray(quad, np.float32), dst.astype(np.float32)).astype(np.float64)


def _rectificar(img, valida, altura, H, ancho, alto) -> Banda:
    tam = (int(ancho), int(alto))
    color = cv2.warpPerspective(img, H, tam, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    val = cv2.warpPerspective(valida.astype(np.uint8), H, tam, flags=cv2.INTER_NEAREST, borderValue=0) > 0
    alt = None
    if altura is not None:
        alt = cv2.warpPerspective(altura.astype(np.float32), H, tam, flags=cv2.INTER_LINEAR, borderValue=0)
    color = color.astype(np.float32)
    gris = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
    return Banda(H, tam[0], tam[1], gris, color, val, alt)


def _filas_claras(b: Banda, u0, u1, esperado=None):
    """Filas del área de teclas en las columnas [u0, u1): (v_atras, v_frente, umbral, nivel_blanco).

    Por fila se toma el percentil 80 entre columnas: en la mitad trasera las negras (≈42 % de las columnas) y algo
    tapado no alcanzan a oscurecerlo. Entre los tramos claros gana el que más se solapa con ``esperado``
    (v0, v1: el rectángulo de entrada), así un objeto claro sobre el panel no se confunde con las teclas."""
    cols = b.gris[:, max(0, int(u0)):min(b.ancho, int(u1))]
    R = np.percentile(cols, 80, axis=1)
    alto_b = np.percentile(R, 95); bajo = np.percentile(R, 5)
    thr = (alto_b + bajo) / 2
    claro = R > thr
    runs, ini = [], None
    for v in range(len(R) + 1):
        if v < len(R) and claro[v]:
            ini = v if ini is None else ini
        elif ini is not None:
            runs.append((ini, v - 1)); ini = None
    if not runs:
        raise ValueError("no encontré el área clara de las teclas")
    if esperado is None:
        a, z = max(runs, key=lambda r: r[1] - r[0])
    else:
        e0, e1 = esperado
        a, z = max(runs, key=lambda r: (min(r[1], e1) - max(r[0], e0), r[1] - r[0]))
    va = a - 1 + _cruce(R[a - 1], R[a], thr) if a > 0 else 0.0
    vb = z + _cruce(R[z], R[z + 1], thr) if z + 1 < len(R) else float(z)
    return va, vb, thr, alto_b


def _bordes_atras_frente(b: Banda, va, vb, thr, u0, u1):
    """Rectas v = a + b·u del borde trasero y del frontal de las teclas, medidas columna por columna."""
    L = vb - va; tol = max(4.0, 0.15 * L)
    us, vf, ub, vbk = [], [], [], []
    g = gaussian_filter1d(b.gris, 0.8, axis=0)
    for u in range(max(1, int(u0)), min(b.ancho - 1, int(u1))):
        col = g[:, u]
        # frente: claro → oscuro cerca de vb
        lo, hi = int(max(1, vb - tol)), int(min(len(col) - 2, vb + tol))
        cand = [j + _cruce(col[j], col[j + 1], thr) for j in range(lo, hi) if col[j] >= thr > col[j + 1]]
        if cand:
            us.append(u); vf.append(min(cand, key=lambda x: abs(x - vb)))
        # atrás: oscuro → claro cerca de va, solo si debajo sigue claro (columna de blanca, no de negra)
        lo, hi = int(max(1, va - tol)), int(min(len(col) - 6, va + tol))
        cand = [j + _cruce(col[j], col[j + 1], thr) for j in range(lo, hi)
                if col[j] < thr <= col[j + 1] and col[j + 2:j + 5].min() >= thr]
        if cand:
            ub.append(u); vbk.append(min(cand, key=lambda x: abs(x - va)))
    rf = _recta(us, vf) if len(us) > 10 else None
    frente = rf[0] if rf is not None else np.array([vb, 0.0])
    rb = _recta(ub, vbk) if len(ub) > 10 else None
    # sin bordes traseros claros: paralelo al frontal, pasando por va al centro
    atras = rb[0] if rb is not None else np.array([va - frente[1] * (u0 + u1) / 2, frente[1]])
    return atras, frente


def _recta_ponderada(x, y, w, k=2.5, piso=0.002, iters=8):
    """Recta robusta ponderada y = a + b·x. Devuelve (a, b) o None."""
    x = np.asarray(x, float); y = np.asarray(y, float); w = np.asarray(w, float)
    ok = np.isfinite(x) & np.isfinite(y) & (w > 0)
    if ok.sum() == 0:
        return None
    if ok.sum() == 1 or np.ptp(x[ok]) < 1e-6:
        return np.array([float(np.average(y[ok], weights=w[ok])), 0.0])
    m = ok.copy(); coef = None
    for _ in range(iters):
        sw = np.sqrt(w[m])
        coef = np.linalg.lstsq(np.column_stack([np.ones(m.sum()), x[m]]) * sw[:, None], y[m] * sw, rcond=None)[0]
        r = y - (coef[0] + coef[1] * x)
        s = max(1.4826 * float(np.median(np.abs(r[m]))), piso)
        nm = ok & (np.abs(r) <= k * s)
        if nm.sum() < 2 or (nm == m).all():
            break
        m = nm
    return coef


def _cantos(b: Banda, atras, frente, thr):
    """Cantos izquierdo y derecho de las teclas como rectas u = a + b·v, medidas fila por fila.

    Solo cuentan los tramos claros que tienen columnas "de tecla": claras en la franja frontal y oscuras justo
    delante del borde frontal (el labio del teclado). Así la mesa clara al lado del teclado no se confunde con una
    tecla. Si el cuerpo del teclado no es oscuro, se usa solo lo claro."""
    umid = b.ancho / 2
    v_a = atras[0] + atras[1] * umid; v_f = frente[0] + frente[1] * umid; L = v_f - v_a
    fr = np.median(b.gris[int(v_a + 0.72 * L):int(v_a + 0.9 * L) + 1], axis=0)
    y0, y1 = int(min(b.alto - 2, v_f + 0.04 * L + 1)), int(min(b.alto, v_f + 0.12 * L + 2))
    tecla = fr > thr
    if y1 > y0:
        # fuera de la imagen (sin dato) no cuenta como oscuro
        delante = np.median(np.where(b.valida[y0:y1], b.gris[y0:y1], 255.0), axis=0)
        con_labio = tecla & (delante < thr)
        if con_labio.sum() > 0.3 * tecla.sum():
            tecla = con_labio
    largo_min = max(8, int(0.01 * b.ancho))
    vi, ui, vd, ud = [], [], [], []
    for v in range(int(v_a + 0.1 * L), int(v_a + 0.92 * L) + 1):
        if not 0 <= v < b.alto:
            continue
        fila = gaussian_filter1d(b.gris[v], 0.8)
        claro = fila > thr
        runs, ini = [], None
        for u in range(len(fila) + 1):
            if u < len(fila) and claro[u]:
                ini = u if ini is None else ini
            elif ini is not None:
                if u - ini >= largo_min and tecla[ini:u].sum() >= 3:
                    runs.append((ini, u - 1))
                ini = None
        if not runs:
            continue
        a = runs[0][0]; z = runs[-1][1]
        vi.append(v); ui.append(a - 1 + _cruce(fila[a - 1], fila[a], thr) if a > 0 else 0.0)
        vd.append(v); ud.append(z + _cruce(fila[z], fila[z + 1], thr) if z + 1 < len(fila) else float(len(fila) - 1))
    cols = np.nonzero(tecla)[0]

    def linea(vs, us, defecto):
        r = _recta(vs, us, piso=0.3) if len(vs) >= 5 else None
        return r[0] if r is not None else np.array([defecto, 0.0])
    return (linea(vi, ui, float(cols.min()) if len(cols) else 0.0),
            linea(vd, ud, float(cols.max()) if len(cols) else float(b.ancho - 1)))


# ---------------------------------------------------------------- paso y fase por plegado de octava

def _plantilla(nb: int) -> np.ndarray:
    x = (np.arange(nb) + 0.5) / nb * 7.0                 # en anchos de blanca desde el borde izquierdo del Do
    t = np.zeros(nb)
    for pc in NEGRA_DEL_BORDE.values():
        t[np.abs(x - (pc + 0.5) * 7 / 12) < ANCHO_NEGRA / 2] = 1.0
    return t


def _plegar(senal, p, nb):
    u = np.arange(len(senal), dtype=float)
    bins = np.minimum((((u / (7 * p)) % 1.0) * nb).astype(int), nb - 1)
    suma = np.bincount(bins, senal, nb); cnt = np.bincount(bins, minlength=nb)
    f = np.where(cnt > 0, suma / np.maximum(cnt, 1), np.nan)
    return np.where(np.isnan(f), np.nanmean(f), f)


def _mejor_fase(senal, p, nb, FT, nT):
    f = _plegar(senal, p, nb); f = f - f.mean(); nf = np.linalg.norm(f)
    if nf < 1e-9:
        return -1.0, 0
    c = np.fft.irfft(np.fft.rfft(f) * FT, nb) / (nf * nT)
    s = int(np.argmax(c))
    return float(c[s]), s


def buscar_paso(oscuro: np.ndarray, pmin: float, pmax: float):
    """Paso de blanca y fase del Do que mejor explican la señal de negras: (correlación, paso, fase).

    La señal se pliega con período de una octava (7 pasos) y se correlaciona en forma circular con la plantilla de
    5 negras (grupos de 2 y 3). Primero una grilla gruesa de pasos, después una fina alrededor del mejor."""
    mejor = (-2.0, pmin, 0.0)
    for nb, rango, razon in ((168, (pmin, pmax), 1.002), (336, None, 1.0002)):
        if rango is None:
            rango = (mejor[1] / 1.006, mejor[1] * 1.006)
        T = _plantilla(nb); T = T - T.mean(); FT = np.conj(np.fft.rfft(T)); nT = np.linalg.norm(T)
        p = rango[0]
        while p <= rango[1]:
            sc, s = _mejor_fase(oscuro, p, nb, FT, nT)
            if sc > mejor[0]:
                mejor = (sc, p, s / nb * 7 * p)
            p *= razon
    return mejor


# ---------------------------------------------------------------- medición local

def _medir_minimo(D, c, ventana):
    """Máximo de la señal de valle D cerca de c (con prior suave), con subpíxel. Devuelve (pos, altura) o None."""
    i0 = max(1, int(math.floor(c - ventana))); i1 = min(len(D) - 2, int(math.ceil(c + ventana)))
    if i1 - i0 < 2:
        return None
    idx = np.arange(i0, i1 + 1)
    peso = D[i0:i1 + 1] * np.exp(-0.5 * ((idx - c) / (0.7 * ventana)) ** 2)
    i = int(idx[np.argmax(peso)])
    if D[i] <= 0 or i in (i0, i1):
        return None
    y0, y1, y2 = D[i - 1], D[i], D[i + 1]
    if min(y0, y2) > 0.02 * y1:
        # ajuste gaussiano de 3 puntos (parábola en log): sin el sesgo de "pegarse al píxel" de la parábola simple
        l0, l1, l2 = math.log(y0), math.log(y1), math.log(y2)
        den = l0 - 2 * l1 + l2
        off = 0.5 * (l0 - l2) / den if den < 0 else 0.0
    else:
        den = y0 - 2 * y1 + y2
        off = 0.5 * (y0 - y2) / den if den < 0 else 0.0
    return i + float(np.clip(off, -0.5, 0.5)), float(y1)


def _perfil(b: Banda, v0, v1):
    """Perfil de columnas: mediana de las filas [v0, v1] (robusta a manchitas del remapeo)."""
    v0 = int(max(0, math.floor(v0))); v1 = int(min(b.alto, math.ceil(v1)))
    if v1 <= v0:
        v1 = v0 + 1
    return np.median(b.gris[v0:v1], axis=0)


def _medir_negra(b: Banda, filas, centro, p, blanco, negro):
    """Bordes izquierdo y derecho de una negra fila por fila (cruce a media altura entre su núcleo y el blanco de
    al lado). ``centro(v)`` es el centro esperado en cada fila (sigue la inclinación). Lista de (v, uL, uR)."""
    out = []
    for v in filas:
        c = float(centro(v))
        i0 = max(0, int(c - 0.85 * p)); i1 = min(b.ancho - 1, int(c + 0.85 * p))
        if i1 - i0 < 6:
            continue
        seg = gaussian_filter1d(b.gris[v, i0:i1 + 1], 0.6)
        j0 = max(1, int(c - 0.4 * p) - i0); j1 = min(len(seg) - 2, int(c + 0.4 * p) - i0)
        if j1 <= j0:
            continue
        j = j0 + int(np.argmin(seg[j0:j1 + 1])); m = seg[j]
        tope = min(blanco, seg[:j].max(initial=0), seg[j:].max(initial=0))
        if tope - m < 0.35 * (blanco - negro):
            continue
        thr = (tope + m) / 2
        l = j
        while l > 0 and seg[l] < thr:
            l -= 1
        r = j
        while r < len(seg) - 1 and seg[r] < thr:
            r += 1
        if seg[l] < thr or seg[r] < thr:
            continue
        uL = i0 + l + _cruce(seg[l], seg[l + 1], thr)
        uR = i0 + r - 1 + _cruce(seg[r - 1], seg[r], thr)
        if 0.3 * p < uR - uL < 1.0 * p:
            out.append((v, uL, uR))
    return out


def _fin_negra(b: Banda, izq, der, v_ini, v_max, blanco):
    """Fin frontal de una negra: siguiendo su inclinación, donde lo oscuro vuelve a blanco. Mediana o None.

    ``izq``/``der`` son funciones u(v) de sus bordes."""
    v0 = int(max(0, v_ini)); v1 = int(min(b.alto - 2, v_max))
    if v1 <= v0 + 2:
        return None
    vs = np.arange(v0, v1 + 1, dtype=float); fines = []
    for f in np.linspace(0.25, 0.75, 5):
        us = izq(vs) + f * (der(vs) - izq(vs))
        if us.min() < 0 or us.max() > b.ancho - 1:
            continue
        col = gaussian_filter1d(map_coordinates(b.gris, [vs, us], order=1), 0.7)
        m = col.min(); thr = (blanco + m) / 2
        j = int(np.argmin(col))
        while j < len(col) - 1 and col[j] < thr:
            j += 1
        if col[j] >= thr and j > 0:
            fines.append(v0 + j - 1 + _cruce(col[j - 1], col[j], thr))
    return float(np.median(fines)) if len(fines) >= 2 else None


def _sobresale(b: Banda, v0, L, u_lo, u_hi, umbral_min) -> np.ndarray:
    """Máscara de lo que sobresale del plano de las teclas según la altura del escaneo.

    Se ajusta la tendencia de la altura a lo largo del teclado (recta robusta en u sobre el área de teclas) y se
    marca lo que queda más de max(6·MAD, umbral_min) por encima. Conservador: el escaneo es ruidoso."""
    zona = np.zeros(b.gris.shape, bool)
    zona[int(max(0, v0)):int(v0 + L), int(max(0, u_lo)):int(u_hi)] = True
    vv, uu = np.nonzero(zona & b.valida)
    if len(vv) < 50:
        return np.zeros(b.gris.shape, bool)
    r = _recta(uu, b.altura[vv, uu])
    res = b.altura[vv, uu] - (r[0][0] + r[0][1] * uu)
    med = float(np.median(res)); mad = 1.4826 * float(np.median(np.abs(res - med)))
    u_todo = np.arange(b.ancho)[None, :]
    return (b.altura - (r[0][0] + r[0][1] * u_todo) - med) > max(6 * mad, umbral_min)


# ---------------------------------------------------------------- análisis de una banda

def _analizar(b: Banda, esperado, umbral_altura: float) -> SimpleNamespace:
    """Mide el teclado en la banda ``b``: bordes, cantos, paso, bordes de blancas, negras, visibilidad y el haz de
    inclinación de los bordes (para la rectificación siguiente). Todo en coordenadas de banda.

    ``esperado`` = (v0, v1, u0, u1): dónde debería estar el área de teclas según la homografía de entrada."""
    A = SimpleNamespace(b=b)
    ev0, ev1, eu0, eu1 = esperado
    va, vb, thr, _ = _filas_claras(b, eu0 + 0.1 * (eu1 - eu0), eu1 - 0.1 * (eu1 - eu0), (ev0, ev1))
    atras, frente = _bordes_atras_frente(b, va, vb, thr, eu0, eu1)
    izq, der = _cantos(b, atras, frente, thr)
    A.thr, A.atras, A.frente, A.izq, A.der = thr, atras, frente, izq, der

    def v_atras(u):
        return atras[0] + atras[1] * np.asarray(u, float)

    def v_frente(u):
        return frente[0] + frente[1] * np.asarray(u, float)

    # extensión total de las teclas: los cantos son inclinados, se toma el extremo entre atrás y adelante
    u_ext0 = float(min(izq[0] + izq[1] * v_atras(izq[0]), izq[0] + izq[1] * v_frente(izq[0])))
    u_ext1 = float(max(der[0] + der[1] * v_atras(der[0]), der[0] + der[1] * v_frente(der[0])))
    A.u_ext = (u_ext0, u_ext1)
    umid = (u_ext0 + u_ext1) / 2
    v0 = float(v_atras(umid)); L = float(v_frente(umid) - v0)
    A.v0, A.L = v0, L

    # fin de las negras: donde cae la variación entre columnas
    cols = b.gris[:, int(max(0, u_ext0 + 2)):int(min(b.ancho, u_ext1 - 1))]
    s_fila = np.std(cols, axis=1)
    rel = (np.arange(b.alto) - v0) / L
    s_neg = np.median(s_fila[(rel > 0.15) & (rel < 0.45)]); s_bla = np.median(s_fila[(rel > 0.8) & (rel < 0.95)])
    v_negra = v0 + LARGO_NEGRA * L
    if s_neg > s_bla * 1.5:
        mitad = (s_neg + s_bla) / 2
        j = int(v0 + 0.3 * L)
        while j < int(v0 + 0.95 * L) and s_fila[j] > mitad:
            j += 1
        v_negra = j - 1 + _cruce(s_fila[j - 1], s_fila[j], mitad)
    Lb = v_negra - v0
    A.v_negra, A.Lb = v_negra, Lb

    # franja frontal (solo blanco) y fila de referencia de los bordes
    fr0 = v_negra + 0.25 * (v0 + L - v_negra); fr1 = v0 + L - 0.15 * (v0 + L - v_negra)
    vm = (fr0 + fr1) / 2
    A.fr0, A.fr1, A.vm = fr0, fr1, vm
    u_lo = float(izq[0] + izq[1] * vm); u_hi = float(der[0] + der[1] * vm)
    A.u_lo, A.u_hi = u_lo, u_hi

    # señales: oscuro de negras (núcleo de la mitad trasera) y perfil de la franja frontal
    perfil = _perfil(b, fr0, fr1)
    blanco = float(np.percentile(perfil[int(u_lo):int(u_hi)], 90))
    nucleo = b.gris[int(v0 + 0.15 * Lb):int(v0 + 0.8 * Lb) + 1]
    negro = float(np.percentile(nucleo[:, int(u_lo):int(u_hi)], 3))
    oscuro = np.clip((blanco - np.median(nucleo, axis=0)) / max(blanco - negro, 1.0), 0, 1)
    A.blanco, A.negro = blanco, negro

    # --- paso y fase por plegado de octava
    ext = u_hi - u_lo
    sc, p, fase = buscar_paso(oscuro[int(u_lo):int(u_hi)], max(4.0, ext / 120), ext / 7.5)
    fase += int(u_lo)
    A.correlacion, A.p, A.fase = sc, p, fase

    # --- bordes de blancas: valle estrecho en la franja frontal, ajuste local + modelo proyectivo 1D.
    # Índice de borde k: k = 0 en un borde izquierdo de Do; la blanca entre k y k+1 es PC_BLANCAS[k % 7].
    tam = max(3, int(round(0.45 * p)) | 1)

    def valle(perf):
        return grey_closing(perf, size=tam) - perf

    D = valle(perfil)
    k_lo = int(math.floor((u_lo - fase) / p)) - 1; k_hi = int(math.ceil((u_hi - fase) / p)) + 1
    medios = [fase + (k + 0.5) * p for k in range(k_lo, k_hi) if u_lo + p < fase + (k + 0.5) * p < u_hi - p]
    ruido = max(1.0, 1.4826 * float(np.median([D[int(round(m))] for m in medios])) + 0.5) if medios else 1.0

    def medir_bordes(Dv, modelo, ventana, ks, margen=0.0):
        obs = {}
        for k in ks:
            c = float(modelo(k))
            if not (u_lo + margen < c < u_hi - margen):
                continue
            r = _medir_minimo(Dv, c, ventana)
            if r is not None and r[1] / ruido >= 3.0:
                obs[k] = r
        return obs

    lineal = Proyectiva1D(p, fase, 0.0, 0.0, 1.0)
    obs = medir_bordes(D, lineal, 0.4 * p, range(k_lo, k_hi + 1), 0.5 * p)
    modelo = _ajustar_proyectiva(list(obs), [obs[k][0] for k in obs])[0] if len(obs) >= 3 else lineal
    # cantos: el borde del modelo más cercano a cada extremo
    k_ini = min(range(k_lo - 2, k_hi + 3), key=lambda k: abs(float(modelo(k)) - u_lo))
    k_fin = min(range(k_lo - 2, k_hi + 3), key=lambda k: abs(float(modelo(k)) - u_hi))
    internos = range(k_ini + 1, k_fin)

    def ajustar(obs, extremos=(True, True)):
        kk = list(obs) + [k for k, e in zip((k_ini, k_fin), extremos) if e]
        uu = [obs[k][0] for k in obs] + [u for u, e in zip((u_lo, u_hi), extremos) if e]
        return _ajustar_proyectiva(kk, uu)[0]

    obs = medir_bordes(D, modelo, 0.25 * p, internos)
    if len(obs) >= 3:
        modelo = ajustar(obs, (False, False))

    # --- cantos reales: más allá hay plástico oscuro dentro de la imagen (no borde de imagen ni algo encima).
    # "Oscuro" aquí es casi tan oscuro como una negra: un objeto encima suele ser más claro que el plástico.
    # Solo un canto real cuenta como borde medido (el de una mancha o del cuadro no es un borde entre teclas).
    oscuro_canto = negro + 0.35 * (blanco - negro)

    def canto_real(ub, pl, lado):
        a, z = (ub - 0.9 * pl, ub - 0.2 * pl) if lado < 0 else (ub + 0.2 * pl, ub + 0.9 * pl)
        a, z = int(round(a)), int(round(z))
        if a < 0 or z >= b.ancho:
            return False
        sl = b.gris[int(fr0):int(fr1) + 1, a:z + 1]; val = b.valida[int(fr0):int(fr1) + 1, a:z + 1]
        return bool(val.mean() > 0.9 and (sl < oscuro_canto).mean() > 0.7)
    A.izq_real = canto_real(u_lo, float(modelo.paso(k_ini)), -1)
    A.der_real = canto_real(u_hi, float(modelo.paso(k_fin)), +1)

    # --- visibilidad de las blancas: color de tecla blanca en su zona frontal (+ altura, si hay)
    franja = b.color[int(fr0):int(fr1) + 1, int(u_lo + 2):int(u_hi - 1)].reshape(-1, 3)
    ref = np.median(franja, axis=0)
    dist = np.linalg.norm(b.color - ref, axis=2)
    tau = max(35.0, 2.5 * float(np.median(np.linalg.norm(franja - ref, axis=1))))
    blanca_ok = (dist < tau) & b.valida
    if b.altura is not None:
        blanca_ok &= ~_sobresale(b, v0, L, u_lo, u_hi, umbral_altura)

    def frac_visible(a, z):
        """Fracción de la zona frontal de la blanca entre los bordes a y z que se ve como tecla blanca."""
        w = z - a
        u0_, u1_ = int(round(a + 0.18 * w)), int(round(z - 0.18 * w))
        if u1_ < 0 or u0_ > b.ancho - 1:
            return 0.0
        sl = blanca_ok[int(round(fr0)):int(round(fr1)) + 1, max(0, u0_):min(b.ancho, u1_ + 1)]
        return float(sl.mean()) if sl.size else 0.0

    # los bordes junto a una blanca tapada no se miden (el borde de la mancha engaña): salen del modelo
    tapada = {k: frac_visible(float(modelo(k)), float(modelo(k + 1))) < 0.5 for k in range(k_ini, k_fin)}
    obs = {k: r for k, r in obs.items() if not (tapada.get(k - 1, False) or tapada.get(k, False))}
    usa_izq = A.izq_real and not tapada.get(k_ini, False)
    usa_der = A.der_real and not tapada.get(k_fin - 1, False)
    modelo = ajustar(obs, (usa_izq, usa_der))
    A.k_ini, A.k_fin, A.obs, A.modelo, A.ruido = k_ini, k_fin, obs, modelo, ruido
    A.frac_visible = frac_visible

    def borde_en(k):
        """Posición del borde k en la fila de referencia vm: medida si es buena, si no la del modelo."""
        um = float(modelo(k)); tol = max(1.5, 2.5 * modelo.sigma)
        if k in obs and abs(obs[k][0] - um) <= tol:
            return obs[k][0], True
        if k == k_ini and usa_izq and abs(u_lo - um) <= tol:
            return u_lo, True
        if k == k_fin and usa_der and abs(u_hi - um) <= tol:
            return u_hi, True
        return um, False
    A.borde_en = borde_en

    # --- haz de inclinación de los bordes (perspectiva): pendiente du/dv lineal en u (rectas por un punto de fuga).
    # Muestras: cantos reales (peso alto), bordes E|F y B|C a todo lo largo de la tecla, sub-franjas frontales y,
    # después, los bordes de las negras.
    muestras = []                                       # (u en vm, pendiente, peso)
    if A.izq_real:
        muestras.append((u_lo, izq[1], 4.0))
    if A.der_real:
        muestras.append((u_hi, der[1], 4.0))
    alto_tira = max(4.0, L / 10)
    tiras = [(a, a + alto_tira) for a in np.arange(v0 + 0.06 * L, fr1 - alto_tira + 1e-6, alto_tira)]
    valles_tira = [((a + z) / 2, valle(_perfil(b, a, z))) for a, z in tiras]
    for k in obs:
        if k % 7 not in (0, 3):
            continue
        puntos = []
        for vc, Dv in valles_tira:
            r = _medir_minimo(Dv, obs[k][0], 0.15 * p)
            if r is not None and r[1] / ruido >= 3.0:
                puntos.append((vc, r[0]))
        if len(puntos) >= 4:
            rr = _recta([q[0] for q in puntos], [q[1] for q in puntos], piso=0.1)
            if rr[1].sum() >= 4:
                muestras.append((obs[k][0], rr[0][1], 1.0))
    sub = [(fr0, vm), (vm, fr1)]
    medidas_sub = [medir_bordes(valle(_perfil(b, a, z)), modelo, 0.2 * p, internos) for a, z in sub]
    dv = (sub[1][0] + sub[1][1]) / 2 - (sub[0][0] + sub[0][1]) / 2
    for k in obs:
        if k in medidas_sub[0] and k in medidas_sub[1]:
            muestras.append((obs[k][0], (medidas_sub[1][k][0] - medidas_sub[0][k][0]) / dv, 0.25))

    def ajustar_haz(muestras):
        h = _recta_ponderada([m[0] for m in muestras], [m[1] for m in muestras], [m[2] for m in muestras])
        return h if h is not None else np.array([0.0, 0.0])

    haz = ajustar_haz(muestras)

    # --- negras: dos pasadas. 1) bordes libres fila por fila → su inclinación entra al haz; 2) bordes con la
    # inclinación del haz (solo se mide su posición: robusto a filas manchadas por el remapeo).
    filas_neg = [int(v) for v in np.arange(v0 + 0.12 * Lb, v0 + 0.88 * Lb)]

    def medir_negras(haz):
        negras = {}
        for k in internos:
            if k % 7 not in NEGRA_DEL_BORDE:
                continue
            pc = NEGRA_DEL_BORDE[k % 7]; ub, _ = borde_en(k); pl = float(modelo.paso(k))
            s = haz[0] + haz[1] * ub
            c_vm = ub + desfase_negra(pc) * pl
            med = _medir_negra(b, filas_neg, lambda v, c=c_vm, s=s: c + s * (v - vm), pl, blanco, negro)
            negras[k] = {"pc": pc, "paso": pl, "s": s, "medidas": med,
                         "ok": len(med) >= max(4, 0.4 * len(filas_neg))}
        return negras

    for n in medir_negras(haz).values():
        if n["ok"]:
            vv = [m[0] for m in n["medidas"]]
            for lado in (1, 2):
                r = _recta(vv, [m[lado] for m in n["medidas"]], piso=0.2)
                if r is not None and r[1].sum() >= 4:
                    muestras.append((r[0][0] + r[0][1] * vm, r[0][1], 1.0))
    haz = ajustar_haz(muestras)
    A.haz = haz
    negras = medir_negras(haz)
    fines = []
    for k, n in negras.items():
        if not n["ok"]:
            continue
        vv = np.array([m[0] for m in n["medidas"]], float); s = n["s"]
        # posición de cada borde en la fila de referencia vm, con la inclinación del haz (mediana robusta)
        uL = np.array([m[1] for m in n["medidas"]]) - s * (vv - vm)
        uR = np.array([m[2] for m in n["medidas"]]) - s * (vv - vm)
        rl = _recta(np.zeros(len(uL)), uL, piso=0.2); rr = _recta(np.zeros(len(uR)), uR, piso=0.2)
        n["uL"], n["uR"] = float(rl[0][0]), float(rr[0][0])
        n["fila_ok"] = len(n["medidas"]) / len(filas_neg)
        n["ancho"] = n["uR"] - n["uL"]; n["centro"] = (n["uL"] + n["uR"]) / 2
        izq_n = lambda v, u=n["uL"], s=s: u + s * (np.asarray(v, float) - vm)
        der_n = lambda v, u=n["uR"], s=s: u + s * (np.asarray(v, float) - vm)
        f = _fin_negra(b, izq_n, der_n, v0 + 0.3 * Lb, v0 + 0.97 * L, blanco)
        n["fin_medido"] = f
        if f is not None:
            fines.append((float(izq_n(f) + der_n(f)) / 2, f))
    rfin = _recta([f[0] for f in fines], [f[1] for f in fines], piso=0.3) if len(fines) >= 3 else None
    A.linea_fin = rfin[0] if rfin is not None else np.array([v_negra, 0.0])
    # desfase y ancho reales por clase de negra (para inferir las que no se ven)
    desf_pc, ancho_rel = {}, []
    for k, n in negras.items():
        if n["ok"]:
            ub, _ = borde_en(k)
            desf_pc.setdefault(n["pc"], []).append((n["centro"] - ub) / n["paso"])
            ancho_rel.append(n["ancho"] / n["paso"])
    A.desf_pc = {pc: float(np.median(v)) for pc, v in desf_pc.items()}
    A.ancho_rel = float(np.median(ancho_rel)) if ancho_rel else ANCHO_NEGRA
    A.negras = negras
    return A


def _homografia_haz(haz, vm):
    """Homografía de banda que vuelve verticales las rectas del haz (pendiente du/dv = a + b·u en la fila vm)
    sin mover la fila vm: manda el punto de fuga al infinito vertical."""
    a, bb = float(haz[0]), float(haz[1])
    if abs(bb) < 1e-7:                                   # rectas paralelas: basta un corte (shear)
        return np.array([[1.0, -a, a * vm], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    Dv = 1.0 / bb; u_f = -a / bb; v_f = vm - Dv        # punto de fuga (u_f, v_f); Dv = vm − v_f
    return np.array([[1.0, u_f / Dv, -u_f - u_f * v_f / Dv],
                     [0.0, vm / Dv + 1.0, -vm * v_f / Dv - vm],
                     [0.0, 1.0 / Dv, -v_f / Dv]])


# ---------------------------------------------------------------- detección principal

def detectar_teclas(img: np.ndarray, rect=None, altura: np.ndarray | None = None, valida: np.ndarray | None = None,
                    n_teclas: int = 61, primera_nota: int = 36, umbral_altura: float = 1.0) -> dict:
    """Detecta cada tecla del teclado en ``img`` (BGR, espacio del proyector). Ver el docstring del módulo.

    ``rect``: minAreaRect (o 4 esquinas) del teclado; si falta se estima. ``altura``: mapa de altura del escaneo en
    el mismo espacio (opcional). ``valida``: máscara de píxeles con dato (por defecto, los no negros)."""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    Himg, Wimg = img.shape[:2]
    if valida is None:
        valida = img.max(axis=2) > 0
    if rect is None:
        rect = estimar_rect(img)
    quad = ordenar_rect(rect)
    largo = float(np.linalg.norm(quad[1] - quad[0])); corto = float(np.linalg.norm(quad[3] - quad[0]))
    avisos: list[str] = []

    # --- 1a. banda inicial: homografía del rectángulo con margen (crece si las teclas tocan el borde); orientación
    m_izq = m_der = max(30.0, 0.08 * largo); mv = max(12.0, 0.45 * corto)
    girado = False
    for intento in range(6):
        H0 = np.array([[1, 0, m_izq], [0, 1, mv], [0, 0, 1]], float) @ _homografia(quad, largo, corto, 0, 0)
        b0 = _rectificar(img, valida, altura, H0, largo + m_izq + m_der, corto + 2 * mv)
        va, vb, thr, _ = _filas_claras(b0, m_izq + 0.1 * largo, m_izq + 0.9 * largo, (mv, mv + corto))
        if not girado and intento == 0:
            # las negras dan mucha variación entre columnas: deben quedar en la mitad trasera (arriba)
            cols = b0.gris[:, int(m_izq + 0.1 * largo):int(m_izq + 0.9 * largo)]
            vmid = (va + vb) / 2
            s_arriba = np.std(cols[int(va + 0.1 * (vb - va)):int(vmid)], axis=1).mean()
            s_abajo = np.std(cols[int(vmid):int(vb - 0.1 * (vb - va))], axis=1).mean()
            if s_abajo > s_arriba:
                quad = quad[[2, 3, 0, 1]]; girado = True
                continue
        atras, frente = _bordes_atras_frente(b0, va, vb, thr, m_izq, m_izq + largo)
        izq, der = _cantos(b0, atras, frente, thr)
        u_lo = float(min(izq[0] + izq[1] * va, izq[0] + izq[1] * vb))
        u_hi = float(max(der[0] + der[1] * va, der[0] + der[1] * vb))
        crece = False
        if u_lo < 3 and b0.valida[:, :3].mean() > 0.5:
            m_izq += 0.25 * largo; crece = True
        if u_hi > b0.ancho - 4 and b0.valida[:, -3:].mean() > 0.5:
            m_der += 0.25 * largo; crece = True
        if va < 2 or vb > b0.alto - 3:
            mv *= 1.5; crece = True
        if not crece:
            break

    # --- 1b. banda refinada: bordes trasero y frontal medidos → rectos y horizontales
    L0 = float((frente[0] - atras[0]) + (frente[1] - atras[1]) * (u_lo + u_hi) / 2)
    src = np.array([[u_lo, atras[0] + atras[1] * u_lo], [u_hi, atras[0] + atras[1] * u_hi],
                    [u_hi, frente[0] + frente[1] * u_hi], [u_lo, frente[0] + frente[1] * u_lo]])
    mu1 = max(25.0, 0.06 * (u_hi - u_lo)); mv1 = max(10.0, 0.35 * L0)
    H1 = _homografia(b0.a_imagen(src), u_hi - u_lo, L0, mu1, mv1)
    b1 = _rectificar(img, valida, altura, H1, u_hi - u_lo + 2 * mu1, L0 + 2 * mv1)
    A1 = _analizar(b1, (mv1, mv1 + L0, mu1, mu1 + u_hi - u_lo), umbral_altura)

    # --- 1c. banda final: la homografía del haz vuelve verticales los bordes entre teclas (perspectiva) y se mide
    # todo de nuevo con columnas rectas. Se repite (máx. 3 veces) hasta que la inclinación residual sea despreciable.
    A, H, haz_inicial = A1, H1, A1.haz
    for _ in range(3):
        s_max = max(abs(A.haz[0] + A.haz[1] * A.u_ext[0]), abs(A.haz[0] + A.haz[1] * A.u_ext[1]))
        if s_max < 0.01:
            break
        if abs(A.haz[1]) > 1e-7 and abs(1.0 / A.haz[1]) < 4 * A.L:
            avisos.append("punto de fuga de los bordes demasiado cerca: no se corrige la perspectiva")
            break
        H2 = _homografia_haz(A.haz, A.vm)
        esq = np.array([[A.u_ext[0], A.v0], [A.u_ext[1], A.v0], [A.u_ext[1], A.v0 + A.L], [A.u_ext[0], A.v0 + A.L]])
        esq2 = cv2.perspectiveTransform(esq.reshape(-1, 1, 2), H2).reshape(-1, 2)
        lo2 = esq2.min(axis=0); hi2 = esq2.max(axis=0); tam2 = hi2 - lo2
        mu2 = max(25.0, 0.06 * tam2[0]); mv2 = max(10.0, 0.35 * tam2[1])
        T2 = np.array([[1, 0, mu2 - lo2[0]], [0, 1, mv2 - lo2[1]], [0, 0, 1]], float)
        H = T2 @ H2 @ H
        b = _rectificar(img, valida, altura, H, tam2[0] + 2 * mu2, tam2[1] + 2 * mv2)
        A = _analizar(b, (mv2, mv2 + tam2[1], mu2, mu2 + tam2[0]), umbral_altura)
    b = A.b

    p, modelo, borde_en, vm = A.p, A.modelo, A.borde_en, A.vm
    atras, frente, L, linea_fin = A.atras, A.frente, A.L, A.linea_fin
    k_ini, k_fin = A.k_ini, A.k_fin
    if A.correlacion < 0.4:
        avisos.append(f"patrón de negras débil (correlación {A.correlacion:.2f})")
    if abs(A.ancho_rel - ANCHO_NEGRA) > 0.2:
        avisos.append(f"ancho medio de negras {A.ancho_rel:.2f} blancas (esperado {ANCHO_NEGRA:.2f})")

    def pendiente(u):
        return A.haz[0] + A.haz[1] * u

    def linea_borde(k):
        um, _ = borde_en(k); s = pendiente(um)
        return lambda v: um + s * (np.asarray(v, float) - vm)

    # --- 2. nombres y anclaje de la octava: el patrón dice qué blanca es cada una (Do = k % 7 == 0); el canto
    # real del teclado dice cuál es la primera (o la última); si no se ve ninguno, se centra (ambiguo)
    blancas_midi = [m for m in range(primera_nota, primera_nota + n_teclas) if not es_negra(m)]
    n_bl = len(blancas_midi)
    j0 = PC_BLANCAS.index(blancas_midi[0] % 12)
    k_ult = k_fin - 1                                  # última blanca vista: entre k_fin-1 y k_fin
    vistas = k_fin - k_ini
    izq_real, der_real = A.izq_real, A.der_real
    # un canto "real" que no calza con la primera/última nota según el patrón no es el fin del teclado (algo encima)
    jl = PC_BLANCAS.index(blancas_midi[-1] % 12)
    izq_ok = izq_real and (k_ini - j0) % 7 == 0
    der_ok = der_real and (k_ult - jl) % 7 == 0
    if (izq_real and not izq_ok) or (der_real and not der_ok):
        avisos.append("un canto visible no coincide con la primera/última nota esperada por el patrón")
    solo_vistas = False
    if izq_ok:
        k0 = k_ini; anclaje = "izquierdo"
        if der_ok and vistas == n_bl:
            anclaje = "ambos"
        elif der_ok:
            # el teclado tiene otro largo que el esperado: se reporta lo que hay entre sus dos cantos
            solo_vistas = True
            avisos.append(f"veo {vistas} blancas entre los dos cantos; se esperaban {n_bl}")
    elif der_ok:
        k0 = k_ult - (n_bl - 1); anclaje = "derecho"
    else:
        centro = (k_ini + k_fin - 1) / 2 - (n_bl - 1) / 2
        k0 = min((j0 + 7 * t for t in range(-40, 40)), key=lambda c: abs(c - centro)); anclaje = "centro (octava ambigua)"

    # --- 3. polígonos de cada tecla, en la banda y en la imagen
    def interseca(linea_u, recta_v):
        """Punto donde la recta casi vertical u = linea_u(v) corta la casi horizontal v = a + b·u."""
        v = recta_v[0] + recta_v[1] * linea_u(recta_v[0])
        for _ in range(3):
            v = recta_v[0] + recta_v[1] * linea_u(v)
        return np.array([float(linea_u(v)), float(v)])

    def desplazada(recta, dv_):
        return np.array([recta[0] + dv_, recta[1]])

    def negra_lineas(k):
        """Rectas izquierda/derecha u(v), fin frontal (recta v(u)) y si fue medida, para la negra sobre el borde k."""
        n = A.negras.get(k); pc = NEGRA_DEL_BORDE[k % 7]
        if n is not None and n["ok"]:
            s = n["s"]
            li = (lambda v, u=n["uL"], s=s: u + s * (np.asarray(v, float) - vm))
            ld = (lambda v, u=n["uR"], s=s: u + s * (np.asarray(v, float) - vm))
            fin = linea_fin
            c_fin = float(linea_fin[0] + linea_fin[1] * n["centro"])
            if n["fin_medido"] is not None and abs(n["fin_medido"] - c_fin) < 2.0:
                fin = np.array([n["fin_medido"] - linea_fin[1] * n["centro"], linea_fin[1]])
            return li, ld, fin, True
        ub, _ = borde_en(k); pl = float(modelo.paso(k)); s = pendiente(ub)
        c = ub + A.desf_pc.get(pc, desfase_negra(pc)) * pl; w = A.ancho_rel * pl
        li = (lambda v, c=c, s=s, w=w: c - w / 2 + s * (np.asarray(v, float) - vm))
        ld = (lambda v, c=c, s=s, w=w: c + w / 2 + s * (np.asarray(v, float) - vm))
        return li, ld, linea_fin, False

    # teclas del teclado que se reportan: blancas (todas, o solo las vistas entre los cantos) y las negras que
    # quedan entre dos de ellas
    blancas_k = {midi: k0 + i for i, midi in enumerate(blancas_midi)
                 if not solo_vistas or k_ini <= k0 + i < k_fin}
    negras_set = {m for m in range(primera_nota, primera_nota + n_teclas)
                  if es_negra(m) and m - 1 in blancas_k and m + 1 in blancas_k}

    def hay_negra(m):
        return m in negras_set

    teclas = []
    for midi, k in blancas_k.items():
        li = linea_borde(k); ld = linea_borde(k + 1)
        P = np.array([interseca(li, atras), interseca(ld, atras), interseca(ld, frente), interseca(li, frente)])
        tercio = desplazada(frente, -L / 3)
        Q = np.array([interseca(li, tercio), interseca(ld, tercio), P[2], P[3]])
        # contorno real: con las muescas de las negras vecinas
        cont = []
        if hay_negra(midi - 1):
            _, ldn, fin_n, _ = negra_lineas(k)
            cont += [interseca(ldn, atras), interseca(ldn, fin_n), interseca(li, fin_n)]
        else:
            cont.append(P[0])
        cont += [P[3], P[2]]
        if hay_negra(midi + 1):
            lin, _, fin_n, _ = negra_lineas(k + 1)
            cont += [interseca(ld, fin_n), interseca(lin, fin_n), interseca(lin, atras)]
        else:
            cont.append(P[1])
        vis = A.frac_visible(borde_en(k)[0], borde_en(k + 1)[0])
        medidos = sum(borde_en(kb)[1] for kb in (k, k + 1))
        teclas.append({"midi": midi, "color": "blanca", "k": k, "banda": P, "cuadrante_banda": Q,
                       "contorno_banda": np.array(cont), "vis": vis, "bordes_medidos": medidos})
    for midi in sorted(negras_set):
        k = blancas_k[midi - 1] + 1
        li, ld, fin_n, medida = negra_lineas(k)
        P = np.array([interseca(li, atras), interseca(ld, atras), interseca(ld, fin_n), interseca(li, fin_n)])
        lb = float(np.mean([P[3, 1] - P[0, 1], P[2, 1] - P[1, 1]]))
        tercio = desplazada(fin_n, -lb / 3)
        Q = np.array([interseca(li, tercio), interseca(ld, tercio), P[2], P[3]])
        n = A.negras.get(k, {})
        teclas.append({"midi": midi, "color": "negra", "k": k, "banda": P, "cuadrante_banda": Q, "contorno_banda": P,
                       "medida": medida, "fila_ok": n.get("fila_ok", 0.0),
                       "ancho_rel": (n["ancho"] / n["paso"]) if medida else None})

    # --- 4. estado y confianza, en píxeles del proyector
    vis_bl = {t["k"]: t["vis"] >= 0.5 for t in teclas if t["color"] == "blanca"}
    salida = []
    for t in sorted(teclas, key=lambda t: t["midi"]):
        poly = b.a_imagen(t["banda"]); cuad = b.a_imagen(t["cuadrante_banda"]); cont = b.a_imagen(t["contorno_banda"])
        centro = poly.mean(axis=0); toque = cuad.mean(axis=0)
        dentro = (0 <= centro[0] < Wimg) and (0 <= centro[1] < Himg) and bool(valida[int(centro[1]), int(centro[0])])
        if t["color"] == "blanca":
            visible = t["vis"] >= 0.5
            conf = (min(1.0, t["vis"] / 0.8) * (0.6 + 0.2 * t["bordes_medidos"])) if visible else 0.35
        else:
            visible = bool(t["medida"]) and (vis_bl.get(t["k"] - 1, False) or vis_bl.get(t["k"], False))
            if visible:
                plaus = math.exp(-((t["ancho_rel"] - A.ancho_rel) / 0.15) ** 2) if t["ancho_rel"] else 0.5
                conf = min(1.0, t["fila_ok"] / 0.8) * (0.6 + 0.4 * plaus)
            else:
                conf = 0.3
        estado = "visible" if visible else ("oculta" if dentro else "fuera")
        if estado == "fuera":
            conf = 0.0
        if anclaje.startswith("centro"):
            conf *= 0.6
        x0_, y0_ = poly.min(axis=0); x1_, y1_ = poly.max(axis=0)
        salida.append({
            "id": nombre_nota(t["midi"]), "nota": t["midi"], "nombre": nombre_nota(t["midi"]), "color": t["color"],
            "estado": estado, "visible": estado == "visible", "confianza": round(float(conf), 3),
            "poligono": np.round(poly, 2).tolist(), "centro": np.round(centro, 2).tolist(),
            "bbox": [round(float(x0_), 2), round(float(y0_), 2), round(float(x1_ - x0_), 2), round(float(y1_ - y0_), 2)],
            "cuadrante": np.round(cuad, 2).tolist(), "punto_toque": np.round(toque, 2).tolist(),
            "contorno": np.round(cont, 2).tolist(),
        })

    blancas_out = [s for s in salida if s["color"] == "blanca"]
    tocar = [np.array(s["punto_toque"]) for s in blancas_out]
    paso_px = float(np.median([np.linalg.norm(tocar[i + 1] - tocar[i]) for i in range(len(tocar) - 1)])) if len(tocar) > 1 else 0.0
    primera_vis = min((s for s in salida if s["estado"] == "visible"), key=lambda s: s["nota"], default=None)
    en_cuadro = [s for s in salida if s["estado"] != "fuera"]
    u_lo, u_hi = A.u_ext
    quad_banda = [[u_lo, float(atras[0] + atras[1] * u_lo)], [u_hi, float(atras[0] + atras[1] * u_hi)],
                  [u_hi, float(frente[0] + frente[1] * u_hi)], [u_lo, float(frente[0] + frente[1] * u_lo)]]
    return {
        "n_teclas": len(salida), "n_blancas": len(blancas_out), "n_negras": len(salida) - len(blancas_out),
        "n_visibles": sum(s["estado"] == "visible" for s in salida),
        "n_ocultas": sum(s["estado"] == "oculta" for s in salida),
        "n_fuera": sum(s["estado"] == "fuera" for s in salida),
        "primera_nota": salida[0]["nota"] if salida else None,
        "primera_visible": {"nota": primera_vis["nota"], "nombre": primera_vis["nombre"]} if primera_vis else None,
        "paso_px": round(paso_px, 3), "paso_banda": round(float(p), 3),
        "confianza_media": round(float(np.mean([s["confianza"] for s in en_cuadro])), 3) if en_cuadro else 0.0,
        "patron": {"correlacion": round(float(A.correlacion), 3), "anclaje": anclaje, "canto_izquierdo_real": izq_real,
                   "canto_derecho_real": der_real, "blancas_entre_cantos": int(vistas)},
        "modelo": {"sigma_bordes_px": round(modelo.sigma, 3), "bordes_medidos": int(len(A.obs)),
                   "negras_medidas": int(sum(n["ok"] for n in A.negras.values())),
                   "haz_banda_inicial": [round(float(haz_inicial[0]), 5), round(float(haz_inicial[1]), 7)],
                   "haz_residual": [round(float(A.haz[0]), 5), round(float(A.haz[1]), 7)],
                   "ancho_negra_rel": round(A.ancho_rel, 3), "largo_negra_rel": round(float(A.Lb / A.L), 3)},
        "banda": {"H": np.round(b.H, 10).tolist(), "tam": [b.ancho, b.alto], "quad": np.round(b.a_imagen(quad_banda), 2).tolist()},
        "avisos": avisos,
        "teclas": salida,
    }

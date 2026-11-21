"""Ajuste por modelo: el teclado entero como un plano, alineado a la foto con una homografía (ECC).

Para cuando la detección borde a borde (``detect.py``) no puede: perspectiva fuerte (punto de fuga demasiado cerca),
teclas inclinadas 30-40° en el espacio del proyector, octava ambigua. Pasó con el Korg EK-50 en ``captures/scan5``.

Idea: las 61 teclas están en un plano y sus medidas relativas son fijas (36 blancas iguales; negras con la
distribución "12 iguales atrás", ancho 7/12 y largo ~0,62 de la blanca). Se dibuja una plantilla del teclado en
coordenadas del modelo (``u`` en anchos de blanca desde el canto izquierdo del primer Do, ``v`` de atrás 0 al frente 1)
con margen oscuro alrededor (el cuerpo del teclado), y ``cv2.findTransformECC`` busca la homografía modelo → imagen
que maximiza la correlación. Arranca desde varios paralelogramos alrededor del rectángulo del teclado (el banco de
trabajo da ``teclado.rect``) y se queda con el mejor; el margen oscuro en ambos cantos fija la octava. Después afina
ancho y largo de las negras y repite el ECC con la plantilla final.

Salida: el mismo formato que ``detectar_teclas`` (polígono, contorno con muescas, cuadrante, punto de toque, nota,
nombre, estado, confianza por tecla), con ``metodo = "modelo"`` y la homografía en ``banda.H_modelo``. Validado
contra scan2 (detección borde a borde confiable): esquinas a 1-7 px.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .detect import ANCHO_NEGRA, LARGO_NEGRA, desfase_negra, es_negra, estimar_rect, nombre_nota, ordenar_rect

# extensión de la plantilla en unidades del modelo (margen oscuro = cuerpo del teclado)
MARGEN_U = 1.5
V_MIN, V_MAX = -0.25, 1.12


class Modelo:
    """Geometría del teclado en coordenadas del modelo (u: anchos de blanca, v: 0 atrás → 1 frente)."""

    def __init__(self, n_teclas: int = 61, primera_nota: int = 36, ancho_negra: float = ANCHO_NEGRA,
                 largo_negra: float = LARGO_NEGRA):
        self.notas = list(range(primera_nota, primera_nota + n_teclas))
        self.blancas = [m for m in self.notas if not es_negra(m)]
        self.k = {m: i for i, m in enumerate(self.blancas)}          # índice de blanca
        self.nb = len(self.blancas)
        self.an = ancho_negra
        self.ln = largo_negra

    def negra(self, m: int) -> tuple[float, float]:
        """(u izquierda, u derecha) de la negra m."""
        borde = self.k[m - 1] + 1
        c = borde + desfase_negra(m % 12)
        return c - self.an / 2, c + self.an / 2

    def negras(self):
        return [m for m in self.notas if es_negra(m) and (m - 1) in self.k and (m + 1) in self.k]

    def poligono(self, m: int) -> np.ndarray:
        if es_negra(m):
            l, r = self.negra(m)
            return np.array([[l, 0], [r, 0], [r, self.ln], [l, self.ln]], float)
        k = self.k[m]
        return np.array([[k, 0], [k + 1, 0], [k + 1, 1], [k, 1]], float)

    def cuadrante(self, m: int) -> np.ndarray:
        P = self.poligono(m)
        v0 = P[2, 1] * 2 / 3
        return np.array([[P[0, 0], v0], [P[1, 0], v0], P[2], P[3]], float)

    def contorno(self, m: int) -> np.ndarray:
        """Blanca con las muescas de sus negras vecinas (mismo orden que detect.py)."""
        if es_negra(m):
            return self.poligono(m)
        k = self.k[m]; ln = self.ln
        cont = []
        if (m - 1) in self.notas and es_negra(m - 1) and (m - 2) in self.k:
            _, r = self.negra(m - 1)
            cont += [[r, 0], [r, ln], [k, ln]]
        else:
            cont.append([k, 0])
        cont += [[k, 1], [k + 1, 1]]
        if (m + 1) in self.notas and es_negra(m + 1) and (m + 2) in self.k:
            l, _ = self.negra(m + 1)
            cont += [[k + 1, ln], [l, ln], [l, 0]]
        else:
            cont.append([k + 1, 0])
        return np.array(cont, float)

    # ------------------------------------------------------------ plantilla
    def a_plantilla(self, S: float, K: float) -> np.ndarray:
        """Matriz modelo → píxeles de la plantilla (S px por blanca; la tecla mide K blancas de largo)."""
        return np.array([[S, 0, MARGEN_U * S], [0, S * K, -V_MIN * S * K], [0, 0, 1]], np.float64)

    def plantilla(self, S: float = 12, K: float = 5) -> tuple[np.ndarray, np.ndarray]:
        M = self.a_plantilla(S, K)
        W = int(round((self.nb + 2 * MARGEN_U) * S)); H = int(round((V_MAX - V_MIN) * S * K))
        t = np.full((H, W), 0.12, np.float32)

        def px(u, v):
            p = M @ np.array([u, v, 1.0])
            return int(round(p[0])), int(round(p[1]))

        cv2.rectangle(t, px(0, 0), px(self.nb, 1), 0.85, -1)
        for i in range(self.nb + 1):
            cv2.line(t, px(i, 0), px(i, 1), 0.55, 1)
        for m in self.negras():
            l, r = self.negra(m)
            cv2.rectangle(t, px(l, 0), px(r, self.ln), 0.12, -1)
        return t, M


def _ecc(gris: np.ndarray, H0: np.ndarray, modelo: Modelo, S: float, K: float, iters: int, suave: float):
    """Afina la homografía modelo → imagen H0 con ECC. Devuelve (H, correlación) o (None, -1)."""
    t, M = modelo.plantilla(S, K)
    W = (H0 @ np.linalg.inv(M)).astype(np.float32)
    W /= W[2, 2]
    g = cv2.GaussianBlur(gris, (0, 0), suave) if suave > 0 else gris
    try:
        cc, W = cv2.findTransformECC(t, g, W, cv2.MOTION_HOMOGRAPHY,
                                     (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, iters, 1e-7), None, 5)
    except cv2.error:
        return None, -1.0
    H = W.astype(np.float64) @ M
    return H / H[2, 2], float(cc)


def _a_imagen(H: np.ndarray, pts) -> np.ndarray:
    P = np.asarray(pts, np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(P, H).reshape(-1, 2)


def _inicios(rect, nb: int) -> list[np.ndarray]:
    """Paralelogramos alrededor del rectángulo del teclado: cizalle (las teclas se ven inclinadas), escala y corrimiento."""
    q = ordenar_rect(rect)                       # atrás-izq, atrás-der, frente-der, frente-izq
    eu = q[1] - q[0]; largo = float(np.linalg.norm(eu)); eu = eu / max(largo, 1e-9)
    ev = q[3] - q[0]; corto = float(np.linalg.norm(ev)); ev = ev / max(corto, 1e-9)
    c = q.mean(axis=0)
    out = []
    for cizalle in (0.0, -0.5, 0.5, -1.0, 1.0, -0.25, 0.25, -0.75, 0.75):
        for escala in (1.0, 0.94, 1.06):
            for corr in (0.0, -0.5, 0.5):
                L = largo * escala
                c0 = c + corr * L / nb * eu
                fi = c0 - L / 2 * eu + corto / 2 * ev; fd = c0 + L / 2 * eu + corto / 2 * ev
                ai = fi - corto * ev + cizalle * corto * eu; ad = fd - corto * ev + cizalle * corto * eu
                out.append(np.array([ai, ad, fd, fi]))
    return out


def _afinar_negras(gris, H, modelo: Modelo, S=16, K=5) -> tuple[float, float]:
    """Mejor ancho y largo de las negras con la homografía fija (correlación con la imagen rectificada)."""
    M = modelo.a_plantilla(S, K)
    t0, _ = modelo.plantilla(S, K)
    rect = cv2.warpPerspective(gris, H @ np.linalg.inv(M), (t0.shape[1], t0.shape[0]),
                               flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    r = (rect - rect.mean()) / (rect.std() + 1e-6)
    mejor = (-2.0, modelo.an, modelo.ln)
    for an in np.arange(0.50, 0.70, 0.02):
        for ln in np.arange(0.50, 0.78, 0.02):
            m2 = Modelo(len(modelo.notas), modelo.notas[0], float(an), float(ln))
            t, _ = m2.plantilla(S, K)
            tt = (t - t.mean()) / (t.std() + 1e-6)
            cc = float((tt * r).mean())
            if cc > mejor[0]:
                mejor = (cc, float(an), float(ln))
    return mejor[1], mejor[2]


def _conf_tecla(rect_img, plantilla, M, P_modelo, margen=0.35) -> float:
    """Correlación local plantilla ↔ imagen rectificada alrededor de una tecla (0..1)."""
    x0, y0 = (M @ np.array([P_modelo[:, 0].min() - margen, V_MIN + 0.3, 1.0]))[:2]
    x1, y1 = (M @ np.array([P_modelo[:, 0].max() + margen, 1.1, 1.0]))[:2]
    x0, y0 = max(0, int(x0)), max(0, int(y0)); x1, y1 = int(math.ceil(x1)), int(math.ceil(y1))
    a = plantilla[y0:y1, x0:x1].ravel(); b = rect_img[y0:y1, x0:x1].ravel()
    if a.size < 10 or a.std() < 1e-6 or b.std() < 1e-6:
        return 0.0
    return float(np.clip(np.corrcoef(a, b)[0, 1], 0.0, 1.0))


def ajustar_modelo(img: np.ndarray, rect=None, n_teclas: int = 61, primera_nota: int = 36,
                   H_inicial: np.ndarray | None = None) -> dict:
    """Ajusta el modelo del teclado a ``img`` (BGR, espacio del proyector). Mismo formato de salida que detectar_teclas."""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    Himg, Wimg = img.shape[:2]
    gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    modelo = Modelo(n_teclas, primera_nota)
    esquinas_modelo = np.array([[0, 0], [modelo.nb, 0], [modelo.nb, 1], [0, 1]], np.float32)
    avisos: list[str] = []

    # --- 1. varios arranques gruesos; el mejor
    if H_inicial is not None:
        candidatos = [np.asarray(H_inicial, float)]
    else:
        if rect is None:
            rect = estimar_rect(img)
        candidatos = [cv2.getPerspectiveTransform(esquinas_modelo, q.astype(np.float32)).astype(np.float64)
                      for q in _inicios(rect, modelo.nb)]
    mejor = (None, -1.0)
    vistos: list[np.ndarray] = []
    for H0 in candidatos:
        H, cc = _ecc(gris, H0, modelo, S=12, K=5, iters=120, suave=1.2)
        if H is None:
            continue
        q = _a_imagen(H, esquinas_modelo)
        if cc > mejor[1]:
            mejor = (H, cc)
        if any(np.abs(q - v).max() < 2.0 for v in vistos):
            if mejor[1] > 0.7 and len(vistos) >= 2:      # varios arranques llegan al mismo lugar: basta
                break
        vistos.append(q)
    H, cc = mejor
    if H is None:
        raise ValueError("el ajuste por modelo no convergió")

    # --- 2. negras afinadas y ajuste fino
    an, ln = _afinar_negras(gris, H, modelo)
    modelo = Modelo(n_teclas, primera_nota, an, ln)
    H2, cc2 = _ecc(gris, H, modelo, S=24, K=5, iters=300, suave=0.8)
    if H2 is not None and cc2 >= cc - 0.02:
        H, cc = H2, cc2
    if cc < 0.6:
        avisos.append(f"correlación baja del modelo ({cc:.2f}): revisar con la imagen de verificación")

    # --- 3. teclas en píxeles del proyector + confianza local
    return teclas_por_homografia(H, modelo, img, cc, avisos, metodo="modelo", anclaje="modelo (cantos por plantilla)")


def teclas_por_homografia(H: np.ndarray, modelo: Modelo, img: np.ndarray | None, cc: float, avisos: list[str],
                          metodo: str = "modelo", anclaje: str = "modelo", extra: dict | None = None) -> dict:
    """Las teclas del modelo llevadas a la imagen con la homografía modelo → imagen `H`, en el formato de keys.json.
    Con `img`, cada tecla trae su confianza local (correlación plantilla ↔ imagen rectificada)."""
    Himg, Wimg = (img.shape[:2] if img is not None else (1080, 1920))
    esquinas_modelo = np.array([[0, 0], [modelo.nb, 0], [modelo.nb, 1], [0, 1]], np.float32)
    an, ln = modelo.an, modelo.ln
    S, K = 16, 5
    plantilla, M = modelo.plantilla(S, K)
    rect_img = None
    if img is not None:
        gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0 if img.ndim == 3 else img.astype(np.float32)
        rect_img = cv2.warpPerspective(gris, H @ np.linalg.inv(M), (plantilla.shape[1], plantilla.shape[0]),
                                       flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    salida = []
    for m in modelo.notas:
        Pm = modelo.poligono(m)
        poly = _a_imagen(H, Pm); cuad = _a_imagen(H, modelo.cuadrante(m)); cont = _a_imagen(H, modelo.contorno(m))
        centro = poly.mean(axis=0); toque = cuad.mean(axis=0)
        dentro = 0 <= centro[0] < Wimg and 0 <= centro[1] < Himg
        conf = (_conf_tecla(rect_img, plantilla, M, Pm) if rect_img is not None else 1.0) if dentro else 0.0
        estado = "fuera" if not dentro else ("visible" if conf >= 0.25 else "oculta")
        x0_, y0_ = poly.min(axis=0); x1_, y1_ = poly.max(axis=0)
        salida.append({
            "id": nombre_nota(m), "nota": m, "nombre": nombre_nota(m), "color": "negra" if es_negra(m) else "blanca",
            "estado": estado, "visible": estado == "visible", "confianza": round(conf, 3),
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
    quad = _a_imagen(H, esquinas_modelo)
    out = {
        "metodo": metodo,
        "n_teclas": len(salida), "n_blancas": len(blancas_out), "n_negras": len(salida) - len(blancas_out),
        "n_visibles": sum(s["estado"] == "visible" for s in salida),
        "n_ocultas": sum(s["estado"] == "oculta" for s in salida),
        "n_fuera": sum(s["estado"] == "fuera" for s in salida),
        "primera_nota": salida[0]["nota"] if salida else None,
        "primera_visible": {"nota": primera_vis["nota"], "nombre": primera_vis["nombre"]} if primera_vis else None,
        "paso_px": round(paso_px, 3), "paso_banda": 1.0,
        # la confianza global es la del ajuste entero (las locales castigan sombras y reflejos de más)
        "confianza_media": round(float(max(0.0, cc)), 3),
        "confianza_teclas": round(float(np.mean([s["confianza"] for s in en_cuadro])), 3) if en_cuadro else 0.0,
        "patron": {"correlacion": round(float(cc), 3), "anclaje": anclaje,
                   "canto_izquierdo_real": True, "canto_derecho_real": True, "blancas_entre_cantos": modelo.nb},
        "modelo": {"ecc": round(float(cc), 4), "ancho_negra_rel": round(an, 3), "largo_negra_rel": round(ln, 3)},
        "banda": {"H_modelo": np.round(H, 10).tolist(), "quad": np.round(quad, 2).tolist()},
        "avisos": avisos,
        "teclas": salida,
    }
    if extra:
        out.update(extra)
    return out

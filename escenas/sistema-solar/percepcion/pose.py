"""Movimiento rígido del canasto entero entre dos cuadros, a partir de sus bolas (y el engranaje, si está).

Las bolas no tienen identidad: son blancas y varias miden casi lo mismo. Por eso no se siguen una por una. Se prueban
hipótesis con dos pares (dos bolas del cuadro viejo ↔ dos del nuevo, con distancias y radios compatibles), cada
hipótesis da una rotación y una traslación, y gana la que deja más bolas calzando (RANSAC exhaustivo: con 10 bolas son
pocos miles de hipótesis, menos de 50 ms). Después se refina con mínimos cuadrados (Kabsch) sobre las que calzan.

Así sale bien aunque falten bolas (una tapada por la mano), aparezca una de más o el canasto gire 180°.
"""

from __future__ import annotations

import itertools
import math

import numpy as np

from .tipos import Cuerpo, Pose


def _kabsch(a: np.ndarray, b: np.ndarray) -> tuple[float, np.ndarray]:
    """Rotación (radianes) y traslación que llevan los puntos a sobre b, en mínimos cuadrados."""
    ca, cb = a.mean(axis=0), b.mean(axis=0)
    h = (a - ca).T @ (b - cb)
    th = math.atan2(h[0, 1] - h[1, 0], h[0, 0] + h[1, 1])
    r = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    return th, cb - r @ ca


def aplicar_pose(puntos, pose: Pose | tuple[float, tuple[float, float]]) -> np.ndarray:
    """Mueve puntos (N×2, mm) con la pose: R·p + t."""
    th_deg, t = (pose.theta_deg, pose.t_mm) if isinstance(pose, Pose) else pose
    th = math.radians(th_deg)
    r = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    return (np.asarray(puntos, dtype=np.float64).reshape(-1, 2) @ r.T) + np.asarray(t, dtype=np.float64)


def pose_grupo(
    ref: list[Cuerpo],
    actual: list[Cuerpo],
    tol_mm: float = 12.0,
    tol_radio: float = 0.3,
    previa: Pose | None = None,
) -> Pose:
    """Pose que lleva el grupo `ref` al grupo `actual`. `previa` desempata a favor del movimiento más parecido."""
    if not ref or not actual:
        return Pose(0.0, (0.0, 0.0), 0, len(ref), 0.0, False)
    pa = np.array([c.centro_mm for c in ref], dtype=np.float64)
    pb = np.array([c.centro_mm for c in actual], dtype=np.float64)
    ra = np.array([c.radio_mm for c in ref])
    rb = np.array([c.radio_mm for c in actual])
    ta = [c.tipo for c in ref]
    tb = [c.tipo for c in actual]

    def compatible(i: int, j: int) -> bool:
        return ta[i] == tb[j] and abs(ra[i] - rb[j]) <= tol_radio * max(ra[i], rb[j]) + 3

    def evaluar(th: float, t: np.ndarray) -> tuple[int, float, list[tuple[int, int]]]:
        r = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
        q = pa @ r.T + t
        d = np.linalg.norm(q[:, None, :] - pb[None, :, :], axis=2)
        pares, usados, err = [], set(), 0.0
        for i in np.argsort(d.min(axis=1)):
            for j in np.argsort(d[i]):
                if d[i, j] > tol_mm:
                    break
                if j in usados or not compatible(i, j):
                    continue
                pares.append((int(i), int(j)))
                usados.add(j)
                err += d[i, j]
                break
        return len(pares), (err / len(pares) if pares else 1e9), pares

    mejor = (0, 1e9, [], 0.0, np.zeros(2))
    n = len(ref)
    idx_a = list(itertools.combinations(range(n), 2)) if n >= 2 else []
    for i, k in idx_a:
        dab = np.linalg.norm(pa[i] - pa[k])
        if dab < 15:
            continue
        for j, l in itertools.permutations(range(len(actual)), 2):
            if not (compatible(i, j) and compatible(k, l)):
                continue
            if abs(np.linalg.norm(pb[j] - pb[l]) - dab) > tol_mm:
                continue
            th, t = _kabsch(pa[[i, k]], pb[[j, l]])
            cuenta, err, pares = evaluar(th, t)
            if previa is not None:
                # Penaliza levemente los saltos grandes respecto de la pose anterior (desempate, no veto).
                err += 0.02 * abs(math.degrees(th) - previa.theta_deg) + 0.01 * float(np.linalg.norm(t - np.array(previa.t_mm)))
            if cuenta > mejor[0] or (cuenta == mejor[0] and err < mejor[1]):
                mejor = (cuenta, err, pares, th, t)
    if n == 1 and len(actual) >= 1:  # una sola bola: solo traslación
        j = int(np.argmin(np.linalg.norm(pb - pa[0], axis=1)))
        t = pb[j] - pa[0]
        return Pose(0.0, (float(t[0]), float(t[1])), 1, 1, 0.0, False, [(0, j)])
    cuenta, _, pares, th, t = mejor
    if cuenta >= 2:
        ii = [p[0] for p in pares]
        jj = [p[1] for p in pares]
        th, t = _kabsch(pa[ii], pb[jj])
        cuenta, err, pares = evaluar(th, t)
    else:
        err = 0.0
    # Con 2 bolas la rotación queda determinada, pero confiamos solo si los radios las distinguen.
    ok = cuenta >= 3 or (cuenta == 2 and abs(ra[pares[0][0]] - ra[pares[1][0]]) > 0.25 * max(ra[pares[0][0]], ra[pares[1][0]]))
    deg = (math.degrees(th) + 180) % 360 - 180
    return Pose(round(deg, 2), (round(float(t[0]), 1), round(float(t[1]), 1)), cuenta, n, round(float(err), 2), bool(ok), pares)

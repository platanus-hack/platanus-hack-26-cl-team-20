"""Solver de calibración del proyector en Python (numpy + OpenCV), para validar el solver JS del casco.

    .venv/bin/python quest/calib_solver.py quest/pruebas/resultados/sintetico.json   # compara con el JS
    .venv/bin/python quest/calib_solver.py quest/calib/calib.json                    # re-resuelve una calibración real

Entrada: JSON con `uv` (px del proyector) y `X` (m, marco del rig) o con `puntos: [{uv, X}]` (export del casco),
más `w`, `h`. Si trae `verdad` y/o `js`, reporta diferencias. Convención OpenCV: x_cam = R·X + t, u ~ K·x_cam.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np


def dlt(uv: np.ndarray, X: np.ndarray) -> np.ndarray:
    """DLT normalizado (Hartley) independiente del JS: P 3x4."""
    c2 = uv.mean(0)
    s2 = np.sqrt(2) / np.linalg.norm(uv - c2, axis=1).mean()
    T2 = np.array([[s2, 0, -s2 * c2[0]], [0, s2, -s2 * c2[1]], [0, 0, 1]])
    c3 = X.mean(0)
    s3 = np.sqrt(3) / np.linalg.norm(X - c3, axis=1).mean()
    T3 = np.diag([s3, s3, s3, 1.0])
    T3[:3, 3] = -s3 * c3
    un = (uv - c2) * s2
    Xn = np.c_[(X - c3) * s3, np.ones(len(X))]
    A = []
    for (u, v), Xh in zip(un, Xn):
        A.append(np.r_[Xh, np.zeros(4), -u * Xh])
        A.append(np.r_[np.zeros(4), Xh, -v * Xh])
    _, _, Vt = np.linalg.svd(np.asarray(A))
    Pn = Vt[-1].reshape(3, 4)
    return np.linalg.inv(T2) @ Pn @ T3


def descomponer(P: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """P → K (K22=1), R (det +1), t con RQ (vía QR de la matriz volteada)."""
    P = P.copy()
    if np.linalg.det(P[:, :3]) < 0:
        P = -P
    F = np.fliplr(np.eye(3))
    Q, Rq = np.linalg.qr((F @ P[:, :3]).T)
    K = F @ Rq.T @ F
    R = F @ Q.T
    D = np.diag(np.sign(np.diag(K)))
    K, R = K @ D, D @ R
    t = np.linalg.solve(K, P[:, 3])
    return K / K[2, 2], R, t


def reproj(K, R, t, X, uv):
    p = (K @ (R @ X.T + t.reshape(3, 1))).T
    p = p[:, :2] / p[:, 2:]
    e = np.linalg.norm(p - uv, axis=1)
    return float(np.sqrt((e ** 2).mean())), e


def rot_deg(Ra, Rb) -> float:
    r, _ = cv2.Rodrigues(np.asarray(Ra) @ np.asarray(Rb).T)
    return float(np.degrees(np.linalg.norm(r)))


def centro(R, t) -> np.ndarray:
    return -np.asarray(R).T @ np.asarray(t)


def opencv_calib(uv, X, w, h, K0, misma_f: bool):
    flags = (cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_ZERO_TANGENT_DIST
             | cv2.CALIB_FIX_K1 | cv2.CALIB_FIX_K2 | cv2.CALIB_FIX_K3)
    K0 = K0.copy()
    K0[0, 1] = 0
    if misma_f:
        f = (K0[0, 0] + K0[1, 1]) / 2
        K0[0, 0] = K0[1, 1] = f
        flags |= cv2.CALIB_FIX_ASPECT_RATIO
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_COUNT, 500, 1e-14)
    rms, K, dist, rv, tv = cv2.calibrateCamera([X.astype(np.float32)], [uv.astype(np.float32)], (w, h), K0,
                                               np.zeros(5), flags=flags, criteria=crit)
    R, _ = cv2.Rodrigues(rv[0])
    return K, R, tv[0].ravel()


def cargar(path: Path):
    d = json.loads(path.read_text())
    if "puntos" in d:
        uv = np.array([p["uv"] for p in d["puntos"]], float)
        X = np.array([p["X"] for p in d["puntos"]], float)
    else:
        uv, X = np.array(d["uv"], float), np.array(d["X"], float)
    return d, uv, X


def fila(nombre, K, R, t, X, uv, ref=None):
    rms, _ = reproj(K, R, t, X, uv)
    s = (f"{nombre:<28} fx={K[0, 0]:8.2f} fy={K[1, 1]:8.2f} cx={K[0, 2]:7.2f} cy={K[1, 2]:7.2f}"
         f"  centro=({', '.join(f'{c:+.4f}' for c in centro(R, t))}) m  rms={rms:6.3f}px")
    if ref is not None:
        Kr, Rr, tr = ref
        s += (f"   Δf={abs(K[0, 0] - Kr[0, 0]):.2e} Δc={abs(K[0, 2] - Kr[0, 2]) + abs(K[1, 2] - Kr[1, 2]):.2e}px"
              f" ΔR={rot_deg(R, Rr):.2e}° Δcentro={np.linalg.norm(centro(R, t) - centro(Rr, tr)) * 1000:.2e}mm")
    print(s)
    return rms


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json", type=Path)
    ap.add_argument("--salida", type=Path, help="escribe la calibración de OpenCV a este JSON")
    a = ap.parse_args()
    d, uv, X = cargar(a.json)
    w, h = int(d.get("w", 1920)), int(d.get("h", 1080))
    print(f"{a.json.name}: {len(uv)} pares, cuadro {w}x{h}")
    if len(uv) < 6:
        print("Faltan puntos (≥ 6)")
        return 1

    P = dlt(uv, X)
    Kd, Rd, td = descomponer(P)
    print("\n— numpy —")
    fila("DLT numpy (con skew)", Kd, Rd, td, X, uv)
    Kc, Rc, tc = opencv_calib(uv, X, w, h, Kd, misma_f=False)
    Kf, Rf, tf = opencv_calib(uv, X, w, h, Kd, misma_f=True)
    print("\n— OpenCV calibrateCamera (sin distorsión, skew 0) —")
    fila("OpenCV fx,fy libres", Kc, Rc, tc, X, uv)
    fila("OpenCV fx=fy", Kf, Rf, tf, X, uv)

    ok = True
    if "js_dlt" in d:
        j = d["js_dlt"]
        print("\n— DLT: JS vs numpy —")
        fila("DLT JS", np.array(j["K"]), np.array(j["R"]), np.array(j["t"]), X, uv, ref=(Kd, Rd, td))
    if "js" in d:
        print("\n— LM: JS vs OpenCV (mismo objetivo) —")
        for nombre, ref in (("completo", (Kc, Rc, tc)), ("fxfy", (Kf, Rf, tf))):
            if nombre in d["js"]:
                j = d["js"][nombre]
                Kj, Rj, tj = np.array(j["K"]), np.array(j["R"]), np.array(j["t"])
                fila(f"JS {nombre}", Kj, Rj, tj, X, uv, ref=ref)
                dc = np.linalg.norm(centro(Rj, tj) - centro(*ref[1:])) * 1000
                ok &= abs(Kj[0, 0] - ref[0][0, 0]) < 0.05 and rot_deg(Rj, ref[1]) < 1e-3 and dc < 0.05
        # PnP con la K del JS
        j = d["js"].get("completo")
        if j:
            Kj = np.array(j["K"])
            okp, rv, tv = cv2.solvePnP(X, uv, Kj, None, flags=cv2.SOLVEPNP_ITERATIVE)
            Rp, _ = cv2.Rodrigues(rv)
            fila("solvePnP con K del JS", Kj, Rp, tv.ravel(), X, uv, ref=(Kj, np.array(j["R"]), np.array(j["t"])))
    if "verdad" in d:
        v = d["verdad"]
        Kv, Rv, tv = np.array(v["K"]), np.array(v["R"]), np.array(v["t"])
        print("\n— contra la verdad sintética —")
        fila("verdad", Kv, Rv, tv, X, uv)
        fila("OpenCV fx=fy vs verdad", Kf, Rf, tf, X, uv, ref=(Kv, Rv, tv))

    if a.salida:
        a.salida.write_text(json.dumps({"K": Kf.tolist(), "R": Rf.tolist(), "t": tf.tolist(), "w": w, "h": h,
                                        "fuente": f"calib_solver.py fx=fy sobre {a.json.name}"}, indent=1))
        print(f"→ {a.salida}")
    if "js" in d:
        print(f"\nJS == OpenCV (tolerancia 0.05 px en f, 1e-3°, 0.05 mm): {'OK' if ok else 'DIFIEREN'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())

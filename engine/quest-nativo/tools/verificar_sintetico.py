#!/usr/bin/env python3
"""Verificación cruzada de las pruebas sintéticas de la app nativa (Kotlin) con slscan + OpenCV.

Uso (desde la raíz del repo sl-scanner, con su .venv):
    cd quest-nativo && ./gradlew :app:testDebugUnitTest      # genera app/build/sintetico/
    ../.venv/bin/python tools/verificar_sintetico.py

1. Gray code: decodifica las MISMAS imágenes PGM que generó el test Kotlin con
   `slscan.graycode.decode` (umbral 6, skip_bits 1) y compara bloque a bloque con el mapa Kotlin.
2. Proyector: para cada CSV de pares 3D↔2D ajusta P con DLT normalizado (numpy SVD) + refinamiento
   con cv2.solvePnP (intrínsecos de la descomposición) y compara residuo y centro con Kotlin.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

AQUI = Path(__file__).resolve().parent
RAIZ_REPO = AQUI.parent.parent
sys.path.insert(0, str(RAIZ_REPO))
from slscan import graycode  # noqa: E402

SINT = AQUI.parent / "app" / "build" / "sintetico"


def verificar_gray() -> dict:
    d = SINT / "gray"
    items = [(spec, path) for spec, path in graycode.load_sequence(d)]
    dec = graycode.decode(items, threshold=6, skip_bits=1, projector_size=(1920, 1080))
    h, w = dec.px.shape
    kb = np.fromfile(d / "kotlin_bloques_i32le.bin", dtype="<i4").reshape(h, w, 2)
    valid_py = dec.valid
    valid_kt = kb[..., 0] >= 0
    bx_py = (dec.px.astype(np.int64) >> 1)
    by_py = (dec.py.astype(np.int64) >> 1)
    ambos = valid_py & valid_kt
    iguales = ambos & (bx_py == kb[..., 0]) & (by_py == kb[..., 1])
    # verdad: homografía cámara → proyector
    H = np.array([float(x) for x in (d / "verdad_homografia.txt").read_text().split()]).reshape(3, 3)
    ys, xs = np.nonzero(valid_py)
    pts = cv2.perspectiveTransform(np.stack([xs, ys], 1).reshape(-1, 1, 2).astype(np.float64), H).reshape(-1, 2)
    # python guarda b|1 en convención "centros enteros": centro continuo del bloque [b, b+2) = b + 1 → px (b|1) es ese valor
    centro_py = np.stack([bx_py[ys, xs] * 2 + 1.0, by_py[ys, xs] * 2 + 1.0], 1)
    err = np.linalg.norm(centro_py - pts, axis=1)
    return {
        "validos_python": int(valid_py.sum()), "validos_kotlin": int(valid_kt.sum()),
        "solo_python": int((valid_py & ~valid_kt).sum()), "solo_kotlin": int((valid_kt & ~valid_py).sum()),
        "bloques_iguales_%": round(100.0 * iguales.sum() / max(1, ambos.sum()), 4),
        "err_bloque_python_mediana_px": round(float(np.median(err)), 3),
        "kotlin": (d / "resultado_kotlin.txt").read_text().strip(),
    }


def dlt(X: np.ndarray, uv: np.ndarray) -> np.ndarray:
    mX = X.mean(0); sX = np.sqrt(3) / np.mean(np.linalg.norm(X - mX, axis=1))
    mu = uv.mean(0); su = np.sqrt(2) / np.mean(np.linalg.norm(uv - mu, axis=1))
    T3 = np.diag([sX, sX, sX, 1.0]); T3[:3, 3] = -sX * mX
    T2 = np.diag([su, su, 1.0]); T2[:2, 2] = -su * mu
    Xn = (np.c_[X, np.ones(len(X))] @ T3.T); un = (np.c_[uv, np.ones(len(uv))] @ T2.T)
    A = []
    for (x, y, z, o), (u, v, _) in zip(Xn, un):
        A.append([x, y, z, o, 0, 0, 0, 0, -u * x, -u * y, -u * z, -u * o])
        A.append([0, 0, 0, 0, x, y, z, o, -v * x, -v * y, -v * z, -v * o])
    _, _, vt = np.linalg.svd(np.asarray(A))
    P = np.linalg.inv(T2) @ vt[-1].reshape(3, 4) @ T3
    if np.mean((np.c_[X, np.ones(len(X))] @ P[2]) > 0) < 0.5:
        P = -P
    return P / np.linalg.norm(P[2, :3])


def reproy(P: np.ndarray, X: np.ndarray, uv: np.ndarray) -> np.ndarray:
    q = np.c_[X, np.ones(len(X))] @ P.T
    return np.linalg.norm(q[:, :2] / q[:, 2:3] - uv, axis=1)


def verificar_pnp(nombre: str) -> dict:
    datos = np.loadtxt(SINT / "pnp" / f"{nombre}.csv", delimiter=",", skiprows=1)
    X, uv = datos[:, :3], datos[:, 3:5]
    kt = json.loads((SINT / "pnp" / f"{nombre}.kotlin.json").read_text())
    Pv = np.array(kt["P_verdad"]).reshape(3, 4)
    # inliers según la verdad (los outliers sintéticos son uv al azar)
    inl = reproy(Pv, X, uv) < 6
    out = {"n": len(X), "inliers_verdad": int(inl.sum())}
    Pk = np.array(kt["P"]).reshape(3, 4)
    rk = reproy(Pk, X[inl], uv[inl])
    out["kotlin"] = {"tipo": kt["tipo"], "mediana_px": round(float(np.median(rk)), 3),
                     "err_centro_mm": round(1000 * float(np.linalg.norm(np.array(kt["C"]) - np.array(kt["C_verdad"]))), 2)}
    if kt["tipo"] != "dlt":
        out["opencv"] = "caso plano: DLT degenerado, OpenCV no aplica sin intrínsecos (Kotlin usa modelo restringido)"
        return out
    P = dlt(X[inl], uv[inl])
    K, R, Ch, *_ = cv2.decomposeProjectionMatrix(P)
    K = K / K[2, 2]
    C = (Ch[:3] / Ch[3]).ravel()
    # el marco cabeza de Spatial SDK es zurdo: se refleja z para que R sea rotación propia (solvePnP)
    F = np.diag([1.0, 1.0, -1.0])
    Xr = X[inl] @ F
    ok, rvec, tvec = cv2.solvePnP(Xr, uv[inl], K, None, flags=cv2.SOLVEPNP_ITERATIVE)
    Rr, _ = cv2.Rodrigues(rvec)
    Pcv = K @ np.c_[Rr, tvec] @ np.block([[F, np.zeros((3, 1))], [np.zeros((1, 3)), np.ones((1, 1))]])
    r_dlt = reproy(P, X[inl], uv[inl]); r_cv = reproy(Pcv, X[inl], uv[inl])
    Ccv = F @ (-Rr.T @ tvec).ravel()
    out["opencv"] = {
        "dlt_mediana_px": round(float(np.median(r_dlt)), 3),
        "solvepnp_mediana_px": round(float(np.median(r_cv)), 3),
        "fx_fy_cx_cy": [round(float(v), 1) for v in (K[0, 0], K[1, 1], K[0, 2], K[1, 2])],
        "err_centro_dlt_mm": round(1000 * float(np.linalg.norm(C - np.array(kt["C_verdad"]))), 2),
        "err_centro_solvepnp_mm": round(1000 * float(np.linalg.norm(Ccv - np.array(kt["C_verdad"]))), 2),
    }
    out["kotlin"]["fx_fy_cx_cy"] = [round(kt[k], 1) for k in ("fx", "fy", "cx", "cy")]
    return out


def main() -> int:
    if not SINT.exists():
        print(f"falta {SINT}: corre ./gradlew :app:testDebugUnitTest primero", file=sys.stderr)
        return 2
    res = {"gray": verificar_gray(), "pnp": {n: verificar_pnp(n) for n in ("sala_1vista", "pared_3vistas", "pared_1vista")}}
    print(json.dumps(res, indent=1, ensure_ascii=False))
    (SINT / "verificacion_python.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    g = res["gray"]
    ok = g["bloques_iguales_%"] > 99.9 and abs(g["validos_python"] - g["validos_kotlin"]) < 0.001 * g["validos_python"]
    for n, r in res["pnp"].items():
        if isinstance(r.get("opencv"), dict):
            ok &= abs(r["opencv"]["solvepnp_mediana_px"] - r["kotlin"]["mediana_px"]) < 0.1
    print("OK: Kotlin y Python/OpenCV coinciden" if ok else "DIFERENCIAS: revisar")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

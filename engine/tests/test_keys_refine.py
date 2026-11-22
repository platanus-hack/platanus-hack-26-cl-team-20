"""Afinado tecla por tecla (playground.keys.refine) con un rig simulado: proyector → escena con reflectancia (teclas
verdaderas corridas respecto de keys.json) → dos cámaras con homografías distintas (como la Osmo y la Brio)."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from playground.keys.refine import (H, W, CamMap, Refiner, aplicar, correr, forma, grupos_separados, roi_teclas,
                                    _fill)

KEYS2 = Path(__file__).resolve().parent / "data" / "keys_scan2.json"


class SimRig:
    """Proyecta un lienzo sobre una escena simulada y devuelve lo que ve cada cámara."""

    def __init__(self, verdad: list[dict], cams: dict[str, tuple[np.ndarray, tuple[int, int]]], ambiente=0.06, ruido=1.5, semilla=0):
        refl = np.full((H, W), 0.45, np.float32)                       # mesa
        cuerpo = np.zeros((H, W), np.uint8)
        P = np.concatenate([np.asarray(t["poligono"], float) for t in verdad])
        x0, y0 = P.min(axis=0) - 30; x1, y1 = P.max(axis=0) + 25
        cv2.rectangle(cuerpo, (int(x0), int(y0)), (int(x1), int(y1)), 255, -1)
        refl[cuerpo > 0] = 0.08                                        # cuerpo del teclado
        for t in verdad:
            if t["color"] == "blanca":
                m = np.zeros((H, W), np.uint8); _fill(m, forma(t)); refl[m > 0] = 0.9
        for t in verdad:                                                # ranuras entre blancas (1 px)
            if t["color"] == "blanca":
                p = np.round(np.asarray(t["poligono"], float) * 4).astype(np.int32)
                cv2.line(refl, tuple(p[0]), tuple(p[3]), 0.3, 1, cv2.LINE_AA, 2)
        for t in verdad:
            if t["color"] == "negra":
                m = np.zeros((H, W), np.uint8); _fill(m, t["poligono"]); refl[m > 0] = 0.1
        self.refl = cv2.GaussianBlur(refl, (0, 0), 0.6)
        self.cams = cams
        self.ambiente = ambiente
        self.rng = np.random.default_rng(semilla)
        self.n = 0

    def capturar(self, img: np.ndarray) -> dict[str, np.ndarray]:
        self.n += 1
        lum = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        rad = (lum + self.ambiente) * self.refl * 235.0
        out = {}
        for c, (Hm, size) in self.cams.items():
            f = cv2.warpPerspective(rad, Hm, size, flags=cv2.INTER_AREA)
            f = cv2.GaussianBlur(f, (0, 0), 0.8) + self.rng.normal(0, 1.5, f.shape)
            out[c] = np.clip(f, 0, 255).astype(np.uint8)
        return out


def _map(Hm: np.ndarray, roi) -> CamMap:
    yy, xx = np.mgrid[roi.y0:roi.y1, roi.x0:roi.x1].astype(np.float32)
    pts = cv2.perspectiveTransform(np.stack([xx, yy], -1).reshape(-1, 1, 2), Hm).reshape(roi.shape + (2,))
    return CamMap("x", pts[..., 0].copy(), pts[..., 1].copy(), np.ones(roi.shape, bool))


def _homografias():
    # "osmo": ~0,5 px de cámara por px de proyector, algo girada; "brio": otra perspectiva, ~0,6
    src = np.float32([[600, 650], [1700, 650], [1700, 850], [600, 850]])
    osmo = cv2.getPerspectiveTransform(src, np.float32([[100, 300], [660, 330], [650, 430], [95, 400]]))
    brio = cv2.getPerspectiveTransform(src, np.float32([[200, 500], [900, 460], [950, 600], [180, 640]]))
    return {"osmo": (osmo, (800, 700)), "brio": (brio, (1100, 800))}


@pytest.fixture(scope="module")
def corrida():
    modelo = [t for t in json.loads(KEYS2.read_text())["teclas"]]
    # además del global, el último tramo del teclado (C6..C7) corrido 3 px más: como una deformación local real
    extra = {m: 3 for m in range(84, 97)}
    verdad = [correr(t, 4 + extra.get(t["nota"], 0), -2) for t in modelo]
    homs = _homografias()
    roi = roi_teclas(modelo)
    maps = {c: _map(Hm, roi) for c, (Hm, _) in homs.items()}
    for c in maps:
        maps[c].name = c
    rig = SimRig(verdad, homs)
    rep = Refiner(modelo, maps, rig, roi, log=lambda *a: None).correr(rango=12, paso=4, umbral=0.8, rango_tecla=6)
    return modelo, extra, rep


def test_encuentra_el_corrimiento_global(corrida):
    _, _, rep = corrida
    assert abs(rep["global"]["dx"] - 4) <= 1 and abs(rep["global"]["dy"] + 2) <= 1, rep["global"]   # ±1 px
    assert rep["concluyente"] and rep["global"]["por_camara"]
    assert sorted(rep["camaras"]) == ["brio", "osmo"]


def test_corrige_teclas_sueltas_y_mejora_iou(corrida):
    modelo, extra, rep = corrida
    aj = rep["ajustes"]
    err = {m: abs(aj[m]["tecla_dx"] - e) for m, e in extra.items()}
    assert np.mean([v <= 1 for v in err.values()]) >= 0.85, err
    otras = [abs(aj[t["nota"]]["tecla_dx"]) for t in modelo if t["nota"] not in extra]
    assert np.mean(np.array(otras) <= 1) > 0.9
    assert rep["iou_media_despues"] > rep["iou_media_antes"] + 0.15, (rep["iou_media_antes"], rep["iou_media_despues"])
    negras_tramo = [m for m in extra if aj[m]["camaras_despues"] and m % 12 in (1, 3, 6, 8, 10)]
    assert all(aj[m]["iou_despues"] > aj[m]["iou_antes"] for m in negras_tramo)
    assert all(set(aj[m]["camaras_despues"]) == {"osmo", "brio"} for m in extra)
    assert rep["capturas"] < 160


def test_aplicar_corre_los_poligonos(corrida):
    modelo, extra, rep = corrida
    keys = {"teclas": modelo, "confianza_media": 0.9}
    nuevo = aplicar(keys, rep)
    t60 = next(t for t in nuevo["teclas"] if t["nota"] == 60)
    v60 = next(t for t in modelo if t["nota"] == 60)
    d = np.asarray(t60["poligono"]) - np.asarray(v60["poligono"])
    assert np.allclose(d, [t60["ajuste"]["dx"], t60["ajuste"]["dy"]])
    assert t60["ajuste"]["global"] == [rep["global"]["dx"], rep["global"]["dy"]]
    assert nuevo["ajuste_refine"]["global"]["dx"] == rep["global"]["dx"]


def test_grupos_separados():
    g = grupos_separados(list(range(20)), por_grupo=4, sep=6)
    assert all(len(x) <= 4 for x in g) and sorted(i for x in g for i in x) == list(range(20))
    assert all(abs(a - b) >= 6 for x in g for a in x for b in x if a != b)


def test_salida_sin_runtime():
    from playground.keys.refine import OverlayWindow
    assert OverlayWindow.estado("http://127.0.0.1:9") == "apagado"


# ---------------------------------------------------------------------------------------------- cuadrilátero y corrimiento

def test_cuadrilatero_por_aristas():
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from test_keys import render_teclado
    from playground.keys.quad import detectar_cuadrilatero, teclas_desde_cuadrilatero
    esquinas = [(583.7, 746.6), (1385.5, 721.4), (1345.2, 822.9), (512.0, 833.5)]     # como el EK-50 en scan5
    img, gt, rect, _ = render_teclado(esquinas=esquinas, mancha=None, semilla=2)
    verdad = np.array([gt[36][0], gt[96][1], gt[96][2], gt[36][3]])
    q0 = verdad + np.array([5.0, -4.0])          # keys.json corrido entero (la dirección de los cantos se mantiene)
    det = detectar_cuadrilatero(img, q0)
    err = np.abs(det["esquinas"] - verdad).max(axis=1)
    assert err.max() < 2.5, (det["esquinas"].round(1), verdad.round(1))
    nuevo = teclas_desde_cuadrilatero(det["esquinas"], img)
    assert nuevo["metodo"] == "cuadrilatero" and nuevo["n_teclas"] == 61
    assert set(nuevo["esquinas"]) == {"atras_izq", "atras_der", "frente_der", "frente_izq"}
    c4 = next(t for t in nuevo["teclas"] if t["nota"] == 60)
    assert np.abs(np.asarray(c4["poligono"]) - gt[60]).max() < 3.0


def test_corrimiento_por_fase():
    from playground.keys.quad import corrimiento_por_fase, patron_aleatorio
    pat = patron_aleatorio(semilla=5).astype(np.float32)
    M = np.float32([[1, 0, 3.0], [0, 1, -7.0]])
    pos = cv2.warpAffine(pat, M, (pat.shape[1], pat.shape[0])) * 0.6 + 20      # lo visto: corrido y con reflectancia
    neg = cv2.warpAffine(255 - pat, M, (pat.shape[1], pat.shape[0])) * 0.6 + 20
    r = corrimiento_por_fase(pat, pos, neg, np.ones(pat.shape, bool))
    assert abs(r["dx"] - 3.0) < 0.3 and abs(r["dy"] + 7.0) < 0.3 and r["corregir_con"] == [-r["dx"], -r["dy"]]

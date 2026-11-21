"""Detección tecla por tecla (playground.keys) con un teclado sintético y, si existe, con el escaneo real.

El sintético se dibuja con el patrón real (61 teclas C2..C7, negras con la distribución "12 iguales atrás"),
supermuestreado ×4 y promediado (bordes con subpíxel exacto), rotado 0,5°, con desenfoque y ruido, y con una mancha
tipo cocodrilo que tapa 5 blancas. La verdad de terreno son los polígonos exactos de cada tecla.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from playground.keys import detectar_teclas, nombre_nota
from playground.keys.fit import ajustar_modelo
from playground.keys.detect import ANCHO_NEGRA, LARGO_NEGRA, PC_BLANCAS, desfase_negra, es_negra

RAIZ = Path(__file__).resolve().parent.parent
ESCANEO = Path(os.environ.get("KEYS_SCAN", RAIZ / "captures" / "scan2"))


# ---------------------------------------------------------------- teclado sintético

def render_teclado(paso=24.0, largo=82.0, angulo=0.5, centro=(1265.0, 750.0), n_teclas=61, primera=36,
                   mancha=(20, 25), ss=4, ruido=2.5, semilla=0, tam=(1920, 1080), altura_mancha=None, esquinas=None):
    """Imagen BGR del teclado + verdad de terreno {midi: polígono 4x2 en px de imagen} + rect + altura opcional.

    Marco del teclado: u a lo largo (0 = canto izquierdo del primer Do), v de atrás (0) al frente (largo).
    ``mancha=(i, j)`` tapa por completo las blancas i..j-1 (y sus negras), como el cocodrilo sobre las teclas.
    ``esquinas``: 4 puntos (atrás-izq, atrás-der, frente-der, frente-izq) donde debe caer el área de teclas: se
    agrega esa perspectiva (homografía exacta, aplicada a los vértices antes de rasterizar)."""
    rng = np.random.default_rng(semilla)
    W, H = tam
    blancas = [m for m in range(primera, primera + n_teclas) if m % 12 in PC_BLANCAS]
    U = len(blancas) * paso
    t = math.radians(angulo); R = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
    c = np.array(centro, float)

    def plano(pts):
        P = np.asarray(pts, float) - [U / 2, largo / 2]
        return P @ R.T + c

    Hp = None
    if esquinas is not None:
        Hp = cv2.getPerspectiveTransform(plano([(0, 0), (U, 0), (U, largo), (0, largo)]).astype(np.float32),
                                         np.asarray(esquinas, np.float32))

    def a_img(pts):
        P = plano(pts)
        return P if Hp is None else cv2.perspectiveTransform(P.reshape(-1, 1, 2), Hp).reshape(-1, 2)

    cuerpo = a_img([(-70, -70), (U + 70, -70), (U + 70, largo + 30), (-70, largo + 30)])
    x0, y0 = np.floor(cuerpo.min(axis=0)).astype(int) - 4; x1, y1 = np.ceil(cuerpo.max(axis=0)).astype(int) + 4
    lienzo = np.zeros(((y1 - y0) * ss, (x1 - x0) * ss, 3), np.uint8); lienzo[:] = (176, 179, 182)   # mesa
    mascara = np.zeros(lienzo.shape[:2], np.uint8)

    def rellenar(img, pts, color):
        # centro de píxel: el píxel (x, y) de salida promedia los ss×ss del lienzo, centrados en (x, y)
        P = (a_img(pts) - [x0, y0] + 0.5) * ss - 0.5
        cv2.fillPoly(img, [np.round(P * 16).astype(np.int32).reshape(-1, 1, 2)], color, lineType=cv2.LINE_8, shift=4)

    rellenar(lienzo, [(-60, -60), (U + 60, -60), (U + 60, largo + 22), (-60, largo + 22)], (46, 47, 50))
    gt = {}
    hueco = 1.5
    for i, m in enumerate(blancas):
        a = i * paso + (hueco / 2 if i > 0 else 0); z = (i + 1) * paso - (hueco / 2 if i < len(blancas) - 1 else 0)
        rellenar(lienzo, [(a, 0), (z, 0), (z, largo), (a, largo)], (226, 229, 231))
        gt[m] = a_img([(i * paso, 0), ((i + 1) * paso, 0), ((i + 1) * paso, largo), (i * paso, largo)])
    lb = LARGO_NEGRA * largo
    for m in range(primera, primera + n_teclas):
        if not es_negra(m):
            continue
        k = blancas.index(m - 1) + 1
        cn = (k + desfase_negra(m % 12)) * paso; w = ANCHO_NEGRA * paso
        rellenar(lienzo, [(cn - w / 2, 0), (cn + w / 2, 0), (cn + w / 2, lb), (cn - w / 2, lb)], (30, 30, 33))
        gt[m] = a_img([(cn - w / 2, 0), (cn + w / 2, 0), (cn + w / 2, lb), (cn - w / 2, lb)])
    if mancha is not None:
        i, j = mancha; a = (i - 0.1) * paso; z = (j + 0.1) * paso
        # contorno redondeado (lomo del cocodrilo) que se sale de las teclas por atrás y por delante
        ts = np.linspace(0, 2 * math.pi, 90)
        cu, cv = (a + z) / 2, largo / 2 - 4
        ru, rv = (z - a) / 2, largo / 2 + 18
        pts = [(cu + ru * np.sign(math.cos(s)) * abs(math.cos(s)) ** 0.25, cv + rv * np.sign(math.sin(s)) * abs(math.sin(s)) ** 0.25) for s in ts]
        rellenar(mascara, pts, 255)
        textura = cv2.GaussianBlur(rng.normal(0, 22, lienzo.shape[:2]).astype(np.float32), (0, 0), 3 * ss)
        base = np.array([112, 150, 124], np.float32)
        piel = np.clip(base[None, None, :] + textura[..., None] * np.array([0.8, 1.0, 0.9], np.float32), 0, 255)
        lienzo = np.where(mascara[..., None] > 0, piel.astype(np.uint8), lienzo)
    roi = cv2.resize(lienzo, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
    img = np.zeros((H, W, 3), np.uint8); img[:] = (176, 179, 182)
    # pegar recortando a la imagen
    ix0, iy0 = max(0, x0), max(0, y0); ix1, iy1 = min(W, x1), min(H, y1)
    img[iy0:iy1, ix0:ix1] = roi[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0]
    img = cv2.GaussianBlur(img, (0, 0), 0.7)
    img = np.clip(img.astype(np.float32) + rng.normal(0, ruido, img.shape), 1, 255).astype(np.uint8)
    altura = None
    if altura_mancha is not None:
        m_roi = cv2.resize(mascara, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
        altura = np.zeros((H, W), np.float32)
        altura[iy0:iy1, ix0:ix1] = altura_mancha * m_roi[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0]
        altura += rng.normal(0, 0.15, altura.shape).astype(np.float32)
    # rectángulo como lo daría el banco: minAreaRect de lo blanco visible, con un poco de error
    teclas = np.concatenate([gt[m] for m in blancas])
    teclas[:, 0] = np.clip(teclas[:, 0], 0, W - 1); teclas[:, 1] = np.clip(teclas[:, 1], 0, H - 1)
    (cx, cy), (rw, rh), ra = cv2.minAreaRect(teclas.astype(np.float32))
    rect = ((cx + 1.5, cy - 1.0), (rw * 1.02, rh * 0.97), ra + 0.2)
    return img, gt, rect, altura


def _por_nota(res):
    return {t["nota"]: t for t in res["teclas"]}


def _error_poligono(t, gt):
    return float(np.abs(np.asarray(t["poligono"]) - gt).max())


# ---------------------------------------------------------------- pruebas sintéticas

@pytest.fixture(scope="module")
def sintetico():
    img, gt, rect, _ = render_teclado()
    return detectar_teclas(img, rect), gt


def test_cuenta_y_nombres(sintetico):
    res, gt = sintetico
    blancas = [t for t in res["teclas"] if t["color"] == "blanca"]
    negras = [t for t in res["teclas"] if t["color"] == "negra"]
    assert len(blancas) == 36 and len(negras) == 25
    assert res["primera_nota"] == 36 and res["primera_visible"]["nombre"] == "C2"
    assert res["patron"]["anclaje"] == "ambos"
    assert [t["nota"] for t in res["teclas"]] == list(range(36, 97))
    for t in res["teclas"]:
        assert t["nombre"] == nombre_nota(t["nota"])
        assert (t["color"] == "negra") == es_negra(t["nota"])
    assert _por_nota(res)[60]["nombre"] == "C4" and _por_nota(res)[96]["nombre"] == "C7"
    assert abs(res["paso_px"] - 24.0) < 0.1


def test_posiciones_subpixel(sintetico):
    res, gt = sintetico
    errores = {t["nota"]: _error_poligono(t, gt[t["nota"]]) for t in res["teclas"]}
    peor = max(errores, key=errores.get)
    assert errores[peor] < 1.0, f"peor tecla {nombre_nota(peor)}: {errores[peor]:.2f} px"
    blancas = [errores[m] for m in errores if not es_negra(m)]
    assert float(np.mean(blancas)) < 0.4


def test_ocultas_bajo_la_mancha(sintetico):
    res, gt = sintetico
    por = _por_nota(res)
    ocultas_bl = sorted(m for m, t in por.items() if t["color"] == "blanca" and t["estado"] == "oculta")
    assert ocultas_bl == [71, 72, 74, 76, 77]                      # B4 C5 D5 E5 F5: blancas 20..24
    ocultas_ng = {m for m, t in por.items() if t["color"] == "negra" and t["estado"] == "oculta"}
    assert {73, 75} <= ocultas_ng <= {70, 73, 75, 78}              # C#5 y D#5 seguro; A#4 y F#5 casi enteras
    assert all(t["estado"] == "visible" for m, t in por.items() if t["color"] == "blanca" and m not in ocultas_bl)
    for m in ocultas_bl + sorted(ocultas_ng):                      # inferidas en su lugar
        assert _error_poligono(por[m], gt[m]) < 1.0, nombre_nota(m)


def test_cuadrante_es_el_tercio_frontal(sintetico):
    res, gt = sintetico
    t = _por_nota(res)[60]; P = gt[60]
    frente = (P[2] + P[3]) / 2; atras = (P[0] + P[1]) / 2
    esperado = frente + (atras - frente) / 6                       # centro del tercio frontal
    assert np.linalg.norm(np.asarray(t["punto_toque"]) - esperado) < 1.0
    assert len(t["poligono"]) == 4 and len(t["cuadrante"]) == 4


def test_altura_marca_lo_que_sobresale():
    # mancha del mismo color que las teclas: solo la altura la delata
    img, gt, rect, altura = render_teclado(mancha=(8, 11), altura_mancha=6.0, semilla=3)
    res = detectar_teclas(img, rect, altura=altura)
    por = _por_nota(res)
    ocultas = sorted(m for m, t in por.items() if t["color"] == "blanca" and t["estado"] == "oculta")
    assert ocultas == [50, 52, 53]                                 # D3 E3 F3: blancas 8..10


def test_vista_parcial_nombra_por_el_patron():
    # el teclado se sale por la izquierda: ~8 blancas fuera de cuadro; el canto derecho ancla la octava
    img, gt, rect, _ = render_teclado(centro=(250.0, 600.0), mancha=None, semilla=1)
    res = detectar_teclas(img, rect)
    por = _por_nota(res)
    assert res["patron"]["anclaje"] == "derecho"
    for m, P in gt.items():
        t = por[m]
        if P[:, 0].min() > 2:                                       # entera dentro de la imagen
            assert t["estado"] == "visible", nombre_nota(m)
            assert _error_poligono(t, P) < 1.0, nombre_nota(m)
        elif P[:, 0].max() < 0:
            assert t["estado"] == "fuera", nombre_nota(m)
    assert res["primera_visible"]["nota"] in (gt_m for gt_m, P in gt.items() if P[:, 0].max() > 0)


def test_orientacion_girada():
    # mismo teclado visto al revés (180°): graves a la derecha de la imagen
    img, gt, rect, _ = render_teclado(angulo=180.5, mancha=None, semilla=2)
    res = detectar_teclas(img, rect)
    por = _por_nota(res)
    assert res["n_blancas"] == 36 and res["primera_visible"]["nombre"] == "C2"
    assert max(_error_poligono(por[m], gt[m]) for m in gt) < 1.0


def test_perspectiva_como_scan2():
    # perspectiva fuerte como la del escaneo real: canto izquierdo casi vertical, el derecho inclinado ~0,5
    esquinas = [(830, 712), (1680, 705), (1722, 787), (833, 792)]
    img, gt, rect, _ = render_teclado(esquinas=esquinas, mancha=(27, 30), semilla=4)
    res = detectar_teclas(img, rect)
    por = _por_nota(res)
    assert res["n_blancas"] == 36 and res["primera_visible"]["nombre"] == "C2" and res["patron"]["anclaje"] == "ambos"
    errores = {m: _error_poligono(por[m], gt[m]) for m in gt}
    peor = max(errores, key=errores.get)
    assert errores[peor] < 1.0, f"peor tecla {nombre_nota(peor)}: {errores[peor]:.2f} px"
    assert sorted(m for m, t in por.items() if t["color"] == "blanca" and t["estado"] == "oculta") == [83, 84, 86]   # B5 C6 D6: blancas 27..29


def test_cli_escribe_json_e_imagenes(tmp_path, capsys):
    from playground.keys.__main__ import main
    img, gt, rect, _ = render_teclado(semilla=5)
    cv2.imwrite(str(tmp_path / "scene_pov.png"), img)
    (tmp_path / "workbench.json").write_text(json.dumps({"teclado": {"rect": [list(rect[0]), list(rect[1]), rect[2]]}}))
    assert main([str(tmp_path)]) == 0
    res = json.loads((tmp_path / "keys.json").read_text())
    assert res["n_teclas"] == 61 and res["primera_visible"]["nombre"] == "C2"
    t = res["teclas"][0]
    assert {"poligono", "centro", "bbox", "cuadrante", "punto_toque", "nota", "nombre", "estado", "confianza"} <= set(t)
    proy = cv2.imread(str(tmp_path / "keys_project.png"))
    assert proy.shape == img.shape and proy.max() > 0
    assert cv2.imread(str(tmp_path / "keys_verify.png")).shape[1] == img.shape[1]
    assert "61 teclas" in capsys.readouterr().out


# ---------------------------------------------------------------- escaneo real (sin asertar números exactos)

@pytest.mark.skipif(not (ESCANEO / "scene_pov.png").exists(), reason="no hay captures/scan2 (KEYS_SCAN=... para elegir)")
def test_escaneo_real():
    from playground.keys.__main__ import cargar_rect
    img = cv2.imread(str(ESCANEO / "scene_pov.png"))
    altura = np.load(ESCANEO / "height_proj.npy") if (ESCANEO / "height_proj.npy").exists() else None
    res = detectar_teclas(img, cargar_rect(ESCANEO, img), altura=altura)
    resumen = {k: res[k] for k in ("n_teclas", "n_blancas", "n_negras", "n_visibles", "n_ocultas", "primera_visible",
                                   "paso_px", "confianza_media", "patron", "avisos")}
    print("\nescaneo real:", json.dumps(resumen, ensure_ascii=False))
    assert res["n_teclas"] > 0


# ---------------------------------------------------------------- ajuste por modelo (fit.py)

@pytest.mark.parametrize("mancha", [None, (20, 25)])
def test_ajuste_por_modelo_perspectiva_fuerte(mancha):
    """Como el EK-50 en scan5: teclas inclinadas ~35° y punto de fuga cerca; el detector borde a borde no puede."""
    esquinas = [(583.7, 746.6), (1385.5, 721.4), (1345.2, 822.9), (512.0, 833.5)]
    img, gt, rect, _ = render_teclado(esquinas=esquinas, mancha=mancha, semilla=5)
    res = ajustar_modelo(img, rect)
    assert res["metodo"] == "modelo" and res["n_teclas"] == 61 and res["confianza_media"] > 0.6
    por = _por_nota(res)
    err_bl = [_error_poligono(por[m], gt[m]) for m in gt if not es_negra(m)]
    err_ng = [_error_poligono(por[m], gt[m]) for m in gt if es_negra(m)]
    assert np.median(err_bl) < 1.5 and max(err_bl) < 4.0, (np.median(err_bl), max(err_bl))
    assert np.median(err_ng) < 3.0, np.median(err_ng)
    assert [t["nota"] for t in res["teclas"]] == list(range(36, 97))
    if mancha is not None:                                        # lo tapado baja su confianza local
        tapadas = [por[m]["confianza"] for m in gt if not es_negra(m)][21:24]
        libres = [por[m]["confianza"] for m in gt if not es_negra(m)][2:5]
        assert np.mean(tapadas) < np.mean(libres)

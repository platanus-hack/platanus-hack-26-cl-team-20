"""Percepción de la libreta sobre fotos sintéticas y sobre la foto real de la mesa (con una libreta sintética pegada)."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from percepcion import (
    Lector,
    caja_rotada,
    espacio_libre,
    hay_mano,
    lo_nuevo,
    mascara_tinta,
    px_a_mm,
    rect_en,
    rectificar,
    recorte_pagina,
    ubicar_libreta,
)
from percepcion import sintetico as s

RAIZ = Path(__file__).resolve().parents[3]
MESA_REAL = RAIZ / "data/mesa/2026-11-21/mesa-cenital-mm-1.jpg"

PRIMERAS = ["como le pongo a", "este endpoint?"]
TERCERA = ["/v1/cobros  o  /pagos"]


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / (aw * ah + bw * bh - inter)


def union(cajas):
    x0 = min(c[0] for c in cajas)
    y0 = min(c[1] for c in cajas)
    x1 = max(c[0] + c[2] for c in cajas)
    y1 = max(c[1] + c[3] for c in cajas)
    return [x0, y0, x1 - x0, y1 - y0]


def error_esquinas(quad, verdad):
    return float(np.max(np.linalg.norm(np.float32(quad) - np.float32(verdad), axis=1)))


# ------------------------------------------------------------------ dónde está la libreta


@pytest.mark.parametrize("semilla", [0, 1, 2])
def test_ubica_libreta_simple(semilla):
    f = s.foto(PRIMERAS, semilla=semilla)
    lib = ubicar_libreta(f["img"])
    assert lib is not None
    assert error_esquinas(lib["quad_px"], f["quad"]) < 12  # px en una foto de 1280×720
    assert len(lib["paginas"]) == 1


def test_ubica_libreta_doble_pagina_y_lomo():
    f = s.foto(PRIMERAS, doble_pagina=True, lineas_izq=["notas viejas", "deploy viernes"])
    lib = ubicar_libreta(f["img"])
    assert lib is not None
    assert error_esquinas(lib["quad_px"], f["quad"]) < 14
    assert len(lib["paginas"]) == 2
    corte = lib["paginas"][1][0]
    assert abs(corte - 0.5) < 0.03


def test_girar_deja_la_esquina_de_la_persona_primero():
    # la libreta está girada un cuarto de vuelta en la foto; con girar=3 vuelve al orden de la persona
    f = s.foto(PRIMERAS, lado=1)
    lib = ubicar_libreta(f["img"], girar=3)
    esperado = np.roll(f["quad"], 1, axis=0)
    assert error_esquinas(lib["quad_px"], esperado) < 12


def test_sin_libreta_no_inventa():
    assert ubicar_libreta(s.luz(s.mesa(semilla=3), 3)) is None


@pytest.mark.skipif(not MESA_REAL.exists(), reason="falta la foto real de la mesa")
def test_foto_real_de_la_mesa_con_pista():
    # foto rectificada de la mesa real (1 px = 1 mm), con una libreta A5 sintética entre el canasto y el pastillero.
    # Ahí también están la caja blanca, la taza, las bolas y el engranaje: con la pista gana la libreta.
    mesa = cv2.imread(str(MESA_REAL))
    hoja = s.pagina()
    s.escribir(hoja, PRIMERAS)
    quad = np.float32([[478, 64], [624, 74], [614, 284], [468, 276]])
    img = s.poner(mesa, hoja, quad)
    lib = ubicar_libreta(img, cerca_de=[545, 175], area_min=0.01)
    assert lib is not None
    assert error_esquinas(lib["quad_px"], quad) < 10
    assert abs(lib["aspecto"] - 148 / 210) < 0.05


# ------------------------------------------------------------------ tinta


def test_tinta_ignora_renglones_y_margen():
    hoja = s.pagina()
    tinta = mascara_tinta(hoja)
    assert np.count_nonzero(tinta) == 0
    cajas = s.escribir(hoja, PRIMERAS)
    tinta = mascara_tinta(hoja)
    x, y, w, h = union(cajas)
    dentro = np.count_nonzero(tinta[y : y + h, x : x + w])
    assert dentro > 400
    assert np.count_nonzero(tinta) - dentro < 0.03 * dentro


def test_tinta_ignora_la_luz_proyectada():
    hoja = s.pagina()
    s.escribir(hoja, PRIMERAS)
    antes = mascara_tinta(hoja)
    con_luz = s.proyectar_luz(hoja, "usa /v1/cobros", (110, 260))
    ahora = mascara_tinta(con_luz)
    assert not lo_nuevo(antes, ahora)["hay_nuevo"]


# ------------------------------------------------------------------ lo nuevo


def _pagina_de(f, lib):
    return recorte_pagina(rectificar(f["img"], lib["quad_px"]), lib["paginas"][-1])


def test_lo_nuevo_encuentra_el_renglon_escrito():
    antes = s.foto(PRIMERAS, semilla=5)
    despues = s.foto(PRIMERAS + TERCERA, semilla=6)  # otra luz y otro ruido, mismo lugar
    la = ubicar_libreta(antes["img"])
    ld = ubicar_libreta(despues["img"])
    tam = (620, 880)
    ta = mascara_tinta(rectificar(antes["img"], la["quad_px"], tam))
    td = mascara_tinta(rectificar(despues["img"], ld["quad_px"], tam))
    n = lo_nuevo(ta, td)
    assert n["hay_nuevo"]
    assert iou(n["caja"], despues["cajas"][2]) > 0.5
    assert len(n["renglones"]) == 1


def test_sin_cambios_no_hay_nuevo_aunque_se_corra():
    f = s.foto(PRIMERAS, semilla=7)
    lib = ubicar_libreta(f["img"])
    tam = (620, 880)
    t1 = mascara_tinta(rectificar(f["img"], lib["quad_px"], tam))
    corrida = np.float32(lib["quad_px"]) + np.float32([3, -2])  # la detección tiembla unos píxeles
    t2 = mascara_tinta(rectificar(f["img"], corrida, tam))
    assert not lo_nuevo(t1, t2)["hay_nuevo"]


def test_mano_encima_se_detecta():
    f = s.foto(PRIMERAS, mano=True)
    lib = ubicar_libreta(f["img"])
    assert hay_mano(f["img"], lib["quad_px"])
    limpia = s.foto(PRIMERAS)
    assert not hay_mano(limpia["img"], ubicar_libreta(limpia["img"])["quad_px"])


# ------------------------------------------------------------------ espacio libre


def test_espacio_libre_debajo_de_lo_escrito_y_sin_tinta():
    hoja = s.pagina()
    cajas = s.escribir(hoja, PRIMERAS)
    tinta = mascara_tinta(hoja)
    alto, ancho = tinta.shape
    caja = [c / d for c, d in zip(union(cajas), (ancho, alto, ancho, alto))]
    r = espacio_libre(tinta, (0.6, 0.15), cerca_de=caja)
    assert r is not None and r["lado"] == "debajo"
    x, y, w, h = r["rect"]
    assert y >= caja[1] + caja[3]
    zona = tinta[int(y * alto) : int((y + h) * alto), int(x * ancho) : int((x + w) * ancho)]
    assert np.count_nonzero(zona) == 0


def test_espacio_libre_respeta_respuestas_previas_y_se_llena():
    hoja = s.pagina()
    tinta = mascara_tinta(hoja)
    usados = []
    for _ in range(12):
        r = espacio_libre(tinta, (0.8, 0.2), ocupado=usados, achicar_hasta=1.0)
        if r is None:
            break
        for u in usados:
            assert iou(r["rect"], u) == 0
        usados.append(r["rect"])
    assert 3 <= len(usados) < 12  # caben unas cuantas y después dice que no hay lugar


# ------------------------------------------------------------------ lector con memoria


def test_lector_de_punta_a_punta():
    lector = Lector()
    f1 = s.foto(PRIMERAS, semilla=10)
    m1 = lector.mirar(f1["img"])
    assert m1["estado"] == "nuevo"  # la primera vez todo es nuevo
    lector.confirmar()
    assert lector.mirar(s.foto(PRIMERAS, semilla=11)["img"])["estado"] == "igual"
    assert lector.mirar(s.foto(PRIMERAS + TERCERA, semilla=12, mano=True)["img"])["estado"] == "mano"
    f3 = s.foto(PRIMERAS + TERCERA, semilla=13)
    m3 = lector.mirar(f3["img"])
    assert m3["estado"] == "nuevo"
    assert iou(m3["nuevo_libreta"], f3["cajas"][2]) > 0.5
    assert m3["recorte"].shape[0] < m3["contexto"].shape[0] / 3
    h = lector.hueco((0.6, 0.12))
    assert h is not None and h["rect_libreta"][1] > m3["nuevo_libreta"][1]
    h2 = lector.hueco((0.6, 0.12))
    assert iou(h["rect_libreta"], h2["rect_libreta"]) == 0  # no escribe encima de la respuesta anterior


def test_lector_doble_pagina_elige_la_pagina_con_tinta_nueva():
    lector = Lector()
    lector.mirar(s.foto(PRIMERAS, doble_pagina=True, lineas_izq=["notas viejas"])["img"])
    lector.confirmar()
    f = s.foto(PRIMERAS + TERCERA, doble_pagina=True, lineas_izq=["notas viejas"], semilla=4)
    m = lector.mirar(f["img"])
    assert m["estado"] == "nuevo" and m["pagina"] == 1
    assert iou(m["nuevo_libreta"], f["cajas"][2]) > 0.4


# ------------------------------------------------------------------ geometría hacia la mesa


def test_de_la_libreta_a_mm_y_widget_girado():
    quad_mm = np.float32([[600, 250], [748, 262], [732, 470], [584, 458]])  # A5 algo girada sobre la mesa
    esquinas = rect_en(quad_mm, [0.1, 0.5, 0.8, 0.2])
    assert esquinas.shape == (4, 2)
    caja = caja_rotada(quad_mm)
    assert 4 < caja["rotate_deg"] < 6
    assert abs(caja["rect_mm"][2] - 148) < 2 and abs(caja["rect_mm"][3] - 208) < 3


def test_px_a_mm_igual_que_la_consola():
    H = np.float32([[0.8, 0, -100], [0, 0.8, -50], [0, 0, 1]]).ravel().tolist()
    calib = {"matrix_px_to_mm": H, "image_px": [1280, 720]}
    assert np.allclose(px_a_mm(calib, [[200, 100]]), [[60, 30]])
    # foto a media resolución: se escala como en el nodo
    assert np.allclose(px_a_mm(calib, [[100, 50]], image_px=[640, 360]), [[60, 30]])
    lente = {**calib, "lens": {"f": 1000, "cx": 640, "cy": 360, "k1": 0.0, "k2": 0.0}}
    assert np.allclose(px_a_mm(lente, [[200, 100]]), [[60, 30]])

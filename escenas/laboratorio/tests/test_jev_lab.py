"""jev_lab sin red: sin Jev devuelve None y una respuesta lenta no frena a nadie."""

import time

import numpy as np

import jev_lab as J


def test_sin_jev_todo_es_none(monkeypatch):
    monkeypatch.setattr(J, "_jv", None)
    monkeypatch.setattr(J, "_cargado", True)
    assert not J.disponible()
    mesa = np.zeros((300, 300, 3), np.uint8)
    assert J.confirmar_celular(mesa, [[10, 10], [80, 10], [80, 160], [10, 160]]) is None
    assert J.vaso_a_la_vista(mesa, 50) is None
    J.calentar()  # no hace nada


def test_una_respuesta_lenta_no_frena():
    t = time.perf_counter()
    assert J._con_limite(lambda: time.sleep(1.0) or 0.9, 0.1) is None
    assert time.perf_counter() - t < 0.5
    assert J._con_limite(lambda: 0.9, 1.0) == 0.9


def test_recorte_con_margen_y_dentro_de_la_foto():
    mesa = np.zeros((563, 1000, 3), np.uint8)
    r = J.recorte(mesa, [[10, 10], [80, 10], [80, 160], [10, 160]], margen_mm=40)
    assert r.shape[:2] == (200, 120)  # pegado al borde de arriba y de la izquierda
    jpg = J._jpg(r)
    assert jpg[:2] == b"\xff\xd8"

"""El contrato de MOTOR.md: al_limpiar(lienzo, ctx) y en_vivo(cuadro, ctx), con el estado en ctx["memoria"]."""

import time
from pathlib import Path

import cv2
import numpy as np

import percepcion as L
from percepcion import sintetico as S
from percepcion import vaso as V

FOTO = Path(__file__).parent / "datos" / "mesa-cenital-mm-1.jpg"


def test_al_limpiar_publica_el_pastillero():
    ctx = {"memoria": {}, "px_por_mm": 1.0}
    out = L.al_limpiar(cv2.imread(str(FOTO)), ctx)
    (obj,) = out["objetos"]
    assert obj["id"] == "lab:pastillero" and len(obj["polygon_mm"]) == 4
    celdas = out["datos"]["pastillero"]["celdas"]
    assert len(celdas) == 28 and celdas["A1"]["color"] == "blanco"
    assert np.hypot(*(np.array(celdas["A1"]["centro_mm"]) - (651, 209))) < 5


def test_al_limpiar_tapado_guarda_la_ultima():
    ctx = {"memoria": {}}
    L.al_limpiar(cv2.imread(str(FOTO)), ctx)
    tapado = np.full((563, 1000, 3), (150, 160, 170), np.uint8)
    out = L.al_limpiar(tapado, ctx)
    assert out["datos"]["pastillero"]["visto"] is False
    assert "celdas" in out["datos"]["pastillero"]


def _mem_con_calibracion(tmp_path):
    vacio = S.vaso(0, semilla=1).vacio
    cal = V.calibrar_con_lecturas(vacio, {50: S.vaso(50, semilla=2).cuadro, 150: S.vaso(150, semilla=3).cuadro})
    cv2.imwrite(str(tmp_path / "vaso-vacio.jpg"), vacio)
    ruta = tmp_path / "vaso.json"
    import json

    ruta.write_text(json.dumps({**cal.a_dict(), "vacio": "vaso-vacio.jpg"}))
    return {"vaso_cal": str(ruta)}


def test_en_vivo_sin_calibracion_no_inventa():
    assert L.en_vivo(S.vaso(40).cuadro, {"memoria": {}}) is None


def test_en_vivo_mide_avisa_y_no_inunda_de_eventos(tmp_path):
    mem = _mem_con_calibracion(tmp_path)
    mem.update({"objetivo_ml": 50, "tolerancia_ml": 3})
    ctx = {"memoria": mem}
    serie = [0, 0, 20, 35, 44, 48, 50, 50, 50, 50, 57, 58, 58, 58, 58, 58]
    eventos, estados = [], []
    for i, ml in enumerate(serie):
        out = L.en_vivo(S.vaso(ml, semilla=i).cuadro, ctx)
        estados.append(out["datos"]["vaso"]["estado"])
        eventos += out.get("eventos", [])
    assert estados[-1] == V.PASADO and V.LISTO in estados
    assert len(eventos) < len(serie)  # solo cuando cambia algo visible
    assert eventos[-1]["data"]["estado"] == V.PASADO and eventos[-1]["data"]["vaso"] == "lab:vaso"


def test_en_vivo_es_barato(tmp_path):
    ctx = {"memoria": _mem_con_calibracion(tmp_path)}
    cuadros = [S.vaso(40, semilla=i).cuadro for i in range(10)]
    L.en_vivo(cuadros[0], ctx)  # la primera carga la calibración
    t = time.perf_counter()
    for c in cuadros:
        L.en_vivo(c, ctx)
    assert (time.perf_counter() - t) / len(cuadros) < 0.030


def test_cambiar_el_objetivo_reinicia_el_aviso(tmp_path):
    mem = _mem_con_calibracion(tmp_path)
    ctx = {"memoria": {**mem, "objetivo_ml": 30}}
    for i in range(4):
        out = L.en_vivo(S.vaso(40, semilla=i).cuadro, ctx)
    assert out["datos"]["vaso"]["estado"] == V.PASADO
    ctx["memoria"]["objetivo_ml"] = 100
    for i in range(4):
        out = L.en_vivo(S.vaso(40, semilla=i).cuadro, ctx)
    assert out["datos"]["vaso"]["estado"] == V.FALTA


def test_al_limpiar_avisa_objeto_contaminado():
    from percepcion import intrusos as I

    ctx = {"memoria": {}}
    mesa = cv2.imread(str(FOTO))
    L.al_limpiar(mesa, ctx)  # la primera foto buena queda de referencia
    out = L.al_limpiar(I.poner_celular(mesa, (330, 470)), ctx)
    ids = [o["id"] for o in out["objetos"]]
    assert "lab:contaminado-1" in ids and "lab:pastillero" in ids
    assert out["eventos"] == [{"event": "contaminacion", "data": {"hay": True, "objetos": ["celular"]}}]
    out = L.al_limpiar(mesa, ctx)
    assert [o["id"] for o in out["objetos"]] == ["lab:pastillero"]
    assert out["eventos"][0]["data"]["hay"] is False

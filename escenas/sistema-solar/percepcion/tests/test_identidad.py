"""Ids estables entre armados: re-armar no hace que los objetos `solar:*` entren y salgan del mundo."""

from __future__ import annotations

import json

import cv2
import pytest
from conftest import BOLAS, foto

import conductor as C


class MesaFalsa:
    def __init__(self):
        self.objetos_publicados: list[list[str]] = []
        self.quitados: list[str] = []

    def llamar(self, tool, args, timeout=0):
        return ({"objects": [], "widgets": []}, [])

    def objetos(self, objs):
        self.objetos_publicados.append(sorted(o["id"] for o in objs))
        return {}

    def project(self, widgets):
        return {}

    def remove(self, ids):
        self.quitados += list(ids)
        return {}

    def emitir(self, evento, data):
        return True


@pytest.fixture
def mesa(tmp_path, monkeypatch):
    (tmp_path / "estado").mkdir()
    cfg = json.loads((C.Path(C.__file__).parent / "mesa.json").read_text(encoding="utf-8"))
    (tmp_path / "mesa.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(C, "ESTADO", tmp_path / "estado" / "estado.json")
    monkeypatch.setattr(C, "MESA", tmp_path / "mesa.json")
    m = MesaFalsa()
    m.foto = foto(1)
    monkeypatch.setattr(C, "foto_mm", lambda mesa, limpia, sim: (m.foto, {"surface_mm": [1000, 563], "calibrated": True}))
    return m


def _ids(m: MesaFalsa) -> list[str]:
    return [i for i in m.objetos_publicados[-1] if not i.startswith("solar:lamina")]


def _tapar(img, x, y, r):
    out = img.copy()
    cv2.circle(out, (x, y), int(r * 1.3), (60, 80, 100), -1)  # mimbre oscuro: ahí ya no hay nada blanco
    return out


def test_rearmar_igual_no_cambia_nada(mesa):
    a = C.armar(mesa, sim=False, intro=False)
    ids = _ids(mesa)
    assert "solar:sol" in ids and "solar:canasto" in ids
    b = C.armar(mesa, sim=False, intro=False)
    assert _ids(mesa) == ids
    assert a["asignacion"]["planetas"] == b["asignacion"]["planetas"]
    assert not b["fantasmas"]


def test_bola_tapada_queda_un_armado_y_despues_sale(mesa):
    a = C.armar(mesa, sim=False, intro=False)
    ids = _ids(mesa)
    x, y, r = BOLAS[5]
    i = next(j for j, c in enumerate(a["cuerpos"]) if abs(c["centro_mm"][0] - x) < 8 and abs(c["centro_mm"][1] - y) < 8)
    pid = next(p for p, j in a["asignacion"]["planetas"].items() if j == i)
    mesa.foto = _tapar(mesa.foto, x, y, r)
    b = C.armar(mesa, sim=False, intro=False)
    assert _ids(mesa) == ids  # sigue: puede ser una mano o un reflejo
    assert list(b["fantasmas"].values()) == [1]
    otros = {p: a["cuerpos"][j]["centro_mm"] for p, j in a["asignacion"]["planetas"].items() if p != pid}
    assert {p: b["cuerpos"][b["asignacion"]["planetas"][p]]["centro_mm"] for p in otros} == otros  # nadie cambió de bola
    C.armar(mesa, sim=False, intro=False)
    assert f"solar:{pid}" not in _ids(mesa)  # dos armados sin verla: salió de verdad
    assert f"solar-planeta-{pid}" in mesa.quitados


def test_bola_nueva_no_le_cambia_el_planeta_a_las_demas(mesa):
    x, y, r = BOLAS[5]
    mesa.foto = _tapar(foto(1), x, y, r)
    a = C.armar(mesa, sim=False, intro=False)
    mesa.foto = foto(1)  # aparece una bola
    b = C.armar(mesa, sim=False, intro=False)
    for p, j in a["asignacion"]["planetas"].items():
        assert b["cuerpos"][b["asignacion"]["planetas"][p]]["centro_mm"] == a["cuerpos"][j]["centro_mm"]
    assert len(b["asignacion"]["planetas"]) == len(a["asignacion"]["planetas"]) + 1


def test_sol_tapado_queda_un_armado_y_despues_sale(mesa):
    from conftest import ENGRANAJE

    C.armar(mesa, sim=False, intro=False)
    mesa.foto = _tapar(mesa.foto, *ENGRANAJE)
    b = C.armar(mesa, sim=False, intro=False)
    assert "solar:sol" in _ids(mesa) and b["asignacion"]["sol"] is not None
    c = C.armar(mesa, sim=False, intro=False)
    assert "solar:sol" not in _ids(mesa) and c["asignacion"]["sol"] is None
    assert "solar-sol" in mesa.quitados
    assert C.widget_orbitas_de(c, False)["anchor"] == {"object_id": "solar:canasto"}

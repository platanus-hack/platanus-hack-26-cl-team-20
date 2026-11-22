"""Capa visual del Sistema solar: reglas de la mesa (reservas, centrado, radio exacto, contorno, envíos).

    uv run pytest visual/tests
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ESCENA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ESCENA))

import visual as V  # noqa: E402

SUP = (1000, 563)
SOL = {"center_mm": (600.0, 240.0), "radius_mm": 45.0}
PLANETAS = {
    "mercurio": {"center_mm": (540.0, 250.0), "radius_mm": 14.0},
    "tierra": {"center_mm": (660.0, 320.0), "radius_mm": 20.0},
    "jupiter": {"center_mm": (480.0, 360.0), "radius_mm": 50.0},
    "neptuno": {"center_mm": (760.0, 120.0), "radius_mm": 22.0},  # cerca de la esquina de voz
}
CANASTO = [[420, 120], [790, 120], [790, 420], [420, 420]]


def _rect(w: dict, centros: dict) -> tuple[float, float, float, float]:
    if "rect_mm" in w:
        return tuple(w["rect_mm"])
    sw, sh = w["params"]["size_mm"]
    cx, cy = centros[w["anchor"]["object_id"]]
    ox, oy = w["anchor"].get("offset_mm", (0, 0))
    return (cx + ox - sw / 2, cy + oy - sh / 2, sw, sh)


def _pisa(r, z) -> bool:
    return r[0] < z[0] + z[2] - 0.05 and r[0] + r[2] > z[0] + 0.05 and r[1] < z[1] + z[3] - 0.05 and r[1] + r[3] > z[1] + 0.05


def _centros(sol=SOL, planetas=PLANETAS, centro=None):
    c = {V.E.oid(p): d["center_mm"] for p, d in planetas.items()}
    if sol:
        c[V.E.oid("sol")] = sol["center_mm"]
    if centro:
        c[V.E.oid("centro")] = centro
    return c


@pytest.mark.parametrize("girada", [False, True])
def test_fondo_y_orbitas_no_pisan_la_interfaz(girada):
    ws = V.widgets(SOL, PLANETAS, SUP, girada=girada, canasto_mm=CANASTO)
    centros = _centros()
    zonas = V.reservas(SUP, girada)
    voz = [0, SUP[1] - 130, 130, 130] if girada else [870, 0, 130, 130]
    assert voz in [list(z) for z in zonas]
    for w in ws:
        if w["id"].startswith(("solar-fondo", "solar-orbitas")):
            r = _rect(w, centros)
            assert not any(_pisa(r, z) for z in zonas), (w["id"], r)
            assert r[0] >= 0 and r[1] >= 0 and r[0] + r[2] <= SUP[0] + 0.1 and r[1] + r[3] <= SUP[1] + 0.1


def test_pista_busca_un_hueco_al_lado_del_planeta():
    jupiter = PLANETAS["jupiter"]["center_mm"]
    planetas = [[400, 450, 410, 73]]
    zonas = V.reservas(SUP)
    widget = V.E.widget_burbuja({"id": "lumo", "nombre": "Lumo"}, "Encuentra Júpiter", 50, (0, 1),
                                 jupiter, SUP, junto_a="jupiter", reservado=zonas, objetos=planetas)
    assert not any(_pisa(_rect(widget, _centros()), z) for z in zonas + planetas)
    assert "wui-pista" in widget["params"]["html"]



def test_fondo_cubre_la_zona_libre():
    grande, columna = V.zonas_libres(SUP)
    assert grande == [150, 0, 720, SUP[1] - V.FRANJA_MM]
    assert columna == [870, 130, 130, SUP[1] - V.FRANJA_MM - 130]


def test_orbitas_centradas_en_el_sol_aunque_se_recorten():
    ws = V.widgets(SOL, PLANETAS, SUP, canasto_mm=CANASTO)
    orb = next(w for w in ws if w["id"] == "solar-orbitas")
    assert orb["anchor"]["object_id"] == V.E.oid("sol")
    x0, y0, _, _ = _rect(orb, _centros())
    cx, cy = (float(v) for v in re.search(r'class="t1" cx="([\d.]+)" cy="([\d.]+)"', orb["params"]["html"]).groups())
    assert abs(x0 + cx - SOL["center_mm"][0]) < 0.2 and abs(y0 + cy - SOL["center_mm"][1]) < 0.2
    radios = sorted(float(r) for r in re.findall(r'class="t1" [^>]*r="([\d.]+)"', orb["params"]["html"]))
    esperados = sorted(math.dist(SOL["center_mm"], p["center_mm"]) for p in PLANETAS.values())
    assert radios == pytest.approx(esperados, abs=0.1)
    assert "fluye" in orb["params"]["css"]  # con Sol, se animan (seguir las descuenta)


def test_sin_sol_quietas_y_ancladas_a_solar_canasto():
    centro = (605.0, 270.0)
    ws = V.widgets(None, PLANETAS, SUP, canasto_mm=CANASTO, centro=centro, ancla_canasto=centro, intro=False)
    assert not any(w["id"] == "solar-sol" for w in ws)
    orb = next(w for w in ws if w["id"] == "solar-orbitas")
    assert orb["anchor"]["object_id"] == V.E.oid("canasto") == "solar:canasto"
    x0, y0, _, _ = _rect(orb, {V.E.oid("canasto"): centro})
    cx, cy = (float(v) for v in re.search(r'class="t1" cx="([\d.]+)" cy="([\d.]+)"', orb["params"]["html"]).groups())
    assert (x0 + cx, y0 + cy) == pytest.approx(centro, abs=0.2)
    assert "animation" not in re.sub(r"@keyframes[^}]*}}", "", orb["params"]["css"].split("--solar-sol-4")[1])
    assert 'class="calma"' in orb["params"]["html"]


def test_sol_viejo_fuera_del_canasto_no_centra_nada():
    viejo = {"center_mm": (300.0, 500.0), "radius_mm": 45.0}
    ws = V.widgets(viejo, PLANETAS, SUP, canasto_mm=CANASTO)
    assert not any(w["id"] == "solar-sol" for w in ws)
    orb = next(w for w in ws if w["id"] == "solar-orbitas")
    x0, y0, _, _ = _rect(orb, _centros(sol=viejo))
    cx, cy = (float(v) for v in re.search(r'class="t1" cx="([\d.]+)" cy="([\d.]+)"', orb["params"]["html"]).groups())
    assert (x0 + cx, y0 + cy) == pytest.approx((605.0, 270.0), abs=0.2)  # el centro del canasto


def test_esfera_exacta_sobre_su_bola():
    for pid, p in PLANETAS.items():
        w = V.esfera(V.PLANETAS[pid], p["radius_mm"], (1, 0))
        assert w["anchor"] == {"object_id": V.E.oid(pid)}  # sin offset
        lado = w["params"]["size_mm"][0]
        m = re.search(r'class="pl[^"]*" style="left:([\d.]+)px;top:([\d.]+)px;width:([\d.]+)px', w["params"]["html"])
        left, top, d = (float(v) for v in m.groups())
        assert d == pytest.approx(2 * p["radius_mm"], abs=0.1)
        assert left + d / 2 == pytest.approx(lado / 2, abs=0.1) and top + d / 2 == pytest.approx(lado / 2, abs=0.1)


def test_fantasma_atenuado_y_quieto_sin_sacarlo():
    ws = V.widgets(SOL, PLANETAS, SUP, canasto_mm=CANASTO, fantasmas={"tierra"})
    ids = [w["id"] for w in ws]
    assert "solar-planeta-tierra" in ids  # sigue en la mesa
    t = next(w for w in ws if w["id"] == "solar-planeta-tierra")
    j = next(w for w in ws if w["id"] == "solar-planeta-jupiter")
    assert 'class="pl fantasma"' in t["params"]["html"] and ".fantasma .tex{animation:none}" in t["params"]["css"]
    assert "fantasma" not in j["params"]["html"]
    orb = next(w for w in ws if w["id"] == "solar-orbitas")
    assert orb["params"]["html"].count('class="o fant"') == 1


def test_fantasmas_desde_el_estado_de_h20():
    est = {"asignacion": {"planetas": {"tierra": 2, "jupiter": 0}}, "fantasmas": {"2": 1}}
    assert V.fantasmas_de(est) == {"tierra"}
    assert V.fantasmas_de({"asignacion": {"planetas": {"tierra": 2}}}) == set()


def _foto_engranaje(c=(300, 200), r_int=40, r_ext=50, dientes=16):
    img = np.full((400, 600, 3), (120, 125, 128), np.uint8)
    pts = []
    for k in range(dientes * 4):
        a = 2 * math.pi * k / (dientes * 4)
        rr = r_ext if (k % 4) in (1, 2) else r_int
        pts.append((c[0] + rr * math.cos(a), c[1] + rr * math.sin(a)))
    cv2.fillPoly(img, [np.array(pts, np.int32)], (245, 245, 245))
    return img


def test_contorno_real_del_engranaje():
    rel = V.contorno_engranaje(_foto_engranaje(), (300, 200), 50)
    assert rel is not None and len(rel) == 144
    radios = [math.hypot(x, y) for x, y in rel]
    assert max(radios) > 47 and min(radios) < 43  # se ven los dientes, no un círculo
    abs_ = V.girar_contorno(rel, (500, 300), 90)
    x, y = rel[0]
    assert abs_[0] == pytest.approx([500 - y, 300 + x], abs=0.2)
    w = V.sol(50, abs_, (500, 300))
    assert w["params"]["html"].count("<polygon") == 2 and "clip-path:polygon(" in w["params"]["css"]


def test_contorno_sin_engranaje_es_none():
    img = np.full((400, 600, 3), (120, 125, 128), np.uint8)
    assert V.contorno_engranaje(img, (300, 200), 50) is None


def test_solo_se_envia_lo_que_cambia(monkeypatch):
    import conductor
    import visual.conductor_visual as CV

    enviados = []
    monkeypatch.setattr(CV, "_project", lambda self, ws: enviados.append([w["id"] for w in ws]) or {"ok": True})
    monkeypatch.setattr(CV, "_remove", lambda self, ids: {"ok": True})
    CV._enviado.clear()
    mesa = object.__new__(conductor.Mesa)
    ws = V.widgets(SOL, PLANETAS, SUP, canasto_mm=CANASTO)
    lamina_h20 = {"id": "solar-lamina", "rect_mm": [760, 270, 180, 120], "params": {"rotate_deg": 8.0, "html": ""}, "z": 2}
    CV.project(mesa, ws + [lamina_h20])
    assert len(enviados[-1]) == len(ws) + 1
    CV.project(mesa, ws + [lamina_h20])
    assert enviados[-1] == ["solar-lamina"]  # lo de h20 siempre pasa; lo mío igual, no
    CV.remove(mesa, ["solar-sol"])
    CV.project(mesa, ws)
    assert enviados[-1] == ["solar-sol"]
    assert json.loads(CV._enviado["solar-sol"])["id"] == "solar-sol"


def test_fantasma_que_vuelve_se_redibuja_solo(monkeypatch):
    import conductor
    import visual.conductor_visual as CV

    proyectados = []
    monkeypatch.setattr(CV, "_publicar_h20", lambda mesa, est: None)
    monkeypatch.setattr(CV, "_capa", lambda est, intro: V.widgets(SOL, PLANETAS, SUP, canasto_mm=CANASTO, fantasmas=V.fantasmas_de(est)))
    mesa = type("M", (), {"project": lambda self, ws: proyectados.append([w["id"] for w in ws])})()
    est = {"asignacion": {"planetas": {"tierra": 2, "jupiter": 0}}, "fantasmas": {"2": 1}}
    CV._fantasmas_dibujados = {"tierra"}
    CV.publicar_objetos(mesa, est)  # nada cambió
    assert proyectados == []
    est["fantasmas"] = {}  # seguir la volvió a ver
    CV.publicar_objetos(mesa, est)
    assert proyectados == [["solar-planeta-tierra", "solar-orbitas"]]
    assert CV._fantasmas_dibujados == set()
    CV._dentro_de_proyectar.si = True  # dentro de proyectar_todo no se adelanta
    est["fantasmas"] = {"2": 1}
    CV.publicar_objetos(mesa, est)
    assert len(proyectados) == 1
    CV._dentro_de_proyectar.si = False
    assert conductor.publicar_objetos is CV.publicar_objetos and conductor.proyectar_todo is CV.proyectar_todo


# ---------------------------------------------------------------- camino B: dibujar sobre los objetos del motor


def _circulo_obj(oid, c, r, n=24):
    return {"id": oid, "center_mm": list(c), "polygon_mm": [[c[0] + r * math.cos(2 * math.pi * k / n), c[1] + r * math.sin(2 * math.pi * k / n)] for k in range(n)]}


def _mundo(con_sol=True):
    objs = [
        _circulo_obj("solar:jupiter", (480, 360), 48),
        _circulo_obj("solar:tierra", (662, 318), 19),
        _circulo_obj("solar:lamina-0", (850, 330), 60),  # no es planeta
        _circulo_obj("m01", (300, 300), 30),  # objeto genérico del motor
        {"id": "solar:canasto", "center_mm": [605, 270], "polygon_mm": CANASTO},
    ]
    if con_sol:
        objs.append(_circulo_obj("solar:sol", (603, 238), 46))
    return {o["id"]: o for o in objs}


def test_camino_b_planetas_sobre_los_objetos_del_motor():
    ws = V.desde_mundo(_mundo(), SUP, intro=False)
    pl = {w["id"]: w for w in ws if w["id"].startswith("solar-planeta-")}
    assert set(pl) == {"solar-planeta-jupiter", "solar-planeta-tierra"}  # solo lo que publica el motor
    j = pl["solar-planeta-jupiter"]
    assert j["anchor"] == {"object_id": "solar:jupiter"}  # anclado, sin offset
    d = float(re.search(r'class="pl[^"]*" style="[^"]*width:([\d.]+)px', j["params"]["html"]).group(1))
    assert d == pytest.approx(96, abs=0.3)  # del tamaño de la silueta del motor, no de la detección de Python
    clip = re.search(r"\.pl\{[^}]*clip-path:polygon\(([^)]*)\)", j["params"]["css"]).group(1)
    assert clip.count("px") == 2 * 24  # recortada a la silueta (24 puntos)


def test_camino_b_sol_con_dientes_y_orbitas_en_el_sol_del_motor():
    dientes = [[(50 if k % 2 else 42) * math.cos(2 * math.pi * k / 64), (50 if k % 2 else 42) * math.sin(2 * math.pi * k / 64)] for k in range(64)]
    ws = V.desde_mundo(_mundo(), SUP, intro=False, sol_dientes_rel=dientes)
    sol = next(w for w in ws if w["id"] == "solar-sol")
    assert sol["anchor"] == {"object_id": "solar:sol"}
    assert sol["params"]["html"].count(" ") > 64  # el polígono con dientes, no el de 24 lados
    lado = sol["params"]["size_mm"][0]
    assert lado == pytest.approx(2.6 * 50, abs=0.2)
    orb = next(w for w in ws if w["id"] == "solar-orbitas")
    assert orb["anchor"]["object_id"] == "solar:sol"
    centros = {"solar:sol": (603, 238)}
    x0, y0, _, _ = _rect(orb, centros)
    cx, cy = (float(v) for v in re.search(r'class="t1" cx="([\d.]+)" cy="([\d.]+)"', orb["params"]["html"]).groups())
    assert (x0 + cx, y0 + cy) == pytest.approx((603, 238), abs=0.2)
    radios = sorted(float(r) for r in re.findall(r'class="t1" [^>]*r="([\d.]+)"', orb["params"]["html"]))
    assert radios == pytest.approx(sorted([math.dist((603, 238), (480, 360)), math.dist((603, 238), (662, 318))]), abs=0.1)


def test_camino_b_sin_sol_orbitas_en_solar_canasto():
    ws = V.desde_mundo(_mundo(con_sol=False), SUP, intro=False)
    assert not any(w["id"] == "solar-sol" for w in ws)
    orb = next(w for w in ws if w["id"] == "solar-orbitas")
    assert orb["anchor"]["object_id"] == "solar:canasto"
    assert "fluye" not in orb["params"]["css"]


def test_camino_b_el_conductor_lee_el_mundo(monkeypatch):
    import visual.conductor_visual as CV

    monkeypatch.setenv("PCX_SIN_OBJETOS", "1")
    objetos = list(_mundo().values())
    mesa = type("M", (), {"llamar": lambda self, tool, args, t=60: ({"objects": objetos}, [])})()
    mundo = CV.leer_mundo(mesa)
    assert set(mundo) == {"solar:jupiter", "solar:tierra", "solar:lamina-0", "solar:canasto", "solar:sol"}
    est = {"asignacion": {"sol": None, "planetas": {}}, "cuerpos": [], "surface_mm": [1000, 563], "pose": {"theta_deg": 0, "t_mm": [0, 0]}}
    monkeypatch.setattr(CV.conductor, "centros_actuales", lambda e: {})
    ids = [w["id"] for w in CV._capa(est, intro=False)]
    assert {"solar-planeta-jupiter", "solar-planeta-tierra", "solar-sol", "solar-orbitas"} <= set(ids)
    CV._mundo = None

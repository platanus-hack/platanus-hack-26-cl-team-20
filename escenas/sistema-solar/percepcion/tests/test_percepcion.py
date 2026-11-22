"""Tests sobre las 4 fotos reales de la mesa y sobre movimientos sintéticos del canasto."""

from __future__ import annotations

import math

import numpy as np
import pytest
from conftest import BOLAS, ENGRANAJE, foto, mover_mesa

from percepcion import (
    Cuerpo,
    aplicar_pose,
    asignar_planetas,
    cargar_catalogo,
    detectar_cuerpos,
    espacio_libre,
    mesa_a_lamina,
    movimiento,
    planetas_en_lamina,
    pose_grupo,
    ubicar_canasto,
    ubicar_laminas,
)


def _cerca(c: Cuerpo, x: float, y: float, tol: float = 5.0) -> bool:
    return math.hypot(c.centro_mm[0] - x, c.centro_mm[1] - y) <= tol


# ---------- canasto y cuerpos ----------


def test_canasto_en_fotos_reales(foto_real):
    k = ubicar_canasto(foto_real)
    assert k is not None
    assert math.hypot(k.centro_mm[0] - 308, k.centro_mm[1] - 215) < 10
    assert 240 < min(k.tam_mm) and max(k.tam_mm) < 300


def test_detecta_sol_y_nueve_bolas(foto_real):
    k = ubicar_canasto(foto_real)
    cs = detectar_cuerpos(foto_real, k.zona())
    eng = [c for c in cs if c.tipo == "engranaje"]
    bolas = [c for c in cs if c.tipo == "bola"]
    assert len(eng) == 1 and _cerca(eng[0], ENGRANAJE[0], ENGRANAJE[1])
    assert abs(eng[0].radio_mm - ENGRANAJE[2]) < 6
    assert len(bolas) == len(BOLAS), [(c.centro_mm, c.radio_mm) for c in bolas]
    for x, y, r in BOLAS:
        hit = [c for c in bolas if _cerca(c, x, y)]
        assert hit, f"falta la bola de ({x}, {y})"
        assert abs(hit[0].radio_mm - r) <= 3, f"radio de ({x}, {y}): {hit[0].radio_mm} vs {r}"


def test_sin_zona_no_confunde_la_taza_ni_la_caja():
    cs = detectar_cuerpos(foto(1))
    lejos = [c for c in cs if c.centro_mm[1] > 380 or c.centro_mm[0] > 600]  # taza, caja, pastillero
    assert not lejos, [(c.centro_mm, c.radio_mm) for c in lejos]


def test_sol_con_histeresis():
    # Un umbral de redondez imposible: el Sol solo se acepta donde estaba antes (sol_previo), nunca en otro lado.
    duro = {"redondez_min": 0.999, "engranaje_redondez_min": 0.999}
    assert not [c for c in detectar_cuerpos(foto(1), params=duro) if c.tipo == "engranaje"]
    cs = detectar_cuerpos(foto(1), params=duro, sol_previo=ENGRANAJE[:2])
    sol = [c for c in cs if c.tipo == "engranaje"]
    assert len(sol) == 1 and math.dist(sol[0].centro_mm, ENGRANAJE[:2]) < 6
    lejos = detectar_cuerpos(foto(1), params=duro, sol_previo=(ENGRANAJE[0] + 200, ENGRANAJE[1]))
    assert not [c for c in lejos if c.tipo == "engranaje"]


def test_traza_explica_descartes():
    traza: list = []
    detectar_cuerpos(foto(1), traza=traza)
    assert traza and all("motivo" in t for t in traza)


# ---------- planetas ----------


def _cuerpos_reales() -> list[Cuerpo]:
    return [Cuerpo("engranaje", ENGRANAJE[:2], ENGRANAJE[2])] + [Cuerpo("bola", (x, y), r) for x, y, r in BOLAS]


def test_asigna_por_tamano_con_dudas():
    cat = cargar_catalogo()
    cs = _cuerpos_reales()
    a = asignar_planetas(cs, cat)
    assert a.sol == 0
    assert a.planetas["jupiter"] == 1  # la más grande
    assert a.planetas["saturno"] == 2
    assert len(a.planetas) == 9 and "pluton" in a.planetas  # 9 bolas: entra el planeta enano
    assert not a.sobrantes
    dudosas = {d["cuerpo"] for d in a.dudas}
    assert dudosas, "15, 15, 14 y 13 mm deberían quedar como duda"
    assert 1 not in dudosas  # Júpiter no tiene competencia


def test_duda_se_desempata_por_distancia_al_sol():
    cat = cargar_catalogo()
    # Dos bolas iguales: la lejana va primero en la lista para que el tamaño no decida por casualidad.
    cs = [Cuerpo("engranaje", (0, 0), 30), Cuerpo("bola", (200, 0), 15), Cuerpo("bola", (60, 0), 15)]
    a = asignar_planetas(cs, cat)
    assert a.planetas == {"jupiter": 2, "saturno": 1}  # Júpiter es más interior que Saturno
    assert {d["cuerpo"]: d["asignado"] for d in a.dudas} == {2: "jupiter", 1: "saturno"}
    cs[1], cs[2] = Cuerpo("bola", (60, 0), 15), Cuerpo("bola", (200, 0), 15)
    assert asignar_planetas(cs, cat).planetas == {"jupiter": 1, "saturno": 2}


def test_fijado_a_mano_manda():
    cat = cargar_catalogo()
    cs = _cuerpos_reales()
    a = asignar_planetas(cs, cat, fijos={"tierra": 4})
    assert a.planetas["tierra"] == 4
    assert len(set(a.planetas.values())) == len(a.planetas)  # ninguna bola con dos planetas


def test_menos_bolas_que_planetas():
    cat = cargar_catalogo()
    cs = [Cuerpo("bola", (0, i * 100), r) for i, r in enumerate([40, 30, 10])]
    a = asignar_planetas(cs, cat)
    assert a.planetas == {"jupiter": 0, "saturno": 1, "urano": 2}


# ---------- pose del grupo ----------


@pytest.mark.parametrize("theta,t", [(0, (0, 0)), (12, (35, -20)), (-30, (-60, 40)), (90, (10, 10)), (180, (0, 0))])
def test_pose_sobre_puntos(theta, t):
    ref = _cuerpos_reales()
    pts = aplicar_pose([c.centro_mm for c in ref], (theta, t))
    act = [Cuerpo(c.tipo, tuple(p), c.radio_mm) for c, p in zip(ref, pts)]
    act = act[::-1]  # el orden no importa
    p = pose_grupo(ref, act)
    assert p.ok and p.inliers == len(ref)
    assert abs(((p.theta_deg - theta + 180) % 360) - 180) < 0.5
    assert np.allclose(p.t_mm, t, atol=1.0)


def test_pose_con_bola_tapada_y_una_de_mas():
    ref = _cuerpos_reales()
    pts = aplicar_pose([c.centro_mm for c in ref], (25, (40, 15)))
    act = [Cuerpo(c.tipo, tuple(p), c.radio_mm) for c, p in zip(ref, pts)]
    del act[3]  # una mano tapa una bola
    act.append(Cuerpo("bola", (700, 100), 20))  # algo nuevo en la mesa
    p = pose_grupo(ref, act)
    assert p.ok and p.inliers == len(ref) - 1
    assert abs(p.theta_deg - 25) < 1 and np.allclose(p.t_mm, (40, 15), atol=2)


def test_pose_con_ruido_de_deteccion():
    rng = np.random.default_rng(7)
    ref = _cuerpos_reales()
    pts = aplicar_pose([c.centro_mm for c in ref], (-15, (-25, 30))) + rng.normal(0, 2.0, (len(ref), 2))
    act = [Cuerpo(c.tipo, tuple(p), c.radio_mm + rng.normal(0, 1.5)) for c, p in zip(ref, pts)]
    p = pose_grupo(ref, act)
    assert p.ok and abs(p.theta_deg + 15) < 2 and np.allclose(p.t_mm, (-25, 30), atol=5)


@pytest.mark.parametrize("theta,t", [(15, (40, 30)), (-25, (60, -10)), (40, (30, 50)), (90, (0, 0))])
def test_pose_de_punta_a_punta_moviendo_la_foto(theta, t):
    """Mueve la foto real (como si alguien moviera el canasto), detecta de nuevo y recupera el movimiento."""
    img = foto(1)
    k0 = ubicar_canasto(img)
    ref = detectar_cuerpos(img, k0.zona())
    c = k0.centro_mm
    movida = mover_mesa(img, theta, t, c)  # gira alrededor del centro del canasto y lo traslada
    k1 = ubicar_canasto(movida)
    assert k1 is not None
    act = detectar_cuerpos(movida, k1.zona())
    p = pose_grupo(ref, act)
    # Girar en torno a c y trasladar t equivale a R·p + (c − R·c + t).
    esperado = np.asarray(c) - aplicar_pose([c], (theta, (0, 0)))[0] + np.asarray(t)
    assert p.ok, p
    assert p.inliers >= 8
    assert abs(p.theta_deg - theta) < 2.0, p
    assert np.allclose(p.t_mm, esperado, atol=6), (p, esperado)


# ---------- lámina ----------


def test_lamina_en_fotos_reales(foto_real):
    ls = ubicar_laminas(foto_real)
    assert len(ls) == 1  # la segunda copia queda fuera de la superficie
    lam = ls[0]
    assert math.hypot(lam.centro_mm[0] - 541, lam.centro_mm[1] - 377) < 8
    assert 150 < lam.tam_mm[0] < 180 and 95 < lam.tam_mm[1] < 120
    # En estas fotos el Sol impreso está a la derecha (+x de la mesa).
    assert abs(((lam.angulo_deg + 180) % 360) - 180) < 20


def test_planetas_impresos_caen_sobre_color():
    img = foto(1)
    lam = ubicar_laminas(img)[0]
    pts = planetas_en_lamina(lam)
    assert set(pts) >= {"mercurio", "venus", "tierra", "marte", "jupiter", "saturno", "urano", "neptuno", "sol"}
    hsv = __import__("cv2").cvtColor(img, __import__("cv2").COLOR_BGR2HSV)
    for pid in ("sol", "jupiter", "saturno", "marte"):
        x, y = (int(round(v)) for v in pts[pid]["centro_mm"])
        assert hsv[y, x, 2] > 100, f"{pid} cae sobre fondo oscuro"


def test_lamina_girada_180():
    img = foto(1)
    girada = img[::-1, ::-1].copy()
    lam = ubicar_laminas(girada)[0]
    assert abs(abs(lam.angulo_deg) - 180) < 20  # el Sol ahora queda a la izquierda
    sol = planetas_en_lamina(lam)["sol"]["centro_mm"]
    assert sol[0] < lam.centro_mm[0]
    # ida y vuelta entre mesa y lámina
    uv = mesa_a_lamina(lam, [sol])[0]
    assert np.allclose(uv, [0.911, 0.459], atol=2e-3)  # el centro sale redondeado a 0,1 mm


# ---------- mesa ----------


def test_espacio_libre_no_pisa_el_canasto():
    img = foto(1)
    k = ubicar_canasto(img)
    r = espacio_libre(img, (160, 110), evitar_mm=[k.zona(0)], cerca_de_mm=(500, 150))
    assert r is not None
    x, y, w, h = r
    zx, zy, zw, zh = k.zona(0)
    assert x >= zx + zw or x + w <= zx or y >= zy + zh or y + h <= zy


def test_movimiento():
    img = foto(1)
    assert movimiento(img, img) == 0.0
    assert movimiento(img, mover_mesa(img, 5, (20, 0), (300, 200))) > 0.02


# ---------- contrato del motor ----------


def test_al_limpiar_arma_y_sigue_el_canasto():
    from percepcion import al_limpiar

    img = foto(1)
    ctx: dict = {"px_por_mm": 1.0}
    r1 = al_limpiar(img, ctx)
    ids = {o["id"] for o in r1["objetos"]}
    assert {"solar:sol", "solar:jupiter", "solar:saturno", "solar:lamina-0"} <= ids
    assert [e["event"] for e in r1["eventos"]] == ["solar_armado"]
    k = ubicar_canasto(img)
    movida = mover_mesa(img, 18, (30, 20), k.centro_mm)
    r2 = al_limpiar(movida, ctx)
    assert [e["event"] for e in r2["eventos"]] == ["canasto_movido"]
    assert abs(r2["datos"]["pose"]["theta_deg"] - 18) < 2
    jup1 = next(o for o in r1["objetos"] if o["id"] == "solar:jupiter")["center_mm"]
    jup2 = next(o for o in r2["objetos"] if o["id"] == "solar:jupiter")["center_mm"]
    esperado = aplicar_pose([jup1], (18, tuple(np.asarray(k.centro_mm) - aplicar_pose([k.centro_mm], (18, (0, 0)))[0] + (30, 20))))[0]
    assert np.allclose(jup2, esperado, atol=4)
    r3 = al_limpiar(movida, ctx)  # mismo cuadro: sin evento de movimiento
    assert r3["eventos"] == []


def test_al_limpiar_mide_tiempo():
    import time

    from percepcion import al_limpiar

    img = foto(2)
    ctx: dict = {}
    al_limpiar(img, ctx)
    t0 = time.perf_counter()
    al_limpiar(img, ctx)
    ms = (time.perf_counter() - t0) * 1000
    print(f"al_limpiar: {ms:.0f} ms por foto limpia")
    assert ms < 1500  # foto limpia (no es la cámara lateral de 30 ms); en Python, con margen

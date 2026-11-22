from pathlib import Path

import cv2
import numpy as np
import pytest

from percepcion import pastillero as P
from percepcion import sintetico as S

CAPTURAS = Path("/Volumes/Externo/superhuman-desk/capturas")
REALES = sorted(CAPTURAS.glob("mesa-cenital-mm-*.jpg")) if CAPTURAS.exists() else []
# Copia de una captura dentro del repo, para que el test corra también fuera de este Mac.
COPIA = Path(__file__).parent / "datos" / "mesa-cenital-mm-1.jpg"


def _reales():
    return REALES or ([COPIA] if COPIA.exists() else [])


@pytest.mark.parametrize("ruta", _reales(), ids=lambda p: p.name)
def test_capturas_reales(ruta):
    g = P.detectar(cv2.imread(str(ruta)))
    assert g.ok, g.motivo
    assert g.confianza >= 0.85
    assert len(g.celdas) == 28
    # Medido a mano sobre la captura: A1 (columna blanca, fila del borde) cerca de (651, 209) mm, caja girada ~ -28°.
    assert np.hypot(*(np.array(g.celda("A1").centro_mm) - (651, 209))) < 5
    assert abs(g.angulo_deg - (-28)) < 3
    assert 20 < g.paso_mm < 24
    assert [g.celda(f"A{c}").color for c in range(1, 8)] == list(P.PALETA_DEMO)


@pytest.mark.parametrize("angulo", [-28, 0, 40, 90, 150, -120, 179])
def test_sintetico_cualquier_giro(angulo):
    img, verdad = S.pastillero(angulo_deg=angulo, centro_mm=(320, 260), semilla=angulo % 7)
    g = P.detectar(img)
    assert g.ok, g.motivo
    # La dirección queda atada al objeto: A1 y D7 están donde los puso el generador, gire como gire la caja.
    assert np.hypot(*(np.array(g.celda("A1").centro_mm) - verdad["A1"])) < 5
    assert np.hypot(*(np.array(g.celda("D7").centro_mm) - verdad["D7"])) < 5


def test_sin_paleta_usa_la_columna_blanca():
    img, verdad = S.pastillero(angulo_deg=-28, centro_mm=(600, 250))
    g = P.detectar(img, paleta=None)
    assert g.ok
    assert np.hypot(*(np.array(g.celda("A1").centro_mm) - verdad["A1"])) < 6
    assert g.celda("A1").color == "blanco"


def test_mesa_vacia_no_inventa():
    mesa = np.full((563, 1000, 3), (150, 160, 170), np.uint8)
    g = P.detectar(mesa)
    assert not g.ok
    assert g.motivo


def test_region_y_escala():
    img, verdad = S.pastillero(angulo_deg=20, centro_mm=(700, 300))
    # Media resolución: 1 px = 2 mm.
    chica = cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    g = P.detectar(chica, mm_por_px=2.0)
    assert g.ok, g.motivo
    assert np.hypot(*(np.array(g.celda("A1").centro_mm) - verdad["A1"])) < 8
    g2 = P.detectar(img, region_mm=(550, 150, 300, 300))
    assert g2.ok
    assert np.hypot(*(np.array(g2.celda("A1").centro_mm) - verdad["A1"])) < 5


def test_celda_en_ida_y_vuelta():
    img, _ = S.pastillero(angulo_deg=33, centro_mm=(400, 300))
    g = P.detectar(img)
    for c in g.celdas:
        assert P.celda_en(g, c.centro_mm) == c.dir
    assert P.celda_en(g, (5, 5)) is None


def test_serializa():
    img, _ = S.pastillero()
    g = P.detectar(img)
    g2 = P.Grilla.desde_dict(g.a_dict())
    assert g2.celda("C4").centro_mm == g.celda("C4").centro_mm


@pytest.mark.parametrize("d,ok", [("A1", True), ("d7", True), (" b3 ", True), ("E1", False), ("A8", False), ("A0", False), ("", False)])
def test_direccion_valida(d, ok):
    assert P.direccion_valida(d) is ok

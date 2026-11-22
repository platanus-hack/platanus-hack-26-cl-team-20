from pathlib import Path

import cv2
import numpy as np

from percepcion import intrusos as I

FOTO = Path(__file__).parent / "datos" / "mesa-cenital-mm-1.jpg"


def _mesa():
    return cv2.imread(str(FOTO))


def test_mesa_igual_no_hay_intrusos():
    m = _mesa()
    ruido = np.clip(m.astype(np.int16) + np.random.default_rng(0).integers(-4, 5, m.shape), 0, 255).astype(np.uint8)
    assert I.detectar(m, ruido) == []


def test_celular_en_la_mesa():
    ref = _mesa()
    con = I.poner_celular(ref, (330, 470), angulo_deg=25)
    (o,) = I.detectar(ref, con)
    assert o["tipo"] == "celular"
    assert np.hypot(*(np.array(o["centro_mm"]) - (330, 470))) < 10
    assert len(o["poligono_mm"]) == 4


def test_celular_en_varios_giros_y_lugares():
    ref = _mesa()
    for k, (c, ang) in enumerate((((560, 120), 0), ((120, 420), 70), ((800, 470), -35))):
        con = I.poner_celular(ref, c, angulo_deg=ang, semilla=k)
        tipos = [o["tipo"] for o in I.detectar(ref, con)]
        assert tipos == ["celular"], (c, ang, tipos)


def test_lo_proyectado_no_es_un_intruso():
    """El proyector solo suma luz: un botón encendido del tamaño de un teléfono no dispara la alarma."""
    ref = _mesa()
    con = ref.astype(np.int16)
    con[300:372, 100:262] += np.array([60, 90, 40], np.int16)  # botón «Listo» lleno de luz: 162 × 72 mm
    con[100:400, 40:44] += 120  # una línea de 4 mm
    con = np.clip(con, 0, 255).astype(np.uint8)
    assert I.detectar(ref, con) == []
    # Con el modo de diferencia completa sí aparecería: por eso el modo por defecto es «más oscuro».
    assert any(o["tipo"] == "celular" for o in I.detectar(ref, con, solo_mas_oscuro=False))


def test_celular_bajo_lo_proyectado_igual_se_ve():
    ref = _mesa()
    con = I.poner_celular(ref, (200, 300)).astype(np.int16)
    con[250:350, 60:340] += 40  # el panel proyectado encima del celular
    con = np.clip(con, 0, 255).astype(np.uint8)
    assert [o["tipo"] for o in I.detectar(ref, con)] == ["celular"]


def test_lo_conocido_se_ignora():
    ref = _mesa()
    con = I.poner_celular(ref, (330, 470))
    assert I.detectar(ref, con, ignorar_rects_mm=[(250, 380, 160, 180)]) == []
    poly = [[240, 370], [420, 370], [420, 570], [240, 570]]
    assert I.detectar(ref, con, ignorar_poligonos_mm=[poly]) == []


def test_un_brazo_desde_el_borde_no_es_celular():
    ref = _mesa()
    con = ref.copy()
    cv2.fillPoly(con, [np.int32([[0, 300], [190, 330], [200, 400], [0, 390]])], (120, 150, 190))  # piel, entra del borde
    tipos = [o["tipo"] for o in I.detectar(ref, con)]
    assert "celular" not in tipos


def test_algo_chico_no_cuenta():
    ref = _mesa()
    con = ref.copy()
    cv2.circle(con, (300, 470), 15, (20, 20, 20), -1)  # una tapa: 700 mm²
    assert I.detectar(ref, con) == []

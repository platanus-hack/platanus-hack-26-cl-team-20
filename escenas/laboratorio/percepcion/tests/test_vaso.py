import numpy as np
import pytest

from percepcion import sintetico as S
from percepcion import vaso as V


def _cal(s, con_vacio=True):
    return V.calibrar_por_marcas(s.roi_px, {50: s.marcas_px[50], 200: s.marcas_px[200]}, vacio=s.vacio if con_vacio else None)


@pytest.mark.parametrize("ml", [20, 50, 87, 140, 230])
@pytest.mark.parametrize("con_vacio", [True, False])
def test_nivel_liquido_de_color(ml, con_vacio):
    s = S.vaso(ml, semilla=ml)
    lec = V.nivel_px(s.cuadro, _cal(s, con_vacio))
    assert not lec.vacio
    assert abs(lec.fila_px - s.fila_superficie) <= 5  # 5 px ≈ 4 ml en este vaso


@pytest.mark.parametrize("ml", [30, 120, 210])
def test_agua_casi_transparente_necesita_la_foto_del_vaso_vacio(ml):
    s = S.vaso(ml, alfa=0.12, semilla=ml)
    con = V.nivel_px(s.cuadro, _cal(s, True))
    assert abs(con.fila_px - s.fila_superficie) <= 7
    sin = V.nivel_px(s.cuadro, _cal(s, False))
    assert sin.vacio  # sin referencia no la ve: lo dice en vez de inventar un nivel


def test_vaso_vacio():
    s = S.vaso(0)
    cal = _cal(s)
    lec = V.nivel_px(s.cuadro, cal)
    assert lec.vacio
    assert cal.ml(lec.fila_px) == pytest.approx(0, abs=1)


def test_calibracion_dos_y_tres_puntos():
    roi = [0, 100, 50, 300]
    dos = V.Calibracion(roi, puntos=[[399, 0], [279, 100]])
    assert dos.ml(339) == pytest.approx(50)
    assert dos.ml(219) == pytest.approx(150)  # extrapola hacia arriba con el último tramo
    assert dos.ml(450) == 0  # nunca negativo
    # Tres puntos: vaso cónico, más ml por px arriba que abajo.
    tres = V.Calibracion(roi, puntos=[[399, 0], [299, 50], [249, 100]])
    assert tres.ml(349) == pytest.approx(25)
    assert tres.ml(274) == pytest.approx(75)
    assert tres.px(75) == pytest.approx(274)
    # Monótona: más arriba, más ml.
    filas = np.arange(400, 120, -7)
    mls = [tres.ml(f) for f in filas]
    assert all(b >= a for a, b in zip(mls, mls[1:]))


def test_calibracion_invalida():
    with pytest.raises(ValueError):
        V.Calibracion([0, 0, 50, 100], puntos=[[90, 0]]).ml(50)
    with pytest.raises(ValueError):
        V.Calibracion([0, 0, 50, 100], puntos=[[90, 0], [90, 10]]).ml(50)
    with pytest.raises(ValueError):
        V.Calibracion([0, 0, 2, 2])


def test_calibracion_por_marcas_con_lectura_real():
    s = S.vaso(100, semilla=3)
    cal = _cal(s)
    ml = cal.ml(V.nivel_px(s.cuadro, cal).fila_px)
    assert ml == pytest.approx(100, abs=5)
    assert cal.a_dict()["roi_px"] == s.roi_px


def test_umbral_no_parpadea_en_el_borde():
    u = V.Umbral(objetivo_ml=50, tolerancia_ml=2, histeresis_ml=1, cuadros=3)
    rng = np.random.default_rng(0)
    # Sube hasta 52 (el borde de arriba) y tiembla ±1,2 ml ahí un buen rato: nunca debe decir «te pasaste».
    for ml in list(np.linspace(0, 52, 40)) + list(52 + rng.uniform(-1.2, 0.9, 60)):
        z = u.actualizar(ml)["zona"]
        assert z != V.PASADO
    assert u.zona == V.LISTO
    assert u.cambios == [(V.FALTA, V.LISTO)]


def test_umbral_te_pasaste_una_sola_vez():
    u = V.Umbral(objetivo_ml=50, tolerancia_ml=2, histeresis_ml=1, cuadros=3)
    rng = np.random.default_rng(1)
    serie = list(np.linspace(0, 60, 80) + rng.normal(0, 0.6, 80)) + [60 + rng.normal(0, 0.6) for _ in range(30)]
    zonas = [u.actualizar(ml)["zona"] for ml in serie]
    assert zonas[-1] == V.PASADO
    assert u.cambios == [(V.FALTA, V.LISTO), (V.LISTO, V.PASADO)]
    r = u.actualizar(60)
    assert r["exceso_ml"] == pytest.approx(10)


def test_umbral_cerca_y_lecturas_vacias():
    u = V.Umbral(objetivo_ml=100, tolerancia_ml=3)
    assert u.actualizar(None)["zona"] == V.FALTA
    assert u.actualizar(85)["cerca"] is True  # faltan 15 ≤ 20 (20 % del objetivo)
    assert u.actualizar(40)["cerca"] is False


def test_medidor_de_punta_a_punta_con_cuadros_sinteticos():
    """Se llena el vaso de a poco hasta pasarse del objetivo: el aviso sale una vez y no vuelve atrás."""
    base = S.vaso(0, semilla=0)
    cal = V.calibrar_por_marcas(base.roi_px, {50: base.marcas_px[50], 200: base.marcas_px[200]}, vacio=base.vacio)
    med = V.Medidor(cal, n=3)
    um = V.Umbral(objetivo_ml=80, tolerancia_ml=3, histeresis_ml=1, cuadros=2)
    zonas = []
    for i, ml in enumerate(np.concatenate([np.arange(0, 80, 6), [80, 80, 80, 81, 82, 86, 90, 94, 96, 96, 96]])):
        r = med.leer(S.vaso(float(ml), semilla=i).cuadro)
        zonas.append(um.actualizar(r["ml"])["zona"])
    assert zonas[0] == V.FALTA
    assert V.LISTO in zonas
    assert zonas[-1] == V.PASADO
    assert [c[1] for c in um.cambios] == [V.LISTO, V.PASADO]


def test_zona_del_vaso_sale_sola():
    v, lleno = S.vaso(0, semilla=1), S.vaso(80, semilla=2)
    roi = V.roi_desde_diferencia(v.vacio, lleno.cuadro)
    x, y, w, h = roi
    # Cubre el interior del vaso a lo ancho y llega al fondo.
    assert abs(x - lleno.roi_px[0]) <= 10 and abs((x + w) - (lleno.roi_px[0] + lleno.roi_px[2])) <= 10
    assert abs((y + h) - S.VASO_FONDO) <= 4
    assert V.roi_desde_diferencia(v.vacio, v.vacio) is None


@pytest.mark.parametrize("ml", [10, 30, 56, 80, 120, 170, 220])
def test_calibracion_con_lecturas_es_exacta(ml):
    """Vacío + 50 y 150 ml leídos por el detector: el sesgo del menisco se cancela."""
    vacio = S.vaso(0, semilla=1).vacio
    cal = V.calibrar_con_lecturas(vacio, {50: S.vaso(50, semilla=2).cuadro, 150: S.vaso(150, semilla=3).cuadro})
    s = S.vaso(ml, semilla=ml + 7)
    assert cal.ml(V.nivel_px(s.cuadro, cal).fila_px) == pytest.approx(ml, abs=3)

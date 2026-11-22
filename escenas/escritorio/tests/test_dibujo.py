"""Las piezas proyectadas: plantillas bien llenas y ubicadas donde corresponde sobre la libreta girada."""

import json
import math
import re
from pathlib import Path

import numpy as np

import dibujo
from pluma import PlumaFija, limpiar

CFG = json.loads((Path(__file__).resolve().parents[1] / "escena.json").read_text())


def sin_huecos(html):
    assert "{{" not in html and "}}" not in html


def test_llenar_secciones_listas_y_escape():
    t = "<b>{{a}}</b>{{#xs}}<i>{{n}}</i>{{/xs}}{{^vacio}}nada{{/vacio}}{{{crudo}}}"
    out = dibujo.llenar(t, {"a": "<x>", "xs": [{"n": 1}, {"n": 2}], "vacio": [], "crudo": "<br>"})
    assert out == "<b>&lt;x&gt;</b><i>1</i><i>2</i>nada<br>"


def test_ubicar_en_libreta_girada():
    # libreta de 150 × 210 mm girada 30° alrededor de (500, 300)
    a = math.radians(30)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    local = np.array([[-75, -105], [75, -105], [75, 105], [-75, 105]])
    esquinas = (local @ R.T + [500, 300]).tolist()
    lib = dibujo.LibretaEnMesa(esquinas)
    assert abs(lib.angulo - 30) < 0.1 and abs(lib.ancho - 150) < 0.5 and abs(lib.alto - 210) < 0.5
    # el centro de la libreta cae en su centro en la mesa
    u = lib.ubicar([0, 0, 150, 210])
    x, y, w, h = u["rect_mm"]
    assert np.allclose([x + w / 2, y + h / 2], [500, 300], atol=0.5)
    # la esquina de arriba a la izquierda (local 0,0) es la primera esquina dada
    assert np.allclose(lib.a_mesa([0, 0]), esquinas[0], atol=0.5)


def test_marco_tiene_boton_tocable_y_cuatro_esquinas():
    lib = dibujo.LibretaEnMesa(CFG["libreta"]["esquinas_mm"])
    w = dibujo.marco(lib, CFG)
    h = w["params"]["html"]
    sin_huecos(h)
    assert 'data-press="responde"' in h
    assert h.count('class="corte"') == 8
    assert w["shader"] == "html" and w["id"] == "escritorio:marco"
    assert "--tinta:" in w["params"]["css"]


def test_nota_revela_el_texto_en_orden_con_el_trazo_wui():
    lib = dibujo.LibretaEnMesa(CFG["libreta"]["esquinas_mm"])
    w = dibujo.nota(lib, CFG, 3, [0.1, 0.4, 0.7, 0.2], ["/v1/cobros", "/v1/pagos"])
    h = w["params"]["html"]
    sin_huecos(h)
    assert w["id"] == "escritorio:nota:3"
    assert 'class="nota wui-pista"' in h
    assert re.findall(r'<span class="wui-linea">(.*?)</span>', h) == ["/v1/cobros", "/v1/pagos"]
    assert "--wui-pista-tiempo: 1720ms" in w["params"]["css"]
    # cae dentro de la libreta
    x, y, ww, hh = w["rect_mm"]
    lx, ly, lw, lh = lib.ubicar([0, 0, lib.ancho, lib.alto])["rect_mm"]
    assert lx <= x and x + ww <= lx + lw and ly <= y and y + hh <= ly + lh


def test_marca_a_la_izquierda_de_lo_escrito():
    lib = dibujo.LibretaEnMesa(CFG["libreta"]["esquinas_mm"])
    w = dibujo.marca(lib, CFG, [[0.2, 0.1, 0.5, 0.04], [0.2, 0.15, 0.4, 0.04]])
    sin_huecos(w["params"]["html"])
    x, y, ww, hh = w["rect_mm"]
    texto_x = lib.a_mesa([0.2 * lib.ancho, 0])[0]
    assert x + ww - 3 <= texto_x  # el trazo (sin el relleno de 3 mm del widget) termina antes de la letra


def test_panel_de_agentes_con_estados():
    lista = [{"nombre": "a", "estado": "trabajando", "detalle": "x"}, {"nombre": "b", "estado": "raro", "detalle": "y"}]
    w = dibujo.agentes(CFG, lista, {"nombre": "escritorio", "estado": "listo", "detalle": "z"})
    h = w["params"]["html"]
    sin_huecos(h)
    assert h.count("<li") == 3
    assert 'class="trabajando"' in h and 'class="listo"' in h
    assert "nuevo" not in h  # la primera vez no se anima nada
    w2 = dibujo.agentes(CFG, [{**lista[0], "detalle": "otra cosa"}, lista[1]], None, anteriores=lista)
    assert w2["params"]["html"].count(" nuevo") == 1


def test_respuesta_cabe_y_no_grita():
    r = limpiar({"leido": "x", "respuesta": ["¡Hola! esta línea es bastante más larga de lo permitido", "dos", "tres", "cuatro"]}, CFG)
    assert len(r["respuesta"]) <= CFG["respuesta"]["max_lineas"]
    assert all(len(l) <= CFG["respuesta"]["max_caracteres"] for l in r["respuesta"])
    assert not any("!" in l or "¡" in l for l in r["respuesta"])


def test_pluma_fija_da_vueltas():
    p = PlumaFija(CFG)
    p.leer.__func__  # existe
    fijas = CFG["respuesta"]["fijas"]
    import time

    t = time.sleep
    time.sleep = lambda s: None
    try:
        vistas = [p.leer([])["respuesta"] for _ in range(len(fijas) + 1)]
    finally:
        time.sleep = t
    assert vistas[0] == vistas[-1]

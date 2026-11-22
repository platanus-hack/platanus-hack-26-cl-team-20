"""La escena completa sin mesa ni modelo: una mesa falsa que guarda lo proyectado, la libreta sintética y respuestas fijas."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

import dibujo
import escritorio as esc
import pluma
from percepcion import sintetico as s

CFG = json.loads((Path(__file__).resolve().parents[1] / "escena.json").read_text())


class MesaFalsa:
    def __init__(self):
        self.widgets: dict[str, dict] = {}
        self.llamadas: list[list[str]] = []

    def proyectar(self, widgets, escena=None):
        self.llamadas.append([w["id"] for w in widgets])
        self.widgets.update({w["id"]: w for w in widgets})
        return {}

    def quitar(self, ids, escena=None):
        for i in ids:
            self.widgets.pop(i, None)
        return {"removed": ids}

    def mundo(self, escena=None):
        return {"widgets": list(self.widgets.values()), "objects": []}

    def escenas(self, accion="list", **args):
        return {"scenes": [{"name": "Escritorio"}]}


@pytest.fixture(autouse=True)
def sin_jev(monkeypatch):
    """Los tests no salen a la red: Jev queda apagado salvo en los que lo simulan."""
    monkeypatch.setattr(esc.jev, "disponible", lambda: False)
    monkeypatch.setattr(esc.jev, "decide", lambda *a, **k: pytest.fail("Jev llamado sin simular"))


@pytest.fixture
def escena(tmp_path, monkeypatch):
    monkeypatch.setattr(pluma.time, "sleep", lambda s: None)
    fotos = tmp_path / "libreta"
    fotos.mkdir()

    def escribir(lineas, semilla):
        ruta = fotos / f"{semilla:03d}.jpg"
        cv2.imwrite(str(ruta), s.foto(lineas, semilla=semilla)["img"])
        import os

        os.utime(ruta, (semilla, semilla))  # la más nueva es la última escrita

    mesa = MesaFalsa()
    e = esc.Escritorio(CFG, mesa, esc.CamaraArchivo(fotos), pluma.PlumaFija(CFG), memoria=tmp_path / "memoria.npz")
    return e, mesa, escribir


def test_montar_proyecta_marco_y_panel_en_una_llamada(escena):
    e, mesa, _ = escena
    e.montar()
    assert ["escritorio:marco", "escritorio:agentes"] in mesa.llamadas


def test_boton_lee_y_contesta_en_dos_llamadas(escena):
    e, mesa, escribir = escena
    e.montar()
    escribir(["notas del sprint"], 1)
    e.mirar()
    e.lector.confirmar()  # lo de antes ya estaba leído
    escribir(["notas del sprint", "como le pongo a este endpoint"], 2)
    disparo, activa = e.leer_eventos([{"payload": {"type": "widget_pressed", "widget_id": "escritorio:marco", "value": "responde"}}], True)
    assert disparo == ("boton", None) and activa
    mesa.llamadas.clear()
    fila = e.ciclo(por="boton")
    assert fila and fila["respuesta"] == CFG["respuesta"]["fijas"][0]
    # 1) corchete + «leyendo» + panel, 2) nota + estado vacío + panel
    assert len(mesa.llamadas) == 2
    assert {"escritorio:marca", "escritorio:estado"} <= set(mesa.llamadas[0])
    assert "escritorio:nota:0" in mesa.llamadas[1]
    assert "te contestó" in mesa.widgets["escritorio:agentes"]["params"]["html"]


def test_sin_nada_nuevo_no_llama_al_modelo(escena):
    e, mesa, escribir = escena
    escribir(["notas del sprint"], 1)
    e.mirar()
    e.lector.confirmar()
    escribir(["notas del sprint"], 3)
    assert e.ciclo(por="boton") is None
    assert "nada nuevo" in mesa.widgets["escritorio:estado"]["params"]["html"]


def test_voz_lee_aunque_no_haya_tinta_nueva(escena):
    e, mesa, escribir = escena
    escribir(["notas del sprint"], 1)
    e.mirar()
    e.lector.confirmar()
    escribir(["notas del sprint"], 4)
    disparo, _ = e.leer_eventos([{"payload": {"type": "flow_emitted", "event": "voice_command", "data": {"text": "resume esto", "scene": "Escritorio"}}}], True)
    assert disparo == ("voz", "resume esto")
    assert e.ciclo(dicho=disparo[1], por="voz") is not None


def test_otra_escena_apaga_la_lectura_y_la_voz_de_otra_escena_no_dispara(escena):
    e, _, _ = escena
    _, activa = e.leer_eventos([{"payload": {"type": "flow_emitted", "event": "scene_changed", "data": {"scene": "Sistema solar"}}}], True)
    assert not activa
    disparo, _ = e.leer_eventos([{"payload": {"type": "flow_emitted", "event": "voice_command", "data": {"text": "hola", "scene": "Sistema solar"}}}], True)
    assert disparo is None


def test_las_respuestas_no_se_pisan_y_siguen_la_numeracion(escena):
    e, mesa, escribir = escena
    escribir(["uno"], 1)
    e.mirar()
    e.lector.confirmar()
    lineas = ["uno"]
    for i, extra in enumerate(["dos dos", "tres tres", "cuatro"]):
        lineas.append(extra)
        escribir(lineas, 10 + i)
        assert e.ciclo(por="auto")
    notas = [w for i, w in mesa.widgets.items() if i.startswith("escritorio:nota:")]
    assert len(notas) >= 2
    rects = [w["rect_mm"] for w in notas]
    for a in range(len(rects)):
        for b in range(a + 1, len(rects)):
            ax, ay, aw, ah = rects[a]
            bx, by, bw, bh = rects[b]
            assert ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay


def _leido(e, escribir):
    escribir(["uno"], 1)
    e.mirar()
    e.lector.confirmar()
    escribir(["uno", "dos dos"], 2)


def test_jev_no_contesta_un_dibujo_cuando_lee_solo(escena, monkeypatch):
    e, mesa, escribir = escena
    e.jev_ok = True
    monkeypatch.setattr(esc.jev, "que_es", lambda img: ("dibujo", 0.9, 120.0))
    _leido(e, escribir)
    fila = e.ciclo(por="auto")
    assert fila["omitido"] == "dibujo" and fila["hueco"] is None and fila["jev"]["que_es"][0] == "dibujo"
    assert not [i for i in mesa.widgets if i.startswith("escritorio:nota:")]
    assert e.mirar()["estado"] == "igual"  # quedó leído: no se vuelve a intentar


def test_jev_con_boton_contesta_siempre(escena, monkeypatch):
    e, mesa, escribir = escena
    e.jev_ok = True
    monkeypatch.setattr(esc.jev, "que_es", lambda img: pytest.fail("con botón no se filtra"))
    _leido(e, escribir)
    assert e.ciclo(por="boton")["hueco"] is not None


def test_jev_lee_lo_que_senala(escena, monkeypatch):
    e, mesa, escribir = escena
    e.jev_ok = True
    _leido(e, escribir)
    m = e.mirar()
    unidad = np.float32([[0, 0], [1, 0], [1, 1], [0, 1]])
    h = cv2.getPerspectiveTransform(unidad, np.float32(m["libreta"]["quad_px"]))
    dedo = cv2.perspectiveTransform(np.float32([[[0.5, 0.3]]]), h)[0, 0]
    monkeypatch.setattr(esc.jev, "senala", lambda foto: {"centro": (float(dedo[0]), float(dedo[1])), "confianza": 0.8, "ms": 90.0})
    vistos = []
    leer = e.pluma.leer
    monkeypatch.setattr(e.pluma, "leer", lambda imgs, dicho=None, timeout=0: vistos.append((len(imgs), dicho)) or leer(imgs, dicho))
    fila = e.ciclo(dicho="qué es esto", por="voz")
    assert abs(fila["jev"]["senala"][1] - 0.3) < 0.03
    assert vistos[0][0] == 2 and "señaló" in vistos[0][1]


def test_el_agente_hace_leer_y_repetir_sin_voice_command(escena):
    e, mesa, escribir = escena
    d, _ = e.leer_eventos([{"payload": {"type": "flow_emitted", "event": "paso_siguiente", "data": {"by": "agent"}}}], True)
    assert d == ("agente", None)
    d, _ = e.leer_eventos([{"payload": {"type": "flow_emitted", "event": "repetir", "data": {"scene": "Sistema solar"}}}], True)
    assert d is None  # de otra escena
    d, _ = e.leer_eventos([{"payload": {"type": "flow_emitted", "event": "repetir", "data": {}}}], True)
    assert d == ("repetir", None)
    _leido(e, escribir)
    e.ciclo(por="agente")
    mesa.llamadas.clear()
    assert e.repetir() and mesa.llamadas == [["escritorio:nota:0"]]


BARRA_X = 150  # barra lateral de pestañas de la consola
VOZ = [870, 0, 130, 130]  # esquina del botón de voz


def _pisa_voz(r):
    x, y, w, h = r
    vx, vy, vw, vh = VOZ
    return x < vx + vw and vx < x + w and y < vy + vh and vy < y + h


def test_nada_en_la_barra_de_pestanas_ni_en_la_esquina_de_voz(escena):
    e, mesa, escribir = escena
    e.montar()
    _leido(e, escribir)
    e.ciclo(por="boton")
    for w in mesa.widgets.values():
        assert w["rect_mm"][0] >= BARRA_X and not _pisa_voz(w["rect_mm"]), w["id"]


def test_recetas_del_preset_respetan_la_barra_y_la_voz():
    import re

    md = (Path(__file__).resolve().parents[1] / "escena.md").read_text()
    rects = [json.loads(m) for m in re.findall(r'"rect_mm": (\[[0-9, ]+\])', md)]
    assert len(rects) >= 4
    for r in rects:
        assert r[0] >= BARRA_X and not _pisa_voz(r), r


class _Respuesta:
    def __init__(self, texto):
        self.texto = texto

    def read(self):
        return self.texto.encode()


@pytest.mark.parametrize(
    "falla",
    [TimeoutError("lectura"), ConnectionResetError("cortada"), "<html>502</html>", '{"jsonrpc": "2.0", "id": 1}', "[1, 2]"],
)
def test_mesa_convierte_cortes_y_respuestas_raras_en_error_mesa(monkeypatch, falla):
    import mesa as m

    def abrir(req, timeout=0):
        if isinstance(falla, Exception):
            raise falla
        return _Respuesta(falla)

    monkeypatch.setattr(m.urllib.request, "urlopen", abrir)
    with pytest.raises(m.ErrorMesa):
        m.Mesa("http://localhost:1/mcp", "tok-prueba").tool("scene")


def test_vivo_sobrevive_a_un_corte_del_gateway(escena, monkeypatch):
    e, mesa, escribir = escena
    from mesa import ErrorMesa

    monkeypatch.setattr(esc.time, "sleep", lambda s: None)
    escribir(["uno"], 1)
    llamadas = []

    def esperar_eventos(cursor, tipos=None, timeout_s=0):
        llamadas.append(cursor)
        if len(llamadas) == 1:
            return {"events": [], "cursor": 0}  # cursor_actual
        if len(llamadas) == 2:
            raise ErrorMesa("HTTP 502")  # el gateway se cae a mitad de la demo
        if len(llamadas) == 3:
            return {"events": [{"payload": {"type": "widget_pressed", "widget_id": "escritorio:marco"}}], "cursor": 5}
        raise KeyboardInterrupt  # fin del test

    mesa.esperar_eventos = esperar_eventos
    with pytest.raises(KeyboardInterrupt):
        e.vivo()
    assert len(llamadas) == 4  # siguió después del 502 y volvió a atender un toque


def test_vivo_sin_cli_claude_sigue_con_respuestas_fijas(escena, monkeypatch):
    e, mesa, escribir = escena
    monkeypatch.setattr(esc.time, "sleep", lambda s: None)
    escribir(["uno"], 1)

    class SinClaude(pluma.PlumaFija):
        def calentar(self, timeout=0):
            raise FileNotFoundError("claude")  # lo que lanza Popen si el CLI no está instalado

    e.pluma = SinClaude(CFG)
    llamadas = []

    def esperar_eventos(cursor, tipos=None, timeout_s=0):
        llamadas.append(cursor)
        if len(llamadas) == 1:
            return {"events": [], "cursor": 7}  # cursor_actual: una sola llamada, sin paginar
        raise KeyboardInterrupt  # fin del test

    mesa.esperar_eventos = esperar_eventos
    with pytest.raises(KeyboardInterrupt):
        e.vivo()
    assert type(e.pluma) is pluma.PlumaFija, "sin modelo, la escena cae a las respuestas fijas en vez de morir"
    assert llamadas == [10**12, 7], "parte del final del historial y sigue desde ahí"


def test_diagnostico_deja_imagen_y_consejo(tmp_path):
    f = s.foto(["como le pongo a", "este endpoint?"])
    r = esc.diagnostico(CFG, f["img"], tmp_path / "d.jpg")
    assert r["libreta"] and (tmp_path / "d.jpg").exists() and r["hueco"]
    vacia = esc.diagnostico(CFG, s.mesa(), tmp_path / "v.jpg")
    assert vacia["libreta"] is None and "consejo" in vacia

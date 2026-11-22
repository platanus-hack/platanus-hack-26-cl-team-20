"""El conductor contra una mesa falsa: sin gateway, sin Chrome, sin cámaras."""

import json
from pathlib import Path

import cv2
import pytest
import yaml

import calibrar_vaso
import conductor as C
from mesa_mcp import ErrorMesa
from percepcion import sintetico as S
from percepcion import vaso as V

AQUI = Path(__file__).parent.parent
FOTO = AQUI / "percepcion" / "tests" / "datos" / "mesa-cenital-mm-1.jpg"


class MesaFalsa:
    def __init__(self, fotos=None):
        self.proyectado: dict[str, dict] = {}
        self.cola: list[dict] = []
        self.fotos = fotos or []
        self.cortes = 0

    def project(self, ws):
        for w in ws:
            self.proyectado[w["id"]] = w
        return {}

    def remove(self, ids):
        for i in ids:
            self.proyectado.pop(i, None)
        return {}

    def eventos(self, cursor, tipos=None, espera_s=0):
        if self.cortes:
            self.cortes -= 1
            raise ErrorMesa("events_wait: sin conexión con el gateway")
        evs, self.cola = self.cola, []
        return cursor + len(evs), evs

    def cursor_actual(self):
        return 0

    def look(self, camera_id=None, annotate=False, clean=False):
        return {}, self.fotos.pop(0)

    def tool(self, nombre, args=None):
        return {"objects": []}, []

    # ayudas del test
    def toque(self, widget, valor, punto=None, gatillo="api"):
        self.cola.append({"type": "widget_pressed", "widget_id": widget, "value": valor, "trigger": gatillo, "point_mm": punto})

    def texto(self, wid):
        return self.proyectado[wid]["params"]["html"]


class VasoGuion:
    """Cámara del vaso que entrega fotos sintéticas de una lista de ml."""

    def __init__(self, serie):
        vacio = S.vaso(0, semilla=1).vacio
        self.cal = V.calibrar_con_lecturas(vacio, {50: S.vaso(50, semilla=2).cuadro, 150: S.vaso(150, semilla=3).cuadro})
        self.serie = list(serie)

    def empezar(self, objetivo):
        pass

    def cuadro(self):
        ml = self.serie.pop(0) if len(self.serie) > 1 else self.serie[0]
        return S.vaso(ml, semilla=int(ml * 10) % 97).cuadro


@pytest.fixture(autouse=True)
def sin_red(monkeypatch):
    """Los tests nunca le preguntan a Jev de verdad (aunque haya jev.py y llave): sin respuesta, como sin red."""
    monkeypatch.setattr(C.jev_lab, "confirmar_celular", lambda *a, **k: None)


@pytest.fixture
def armado(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ESTADO", tmp_path)
    prot = C.cargar_protocolo(AQUI / "escena.md")
    g = C.P.detectar(cv2.imread(str(FOTO)), paleta=prot["pastillero"]["colores"])
    mesa = MesaFalsa()
    vaso = VasoGuion([0, 0, 10, 25, 40, 46, 50, 51, 51, 51, 57, 58, 58, 58, 58])
    e = C.Estado(prot=prot, grilla=g, vaso_mm=prot["vaso"]["posicion_mm"])
    c = C.Conductor(mesa, e, vaso, log=lambda *_: None)
    c.cursor = 0
    c.entrar(0)
    return c, mesa, tmp_path


def paso(c, mesa, n=1):
    """Una vuelta del bucle sin esperas."""
    for _ in range(n):
        c.cursor, evs = mesa.eventos(c.cursor)
        for ev in evs:
            c.evento(ev)
        c.medir()


def test_protocolo_de_la_demo_es_valido():
    prot = C.cargar_protocolo(AQUI / "escena.md")
    assert len(prot["pasos"]) == 5
    assert prot["pasos"][0]["celda"] == "A2" and prot["pasos"][1]["desde"] == "A2" and prot["pasos"][1]["hasta"] == "C5"
    assert prot["viales"]["B3"] == "anticuerpo"


def test_protocolo_con_errores_explica_que_arreglar(tmp_path):
    malo = {"pasos": [{"di": "Toma la muestra", "celda": "E9"}, {"detalle": "sin instrucción"}, {"di": "x", "elegir": True}]}
    ruta = tmp_path / "p.yaml"
    ruta.write_text(yaml.safe_dump(malo, allow_unicode=True))
    with pytest.raises(SystemExit) as e:
        C.cargar_protocolo(ruta)
    msg = str(e.value)
    assert "E9" in msg and "falta «di:»" in msg and "necesitan una «celda:»" in msg


def test_recorrido_completo(armado, monkeypatch):
    c, mesa, carpeta = armado
    assert {"lab-grilla", "lab-paso", "lab-protocolo"} <= set(mesa.proyectado)
    assert not any(i.startswith("lab-ambiente") for i in mesa.proyectado)
    assert 'data-press="A2"' in mesa.texto("lab-grilla") and "muestra 7" in mesa.texto("lab-grilla")

    # 1 · Elegir: toca el vial equivocado y la mesa corrige sin avanzar.
    mesa.toque("lab-grilla", "C4")
    paso(c, mesa)
    assert c.e.i == 0
    assert "Esa es la C4, azul. Busca la A2" in mesa.texto("lab-paso")
    # Con el dedo manda la punta: aunque el blanco diga otro vial, gana el que está bajo el dedo.
    mesa.toque("lab-grilla", "B4", punto=c.e.grilla.celda("A2").centro_mm, gatillo="dwell")
    paso(c, mesa)
    assert c.e.i == 1

    # 2 · Traspaso A2 → C5: flecha entre viales y Listo ya disponible.
    g = mesa.texto("lab-grilla")
    assert "lab-flecha" in g and "A2 → C5" in g
    assert "lab-listo ya" in mesa.texto("lab-paso")
    mesa.toque("lab-paso", "listo")
    paso(c, mesa)

    # 3 · Traspaso B3 → C5, por voz.
    assert c.e.i == 2 and "B3 → C5" in mesa.texto("lab-grilla")
    mesa.cola.append({"type": "flow_emitted", "event": "voice_command", "data": {"text": "Listo"}})
    paso(c, mesa)

    # 4 · Mezclar: cuenta regresiva; al terminar, «Ya está mezclado» y Listo se enciende.
    assert c.e.i == 3 and "lab-reloj" in mesa.texto("lab-paso") and "lab-listo ya" not in mesa.texto("lab-paso")
    c.reloj_fin = C.time.monotonic() - 0.1
    c.revisar_reloj()
    assert "Ya está mezclado" in mesa.texto("lab-paso") and "lab-listo ya" in mesa.texto("lab-paso")
    mesa.toque("lab-paso", "listo")
    paso(c, mesa)

    # 5 · Medir el buffer hasta pasarse; saca lo que sobra y queda justo.
    assert c.e.i == 4 and "lab-medidor" in mesa.proyectado
    zonas = []
    for _ in range(15):
        paso(c, mesa)
        zonas.append(c.e.lectura["zona"])
    assert V.LISTO in zonas and zonas[-1] == V.PASADO
    assert "Te pasaste" in mesa.texto("lab-medidor")
    c.vaso.serie = [50]
    paso(c, mesa, 5)
    assert c.e.lectura["zona"] == V.LISTO
    mesa.toque("lab-paso", "listo")
    paso(c, mesa)
    assert c.e.terminado and "Protocolo completo" in mesa.texto("lab-paso")

    registro = json.loads(next((carpeta / "registros").glob("*.json")).read_text())
    resultados = [p["resultado"] for p in registro["pasos"]]
    assert resultados[:4] == ["A2", "A2 → C5", "B3 → C5", "C5 10 s"]
    assert resultados[4].endswith(" ml") and abs(int(resultados[4].split()[0]) - 50) <= 3


def test_celular_en_la_mesa_pausa_el_protocolo(armado):
    c, mesa, _ = armado
    ref = cv2.imread(str(FOTO))
    con_celular = C.I.poner_celular(ref, (330, 470), angulo_deg=20)
    foto = {"ahora": ref}
    c.vigia = C.Vigia(ref, lambda: foto["ahora"], periodo_s=0)

    c.vigilar()
    assert not c.e.intrusos
    foto["ahora"] = con_celular
    c.vigilar()  # una sola vez puede ser una sombra: todavía no alarma
    assert not c.e.intrusos
    c.vigilar()
    assert c.e.intrusos and c.e.intrusos[0]["tipo"] == "celular"
    assert "lab-alarma" in mesa.proyectado and "lab-alarma-1" in mesa.proyectado and "lab-paso" not in mesa.proyectado
    assert "Retira tu celular del espacio, por favor" in mesa.texto("lab-alarma")

    mesa.toque("lab-grilla", "A2")  # con la mesa contaminada el protocolo espera
    paso(c, mesa)
    assert c.e.i == 0

    foto["ahora"] = ref
    c.vigilar()
    assert not c.e.intrusos and "lab-paso" in mesa.proyectado and "lab-alarma" not in mesa.proyectado


def test_un_corte_del_gateway_no_bota_la_guia(armado, monkeypatch):
    c, mesa, _ = armado
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    mesa.cortes = 2
    mesa.proyectado.clear()  # el gateway se reinició y perdió lo proyectado
    t = iter(range(100))
    monkeypatch.setattr(C.time, "monotonic", lambda: next(t))
    c.correr(hasta=6)
    assert {"lab-grilla", "lab-paso"} <= set(mesa.proyectado)


def test_un_evento_que_falla_no_bota_la_guia(armado, monkeypatch):
    c, mesa, _ = armado
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    original, llamados = c.evento, []

    def evento(ev):
        llamados.append(ev["widget_id"])
        if ev["widget_id"] == "roto":
            raise KeyError("point_mm")
        original(ev)

    monkeypatch.setattr(c, "evento", evento)
    mesa.toque("roto", "x")
    mesa.toque("lab-grilla", "A1")
    t = iter(range(100))
    monkeypatch.setattr(C.time, "monotonic", lambda: next(t))
    c.correr(hasta=4)
    assert llamados[:2] == ["roto", "lab-grilla"], "el evento que viene después del que falló igual se atiende"
    assert {"lab-grilla", "lab-paso"} <= set(mesa.proyectado)


class _Respuesta:
    def __init__(self, texto):
        self.texto = texto

    def read(self):
        return self.texto.encode()


@pytest.mark.parametrize("texto", ['data: {"jsonrpc": "2.0", "id": 1, "result": {roto}}', '{"jsonrpc": "2.0", "id": 1}'])
def test_mesa_convierte_respuestas_raras_en_error_mesa(monkeypatch, texto):
    import mesa_mcp

    monkeypatch.setattr(mesa_mcp.urllib.request, "urlopen", lambda req, timeout=None: _Respuesta(texto))
    with pytest.raises(ErrorMesa):
        mesa_mcp.Mesa(url="http://localhost:1/mcp", token="t")._rpc("tools/list", {})


def test_calibrar_vaso_guiado(tmp_path, monkeypatch):
    monkeypatch.setattr(calibrar_vaso, "ESTADO", tmp_path)
    fotos = [S.vaso(0, semilla=1).cuadro, S.vaso(50, semilla=2).cuadro, S.vaso(150, semilla=3).cuadro]
    cal, vacio, ultima = calibrar_vaso.en_la_mesa("cam-2", [50, 150], mesa=MesaFalsa(fotos), esperar=lambda _: "")
    ruta = calibrar_vaso.guardar(cal, vacio, ultima)
    assert (tmp_path / "vaso-revision.jpg").exists()
    cal2 = C.cargar_cal_vaso(ruta)
    s = S.vaso(90, semilla=9)
    assert cal2.ml(V.nivel_px(s.cuadro, cal2).fila_px) == pytest.approx(90, abs=3)


def test_jev_confirma_el_celular_antes_de_la_alarma(armado, monkeypatch):
    c, mesa, _ = armado
    ref = cv2.imread(str(FOTO))
    con = C.I.poner_celular(ref, (330, 470), angulo_deg=20)
    c.vigia = C.Vigia(ref, lambda: con, periodo_s=0)
    preguntas, resp = [], {"p": 0.1}
    monkeypatch.setattr(C.jev_lab, "confirmar_celular", lambda mesa_mm, poly: preguntas.append(poly) or resp["p"])
    for _ in range(4):
        c.vigilar()
    assert not c.e.intrusos, "Jev dijo que no es un celular (un mouse oscuro, por ejemplo): sin alarma"
    assert len(preguntas) == 1, "una pregunta por candidato: lo ya dicho se recuerda"
    c.jev_dicho.clear()
    resp["p"] = 0.9
    c.vigilar()
    c.vigilar()
    assert c.e.intrusos and "lab-alarma" in mesa.proyectado
    # Sin Jev (sin llave o sin red) la alarma sale igual, solo por la forma: lo cubre test_celular_en_la_mesa_pausa_el_protocolo.


def test_la_voz_mueve_el_protocolo_con_eventos_propios(armado):
    c, mesa, _ = armado
    voz = lambda nombre: mesa.cola.append({"type": "flow_emitted", "event": nombre, "data": {}, "by": "agent"})  # noqa: E731
    voz("paso_siguiente")
    paso(c, mesa)
    assert c.e.i == 1
    voz("paso_anterior")
    paso(c, mesa)
    assert c.e.i == 0
    mesa.proyectado.clear()
    voz("repetir")
    paso(c, mesa)
    assert {"lab-grilla", "lab-paso"} <= set(mesa.proyectado)
    # Con la mesa contaminada, la voz tampoco avanza.
    c.e.intrusos = [{"tipo": "celular", "poligono_mm": [[300, 400], [370, 400], [370, 550], [300, 550]], "centro_mm": [335, 475]}]
    voz("paso_siguiente")
    paso(c, mesa)
    assert c.e.i == 0


def test_nada_bajo_la_barra_lateral_ni_proteinas_sobre_la_gradilla(armado, capsys):
    c, mesa, _ = armado
    e = c.e
    momentos = []
    for i, p in enumerate(e.prot["pasos"]):
        e.i = i
        e.lectura = {"ml": 56, "zona": V.PASADO, "cerca": False, "exceso_ml": 6} if p.get("medir_ml") else None
        momentos.append(C.vistas(e))
    e.intrusos = [{"tipo": "celular", "poligono_mm": [[300, 400], [370, 400], [370, 550], [300, 550]], "centro_mm": [335, 475]}]
    momentos.append(C.vistas(e))
    e.intrusos, e.terminado = [], True
    momentos.append(C.vistas(e))
    g = e.grilla.esquinas_mm
    for ws in momentos:
        for w in ws:
            assert w["rect_mm"][0] >= C.BARRA_MM, (w["id"], w["rect_mm"])
            if w["id"].startswith("lab-ambiente"):
                assert not C._se_tocan(w["rect_mm"], __import__("numpy").array(g), margen=0), w["rect_mm"]
    # Un preset viejo que empieza bajo la barra se corre y avisa.
    assert C._fuera_de_la_barra([30, 64, 360, 420], "panel_mm") == [C.BARRA_MM, 64.0, 360.0, 420.0]
    assert "barra lateral" in capsys.readouterr().err

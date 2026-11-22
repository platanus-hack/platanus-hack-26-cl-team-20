"""Partes puras de la voz: sin red, sin ElevenLabs y sin mesa."""

import asyncio
import time

from sdvoz import agentes
from sdvoz.escenas import ESCENAS, escena
from sdvoz.herramientas import (BARRA_MM, CATALOGO, ESQUINA_MM, MESA_ANCHO, client_config, describir_evento,
                                junto_a, resolver, texto_widget)
from sdvoz.servidor import Oido, _plano
from sdvoz.sim import objetos as objetos_sim

OBJS = objetos_sim()


def test_resolver_por_nombre_id_y_articulo():
    assert resolver(OBJS, "Saturno", "solar:")["id"] == "solar:saturno"
    assert resolver(OBJS, "la Tierra", "solar:")["id"] == "solar:tierra"
    assert resolver(OBJS, "jupiter", "solar:")["id"] == "solar:jupiter"      # sin tilde
    assert resolver(OBJS, "Júpiter", "solar:")["id"] == "solar:jupiter"
    assert resolver(OBJS, "solar:marte")["id"] == "solar:marte"
    assert resolver(OBJS, "el vaso")["id"] == "lab:vaso"
    assert resolver(OBJS, "Plutón") is None


def test_junto_a_no_pisa_franja_ni_esquina_y_queda_en_la_mesa():
    for o in OBJS:
        x, y, w, h = junto_a(o, 340, 160, 563)
        assert x >= BARRA_MM and x + w <= MESA_ANCHO
        assert y >= 0 and y + h <= 563
        assert not (x + w > MESA_ANCHO - ESQUINA_MM and y < ESQUINA_MM)


def test_describir_eventos():
    assert "marte" in describir_evento({"type": "widget_pressed", "widget_id": "solar-lamina", "value": "marte"})
    n = describir_evento({"type": "flow_emitted", "event": "nivel",
                          "data": {"ml": 56.2, "objetivo_ml": 50, "estado": "te_pasaste", "exceso_ml": 6}})
    assert n == "vaso: 56 ml de 50, te pasaste (sobran 6 ml)"
    assert describir_evento({"type": "object_moved", "object_id": "x"}) is None
    assert "libreta" in describir_evento({"type": "flow_emitted", "event": "libreta_nueva", "data": {"pagina": 3}})


def test_texto_widget_quita_html_y_estilos():
    w = {"id": "lab-paso", "params": {"html": "<style>.a{}</style><h1>Paso 2</h1><p>Pon 50 ml</p>", "css": "x"}}
    assert texto_widget(w) == "Paso 2 Pon 50 ml"


def test_plano_normaliza_las_tres_formas():
    assert _plano({"seq": 3, "payload": {"type": "widget_pressed", "widget_id": "a"}})["type"] == "widget_pressed"
    assert _plano({"event": {"type": "object_removed", "object_id": "b"}})["type"] == "object_removed"
    assert _plano({"event": "nivel", "data": {"ml": 3}}) == {"type": "flow_emitted", "event": "nivel", "data": {"ml": 3}}


def _oido_con(clave):
    oido = Oido(None, {})
    q = asyncio.Queue()
    oido.subs.add((clave, q))
    return oido, q


def test_oido_nivel_habla_solo_al_cambiar_de_zona_y_al_lab():
    oido, q_lab = _oido_con("lab")
    q_solar = asyncio.Queue()
    oido.subs.add(("solar", q_solar))
    ev = {"event": "nivel", "data": {"vaso": "lab:vaso", "ml": 56, "objetivo_ml": 50, "estado": "te_pasaste",
                                     "exceso_ml": 6, "escena": "laboratorio"}}
    oido.recibir(ev, "test")
    m = q_lab.get_nowait()
    assert m["tipo"] == "hablar" and m["urgente"]
    assert q_solar.empty()                                   # Lumo no habla del vaso
    oido.recibir({**ev, "data": {**ev["data"], "ml": 57}}, "test")   # misma zona: no vuelve a hablar
    assert all(x["tipo"] != "hablar" for x in [q_lab.get_nowait() for _ in range(q_lab.qsize())])


def test_oido_ignora_la_interfaz_de_la_mesa_y_avisa_cambio_de_pestana():
    oido, q = _oido_con("solar")
    oido.recibir({"type": "widget_pressed", "widget_id": "pcx:tab:2"}, "test")
    assert q.empty()
    oido.recibir({"type": "flow_emitted", "event": "scene_changed", "data": {"scene": "Laboratorio"}}, "test")
    assert q.get_nowait() == {"tipo": "escena", "pestana": "Laboratorio", "texto": "[mesa] cambió la pestaña a Laboratorio"}


def _alta(oido, oid, label):
    oido.recibir({"type": "object_appeared", "object": {"id": oid, "label": label}}, "test")


def _baja(oido, oid, label):
    oido.recibir({"type": "object_removed", "object_id": oid, "label": label}, "test")


def _vaciar(q):
    return [q.get_nowait() for _ in range(q.qsize())]


def test_cambios_parpadeo_y_rearmado_no_se_avisan():
    from sdvoz.servidor import Cambios
    c = Cambios(estable_s=5)
    c.baja("solar:sol", "Sol", 0)
    c.alta("solar:sol", "Sol", 3)                            # volvió antes de 5 s
    c.baja("solar:venus#1", "Venus", 1)
    c.alta("solar:venus#2", "Venus", 2)                      # re-armado: otro id, mismo nombre
    assert c.maduros(4) == ([], []) and c.maduros(10) == ([], [])
    assert c.ignorados == 2 and not c.cambio


def test_cambios_avisa_lo_que_se_mantiene_y_no_antes():
    from sdvoz.servidor import Cambios
    c = Cambios(estable_s=5)
    c.alta("solar:marte", "Marte", 0)
    c.baja("solar:sol", "Sol", 0)
    assert c.maduros(4.9) == ([], [])
    assert c.maduros(5.0) == (["Marte"], ["Sol"])
    c.alta("solar:marte", "Marte", 6)                        # alta repetida del mismo id: nada nuevo
    assert c.maduros(20) == ([], [])


def test_cambios_agrupa_lo_que_madura_junto_sin_adelantar():
    from sdvoz.servidor import Cambios
    c = Cambios(estable_s=5, junta_s=1.5)
    c.alta("a", "Tierra", 0)
    c.alta("b", "Luna", 1)
    assert c.maduros(5.2) == ([], [])                        # Luna está por madurar: espera
    assert c.maduros(6.0) == (["Tierra", "Luna"], [])


def test_oido_no_comenta_objetos_que_entran_y_salen_en_cada_rearmado():
    oido, q = _oido_con("solar")
    _baja(oido, "solar:sol", "Sol")
    _alta(oido, "solar:venus", "Venus")
    _alta(oido, "solar:marte", "Marte")
    _alta(oido, "solar:sol", "Sol")
    _baja(oido, "solar:venus", "Venus")
    _baja(oido, "solar:marte", "Marte")
    oido.avisar_cambios(time.monotonic() + 10)
    assert _vaciar(q) == []
    _alta(oido, "solar:saturno", "Saturno")
    oido.avisar_cambios(time.monotonic() + 1)
    assert _vaciar(q) == []
    oido.avisar_cambios(time.monotonic() + 10)
    assert _vaciar(q) == [{"tipo": "hablar", "texto": "[mesa] apareció Saturno"}]
    _baja(oido, "solar:saturno", "Saturno")
    oido.avisar_cambios(time.monotonic() + 10)
    assert _vaciar(q) == [{"tipo": "contexto", "texto": "[mesa] se fue Saturno"}]


def test_oido_avisa_solo_lo_estable_con_el_bucle_real():
    from sdvoz.servidor import Cambios

    async def correr():
        oido, q = _oido_con("solar")
        oido.cambios = Cambios(estable_s=0.6, junta_s=0.2)
        _baja(oido, "solar:sol", "Sol")
        await asyncio.sleep(0.2)
        _alta(oido, "solar:sol", "Sol")                      # parpadeo
        _alta(oido, "solar:marte", "Marte")
        await asyncio.sleep(0.3)
        assert _vaciar(q) == []                              # Marte aún no se mantiene
        await asyncio.sleep(1.2)
        return _vaciar(q), oido.tarea_cambios.done()

    mensajes, termino = asyncio.run(correr())
    assert mensajes == [{"tipo": "hablar", "texto": "[mesa] apareció Marte"}] and termino


def test_oido_entrega_preset_hecho_a_la_tool_que_espera():
    from sdvoz.herramientas import Contexto
    ctx = Contexto(None, escena("solar"))
    oido = Oido(None, {"solar": ctx})
    oido.recibir({"type": "flow_emitted", "event": "preset_hecho", "data": {"id": "voz-1", "ok": True}}, "test")
    assert ctx.respuestas["voz-1"]["ok"] is True


def test_client_tools_validas_para_elevenlabs():
    nombres = [h.nombre for h in CATALOGO]
    assert len(nombres) == len(set(nombres))
    for h in CATALOGO:
        c = client_config(h)
        assert c["type"] == "client" and c["parameters"]["type"] == "object"
        for k, v in c["parameters"]["properties"].items():
            assert v.get("description"), (h.nombre, k)            # ElevenLabs exige descripción por parámetro
        assert set(c["parameters"]["required"]) <= set(c["parameters"]["properties"])


def test_payload_de_los_agentes():
    for clave, e in ESCENAS.items():
        b = agentes.agent_body(e, ["t1"], {"llm": "claude-haiku-5-5"})
        cc = b["conversation_config"]
        assert b["name"].startswith("Superhuman Desk · ") and agentes.TAG in b["tags"]
        assert cc["agent"]["language"] == "es" and cc["turn"]["turn_eagerness"] in ("patient", "normal", "eager")
        assert b["platform_settings"]["auth"]["enable_auth"] is (clave != "solar")   # solo Lumo es público (q50)
        assert b["platform_settings"]["overrides"]["conversation_config_override"]["conversation"]["text_only"]
        assert "skip_turn" in cc["agent"]["prompt"]["built_in_tools"]
        assert "preset_leer" in cc["agent"]["prompt"]["prompt"]


def test_zonas_de_las_escenas_no_pisan_la_barra_ni_el_boton_de_voz():
    for e in ESCENAS.values():
        for x, y, w, h in (e.panel_mm, e.aviso_mm):
            assert x >= BARRA_MM, (e.clave, x)
            assert not (x + w > MESA_ANCHO - ESQUINA_MM and y < ESQUINA_MM), e.clave


def test_oido_no_se_cuenta_sus_propios_pedidos():
    oido, q = _oido_con("lab")
    oido.recibir({"type": "flow_emitted", "event": "paso_siguiente",
                  "data": {"preset": "lab", "id": "voz-1-1", "de": "voz", "by": "agent", "scene": "Laboratorio"}}, "t")
    assert q.empty()


def test_leer_acceso_en_sus_formatos(tmp_path):
    from sdvoz.acceso import leer_acceso
    casos = {
        "a.json": ('{"mcp": "https://mesa.example.ts.net/mcp", "user_token": "tok_A-1"}', "https://mesa.example.ts.net", "tok_A-1"),
        "b.txt": ('claude mcp add --transport http pcx https://gw.example.com/mcp --header "Authorization: Bearer tok_B.2"',
                  "https://gw.example.com", "tok_B.2"),
        "c.txt": ("abre https://gw.example.com/mesa#s=ses1.nodo_x.tok_C3", "https://gw.example.com", "tok_C3"),
        "d.env": ("PCX_MCP_URL=http://192.0.2.10:9210/mcp\nPCX_USER_TOKEN=tok_D4\n", "http://192.0.2.10:9210", "tok_D4"),
    }
    for nombre, (texto, gw, tok) in casos.items():
        p = tmp_path / nombre
        p.write_text(texto)
        assert leer_acceso(str(p)) == (gw, tok), nombre


def test_tools_solo_desde_la_pagina_de_este_servidor():
    from starlette.requests import Request

    from sdvoz.servidor import _origen_ok

    def req(**h):
        return Request({"type": "http", "headers": [(k.replace("_", "-").encode(), v.encode()) for k, v in h.items()]})

    assert _origen_ok(req(host="127.0.0.1:9325"))  # curl o `probar`: sin Origin
    assert _origen_ok(req(host="127.0.0.1:9325", origin="http://127.0.0.1:9325"))
    assert _origen_ok(req(host="localhost:9325", origin="http://localhost:9325"))
    assert not _origen_ok(req(host="127.0.0.1:9325", origin="https://otro-sitio.example"))  # otra página en el Mac
    assert not _origen_ok(req(host="127.0.0.1:9325", origin="null"))
    assert not _origen_ok(req(host="rebind.example:9325", origin="http://rebind.example:9325"))  # DNS rebinding
    assert _origen_ok(req(host="mac.example.ts.net", origin="https://mac.example.ts.net", tailscale_user_login="x"))

"""Piano Hero sin hardware: canciones y niveles, .mid, juicio de tiempo, puntaje, práctica, render con las teclas de
scan2 (copia en tests/data), fuente MIDI falsa y el puente UDP PC → Mac por localhost."""

from __future__ import annotations

import importlib.util
import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from playground.pianohero.game import Game
from playground.pianohero.layout import from_keys, load_layout, synthetic
from playground.pianohero.songs import (BUILTIN, LEVELS, Note, Song, _racimos, es_negra, fit_range, get_song, load_midi,
                                        simplify)

ROOT = Path(__file__).resolve().parent.parent
KEYS2 = ROOT / "tests" / "data" / "keys_scan2.json"


def _bridge_mod():
    spec = importlib.util.spec_from_file_location("midi_bridge", ROOT / "windows" / "midi_bridge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------------------------ canciones y niveles

@pytest.mark.parametrize("name", sorted(BUILTIN))
def test_niveles_simplifican(name):
    song = get_song(name)
    l1, l2, l3 = (simplify(song, n) for n in (1, 2, 3))
    for s in (l1, l2, l3):
        assert s.notes and all(36 <= n.midi <= 96 for n in s.notes)
        assert [n.t for n in s.notes] == sorted(n.t for n in s.notes)
    # fácil: una mano, una nota a la vez, solo blancas, rango acotado, separadas y más lenta
    assert {n.hand for n in l1.notes} == {"R"}
    assert all(len(g) == 1 for g in _racimos(l1.notes))
    assert not any(es_negra(n.midi) for n in l1.notes)
    assert max(n.midi for n in l1.notes) - min(n.midi for n in l1.notes) <= 16
    assert min(b.t - a.t for a, b in zip(l1.notes, l1.notes[1:])) >= LEVELS[1].min_gap["R"] - 1e-6
    assert l1.duration > l2.duration > l3.duration * 0.99
    # medio: dos manos, una nota por mano a la vez, la izquierda espaciada
    assert {n.hand for n in l2.notes} == {"R", "L"}
    for hand in "RL":
        mano = [n for n in l2.notes if n.hand == hand]
        assert all(len(g) == 1 for g in _racimos(mano))
    izq = [n.t for n in l2.notes if n.hand == "L"]
    assert min(b - a for a, b in zip(izq, izq[1:])) >= LEVELS[2].min_gap["L"] - 1e-6
    # difícil: todo lo del arreglo
    assert len(l3.notes) >= len(l2.notes) >= len(l1.notes)


def test_dificil_tiene_acordes_y_negras():
    oda3 = simplify(get_song("oda"), 3)
    assert any(len([n for n in g if n.hand == "L"]) >= 2 for g in _racimos(oda3.notes))
    elisa3 = simplify(get_song("elisa"), 3)
    assert any(es_negra(n.midi) for n in elisa3.notes)
    assert not any(es_negra(n.midi) for n in simplify(get_song("elisa"), 1).notes)   # E D# E queda E D E


def test_nombres_y_alias():
    assert get_song("Para Elisa").name == "elisa" and get_song("fur elise").name == "elisa"
    assert get_song("Oda a la Alegría").name == "oda" and get_song("est").name == "estrellita"
    with pytest.raises(KeyError):
        get_song("no existe")


def test_midi_tempo_manos_y_transposicion(tmp_path):
    import mido
    mid = mido.MidiFile(ticks_per_beat=480)
    der, izq, bat = mido.MidiTrack(), mido.MidiTrack(), mido.MidiTrack()
    mid.tracks += [der, izq, bat]
    der.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))           # 120 bpm
    der.append(mido.MetaMessage("time_signature", numerator=3, denominator=4, time=0))
    # derecha: acorde (100, 104, 107: fuera de rango por arriba) en 0, luego 2 negras; cambio de tempo a 60 bpm en la 2ª
    for n in (100, 104, 107):
        der.append(mido.Message("note_on", note=n, velocity=90, time=0))
    der.append(mido.Message("note_off", note=100, time=480)); der.append(mido.Message("note_off", note=104, time=0))
    der.append(mido.Message("note_off", note=107, time=0))
    der.append(mido.MetaMessage("set_tempo", tempo=1000000, time=0))
    der.append(mido.Message("note_on", note=102, velocity=90, time=0)); der.append(mido.Message("note_off", note=102, time=480))
    der.append(mido.Message("note_on", note=103, velocity=90, time=0)); der.append(mido.Message("note_off", note=103, time=480))
    # izquierda: graves (24 = C1, fuera de rango por abajo)
    for t in (0, 960, 0):
        izq.append(mido.Message("note_on", note=24 + (t > 0) * 7, velocity=80, time=t))
        izq.append(mido.Message("note_off", note=24 + (t > 0) * 7, time=240))
    bat.append(mido.Message("note_on", channel=9, note=36, velocity=100, time=0))   # batería: se ignora
    bat.append(mido.Message("note_off", channel=9, note=36, time=100))
    p = tmp_path / "prueba.mid"
    mid.save(p)

    s = load_midi(p)
    assert s.title == "prueba" and s.beats_per_bar == 3 and round(s.bpm) == 120
    der_n = sorted((n for n in s.notes if n.hand == "R"), key=lambda n: n.t)
    assert {n.hand for n in s.notes} == {"R", "L"} and len(der_n) == 5
    assert [round(n.t, 3) for n in der_n[3:]] == [0.5, 1.5]                   # 480 ticks a 120 bpm, luego a 60 bpm
    assert round(der_n[3].dur, 3) == 1.0
    assert all(n.midi != 36 or n.hand == "L" for n in s.notes)                 # el bombo no entró
    s3 = simplify(s, 3)
    assert all(36 <= n.midi <= 96 for n in s3.notes)
    s1 = simplify(s, 1)
    assert {n.hand for n in s1.notes} == {"R"} and len(_racimos(s1.notes)) == len(s1.notes)
    assert get_song(str(p)).name == s.name


def test_fit_range_no_mueve_lo_que_cabe():
    notas = [Note(0, 1, m) for m in (48, 60, 72)]
    assert [n.midi for n in fit_range(notas)] == [48, 60, 72]
    assert [n.midi for n in fit_range([Note(0, 1, m) for m in (100, 110)])] == [76, 86]       # 2 octavas abajo: cabe todo


# ------------------------------------------------------------------------------------ juicio y puntaje

def _cancion(midis, paso=1.0, t0=1.0):
    return Song("t", "Prueba", [Note(t0 + i * paso, 0.4, m) for i, m in enumerate(midis)], 60, 4)


def _juego(midis, level=2, **kw):
    g = Game(_cancion(midis), level, lead_in=0.0, **kw)
    # el nivel cambia el tempo: se trabaja con los tiempos ya escalados
    g.start(1000.0)
    return g


def test_juicio_por_ventanas_y_fallo():
    g = _juego([60, 62, 64, 65, 67])
    w0, w1, w2 = g.level.windows
    t = [n.t for n in g.notes]
    g.update(1000.0 + t[0] + 0.01, [("on", 60, 1000.0 + t[0] + 0.01)])
    g.update(1000.0 + t[1] - (w0 + w1) / 2, [("on", 62, 1000.0 + t[1] - (w0 + w1) / 2)])
    g.update(1000.0 + t[2] + (w1 + w2) / 2, [("on", 64, 1000.0 + t[2] + (w1 + w2) / 2)])
    g.update(1000.0 + t[3] + w2 + 0.05)                                       # la 4ª pasa sin tocarse
    assert [n.judge for n in g.notes[:4]] == ["perfecto", "bien", "ok", "fallo"]
    assert g.counts == {"perfecto": 1, "bien": 1, "ok": 1, "fallo": 1} and g.combo == 0
    assert g.notes[1].dt < 0 < g.notes[2].dt                                  # adelantada / atrasada
    assert abs(g.accuracy - (1 + 0.7 + 0.4 + 0) / 4) < 1e-9


def test_evento_atrasado_se_juzga_a_su_hora():
    g = _juego([60])
    t = g.notes[0].t
    # el cuadro llega 120 ms después del toque: cuenta la hora del toque, no la del cuadro
    g.update(1000.0 + t + 0.12, [("on", 60, 1000.0 + t + 0.005)])
    assert g.notes[0].judge == "perfecto" and abs(g.notes[0].dt - 0.005) < 1e-6


def test_cuenta_regresiva_sin_errores():
    g = Game(_cancion([60, 62]), 2, lead_in=3.0)
    g.start(1000.0)
    g.update(1001.0, [("on", 70, 1001.0)])                                    # song_t = -2: todavía no empieza
    assert g.wrong == 0 and g.song_t < 0


def test_offset_compensa_latencia():
    g = _juego([60], offset_ms=80)
    t = g.notes[0].t
    g.update(1000.0 + t + 0.08, [("on", 60, 1000.0 + t + 0.08)])
    assert g.notes[0].judge == "perfecto" and abs(g.notes[0].dt) < 1e-6


def test_errores_y_combo_por_nivel():
    g2 = _juego([60, 62], level=2)
    t = [n.t for n in g2.notes]
    g2.update(1000 + t[0], [("on", 60, 1000 + t[0])])
    g2.update(1000 + t[0] + 0.3, [("on", 70, 1000 + t[0] + 0.3)])             # tecla equivocada
    assert g2.wrong == 1 and g2.combo == 0
    g1 = _juego([60, 62], level=1)
    t = [n.t for n in g1.notes]
    g1.update(1000 + t[0], [("on", 60, 1000 + t[0])])
    g1.update(1000 + t[0] + 0.3, [("on", 70, 1000 + t[0] + 0.3)])
    assert g1.wrong == 1 and g1.combo == 1                                    # en fácil un error no corta el combo
    assert g1.feedback[-1]["kind"] == "wrong"


def test_puntaje_con_multiplicador_y_estrellas():
    g = _juego([60 + (i % 5) for i in range(12)], level=3)
    for n in g.notes:
        g.update(1000 + n.t, [("on", n.midi, 1000 + n.t), ("off", n.midi, 1000 + n.t + 0.05)])
    assert g.counts["perfecto"] == 12 and g.max_combo == 12
    assert g.score == 300 * 9 + 300 * 2 * 3                                  # x2 desde el combo 10
    assert g.stars() == 3
    g.update(1000 + g.notes[-1].t + 2.0)
    assert g.state == "finished"
    s = g.summary()
    assert s["accuracy"] == 1.0 and s["counts"]["perfecto"] == 12 and s["mean_dt_ms"] == 0.0


def test_practica_espera_la_nota():
    g = _juego([60, 64], level=2, practice=True)
    t0 = g.notes[0].t
    g.update(1000 + t0 + 5.0)                                                 # pasan 5 s sin tocar
    assert g.waiting and abs(g.song_t - t0) < 1e-9 and g.notes[0].state == "pending" and g.counts["fallo"] == 0
    g.update(1000 + t0 + 5.1, [("on", 61, 1000 + t0 + 5.1)])                  # equivocada: no avanza
    assert g.wrong == 1 and g.notes[0].state == "pending"
    g.update(1000 + t0 + 5.2, [("on", 60, 1000 + t0 + 5.2)])
    assert g.notes[0].state == "hit"
    g.update(1000 + t0 + 5.5)
    assert g.song_t > t0 and abs(g.accuracy - 0.5) < 1e-9                    # 1 acierto, 1 error


def test_pausa_detiene_el_reloj():
    g = _juego([60])
    g.update(1000.5)
    g.pause(1000.5)
    g.update(1010.0)
    assert abs(g.song_t - 0.5) < 1e-9 and g.counts["fallo"] == 0
    g.resume(1010.0); g.update(1010.25)
    assert abs(g.song_t - 0.75) < 1e-9


# ------------------------------------------------------------------------------------ geometría y render

def test_layout_desde_keys_scan2():
    lay = load_layout(KEYS2, refit=False)
    assert len(lay.keys) == 61 and lay.lo == 36 and lay.hi == 96 and lay.method == "bordes"
    c4 = lay.keys[60]
    # el carril sale del borde trasero hacia atrás (arriba en el proyector) y conserva el ancho de la tecla
    assert c4.at(0, 0)[1] == pytest.approx(c4.poly[0][1]) and c4.at(100, 0.5)[1] < c4.poly[0][1] - 90
    ancho0 = np.linalg.norm(c4.at(0, 1) - c4.at(0, 0)); ancho1 = np.linalg.norm(c4.at(lay.lane_px, 1) - c4.at(lay.lane_px, 0))
    assert abs(ancho1 - ancho0) < 0.25 * ancho0
    # carriles vecinos pegados: el borde derecho de C4 es el izquierdo de D4 a cualquier altura
    d4 = lay.keys[62]
    assert np.linalg.norm(c4.at(120, 1) - d4.at(120, 0)) < 2.0
    w, h = lay.hud[2], lay.hud[3]
    assert w > 100 and h > 50
    lay2 = lay.scaled((960, 540))
    assert np.allclose(lay2.keys[60].poly, lay.keys[60].poly / 2)


def _rt(tmp_path, size=(1920, 1080)):
    from playground.runtime import Runtime
    return Runtime(canvas_size=size, demo=False, requests_dir=tmp_path / "requests")


def _px(img, p):
    return img[int(round(p[1])), int(round(p[0]))].astype(int)


def test_render_cuadro_con_teclas_scan2(tmp_path):
    rt = _rt(tmp_path)
    out = rt.command("pianohero", {"action": "start", "level": 1, "song": "escala", "keys": str(KEYS2), "prefer": "fake"})
    assert out["state"] == "playing" and out["layout"]["keys"] == 61 and out["keyboard"]["source"] == "fake"
    inst = next(i for i in rt.instances.values() if i.name == "pianohero")
    g, lay = inst.game, inst.layout
    n0 = g.notes[0]
    k = lay.keys[n0.midi]
    # la primera nota a mitad del carril
    g.song_t = n0.t - g.lead / 2; g._last = time.time()
    rt.tick(0.0)
    img = rt.frame
    s_mid = lay.lane_px / 2
    assert _px(img, k.at(s_mid + 15, 0.5)).max() > 150                       # la nota está en su carril
    lejos = lay.keys[n0.midi + 19]
    assert _px(img, lejos.at(s_mid + 15, 0.5)).max() < 40                     # otro carril, oscuro
    x, y, w, h = lay.hud
    assert img[int(y):int(y + h), int(x):int(x + w)].max() > 200             # HUD
    # acierto: la tecla entera se ilumina verde (perfecto)
    g.song_t = n0.t; g._last = time.time()
    rt.command("simulate", {"device": "ek50", "control": f"note{n0.midi}", "value": 0.8, "state": "down"})
    rt.tick(0.0)
    assert n0.judge == "perfecto"
    b, gr, r = _px(rt.frame, k.poly[2] * 0.5 + k.poly[3] * 0.5 + (k.poly[0] - k.poly[3]) * 0.15)
    assert gr > 200 and r < 120 and b < 160                                   # RGB [60,255,120] → verde
    # fallo: la siguiente pasa sin tocar → su tecla en rojo
    rt.command("simulate", {"device": "ek50", "control": f"note{n0.midi}", "value": 0.0, "state": "up"})
    n1 = g.notes[1]
    g.song_t = n1.t + g.level.windows[2] + 0.05; g._last = time.time()
    rt.tick(0.0)
    assert n1.judge == "fallo"
    k1 = lay.keys[n1.midi]
    b, gr, r = _px(rt.frame, k1.poly[2] * 0.5 + k1.poly[3] * 0.5 + (k1.poly[0] - k1.poly[3]) * 0.15)
    assert r > 200 and gr < 80
    st = rt.command("pianohero", {"action": "state"})
    assert st["counts"]["perfecto"] == 1 and st["counts"]["fallo"] == 1 and st["score"] == 300


def test_runtime_entrega_entradas_y_controla_el_juego(tmp_path):
    rt = _rt(tmp_path, (640, 360))
    assert rt.command("pianohero", {"action": "state"})["running"] is False
    rt.command("connect_keyboard", {"prefer": "fake"})
    rt.command("simulate", {"device": "ek50", "control": "note60", "value": 1.0, "state": "down"})
    rt.tick(0.01)
    assert [(e["control"], e["state"]) for e in rt.scene.inputs] == [("note60", "down")] and rt.scene.inputs[0]["t"] > 0
    rt.tick(0.01)
    assert rt.scene.inputs == []                                              # cada evento se entrega una sola vez
    out = rt.command("pianohero", {"action": "level", "level": 2, "song": "oda", "keys": str(KEYS2), "prefer": "none"})
    assert out["level"] == 2 and out["state"] == "playing"
    assert rt.command("pianohero", {"action": "pause"})["state"] == "paused"
    out = rt.command("pianohero", {"action": "song", "song": "estrellita"})
    assert out["title"] == "Estrellita" and out["state"] == "ready"           # canción nueva: espera una tecla
    rt.command("simulate", {"device": "ek50", "control": "note60", "value": 1.0, "state": "down"})
    rt.tick(0.01)
    assert rt.command("pianohero", {"action": "state"})["state"] == "playing"  # tocar una tecla la empieza
    rt.tick(0.03)                                                             # el lienzo chico también dibuja
    assert rt.command("pianohero", {"action": "stop"})["stopped"] is True
    assert not rt.instances


def test_hot_reload_conserva_el_juego(tmp_path):
    rt = _rt(tmp_path, (640, 360))
    rt.command("pianohero", {"action": "start", "level": 1, "song": "oda", "keys": str(KEYS2), "prefer": "fake"})
    iid, inst = next(iter(rt.instances.items()))
    inst.game.score = 4242
    eff = ROOT / "playground" / "effects" / "pianohero.py"
    import os
    st = eff.stat()
    os.utime(eff, (st.st_atime, st.st_mtime + 5))
    try:
        rt.tick(0.01)
        assert rt.instances[iid] is not inst and rt.instances[iid].game.score == 4242
    finally:
        os.utime(eff, (st.st_atime, st.st_mtime))


def test_sintetico_sin_keys():
    lay = synthetic()
    assert len(lay.keys) == 61 and lay.keys[61].black and not lay.keys[60].black
    lay2 = from_keys(json.loads(KEYS2.read_text()))
    assert lay2.lane_px > 100


# ------------------------------------------------------------------------------------ MIDI: fuente falsa y puente

def test_puente_udp_localhost():
    from playground.devices import DeviceRegistry
    from playground.devices.keyboard import BridgeMidiSource, ek50_device
    mb = _bridge_mod()
    br = mb.Bridge(listen_port=0, log=lambda *a: None)
    th = threading.Thread(target=br.serve_forever, daemon=True); th.start()
    reg = DeviceRegistry(); reg.add(ek50_device())
    evs = []
    reg.on_event = evs.append
    src = BridgeMidiSource("ek50", f"127.0.0.1:{br.listen_port}", listen_port=0, hello_s=0.1)
    reg.attach(src)
    try:
        t0 = time.time()
        while not br.subs and time.time() - t0 < 3:
            time.sleep(0.02)
        assert br.subs, "el hello no llegó al puente"
        assert br.send_midi([0x90, 60, 100]) and br.send_midi([0x80, 60, 0])
        assert not br.send_midi([0xF8])                                       # reloj: no se manda
        br.send_midi([0x90, 20, 90])                                          # fuera de las 61 teclas
        t0 = time.time()
        while (len(evs) < 2 or src.dropped < 1) and time.time() - t0 < 3:
            time.sleep(0.02)
        assert [(e["control"], e["state"]) for e in evs] == [("note60", "down"), ("note60", "up")]
        assert abs(evs[0]["value"] - 100 / 127) < 1e-3 and evs[0]["t_event"] > 0
        d = src.describe()
        assert d["notes"] == 2 and d["dropped"] == 1 and d["lost"] == 0 and d["rtt_ms"] is not None
        assert reg.devices["ek50"].status == "puente"
        # duplicado (mismo seq): se descarta
        assert src.handle_datagram(json.dumps({"k": "m", "s": 1, "b": [0x90, 62, 90]}).encode(), ("127.0.0.1", 1), time.time()) is None
        # fuera de la LAN: se ignora
        assert src.handle_datagram(json.dumps({"k": "m", "s": 99, "b": [0x90, 62, 90]}).encode(), ("8.8.8.8", 1), time.time()) is None
    finally:
        src.stop(); br.close()


def test_puente_partes():
    mb = _bridge_mod()
    assert mb.pick_port(["Microsoft GS Wavetable Synth 0", "Generic USB MIDI 0"]) == "Generic USB MIDI 0"
    assert mb.pick_port(["Generic USB MIDI 0", "KORG EK-50 MIDI"]) == "KORG EK-50 MIDI"
    assert mb.pick_port(["IAC Driver Bus 1"]) == ""
    assert mb.pick_port(["A", "Mi Teclado 2"], "teclado") == "Mi Teclado 2"
    assert mb.parse_target("192.168.1.38") == ("192.168.1.38", 8796) and mb.parse_target("10.0.0.2:9000") == ("10.0.0.2", 9000)
    assert mb.privada("100.101.2.3") and mb.privada("192.168.1.38") and not mb.privada("8.8.8.8")
    gen = mb.fake_notes(120)
    pasos = [next(gen) for _ in range(6)]
    on = [b for _, b in pasos if b and b[0] == 0x90]; off = [b for _, b in pasos if b and b[0] == 0x80]
    assert len(on) == 2 and [b[1] for b in on] == [b[1] for b in off] == [60, 62]


def test_traduccion_midi():
    from playground.devices.keyboard import midi_to_event, parse_hostport
    assert midi_to_event([0x90, 60, 127]) == ("note60", 1.0, "down")
    assert midi_to_event([0x91, 61, 0]) == ("note61", 0.0, "up")                # note_on con velocidad 0 = suelta
    assert midi_to_event([0x80, 62, 30]) == ("note62", 0.0, "up")
    assert midi_to_event([0xE0, 0, 64]) is None
    assert parse_hostport("192.168.1.233") == ("192.168.1.233", 8796)


# ------------------------------------------------------------------------------------ como efecto en otro runtime

def _escena_con_teclas(rt):
    """Como scene_from_scan del runtime de master: teclas en objects["keys"].extra["keys"], hoja en objects["papel"]."""
    from playground.scene import SceneObject
    teclas = json.loads(KEYS2.read_text())["teclas"]
    rt.scene.objects["keys"] = SceneObject("keys", "teclado", "instrument", [826, 700, 0, 1730, 795, 60],
                                           [[826, 795], [1730, 795], [1730, 700], [826, 700]], extra={"keys": teclas})
    rt.scene.objects["papel"] = SceneObject("papel", "hoja", "prop", [1370, 232, 0, 1656, 547, 1],
                                            [[1370, 232], [1656, 232], [1656, 547], [1370, 547]])


def test_efecto_lee_teclas_de_la_escena_y_demo(tmp_path):
    rt = _rt(tmp_path)
    _escena_con_teclas(rt)
    st = rt.spawn("pianohero", None, {"level": 2, "song": "oda", "demo": True})
    inst = rt.instances[st["id"]]
    rt.tick(0.0)
    lay = inst.layout
    assert lay.source == "escena:keys" and lay.hud_source == "papel" and len(lay.keys) == 61
    assert np.allclose(lay.keys[60].poly, json.loads(KEYS2.read_text())["teclas"][24]["poligono"])
    g = inst.game
    assert g.state == "playing"                                               # demo arranca solo
    n0 = g.notes[0]
    g.song_t = n0.t + 0.01; g._last = time.time()
    rt.tick(0.0)
    assert n0.state == "hit" and n0.judge == "perfecto"                       # se tocó solo, a tiempo
    # las teclas de la escena se corren (nudge): el layout se rehace
    for t in rt.scene.objects["keys"].extra["keys"]:
        t["poligono"] = [[x + 5, y] for x, y in t["poligono"]]
    rt.tick(0.0)
    assert np.allclose(inst.layout.keys[60].poly[:, 0], lay.keys[60].poly[:, 0] + 5)
    # cmd por set_param, para runtimes sin el comando pianohero
    rt.set_param(st["id"], "cmd", "pause")
    assert inst.game.state == "paused"
    rt.set_param(st["id"], "cmd", "resume")
    assert inst.game.state == "playing"


def test_efecto_sin_inputs_usa_controls():
    """En un runtime sin scene.inputs (master antes del merge), las teclas salen de los cambios de scene.controls."""
    from types import SimpleNamespace
    from playground.effects.pianohero import PianoHero
    eff = PianoHero(None, {"keys": str(KEYS2)}, "x1")
    t = time.time()
    scene = SimpleNamespace(objects={}, controls={"ek50": {"note60": {"value": 1, "state": "down", "t": t}}})
    assert eff._events(scene) == [("on", 60, t)]
    assert eff._events(scene) == []                                           # mismo evento: una sola vez
    scene.controls["ek50"]["note60"] = {"value": 0, "state": "up", "t": t + 0.2}
    assert eff._events(scene) == [("off", 60, t + 0.2)]
    eff.kill()

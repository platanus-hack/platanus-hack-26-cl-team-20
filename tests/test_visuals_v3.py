"""Visuales v3 como efectos del runtime: buda_breath, ddj_skin, torre_faces, papel_canvas, scene_texture.

Sin hardware. Un escaneo sintético (foto en px del proyector + segments/workbench + relieve) prueba que la geometría
fina sale de la foto (`playground.scan_geom`) y que cada efecto cae dentro de su objeto, respira en su lugar y sigue
los ajustes finos (nudge). Si existe captures/scan5 (o SLSCAN_SCAN), se renderizan también sobre la escena real.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from playground import scan_geom as sg
from playground.runtime import Runtime
from playground.scene import scene_from_scan

V3 = ("buda_breath", "ddj_skin", "torre_faces", "papel_canvas", "scene_texture")
DEFAULT = {"buda_breath": "buda", "ddj_skin": "ddj", "torre_faces": "torre", "papel_canvas": "papel"}

PAINTING = [[400, 180], [950, 90], [1040, 490], [480, 520]]
PAPER = [[1140, 140], [1350, 115], [1390, 425], [1185, 435]]
JOGS = [((870, 955), (100, 170), 62), ((1150, 965), (100, 170), 62)]
TORRE = [[130, 160], [370, 160], [370, 1070], [130, 1070]]


def _real_scan() -> Path | None:
    for c in (os.environ.get("SLSCAN_SCAN"), Path(__file__).parent.parent / "captures" / "scan5"):
        if c and (Path(c) / "scene_pov.png").exists() and (Path(c) / "segments.json").exists():
            return Path(c)
    return None


def _poly(img, pts, color):
    cv2.fillPoly(img, [np.asarray(pts, np.int32).reshape(-1, 1, 2)], color)


@pytest.fixture(scope="module")
def synth(tmp_path_factory) -> Path:
    """Un solo escaneo sintético para el módulo (~10 MB); las pruebas que escriben ajustes usan `synth_rw`."""
    return make_synth(tmp_path_factory.mktemp("synth"))


@pytest.fixture
def synth_rw(synth, tmp_path) -> Path:
    import shutil
    d = tmp_path / "scan"
    shutil.copytree(synth, d)
    return d


def make_synth(tmp_path: Path) -> Path:
    """Escaneo sintético: pared clara, tapiz saturado, pantalla negra con hoja blanca, DDJ oscura con dos platos
    claros (con etiqueta oscura), torre blanca. segments.json trae cajas y platos corridos, como el real."""
    pov = np.full((1080, 1920, 3), (200, 210, 200), np.uint8)
    _poly(pov, PAINTING, (0, 110, 240))                              # naranjo saturado (BGR)
    cv2.circle(pov, (700, 260), 60, (40, 220, 250), -1)              # cabeza: amarillo claro y saturado
    _poly(pov, [[560, 300], [640, 300], [640, 420], [560, 420]], (40, 200, 40))
    cv2.rectangle(pov, (1080, 80), (1450, 620), (20, 20, 20), -1)    # pantalla negra
    _poly(pov, PAPER, (245, 245, 245))                               # hoja carta
    cv2.rectangle(pov, (700, 800), (1280, 1079), (40, 40, 40), -1)   # cuerpo de la DDJ
    for c, ax, ang in JOGS:
        cv2.ellipse(pov, (c, ax, ang), (185, 185, 185), -1)
        cv2.ellipse(pov, (c, (ax[0] * 0.35, ax[1] * 0.35), ang), (60, 60, 60), -1)
    _poly(pov, TORRE, (235, 235, 235))
    cv2.imwrite(str(tmp_path / "scene_pov.png"), pov)
    seg = {"buda": {"bbox": [400, 90, 640, 430], "quad": [[380, 60], [1060, 60], [1060, 560], [380, 560]]},
           "papel": {"bbox": [1100, 90, 330, 360], "quad": [[1080, 80], [1450, 80], [1450, 620], [1080, 620]], "mm": [215.9, 279.4]},
           "ddj": {"bbox": [700, 800, 580, 280], "jogs": [[885, 942, 114], [1091, 926, 109]]},
           "torre": {"bbox": [130, 160, 240, 910]}}
    (tmp_path / "segments.json").write_text(json.dumps(seg))
    (tmp_path / "workbench.json").write_text(json.dumps({"torre": {"bbox": [130, 160, 240, 910], "contour": TORRE, "split_y": 360}}))
    yy, xx = np.mgrid[0:1080, 0:1920].astype(np.float32)
    hgt = 0.01 * yy + 20 * np.exp(-((xx - 250) ** 2 + (yy - 700) ** 2) / (2 * 120 ** 2))
    hgt[600:640, 220:260] = 400                                       # mancha suelta del gray code (debe ignorarse)
    np.save(tmp_path / "height_proj.npy", hgt.astype(np.float16))
    return tmp_path


def _rt(scene, size=(1920, 1080)):
    return Runtime(scene=scene, canvas_size=size, demo=False, requests_dir=Path("/tmp/_v3_req_unused"))


def _errors(rt):
    return [e for e in rt.events if e["type"] in ("effect_error", "reload_error")]


def _lit(frame, thr=12):
    return frame.max(axis=2) > thr


def _quad_err(a, b) -> float:
    return float(np.abs(np.asarray(sg.order_quad(a)) - np.asarray(sg.order_quad(b))).max())


# ---------------------------------------------------------------------- geometría desde la foto

def test_scan_geom_finds_real_shapes(synth):
    sd = sg.load(synth)
    assert _quad_err(sg.papel_quad(sd), PAPER) < 6                 # la hoja, no la pantalla entera
    jogs = sg.ddj_layout(sd)["jogs"]
    assert len(jogs) == 2
    for j, (c, ax, _) in zip(jogs, JOGS):
        assert np.hypot(j["c"][0] - c[0], j["c"][1] - c[1]) < 6
        assert abs(max(j["axes"]) - max(ax)) / max(ax) < 0.12
    ol = sg.buda_outline(sd)
    truth = sg.poly_mask((1080, 1920), PAINTING); got = sg.poly_mask((1080, 1920), ol["outline"])
    assert (truth & got).sum() / (truth | got).sum() > 0.9
    assert _quad_err(ol["quad"], PAINTING) < 12
    hx, hy = sg.buda_head(sd, ol["outline"])
    assert abs(hx - 700) < 40 and abs(hy - 260) < 40
    tf = sg.torre_faces(sd)
    assert tf["split_y"] == 360 and len(tf["contour"]) == 4
    r = sg.relief(sd, 130, 360, 240, 700, None)
    assert r.shape == (700, 240) and 0 <= r.min() and r.max() <= 1
    assert r[340, 120] > r[20, 120]                                  # la loma de la torre sube; la mancha no manda


def test_scene_from_scan_uses_fine_geometry(synth_rw):
    synth = synth_rw
    s = scene_from_scan(synth)
    assert _quad_err(s.objects["papel"].contour, PAPER) < 6
    assert _quad_err(s.objects["buda"].contour, PAINTING) < 12 and len(s.objects["buda"].extra["outline"]) >= 4
    assert s.objects["torre"].extra["split_y"] == 360
    assert len(s.objects["ddj"].extra["jog_ellipses"]) == 2 and abs(s.objects["ddj"].extra["jogs"][0][0] - 870) < 6
    s.nudge("ddj", 10, -5)                                           # el ajuste fino mueve también las elipses
    assert abs(s.objects["ddj"].extra["jog_ellipses"][0][0] - 880) < 6
    assert json.loads((synth / "ajustes.json").read_text())["ddj"] == [10.0, -5.0]


# ---------------------------------------------------------------------- efectos

@pytest.mark.parametrize("effect", V3)
def test_effect_lands_on_its_object(synth, effect):
    rt = _rt(scene_from_scan(synth))
    rt.spawn(effect)                                                  # sin objeto: va sobre el suyo
    for _ in range(3):
        f = rt.tick(0.05)
    assert not _errors(rt) and rt.registry.errors == {}
    lit = _lit(f)
    assert lit.sum() > 2000
    if effect in DEFAULT:
        o = rt.scene.objects[DEFAULT[effect]]
        inside = sg.poly_mask(f.shape, o.extra.get("outline") or o.contour, dilate=6)
        if effect == "ddj_skin":
            inside = sg.poly_mask(f.shape, o.contour, dilate=8)
        assert (lit & ~inside).sum() < 0.02 * lit.sum(), effect      # no se derrama fuera de lo real


def test_buda_breathes_in_place(synth):
    rt = _rt(scene_from_scan(synth))
    inst_id = rt.spawn("buda_breath", "buda", {"respiracion": 0.015})["id"]
    inst = rt.instances[inst_id]
    frames = []
    for t in (1.47, 4.41):                                            # máximo y mínimo de la respiración (~6 s)
        inst.t = t
        frames.append(rt.tick(0.0).astype(np.float32))
    assert np.abs(frames[0] - frames[1]).mean() > 0.5                 # algo respira
    cents = []
    for fr in frames:
        ys, xs = np.nonzero(fr.max(axis=2) > 12)
        cents.append((xs.mean(), ys.mean()))
    assert np.hypot(cents[0][0] - cents[1][0], cents[0][1] - cents[1][1]) < 3   # sin moverse de lugar


def test_effects_follow_nudge(synth_rw):
    rt = _rt(scene_from_scan(synth_rw))
    rt.spawn("papel_canvas", params={"modo": "texto", "texto": "hola"})
    before = _lit(rt.tick(0.05))
    rt.command("nudge", {"object": "papel", "dx": 20, "dy": 0})
    after = _lit(rt.tick(0.05))
    dx = np.nonzero(after)[1].mean() - np.nonzero(before)[1].mean()
    assert 15 < dx < 25


def test_ddj_skin_reacts_to_controls_and_audio(synth):
    rt = _rt(scene_from_scan(synth))
    e = rt.spawn("ddj_skin")["id"]
    inst = rt.instances[e]
    rt.tick(0.05)
    rt.scene.controls = {}
    rt.tick(0.05)
    quiet = rt.frame.astype(np.float32).sum()
    rt.set_audio({"bass": 1.0, "mid": 1.0}, hold=5.0)
    rt.tick(0.05)
    assert rt.frame.astype(np.float32).sum() > quiet                  # el bajo enciende anillos y bordes
    ctrl = inst._read_controls(type("S", (), {"controls": {"ddj": {"jog_l": {"value": 0.25, "state": "move", "t": 1.0},
                                                                    "pad_r_3": {"value": 1.0, "state": "on", "t": 1.0},
                                                                    "fader_l": {"value": 0.8, "state": "move", "t": 1.0}}}})())
    assert ctrl["jog"][0] == 0.25 and (1, 3) in ctrl["pads"] and ctrl["fader"][0] == 0.8


def test_papel_canvas_modes(synth, tmp_path):
    rt = _rt(scene_from_scan(synth))
    e = rt.spawn("papel_canvas", params={"modo": "estado"})["id"]
    rt.scene.hud = {"puntaje": 1234}
    rt.tick(0.05)
    assert any("puntaje: 1234" in ln for ln in rt.instances[e].lines)
    rt.set_param(e, "modo", "texto"); rt.set_param(e, "texto", "uno|dos")
    rt.tick(0.05)
    assert rt.instances[e].lines == ["uno", "dos"]
    img = np.zeros((40, 60, 3), np.uint8); img[:] = (0, 0, 255)
    cv2.imwrite(str(tmp_path / "x.png"), img)
    rt.set_param(e, "modo", "imagen"); rt.set_param(e, "imagen", str(tmp_path / "x.png"))
    f = rt.tick(0.05)
    assert f[..., 2].max() > 150 and not _errors(rt)                  # la imagen roja sale en la hoja


def test_scene_texture_on_one_object_and_vignette(synth):
    rt = _rt(scene_from_scan(synth))
    rt.spawn("scene_texture", "papel")
    f = _lit(rt.tick(0.05))
    q = sg.poly_mask(f.shape, rt.scene.objects["papel"].contour, dilate=20)
    assert f.sum() > 0 and (f & ~q).sum() == 0
    rt.kill()
    e = rt.spawn("scene_texture", params={"fondo": 0.0})["id"]
    f = rt.tick(0.05)
    assert f[30:60, 1600:1700].max() < 15                             # fondo apagado (pared lejos de todo objeto)


def test_small_canvas_and_demo_scene(synth):
    rt = _rt(scene_from_scan(synth), size=(640, 360))                  # lienzo chico: se escala
    for eff in V3:
        rt.spawn(eff)
    f = rt.tick(0.05)
    assert f.shape == (360, 640, 3) and f.max() > 0 and not _errors(rt)
    demo = Runtime(canvas_size=(640, 320), requests_dir=Path("/tmp/_v3_req_unused"))   # demo en mm, sin foto
    demo.spawn("buda_breath", "cuadro"); demo.spawn("ddj_skin", "teclado"); demo.spawn("torre_faces", "cuadro")
    demo.spawn("papel_canvas", "pantalla"); demo.spawn("scene_texture")
    f = demo.tick(0.05)
    assert f.max() > 0 and not _errors(demo)


def test_gallery_aliases_for_voice(synth):
    from playground.secretaria.tools import resolve_effect
    rt = _rt(scene_from_scan(synth))
    g = rt.registry.gallery()
    assert {"buda_breath", "ddj_skin", "torre_faces", "papel_canvas", "scene_texture"} <= {x["name"] for x in g}
    assert resolve_effect(g, "respira el Buda") == "buda_breath"
    assert resolve_effect(g, "que respire el buda") == "buda_breath"
    assert resolve_effect(g, "skin de la DDJ") == "ddj_skin"
    assert resolve_effect(g, "torre") == "torre_faces"
    assert resolve_effect(g, "la hoja") == "papel_canvas"
    assert resolve_effect(g, "skin a full detalle") == "scene_texture"
    assert resolve_effect(g, "halo") == "contour_glow"
    assert all(x.get("aliases") for x in g if x["name"] in V3)


# ---------------------------------------------------------------------- escena real (si está el escaneo)

@pytest.mark.skipif(_real_scan() is None, reason="sin captures/scan5")
def test_real_scan5_geometry_and_render():
    scan = _real_scan()
    s = scene_from_scan(scan)
    s.scan_dir = str(scan)
    papel = np.asarray(s.objects["papel"].contour, float)
    area = cv2.contourArea(papel.astype(np.float32))
    assert 40000 < area < 110000                                      # la hoja (~210x300 px), no la pantalla (~350x480)
    jogs = s.objects["ddj"].extra.get("jog_ellipses")
    assert jogs and len(jogs) == 2 and abs(jogs[0][1] - jogs[1][1]) < 60
    rt = _rt(s)
    for eff in V3:
        rt.spawn(eff)
    for _ in range(2):
        f = rt.tick(0.05)
    assert not _errors(rt) and f.max() > 0


def test_capa_orders_drawing(synth):
    rt = _rt(scene_from_scan(synth))
    a = rt.spawn("contour_glow", "papel")["id"]                      # capa 0
    b = rt.spawn("buda_breath")["id"]                                 # capa -10 por defecto: va debajo
    c = rt.spawn("papel_canvas", params={"capa": 5})["id"]
    assert [i for i, _ in rt.layered()] == [b, a, c]
    rt.set_param(a, "capa", 10)
    assert [i for i, _ in rt.layered()] == [b, c, a]
    rt.tick(0.05)
    assert not _errors(rt)

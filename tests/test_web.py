"""Visor 3D: estáticos en /web, /api/scene3d (escaneo sintético, demo y rig) y tamaño del JSON."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from playground.runtime import Runtime, build_app
from playground.web.scene3d import scan_dir, has_scan

W, H = 1920, 1080


def _rect(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def fake_scan(d: Path) -> Path:
    """Escaneo sintético con los mismos archivos y formatos que captures/scan2 (segment_pov + workbench)."""
    d.mkdir(parents=True, exist_ok=True)
    pov = np.full((H, W, 3), 190, np.uint8)
    pov[:, :20] = 0                                               # borde sin correspondencia
    cv2.rectangle(pov, (590, 160), (1100, 560), (40, 120, 230), -1)   # Buda (cálido, BGR)
    cv2.rectangle(pov, (830, 709), (1700, 790), (250, 250, 250), -1)  # teclado
    cv2.imwrite(str(d / "scene_pov.png"), pov)

    h = np.zeros((H, W), np.float32)
    h[217:368, 120:640] = 8.0                                     # torre: cara superior más alta
    h[368:1080, 120:640] = 3.0                                    # torre: cara frontal
    h[560:700, 830:1500] = 2.0                                    # cocodrilo
    h[232:547, 1370:1656] = -120.0                                # pozo falso del decode (hoja): se recorta
    h[500, 1000] = 90.0                                           # punto suelto: lo quita la mediana
    np.save(d / "height_proj.npy", h)

    gx, gy = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
    valid = np.ones((H, W), bool)
    valid[:, :20] = False
    np.savez(d / "proj_to_cam.npz", mapx=gx * 0.3, mapy=gy * 0.3, valid=valid, shift=np.zeros(2))

    whites = [[830 + 34 * i, 710, 29, 49] for i in range(25)]
    seg = {
        "buda": {"bbox": [594, 159, 512, 405], "quad": [[600, 569], [600, 160], [1100, 160], [1100, 569]]},
        "torre": {"bbox": [120, 217, 520, 863]},
        "papel": {"bbox": [1370, 232, 286, 315], "quad": _rect(1370, 232, 286, 315), "mm": [215.9, 279.4]},
        "teclado": {"bbox": [828, 710, 877, 49], "pitch_px": 33.73, "teclas_blancas": whites, "n_blancas": 25,
                    "teclas_negras": [[1104, 710, 29, 24], [1138, 710, 29, 24]], "n_negras": 2},
        "controladores": {"bbox": [643, 594, 1277, 445]},
        "ddj": {"jogs": [[1199, 861, 86], [1559, 889, 106]], "bbox": [1000, 759, 920, 280]},
        "circuit": {"bbox": [643, 759, 357, 280]},
        "cocodrilo": {"bbox": [828, 560, 690, 148], "contour": _rect(828, 560, 690, 148)},
    }
    (d / "segments.json").write_text(json.dumps(seg))
    wb = {
        "teclado": {"bbox": [828, 709, 877, 81], "rect": [[1266.0, 749.9], [877.0, 81.0], 0.0], "pitch_px": 33.73,
                    "teclas_blancas": [{"i": i, "x": 830 + 34 * i, "y": 709, "w": 30, "h": 81,
                                        "visible": 0.3 if 5 <= i <= 8 else 0.9} for i in range(26)],
                    "teclas_negras": [[1205, 709, 32, 25], [1239, 709, 32, 25], [1307, 709, 32, 25]]},
        "cocodrilo": {"contour": [[788, 647], [900, 560], [1300, 570], [1466, 620], [1300, 690], [900, 694]],
                      "bbox": [788, 544, 678, 151]},
        "torre": {"bbox": [120, 217, 520, 863], "contour": _rect(120, 217, 519, 862), "split_y": 368},
        "mesa": {"area_px": 1326220, "bbox": [2, 26, 1918, 1054]},
        "buda": {"quad": [[580, 571], [580, 160], [1105, 160], [1105, 571]], "bbox": [575, 159, 531, 408]},
    }
    (d / "workbench.json").write_text(json.dumps(wb))
    return d


@pytest.fixture
def scan(tmp_path, monkeypatch):
    d = fake_scan(tmp_path / "captures" / "scan2")
    monkeypatch.setenv("PLAYGROUND_SCAN", str(d))
    return d


def _client(tmp_path):
    rt = Runtime(canvas_size=(640, 360), requests_dir=tmp_path / "requests")
    return rt, TestClient(build_app(rt))


def test_web_static_files(tmp_path):
    _, c = _client(tmp_path)
    for path in ("/web/", "/web/index.html"):
        r = c.get(path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        assert "three@0.160.0" in r.text and 'src="app.js"' in r.text
    assert c.get("/web", follow_redirects=False).status_code in (301, 307)       # /web → /web/
    js = c.get("/web/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert "/api/scene3d" in js.text and "/api/events" in js.text
    assert js.headers["cache-control"] == "no-cache"                             # se edita en vivo
    assert c.get("/web/style.css").status_code == 200
    assert c.get("/web/scene3d.py").status_code == 404                           # el Python no se sirve
    assert c.get("/web/__init__.py").status_code == 404


def test_scene3d_from_scan(tmp_path, scan):
    _, c = _client(tmp_path)
    r = c.get("/api/scene3d")
    assert r.status_code == 200
    d = r.json()
    assert {"source", "size", "heightfield", "texture", "frame", "projector", "objects"} <= set(d)
    assert d["source"] == "scan" and d["size"] == [W, H]
    hf = d["heightfield"]
    assert (hf["nx"], hf["ny"]) == (192, 108)
    assert len(hf["xs"]) == 192 and len(hf["ys"]) == 108 and len(hf["z"]) == 192 * 108
    assert 0 <= hf["xs"][0] < hf["xs"][-1] <= W and 0 <= hf["ys"][0] < hf["ys"][-1] <= H
    z = np.array(hf["z"])
    assert np.isfinite(z).all() and z.max() > 0
    assert z.min() >= hf["clip"][0] * hf["z_scale"] - 0.5                         # el pozo falso quedó recortado
    assert d["frame"] == {"url": "/api/frame.png", "size": [640, 360]}
    assert d["projector"]["source"] == "supuesto" and d["projector"]["apex"][2] > 0

    objs = {o["id"]: o for o in d["objects"]}
    classes = {o["class"] for o in d["objects"]}
    assert {"buda", "torre", "papel", "cocodrilo", "teclado", "tecla_blanca", "tecla_negra", "ddj", "circuit"} <= classes
    for o in d["objects"]:
        assert {"id", "name", "class", "group", "role", "polygon", "z", "z_min", "z_max", "bbox", "center"} <= set(o)
        assert len(o["polygon"]) >= 3 and o["z_min"] <= o["z"] <= o["z_max"]
        assert all(-1 <= x <= W + 1 and -1 <= y <= H + 1 for x, y in o["polygon"])
    # workbench manda sobre segments: 26 blancas con `visible`, 3 negras, contorno fino del cocodrilo
    assert sum(o["class"] == "tecla_blanca" for o in d["objects"]) == 26
    assert sum(o["class"] == "tecla_negra" for o in d["objects"]) == 3
    assert objs["tecla_b06"]["extra"]["visible"] == 0.3 and objs["tecla_b06"]["group"] == "teclado"
    assert objs["cocodrilo"]["source"] == "workbench.json" and len(objs["cocodrilo"]["polygon"]) == 6
    # torre partida en dos caras por split_y; la de arriba es más alta
    assert objs["torre"]["role"] == "outline"
    assert objs["torre_superior"]["z"] > objs["torre_frontal"]["z"] > 0
    assert objs["torre_superior"]["bbox"][1] + objs["torre_superior"]["bbox"][3] <= 369
    # desde segments.json: papel, jogs del DDJ y Circuit
    assert objs["papel"]["extra"]["mm"] == [215.9, 279.4]
    assert objs["ddj_jog1"]["group"] == "ddj" and len(objs["ddj_jog1"]["polygon"]) == 32
    assert objs["circuit"]["role"] == "solid"

    # solo objetos (para refrescar en vivo) y textura
    only = c.get("/api/scene3d?parts=objects").json()
    assert "heightfield" not in only and len(only["objects"]) == len(d["objects"])
    tex = c.get("/api/scene3d/texture.jpg")
    assert tex.headers["content-type"] == "image/jpeg"
    img = cv2.imdecode(np.frombuffer(tex.content, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == (H, W, 3)


def test_heightfield_json_under_2mb(tmp_path, scan):
    _, c = _client(tmp_path)
    assert len(c.get("/api/scene3d").content) < 2_000_000
    big = c.get("/api/scene3d?nx=10000")                                          # se limita a 480 columnas
    assert big.json()["heightfield"]["nx"] == 480 and len(big.content) < 2_000_000


def test_projector_from_rig(tmp_path, scan):
    K = [[2400.0, 0, 960.0], [0, 2400.0, 1000.0], [0, 0, 1]]
    rig = {"format": "slscan-rig/1", "devices": {"proj": {"name": "proj", "kind": "projector", "size": [W, H], "K": K}}}
    (scan.parent / "rig.json").write_text(json.dumps(rig))
    _, c = _client(tmp_path)
    p = c.get("/api/scene3d").json()["projector"]
    assert p["source"] == "rig.json" and p["apex"] == [960.0, 1000.0, 2400.0]


def test_scene3d_demo_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYGROUND_SCAN", str(tmp_path / "no_existe"))
    rt, c = _client(tmp_path)
    d = c.get("/api/scene3d").json()
    assert d["source"] == "demo" and d["size"] == [640, 360]
    hf = d["heightfield"]
    assert len(hf["z"]) == hf["nx"] * hf["ny"] and set(hf["z"]) == {0.0}
    objs = {o["id"]: o for o in d["objects"]}
    assert objs["croc"]["class"] == "cocodrilo" and objs["keys"]["class"] == "teclado"
    assert objs["table"]["role"] == "outline" and objs["screen"]["z_max"] > 0
    # la demo se mueve: los objetos que entrega el endpoint siguen al cocodrilo
    before = objs["croc"]["center"]
    rt.tick(1.0)
    after = {o["id"]: o for o in c.get("/api/scene3d?parts=objects").json()["objects"]}["croc"]["center"]
    assert before != after
    tex = c.get("/api/scene3d/texture.jpg")
    img = cv2.imdecode(np.frombuffer(tex.content, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == (360, 640, 3)


@pytest.mark.skipif(not has_scan(scan_dir()), reason="sin captures/scan2 en este checkout")
def test_scene3d_real_scan(tmp_path):
    """Con el escaneo real del repo (captures/scan2), si está: tamaños y objetos principales."""
    _, c = _client(tmp_path)
    r = c.get("/api/scene3d")
    d = r.json()
    assert d["source"] == "scan" and len(r.content) < 2_000_000
    assert len(d["heightfield"]["z"]) == d["heightfield"]["nx"] * d["heightfield"]["ny"]
    assert {"cocodrilo", "buda", "torre"} <= {o["class"] for o in d["objects"]}

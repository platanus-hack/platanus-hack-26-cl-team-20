"""Dashboard (playground.dashboard) sin hardware: página, proxy al runtime falso, Continuar y un dale falso por SSE."""

from __future__ import annotations

import json
import sys
import textwrap
import time

import pytest
from fastapi.testclient import TestClient

from playground import dashboard as db

FAKE_DALE = textwrap.dedent('''
    import json, os, sys, time
    cont = sys.argv[1]
    def ev(paso, msg, ok=True, **k):
        print(json.dumps({"t": "x", "paso": paso, "ok": ok, "msg": msg, **k}, ensure_ascii=False), flush=True)
    ev("inicio", "sesión falsa " + " ".join(sys.argv[2:]))
    ev("liberar", "proyector libre")
    ev("guia", "Coloca el proyector y déjalo quieto", step="proyector_quieto")
    t0 = time.time()
    while not os.path.exists(cont) and time.time() - t0 < 20:
        time.sleep(0.05)
    os.unlink(cont)
    print("una línea de log que no es JSON", flush=True)
    ev("calib", "Vista 1", step="capture", progress=0.5, view=1)
    ev("pipeline", "etapas", decode="passed", mesh="blocked")
    ev("fin", "listo")
''')

STATE = {
    "scene": {"objects": {"croc": {"id": "croc", "name": "cocodrilo", "kind": "prop", "contour": [[0, 0], [1, 1]], "extra": {}},
                          "keys": {"id": "keys", "name": "teclado", "kind": "instrument", "contour": [], "extra": {}}},
              "coordinate_space": "projector_px"},
    "effects": [{"id": "e1", "effect": "contour_glow", "target": "croc", "params": {"width_mm": 30}, "t": 1.0, "alive": True}],
    "gallery": [{"name": "contour_glow", "label": "Halo", "targets": ["*"], "params": {"width_mm": 30, "color": [0, 255, 200]}, "description": "", "aliases": []},
                {"name": "keys_map", "label": "Teclas", "targets": ["instrument"], "params": {"relleno": 0.06}, "description": "", "aliases": []},
                {"name": "buda_breath", "label": "Buda", "targets": ["statue"], "params": {}, "description": "", "aliases": []}],
    "errors": {}, "stats": {"fps": 30},
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "CONTINUE_FILE", tmp_path / "dale.continue")
    monkeypatch.setattr(db, "ESTADO_FILE", tmp_path / "dale.estado.json")
    monkeypatch.setattr(db, "liberar_visor", lambda: None)
    monkeypatch.setattr(db, "DALE", db.Dale())
    with TestClient(db.build_app(watch=False)) as c:
        yield c
    db.DALE.cancel()


def test_pagina_y_estaticos(client):
    r = client.get("/")
    assert r.status_code == 200 and "Nueva vista" in r.text and "8796" in r.text
    assert client.get("/ui/app.js").status_code == 200 and client.get("/ui/style.css").status_code == 200
    assert client.get("/ui/../dashboard.py").status_code == 404


def test_escena_con_runtime_falso(client, monkeypatch):
    monkeypatch.setattr(db, "runtime_get", lambda path, timeout=2.5: json.dumps(STATE).encode())
    d = client.get("/api/escena").json()
    assert d["ok"] and {o["id"] for o in d["objects"]} == {"croc", "keys"}
    comp = {o["id"]: o["compatibles"] for o in d["objects"]}
    assert comp["croc"] == ["contour_glow"] and comp["keys"] == ["contour_glow", "keys_map"]
    assert d["effects"][0]["id"] == "e1" and d["globales"] == ["contour_glow"]
    llamadas = []
    monkeypatch.setattr(db, "runtime_cmd", lambda cmd, args, timeout=6.0: (llamadas.append((cmd, args)), {"ok": True, "result": {"id": "e2"}})[1])
    assert client.post("/api/spawn", json={"effect": "keys_map", "target": "keys"}).json()["ok"]
    assert client.post("/api/param", json={"id": "e1", "key": "width_mm", "value": 12}).json()["ok"]
    assert client.post("/api/kill", json={}).json()["ok"]
    assert llamadas == [("spawn", {"effect": "keys_map", "target": "keys", "params": None}),
                        ("set_param", {"id": "e1", "key": "width_mm", "value": 12}), ("kill", {"id": None})]


def test_escena_sin_runtime_no_rompe(client, monkeypatch):
    def caido(path, timeout=2.5):
        raise ConnectionRefusedError()
    monkeypatch.setattr(db, "runtime_get", caido)
    d = client.get("/api/escena").json()
    assert d["ok"] is False and "no responde" in d["error"]
    assert client.get("/api/frame.png").status_code == 503


def test_continuar_crea_archivo(client):
    assert client.post("/api/continuar").json()["ok"]
    assert db.CONTINUE_FILE.is_file()


def test_opciones_del_dale():
    cmd = db.dale_cmd({"views": 2, "scale": "huincha:600", "synthetic": True, "no_runtime": True, "escena_efectos": "cuadros"})
    assert cmd[len(db.DALE_CMD):] == ["--views", "2", "--scale", "huincha:600", "--calib", "auto", "--escena-efectos", "cuadros",
                                      "--synthetic", "--no-runtime"]
    with pytest.raises(Exception):
        db.dale_cmd({"views": 7})
    with pytest.raises(Exception):
        db.dale_cmd({"scale": "rm -rf"})


def test_dale_falso_emite_eventos_y_espera_continuar(client, monkeypatch, tmp_path):
    script = tmp_path / "fake_dale.py"; script.write_text(FAKE_DALE)
    monkeypatch.setattr(db, "DALE_CMD", [sys.executable, str(script), str(db.CONTINUE_FILE)])
    r = client.post("/api/dale", json={"synthetic": True, "no_runtime": True})
    assert r.status_code == 200 and r.json()["running"]
    assert client.post("/api/dale", json={}).status_code == 409        # uno a la vez

    def esperar(cond, s=10.0):
        t0 = time.time()
        while time.time() - t0 < s:
            evs = client.get("/api/dale").json()["events"]
            if cond(evs):
                return evs
            time.sleep(0.05)
        raise AssertionError(evs)

    evs = esperar(lambda e: any(x.get("step") == "proyector_quieto" for x in e))
    assert not any(x.get("paso") == "fin" for x in evs)               # está esperando a Max
    client.post("/api/continuar")
    evs = esperar(lambda e: any(x.get("kind") == "proc" and x.get("estado") == "fin" for x in e))
    pasos = [x["paso"] for x in evs if x["kind"] == "evento"]
    assert pasos == ["inicio", "liberar", "guia", "calib", "pipeline", "fin"]
    assert "--synthetic" in evs[0]["cmd"] and any(x["kind"] == "log" and "no es JSON" in x["msg"] for x in evs)
    assert [x for x in evs if x["kind"] == "proc"][-1]["rc"] == 0
    # el mismo evento leído desde dale.estado.json no se duplica
    linea = json.dumps({"t": "x", "paso": "fin", "ok": True, "msg": "listo"}, ensure_ascii=False)
    db.ESTADO_FILE.write_text(linea + "\n")
    db.DALE.poll_estado()
    assert len(client.get("/api/dale").json()["events"]) == len(evs)


def test_sse_entrega_eventos(client, monkeypatch, tmp_path):
    db.DALE._add({"kind": "evento", "paso": "liberar", "ok": True, "msg": "libre"})
    db.DALE._add({"kind": "proc", "estado": "fin", "rc": 0})
    with client.stream("GET", "/api/dale/events?hasta_fin=1") as r:
        vistos = []
        for line in r.iter_lines():
            if line.startswith("data:") and '"paso"' in line:
                vistos.append(json.loads(line[5:]))
    assert vistos and vistos[0]["paso"] == "liberar"

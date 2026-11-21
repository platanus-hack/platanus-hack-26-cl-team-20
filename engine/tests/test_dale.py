"""Ensayo sin hardware del enganche dale ↔ autocalib: eventos JSONL reenviados, move_camera → 'continue' por stdin."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

from playground import dale


FAKE_AUTOCALIB = textwrap.dedent('''
    import json, sys
    out = sys.argv[1]
    def ev(step, progress, msg, ok=True, **k):
        print(json.dumps({"stage": "autocalib", "step": step, "progress": progress, "msg": msg, "ok": ok, **k}), flush=True)
    ev("preflight", 0.0, "inicio")
    for v in (1, 2):
        ev("capture", 0.3 * v, f"Vista {v}: patrón 44/44", view=v)
        ev("move_camera", 0.3 * v + 0.05, "Mueve solo la cámara hacia un lado.", view=v)
        line = sys.stdin.readline()
        if "continue" not in line:
            ev("failed", 1.0, "sin continue", ok=False); sys.exit(2)
    ev("capture", 0.9, "Vista 3: patrón 44/44", view=3)
    open(out, "w").write(json.dumps({"format": "slscan-rig/2", "fake": True}))
    ev("rig", 1.0, "rig candidato escrito", path=out)
''')


def test_dale_reenvia_eventos_y_responde_continue(tmp_path, monkeypatch, capsys):
    script = tmp_path / "fake_autocalib.py"; script.write_text(FAKE_AUTOCALIB)
    rig = tmp_path / "rig.json"
    monkeypatch.setattr(dale, "AUTO_CONTINUE", True)
    monkeypatch.setattr(dale, "ESTADO_FILE", tmp_path / "estado.json")        # no ensuciar el estado del dashboard real
    narrado = []
    monkeypatch.setattr(dale, "narrar", lambda t: narrado.append(t))
    rc = dale.run([sys.executable, str(script), str(rig)], "calib", cwd=tmp_path, stream_json=True, on_event=dale.on_autocalib)
    assert rc == 0 and rig.is_file() and json.loads(rig.read_text())["fake"] is True
    eventos = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    pasos = [e.get("step") for e in eventos if e["paso"] == "calib"]
    assert pasos.count("move_camera") == 2 and "rig" in pasos
    assert sum("Mueve solo la cámara" in t and "continuar" in t for t in narrado) == 2   # narra el msg del autocalib


def test_dale_sin_continue_termina_con_2(tmp_path, monkeypatch, capsys):
    script = tmp_path / "fake_autocalib.py"; script.write_text(FAKE_AUTOCALIB)
    monkeypatch.setattr(dale, "narrar", lambda t: None)
    monkeypatch.setattr(dale, "ESTADO_FILE", tmp_path / "estado.json")
    rc = dale.run([sys.executable, str(script), str(tmp_path / "rig.json")], "calib", cwd=tmp_path, stream_json=True, on_event=None)
    assert rc == 2 and not (tmp_path / "rig.json").exists()

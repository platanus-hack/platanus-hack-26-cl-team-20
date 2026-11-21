"""Showcase: deja corriendo en el runtime (que ya proyecta con la escena real) un set de efectos sobre cada objeto y, si se
pide, va rotando escenas. Usa solo efectos que existan en la galería del runtime, así sirve antes y después de que lleguen
los nuevos (buda_breath, ddj_skin, torre_faces, papel_canvas, croc_synth, croc_alive, pianohero…).

    python -m playground.showcase                 # set completo, una vez
    python -m playground.showcase --rotar 45      # rota escenas cada 45 s (Ctrl-C para parar)
    python -m playground.showcase --apagar        # apaga todo
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request

URL = "http://127.0.0.1:8790"

# escenas: lista de (efecto, objeto, params); se saltan los efectos que la galería no tenga
ESCENAS = {
    "halos": [("contour_glow", "croc", {"color": [0, 255, 200], "width_mm": 30}), ("contour_glow", "keys", {"color": [80, 200, 255], "width_mm": 14}),
              ("contour_glow", "buda", {"color": [255, 160, 40], "width_mm": 22}), ("contour_glow", "ddj", {"color": [255, 60, 220], "width_mm": 20}),
              ("keys_map", "keys", {"relleno": 0.06, "nombres": False})],
    "texturas": [("buda_breath", "buda", {}), ("ddj_skin", "ddj", {}), ("torre_faces", "torre", {}), ("papel_canvas", "papel", {}),
                 ("scene_texture", None, {}), ("contour_glow", "croc", {"color": [0, 255, 200], "width_mm": 20})],
    "cocodrilo": [("croc_alive", "croc", {}), ("croc_synth", "croc", {}), ("buda_breath", "buda", {}), ("keys_map", "keys", {"relleno": 0.04, "nombres": False})],
    "audio": [("bass_pulse", None, {}), ("bass_ripple", None, {}), ("channel_react", None, {}), ("buda_breath", "buda", {"audio": "bass"})],
    "piano": [("pianohero", "keys", {"demo": True}), ("buda_breath", "buda", {}), ("ddj_skin", "ddj", {})],
    "linterna": [("linterna", None, {"demo": True})],
    "cuadros": [("cuadro_vivo", None, {}), ("marco_respira", None, {}), ("parallax", None, {}), ("torre_faces", "torre", {}), ("papel_canvas", "papel", {})],
    "portal": [("cuadro_portal", None, {}), ("marco_respira", None, {"color": [120, 200, 255]}), ("torre_faces", "torre", {})],
    "ambiente": [("pared_viva", None, {"modo": "calma", "paleta": "aurora"}), ("sombras_largas", None, {}), ("plantas", None, {}), ("cuadro_vivo", None, {})],
    "fiesta": [("pared_viva", None, {"modo": "fiesta", "paleta": "neon"}), ("lluvia_estrellas", None, {}), ("marco_respira", None, {}), ("bass_ripple", None, {})],
}
ORDEN = ["halos", "texturas", "cuadros", "portal", "ambiente", "fiesta", "cocodrilo", "piano", "audio", "linterna"]


def cmd(c: str, **args) -> dict:
    body = json.dumps({"cmd": c, "args": args}).encode()
    with urllib.request.urlopen(urllib.request.Request(URL + "/api/command", body, {"Content-Type": "application/json"}), timeout=6) as r:
        return json.loads(r.read())


def galeria() -> set[str]:
    return {g["name"] for g in (cmd("list_effects").get("result") or {}).get("gallery", [])}


def apagar_todo() -> int:
    with urllib.request.urlopen(URL + "/api/state", timeout=6) as r:
        st = json.loads(r.read())
    n = 0
    for e in st.get("effects", []):
        cmd("kill", id=e.get("id")); n += 1
    return n


def lanzar(nombre: str, gal: set[str]) -> list[str]:
    out = []
    for eff, obj, params in ESCENAS[nombre]:
        if eff not in gal:
            continue
        try:
            r = cmd("spawn", effect=eff, target=obj, params=params or None)
            out.append(f"{eff}→{obj or '-'}:{(r.get('result') or {}).get('id') or r.get('error', '?')}")
        except Exception as e:  # noqa: BLE001
            out.append(f"{eff}: error {e}")
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--escena", default="halos", choices=list(ESCENAS)); p.add_argument("--rotar", type=float, default=0.0)
    p.add_argument("--apagar", action="store_true"); p.add_argument("--todo", action="store_true", help="todas las escenas a la vez")
    a = p.parse_args(argv)
    if a.apagar:
        print("apagados:", apagar_todo()); return 0
    gal = galeria(); print("galería:", sorted(gal))
    if a.todo:
        apagar_todo()
        for n in ORDEN:
            print(n, lanzar(n, gal))
        return 0
    if not a.rotar:
        apagar_todo(); print(a.escena, lanzar(a.escena, gal)); return 0
    i = ORDEN.index(a.escena) if a.escena in ORDEN else 0
    try:
        while True:
            n = ORDEN[i % len(ORDEN)]; apagar_todo(); print(time.strftime("%H:%M:%S"), n, lanzar(n, gal)); i += 1
            time.sleep(a.rotar)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

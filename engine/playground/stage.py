"""Formas de lo real (Buda, teclas, platos de la DDJ, Circuit Tracks…) en píxeles del proyector 1920x1080.

Para efectos que dibujan directo sobre la mesa del estudio sin pasar por la escena en mm. Fuente: el escaneo
(`workbench.json`, más fino, y lo que falte de `segments.json`) en `PLAYGROUND_SCAN`, `captures/scan4` o
`captures/scan2`; se recarga si cambian los archivos. Lo que el escaneo no traiga sale de `FALLBACK` (el layout de
scan4 del 16-oct, copiado aquí para tests, demo y el PC). `stage(size=…)` lo escala al lienzo del runtime.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
SCAN_SIZE = (1920, 1080)


def _rect(b) -> list[list[float]]:
    x, y, w, h = [float(v) for v in b[:4]]
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


FALLBACK: dict = {
    "buda": [[529.5, 550.4], [458.5, 148.5], [922.4, 66.5], [993.4, 468.4]],
    "keyboard": [[653.0, 640.0], [1445.0, 640.0], [1445.0, 743.0], [653.0, 743.0]],
    "keys": [_rect([663 + 34 * i, 640, 30, 103]) for i in range(23)],
    "black_keys": [],
    "jogs": [[1030.0, 854.0, 114.0], [1245.0, 905.0, 108.0]],
    # el Circuit Tracks no está segmentado en scan4: esquinas a ojo sobre captures/scan4/scene_pov.png
    "circuit": [[1450.0, 855.0], [1695.0, 955.0], [1545.0, 1085.0], [1295.0, 1000.0]],
    "ohm": _rect([458, 716, 542, 364]),
    "cocodrilo": None,
}


def scan_dir(explicit: str | Path | None = None) -> Path | None:
    if explicit:            # una carpeta pedida que no existe → formas de respaldo, no otro escaneo
        p = Path(explicit)
        return p if (p / "segments.json").exists() or (p / "workbench.json").exists() else None
    # último escaneo calibrado primero (scan5, 16-oct, luz apagada); SLSCAN_SCAN es la variable que usan workbench y firstlook
    cands = [os.environ.get("PLAYGROUND_SCAN"), os.environ.get("SLSCAN_SCAN"), PROJECT_DIR / "captures" / "scan5",
             PROJECT_DIR / "captures" / "scan4", PROJECT_DIR / "captures" / "scan2"]
    for c in cands:
        if c and ((Path(c) / "segments.json").exists() or (Path(c) / "workbench.json").exists()):
            return Path(c)
    return None


_cache: dict = {}


def _read(p: Path) -> dict:
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except (OSError, json.JSONDecodeError):          # el escáner puede estar escribiéndolo justo ahora
        return {}


def _parse(seg: dict, wb: dict) -> dict:
    out: dict = {"from": {}}

    def pick(key):
        if key in wb:
            return wb[key], "workbench"
        if key in seg:
            return seg[key], "segments"
        return None, ""

    b, src = pick("buda")
    if b:
        out["buda"] = b.get("quad") or _rect(b["bbox"]); out["from"]["buda"] = src
    kb, src = pick("teclado")
    if kb:
        if kb.get("rect"):
            import cv2
            (cx, cy), (w, h), ang = kb["rect"]
            out["keyboard"] = cv2.boxPoints(((cx, cy), (w, h), ang)).tolist()
        elif kb.get("bbox"):
            out["keyboard"] = _rect(kb["bbox"])
        keys = [_rect([k["x"], k["y"], k["w"], k["h"]]) if isinstance(k, dict) else _rect(k)
                for k in kb.get("teclas_blancas", [])]
        if keys:
            out["keys"] = sorted(keys, key=lambda p: p[0][0]); out["from"]["keys"] = src
        out["black_keys"] = [_rect(k) for k in kb.get("teclas_negras", [])]
    dj, src = pick("ddj")
    if dj and dj.get("jogs"):
        out["jogs"] = [[float(v) for v in j[:3]] for j in dj["jogs"]]; out["from"]["jogs"] = src
    for name in ("circuit", "ohm", "cocodrilo"):
        o, src = pick(name)
        if o:
            out[name] = o.get("contour") or o.get("quad") or (_rect(o["bbox"]) if o.get("bbox") else None)
            out["from"][name] = src
    return out


def load(scan: str | Path | None = None) -> dict:
    """Formas en px del proyector (SCAN_SIZE). Cacheado por mtime de los JSON del escaneo."""
    d = scan_dir(scan)
    files = [d / "segments.json", d / "workbench.json"] if d else []
    key = (str(d), tuple(f.stat().st_mtime if f.exists() else 0 for f in files))
    if _cache.get("key") == key:
        return _cache["data"]
    parsed = _parse(_read(files[0]), _read(files[1])) if d else {"from": {}}
    data = {k: v for k, v in FALLBACK.items()}
    data.update({k: v for k, v in parsed.items() if k != "from" and v})
    data["source"] = str(d) if d else "fallback"
    data["from"] = {k: parsed["from"].get(k, "fallback") for k in FALLBACK}
    _cache.update(key=key, data=data)
    return data


def _scale(v, sx: float, sy: float):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    if v and isinstance(v[0], (int, float)):
        return [v[0] * sx, v[1] * sy] + ([v[2] * (sx + sy) / 2] if len(v) > 2 else [])
    return [_scale(p, sx, sy) for p in v]


def stage(scan: str | Path | None = None, size: tuple[int, int] = SCAN_SIZE) -> dict:
    """Formas escaladas al lienzo `size` (w, h). Polígonos [[x, y], …]; jogs [[cx, cy, r], …]."""
    data = load(scan)
    if tuple(size) == SCAN_SIZE:
        return data
    sx, sy = size[0] / SCAN_SIZE[0], size[1] / SCAN_SIZE[1]
    out = {k: (_scale(v, sx, sy) if k not in ("source", "from") else v) for k, v in data.items()}
    return out

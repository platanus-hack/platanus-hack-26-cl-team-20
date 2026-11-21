"""Mapeo canal → partes de la escena (`mapping.json`), leído por el efecto `channel_react`.

Se recarga cuando cambia el archivo; si queda mal escrito, se sigue usando el último bueno y se informa el error.
También acepta el mapeo como dict o lista (p.ej. desde `pg_set_param <id> mapping '{"parts": [...]}'`).
"""

from __future__ import annotations

import json
from pathlib import Path

from .features import parse_key

MAPPING_PATH = Path(__file__).with_name("mapping.json")
PROJECT_DIR = Path(__file__).parent.parent.parent
ALIASES = {"teclas": "keys", "tecla": "keys", "platos": "jogs", "plato": "jogs", "ddj": "jogs", "jog": "jogs",
           "croc": "cocodrilo", "teclado": "keyboard", "circuit tracks": "circuit", "budha": "buda"}
DEFAULT_STYLE = {"buda": "halo", "keys": "keys", "jogs": "rings"}
STYLES = ("halo", "keys", "rings", "outline", "fill")

_cache: dict = {}


def normalize(p: dict) -> dict:
    part = str(p.get("part", "")).strip().lower()
    part = ALIASES.get(part, part)
    src, feat = parse_key(f"{p.get('source', 'mix')}.{p.get('feature', 'rms')}") or ("mix", "rms")
    style = str(p.get("style") or DEFAULT_STYLE.get(part, "outline")).lower()
    return {"part": part, "source": src, "feature": feat, "key": feat if src == "mix" else f"{src}.{feat}",
            "style": style if style in STYLES else "outline", "color": list(p.get("color") or [255, 255, 255])[:3],
            "gain": float(p.get("gain", 1.0)), "ripples": bool(p.get("ripples", style == "rings")),
            "label": str(p.get("label") or f"{src}.{feat} → {part}")}


def _parse(data) -> list[dict]:
    parts = data.get("parts", []) if isinstance(data, dict) else data
    if not isinstance(parts, list):
        raise ValueError("el mapeo necesita una lista 'parts'")
    return [normalize(p) for p in parts if isinstance(p, dict) and p.get("part")]


def load_mapping(src=None) -> tuple[list[dict], str | None, str]:
    """(partes normalizadas, error o None, origen). `src`: '' = mapping.json del repo, ruta, dict o lista."""
    if isinstance(src, (dict, list)):
        try:
            return _parse(src), None, "param"
        except (ValueError, TypeError) as e:
            return [], str(e), "param"
    if isinstance(src, str) and src.strip().startswith(("{", "[")):
        try:
            return _parse(json.loads(src)), None, "param"
        except (ValueError, TypeError) as e:
            return [], str(e), "param"
    path = Path(src) if src else MAPPING_PATH
    if not path.is_absolute() and not path.exists():
        path = PROJECT_DIR / path
    hit = _cache.get("key:" + str(path))
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return (hit[1] if hit else []), f"no existe {path}", str(path)
    key = (str(path), mtime)
    if hit and hit[0] == key:
        return hit[1], hit[2], str(path)
    try:
        parts, err = _parse(json.loads(path.read_text())), None
    except (OSError, ValueError, TypeError) as e:          # mal escrito: sigue el último bueno
        parts, err = (hit[1] if hit else []), f"{path.name}: {e}"
    _cache["key:" + str(path)] = (key, parts, err)
    return parts, err, str(path)

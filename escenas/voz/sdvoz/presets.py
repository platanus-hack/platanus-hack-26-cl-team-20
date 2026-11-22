"""Manifiestos de los presets (`escenas/<preset>/preset.json`, CONTRATO-ASISTENTE.md §2). Solo se leen: los arma
Matilda y los conductores de cada escena.

Se buscan en `$SD_PRESETS` (carpetas separadas por «:», p. ej. los worktrees de cada escena) y en `escenas/` del repo.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path

from .escenas import REPO, Escena


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def carpetas() -> list[Path]:
    extra = [Path(p) for p in os.environ.get("SD_PRESETS", "").split(":") if p]
    return [*extra, REPO / "escenas"]


def todos() -> list[tuple[Path, dict]]:
    vistos, out = set(), []
    for base in carpetas():
        for p in sorted(base.glob("*/preset.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if d.get("id") and d["id"] not in vistos:
                vistos.add(d["id"])
                out.append((p, d))
    return out


def preset_de(nombre_o_pestana: str | None, escena: Escena | None = None) -> dict | None:
    """El manifiesto por id, por nombre o por la pestaña activa; si no, el de la escena de la voz."""
    lista = todos()
    n = _norm(nombre_o_pestana or "")
    for _, d in lista:
        if n and n in (_norm(d.get("id")), _norm(d.get("nombre"))):
            return d
    if escena:
        for _, d in lista:
            if d.get("id") == escena.preset_id or _norm(d.get("nombre")) == _norm(escena.pestana):
                return d
    return None


def ruta(preset_id: str) -> Path | None:
    for p, d in todos():
        if d.get("id") == preset_id:
            return p
    return None


def _args(spec: dict) -> str:
    partes = []
    for k, v in (spec or {}).items():
        t = v.get("tipo", "") if isinstance(v, dict) else str(v)
        if isinstance(v, dict) and v.get("prefijo"):
            t += f" {v['prefijo']}"
        partes.append(f"{k}: {t}" + (" (opcional)" if isinstance(v, dict) and v.get("opcional") else ""))
    return ", ".join(partes)


def resumen_preset(d: dict) -> str:
    """El manifiesto en texto corto para un LLM."""
    lineas = [f"Preset «{d.get('nombre')}» ({d.get('id')}): {d.get('para_que', '')}"]
    acciones = d.get("acciones") or {}
    if acciones:
        lineas.append("Acciones (preset_accion): " + " | ".join(
            f"{k}({_args(v.get('args'))}): {v.get('que_hace', '')}" for k, v in acciones.items()))
    params = d.get("params") or {}
    if params:
        def rango(v):
            r = f" ({v['min']}–{v['max']})" if "min" in v and "max" in v else ""
            return f"{v.get('valor')}{' ' + v['unidad'] if v.get('unidad') else ''}{r}"
        lineas.append("Parámetros (preset_ajustar): " + " | ".join(
            f"{k} = {rango(v)}: {v.get('que_es', '')}" for k, v in params.items()))
    eventos = d.get("eventos") or {}
    if eventos:
        lineas.append("Eventos que avisa: " + " | ".join(f"{k}: {v.get('que_es', '')}" for k, v in eventos.items()))
    return "\n".join(lineas)


def seccion_voz(d: dict) -> str | None:
    """La sección de voz del escena.md del preset (`voz.persona` = «escena.md#ancla»), si existe."""
    persona = ((d.get("voz") or {}).get("persona") or "")
    if "#" not in persona:
        return None
    archivo, ancla = persona.split("#", 1)
    p = ruta(d["id"])
    if not p or not (p.parent / archivo).exists():
        return None
    texto = (p.parent / archivo).read_text(encoding="utf-8")
    bloques = re.split(r"(?m)^(#{1,3} .+)$", texto)
    for i in range(1, len(bloques), 2):
        titulo = bloques[i].lstrip("#").strip()
        slug = re.sub(r"[^a-z0-9]+", "-", _norm(titulo)).strip("-")
        if slug == ancla or ancla.startswith(slug) or slug.startswith(ancla):
            return (bloques[i] + bloques[i + 1]).strip()
    return None

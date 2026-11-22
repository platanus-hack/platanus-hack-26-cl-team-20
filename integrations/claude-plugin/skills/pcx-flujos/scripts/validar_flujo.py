#!/usr/bin/env python3
"""Valida argumentos de `run_flow` contra los contratos de pcx y avisa de problemas de diseño.

Uso (desde la raíz del repo de pcx; `uv` arma el entorno de `contracts/`):

    uv run --project contracts python integrations/skills/pcx-flujos/scripts/validar_flujo.py flujo.json [otro.json ...]

Cada archivo puede ser los argumentos de la tool (`{"request_id", "flow"}`), el comando completo
(`{"type": "run_flow", ...}`) o solo el flujo (`{"id", "initial", "states"}`).

Errores (salida 1): lo que el nodo rechazaría (`parse_args` + `check_flow` + JSON Schema de la tool).
Avisos (salida 0): cosas válidas que suelen ser un error de diseño (estados inalcanzables, sin salida, filtros
`where` con campos que el evento no tiene, widgets que se actualizan sin haberse proyectado).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, get_args

try:
    import msgspec
    from pcx_contracts import events as ev
    from pcx_contracts.tools import input_schema, parse_args
except ImportError:  # pragma: no cover
    sys.exit("No encuentro pcx_contracts. Corre con: uv run --project contracts python <este script> flujo.json")

EVENTS: dict[str, type] = {t.__struct_config__.tag: t for t in get_args(ev.Event)}


def _struct_fields(cls: type) -> dict[str, Any]:
    return {f.name: f.type for f in msgspec.structs.fields(cls)}


def _unwrap(tp: Any) -> Any:
    """`X | None` → X (para seguir rutas con punto dentro de structs anidados)."""
    args = [a for a in get_args(tp) if a is not type(None)]
    return args[0] if len(args) == 1 else tp


def check_where(event: str, where: dict[str, Any]) -> list[str]:
    if event not in EVENTS:
        return [f"evento desconocido {event!r}: nunca va a disparar (conocidos: {', '.join(sorted(EVENTS))})"]
    out = []
    for path in where:
        cls: Any = EVENTS[event]
        for part in path.split("."):
            if isinstance(cls, type) and issubclass(cls, msgspec.Struct):
                fields = _struct_fields(cls)
                if part not in fields:
                    out.append(f"`{event}` no tiene el campo {path!r} (campos: {', '.join(fields)})")
                    break
                cls = _unwrap(fields[part])
            else:
                break  # dict libre (p. ej. `data.*`) o tipo simple: no se puede verificar más
    return out


def lint(flow: dict[str, Any]) -> list[str]:
    warns: list[str] = []
    states = {s["id"]: s for s in flow["states"]}
    if flow.get("timeout_s") is None:
        warns.append("sin `timeout_s`: si nadie lo termina, el flujo queda corriendo")
    # alcanzables desde `initial`
    seen, todo = set(), [flow["initial"]]
    while todo:
        cur = todo.pop()
        if cur in seen or cur not in states:
            continue
        seen.add(cur)
        todo += [t["to"] for t in states[cur].get("transitions", [])]
    warns += [f"estado inalcanzable: {s}" for s in states if s not in seen]
    projected: set[str] = set()
    touched: list[tuple[str, str]] = []
    for sid, s in states.items():
        trans = s.get("transitions", [])
        if not s.get("final") and not trans:
            warns.append(f"{sid}: sin transiciones y no es `final` (solo sale por `timeout_s`)")
        if flow.get("timeout_s") is None and not s.get("final") and trans and all(t.get("on") for t in trans):
            warns.append(f"{sid}: solo sale por evento y el flujo no tiene `timeout_s`: puede quedarse pegado")
        actions = list(s.get("on_enter", [])) + [a for t in trans for a in t.get("actions", [])]
        for a in actions:
            if a["type"] == "project":
                projected |= {w["id"] for w in a["widgets"]}
            elif a["type"] == "update":
                touched.append((sid, a["id"]))
        for t in trans:
            if t.get("on"):
                warns += [f"{sid}: {m}" for m in check_where(t["on"]["event"], t["on"].get("where", {}))]
    warns += [f"{sid}: `update` de {wid!r}, que este flujo nunca proyecta" for sid, wid in touched if wid not in projected]
    return warns


def load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("type", None)
    if "flow" not in data:
        data = {"request_id": f"validar-{path.stem}", "flow": data}
    return data


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 2
    try:
        import jsonschema
        validator = jsonschema.Draft202012Validator(input_schema("run_flow"))
    except ImportError:
        validator = None
    bad = 0
    for p in map(Path, paths):
        try:
            args = load(p)
            cmd = parse_args("run_flow", args)
            if validator is not None:
                # forma canónica (opcionales en `null`), sin el tag que la tool implica
                canon = msgspec.to_builtins(cmd)
                canon.pop("type", None)
                validator.validate(canon)
        except Exception as e:  # msgspec.ValidationError, jsonschema.ValidationError, JSON inválido…
            bad += 1
            print(f"ERROR  {p}: {str(e).splitlines()[0]}")
            continue
        f = args["flow"]
        print(f"OK     {p}: flujo {f['id']!r}, {len(f['states'])} estados")
        for w in lint(f):
            print(f"  aviso: {w}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

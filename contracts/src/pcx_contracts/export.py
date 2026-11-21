"""Exporta JSON Schema, tools MCP y fixtures canónicos.  Uso: `uv run python -m pcx_contracts.export`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import msgspec

from . import FIXTURES, FIXTURES_DIR, ROOTS, SCHEMA_DIR
from .envelope import CONTRACT_VERSION
from .tools import mcp_tools


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def bundle_schema() -> dict[str, Any]:
    """Un solo documento con todos los `$defs` y una raíz que referencia cada tipo raíz (para TS)."""
    schemas, components = msgspec.json.schema_components(list(ROOTS.values()), ref_template="#/$defs/{name}")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Pcx",
        "description": f"Contratos pcx v{CONTRACT_VERSION}",
        "type": "object",
        "properties": dict(zip(ROOTS.keys(), schemas)),
        "$defs": components,
    }


def to_draft07(node: Any) -> Any:
    """Variante draft-07 (`prefixItems` -> `items: [...]`, `$defs` -> `definitions`) para json-schema-to-typescript."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k == "prefixItems":
                out["items"] = to_draft07(v)
                out["additionalItems"] = False
            elif k == "$defs":
                out["definitions"] = to_draft07(v)
            elif k == "$ref":
                out[k] = v.replace("#/$defs/", "#/definitions/")
            elif k == "$schema":
                out[k] = "http://json-schema.org/draft-07/schema#"
            else:
                out[k] = to_draft07(v)
        if "properties" in out and out.get("type") == "object":
            out.setdefault("additionalProperties", False)  # structs cerrados; dicts libres quedan abiertos
        return out
    if isinstance(node, list):
        return [to_draft07(x) for x in node]
    return node


def to_ts_profile(node: Any, key: str | None = None) -> Any:
    """Perfil JSON para clientes TS (sobre draft-07): campos `*_ns` enteros pasan a cadena decimal."""
    if isinstance(node, dict):
        if key is not None and key.endswith("_ns") and node.get("type") in ("integer", ["integer", "null"]):
            nullable = isinstance(node.get("type"), list)
            out = {k: v for k, v in node.items() if k not in ("type", "minimum", "maximum")}
            out["type"] = ["string", "null"] if nullable else "string"
            out["pattern"] = r"^-?[0-9]+$"
            out["description"] = (out.get("description", "") + " (ns como cadena decimal; usar BigInt)").strip()
            return out
        if "properties" in node:
            props = {k: to_ts_profile(v, k) for k, v in node["properties"].items()}
            return {**{k: to_ts_profile(v) for k, v in node.items() if k != "properties"}, "properties": props}
        if key is not None and key.endswith("_ns") and "anyOf" in node:
            return {**node, "anyOf": [to_ts_profile(x, key) for x in node["anyOf"]]}
        return {k: to_ts_profile(v) for k, v in node.items()}
    if isinstance(node, list):
        return [to_ts_profile(x) for x in node]
    return node


def canonical(name: str) -> Any:
    """Fixture decodificado y re-encodificado: forma completa (con defaults y nulls) que Rust debe reproducir."""
    raw = (FIXTURES_DIR / name).read_bytes()
    obj = msgspec.json.decode(raw, type=FIXTURES[name])
    return json.loads(msgspec.json.encode(obj))


def main() -> None:
    for name, tp in ROOTS.items():
        _write(SCHEMA_DIR / f"{name}.schema.json", msgspec.json.schema(tp))
    bundle = bundle_schema()
    _write(SCHEMA_DIR / "pcx.schema.json", bundle)
    _write(SCHEMA_DIR / "pcx.draft07.json", to_draft07(bundle))
    _write(SCHEMA_DIR / "pcx.ts.json", to_ts_profile(to_draft07(bundle)))
    tools = mcp_tools()
    _write(SCHEMA_DIR / "tools.json", tools)
    for t in tools:
        _write(SCHEMA_DIR / "tools" / f"{t['name']}.input.json", t["inputSchema"])
    for name in FIXTURES:
        _write(FIXTURES_DIR / "canonical" / name, canonical(name))
    print(f"schema -> {SCHEMA_DIR}  ({len(ROOTS)} raíces, {len(tools)} tools); canónicos -> {FIXTURES_DIR / 'canonical'}")


if __name__ == "__main__":
    main()

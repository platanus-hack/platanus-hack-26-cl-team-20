"""Tools MCP: nombre, descripción, anotaciones y JSON Schema de entrada generado desde los contratos.

Los esquemas salen planos (sin `$ref`/`$defs` ni `discriminator`) porque Codex y otros clientes soportan solo un
subconjunto de JSON Schema (aprendizaje del spike MCP). La nulabilidad se conserva: `anyOf[X, null]` pasa a
`type: [X, "null"]` (o `enum` con `null`) cuando X es simple, y queda como `anyOf` si no; así un objeto canónico
(opcionales como `null`) valida contra el `inputSchema` tal cual.

Idempotencia: las tools mutables exigen `request_id` (ver `commands`). `run_flow` se anuncia `destructiveHint` porque
puede contener `home_call`; `flow_is_destructive()` lo precisa por flujo.
Nombres: `[a-z_]` solamente; `events.wait` se expone como `events_wait` porque la API de Claude y la de OpenAI
rechazan puntos en nombres de tools.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Annotated, Any

import msgspec
from msgspec import Meta, Struct

from .commands import Ask, Calibrate, HomeCall, Project, Remove, RunFlow, check_flow
from .devices import DeviceKind


class SceneArgs(Struct, kw_only=True):
    include_hands: bool = False
    include_devices: bool = False


class LookArgs(Struct, kw_only=True):
    camera_id: str | None = None
    """Cámara a usar (None = la cenital principal)."""
    annotate: bool = True
    """Dibuja ids de objetos, grilla en mm y la luz proyectada sobre la imagen."""


class EventsWaitArgs(Struct, kw_only=True):
    cursor: int = 0
    """Devuelve eventos con `seq` > cursor. Usa el `cursor` de la respuesta anterior."""
    types: list[str] = []
    """Filtra por `type` de evento (vacío = todos)."""
    timeout_s: Annotated[float, Meta(ge=0, le=50)] = 25.0
    """Long-poll: espera hasta este plazo si no hay eventos (bajo el límite de ~60 s de los clientes)."""
    max_events: Annotated[int, Meta(ge=1, le=500)] = 50


class DevicesArgs(Struct, kw_only=True):
    kind: DeviceKind | None = None


@dataclass(frozen=True, kw_only=True)
class ToolDef:
    name: str
    description: str
    args: type
    read_only: bool = False
    destructive: bool = False


TOOLS: list[ToolDef] = [
    ToolDef(name="scene", args=SceneArgs, read_only=True,
            description="Estado actual en JSON: superficie, objetos (id, label, polygon_mm, center_mm), widgets, "
                        "transformaciones y el cursor de eventos. Coordenadas en mm, origen arriba a la izquierda."),
    ToolDef(name="look", args=LookArgs, read_only=True,
            description="Imagen de una cámara con anotaciones (ids, grilla en mm) más un resumen en texto."),
    ToolDef(name="project", args=Project,
            description="Crea o reemplaza widgets proyectados (label, halo, arrow, panel, button, image, effect). "
                        "Ánclalos con anchor.object_id para que sigan al objeto."),
    ToolDef(name="remove", args=Remove, description="Borra widgets proyectados por id."),
    ToolDef(name="ask", args=Ask,
            description="Proyecta una pregunta con un botón por opción y espera el toque o el plazo."),
    ToolDef(name="events_wait", args=EventsWaitArgs, read_only=True,
            description="Long-poll de eventos físicos (toques, objetos, flujos) posteriores a `cursor`."),
    ToolDef(name="run_flow", args=RunFlow, destructive=True,
            description="Ejecuta en el nodo una máquina de estados (estados, transiciones por evento o tiempo y "
                        "acciones project/remove/emit/home_call) sin ida y vuelta al agente por cada paso. Cada "
                        "transición tiene `on` o `after_s` (no ambos); el evento gana al temporizador. Los home_call "
                        "siguen la política del nodo (pueden quedar pendientes de confirmación)."),
    ToolDef(name="calibrate", args=Calibrate,
            description="Calibra cámara↔proyector↔mesa: quick (ArUco, ~3 s), precise (gray code) o verify."),
    ToolDef(name="devices", args=DevicesArgs, read_only=True,
            description="Lista los dispositivos del nodo (cámaras, proyectores, celulares, Home Assistant, MIDI)."),
    ToolDef(name="home", args=HomeCall, destructive=True,
            description="Llama un servicio de Home Assistant (p. ej. light.turn_on). La política del nodo decide: "
                        "se ejecuta, se rechaza o queda `pending` con `confirmation_id` hasta que una persona "
                        "confirme con un toque en la mesa o en la consola (evento home_call_done)."),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def flatten(schema: dict[str, Any]) -> dict[str, Any]:
    """Resuelve `$ref`, quita `discriminator` y expresa `anyOf[X, null]` sin perder el `null`."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = copy.deepcopy(defs[node["$ref"].split("/")[-1]])
                return walk({**target, **{k: v for k, v in node.items() if k != "$ref"}})
            if "anyOf" in node:
                non_null = [s for s in node["anyOf"] if s.get("type") != "null"]
                if len(non_null) == 1 and len(node["anyOf"]) == 2:
                    rest = {k: walk(v) for k, v in node.items() if k != "anyOf"}
                    x = walk(non_null[0])
                    if "enum" in x:
                        x = {**x, "enum": [*x["enum"], None]}
                        if isinstance(x.get("type"), str):
                            x["type"] = [x["type"], "null"]
                        return {**x, **rest}
                    if isinstance(x.get("type"), str) and not ({"anyOf", "oneOf", "const"} & x.keys()):
                        return {**x, "type": [x["type"], "null"], **rest}
                    return {"anyOf": [x, {"type": "null"}], **rest}
            return {k: walk(v) for k, v in node.items() if k not in ("$defs", "discriminator")}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def input_schema(tool: ToolDef | str) -> dict[str, Any]:
    t = TOOLS_BY_NAME[tool] if isinstance(tool, str) else tool
    s = flatten(msgspec.json.schema(t.args))
    # El tag del comando (`type`) lo implica el nombre de la tool.
    tag_field = t.args.__struct_config__.tag_field
    if tag_field:
        s.get("properties", {}).pop(tag_field, None)
        s["required"] = [r for r in s.get("required", []) if r != tag_field]
    if not s.get("required"):
        s.pop("required", None)
    s.pop("title", None)
    s["type"] = "object"
    s.setdefault("properties", {})
    return s


def mcp_tools() -> list[dict[str, Any]]:
    """Lista lista para `tools/list` de MCP."""
    return [{
        "name": t.name,
        "description": t.description,
        "inputSchema": input_schema(t),
        "annotations": {"readOnlyHint": t.read_only, "destructiveHint": t.destructive, "openWorldHint": False},
    } for t in TOOLS]


def parse_args(tool: str, args: dict[str, Any]) -> Any:
    """Valida los argumentos de una tool y devuelve el struct (comando o *Args)."""
    t = TOOLS_BY_NAME[tool]
    tag = t.args.__struct_config__.tag
    data = {**args, "type": tag} if tag else args
    obj = msgspec.convert(data, t.args)  # Flow.__post_init__ ya corre check_flow; se repite por claridad
    if isinstance(obj, RunFlow) and (errs := check_flow(obj.flow)):
        raise msgspec.ValidationError("flujo inválido: " + "; ".join(errs))
    return obj

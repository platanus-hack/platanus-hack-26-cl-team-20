"""Domótica: política declarada en el nodo y flujo de confirmación verificable.

La política (`HomePolicy`) vive en la configuración del nodo; ni el agente ni la nube la envían. Por cada
`home_call` (tool `home` o acción de un flujo) el nodo decide con `decide()`:

- `deny`: el servicio no está en `allow` → `HomeCallResult{status: "denied"}`.
- `allow`: se ejecuta → `HomeCallResult{status: "done" | "failed"}`.
- `confirm`: no se ejecuta todavía → `HomeCallResult{status: "pending", confirmation_id, action_digest}` y el
  evento `confirmation_requested`. El nodo proyecta un botón reservado `pcx:confirm:<confirmation_id>` que solo
  acepta toques físicos (`dwell`/`pinch`, nunca `api`), y la consola (local o vía la nube) puede responder con `ConfirmAction`.
  `ConfirmAction` **no** es un `Command`: no existe tool MCP ni `tool_call` que lo produzca (viaja como
  `CloudMessage` desde la consola de la nube, nunca desde un agente).
  La confirmación queda vinculada a la acción por `confirmation_id` + `action_digest` (sha256 de la acción
  canónica); si no coinciden, el nodo la rechaza. Al resolverse: `confirmation_resolved` y luego `home_call_done`.
"""

from __future__ import annotations

import hashlib
import json
from fnmatch import fnmatchcase
from typing import Annotated, Literal

from msgspec import Meta, Struct, field

from .commands import HomeCallAction
from .geometry import Sha256

ConfirmVia = Literal["touch", "console"]
Decision = Literal["allow", "confirm", "deny"]


class HomePolicy(Struct, kw_only=True):
    """Política del nodo. Patrones `domain.service` con comodines (`light.*`, `*`)."""

    allow: list[str] = []
    """Servicios permitidos. Lo que no calce se rechaza."""
    confirm: list[str] = field(default_factory=lambda: ["*"])
    """De los permitidos, los que requieren confirmación física o de consola (por defecto: todos)."""
    confirm_via: list[ConfirmVia] = field(default_factory=lambda: ["touch", "console"])
    confirm_timeout_s: Annotated[float, Meta(gt=0, le=600)] = 30.0


def decide(policy: HomePolicy, call: HomeCallAction) -> Decision:
    name = f"{call.domain}.{call.service}"
    if not any(fnmatchcase(name, p) for p in policy.allow):
        return "deny"
    return "confirm" if any(fnmatchcase(name, p) for p in policy.confirm) else "allow"


def action_digest(call: HomeCallAction) -> str:
    """sha256 hex de `{"domain","service","data"}` en JSON canónico (claves ordenadas, sin espacios, UTF-8)."""
    body = {"domain": call.domain, "service": call.service, "data": call.data}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


HomeCallStatus = Literal["done", "pending", "denied", "failed"]


class HomeCallResult(Struct, kw_only=True):
    """Resultado inmediato de un `home_call` (va en `ToolResult.structured` de la tool `home`)."""

    request_id: str
    status: HomeCallStatus
    confirmation_id: str | None = None
    """Solo si `pending`."""
    action_digest: Sha256 | None = None
    expires_ns: int | None = None
    error: str | None = None


class ConfirmAction(Struct, kw_only=True, tag_field="type", tag="confirm_action"):
    """Consola (local o en la nube, como `CloudMessage`) → nodo. Aprueba o rechaza una confirmación pendiente;
    debe repetir el `action_digest`. v2.1: `approve` → `approved`, nuevo `by`, `operator` opcional."""

    confirmation_id: str
    approved: bool
    by: ConfirmVia = "console"
    action_digest: Sha256
    operator: str | None = None
    """Quién confirma (para auditoría)."""

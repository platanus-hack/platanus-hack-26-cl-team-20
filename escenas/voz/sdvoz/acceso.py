"""Lee el acceso de agente a una sesión de mesa desde un archivo (p. ej. el que llega por Taildrop), sin imprimirlo.

Formatos que entiende (el primero que calce):
- JSON con `user_token` (o `token`, `pcx_user_token`) y `mcp` / `mcp_url` / `url` / `gateway`.
- El comando para el agente: `claude mcp add --transport http pcx https://HOST/mcp --header "Authorization: Bearer …"`.
- El enlace de la mesa: `https://HOST/mesa#s=<session_id>.<node_token>.<user_token>`.
- Líneas de entorno: `PCX_USER_TOKEN=…` y `PCX_MCP_URL=…` (o `PCX_GATEWAY=…`).

Devuelve (gateway sin /mcp, token). Nunca escribe el token en pantalla ni en el registro.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def _gateway(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip().rstrip("/")
    url = re.sub(r"/mcp/?$", "", url)
    m = re.match(r"(https?://[^/#?\s]+)", url)
    return m.group(1) if m else None


def _de_json(d) -> tuple[str | None, str | None]:
    if isinstance(d, list):
        for x in d:
            g, t = _de_json(x)
            if t:
                return g, t
        return None, None
    if not isinstance(d, dict):
        return None, None
    tok = next((d[k] for k in ("user_token", "pcx_user_token", "token", "bearer") if isinstance(d.get(k), str)), None)
    url = next((d[k] for k in ("mcp", "mcp_url", "url", "gateway", "base") if isinstance(d.get(k), str)), None)
    if not tok:
        for v in d.values():
            g, t = _de_json(v)
            if t:
                return g or _gateway(url), t
    return _gateway(url), tok


def leer_acceso(ruta: str) -> tuple[str | None, str | None]:
    texto = Path(ruta).expanduser().read_text(encoding="utf-8", errors="ignore")
    try:
        g, t = _de_json(json.loads(texto))
        if t:
            return g, t
    except ValueError:
        pass
    m = re.search(r"(https?://[^\s\"']+)/mesa[^\s#]*#s=[^.\s]+\.[^.\s]+\.([A-Za-z0-9_\-]+)", texto)
    if m:
        return _gateway(m.group(1)), m.group(2)
    tok = None
    m = re.search(r"Authorization:\s*Bearer\s+([A-Za-z0-9_\-\.]+)", texto)
    if m:
        tok = m.group(1)
    m = re.search(r"PCX_USER_TOKEN\s*=\s*[\"']?([A-Za-z0-9_\-\.]+)", texto)
    if m and not tok:
        tok = m.group(1)
    url = None
    for patron in (r"PCX_MCP_URL\s*=\s*[\"']?(https?://\S+?)[\"'\s]", r"PCX_GATEWAY\s*=\s*[\"']?(https?://\S+?)[\"'\s]",
                   r"(https?://[^\s\"']+/mcp)\b"):
        m = re.search(patron, texto + "\n")
        if m:
            url = m.group(1)
            break
    return _gateway(url), tok

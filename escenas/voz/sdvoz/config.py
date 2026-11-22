"""Rutas, config y secretos por referencia.

- Llave de ElevenLabs: `$ELEVENLABS_API_KEY`, si no `~/.elevenlabs/api_key`, si no el llavero (servicio `elevenlabs`).
  Nunca se imprime, nunca se registra, nunca va al navegador: la página recibe un token de conversación.
- Config propia: `~/.config/superhuman-desk/voz.json` (0600). Guarda ids de agentes y herramientas, la URL del gateway
  y el token de la sesión de la mesa (también es secreto: no sale del Mac ni del servidor local).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

DIR = Path(os.environ.get("SD_VOZ_CONFIG_DIR", Path.home() / ".config" / "superhuman-desk"))
ARCHIVO = DIR / "voz.json"
REGISTRO = Path(os.environ.get("SD_VOZ_LOG", Path.home() / ".local" / "state" / "superhuman-desk" / "voz.jsonl"))

DEFAULTS = {
    "gateway": "http://localhost:9324",  # gateway local de la voz (?sim=1); en la demo, el de la mesa
    "puerto": 9325,                      # servidor local de la voz (página + token + client tools)
    "llm": "claude-haiku-5-5",
    "agentes": {},                       # escena → agent_id
    "herramientas": {},                  # nombre → tool_id (client tools, compartidas por los 3 agentes)
}


def load() -> dict:
    try:
        data = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        data = {}
    return {**DEFAULTS, **data}


def save(cambios: dict) -> dict:
    cfg = load()
    cfg.update(cambios)
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = ARCHIVO.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(ARCHIVO)
    return cfg


def api_key() -> str | None:
    if os.environ.get("ELEVENLABS_API_KEY"):
        return os.environ["ELEVENLABS_API_KEY"].strip()
    p = Path.home() / ".elevenlabs" / "api_key"
    if p.exists():
        k = p.read_text(encoding="utf-8").strip()
        if k:
            return k
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", "elevenlabs", "-w"], capture_output=True,
                           text=True, timeout=5)
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def api_key_source() -> str:
    if os.environ.get("ELEVENLABS_API_KEY"):
        return "$ELEVENLABS_API_KEY"
    if (Path.home() / ".elevenlabs" / "api_key").exists():
        return "~/.elevenlabs/api_key"
    return "llavero (elevenlabs)"


def session_token(cfg: dict | None = None) -> str | None:
    """Token de usuario de la sesión de la mesa: $PCX_USER_TOKEN o el guardado en la config."""
    return os.environ.get("PCX_USER_TOKEN") or (cfg or load()).get("pcx_user_token")


def gateway_url(cfg: dict | None = None) -> str:
    url = (os.environ.get("PCX_GATEWAY") or os.environ.get("PCX_MCP_URL") or (cfg or load())["gateway"]).rstrip("/")
    return url[:-4] if url.endswith("/mcp") else url        # se acepta con o sin /mcp


def log(tipo: str, datos: dict) -> None:
    """Una línea JSON por hecho. Nunca lleva llaves ni tokens."""
    try:
        REGISTRO.parent.mkdir(parents=True, exist_ok=True)
        with REGISTRO.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "tipo": tipo, **datos}, ensure_ascii=False)
                    + "\n")
    except OSError:
        pass

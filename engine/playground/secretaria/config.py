"""Config, rutas y secretos del agente de voz del playground. Nunca imprime ni registra llaves.

Todo lo propio vive fuera del repo (nada sensible en git):

    ~/.config/sl-scanner/secretaria/config.json   ids de ElevenLabs (agente, herramientas, secreto), URL del túnel, voz
    ~/.config/sl-scanner/secretaria/token         token del puente (600, se genera solo); ElevenLabs lo guarda como
                                                  secreto del workspace y lo manda en X-Playground-Token
    ~/.local/state/sl-scanner/secretaria/         logs (llamadas.jsonl, túnel), pids

Referencias (en este orden; la primera que exista gana):
    llave de ElevenLabs   $ELEVENLABS_API_KEY · archivo `api_key_archivo` (por defecto ~/.elevenlabs/api_key, el mismo
                          de la secretaria: misma cuenta de Max) · llavero `api_key_llavero` (security -s <servicio>)
    token del puente      $PLAYGROUND_PUENTE_TOKEN · archivo token · llavero `token_llavero`

Variables para pruebas: PLAYGROUND_SECRETARIA_CONFIG_DIR, PLAYGROUND_SECRETARIA_STATE_DIR.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import subprocess
from pathlib import Path

HOME = Path.home()
PUERTO = 8793          # webhooks (lo publica el túnel propio); 8790 runtime, 8791/8792 la secretaria
PUERTO_APP = 8794      # página local para hablar (solo 127.0.0.1, nunca por el túnel)
RUTA = "/playground"   # prefijo de las rutas del puente

DEFAULTS = {
    "nombre_agente": "Playground",
    "agente_id": "",
    "secreto_id": "",
    "secreto_nombre": "PLAYGROUND_PUENTE_TOKEN",
    "modo": "local",                           # local: client tools en el proceso de voz · tunel: webhooks
    "herramientas_ids": {},                    # webhooks (modo tunel)
    "herramientas_cliente_ids": {},            # client tools (modo local)
    "puente_url": "",
    "puerto": PUERTO,
    "puerto_app": PUERTO_APP,
    "runtime_url": os.environ.get("PLAYGROUND_URL", "http://127.0.0.1:8790"),
    # repo desde donde se levanta el runtime y donde está la cola requests/: el checkout principal, aunque este código
    # corra desde un worktree de un agente (si no, levantaría código viejo y dejaría pedidos donde nadie los lee)
    "runtime_repo": str(Path(__file__).resolve().parent.parent.parent).split("/.claude/worktrees/")[0],
    # Voz: Max pidió por voz (16-oct 02:57) la de su secretaria, Catalina, con eleven_v4 expresivo, «más pacífica».
    # Si una voz tiene moderación en vivo, la API la rechaza para agentes («Andrea Rabbit» e «Isa Pro» la tienen):
    # `publicar` prueba voces_respaldo en orden y guarda en voz_id la que quedó.
    "voz_id": "6Gr4AVmTax1pMJO0lHRK",          # «Secretaria · Catalina (CL)»
    "voces_respaldo": ["NDx3GTwfaeouBvRv4NrB"], # «Ignacio - Chileno natural, ritmo pausado»
    "llm": "claude-sonnet-5-5",
    "tts_modelo": "eleven_v4",
    "tts_expresivo": True,                     # expressive_mode de eleven_v4 (etiquetas [warmly], [whispers]…)
    "tts_velocidad": 0.9,                      # algo lenta
    "tts_estabilidad": 0.65,                   # pareja pero expresiva (la secretaria usa 0,6)
    "audio_entrada": "MacBook",                # micrófono del MacBook (por nombre; vacío = el por defecto)
    "audio_salida": "MacBook",                 # parlantes del MacBook
    "orq_cli": str(HOME / "bin" / "orq-creativo"),
    "orq_unidad": "h3",
    "api_key_archivo": str(HOME / ".elevenlabs" / "api_key"),
    "api_key_llavero": "",
    "token_llavero": "",
    "tunel": {"modo": "quick", "nombre": "", "hostname": ""},   # quick: trycloudflare sin cuenta; named: login de Max
    "cliente_js": str(HOME / ".local" / "share" / "secretaria" / "app" / "vendor" / "elevenlabs-client.js"),
}


def config_dir() -> Path:
    return Path(os.environ.get("PLAYGROUND_SECRETARIA_CONFIG_DIR", HOME / ".config" / "sl-scanner" / "secretaria"))


def state_dir() -> Path:
    return Path(os.environ.get("PLAYGROUND_SECRETARIA_STATE_DIR", HOME / ".local" / "state" / "sl-scanner" / "secretaria"))


def _private_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    path = config_dir() / "config.json"
    if path.exists():
        own = json.loads(path.read_text(encoding="utf-8") or "{}")
        for k, v in own.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


def save(changes: dict) -> dict:
    """Mezcla `changes` en config.json (600, escritura atómica). Devuelve la config completa."""
    d = _private_dir(config_dir())
    path = d / "config.json"
    current = json.loads(path.read_text(encoding="utf-8") or "{}") if path.exists() else {}
    current.update(changes)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return load()


def _keychain(service: str) -> str | None:
    if not service:
        return None
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"], capture_output=True,
                           text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def api_key(cfg: dict | None = None) -> str | None:
    """La llave de ElevenLabs por referencia (env → archivo → llavero). None si no hay."""
    cfg = cfg or load()
    env = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if env:
        return env
    path = Path(os.path.expanduser(cfg.get("api_key_archivo") or ""))
    if cfg.get("api_key_archivo") and path.is_file():
        val = path.read_text(encoding="utf-8").strip()
        if val:
            return val
    return _keychain(cfg.get("api_key_llavero", ""))


def api_key_source(cfg: dict | None = None) -> str:
    """Dónde está la llave (para decirlo sin mostrarla)."""
    cfg = cfg or load()
    if os.environ.get("ELEVENLABS_API_KEY", "").strip():
        return "variable ELEVENLABS_API_KEY"
    path = Path(os.path.expanduser(cfg.get("api_key_archivo") or ""))
    if cfg.get("api_key_archivo") and path.is_file():
        return f"archivo {cfg['api_key_archivo']}"
    if cfg.get("api_key_llavero"):
        return f"llavero (servicio {cfg['api_key_llavero']})"
    return "ninguna"


def token(create: bool = False) -> str | None:
    """Token del puente por referencia (env → archivo → llavero). Con create=True genera el archivo si falta."""
    env = os.environ.get("PLAYGROUND_PUENTE_TOKEN", "").strip()
    if env:
        return env
    path = config_dir() / "token"
    if path.is_file():
        val = path.read_text(encoding="utf-8").strip()
        if val:
            return val
    kc = _keychain(load().get("token_llavero", ""))
    if kc:
        return kc
    if not create:
        return None
    _private_dir(config_dir())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(32))
    return path.read_text(encoding="utf-8").strip()


def log(name: str, record: dict) -> None:
    """Una línea JSON en <state>/<name>.jsonl (600). Quien llama no pasa secretos."""
    d = _private_dir(state_dir())
    path = d / f"{name}.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"), **record},
                           ensure_ascii=False, default=str) + "\n")


def mask_url(url: str) -> str:
    """https://abc-def.trycloudflare.com → https://abc…trycloudflare.com (la URL del túnel no se muestra entera)."""
    from urllib.parse import urlparse
    if not url:
        return "(sin URL)"
    u = urlparse(url)
    host = u.hostname or ""
    parts = host.split(".")
    head = parts[0][:3] + "…" if parts else ""
    return f"{u.scheme}://{head}{'.'.join(parts[-2:]) if len(parts) > 1 else ''}"

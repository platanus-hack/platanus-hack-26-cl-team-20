"""Túnel PROPIO del agente «Playground» (no el de la secretaria): Cloudflare, el mismo proveedor que usa ella.

    python -m playground.secretaria tunel        # en primer plano (para launchd o una terminal)

Modos (config.json › tunel.modo):
  quick  (por defecto) `cloudflared tunnel --url http://127.0.0.1:8793`: túnel rápido de trycloudflare.com, sin
         cuenta ni login, con un subdominio al azar distinto del de la secretaria. La dirección cambia cada vez que
         se abre: al abrirse se guarda en la config y se republican las herramientas del agente con la nueva URL.
  named  `cloudflared tunnel run --url … <nombre>` con hostname fijo (p. ej. playground.<zona de Max>). Necesita que
         Max haga una vez `cloudflared tunnel login` (elige la zona en el navegador), `cloudflared tunnel create
         sl-scanner-playground` y `cloudflared tunnel route dns sl-scanner-playground <hostname>`; después
         tunel = {"modo": "named", "nombre": "sl-scanner-playground", "hostname": "<hostname>"}.

El puente solo responde bajo /playground y con token; la página local (:8794) nunca pasa por aquí.
"""

from __future__ import annotations

import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from . import config as cfgmod

QUICK_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def cloudflared() -> str | None:
    return shutil.which("cloudflared") or next((p for p in ("/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared")
                                                if Path(p).exists()), None)


def tunnel_command(cfg: dict, binary: str = "cloudflared") -> tuple[list[str], str | None]:
    """(argv, URL fija o None si hay que leerla del log)."""
    local = f"http://127.0.0.1:{int(cfg.get('puerto') or cfgmod.PUERTO)}"
    t = cfg.get("tunel") or {}
    if t.get("modo") == "named":
        if not (t.get("nombre") and t.get("hostname")):
            raise ValueError("tunel.modo named necesita tunel.nombre y tunel.hostname en la config")
        return [binary, "tunnel", "--no-autoupdate", "run", "--url", local, t["nombre"]], f"https://{t['hostname']}"
    return [binary, "tunnel", "--no-autoupdate", "--url", local], None


def find_url(text: str) -> str | None:
    m = QUICK_RE.search(text or "")
    return m.group(0) if m else None


def wait_health(url: str, seconds: float = 60, http: httpx.Client | None = None) -> bool:
    """El túnel recién abierto tarda unos segundos en resolver (DNS): se espera a que /playground/salud conteste."""
    http = http or httpx.Client(timeout=5)
    end = time.time() + seconds
    while time.time() < end:
        try:
            if http.get(f"{url}{cfgmod.RUTA}/salud").status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2)
    return False


def _say(msg: str) -> None:
    cfgmod.log("tunel", {"linea": msg})
    print(msg, flush=True)


def run(publish: bool = True) -> int:
    cfg = cfgmod.load()
    binary = cloudflared()
    if not binary:
        _say("falta cloudflared (brew install cloudflared)")
        return 1
    argv, fixed = tunnel_command(cfg, binary)
    logpath = cfgmod.state_dir() / "tunel.cloudflared.log"
    logpath.parent.mkdir(parents=True, exist_ok=True)
    log = open(logpath, "wb")
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)

    def stop(*_):
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    url = fixed
    for _ in range(60):
        if url or proc.poll() is not None:
            break
        time.sleep(1)
        url = find_url(logpath.read_text(errors="replace"))
    if not url:
        _say("cloudflared no entregó URL (ver tunel.cloudflared.log)")
        proc.terminate()
        return 1
    cfgmod.save({"puente_url": url})
    healthy = wait_health(url)
    _say(f"túnel abierto {cfgmod.mask_url(url)} · salud {'ok' if healthy else 'sin respuesta (¿puente apagado?)'}")
    if publish:
        if not cfgmod.api_key():
            _say("sin llave de ElevenLabs por referencia: el agente no se republicó")
        else:
            from .agent import publish as do_publish, ElevenLabsError
            try:
                do_publish(url, say=_say)
                _say("agente republicado con la URL nueva")
            except ElevenLabsError as e:
                _say(f"túnel abierto pero publicar falló: {e}")
    return proc.wait()

"""Procesos que la voz levanta (runtime y captura de audio), siempre desde el checkout principal (`runtime_repo`) y con
los mismos pidfiles y logs que el MCP `playground` (~/.local/state/sl-scanner/playground-<puerto>.pid,
audio-<puerto>.pid), para que el MCP y la voz vean y apaguen lo mismo.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from . import config as cfgmod


def state_dir() -> Path:
    return Path(os.environ.get("PLAYGROUND_STATE", Path.home() / ".local" / "state" / "sl-scanner"))


def repo() -> Path:
    return Path(cfgmod.load().get("runtime_repo") or cfgmod.DEFAULTS["runtime_repo"])


def python() -> str:
    py = repo() / ".venv" / "bin" / "python"
    return str(py) if py.exists() else sys.executable


def port_of(url: str) -> int:
    return urlparse(url).port or 8790


def pid_of(pidfile: Path) -> int | None:
    try:
        pid = int(pidfile.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def start(name: str, args: list[str], pidfile: Path, logfile: Path) -> subprocess.Popen:
    """`<repo>/.venv/bin/python -m <args>` desacoplado (sesión propia, log a archivo), con su pidfile."""
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    log = open(logfile, "ab")
    proc = subprocess.Popen([python(), "-m", *args], cwd=str(repo()), stdin=subprocess.DEVNULL, stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    pidfile.write_text(str(proc.pid))
    cfgmod.log("procesos", {"inicio": name, "pid": proc.pid, "repo": str(repo())})
    return proc


def stop(pidfile: Path) -> dict:
    pid = pid_of(pidfile)
    if pid is None:
        return {"status": "stopped", "pid": None}
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(50):
        if pid_of(pidfile) is None:
            break
        time.sleep(0.1)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    pidfile.unlink(missing_ok=True)
    return {"status": "stopped", "pid": pid}


def runtime_files(url: str) -> tuple[Path, Path]:
    p = port_of(url)
    return state_dir() / f"playground-{p}.pid", state_dir() / f"playground-{p}.log"


def audio_files(url: str) -> tuple[Path, Path]:
    p = port_of(url)
    return state_dir() / f"audio-{p}.pid", state_dir() / f"audio-{p}.log"

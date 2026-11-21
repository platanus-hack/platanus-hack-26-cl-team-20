"""Control del PC Windows por SSH (Tailscale): sincronizar código, correr comandos, chequeo.

Sin servicios externos: `tar` por el canal SSH para copiar el código (Windows trae tar.exe),
PowerShell como shell remoto (lo deja así windows/setup.ps1).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

SYNC_DIRS = ("slscan", "playground", "tests", "windows", "requests", "PLAN.md", "SPEC.md", "pyproject.toml")
EXCLUDES = ("__pycache__", ".pytest_cache", ".DS_Store")


class Remote:
    def __init__(self, host: str, user: str | None = None, root: str = "C:/sl-scanner", ssh: str = "ssh"):
        self.target = f"{user}@{host}" if user else host
        self.root = root
        self.ssh = ssh

    def _ssh_base(self) -> list[str]:
        return [self.ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.target]

    def run(self, command: str, capture: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess:
        """Corre `command` en PowerShell remoto, con C:\\sl-scanner como cwd y el venv en el PATH."""
        ps = (f"$env:Path = '{self.root}/.venv/Scripts;' + $env:Path; Set-Location '{self.root}'; {command}")
        return subprocess.run(self._ssh_base() + [ps], capture_output=capture, text=True, timeout=timeout)

    def ping(self) -> bool:
        r = subprocess.run(self._ssh_base() + ["echo ok"], capture_output=True, text=True, timeout=20)
        return r.returncode == 0 and "ok" in r.stdout

    def sync(self, project: Path, dirs=SYNC_DIRS) -> subprocess.CompletedProcess:
        """Empaqueta `dirs` del proyecto con tar y lo desempaqueta en el root remoto."""
        present = [d for d in dirs if (project / d).exists()]
        tar_cmd = ["tar", "czf", "-", "-C", str(project)] + [f"--exclude={e}" for e in EXCLUDES] + present
        remote = (f"New-Item -ItemType Directory -Force -Path '{self.root}' | Out-Null; "
                  f"tar xzf - -C '{self.root}'")
        env = {**os.environ, "COPYFILE_DISABLE": "1"}          # sin ._archivos AppleDouble en Windows
        tar = subprocess.Popen(tar_cmd, stdout=subprocess.PIPE, env=env)
        r = subprocess.run(self._ssh_base() + [remote], stdin=tar.stdout, capture_output=True, text=True)
        tar.stdout.close(); tar.wait()
        if tar.returncode != 0:
            raise RuntimeError(f"tar local falló ({tar.returncode})")
        return r

    def doctor(self) -> dict:
        r = self.run("python windows/doctor.py")
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or r.stdout.strip() or f"doctor falló ({r.returncode})")
        start = r.stdout.find("{")
        return json.loads(r.stdout[start:])


def summarize_doctor(d: dict) -> list[str]:
    lines = [f"{d.get('host')} · {d.get('os')} · Python {d.get('python')} · disco libre {d.get('disk_free_gb')} GB"]
    g = d.get("gpu", {})
    lines.append(f"GPU: {g.get('nvidia_smi')} · torch {g.get('torch')}")
    lines.append(f"displays: {[(m['w'], m['h']) for m in d.get('displays', [])]}")
    lines.append(f"cámaras: {[(c.get('index'), c.get('width'), c.get('height')) for c in d.get('cameras', [])]}")
    dmx = [p for p in d.get("serial_ports", []) if p.get("dmx_candidate")]
    lines.append(f"puertos serie: {len(d.get('serial_ports', []))} · candidatos DMX (FTDI): {[p['device'] for p in dmx]}")
    lines.append(f"módulos: {d.get('modules')}")
    return lines

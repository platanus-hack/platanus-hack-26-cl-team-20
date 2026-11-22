"""El LLM que lee la libreta y contesta: un proceso de Claude Code que queda abierto entre lecturas.

Arrancar `claude` toma 12 a 20 s en este Mac; con el proceso abierto, cada lectura toma lo que tarda el modelo (unos 6 s).
Usa la sesión de Claude Code de quien lo corre (sin API key), sin herramientas, sin MCP, sin hooks y sin los CLAUDE.md
de la máquina: solo la voz de la escena (`voz.md`) y las imágenes de la libreta.

`PlumaFija` contesta de una lista, para ensayar sin red ni modelo.
"""

from __future__ import annotations

import base64
import json
import queue
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent


def instrucciones(cfg: dict) -> str:
    r = cfg["respuesta"]
    texto = (AQUI / r.get("voz", "voz.md")).read_text()
    return texto.replace("{max_lineas}", str(r.get("max_lineas", 3))).replace("{max_caracteres}", str(r.get("max_caracteres", 26)))


def limpiar(respuesta: dict, cfg: dict) -> dict:
    """Que la respuesta quepa: líneas cortas, pocas, sin signos de exclamación."""
    r = cfg["respuesta"]
    max_l, max_c = r.get("max_lineas", 3), r.get("max_caracteres", 26)
    lineas = []
    for linea in respuesta.get("respuesta") or []:
        linea = str(linea).replace("¡", "").replace("!", "").strip()
        while len(linea) > max_c and len(lineas) < max_l:  # corta por palabra si el modelo se pasó
            corte = linea.rfind(" ", 0, max_c + 1)
            corte = corte if corte > 0 else max_c
            lineas.append(linea[:corte].strip())
            linea = linea[corte:].strip()
        if linea and len(lineas) < max_l:
            lineas.append(linea)
    return {"leido": str(respuesta.get("leido", "")), "respuesta": lineas or ["no alcanzo a leer", "¿lo repasas?"]}


def _json_de(texto: str) -> dict:
    m = re.search(r"\{.*\}", texto, re.S)
    if not m:
        raise ValueError(f"sin JSON en la respuesta: {texto[:200]!r}")
    return json.loads(m.group(0))


class Pluma:
    def __init__(self, cfg: dict, *, log=print):
        self.cfg = cfg
        self.log = log
        self.proc: subprocess.Popen | None = None
        self.salida: queue.Queue = queue.Queue()

    def abrir(self):
        r = self.cfg["respuesta"]
        cmd = [
            "claude", "-p",
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--model", r.get("modelo", "opus"), "--effort", r.get("esfuerzo", "low"),
            "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence",
            "--settings", json.dumps({"disableAllHooks": True}),
            "--system-prompt", instrucciones(self.cfg),
        ]
        # en una carpeta temporal: que no encuentre CLAUDE.md de ningún proyecto
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, cwd=tempfile.gettempdir(), bufsize=1)
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self):
        for linea in self.proc.stdout:
            if linea.startswith("{"):
                try:
                    self.salida.put(json.loads(linea))
                except json.JSONDecodeError:
                    pass
        self.salida.put(None)

    def cerrar(self):
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def _preguntar(self, contenido: list, timeout: float) -> str:
        if not self.proc or self.proc.poll() is not None:
            self.abrir()
        while not self.salida.empty():
            self.salida.get_nowait()
        msg = {"type": "user", "message": {"role": "user", "content": contenido}}
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        limite = time.monotonic() + timeout
        while True:
            resto = limite - time.monotonic()
            if resto <= 0:
                self.cerrar()
                raise TimeoutError(f"el modelo no contestó en {timeout:.0f} s")
            try:
                ev = self.salida.get(timeout=resto)
            except queue.Empty:
                continue
            if ev is None:
                self.proc = None
                raise RuntimeError("se cerró el proceso de claude")
            if ev.get("type") == "result":
                if ev.get("is_error"):
                    raise RuntimeError(f"claude: {ev.get('result') or ev.get('subtype')}")
                return ev.get("result") or ""

    def calentar(self, timeout: float = 90) -> float:
        """Arranca el proceso y hace una pregunta mínima, para que la primera lectura de verdad no pague el arranque."""
        t = time.monotonic()
        self._preguntar([{"type": "text", "text": 'Prueba de arranque, sin imagen. Contesta {"leido": "", "respuesta": ["listo"]}'}], timeout)
        return time.monotonic() - t

    def leer(self, imagenes_jpg: list[bytes], dicho: str | None = None, timeout: float = 45) -> dict:
        contenido = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(j).decode()}}
            for j in imagenes_jpg
        ]
        texto = "Esto acaba de escribir en la libreta."
        if dicho:
            texto += f" Además dijo en voz alta: «{dicho}»."
        contenido.append({"type": "text", "text": texto + " Contesta solo con el JSON."})
        return limpiar(_json_de(self._preguntar(contenido, timeout)), self.cfg)


class PlumaFija:
    """Contesta en orden las respuestas de `respuesta.fijas` de escena.json. Para ensayar sin modelo."""

    def __init__(self, cfg: dict, **_):
        self.cfg = cfg
        self.i = 0

    def abrir(self):
        pass

    def cerrar(self):
        pass

    def calentar(self, timeout: float = 0) -> float:
        return 0.0

    def leer(self, imagenes_jpg, dicho=None, timeout=0) -> dict:
        fijas = self.cfg["respuesta"].get("fijas") or [["listo"]]
        r = fijas[self.i % len(fijas)]
        self.i += 1
        time.sleep(0.8)  # que se alcance a ver el «leyendo»
        return limpiar({"leido": "(respuesta fija)", "respuesta": r}, self.cfg)

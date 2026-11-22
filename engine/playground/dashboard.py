"""Dashboard del show: una sola pestaña de Chrome en http://127.0.0.1:8795.

- «Nueva vista»: lanza `python -m playground.dale` y muestra su progreso en vivo (SSE) con los pasos, la instrucción
  grande cuando el dale espera a Max (proyector quieto / mover la cámara) y el botón Continuar (crea dale.continue).
- Efectos: objetos de la escena del runtime (:8790) con sus efectos compatibles (prender, apagar, params en vivo),
  escenas del showcase y «Apagar todo». Todo por proxy desde este servidor (sin CORS).
- Miniatura del frame proyectado y el visor del pipeline P6 (:8796) en un iframe.

    python -m playground.dashboard            # http://127.0.0.1:8795

Nunca abre ventanas (ni en el proyector ni fullscreen): solo sirve HTTP en loopback.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

PY = sys.executable
ROOT = Path(__file__).resolve().parents[1]
UI = Path(__file__).parent / "dashboard_ui"
STATE_DIR = Path.home() / ".local/state/sl-scanner"
CONTINUE_FILE = STATE_DIR / "dale.continue"
ESTADO_FILE = STATE_DIR / "dale.estado.json"
RUNTIME_URL = os.environ.get("SLSCAN_RUNTIME_URL", "http://127.0.0.1:8790")
PIPELINE_URL = os.environ.get("SLSCAN_PIPELINE_URL", "http://127.0.0.1:8796")
DALE_CMD = [PY, "-m", "playground.dale"]          # los tests lo cambian por un script falso
SHOWCASE_CMD = [PY, "-m", "playground.showcase"]
ESPERAS = ("proyector_quieto", "move_camera")       # eventos del dale que esperan «Continuar»
RUIDO = ("objc[", "OpenCV:", "WARN:")


# ------------------------------------------------------------------------------------------------- proceso del dale
class Dale:
    """Un dale a la vez. Junta eventos del stdout del proceso y de dale.estado.json (deduplicados por línea)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.cmd: list[str] = []
        self.run_id = 0
        self.events: list[dict] = []
        self.seen: set[str] = set()
        self.t0 = 0.0
        self._offset = 0
        self._watch: threading.Thread | None = None

    # --- eventos
    def _add(self, ev: dict, raw: str | None = None) -> None:
        with self.lock:
            if raw is not None:
                if raw in self.seen:
                    return
                self.seen.add(raw)
            ev = {**ev, "seq": len(self.events), "run": self.run_id}
            self.events.append(ev)

    def _reset(self) -> None:
        with self.lock:
            self.run_id += 1
            self.events = []
            self.seen = set()

    def ingest_line(self, line: str) -> None:
        line = line.rstrip()
        if not line or any(r in line for r in RUIDO):
            return
        if line.startswith("{"):
            try:
                d = json.loads(line)
                if isinstance(d, dict) and "paso" in d:
                    self._add({"kind": "evento", **d}, raw=line)
                    return
            except json.JSONDecodeError:
                pass
        self._add({"kind": "log", "msg": line[:300]})

    # --- proceso
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, cmd: list[str]) -> dict:
        if self.running():
            raise RuntimeError("ya hay un dale corriendo")
        self._reset()
        self.cmd = cmd
        self.t0 = time.time()
        CONTINUE_FILE.unlink(missing_ok=True)
        self._add({"kind": "proc", "estado": "inicio", "cmd": " ".join(cmd)})
        self.proc = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, bufsize=1, start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        threading.Thread(target=self._reader, args=(self.proc, self.run_id), daemon=True).start()
        return self.status()

    def _reader(self, proc: subprocess.Popen, run_id: int) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            if self.run_id != run_id:
                break
            self.ingest_line(line)
        rc = proc.wait()
        if self.run_id == run_id:
            self._add({"kind": "proc", "estado": "fin", "rc": rc, "seg": round(time.time() - self.t0, 1)})

    def cancel(self) -> bool:
        p = self.proc
        if p is None or p.poll() is not None:
            return False
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                p.kill()
        self._add({"kind": "log", "msg": "cancelado desde el dashboard"})
        return True

    def status(self) -> dict:
        with self.lock:
            return {"running": self.running(), "pid": self.proc.pid if self.proc else None, "cmd": " ".join(self.cmd),
                    "run": self.run_id, "n": len(self.events)}

    def since(self, run_id: int, seq: int) -> tuple[int, list[dict]]:
        with self.lock:
            if run_id != self.run_id:
                return self.run_id, list(self.events)
            return self.run_id, self.events[seq:]

    # --- dale.estado.json (eventos del dale aunque lo haya lanzado la secretaria u otra terminal)
    def poll_estado(self) -> None:
        try:
            size = ESTADO_FILE.stat().st_size
        except OSError:
            return
        if size < self._offset:            # el dale nuevo lo trunca al empezar
            self._offset = 0
            if not self.running():
                self._reset()
                self._add({"kind": "proc", "estado": "externo", "cmd": "dale lanzado fuera del dashboard"})
        if size == self._offset:
            return
        try:
            with ESTADO_FILE.open() as f:
                f.seek(self._offset)
                chunk = f.read()
        except OSError:
            return
        complete, _, _rest = chunk.rpartition("\n")
        if not _:
            return
        self._offset += len((complete + "\n").encode())
        for line in complete.splitlines():
            if line.strip():
                self.ingest_line(line)

    def watch(self, interval: float = 0.5) -> None:
        if self._watch:
            return

        def loop():
            while True:
                self.poll_estado()
                time.sleep(interval)
        self._watch = threading.Thread(target=loop, daemon=True)
        self._watch.start()


DALE = Dale()


def liberar_visor() -> None:
    """Cierra el visor del pipeline del dale anterior (si no, el :8796 seguiría mostrando la corrida vieja)."""
    subprocess.run(["pkill", "-f", "slscan pipeline serve"], capture_output=True)


def dale_cmd(o: dict) -> list[str]:
    cmd = list(DALE_CMD)
    try:
        views = int(o.get("views") or 1)
    except (TypeError, ValueError):
        raise HTTPException(400, "views debe ser 1, 2 o 3")
    if views not in (1, 2, 3):
        raise HTTPException(400, "views debe ser 1, 2 o 3")
    cmd += ["--views", str(views)]
    scale = (o.get("scale") or "").strip()
    if scale:
        if not re.fullmatch(r"(huincha|teclas):\d+(\.\d+)?", scale):
            raise HTTPException(400, "scale: huincha:MM o teclas:MM")
        cmd += ["--scale", scale]
        px = (o.get("scale_pixels") or "").strip()
        if px:
            if not re.fullmatch(r"\d+(\.\d+)?(,\d+(\.\d+)?){3}", px):
                raise HTTPException(400, "scale_pixels: x1,y1,x2,y2")
            cmd += ["--scale-pixels", px]
    calib = o.get("calib") or "auto"
    if calib not in ("auto", "skip"):
        raise HTTPException(400, "calib: auto o skip")
    cmd += ["--calib", calib]
    rig = (o.get("rig") or "").strip()
    if rig:
        if not (ROOT / rig).is_file() and not Path(rig).is_file():
            raise HTTPException(400, f"no existe el rig {rig}")
        cmd += ["--rig", rig]
    esc = (o.get("escena_efectos") or "").strip()
    if esc:
        from .showcase import ESCENAS
        if esc not in ESCENAS:
            raise HTTPException(400, f"escena desconocida: {esc}")
        cmd += ["--escena-efectos", esc]
    for flag in ("synthetic", "no_runtime", "auto_continue", "no_scan"):
        if o.get(flag):
            cmd.append("--" + flag.replace("_", "-"))
    return cmd


# ------------------------------------------------------------------------------------------------- proxy al runtime
def runtime_get(path: str, timeout: float = 2.5) -> bytes:
    with urllib.request.urlopen(RUNTIME_URL + path, timeout=timeout) as r:
        return r.read()


def runtime_cmd(cmd: str, args: dict, timeout: float = 6.0) -> dict:
    body = json.dumps({"cmd": cmd, "args": args}).encode()
    req = urllib.request.Request(RUNTIME_URL + "/api/command", body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:          # 400 del runtime: {"ok": false, "error": ...}
        try:
            return json.loads(e.read())
        except (ValueError, OSError):
            return {"ok": False, "error": f"runtime HTTP {e.code}"}


def compatibles(kind: str, gallery: list[dict]) -> list[str]:
    return [g["name"] for g in gallery if "*" in (g.get("targets") or ["*"]) or kind in (g.get("targets") or [])]


def escena() -> dict:
    try:
        st = json.loads(runtime_get("/api/state"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"el runtime no responde en {RUNTIME_URL} ({type(e).__name__})"}
    scene = st.get("scene") or {}
    gallery = [{k: g.get(k) for k in ("name", "label", "description", "targets", "params", "aliases")} for g in st.get("gallery", [])]
    objs = []
    raw = scene.get("objects") or {}
    for oid, o in (raw.items() if isinstance(raw, dict) else ((x.get("id"), x) for x in raw)):
        kind = o.get("kind") or "prop"
        objs.append({"id": o.get("id") or oid, "name": o.get("name") or oid, "kind": kind,
                     "puntos": len(o.get("contour") or []), "compatibles": compatibles(kind, gallery)})
    return {"ok": True, "objects": objs, "gallery": gallery, "effects": st.get("effects", []),
            "globales": [g["name"] for g in gallery if "*" in (g.get("targets") or [])],
            "coordinate_space": scene.get("coordinate_space"), "projector": st.get("projector"),
            "errors": st.get("errors") or {}, "fps": (st.get("stats") or {}).get("fps")}


# ------------------------------------------------------------------------------------------------- app
def build_app(watch: bool = True) -> FastAPI:
    app = FastAPI(title="sl-scanner · dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    nocache = {"Cache-Control": "no-store"}
    if watch:
        DALE.poll_estado()
        DALE.watch()

    @app.get("/")
    def index():
        return FileResponse(UI / "index.html", headers=nocache)

    @app.get("/ui/{name}")
    def ui(name: str):
        if name not in ("app.js", "style.css"):
            raise HTTPException(404)
        return FileResponse(UI / name, headers=nocache)

    # --- dale
    @app.post("/api/dale")
    def dale_start(body: dict = Body(default={})):
        cmd = dale_cmd(body or {})
        if DALE.running():
            raise HTTPException(409, "ya hay un dale corriendo")
        liberar_visor()
        return DALE.start(cmd)

    @app.get("/api/dale")
    def dale_state():
        st = DALE.status()
        _, evs = DALE.since(-1, 0)
        return {**st, "events": evs}

    @app.post("/api/cancelar")
    def cancelar():
        return {"cancelado": DALE.cancel(), **DALE.status()}

    @app.post("/api/continuar")
    def continuar():
        CONTINUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONTINUE_FILE.write_text(time.strftime("%H:%M:%S"))
        DALE._add({"kind": "log", "msg": "Continuar apretado"})
        return {"ok": True, "archivo": str(CONTINUE_FILE)}

    @app.get("/api/dale/events")
    async def dale_events(request: Request, hasta_fin: bool = False):
        """SSE: `reset` al cambiar de corrida, `dale` por evento, `status` cada ~1 s. Con ?hasta_fin=1 se cierra cuando
        el proceso termina (para curl y tests)."""
        async def gen():
            run, seq, beat = None, 0, 0
            while not await request.is_disconnected():
                cur, evs = DALE.since(run if run is not None else -1, seq)
                if cur != run:
                    yield f"event: reset\ndata: {json.dumps(DALE.status())}\n\n"
                    run, seq = cur, 0
                fin = False
                for ev in evs:
                    yield f"event: dale\ndata: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
                    fin = fin or (ev.get("kind") == "proc" and ev.get("estado") == "fin")
                seq += len(evs)
                if hasta_fin and fin and not DALE.running():
                    return
                beat += 1
                if beat >= 4:                        # estado del proceso cada ~1 s
                    yield f"event: status\ndata: {json.dumps(DALE.status())}\n\n"
                    beat = 0
                await asyncio.sleep(0.25)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/pipeline")
    def pipeline():
        try:
            with urllib.request.urlopen(PIPELINE_URL + "/api/pipeline", timeout=1.5) as r:
                d = json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": type(e).__name__}
        rep = d.get("report") or {}
        labels = d.get("labels") or {}
        return {"ok": True, "stages": [{"id": k, "label": labels.get(k, k), "state": v.get("state")} for k, v in (rep.get("stages") or {}).items()]}

    # --- efectos (proxy a :8790)
    @app.get("/api/escena")
    def get_escena():
        return escena()

    def _cmd(cmd: str, args: dict) -> JSONResponse:
        try:
            r = runtime_cmd(cmd, args)
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": f"runtime no responde ({type(e).__name__})"}, status_code=503)
        return JSONResponse(r, status_code=200 if r.get("ok", True) else 400)

    @app.post("/api/spawn")
    def spawn(body: dict = Body(...)):
        if not body.get("effect"):
            raise HTTPException(400, "falta effect")
        return _cmd("spawn", {"effect": body["effect"], "target": body.get("target") or None, "params": body.get("params") or None})

    @app.post("/api/kill")
    def kill(body: dict = Body(default={})):
        return _cmd("kill", {"id": (body or {}).get("id")})

    @app.post("/api/param")
    def param(body: dict = Body(...)):
        if not body.get("id") or not body.get("key"):
            raise HTTPException(400, "falta id o key")
        return _cmd("set_param", {"id": body["id"], "key": body["key"], "value": body.get("value")})

    @app.post("/api/showcase")
    def showcase(body: dict = Body(...)):
        from .showcase import ESCENAS
        esc = (body or {}).get("escena")
        if esc == "apagar":
            args = ["--apagar"]
        elif esc in ESCENAS:
            args = ["--escena", esc]
        else:
            raise HTTPException(400, f"escena desconocida: {esc}")
        try:
            r = subprocess.run(SHOWCASE_CMD + args, cwd=ROOT, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return JSONResponse({"ok": False, "error": "showcase tardó más de 60 s"}, status_code=504)
        out = (r.stdout + r.stderr).strip().splitlines()
        return JSONResponse({"ok": r.returncode == 0, "rc": r.returncode, "salida": out[-6:]}, status_code=200 if r.returncode == 0 else 502)

    @app.get("/api/showcase")
    def showcase_list():
        from .showcase import ESCENAS, ORDEN
        return {"escenas": ORDEN + [e for e in ESCENAS if e not in ORDEN],
                "detalle": {k: [f"{e}→{o or 'escena'}" for e, o, _ in v] for k, v in ESCENAS.items()}}

    @app.get("/api/frame.png")
    def frame():
        try:
            return Response(runtime_get("/api/frame.png", timeout=2.0), media_type="image/png", headers=nocache)
        except Exception:  # noqa: BLE001
            return Response(status_code=503)

    return app


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="dashboard del show (Nueva vista + efectos)")
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8795)
    a = p.parse_args(argv)
    import uvicorn
    print(f"dashboard en http://{a.host}:{a.port}", flush=True)
    uvicorn.run(build_app(), host=a.host, port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""MCP del playground (stdio): las tools que usa la secretaria y cualquier agente.

Se registra en Claude Code como `playground` (ver README). Habla con el runtime por HTTP
(`PLAYGROUND_URL`, por defecto http://127.0.0.1:8790) y con la cola de pedidos en `requests/`.
Las tools de hardware (crop, geometría, gestos, cámaras, MIDI, DMX, Ableton) son stubs que
responden con el estado conocido y dicen qué falta; se van llenando a medida que el PC y los
dispositivos entran. Ableton ya tiene su propio MCP en el Mac: aquí solo se enlaza. `pg_audio` levanta
el cliente de audio (`python -m playground.audio run`) como proceso aparte, igual que `pg_runtime`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

try:                                    # mcp >= 2: FastMCP pasó a llamarse MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:                     # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from .requests_queue import RequestQueue

URL = os.environ.get("PLAYGROUND_URL", "http://127.0.0.1:8790")
PROJECT = Path(__file__).parent.parent
STATE_DIR = Path(os.environ.get("PLAYGROUND_STATE", Path.home() / ".local" / "state" / "sl-scanner"))
queue_ = RequestQueue(PROJECT / "requests")
mcp = FastMCP("playground")


# ------------------------------------------------------------------ runtime como proceso aparte

def _port() -> int:
    from urllib.parse import urlparse
    return urlparse(URL).port or 8790


def _pidfile() -> Path:
    return STATE_DIR / f"playground-{_port()}.pid"


def _logfile() -> Path:
    return STATE_DIR / f"playground-{_port()}.log"


def _alive() -> bool:
    try:
        return httpx.get(f"{URL}/api/schema", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def _pid() -> int | None:
    try:
        pid = int(_pidfile().read_text().strip())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def runtime_start(wait_s: float = 15.0) -> dict:
    """Levanta `python -m playground serve` desacoplado (sesión propia, log en archivo, sin ventana)."""
    import subprocess, sys, time
    if _alive():
        return {"status": "running", "url": URL, "pid": _pid(), "started": False}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(_logfile(), "ab")
    proc = subprocess.Popen(
        [sys.executable, "-m", "playground", "serve", "--port", str(_port())],
        cwd=str(PROJECT), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True, close_fds=True)          # sobrevive al cierre de la sesión del agente
    _pidfile().write_text(str(proc.pid))
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if _alive():
            return {"status": "running", "url": URL, "pid": proc.pid, "started": True, "log": str(_logfile())}
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    tail = _logfile().read_text(errors="replace")[-800:] if _logfile().exists() else ""
    return {"status": "failed", "url": URL, "pid": proc.pid, "exit": proc.poll(), "log": str(_logfile()), "tail": tail}


def runtime_stop() -> dict:
    import signal, time
    pid = _pid()
    if pid is None:
        return {"status": "stopped", "pid": None}
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(40):
        if _pid() is None and not _alive():
            break
        time.sleep(0.1)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    _pidfile().unlink(missing_ok=True)
    return {"status": "stopped", "pid": pid}


def runtime_status() -> dict:
    return {"status": "running" if _alive() else "stopped", "url": URL, "pid": _pid(), "log": str(_logfile())}


def _ensure() -> dict | None:
    """None si el runtime responde (o se pudo levantar); si no, el dict de error."""
    if _alive():
        return None
    r = runtime_start()
    return None if r["status"] == "running" else {"error": f"no pude levantar el runtime: {r}"}


def _cmd(cmd: str, **args):
    err = _ensure()
    if err:
        return err
    try:
        r = httpx.post(f"{URL}/api/command", json={"cmd": cmd, "args": args}, timeout=10)
        d = r.json()
        return d.get("result") if d.get("ok") else {"error": d.get("error")}
    except httpx.HTTPError as e:
        return {"error": f"runtime no responde en {URL}: {e}"}


def _state():
    err = _ensure()
    if err:
        return err
    try:
        return httpx.get(f"{URL}/api/state", timeout=10).json()
    except httpx.HTTPError as e:
        return {"error": f"runtime no responde en {URL}: {e}"}


@mcp.tool()
def pg_runtime(action: str = "status") -> dict:
    """Runtime del playground como proceso aparte: status | start | stop | restart. Las demás tools lo levantan solas si está apagado."""
    if action == "start":
        return runtime_start()
    if action == "stop":
        return runtime_stop()
    if action == "restart":
        runtime_stop()
        return runtime_start()
    return runtime_status()


# ------------------------------------------------------------------ estado y escena

@mcp.tool()
def pg_state() -> dict:
    """Estado del playground: escena (objetos, personas, luces, proyectores), efectos vivos, galería, pedidos, fps."""
    s = _state()
    if "error" in s:
        return s
    return {"objects": {k: {"name": v["name"], "kind": v["kind"], "texture": v["texture"], "tracked": v["tracked"]}
                        for k, v in s["scene"]["objects"].items()},
            "people": list(s["scene"]["people"]), "lights": s["scene"]["lights"], "projectors": s["scene"]["projectors"],
            "effects_alive": len(s["effects"]),
            "effects": [f'{e["id"]}: {e["effect"]} sobre {e["target"]} (params {e["params"]})' for e in s["effects"]],
            "gallery": [g["name"] for g in s["gallery"]], "errors": s["errors"],
            "requests": s["requests"], "fps": s["stats"]["fps"], "beat": s["scene"]["beat"],
            "audio": {k: (s.get("audio") or {}).get("features", {}).get(k) for k in ("live", "bass", "beat", "bpm")}}


@mcp.tool()
def pg_scene_objects(kind: str = "") -> dict:
    """Objetos de la escena con su geometría (bbox mm, contorno, plano, textura, pose). kind filtra: table, screen, frame, prop, instrument, person."""
    sc = _cmd("scene")
    if "error" in sc:
        return sc
    objs = {k: v for k, v in sc["objects"].items() if not kind or v["kind"] == kind}
    return {"table_mm": sc["table_mm"], "objects": objs}


@mcp.tool()
def pg_find(query: str) -> dict:
    """Busca un objeto por nombre, id o tipo (p.ej. 'cocodrilo', 'cuadro', 'screen')."""
    return _cmd("find", query=query)


@mcp.tool()
def pg_geometry(object_id: str) -> dict:
    """Geometría de un objeto: dimensiones en mm, centro, área del contorno, si es plano y su normal. (Malla completa: cuando el PC escanee.)"""
    r = _cmd("find", query=object_id)
    o = (r or {}).get("object")
    if not o:
        return {"error": f"no existe {object_id}"}
    b = o["bbox"]
    xs = [p[0] for p in o["contour"]]; ys = [p[1] for p in o["contour"]]
    area = 0.5 * abs(sum(xs[i] * ys[(i + 1) % len(xs)] - xs[(i + 1) % len(xs)] * ys[i] for i in range(len(xs))))
    return {"id": o["id"], "size_mm": [b[3] - b[0], b[4] - b[1], b[5] - b[2]],
            "center_mm": [(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2],
            "contour_area_mm2": round(area), "plane": o["plane"], "texture": o["texture"], "albedo": o["albedo"],
            "tracked": o["tracked"], "velocity_mm_s": o["velocity"], "mesh": "pendiente: escaneo F2/F5 en el PC"}


# ------------------------------------------------------------------ efectos

@mcp.tool()
def pg_list_effects() -> dict:
    """Galería de efectos disponibles con lo que leen (needs), a qué objetos aplican y sus parámetros."""
    return _cmd("list_effects")


@mcp.tool()
def pg_spawn(effect: str, target: str = "", params: str = "") -> dict:
    """Spawnea un efecto sobre un objeto (target: nombre/id/tipo; vacío = sin objeto). params: JSON opcional; los colores van en RGB 0-255, p.ej. '{"color":[0,255,200]}' = turquesa."""
    p = json.loads(params) if params else None
    return _cmd("spawn", effect=effect, target=target or None, params=p)


@mcp.tool()
def pg_kill(instance_id: str = "") -> dict:
    """Mata una instancia de efecto (id tipo e3) o todas si va vacío."""
    return _cmd("kill", id=instance_id or None)


@mcp.tool()
def pg_set_param(instance_id: str, key: str, value: str) -> dict:
    """Cambia un parámetro de una instancia viva. value en JSON (número, lista, texto entre comillas); colores en RGB 0-255."""
    try:
        v = json.loads(value)
    except json.JSONDecodeError:
        v = value
    return _cmd("set_param", id=instance_id, key=key, value=v)


@mcp.tool()
def pg_snapshot(path: str = "") -> dict:
    """Guarda un PNG de lo que está proyectando ahora (para mostrárselo a Max)."""
    return _cmd("snapshot", **({"path": path} if path else {}))


@mcp.tool()
def pg_beat(bpm: float = 0, playing: str = "") -> dict:
    """Fija el tempo del playground (hasta que Ableton mande el suyo). playing: 'true'/'false'."""
    args = {}
    if bpm:
        args["bpm"] = bpm
    if playing:
        args["playing"] = playing.lower() == "true"
    return _cmd("beat", **args)


# ------------------------------------------------------------------ pedidos de features

@mcp.tool()
def pg_request_feature(title: str, body: str, kind: str = "visual", sender: str = "secretaria") -> dict:
    """Deja un pedido de feature (visual | sound | tool | fix) en requests/NNN-slug.md para la unidad que programa en vivo. Escribe qué quiere Max, sobre qué objeto y cómo se vería/sonaría."""
    r = queue_.add(title, body, kind, sender)
    _cmd("request", title=title, body=body, kind=kind)
    return r.to_json()


@mcp.tool()
def pg_requests(status: str = "") -> dict:
    """Lista los pedidos (status: open | doing | done | rejected; vacío = todos)."""
    return {"requests": [r.to_json() for r in queue_.list(status or None)], "summary": queue_.summary()}


# ------------------------------------------------------------------ hardware (stubs honestos)

@mcp.tool()
def pg_cameras() -> dict:
    """Cámaras del rig y su estado (Osmo×2, FX3, cenital). Hoy lista lo que ve esta máquina."""
    try:
        from slscan.capture import list_cameras, list_displays
        return {"cameras": list_cameras(6), "displays": list_displays(),
                "rig": "sin calibrar: F1 pendiente de hardware en el PC"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


@mcp.tool()
def pg_crop_object(object_id: str, camera: str = "general") -> dict:
    """Recorte visual de un objeto en el feed de una cámara (máscara + bbox). Requiere el rig calibrado y SAM en el PC; hoy devuelve el bbox proyectado en el lienzo."""
    r = _cmd("find", query=object_id)
    o = (r or {}).get("object")
    if not o:
        return {"error": f"no existe {object_id}"}
    return {"object": o["id"], "camera": camera, "bbox_mm": o["bbox"], "contour_mm": o["contour"],
            "mask": "pendiente: SAM sobre el feed 4K en el PC (F5)"}


@mcp.tool()
def pg_gestures() -> dict:
    """Gestos y poses detectados ahora: toques y manos sobre la mesa (mesa táctil) y personas (F6, pendiente)."""
    s = _state()
    if "error" in s:
        return s
    sc, tc = s.get("scene", {}), s.get("touch", {})
    return {"touches": tc.get("touches", {}), "hands": tc.get("hands", {}), "people": sc.get("people", {}),
            "status": "toques y manos: comando touch / hands (playground.hands, playground.touchsynth live); "
                      "cuerpo: F6 pendiente en el PC"}


@mcp.tool()
def pg_touch(action: str = "list", x: float = -1, y: float = -1, target: str = "", id: str = "",
             velocity: float = -1, salida: str = "") -> dict:
    """Mesa táctil (cocodrilo sintetizador y cocodrilo vivo, dibujo, marimba, linterna).
    action: list (toques vivos, manos, instrumentos y estado de la fusión de cámaras) | tap (toque corto) | down |
    move | up | clear (borra los dibujos) | panic (apaga todas las notas) | recalibrar (la Osmo vuelve a medir la
    proyección) | sonido (por dónde suena: salida 'auto' = la mesa (PC) si responde, si no el MacBook; 'pc', 'mac',
    'virtual' (Ableton), 'null'; vacío = ver el estado). Posición: x, y en px del proyector (1920x1080) o target:
    'croc:0' (zona 0 = cabeza), 'croc:9', 'marimba:3', 'cocodrilo', 'marimba'. id: para seguir un mismo toque entre
    down/move/up. velocity 0..1 opcional."""
    if action == "list":
        return _cmd("touches")
    if action in ("clear", "panic", "recalibrar"):
        return _cmd("touchsynth", action=action)
    if action == "sonido":
        return _cmd("touchsynth", action="sonido", **({"salida": salida} if salida else {}))
    if action not in ("tap", "down", "move", "up"):
        return {"error": f"acción desconocida: {action}"}
    args: dict = {"state": action, "source": "mcp"}
    if x >= 0 and y >= 0:
        args.update(x=x, y=y)
    elif target:
        args["target"] = target
    else:
        return {"error": "falta x, y (px del proyector) o target ('croc:3', 'marimba:5')"}
    if id:
        args["id"] = id
    if velocity >= 0:
        args["velocity"] = velocity
    return _cmd("touch", **args)


@mcp.tool()
def pg_midi(action: str = "status", device: str = "ohm", control: str = "", value: float = 0.0, state: str = "move",
            prefer: str = "auto") -> dict:
    """Dispositivos MIDI del playground (OHM RGB hoy; Circuit Tracks y DDJ-SB2 con el pedido 001).
    action: status (dispositivos, fuente, puertos MIDI, últimos eventos) | connect (prefer auto|smartdeck|direct|fake) |
    simulate (control + value 0..1 + state) para probar sin equipo. Quién manda el puerto del OHM: SmartDeck si corre; si no, directo."""
    if action == "connect":
        return _cmd("connect_devices", prefer=prefer)
    if action == "simulate":
        return _cmd("simulate", device=device, control=control, value=value, state=state)
    return _cmd("devices")


# ------------------------------------------------------------------ audio reactivo (cliente como proceso aparte)

def _audio_pidfile() -> Path:
    return STATE_DIR / f"audio-{_port()}.pid"


def _audio_logfile() -> Path:
    return STATE_DIR / f"audio-{_port()}.log"


def _audio_pid() -> int | None:
    try:
        pid = int(_audio_pidfile().read_text().strip())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def _audio_summary() -> dict:
    a = _cmd("audio")
    if not isinstance(a, dict) or "error" in a:
        return {"runtime": a}
    m = a.get("meta") or {}
    f = a.get("features") or {}
    return {"live": a.get("live"), "levels": {k: f.get(k) for k in ("sub", "bass", "mid", "high", "rms", "beat")},
            "onsets": f.get("onsets"), "bpm": f.get("bpm"), "device": m.get("device"), "kind": m.get("kind"),
            "lag_ms": a.get("lag_ms"), "in_latency_ms": m.get("in_latency_ms"), "silent": m.get("silent"),
            "binds": a.get("binds")}


def audio_start(device: str = "", channel: int = -1, wait_s: float = 8.0) -> dict:
    """Levanta `python -m playground.audio run` desacoplado, publicando al runtime de PLAYGROUND_URL."""
    import subprocess, sys, time
    err = _ensure()
    if err:
        return err
    if _audio_pid():
        return {"status": "running", "pid": _audio_pid(), "started": False, **_audio_summary()}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(_audio_logfile(), "ab")
    cmd = [sys.executable, "-m", "playground.audio", "run", "--url", URL, "--log", "--channel", str(channel)]
    if device:
        cmd += ["--device", device]
    proc = subprocess.Popen(cmd, cwd=str(PROJECT), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True, close_fds=True)
    _audio_pidfile().write_text(str(proc.pid))
    t0 = time.time()
    while time.time() - t0 < wait_s:
        a = _cmd("audio")
        if isinstance(a, dict) and a.get("live") and (a.get("meta") or {}).get("pid") == proc.pid:
            return {"status": "running", "pid": proc.pid, "started": True, "log": str(_audio_logfile()), **_audio_summary()}
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    tail = _audio_logfile().read_text(errors="replace")[-800:] if _audio_logfile().exists() else ""
    return {"status": "failed" if proc.poll() is not None else "starting", "pid": proc.pid, "exit": proc.poll(),
            "log": str(_audio_logfile()), "tail": tail}


def audio_stop() -> dict:
    import signal, time
    pid = _audio_pid()
    if pid is None:
        return {"status": "stopped", "pid": None}
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)       # el cliente avisa al runtime (stop) antes de salir
    except ProcessLookupError:
        pass
    for _ in range(40):
        try:
            os.waitpid(pid, os.WNOHANG)                  # si es hijo de este proceso, que no quede zombi
        except ChildProcessError:
            pass
        if _audio_pid() is None:
            break
        time.sleep(0.1)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    _audio_pidfile().unlink(missing_ok=True)
    return {"status": "stopped", "pid": pid}


@mcp.tool()
def pg_audio(action: str = "status", device: str = "", channel: int = -1, features: str = "") -> dict:
    """Audio reactivo: capta el bajo / la mezcla y publica audio.sub/bass/mid/high/rms/beat/bpm al runtime; los
    efectos bass_pulse y bass_ripple (y contour_glow/control_glow con audio=bass) lo siguen.
    action: status (niveles, golpes, bpm, dispositivo, latencia) | start (device: nombre o índice; vacío = QX1222 >
    BlackHole/Loopback > micrófono; channel = canal de entrada, -1 mezcla) | stop | restart | devices (entradas y cuál
    se elige) | simulate (features JSON, p.ej. '{"bass":0.9,"beat":1}': prueba sin audio, dura 2 s)."""
    if action == "start":
        return audio_start(device, channel)
    if action == "stop":
        return audio_stop()
    if action == "restart":
        audio_stop()
        return audio_start(device, channel)
    if action == "devices":
        import subprocess, sys
        try:
            out = subprocess.run([sys.executable, "-m", "playground.audio", "devices", "--json"], cwd=str(PROJECT),
                                 capture_output=True, text=True, timeout=20)
            return json.loads(out.stdout) if out.returncode == 0 else {"error": out.stderr[-400:]}
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            return {"error": str(e)}
    if action == "simulate":
        try:
            f = json.loads(features) if features else {"bass": 0.9, "beat": 1.0}
        except json.JSONDecodeError as e:
            return {"error": f"features no es JSON: {e}"}
        _cmd("audio", features=f, hold=2.0)
        return {"simulated": f, **_audio_summary()}
    summary = _audio_summary() if _alive() else {"runtime": "apagado"}
    status = "running" if _audio_pid() else ("externo" if summary.get("live") else "stopped")   # externo: otro cliente publica
    return {"status": status, "pid": _audio_pid(), "log": str(_audio_logfile()), **summary}


@mcp.tool()
def pg_controls(device: str = "ohm", type: str = "") -> dict:
    """Controles de un dispositivo con posición en la skin, valor 0..1 y último estado. type filtra: knob | fader | pad | button | crossfader."""
    return _cmd("controls", device=device, type=type)


@mcp.tool()
def pg_bind(control: str, target: str, lo: float = 0.0, hi: float = 1.0, remove: bool = False) -> dict:
    """Enlaza un control (p.ej. 'ohm/knob0') a un parámetro: 'effect:e2:width_mm' (lo..hi) o 'beat:bpm'. remove=True lo quita.
    El audio también sirve de control: 'audio/bass' (sub, mid, high, rms, beat) → cualquier parámetro sigue al bajo;
    'audio/bpm' → 'beat:bpm' (lo 0, hi 1) hace que el tempo del playground siga al de los golpes."""
    if remove:
        return _cmd("unbind", control=control, target=target)
    return _cmd("bind", control=control, target=target, lo=lo, hi=hi)


@mcp.tool()
def pg_pianohero(action: str = "state", level: int = 0, song: str = "", practice: str = "", offset_ms: float | None = None,
                 speed: float = 0.0, prefer: str = "", demo: str = "") -> dict:
    """Piano Hero proyectado sobre las teclas del Korg EK-50: las notas bajan por el carril de su tecla y se tocan en el
    teclado real. action: start (empieza; con level/song/practice cambia antes) | pause | resume | toggle | restart |
    level (1 fácil, 2 medio, 3 difícil) | song (oda, estrellita, elisa, escala o ruta a un .mid) | practice ('true'/'false':
    espera cada nota) | offset (offset_ms: latencia a compensar) | state (puntaje, combo, precisión, progreso, teclado) |
    songs | stop. demo 'true': se toca solo (para mostrarlo sin teclado). prefer: fuente del teclado (auto: local si
    está en el Mac, si no el puente del PC; bridge, direct, fake). Corre como efecto en el runtime: se ve en el proyector
    si ese runtime proyecta (serve --projector o python -m playground.pianohero); las teclas salen de la escena."""
    args: dict = {"action": action}
    if level:
        args["level"] = int(level)
    if song:
        args["song"] = song
    if practice:
        args["practice"] = practice.lower() in ("true", "1", "si", "sí", "on")
    if offset_ms is not None:
        args["offset_ms"] = float(offset_ms)
    if speed:
        args["speed"] = float(speed)
    if prefer:
        args["prefer"] = prefer
    if demo:
        args["demo"] = demo.lower() in ("true", "1", "si", "sí", "on")
    return _cmd("pianohero", **args)


@mcp.tool()
def pg_light(action: str = "status", light: str = "smallrig", match: str = "", seconds: float = 8.0, address: str = "",
             on: str = "", intensity: float = -1, cct: int = 0, rgb: str = "", char: str = "", data_hex: str = "") -> dict:
    """Foco SmallRig RC 100 por Bluetooth LE (y otras luces BLE).
    action: status | scan (match filtra por nombre, seconds) | dump (address: servicios y características, solo lectura) |
    set (on 'true'/'false', intensity 0..1, cct K, rgb '[r,g,b]') | learn (address + char: guarda qué característica se escribe) |
    write (address, char, data_hex: bytes crudos para aprender el protocolo con el foco a la vista).
    Requiere Bluetooth encendido en esta máquina; si está apagado, lo dice."""
    from .lights import BleLight, scan, dump_gatt
    from .lights.ble import write_raw
    if action == "scan":
        return scan(seconds, match)
    if action == "dump":
        return dump_gatt(address) if address else {"error": "falta address (pg_light scan)"}
    if action == "write":
        return write_raw(address, char, data_hex) if (address and char and data_hex) else {"error": "faltan address, char o data_hex"}
    lt = BleLight(light)
    if action == "learn":
        return lt.learn(address=address or None, write_char=char or None)
    if action == "set":
        return lt.set(on=(on.lower() == "true") if on else None, intensity=intensity if intensity >= 0 else None,
                      cct=cct or None, rgb=json.loads(rgb) if rgb else None)
    return lt.status()


@mcp.tool()
def pg_dmx(light: str = "dmx0", rgb: str = "", cct: int = 0, intensity: float = -1) -> dict:
    """Luces DMX (barra o foco con DMX): status o set (rgb '[r,g,b]' 0-1, cct K, intensity 0-1). Pendiente de una interfaz USB-DMX; el SmallRig va por pg_light."""
    return {"light": light, "status": "pendiente: interfaz USB-DMX no conectada (F3 en el PC)",
            "wanted": {"rgb": rgb, "cct": cct, "intensity": intensity}}


@mcp.tool()
def pg_ableton() -> dict:
    """Cómo controlar Ableton: usa el MCP `ableton` del Mac (get_session_info, fire_clip, set_device_parameter, set_tempo…). El playground recibe el tempo por pg_beat."""
    return {"use": "mcp ableton (ya instalado en el Mac)", "tools": ["get_session_info", "fire_clip", "set_tempo",
            "set_device_parameter", "create_midi_track", "add_notes_to_clip"], "bridge": "pendiente: tempo de Live → pg_beat automático"}


if __name__ == "__main__":
    mcp.run()

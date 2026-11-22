"""Puente toques → runtime del playground, sin tocar runtime.py.

Formato propuesto del comando "touch" (lo agrega quien mantiene runtime.py):

    POST /api/command
    {"cmd": "touch", "args": {
        "id": "t12",                     # id del toque: nace en "down" y vive hasta su "up"
        "state": "down" | "move" | "up",
        "x": 812.4, "y": 433.0,          # px del lienzo del proyector `projector`
        "space": "proj", "projector": "proj0",
        "mm": [x, y] | null,             # mm sobre la mesa si hay métrica (calib.H_proj_to_mm)
        "hand": "3", "handed": "Right",  # mano en el servicio (id estable mientras se vea)
        "trigger": "pinch" | "apoyo",
        "t": 1760000000.123,             # epoch s, reloj del Mac (captura en el PC corregida por el desfase)
        "source": "brio",
        "reason": "mano perdida"}}       # opcional, en algunos "up"
    → {"ok": true, "result": {...}}

Sugerencia para `Runtime.command`:

    if cmd == "touch":
        self.emit("touch", **a)                       # llega a /api/events (SSE) y a pg_gestures
        touches = self.scene.__dict__.setdefault("touches", {})
        if a["state"] == "up":
            touches.pop(a["id"], None)
        else:
            touches[a["id"]] = a                      # los efectos leen scene.touches
        return {"touches": len(touches)}

y en SCHEMA: "touch": {"id": "str", "state": "down|move|up", "x": "px", "y": "px", "projector": "str?",
"mm": "[x, y]?", "hand": "str?", "trigger": "pinch|apoyo?", "t": "float?"}.

Mientras el runtime no tenga el comando:
- con un objeto Runtime en el mismo proceso, el puente cae a `rt.emit("touch", **args)` (el evento igual llega a
  /api/events);
- por HTTP el runtime responde 400 "comando desconocido: touch": el puente lo anota en `status()` y sigue mandando
  (cuando el runtime se reinicie con el comando, empieza a funcionar solo).

Por HTTP los envíos van en un hilo aparte (la red no frena el tracking); si la cola se llena se botan "move", nunca
"down" ni "up".
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .touch import TouchEvent


class TouchBridge:
    def __init__(self, target: Any, projector: str = "proj0", source: str = "brio", timeout: float = 0.5,
                 queue_size: int = 256):
        self.target = target
        self.url = target.rstrip("/") if isinstance(target, str) else None
        self.projector = projector
        self.source = source
        self.timeout = timeout
        self.sent = 0
        self.errors = 0
        self.dropped = 0
        self.fallback_emit = 0
        self.unsupported = False
        self.last_error: str | None = None
        self.last_result: Any = None
        self._q: queue.Queue | None = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        if self.url is not None:
            self._q = queue.Queue(maxsize=queue_size)
            self._worker = threading.Thread(target=self._drain, name="touch-bridge", daemon=True)
            self._worker.start()

    @property
    def mode(self) -> str:
        return "http" if self.url is not None else "object"

    def args_of(self, ev: TouchEvent | dict) -> dict:
        return ev.to_args(self.projector, self.source) if isinstance(ev, TouchEvent) else dict(ev)

    # ------------------------------------------------------------------ envío
    def send(self, ev: TouchEvent | dict) -> None:
        args = self.args_of(ev)
        if self._q is None:
            self._deliver_object(args)
            return
        try:
            self._q.put_nowait(args)
        except queue.Full:
            if args.get("state") == "move":
                self.dropped += 1
                return
            try:
                self._q.put(args, timeout=self.timeout)      # down/up esperan un poco antes de perderse
            except queue.Full:
                self.dropped += 1
                self.last_error = "cola llena: runtime lento o caído"

    def _deliver_object(self, args: dict) -> None:
        rt = self.target
        try:
            self.last_result = rt.command("touch", args)
            self.sent += 1
            self.unsupported = False
        except KeyError as e:                              # runtime sin el comando "touch" todavía
            self.unsupported = True
            self.last_error = str(e)
            if hasattr(rt, "emit"):
                rt.emit("touch", **args)
                self.fallback_emit += 1
            else:
                self.errors += 1
        except Exception as e:  # noqa: BLE001  un error del runtime no tumba el tracking
            self.errors += 1
            self.last_error = f"{type(e).__name__}: {e}"

    def _post(self, args: dict) -> None:
        body = json.dumps({"cmd": "touch", "args": args}).encode()
        req = urllib.request.Request(f"{self.url}/api/command", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                self.last_result = json.loads(r.read() or b"{}")
            self.sent += 1
            self.unsupported = False
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            self.errors += 1
            self.unsupported = "desconocido" in detail
            self.last_error = f"HTTP {e.code}: {detail[:200]}"
        except Exception as e:  # noqa: BLE001  runtime caído o red: se sigue
            self.errors += 1
            self.last_error = f"{type(e).__name__}: {e}"

    def _drain(self) -> None:
        while not self._stop.is_set() or (self._q is not None and not self._q.empty()):
            try:
                args = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            self._post(args)
            self._q.task_done()

    def flush(self, timeout: float = 2.0) -> None:
        """Espera a que la cola HTTP se vacíe (para tests y al cerrar)."""
        if self._q is None:
            return
        t_end = time.time() + timeout
        while self._q.unfinished_tasks and time.time() < t_end:
            time.sleep(0.01)

    def close(self) -> None:
        self.flush()
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=2)

    def status(self) -> dict:
        return {"mode": self.mode, "target": self.url or type(self.target).__name__, "sent": self.sent,
                "errors": self.errors, "dropped": self.dropped, "fallback_emit": self.fallback_emit,
                "runtime_sin_touch": self.unsupported, "last_error": self.last_error}


# ---------------------------------------------------------------------- lazo completo

def run_loop(client, tracker, bridge: TouchBridge | None = None, seconds: float | None = None,
             on_event: Callable[[TouchEvent, float], None] | None = None, stop: threading.Event | None = None) -> dict:
    """Cliente → toques → puente hasta `seconds` (o para siempre). Devuelve estadísticas con la latencia medida
    captura (PC) → evento listo (Mac), en ms."""
    t_end = None if seconds is None else time.time() + seconds
    last_seq = None
    lat: list[float] = []
    counts = {"down": 0, "move": 0, "up": 0}
    frames = 0
    while (t_end is None or time.time() < t_end) and not (stop is not None and stop.is_set()):
        f = client.wait(timeout=0.3, after_seq=last_seq)
        now = time.time()
        if f is None:
            evs = tracker.update([], now)                 # sin mensajes: las manos se dan por perdidas
            t_cap = now
        else:
            last_seq = f.seq
            frames += 1
            t_cap = client.to_local_time(f.t) if (f.t and client.clock_offset is not None) else f.received
            evs = tracker.update(f.hands, f.received)
        for ev in evs:
            ev.t = t_cap
            counts[ev.state] += 1
            lat.append(1000 * (time.time() - t_cap))
            if bridge is not None:
                bridge.send(ev)
            if on_event is not None:
                on_event(ev, lat[-1])
    lat.sort()
    return {"frames": frames, "events": counts,
            "latency_ms_median": round(lat[len(lat) // 2], 1) if lat else None,
            "latency_ms_p90": round(lat[int(len(lat) * 0.9)], 1) if lat else None,
            "client": client.status(), "bridge": bridge.status() if bridge else None}

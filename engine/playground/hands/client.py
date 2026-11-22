"""Cliente (Mac) del servicio de manos del PC: WebSocket :8793, estado más reciente y reconexión sola.

    from playground.hands.client import HandsClient
    c = HandsClient("ws://192.168.1.233:8793").start()
    frame = c.wait(timeout=2)          # HandsFrame o None
    c.hands()                          # manos del último mensaje si no está viejo (stale_s), si no []
    c.status()                         # conectado, fps, latencia estimada, reconexiones, error

Corre en un hilo propio. Si el PC se cae o se corta la red, reintenta con espera creciente (0,5 → 5 s) y vuelve a
quedar al día sin que nadie haga nada. Cada `ping_every` s manda {"type": "ping", "t0"} y con el "pong" estima el
desfase de relojes Mac↔PC (muestra de menor ida y vuelta, como NTP): así `status()["latency_ms"]` es la latencia
captura en el PC → llegada al Mac, no solo la de la red.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Callable

from .model import Hand, HandsFrame

DEFAULT_PORT = 8793


def ws_url(pc: str, port: int = DEFAULT_PORT) -> str:
    """'192.168.1.233' → 'ws://192.168.1.233:8793/'; deja pasar una URL ws:// completa."""
    if pc.startswith(("ws://", "wss://")):
        return pc
    host = pc.split("://")[-1].split("/")[0]
    if ":" not in host:
        host = f"{host}:{port}"
    return f"ws://{host}/"


def _connect(url: str, timeout: float):
    from websockets.sync.client import connect
    try:
        return connect(url, open_timeout=timeout, proxy=None, compression=None)   # proxy: websockets >= 15
    except TypeError:
        return connect(url, open_timeout=timeout, compression=None)


class HandsClient:
    def __init__(self, url: str = f"ws://192.168.1.233:{DEFAULT_PORT}/", on_frame: Callable[[HandsFrame], None] | None = None,
                 stale_s: float = 0.5, ping_every: float = 2.0, open_timeout: float = 3.0,
                 backoff: tuple[float, float] = (0.5, 5.0)):
        self.url = ws_url(url)
        self.on_frame = on_frame
        self.stale_s = stale_s
        self.ping_every = ping_every
        self.open_timeout = open_timeout
        self.backoff = backoff
        self.latest: HandsFrame | None = None
        self.connected = False
        self.reconnects = 0                 # conexiones logradas después de la primera
        self.messages = 0
        self.error: str | None = None
        self._connections = 0
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws = None
        self._arrivals: deque[float] = deque(maxlen=60)
        self._pongs: deque[tuple[float, float]] = deque(maxlen=20)      # (ida y vuelta, desfase PC − Mac)
        self._lat: deque[float] = deque(maxlen=60)

    # ------------------------------------------------------------------ ciclo de vida
    def start(self) -> "HandsClient":
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="hands-client", daemon=True)
            self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)

    def __enter__(self) -> "HandsClient":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ lectura
    def wait(self, timeout: float = 1.0, after_seq: int | None = None) -> HandsFrame | None:
        """Espera un mensaje nuevo (con seq distinto de `after_seq`, o cualquiera si no hay ninguno)."""
        with self._cond:
            ok = self._cond.wait_for(lambda: self.latest is not None and (after_seq is None or self.latest.seq != after_seq),
                                     timeout=timeout)
            return self.latest if ok else None

    def hands(self) -> list[Hand]:
        f = self.latest
        if f is None or time.time() - f.received > self.stale_s:
            return []
        return f.hands

    @property
    def clock_offset(self) -> float | None:
        """Reloj del PC − reloj local (s), de la muestra con menor ida y vuelta; None si aún no hay pong."""
        if not self._pongs:
            return None
        return min(self._pongs)[1]

    def to_local_time(self, t_pc: float) -> float:
        off = self.clock_offset
        return t_pc - off if off is not None else t_pc

    def status(self) -> dict:
        f = self.latest
        now = time.time()
        arr = list(self._arrivals)
        fps = (len(arr) - 1) / (arr[-1] - arr[0]) if len(arr) > 2 and arr[-1] > arr[0] else 0.0
        lat = sorted(self._lat)
        rtt = min(self._pongs)[0] if self._pongs else None
        return {"url": self.url, "connected": self.connected, "messages": self.messages, "reconnects": self.reconnects,
                "fps": round(fps, 1), "age_s": round(now - f.received, 3) if f else None,
                "hands": len(f.hands) if f else 0, "detector": f.detector if f else None,
                "latency_ms": round(1000 * lat[len(lat) // 2], 1) if lat else None,
                "rtt_ms": round(1000 * rtt, 1) if rtt is not None else None,
                "clock_offset_ms": round(1000 * self.clock_offset, 1) if self.clock_offset is not None else None,
                "error": self.error}

    # ------------------------------------------------------------------ hilo
    def _handle(self, raw: str | bytes) -> None:
        now = time.time()
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "pong":
            t0 = msg.get("t0")
            if isinstance(t0, (int, float)) and isinstance(msg.get("t"), (int, float)):
                rtt = now - t0
                self._pongs.append((rtt, float(msg["t"]) - (t0 + now) / 2))
            return
        if "hands" not in msg:
            return
        frame = HandsFrame.from_json(msg, received=now)
        self.messages += 1
        self._arrivals.append(now)
        if self.clock_offset is not None and frame.t:
            self._lat.append(now - self.to_local_time(frame.t))
        with self._cond:
            self.latest = frame
            self._cond.notify_all()
        if self.on_frame is not None:
            try:
                self.on_frame(frame)
            except Exception as e:  # noqa: BLE001  un callback roto no corta la conexión
                self.error = f"on_frame: {e!r}"

    def _run(self) -> None:
        delay = self.backoff[0]
        while not self._stop.is_set():
            try:
                with _connect(self.url, self.open_timeout) as ws:
                    self._ws = ws
                    self.connected = True
                    self.error = None
                    self._connections += 1
                    if self._connections > 1:
                        self.reconnects += 1
                    delay = self.backoff[0]
                    next_ping = 0.0
                    while not self._stop.is_set():
                        now = time.time()
                        if self.ping_every and now >= next_ping:
                            ws.send(json.dumps({"type": "ping", "t0": now}))
                            next_ping = now + self.ping_every
                        try:
                            raw = ws.recv(timeout=0.25)
                        except TimeoutError:
                            continue
                        self._handle(raw)
            except Exception as e:  # noqa: BLE001  red caída, PC apagado, cierre del servidor: se reintenta
                self.error = f"{type(e).__name__}: {e}"
            finally:
                self.connected = False
                self._ws = None
            if self._stop.wait(delay):
                break
            delay = min(self.backoff[1], delay * 2)

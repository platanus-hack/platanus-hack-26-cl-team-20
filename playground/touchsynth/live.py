"""Lazo en vivo de la mesa táctil: cámaras → fusión → runtime del playground (que es el que proyecta).

    python -m playground.touchsynth live --modes croc,draw --ver          # runtime en http://127.0.0.1:8790

Este proceso **no abre ventanas en el proyector**: el runtime (`python -m playground serve --projector …`) es el
único dueño de la salida. Aquí:

- **efectos**: los pedidos en `--modes` se spawnean en el runtime por su API (y se matan al salir, salvo `--keep`);
  `--ver` agrega `touch_marks` (anillo en cada punta, verde = toca) con la luz base `--luz-base`.
- **Osmo** (hilo propio): cuadro 4K reducido y rotado → vista del proyector (mapa del escaneo) → `OsmoHands`. El
  aporte del proyector se mide al arrancar mostrando 4 tableros con el comando `overlay` del runtime (~5 s, sin
  manos en la mesa); lo que el runtime proyecta después se baja de `/api/frame.png` (~8 Hz) para el modelo.
- **Brio** (hilo propio, si `--brio-ws` responde): manos de MediaPipe del PC → `BrioContact` → contacto.
- **fusión**: los toques van al comando `touch` (puente HTTP de `playground.hands`) y las manos a `hands` (linterna,
  marcas). El estado queda en el runtime: `touchsynth status` / `pg_touch list` → `en_vivo`.

Ctrl+C: mata los efectos propios, apaga las notas y cierra las cámaras.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from .fusion import BrioContact, Fusion, FusionConfig
from .osmo_hands import OsmoConfig, OsmoHands, PovMapper, ProjectionModel

MODES = {"croc": "croc_synth", "cocodrilo": "croc_synth", "draw": "touch_draw", "dibujo": "touch_draw",
         "marimba": "marimba", "linterna": "linterna", "vivo": "croc_alive", "alive": "croc_alive"}


def _p(msg: str) -> None:
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


class RuntimeClient:
    """La API HTTP del runtime del playground."""

    def __init__(self, url: str = "http://127.0.0.1:8790", timeout: float = 2.0):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def cmd(self, cmd: str, timeout: float | None = None, **args):
        body = json.dumps({"cmd": cmd, "args": args}).encode()
        req = urllib.request.Request(f"{self.url}/api/command", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError as e:
            d = json.loads(e.read() or b"{}")
        if not d.get("ok"):
            raise RuntimeError(f"{cmd}: {d.get('error')}")
        return d.get("result")

    def alive(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/api/schema", timeout=self.timeout) as r:
                return r.status == 200
        except OSError:
            return False

    def frame(self) -> np.ndarray | None:
        try:
            with urllib.request.urlopen(f"{self.url}/api/frame.png", timeout=self.timeout) as r:
                return cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_COLOR)
        except OSError:
            return None


class CamReader:
    """Lee la cámara en un hilo y guarda el último cuadro (con número), para no frenar a nadie."""

    def __init__(self, cam):
        self.cam = cam
        self.frame: np.ndarray | None = None
        self.t = 0.0
        self.seq = 0
        self.fps = 0.0
        self.error: str | None = None
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._th = threading.Thread(target=self._run, name="cam-reader", daemon=True)

    def start(self) -> "CamReader":
        self._th.start()
        return self

    def _run(self) -> None:
        n, t0 = 0, time.time()
        while not self._stop.is_set():
            try:
                f = self.cam.read()
            except Exception as e:  # noqa: BLE001  cámara ocupada o desenchufada: reintenta
                self.error = f"{type(e).__name__}: {e}"
                time.sleep(0.5)
                continue
            with self._cond:
                self.frame, self.t = f, time.time()
                self.seq += 1
                self._cond.notify_all()
            n += 1
            if time.time() - t0 >= 2:
                self.fps, n, t0 = n / (time.time() - t0), 0, time.time()

    def wait_new(self, after: int, timeout: float = 1.0):
        with self._cond:
            self._cond.wait_for(lambda: self.seq > after or self._stop.is_set(), timeout)
            return (self.frame, self.t, self.seq) if self.seq > after else (None, 0.0, after)

    def close(self) -> None:
        self._stop.set()
        self._th.join(timeout=2)
        try:
            self.cam.close()
        except Exception:  # noqa: BLE001
            pass


def checkerboards(size=(1920, 1080), block: int = 48) -> list[np.ndarray]:
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    out = []
    for off in (0, block // 2):
        c = ((((xx + off) // block) + ((yy + off) // block)) % 2).astype(np.uint8) * 255
        out += [c, 255 - c]
    return out


class Live:
    def __init__(self, a, client: RuntimeClient | None = None):
        self.a = a
        self.scan = Path(a.scan)
        self.rt = client or RuntimeClient(a.runtime)
        self.fusion = Fusion(FusionConfig(osmo_contact=a.osmo_contact), emit=self._emit_touch)
        self.flock = threading.Lock()
        self.stop = threading.Event()
        self.bridge = None
        self.osmo: CamReader | None = None
        self.detector: OsmoHands | None = None
        self.brio = None
        self.brio_client = None
        self.osmo_tips = []
        self.osmo_fps = 0.0
        self.spawned: list[str] = []
        self.threads: list[threading.Thread] = []
        self._calibrating = False
        self._t_hands = 0.0

    # ------------------------------------------------------------------ salida de la fusión
    def _emit_touch(self, ev: dict) -> None:
        if self.bridge is not None:
            self.bridge.send(ev)
        if self.a.verbose:
            _p(f"{ev['state']:>4} {ev['id']} ({ev['x']:.0f}, {ev['y']:.0f}) {ev['source']}/{ev['trigger']}"
               + (f" v={ev['velocity']:.2f}" if "velocity" in ev else ""))

    def _step(self, now: float) -> None:
        with self.flock:
            self.fusion.step(now)
            hands = self.fusion.hands(now)
        if now - self._t_hands >= 0.07:                      # manos a ~15 Hz (linterna, marcas)
            self._t_hands = now
            try:
                self.rt.cmd("hands", timeout=0.3, hands=hands, source="fusion")
            except Exception:  # noqa: BLE001  runtime lento o caído: los toques siguen por el puente
                pass

    # ------------------------------------------------------------------ arranque
    def start(self) -> None:
        from ..hands.bridge import TouchBridge
        a = self.a
        if not self.rt.alive():
            raise SystemExit(f"el runtime no responde en {self.rt.url}: levántalo con "
                             "`python -m playground serve --projector …` (él es el dueño del proyector)")
        self.bridge = TouchBridge(self.rt.url, source="fusion")
        for m in [m.strip() for m in a.modes.split(",") if m.strip()]:
            eff = MODES.get(m, m)
            params = {"scan": str(self.scan)} if eff in ("croc_synth", "marimba", "linterna", "croc_alive") else {}
            self.spawned.append(self.rt.cmd("spawn", effect=eff, params=params)["id"])
        if a.ver or a.luz_base:
            self.spawned.append(self.rt.cmd("spawn", effect="touch_marks",
                                            params={"luz_base": a.luz_base, "rings": bool(a.ver)})["id"])
        _p(f"efectos en el runtime: {self.spawned}")
        if a.osmo:
            self._start_osmo()
        if a.brio_ws and a.brio_ws != "none":
            self._start_brio()
        for target, name in ((self._watchdog, "fusion-watchdog"), (self._frames_loop, "runtime-frames")):
            th = threading.Thread(target=target, name=name, daemon=True)
            th.start()
            self.threads.append(th)

    def _start_osmo(self) -> None:
        from slscan.capture import Camera
        a = self.a
        mapper = PovMapper.load(self.scan, scale=a.osmo_scale)
        cam = None
        for k in range(a.cam_retries):
            try:
                cam = Camera(a.camera, size="3840x2160", rotate=a.rotate, downscale=2)
                break
            except Exception as e:  # noqa: BLE001  otra unidad puede tener la Osmo: espera y reintenta
                _p(f"Osmo no abre ({e}); reintento {k + 1}/{a.cam_retries} en 5 s")
                time.sleep(5)
        if cam is None:
            _p("sin Osmo: sigo en modo degradado (solo Brio o solo API)")
            return
        self.osmo = CamReader(cam).start()
        self.detector = OsmoHands(mapper, OsmoConfig(model=a.model))
        if a.model in ("proyeccion", "hibrido"):
            self._measure_model()
        th = threading.Thread(target=self._osmo_loop, name="osmo", daemon=True)
        th.start()
        self.threads.append(th)

    def _measure_model(self) -> None:
        """4 tableros con el comando overlay del runtime → aporte del proyector por píxel (A, B)."""
        _p("midiendo la proyección con la Osmo (4 tableros vía overlay, ~5 s): no pongas las manos")
        self._calibrating = True
        tmp = Path(tempfile.mkdtemp(prefix="touchsynth_tableros_"))
        boards = []
        for i, b in enumerate(checkerboards()):
            path = tmp / f"tablero_{i}.png"
            cv2.imwrite(str(path), b)
            boards.append(path)
        shown = {"i": 0}
        last = self.osmo.seq

        def show(img):                                   # ProjectionModel.capture pide mostrar el tablero i
            path = boards[shown["i"] % len(boards)]
            shown["i"] += 1
            self.rt.cmd("overlay", path=str(path), seconds=self.a.settle + 2.0)

        def read_pov():
            nonlocal last
            f = None
            for _ in range(3):                               # cuadros nuevos (la Osmo trae buffer)
                f, _, last = self.osmo.wait_new(last, 2.0)
            if f is None:
                raise RuntimeError("la Osmo no entrega cuadros")
            return self.detector.mapper.to_pov(f)

        try:
            self.detector.model = ProjectionModel.capture(show, read_pov, settle=self.a.settle)
            self.detector.cfg.model = self.a.model
            _p(f"modelo {self.a.model} listo: luz del proyector media {float(self.detector.model.A.mean()):.0f} niveles")
        except Exception as e:  # noqa: BLE001
            _p(f"no pude medir la proyección ({e}): uso modelo 'fondo'")
            self.detector.cfg.model = "fondo"
        finally:
            try:
                self.rt.cmd("overlay", off=True)
            except Exception:  # noqa: BLE001
                pass
            for pth in boards:
                pth.unlink(missing_ok=True)
            tmp.rmdir()
            self.detector.stats["gain"] = 1.0
            self.detector.pause(2.5)                          # tableros en cola y auto-exposición: espera
            self._calibrating = False

    def _frames_loop(self) -> None:
        """Lo que proyecta el runtime, para el modelo de la Osmo (~8 Hz)."""
        while not self.stop.is_set():
            t0 = time.time()
            if self.detector is not None and not self._calibrating:
                f = self.rt.frame()
                if f is not None:
                    self.detector.push_projected(f, t=t0)
            time.sleep(max(0.0, 0.12 - (time.time() - t0)))

    def _osmo_loop(self) -> None:
        last, n, t0 = 0, 0, time.time()
        while not self.stop.is_set():
            f, t_cap, last = self.osmo.wait_new(last, 1.0)
            if f is None or self._calibrating:
                continue
            tips = self.detector.detect_frame(f, t_cap)
            now = time.time()
            if self.a.debug_dir and now - getattr(self, "_t_dbg", 0) >= self.a.debug_every:
                self._t_dbg = now
                self._dump_debug(tips)
            with self.flock:
                self.fusion.update_osmo(tips, now)
            self.osmo_tips = tips
            self._step(now)
            n += 1
            if now - t0 >= 2:
                self.osmo_fps, n, t0 = n / (now - t0), 0, now

    def _dump_debug(self, tips) -> None:
        """Depuración PRIVADA (cuadro de la Osmo en la vista del proyector + máscaras): solo con --debug-dir, que
        debe quedar en reports/ (fuera de git) y borrarse después de mirarlo."""
        d = Path(self.a.debug_dir)
        d.mkdir(parents=True, exist_ok=True)
        pov = getattr(self.detector, "last_pov", None)
        dbg = self.detector.debug
        if pov is None or not dbg:
            return
        over = pov.copy()
        over[dbg["shadow"]] = (over[dbg["shadow"]] * 0.4 + np.array([255, 0, 0]) * 0.6).astype(np.uint8)
        over[dbg["hand"]] = (over[dbg["hand"]] * 0.4 + np.array([0, 255, 0]) * 0.6).astype(np.uint8)
        s = self.detector.scale
        for tp in tips:
            cv2.circle(over, (int(tp.x / s), int(tp.y / s)), 8, (0, 0, 255), 2)
        k = getattr(self, "_n_dbg", 0) % 5
        self._n_dbg = k + 1
        cv2.imwrite(str(d / f"osmo_pov_{k}.png"), over)

    def _start_brio(self) -> None:
        from ..hands.calib import Calibration
        from ..hands.client import HandsClient
        a = self.a
        cal = None
        if a.brio_calib and Path(a.brio_calib).exists():
            try:
                cal = Calibration.load(a.brio_calib)
            except Exception as e:  # noqa: BLE001
                _p(f"calibración de la Brio ilegible ({e}): sin posición de la Brio")
        self.brio = BrioContact(cal, mode=a.brio_mode)
        self.brio_client = HandsClient(a.brio_ws).start()
        _p(f"Brio: {a.brio_ws} · calibración {'sí' if cal else 'no (solo contacto, asociación 1 a 1)'}")
        th = threading.Thread(target=self._brio_loop, name="brio", daemon=True)
        th.start()
        self.threads.append(th)

    def _brio_loop(self) -> None:
        last = None
        while not self.stop.is_set():
            f = self.brio_client.wait(timeout=0.5, after_seq=last)
            if f is None:
                continue
            last = f.seq
            now = time.time()
            obs = self.brio.observe(f.hands, now)
            with self.flock:
                self.fusion.update_brio(obs, now, self.brio.calibrated)
            self._step(now)

    def _watchdog(self) -> None:
        """Paso lento (10 Hz) para soltar toques si una cámara se calla; estado al runtime cada 1 s."""
        t_pub = 0.0
        while not self.stop.is_set():
            now = time.time()
            self._step(now)
            if now - t_pub >= 1.0:
                t_pub = now
                try:
                    r = self.rt.cmd("touchsynth", timeout=0.5, action="en_vivo", status=self.status(now))
                    if r.get("recalibrar") and self.detector is not None:
                        threading.Thread(target=self._measure_model, daemon=True).start()
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.1)

    # ------------------------------------------------------------------ vida
    def run(self) -> int:
        a = self.a
        self.start()
        t_end = None if not a.seconds else time.time() + a.seconds
        t_status = 0.0
        try:
            while not self.stop.is_set() and (t_end is None or time.time() < t_end):
                if time.time() - t_status >= a.status_every:
                    t_status = time.time()
                    _p(json.dumps(self.status_short(), ensure_ascii=False))
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.close()
        return 0

    def close(self) -> None:
        self.stop.set()
        if not self.a.keep:
            for iid in self.spawned:
                try:
                    self.rt.cmd("kill", id=iid)
                except Exception:  # noqa: BLE001
                    pass
        try:
            self.rt.cmd("touchsynth", action="panic")
        except Exception:  # noqa: BLE001
            pass
        if self.bridge is not None:
            self.bridge.close()
        if self.osmo is not None:
            self.osmo.close()
        if self.brio_client is not None:
            self.brio_client.close()

    # ------------------------------------------------------------------ estado
    def status(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self.flock:
            fu = self.fusion.status(now)
        return {"fusion": fu,
                "osmo": None if self.detector is None else {**self.detector.status(), "fps": round(self.osmo_fps, 1),
                                                             "camara_fps": round(self.osmo.fps, 1) if self.osmo else 0,
                                                             "error": self.osmo.error if self.osmo else None},
                "brio": None if self.brio_client is None else self.brio_client.status(),
                "efectos": self.spawned, "puente": self.bridge.status() if self.bridge else None}

    def status_short(self) -> dict:
        s = self.status()
        o = s["osmo"] or {}
        return {"modo": s["fusion"]["modo"], "detalle": s["fusion"]["detalle"], "osmo_fps": o.get("fps"),
                "osmo_ms": o.get("ms"), "puntas": s["fusion"]["osmo"]["puntas"], "tocando": s["fusion"]["tocando"],
                "eventos": s["fusion"]["eventos"]}

"""Runtime del playground: escena viva, galería de efectos con hot reload, lienzo por proyector y API.

- Descubre efectos en `playground/effects/*.py`; si un archivo cambia (mtime), recarga el módulo y
  reconstruye las instancias vivas de esa clase con los mismos parámetros. Un efecto roto no tumba el
  runtime: queda marcado con su error hasta el siguiente guardado.
- Tick a `fps` Hz: anima la escena (demo o tracking), `update` y `draw` de cada efecto en el lienzo.
- API HTTP (FastAPI): GET /api/state · POST /api/command {cmd, args} · GET /api/events (SSE) ·
  GET /api/schema · GET /api/frame.png · GET /api/effects · GET /api/scene3d + /web (visor 3D, playground/web).
  El MCP, la UI web y la secretaria hablan
  por aquí. Comandos: list_effects, spawn, kill, set_param, scene, find, request, requests, set_status,
  reload, snapshot, beat, audio.
- Audio reactivo: el cliente `python -m playground.audio run` publica ~50 veces por segundo el comando `audio`
  ({features: {sub, bass, mid, high, rms, beat, onsets, bpm, tc, ch: [{…por canal…}]}}). El runtime lo deja en
  `scene.audio` en cada tick (si no llega nada en 0,5 s, cae a cero) y los efectos lo leen de ahí, sin saber de dónde
  viene. `bind` acepta `audio/<rasgo>` como control (`audio/bass`, `audio/ch2.rms`, `audio/bpm`): cualquier parámetro
  de cualquier efecto sigue al audio.
  reload, snapshot, beat, touch, touches, hands, touchsynth (ver SCHEMA).
- Mesa táctil: `touch` recibe toques down/move/up en px del proyector (de `playground.hands`, de la fusión
  Osmo+Brio de `playground.touchsynth` o del MCP `pg_touch`), los deja en `touchsynth.registry.touch_state(scene)`
  (con `seq`, para que los efectos los consuman sin perder ninguno) y los publica en /api/events.
  reload, snapshot, beat, devices, controls, connect_devices, connect_keyboard, simulate, bind, unbind, pianohero.
- Entradas: cada evento de un dispositivo (MIDI del OHM, teclas del Korg EK-50 por el puente del PC) se publica como
  evento `control` y, con su hora de llegada, se entrega a los efectos en `scene.inputs` del cuadro siguiente.
"""

from __future__ import annotations

import collections
import importlib
import inspect
import json
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

import cv2
import numpy as np

from .effects.base import Effect, Canvas
from .scene import Scene, demo_scene, animate_demo
from .requests_queue import RequestQueue
from .devices import DeviceRegistry, FakeSource, SmartDeckSource, DirectMidiSource, ohm_device, ohm_scene_object
from .devices.midi import Binding
from .audio.features import (AUDIO_KEYS, AUDIO_STALE_S, CH_KEYS, LEVELS, audio_level, audio_value, audio_zero,
                             valid_key)
from .devices.keyboard import KeyboardSource, ek50_device, DEFAULT_BRIDGE, BRIDGE_PORT

EFFECTS_DIR = Path(__file__).parent / "effects"
PROJECT_DIR = Path(__file__).parent.parent
TOUCH_STATES = ("down", "move", "up")


class EffectRegistry:
    def __init__(self, folder: Path = EFFECTS_DIR):
        self.folder = folder
        self.classes: dict[str, type[Effect]] = {}
        self.errors: dict[str, str] = {}
        self.mtimes: dict[str, float] = {}
        self.modules: dict[str, object] = {}

    def _files(self):
        return [p for p in self.folder.glob("*.py") if p.stem not in ("__init__", "base")]

    def load(self, path: Path) -> list[str]:
        """(Re)carga un archivo de efectos. Devuelve los nombres de efectos que define."""
        modname = f"playground.effects.{path.stem}"
        try:
            if modname in sys.modules:
                mod = importlib.reload(sys.modules[modname])
            else:
                mod = importlib.import_module(modname)
            self.errors.pop(path.stem, None)
        except Exception:  # noqa: BLE001  (un plugin roto no tumba el runtime)
            self.errors[path.stem] = traceback.format_exc(limit=3)
            return []
        self.modules[path.stem] = mod
        names = []
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            if issubclass(cls, Effect) and cls is not Effect and cls.__module__ == modname:
                self.classes[cls.name] = cls
                names.append(cls.name)
        try:
            self.mtimes[path.stem] = path.stat().st_mtime
        except FileNotFoundError:
            # Git/editors may replace the file after import; retry next frame.
            self.mtimes.pop(path.stem, None)
        return names

    def load_all(self) -> None:
        self.mtimes["base"] = (self.folder / "base.py").stat().st_mtime
        for p in self._files():
            self.load(p)

    def changed(self) -> list[Path]:
        """Archivos de efectos que cambiaron. Si cambió base.py, se recarga primero y todos los efectos con él."""
        base = self.folder / "base.py"
        try:
            base_mtime = base.stat().st_mtime
        except FileNotFoundError:
            return []  # atomic replacement in progress; keep current instances
        if base_mtime != self.mtimes.get("base"):
            if "base" in self.mtimes:            # no en la primera carga
                importlib.reload(sys.modules["playground.effects.base"])
                for p in self._files():
                    self.mtimes.pop(p.stem, None)
            self.mtimes["base"] = base_mtime
        out = []
        for p in self._files():
            try:
                changed = p.stat().st_mtime != self.mtimes.get(p.stem)
            except FileNotFoundError:
                continue  # file disappeared between glob and stat during save/merge
            if changed:
                out.append(p)
        return out

    def gallery(self) -> list[dict]:
        return [c.meta() for c in self.classes.values()]


class Runtime:
    def __init__(self, scene: Scene | None = None, canvas_size=(1920, 1080), fps: float = 30.0, demo: bool = True,
                 requests_dir: Path | None = None):
        self.scene = scene or demo_scene()
        self.demo = demo
        self.fps = fps
        self.registry = EffectRegistry()
        self.registry.load_all()
        self.instances: dict[str, Effect] = {}
        self._n = 0
        self.canvas_size = canvas_size
        self.px_per_mm = min(canvas_size[0] / self.scene.table_mm[0], canvas_size[1] / self.scene.table_mm[1])
        self.frame = np.zeros((canvas_size[1], canvas_size[0], 3), np.uint8)
        self.overlay = None; self.overlay_until = 0.0     # imagen de calibración que tapa los efectos unos segundos
        self.registered_scene = None
        self.metric_mode = False
        self.metric_frame = None
        self.metric_until = 0.0
        # corrimiento global de la SALIDA (px): si el proyector se movió desde el escaneo, todo lo dibujado se corre
        # entero antes de salir (lo midió el agente de teclas: (-1, +20) el 16-oct); persiste en <scan>/salida.json
        self.output_offset = (0.0, 0.0)
        sd = getattr(self.scene, "scan_dir", None)
        if self.scene.coordinate_space != "world_mm" and sd and (Path(sd) / "salida.json").exists():
            try:
                d = json.loads((Path(sd) / "salida.json").read_text()); self.output_offset = (float(d.get("dx", 0)), float(d.get("dy", 0)))
            except Exception:  # noqa: BLE001
                pass
        self.events: list[dict] = []
        self.subscribers: list[queue.Queue] = []
        self.requests = RequestQueue(requests_dir or PROJECT_DIR / "requests")
        self.lock = threading.RLock()
        self.running = False
        self.stats = {"ticks": 0, "fps": 0.0, "reloads": 0, "started": time.time()}
        self.devices = DeviceRegistry()
        self._inputs: collections.deque = collections.deque(maxlen=4000)
        self.devices.on_event = self._on_device_event
        self.projector: dict | None = None       # lo fija quien muestra self.frame en un proyector (p.ej. pianohero)
        self.devices.on_bind = self._apply_binding
        self._setup_devices()
        self.audio_in: dict = {"features": {}, "meta": {}, "recv_t": 0.0, "hold": AUDIO_STALE_S, "lag_ms": None}
        self.audio_binds: list[Binding] = []
        self.scene.audio = audio_zero()

    # ------------------------------------------------------------------ dispositivos
    def _setup_devices(self) -> None:
        """OHM RGB como dispositivo y objeto de escena; fuente según quién tenga el puerto (ver PLAN › 002)."""
        dev = self.devices.add(ohm_device())
        if self.scene.coordinate_space != "world_mm":
            self.scene.objects["ohm"] = ohm_scene_object(dev)
        self.devices.add(ek50_device())           # Korg EK-50: se conecta con connect_keyboard (puente del PC o local)

    def _on_device_event(self, ev: dict) -> None:
        self._inputs.append({**ev, "t": ev.get("t_event") or time.time()})
        self.emit("control", **{("ctype" if k == "type" else k): v for k, v in ev.items() if k != "t_event"})

    def _drain_inputs(self) -> list[dict]:
        out = []
        while True:
            try:
                out.append(self._inputs.popleft())
            except IndexError:
                return out

    def connect_devices(self, prefer: str = "auto") -> dict:
        """Conecta la fuente de eventos del OHM: smartdeck (WS 8090) si corre, si no MIDI directo; fake = simulado."""
        with self.lock:
            self._detach("ohm")
            if prefer == "fake":
                self.devices.attach(FakeSource("ohm")); return self.devices.to_json()
            sd = SmartDeckSource("ohm")
            if prefer in ("auto", "smartdeck") and sd.available():
                self.devices.attach(sd)
            elif prefer in ("auto", "direct"):
                self.devices.attach(DirectMidiSource("ohm", self.devices.devices["ohm"].port_match))
            return self.devices.to_json()

    def _detach(self, device_id: str) -> None:
        """Para y saca las fuentes de un dispositivo (las de los demás siguen)."""
        for src in [x for x in self.devices.sources if getattr(x, "device_id", None) == device_id]:
            try:
                src.stop()
            except Exception:  # noqa: BLE001
                pass
            self.devices.sources.remove(src)

    def connect_keyboard(self, prefer: str = "auto", bridge: str | None = None, listen_port: int = BRIDGE_PORT) -> dict:
        """Fuente del Korg EK-50: auto (MIDI local si está en el Mac, si no el puente UDP del PC) | direct | bridge | fake."""
        with self.lock:
            self._detach("ek50")
            if prefer == "fake":
                self.devices.attach(FakeSource("ek50"))
            else:
                self.devices.attach(KeyboardSource("ek50", prefer, bridge or DEFAULT_BRIDGE, listen_port))
            src = next(x for x in self.devices.sources if getattr(x, "device_id", None) == "ek50")
            return {"device": "ek50", "status": self.devices.devices["ek50"].status, "source": src.describe()}

    def keyboard_connected(self) -> bool:
        return any(getattr(x, "device_id", None) == "ek50" for x in self.devices.sources)

    def pianohero(self, a: dict) -> dict:
        """Piano Hero: arranca/controla la instancia del efecto `pianohero` (una sola). Ver SCHEMA["pianohero"]."""
        action = a.get("action", "state")
        with self.lock:
            inst = next((i for i in self.instances.values() if i.name == "pianohero"), None)
            if action == "songs":
                from .pianohero.songs import list_songs, LEVELS
                return {"songs": list_songs(), "levels": {n: L.name for n, L in LEVELS.items()}}
            if action == "stop":
                if inst is not None:
                    st = inst.status(); self.kill(inst.instance_id)
                    return {"stopped": True, "last": st}
                return {"stopped": False, "running": False}
            if inst is None:
                if action == "state":
                    return {"running": False, "hint": "pg_pianohero start (level 1-3, song) para jugar"}
                params = {k: a[k] for k in ("level", "song", "practice", "keys", "offset_ms", "speed", "lane_px", "hud", "demo")
                          if a.get(k) not in (None, "")}
                self.spawn("pianohero", None, params)
                inst = next(i for i in self.instances.values() if i.name == "pianohero")
                if action in ("level", "song", "practice"):
                    action = "start"
            if not self.keyboard_connected() and a.get("prefer") != "none":
                self.connect_keyboard(a.get("prefer") or "auto", a.get("bridge"))
            out = inst.control(action, a)
            out["id"] = inst.instance_id
            out["keyboard"] = next((x.describe() for x in self.devices.sources if getattr(x, "device_id", None) == "ek50"), None)
            out["projector"] = self.projector or "este runtime no está proyectando: corre `python -m playground.pianohero` (proyecta y sirve la API)"
            return out

    def _apply_binding(self, b, value: float) -> None:
        kind, _, rest = b.target.partition(":")
        if kind == "effect":
            iid, _, key = rest.partition(":")
            inst = self.instances.get(iid)
            if inst is not None:
                inst.set(key, value)
        elif kind == "beat" and rest == "bpm":
            self.scene.beat["bpm"] = value
        elif kind == "beat" and rest == "playing":
            self.scene.beat["playing"] = value >= 0.5

    # ------------------------------------------------------------------ audio
    def set_audio(self, features: dict | None = None, meta: dict | None = None, hold: float | None = None,
                  stop: bool = False) -> dict:
        """Lo llama el cliente de audio (~50 Hz) o una simulación. No toma el lock del tick: reemplaza un dict."""
        now = time.time()
        if stop:
            self.audio_in = {**self.audio_in, "recv_t": 0.0, "stopped_t": now}
            return {"ok": True}
        prev = self.audio_in
        f = dict(prev["features"]) if hold is not None else {}
        for k in AUDIO_KEYS:
            if k in (features or {}):
                f[k] = int(features[k]) if k == "onsets" else float(features[k])
        if isinstance((features or {}).get("ch"), list):          # por canal de entrada
            f["ch"] = [{k: (int(c[k]) if k == "onsets" else float(c[k])) for k in CH_KEYS if k in c}
                       for c in features["ch"] if isinstance(c, dict)]
        if "onsets" not in (features or {}) and float((features or {}).get("beat", 0.0)) >= 0.999:
            f["onsets"] = int(prev["features"].get("onsets", self.scene.audio.get("onsets", 0))) + 1   # simulación
        lag = prev.get("lag_ms")
        if features and "tc" in features:
            d = (now - float(features["tc"])) * 1000
            lag = d if lag is None else 0.9 * lag + 0.1 * d
        self.audio_in = {"features": f, "meta": {**prev["meta"], **(meta or {})}, "recv_t": now, "lag_ms": lag,
                         "hold": AUDIO_STALE_S if hold is None else float(hold), "n": prev.get("n", 0) + 1}
        return {"ok": True}

    def _tick_audio(self) -> None:
        a = self.audio_in
        cur = self.scene.audio
        age = time.time() - a["recv_t"]
        if age <= a["hold"]:
            self.scene.audio = {**audio_zero(), **cur, **a["features"], "live": True}
            if age > 0.1:                            # valores retenidos (simulación): el golpe igual se apaga
                self.scene.audio["beat"] = round(float(cur.get("beat", 0.0)) * 0.75, 4)
                prev_ch = cur.get("ch") or []
                self.scene.audio["ch"] = [{**c, "beat": round(float((prev_ch[i] if i < len(prev_ch) else c).get("beat", 0.0)) * 0.75, 4)}
                                          for i, c in enumerate(self.scene.audio.get("ch") or [])]
        else:                                        # sin datos: cae a cero suave (sin cortes), bpm se queda
            decay = lambda d: {**d, **{k: round(float(d.get(k, 0.0)) * 0.8, 4) for k in LEVELS}}
            self.scene.audio = {**decay(cur), "ch": [decay(c) for c in cur.get("ch") or []], "live": False}
        for b in self.audio_binds:
            key = b.control.partition("/")[2]
            feat = key.rpartition(".")[2]
            v = audio_value(self.scene.audio, key) if feat in ("bpm", "onsets") else audio_level(self.scene.audio, key)
            if feat == "bpm" and v <= 0:
                continue
            self._apply_binding(b, b.map(v))

    def audio_state(self) -> dict:
        a = self.audio_in
        return {"live": bool(self.scene.audio.get("live")), "features": self.scene.audio,
                "age_s": round(time.time() - a["recv_t"], 3) if a["recv_t"] else None,
                "lag_ms": round(a["lag_ms"], 1) if a.get("lag_ms") is not None else None, "packets": a.get("n", 0),
                "meta": a["meta"], "binds": [{"control": b.control, "target": b.target, "lo": b.lo, "hi": b.hi}
                                             for b in self.audio_binds]}

    # ------------------------------------------------------------------ eventos
    def emit(self, type_: str, **data) -> None:
        ev = {"type": type_, "t": round(time.time(), 3), **data}
        self.events.append(ev)
        self.events = self.events[-500:]
        for q in list(self.subscribers):
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass

    # ------------------------------------------------------------------ efectos
    def spawn(self, effect: str, target: str | None = None, params: dict | None = None) -> dict:
        with self.lock:
            if self.scene.coordinate_space == "world_mm":
                raise ValueError("Objetos métricos requieren rutas UV/lighting del renderer calibrado P4")
            cls = self.registry.classes.get(effect)
            if cls is None:
                raise KeyError(f"efecto desconocido: {effect}. Hay: {sorted(self.registry.classes)}")
            obj = None
            if target:
                obj = self.scene.find(target)
                if obj is None:
                    raise KeyError(f"objeto desconocido: {target}. Hay: {sorted(self.scene.objects)}")
                if "*" not in cls.targets and obj.kind not in cls.targets:
                    raise ValueError(f"{effect} no aplica a {obj.kind}; sirve para {cls.targets}")
            self._n += 1
            iid = f"e{self._n}"
            inst = cls(obj, params, iid)
            self.instances[iid] = inst
            self.emit("spawn", id=iid, effect=effect, target=obj.id if obj else None)
            return inst.state()

    def kill(self, instance_id: str | None = None) -> list[str]:
        with self.lock:
            ids = [instance_id] if instance_id else list(self.instances)
            for i in ids:
                if i in self.instances:
                    self.instances[i].kill()
                    del self.instances[i]
                    self.emit("kill", id=i)
            return ids

    def layered(self) -> list[tuple[str, Effect]]:
        """Instancias en orden de dibujo: param `capa` (número, menor = más abajo, 0 por defecto) y luego orden de spawn."""
        def key(kv):
            try:
                capa = float(kv[1].p.get("capa", 0) or 0)
            except (TypeError, ValueError):
                capa = 0.0
            n = kv[0][1:]
            return (capa, int(n) if n.isdigit() else 0)
        return sorted(self.instances.items(), key=key)

    def set_param(self, instance_id: str, key: str, value) -> dict:
        with self.lock:
            inst = self.instances[instance_id]
            inst.set(key, value)
            self.emit("param", id=instance_id, key=key, value=value)
            return inst.state()

    def hot_reload(self) -> list[str]:
        """Recarga los archivos de efectos que cambiaron y reconstruye sus instancias vivas."""
        reloaded = []
        with self.lock:
            changed = self.registry.changed()
            for path in changed:
                names = self.registry.load(path)
                reloaded += names
                for iid, inst in list(self.instances.items()):
                    if inst.name in names:
                        cls = self.registry.classes[inst.name]
                        self.instances[iid] = cls(inst.target, inst.p, iid)
                        self.instances[iid].t = inst.t
                if path.stem in self.registry.errors:
                    self.emit("reload_error", file=path.name, error=self.registry.errors[path.stem].splitlines()[-1])
                else:
                    self.stats["reloads"] += 1
                    self.emit("reload", file=path.name, effects=names)
        return reloaded

    # ------------------------------------------------------------------ tick
    def tick(self, dt: float) -> np.ndarray:
        self.hot_reload()
        with self.lock:
            if self.metric_mode:
                # Native registered pixels bypass every legacy canvas/offset/effect.
                self.frame = (self.metric_frame.copy() if self.metric_frame is not None
                              and time.monotonic() < self.metric_until else
                              np.zeros((self.canvas_size[1], self.canvas_size[0], 3), np.uint8))
                self.stats['ticks'] += 1
                return self.frame
            if self.demo:
                animate_demo(self.scene, dt)
            else:
                self.scene.t += dt
            self.scene.inputs = self._drain_inputs()
            self.scene.controls = {d.id: {cid: {"value": c.value, "state": c.state, "t": c.last_event_t}
                                          for cid, c in d.controls.items() if c.last_event_t > 0}
                                   for d in self.devices.devices.values()}
            self._tick_audio()
            # escena del escaneo: 1 unidad = 1 px del proyector, y hacia abajo (sin flip); demo: mm con y hacia arriba
            canvas = Canvas(np.zeros((self.canvas_size[1], self.canvas_size[0], 3), np.uint8), self.px_per_mm,
                            flip_y=not getattr(self.scene, "projector_px", False))
            for iid, inst in ([] if self.scene.coordinate_space == "world_mm" else self.layered()):
                try:
                    inst.update(dt, self.scene)
                    inst.draw(canvas)
                except Exception:  # noqa: BLE001
                    err = traceback.format_exc(limit=2).splitlines()[-1]
                    self.emit("effect_error", id=iid, effect=inst.name, error=err)
                    inst.kill(); del self.instances[iid]
            if self.overlay is not None:               # calibración: la imagen pedida tapa los efectos hasta que venza
                if time.time() < self.overlay_until:
                    canvas.img = self.overlay
                else:
                    self.overlay = None
            if self.scene.coordinate_space != "world_mm" and self.output_offset != (0.0, 0.0):
                M = np.float32([[1, 0, self.output_offset[0]], [0, 1, self.output_offset[1]]])
                canvas.img = cv2.warpAffine(canvas.img, M, (canvas.img.shape[1], canvas.img.shape[0]))
            self.frame = canvas.img
            self.stats["ticks"] += 1
        return self.frame

    def run(self, seconds: float | None = None) -> None:
        self.running = True
        last = time.perf_counter(); t0 = last; start = last; n = 0
        while self.running:
            now = time.perf_counter()
            dt = now - last; last = now
            self.tick(dt)
            n += 1
            if now - t0 >= 1.0:
                self.stats["fps"] = n / (now - t0); n = 0; t0 = now
            if seconds is not None and now - start >= seconds:
                break
            time.sleep(max(0.0, 1.0 / self.fps - (time.perf_counter() - now)))

    # ------------------------------------------------------------------ estado y comandos
    def state(self) -> dict:
        with self.lock:
            return {
                "scene": self.scene.to_json(),
                "effects": [i.state() for i in self.instances.values()],
                "gallery": self.registry.gallery(),
                "errors": self.registry.errors,
                "requests": self.requests.summary(),
                "devices": self.devices.to_json(with_controls=False),
                "audio": self.audio_state(),
                "stats": self.stats, "canvas": list(self.canvas_size), "px_per_mm": self.px_per_mm,
                "touch": self._touch_json(),
                "projector": self.projector,
            }

    # ------------------------------------------------------------------ mesa táctil
    def _touch_json(self) -> dict:
        from .touchsynth.registry import touch_state
        return touch_state(self.scene).to_json()

    def touch(self, a: dict) -> dict:
        """Un toque down/move/up en px del proyector (formato en playground/hands/bridge.py). Sin x/y y con
        `target` ("croc:3", "marimba:5", "cocodrilo") toca el centro de esa zona. state "tap" = down + up."""
        a = dict(a)
        state = str(a.get("state", "")).lower()
        if state not in TOUCH_STATES + ("tap",):
            raise ValueError(f"state tiene que ser down, move, up o tap (llegó {a.get('state')!r})")
        if ("x" not in a or "y" not in a) and a.get("target"):
            from .touchsynth.registry import registry_of
            xy = registry_of(self.scene).resolve(str(a["target"]))
            if xy is None:                        # cualquier objeto de la escena por nombre: su centro
                from .touchsynth.objetos import to_objpx
                o = self.scene.find(str(a["target"]).partition(":")[0])
                op = to_objpx(self.scene, o) if o is not None else None
                xy = op.center if op is not None else None
            if xy is None:
                raise KeyError(f"no sé dónde queda {a['target']!r} (¿está spawneado ese instrumento?)")
            a["x"], a["y"] = xy
        try:
            a["x"], a["y"] = float(a["x"]), float(a["y"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("touch necesita x e y numéricos (px del proyector) o un target") from None
        from .touchsynth.registry import touch_state
        with self.lock:
            ts = touch_state(self.scene)
            a["id"] = str(a.get("id") or f"t{ts.seq + 1}")
            a.setdefault("t", round(time.time(), 4))
            a.setdefault("projector", "proj0")
            a.setdefault("source", "api")
            if state == "tap":
                self._touch_one(ts, {**a, "state": "down"})
                return self._touch_one(ts, {**a, "state": "up"})
            return self._touch_one(ts, {**a, "state": state})

    def _touch_one(self, ts, a: dict) -> dict:
        ev = ts.add(a)
        self.emit("touch", **{k: v for k, v in ev.items() if k != "type"})
        return {"touches": len(ts.touches), "seq": ts.seq, "id": a["id"]}

    def set_hands(self, a: dict) -> dict:
        """Manos sobre la mesa (no son toques): [{id, x, y, height?, dir?, source}] en px del proyector. Las usa la
        linterna. Publica un evento "hands" solo cuando cambia el conjunto de manos (no a 30 Hz)."""
        now = round(time.time(), 4)
        new = {}
        for i, h in enumerate(a.get("hands") or []):
            hid = str(h.get("id", i))
            try:
                new[hid] = {**h, "id": hid, "x": float(h["x"]), "y": float(h["y"]), "t": h.get("t", now)}
            except (KeyError, TypeError, ValueError):
                raise ValueError("cada mano necesita x e y numéricos (px del proyector)") from None
        from .touchsynth.registry import touch_state
        with self.lock:
            ts = touch_state(self.scene)
            changed = set(new) != set(ts.hands)
            ts.hands = new
        if changed:
            self.emit("hands", ids=sorted(new), source=a.get("source", "api"))
        return {"hands": len(new)}

    def set_gaze(self, a: dict) -> dict:
        """Mirada (gancho para la cámara de ojos; hoy se simula): {x, y} px del proyector o {objeto: 'buda'} (su
        centro); {off: true} la borra; sin args devuelve la actual. Queda en scene.gaze (la lee el efecto mirada)."""
        if a.get("off"):
            with self.lock:
                self.scene.gaze = {}
            self.emit("gaze", off=True)
            return {"gaze": {}}
        if "x" not in a and "y" not in a and not a.get("objeto"):
            return {"gaze": dict(self.scene.gaze)}
        g = {"t": round(time.time(), 4), "source": str(a.get("source", "api"))}
        if a.get("objeto") and ("x" not in a or "y" not in a):
            from .touchsynth.objetos import to_objpx
            o = self.scene.find(str(a["objeto"]))
            op = to_objpx(self.scene, o) if o is not None else None
            if op is None:
                raise KeyError(f"objeto desconocido o sin contorno en px: {a['objeto']}")
            g.update(x=op.center[0], y=op.center[1], objeto=op.id)
        else:
            try:
                g.update(x=float(a["x"]), y=float(a["y"]))
            except (KeyError, TypeError, ValueError):
                raise ValueError("gaze necesita x e y numéricos (px del proyector) u objeto") from None
        for k in ("conf", "persona"):
            if k in a:
                g[k] = a[k]
        with self.lock:
            self.scene.gaze = g
        self.emit("gaze", **{k: v for k, v in g.items() if k != "t"})
        return {"gaze": g}

    def snapshot(self, path: str | Path) -> str:
        cv2.imwrite(str(path), self.frame)
        return str(path)

    def command(self, cmd: str, args: dict | None = None) -> dict:
        a = args or {}
        if cmd == 'registered_scene':
            from slscan.registered import RegisteredScene
            registered = RegisteredScene(a['path'])
            if tuple(registered.projector['size']) != tuple(self.canvas_size):
                raise ValueError('registered scene and runtime have different pixel sizes')
            with self.lock:
                self.registered_scene = registered
                self.metric_mode = True
                self.metric_frame = None
                self.frame = np.zeros_like(self.frame)
            return {'scene_sha256': registered.sha, 'metric_status': registered.doc['metric_status'],
                    'physical_verified': False, 'viewer': '/web/registered.html'}
        if cmd == "list_effects":
            return {"gallery": self.registry.gallery(), "errors": self.registry.errors}
        if cmd == "spawn":
            return self.spawn(a["effect"], a.get("target"), a.get("params"))
        if cmd == "kill":
            return {"killed": self.kill(a.get("id"))}
        if cmd == "set_param":
            return self.set_param(a["id"], a["key"], a["value"])
        if cmd == "scene":
            return self.scene.to_json()
        if cmd == "overlay":                   # mostrar una imagen (PNG local) N segundos en vez de los efectos
            if self.metric_mode:
                raise ValueError('registered mode accepts only native metric frames')
            if a.get("off"):
                self.overlay = None; return {"overlay": None}
            img = cv2.imread(str(a["path"]), cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError(f"no se pudo leer {a['path']}")
            if img.shape[:2] != (self.canvas_size[1], self.canvas_size[0]):
                img = cv2.resize(img, self.canvas_size, interpolation=cv2.INTER_NEAREST)
            self.overlay = img; self.overlay_until = time.time() + float(a.get("seconds", 3.0))
            return {"overlay": str(a["path"]), "seconds": float(a.get("seconds", 3.0))}
        if cmd == "output_offset":             # corrimiento global de la salida (px), persistido en <scan>/salida.json
            if self.scene.coordinate_space == "world_mm":
                raise ValueError("Escena métrica: offsets deshabilitados; revisar calibración")
            if self.metric_mode:
                raise ValueError('offsets are forbidden in registered mode')
            if "dx" in a or "dy" in a:
                self.output_offset = (float(a.get("dx", self.output_offset[0])), float(a.get("dy", self.output_offset[1])))
                sd = getattr(self.scene, "scan_dir", None)
                if sd:
                    (Path(sd) / "salida.json").write_text(json.dumps({"dx": self.output_offset[0], "dy": self.output_offset[1]}, indent=1))
                self.emit("output_offset", dx=self.output_offset[0], dy=self.output_offset[1])
            return {"dx": self.output_offset[0], "dy": self.output_offset[1]}
        if cmd == "nudge":                     # 'un poquito más abajo': corre un objeto dx, dy px y lo deja anotado
            if self.metric_mode:
                raise ValueError('object nudges are forbidden in registered mode')
            o = self.scene.nudge(a["object"], float(a.get("dx", 0)), float(a.get("dy", 0)))
            if o is None:
                raise KeyError(f"objeto desconocido: {a['object']}")
            self.emit("nudge", object=o.id, dx=a.get("dx", 0), dy=a.get("dy", 0), ajuste=o.extra.get("ajuste"))
            return {"object": o.id, "ajuste": o.extra.get("ajuste"), "bbox": o.bbox}
        if cmd == "activate_prepared":
            from .auto_scene import activate_runtime
            return activate_runtime(self, a["scan"], a["prepared"])
        if cmd == "reload_scene":              # vuelve a leer segments/workbench/keys (+ajustes) del escaneo
            from .scene import scene_from_scan
            sd = a.get("scan") or getattr(self.scene, "scan_dir", None)
            if not sd:
                raise ValueError("la escena no viene de un escaneo")
            new = scene_from_scan(sd); new.beat, new.audio, new.controls = self.scene.beat, self.scene.audio, self.scene.controls
            new.gaze = dict(getattr(self.scene, "gaze", {}) or {})
            for iid, inst in list(self.instances.items()):
                if new.coordinate_space == "world_mm" or (inst.target and inst.target.id not in new.objects):
                    inst.kill()
                    del self.instances[iid]
            for k in (() if new.coordinate_space == "world_mm" else ("_touchsynth", "_touch")):
                if k in self.scene.__dict__:
                    new.__dict__[k] = self.scene.__dict__[k]
            self.scene = new; self.emit("scene_reloaded", scan=sd)
            return {"scan": sd, "objects": list(new.objects)}
        if cmd == "find":
            o = self.scene.find(a["query"])
            return {"object": o.__dict__ if o else None}
        if cmd == "request":
            r = self.requests.add(a["title"], a.get("body", ""), a.get("kind", "visual"), a.get("from", "secretaria"))
            self.emit("request", id=r.id, title=r.title, kind=r.kind)
            return r.to_json()
        if cmd == "requests":
            return {"requests": [r.to_json() for r in self.requests.list(a.get("status"))]}
        if cmd == "set_status":
            return self.requests.set_status(a["id"], a["status"], a.get("note", "")).to_json()
        if cmd == "reload":
            for p in self.registry._files():
                self.registry.mtimes.pop(p.stem, None)
            return {"reloaded": self.hot_reload()}
        if cmd == "snapshot":
            return {"path": self.snapshot(a.get("path", PROJECT_DIR / "reports" / "playground_snapshot.png"))}
        if cmd == "beat":
            self.scene.beat.update({k: a[k] for k in ("bpm", "playing") if k in a})
            return self.scene.beat
        if cmd == "devices":
            return self.devices.to_json(with_controls=bool(a.get("controls", False)))
        if cmd == "controls":
            dev = self.devices.devices.get(a["device"])
            if dev is None:
                raise KeyError(f"dispositivo desconocido: {a['device']}. Hay: {sorted(self.devices.devices)}")
            return {"device": dev.id, "status": dev.status,
                    "controls": {k: v.to_json() for k, v in dev.controls.items() if not a.get("type") or v.type == a["type"]}}
        if cmd == "connect_devices":
            return self.connect_devices(a.get("prefer", "auto"))
        if cmd == "connect_keyboard":
            return self.connect_keyboard(a.get("prefer", "auto"), a.get("bridge"))
        if cmd == "pianohero":
            return self.pianohero(a)
        if cmd == "simulate":
            dev_id = a.get("device", "ohm")
            fake = next((s_ for s_ in self.devices.sources if isinstance(s_, FakeSource) and s_.device_id == dev_id), None)
            if fake is None:
                fake = FakeSource(dev_id); self.devices.attach(fake)
            ev = fake.send(a["control"], float(a["value"]), a.get("state", "move"))
            if ev is None:
                raise KeyError(f"control desconocido: {a.get('device', 'ohm')}/{a['control']}")
            return ev
        if cmd == "audio":
            if "features" in a or a.get("stop"):
                return self.set_audio(a.get("features"), a.get("meta"), a.get("hold"), bool(a.get("stop")))
            return self.audio_state()
        if cmd == "bind" and str(a.get("control", "")).startswith("audio/"):
            key = a["control"].partition("/")[2]
            if not valid_key(key):
                raise KeyError(f"rasgo de audio desconocido: {key}. Hay: {list(AUDIO_KEYS)}, con canal: ch1.rms, R.bass…")
            self.audio_binds = [x for x in self.audio_binds if not (x.control == a["control"] and x.target == a["target"])]
            b = Binding(a["control"], a["target"], float(a.get("lo", 0.0)), float(a.get("hi", 1.0)))
            self.audio_binds.append(b)
            self.emit("bind", control=b.control, target=b.target)
            return {"control": b.control, "target": b.target, "lo": b.lo, "hi": b.hi}
        if cmd == "unbind" and str(a.get("control", "")).startswith("audio/"):
            before = len(self.audio_binds)
            self.audio_binds = [x for x in self.audio_binds
                                if not (x.control == a["control"] and (a.get("target") is None or x.target == a["target"]))]
            return {"removed": before - len(self.audio_binds)}
        if cmd == "bind":
            b = self.devices.bind(a["control"], a["target"], float(a.get("lo", 0.0)), float(a.get("hi", 1.0)))
            self.emit("bind", control=b.control, target=b.target)
            return {"control": b.control, "target": b.target, "lo": b.lo, "hi": b.hi}
        if cmd == "unbind":
            return {"removed": self.devices.unbind(a["control"], a.get("target"))}
        if cmd == "touch":
            return self.touch(a)
        if cmd == "touches":
            from .touchsynth.registry import registry_of, touch_state
            with self.lock:
                return {**touch_state(self.scene).to_json(), "touchsynth": registry_of(self.scene).status()}
        if cmd == "hands":
            return self.set_hands(a)
        if cmd == "gaze":
            return self.set_gaze(a)
        if cmd == "touchsynth":
            from .touchsynth.registry import registry_of
            with self.lock:
                return registry_of(self.scene).command(a.get("action", "status"), a)
        if cmd == "schema":
            return SCHEMA
        raise KeyError(f"comando desconocido: {cmd}")


SCHEMA = {
    "activate_prepared": {"scan": "existing runtime scan", "prepared": "local prepared.json; atomic effects swap"},
    'registered_scene': {'path': 'local scene.json; locks native output, disables offsets and nudges'},
    "list_effects": {}, "spawn": {"effect": "str", "target": "str?", "params": "dict?"}, "kill": {"id": "str?"},
    "set_param": {"id": "str", "key": "str", "value": "any"}, "scene": {}, "find": {"query": "str"},
    "request": {"title": "str", "body": "str", "kind": "visual|sound|tool|fix", "from": "str"},
    "requests": {"status": "open|doing|done|rejected?"}, "set_status": {"id": "str", "status": "str", "note": "str?"},
    "reload": {}, "snapshot": {"path": "str?"}, "beat": {"bpm": "float?", "playing": "bool?"}, "schema": {},
    "devices": {"controls": "bool?"}, "controls": {"device": "str", "type": "knob|fader|pad|button|crossfader?"},
    "connect_devices": {"prefer": "auto|smartdeck|direct|fake"},
    "connect_keyboard": {"prefer": "auto|direct|bridge|fake", "bridge": "host:puerto? (def. 192.168.1.233:8796)"},
    "pianohero": {"action": "start|pause|resume|toggle|restart|level|song|practice|offset|state|songs|stop",
                  "level": "1|2|3?", "song": "oda|estrellita|elisa|escala|ruta.mid?", "practice": "bool?",
                  "offset_ms": "float?", "speed": "float?", "keys": "''|scene|ruta keys.json?", "demo": "bool? (se toca solo)",
                  "prefer": "auto|direct|bridge|fake|none?"},
    "simulate": {"device": "str?", "control": "str", "value": "0..1", "state": "down|up|move?"},
    "bind": {"control": "dev/control | audio/<sub|bass|mid|high|rms|beat|bpm> | audio/ch<N>.<rasgo> | audio/L.bass",
             "target": "effect:<id>:<param> | beat:bpm | beat:playing", "lo": "float?", "hi": "float?"},
    "audio": {"features": "dict? {sub,bass,mid,high,rms,beat 0..1, onsets int, bpm, tc, ch: [{...}, ...]}", "meta": "dict?",
              "hold": "float? (s que duran los valores; simulación)", "stop": "bool?"},
    "unbind": {"control": "str", "target": "str?"},
    "touch": {"id": "str?", "state": "down|move|up|tap", "x": "px proyector", "y": "px proyector",
              "target": "croc:N | marimba:N | cocodrilo | marimba (en vez de x, y)", "projector": "str?",
              "mm": "[x, y]?", "hand": "str?", "handed": "str?", "trigger": "pinch|apoyo|sombra|presencia|fusion?",
              "t": "float?", "source": "brio|osmo|fusion|mcp?", "velocity": "0..1?", "height": "0..1?"},
    "touches": {}, "hands": {"hands": "[{id, x, y, height?, dir?, source?}]", "source": "str?"},
    "gaze": {"x": "px proyector?", "y": "px proyector?", "objeto": "id|alias? (en vez de x, y)", "off": "bool?",
             "source": "str? (sim|ojos)", "conf": "0..1?"},
    "touchsynth": {"action": "status | clear | panic | recalibrar | sonido | en_vivo",
                   "salida": "auto|pc|mac|virtual|null (con sonido)", "status": "dict (con en_vivo)"},
}


# ---------------------------------------------------------------------- servidor HTTP

def build_app(rt: Runtime):
    from fastapi import FastAPI, Body
    from fastapi.responses import JSONResponse, Response, StreamingResponse

    app = FastAPI(title="live mapping playground")
    from .web.registered_api import mount_registered
    mount_registered(app, rt)

    @app.get("/api/state")
    def state():
        return JSONResponse(json.loads(json.dumps(rt.state(), default=str)))

    @app.get("/api/schema")
    def schema():
        return SCHEMA

    @app.get("/api/effects")
    def effects():
        return rt.command("list_effects")

    @app.post("/api/command")
    def command(body: dict = Body(...)):      # (con `from __future__ import annotations` FastAPI no resuelve Request local)
        try:
            out = rt.command(body.get("cmd", ""), body.get("args") or {})
            return JSONResponse({"ok": True, "result": json.loads(json.dumps(out, default=str))})
        except (KeyError, ValueError) as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.get("/api/frame.png")
    def frame():
        ok, buf = cv2.imencode(".png", rt.frame)
        return Response(buf.tobytes(), media_type="image/png")

    @app.get("/api/events")
    def events():
        q: queue.Queue = queue.Queue(maxsize=200)
        rt.subscribers.append(q)

        def gen():
            try:
                for ev in rt.events[-20:]:
                    yield f"data: {json.dumps(ev)}\n\n"
                while True:
                    try:
                        ev = q.get(timeout=15)
                        yield f"data: {json.dumps(ev)}\n\n"
                    except queue.Empty:
                        yield ": keepalive\n\n"
            finally:
                rt.subscribers.remove(q)
        return StreamingResponse(gen(), media_type="text/event-stream")

    # visor 3D: estáticos de playground/web en /web y la escena (heightfield + objetos) en /api/scene3d
    from .web import mount_web
    mount_web(app, rt)
    return app


def serve(rt: Runtime, host: str = "127.0.0.1", port: int = 8790, window: bool = False, projector: str | None = None) -> None:
    """projector='1920x1080': saca el lienzo entero al proyector con PatternWindow (SDL, hilo principal; nunca al
    display interno ni al Sidecar). window=True: ventanita de vista previa con OpenCV (a mitad de tamaño)."""
    import uvicorn
    app = build_app(rt)
    th = threading.Thread(target=rt.run, daemon=True)
    th.start()
    if window:
        def show():
            while rt.running:
                cv2.imshow("playground", cv2.resize(rt.frame, None, fx=0.5, fy=0.5))
                if cv2.waitKey(30) & 0xFF == ord("q"):
                    rt.running = False
        threading.Thread(target=show, daemon=True).start()
    if projector:
        from slscan.capture import PatternWindow
        srv = threading.Thread(target=lambda: uvicorn.run(app, host=host, port=port, log_level="warning"), daemon=True)
        srv.start()
        win = PatternWindow((rt.canvas_size[0], rt.canvas_size[1]), screen=projector, name="playground")
        try:
            t_chk = 0.0
            while rt.running and srv.is_alive():
                if rt.metric_mode and tuple(win.win_size) != (1920, 1080):
                    raise ValueError('registered projection requires an actual 1920x1080 display; scaling is forbidden')
                # si el proyector (AirPlay) desaparece, la ventana se oculta: jamás sobre los monitores de trabajo
                if time.time() - t_chk > 1.0:
                    t_chk = time.time()
                    if win.display_present():
                        if win.oculta:
                            win.unhide()
                    else:
                        win.hide()
                if win.oculta:
                    time.sleep(0.5); continue
                win.show(rt.frame); time.sleep(1.0 / max(rt.fps, 1.0))
        except KeyboardInterrupt:
            pass
        finally:
            win.close(); rt.running = False
        return
    uvicorn.run(app, host=host, port=port, log_level="warning")
    rt.running = False

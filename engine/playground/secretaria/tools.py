"""Catálogo de herramientas del agente de voz «Playground» y sus handlers.

Cada herramienta tiene nombre, descripción para el LLM, parámetros en JSON Schema (el subconjunto que acepta
ElevenLabs: string / number / integer / boolean, con `description` y `enum`) y un handler que habla con la API HTTP
del runtime (`/api/command`, `/api/state`, `/api/frame.png`; por defecto http://127.0.0.1:8790, `PLAYGROUND_URL`).
`webhook_config()` arma el `tool_config` de ElevenLabs (webhook → puente → handler) para `agent.py`.

Las respuestas son cortas y en palabras: las lee un LLM que habla. Los errores vuelven como {"error": "…"} (nunca
excepción) para que la voz pueda decir «falló» y seguir.

Modos y audio reactivo: el runtime todavía no tiene comandos para eso. Contrato propuesto (lo implementa quien
mantiene runtime.py; las herramientas ya lo usan si existe):

    {"cmd": "mode",  "args": {"mode": "touch|marimba|dibujo|none", "on": true}}
    {"cmd": "audio", "args": {"reactive": true, "sensitivity": 0.5}}

Mientras no exista, `pg_modo` y `pg_audio_reactivo` buscan un efecto de la galería que lo haga (por nombre:
touch/táctil, marimba, draw/dibujo/paint, audio) y lo lanzan o apagan; si no hay, responden «pendiente» y la voz
ofrece dejarlo como idea (`pg_idea`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from . import config as cfgmod

PROJECT = Path(__file__).resolve().parent.parent.parent


class CommandError(Exception):
    """El runtime contestó {"ok": false, "error": …} (o no contestó)."""


# ---------------------------------------------------------------------------- cliente del runtime

class RuntimeClient:
    """Cliente HTTP del runtime. `http` se puede inyectar (un TestClient de FastAPI o un httpx.MockTransport)."""

    def __init__(self, url: str | None = None, http: httpx.Client | None = None, autostart: bool = True,
                 timeout: float = 10.0):
        self.url = (url or cfgmod.load()["runtime_url"]).rstrip("/")
        self.http = http or httpx.Client(timeout=timeout)
        self.autostart = autostart

    def alive(self) -> bool:
        try:
            return self.http.get(f"{self.url}/api/schema", timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    def start(self, wait_s: float = 20.0) -> dict:
        """Levanta el runtime desde el checkout principal (runtime_repo), con el pidfile del MCP `playground`."""
        import time
        from . import procs
        if self.alive():
            return {"status": "running", "started": False}
        pidfile, logfile = procs.runtime_files(self.url)
        if not (procs.repo() / "playground" / "runtime.py").exists():
            return {"status": "failed", "error": f"no hay playground en {procs.repo()} (runtime_repo)"}
        proc = procs.start("runtime", ["playground", "serve", "--port", str(procs.port_of(self.url))], pidfile, logfile)
        t0 = time.time()
        while time.time() - t0 < wait_s:
            if self.alive():
                return {"status": "running", "pid": proc.pid, "started": True}
            if proc.poll() is not None:
                break
            time.sleep(0.25)
        tail = logfile.read_text(errors="replace")[-400:] if logfile.exists() else ""
        return {"status": "failed", "pid": proc.pid, "exit": proc.poll(), "tail": tail}

    def stop(self) -> dict:
        from . import procs
        return procs.stop(procs.runtime_files(self.url)[0])

    def ensure(self) -> None:
        if self.alive():
            return
        if not self.autostart:
            raise CommandError(f"el playground está apagado ({self.url}); se levanta con `python -m playground serve`")
        r = self.start()
        if r.get("status") != "running":
            raise CommandError(f"el playground está apagado y no pude levantarlo: {r.get('error') or r.get('tail', '')[-200:]}")

    def command(self, cmd: str, **args):
        self.ensure()
        try:
            r = self.http.post(f"{self.url}/api/command", json={"cmd": cmd, "args": args}, timeout=15)
            d = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise CommandError(f"el runtime no responde: {type(e).__name__}") from e
        if not d.get("ok"):
            raise CommandError(str(d.get("error") or f"HTTP {r.status_code}"))
        return d.get("result")

    def state(self) -> dict:
        self.ensure()
        try:
            return self.http.get(f"{self.url}/api/state", timeout=15).json()
        except (httpx.HTTPError, ValueError) as e:
            raise CommandError(f"el runtime no responde: {type(e).__name__}") from e

    def frame_png(self) -> bytes | None:
        try:
            r = self.http.get(f"{self.url}/api/frame.png", timeout=10)
            return r.content if r.status_code == 200 else None
        except httpx.HTTPError:
            return None


def _unknown(e: Exception) -> bool:
    return "comando desconocido" in str(e)


# ---------------------------------------------------------------------------- ayudas de lenguaje

COLORES = {
    "rojo": [255, 0, 0], "verde": [0, 255, 0], "azul": [0, 0, 255], "blanco": [255, 255, 255], "negro": [0, 0, 0],
    "amarillo": [255, 230, 0], "naranjo": [255, 140, 0], "naranja": [255, 140, 0], "morado": [150, 0, 255],
    "violeta": [150, 0, 255], "lila": [200, 140, 255], "rosado": [255, 100, 180], "rosa": [255, 100, 180],
    "fucsia": [255, 0, 200], "magenta": [255, 0, 255], "turquesa": [0, 255, 200], "celeste": [100, 200, 255],
    "cian": [0, 255, 255], "dorado": [255, 190, 40], "calido": [255, 180, 110], "cálido": [255, 180, 110],
    "frio": [170, 210, 255], "frío": [170, 210, 255],
}

# lo que se dice → efecto de la galería (además del nombre exacto y del label)
ALIAS_EFECTOS = {
    "halo": "contour_glow", "contorno": "contour_glow", "brillo": "contour_glow",
    "textura": "texture_grid", "grilla": "texture_grid", "vetas": "texture_grid", "patrón": "texture_grid",
    "feed": "frame_feed", "cámara": "frame_feed", "camara": "frame_feed",
    "pads": "control_glow", "controles": "control_glow",
    "respira": "buda_breath", "respiración": "buda_breath", "respiracion": "buda_breath", "respire": "buda_breath",
    "skin": "ddj_skin", "torre": "torre_faces", "hoja": "papel_canvas", "lienzo": "papel_canvas", "hud": "papel_canvas",
}

MODOS = {   # modo → palabras que delatan un efecto que lo hace (playground/touchsynth: croc_synth, marimba, touch_draw)
    "touch": ("croc_synth", "sintetizador"),       # la mesa táctil que suena; no touch_marks (son solo marcas)
    "marimba": ("marimba",),
    "dibujo": ("touch_draw", "dibujar", "dibujo", "paint", "pintar", "trazo", "sketch"),
}


def parse_color(v):
    """'[0,255,0]' | [0,255,0] | 'verde' | '#00ff00' → [r,g,b] (o None)."""
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return [max(0, min(255, int(round(float(x))))) for x in v]
    if not isinstance(v, str):
        return None
    s = v.strip().lower()
    if s in COLORES:
        return list(COLORES[s])
    m = re.fullmatch(r"#?([0-9a-f]{6})", s)
    if m:
        h = m.group(1)
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    try:
        j = json.loads(s)
    except ValueError:
        return None
    return parse_color(j) if isinstance(j, list) else None


def parse_value(key: str, raw):
    """Valor hablado → valor del parámetro. JSON si se puede; nombres de colores en las claves de color."""
    v = raw
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
        except ValueError:
            v = raw.strip()
    if "color" in key.lower():
        c = parse_color(v)
        if c is not None:
            return c
    return v


def parse_params(raw) -> dict:
    if raw in (None, "", {}):
        return {}
    if isinstance(raw, dict):
        d = raw
    else:
        try:
            d = json.loads(raw)
        except ValueError as e:
            raise ValueError("parametros tiene que ser JSON, por ejemplo {\"color\": \"verde\", \"width_mm\": 40}") from e
        if not isinstance(d, dict):
            raise ValueError("parametros tiene que ser un objeto JSON")
    return {k: parse_value(k, v) for k, v in d.items()}


def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().replace("_", " ").split())


def resolve_effect(gallery: list[dict], ref: str) -> str | None:
    """'contour_glow' | 'halo' | 'Halo de contorno' → nombre en la galería."""
    names = {g["name"]: g for g in gallery}
    r = _norm(ref)
    for n in names:
        if _norm(n) == r:
            return n
    for n, g in names.items():
        if r and (r == _norm(g.get("label")) or r in _norm(g.get("label"))):
            return n
    for n, g in names.items():                  # alias del propio efecto: exacto, o contenido si es una frase (3+ palabras)
        for al in g.get("aliases") or []:
            a = _norm(al)
            if a and (a == r or (len(a.split()) >= 3 and a in r)):
                return n
    for word, n in ALIAS_EFECTOS.items():
        if word in r.split() or r == word:
            if n in names:
                return n
    for n in names:
        if r and (r in _norm(n) or _norm(n) in r):
            return n
    return None


def resolve_instance(state: dict, ref: str) -> str | None:
    """'e3' | 'el halo' | 'contour_glow' | 'cocodrilo' → id de la instancia viva más reciente que calce."""
    effects = state.get("effects") or []
    ids = {e["id"] for e in effects}
    r = (ref or "").strip().lower()
    if r in ids:
        return r
    m = re.search(r"\be?(\d+)\b", r)
    if m and f"e{m.group(1)}" in ids:
        return f"e{m.group(1)}"
    gallery = state.get("gallery") or []
    objects = (state.get("scene") or {}).get("objects") or {}
    name = resolve_effect(gallery, r) if r else None

    def num(e):
        try:
            return int(str(e["id"]).lstrip("e"))
        except ValueError:
            return 0
    for e in sorted(effects, key=num, reverse=True):
        tgt = objects.get(e.get("target") or "", {})
        if name and e["effect"] == name:
            return e["id"]
        if r and e.get("target") and (r in _norm(e["target"]) or r in _norm(tgt.get("name"))):
            return e["id"]
    return None


def describe_effect(e: dict, state: dict) -> str:
    gallery = {g["name"]: g for g in state.get("gallery") or []}
    objects = (state.get("scene") or {}).get("objects") or {}
    label = (gallery.get(e["effect"]) or {}).get("label") or e["effect"]
    tgt = e.get("target")
    where = f" sobre {(objects.get(tgt) or {}).get('name') or tgt}" if tgt else ""
    params = ", ".join(f"{k} {json.dumps(v, ensure_ascii=False)}" for k, v in (e.get("params") or {}).items())
    return f"{e['id']}: {label}{where}" + (f" ({params})" if params else "")


AUDIO_EFFECTS = ("bass_pulse", "bass_ripple", "channel_react")


def audio_summary(a) -> dict | None:
    """Resumen corto del estado de audio del runtime (si lo tiene)."""
    if not isinstance(a, dict) or "live" not in a:
        return None
    f = a.get("features") or {}
    return {"vivo": bool(a.get("live")), "bajo": f.get("bass"), "golpe": f.get("beat"), "bpm": f.get("bpm"),
            "dispositivo": (a.get("meta") or {}).get("device")}


def _size_mm(o: dict) -> list | None:
    b = o.get("bbox")
    if not b or len(b) < 6:
        return None
    return [round(b[3] - b[0]), round(b[4] - b[1]), round(b[5] - b[2])]


# ---------------------------------------------------------------------------- catálogo

@dataclass
class Tool:
    name: str
    description: str
    handler: Callable
    properties: dict = field(default_factory=dict)
    required: list = field(default_factory=list)
    timeout: int = 20

    def schema(self) -> dict:
        return {"type": "object", "required": list(self.required), "properties": self.properties}


def S(desc, enum=None):
    d = {"type": "string", "description": desc}
    if enum:
        d["enum"] = list(enum)
    return d


def N(desc):
    return {"type": "number", "description": desc}


def I(desc):  # noqa: E743
    return {"type": "integer", "description": desc}


def B(desc):
    return {"type": "boolean", "description": desc}


MOTIVO = {"motivo": S("Para qué lo consultas, en pocas palabras.")}


class Toolbox:
    """Las herramientas ligadas a un runtime (y, para las luces, a la fábrica de luces BLE)."""

    def __init__(self, runtime: RuntimeClient | None = None, lights: Callable | None = None,
                 requests_dir: Path | None = None, orq_runner: Callable | None = None, audio_ctl=None):
        import threading
        self.rt = runtime or RuntimeClient()
        self._lights = lights
        self.requests_dir = Path(requests_dir) if requests_dir else Path(cfgmod.load()["runtime_repo"]) / "requests"
        self._orq = orq_runner
        self._audio_ctl = audio_ctl
        self._write_lock = threading.Lock()      # las client tools corren en paralelo: un número de pedido por idea
        self.tools: dict[str, Tool] = {t.name: t for t in self._catalog()}

    # ---------------------------------------------------------------- ejecución
    def call(self, name: str, args: dict | None) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            return {"error": f"no existe la herramienta {name}"}
        try:
            clean = coerce(tool, args or {})
        except ValueError as e:
            return {"error": str(e)}
        try:
            out = tool.handler(clean)
        except CommandError as e:
            return {"error": str(e)}
        except (KeyError, ValueError, TypeError) as e:
            return {"error": str(e).strip("'\"")}
        return json.loads(json.dumps(out, ensure_ascii=False, default=str))

    def audio_ctl(self):
        """Quien levanta y apaga la captura de audio (`python -m playground.audio run`): el mismo lanzador del MCP."""
        if self._audio_ctl is not None:
            return self._audio_ctl
        from . import procs
        if not (procs.repo() / "playground" / "audio" / "__main__.py").exists():
            return None
        rt = self.rt

        class _Ctl:
            @staticmethod
            def start(device: str = "", channel: int = -1) -> dict:
                import time
                pidfile, logfile = procs.audio_files(rt.url)
                if procs.pid_of(pidfile):
                    return {"status": "running", "pid": procs.pid_of(pidfile), "started": False}
                args = ["playground.audio", "run", "--url", rt.url, "--log", "--channel", str(channel)]
                proc = procs.start("audio", args + (["--device", device] if device else []), pidfile, logfile)
                for _ in range(32):                       # hasta ~8 s a que el runtime reciba audio
                    try:
                        a = rt.command("audio")
                    except CommandError:
                        a = {}
                    if isinstance(a, dict) and a.get("live"):
                        return {"status": "running", "pid": proc.pid, "device": (a.get("meta") or {}).get("device")}
                    if proc.poll() is not None:
                        return {"status": "failed", "exit": proc.poll(),
                                "tail": logfile.read_text(errors="replace")[-300:] if logfile.exists() else ""}
                    time.sleep(0.25)
                return {"status": "starting", "pid": proc.pid}

            @staticmethod
            def stop() -> dict:
                return procs.stop(procs.audio_files(rt.url)[0])
        return _Ctl

    def lights(self, light_id: str):
        if self._lights is not None:
            return self._lights(light_id)
        from ..lights import BleLight
        return BleLight(light_id)

    # ---------------------------------------------------------------- handlers
    def h_estado(self, a):
        s = self.rt.state()
        sc = s.get("scene") or {}
        return {"objetos": [f"{o.get('name')} ({o.get('kind')})" for o in (sc.get("objects") or {}).values()],
                "efectos_vivos": [describe_effect(e, s) for e in s.get("effects") or []] or "ninguno",
                "galeria": [g["name"] for g in s.get("gallery") or []],
                "modos": s.get("modes") or sc.get("modes") or "el runtime aún no tiene modos (touch, marimba, dibujo)",
                "audio_reactivo": audio_summary(s.get("audio")) or "el runtime aún no tiene audio reactivo",
                "pulso": sc.get("beat"), "fps": round(float((s.get("stats") or {}).get("fps") or 0), 1),
                "pedidos": s.get("requests"), "efectos_con_error": sorted((s.get("errors") or {}).keys())}

    def h_que_proyectas(self, a):
        s = self.rt.state()
        effects = s.get("effects") or []
        out = {"efectos": [describe_effect(e, s) for e in effects]}
        if not effects:
            out["resumen"] = "Nada: no hay efectos vivos, el lienzo está negro."
        else:
            out["resumen"] = f"{len(effects)} efecto{'s' if len(effects) != 1 else ''} vivo{'s' if len(effects) != 1 else ''}."
        cov = frame_coverage(self.rt.frame_png())
        if cov:
            out["lienzo"] = cov
        try:
            shot = cfgmod.state_dir() / "proyectando.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            out["captura"] = (self.rt.command("snapshot", path=str(shot)) or {}).get("path")
        except CommandError:
            pass
        return out

    def h_efectos(self, a):
        g = self.rt.command("list_effects") or {}
        return {"efectos": [{"nombre": x["name"], "que_es": x.get("label"), "va_sobre": x.get("targets"),
                             "parametros": x.get("params"), "descripcion": (x.get("description") or "")[:200]}
                            for x in g.get("gallery") or []],
                "con_error": sorted((g.get("errors") or {}).keys())}

    def h_objetos(self, a):
        sc = self.rt.command("scene") or {}
        kind = "" if a.get("tipo") in (None, "", "todos") else a["tipo"]
        objs = [{"id": k, "nombre": o.get("name"), "tipo": o.get("kind"), "medidas_mm": _size_mm(o),
                 "textura": o.get("texture"), "trackeado": o.get("tracked")}
                for k, o in (sc.get("objects") or {}).items() if not kind or o.get("kind") == kind]
        return {"objetos": objs, "mesa_mm": sc.get("table_mm")}

    def h_buscar_objeto(self, a):
        o = (self.rt.command("find", query=a["nombre"]) or {}).get("object")
        if not o:
            return {"error": f"no encuentro «{a['nombre']}» en la escena"}
        b = o.get("bbox") or [0] * 6
        return {"id": o.get("id"), "nombre": o.get("name"), "tipo": o.get("kind"), "medidas_mm": _size_mm(o),
                "centro_mm": [round((b[0] + b[3]) / 2), round((b[1] + b[4]) / 2), round((b[2] + b[5]) / 2)],
                "textura": o.get("texture"), "plano": bool(o.get("plane")), "trackeado": o.get("tracked")}

    def h_lanzar(self, a):
        g = (self.rt.command("list_effects") or {}).get("gallery") or []
        name = resolve_effect(g, a["efecto"])
        if not name:
            return {"error": f"no hay un efecto «{a['efecto']}»; hay: {', '.join(x['name'] for x in g)}"}
        params = parse_params(a.get("parametros"))
        r = self.rt.command("spawn", effect=name, target=a.get("objeto") or None, params=params or None)
        return {"lanzado": r.get("id"), "efecto": name, "sobre": r.get("target"), "parametros": r.get("params")}

    def h_mover(self, a):
        """«Un poquito más abajo»: corre un objeto en píxeles del proyector (abajo = hacia el teclado / hacia Max)."""
        pasos = float(a.get("pasos") or 1); paso_px = 4.0
        dx = float(a.get("dx") or 0); dy = float(a.get("dy") or 0)
        d = (a.get("direccion") or "").lower()
        if d.startswith("ab"):
            dy += pasos * paso_px
        elif d.startswith("ar"):
            dy -= pasos * paso_px
        elif d.startswith("der"):
            dx += pasos * paso_px
        elif d.startswith("izq"):
            dx -= pasos * paso_px
        if not dx and not dy:
            return {"error": "dime hacia dónde (arriba, abajo, izquierda, derecha) o dx/dy en píxeles"}
        r = self.rt.command("nudge", object=a["objeto"], dx=dx, dy=dy)
        return {"objeto": r.get("object"), "corrido_px": [dx, dy], "ajuste_total_px": r.get("ajuste"),
                "nota": "queda guardado en ajustes.json del escaneo; se ve al instante en el proyector"}

    def h_apagar(self, a):
        if a.get("todos"):
            return {"apagados": self.rt.command("kill", id=None).get("killed")}
        ref = a.get("efecto") or ""
        if not ref:
            return {"error": "dime cuál (id, nombre del efecto u objeto) o usa todos=true"}
        iid = resolve_instance(self.rt.state(), ref)
        if not iid:
            return {"error": f"no hay un efecto vivo que calce con «{ref}»"}
        return {"apagados": self.rt.command("kill", id=iid).get("killed")}

    def h_ajustar(self, a):
        iid = resolve_instance(self.rt.state(), a["efecto"])
        if not iid:
            return {"error": f"no hay un efecto vivo que calce con «{a['efecto']}»"}
        key = a["parametro"]
        r = self.rt.command("set_param", id=iid, key=key, value=parse_value(key, a["valor"]))
        return {"efecto": iid, "parametro": key, "valor": (r.get("params") or {}).get(key)}

    def _mode_effects(self, words) -> tuple[list[dict], list[dict]]:
        s = self.rt.state()
        gallery = [g for g in s.get("gallery") or []
                   if any(w in (_norm(g["name"]) + " " + _norm(g.get("label"))) for w in words)]
        names = {g["name"] for g in gallery}
        return gallery, [e for e in s.get("effects") or [] if e["effect"] in names]

    def h_modo(self, a):
        modo, on = a["modo"], a.get("activo", True)
        wire = "none" if modo == "ninguno" else modo
        try:
            r = self.rt.command("mode", mode=wire, on=bool(on) and modo != "ninguno")
            return {"modo": modo, "activo": bool(on) and modo != "ninguno", "runtime": r}
        except CommandError as e:
            if not _unknown(e):
                raise
        words = sum(MODOS.values(), ()) if modo == "ninguno" else MODOS[modo]
        gallery, alive = self._mode_effects(words)
        if modo == "ninguno" or not on:
            killed = [self.rt.command("kill", id=e["id"]).get("killed") for e in alive]
            return {"modo": modo, "activo": False, "apagados": sum(killed, []),
                    "nota": "" if alive else "no había nada de ese modo prendido"}
        if alive:
            return {"modo": modo, "activo": True, "nota": f"ya estaba prendido ({alive[0]['id']})"}
        if gallery:
            g = gallery[0]
            tgt = None if "*" in g.get("targets", ["*"]) else ("mesa" if "table" in g.get("targets", []) else None)
            r = self.rt.command("spawn", effect=g["name"], target=tgt)
            return {"modo": modo, "activo": True, "efecto": g["name"], "id": r.get("id")}
        pend = {"touch": "la mesa touch es el pedido 004 (falta calibración cámara-mesa y manos en vivo)"}.get(modo, "")
        return {"estado": "pendiente", "modo": modo,
                "detalle": f"el runtime todavía no tiene el modo {modo}. {pend}".strip() +
                           " Ofrece dejarlo como idea con pg_idea."}

    def h_audio(self, a):
        on = bool(a["activo"])
        st = self.rt.state()
        if isinstance(st.get("audio"), dict) and "live" in st["audio"]:
            # runtime con audio reactivo: lo alimenta el proceso de captura (playground.audio), no un comando
            ctl = self.audio_ctl()
            if ctl is None:
                return {"error": "no encuentro el lanzador de la captura de audio (pg_audio del MCP)"}
            r = ctl.start(a.get("dispositivo") or "", -1) if on else ctl.stop()
            follow = [describe_effect(e, st) for e in st.get("effects") or []
                      if e["effect"] in AUDIO_EFFECTS or (e.get("params") or {}).get("audio")]
            out = {"audio_reactivo": on, "captura": r.get("status") if isinstance(r, dict) else r}
            if isinstance(r, dict) and r.get("device"):
                out["dispositivo"] = r["device"]
            if on:
                out["lo_siguen"] = follow or ("nadie todavía: lanza bass_pulse, bass_ripple o channel_react, o un "
                                              "halo con audio=bass")
            if a.get("sensibilidad") is not None:
                out["nota"] = "la sensibilidad se ajusta sola (AGC); no hace falta tocarla"
            return out
        args = {"reactive": on}
        if a.get("sensibilidad") is not None:
            args["sensitivity"] = max(0.0, min(1.0, float(a["sensibilidad"])))
        try:
            return {"audio_reactivo": on, "runtime": self.rt.command("audio", **args)}
        except CommandError as e:
            if not _unknown(e):
                raise
        gallery, alive = self._mode_effects(("audio", "reactiv", "espectro", "fft"))
        if not on:
            killed = [self.rt.command("kill", id=e["id"]).get("killed") for e in alive]
            return {"audio_reactivo": False, "apagados": sum(killed, [])}
        if gallery:
            r = self.rt.command("spawn", effect=gallery[0]["name"], target=None)
            return {"audio_reactivo": True, "efecto": gallery[0]["name"], "id": r.get("id")}
        return {"estado": "pendiente", "detalle": "el runtime todavía no escucha audio (pedido 001: dispositivos MIDI y "
                "audio). Hoy los efectos siguen el pulso de pg_tempo. Ofrece dejarlo como idea con pg_idea."}

    def h_luz(self, a):
        luz = a.get("luz") or "smallrig"
        if luz.startswith("dmx"):
            return {"estado": "pendiente", "detalle": "las luces DMX esperan la interfaz USB-DMX; el SmallRig va por Bluetooth"}
        try:
            lt = self.lights(luz)
            acc = a["accion"]
            if acc == "estado":
                return lt.status()
            rgb = parse_color(a["color"]) if a.get("color") else None
            if a.get("color") and rgb is None:
                return {"error": f"no entendí el color «{a['color']}»"}
            if acc == "apagar":
                return lt.set(on=False)
            return lt.set(on=True if acc == "encender" else None,
                          intensity=max(0.0, min(1.0, float(a["intensidad"]))) if a.get("intensidad") is not None else None,
                          cct=int(a["temperatura_k"]) if a.get("temperatura_k") else None, rgb=rgb)
        except Exception as e:  # noqa: BLE001  (Bluetooth apagado, bleak ausente, foco sin aprender)
            return {"error": f"la luz {luz} no respondió: {type(e).__name__}: {str(e)[:160]}"}

    def h_midi(self, a):
        acc = a["accion"]
        dev = a.get("dispositivo") or "ohm"
        if acc == "conectar":
            return self.rt.command("connect_devices", prefer=a.get("fuente") or "auto")
        if acc == "controles":
            r = self.rt.command("controls", device=dev, type="" if a.get("tipo_control") in (None, "todos") else a["tipo_control"])
            return {"dispositivo": r.get("device"), "estado": r.get("status"),
                    "controles": {k: {"tipo": v.get("type"), "valor": v.get("value")} for k, v in (r.get("controls") or {}).items()}}
        if acc == "simular":
            if not a.get("control"):
                return {"error": "falta el control (por ejemplo knob0)"}
            return self.rt.command("simulate", device=dev, control=a["control"],
                                   value=float(a.get("valor") if a.get("valor") is not None else 1.0), state="move")
        return self.rt.command("devices")

    def h_enlazar(self, a):
        if a.get("quitar"):
            return self.rt.command("unbind", control=a["control"], target=a.get("destino") or None)
        if not a.get("destino"):
            return {"error": "falta el destino (effect:<id>:<parametro>, beat:bpm o beat:playing)"}
        dest = a["destino"]
        m = re.fullmatch(r"effect:([^:]+):(.+)", dest)
        if m and not re.fullmatch(r"e\d+", m.group(1)):
            iid = resolve_instance(self.rt.state(), m.group(1))
            if not iid:
                return {"error": f"no hay un efecto vivo que calce con «{m.group(1)}»"}
            dest = f"effect:{iid}:{m.group(2)}"
        return self.rt.command("bind", control=a["control"], target=dest,
                               lo=float(a.get("minimo") if a.get("minimo") is not None else 0.0),
                               hi=float(a.get("maximo") if a.get("maximo") is not None else 1.0))

    def h_tempo(self, a):
        args = {}
        if a.get("bpm"):
            bpm = float(a["bpm"])
            if not 20 <= bpm <= 300:
                return {"error": "el tempo va entre 20 y 300 bpm"}
            args["bpm"] = bpm
        if a.get("tocando") is not None:
            args["playing"] = bool(a["tocando"])
        return {"pulso": self.rt.command("beat", **args)}

    def h_idea(self, a):
        # directo a la cola (requests/ del checkout principal, el mismo que lee el runtime): sin levantar el runtime
        # solo para anotar, y con candado para que dos ideas dichas juntas no saquen el mismo número
        from ..requests_queue import RequestQueue
        with self._write_lock:
            r = RequestQueue(self.requests_dir).add(a["titulo"].strip()[:120], a["detalle"].strip(),
                                                    a.get("tipo") or "visual", "Max por voz (agente Playground)")
        return {"anotada": r.id, "titulo": r.title, "tipo": r.kind}

    def h_ideas(self, a):
        from ..requests_queue import RequestQueue
        st = a.get("estado") if a.get("estado") not in (None, "", "todos") else None
        reqs = RequestQueue(self.requests_dir).list(st)
        return {"ideas": [f"{x.id} · {x.title} ({x.kind}, {x.status})" for x in reqs[-15:]], "total": len(reqs)}

    # ---------------------------------------------------------------- feedback y decisiones de Max (voz local)
    def orq_note(self, texto: str) -> bool:
        """`orq-creativo note h3 "…"` (la unidad la configura orq_unidad). False si no se pudo."""
        import os
        import subprocess
        cfg = cfgmod.load()
        argv = [os.path.expanduser(cfg.get("orq_cli") or "~/bin/orq-creativo"), "note", cfg.get("orq_unidad") or "h3", texto]
        try:
            if self._orq:
                r = self._orq(argv)
            else:
                r = subprocess.run(argv, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
                                   env={**os.environ, "ORQUESTADOR_BY": "playground-voz"})
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _feedback_line(self, rec: dict) -> None:
        import datetime as dt
        import os
        p = cfgmod.state_dir() / "feedback.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"), **rec},
                               ensure_ascii=False) + "\n")

    def h_feedback(self, a):
        texto = a["texto"].strip()
        self._feedback_line({"tipo": "feedback", "texto": texto})
        from ..requests_queue import RequestQueue
        title = "Feedback de Max por voz: " + " ".join(texto.split()[:8])
        with self._write_lock:
            r = RequestQueue(self.requests_dir).add(title[:120], f"Feedback de Max por voz (textual):\n\n{texto}",
                                                    a.get("tipo") or "fix", "Max por voz (agente Playground)")
        nota = self.orq_note(f"Feedback de Max por voz (textual): {texto}")
        return {"mandado": True, "pedido": r.id, "nota_a_h3": "ok" if nota else "no se pudo (queda en feedback.jsonl y requests/)"}

    def h_decision(self, a):
        import re as _re
        opcion = a["opcion"].strip()
        q = (a.get("pregunta") or "").strip().lower()
        self._feedback_line({"tipo": "decision", "texto": opcion, **({"pregunta": q} if q else {})})
        out = {"registrada": True}
        if _re.fullmatch(r"q\d+", q):
            import os
            import subprocess
            cfg = cfgmod.load()
            argv = [os.path.expanduser(cfg.get("orq_cli") or "~/bin/orq-creativo"), "answer", q,
                    f"{opcion} (Max por voz al agente Playground)", "--de", "persona", "--send"]
            try:
                r = self._orq(argv) if self._orq else subprocess.run(
                    argv, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
                    env={**os.environ, "ORQUESTADOR_BY": "playground-voz"})
                out["respuesta_" + q] = "ok" if r.returncode == 0 else "no se pudo: " + (r.stderr or r.stdout or "")[-160:]
            except (OSError, subprocess.SubprocessError) as e:
                out["respuesta_" + q] = f"no se pudo: {type(e).__name__}"
        nota = self.orq_note(f"Decisión de Max por voz (textual): {opcion}" + (f" (sobre {q})" if q else ""))
        out["nota_a_h3"] = "ok" if nota else "no se pudo (queda en feedback.jsonl)"
        return out

    def h_runtime(self, a):
        acc = a["accion"]
        if acc == "estado":
            alive = self.rt.alive()
            out = {"runtime": "prendido" if alive else "apagado", "url": self.rt.url}
            if alive:
                st = (self.rt.state().get("stats") or {})
                out.update({"fps": round(float(st.get("fps") or 0), 1), "recargas": st.get("reloads")})
            return out
        if acc == "levantar":
            if self.rt.alive():
                return {"runtime": "ya estaba prendido"}
            r = self.rt.start()
            return {"runtime": r.get("status"), "detalle": r.get("error", "")}
        if acc == "reiniciar":
            self.rt.stop()
            r = self.rt.start()
            return {"runtime": r.get("status"), "detalle": r.get("error", "")}
        if acc == "recargar_efectos":
            return {"recargados": self.rt.command("reload").get("reloaded")}
        return {"error": f"acción desconocida: {acc}"}

    # ---------------------------------------------------------------- catálogo
    def _catalog(self) -> list[Tool]:
        return [
            Tool("pg_estado", "Estado del playground: objetos de la escena, efectos vivos, galería, modos, audio "
                 "reactivo, pulso y pedidos. Úsala cuando pregunte «qué hay», «cómo está» o antes de actuar si no "
                 "sabes qué está prendido.", self.h_estado, dict(MOTIVO), ["motivo"]),
            Tool("pg_que_proyectas", "Qué se está proyectando ahora: efectos vivos en palabras, cuánto del lienzo "
                 "está encendido y su color dominante; guarda una captura. Para «qué estás proyectando», «qué se "
                 "ve», «muéstrame».", self.h_que_proyectas, dict(MOTIVO), ["motivo"]),
            Tool("pg_efectos", "Galería de efectos disponibles: nombre, qué es, sobre qué objetos van y sus "
                 "parámetros con valores por defecto. Para «qué efectos hay» o antes de ajustar un parámetro que no "
                 "conoces.", self.h_efectos, dict(MOTIVO), ["motivo"]),
            Tool("pg_objetos", "Objetos de la escena con id, nombre, tipo, medidas en mm, textura y si está "
                 "trackeado. tipo filtra (vacío = todos).", self.h_objetos,
                 {"tipo": S("Filtra por tipo; todos = sin filtro.", ["todos", "table", "screen", "frame", "prop", "instrument", "person"])}),
            Tool("pg_buscar_objeto", "Busca un objeto por nombre, id o tipo (cocodrilo, cuadro, mesa, teclado, ohm) "
                 "y da sus medidas, centro y textura. Para «cuánto mide», «dónde está».", self.h_buscar_objeto,
                 {"nombre": S("Nombre, id o tipo del objeto, como lo dijo Max.")}, ["nombre"]),
            Tool("pg_lanzar_efecto", "Lanza (spawnea) un efecto de la galería sobre un objeto. efecto: nombre de la "
                 "galería o como lo dijo Max (halo, textura, feed). objeto: nombre o id (vacío = sin objeto). "
                 "parametros: JSON opcional; colores en RGB 0-255 o por nombre, p. ej. {\"color\":\"turquesa\"}.",
                 self.h_lanzar,
                 {"efecto": S("Efecto de la galería (contour_glow, texture_grid, frame_feed, control_glow…) o su apodo."),
                  "objeto": S("Objeto sobre el que va (cocodrilo, mesa, cuadro, ohm…); vacío si no aplica."),
                  "parametros": S("JSON opcional con parámetros iniciales, p. ej. {\"color\":[0,255,0],\"width_mm\":40}.")},
                 ["efecto"]),
            Tool("pg_mover", "Corre (alinea) un OBJETO de la escena unos píxeles en el proyector cuando Max dice «el "
                 "cocodrilo un poquito más abajo», «dos pasos más a la derecha». Cada paso son 4 px; abajo es hacia el "
                 "teclado y hacia Max. Queda guardado y se ve al instante. No es para parámetros de efectos (eso es pg_ajustar).",
                 self.h_mover,
                 {"objeto": S("Objeto a correr (cocodrilo, teclado, buda, ddj, ohm, torre, hoja)."),
                  "direccion": S("arriba | abajo | izquierda | derecha", ["arriba", "abajo", "izquierda", "derecha"]),
                  "pasos": S("Cuántos pasos de 4 px (número, por defecto 1; admite decimales)."),
                  "dx": S("Alternativa: corrimiento en px hacia la derecha (negativo = izquierda)."),
                  "dy": S("Alternativa: corrimiento en px hacia abajo (negativo = arriba).")},
                 ["objeto"]),
            Tool("pg_apagar_efecto", "Apaga un efecto vivo (por id como e3, por nombre como «el halo», o por el "
                 "objeto sobre el que está) o todos con todos=true («limpia todo»).", self.h_apagar,
                 {"efecto": S("Id (e3), efecto (halo, contour_glow) u objeto (cocodrilo). Vacío si todos=true."),
                  "todos": B("true para apagar todos los efectos.")}),
            Tool("pg_ajustar", "Cambia un parámetro de un efecto vivo: «más verde», «más grueso», «más rápido». "
                 "efecto: id, nombre o objeto; valor: número, color (nombre o [r,g,b]) o JSON.", self.h_ajustar,
                 {"efecto": S("Id (e3), efecto (halo) u objeto (cocodrilo) del efecto vivo."),
                  "parametro": S("Nombre del parámetro (color, width_mm, speed…; ver pg_efectos)."),
                  "valor": S("Valor nuevo: 40, 2.5, \"verde\", [0,255,0]…")}, ["efecto", "parametro", "valor"]),
            Tool("pg_modo", "Prende o apaga un modo de interacción de la mesa: touch (la mesa como superficie "
                 "táctil), marimba (tocar la mesa suena como marimba), dibujo (dibujar con el dedo); ninguno apaga "
                 "todos. Si vuelve «pendiente», dilo corto y ofrece dejarlo como idea.", self.h_modo,
                 {"modo": S("Modo.", ["touch", "marimba", "dibujo", "ninguno"]),
                  "activo": B("true prende, false apaga.")}, ["modo", "activo"]),
            Tool("pg_audio_reactivo", "Prende o apaga que los efectos reaccionen al audio (la música del estudio: "
                 "mezcladora, loopback de Ableton o el micrófono del Mac). Dice qué efectos lo siguen; si no hay "
                 "ninguno, ofrece lanzar bass_pulse o bass_ripple. Si vuelve «pendiente», dilo corto.", self.h_audio,
                 {"activo": B("true prende, false apaga."), "sensibilidad": N("0 a 1, opcional."),
                  "dispositivo": S("Entrada de audio (QX1222, BlackHole, MacBook); vacío = automático.")},
                 ["activo"], timeout=30),
            Tool("pg_luz", "Luces del estudio: el foco SmallRig por Bluetooth (luz smallrig). accion estado | "
                 "encender | apagar | ajustar; intensidad 0 a 1, temperatura en Kelvin (3200 cálida, 5600 día), "
                 "color por nombre o [r,g,b]. «Más luz», «luz cálida», «apaga el foco».", self.h_luz,
                 {"accion": S("Qué hacer.", ["estado", "encender", "apagar", "ajustar"]),
                  "luz": S("Id de la luz; vacío = smallrig."),
                  "intensidad": N("0 a 1."), "temperatura_k": I("Temperatura de color en Kelvin, 2700 a 6500."),
                  "color": S("Color por nombre o [r,g,b] 0-255 (si el foco es RGB).")}, ["accion"], timeout=40),
            Tool("pg_midi", "Controladores MIDI (OHM RGB hoy): estado (dispositivos y quién tiene el puerto), "
                 "conectar (fuente auto, smartdeck, direct o fake), controles (lista con valores) o simular un "
                 "control sin equipo.", self.h_midi,
                 {"accion": S("Qué hacer.", ["estado", "conectar", "controles", "simular"]),
                  "dispositivo": S("Id del dispositivo; vacío = ohm."),
                  "fuente": S("Para conectar.", ["auto", "smartdeck", "direct", "fake"]),
                  "tipo_control": S("Filtra controles; todos = sin filtro.", ["todos", "knob", "fader", "pad", "button", "crossfader"]),
                  "control": S("Control a simular, p. ej. knob0, fader1, pad3."),
                  "valor": N("Valor 0 a 1 para simular.")}, ["accion"]),
            Tool("pg_enlazar", "Enlaza un control MIDI a un parámetro: «que la perilla 1 mueva el grosor del halo» → "
                 "control ohm/knob0, destino effect:halo:width_mm, minimo 5, maximo 80. Destinos: "
                 "effect:<id o nombre>:<parametro>, beat:bpm, beat:playing. quitar=true lo suelta.", self.h_enlazar,
                 {"control": S("dispositivo/control, p. ej. ohm/knob0, ohm/fader0."),
                  "destino": S("effect:<id o nombre>:<parametro> | beat:bpm | beat:playing."),
                  "minimo": N("Valor con el control abajo."), "maximo": N("Valor con el control arriba."),
                  "quitar": B("true para quitar el enlace.")}, ["control"]),
            Tool("pg_tempo", "Fija el tempo (bpm) del playground y si está tocando. «Sube el tempo», «a 128».",
                 self.h_tempo, {"bpm": N("Tempo en bpm, 20 a 300."), "tocando": B("true si está sonando.")}),
            Tool("pg_idea", "Deja una idea o pedido de feature para quienes programan en vivo (requests/). Úsala "
                 "cuando Max quiera algo que no existe. titulo corto; detalle con qué objeto, qué pasa y cuándo, "
                 "cómo se ve o suena y qué efecto se parece.", self.h_idea,
                 {"titulo": S("Título corto de la idea."),
                  "detalle": S("Qué objeto, qué pasa y cuándo, cómo se ve o suena, qué efecto existente se parece."),
                  "tipo": S("Tipo de pedido.", ["visual", "sound", "tool", "fix"])}, ["titulo", "detalle", "tipo"]),
            Tool("pg_ideas", "Lista las ideas y pedidos y en qué van (open, doing, done, rejected). «Qué pedí», "
                 "«cómo van los pedidos».", self.h_ideas,
                 {"estado": S("Filtra; todos = sin filtro.", ["todos", "open", "doing", "done", "rejected"])}),
            Tool("feedback", "Manda el feedback de Max (lo que le gusta, lo que no, lo que cambiaría) a quien "
                 "programa (h3): queda textual en feedback.jsonl, en requests/ y como nota de h3. Usa sus palabras "
                 "tal cual; si no está claro, pregúntale antes. Después confirma en una frase que lo mandaste.",
                 self.h_feedback,
                 {"texto": S("El feedback de Max, textual, con el contexto mínimo para entenderlo."),
                  "tipo": S("Tipo de pedido.", ["fix", "visual", "sound", "tool"])}, ["texto"], timeout=40),
            Tool("decision", "Registra lo que Max eligió cuando un avance le pregunta algo (opciones). Antes "
                 "repite en una frase qué vas a registrar. pregunta = el número qN si el avance lo trae.",
                 self.h_decision,
                 {"opcion": S("Lo que eligió Max, con sus palabras y cualquier condición."),
                  "pregunta": S("El número de la pregunta (q16) si el avance lo trae; vacío si no.")}, ["opcion"],
                 timeout=40),
            Tool("pg_runtime", "El proceso del playground: estado | levantar | reiniciar | recargar_efectos. Las "
                 "demás herramientas lo levantan solas si está apagado.", self.h_runtime,
                 {"accion": S("Qué hacer.", ["estado", "levantar", "reiniciar", "recargar_efectos"])}, ["accion"],
                 timeout=40),
        ]


# ---------------------------------------------------------------------------- validación y ElevenLabs

_TRUE = ("true", "1", "si", "sí", "yes", "on", "prende", "prendido")


def coerce(tool: Tool, args: dict) -> dict:
    """Ajusta los tipos que manda el LLM (números como texto, booleanos como 'true') y valida required/enum."""
    if not isinstance(args, dict):
        raise ValueError("los argumentos tienen que ser un objeto JSON")
    out = {}
    for k, spec in tool.properties.items():
        if k not in args or args[k] is None:
            continue
        v, t = args[k], spec["type"]
        try:
            if t == "number":
                v = float(v)
            elif t == "integer":
                v = int(float(v))
            elif t == "boolean":
                v = v if isinstance(v, bool) else str(v).strip().lower() in _TRUE
            elif t == "string":
                v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        except (TypeError, ValueError):
            raise ValueError(f"{k} tiene que ser {t}") from None
        if "enum" in spec and v not in spec["enum"]:
            raise ValueError(f"{k} tiene que ser uno de: {', '.join(spec['enum'])}")
        out[k] = v
    for k in tool.required:
        if k not in out or out[k] == "":
            raise ValueError(f"falta {k}")
    return out


_PALETA = ("rojo", "verde", "azul", "blanco", "amarillo", "naranjo", "morado", "rosado", "magenta", "turquesa",
           "celeste", "cian", "dorado")


def frame_coverage(png: bytes | None) -> str | None:
    """'≈12% del lienzo encendido, sobre todo turquesa' a partir del PNG del lienzo."""
    if not png:
        return None
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001
        return None
    if img is None or img.size == 0:
        return None
    lit = img.max(axis=2) > 24
    frac = float(lit.mean())
    if frac < 0.002:
        return "lienzo negro"
    mean = img[lit].mean(axis=0)[::-1]                    # BGR → RGB
    rgb = mean / max(float(mean.max()), 1.0)
    palette = {k: v for k, v in COLORES.items() if k in _PALETA}
    best = min(palette, key=lambda k: sum((palette[k][i] / 255 - float(rgb[i])) ** 2 for i in range(3)))
    return f"≈{round(frac * 100)}% del lienzo encendido, sobre todo {best}"


def webhook_config(tool: Tool, base_url: str, secret_id: str) -> dict:
    """tool_config de ElevenLabs para una herramienta webhook que llega al puente (mismo formato que la secretaria)."""
    base = base_url.rstrip("/")
    return {
        "type": "webhook",
        "name": tool.name,
        "description": tool.description,
        "response_timeout_secs": tool.timeout,
        "execution_mode": "immediate",
        "pre_tool_speech": "auto",
        "tool_error_handling_mode": "passthrough",
        "api_schema": {
            "url": f"{base}{cfgmod.RUTA}/h/{tool.name}",
            "method": "POST",
            "content_type": "application/json",
            "request_headers": {
                "X-Playground-Token": {"secret_id": secret_id},
                "X-Conversacion": {"variable_name": "system__conversation_id"},
            },
            "request_body_schema": tool.schema(),
        },
    }


def client_config(tool: Tool) -> dict:
    """tool_config de ElevenLabs para una client tool: la ejecuta el proceso de voz del Mac (voz.py)."""
    return {
        "type": "client",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.schema(),
        "expects_response": True,
        "response_timeout_secs": min(int(tool.timeout), 120),
        "execution_mode": "immediate",
        "pre_tool_speech": "auto",
    }


def catalog() -> list[Tool]:
    """El catálogo sin runtime vivo (para validar, publicar o el dry-run)."""
    return list(Toolbox(RuntimeClient(url="http://127.0.0.1:0", autostart=False)).tools.values())

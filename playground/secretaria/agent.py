"""Crea o actualiza el agente «Playground» en ElevenLabs (Conversational AI) por su API.

    python -m playground.secretaria publicar [--modo local|tunel] [--url https://<túnel>] [--dry-run]

Dos modos (config.modo; por defecto local):
  local  las herramientas son *client tools*: las ejecuta el proceso de voz del Mac (`voz.py`) contra el runtime en
         localhost. Sin túnel, sin webhooks, sin secreto del workspace.
  tunel  las herramientas son *webhooks* hacia el puente (`server.py`) detrás del túnel propio, con el secreto
         PLAYGROUND_PUENTE_TOKEN en X-Playground-Token (opcional; ver README).

Pasos (idempotentes; los ids quedan en ~/.config/sl-scanner/secretaria/config.json): herramientas del modo, el
agente (prompt de prompt.md + la galería, voz calmada chilena, LLM, auth habilitada, sin grabar voz) y una
verificación de lo que de verdad quedó (la API descarta en silencio campos que no conoce).

La llave de ElevenLabs se lee por referencia (config.api_key). `--dry-run` no toca la red ni lee la llave ni el
token: imprime el payload con marcadores (<secret_id>, <tool_id:…>).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import httpx

from . import config as cfgmod
from .tools import catalog, client_config, webhook_config

API = "https://api.elevenlabs.io"
AQUI = Path(__file__).parent
TAGS = ["playground", "sl-scanner", "smartstack"]
ASR_KEYWORDS = ["playground", "cocodrilo", "halo", "marimba", "touch", "OHM", "fader", "perilla", "pads",
                "contour_glow", "texture_grid", "frame_feed", "control_glow", "SmallRig", "bpm", "proyector",
                "turquesa", "feed", "cuadro", "Buda", "DDJ", "torre", "hoja", "skin", "respira"]


class ElevenLabsError(Exception):
    pass


class ElevenLabs:
    """Cliente mínimo de la API. `http` se puede inyectar (httpx.MockTransport en los tests)."""

    def __init__(self, key: str, http: httpx.Client | None = None):
        if not key:
            raise ElevenLabsError("falta la llave de ElevenLabs (ELEVENLABS_API_KEY o ~/.elevenlabs/api_key)")
        self.http = http or httpx.Client(base_url=API, timeout=60)
        self._headers = {"xi-api-key": key, "Content-Type": "application/json"}

    def req(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        try:
            r = self.http.request(method, path if path.startswith("http") else API + path, headers=self._headers,
                                  json=body)
        except httpx.HTTPError as e:
            raise ElevenLabsError(f"{method} {path.split('?')[0]}: {type(e).__name__}") from e
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {"error": r.text[:300]}
        return r.status_code, data

    def ok(self, method: str, path: str, body: dict | None = None, what: str = "") -> dict:
        code, data = self.req(method, path, body)
        if not 200 <= code < 300:
            raise ElevenLabsError(f"{what or path} falló ({code}): {json.dumps(data, ensure_ascii=False)[:300]}")
        return data


# ---------------------------------------------------------------------------- payloads

def scene_summary(runtime_url: str | None = None) -> str:
    """Galería y objetos para el prompt, si el runtime está prendido (sin levantarlo)."""
    from .tools import RuntimeClient, CommandError
    rt = RuntimeClient(url=runtime_url, autostart=False)
    try:
        s = rt.state()
    except CommandError:
        try:                     # apagado: la galería sale del código (es la misma), los objetos se ven en vivo
            from ..runtime import EffectRegistry
            reg = EffectRegistry()
            reg.load_all()
            s = {"gallery": reg.gallery(), "scene": {"objects": {}}}
        except Exception:  # noqa: BLE001
            return "(el playground estaba apagado al publicar: usa pg_estado y pg_efectos para ver qué hay)"
    objs = ", ".join(f"{o.get('name')} ({o.get('kind')})" for o in (s.get("scene") or {}).get("objects", {}).values())
    if not objs:
        objs = "míralos con pg_objetos (mesa, pantalla, cuadro, cocodrilo, teclado y OHM en la demo)"
    gal = "; ".join(f"{g['name']} = {g.get('label')} (va sobre {', '.join(g.get('targets') or [])}; parámetros "
                    f"{', '.join((g.get('params') or {}).keys())})" for g in s.get("gallery") or [])
    return (f"- Objetos: {objs or 'ninguno'}.\n- Efectos de la galería: {gal or 'ninguno'}.\n"
            "- Esto es una foto al publicar: la galería crece con hot reload; ante la duda, `pg_efectos`.")


def prompt_text(escena: str | None = None) -> str:
    base = (AQUI / "prompt.md").read_text(encoding="utf-8")
    return base.replace("{{ESCENA}}", escena or "(usa pg_estado y pg_efectos para ver qué hay)")


def tool_configs(url: str, secret_id: str) -> list[dict]:
    return [webhook_config(t, url, secret_id) for t in catalog()]


def client_configs() -> list[dict]:
    return [client_config(t) for t in catalog()]


AUDIO_TAGS = [{"tag": "warmly", "description": "calidez, lo normal"},
              {"tag": "softly", "description": "suave, para contar un avance"},
              {"tag": "whispers", "description": "algo íntimo o delicado"},
              {"tag": "reassuring", "description": "calmar, vamos bien"},
              {"tag": "thoughtful", "description": "pensando"},
              {"tag": "sighs", "description": "respiro, pausa o mala noticia"},
              {"tag": "chuckles", "description": "complicidad"},
              {"tag": "playfully", "description": "juego, coqueta"}]

FIRST_MESSAGE ="Hola, Max. Respira... aquí estoy. Te voy contando los avances a medida que lleguen."


def agent_body(cfg: dict, tool_ids: list[str], prompt: str) -> dict:
    return {
        "name": cfg.get("nombre_agente") or "Playground",
        "tags": TAGS,
        "conversation_config": {
            "agent": {
                "first_message": FIRST_MESSAGE,
                "language": "es",
                "prompt": {
                    "prompt": prompt,
                    "llm": cfg.get("llm") or "claude-sonnet-5-5",
                    "temperature": 0.4,
                    "tool_ids": list(tool_ids),
                    "reasoning_effort": "low",
                    "built_in_tools": {
                        "end_call": {"type": "system", "name": "end_call",
                                     "description": "Cuelga cuando Max se despide («chao», «listo», «corta»). Ella "
                                                    "vuelve sola con el próximo avance.",
                                     "params": {"system_tool_type": "end_call"}},
                        "skip_turn": {"type": "system", "name": "skip_turn",
                                      "description": "Quédate callada este turno. Úsala cuando Max está en silencio, "
                                                     "trabajando, o habla con otra persona y no contigo.",
                                      "params": {"system_tool_type": "skip_turn"}},
                    },
                },
            },
            "tts": {"model_id": cfg.get("tts_modelo") or "eleven_v4", "voice_id": cfg.get("voz_id") or "",
                    "stability": float(cfg.get("tts_estabilidad") or 0.65),
                    "speed": float(cfg.get("tts_velocidad") or 0.9),
                    **({"expressive_mode": True, "suggested_audio_tags": AUDIO_TAGS}
                       if cfg.get("tts_expresivo", True) else {})},
            "asr": {"keywords": ASR_KEYWORDS},
            "turn": {"turn_timeout": 30, "silence_end_call_timeout": -1,
                     "soft_timeout_config": {"timeout_seconds": -1}},
            "conversation": {"max_duration_seconds": 3600},
        },
        "platform_settings": {
            "auth": {"enable_auth": True},
            "privacy": {"record_voice": False, "retention_days": 30},
            "call_limits": {"agent_concurrency_limit": 2, "daily_limit": 60},
            "summary_language": "es",
            "overrides": {"conversation_config_override": {"agent": {"first_message": True}}},
        },
    }


def dry_run(url: str | None = None, cfg: dict | None = None, escena: str | None = None,
            modo: str | None = None) -> dict:
    """Lo que se subiría, con marcadores en vez de ids y sin leer secretos."""
    cfg = cfg or cfgmod.load()
    modo = modo or cfg.get("modo") or "local"
    out = {"modo": modo, "agente_id": cfg.get("agente_id") or "(nuevo)", "llave": cfgmod.api_key_source(cfg)}
    if modo == "tunel":
        url = (url or cfg.get("puente_url") or "https://<tunel>").rstrip("/")
        tools = tool_configs(url, "<secret_id>")
        out.update({"puente_url": url, "secreto": {"name": cfg.get("secreto_nombre"), "value": "<token del puente>"}})
    else:
        tools = client_configs()
    out["herramientas"] = tools
    out["agente"] = agent_body(cfg, [f"<tool_id:{t['name']}>" for t in tools], prompt_text(escena))
    return out


# ---------------------------------------------------------------------------- publicar

def _ensure_secret(el: ElevenLabs, cfg: dict, token: str) -> str:
    name = cfg.get("secreto_nombre") or "PLAYGROUND_PUENTE_TOKEN"
    sid = cfg.get("secreto_id")
    if sid:
        code, _ = el.req("PATCH", f"/v1/convai/secrets/{sid}", {"type": "update", "name": name, "value": token})
        if 200 <= code < 300:
            return sid
    code, data = el.req("POST", "/v1/convai/secrets", {"type": "new", "name": name, "value": token})
    if 200 <= code < 300 and data.get("secret_id"):
        return data["secret_id"]
    # ya existía con ese nombre (config perdida): se busca y se actualiza
    listing = el.ok("GET", "/v1/convai/secrets", what="listar secretos")
    for s in listing.get("secrets") or []:
        if s.get("name") == name and s.get("secret_id"):
            el.ok("PATCH", f"/v1/convai/secrets/{s['secret_id']}", {"type": "update", "name": name, "value": token},
                  what="actualizar secreto")
            return s["secret_id"]
    raise ElevenLabsError(f"crear secreto falló ({code})")


def _find_agent(el: ElevenLabs, name: str) -> str | None:
    """Un agente propio con ese nombre (tag sl-scanner) por si se perdió la config: así queda uno solo."""
    code, data = el.req("GET", f"/v1/convai/agents?search={name}&page_size=100")
    if not 200 <= code < 300:
        return None
    for a in data.get("agents") or []:
        if a.get("name") == name and "sl-scanner" in (a.get("tags") or []):
            return a.get("agent_id")
    return None


def _upsert_tools(el: ElevenLabs, confs: list[dict], ids: dict) -> list[str]:
    tool_ids = []
    for conf in confs:
        tid = ids.get(conf["name"])
        if tid:
            code, data = el.req("PATCH", f"/v1/convai/tools/{tid}", {"tool_config": conf})
            if code == 404:
                tid = None
            elif not 200 <= code < 300:
                raise ElevenLabsError(f"actualizar {conf['name']} falló ({code}): {json.dumps(data, ensure_ascii=False)[:300]}")
        if not tid:
            tid = el.ok("POST", "/v1/convai/tools", {"tool_config": conf}, what=f"crear {conf['name']}")["id"]
            ids[conf["name"]] = tid
        tool_ids.append(tid)
    return tool_ids


def publish(url: str | None = None, el: ElevenLabs | None = None, say: Callable[[str], None] = print,
            escena: str | None = None, modo: str | None = None) -> dict:
    cfg = cfgmod.load()
    modo = modo or cfg.get("modo") or "local"
    if modo not in ("local", "tunel"):
        raise ElevenLabsError("modo tiene que ser local o tunel")
    url = (url or cfg.get("puente_url") or "").rstrip("/")
    if modo == "tunel" and not url.startswith("https://"):
        raise ElevenLabsError("falta la URL pública del túnel (https://…): `--url` o levanta el túnel primero")
    el = el or ElevenLabs(cfgmod.api_key(cfg) or "")

    sid = None
    if modo == "tunel":
        token = cfgmod.token(create=True)
        sid = _ensure_secret(el, cfg, token)
        cfg = cfgmod.save({"secreto_id": sid})
        say(f"✓ secreto del puente ({cfg['secreto_nombre']})")
        ids = dict(cfg.get("herramientas_ids") or {})
        tool_ids = _upsert_tools(el, tool_configs(url, sid), ids)
        cfg = cfgmod.save({"herramientas_ids": ids})
        say(f"✓ {len(tool_ids)} herramientas webhook → túnel {cfgmod.mask_url(url)}")
    else:
        ids = dict(cfg.get("herramientas_cliente_ids") or {})
        tool_ids = _upsert_tools(el, client_configs(), ids)
        cfg = cfgmod.save({"herramientas_cliente_ids": ids})
        say(f"✓ {len(tool_ids)} herramientas de cliente (corren en el proceso de voz del Mac)")

    escena = escena if escena is not None else scene_summary(cfg.get("runtime_url"))
    body = agent_body(cfg, tool_ids, prompt_text(escena))
    aid = cfg.get("agente_id") or _find_agent(el, body["name"])
    created = False
    voices = [cfg.get("voz_id")] + [v for v in cfg.get("voces_respaldo") or [] if v != cfg.get("voz_id")]
    for i, voice in enumerate(voices):
        body["conversation_config"]["tts"]["voice_id"] = voice
        if aid:
            code, data = el.req("PATCH", f"/v1/convai/agents/{aid}", body)
            if code == 404:
                aid = None
        if not aid:
            code, data = el.req("POST", "/v1/convai/agents/create", body)
            if 200 <= code < 300:
                aid, created = data["agent_id"], True
        if 200 <= code < 300:
            break
        moderated = "live_moderat" in json.dumps(data)
        if not moderated or i == len(voices) - 1:
            raise ElevenLabsError(f"{'actualizar' if aid else 'crear'} agente falló ({code}): "
                                  f"{json.dumps(data, ensure_ascii=False)[:400]}")
        say(f"· la voz {voice} tiene moderación en vivo (no sirve para agentes); pruebo la siguiente")
    cfg = cfgmod.save({"agente_id": aid, "modo": modo, "voz_id": body["conversation_config"]["tts"]["voice_id"],
                       **({"puente_url": url} if modo == "tunel" else {})})
    say(f"✓ agente «{body['name']}» {'creado' if created else 'actualizado'} ({aid}) · modo {modo}")

    live = el.ok("GET", f"/v1/convai/agents/{aid}", what="leer agente")
    cc, ps = live.get("conversation_config") or {}, live.get("platform_settings") or {}
    tts = cc.get("tts") or {}
    checks = {
        "nombre": live.get("name") == body["name"],
        "herramientas": sorted(((cc.get("agent") or {}).get("prompt") or {}).get("tool_ids") or []) == sorted(tool_ids),
        "auth (sin enlace público)": (ps.get("auth") or {}).get("enable_auth") is True,
        "voz": tts.get("voice_id") == body["conversation_config"]["tts"]["voice_id"],
        "idioma es": (cc.get("agent") or {}).get("language") == "es",
    }
    for k, v in checks.items():
        say(f"{'✓' if v else '✗'} {k}")
    cfgmod.log("publicar", {"agente_id": aid, "creado": created, "modo": modo, "herramientas": len(tool_ids),
                            "chequeos": checks, **({"url": cfgmod.mask_url(url)} if modo == "tunel" else {})})
    return {"agente_id": aid, "creado": created, "modo": modo, "herramientas_ids": ids, "secreto_id": sid,
            "chequeos": checks}


def conversation_token(cfg: dict | None = None, el: ElevenLabs | None = None) -> str:
    """Token de conversación (WebRTC) para la página local; la llave se queda en el Mac."""
    cfg = cfg or cfgmod.load()
    if not cfg.get("agente_id"):
        raise ElevenLabsError("el agente no está publicado (python -m playground.secretaria publicar)")
    el = el or ElevenLabs(cfgmod.api_key(cfg) or "")
    data = el.ok("GET", f"/v1/convai/conversation/token?agent_id={cfg['agente_id']}", what="token de conversación")
    if not data.get("token"):
        raise ElevenLabsError("ElevenLabs no devolvió token")
    return data["token"]

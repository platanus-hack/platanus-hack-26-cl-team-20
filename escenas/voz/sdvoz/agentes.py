"""Crea o actualiza los agentes de ElevenLabs de las escenas (uno por escena) y sus client tools, por la API.

    uv run python -m sdvoz publicar [--escena solar|lab|escritorio] [--dry-run]

Idempotente: los ids quedan en ~/.config/superhuman-desk/voz.json. Si se pierde la config, busca el agente por nombre
y por el tag `superhuman-desk`; nunca toca agentes sin ese tag (la secretaria, «Playground»). Después de publicar
relee el agente y verifica lo que de verdad quedó (la API descarta en silencio campos que no conoce).

`--dry-run` no toca la red ni lee la llave: imprime el payload con marcadores.
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable

import httpx

from . import config as cfgmod
from .escenas import ESCENAS, Escena, escena as get_escena
from .herramientas import CATALOGO, client_config, de_escena

API = "https://api.elevenlabs.io"
TAG = "superhuman-desk"
VERSION = 4                      # sube cuando cambia lo publicado: una versión vieja del código no pisa una nueva
TAGS = [TAG, "platanus-hack-26", "smartstack", f"sdvoz-v{VERSION}"]

AUDIO_TAGS = {
    "solar": [{"tag": "excited", "description": "asombro con un dato"},
              {"tag": "playfully", "description": "juego, adivinanza"},
              {"tag": "warmly", "description": "cariño, animarla"},
              {"tag": "whispers", "description": "un secreto del universo"},
              {"tag": "giggles", "description": "risa chica"}],
    "lab": [{"tag": "calm", "description": "lo normal"},
            {"tag": "reassuring", "description": "cuando algo sale mal"},
            {"tag": "firmly", "description": "para que deje de echar"}],
    "escritorio": [{"tag": "matter-of-fact", "description": "lo normal"},
                   {"tag": "thoughtful", "description": "pensando una opción"}],
}


class ElevenLabsError(Exception):
    pass


class ElevenLabs:
    """Cliente mínimo. `http` se puede inyectar (httpx.MockTransport en los tests)."""

    def __init__(self, key: str, http: httpx.Client | None = None):
        if not key:
            raise ElevenLabsError("falta la llave de ElevenLabs (ELEVENLABS_API_KEY o ~/.elevenlabs/api_key)")
        self.http = http or httpx.Client(base_url=API, timeout=60)
        self._headers = {"xi-api-key": key, "Content-Type": "application/json"}

    def req(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        try:
            r = self.http.request(method, API + path, headers=self._headers, json=body)
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
            raise ElevenLabsError(f"{what or path} falló ({code}): {json.dumps(data, ensure_ascii=False)[:400]}")
        return data


# ------------------------------------------------------------------------------------------------ payloads


def prompt_text(e: Escena) -> str:
    """Personalidad propia + la sección de voz del escena.md del preset (si la trae; la escribe quien arma el preset)
    + las reglas comunes de la mesa."""
    from .presets import preset_de, seccion_voz
    propio = e.prompt_md.read_text(encoding="utf-8").rstrip()
    comun = (e.prompt_md.parent / "comun.md").read_text(encoding="utf-8")
    p = preset_de(e.preset_id, e)
    voz = seccion_voz(p) if p else None
    if voz:
        propio += "\n\n## Lo que dice el preset de tu voz\n\n" + re.sub(r"(?m)^#{1,3} ", "### ", voz)
    return propio + "\n\n" + comun


def agent_body(e: Escena, tool_ids: list[str], cfg: dict, voz: str | None = None) -> dict:
    from .escenas import ASENTIR, COMUN_ASR
    return {
        "name": e.agente,
        "tags": TAGS + [e.clave],
        "conversation_config": {
            "agent": {
                "first_message": e.primer_mensaje,
                "language": "es",
                "prompt": {
                    "prompt": prompt_text(e),
                    "llm": cfg.get("llm") or "claude-haiku-5-5",
                    "temperature": 0.5 if e.clave == "solar" else 0.3,
                    "tool_ids": list(tool_ids),
                    "built_in_tools": {
                        "skip_turn": {"type": "system", "name": "skip_turn",
                                      "description": "Quédate en silencio este turno: la persona habla con otra gente "
                                                     "(presenta, conversa con alguien al lado) o no te habla a ti.",
                                      "params": {"system_tool_type": "skip_turn"}},
                        "end_call": {"type": "system", "name": "end_call",
                                     "description": "Corta solo si la persona se despide claramente («chao, Lumo», "
                                                    "«terminamos»).",
                                     "params": {"system_tool_type": "end_call"}},
                    },
                },
            },
            "tts": {"model_id": cfg.get("tts_modelo") or "eleven_v4", "voice_id": voz or e.voz,
                    "stability": e.estabilidad, "speed": e.velocidad,
                    "expressive_mode": True, "suggested_audio_tags": AUDIO_TAGS.get(e.clave, [])},
            "asr": {"keywords": list(dict.fromkeys((*COMUN_ASR, *e.asr)))},
            "turn": {"turn_timeout": 20, "silence_end_call_timeout": e.silencio_s, "turn_eagerness": e.turno,
                     "interruption_ignore_terms": list(ASENTIR),
                     "soft_timeout_config": {"timeout_seconds": -1}},
            "conversation": {"max_duration_seconds": 1800},
        },
        "platform_settings": {
            "auth": {"enable_auth": not e.publico, "allowlist": [{"hostname": h} for h in e.origenes]},
            "privacy": {"record_voice": False, "retention_days": 30},
            "call_limits": {"agent_concurrency_limit": 3, "daily_limit": 120, "bursting_enabled": e.bursting},
            "summary_language": "es",
            "overrides": {"conversation_config_override": {"agent": {"first_message": True},
                                                           "conversation": {"text_only": True}}},
        },
    }


def tool_payloads() -> list[dict]:
    return [client_config(h) for h in CATALOGO]


def dry_run(claves: list[str] | None = None, cfg: dict | None = None) -> dict:
    cfg = cfg or cfgmod.load()
    claves = claves or list(ESCENAS)
    out = {"llave": cfgmod.api_key_source(), "herramientas": tool_payloads(), "agentes": {}}
    for c in claves:
        e = get_escena(c)
        ids = [f"<tool_id:{h.nombre}>" for h in de_escena(e)]
        out["agentes"][e.clave] = {"agent_id": (cfg.get("agentes") or {}).get(e.clave) or "(nuevo)",
                                   "body": agent_body(e, ids, cfg)}
    return out


# ------------------------------------------------------------------------------------------------ publicar


def _find_agent(el: ElevenLabs, name: str) -> str | None:
    code, data = el.req("GET", f"/v1/convai/agents?search={httpx.QueryParams({'q': name})['q']}&page_size=100")
    if not 200 <= code < 300:
        return None
    for a in data.get("agents") or []:
        if a.get("name") == name and TAG in (a.get("tags") or []):
            return a.get("agent_id")
    return None


def voz_usable(el: ElevenLabs, voz: str, modelo: str) -> tuple[bool, str]:
    """¿La voz habla de verdad? Un TTS de una palabra. El agente acepta al crearse una voz que después falla en vivo
    (p. ej. una voz de la biblioteca que su dueña deshabilitó: «Non retryable error during text to speech»)."""
    try:
        r = el.http.request("POST", f"{API}/v1/text-to-speech/{voz}?output_format=mp3_22050_32",
                            headers=el._headers, json={"text": "Hola.", "model_id": modelo}, timeout=30)
    except httpx.HTTPError as e:
        return False, type(e).__name__
    if r.status_code == 200:
        return True, ""
    try:
        return False, (r.json().get("detail") or {}).get("code") or str(r.status_code)
    except ValueError:
        return False, str(r.status_code)


def _upsert_tools(el: ElevenLabs, ids: dict, say: Callable[[str], None]) -> dict:
    for conf in tool_payloads():
        tid = ids.get(conf["name"])
        if tid:
            code, data = el.req("PATCH", f"/v1/convai/tools/{tid}", {"tool_config": conf})
            if code == 404:
                tid = None
            elif not 200 <= code < 300:
                raise ElevenLabsError(f"actualizar {conf['name']} falló ({code}): "
                                      f"{json.dumps(data, ensure_ascii=False)[:300]}")
        if not tid:
            tid = el.ok("POST", "/v1/convai/tools", {"tool_config": conf}, what=f"crear {conf['name']}")["id"]
            ids[conf["name"]] = tid
    say(f"✓ {len(ids)} client tools")
    return ids


def _version_viva(tags: list[str]) -> int:
    vs = [int(t[6:]) for t in tags or [] if t.startswith("sdvoz-v") and t[6:].isdigit()]
    return max(vs, default=0)


def publish(claves: list[str] | None = None, el: ElevenLabs | None = None,
            say: Callable[[str], None] = print, forzar: bool = False) -> dict:
    if os.environ.get("SD_VOZ_AGENT_ID") and not forzar:
        raise ElevenLabsError("Este computador usa el Lumo público (SD_VOZ_AGENT_ID): publicar se corre solo desde el "
                              "Mac de Max. No hace falta para hablar con Lumo.")
    cfg = cfgmod.load()
    el = el or ElevenLabs(cfgmod.api_key() or "")
    ids = _upsert_tools(el, dict(cfg.get("herramientas") or {}), say)
    cfg = cfgmod.save({"herramientas": ids})
    agentes = dict(cfg.get("agentes") or {})
    resultado = {}
    for c in claves or list(ESCENAS):
        e = get_escena(c)
        tool_ids = [ids[h.nombre] for h in de_escena(e)]
        aid = agentes.get(e.clave) or _find_agent(el, e.agente)
        if aid and not forzar:
            code, vivo = el.req("GET", f"/v1/convai/agents/{aid}")
            if 200 <= code < 300 and _version_viva(vivo.get("tags")) > VERSION:
                raise ElevenLabsError(f"«{e.agente}» lo publicó una versión más nueva del código "
                                      f"(v{_version_viva(vivo.get('tags'))} > v{VERSION}): actualiza la rama, no lo pises.")
        creado, body = False, None
        propia = os.environ.get(f"SD_VOZ_{e.clave.upper()}")          # otra cuenta: una voz de esa cuenta
        voces = tuple(dict.fromkeys(v for v in (propia, e.voz, *e.voces_respaldo) if v))
        modelo = cfg.get("tts_modelo") or "eleven_v4"
        utiles = []
        for v in voces:
            ok, motivo = voz_usable(el, v, modelo)
            if ok:
                utiles.append(v)
            else:
                say(f"· la voz {v} no habla en esta cuenta ({motivo}); la salto")
        if not utiles:
            raise ElevenLabsError(f"ninguna voz de «{e.agente}» habla ({modelo}). Define SD_VOZ_{e.clave.upper()}.")
        voces = tuple(utiles)
        for i, voz in enumerate(voces):
            body = agent_body(e, tool_ids, cfg, voz)
            code, data = 0, {}
            if aid:
                code, data = el.req("PATCH", f"/v1/convai/agents/{aid}", body)
                if code == 404:
                    aid = None
            if not aid:
                code, data = el.req("POST", "/v1/convai/agents/create", body)
                if 200 <= code < 300:
                    aid, creado = data["agent_id"], True
            if 200 <= code < 300:
                break
            de_voz = any(k in json.dumps(data).lower() for k in ("moderat", "voice"))
            if not de_voz or i == len(voces) - 1:
                pista = (f" · Si es la voz: define SD_VOZ_{e.clave.upper()}=<voice_id de tu cuenta>." if de_voz else "")
                raise ElevenLabsError(f"{'actualizar' if aid else 'crear'} «{e.agente}» falló ({code}): "
                                      f"{json.dumps(data, ensure_ascii=False)[:400]}{pista}")
            say(f"· la voz {voz} no sirve en esta cuenta o para agentes; pruebo la siguiente")
        agentes[e.clave] = aid
        cfgmod.save({"agentes": agentes})
        live = el.ok("GET", f"/v1/convai/agents/{aid}", what="leer agente")
        cc, ps = live.get("conversation_config") or {}, live.get("platform_settings") or {}
        turn = cc.get("turn") or {}
        conv = cc.get("conversation") or {}
        ov = ((ps.get("overrides") or {}).get("conversation_config_override") or {})
        checks = {
            "nombre": live.get("name") == e.agente,
            "tag propio": TAG in (live.get("tags") or []),
            "herramientas": sorted(((cc.get("agent") or {}).get("prompt") or {}).get("tool_ids") or []) == sorted(tool_ids),
            "voz": (cc.get("tts") or {}).get("voice_id") == body["conversation_config"]["tts"]["voice_id"],
            "idioma es": (cc.get("agent") or {}).get("language") == "es",
            f"turno {e.turno}": turn.get("turn_eagerness") == e.turno,
            "interrupciones (client event)": "interruption" in (conv.get("client_events") or ["interruption"]),
            "modo texto permitido": bool((ov.get("conversation") or {}).get("text_only")),
            "acceso " + ("público" if e.publico else "privado"): (ps.get("auth") or {}).get("enable_auth") is (not e.publico),
        }
        say(f"{'✓' if all(checks.values()) else '·'} «{e.agente}» {'creado' if creado else 'actualizado'} ({aid})")
        for k, v in checks.items():
            if not v:
                say(f"  ✗ {k}")
        cfgmod.log("publicar", {"escena": e.clave, "agente_id": aid, "creado": creado, "chequeos": checks})
        resultado[e.clave] = {"agent_id": aid, "creado": creado, "chequeos": checks,
                              "client_events": conv.get("client_events")}
    return resultado


def signed_url(clave: str, el: ElevenLabs | None = None) -> str:
    """URL firmada (websocket, sirve para modo texto). La llave se queda en el Mac."""
    aid = (cfgmod.load().get("agentes") or {}).get(get_escena(clave).clave)
    if not aid:
        raise ElevenLabsError(f"el agente de {clave} no está publicado (uv run python -m sdvoz publicar)")
    el = el or ElevenLabs(cfgmod.api_key() or "")
    data = el.ok("GET", f"/v1/convai/conversation/get-signed-url?agent_id={aid}", what="URL firmada")
    if not data.get("signed_url"):
        raise ElevenLabsError("ElevenLabs no devolvió signed_url")
    return data["signed_url"]


def conversation_token(clave: str, el: ElevenLabs | None = None) -> str:
    """Token de conversación (WebRTC, para voz). La llave se queda en el Mac."""
    aid = (cfgmod.load().get("agentes") or {}).get(get_escena(clave).clave)
    if not aid:
        raise ElevenLabsError(f"el agente de {clave} no está publicado (uv run python -m sdvoz publicar)")
    el = el or ElevenLabs(cfgmod.api_key() or "")
    data = el.ok("GET", f"/v1/convai/conversation/token?agent_id={aid}", what="token de conversación")
    if not data.get("token"):
        raise ElevenLabsError("ElevenLabs no devolvió token")
    return data["token"]

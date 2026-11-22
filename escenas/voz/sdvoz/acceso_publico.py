"""Abrir o cerrar el acceso público de UN agente de las escenas (enable_auth), sin tocar nada más.

    uv run python -m sdvoz acceso solar publico     # sin autenticación: la página se conecta con el agent_id
    uv run python -m sdvoz acceso solar privado     # vuelve a exigir URL firmada o token
    uv run python -m sdvoz acceso solar privado --a-las 20:05   # espera hasta esa hora y lo cierra

Lee la configuración completa del agente, cambia solo `platform_settings.auth.enable_auth`, la sube y verifica que el
resto (privacidad, límites, overrides) quedó igual. Deja la hora exacta en el registro de la voz.
"""

from __future__ import annotations

import asyncio
import copy
import time
from datetime import datetime

from . import config as cfgmod
from .agentes import TAG, ElevenLabs, ElevenLabsError
from .escenas import escena as get_escena


def cambiar(clave: str, publico: bool, el: ElevenLabs | None = None) -> dict:
    e = get_escena(clave)
    aid = (cfgmod.load().get("agentes") or {}).get(e.clave)
    if not aid:
        raise ElevenLabsError(f"el agente de {e.clave} no está publicado")
    el = el or ElevenLabs(cfgmod.api_key() or "")
    antes = el.ok("GET", f"/v1/convai/agents/{aid}", what="leer agente")
    if antes.get("name") != e.agente or TAG not in (antes.get("tags") or []):
        raise ElevenLabsError(f"{aid} no es «{e.agente}» con el tag {TAG}: no lo toco")
    ps = copy.deepcopy(antes.get("platform_settings") or {})
    ps.setdefault("auth", {})["enable_auth"] = not publico
    el.ok("PATCH", f"/v1/convai/agents/{aid}", {"platform_settings": ps}, what="cambiar acceso")
    despues = el.ok("GET", f"/v1/convai/agents/{aid}", what="verificar")
    pa, pd = antes.get("platform_settings") or {}, despues.get("platform_settings") or {}
    iguales = {k: pa.get(k) == pd.get(k) for k in ("privacy", "call_limits", "overrides")}
    ok = (pd.get("auth") or {}).get("enable_auth") is (not publico)
    hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    res = {"agente": e.agente, "agent_id": aid, "publico": publico, "ok": ok, "hora": hora, "resto_igual": iguales}
    cfgmod.log("acceso", res)
    return res


def cerrar_a_las(clave: str, hhmm: str) -> dict:
    h, m = (int(x) for x in hhmm.split(":"))
    while True:
        ahora = datetime.now()
        if (ahora.hour, ahora.minute) >= (h, m):
            break
        time.sleep(5)
    for intento in range(5):
        try:
            return cambiar(clave, publico=False)
        except ElevenLabsError as ex:
            cfgmod.log("acceso", {"error": str(ex)[:200], "intento": intento})
            time.sleep(10)
    raise ElevenLabsError("no pude cerrar el acceso público tras 5 intentos")


async def _probar_origen(aid: str, origen: str) -> str:
    """Abre una conversación pública en modo texto con ese Origin y dice si ElevenLabs la acepta."""
    import json as _json

    import websockets
    url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={aid}"
    try:
        async with websockets.connect(url, origin=origen, open_timeout=15) as ws:
            await ws.send(_json.dumps({"type": "conversation_initiation_client_data",
                                       "conversation_config_override": {"conversation": {"text_only": True}}}))
            for _ in range(5):
                m = _json.loads(await asyncio.wait_for(ws.recv(), 15))
                if m.get("type") == "conversation_initiation_metadata":
                    return "conecta"
            return "sin metadata"
    except Exception as e:  # noqa: BLE001
        return f"rechazada ({type(e).__name__}: {str(e)[:80]})"


def frenos(clave: str = "solar", a_las: str | None = None) -> dict:
    """Publica la escena con sus frenos (escenas.py), verifica lo que quedó y prueba un origen permitido y uno ajeno."""
    from .agentes import publish
    if a_las:
        h, m = (int(x) for x in a_las.split(":"))
        while (datetime.now().hour, datetime.now().minute) < (h, m):
            time.sleep(5)
    e = get_escena(clave)
    publish([e.clave], say=lambda t: cfgmod.log("frenos", {"publicar": t}))
    aid = (cfgmod.load().get("agentes") or {})[e.clave]
    a = ElevenLabs(cfgmod.api_key() or "").ok("GET", f"/v1/convai/agents/{aid}")
    ps, turn = a.get("platform_settings") or {}, (a.get("conversation_config") or {}).get("turn") or {}
    res = {"hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "publico": not (ps.get("auth") or {}).get("enable_auth"),
           "allowlist": [x.get("hostname") for x in (ps.get("auth") or {}).get("allowlist") or []],
           "silencio_s": turn.get("silence_end_call_timeout"), "call_limits": ps.get("call_limits")}
    res["origen localhost:9325"] = asyncio.run(_probar_origen(aid, "http://localhost:9325"))
    res["origen ajeno"] = asyncio.run(_probar_origen(aid, "https://ejemplo-ajeno.com"))
    cfgmod.log("frenos", res)
    return res

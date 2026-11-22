"""Prueba en modo texto, sin micrófono ni navegador: el agente de una escena conversa por el SDK de Python y sus
client tools pasan por el mismo servidor local que usa la página (así se prueba la cadena entera hasta la mesa).

    uv run python -m sdvoz probar solar "¿Qué planeta es el más grande?" "Muéstrame Saturno"
    uv run python -m sdvoz probar lab '!evento {"event":"nivel","data":{"vaso":"lab:vaso","ml":56,"objetivo_ml":50,"estado":"te_pasaste","exceso_ml":6,"escena":"laboratorio"}}'

Una línea que parte con `!evento <json>` inyecta un evento en el servidor (como si lo escribiera el motor) y manda
a la conversación lo que la página mandaría (`hablar` → mensaje, `contexto` → contextual update).
"""

from __future__ import annotations

import json
import queue
import threading
import time

import httpx

from . import config as cfgmod
from .escenas import escena as get_escena
from .herramientas import CATALOGO


def _sse(url: str, salida: queue.Queue, parar: threading.Event):
    try:
        with httpx.stream("GET", url, timeout=None) as r:
            for linea in r.iter_lines():
                if parar.is_set():
                    return
                if linea.startswith("data: "):
                    salida.put(json.loads(linea[6:]))
    except httpx.HTTPError:
        return


def probar(clave: str, frases: list[str], servidor: str | None = None, espera_s: float = 40.0) -> list[dict]:
    from elevenlabs.client import ElevenLabs
    from elevenlabs.conversational_ai.conversation import ClientTools, Conversation, ConversationInitiationData

    e = get_escena(clave)
    cfg = cfgmod.load()
    aid = (cfg.get("agentes") or {}).get(e.clave)
    if not aid:
        raise SystemExit(f"el agente de {e.clave} no está publicado (uv run python -m sdvoz publicar)")
    base = (servidor or f"http://127.0.0.1:{cfg['puerto']}").rstrip("/")
    registro: list[dict] = []
    respondio = threading.Event()
    http = httpx.Client(timeout=30)

    def tool(nombre):
        def handler(params: dict):
            params = {k: v for k, v in (params or {}).items() if k != "tool_call_id"}
            r = http.post(f"{base}/api/tool/{e.clave}/{nombre}", json=params).json().get("resultado", "")
            registro.append({"tool": nombre, "args": params, "resultado": r})
            print(f"   ⚙ {nombre}({json.dumps(params, ensure_ascii=False)}) → {r}")
            return r
        return handler

    tools = ClientTools()
    for h in CATALOGO:
        tools.register(h.nombre, tool(h.nombre))

    def agente(texto: str):
        registro.append({"agente": texto})
        print(f"🗣  {e.agente.split('· ')[-1]}: {texto}")
        respondio.set()

    conv = Conversation(
        ElevenLabs(api_key=cfgmod.api_key()), aid, requires_auth=True, audio_interface=None, client_tools=tools,
        config=ConversationInitiationData(conversation_config_override={"conversation": {"text_only": True}}),
        callback_agent_response=agente,
    )
    sse_q: queue.Queue = queue.Queue()
    parar = threading.Event()
    threading.Thread(target=_sse, args=(f"{base}/api/eventos/{e.clave}", sse_q, parar), daemon=True).start()
    conv.start_session()
    try:
        respondio.wait(espera_s)                 # el primer mensaje
        for frase in frases:
            respondio.clear()
            if frase.startswith("!evento "):
                while not sse_q.empty():
                    sse_q.get_nowait()
                http.post(f"{base}/api/simular", json=json.loads(frase[8:]))
                try:
                    msg = sse_q.get(timeout=5)
                except queue.Empty:
                    print("   (el servidor no repartió nada)")
                    continue
                print(f"   ⇢ página: {msg['tipo']} {msg.get('texto')}")
                if msg["tipo"] == "hablar":
                    conv.send_user_message(msg["texto"])
                else:
                    conv.send_contextual_update(msg["texto"])
                    continue
            else:
                print(f"🙋 {frase}")
                registro.append({"persona": frase})
                conv.send_user_message(frase)
            if not respondio.wait(espera_s):
                print("   (sin respuesta)")
            time.sleep(2.5)                      # deja terminar las tools y una segunda frase si la hay
    finally:
        parar.set()
        conv.end_session()
        with contextlib_suppress():
            conv.wait_for_session_end()
    cfgmod.log("probar", {"escena": e.clave, "registro": registro[-40:]})
    return registro


class contextlib_suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True

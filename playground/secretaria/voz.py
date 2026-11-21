"""Conversación de voz nativa en el Mac con el agente «Playground»: micrófono y parlantes del MacBook, sin navegador
ni túnel.

    python -m playground.secretaria voz [--duplex] [--dormir-tras 10] [--con-orquestador 90] [--sin-saludo]

- SDK `elevenlabs` (Conversation con requires_auth: la llave se lee por referencia y el SDK pide una URL firmada).
- Audio con `SoundDeviceAudio` (audio.py), semidúplex por defecto (sin cancelación de eco).
- Herramientas = client tools: se ejecutan aquí mismo (tools.Toolbox → runtime en localhost:8790), más feedback y
  decision para que Max mande feedback por voz.
- Avances en vivo: vigila avances.jsonl (avances.py) y entrega cada avance como contextual update + un aviso del
  sistema para que ella lo cuente, cuando Max no está hablando y ella tampoco.
- Si se corta (red, error), reconecta con espera creciente y sin saludar. Si ella cuelga (Max se despidió) o pasan
  `--dormir-tras` minutos sin nada, queda dormida: el próximo avance la despierta (no se paga una llamada muda).
- Todo lo que pasa queda en ~/.local/state/sl-scanner/secretaria/voz.jsonl (lo dicho, herramientas, avances).
"""

from __future__ import annotations

import json
import logging
import re
import signal
import threading
import time
from typing import Callable

from . import avances
from . import config as cfgmod
from .tools import Toolbox


HANGUP = re.compile(r"\b(cort[aá]|cuelg[aá]|termin[aá]|cierr[aá])(la|lo)?\s+(la\s+|esta\s+)?(llamada|conversaci[oó]n)\b"
                    r"|\bcu[eé]lga(te)?\b|\bch[aá]o\b|\bchau\b|\badi[oó]s\b", re.I)


class _ErrorFlag(logging.Handler):
    """El SDK registra con logger.error cuando la conexión se cae: así se distingue un corte de un colgar normal."""

    def __init__(self, on_error: Callable[[str], None]):
        super().__init__(level=logging.ERROR)
        self.on_error = on_error

    def emit(self, record):
        self.on_error(record.getMessage()[:200])


class Voz:
    def __init__(self, cfg: dict | None = None, toolbox: Toolbox | None = None, audio=None, client=None,
                 conversation_cls=None, client_tools_cls=None, init_data_cls=None, tail: avances.Tail | None = None,
                 dormir_tras_s: float = 600, orq_cada_s: float = 0, saludo: bool = True,
                 say: Callable[[str], None] | None = None):
        self.cfg = cfg or cfgmod.load()
        self.toolbox = toolbox or Toolbox()
        self.audio = audio
        self.client = client
        self.conversation_cls, self.client_tools_cls, self.init_data_cls = conversation_cls, client_tools_cls, init_data_cls
        self.tail = tail or avances.Tail()
        self.dormir_tras_s, self.orq_cada_s, self.saludo = dormir_tras_s, orq_cada_s, saludo
        self.say = say or (lambda m: print(m, flush=True))
        self.conv = None
        self.state = "apagada"                # conectando | viva | dormida | apagada
        self.last_user = self.last_agent = self.last_delivery = self.session_start = 0.0
        self.last_orq = 0.0
        self.last_beat = 0.0
        self.hangup_at = 0.0
        self.pending_since = 0.0
        self.max_wait_s = 60.0                # ruido o charla en la sala: pasado esto, el avance entra en la primera pausa
        self.pending: list[dict] = []
        self.pending_offset = 0
        self.abnormal = False
        self.ended_by: str | None = None      # dormir | salir (lo cerramos nosotros)
        self._end_cb = False
        self.stop_ev = threading.Event()
        self.wake_ev = threading.Event()
        self.lock = threading.RLock()

    # ---------------------------------------------------------------- registro
    def log(self, tipo: str, **d) -> None:
        cfgmod.log("voz", {"tipo": tipo, **d})
        if tipo in ("max", "ella"):
            self.say(f"{'Max' if tipo == 'max' else 'Ella'}: {d.get('texto', '')}")
        elif tipo in ("estado", "avance", "herramienta", "aviso", "error"):
            self.say(f"· {tipo}: " + json.dumps(d, ensure_ascii=False)[:240])

    # ---------------------------------------------------------------- callbacks del SDK
    def _on_user(self, text: str) -> None:
        self.last_user = time.monotonic()
        if HANGUP.search(text or ""):
            self.hangup_at = self.last_user        # respaldo de end_call: el LLM a veces se despide sin colgar
        self.log("max", texto=text)

    def _on_agent(self, text: str) -> None:
        self.last_agent = time.monotonic()
        self.log("ella", texto=text)

    def _on_end(self) -> None:
        self._end_cb = True

    def _on_sdk_error(self, msg: str) -> None:
        self.abnormal = True
        self.log("error", detalle=msg)

    def _tool(self, name: str):
        def handler(params: dict):
            p = {k: v for k, v in (params or {}).items() if k != "tool_call_id"}
            self.log("herramienta", nombre=name, args=json.dumps(p, ensure_ascii=False)[:200])
            out = self.toolbox.call(name, p)
            cfgmod.log("voz", {"tipo": "herramienta_fin", "nombre": name, "ok": "error" not in out,
                               "r": json.dumps(out, ensure_ascii=False)[:300]})
            return json.dumps(out, ensure_ascii=False)
        return handler

    # ---------------------------------------------------------------- sesión
    def _sdk(self):
        if self.conversation_cls is None:
            from elevenlabs.conversational_ai.conversation import (ClientTools, Conversation,
                                                                    ConversationInitiationData)
            self.conversation_cls, self.client_tools_cls, self.init_data_cls = Conversation, ClientTools, ConversationInitiationData
        if self.client is None:
            from elevenlabs.client import ElevenLabs
            key = cfgmod.api_key(self.cfg)
            if not key:
                raise RuntimeError("falta la llave de ElevenLabs (ELEVENLABS_API_KEY o ~/.elevenlabs/api_key)")
            self.client = ElevenLabs(api_key=key)
        if self.audio is None:
            from .audio import SoundDeviceAudio
            self.audio = SoundDeviceAudio(input_match=self.cfg.get("audio_entrada") or "",
                                          output_match=self.cfg.get("audio_salida") or "",
                                          on_warning=lambda m: self.log("aviso", detalle=m))

    def make_conversation(self, first_message: str | None):
        tools = self.client_tools_cls()
        for name in self.toolbox.tools:
            tools.register(name, self._tool(name))
        override = {"agent": {"first_message": first_message}} if first_message is not None else {}
        return self.conversation_cls(
            self.client, self.cfg["agente_id"], requires_auth=True, audio_interface=self.audio,
            config=self.init_data_cls(conversation_config_override=override), client_tools=tools,
            callback_agent_response=self._on_agent, callback_user_transcript=self._on_user,
            callback_end_session=self._on_end)

    def session(self, first_message: str | None) -> str:
        """Una conversación hasta que termina. Devuelve normal | error | dormir | salir."""
        self.abnormal, self.ended_by, self._end_cb = False, None, False
        with self.lock:
            self.state = "conectando"
            self.conv = self.make_conversation(first_message)
        try:
            self.conv.start_session()
        except Exception as e:  # noqa: BLE001  (URL firmada, red)
            self.log("error", detalle=f"no pude abrir la conversación: {type(e).__name__}: {str(e)[:160]}")
            with self.lock:
                self.conv, self.state = None, "apagada"
            return "error"
        self.session_start = time.monotonic()
        self.state = "viva"
        self.log("estado", estado="viva", saludo=first_message is None,
                 audio=getattr(self.audio, "devices", {}) or "")
        self.conv.wait_for_session_end()
        with self.lock:
            conv_id = getattr(self.conv, "_conversation_id", None)
            self.conv = None
            self.state = "apagada"
        self.log("estado", estado="terminada", conv=conv_id, por=self.ended_by or ("corte" if self.abnormal or not self._end_cb else "ella"),
                 sono_s=round(getattr(self.audio, "played_s", 0.0), 1))
        if self.ended_by:
            return self.ended_by
        return "error" if (self.abnormal or not self._end_cb) else "normal"

    def end(self, why: str) -> None:
        with self.lock:
            self.ended_by = why
            conv = self.conv
        if conv is not None:
            try:
                conv.end_session()
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- avances
    def connected(self) -> bool:
        conv = self.conv
        return self.state == "viva" and conv is not None and getattr(conv, "_conversation_id", None) is not None \
            and getattr(conv, "_ws", True) is not None

    def can_speak(self, now: float | None = None, ignore_vad: bool = False) -> bool:
        now = time.monotonic() if now is None else now
        if not self.connected() or now - self.session_start < 1.0:
            return False
        a = self.audio
        if a is not None and (a.playing(now) or (not ignore_vad and a.user_speaking(1.5, now))):
            return False
        if self.last_user > self.last_agent and now - self.last_user < 10:
            return False                      # Max habló y ella todavía no contesta
        if self.last_delivery > self.last_agent and now - self.last_delivery < 15:
            return False                      # el avance anterior todavía no lo cuenta
        return True

    def deliver(self, recs: list[dict], now: float | None = None) -> bool:
        ctx, trigger = avances.for_agent(recs)
        conv = self.conv
        try:
            conv.send_contextual_update(ctx)
            conv.send_user_message(trigger)
        except Exception as e:  # noqa: BLE001
            self.log("error", detalle=f"no pude entregar el avance: {type(e).__name__}")
            return False
        self.last_delivery = time.monotonic() if now is None else now
        self.log("avance", ids=[r["id"] for r in recs], textos=[r["texto"][:120] for r in recs])
        return True

    def tick(self, now: float | None = None) -> None:
        """Una pasada del vigilante: avances nuevos, entrega, notas del orquestador y dormir por inactividad."""
        now = time.monotonic() if now is None else now
        recs, off = self.tail.poll()
        if recs and off > self.pending_offset:
            known = {r["id"] for r in self.pending}
            if not self.pending:
                self.pending_since = now
            self.pending += [r for r in recs if r["id"] not in known]
            self.pending_offset = off
        elif off > self.tail.offset and not recs:
            self.tail.commit(off)             # líneas viejas o vacías: se saltan
        if self.pending:
            if self.state == "dormida":
                self.wake_ev.set()
            elif self.can_speak(now, ignore_vad=now - self.pending_since > self.max_wait_s) and self.deliver(self.pending, now):
                self.tail.commit(self.pending_offset)
                self.pending = []
        if now - self.last_beat >= 20:            # latido para diagnosticar (solo al log, no a la terminal)
            self.last_beat = now
            a = self.audio
            cfgmod.log("voz", {"tipo": "latido", "estado": self.state, "pendientes": len(self.pending),
                               "puede_hablar": self.can_speak(now) if self.state == "viva" else None,
                               **({"suena": a.playing(now), "max_habla": a.user_speaking(1.5, now),
                                   "mic_db": round(getattr(a, "level", 0.0), 1), "piso_db": round(getattr(a, "floor", 0.0), 1),
                                   "sono_s": round(getattr(a, "played_s", 0.0), 1), "audio": getattr(a, "devices", {})}
                                  if a is not None else {})})
        if self.orq_cada_s and now - self.last_orq >= self.orq_cada_s:
            self.last_orq = now
            try:
                avances.from_orquestador(self.cfg)
            except Exception as e:  # noqa: BLE001
                self.log("error", detalle=f"notas del orquestador: {type(e).__name__}")
        if (self.hangup_at and self.state == "viva" and self.last_agent > self.hangup_at
                and now - self.last_agent > 1.5 and not (self.audio is not None and self.audio.playing(now))):
            self.hangup_at = 0.0                  # Max pidió colgar, ella ya se despidió y terminó de hablar
            self.log("estado", estado="colgando", por="Max se despidió")
            self.end("dormir")
            return
        if self.dormir_tras_s and self.state == "viva" and not self.pending:
            a = self.audio
            last = max(self.last_user, self.last_agent, self.last_delivery, self.session_start,
                       getattr(a, "last_voice", 0.0) if a is not None else 0.0)
            if now - last > self.dormir_tras_s and not (a is not None and a.playing(now)):
                self.log("estado", estado="a dormir", tras_s=round(now - last))
                self.end("dormir")

    def _watch(self) -> None:
        while not self.stop_ev.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001
                self.log("error", detalle=f"vigilante: {type(e).__name__}: {str(e)[:120]}")
            self.stop_ev.wait(0.5)

    # ---------------------------------------------------------------- bucle
    def stop(self, *_):
        self.stop_ev.set()
        self.wake_ev.set()
        self.end("salir")

    def run(self) -> int:
        if not self.cfg.get("agente_id"):
            self.say("el agente no está publicado: python -m playground.secretaria publicar")
            return 1
        self._sdk()
        flag = _ErrorFlag(self._on_sdk_error)
        logging.getLogger("elevenlabs.conversational_ai.conversation").addHandler(flag)
        threading.Thread(target=self._watch, name="voz-avances", daemon=True).start()
        first: str | None = None if self.saludo else ""
        backoff = 1.0
        try:
            while not self.stop_ev.is_set():
                result = self.session(first)
                if result == "salir" or self.stop_ev.is_set():
                    break
                first = ""                                      # al volver no saluda: sigue la conversación
                if result == "error":
                    self.log("estado", estado="reconectando", en_s=backoff)
                    self.stop_ev.wait(backoff)
                    backoff = min(backoff * 2, 30.0)
                    continue
                backoff = 1.0
                self.state = "dormida"
                self.log("estado", estado="dormida", despierta="con el próximo avance")
                self.wake_ev.clear()
                while not self.stop_ev.is_set() and not self.wake_ev.wait(1.0):
                    pass
        finally:
            logging.getLogger("elevenlabs.conversational_ai.conversation").removeHandler(flag)
            self.state = "apagada"
        return 0


def main(duplex: bool = False, dormir_min: float = 10, orq_cada_s: float = 0, saludo: bool = True) -> int:
    cfg = cfgmod.load()
    from .audio import SoundDeviceAudio
    voz = Voz(cfg, dormir_tras_s=dormir_min * 60, orq_cada_s=orq_cada_s, saludo=saludo)
    voz.audio = SoundDeviceAudio(input_match=cfg.get("audio_entrada") or "", output_match=cfg.get("audio_salida") or "",
                                 half_duplex=not duplex, on_warning=lambda m: voz.log("aviso", detalle=m))
    signal.signal(signal.SIGINT, voz.stop)
    signal.signal(signal.SIGTERM, voz.stop)
    return voz.run()

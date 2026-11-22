"""Jev en la escena Escritorio: preguntas tipadas sobre lo que ve la cámara secundaria, en un segundo más o menos.

Mismo contrato que `engine/playground/jev.py` (rama max/motor): `/decision-model` del Vercel AI Gateway, con estado
multimodal (imágenes) y preguntas choice / score / boolean / box. Jev no transcribe: decide. La lectura de la libreta
sigue siendo del modelo grande (`pluma.py`); Jev sirve para dos cosas rápidas:

- `que_es`: si lo recién escrito es una pregunta, una idea o nota, un dibujo o algo tachado (para no contestar garabatos);
- `senala`: dónde apunta el dedo en la foto (para «¿qué es esto?» dicho en voz alta mientras señala).

Llave por referencia: `AI_GATEWAY_API_KEY` del entorno; si no está y `JEV_ENV_FILE` apunta a un .env, se lee SOLO esa
variable de ahí y queda en la memoria de este proceso. No se imprime, no se escribe en ningún archivo ni en os.environ.

Si algo falla (sin llave, red, timeout, imagen fuera de rango) las funciones devuelven None y la escena sigue igual.

    uv run python jev.py foto.jpg            # prueba: qué es lo marcado y dónde señala
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.request

import cv2
import numpy as np

URL = "https://ai-gateway.vercel.sh/v4/ai/decision-model"
MODELO = os.environ.get("JEV_VISION_MODEL", "typesafe-ai/jev-1.14.0")
# límites de TypeSafe para imágenes: cada lado entre 280 y 1568 px (fuera de eso se rechaza, no se reescala)
LADO_MIN, LADO_MAX = 280, 1568

_llave: str | None = None


def llave() -> str | None:
    global _llave
    v = os.environ.get("AI_GATEWAY_API_KEY", "").strip()
    if v:
        return v
    if _llave is None:
        _llave = ""
        try:
            for linea in open(os.environ.get("JEV_ENV_FILE", ""), encoding="utf-8"):
                m = re.match(r"\s*(?:export\s+)?AI_GATEWAY_API_KEY\s*=\s*(.*)$", linea)
                if m:
                    v = m.group(1).strip()
                    _llave = v[1:-1] if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0] else re.split(r"\s+#", v, maxsplit=1)[0].strip()
        except OSError:
            pass
    return _llave or None


def disponible() -> bool:
    """¿Hay llave? (no llama a nada)."""
    return bool(llave())


def imagen(img: np.ndarray, calidad: int = 85) -> dict:
    """Parte de estado con una imagen BGR, llevada al rango de tamaños que acepta Jev (rellena con blanco si es muy
    angosta, como un recorte de un renglón)."""
    h, w = img.shape[:2]
    f = min(LADO_MAX / max(h, w), max(1.0, LADO_MIN / min(h, w)))
    if f != 1.0:
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)
    h, w = img.shape[:2]
    if min(h, w) < LADO_MIN:
        img = cv2.copyMakeBorder(img, 0, max(0, LADO_MIN - h), 0, max(0, LADO_MIN - w), cv2.BORDER_CONSTANT, value=(255, 255, 255))
    datos = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, calidad])[1].tobytes()
    return {"type": "file", "mediaType": "image/jpeg", "data": {"type": "data", "data": base64.b64encode(datos).decode("ascii")}}


def decide(partes: list[dict], preguntas: dict, timeout: float = 6.0) -> dict | None:
    """Una llamada a /decision-model. → {answers, ms} o None. Box: {box: {top, bottom, left, right} en fracciones de la
    imagen, confidence}; boolean: {probabilidad}; choice: {choice, confidence}."""
    k = llave()
    if not k:
        return None
    t0 = time.perf_counter()
    qs = {}
    for nombre, q in preguntas.items():
        qs[nombre] = {key: q[key] for key in ("type", "instructions", "criteria") if key in q}
    cabeceras = {
        "Authorization": f"Bearer {k}",
        "ai-gateway-protocol-version": "0.0.1",
        "ai-gateway-auth-method": "api-key",
        "ai-decision-model-specification-version": "4",
        "ai-model-id": MODELO,
        "content-type": "application/json",
        "user-agent": "superhuman-desk-escritorio/1",
    }
    cuerpo = json.dumps({"stateParts": partes, "questions": qs}, ensure_ascii=False).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(URL, data=cuerpo, headers=cabeceras, method="POST"), timeout=timeout) as r:
            data = json.loads(r.read())
    except Exception:  # noqa: BLE001  (sin red, 4xx/5xx, timeout: la escena sigue sin Jev)
        return None
    conf = ((data.get("providerMetadata") or {}).get("typesafe") or {}).get("confidence") or {}
    answers = {}
    for nombre, a in (data.get("answers") or {}).items():
        tipo = a.get("type")
        if tipo == "box":
            answers[nombre] = {"box": {e: float(a["box"][e]) for e in ("top", "bottom", "left", "right")}, "confidence": float(conf.get(nombre, 0.0))}
        elif tipo == "boolean":
            answers[nombre] = {"probabilidad": float(a.get("probability", 0.0))}
        elif tipo in ("choice", "score"):
            probs = {str(o): float(p) for o, p in (a.get("probabilities") or {}).items()}
            elegido = str(a.get("choice", a.get("score")))
            answers[nombre] = {"choice": a.get("choice"), "score": a.get("score"), "confidence": probs.get(elegido, float(conf.get(nombre, 0.0)))}
    if any(nombre not in answers for nombre in preguntas):
        return None
    return {"answers": answers, "ms": round((time.perf_counter() - t0) * 1000.0, 1)}


# ------------------------------------------------------------------ las dos preguntas de la escena

QUE_ES = {
    "pregunta": "una pregunta o un pedido para el agente (por ejemplo «¿cómo le pongo a este endpoint?»)",
    "idea": "una idea, un plan, una lista, una nota o una cuenta con texto o números legibles",
    "dibujo": "un dibujo, una flecha, un subrayado o un garabato, sin texto que leer",
    "tachado": "texto tachado, rayado o borrado: la persona lo descartó",
}


def que_es(pagina_marcada: np.ndarray) -> tuple[str, float, float] | None:
    """Qué es lo encerrado en el recuadro rojo de la página. → (tipo, confianza, ms) o None."""
    r = decide(
        [{"type": "text", "text": "Página de una libreta, vista de frente. Lo recién escrito está encerrado en un recuadro rojo."}, imagen(pagina_marcada)],
        {"que_es": {"type": "choice", "instructions": "¿Qué es lo que está dentro del recuadro rojo?", "criteria": QUE_ES}},
    )
    if not r:
        return None
    a = r["answers"]["que_es"]
    return str(a["choice"]), float(a["confidence"]), r["ms"]


def senala(foto: np.ndarray) -> dict | None:
    """Dónde apunta el dedo índice en la foto: {centro: (x, y) en px de `foto`, confianza, ms} o None si no señala."""
    r = decide(
        [{"type": "text", "text": "Foto de una libreta sobre un escritorio, tomada de cerca. Puede haber una mano señalando."}, imagen(foto)],
        {
            "senala": {"type": "boolean", "instructions": "¿Hay un dedo señalando algo escrito en la libreta?"},
            "donde": {"type": "box", "instructions": "La zona de la libreta que toca o apunta la punta del dedo índice."},
        },
    )
    if not r or r["answers"]["senala"]["probabilidad"] < 0.5:
        return None
    b = r["answers"]["donde"]["box"]
    h, w = foto.shape[:2]
    return {"centro": ((b["left"] + b["right"]) / 2 * w, (b["top"] + b["bottom"]) / 2 * h), "confianza": r["answers"]["donde"]["confidence"], "ms": r["ms"]}


DEICTICOS = re.compile(r"\b(esto|esta|este|eso|esa|ese|ac[aá]|aqu[ií]|ah[ií]|lo que (se[nñ]alo|apunto|marco))\b", re.IGNORECASE)


def es_deictico(texto: str | None) -> bool:
    """«¿Qué es esto?», «¿y acá?»: lo dicho necesita saber dónde señala."""
    return bool(texto and DEICTICOS.search(texto))


if __name__ == "__main__":
    if not disponible():
        sys.exit("sin AI_GATEWAY_API_KEY (entorno o JEV_ENV_FILE)")
    img = cv2.imread(sys.argv[1])
    print("que_es:", que_es(img))
    print("senala:", senala(img))

"""Jev (TypeSafe, modelo de decisión «System One») para la mesa: cliente mínimo, waterfall de emoji, ruta de efecto e
intensidad por objeto. Pedido de Max, 8-nov.

Jev recibe un `state` de TEXTO y preguntas tipadas y devuelve probabilidades (~200 ms por el Vercel AI Gateway). No ve
imágenes: aquí solo viajan etiquetas y medidas en texto (nada de fotos de la casa).

Contrato (el mismo de `/api/jev` en remotion-live, live/vite.config.ts y live/src/director/jevClient.ts):

    pedido     {state: str, questions: {clave: {type: "choice", instructions, criteria: {opción: descripción}}
                                        | {type: "score", instructions, criteria: [nivel0, nivel1, ...]}
                                        | {type: "noul", instructions}}}
    respuesta  {answers: {clave: {type: "choice", choice, confidence, probabilities}
                               | {type: "score", score (0..n-1), confidence, legend, probabilities}
                               | {type: "noul", noul}},
                usage: {input_tokens, output_tokens}}

Rutas (variable JEV_ROUTE):
  gateway (por defecto)  POST https://ai-gateway.vercel.sh/v4/ai/evaluation-model, modelo `typesafe-ai/jev` (JEV_MODEL):
                         el mismo pedido que arma `ai@7 experimental_evaluate`; `noul` viaja como `boolean`.
  direct                 POST https://api.typesafe.ai/v1/systemone con TYPESAFE_API_KEY; si falla, cae al gateway.

Llave: AI_GATEWAY_API_KEY del entorno del proceso. Si no está, se lee SOLO esa variable desde JEV_ENV_FILE (por
defecto el .env de remotion-live) y queda en la memoria de este proceso: no se escribe en os.environ (así no la heredan
los subprocesos: Claude CLI, displayplacer) ni en ningún archivo, log o respuesta.

Si algo falla (sin llave, red, timeout, respuesta rara) las funciones devuelven None y el que llama sigue igual.

    .venv/bin/python -m playground.jev "teléfono"        # prueba rápida: emoji + ruta + intensidad de una etiqueta
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

AQUI = Path(__file__).resolve().parent
ARBOL_EMOJI = AQUI / "data" / "emoji_tree.json"        # copia de remotion-live live/catalog/trees/emoji.json (1.914 emoji)
GATEWAY_URL = "https://ai-gateway.vercel.sh/v4/ai/evaluation-model"
DIRECT_URL = "https://api.typesafe.ai/v1/systemone"
MODELO = os.environ.get("JEV_MODEL", "typesafe-ai/jev")
USD_POR_M_ENTRADA = 0.042                                # lista del gateway (GET /v1/models, 8-nov-2026); salida: 0
ENV_FILE_DEF = "/Users/max/Desktop/coding_projects/remotion-live/.env"
TIMEOUT_S = 4.0


class JevError(RuntimeError):
    pass


# ───────────────────────── llave y transporte ─────────────────────────

_llaves: dict[str, str] = {}
_lock = threading.Lock()
_http: httpx.Client | None = None
_stats = {"llamadas": 0, "fallas": 0, "tokens_in": 0, "tokens_out": 0, "ms_total": 0.0, "costo_usd": 0.0,
          "ms_ultimas": [], "ultimo_error": None, "ultima_ruta": None}


def _llave(nombre: str) -> str | None:
    """Valor de `nombre` desde el entorno o, si no está, desde JEV_ENV_FILE (solo esa variable, solo en memoria)."""
    v = os.environ.get(nombre)
    if v and v.strip():
        return v.strip()
    with _lock:
        if nombre not in _llaves:
            val = ""
            try:
                for linea in Path(os.environ.get("JEV_ENV_FILE", ENV_FILE_DEF)).read_text().splitlines():
                    m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", linea)
                    if not m or m.group(1) != nombre:
                        continue
                    v_ = m.group(2).strip()
                    if len(v_) >= 2 and v_[0] in "\"'" and v_[-1] == v_[0]:
                        v_ = v_[1:-1]
                    else:
                        v_ = re.split(r"\s+#", v_, maxsplit=1)[0].strip()
                    val = v_
            except OSError:
                pass
            _llaves[nombre] = val
        return _llaves[nombre] or None


def llave_gateway() -> str | None:
    return _llave("AI_GATEWAY_API_KEY")


def ruta_http() -> str:
    return (os.environ.get("JEV_ROUTE") or "gateway").strip().lower()


def disponible() -> bool:
    """¿Hay con qué llamar? (no hace ninguna llamada)."""
    return bool(llave_gateway() or (ruta_http() == "direct" and _llave("TYPESAFE_API_KEY")))


def cabeceras_gateway(llave: str, modelo: str | None = None, tipo: str = "evaluation") -> dict[str, str]:
    """Cabeceras que pone @ai-sdk/gateway 4.x (protocolo 0.0.1, especificación v4). `tipo`: evaluation (ai@7.0.114,
    /evaluation-model) o decision (ai@7.0.165+, /decision-model: estado multimodal y Box)."""
    return {"Authorization": f"Bearer {llave}", "ai-gateway-protocol-version": "0.0.1", "ai-gateway-auth-method": "api-key",
            f"ai-{tipo}-model-specification-version": "4", "ai-model-id": modelo or MODELO, "content-type": "application/json",
            "user-agent": "sl-scanner-jev/1 ai-sdk/gateway-compatible"}


def _costo_gateway(cuerpo: dict) -> float | None:
    """Costo real que informa el gateway (providerMetadata.gateway.cost, US$) o None."""
    try:
        return float(((cuerpo.get("providerMetadata") or {}).get("gateway") or {}).get("cost"))
    except (TypeError, ValueError):
        return None


def a_gateway(questions: dict) -> tuple[dict, set[str]]:
    """Preguntas en formato systemone → formato del gateway (`noul` → `boolean`). Solo pasan type/instructions/criteria."""
    qs, noul = {}, set()
    for k, q in questions.items():
        tipo = q.get("type")
        if tipo == "noul":
            noul.add(k)
            qs[k] = {"type": "boolean", "instructions": q["instructions"]}
        elif tipo in ("choice", "score"):
            qs[k] = {"type": tipo, "instructions": q["instructions"], "criteria": q["criteria"]}
        else:
            raise JevError(f"pregunta {k}: tipo {tipo!r} no soportado")
    return qs, noul


def de_gateway(cuerpo: dict, questions: dict, noul: set[str]) -> tuple[dict, dict]:
    """Respuesta del gateway → (answers en formato systemone, usage {input_tokens, output_tokens})."""
    out = {}
    for k, a in (cuerpo.get("answers") or {}).items():
        if k in noul:
            out[k] = {"type": "noul", "noul": float(a.get("probability", 0.0))}
            continue
        probs = {str(o): float(p) for o, p in (a.get("probabilities") or {}).items()}
        if a.get("type") == "score":
            crit = (questions.get(k) or {}).get("criteria") or []
            out[k] = {"type": "score", "score": float(a["score"]), "confidence": max(probs.values(), default=1.0),
                      "legend": {str(i): str(c) for i, c in enumerate(crit)}, "probabilities": probs}
        else:
            out[k] = {"type": "choice", "choice": a.get("choice"), "confidence": probs.get(str(a.get("choice")), 1.0),
                      "probabilities": probs}
    u = cuerpo.get("usage") or {}
    return out, {"input_tokens": int(u.get("inputTokens") or 0), "output_tokens": int(u.get("outputTokens") or 0)}


def _cliente() -> httpx.Client:
    global _http
    with _lock:
        if _http is None:                   # una conexión keep-alive: la segunda llamada ya no paga TLS
            _http = httpx.Client(timeout=httpx.Timeout(TIMEOUT_S, connect=2.5))
        return _http


def _validar(answers: dict, questions: dict) -> None:
    for k, q in questions.items():
        a = answers.get(k)
        if not a or a.get("type") != q["type"]:
            raise JevError(f"respuesta sin {k} o de otro tipo")
        if q["type"] == "choice" and a.get("choice") not in q["criteria"]:
            raise JevError(f"{k}: opción desconocida {a.get('choice')!r}")
        if q["type"] == "score" and not (isinstance(a.get("score"), (int, float)) and math.isfinite(a["score"])):
            raise JevError(f"{k}: score inválido")


def _por_gateway(estado: str, questions: dict, timeout: float) -> dict:
    llave = llave_gateway()
    if not llave:
        raise JevError("sin AI_GATEWAY_API_KEY")
    qs, noul = a_gateway(questions)
    r = _cliente().post(GATEWAY_URL, headers=cabeceras_gateway(llave), timeout=timeout,
                        content=json.dumps({"state": estado, "questions": qs}, ensure_ascii=False).encode())
    if r.status_code != 200:
        raise JevError(f"gateway {r.status_code}: {r.text[:200]}")
    cuerpo = r.json()
    answers, usage = de_gateway(cuerpo, questions, noul)
    return {"answers": answers, "usage": usage, "ruta": "gateway", "modelo": MODELO, "costo_gateway_usd": _costo_gateway(cuerpo)}


def _directo(estado: str, questions: dict, timeout: float) -> dict:
    llave = _llave("TYPESAFE_API_KEY")
    if not llave:
        raise JevError("sin TYPESAFE_API_KEY")
    r = _cliente().post(DIRECT_URL, timeout=timeout, headers={"Authorization": f"Bearer {llave}", "content-type": "application/json"},
                        content=json.dumps({"model": "jev-latest", "state": estado, "questions": questions}, ensure_ascii=False).encode())
    if r.status_code != 200:                # 402 = TypeSafe sin créditos (visto el 25-sep): cae al gateway
        raise JevError(f"typesafe {r.status_code}: {r.text[:200]}")
    j = r.json()
    u = j.get("usage") or {}
    return {"answers": j.get("answers") or {}, "usage": {"input_tokens": int(u.get("input_tokens") or 0),
            "output_tokens": int(u.get("output_tokens") or 0)}, "ruta": "direct", "modelo": j.get("model", "jev-latest")}


def evaluate(state, questions: dict, *, timeout: float = TIMEOUT_S) -> dict | None:
    """Una llamada a Jev. Devuelve {answers, usage, ruta, modelo, ms, costo_usd} o None si falla (el error queda en
    stats()["ultimo_error"])."""
    estado = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    pasos = [_directo, _por_gateway] if ruta_http() == "direct" else [_por_gateway]
    err = "sin pasos"
    for paso in pasos:
        t0 = time.perf_counter()
        try:
            r = paso(estado, questions, timeout)
            _validar(r["answers"], questions)
        except Exception as e:  # noqa: BLE001
            err = f"{paso.__name__.strip('_')}: {e}"[:300]
            continue
        r["ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        _contar(r)
        return r
    _contar_falla(err)
    return None


def _contar(r: dict) -> None:
    r["costo_usd"] = r["usage"]["input_tokens"] / 1e6 * USD_POR_M_ENTRADA
    with _lock:
        s = _stats
        s["llamadas"] += 1; s["tokens_in"] += r["usage"]["input_tokens"]; s["tokens_out"] += r["usage"]["output_tokens"]
        s["ms_total"] += r["ms"]; s["costo_usd"] += r["costo_usd"]; s["ultima_ruta"] = r["ruta"]
        if r.get("costo_gateway_usd") is not None:
            s["costo_gateway_usd"] = s.get("costo_gateway_usd", 0.0) + r["costo_gateway_usd"]
        s["ms_ultimas"] = (s["ms_ultimas"] + [r["ms"]])[-200:]


def _contar_falla(err: str) -> None:
    with _lock:
        _stats["fallas"] += 1; _stats["ultimo_error"] = f"{time.strftime('%H:%M:%S')} {err}"


# ───────────────────────── estado multimodal y Box (gateway /decision-model) ─────────────────────────

DECISION_URL = "https://ai-gateway.vercel.sh/v4/ai/decision-model"
# imágenes y Box: Jev 1.14 versionado (retención cero «all» en el gateway; el alias typesafe-ai/jev figura con «some»)
MODELO_VISION = os.environ.get("JEV_VISION_MODEL", "typesafe-ai/jev-1.14.0")
# límites de TypeSafe para imágenes (docs.typesafe.ai/images, nov-2026): PNG/JPEG, cada lado 280-1568 px, ≤ 2.458.624 px,
# ≤ 16 MiB por imagen y 32 MiB por pedido; una imagen fuera de rango se rechaza (no se reescala)
IMG_LADO_MIN, IMG_LADO_MAX = 280, 1568


def parte_imagen(jpg: bytes, referencia: str | None = None, media: str = "image/jpeg") -> dict:
    """Parte de estado con una imagen (base64, sin prefijo data:). Con `referencia` (p. ej. "[mesa-1]"), las preguntas
    la nombran por la ruta del JSON de estado que la contiene (`mesa`) y la imagen va una sola vez."""
    import base64
    p = {"type": "file", "mediaType": media, "data": {"type": "data", "data": base64.b64encode(jpg).decode("ascii")}}
    if referencia:
        p["providerOptions"] = {"typesafe": {"reference": referencia}}
    return p


def decide(partes, questions: dict, *, modelo: str | None = None, timeout: float = 12.0) -> dict | None:
    """Una llamada a /decision-model del gateway. `partes`: texto, dict JSON o lista de partes ({type: text|json|file}).
    Preguntas choice / score / noul / box (box = {type: "box", instructions}, sin criteria; Jev ≥ 1.14 y una imagen).
    → {answers, usage, ms, costo_usd, costo_gateway_usd, modelo} o None. Box: {type: "box", box: {top, bottom, left,
    right} en fracciones de la imagen, confidence}; noul: {type: "noul", noul}; choice/score como evaluate()."""
    modelo = modelo or MODELO_VISION
    if isinstance(partes, str):
        partes = [{"type": "text", "text": partes}]
    elif isinstance(partes, dict):
        partes = [{"type": "json", "value": partes}]
    t0 = time.perf_counter()
    try:
        llave = llave_gateway()
        if not llave:
            raise JevError("sin AI_GATEWAY_API_KEY")
        qs, noul = {}, set()
        for k, q in questions.items():
            if q["type"] == "box":
                qs[k] = {"type": "box", "instructions": q.get("instructions")}
            elif q["type"] == "noul":
                noul.add(k); qs[k] = {"type": "boolean", "instructions": q["instructions"]}
            else:
                qs[k] = {"type": q["type"], "instructions": q["instructions"], "criteria": q["criteria"]}
        r = _cliente().post(DECISION_URL, headers=cabeceras_gateway(llave, modelo, "decision"), timeout=timeout,
                            content=json.dumps({"stateParts": partes, "questions": qs}, ensure_ascii=False).encode())
        if r.status_code != 200:
            raise JevError(f"gateway {r.status_code}: {r.text[:200]}")
        cuerpo = r.json()
        conf = ((cuerpo.get("providerMetadata") or {}).get("typesafe") or {}).get("confidence") or {}
        answers = {}
        for k, a in (cuerpo.get("answers") or {}).items():
            tipo = a.get("type")
            if tipo == "box":
                answers[k] = {"type": "box", "box": {e: float(a["box"][e]) for e in ("top", "bottom", "left", "right")},
                              "confidence": float(conf.get(k, 0.0))}
            elif tipo == "boolean" or k in noul:
                answers[k] = {"type": "noul", "noul": float(a.get("probability", 0.0))}
            elif tipo == "refusal":
                answers[k] = {"type": "refusal"}
            else:
                probs = {str(o): float(p) for o, p in (a.get("probabilities") or {}).items()}
                if tipo == "score":
                    answers[k] = {"type": "score", "score": float(a["score"]), "confidence": float(conf.get(k, max(probs.values(), default=1.0))),
                                  "probabilities": probs}
                else:
                    answers[k] = {"type": "choice", "choice": a.get("choice"), "confidence": probs.get(str(a.get("choice")), 1.0),
                                  "probabilities": probs}
        faltan = [k for k in questions if k not in answers]
        if faltan:
            raise JevError(f"respuesta sin {faltan[:3]}")
        u = cuerpo.get("usage") or {}
        res = {"answers": answers, "usage": {"input_tokens": int(u.get("inputTokens") or 0), "output_tokens": int(u.get("outputTokens") or 0)},
               "ruta": "gateway", "modelo": cuerpo.get("model") or modelo, "costo_gateway_usd": _costo_gateway(cuerpo),
               "ms": round((time.perf_counter() - t0) * 1000.0, 1)}
    except Exception as e:  # noqa: BLE001
        _contar_falla(f"decide: {e}"[:300])
        return None
    _contar(res)
    return res


def stats() -> dict:
    with _lock:
        s = dict(_stats); ms = sorted(s.pop("ms_ultimas"))
    s["ms_p50"] = ms[len(ms) // 2] if ms else None
    s["ms_p95"] = ms[min(len(ms) - 1, int(len(ms) * 0.95))] if ms else None
    s["ms_total"] = round(s["ms_total"], 1); s["costo_usd"] = round(s["costo_usd"], 7)
    if "costo_gateway_usd" in s:
        s["costo_gateway_usd"] = round(s["costo_gateway_usd"], 7)
    s.update(modelo=MODELO, modelo_vision=MODELO_VISION, ruta=ruta_http(), disponible=disponible())
    return s


# ───────────────────────── waterfall de emoji (3 niveles) ─────────────────────────

_arbol: dict | None = None


def arbol_emoji() -> dict:
    global _arbol
    if _arbol is None:
        _arbol = json.loads(ARBOL_EMOJI.read_text())
    return _arbol


def _es_nodo(c: dict) -> bool:
    return isinstance(c.get("children"), list)


def _visibles(nodo: dict) -> list[dict]:
    return [c for c in nodo["children"] if not c.get("off")]          # sin banderas (off), como en remotion-live


def _palabras(s: str, n: int = 6) -> str:
    return " ".join(str(s).split()[:n])


def _muestra(hijos: list[dict], n: int) -> list[dict]:
    """n hijos repartidos parejo (no solo los primeros: «computer» empieza por battery, low-battery, electric-plug…)."""
    if len(hijos) <= n:
        return hijos
    return [hijos[round(i * (len(hijos) - 1) / (n - 1))] for i in range(n)]


def _desc(c: dict) -> str:
    """Descripción de una opción. Más larga que en remotion-live (≤ 6 palabras, pensada para 2-3 Hz en vivo): aquí es
    una vez por etiqueta y el costo es despreciable, y con los ejemplos Jev no confunde «cable» con «otros objetos».
    Grupo = sus subgrupos; subgrupo = 10 emoji de muestra en inglés; hoja = emoji + nombre en inglés + glosa en español."""
    if _es_nodo(c):
        hijos = _visibles(c)
        if hijos and _es_nodo(hijos[0]):                            # grupo (nivel 0): lista de subgrupos
            return f"{c.get('desc') or c['name']}: " + ", ".join(h["name"].replace("-", " ") for h in hijos)
        ej = ", ".join(h["name"].replace("-", " ") for h in _muestra(hijos, 10))
        return f"{c['name'].replace('-', ' ')}: {ej}"
    en = c["name"].replace("-", " ")
    es = c.get("es") or ""
    return f"{c['id']} {en}" + (f" · {_palabras(es, 4)}" if es and es.lower() != en else "")


INSTR_EMOJI = (
    "The state names (in Spanish) a physical object lying on a desk, seen from above. Pick the emoji group that holds an "
    "emoji of that object itself, literally (the thing, not a mood). If the name is only a color or a vague 'objeto', "
    "pick symbols (a shape of that color).",
    "Emoji in {ruta}: pick the subgroup that holds an emoji of the object itself.",
    "Emoji in {ruta}: pick the emoji that depicts the object itself, literally; if none is exact, the closest-looking one.",
)


HAZ_ACUM, HAZ_MAX, MAX_OPCIONES = 0.75, 3, 250


def _cascada(estado: str, extra: dict | None = None, timeout: float = TIMEOUT_S) -> dict | None:
    """Grupo → subgrupo → emoji, una Choice por llamada (3 llamadas). Las preguntas `extra` viajan en la PRIMERA
    llamada (sin costo de latencia). Si un nivel sale dudoso, el siguiente junta los hijos de las mejores ramas hasta
    sumar HAZ_ACUM de probabilidad (máx. HAZ_MAX ramas y MAX_OPCIONES opciones): así «cable» no queda atrapado en
    phone cuando computer (🔌) estaba casi empatado, y sigue siendo una llamada por nivel.
    None si falla la primera llamada; si falla una posterior, sale sin emoji."""
    raiz = arbol_emoji()["root"]
    ramas: list[tuple[dict, list[str]]] = [(raiz, [])]          # (nodo, camino) que se abren en este nivel
    camino_final, confs = [], []
    res = {"emoji": None, "llamadas": 0, "ms": 0.0, "tokens_in": 0, "costo_usd": 0.0, "extra": {}, "emoji_haz": []}
    for nivel in range(3):
        opciones: dict[str, tuple[dict, list[str]]] = {}
        for nodo, cam in ramas:
            for c in _visibles(nodo):
                if c["name"] not in opciones and len(opciones) < MAX_OPCIONES:
                    opciones[c["name"]] = (c, cam + [c["name"]])
        donde = " or ".join(" > ".join(cam) for _, cam in ramas)
        preguntas = {"emoji": {"type": "choice", "instructions": INSTR_EMOJI[nivel].format(ruta=donde),
                               "criteria": {k: _desc(c) for k, (c, _) in opciones.items()}}}
        if nivel == 0 and extra:
            preguntas.update(extra)
        r = evaluate(estado, preguntas, timeout=timeout)
        if r is None:
            if nivel == 0:
                return None
            break
        res["llamadas"] += 1; res["ms"] += r["ms"]; res["tokens_in"] += r["usage"]["input_tokens"]; res["costo_usd"] += r["costo_usd"]
        if nivel == 0 and extra:
            res["extra"] = {k: r["answers"][k] for k in extra}
        a = r["answers"]["emoji"]
        if a["choice"] not in opciones:
            break
        hijo, camino_final = opciones[a["choice"]]
        confs.append(round(float(a.get("confidence", 0.0)), 3))
        if not _es_nodo(hijo):
            res.update(emoji=hijo["id"], emoji_nombre=hijo["name"], emoji_es=hijo.get("es"))
            break
        # haz: la elegida primero y luego las siguientes ramas por probabilidad hasta HAZ_ACUM
        orden = sorted(a.get("probabilities", {}).items(), key=lambda kv: -kv[1])
        ramas, acum = [(hijo, camino_final)], float(a.get("probabilities", {}).get(hijo["name"], 1.0))
        for nombre, p in orden:
            if acum >= HAZ_ACUM or len(ramas) >= HAZ_MAX:
                break
            c_ = opciones.get(nombre)
            if c_ is None or nombre == hijo["name"] or not _es_nodo(c_[0]):
                continue
            ramas.append(c_); acum += p
        res["emoji_haz"].append("|".join(cam[-1] for _, cam in ramas))
    res.update(emoji_ruta=camino_final, emoji_conf=confs, ms=round(res["ms"], 1))
    return res


def waterfall_emoji(descripcion: str, *, timeout: float = TIMEOUT_S) -> dict | None:
    """Emoji para un objeto descrito en texto: 3 llamadas (grupo, subgrupo, emoji) sobre el árbol de 1.914 emoji.
    → {emoji, emoji_nombre, emoji_es, emoji_ruta, emoji_conf, emoji_haz, llamadas, ms, tokens_in, costo_usd} o None."""
    r = _cascada(descripcion, None, timeout)
    if r is not None:
        r.pop("extra", None)
    return r


# ───────────────────────── ruta de efecto e intensidad ─────────────────────────

# modos de la capa catálogo / feed de la mesa (catalogo_anim + superficie_feed)
MODOS_CATALOGO = {
    "patron_emoji": "its emoji repeated as a moving pattern inside",
    "aristas": "only a glowing outline along its edges",
    "pantalla_virtual": "a virtual screen shown on its flat face",
    "aura": "a soft aura of light around it",
    "nada": "project nothing on it, leave it dark",
}
# rutas P4 de slscan.object_semantics.CATALOG
DESC_RUTAS_P4 = {
    "surface_light": "plain light washing over its surface",
    "flat_texture": "a flat image mapped on its face",
    "canvas": "a flat canvas texture painted on it",
    "page_texture": "a paper page texture on it",
    "foliage": "light following its leaves",
    "relief": "relief light on a textured surface",
    "ceramic_glaze": "a ceramic glaze shimmer",
    "fabric_wave": "a waving fabric texture",
}
# etiqueta (español, como las da el agente de visión) → tipo de object_semantics.TYPES
TIPO_POR_PALABRA = (("hoja", "paper"), ("papel", "paper"), ("nota", "paper"), ("libreta", "book"), ("cuaderno", "book"),
                    ("libro", "book"), ("revista", "book"), ("laptop", "screen"), ("notebook", "screen"), ("monitor", "screen"),
                    ("pantalla", "screen"), ("teléfono", "screen"), ("telefono", "screen"), ("celular", "screen"),
                    ("tablet", "screen"), ("planta", "plant"), ("florero", "vase"), ("jarrón", "vase"), ("cojín", "cushion"))
# la mesa es un plano calibrado (lienzo → proyector, homografía): se puede texturizar y es plana; sin normales por cara
CAPACIDADES_MESA = ("mesh", "textured", "planar")


def opciones_ruta(etiqueta: str = "", capacidades=CAPACIDADES_MESA) -> dict[str, str]:
    """Rutas de `object_semantics.routes()` para el tipo que sugiere la etiqueta (filtradas por capacidades) más los
    modos de la capa catálogo. {id: descripción corta}."""
    out: dict[str, str] = {}
    try:
        from slscan import object_semantics as osem
        l_ = etiqueta.lower()
        tipo = next((t for p, t in TIPO_POR_PALABRA if p in l_), "unknown")
        sem = osem.describe({"type": tipo, "confidence": 0.7 if tipo != "unknown" else 0.0}, source="etiqueta")
        for r in osem.routes(sem, capacidades):
            out[r["effect"]] = DESC_RUTAS_P4.get(r["effect"], r["operation"].replace("_", " "))
    except Exception:  # noqa: BLE001                      # sin slscan: solo los modos de la capa
        out["surface_light"] = DESC_RUTAS_P4["surface_light"]
    out.update(MODOS_CATALOGO)
    return out


def _pregunta_ruta(opciones) -> dict:
    if not isinstance(opciones, dict):
        todas = {**DESC_RUTAS_P4, **MODOS_CATALOGO}
        opciones = {o: todas.get(o, o.replace("_", " ")) for o in opciones}
    return {"type": "choice", "criteria": dict(opciones),
            "instructions": "Which light effect projected on this desk object looks best and stays useful? Do not cover "
                            "what a person is using, reading or touching (screens, hands near)."}


NIVELES_INTENSIDAD = ["off, nothing", "very faint", "soft", "medium", "strong and saturated"]


def _pregunta_intensidad() -> dict:
    return {"type": "score", "criteria": NIVELES_INTENSIDAD,
            "instructions": "How strong should the light projected onto this object be? Softer when hands are near or the "
                            "object is a screen or something to read; stronger for idle matte objects that look good lit."}


def _ruta_de(a: dict) -> dict:
    return {"ruta": a["choice"], "ruta_conf": round(float(a.get("confidence", 0.0)), 3),
            "ruta_probs": {k: round(v, 3) for k, v in sorted(a.get("probabilities", {}).items(), key=lambda kv: -kv[1])[:5]}}


def _intensidad_de(a: dict) -> dict:
    n = len(NIVELES_INTENSIDAD) - 1
    return {"intensidad": round(float(a["score"]) / n, 3), "intensidad_nivel": NIVELES_INTENSIDAD[int(round(float(a["score"])))],
            "intensidad_probs": {NIVELES_INTENSIDAD[int(k)]: round(v, 3) for k, v in a.get("probabilities", {}).items()}}


def ruta_efecto(descripcion: str, opciones=None, *, timeout: float = TIMEOUT_S) -> dict | None:
    """Choice entre rutas de efecto (dict {id: desc} o lista de ids; por defecto `opciones_ruta()`).
    → {ruta, ruta_conf, ruta_probs} o None."""
    r = evaluate(descripcion, {"ruta": _pregunta_ruta(opciones or opciones_ruta())}, timeout=timeout)
    return None if r is None else _ruta_de(r["answers"]["ruta"])


def intensidad(descripcion: str, *, timeout: float = TIMEOUT_S) -> dict | None:
    """Score 0-1 de cuánta luz proyectar sobre el objeto. → {intensidad, intensidad_nivel, intensidad_probs} o None."""
    r = evaluate(descripcion, {"intensidad": _pregunta_intensidad()}, timeout=timeout)
    return None if r is None else _intensidad_de(r["answers"]["intensidad"])


def decidir_objeto(descripcion: str, opciones=None, *, timeout: float = TIMEOUT_S) -> dict | None:
    """Emoji + ruta + intensidad en 3 llamadas (ruta e intensidad viajan con el primer nivel del waterfall)."""
    r = _cascada(descripcion, {"ruta": _pregunta_ruta(opciones or opciones_ruta()), "intensidad": _pregunta_intensidad()}, timeout)
    if r is None:
        return None
    ex = r.pop("extra")
    r.update(_ruta_de(ex["ruta"])); r.update(_intensidad_de(ex["intensidad"]))
    return r


# ───────────────────────── estado de texto de un ítem del catálogo ─────────────────────────

def _gap_mm(a, b, mmpx: float) -> float:
    dx = max(0, max(a[0] - b[2], b[0] - a[2])); dy = max(0, max(a[1] - b[3], b[1] - a[3]))
    return math.hypot(dx, dy) * mmpx


def describir_item(it: dict, mm_por_px: float, lienzo_wh: tuple[int, int], manos: list | None = None,
                   cerca_mm: float = 60.0) -> str:
    """Texto que ve Jev: nombre, tamaño en mm, posición en la mesa, z-index y si hay manos cerca."""
    x0, y0, x1, y1 = it["bbox"]; lw, lh = lienzo_wh; mmpx = float(mm_por_px or 0.0)
    cx, cy = (x0 + x1) / 2 / lw, (y0 + y1) / 2 / lh
    zona = (["izquierda", "centro", "derecha"][min(2, int(cx * 3))] + ", "
            + ["arriba", "al medio", "abajo"][min(2, int(cy * 3))] + " del área proyectable")
    cerca = any(_gap_mm(it["bbox"], m, mmpx) <= cerca_mm for m in (manos or []))
    tam = (f"Tamaño aprox. {(x1 - x0) * mmpx:.0f} × {(y1 - y0) * mmpx:.0f} mm (área {it.get('area_px', 0) * mmpx * mmpx:.0f} mm²). "
           if mmpx > 0 else "")
    return (f"Objeto sobre una mesa de trabajo, visto desde arriba por una cámara: «{it['label']}». {tam}"
            f"Posición: {zona} (x {cx:.2f}, y {cy:.2f}). "
            f"Capa z {it.get('z', '?')} (0 = mesa, 2 = soportes como teclado u hoja, 3-5 = objetos encima, 10 = manos). "
            f"Manos cerca: {'sí' if cerca else 'no'}.")


# ───────────────────────── caché por etiqueta + catálogo completo ─────────────────────────

class Cache:
    """Caché en disco por etiqueta (captures/<scan>/jev_cache.json): una etiqueta ya decidida no vuelve a llamar."""

    def __init__(self, ruta: Path):
        self.ruta = Path(ruta)
        self._lock = threading.Lock()
        self.datos: dict[str, dict] = {}
        try:
            self.datos = json.loads(self.ruta.read_text()).get("etiquetas", {})
        except (OSError, ValueError, AttributeError):
            pass

    @staticmethod
    def clave(etiqueta: str) -> str:
        return " ".join(str(etiqueta).replace("_", " ").lower().split())

    def get(self, etiqueta: str) -> dict | None:
        return self.datos.get(self.clave(etiqueta))

    def emoji(self, etiqueta: str) -> str | None:
        v = self.datos.get(self.clave(etiqueta))
        return v.get("emoji") if v else None

    def put(self, etiqueta: str, valor: dict) -> None:
        with self._lock:
            self.datos[self.clave(etiqueta)] = valor
            tmp = self.ruta.with_name(self.ruta.name + ".tmp")
            tmp.write_text(json.dumps({"version": 1, "modelo": MODELO, "etiquetas": self.datos}, ensure_ascii=False, indent=1))
            os.replace(tmp, self.ruta)

    def __len__(self) -> int:
        return len(self.datos)


def _manos_de(dir_: Path) -> list:
    """Cajas de manos (px de lienzo) que dejó la segmentación en esa carpeta (SAM + MediaPipe en catalog.json)."""
    out = []
    for nombre in ("catalog.json", "catalog_refinado.json"):
        try:
            out += [it["bbox"] for it in json.loads((dir_ / nombre).read_text())["items"] if it.get("label") == "mano"]
        except (OSError, ValueError, KeyError):
            pass
    return out


def consultar_catalogo(dir_: Path, cache: Cache, mm_por_px: float, lienzo_wh: tuple[int, int], *, hilos: int = 4,
                       forzar: bool = False, timeout: float = TIMEOUT_S) -> dict:
    """Lee catalog_refinado.json de `dir_`, pregunta a Jev por cada etiqueta que no esté en la caché (en paralelo,
    `hilos` a la vez) y escribe jev.json en la misma carpeta. Devuelve ese contenido; `nuevas` = respuestas nuevas."""
    dir_ = Path(dir_)
    cat = json.loads((dir_ / "catalog_refinado.json").read_text())
    manos = _manos_de(dir_)
    t0 = time.perf_counter()
    por_etiqueta: dict[str, tuple[dict, str]] = {}
    for it in cat["items"]:
        por_etiqueta.setdefault(Cache.clave(it["label"]), (it, describir_item(it, mm_por_px, lienzo_wh, manos)))
    pendientes = {k: v for k, v in por_etiqueta.items() if forzar or cache.get(k) is None}
    resp: dict[str, dict | None] = {}
    if pendientes:
        with ThreadPoolExecutor(max_workers=max(1, hilos)) as ex:
            futs = {k: ex.submit(decidir_objeto, est, opciones_ruta(it["label"]), timeout=timeout) for k, (it, est) in pendientes.items()}
            resp = {k: f.result() for k, f in futs.items()}
    llam = tok = 0; ms = 0.0; usd = 0.0; nuevas = 0
    for k, r in resp.items():
        if r is None:
            continue
        llam += r["llamadas"]; tok += r["tokens_in"]; ms += r["ms"]; usd += r["costo_usd"]
        if r.get("emoji"):
            nuevas += 1
            cache.put(k, {**r, "etiqueta": por_etiqueta[k][0]["label"], "estado": por_etiqueta[k][1], "t": time.strftime("%Y-%m-%d %H:%M:%S")})
    items = []
    for it in cat["items"]:
        k = Cache.clave(it["label"])
        r = resp.get(k) if k in resp else cache.get(k)
        fila = {"id": it.get("id"), "label": it["label"], "fuente": "falla" if r is None else ("jev" if k in resp else "cache")}
        if r is not None:
            fila.update({c: r.get(c) for c in ("emoji", "emoji_nombre", "emoji_es", "emoji_ruta", "emoji_conf", "emoji_haz", "ruta", "ruta_conf",
                                               "ruta_probs", "intensidad", "intensidad_nivel", "intensidad_probs")})
        fila["estado"] = por_etiqueta[k][1]
        items.append(fila)
    out = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "dir": str(dir_), "modelo": MODELO, "ruta_http": ruta_http(),
           "etiquetas": len(por_etiqueta), "consultadas": len(pendientes), "nuevas": nuevas, "fallas": sum(r is None for r in resp.values()),
           "llamadas": llam, "tokens_in": tok, "ms_llamadas": round(ms, 1), "ms_por_llamada": round(ms / llam, 1) if llam else None,
           "pared_s": round(time.perf_counter() - t0, 2), "costo_usd": round(usd, 7), "error": stats()["ultimo_error"] if any(r is None for r in resp.values()) else None,
           "items": items}
    tmp = dir_ / "jev.json.tmp"
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1)); os.replace(tmp, dir_ / "jev.json")
    return out


if __name__ == "__main__":
    texto = " ".join(sys.argv[1:]) or "teléfono"
    if not disponible():
        print("Jev no disponible: falta AI_GATEWAY_API_KEY (entorno o JEV_ENV_FILE)"); sys.exit(1)
    est = describir_item({"label": texto, "bbox": [800, 400, 1100, 560], "area_px": 40000, "z": 3}, 0.3947, (1920, 1080))
    print(est)
    print(json.dumps(decidir_objeto(est, opciones_ruta(texto)), ensure_ascii=False, indent=1))
    print(json.dumps(stats(), ensure_ascii=False))

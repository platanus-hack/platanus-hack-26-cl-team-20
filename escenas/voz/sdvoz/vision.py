"""La voz «ve» con JEV (engine/playground/jev.py): preguntas tipadas sobre la foto de la mesa en ~1 s.

El LLM de ElevenLabs no recibe imágenes por una client tool. Para lo que hay que entender en vivo (qué señala la
persona, si el vaso se ve rebalsado, si hay tinta nueva), la voz pregunta y JEV contesta sobre la foto de `look`:
elige entre opciones (choice) o dice sí/no con su probabilidad (noul).

La llave (AI_GATEWAY_API_KEY) la lee jev.py por referencia y queda solo en la memoria de este proceso. La foto va al
gateway de Vercel con el modelo versionado de retención cero (`typesafe-ai/jev-1.14.0`), por pedido de Max (21-nov).
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import os
from functools import lru_cache
from pathlib import Path

from .escenas import REPO

LADO_MIN, LADO_MAX, PIXELES_MAX = 280, 1568, 2_400_000


@lru_cache(maxsize=1)
def jev():
    ruta = Path(os.environ.get("SD_JEV", REPO / "engine" / "playground" / "jev.py"))
    if not ruta.exists():
        return None
    spec = importlib.util.spec_from_file_location("sdvoz_jev", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def disponible() -> bool:
    j = jev()
    return bool(j and j.disponible())


def ajustar(jpg: bytes) -> bytes:
    """Deja la foto dentro de los límites de JEV (cada lado 280-1568 px, ≤ 2,4 MP) sin deformarla."""
    from PIL import Image

    im = Image.open(io.BytesIO(jpg))
    w, h = im.size
    f = min(1.0, LADO_MAX / max(w, h), (PIXELES_MAX / (w * h)) ** 0.5)
    if min(w, h) * f < LADO_MIN:
        f = LADO_MIN / min(w, h)
    if abs(f - 1.0) < 1e-3 and im.format == "JPEG":
        return jpg
    im = im.convert("RGB").resize((max(LADO_MIN, round(w * f)), max(LADO_MIN, round(h * f))))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85)
    return out.getvalue()


def foto_de(resultado_look: dict) -> bytes | None:
    for c in resultado_look.get("content", []):
        if c.get("type") == "image" and c.get("data"):
            return base64.b64decode(c["data"])
    return None


async def preguntar(jpg: bytes, pregunta: str, opciones: list[str] | None, estado: dict) -> dict | None:
    """→ {"choice", "confidence"} o {"si": prob} y "ms"; None si JEV no está o falló."""
    j = jev()
    if not j or not j.disponible():
        return None
    partes = [j.parte_imagen(ajustar(jpg), referencia="[mesa-1]"), {"type": "json", "value": {"mesa": "[mesa-1]", **estado}}]
    if opciones:
        q = {"r": {"type": "choice", "instructions": pregunta, "criteria": {o: o for o in opciones}}}
    else:
        q = {"r": {"type": "noul", "instructions": pregunta}}
    r = await asyncio.to_thread(j.decide, partes, q)
    if not r:
        return None
    a = r["answers"]["r"]
    if a.get("type") == "choice":
        return {"choice": a.get("choice"), "confidence": round(a.get("confidence", 0.0), 2), "ms": r.get("ms")}
    if a.get("type") == "noul":
        return {"si": round(a.get("noul", 0.0), 2), "ms": r.get("ms")}
    return None

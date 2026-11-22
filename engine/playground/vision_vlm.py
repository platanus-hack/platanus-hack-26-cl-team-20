"""Agente de visión (Sonnet por el CLI de `claude`, sesión de Max, sin manejar llaves): recibe el lienzo rectificado y
devuelve qué objetos hay y dónde (cajas en px del lienzo), para que MobileSAM los segmente con prompts de caja en vez
de una grilla ciega. Pensado para correr cada ~30 s en un hilo aparte.

    python -m playground.vision_vlm <imagen.jpg> [--model sonnet] [--timeout 90]

Salida: JSON {"objetos": [{"nombre": "mouse", "caja": [x0, y0, x1, y1], "centro": [x, y], "confianza": 0.9}, …],
"segundos": t}. El CLI corre con `--allowedTools Read` para que lea la imagen; nada más.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

PROMPT = (
    "Lee la imagen {path} con la herramienta Read. Es la vista cenital RECTIFICADA de un escritorio ({w}x{h} px; el usuario "
    "está sentado arriba, así que lo que ves está al revés para él). Enumera TODOS los objetos físicos visibles (manos, hojas o "
    "libretas, mouse, teclado, moledor de hierba y sus mitades, vape, lápiz o lapicera, taza, cables, teléfono, control, etc.), "
    "uno por instancia. Responde SOLO con JSON válido, sin texto alrededor, con esta forma: "
    '{{"objetos": [{{"nombre": "<nombre corto en español, minúsculas>", "caja": [x0, y0, x1, y1], "centro": [x, y], '
    '"confianza": 0.0-1.0}}]}}. Las coordenadas son píxeles de la imagen (origen arriba a la izquierda). Cajas ajustadas al '
    "objeto, sin margen. Si dudas entre dos nombres usa el más genérico."
)


def describir(path: str | Path, model: str = "sonnet", timeout: float = 90.0, size: tuple[int, int] | None = None) -> dict:
    p = Path(path).resolve()
    if size is None:
        try:
            import cv2
            im = cv2.imread(str(p)); size = (im.shape[1], im.shape[0])
        except Exception:  # noqa: BLE001
            size = (1920, 1126)
    prompt = PROMPT.format(path=str(p), w=size[0], h=size[1])
    t0 = time.time()
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json", "--allowedTools", "Read", "--max-turns", "3"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(p.parent))
    except subprocess.TimeoutExpired:
        return {"objetos": [], "error": f"timeout {timeout}s", "segundos": round(time.time() - t0, 1)}
    out = r.stdout.strip()
    texto = out
    try:
        env = json.loads(out)
        texto = env.get("result", out) if isinstance(env, dict) else out
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", texto, re.S)
    objetos = []
    if m:
        try:
            objetos = json.loads(m.group(0)).get("objetos", [])
        except json.JSONDecodeError:
            pass
    limpio = []
    for o in objetos:
        try:
            x0, y0, x1, y1 = (float(v) for v in o["caja"])
            x0, x1 = sorted((max(0, x0), min(size[0], x1))); y0, y1 = sorted((max(0, y0), min(size[1], y1)))
            if x1 - x0 < 8 or y1 - y0 < 8:
                continue
            limpio.append({"nombre": str(o.get("nombre", "objeto")).strip().lower()[:40], "caja": [round(x0), round(y0), round(x1), round(y1)],
                           "centro": [round((x0 + x1) / 2), round((y0 + y1) / 2)], "confianza": float(o.get("confianza", 0.5))})
        except (KeyError, TypeError, ValueError):
            continue
    return {"objetos": limpio, "segundos": round(time.time() - t0, 1), "rc": r.returncode, "stderr": r.stderr.strip()[-200:] if r.returncode else ""}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("imagen"); ap.add_argument("--model", default="sonnet"); ap.add_argument("--timeout", type=float, default=90)
    a = ap.parse_args(argv)
    print(json.dumps(describir(a.imagen, a.model, a.timeout), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

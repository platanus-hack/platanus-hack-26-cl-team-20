"""Cliente mínimo de Jev (modelo de decisión de TypeSafe por el AI Gateway de Vercel). Mismo contrato que escenas/escritorio/jev.py
de Max. La llave viene de AI_GATEWAY_API_KEY (lanzar con `doppler run -p platanus-hack-26 -c dev -- ...`). No se imprime."""
import base64, json, os, time, urllib.request
import cv2

URL = "https://ai-gateway.vercel.sh/v4/ai/decision-model"; MODELO = os.environ.get("JEV_VISION_MODEL", "typesafe-ai/jev-1.14.0")


def imagen(img, calidad=88):
    h, w = img.shape[:2]; f = min(1568 / max(h, w), max(1.0, 280 / min(h, w)))
    if f != 1.0: img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)
    return {"type": "file", "mediaType": "image/jpeg", "data": {"type": "data", "data": base64.b64encode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, calidad])[1].tobytes()).decode("ascii")}}


def decide(partes, preguntas, timeout=8.0):
    k = os.environ.get("AI_GATEWAY_API_KEY", "").strip()
    if not k: return {"error": "sin llave"}
    t0 = time.perf_counter()
    cab = {"Authorization": f"Bearer {k}", "ai-gateway-protocol-version": "0.0.1", "ai-gateway-auth-method": "api-key", "ai-decision-model-specification-version": "4",
           "ai-model-id": MODELO, "content-type": "application/json", "user-agent": "wui-mesa-v2/1"}
    try:
        with urllib.request.urlopen(urllib.request.Request(URL, data=json.dumps({"stateParts": partes, "questions": preguntas}, ensure_ascii=False).encode(), headers=cab, method="POST"), timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e: return {"error": f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}"}
    except Exception as e: return {"error": str(e)[:200]}
    out = {}
    for n, a in (data.get("answers") or {}).items():
        t = a.get("type")
        if t == "box": out[n] = a.get("box")
        elif t == "boolean": out[n] = a.get("probability")
        else: out[n] = {"choice": a.get("choice", a.get("score")), "p": (a.get("probabilities") or {}).get(str(a.get("choice")))}
    return {"answers": out, "ms": round((time.perf_counter() - t0) * 1000)}

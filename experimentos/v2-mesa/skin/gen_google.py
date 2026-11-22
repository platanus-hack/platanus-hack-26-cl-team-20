"""Edita la imagen normalizada con un modelo de imagen de Gemini via Vertex AI (token de gcloud; no imprime secretos).
uso: gen_google.py <estilo> [modelo] [entrada]   -> salidas/google-<estilo>.png (ya recortada a 2680x1420)
La entrada (1.887:1) se rellena a 16:9 replicando bordes (2680x1508); la salida se reescala y se recorta de vuelta."""
import os, base64, json, subprocess, sys, urllib.request, urllib.error, cv2, numpy as np
from pathlib import Path
D=Path(__file__).resolve().parent; E=sys.argv[1]; MODEL=sys.argv[2] if len(sys.argv)>2 else "gemini-3-pro-image"
IN=D/(sys.argv[3] if len(sys.argv)>3 else "entrada-norm.png")
im=cv2.imread(str(IN)); h,w=im.shape[:2]; H16=int(round(w*9/16/2))*2; pad=(H16-h)//2
p16=cv2.copyMakeBorder(im,pad,H16-h-pad,0,0,cv2.BORDER_REPLICATE); cv2.imwrite(str(D/"entrada-norm-16x9.png"),p16)
ok,buf=cv2.imencode(".png",p16)
tok=subprocess.run(["gcloud","auth","print-access-token"],capture_output=True,text=True).stdout.strip()
proj=subprocess.run(["gcloud","config","get-value","project"],capture_output=True,text=True).stdout.strip()
url=f"https://aiplatform.googleapis.com/v1/projects/{proj}/locations/global/publishers/google/models/{MODEL}:generateContent"
body={"contents":[{"role":"user","parts":[{"inlineData":{"mimeType":"image/png","data":base64.b64encode(buf.tobytes()).decode()}},{"text":(D/"prompts"/f"{E}.txt").read_text()}]}],
      "generationConfig":{"responseModalities":["TEXT","IMAGE"],"imageConfig":{"aspectRatio":"16:9"}}}
if "pro" in MODEL or "3.1" in MODEL or "banana" in MODEL: body["generationConfig"]["imageConfig"]["imageSize"]=os.environ.get("IMG_SIZE","2K")
req=urllib.request.Request(url,data=json.dumps(body).encode(),headers={"Authorization":"Bearer "+tok,"Content-Type":"application/json","x-goog-user-project":proj})
try: r=json.loads(urllib.request.urlopen(req,timeout=300).read())
except urllib.error.HTTPError as e: print("HTTP",e.code,e.read().decode()[:1500]); sys.exit(1)
for part in r.get("candidates",[{}])[0].get("content",{}).get("parts",[]):
    if "inlineData" in part:
        raw=base64.b64decode(part["inlineData"]["data"]); (D/"salidas"/f"google-{E}-cruda.png").write_bytes(raw)
        o=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR); print("cruda",o.shape,"modelo",MODEL)
        o=cv2.resize(o,(w,H16),interpolation=cv2.INTER_AREA if o.shape[1]>w else cv2.INTER_CUBIC)[pad:pad+h]
        cv2.imwrite(str(D/"salidas"/f"google-{E}.png"),o); print("ok",o.shape)
    elif "text" in part: print("texto:",part["text"][:300])
print("usage:",r.get("usageMetadata",{}).get("totalTokenCount"), "finish:",r.get("candidates",[{}])[0].get("finishReason"))

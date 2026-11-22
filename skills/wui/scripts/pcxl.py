#!/usr/bin/env python3
"""pcxl.py <tool> '<json args>' — llama una tool del MCP de WUI (servidor `pcx`) desde la línea de comandos.

Versión portable del cliente que usan los scripts de experimentos/v2-mesa (el original vive en un directorio temporal
de la máquina de origen). Solo biblioteca estándar.

  pcxl.py list                       lista las tools y su esquema
  pcxl.py scene '{}'                 llama una tool; imprime el texto y guarda las imágenes

Configuración (en este orden):
  WUI_MCP_URL    URL del MCP. Por defecto http://localhost:9217/mcp (gateway local).
  WUI_TOKEN      user_token de la sesión. Si no está, se lee el archivo `local-token` junto a este script
                 (los scripts de v2-mesa leen ese mismo archivo). No lo subas al repositorio.
  WUI_IMG_DIR    dónde guardar las imágenes de `look`. Por defecto, la carpeta de este script.
"""
import base64, json, os, re, sys, time, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
U = os.environ.get("WUI_MCP_URL", "http://localhost:9217/mcp")
IMG = os.environ.get("WUI_IMG_DIR", AQUI)


def token():
    t = os.environ.get("WUI_TOKEN", "").strip()
    if t: return t
    try: return open(os.path.join(AQUI, "local-token")).read().strip()
    except FileNotFoundError: sys.exit("pcxl: falta el token. Define WUI_TOKEN o crea el archivo local-token junto a pcxl.py")


def call(method, params, timeout=200):
    req = urllib.request.Request(U, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                 headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    t = urllib.request.urlopen(req, timeout=timeout).read().decode()
    return json.loads(re.search(r"\{.*\}", t, re.S).group(0))


if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(__doc__)
    if sys.argv[1] == "list":
        for x in call("tools/list", {})["result"]["tools"]:
            print(x["name"], "::", json.dumps(x.get("inputSchema", {}), ensure_ascii=False)[:900]); print("   ", (x.get("description") or "")[:400].replace("\n", " "))
    else:
        r = call("tools/call", {"name": sys.argv[1], "arguments": json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}})
        if "error" in r: print("ERROR", r["error"]); sys.exit(1)
        for i, c in enumerate(r["result"].get("content", [])):
            if c["type"] == "image":
                p = os.path.join(IMG, "look-%d-%d-%d.%s" % (time.time(), os.getpid(), i, c.get("mimeType", "image/jpeg").split("/")[-1]))
                open(p, "wb").write(base64.b64decode(c["data"])); print("IMAGE", p)
            else: print(c.get("text", "")[:400000])
        if r["result"].get("isError"): print("isError")

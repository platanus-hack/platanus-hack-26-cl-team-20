#!/usr/bin/env python3
"""pcx.py <tool> '<json args>' — llama una tool del MCP de pcx; guarda imágenes en el scratchpad."""
import sys, json, re, base64, urllib.request, time, os
U=os.environ.get("PCX_MCP_URL","https://pcx-gateway.onrender.com/mcp"); T=os.environ["PCX_USER_TOKEN"]  # el token sale del botón Copiar de la consola
def call(method, params, timeout=200):
    req=urllib.request.Request(U, data=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode(),
        headers={"Authorization":"Bearer "+T,"Content-Type":"application/json","Accept":"application/json, text/event-stream"})
    t=urllib.request.urlopen(req, timeout=timeout).read().decode()
    m=re.search(r'\{.*\}', t, re.S); return json.loads(m.group(0))
if sys.argv[1]=="list":
    for x in call("tools/list",{})["result"]["tools"]: print(x["name"], "::", json.dumps(x.get("inputSchema",{}),ensure_ascii=False)[:900]); print("   ", (x.get("description") or "")[:400].replace("\n"," "))
else:
    r=call("tools/call",{"name":sys.argv[1],"arguments":json.loads(sys.argv[2]) if len(sys.argv)>2 else {}})
    if "error" in r: print("ERROR", r["error"]); sys.exit(1)
    for i,c in enumerate(r["result"].get("content",[])):
        if c["type"]=="image":
            p=os.path.join(os.path.dirname(__file__), "look-%d-%d-%d.%s"%(time.time(),os.getpid(),i,c.get("mimeType","image/jpeg").split("/")[-1]))
            open(p,"wb").write(base64.b64decode(c["data"])); print("IMAGE", p)
        else: print(c.get("text","")[:400000])
    if r["result"].get("isError"): print("isError")

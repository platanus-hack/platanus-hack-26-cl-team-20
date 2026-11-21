#!/bin/bash
# Conecta Claude Code al proxy de tokens de Platanus Hack 26 en este MacBook y en el mini.
# Uso: copia tu URL del proxy (botón «Copiar» de la página) y corre: bash proxy-tokens.sh
#      Para deshacer: bash proxy-tokens.sh --quitar
set -e
PY='
import json,sys,os,shutil,time
p=os.path.expanduser("~/.claude/settings.json")
d=json.load(open(p)) if os.path.exists(p) else {}
if os.path.exists(p): shutil.copy2(p,p+".bak-proxy-"+time.strftime("%Y%m%d-%H%M%S"))
env=d.setdefault("env",{})
url=sys.stdin.read().strip()
if url: env["ANTHROPIC_BASE_URL"]=url
else: env.pop("ANTHROPIC_BASE_URL",None)
json.dump(d,open(p,"w"),indent=2,ensure_ascii=False); open(p,"a").write("\n")
print(os.uname().nodename+": "+("conectado al proxy" if url else "proxy quitado"))
'
if [ "$1" = "--quitar" ]; then
  echo -n "" | python3 -c "$PY"; echo -n "" | ssh mini-sat "python3 -c '$PY'"; exit 0
fi
U=$(pbpaste | tr -d '[:space:]')
if ! [[ "$U" =~ ^https://llm\.hack\.platan\.us/t/phk_[A-Za-z0-9_-]+$ ]]; then
  echo "En el portapapeles no hay una URL del proxy. Aprieta «Copiar» al lado de «Tu URL del proxy» y vuelve a correr."; exit 1
fi
echo -n "$U/anthropic" | python3 -c "$PY"
echo -n "$U/anthropic" | ssh mini-sat "python3 -c '$PY'"
echo "Listo. Reinicia las sesiones de claude para que tomen el cambio."

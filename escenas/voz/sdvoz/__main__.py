"""CLI de la voz de WUI.

    uv run python -m sdvoz publicar [--escena solar] [--dry-run]   # agentes y client tools en ElevenLabs
    uv run python -m sdvoz mesa-sim                                 # sesión nueva en el gateway local (para ?sim=1)
    uv run python -m sdvoz servir [--puerto 9325] [--voz]           # página + token + tools + oído de la mesa
    uv run python -m sdvoz probar solar "frase" "frase"             # conversación en modo texto (sin micrófono)
    uv run python -m sdvoz estado
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

from . import config as cfgmod


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sdvoz")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publicar")
    p.add_argument("--escena", action="append")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--forzar", action="store_true", help="pisa aunque el agente vivo sea de una versión más nueva")
    p = sub.add_parser("servir")
    p.add_argument("--puerto", type=int)
    p.add_argument("--voz", action="store_true", help="permite el modo voz (micrófono): solo con el OK de Max")
    p = sub.add_parser("mesa-sim")
    p.add_argument("--gateway")
    p = sub.add_parser("mesa")
    p.add_argument("--gateway")
    p.add_argument("--token-env", default="PCX_USER_TOKEN", help="variable con el token de la sesión (no se pega)")
    p.add_argument("--desde-archivo", help="archivo con el acceso de agente a la sesión (JSON, comando claude mcp add o "
                                           "texto con la URL /mcp y el Bearer); el token no se imprime")
    p = sub.add_parser("probar")
    p.add_argument("escena")
    p.add_argument("frases", nargs="*")
    sub.add_parser("estado")
    p = sub.add_parser("acceso")
    p.add_argument("escena")
    p.add_argument("modo", choices=["publico", "privado"])
    p.add_argument("--a-las", help="HH:MM: espera hasta esa hora antes de cambiarlo")
    p = sub.add_parser("frenos")
    p.add_argument("escena", nargs="?", default="solar")
    p.add_argument("--a-las", help="HH:MM: espera hasta esa hora antes de aplicarlos")
    p = sub.add_parser("sim-mesa")
    p.add_argument("--pestana", default="Sistema solar")
    a = ap.parse_args(argv)

    if a.cmd == "publicar":
        from . import agentes
        if a.dry_run:
            print(json.dumps(agentes.dry_run(a.escena), ensure_ascii=False, indent=2))
            return
        try:
            agentes.publish(a.escena, forzar=a.forzar)
        except agentes.ElevenLabsError as ex:
            sys.exit(f"✗ {ex}")
    elif a.cmd == "servir":
        import uvicorn
        from .servidor import crear_app
        cfg = cfgmod.load()
        uvicorn.run(crear_app(permitir_voz=a.voz), host="127.0.0.1", port=a.puerto or cfg["puerto"],
                    log_level="warning")
    elif a.cmd == "mesa-sim":
        gw = (a.gateway or cfgmod.gateway_url()).rstrip("/")
        s = httpx.post(gw + "/api/sessions", timeout=20).json()
        cfgmod.save({"gateway": gw, "pcx_user_token": s["user_token"]})
        enlace = f"{gw}/mesa?sim=1#s={s['session_id']}.{s['node_token']}.{s['user_token']}"
        destino = cfgmod.DIR / "mesa-sim.url"
        destino.write_text(enlace + "\n", encoding="utf-8")
        os.chmod(destino, 0o600)
        print(f"✓ sesión nueva en {gw} (token guardado en {cfgmod.ARCHIVO}, 600)")
        print(f"  Abre la consola simulada con el enlace de {destino} (lleva los tokens: no lo pegues en chats).")
    elif a.cmd == "mesa":
        if a.desde_archivo:
            from .acceso import leer_acceso
            gw, tok = leer_acceso(a.desde_archivo)
            gw = (a.gateway or gw or "").rstrip("/")
        else:
            gw, tok = (a.gateway or "").rstrip("/"), os.environ.get(a.token_env)
        if not gw or not tok:
            sys.exit("no encontré " + ("el gateway" if not gw else "el token") + " (no se imprime nada del archivo)")
        cfgmod.save({"gateway": gw, "pcx_user_token": tok})
        print(f"✓ la voz usa la mesa {gw} (token de {len(tok)} caracteres guardado en {cfgmod.ARCHIVO}, 600)")
        print("  Reinicia `servir` para que el oído y las tools usen esta sesión.")
    elif a.cmd == "probar":
        from .probar import probar
        probar(a.escena, a.frases)
    elif a.cmd == "sim-mesa":
        import asyncio
        from .mesa import Mesa
        from .sim import armar

        async def correr():
            m = Mesa(cfgmod.gateway_url(), cfgmod.session_token() or "")
            try:
                return await armar(m, a.pestana)
            finally:
                await m.aclose()
        print(json.dumps(asyncio.run(correr()), ensure_ascii=False))
    elif a.cmd == "acceso":
        from .acceso_publico import cambiar, cerrar_a_las
        if a.a_las:
            if a.modo != "privado":
                sys.exit("--a-las es solo para cerrar (privado)")
            r = cerrar_a_las(a.escena, a.a_las)
        else:
            r = cambiar(a.escena, a.modo == "publico")
        print(json.dumps(r, ensure_ascii=False))
    elif a.cmd == "frenos":
        from .acceso_publico import frenos
        print(json.dumps(frenos(a.escena, a.a_las), ensure_ascii=False), flush=True)
    elif a.cmd == "estado":
        cfg = cfgmod.load()
        print(json.dumps({"gateway": cfg["gateway"], "puerto": cfg["puerto"], "llm": cfg["llm"],
                          "agentes": cfg.get("agentes"), "herramientas": len(cfg.get("herramientas") or {}),
                          "sesion_mesa": bool(cfgmod.session_token(cfg)), "llave": cfgmod.api_key_source()},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

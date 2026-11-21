"""CLI del agente de voz «Playground».

Voz nativa en el Mac (modo local, por defecto):
  python -m playground.secretaria publicar [--dry-run]       crea o actualiza el agente (client tools, sin túnel)
  python -m playground.secretaria voz [--fondo|--parar]       conversación en vivo: micrófono y parlantes del MacBook
        [--duplex] [--dormir-tras 10] [--con-orquestador 90] [--sin-saludo]
  python -m playground.secretaria avance "texto" [--nivel cambio|trabajo] [--opciones "A|B"] [--pregunta q16]
  python -m playground.secretaria avance --orquestador       las notas nuevas de h3 en orq-creativo, a la cola
  python -m playground.secretaria estado                     qué está prendido

Vía túnel (opcional):
  python -m playground.secretaria up | down                  puente + túnel propio en segundo plano
  python -m playground.secretaria serve | tunel              cada uno en primer plano
  python -m playground.secretaria publicar --modo tunel [--url URL]
  python -m playground.secretaria probar <tool> ['{"json"}'] [--tunel]
  python -m playground.secretaria hablar                     página local (navegador) para hablar

  python -m playground.secretaria herramientas               el catálogo
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time

import httpx

from . import config as cfgmod

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _pidfile(name: str):
    return cfgmod.state_dir() / f"{name}.pid"


def _pid(name: str) -> int | None:
    try:
        pid = int(_pidfile(name).read_text().strip())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def _spawn(name: str, args: list[str]) -> int:
    cfgmod.state_dir().mkdir(parents=True, exist_ok=True)
    log = open(cfgmod.state_dir() / f"{name}.out.log", "ab")
    proc = subprocess.Popen([sys.executable, "-m", "playground.secretaria", *args], cwd=PROJECT,
                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True, close_fds=True)
    _pidfile(name).write_text(str(proc.pid))
    return proc.pid


def _stop(name: str) -> int | None:
    pid = _pid(name)
    if pid is None:
        _pidfile(name).unlink(missing_ok=True)
        return None
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(50):
        if _pid(name) is None:
            break
        time.sleep(0.1)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    _pidfile(name).unlink(missing_ok=True)
    return pid


def _local_ok(cfg) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{cfg['puerto']}{cfgmod.RUTA}/salud", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def cmd_up(a) -> int:
    cfg = cfgmod.load()
    if not _local_ok(cfg):
        _spawn("puente", ["serve"])
        for _ in range(60):
            if _local_ok(cfg):
                break
            time.sleep(0.25)
    print(f"puente: {'ok' if _local_ok(cfg) else 'NO responde (ver ' + str(cfgmod.state_dir() / 'puente.out.log') + ')'}")
    if _pid("tunel") is None:
        before = cfg.get("puente_url")
        mark = (cfgmod.state_dir() / "tunel.jsonl").stat().st_size if (cfgmod.state_dir() / "tunel.jsonl").exists() else 0
        _spawn("tunel", ["tunel"])
        for _ in range(120):                              # abrir + DNS + publicar: hasta ~2 min
            time.sleep(1)
            path = cfgmod.state_dir() / "tunel.jsonl"
            new = path.read_text(encoding="utf-8")[mark:] if path.exists() else ""
            if "republicado" in new or "publicar falló" in new or "no se republicó" in new or "no entregó" in new:
                break
            if _pid("tunel") is None:
                break
        for line in new.splitlines():
            try:
                print(json.loads(line)["linea"])
            except (ValueError, KeyError):
                pass
        if cfgmod.load().get("puente_url") == before and before:
            print("ojo: la URL del túnel no cambió (¿falló al abrir?)")
    else:
        print(f"túnel: ya corría ({cfgmod.mask_url(cfg.get('puente_url'))})")
    return 0


def cmd_down(a) -> int:
    for name in ("tunel", "puente"):
        pid = _stop(name)
        print(f"{name}: {'apagado (pid ' + str(pid) + ')' if pid else 'no corría'}")
    return 0


def cmd_estado(a) -> int:
    cfg = cfgmod.load()
    print(f"puente local :{cfg['puerto']}: {'ok' if _local_ok(cfg) else 'apagado'} · página :{cfg['puerto_app']}")
    print(f"túnel ({(cfg.get('tunel') or {}).get('modo', 'quick')}): {'corriendo' if _pid('tunel') else 'apagado'} · "
          f"{cfgmod.mask_url(cfg.get('puente_url')) if not a.mostrar_url else cfg.get('puente_url')}")
    print(f"agente: {cfg.get('nombre_agente')} · {cfg.get('agente_id') or 'sin publicar'} · "
          f"{len(cfg.get('herramientas_ids') or {})} webhooks (vía túnel)")
    voz_pid = _pid("voz")
    print(f"modo: {cfg.get('modo')} · voz en vivo: {'corriendo (pid ' + str(voz_pid) + ')' if voz_pid else 'apagada'} · "
          f"{len(cfg.get('herramientas_cliente_ids') or {})} client tools")
    print(f"llave ElevenLabs: {cfgmod.api_key_source(cfg)} · token del puente: {'sí' if cfgmod.token() else 'falta'}")
    try:
        ok = httpx.get(f"{cfg['runtime_url']}/api/schema", timeout=2).status_code == 200
    except httpx.HTTPError:
        ok = False
    print(f"runtime {cfg['runtime_url']}: {'prendido' if ok else 'apagado (las herramientas lo levantan solas)'}")
    return 0


def cmd_publicar(a) -> int:
    from .agent import dry_run, publish, ElevenLabsError
    if a.dry_run:
        print(json.dumps(dry_run(a.url, modo=a.modo), ensure_ascii=False, indent=2))
        return 0
    try:
        publish(a.url or "", modo=a.modo)
    except ElevenLabsError as e:
        print(f"publicar: {e}")
        return 1
    return 0


def cmd_voz(a) -> int:
    if a.parar:
        pid = _stop("voz")
        print(f"voz: {'apagada (pid ' + str(pid) + ')' if pid else 'no corría'}")
        return 0
    if a.fondo:
        if _pid("voz"):
            print(f"voz: ya corre (pid {_pid('voz')})")
            return 0
        args = ["voz", "--dormir-tras", str(a.dormir_tras), "--con-orquestador", str(a.con_orquestador)]
        args += ["--duplex"] if a.duplex else []
        args += ["--sin-saludo"] if a.sin_saludo else []
        pid = _spawn("voz", args)
        print(f"voz en segundo plano (pid {pid}); lo que pasa: {cfgmod.state_dir() / 'voz.jsonl'}")
        return 0
    from .voz import main as voz_main
    _pidfile("voz").parent.mkdir(parents=True, exist_ok=True)
    if not _pid("voz"):
        _pidfile("voz").write_text(str(os.getpid()))
    try:
        return voz_main(duplex=a.duplex, dormir_min=a.dormir_tras, orq_cada_s=a.con_orquestador, saludo=not a.sin_saludo)
    finally:
        if _pid("voz") == os.getpid():
            _pidfile("voz").unlink(missing_ok=True)


def cmd_avance(a) -> int:
    from . import avances
    if a.orquestador:
        print(json.dumps(avances.from_orquestador(), ensure_ascii=False))
        return 0
    if not a.texto:
        print("falta el texto del avance")
        return 1
    try:
        rec = avances.add(" ".join(a.texto), a.nivel, a.de, a.opciones.split("|") if a.opciones else None, a.pregunta)
    except ValueError as e:
        print(f"avance: {e}")
        return 1
    print(f"avance {rec['id']} en cola ({'la voz está viva' if _pid('voz') else 'la voz no corre: se cuenta al prenderla'})")
    return 0


def cmd_probar(a) -> int:
    cfg = cfgmod.load()
    base = cfg.get("puente_url") if a.tunel else f"http://127.0.0.1:{cfg['puerto']}"
    if not base:
        print("no hay URL del túnel (python -m playground.secretaria up)")
        return 1
    tok = cfgmod.token()
    if not tok:
        print("falta el token del puente (se crea con serve o publicar)")
        return 1
    try:
        r = httpx.post(f"{base.rstrip('/')}{cfgmod.RUTA}/h/{a.tool}", json=json.loads(a.args or "{}"),
                       headers={"X-Playground-Token": tok, "X-Conversacion": "prueba-cli"}, timeout=60)
    except httpx.HTTPError as e:
        print(f"el puente no contesta: {type(e).__name__}")
        return 1
    print(r.status_code, json.dumps(r.json(), ensure_ascii=False, indent=2))
    return 0 if r.status_code == 200 else 1


def cmd_hablar(a) -> int:
    cfg = cfgmod.load()
    url = f"http://127.0.0.1:{cfg['puerto_app']}/app"
    try:
        httpx.get(url, timeout=2)
    except httpx.HTTPError:
        print("el puente no corre: python -m playground.secretaria up")
        return 1
    subprocess.run(["open", url], check=False)
    print(f"página abierta: {url} (acepta el micrófono)")
    return 0


def cmd_herramientas(a) -> int:
    from .tools import catalog
    for t in catalog():
        req = set(t.required)
        params = ", ".join(f"{k}{'' if k in req else '?'}" for k in t.properties)
        print(f"{t.name}({params})")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m playground.secretaria", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("up"); sp.add_parser("down")
    s = sp.add_parser("estado"); s.add_argument("--mostrar-url", action="store_true")
    s = sp.add_parser("serve"); s.add_argument("--port", type=int); s.add_argument("--app-port", type=int)
    s = sp.add_parser("tunel"); s.add_argument("--sin-publicar", action="store_true")
    s = sp.add_parser("publicar"); s.add_argument("--url"); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--modo", choices=["local", "tunel"])
    s = sp.add_parser("voz"); s.add_argument("--duplex", action="store_true", help="con audífonos: Max la puede interrumpir")
    s.add_argument("--dormir-tras", type=float, default=10, help="minutos sin nada antes de colgar (0 = nunca)")
    s.add_argument("--con-orquestador", type=float, default=0, help="cada cuántos s leer notas de h3 (0 = no)")
    s.add_argument("--sin-saludo", action="store_true"); s.add_argument("--fondo", action="store_true")
    s.add_argument("--parar", action="store_true")
    s = sp.add_parser("avance"); s.add_argument("texto", nargs="*")
    s.add_argument("--nivel", choices=["cambio", "trabajo"], default="trabajo"); s.add_argument("--de", default="h3")
    s.add_argument("--opciones", help="A|B|C"); s.add_argument("--pregunta", default="", help="qN")
    s.add_argument("--orquestador", action="store_true", help="pasar a la cola las notas nuevas de h3 (orq-creativo)")
    s = sp.add_parser("probar"); s.add_argument("tool"); s.add_argument("args", nargs="?")
    s.add_argument("--tunel", action="store_true", help="por la URL pública en vez de 127.0.0.1")
    sp.add_parser("hablar"); sp.add_parser("herramientas")
    a = p.parse_args(argv)
    if a.cmd == "serve":
        from .server import serve
        serve(a.port, a.app_port)
        return 0
    if a.cmd == "tunel":
        from .tunel import run
        return run(publish=not a.sin_publicar)
    return {"up": cmd_up, "down": cmd_down, "estado": cmd_estado, "publicar": cmd_publicar, "probar": cmd_probar,
            "hablar": cmd_hablar, "herramientas": cmd_herramientas, "voz": cmd_voz, "avance": cmd_avance}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())

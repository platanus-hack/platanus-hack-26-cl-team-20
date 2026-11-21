"""Servidor de desarrollo Quest → proyector: http estático + señalización WebRTC (WebSocket) + logs y calibraciones.

    .venv/bin/python quest/server.py                 # puerto 8800, hace `adb reverse` si el casco está conectado
    .venv/bin/python quest/server.py --abrir-casco   # además abre quest.html en el navegador del casco
    .venv/bin/python quest/server.py --https         # además https en 8801 con certificado autofirmado (sin adb)

Casco:     http://localhost:8800/quest.html        (localhost vía `adb reverse` = contexto seguro para WebXR)
           https://<ip-del-mac>:8801/quest.html    (sin cable: aceptar la advertencia del certificado una vez)
Proyector: http://<ip-del-mac>:8800/proyector.html
El Mac solo sirve páginas y señalización: el video va directo casco → proyector por WebRTC. http y https comparten
las mismas salas de señalización.

Rutas: /ws (señalización por salas), /scan/sala.glb (escaneo para ?sim=1), POST /api/log (logs del casco a la
terminal y a quest/logs/), POST|GET /api/calib (guarda/lee quest/calib/calib.json), GET /api/estado,
POST /api/jev (Jev de TypeSafe por el AI Gateway con la llave del Mac; ver api_jev).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import ipaddress
import json
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import aiohttp
from aiohttp import WSMsgType, web

AQUI = Path(__file__).resolve().parent
REPO = AQUI.parent
SCAN_DEF = REPO / "captures/scans/ipad-20261030/sala.glb"
ADB_DEF = Path.home() / "Library/Android/sdk/platform-tools/adb"
LOGS = AQUI / "logs"
CERTS = AQUI / "certs"
CALIB = AQUI / "calib"


def ahora() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def ip_lan() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.4.1", 9))  # no envía nada; solo elige la interfaz de la LAN
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# ───────────────────────── señalización ─────────────────────────

class Sala:
    def __init__(self) -> None:
        self.clientes: dict[str, tuple[web.WebSocketResponse, str]] = {}


SALAS: dict[str, Sala] = {}


async def enviar(ws: web.WebSocketResponse, msg: dict) -> None:
    if not ws.closed:
        try:
            await ws.send_str(json.dumps(msg))
        except ConnectionResetError:
            pass


async def ws_handler(req: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4 << 20)
    await ws.prepare(req)
    cid, sala_id, rol = None, None, None
    try:
        async for m in ws:
            if m.type != WSMsgType.TEXT:
                continue
            try:
                msg = json.loads(m.data)
            except json.JSONDecodeError:
                continue
            t = msg.get("t")
            if t == "hello" and cid is None:
                rol = str(msg.get("rol", "?"))[:16]
                sala_id = str(msg.get("sala", "sala"))[:32]
                cid = f"{rol[0]}{secrets.token_hex(2)}"
                sala = SALAS.setdefault(sala_id, Sala())
                otros = [{"id": k, "rol": r} for k, (_, r) in sala.clientes.items()]
                sala.clientes[cid] = (ws, rol)
                await enviar(ws, {"t": "welcome", "id": cid, "peers": otros})
                for k, (w, _) in list(sala.clientes.items()):
                    if k != cid:
                        await enviar(w, {"t": "join", "id": cid, "rol": rol})
                print(f"{ahora()} [ws] {rol} {cid} entró a '{sala_id}' ({req.remote}) · en la sala: "
                      f"{', '.join(f'{r} {k}' for k, (_, r) in sala.clientes.items())}", flush=True)
            elif t == "signal" and cid:
                sala = SALAS.get(sala_id)
                dest = sala.clientes.get(msg.get("to")) if sala else None
                if dest:
                    await enviar(dest[0], {"t": "signal", "from": cid, "data": msg.get("data")})
            elif t == "ping":
                await enviar(ws, {"t": "pong", "ts": msg.get("ts")})
    finally:
        if cid and sala_id in SALAS:
            sala = SALAS[sala_id]
            sala.clientes.pop(cid, None)
            for w, _ in sala.clientes.values():
                await enviar(w, {"t": "leave", "id": cid})
            print(f"{ahora()} [ws] {rol} {cid} salió de '{sala_id}'", flush=True)
    return ws


# ───────────────────────── api ─────────────────────────

async def api_log(req: web.Request) -> web.Response:
    try:
        datos = await req.json()
    except Exception:
        return web.json_response({"ok": False}, status=400)
    entradas = datos if isinstance(datos, list) else [datos]
    LOGS.mkdir(exist_ok=True)
    archivo = LOGS / f"casco-{dt.date.today():%Y%m%d}.jsonl"
    with archivo.open("a") as f:
        for e in entradas[:200]:
            e = dict(e) if isinstance(e, dict) else {"msg": str(e)}
            e["_srv"] = dt.datetime.now().isoformat(timespec="milliseconds")
            e["_ip"] = req.remote
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
            nivel = e.get("nivel", "info")
            if nivel in ("error", "warn", "info"):
                extra = ""
                if e.get("datos") is not None:
                    extra = " " + json.dumps(e["datos"], ensure_ascii=False)[:600]
                print(f"{ahora()} [{e.get('origen', 'web')} {nivel}] {e.get('msg', '')}{extra}", flush=True)
    return web.json_response({"ok": True})


async def api_calib_post(req: web.Request) -> web.Response:
    try:
        datos = await req.json()
    except Exception:
        return web.json_response({"ok": False}, status=400)
    CALIB.mkdir(exist_ok=True)
    nombre = f"calib-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    txt = json.dumps(datos, indent=1, ensure_ascii=False)
    (CALIB / nombre).write_text(txt)
    (CALIB / "calib.json").write_text(txt)
    print(f"{ahora()} [calib] guardada {nombre} · modo={datos.get('modo')} rms={datos.get('rms_px')} "
          f"n={len(datos.get('puntos', []))}", flush=True)
    return web.json_response({"ok": True, "archivo": f"calib/{nombre}"})


async def api_calib_get(req: web.Request) -> web.StreamResponse:
    f = CALIB / "calib.json"
    if not f.exists():
        return web.json_response({"ok": False, "error": "no hay calib.json"}, status=404)
    return web.FileResponse(f, headers={"Cache-Control": "no-store"})


async def api_estado(req: web.Request) -> web.Response:
    return web.json_response({k: [{"id": c, "rol": r} for c, (_, r) in s.clientes.items()] for k, s in SALAS.items()})


# ───────────────────────── jev ─────────────────────────

JEV_MAX_BYTES = 64 << 10
JEV_MAX_PREGUNTAS = 16
JEV_TOPE_POR_MIN = 120
_jev_llamadas: deque[float] = deque()
_jev_sesion: aiohttp.ClientSession | None = None


def _jev_mod():
    """playground/jev.py (contrato, cabeceras y llave: la misma lógica que usa la mesa)."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from playground import jev
    return jev


async def api_jev(req: web.Request) -> web.Response:
    """POST /api/jev {state, questions} → Jev (TypeSafe «System One») por el Vercel AI Gateway, con la llave del
    entorno del Mac (AI_GATEWAY_API_KEY o, si falta, JEV_ENV_FILE; ver playground/jev.py): la llave nunca va al casco,
    al APK ni al navegador. Mismo contrato que /api/jev de remotion-live: responde {model, answers, usage} con
    respuestas choice / score / noul. Solo desde loopback, LAN o tailnet; tope de JEV_TOPE_POR_MIN llamadas por minuto."""
    global _jev_sesion
    try:
        if ipaddress.ip_address((req.remote or "").split("%")[0]).is_global:
            return web.json_response({"error": "solo desde la red local"}, status=403)
    except ValueError:
        return web.json_response({"error": "origen desconocido"}, status=403)
    if (req.content_length or 0) > JEV_MAX_BYTES:
        return web.json_response({"error": "pedido muy grande"}, status=413)
    try:
        datos = await req.json()
    except Exception:
        return web.json_response({"error": "JSON inválido"}, status=400)
    state, questions = (datos.get("state"), datos.get("questions")) if isinstance(datos, dict) else (None, None)
    if not isinstance(state, (str, dict, list)) or not isinstance(questions, dict) or not 0 < len(questions) <= JEV_MAX_PREGUNTAS:
        return web.json_response({"error": f"se espera {{state, questions}} con 1 a {JEV_MAX_PREGUNTAS} preguntas"}, status=400)
    t_ = time.monotonic()
    while _jev_llamadas and t_ - _jev_llamadas[0] > 60.0:
        _jev_llamadas.popleft()
    if len(_jev_llamadas) >= JEV_TOPE_POR_MIN:
        return web.json_response({"error": f"tope de {JEV_TOPE_POR_MIN} llamadas por minuto"}, status=429)
    try:
        jv = _jev_mod()
    except Exception as e:  # noqa: BLE001
        return web.json_response({"error": f"no pude cargar playground/jev.py: {e}"}, status=500)
    llave = jv.llave_gateway()
    if not llave:
        return web.json_response({"error": "sin AI_GATEWAY_API_KEY en el entorno del Mac"}, status=503)
    try:
        qs, noul = jv.a_gateway(questions)
    except (KeyError, TypeError, AttributeError, jv.JevError) as e:
        return web.json_response({"error": f"pregunta inválida: {e}"}, status=400)
    texto = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    _jev_llamadas.append(t_)
    if _jev_sesion is None or _jev_sesion.closed:
        _jev_sesion = aiohttp.ClientSession()          # keep-alive: desde la segunda llamada no se paga TLS
    try:
        async with _jev_sesion.post(jv.GATEWAY_URL, headers=jv.cabeceras_gateway(llave), timeout=aiohttp.ClientTimeout(total=6),
                                    data=json.dumps({"state": texto, "questions": qs}, ensure_ascii=False).encode()) as r:
            estado, cuerpo = r.status, await r.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        return web.json_response({"error": "jev unavailable", "message": str(e)[:200] or type(e).__name__}, status=502)
    if estado != 200:
        return web.json_response({"error": "jev unavailable", "status": estado, "message": cuerpo[:300]}, status=502)
    try:
        answers, usage = jv.de_gateway(json.loads(cuerpo), questions, noul)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"error": "respuesta de jev ilegible", "message": str(e)[:200]}, status=502)
    ms = round((time.monotonic() - t_) * 1000)
    print(f"{ahora()} [jev] {len(questions)} pregunta(s) · {usage['input_tokens']} tokens · {ms} ms ({req.remote})", flush=True)
    return web.json_response({"model": f"{jv.MODELO}@gateway", "answers": answers, "usage": usage},
                             headers={"x-jev-route": "gateway", "x-jev-ms": str(ms), "Cache-Control": "no-store"})


async def _jev_cerrar(app: web.Application) -> None:
    if _jev_sesion is not None and not _jev_sesion.closed:
        await _jev_sesion.close()


async def index(req: web.Request) -> web.Response:
    ip = req.app["ip"]
    port = req.app["port"]
    html = f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Quest proyector</title><style>body{{font:16px system-ui;background:#111;color:#ddd;padding:24px}}a{{color:#6cf}}</style>
<h1>Quest → proyector</h1><ul>
<li><a href="/quest.html">quest.html</a> (en el casco: http://localhost:{port}/quest.html)</li>
<li><a href="/quest.html?sim=1">quest.html?sim=1</a> (simulador en el Mac, con el escaneo de la sala)</li>
<li><a href="/proyector.html">proyector.html</a> (en el proyector: http://{ip}:{port}/proyector.html)</li>
</ul>"""
    return web.Response(text=html, content_type="text/html")


@web.middleware
async def sin_cache(req: web.Request, handler):
    resp = await handler(req)
    if req.path.endswith((".html", ".js", ".mjs", ".json")) or req.path == "/":
        resp.headers["Cache-Control"] = "no-store"
    return resp


def certificado(ip: str) -> ssl.SSLContext:
    """Certificado autofirmado para localhost + la IP de la LAN (se genera una vez en quest/certs/)."""
    CERTS.mkdir(exist_ok=True)
    crt, key = CERTS / f"dev-{ip}.crt", CERTS / f"dev-{ip}.key"
    if not crt.exists():
        cnf = CERTS / "openssl.cnf"
        cnf.write_text("[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=quest-proyector-dev\n"
                       f"[ext]\nsubjectAltName=DNS:localhost,IP:127.0.0.1,IP:{ip}\nbasicConstraints=CA:FALSE\n"
                       "keyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825", "-keyout", str(key),
                        "-out", str(crt), "-config", str(cnf)], check=True, capture_output=True)
        print(f"certificado autofirmado nuevo: {crt}")
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(crt, key)
    return ctx


def adb_reverse(adb: Path, port: int, abrir: bool) -> None:
    """`adb reverse` en cada casco conectado (USB y/o Wi-Fi: adb connect 192.168.4.161:5555)."""
    if not adb.exists():
        print(f"(adb no encontrado en {adb}; sin adb reverse)")
        return
    try:
        devs = subprocess.run([str(adb), "devices"], capture_output=True, text=True, timeout=5).stdout
        seriales = [l.split("\t")[0] for l in devs.splitlines() if l.endswith("\tdevice")]
        if not seriales:
            print("(ningún casco por adb: conéctalo por USB o `adb connect <ip>:5555`; o usa --https)")
            return
        for s in seriales:
            r = subprocess.run([str(adb), "-s", s, "reverse", f"tcp:{port}", f"tcp:{port}"], timeout=8,
                               capture_output=True, text=True)
            print(f"adb -s {s} reverse tcp:{port} tcp:{port}: {'OK' if r.returncode == 0 else r.stderr.strip()}")
        if abrir:
            subprocess.run([str(adb), "-s", seriales[0], "shell", "am", "start", "-a", "android.intent.action.VIEW",
                            "-d", f"http://localhost:{port}/quest.html", "com.oculus.browser"],
                           check=True, timeout=10, capture_output=True)
            print("Abrí quest.html en el navegador del casco")
    except Exception as e:  # noqa: BLE001
        print(f"(adb falló: {e})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--scan", type=Path, default=SCAN_DEF, help="malla .glb para el modo ?sim=1")
    ap.add_argument("--adb", type=Path, default=Path(shutil.which("adb") or ADB_DEF))
    ap.add_argument("--sin-adb", action="store_true", help="no tocar adb")
    ap.add_argument("--abrir-casco", action="store_true", help="abre quest.html en el navegador del casco")
    ap.add_argument("--https", action="store_true", help="sirve también https (autofirmado) en --port-https")
    ap.add_argument("--port-https", type=int, default=None, help="por defecto --port + 1")
    a = ap.parse_args()
    a.port_https = a.port_https or a.port + 1

    app = web.Application(middlewares=[sin_cache], client_max_size=8 << 20)
    app["ip"], app["port"] = ip_lan(), a.port
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_post("/api/log", api_log)
    app.router.add_post("/api/calib", api_calib_post)
    app.router.add_get("/api/calib", api_calib_get)
    app.router.add_get("/api/estado", api_estado)
    app.router.add_post("/api/jev", api_jev)
    app.on_cleanup.append(_jev_cerrar)
    if a.scan.exists():
        app.router.add_get("/scan/sala.glb", lambda r: web.FileResponse(a.scan))
    app.router.add_static("/", AQUI, show_index=False, follow_symlinks=False)

    print(f"Quest → proyector · sirviendo {AQUI}")
    print(f"  casco:     http://localhost:{a.port}/quest.html   (con adb reverse)")
    if a.https:
        print(f"  casco:     https://{app['ip']}:{a.port_https}/quest.html   (sin cable; aceptar el certificado)")
    print(f"  proyector: http://{app['ip']}:{a.port}/proyector.html")
    print(f"  simulador: http://localhost:{a.port}/quest.html?sim=1   (escaneo: {a.scan if a.scan.exists() else 'NO'})")
    if not a.sin_adb:
        adb_reverse(a.adb, a.port, a.abrir_casco)
    ctx = certificado(app["ip"]) if a.https else None

    async def correr() -> None:
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, a.host, a.port).start()
        if ctx:
            await web.TCPSite(runner, a.host, a.port_https, ssl_context=ctx).start()
        await asyncio.Event().wait()

    asyncio.run(correr())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass

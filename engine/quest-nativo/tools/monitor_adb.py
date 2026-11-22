#!/usr/bin/env python3
"""Monitor en el browser de todo lo que entrega el casco + la app com.smartup.proyector.

    /Volumes/Externo/sl-scanner/.venv/bin/python tools/monitor_adb.py      # http://127.0.0.1:8810/

Fuentes:
  - si la app expone su servidor (http://<casco>:8787/estado.json responde): pose, anclas, cámara,
    render, profundidad y malla en vivo desde la app (fase 2); el adb queda para logcat y dumpsys.
  - si no: solo adb. Cada pocos segundos manda `estado`, `camara_debug`, `captura_salida monitor`
    (y `profundidad` cada 20 s), baja lo que la app escribe en files/, lee logcat (stream propio,
    sobrevive a los `logcat -c` de cmd.sh), dumpsys display/window/battery/meminfo y screencap.

No manda comandos que muevan la cámara mientras la app está calibrando. Caché en descargas/monitor/.
"""
from __future__ import annotations

import asyncio
import collections
import io
import json
import os
import re
import sys
import time
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

ADB = os.environ.get("ADB", "/Users/max/Library/Android/sdk/platform-tools/adb")
SERIAL = os.environ.get("ANDROID_SERIAL", "192.168.4.161:5555")
CASCO_IP = SERIAL.split(":")[0]
APP = "com.smartup.proyector"
DIR = f"/sdcard/Android/data/{APP}/files"
PUERTO = int(os.environ.get("MONITOR_PUERTO", "8810"))
APP_URL = os.environ.get("APP_URL", f"http://{CASCO_IP}:8787")
AQUI = Path(__file__).resolve().parent.parent
CACHE = AQUI / "descargas" / "monitor"
ASSETS = AQUI / "app" / "src" / "main" / "assets" / "monitor"      # misma página que sirve la app en :8787
SALA_GLB = Path(os.environ.get("SALA_GLB", "/Volumes/Externo/sl-scanner/captures/scans/ipad-20261030/sala.glb"))
RUTAS_APP = {"estado.json", "profundidad.json", "malla.json", "log", "cmd", "cam.jpg", "cam.mjpg", "render.jpg", "render.mjpg",
             "depth.jpg", "depth.mjpg", "depth.json", "cam_full.png", "cam_full.jpg", "cam_pose.json", "cuerpo.json"}
CACHE.mkdir(parents=True, exist_ok=True)

ENV = dict(os.environ, ANDROID_SERIAL=SERIAL)
CALIB_LIBRE = {"INACTIVO", "LISTO", "ERROR", "CANCELADO", "?"}

S: dict = {
    "inicio": time.time(),
    "adb_ok": False,
    "app_http": False,          # fase 2: servidor de la app responde
    "app_estado": None,         # /estado.json de la app
    "estado": {},               # parseado del log
    "estado_ts": 0,
    "lineas": {},               # última línea por etiqueta ([cam] intrínsecos, [sala], [prof]...)
    "foco": {},
    "displays": [],
    "sistema": {},
    "archivos": [],
    "calib": None,
    "cmds": {},                 # último envío por comando
    "errores": [],
}
LOG: collections.deque = collections.deque(maxlen=3000)
PERIODICOS = {  # comando: [periodo s, activo]
    "estado": [3, True],
    "camara_debug": [4, True],
    "captura_salida monitor": [4, True],
    "profundidad": [20, True],
    "screencap": [6, False],  # en Quest 3 v209 screencap sale vacío (display seguro)
}
ultimo_envio: dict[str, float] = {}
mtimes: dict[str, tuple] = {}


async def run(*args: str, timeout: float = 8, binario: bool = False):
    p = await asyncio.create_subprocess_exec(ADB, *args, stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.PIPE, env=ENV)
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        raise RuntimeError(f"adb {' '.join(args[:3])}: timeout")
    if p.returncode != 0 and not out:
        raise RuntimeError(err.decode(errors="replace").strip()[:200])
    return out if binario else out.decode(errors="replace")


async def broadcast(c: str):
    await run("shell", "am", "broadcast", "-a", f"{APP}.CMD", "-p", APP, "--es", "c", f"'{c}'")
    S["cmds"][c] = time.time()


# ------------------------------------------------------------------ logcat (stream propio)
RE_LOG = re.compile(r"^(\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\s+(\d+)\s+(\d+)\s+([VDIWEF])\s+(\S+?)\s*:\s?(.*)$")


def procesar_linea(l: str):
    m = RE_LOG.match(l)
    if not m:
        if l.strip() and not l.startswith("-----"):
            LOG.append({"t": "", "n": "I", "tag": "", "m": l.rstrip()})
        return
    t, _pid, _tid, nivel, tag, msg = m.groups()
    LOG.append({"t": t, "n": nivel, "tag": tag, "m": msg})
    if tag != "Proyector":
        return
    if msg.startswith("[estado] "):
        S["estado"] = parsear_estado(msg[9:].split(" | "))
        S["estado_ts"] = time.time()
    elif msg.startswith("[cmd] 'estado' → "):
        S["estado"] = parsear_estado(re.split(r" (?=salida: |cámara: |sala: |modelo: |  P=|modo: |ajuste: |GL: )", msg[17:]))
        S["estado_ts"] = time.time()
    for clave, patron in [("intrinsecos", "[cam] intrínsecos"), ("eje", "[cam] eje óptico"), ("cam_sesion", "[cam] sesión"),
                          ("cam_base", "[cam] base de timestamp"), ("cam_primer", "[cam] primer cuadro"),
                          ("cam_error", "[cam] error"), ("cam_err2", "CAMERA_DISABLED"), ("sala", "[sala]"),
                          ("prof", "[prof]"), ("debug", "[debug]"), ("salida", "[salida]"), ("gl_destino", "[gl] destino"),
                          ("raycaster", "environmentRaycaster"), ("prueba", "[prueba]"), ("cal", "[cal]"),
                          ("escena", "escena lista"), ("permisos", "permisos")]:
        if patron in msg:
            S["lineas"][clave] = {"t": t, "m": msg}


def parsear_estado(partes: list[str]) -> dict:
    e: dict = {"crudo": partes}
    for p in partes:
        p = p.strip()
        if p.startswith("CALIB:"):
            e["calib"] = p[6:].strip()
            e["fase"] = p[6:].split()[0] if p[6:].split() else "?"
        elif p.startswith("salida:"):
            e["salida"] = p[7:].strip()
            m = re.search(r"render (\d+) fps", p)
            if m:
                e["render_fps"] = int(m.group(1))
        elif p.startswith("cámara:"):
            e["camara"] = p[7:].strip()
            m = re.search(r"(\d+) fps, (\d+) cuadros", p)
            if m:
                e["cam_fps"], e["cam_cuadros"] = int(m.group(1)), int(m.group(2))
        elif p.startswith("sala:"):
            e["sala"] = p[5:].strip()
        elif p.startswith("modelo:"):
            e["modelo"] = p[7:].strip()
        elif p.startswith("P=") or p.startswith("K="):
            e["modelo_det"] = p
        elif p.startswith("modo:"):
            e["modo"] = p[5:].strip()
        elif p.startswith("ajuste:"):
            e["ajuste"] = p[7:].strip()
        elif p.startswith("GL:"):
            e["gl"] = p[3:].strip()
        else:
            e.setdefault("otros", []).append(p)
    return e


async def tarea_logcat():
    while True:
        try:
            p = await asyncio.create_subprocess_exec(
                ADB, "logcat", "-T", "400", "-v", "threadtime", "-s", "Proyector:*", "AndroidRuntime:E",
                "CameraService:W", "Camera2-Device:W", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=ENV)
            assert p.stdout
            while True:
                l = await p.stdout.readline()
                if not l:
                    break
                procesar_linea(l.decode(errors="replace").rstrip("\n"))
            await p.wait()
        except Exception as ex:  # noqa: BLE001
            nota_error(f"logcat: {ex}")
        await asyncio.sleep(2)


def nota_error(m: str):
    S["errores"] = (S["errores"] + [f"{time.strftime('%H:%M:%S')} {m}"])[-12:]


# ------------------------------------------------------------------ dumpsys y archivos
async def leer_sistema():
    try:
        w = await run("shell", "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp|mFocusedWindow|mTopFullscreenOpaqueWindowState' | head -8")
        a = await run("shell", "dumpsys activity activities | grep -E 'mResumedActivity|topResumedActivity|ResumedActivity' | head -4")
        S["foco"] = {"window": list(dict.fromkeys(x.strip() for x in w.splitlines() if x.strip())),
                     "activity": list(dict.fromkeys(x.strip() for x in a.splitlines() if x.strip())),
                     "app_resumida": APP in a,
                     "app_tiene_foco": any(APP in x for x in w.splitlines() if "mFocusedApp" in x or "mCurrentFocus" in x)}
        d = await run("shell", "dumpsys display | grep -E 'DisplayDeviceInfo\\{|mDisplayId=|  Display [0-9]+:' | head -20")
        disp = []
        for l in d.splitlines():
            m = re.search(r'DisplayDeviceInfo\{"([^"]*)": uniqueId="([^"]*)", (\d+) x (\d+), modeId \d+, renderFrameRate ([\d.]+)|DisplayDeviceInfo\{"([^"]*)": uniqueId="([^"]*)", (\d+) x (\d+)', l)
            if m:
                g = m.groups()
                nombre = g[0] or g[5]
                disp.append({"nombre": nombre, "id": g[1] or g[6], "w": int(g[2] or g[7]), "h": int(g[3] or g[8]),
                             "hz": g[4], "hdmi": "HDMI" in nombre.upper(), "flags": re.findall(r"FLAG_[A-Z_]+", l)})
        # sin duplicados (dumpsys repite DisplayDeviceInfo)
        vistos, unicos = set(), []
        for x in disp:
            if x["id"] not in vistos:
                vistos.add(x["id"]); unicos.append(x)
        S["displays"] = unicos
        b = await run("shell", "dumpsys battery | grep -E 'level|temperature|AC powered|USB powered|status' | head -6")
        pid = (await run("shell", f"pidof {APP}")).strip()
        mem = await run("shell", f"dumpsys meminfo {APP} | grep -E 'TOTAL PSS|TOTAL:' | head -1") if pid else ""
        top = await run("shell", f"top -b -n 1 -p {pid} | tail -1") if pid else ""
        th = await run("shell", "dumpsys thermalservice | grep -A3 'Current temperatures from HAL' | tail -3")
        wifi = await run("shell", "dumpsys wifi 2>/dev/null | grep -m1 -E 'RSSI_POLL.*rssi='")
        S["sistema"] = {"bateria": " · ".join(x.strip() for x in b.splitlines()), "pid": pid,
                        "mem": " ".join(mem.split())[:120], "top": " ".join(top.split())[:160],
                        "termico": [x.strip()[:160] for x in th.splitlines() if x.strip()],
                        "wifi": " ".join(re.findall(r'"[^"]+"|rssi=-?\d+|link=\d+', wifi))}
        S["adb_ok"] = True
    except Exception as ex:  # noqa: BLE001
        S["adb_ok"] = False
        nota_error(f"dumpsys: {ex}")


async def leer_archivos():
    try:
        out = await run("shell", f"cd {DIR} && stat -c '%n|%s|%Y' * */* 2>/dev/null")
    except Exception as ex:  # noqa: BLE001
        nota_error(f"ls files: {ex}")
        return
    lista = []
    for l in out.splitlines():
        try:
            n, s, m = l.strip().split("|")
        except ValueError:
            continue
        lista.append({"n": n, "s": int(s), "m": int(m)})
        if not re.search(r"\.(jpg|jpeg|png|json|csv|txt)$", n, re.I):
            continue
        if mtimes.get(n) == (int(s), int(m)) and (CACHE / n).exists():
            continue
        if int(s) > 12_000_000:
            continue
        try:
            data = await run("exec-out", f"cat '{DIR}/{n}'", binario=True, timeout=20)
            if len(data) == int(s):
                dst = CACHE / n
                dst.parent.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_suffix(dst.suffix + ".tmp")
                tmp.write_bytes(data)
                tmp.replace(dst)
                mtimes[n] = (int(s), int(m))
        except Exception as ex:  # noqa: BLE001
            nota_error(f"pull {n}: {ex}")
    lista.sort(key=lambda x: -x["m"])
    S["archivos"] = lista
    cj = CACHE / "calib_proyector.json"
    if cj.exists():
        try:
            S["calib"] = json.loads(cj.read_text())
        except Exception:  # noqa: BLE001
            pass


async def screencap():
    try:
        png = await run("exec-out", "screencap -p", binario=True, timeout=15)
        if len(png) < 1000:
            S["screencap_nota"] = f"screencap vacío ({len(png)} bytes): Horizon OS no deja capturar el display del casco por adb"
            return
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(png)).convert("RGB")
            im.thumbnail((1100, 1100))
            b = io.BytesIO(); im.save(b, "JPEG", quality=80)
            data = b.getvalue()
        except Exception:  # noqa: BLE001
            data = png
        (CACHE / "_screencap.jpg").write_bytes(data)
        S["screencap_ts"] = time.time()
    except Exception as ex:  # noqa: BLE001
        nota_error(f"screencap: {ex}")


async def leer_app_http(ses: ClientSession):
    try:
        async with ses.get(f"{APP_URL}/estado.json") as r:
            if r.status == 200:
                S["app_estado"] = await r.json(content_type=None)
                S["app_http"] = True
                return
    except Exception:  # noqa: BLE001
        pass
    S["app_http"] = False


async def bucle():
    ses = ClientSession(timeout=ClientTimeout(total=1.5))
    n = 0
    while True:
        t0 = time.time()
        try:
            await run("connect", SERIAL, timeout=5) if not S["adb_ok"] and n % 5 == 0 else None
        except Exception:  # noqa: BLE001
            pass
        await leer_app_http(ses)
        fase = S["estado"].get("fase", "?")
        libre = fase in CALIB_LIBRE
        for c, (periodo, activo) in PERIODICOS.items():
            if not activo or time.time() - ultimo_envio.get(c, 0) < periodo:
                continue
            if c == "screencap":
                ultimo_envio[c] = time.time()
                asyncio.create_task(screencap())
                continue
            if S["app_http"] and c in ("camara_debug", "captura_salida monitor", "estado", "profundidad"):
                continue  # en vivo desde la app (:8787)
            if c != "estado" and not libre:
                continue
            ultimo_envio[c] = time.time()
            try:
                await broadcast(c)
            except Exception as ex:  # noqa: BLE001
                nota_error(f"cmd {c}: {ex}")
        await leer_archivos()
        if n % 2 == 0:
            await leer_sistema()
        n += 1
        await asyncio.sleep(max(0.3, 2.5 - (time.time() - t0)))


# ------------------------------------------------------------------ http
async def h_estado(_req):
    d = {k: v for k, v in S.items()}
    d["ahora"] = time.time()
    d["log"] = list(LOG)[-120:]
    d["periodicos"] = {k: {"periodo": v[0], "activo": v[1], "ultimo": ultimo_envio.get(k)} for k, v in PERIODICOS.items()}
    # edad = mtime en el casco (cuándo la app escribió el archivo), no cuándo se bajó
    d["cache"] = {p.name: (mtimes[p.name][1] if p.name in mtimes else p.stat().st_mtime) for p in CACHE.glob("*") if p.is_file()}
    d["app_url"] = APP_URL
    d["version"] = version_pagina()
    return web.json_response(d, dumps=lambda o: json.dumps(o, default=str, ensure_ascii=False))


async def h_img(req):
    n = req.match_info["n"]
    p = (CACHE / n).resolve()
    if not str(p).startswith(str(CACHE.resolve())) or not p.exists():
        raise web.HTTPNotFound()
    return web.FileResponse(p, headers={"Cache-Control": "no-store"})


async def h_cmd(req):
    c = (req.query.get("c") or (await req.post()).get("c") or "").strip()
    if not c or len(c) > 120 or "'" in c:
        raise web.HTTPBadRequest(text="comando inválido")
    await broadcast(c)
    return web.json_response({"ok": True, "c": c})


async def h_toggle(req):
    k = req.query.get("k", "")
    if k in PERIODICOS:
        PERIODICOS[k][1] = not PERIODICOS[k][1]
    return web.json_response({k: PERIODICOS.get(k)})


async def h_proxy(req):
    """Reenvía /app/<ruta> al servidor de la app (fase 2) para que la página no dependa de CORS."""
    ruta = req.match_info["r"]
    async with ClientSession(timeout=ClientTimeout(total=None, sock_read=15)) as ses:
        async with ses.get(f"{APP_URL}/{ruta}", params=req.query) as r:
            cab = {"Content-Type": r.headers.get("Content-Type", "application/octet-stream"), "Cache-Control": "no-store"}
            cab.update({k: v for k, v in r.headers.items() if k.lower().startswith("x-")})
            resp = web.StreamResponse(status=r.status, headers=cab)
            await resp.prepare(req)
            async for chunk in r.content.iter_chunked(65536):
                await resp.write(chunk)
            return resp


def version_pagina() -> str:
    """Cambia cuando cambia la página servida: las pestañas abiertas se recargan solas."""
    return str(hash(pagina_actual()))


def pagina_actual() -> str:
    # con la app sirviendo :8787 → la página en vivo (assets/monitor/index.html); si no → la de adb
    if S["app_http"] and (ASSETS / "index.html").exists():
        return (ASSETS / "index.html").read_text()
    return PAGINA


def estado_fallback() -> dict:
    """/estado.json armado desde adb cuando la app no expone :8787 (sin poses)."""
    e = S["estado"]
    return {
        "fuente": "adb (la app no responde en :8787)",
        "app": {"foco": S["foco"].get("app_tiene_foco", False), "resumida": S["foco"].get("app_resumida", False)},
        "camara": {"estado": e.get("camara", "—"), "abierta": "fps" in e.get("camara", ""), "fps": e.get("cam_fps", 0)},
        "render": {"fps": e.get("render_fps"), "vista": None},
        "salida": {"activa": e.get("salida", "—"), "displays": [
            {"id": d["id"], "nombre": d["nombre"], "w": d["w"], "h": d["h"], "hz": float(d["hz"] or 0), "hdmi": d["hdmi"], "flags": d["flags"]}
            for d in S["displays"] if not re.search(r"MirrorRoot|AndroidPanelLayer", d["nombre"])]},
        "mruk": {"resumen": e.get("sala", "—"), "anclas": [], "malla_version": -1},
        "calibracion": {"fase": e.get("fase", "?"), "mensaje": e.get("calib", "")},
        "controles": {},
    }


async def h_app(req):
    """Rutas de la app: proxy a :8787 si responde; si no, respaldo desde adb."""
    ruta = req.match_info["r"]
    c = (req.query.get("c") or "").strip()
    if ruta == "cmd" and c.startswith("aruco grid"):
        # la grilla se genera en el Mac, se proyecta por el runtime de la mesa (:8790) y el JSON va a la app
        p = await asyncio.create_subprocess_exec(sys.executable, str(AQUI / "tools" / "aruco_grid.py"), *c.split()[2:5],
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=ENV)
        out, _ = await p.communicate()
        return web.json_response({"c": c, "r": out.decode(errors="replace").strip()[-600:]})
    if S["app_http"]:
        try:
            return await proxy(req, ruta)
        except Exception as ex:  # noqa: BLE001
            nota_error(f"proxy {ruta}: {ex}")
    if ruta == "estado.json":
        return web.json_response(estado_fallback())
    if ruta == "malla.json":
        return web.json_response({"version": -1, "mruk": None, "prof": None, "fuente": "adb"})
    if ruta == "profundidad.json":
        return web.json_response({"error": "la app no expone :8787 (no está corriendo o es una versión sin monitor)"})
    if ruta == "log":
        n = int(req.query.get("n", "120"))
        return web.Response(text="\n".join(f"{l['t']} {l['n']}/{l['tag']}: {l['m']}" for l in list(LOG)[-n:]))
    if ruta == "cmd":
        return await h_cmd(req)
    if ruta in ("cam.jpg", "render.jpg"):
        cands = ["camara_debug.jpg"] if ruta == "cam.jpg" else ["prueba_monitor.png"] + sorted(
            (p.name for p in CACHE.glob("prueba_*.png")), key=lambda n: -(CACHE / n).stat().st_mtime)
        for n in cands:
            if (CACHE / n).exists():
                return web.FileResponse(CACHE / n, headers={"Cache-Control": "no-store"})
    raise web.HTTPNotFound(text=f"{ruta}: la app no responde en :8787")


async def proxy(req, ruta: str):
    async with ClientSession(timeout=ClientTimeout(total=None, sock_connect=3, sock_read=15)) as ses:
        async with ses.get(f"{APP_URL}/{ruta}", params=req.query) as r:
            cab = {"Content-Type": r.headers.get("Content-Type", "application/octet-stream"), "Cache-Control": "no-store"}
            cab.update({k: v for k, v in r.headers.items() if k.lower().startswith("x-")})
            resp = web.StreamResponse(status=r.status, headers=cab)
            await resp.prepare(req)
            try:
                async for chunk in r.content.iter_any():
                    await resp.write(chunk)
            except (ConnectionResetError, asyncio.CancelledError):
                pass
            return resp


async def h_estatico(req):
    """3d.html, three.js y addons desde assets/monitor/ (lo mismo que empaqueta la app); sala.glb de respaldo."""
    r = req.match_info["r"] or "index.html"
    if r in RUTAS_APP:
        return await h_app(req)
    if r in ("3d", "3d.html"):
        r = "3d.html"
    if r == "sala.glb":
        if SALA_GLB.exists():
            return web.FileResponse(SALA_GLB, headers={"Cache-Control": "max-age=3600"})
        raise web.HTTPNotFound()
    p = (ASSETS / r).resolve()
    if not str(p).startswith(str(ASSETS.resolve())) or not p.is_file():
        raise web.HTTPNotFound()
    tipo = {".html": "text/html", ".js": "text/javascript"}.get(p.suffix, "application/octet-stream")
    return web.FileResponse(p, headers={"Cache-Control": "no-store", "Content-Type": tipo})


async def h_index(_req):
    return web.Response(text=pagina_actual(), content_type="text/html", headers={"Cache-Control": "no-store"})


async def al_iniciar(app):
    app["t1"] = asyncio.create_task(tarea_logcat())
    app["t2"] = asyncio.create_task(bucle())


PAGINA = r"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Monitor Quest</title>
<style>
:root{--bg:#0b0d10;--pn:#14181d;--ln:#232a32;--tx:#d7dde4;--mu:#7d8a97;--ok:#4ade80;--wa:#fbbf24;--er:#f87171;--ac:#60a5fa}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace}
header{display:flex;gap:14px;align-items:center;padding:8px 14px;border-bottom:1px solid var(--ln);flex-wrap:wrap}
header b{font-size:15px}.pill{padding:2px 8px;border-radius:10px;background:#1d242c;color:var(--mu)}.pill.ok{color:#04130a;background:var(--ok)}.pill.er{color:#1a0505;background:var(--er)}.pill.wa{color:#1a1203;background:var(--wa)}
main{display:grid;grid-template-columns:repeat(12,1fr);gap:10px;padding:10px}
.p{background:var(--pn);border:1px solid var(--ln);border-radius:8px;padding:8px 10px;min-width:0;overflow:hidden}
.p h2{margin:0 0 6px;font-size:12px;color:var(--mu);text-transform:uppercase;letter-spacing:.06em;display:flex;justify-content:space-between;gap:8px}
.c3{grid-column:span 3}.c4{grid-column:span 4}.c5{grid-column:span 5}.c6{grid-column:span 6}.c7{grid-column:span 7}.c8{grid-column:span 8}.c12{grid-column:span 12}
@media(max-width:1100px){.c3,.c4,.c5,.c6,.c7,.c8{grid-column:span 12}}
img.v{width:100%;max-height:420px;object-fit:contain;background:#000;border-radius:4px;display:block}
.vacio{height:200px;display:flex;align-items:center;justify-content:center;color:var(--mu);background:#0f1216;border-radius:4px;text-align:center;padding:10px}
table{width:100%;border-collapse:collapse}td{padding:2px 4px;border-bottom:1px solid #1b2128;vertical-align:top;word-break:break-word}td:first-child{color:var(--mu);white-space:nowrap;width:1%}
.log{height:520px;overflow:auto;white-space:pre-wrap;word-break:break-all;font-size:12px}.log .E,.log .F{color:var(--er)}.log .W{color:var(--wa)}.log .t{color:#55606b}
.ok{color:var(--ok)}.er{color:var(--er)}.wa{color:var(--wa)}.mu{color:var(--mu)}
.gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px}.gal figure{margin:0}.gal img{width:100%;height:100px;object-fit:contain;background:#000;border-radius:3px}.gal figcaption{font-size:11px;color:var(--mu);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
button,input{background:#1d242c;color:var(--tx);border:1px solid var(--ln);border-radius:5px;padding:3px 8px;font:inherit;cursor:pointer}button.on{border-color:var(--ok);color:var(--ok)}
.age{font-size:11px;color:var(--mu);text-transform:none;letter-spacing:0}
canvas#c3d{width:100%;height:420px;display:block;background:#05070a;border-radius:4px}
</style></head><body>
<header><b>Monitor Quest · com.smartup.proyector</b><span id="adb" class="pill">adb</span><span id="http" class="pill">app :8787</span><span id="foco" class="pill">foco</span><span id="cal" class="pill">calib</span><span id="hdmi" class="pill">HDMI</span><span class="mu" id="hora"></span>
<span style="margin-left:auto"></span><input id="cmd" placeholder="comando (ver prueba, efecto aristas…)" size="30"><button onclick="mandar()">enviar</button></header>
<main>
<section class="p c4"><h2>Cámara passthrough <span class="age" id="a_cam"></span></h2><div id="v_cam"></div><div class="mu" id="t_cam"></div></section>
<section class="p c4"><h2>Render del proyector <span class="age" id="a_ren"></span></h2><div id="v_ren"></div><div class="mu" id="t_ren"></div></section>
<section class="p c4"><h2>Vista del casco (screencap) <span class="age" id="a_scr"></span></h2><div id="v_scr"></div></section>
<section class="p c4"><h2>Estado de la app <span class="age" id="a_est"></span></h2><table id="t_est"></table></section>
<section class="p c4"><h2>Pose · tracking · MRUK</h2><table id="t_pose"></table></section>
<section class="p c4"><h2>Displays · foco · sistema</h2><table id="t_sis"></table></section>
<section class="p c6" id="s3d"><h2>Escena 3D (malla + cabeza + proyector) <span class="age" id="a_3d"></span></h2><div id="v_3d"><div class="vacio">Necesita el servidor de la app (:8787, fase 2). Por ahora: profundidad en texto →</div></div></section>
<section class="p c6"><h2>Profundidad · cámara · calibración</h2><table id="t_prof"></table></section>
<section class="p c7"><h2>Log en vivo (Proyector) <span class="age"><label><input type="checkbox" id="soloerr"> solo W/E</label></span></h2><div class="log" id="log"></div></section>
<section class="p c5"><h2>Comandos periódicos · archivos</h2><div id="per"></div><div id="errs" class="er" style="margin:6px 0"></div><div class="gal" id="gal"></div></section>
</main>
<script>
const $=id=>document.getElementById(id);let D={},last={};
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const edad=t=>{if(!t)return'';const s=Date.now()/1000-t;return s<60?s.toFixed(0)+' s':s<3600?(s/60).toFixed(0)+' min':(s/3600).toFixed(1)+' h'};
const fila=(k,v,cl)=>`<tr><td>${esc(k)}</td><td class="${cl||''}">${v}</td></tr>`;
function img(div,src,key){const el=$(div);if(!src){return}
  if(last[div]===key)return;last[div]=key;
  const im=new Image();im.className='v';im.onload=()=>{el.innerHTML='';el.appendChild(im)};im.src=src}
function vacio(div,msg){if(last[div]!=='vacio:'+msg){last[div]='vacio:'+msg;$(div).innerHTML=`<div class="vacio">${esc(msg)}</div>`}}
function pill(id,txt,cl){const e=$(id);e.textContent=txt;e.className='pill '+(cl||'')}
async function mandar(c){c=c||$('cmd').value;if(!c)return;await fetch('/api/cmd?c='+encodeURIComponent(c));$('cmd').value=''}
$('cmd').addEventListener('keydown',e=>{if(e.key==='Enter')mandar()});
async function tog(k){await fetch('/api/toggle?k='+encodeURIComponent(k));tick()}
async function tick(){
 try{D=await (await fetch('/api/estado')).json()}catch(e){pill('adb','monitor caído','er');return}
 if(window.VER&&D.version&&D.version!==window.VER){location.reload();return}window.VER=window.VER||D.version;
 const E=D.estado||{},L=D.lineas||{},A=D.app_estado,F=D.foco||{},C=D.cache||{};
 $('hora').textContent=new Date().toLocaleTimeString();
 pill('adb',D.adb_ok?'adb ok':'adb sin conexión',D.adb_ok?'ok':'er');
 pill('http',D.app_http?'app :8787 en vivo':'app :8787 no (solo adb)',D.app_http?'ok':'');
 pill('foco',F.app_tiene_foco?'app con foco':F.app_resumida?'app resumida, SIN foco (cámara bloqueada)':'app no está al frente',F.app_tiene_foco?'ok':'wa');
 pill('cal','calib: '+(E.fase||'?'),E.fase==='LISTO'?'ok':E.fase==='ERROR'?'er':'');
 const hd=(D.displays||[]).filter(d=>d.hdmi);pill('hdmi',hd.length?'HDMI '+hd[0].w+'x'+hd[0].h:'sin HDMI',hd.length?'ok':'');
 // imágenes
 if(D.app_http){img('v_cam','/app/cam.jpg?t='+Date.now(),'live'+Math.floor(Date.now()/1000));img('v_ren','/app/render.jpg?t='+Date.now(),'live'+Math.floor(Date.now()/1000));$('a_cam').textContent='en vivo';$('a_ren').textContent='en vivo'}
 else{
  if(C['camara_debug.jpg'])img('v_cam','/img/camara_debug.jpg?t='+C['camara_debug.jpg'],C['camara_debug.jpg']);else vacio('v_cam','sin camara_debug.jpg todavía');
  $('a_cam').textContent=C['camara_debug.jpg']?'hace '+edad(C['camara_debug.jpg']):'';
  const r=C['prueba_monitor.png']?'prueba_monitor.png':Object.keys(C).filter(n=>n.startsWith('prueba_')).sort((a,b)=>C[b]-C[a])[0];
  if(r){img('v_ren','/img/'+r+'?t='+C[r],r+C[r]);$('a_ren').textContent=r+' · hace '+edad(C[r])}else vacio('v_ren','sin captura de la salida');
 }
 $('t_cam').textContent=(L.debug||{}).m||'';
 $('t_ren').textContent=(E.salida||'')+(E.modo?' · '+E.modo:'');
 if(C['_screencap.jpg']){img('v_scr','/img/_screencap.jpg?t='+C['_screencap.jpg'],C['_screencap.jpg']);$('a_scr').textContent='hace '+edad(C['_screencap.jpg'])}else vacio('v_scr',D.screencap_nota||(D.periodicos?.screencap?.activo?'esperando screencap':'screencap apagado: en Quest 3 sale vacío (display seguro). Actívalo abajo para reintentar.'));
 // estado
 let h='';const fps=(v,ok)=>v==null?'—':`<span class="${v>=ok?'ok':'wa'}">${v} fps</span>`;
 if(A){h+=fila('fuente','<span class="ok">app :8787</span>');}
 h+=fila('calibración',esc(E.calib||'—'),E.fase==='ERROR'?'er':'');
 h+=fila('render',fps(A?.render?.fps?.toFixed?.(0)??E.render_fps,55));
 h+=fila('cámara',esc(E.camara||'—')+(E.cam_fps!=null?'':''),/falló|no/.test(E.camara||'')?'er':'');
 h+=fila('salida',esc(E.salida||'—'));h+=fila('sala',esc(E.sala||'—'),/SIN|error/.test(E.sala||'')?'wa':'');
 h+=fila('modelo',esc(E.modelo||'—')+(E.modelo_det?'<br><span class="mu">'+esc(E.modelo_det)+'</span>':''));
 h+=fila('modo',esc(E.modo||'—'));h+=fila('ajuste',esc(E.ajuste||'—'));if(E.gl)h+=fila('GL',esc(E.gl),'er');
 $('t_est').innerHTML=h;$('a_est').textContent=D.estado_ts?'hace '+edad(D.estado_ts):'';
 // pose
 h='';
 if(A){const P=A.cabeza;const f=v=>v.map(x=>x.toFixed(3)).join(', ');
  if(P)h+=fila('cabeza t',f(P.t))+fila('cabeza q',f(P.q));
  if(A.camara?.pose)h+=fila('cámara t',f(A.camara.pose.t));
  for(const [k,v] of Object.entries(A.controles||{}))h+=fila('control '+k,v?f(v.t):'—');
  h+=fila('anclas MRUK',(A.mruk?.anclas||[]).length+' · '+esc((A.mruk?.anclas||[]).map(a=>a.etiqueta).join(', ')));
  h+=fila('triángulos',esc(A.mruk?.triangulos_mruk+' MRUK · '+A.mruk?.triangulos_prof+' prof.'));
 }else{h+=fila('cabeza','<span class="mu">sin feed (la app aún no expone la pose; fase 2)</span>');}
 h+=fila('sala',esc((L.sala||{}).m||'—'));h+=fila('raycaster',esc((L.raycaster||{}).m||'—'));
 $('t_pose').innerHTML=h;
 // sistema
 h='';const ds=D.displays||[];const sec=ds.filter(d=>/MirrorRoot|AndroidPanelLayer/.test(d.nombre));
 if(sec.length)h+=fila('otros',`${sec.length} displays internos (MirrorRoot / paneles del sistema)`,'mu');
 for(const d of ds.filter(d=>!sec.includes(d)))h+=fila('display',`${esc(d.nombre)} ${d.w}x${d.h}${d.hz?' @'+d.hz:''} <span class="mu">${esc(d.flags.join(' '))}</span>`,d.hdmi?'ok':'');
 h+=fila('foco ventana',esc((F.window||[]).join('\n')).replace(/\n/g,'<br>'));h+=fila('actividad',esc((F.activity||[]).join('\n')).replace(/\n/g,'<br>'));
 const Si=D.sistema||{};h+=fila('wifi',esc(Si.wifi||''));h+=fila('pid app',esc(Si.pid||'no corre'),Si.pid?'':'er');h+=fila('cpu',esc(Si.top||''));h+=fila('memoria',esc(Si.mem||''));h+=fila('batería',esc(Si.bateria||''));
 for(const t of Si.termico||[])h+=fila('térmico',esc(t));
 $('t_sis').innerHTML=h;
 // profundidad / cámara / calib
 h='';for(const k of ['prof','debug','intrinsecos','eje','cam_sesion','cam_primer','cam_base','cam_error','cam_err2','salida','gl_destino','escena','permisos','prueba','cal'])if(L[k])h+=fila(k,esc(L[k].t.slice(6))+' '+esc(L[k].m),/error|CAMERA_DISABLED|falló/.test(L[k].m)?'er':'');
 const K=D.calib;if(K){h+=fila('calib json',esc(JSON.stringify({tipo:K.tipo,fecha:K.fecha,gray:K.gray_mediana_px,lazo:K.lazo_mediana_px,lat:K.latencia_lazo_ms,salida:K.salida}).slice(0,400)))}else h+=fila('calib json','<span class="mu">no hay calib_proyector.json (sin calibrar)</span>');
 $('t_prof').innerHTML=h;
 // log
 const se=$('soloerr').checked;const lg=$('log');const abajo=lg.scrollTop+lg.clientHeight>=lg.scrollHeight-30;
 lg.innerHTML=(D.log||[]).filter(l=>!se||/[WEF]/.test(l.n)).slice(-60).map(l=>`<div class="${l.n}"><span class="t">${esc(l.t.slice(6))}</span> ${l.n} ${esc(l.tag!=='Proyector'?l.tag+': ':'')}${esc(l.m)}</div>`).join('');
 if(abajo)lg.scrollTop=lg.scrollHeight;
 // periódicos
 h='';for(const [k,v] of Object.entries(D.periodicos||{}))h+=`<button class="${v.activo?'on':''}" onclick="tog('${k}')">${esc(k)} /${v.periodo}s ${v.ultimo?'· '+edad(v.ultimo):''}</button> `;
 $('per').innerHTML=h+' <span class="mu">(se pausan mientras calibra)</span>';
 $('errs').innerHTML=(D.errores||[]).slice(-4).map(esc).join('<br>');
 const ims=(D.archivos||[]).filter(a=>/\.(jpg|png)$/i.test(a.n)).slice(0,24);
 const gk=ims.map(a=>a.n+a.m).join();if(last.gal!==gk){last.gal=gk;$('gal').innerHTML=ims.map(a=>`<figure><a href="/img/${a.n}" target="_blank"><img loading="lazy" src="/img/${a.n}?t=${a.m}"></a><figcaption>${esc(a.n)} · ${edad(a.m)}</figcaption></figure>`).join('')}
}
tick();setInterval(tick,2000);
</script></body></html>"""


def main():
    app = web.Application()
    app.router.add_get("/", h_index)
    app.router.add_get("/api/estado", h_estado)
    app.router.add_get("/api/cmd", h_cmd)
    app.router.add_post("/api/cmd", h_cmd)
    app.router.add_get("/api/toggle", h_toggle)
    app.router.add_get("/img/{n:.+}", h_img)
    app.router.add_get("/app/{r:.*}", h_proxy)
    app.router.add_get("/{r:.*}", h_estatico)
    app.on_startup.append(al_iniciar)
    print(f"monitor en http://127.0.0.1:{PUERTO}/  (casco {SERIAL}, app {APP_URL})", flush=True)
    web.run_app(app, host="127.0.0.1", port=PUERTO, print=None)


if __name__ == "__main__":
    sys.exit(main())

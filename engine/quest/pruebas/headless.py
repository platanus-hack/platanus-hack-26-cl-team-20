"""Pruebas sin casco con Chrome headless (sin ventanas en el monitor) vía DevTools Protocol.

    .venv/bin/python quest/server.py --sin-adb &          # o el servidor ya corriendo
    .venv/bin/python quest/pruebas/headless.py             # todos los escenarios
    .venv/bin/python quest/pruebas/headless.py sim e2e     # algunos

Escenarios: smoke (carga sin errores de consola), sim (calibración sintética con el escaneo), xr (camino del casco con
un WebXR falso: sesión, malla/planos, panel, toques con el control, pozo, ajuste), e2e (video WebRTC
quest.html?sim=1 → proyector.html), peerjs (lo mismo con señalización PeerJS cloud; necesita internet).
Capturas en quest/pruebas/capturas/, resumen en quest/pruebas/resultados/headless.json.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets

AQUI = Path(__file__).resolve().parent
CAPT = AQUI / "capturas"
RES = AQUI / "resultados"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PERFIL = "/Volumes/Externo/cache/chrome-test"


class CDP:
    def __init__(self, ws):
        self.ws, self.n, self.pend, self.oyentes = ws, 0, {}, []

    async def bucle(self):
        async for raw in self.ws:
            m = json.loads(raw)
            if "id" in m and m["id"] in self.pend:
                self.pend.pop(m["id"]).set_result(m)
            else:
                for f in self.oyentes:
                    f(m)

    async def send(self, metodo, params=None, sesion=None, timeout=60):
        self.n += 1
        msg = {"id": self.n, "method": metodo, "params": params or {}}
        if sesion:
            msg["sessionId"] = sesion
        fut = asyncio.get_running_loop().create_future()
        self.pend[self.n] = fut
        await self.ws.send(json.dumps(msg))
        r = await asyncio.wait_for(fut, timeout)
        if "error" in r:
            raise RuntimeError(f"{metodo}: {r['error']}")
        return r.get("result", {})


class Pagina:
    def __init__(self, cdp: CDP, nombre: str):
        self.cdp, self.nombre, self.sesion, self.consola, self.errores = cdp, nombre, None, [], []
        cdp.oyentes.append(self._evento)

    def _evento(self, m):
        if m.get("sessionId") != self.sesion:
            return
        met, p = m.get("method"), m.get("params", {})
        if met == "Runtime.consoleAPICalled":
            txt = " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))
            self.consola.append(f"{p.get('type')}: {txt}")
            if p.get("type") == "error":
                self.errores.append(txt)
        elif met == "Runtime.exceptionThrown":
            d = p["exceptionDetails"]
            self.errores.append(f"excepción: {d.get('exception', {}).get('description', d.get('text'))}")
        elif met == "Log.entryAdded" and p["entry"]["level"] == "error":
            e = p["entry"]
            self.errores.append(f"log: {e.get('text')} {e.get('url', '')}")

    async def abrir(self, url, script=None):
        t = await self.cdp.send("Target.createTarget", {"url": "about:blank"})
        self.target = t["targetId"]
        self.sesion = (await self.cdp.send("Target.attachToTarget", {"targetId": self.target, "flatten": True}))["sessionId"]
        for d in ("Runtime.enable", "Log.enable", "Page.enable"):
            await self.cdp.send(d, sesion=self.sesion)
        if script:
            await self.cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": script}, sesion=self.sesion)
        await self.cdp.send("Emulation.setDeviceMetricsOverride",
                            {"width": 1600, "height": 900, "deviceScaleFactor": 1, "mobile": False}, sesion=self.sesion)
        await self.cdp.send("Page.navigate", {"url": url}, sesion=self.sesion)
        return self

    async def eval(self, expr, timeout=60):
        r = await self.cdp.send("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True},
                                sesion=self.sesion, timeout=timeout)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", r["exceptionDetails"]))
        return r["result"].get("value")

    async def esperar(self, expr, timeout=60, cada=0.25):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                v = await self.eval(expr)
                if v:
                    return v
            except RuntimeError:
                pass
            await asyncio.sleep(cada)
        raise TimeoutError(f"{self.nombre}: no se cumplió {expr!r} en {timeout}s")

    async def captura(self, nombre):
        CAPT.mkdir(parents=True, exist_ok=True)
        r = await self.cdp.send("Page.captureScreenshot", {"format": "png"}, sesion=self.sesion)
        p = CAPT / f"{nombre}.png"
        p.write_bytes(base64.b64decode(r["data"]))
        return str(p.relative_to(AQUI.parent))

    async def cerrar(self):
        await self.cdp.send("Target.closeTarget", {"targetId": self.target})


def lanzar_chrome(puerto):
    args = [CHROME, "--headless=new", f"--user-data-dir={PERFIL}", f"--remote-debugging-port={puerto}",
            "--window-size=1600,900", "--no-first-run", "--no-default-browser-check", "--mute-audio",
            "--autoplay-policy=no-user-gesture-required", "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
            "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "about:blank"]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/json/version", timeout=1) as r:
                return proc, json.load(r)["webSocketDebuggerUrl"]
        except Exception:
            time.sleep(0.25)
    proc.kill()
    raise RuntimeError("Chrome headless no levantó")


async def smoke(cdp, base, out):
    q = await Pagina(cdp, "quest").abrir(f"{base}/quest.html")
    p = await Pagina(cdp, "proyector").abrir(f"{base}/proyector.html?debug=1")
    await q.esperar("window.__capacidades")
    await asyncio.sleep(2.5)
    out["smoke"] = {
        "quest_errores": q.errores, "proyector_errores": p.errores,
        "capacidades_chrome_mac": await q.eval("window.__capacidades"),
        "capturas": [await q.captura("smoke-quest"), await p.captura("smoke-proyector")],
    }
    await q.cerrar(); await p.cerrar()


async def sim(cdp, base, out):
    res = {}
    for nombre, qs in (("cabeza_ruido1cm", "modo=cabeza&ruido=1"), ("cabeza_sin_ruido", "modo=cabeza&ruido=0"),
                       ("fijo_ruido1cm", "modo=fijo&ruido=1"), ("cabeza_20pts_ruido1cm", "modo=cabeza&ruido=1&puntos=20")):
        q = await Pagina(cdp, f"sim-{nombre}").abrir(f"{base}/quest.html?sim=1&senal=no&{qs}")
        await q.esperar("window.__simListo", 90)
        await q.eval("localStorage.clear(), window.__app.proyCam.restablecer(), true")
        await asyncio.sleep(1.0)
        caps = []
        if nombre == "cabeza_ruido1cm":
            caps.append(await q.captura("sim-1-antes-sin-calibrar"))
        r = await q.eval("window.__app.calibracionSintetica()", timeout=120)
        await asyncio.sleep(1.0)
        if nombre != "cabeza_sin_ruido":
            # varias semillas: mediana del error del modelo (una sola corrida con ruido es anecdótica)
            serie = []
            for k in range(1, 9):
                rk = await q.eval(f"window.__app.calibracionSintetica({{semilla: {k}}})", timeout=120)
                if rk and rk.get("ok"):
                    serie.append(rk["errorModelo2mPx"]["rms"])
            await q.eval("window.__app.calibracionSintetica()", timeout=120)  # deja la semilla por defecto para las capturas
            await asyncio.sleep(0.8)
            serie.sort()
            res[f"{nombre}_8semillas_error_modelo_2m_px"] = {"mediana": serie[len(serie) // 2] if serie else None,
                                                             "min": serie[0] if serie else None, "max": serie[-1] if serie else None}
        if nombre in ("cabeza_ruido1cm", "fijo_ruido1cm"):
            caps.append(await q.captura(f"sim-2-{nombre}-calibrado-wireframe"))
        if nombre == "cabeza_ruido1cm":
            await q.eval("window.__app.efecto('aristas'), true"); await asyncio.sleep(0.6)
            caps.append(await q.captura("sim-3-aristas"))
            await q.eval("window.__app.efecto('mesa'), true"); await asyncio.sleep(0.6)
            caps.append(await q.captura("sim-4-mesa"))
            ok = await q.eval("window.__app.pozoEnMesa()")
            await asyncio.sleep(0.6)
            caps.append(await q.captura("sim-5-pozo"))
            await q.eval("window.__app.iniciarCalibracion(), true"); await asyncio.sleep(0.6)
            caps.append(await q.captura("sim-6-cruz-calibracion"))
            res["pozo_colocado"] = ok
        res[nombre] = {"resultado": r, "autotest_proyeccion_px": await q.eval("window.__autotestProyeccion"),
                       "errores": q.errores, "capturas": caps}
        await q.cerrar()
    out["sim"] = res


async def e2e(cdp, base, out, senal="ws", sala="sala"):
    extra = f"&senal={senal}&sala={sala}" if senal != "ws" else f"&sala={sala}"
    p = await Pagina(cdp, f"e2e-proyector-{senal}").abrir(f"{base}/proyector.html?debug=1{extra}")
    await asyncio.sleep(1.5 if senal == "ws" else 4)
    q = await Pagina(cdp, f"e2e-quest-{senal}").abrir(f"{base}/quest.html?sim=1{extra}")
    await q.esperar("window.__simListo", 90)
    t0 = time.time()
    try:
        st = await p.esperar("window.__statsProyector && window.__statsProyector.decodificados > 30 && window.__statsProyector", 60)
        t_video = round(time.time() - t0, 1)
    except TimeoutError as e:
        st, t_video = {"error": str(e)}, None
    # cabeza en movimiento: contenido que cambia cada cuadro (si no, H.264 manda casi nada)
    await q.eval("setInterval(() => { const s = window.__app.sim; s.cabeza.yaw = 15 * Math.sin(performance.now() / 600); }, 16), true")
    por_efecto = {}
    for ef in ("mesa", "aristas", "wireframe"):
        await q.eval(f"window.__app.efecto('{ef}'), true")
        await asyncio.sleep(8)
        por_efecto[ef] = {"proyector": await p.eval("window.__statsProyector"), "emisor": await q.eval("window.__statsEmisor || null")}
    stp = por_efecto["wireframe"]["proyector"]
    ste = por_efecto["wireframe"]["emisor"]
    out[f"e2e_{senal}"] = {
        "segundos_hasta_video": t_video, "proyector": stp, "emisor": ste, "por_efecto_cabeza_moviendose": por_efecto,
        "fuente_video": await q.eval("window.__app.fuente.tipo"),
        "quest_errores": q.errores, "proyector_errores": p.errores,
        "capturas": [await p.captura(f"e2e-{senal}-proyector"), await q.captura(f"e2e-{senal}-quest-sim")],
    }
    await q.cerrar(); await p.cerrar()


JS_TOQUES = r"""
(async () => {
  const M = window.__xrMock, A = window.__app;
  const espera = (ms) => new Promise((r) => setTimeout(r, ms));
  const Kverdad = A.proyCam.K, Rtverdad = A.proyCam.Rt;
  let rnd = 1; const azar = () => { rnd = (rnd * 16807) % 2147483647; return rnd / 2147483647 - 0.5; };
  const p0 = -30 * Math.PI / 180;
  let vueltas = 0, saltadas = 0;
  while (A.estado.calib.activa && vueltas++ < 40) {
    // la cabeza se mueve un poco entre toques (modo cabeza: la cruz va pegada a la cabeza)
    const yaw = azar() * 0.1, p = p0 + azar() * 0.1;
    M.cabeza = M.mirando([azar() * 0.05, 1.6 + azar() * 0.03, azar() * 0.05], [Math.sin(yaw) * Math.cos(p), Math.sin(p), -Math.cos(yaw) * Math.cos(p)]);
    await espera(150);
    const c = A.estado.calib;
    const [u, v] = c.cruces[c.idx];
    const r = A.proyCam.rayo(u, v);
    const hit = A.sala.raycast(r.origin, r.direction);
    if (!hit) { saltadas++; A.panel.botones.find((b) => b.id === 'saltar').accion(); continue; }
    // control apuntando hacia la superficie, con la punta (offset en el espacio del rayo) justo en el punto
    const d = [0, -0.8, -0.6].map((x) => x / 1.0);
    const m = M.mirando([0, 0, 0], d);
    const pt = A.estado.punta;
    const off = [0, 1, 2].map((k) => m[k] * pt[0] + m[4 + k] * pt[1] + m[8 + k] * pt[2]);
    const C = [hit.punto.x - off[0], hit.punto.y - off[1], hit.punto.z - off[2]];
    M.der.m = M.mirando(C, d);
    await espera(420);
    await M.apretar('der', 0);
  }
  await espera(300);
  const info = A.proyCam.info;
  return { calibrado: A.proyCam.calibrado, info, saltadas, Kverdad, Kest: A.proyCam.K,
           dRdeg: (() => { const a = A.proyCam.Rt.R, b = Rtverdad.R; let tr = 0; for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) tr += a[i][j] * b[i][j]; return Math.acos(Math.min(1, (tr - 1) / 2)) * 180 / Math.PI; })(),
           dt_cm: Math.hypot(...A.proyCam.Rt.t.map((x, i) => x - Rtverdad.t[i])) * 100 };
})()
"""


async def xr(cdp, base, out):
    mock = (AQUI / "xr-mock.js").read_text()
    q = await Pagina(cdp, "xr").abrir(f"{base}/quest.html?senal=no", script=mock)
    await q.esperar("window.__app && window.__capacidades && !document.getElementById('entrar').disabled", 20)
    await q.eval("localStorage.clear(), window.__app.proyCam.restablecer(), document.getElementById('entrar').click(), true")
    await q.esperar("window.__app.estado.xr.session && window.__xrMock.cuadros > 30", 20)
    await asyncio.sleep(0.8)
    r = {"inicio": await q.eval("({sala: window.__app.sala.stats(), features: window.__app.estado.xr.features, ref: window.__app.estado.xr.refTipo, "
                                "depth: window.__app.estado.xr.depth, panelVisible: window.__app.panel.mesh.visible, cuadros: window.__xrMock.cuadros})")}
    # clic en el botón Calibrar del panel con el rayo derecho + gatillo
    r["clic_panel_calibrar"] = await q.eval("""(async () => { const M = window.__xrMock; const Q = window.__app.puntoBoton('calibrar');
        const C = [Q[0] + 0.1, Q[1] - 0.12, Q[2] + 0.3]; M.der.m = M.mirando(C, [Q[0] - C[0], Q[1] - C[1], Q[2] - C[2]]);
        await new Promise((ok) => setTimeout(ok, 250)); await M.apretar('der', 0); return window.__app.estado.calib.activa; })()""")
    await asyncio.sleep(0.3)
    caps = [await q.captura("xr-1-cruz-panel")]
    r["toques"] = await q.eval(JS_TOQUES, timeout=180)
    # botón A apuntando a la mesa → pozo; X izq → modo de ajuste; stick izq → giro
    r["pozo"] = await q.eval("""(async () => { const M = window.__xrMock, A = window.__app;
        A.efecto('pozo'); M.der.m = M.mirando([0.1, 1.3, -0.4], [0, -0.55, -0.8]);
        await new Promise((ok) => setTimeout(ok, 300)); await M.apretar('der', 4); return !!A.sala.pozo; })()""")
    r["ajuste"] = await q.eval("""(async () => { const M = window.__xrMock, A = window.__app; const y0 = A.proyCam.ajuste.yaw;
        await M.apretar('izq', 4); M.izq.src.gamepad.axes[2] = 1; await new Promise((ok) => setTimeout(ok, 500)); M.izq.src.gamepad.axes[2] = 0;
        const out = { modo: A.estado.ajusteModo, dyaw: A.proyCam.ajuste.yaw - y0 }; await M.apretar('izq', 4); await M.apretar('izq', 4); await M.apretar('izq', 4); await M.apretar('izq', 4); return out; })()""")
    await asyncio.sleep(0.5)
    await q.eval("document.getElementById('vista').style.display = 'block', document.getElementById('ui').style.display = 'none', true")
    await asyncio.sleep(0.5)
    caps.append(await q.captura("xr-2-vistas-casco-pozo"))
    r["fin"] = await q.eval("(window.__xrMock.sesion.end(), new Promise((ok) => setTimeout(() => ok(window.__app.estado.xr.session === null), 300)))")
    r["errores"] = q.errores
    r["capturas"] = caps
    out["xr_mock"] = r
    await q.cerrar()


async def main_async(a):
    proc, wsurl = lanzar_chrome(a.puerto_cdp)
    out = {"base": a.base, "fecha": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        async with websockets.connect(wsurl, max_size=64 << 20) as ws:
            cdp = CDP(ws)
            tarea = asyncio.create_task(cdp.bucle())
            out["chrome"] = (await cdp.send("Browser.getVersion"))["product"]
            for esc in a.escenarios:
                t0 = time.time()
                try:
                    if esc == "smoke":
                        await smoke(cdp, a.base, out)
                    elif esc == "sim":
                        await sim(cdp, a.base, out)
                    elif esc == "e2e":
                        await e2e(cdp, a.base, out)
                    elif esc == "xr":
                        await xr(cdp, a.base, out)
                    elif esc == "peerjs":
                        await e2e(cdp, a.base, out, senal="peerjs", sala=f"prueba-{int(time.time()) % 100000}")
                except Exception as e:  # noqa: BLE001
                    out[f"{esc}_fallo"] = f"{type(e).__name__}: {e}"
                print(f"· {esc}: {time.time() - t0:.1f}s", flush=True)
            tarea.cancel()
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
    RES.mkdir(parents=True, exist_ok=True)
    (RES / "headless.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps(out, indent=1, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("escenarios", nargs="*", default=["smoke", "sim", "xr", "e2e"])
    ap.add_argument("--base", default="http://localhost:8800")
    ap.add_argument("--puerto-cdp", type=int, default=9333)
    a = ap.parse_args()
    asyncio.run(main_async(a))


if __name__ == "__main__":
    sys.exit(main())

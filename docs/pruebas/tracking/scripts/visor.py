"""Observador de solo lectura del visor /view. Solo manda el token; nunca pide foto, cámara ni escena."""
import asyncio, json, sys, time, os
import websockets

URL = "wss://pcx-gateway.onrender.com/view"
TOKEN = os.environ["PCX_VIEW_TOKEN"]
OUT = sys.argv[1]
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 25
EVERY = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0  # guarda un cuadro cada N segundos


async def main():
    os.makedirs(OUT, exist_ok=True)
    log = open(os.path.join(OUT, "mensajes.jsonl"), "a")
    t0 = time.time(); last_saved = 0; nframes = 0; nbytes = 0; kinds = {}
    async with websockets.connect(URL, max_size=4_000_000, user_agent_header="pcx-visor-lectura") as ws:
        await ws.send(json.dumps({"token": TOKEN}))
        while time.time() - t0 < SECS:
            try:
                msg = await asyncio.wait_for(ws.recv(), 5)
            except asyncio.TimeoutError:
                continue
            now = time.time()
            if isinstance(msg, bytes):
                nframes += 1; nbytes += len(msg)
                if now - last_saved >= EVERY:
                    last_saved = now
                    open(os.path.join(OUT, f"cuadro-{now:.2f}.jpg"), "wb").write(msg)
                continue
            try:
                m = json.loads(msg)
            except ValueError:
                continue
            k = m.get("kind", "?"); kinds[k] = kinds.get(k, 0) + 1
            if k == "hands" and kinds[k] % 15 != 1:
                continue  # las manos llegan a 15 Hz: basta una muestra por segundo
            log.write(json.dumps({"t": round(now, 3), **m}, ensure_ascii=False) + "\n")
    el = time.time() - t0
    print(json.dumps({"segundos": round(el, 1), "cuadros": nframes, "fps": round(nframes / el, 1), "kB_por_cuadro": round(nbytes / max(1, nframes) / 1024, 1), "mensajes": kinds}))


asyncio.run(main())

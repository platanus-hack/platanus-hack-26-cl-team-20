"""Motor falso de pcx: habla el protocolo motor v0.1 (cámara → segmentación → objetos en mm) para probar la consola
sin el motor Rust, sin cámara y sin proyector. Nunca accede a dispositivos: su «cámara» es un JPEG sintético (o el
que le pases con --jpeg) y, con --sin-camara, le pide los cuadros a la consola (need_frame).

    cd cloud/gateway && uv run python -m tools.fake_motor --token secreto
    # consola local:  http://localhost:9215/mesa?sim=1&motor=ws://127.0.0.1:9230&motor_token=secreto

Qué hace: valida Origin y token, responde `welcome`, guarda `calib` y corre un ciclo de percepción falso cuando las
manos se van (250 ms de calma), con `perceive` y cada --ciclo-s: `blank` → espera `blanked` → «captura» → `unblank`
→ `objects` incremental (una taza y un libro que aparecen, se mueven y se van) → `status`. `look` responde
`look_frame` (con `clean: true`, después de su propio blank/unblank).

v0.1: `welcome` trae `scenes` (lab, solar, desk) y `cameras` (cenital y lateral), y justo después un `objects` con
todos los objetos vigentes (la memoria sobrevive a las reconexiones). `scene {name}` activa un módulo; con `lab`
manda `event {scene, event: "nivel", data}` al activarse y en cada ciclo.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hmac
import itertools
import json
import math
import os
import re
import secrets
import struct
import time
from contextlib import suppress
from dataclasses import dataclass, field

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

VERSION = "fake-motor-0"
# Orígenes permitidos del protocolo: la consola desplegada y cualquier puerto de localhost / 127.0.0.1.
ALLOWED = re.compile(r"^(https://pcx-gateway\.onrender\.com|http://(localhost|127\.0\.0\.1)(:\d+)?)$")

# JPEG sintético de 160×90 (mesa gris, una «taza» y un «libro»), sin metadatos: no viene de ninguna cámara.
FAKE_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAASABIAAD/wAARCABaAKADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QA"
    "tRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6"
    "Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJ"
    "ytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQD"
    "BAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElK"
    "U1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX"
    "2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9sAQwAEBAQEBAQGBAQGCQYGBgkMCQkJCQwPDAwMDAwPEg8PDw8PDxISEhISEhISFRUVFRUV"
    "GRkZGRkcHBwcHBwcHBwc/9sAQwEEBQUHBwcMBwcMHRQQFB0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0d"
    "HR0dHR0dHR0d/90ABAAK/9oADAMBAAIRAxEAPwD5xooorE0CiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKAP/9D5"
    "xooorE0CiiigAoq/pel6hrWoQaVpUDXN3ctsjjTqT178AAcknAABJIAr6n0f9nXRUsl/t/UrmW8bBb7JsjjXIGVG9HZsNn5vlyMf"
    "KKaTYm0j5Jor7Avf2dfC8ls6adqV7BcHGx5vLlQcjOUVIycjIHzDB556H5d8S+GtX8J6vLo2sxeXPHyrDlJEOdro2BlTj6g5BAII"
    "A00CaZg0UUUhhRRVu1sbq83fZk37MZ5A69OpHpSbSV2XCEpy5YK78ipRWr/Ymp/88f8Ax5f8aP7E1P8A54/+PL/jWftYfzL7zp+o"
    "4n/n1L7mZVFav9ian/zx/wDHl/xo/sTU/wDnj/48v+NHtYfzL7w+o4n/AJ9S+5mVRT5EaJ2jcYZCVI9x1planI007MKKKKBH/9H5"
    "xooorE0CiiigD62/Z10eyTRdS1/buvJbn7JuIB2xxoj4U43Dcz/Nzg7V44r6Or57/Z1vbaTwvqWnI+biC9850weEljRUOcYOTG3A"
    "ORjnqK+hK2jsYy3CvGvjro9lf+A7jUp1/wBI0ySKSFwBn95IsTqSQTtIbJAIyVUnpivZa8k+OF7bWvw6v4J32veSQQwjBO5xKshH"
    "A4+RGOTgcY6kU3sJbnwtRRRWBuFdV4Z/5ef+Af8As1crXVeGf+Xn/gH/ALNXNiv4TPZyb/fafz/JnVUUUV4R+khRRRQB5tf/APH9"
    "cf8AXV//AEI1Uq3f/wDH9cf9dX/9CNVK+kj8KPyOv/Fl6sKKKKoxP//S+caKKKxNAooooA3vDXiXV/Cerxazo0vlzx8Mp5SRDjcj"
    "rkZU4+oOCCCAR9g6P8dfAd/ZLPqVxJplxwHhkikk5wCSrxKwK5JAJ2k4yVHFfEFFUm0S0mfdN78cPh1a2zzwX8l464xDDBKHbJA4"
    "MionHU5YcDjJ4r5J8b+N9X8c6udR1E+XBHlba2U5SFD2HTLHA3NjJPoAAOMoocmwUUgoooqSgrqvDP8Ay8/8A/8AZq5Wul8PTwQf"
    "aPOkWPdsxuIGcZ9a5sSr0nY9fJ5KOMg2+/5M6+iqv2+x/wCfiL/vsf40fb7H/n4i/wC+x/jXi8kux+i+3p/zL7y1RVX7fY/8/EX/"
    "AH2P8aPt9j/z8Rf99j/Gjkl2D29P+ZfecBf/APH9cf8AXV//AEI1Uqzesr3k7KQVMjEEdCMmq1fQx+FH5TW/iS9WFFFFUYn/0/nG"
    "iiisTQKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooA//1PnGiiisTQKKKKACiiigAooooAKKKKACiiigAooooAKK"
    "KKACiiigAooooA//2Q=="
)


def jpeg_size(data: bytes) -> tuple[int, int]:
    """Ancho y alto de un JPEG (lee el marcador SOF)."""
    i = 2
    while i + 9 < len(data) and data[i] == 0xFF:
        marker, length = data[i + 1], int.from_bytes(data[i + 2 : i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5 : i + 9])
            return w, h
        i += 2 + length
    raise ValueError("JPEG sin marcador SOF")


def allowed_origin(origin: str | None) -> bool:
    """Sin Origin es un cliente que no es navegador (lo cuida el token); con Origin, solo los de la lista."""
    return origin is None or bool(ALLOWED.match(origin))


@dataclass
class MotorConfig:
    token: str
    camera: bool = True
    jpeg: bytes = FAKE_JPEG
    cycle_s: float = 45
    calm_ms: int = 250
    blank_ms: int = 1500
    log: object = print
    sessions: list = field(default_factory=list)
    scenes: tuple = ("lab", "solar", "desk")
    # Memoria del motor: sobrevive a las reconexiones de la consola (por eso v0.1 manda todo después de welcome).
    objects: dict = field(default_factory=dict)
    step: int = 0
    rev: int = 0

    def camera_info(self):
        if not self.camera:
            return None
        w, h = jpeg_size(self.jpeg)
        return {"id": "fake-0", "name": "Cámara falsa (JPEG sintético)", "px": [w, h]}

    def cameras(self):
        lateral = {"id": "fake-lat", "role": "lateral", "name": "Lateral falsa"}
        cam = self.camera_info()
        return ([{"id": cam["id"], "role": "cenital", "name": cam["name"]}] if cam else []) + [lateral]


def circle(cx, cy, r, n=16):
    return [
        [round(cx + r * math.cos(2 * math.pi * k / n), 1), round(cy + r * math.sin(2 * math.pi * k / n), 1)]
        for k in range(n)
    ]


def rect(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def brief(msg: dict) -> str:
    """El mensaje para el registro, sin la imagen."""
    short = {k: (f"<{len(v)} b64>" if k == "jpeg_b64" else v) for k, v in msg.items()}
    return json.dumps(short, ensure_ascii=False)


def calib_problem(msg: dict) -> str | None:
    def nums(v, n):
        return isinstance(v, list) and len(v) == n and all(isinstance(x, (int, float)) for x in v)

    if not nums(msg.get("camera_px"), 2):
        return "camera_px debe ser [w, h]"
    if not nums(msg.get("matrix_px_to_mm"), 9):
        return "matrix_px_to_mm debe tener 9 números (por filas)"
    if not nums(msg.get("surface_mm"), 2):
        return "surface_mm debe ser [w, h]"
    lens = msg.get("lens")
    keys = ("f", "cx", "cy", "k1", "k2")
    if lens is not None and not (
        isinstance(lens, dict) and all(isinstance(lens.get(k), (int, float)) for k in keys)
    ):
        return "lens debe ser null o {f, cx, cy, k1, k2}"
    return None


class Session:
    """Una consola conectada."""

    def __init__(self, ws: WebSocket, cfg: MotorConfig):
        self.ws, self.cfg = ws, cfg
        self.lock = asyncio.Lock()
        self.cycle_lock = asyncio.Lock()
        self.ids = itertools.count(1)
        self.waiting: dict[str, asyncio.Future] = {}
        self.tasks: set[asyncio.Task] = set()
        self.calm_task: asyncio.Task | None = None
        self.received: list[dict] = []
        self.calib: dict | None = None
        self.hands: bool | None = None
        self.scene: str | None = None
        self.last_cycle_ms: float | None = None

    def log(self, text):
        self.cfg.log(f"[motor falso] {text}")

    async def send(self, msg: dict):
        async with self.lock:
            await self.ws.send_text(json.dumps(msg))
        if msg["type"] != "status":
            self.log(f"→ {brief(msg)}")

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def expect(self, key: str) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self.waiting[key] = fut
        return fut

    async def wait(self, key: str, fut: asyncio.Future, seconds: float):
        try:
            return await asyncio.wait_for(fut, seconds)
        finally:
            self.waiting.pop(key, None)

    async def status(self, state: str, stages: dict | None = None):
        msg = {
            "type": "status",
            "state": state,
            "last_cycle_ms": self.last_cycle_ms,
            "stages_ms": stages or {},
        }
        await self.send(msg)

    async def run(self):
        cam = self.cfg.camera_info()
        welcome = {
            "type": "welcome",
            "v": 0,
            "motor": VERSION,
            "camera": cam,
            "models": {"sam": False, "hands": False},
            "needs_frames": cam is None,
            "scenes": list(self.cfg.scenes),
            "cameras": self.cfg.cameras(),
        }
        await self.send(welcome)
        everything = list(self.cfg.objects.values())
        await self.send({"type": "objects", "rev": self.cfg.rev, "upsert": everything, "remove": []})
        await self.status("idle")
        if self.cfg.cycle_s > 0:
            self.spawn(self.periodic())
        try:
            while True:
                text = await self.ws.receive_text()
                try:
                    msg = json.loads(text)
                except ValueError:
                    msg = None
                if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
                    await self.send(
                        {"type": "error", "code": "bad_json", "message": "Se espera JSON con `type`."}
                    )
                    continue
                self.received.append(msg)
                await self.handle(msg)
        except WebSocketDisconnect:
            self.log("la consola se desconectó")
        finally:
            for task in list(self.tasks):
                task.cancel()
            for fut in self.waiting.values():
                fut.cancel()

    async def handle(self, msg: dict):
        kind = msg["type"]
        if kind != "frame":
            self.log(f"← {brief(msg)}")
        if kind in ("blanked", "unblanked", "frame"):
            fut = self.waiting.get(f"{kind}:{msg.get('id')}")
            if fut and not fut.done():
                fut.set_result(msg)
        elif kind == "calib":
            problem = calib_problem(msg)
            if problem:
                await self.send({"type": "error", "code": "bad_calib", "message": problem})
            else:
                self.calib = msg
        elif kind == "hands":
            self.hands = bool(msg.get("present"))
            if self.calm_task:
                self.calm_task.cancel()
                self.calm_task = None
            if not self.hands:
                self.calm_task = self.spawn(self.calm())
        elif kind == "perceive":
            self.spawn(self.cycle("perceive"))
        elif kind == "reset":
            self.cfg.objects.clear()
            self.cfg.step = 0
        elif kind == "look":
            self.spawn(self.look(str(msg.get("id")), bool(msg.get("clean"))))
        elif kind == "scene":
            name = msg.get("name")
            if name is not None and name not in self.cfg.scenes:
                await self.send(
                    {"type": "error", "code": "unknown_scene", "message": f"No hay módulo «{name}»."}
                )
                return
            self.scene = name
            await self.scene_event()
        else:
            await self.send({"type": "error", "code": "unknown_type", "message": f"Tipo desconocido: {kind}"})

    async def calm(self):
        await self.status("calm")
        await asyncio.sleep(self.cfg.calm_ms / 1000)
        if self.hands is False:
            self.calm_task = None
            await self.cycle("manos")

    async def periodic(self):
        while True:
            await asyncio.sleep(self.cfg.cycle_s)
            if self.hands is not True:
                await self.cycle("periódico")

    async def clean_capture(self, stages: dict) -> bytes | None:
        """blank → blanked → cuadros posteriores → unblank. None si la consola no confirmó el negro."""
        bid = f"b{next(self.ids)}"
        await self.status("blank")
        t = time.perf_counter()
        fut = self.expect(f"blanked:{bid}")
        await self.send({"type": "blank", "id": bid, "max_ms": self.cfg.blank_ms})
        try:
            blanked = await self.wait(f"blanked:{bid}", fut, self.cfg.blank_ms / 1000)
        except TimeoutError:
            await self.send({"type": "unblank", "id": bid})
            message = f"Sin blanked en {self.cfg.blank_ms} ms."
            await self.send({"type": "error", "code": "blank_timeout", "message": message})
            return None
        stages["blank"] = round((time.perf_counter() - t) * 1000, 1)
        await self.status("capturing", stages)
        t = time.perf_counter()
        frame = None
        if self.cfg.camera:
            await asyncio.sleep(0.1)  # «3 cuadros posteriores a blanked»
            frame = self.cfg.jpeg
        else:
            for k in range(3):
                fid = f"{bid}-f{k + 1}"
                fut = self.expect(f"frame:{fid}")
                await self.send({"type": "need_frame", "id": fid})
                try:
                    got = await self.wait(f"frame:{fid}", fut, 3)
                except TimeoutError:
                    break
                self.log(f"← frame {fid} {got.get('w')}×{got.get('h')}")
                if int(got.get("t_ns") or 0) < int(blanked.get("t_ns") or 0):
                    self.log(f"cuadro {fid} anterior a blanked: descartado")
                    continue
                frame = base64.b64decode(got.get("jpeg_b64") or "")
        stages["captura"] = round((time.perf_counter() - t) * 1000, 1)
        fut = self.expect(f"unblanked:{bid}")
        await self.send({"type": "unblank", "id": bid})
        with suppress(TimeoutError):
            await self.wait(f"unblanked:{bid}", fut, 2)
        return frame

    async def cycle(self, why: str):
        if self.cycle_lock.locked():
            return
        async with self.cycle_lock:
            self.log(f"ciclo ({why})")
            t0 = time.perf_counter()
            stages: dict = {}
            frame = await self.clean_capture(stages)
            if frame is None:
                await self.status("idle", stages)
                return
            await self.status("perceiving", stages)
            t = time.perf_counter()
            await asyncio.sleep(0.15)
            upsert, remove = self.perceive()
            stages["percepcion"] = round((time.perf_counter() - t) * 1000, 1)
            if upsert or remove:
                self.cfg.rev += 1
                await self.send({"type": "objects", "rev": self.cfg.rev, "upsert": upsert, "remove": remove})
            self.last_cycle_ms = round((time.perf_counter() - t0) * 1000, 1)
            await self.scene_event()
            await self.status("idle", stages)

    async def scene_event(self):
        """Módulo de escena de mentira: «lab» mide el agua de la taza."""
        if self.scene != "lab":
            return
        ml = 60 + 40 * (self.cfg.step % 4)
        data = {"vaso": "taza", "ml": ml, "objetivo_ml": 200, "estado": "listo" if ml >= 200 else "llenando"}
        await self.send({"type": "event", "scene": "lab", "event": "nivel", "data": data})

    def perceive(self) -> tuple[list, list]:
        """Percepción de mentira, incremental: aparecen taza y libro, la taza se mueve, el libro se va y vuelve."""
        w, h = self.calib["surface_mm"] if self.calib else (1000, 562)
        step = self.cfg.step if self.cfg.step < 4 else 1 + (self.cfg.step - 1) % 3
        self.cfg.step += 1

        def taza(dx):
            c = [round(0.3 * w + dx, 1), round(0.45 * h, 1)]
            return {
                "id": "taza",
                "label": "taza",
                "polygon_mm": circle(*c, 45),
                "center_mm": c,
                "confidence": 0.9,
            }

        x, y = round(0.6 * w), round(0.35 * h)
        libro = {
            "id": "libro",
            "label": "libro",
            "polygon_mm": rect(x, y, 180, 130),
            "center_mm": [x + 90, y + 65],
            "confidence": 0.8,
        }
        plan = {0: ([taza(0), libro], []), 1: ([taza(40)], []), 2: ([], ["libro"])}
        upsert, remove = plan.get(step, ([taza(0), libro], []))
        for o in upsert:
            self.cfg.objects[o["id"]] = o
        for oid in remove:
            self.cfg.objects.pop(oid, None)
        return upsert, remove

    async def look(self, lid: str, clean: bool):
        if not self.cfg.camera:
            await self.send(
                {"type": "error", "code": "no_camera", "message": "El motor falso corre sin cámara."}
            )
            return
        frame = self.cfg.jpeg
        if clean:
            async with self.cycle_lock:
                frame = await self.clean_capture({})
                await self.status("idle")
            if frame is None:
                return
        w, h = jpeg_size(frame)
        jpeg_b64 = base64.b64encode(frame).decode()
        await self.send(
            {"type": "look_frame", "id": lid, "jpeg_b64": jpeg_b64, "t_ns": time.time_ns(), "w": w, "h": h}
        )


def create_app(cfg: MotorConfig) -> Starlette:
    def cors(origin):
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} if origin else {}

    async def health(request: Request):
        origin = request.headers.get("origin")
        if not allowed_origin(origin):
            return JSONResponse({"error": "Origen no permitido"}, status_code=403)
        headers = cors(origin)
        if request.method == "OPTIONS":
            headers |= {"Access-Control-Allow-Methods": "GET", "Access-Control-Max-Age": "600"}
            # Preflight de Private/Local Network Access (Chrome): una página pública pide hablar con 127.0.0.1.
            if request.headers.get("access-control-request-private-network") == "true":
                headers["Access-Control-Allow-Private-Network"] = "true"
            return Response(status_code=204, headers=headers)
        body = {
            "ok": True,
            "version": VERSION,
            "camera": cfg.camera_info(),
            "models": {"sam": False, "hands": False},
        }
        return JSONResponse(body, headers=headers)

    async def socket(ws: WebSocket):
        origin = ws.headers.get("origin")
        if not allowed_origin(origin):
            cfg.log(f"[motor falso] origen rechazado: {origin}")
            await ws.close(code=4403)  # antes de aceptar: el navegador recibe un 403
            return
        await ws.accept()
        try:
            hello = json.loads(await asyncio.wait_for(ws.receive_text(), 10))
        except (TimeoutError, ValueError, WebSocketDisconnect):
            with suppress(Exception):
                await ws.close(code=4400)
            return
        if not isinstance(hello, dict) or hello.get("type") != "hello" or hello.get("v") != 0:
            await ws.send_text(
                json.dumps({"type": "error", "code": "bad_hello", "message": "Se espera hello v0."})
            )
            await ws.close(code=4400)
            return
        if not hmac.compare_digest(str(hello.get("token") or "").encode(), cfg.token.encode()):
            cfg.log("[motor falso] token inválido")
            await ws.send_text(json.dumps({"type": "error", "code": "auth", "message": "Token inválido."}))
            await ws.close(code=4401)
            return
        cfg.log(f"[motor falso] consola conectada (sesión {hello.get('session_id')}, {hello.get('app')})")
        session = Session(ws, cfg)
        cfg.sessions.append(session)
        try:
            await session.run()
        finally:
            cfg.sessions.remove(session)

    return Starlette(
        routes=[
            Route("/v1/health", health, methods=["GET", "OPTIONS"]),
            WebSocketRoute("/v1/ws", socket),
        ]
    )


def main():
    p = argparse.ArgumentParser(description="Motor falso de pcx (protocolo motor v0). No toca cámaras.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9230)
    p.add_argument("--token", default=os.getenv("PCX_MOTOR_TOKEN") or secrets.token_urlsafe(16))
    p.add_argument("--sin-camara", action="store_true", help="sin cámara propia: pide cuadros con need_frame")
    p.add_argument("--jpeg", help="JPEG que hace de cámara (por defecto, uno sintético de 160×90)")
    p.add_argument("--ciclo-s", type=float, default=45, help="ciclo periódico en segundos (0 = apagado)")
    p.add_argument("--consola", default="http://localhost:9215", help="URL de la consola para el enlace")
    a = p.parse_args()
    jpeg = FAKE_JPEG
    if a.jpeg:
        with open(a.jpeg, "rb") as f:
            jpeg = f.read()
        jpeg_size(jpeg)
    cfg = MotorConfig(token=a.token, camera=not a.sin_camara, jpeg=jpeg, cycle_s=a.ciclo_s)
    motor = f"ws://{a.host}:{a.port}"
    sim = "sim=1&" if a.consola.startswith("http://") else ""
    print(f"Motor falso en {motor}/v1/ws · token {a.token}")
    print(f"Consola: {a.consola.rstrip('/')}/mesa?{sim}motor={motor}&motor_token={a.token}")
    uvicorn.run(create_app(cfg), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()

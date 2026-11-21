"""Cámara de la mesa = el Quest 3 colgado junto al proyector (app com.smartup.proyector, HTTP :8787). Desde el 9-nov-2026.

    .venv/bin/python -m playground.quest_cam medir [--segundos 8]           # fps, kB/s y latencia: USB (adb forward) vs Wi-Fi
    .venv/bin/python -m playground.quest_cam medir --latencia               # + latencia por escalón negro→blanco en la ventana

Fuentes:
  · MjpegCam(url)  /cam.mjpg?fps=&w=&q=  → cuadro BGR (la app manda luma con algo de croma; gris→BGR si hace falta).
    La app reduce el cuadro del monitor a la MITAD por decimación (ScanCamera.reducirNv21: píxel i = píxel 2i del cuadro
    completo), así que el máximo es 864x864 aunque se pida w=1296: x_864 = x_1728 / 2 exacto.
  · cuadro_full(base) /cam_full.jpg  → 1728x1728 luma + X-Ts-Ns, una captura por pedido (~0,2 s por USB). Para los
    momentos que importan (segmentación base, recalibración).

Por USB: `adb -s 2G0YC5ZGCM043L forward tcp:8787 tcp:8787` y http://127.0.0.1:8787 (no carga la Wi-Fi, que comparte el
AirPlay del proyector). Por Wi-Fi: http://192.168.4.161:8787.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.request

import cv2
import numpy as np

USB = "http://127.0.0.1:8787"
WIFI = "http://192.168.4.161:8787"
SERIAL_USB = "2G0YC5ZGCM043L"
LADO_FULL = 1728                        # cuadro completo de la cámara izquierda (intrínsecos de /cam_pose.json)


def base_de(url: str) -> str:
    """http://host:puerto/lo/que/sea?x → http://host:puerto"""
    p = url.split("/")
    return "/".join(p[:3])


class MjpegCam:
    """Lector MJPEG en su hilo: guarda el último cuadro decodificado, se reconecta solo (backoff 1-5 s). Interfaz de
    `slscan.capture.Camera` para `superficie_feed.CamReader`: read() bloquea hasta un cuadro NUEVO (o lanza tras
    `timeout` s sin cuadros) y close().

    Si la URL es la del adb forward (127.0.0.1:8787) y se cae, rehace `adb forward` (el casco se reconectó por USB) y,
    tras 3 fallos seguidos, prueba el `respaldo` (por defecto la misma ruta por Wi-Fi); desde el respaldo vuelve a
    probar el USB cada 30 s."""

    def __init__(self, url: str, timeout: float = 4.0, decodificar: bool = True, respaldo: str | None = "auto"):
        self.url = url; self.timeout = timeout; self.decodificar = decodificar
        if respaldo == "auto":
            respaldo = url.replace(USB, WIFI) if url.startswith(USB) else None
        self.urls = [url] + ([respaldo] if respaldo else []); self.i_url = 0; self.t_respaldo = 0.0
        self.frame: np.ndarray | None = None; self.jpg: bytes | None = None
        self.seq = 0; self._leido = 0; self.t = 0.0
        self.fps = 0.0; self.kbps = 0.0; self.bytes_total = 0; self.reconexiones = 0; self.error: str | None = None
        self.size: tuple[int, int] | None = None
        self._cond = threading.Condition(); self._stop = threading.Event()
        self._resp = None
        threading.Thread(target=self._run, daemon=True, name="mjpeg").start()

    def _run(self) -> None:
        espera = 1.0; fallos = 0
        while not self._stop.is_set():
            try:
                if self.i_url > 0 and time.time() - self.t_respaldo > 30:      # en el respaldo: ¿volvió el USB?
                    try:
                        urllib.request.urlopen(self.urls[0].split("/cam.mjpg")[0] + "/estado.json", timeout=1.5).read()
                        self.i_url = 0
                    except Exception:  # noqa: BLE001
                        self.t_respaldo = time.time()
                self.url = self.urls[self.i_url]
                self._resp = urllib.request.urlopen(self.url, timeout=self.timeout)
                self.error = None; espera = 1.0; fallos = 0
                n, nb, t0 = 0, 0, time.time()
                while not self._stop.is_set():
                    largo = None
                    while True:                                         # cabecera de la parte
                        ln = self._resp.readline()
                        if not ln:
                            raise ConnectionError("stream cerrado")
                        ln = ln.strip()
                        if ln.lower().startswith(b"content-length:"):
                            largo = int(ln.split(b":", 1)[1])
                        elif not ln and largo is not None:
                            break
                    data = self._resp.read(largo)
                    if len(data) != largo:
                        raise ConnectionError("parte incompleta")
                    t = time.time()
                    img = None
                    if self.decodificar:
                        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)   # gris → BGR de 3 canales
                        if img is None:
                            continue
                        self.size = (img.shape[1], img.shape[0])
                    with self._cond:
                        self.frame, self.jpg, self.t, self.seq = img, data, t, self.seq + 1
                        self._cond.notify_all()
                    n += 1; nb += len(data); self.bytes_total += len(data)
                    if t - t0 >= 2.0:
                        self.fps, self.kbps = n / (t - t0), nb / 1024 / (t - t0); n, nb, t0 = 0, 0, t
            except Exception as e:  # noqa: BLE001
                if self._stop.is_set():
                    break
                self.error = f"{time.strftime('%H:%M:%S')} {self.url.split('/cam')[0]}: {str(e)[:120]}"; self.reconexiones += 1; self.fps = 0.0
                fallos += 1
                try:
                    if self._resp is not None:
                        self._resp.close()
                except Exception:  # noqa: BLE001
                    pass
                if self.url.startswith(USB) and fallos % 2 == 1:
                    try:
                        adb_forward()
                    except Exception:  # noqa: BLE001
                        pass
                if fallos >= 3 and len(self.urls) > 1:
                    self.i_url = (self.i_url + 1) % len(self.urls); fallos = 0; self.t_respaldo = time.time()
                time.sleep(espera); espera = min(5.0, espera * 1.6)

    def read(self) -> np.ndarray:
        with self._cond:
            if not self._cond.wait_for(lambda: self.seq > self._leido and self.frame is not None, self.timeout):
                raise RuntimeError(f"sin cuadros de {self.url} en {self.timeout:.0f} s ({self.error or 'conectando'})")
            self._leido = self.seq
            return self.frame

    def close(self) -> None:
        self._stop.set()
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:  # noqa: BLE001
            pass

    def status(self) -> dict:
        return {"url": self.url, "respaldo": self.i_url > 0, "fps": round(self.fps, 1), "kB_s": round(self.kbps), "size": self.size, "cuadros": self.seq,
                "reconexiones": self.reconexiones, "error": self.error, "edad_ms": round((time.time() - self.t) * 1000) if self.t else None}


def cuadro_full(base: str, timeout: float = 6.0) -> tuple[np.ndarray, dict]:
    """/cam_full.jpg: cuadro completo 1728x1728 (luma, BGR de 3 canales) + {ts_ns, ms, bytes}."""
    t0 = time.time()
    with urllib.request.urlopen(base + "/cam_full.jpg", timeout=timeout) as r:
        data = r.read(); ts = r.headers.get("X-Ts-Ns")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("cam_full.jpg ilegible")
    return img, {"ts_ns": int(ts) if ts else None, "ms": round((time.time() - t0) * 1000), "bytes": len(data)}


def quest_json(base: str, path: str, timeout: float = 4.0):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.loads(r.read())


def adb_forward(serial: str = SERIAL_USB, puerto: int = 8787) -> bool:
    import os
    import shutil
    import subprocess
    adb = os.environ.get("ADB") or shutil.which("adb") or "/Users/max/Library/Android/sdk/platform-tools/adb"
    r = subprocess.run([adb, "-s", serial, "forward", f"tcp:{puerto}", f"tcp:{puerto}"], capture_output=True, text=True, timeout=10)
    return r.returncode == 0


# ---------------------------------------------------------------------------------------------------- medición

def medir_stream(url: str, segundos: float) -> dict:
    cam = MjpegCam(url, decodificar=True)
    t0 = time.time(); llegadas = []; seq = 0
    while time.time() - t0 < segundos + 1.0:
        with cam._cond:
            cam._cond.wait_for(lambda: cam.seq > seq, 2.0)
            if cam.seq > seq:
                seq = cam.seq; llegadas.append(cam.t)
    cam.close()
    ll = np.array([t for t in llegadas if t - t0 >= 1.0])            # descarta el arranque
    if len(ll) < 3:
        return {"url": url, "error": cam.error or "sin cuadros"}
    dt = np.diff(ll)
    return {"url": url, "fps": round(len(ll) / (ll[-1] - ll[0]), 1), "dt_ms_med": round(float(np.median(dt)) * 1000),
            "dt_ms_p95": round(float(np.percentile(dt, 95)) * 1000), "dt_ms_max": round(float(dt.max()) * 1000),
            "kB_s": round(cam.bytes_total / 1024 / max(1e-3, time.time() - t0)), "size": cam.size, "reconexiones": cam.reconexiones}


def medir_latencia(urls: dict[str, str], ventana: str, roi=None, n: int = 4) -> dict:
    """Escalón negro→blanco por la ventana del proyector, visto a la vez por varias fuentes MJPEG y por /cam.jpg
    sondeado (USB). Latencia de una fuente = t(ve el cambio) − t(POST); la diferencia contra el sondeo de /cam.jpg
    (~30 ms: el cuadro más nuevo + JPEG + USB) aísla el retardo del stream del retardo AirPlay+proyector."""
    cams = {k: MjpegCam(u) for k, u in urls.items()}
    time.sleep(2.0)
    base_usb = USB

    def media(img):
        g = img[..., 1] if img.ndim == 3 else img
        if roi is not None:
            h, w = g.shape[:2]; x0, y0, x1, y1 = roi
            g = g[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
        return float(g.mean())

    def get(path):
        with urllib.request.urlopen(ventana + path, timeout=3) as r:
            r.read()
    res = {k: [] for k in list(cams) + ["sondeo_cam_jpg"]}
    for i in range(n):
        get("/negro"); time.sleep(2.5)
        ref = {k: media(c.frame) for k, c in cams.items() if c.frame is not None}
        with urllib.request.urlopen(base_usb + "/cam.jpg", timeout=3) as r:
            ref_s = media(cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_COLOR))
        t_send = time.time(); get("/blanco")
        vistos: dict[str, float] = {}; seqs = {k: c.seq for k, c in cams.items()}
        while time.time() - t_send < 4.0 and len(vistos) < len(cams) + 1:
            for k, c in cams.items():
                if k not in vistos and c.seq > seqs[k]:
                    seqs[k] = c.seq
                    if media(c.frame) - ref.get(k, 0) > 12:
                        vistos[k] = c.t - t_send
            if "sondeo_cam_jpg" not in vistos:
                with urllib.request.urlopen(base_usb + "/cam.jpg", timeout=3) as r:
                    im = cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_COLOR); tt = time.time()
                if media(im) - ref_s > 12:
                    vistos["sondeo_cam_jpg"] = tt - t_send
            time.sleep(0.004)
        for k, v in vistos.items():
            res[k].append(v)
        time.sleep(0.5)
    get("/gris?v=128")
    for c in cams.values():
        c.close()
    out = {}
    s0 = np.median(res["sondeo_cam_jpg"]) if res["sondeo_cam_jpg"] else None
    for k, v in res.items():
        if v:
            med = float(np.median(v))
            out[k] = {"escalon_ms": [round(x * 1000) for x in v], "mediana_ms": round(med * 1000),
                      "stream_vs_sondeo_ms": None if (s0 is None or k == "sondeo_cam_jpg") else round((med - s0) * 1000)}
    out["nota"] = ("escalon_ms = POST a la ventana → la fuente ve el blanco (incluye AirPlay + proyector + exposición). "
                   "Latencia propia del stream ≈ stream_vs_sondeo_ms + ~30 ms (el sondeo de /cam.jpg por USB trae el cuadro más nuevo)")
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("accion", choices=["medir", "full"])
    p.add_argument("--segundos", type=float, default=8.0)
    p.add_argument("--latencia", action="store_true", help="también el escalón negro→blanco por la ventana (proyecta 4 veces)")
    p.add_argument("--ventana", default="http://127.0.0.1:8793")
    p.add_argument("--configs", default="864x15,864x20,864x30", help="ancho x fps del /cam.mjpg a probar")
    p.add_argument("--q", type=int, default=70)
    p.add_argument("--salida", default=None)
    a = p.parse_args(argv)
    if a.accion == "full":
        img, info = cuadro_full(USB)
        print(json.dumps({**info, "shape": img.shape}))
        return 0
    adb_forward()
    out = {"fecha": time.strftime("%Y-%m-%d %H:%M:%S"), "streams": [], "full": []}
    for base, nombre in ((USB, "usb"), (WIFI, "wifi")):
        for cfg in a.configs.split(","):
            w, fps = cfg.split("x")
            url = f"{base}/cam.mjpg?w={w}&fps={fps}&q={a.q}"
            r = medir_stream(url, a.segundos); r["via"] = nombre; out["streams"].append(r)
            print(json.dumps(r), flush=True)
        ts = []
        for _ in range(6):
            try:
                _, info = cuadro_full(base); ts.append(info["ms"])
            except Exception as e:  # noqa: BLE001
                ts.append(None); print(nombre, "full:", e)
        r = {"via": nombre, "cam_full_ms": ts}
        out["full"].append(r); print(json.dumps(r), flush=True)
    if a.latencia:
        urls = {"usb_864x15": f"{USB}/cam.mjpg?w=864&fps=15&q={a.q}", "wifi_864x15": f"{WIFI}/cam.mjpg?w=864&fps=15&q={a.q}"}
        out["latencia"] = medir_latencia(urls, a.ventana, roi=(0.25, 0.45, 0.7, 0.68))
        print(json.dumps(out["latencia"], ensure_ascii=False), flush=True)
    if a.salida:
        with open(a.salida, "w") as f:
            json.dump(out, f, indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

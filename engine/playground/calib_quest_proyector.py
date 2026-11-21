"""Calibración completa del proyector de la mesa con el Quest 3 como cámara (luz estructurada + MRUK).

    .venv/bin/python playground/calib_quest_proyector.py todo [--sesion DIR] [--push]   # todo, en vivo
    .venv/bin/python playground/calib_quest_proyector.py todo --dry                      # escena sintética (prueba)
    .venv/bin/python playground/calib_quest_proyector.py captura|decodificar|geometria|resolver|pixel|lazo|exportar [--sesion DIR]
    .venv/bin/python playground/calib_quest_proyector.py bundle [--push]    # ajuste conjunto con TODO lo acumulado → pose fija
    .venv/bin/python playground/calib_quest_proyector.py verificar          # 20 puntos sobre la mesa, sin recalibrar

Pose fija: proyector y Quest están montados estáticos, así que la pose proyector↔Quest (marco de la cámara del Quest) es UNA
constante. `bundle` junta todas las vistas (gray code y ArUco de cada captures/quest-cal-2*/, damero de
captures/quest-cal-checker/) en el marco del Quest y resuelve un solo ajuste (K + pose + k1 opcional, Huber, covarianza),
parte del best guess de Max (proyector ~10 cm sobre el Quest, ~20 cm al lado: prior blando que solo pesa si los datos son
degenerados) y escribe captures/quest-cal-fija/ (calib_proyector_mesa.json y calib_mesa.json con pose_fija, incertidumbre e
historial_residuo). La pose en la sala sale de la pose actual de la cámara del Quest. `verificar` mide el residuo actual y,
si pasa el umbral (3 px / 2 mm), anota que hay que re-estimar (captura nueva + bundle).

Pasos (cada uno lee y escribe en captures/quest-cal-<fecha>/; sin --sesion se usa la última):
 1. captura     gray code 11+11 bits (normal e inverso) + blanco y negro por la VENTANA del proyector
                (playground/ventana_proyector.py, HTTP :8793/:8792, contenido 1900x1040 = espacio de píxeles del
                proyector en toda la salida) y cuadro del Quest tras cada patrón: ≥1,5 s (AirPlay) + cambio real visto con
                /cam.jpg (rápido), luego el cuadro completo (/cam_full.png si existe). Primero la grilla ArUco 5x3 de la
                ventana (captures/dale-20261102-1511/aruco_ventana.*) como primera vista del PnP.
                Antes de CADA patrón: display del proyector (0,1080) presente, ventana respondiendo y sin "DCPEXT" en
                `log stream`; si falta algo no manda nada, espera (5 s, hasta 10 min) y al volver revalida con un blanco;
                si aparece DCPEXT, para. Nunca abre ventanas propias.
                Guarda estado.json, malla.json (MRUK), cam_pose.json (pose promedio de la cámara fija), patrones y capturas.
 2. decodificar mapa denso cámara→proyector (umbral adaptativo por píxel, bits finos ilegibles se omiten) y pares
                subpíxel en una grilla del proyector (afín local sobre bloques) = mapa inverso proyector→cámara.
 3. geometria   rayo de cada par (intrínsecos + pose de la cámara, marco de la sala) ∩ malla MRUK / planos de las anclas.
 4. resolver    PnP del proyector: DLT o init plana (Zhang con cx fijo, ambas quiralidades) + LM robusto; con los puntos
                fuera del plano (paralaje: teclado, cajas) suma la restricción epipolar, que fija el centro del proyector.
                Elige el lado del plano de la cámara (el proyector no puede estar bajo la mesa).
 5. pixel       ArUco/ChArUco en el Pixel 9 (quest/charuco.html): PnP con tamaño conocido → escala y pose del casco (mm).
 6. lazo        proyecta puntos en posiciones previstas de puntos 3D de la mesa (y del Pixel), mide dónde caen con el
                Quest, residuo en px del proyector y mm; corrige con un remap residual (2-3 iteraciones).
 7. exportar    calib_proyector_mesa.json (K, dist, R, t en la sala, residuos), calib_mesa.json en el formato de la app
                (P en el mundo de la sala, píxel con bordes: centro del píxel i en i+0,5), H_proj_to_mesa (px→mm),
                vista3d.png; --push lo copia a files/ de com.smartup.proyector.

Convenciones: mundo de la app = Spatial SDK (x der, y arriba, z adelante, zurdo, m). Para OpenCV se usa el diestro
X_r = (x, y, -z). Cámara de la app: x der, y arriba, z adelante; píxel (u, v desde arriba) → d = ((u-cx)/fx, (cy-v)/fy, 1).
Píxeles del proyector = contenido de la ventana (1900x1040), centros enteros (OpenCV): la columna i del PNG es u = i.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from slscan.graycode import gray2bin, pattern as gc_pattern  # noqa: E402

QUEST = os.environ.get("QUEST_URL", "http://192.168.4.161:8787")
VENTANA = os.environ.get("VENTANA_URL", "http://127.0.0.1:8793")   # playground/ventana_proyector.py (8792 lo usa la secretaria)
ADB = os.environ.get("ADB", "/Users/max/Library/Android/sdk/platform-tools/adb")
ADB_SERIAL = os.environ.get("ADB_SERIAL", "192.168.4.161:5555")
APP = "com.smartup.proyector"
W, H = 1900, 1040                         # contenido de la ventana del proyector (se lee de /estado)
BITS = 11
DISPLAY = (0, 1080, 1920, 1080)           # display del proyector (AirPlay extendido)
ARUCO_DIR = REPO / "captures/dale-20261102-1511"
ARUCO_GRILLAS = ("aruco_ventana_3x2", "aruco_ventana")      # <nombre>.png/.json (+ _inv.png); esquinas en px del contenido
FZ = np.array([1.0, 1.0, -1.0])           # mundo app (zurdo) <-> diestro
CAPTURES = REPO / "captures"
PIXEL_ESPESOR_MM = 8.5                    # Pixel 9: la pantalla queda ~8,5 mm sobre la mesa (sin funda)
CAM_DEF = {"m4": None, "t": [-0.0267, 1.2337, 0.5893], "q": [-0.0267, 0.0296, 0.9123, -0.4075],
           "intr": {"fx": 1164.597, "fy": 1164.597, "cx": 864.6926, "cy": 861.9245, "w": 1728, "h": 1728}}


# ============================================================================ utilidades

def _js(o):
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(type(o))


class Sesion:
    def __init__(self, path: Path, dry: bool = False):
        self.dir = Path(path)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.dry = dry

    def p(self, *a) -> Path:
        return self.dir.joinpath(*a)

    def log(self, msg: str) -> None:
        line = f"{dt.datetime.now():%H:%M:%S} {msg}"
        print(line, flush=True)
        with open(self.p("log.txt"), "a") as f:
            f.write(line + "\n")

    def jw(self, name: str, obj) -> None:
        self.p(name).write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=_js))

    def jr(self, name: str):
        return json.loads(self.p(name).read_text())

    def dormir(self, s: float) -> None:
        if not self.dry:
            time.sleep(s)


def quat_mat(q) -> np.ndarray:
    w, x, y, z = (float(v) for v in q)
    n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def mat_quat(R: np.ndarray) -> list[float]:
    R = np.asarray(R, float)
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = [0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s]
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        q = [(R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s]
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        q = [(R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s]
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        q = [(R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s]
    return [float(v) for v in q]


def rot_deg(Ra, Rb) -> float:
    r, _ = cv2.Rodrigues(np.asarray(Ra) @ np.asarray(Rb).T)
    return float(np.degrees(np.linalg.norm(r)))


def stats(v) -> dict:
    v = np.abs(np.asarray(v, float).ravel())
    if v.size == 0:
        return {"n": 0}
    return {"n": int(v.size), "mediana": round(float(np.median(v)), 3), "p90": round(float(np.percentile(v, 90)), 3),
            "rms": round(float(np.sqrt((v ** 2).mean())), 3), "max": round(float(v.max()), 3)}


# ============================================================================ salida al proyector (runtime) y guardas

def display_ok() -> bool:
    try:
        import Quartz
        err, ids, n = Quartz.CGGetActiveDisplayList(16, None, None)
        for i in list(ids)[:n]:
            b = Quartz.CGDisplayBounds(i)
            if (round(b.origin.x), round(b.origin.y), round(b.size.width), round(b.size.height)) == DISPLAY:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def ventana_estado(url: str | None = None, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen((url or VENTANA) + "/estado", timeout=timeout) as r:
            j = json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None
    return j if isinstance(j, dict) and "size" in j and "seq" in j else None


def buscar_ventana() -> str | None:
    """La ventana del proyector: VENTANA_URL/--ventana, si no 8793 y 8792 (8792 suele ser la secretaria)."""
    global VENTANA
    for u in [VENTANA, "http://127.0.0.1:8793", "http://127.0.0.1:8792", "http://127.0.0.1:8794"]:
        if ventana_estado(u):
            VENTANA = u
            return u
    return None


def fijar_proyector(w: int, h: int) -> None:
    global W, H
    W, H = int(w), int(h)


class VigiaDCPEXT:
    """`log stream` filtrando DCPEXT (firmware del DCP del display externo, ver ventana_proyector.py): si aparece, se
    deja de mandar patrones."""

    def __init__(self, s: Sesion):
        self.s, self.linea, self.p = s, None, None
        try:
            self.p = subprocess.Popen(["/usr/bin/log", "stream", "--style", "compact", "--predicate", 'eventMessage CONTAINS "DCPEXT"'],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            threading.Thread(target=self._leer, daemon=True).start()
            s.log("vigía DCPEXT: log stream activo")
        except Exception as e:  # noqa: BLE001
            s.log(f"vigía DCPEXT: no pude abrir log stream ({e})")

    def _leer(self) -> None:
        for line in self.p.stdout:
            if "DCPEXT" in line and not line.startswith("Filtering"):
                self.linea = line.strip()
                try:
                    http_get(VENTANA + "/negro", 3)
                except Exception:  # noqa: BLE001
                    pass
                self.s.log(f"¡DCPEXT en el log del sistema! puse /negro y dejo de proyectar: {self.linea[:220]}")

    def cerrar(self) -> None:
        if self.p is not None:
            self.p.terminate()


VIGIA: VigiaDCPEXT | None = None


def chequear_dcpext() -> None:
    if VIGIA is not None and VIGIA.linea is not None:
        raise RuntimeError(f"DCPEXT en el log del sistema: paré de proyectar ({VIGIA.linea[:160]})")


def esperar_salida(s: Sesion, max_s: float = 600) -> bool:
    """Bloquea hasta que estén el display del proyector y la ventana. Devuelve True si hubo que esperar."""
    if s.dry:
        return False
    t0 = time.time()
    espero = False
    while True:
        chequear_dcpext()
        d, v = display_ok(), ventana_estado() is not None
        if d and v:
            if espero:
                s.log(f"salida de vuelta tras {time.time() - t0:.0f} s de pausa")
            return espero
        if not espero:
            s.log(f"PAUSA: display {DISPLAY[:2]} {'ok' if d else 'AUSENTE'} · ventana {VENTANA} {'ok' if v else 'NO RESPONDE'}"
                  " → no mando patrones; reintento cada 5 s (máx 10 min)")
        espero = True
        if time.time() - t0 > max_s:
            raise RuntimeError(f"la salida al proyector no volvió en {max_s:.0f} s (display {'ok' if d else 'ausente'}, "
                               f"ventana {'ok' if v else 'caída'})")
        time.sleep(5)


class Proyector:
    """Manda imágenes del tamaño del contenido de la ventana del proyector por su HTTP (nunca abre ventanas propias)."""

    def __init__(self, s: Sesion, sim=None):
        self.s = s
        self.sim = sim
        self.dir = s.p("patrones")
        self.dir.mkdir(exist_ok=True)

    def mostrar(self, img: np.ndarray, nombre: str, seconds: float | None = None) -> bool:
        if img.shape[:2] != (H, W):
            raise ValueError(f"{nombre}: {img.shape[1]}x{img.shape[0]} y la ventana es {W}x{H}")
        path = self.dir / f"{nombre}.png"
        cv2.imwrite(str(path), img)
        if self.sim is not None:
            self.sim.actual = img
            return False
        chequear_dcpext()
        espero = esperar_salida(self.s)
        b, _ = http_get(VENTANA + "/mostrar?path=" + quote(str(path.resolve())), 8)
        r = json.loads(b)
        if not r.get("ok"):
            raise RuntimeError(f"la ventana rechazó {path.name}: {r}")
        seq, t0 = int(r.get("seq", 0)), time.time()
        while time.time() - t0 < 2.0:
            e = ventana_estado()
            if e and int(e["seq"]) >= seq:
                break
            time.sleep(0.05)
        return espero

    def apagar(self) -> None:
        if self.sim is not None:
            self.sim.actual = np.zeros((H, W, 3), np.uint8)
            return
        try:
            http_get(VENTANA + "/negro", 4)
        except Exception:  # noqa: BLE001
            pass


# ============================================================================ Quest: cuadros, pose, malla

def http_get(url: str, timeout: float = 8.0) -> tuple[bytes, dict]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read(), dict(r.headers)


def quest_json(path: str, timeout: float = 8.0):
    b, _ = http_get(QUEST + path, timeout)
    return json.loads(b)


def quest_cmd(c: str) -> dict:
    try:
        return quest_json("/cmd?c=" + quote(c), 10)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


class CamaraQuest:
    FUENTES = ("/cam_full.png", "/cam.jpg?full=1", "/cam.jpg")

    def __init__(self, s: Sesion, sim=None):
        self.s = s
        self.sim = sim
        self.fuente = None

    def elegir_fuente(self) -> str:
        if self.sim is not None:
            self.fuente = "sim"
            return self.fuente
        for f in self.FUENTES:
            try:
                img, _ = self._get(f)
            except Exception as e:  # noqa: BLE001
                self.s.log(f"cámara: {f} no sirve ({e})")
                continue
            if f != "/cam.jpg" and img.shape[0] != 1728:
                self.s.log(f"cámara: {f} entrega {img.shape} (no 1728): sigo buscando")
                continue
            self.fuente = f
            self.s.log(f"cámara: fuente {f} ({img.shape[1]}x{img.shape[0]})")
            return f
        raise RuntimeError("el Quest no entrega cuadros de la cámara")

    def _get(self, f: str) -> tuple[np.ndarray, dict]:
        b, hd = http_get(QUEST + f, timeout=10)
        img = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise RuntimeError(f"no pude decodificar {f} ({len(b)} bytes)")
        ts = hd.get("X-Ts-Ns") or hd.get("x-ts-ns") or 0
        return img, {"ts_ns": int(ts), "t": time.time(), "exp": hd.get("X-Exposure")}

    def cuadro(self) -> tuple[np.ndarray, dict]:
        if self.sim is not None:
            return self.sim.render(), {"ts_ns": 0, "t": time.time()}
        err = None
        esperado = None if self.fuente == "/cam.jpg" else (1728, 1728)
        for k in range(8):
            try:
                img, meta = self._get(self.fuente)
                if esperado is None or img.shape == esperado:
                    return img, meta
                err = f"tamaño {img.shape} (¿cartel de 'sin cuadro'?)"
            except Exception as e:  # noqa: BLE001
                err = e
            time.sleep(0.5 + 0.5 * k)
        raise RuntimeError(f"sin cuadro del Quest: {err}")

    def rapido(self) -> np.ndarray:
        """Cuadro chico y rápido (/cam.jpg, 864 px) para detectar cambios."""
        if self.sim is not None:
            return cv2.resize(self.sim.render(), (864, 864), interpolation=cv2.INTER_AREA)
        t0, aviso = time.time(), False
        while time.time() - t0 < 30:
            try:
                b, _ = http_get(QUEST + "/cam.jpg", 5)
                g = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_GRAYSCALE)
                if g is not None and g.shape == (864, 864):
                    return g
                if not aviso:
                    self.s.log(f"  /cam.jpg devolvió {None if g is None else g.shape} (¿cartel de 'sin cuadro'?): reintento")
                    aviso = True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        raise RuntimeError("sin /cam.jpg válido del Quest en 30 s")

    def promedio(self, n: int = 3) -> np.ndarray:
        acc = None
        for _ in range(n):
            f, _ = self.cuadro()
            acc = f.astype(np.float32) if acc is None else acc + f
        return acc / n


def leer_pose_camara(s: Sesion, n: int = 6) -> dict:
    """Pose de la cámara izquierda en el mundo de la sala, promediada (la cámara está fija)."""
    Ms, fuente, intr, dist, ancla = [], None, None, None, None
    for _ in range(n):
        try:
            try:
                http_get(QUEST + "/cam_full.png", 10)      # la pose de /cam_pose.json es la del último cuadro completo
            except Exception:  # noqa: BLE001
                pass
            j = quest_json("/cam_pose.json")
            if "error" in j:
                raise RuntimeError(j["error"])
            M = np.array(j["camara"]["m4_mundo_desde_cam"], float).reshape(4, 4)
            intr = j["intrinsecos"]
            dist = intr.get("distorsion")
            a = j.get("ancla_sala")
            ancla = a.get("m4") if isinstance(a, dict) else None
            fuente = "cam_pose.json"
        except Exception:  # noqa: BLE001
            e = quest_json("/estado.json")
            c = e["camara"]
            M = np.eye(4)
            M[:3, :3] = quat_mat(c["pose"]["q"])
            M[:3, 3] = c["pose"]["t"]
            intr = c["intrinsecos"]
            a = (e.get("mruk") or {}).get("ancla_sala")
            if a:
                A = np.eye(4); A[:3, :3] = quat_mat(a["q"]); A[:3, 3] = a["t"]; ancla = A.ravel().tolist()
            fuente = "estado.json (q redondeado a 1e-4)"
        Ms.append(M)
        time.sleep(0.25)
    ts = np.array([M[:3, 3] for M in Ms])
    t = ts.mean(0)
    U, _, Vt = np.linalg.svd(sum(M[:3, :3] for M in Ms))
    R = U @ Vt
    if np.linalg.det(R) < 0:
        U[:, -1] *= -1
        R = U @ Vt
    disp_mm = float(np.linalg.norm(ts - t, axis=1).max() * 1000)
    disp_deg = max(rot_deg(M[:3, :3], R) for M in Ms)
    Mm = np.eye(4); Mm[:3, :3] = R; Mm[:3, 3] = t
    s.log(f"pose cámara ({fuente}, {n} muestras): t={np.round(t, 4).tolist()} dispersión {disp_mm:.1f} mm / {disp_deg:.3f}°")
    return {"m4": Mm.tolist(), "intr": intr, "dist": dist, "ancla_sala_m4": ancla, "fuente": fuente,
            "dispersion_mm": disp_mm, "dispersion_deg": disp_deg, "muestras": n}


class Cam:
    """Modelo de la cámara del Quest (pinhole + distorsión camera2 opcional) en el mundo de la app."""

    def __init__(self, pose: dict, ancho: int = 1728):
        intr = pose["intr"]
        sc = ancho / float(intr["w"])
        self.fx, self.fy = intr["fx"] * sc, intr["fy"] * sc
        self.cx, self.cy = (intr["cx"] + 0.5) * sc - 0.5, (intr["cy"] + 0.5) * sc - 0.5
        self.w = self.h = ancho
        d = pose.get("dist") or [0, 0, 0, 0, 0]
        self.dist = np.array(list(d) + [0] * (5 - len(d)), float)[:5]
        self.con_dist = bool(np.any(np.abs(self.dist) > 1e-7))
        if pose.get("m4") is not None:
            M = np.array(pose["m4"], float).reshape(4, 4)
        else:
            M = np.eye(4); M[:3, :3] = quat_mat(pose["q"]); M[:3, 3] = pose["t"]
        self.R, self.o = M[:3, :3], M[:3, 3]

    def _fwd(self, x, y):   # camera2 LENS_DISTORTION: normalizado ideal → muestreo en la imagen cruda
        k1, k2, k3, p1, p2 = self.dist
        r2 = x * x + y * y
        rad = 1 + k1 * r2 + k2 * r2 * r2 + k3 * r2 ** 3
        return x * rad + p1 * 2 * x * y + p2 * (r2 + 2 * x * x), y * rad + p1 * (r2 + 2 * y * y) + p2 * 2 * x * y

    def ideal(self, uv: np.ndarray) -> np.ndarray:
        uv = np.asarray(uv, float).reshape(-1, 2)
        if not self.con_dist:
            return uv
        xr, yr = (uv[:, 0] - self.cx) / self.fx, (uv[:, 1] - self.cy) / self.fy
        x, y = xr.copy(), yr.copy()
        for _ in range(25):
            xc, yc = self._fwd(x, y)
            x, y = x - (xc - xr), y - (yc - yr)
        return np.stack([self.cx + self.fx * x, self.cy + self.fy * y], 1)

    def rayos(self, uv: np.ndarray) -> np.ndarray:
        uv = self.ideal(uv)
        d = np.stack([(uv[:, 0] - self.cx) / self.fx, (self.cy - uv[:, 1]) / self.fy, np.ones(len(uv))], 1)
        d = d @ self.R.T
        return d / np.linalg.norm(d, axis=1, keepdims=True)

    def proyectar(self, X: np.ndarray) -> np.ndarray:
        Xc = (np.asarray(X, float).reshape(-1, 3) - self.o) @ self.R
        x, y = Xc[:, 0] / Xc[:, 2], -Xc[:, 1] / Xc[:, 2]
        if self.con_dist:
            x, y = self._fwd(x, y)
        uv = np.stack([self.cx + self.fx * x, self.cy + self.fy * y], 1)
        uv[Xc[:, 2] <= 0] = np.nan
        return uv

    def K_cv(self) -> np.ndarray:
        return np.array([[self.fx, 0, self.cx], [0, self.fy, self.cy], [0, 0, 1.0]])


def plano_de_ancla(a: dict) -> dict:
    R = quat_mat(a["pose"]["q"])
    c = np.array(a["pose"]["t"], float)
    w, h = (a.get("plano") or [0, 0])[:2]
    return {"etiqueta": a.get("etiqueta", "?"), "c": c, "e1": R[:, 0], "e2": R[:, 1], "n": R[:, 2], "R": R,
            "w": float(w), "h": float(h)}


def rayo_plano(o, D, pl: dict, margen: float = 0.05):
    """t de los rayos o + t·D contra el rectángulo del plano (inf si no pegan)."""
    den = D @ pl["n"]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = ((pl["c"] - o) @ pl["n"]) / den
    X = o + t[:, None] * D
    a = (X - pl["c"]) @ pl["e1"]
    b = (X - pl["c"]) @ pl["e2"]
    ok = (np.abs(den) > 1e-9) & (t > 1e-3)
    if pl["w"] > 0 and pl["h"] > 0:
        ok &= (np.abs(a) <= pl["w"] / 2 + margen) & (np.abs(b) <= pl["h"] / 2 + margen)
    return np.where(ok, t, np.inf)


class Malla:
    def __init__(self, mj: dict):
        m = mj.get("mruk") or {}
        pos = np.array(m.get("pos") or [], np.float64).reshape(-1, 3)
        n = len(pos) // 3
        self.tri = pos[: n * 3].reshape(n, 3, 3)
        self.tipo = np.array(m.get("tipo") or [0] * n)[:n]
        self.anclas = mj.get("anclas") or []

    def interseca(self, o: np.ndarray, D: np.ndarray, max_t: float = 8.0, bloque: int = 48) -> np.ndarray:
        """Möller–Trumbore vectorizado, rayos con origen común o. Devuelve t (inf si no pega)."""
        if len(self.tri) == 0:
            return np.full(len(D), np.inf)
        cen = self.tri.mean(1)
        v = cen - o
        dist = np.linalg.norm(v, axis=1)
        dm = D.mean(0); dm /= np.linalg.norm(dm)
        cosmax = float(np.min(D @ dm))
        ang = math.acos(max(-1.0, min(1.0, cosmax))) + math.radians(12)
        rad = np.linalg.norm(self.tri - cen[:, None, :], axis=2).max(1)
        cosang = (v @ dm) / np.maximum(dist, 1e-9)
        sel = (dist < max_t) & ((cosang > math.cos(min(ang, math.pi * 0.95))) | (dist < rad + 0.05))
        T = self.tri[sel]
        v0, e1, e2 = T[:, 0], T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
        tv = o - v0
        qv = np.cross(tv, e1)
        tq = np.einsum("ij,ij->i", e2, qv)
        out = np.full(len(D), np.inf)
        for i in range(0, len(D), bloque):
            d = D[i:i + bloque]
            pv = np.cross(d[:, None, :], e2[None, :, :])
            det = np.einsum("mj,kmj->km", e1, pv)
            with np.errstate(divide="ignore", invalid="ignore"):
                inv = 1.0 / det
                u = np.einsum("mj,kmj->km", tv, pv) * inv
                vv = (d @ qv.T) * inv
                t = tq[None, :] * inv
                ok = (np.abs(det) > 1e-12) & (u >= -1e-6) & (vv >= -1e-6) & (u + vv <= 1 + 1e-6) & (t > 1e-3)
            t = np.where(ok, t, np.inf)
            out[i:i + bloque] = t.min(1)
        return out


# ============================================================================ escena sintética (--dry)

def rayo_caja(o, D, bmin, bmax) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / D
        t1 = (bmin - o) * inv
        t2 = (bmax - o) * inv
        tmin = np.nanmax(np.minimum(t1, t2), axis=1)
        tmax = np.nanmin(np.maximum(t1, t2), axis=1)
    hit = (tmax >= tmin) & (tmin > 1e-6)
    return np.where(hit, tmin, np.inf)


class Sim:
    """Mesa (ancla TABLE real o por defecto) + una caja y un 'teclado' sobre ella, cámara del Quest y un proyector
    verdad colgado del techo. render() forma la imagen de la cámara con lo que haya en self.actual."""

    def __init__(self, cam_pose: dict, anclas: list, seed: int = 0, escena: str | None = None):
        escena = escena or os.environ.get("CALIB_DRY_ESCENA", "mesa+cajas")
        self.escena = escena
        self.rng = np.random.default_rng(seed)
        self.cam = Cam(cam_pose, 1728)
        self.anclas = anclas
        self.mesa = plano_de_ancla(next(a for a in anclas if a["etiqueta"] == "TABLE"))
        y0 = float(self.mesa["c"][1])
        # 'piso': la huella se sale de la mesa hacia el piso (no coplanar); 'mesa': todo cae en la mesa
        cen = np.array([0.17, y0, 0.08]) if "piso" in escena else np.array([0.17, y0, 0.33])
        self.cajas = [(cen + [-0.27, 0, -0.10], cen + [-0.07, 0.15, 0.05]),   # caja 20x15x15 (en la malla)
                      (cen + [0.05, 0, 0.06], cen + [0.45, 0.03, 0.18])] if "cajas" in escena else []   # teclado (fuera)
        # proyector verdad (diestro): colgado del techo, mira al centro con ~22° de inclinación, imagen volteada (techo)
        C = (np.array([0.066, 1.462, 0.611]) + np.array([0, 0, cen[2] - 0.08])) * FZ
        T = cen * FZ
        z = (T - C) / np.linalg.norm(T - C)
        up = np.array([0, 0, -1.0]) * FZ
        x = np.cross(z, up); x /= np.linalg.norm(x)
        y = np.cross(z, x)
        R = np.stack([x, y, z])
        self.v = {"f": 3100.0 * W / 1920, "cx": W / 2, "cy": 0.917 * H, "k1": -0.02, "sx": 1.0, "R": R, "t": -R @ C, "C_app": C * FZ}
        self.actual = np.zeros((H, W, 3), np.uint8)
        self._precalcular()

    def proyectar_verdad(self, Xapp: np.ndarray) -> np.ndarray:
        v = self.v
        Xc = (Xapp * FZ) @ v["R"].T + v["t"]
        x, y = Xc[:, 0] / Xc[:, 2], Xc[:, 1] / Xc[:, 2]
        d = 1 + v["k1"] * (x * x + y * y)
        uv = np.stack([v["sx"] * v["f"] * x * d + v["cx"], v["f"] * y * d + v["cy"]], 1)
        uv[Xc[:, 2] <= 0] = np.nan
        return uv

    def _precalcular(self) -> None:
        n = self.cam.w
        mapu = np.full((n, n), -10, np.float32); mapv = np.full((n, n), -10, np.float32)
        alb = np.zeros((n, n), np.float32)
        Cp = self.v["C_app"]
        piso = next((plano_de_ancla(a) for a in self.anclas if a["etiqueta"] == "FLOOR"), None)
        for r0 in range(0, n, 96):
            vv, uu = np.mgrid[r0:min(n, r0 + 96), 0:n]
            uv = np.stack([uu.ravel(), vv.ravel()], 1).astype(float)
            D = self.cam.rayos(uv)
            o = self.cam.o
            tm = rayo_plano(o, D, self.mesa, margen=0)
            tp = rayo_plano(o, D, piso, margen=10) if piso else np.full(len(D), np.inf)
            tb = [rayo_caja(o, D, b0, b1) for b0, b1 in self.cajas]
            T = np.stack([tm, tp] + tb, 1)
            k = T.argmin(1)
            t = T[np.arange(len(T)), k]
            X = o + np.where(np.isfinite(t), t, 0)[:, None] * D
            a = np.choose(k, [0.8, 0.45] + [0.65] * len(tb)).astype(np.float32)
            a[~np.isfinite(t)] = 0
            # sombra: el segmento X→proyector pega en una caja
            Ds = Cp - X
            L = np.linalg.norm(Ds, axis=1, keepdims=True)
            Ds = Ds / np.maximum(L, 1e-9)
            for b0, b1 in self.cajas:
                ts = rayo_caja(X + Ds * 1e-4, Ds, b0, b1)
                a[(ts < L[:, 0] - 1e-3)] *= 0.0
            puv = self.proyectar_verdad(X)
            bad = ~np.isfinite(puv).all(1) | (a <= 0)
            puv[bad] = -10
            sl = slice(r0, min(n, r0 + 96))
            mapu[sl] = puv[:, 0].reshape(-1, n); mapv[sl] = puv[:, 1].reshape(-1, n); alb[sl] = a.reshape(-1, n)
        self.mapu, self.mapv, self.alb = mapu, mapv, alb

    def render(self) -> np.ndarray:
        g = cv2.cvtColor(self.actual, cv2.COLOR_BGR2GRAY).astype(np.float32) if self.actual.ndim == 3 else self.actual.astype(np.float32)
        L = cv2.remap(g, self.mapu, self.mapv, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        img = 10 + self.alb * (6 + 0.8 * L)
        img = cv2.GaussianBlur(img, (0, 0), 0.8) + self.rng.normal(0, 1.5, img.shape)
        return np.clip(img, 0, 255).astype(np.uint8)

    def malla_json(self) -> dict:
        def quad(p0, p1, p2, p3):
            return [p0, p1, p2, p0, p2, p3]
        pts = []
        m = self.mesa
        c, e1, e2 = m["c"], m["e1"], m["e2"]
        hw, hh = m["w"] / 2, m["h"] / 2
        pts += quad(c - e1 * hw - e2 * hh, c + e1 * hw - e2 * hh, c + e1 * hw + e2 * hh, c - e1 * hw + e2 * hh)
        if self.cajas:
            b0, b1 = self.cajas[0]
            x0, y0, z0 = b0; x1, y1, z1 = b1
            P = lambda x, y, z: np.array([x, y, z])  # noqa: E731
            pts += quad(P(x0, y1, z0), P(x1, y1, z0), P(x1, y1, z1), P(x0, y1, z1))
            pts += quad(P(x0, y0, z0), P(x1, y0, z0), P(x1, y1, z0), P(x0, y1, z0))
            pts += quad(P(x0, y0, z1), P(x1, y0, z1), P(x1, y1, z1), P(x0, y1, z1))
            pts += quad(P(x0, y0, z0), P(x0, y0, z1), P(x0, y1, z1), P(x0, y1, z0))
            pts += quad(P(x1, y0, z0), P(x1, y0, z1), P(x1, y1, z1), P(x1, y1, z0))
        pos = np.array(pts).ravel().round(4).tolist()
        return {"version": 0, "anclas": self.anclas, "mruk": {"pos": pos, "tipo": [0] * (len(pts) // 3)}}


def anclas_por_defecto(mesa_grande: bool = False) -> list:
    R = np.stack([[-0.9997, 0, 0.0254], [0.0254, 0, 0.9997], [0, 1, 0]], 1)   # columnas: x, y (arriba), z (normal)
    Rf = np.stack([[-0.011, 0, 0.9999], [0.9999, 0, 0.011], [0, 1, 0]], 1)
    return [{"etiqueta": "TABLE", "pose": {"t": [0.1818, 0.1935, 0.3619], "q": mat_quat(R)},
             "plano": [4.0, 4.0] if mesa_grande else [1.4981, 0.8795]},
            {"etiqueta": "FLOOR", "pose": {"t": [0.8817, -0.5539, -1.1467], "q": mat_quat(Rf)}, "plano": [4.1288, 7.2466]}]


def sim_para(s: Sesion) -> Sim:
    pose = s.jr("cam_pose.json")
    anclas = s.jr("malla.json")["anclas"]
    esc = s.jr("verdad_sintetica.json").get("escena") if s.p("verdad_sintetica.json").exists() else None
    return Sim(pose, anclas, seed=1, escena=esc)


# ============================================================================ 1. captura

def lista_patrones(bits: int = BITS) -> list[dict]:
    out = [{"nombre": "negro", "tipo": "negro"}, {"nombre": "blanco", "tipo": "blanco"}]
    for eje, n in ((0, "x"), (1, "y")):
        for b in range(bits - 1, -1, -1):
            for inv in (False, True):
                out.append({"nombre": f"{n}{b:02d}{'i' if inv else 'n'}", "tipo": "gray", "eje": eje, "bit": b, "inverso": inv})
    return out


def imagen_patron(p: dict) -> np.ndarray:
    if p["tipo"] == "negro":
        g = np.zeros((H, W), np.uint8)
    elif p["tipo"] == "blanco":
        g = np.full((H, W), 255, np.uint8)
    else:
        g = gc_pattern((W, H), p["eje"], p["bit"], p["inverso"])
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


class Capturador:
    """Manda un patrón y espera ≥ 1,5 s (AirPlay) + el cambio real en la cámara (cuadros rápidos de 864 px) y que se
    estabilice; recién ahí toma el cuadro completo (o el promedio de n)."""

    def __init__(self, s: Sesion, cam: CamaraQuest, proy: Proyector, promedio: int = 1):
        self.s, self.cam, self.proy = s, cam, proy
        self.lat = 1.5
        self.roi_r = None          # región iluminada en el cuadro rápido
        self.roi = None            # ídem en el completo
        self.ref_blanco = None
        self.ref_negro = None
        self.prev_r = None         # cuadro rápido del patrón anterior
        self.n = promedio

    def _cambio(self, a, b) -> float:
        d = np.abs(a.astype(np.int16) - b.astype(np.int16))
        m = self.roi_r if self.roi_r is not None and self.roi_r.shape == d.shape else np.ones(d.shape, bool)
        return float((d[m] > 18).mean()) if m.any() else 0.0

    def esperar_cuadro(self, t_envio: float, min_cambio: float = 0.05, extra: float = 1.6, minimo: float = 1.5):
        if self.s.dry:
            f = self.cam.promedio(2)
            self.prev_r = cv2.resize(np.clip(f, 0, 255).astype(np.uint8), (864, 864), interpolation=cv2.INTER_AREA)
            return f, {"cambio": 1.0, "espera_s": 0.0, "por": "sim"}
        time.sleep(max(0.0, t_envio + max(minimo, self.lat - 0.3) - time.time()))
        ultimo, aviso = None, False
        while True:
            f = self.cam.rapido()
            el = time.time() - t_envio
            frac = 1.0 if self.prev_r is None else self._cambio(f, self.prev_r)
            if ultimo is not None:
                est = self._cambio(f, ultimo)
                if est < 0.015 and (frac >= min_cambio or el > self.lat + extra):
                    full = self.cam.promedio(self.n)
                    self.prev_r = f
                    por = "cambio" if frac >= min_cambio else "tiempo (sin cambio visible)"
                    return full, {"cambio": round(frac, 4), "espera_s": round(el, 2), "por": por}
            if el > self.lat + 12 and not aviso:
                self.s.log(f"  la imagen no se estabiliza tras {el:.0f} s (¿alguien se mueve frente al Quest?); sigo esperando")
                aviso = True
            if el > 90:
                raise RuntimeError("la imagen no se estabiliza en 90 s")
            ultimo = f
            time.sleep(0.04)

    def medir_latencia(self) -> None:
        negro = imagen_patron({"tipo": "negro"}); blanco = imagen_patron({"tipo": "blanco"})
        self.proy.mostrar(negro, "negro")
        self.s.dormir(3.0)
        if self.s.dry:
            return
        a = self.cam.rapido()
        lats = []
        for img, nombre in ((blanco, "blanco"), (negro, "negro"), (blanco, "blanco")):
            t0 = time.time()
            self.proy.mostrar(img, nombre)
            while True:
                f = self.cam.rapido()
                if self._cambio(f, a) > 0.02:
                    lats.append(time.time() - t0); break
                if time.time() - t0 > 10:
                    raise RuntimeError("no veo cambio entre negro y blanco en 10 s: ¿el Quest ve la proyección? ¿AirPlay vivo?")
                time.sleep(0.03)
            time.sleep(1.5)
            a = self.cam.rapido()
        self.lat = max(lats)
        self.s.log(f"latencia ventana→proyector→cámara: {', '.join(f'{x:.2f}' for x in lats)} s → uso {self.lat:.2f} s")

    def revalidar(self) -> None:
        """Tras una pausa de la salida: blanco de referencia y comparación con el original."""
        if self.ref_blanco is None:
            return
        self.proy.mostrar(imagen_patron({"tipo": "blanco"}), "blanco")
        self.prev_r = None
        f, _ = self.esperar_cuadro(time.time())
        con = (self.ref_blanco - self.ref_negro)[self.roi]
        d = np.abs(f - self.ref_blanco)[self.roi]
        rel = float(np.median(d) / max(1.0, float(np.median(con))))
        self.s.log(f"revalidación tras pausa: |blanco - blanco_ref| mediana = {rel:.2f} del contraste")
        if rel > 0.35:
            raise RuntimeError("tras la pausa la proyección no se ve donde debe (el blanco no coincide con la referencia)")

    def mostrar(self, img, nombre) -> float:
        espero = self.proy.mostrar(img, nombre)
        if espero:
            self.revalidar()
            self.proy.mostrar(img, nombre)
        return time.time()


def detectar_aruco_ventana(img: np.ndarray, jv: dict) -> dict:
    """Marcadores de la grilla de la ventana en el cuadro: pares esquina proyector (centros enteros) ↔ cámara."""
    dic = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    prm = cv2.aruco.DetectorParameters()
    prm.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    corners, ids, _ = cv2.aruco.ArucoDetector(dic, prm).detectMarkers(img)
    por_id = {int(m["id"]): np.array(m["esquinas_px"], float) - 0.5 for m in jv["markers"]}   # bordes → centros enteros
    proj, cam, idl = [], [], []
    for c, i in zip(corners, (ids.ravel() if ids is not None else [])):
        if int(i) in por_id:
            proj += por_id[int(i)].tolist(); cam += c.reshape(4, 2).tolist(); idl += [int(i)] * 4
    return {"proj": proj, "cam": cam, "ids": idl, "detectados": sorted(set(idl)), "de": len(por_id)}


def paso_captura(s: Sesion, bits: int = BITS, promedio: int = 1) -> None:
    global VIGIA
    s.log(f"== captura ({'SINTÉTICA' if s.dry else 'en vivo'}) en {s.dir}")
    sim = None
    meta = {"bits": bits, "inicio": dt.datetime.now().isoformat(timespec="seconds"), "capturas": []}
    if s.dry:
        pose = {"m4": None, "t": CAM_DEF["t"], "q": CAM_DEF["q"], "intr": CAM_DEF["intr"], "dist": None,
                "ancla_sala_m4": np.eye(4).ravel().tolist(), "fuente": "por defecto (dry)"}
        M = np.eye(4); M[:3, :3] = quat_mat(pose["q"]); M[:3, 3] = pose["t"]; pose["m4"] = M.tolist()
        s.jw("cam_pose.json", pose)
        esc = os.environ.get("CALIB_DRY_ESCENA", "mesa+cajas")
        sim = Sim(pose, anclas_por_defecto(mesa_grande=esc.startswith("plano")), seed=1, escena=esc)
        s.jw("malla.json", sim.malla_json())
        v = sim.v
        s.log(f"escena sintética: {sim.escena}")
        s.jw("verdad_sintetica.json", {"escena": sim.escena, "f": v["f"], "cx": v["cx"], "cy": v["cy"], "k1": v["k1"], "sx": v["sx"],
                                       "R": v["R"], "t": v["t"], "C_app": v["C_app"]})
        meta["ventana"] = {"size": [W, H], "display": [0, 1080], "pos": [10, 1116], "sintetica": True}
    else:
        if buscar_ventana() is None:
            raise SystemExit("no encuentro la ventana del proyector (playground/ventana_proyector.py en :8793/:8792): "
                             "no proyecto nada (y no abro ventanas propias)")
        est = ventana_estado()
        fijar_proyector(*est["size"])
        meta["ventana"] = {**est, "url": VENTANA}
        s.log(f"ventana del proyector: {VENTANA} contenido {W}x{H} en {est.get('pos')} (display {est.get('display')}), {est.get('fps')} fps")
        if not display_ok():
            s.log(f"OJO: no veo el display {DISPLAY[:2]} {DISPLAY[2]}x{DISPLAY[3]}: espero antes de proyectar")
        VIGIA = VigiaDCPEXT(s)
        estado = quest_json("/estado.json")
        s.jw("estado.json", estado)
        s.log(f"Quest: cámara {estado['camara'].get('estado')}")
        malla = quest_json("/malla.json", timeout=60)
        s.jw("malla.json", malla)
        s.log(f"malla MRUK: {len((malla.get('mruk') or {}).get('tipo') or [])} triángulos, {len(malla.get('anclas') or [])} anclas")
        s.jw("cam_pose.json", leer_pose_camara(s))
        s.log(f"exposición: ae_lock on → {quest_cmd('ae_lock on').get('r')}")
    meta["proyector"] = [W, H]
    cam = CamaraQuest(s, sim)
    cam.elegir_fuente()
    meta["fuente"] = cam.fuente
    proy = Proyector(s, sim)
    cap = Capturador(s, cam, proy, promedio=promedio)
    cdir = s.p("capturas"); cdir.mkdir(exist_ok=True)
    try:
        cap.medir_latencia()
        refs = {}
        for nombre in ("negro", "blanco"):
            t0 = cap.mostrar(imagen_patron({"tipo": nombre}), nombre)
            f, info = cap.esperar_cuadro(t0)
            if not s.dry:
                f = (f + cam.promedio(2) * 2) / 3
            refs[nombre] = f
            cv2.imwrite(str(cdir / f"{nombre}.png"), np.clip(np.round(f), 0, 255).astype(np.uint8))
            meta["capturas"].append({"nombre": nombre, "archivo": f"capturas/{nombre}.png", "t_envio": t0, **info})
        con = refs["blanco"] - refs["negro"]
        thr = max(12.0, 0.25 * float(np.percentile(con, 99.5)))
        cap.roi = cv2.erode((con > thr).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        cap.roi_r = cv2.resize(cap.roi.astype(np.uint8) * 255, (864, 864), interpolation=cv2.INTER_AREA) > 127
        cap.ref_blanco, cap.ref_negro = refs["blanco"], refs["negro"]
        frac = float(cap.roi.mean())
        s.log(f"región iluminada: {frac * 100:.1f}% del cuadro (umbral {thr:.0f}); contraste mediano "
              f"{np.median(con[cap.roi]) if frac > 0 else 0:.0f} niveles")
        if frac < 0.01:
            raise RuntimeError("el Quest casi no ve la proyección (blanco ≈ negro): ¿apunta a la mesa? ¿luz de la sala?")
        # ---- primera vista: grillas ArUco de la ventana, cada una con su invertida; se detecta sobre la binaria por
        # signo de (normal − invertida) + medianBlur 5: cancela el albedo de la mesa (teclado, cables)
        vistas = []
        for nombre in ARUCO_GRILLAS:
            jp = ARUCO_DIR / f"{nombre}.json"
            if not jp.exists():
                continue
            jv = json.loads(jp.read_text())
            if tuple(jv.get("size", [0, 0])) != (W, H):
                s.log(f"{nombre}.json es {jv.get('size')} y la ventana {W}x{H}: la salto")
                continue
            img = cv2.imread(str(ARUCO_DIR / f"{nombre}.png"), cv2.IMREAD_COLOR)
            pinv = ARUCO_DIR / f"{nombre}_inv.png"
            inv = cv2.imread(str(pinv), cv2.IMREAD_COLOR) if pinv.exists() else 255 - img
            fa, _ = cap.esperar_cuadro(cap.mostrar(img, nombre))
            fb, _ = cap.esperar_cuadro(cap.mostrar(inv, nombre + "_inv"))
            b = np.where(fa - fb > 0, 255, 0).astype(np.uint8)
            b = cv2.medianBlur(b, 5)
            b[~cv2.dilate(cap.roi.astype(np.uint8), np.ones((25, 25), np.uint8)).astype(bool)] = 0
            for suf, im in (("a", fa), ("b", fb)):
                cv2.imwrite(str(cdir / f"{nombre}_{suf}.png"), np.clip(np.round(im), 0, 255).astype(np.uint8))
            cv2.imwrite(str(cdir / f"{nombre}_bin.png"), b)
            det = detectar_aruco_ventana(b, jv)
            det["grilla"] = nombre
            vistas.append(det)
            s.log(f"ArUco {nombre} (normal − invertida, binaria por signo): {len(det['detectados'])}/{det['de']} marcadores {det['detectados']}")
        if vistas:
            out = {"vistas": [{k: v for k, v in d.items()} for d in vistas],
                   "proj": [q for d in vistas for q in d["proj"]], "cam": [q for d in vistas for q in d["cam"]],
                   "ids": [f"{d['grilla']}:{i}" for d in vistas for i in d["ids"]]}
            if sim is None:
                try:
                    out["app"] = quest_json("/estado.json").get("aruco")
                except Exception as e:  # noqa: BLE001
                    out["app"] = {"error": str(e)}
            s.jw("aruco_2d.json", out)
        cap.prev_r = None
        for i, p in enumerate(lista_patrones(bits)[2:]):
            t0 = cap.mostrar(imagen_patron(p), p["nombre"])
            f, info = cap.esperar_cuadro(t0)
            arch = f"capturas/{i + 2:02d}_{p['nombre']}.png"
            cv2.imwrite(str(s.p(arch)), np.clip(np.round(f), 0, 255).astype(np.uint8))
            meta["capturas"].append({**p, "archivo": arch, "t_envio": t0, **info})
            if i % 6 == 0 or info["por"] != "cambio":
                s.log(f"  {p['nombre']}: cambio {info['cambio']:.3f} a los {info['espera_s']:.1f} s ({info['por']})")
        meta["fin"] = dt.datetime.now().isoformat(timespec="seconds")
        meta["latencia_s"] = cap.lat
    finally:
        try:
            proy.apagar()
        finally:
            s.jw("captura.json", meta)
            if VIGIA is not None:
                VIGIA.cerrar()
                VIGIA = None
    s.log(f"captura lista: {len(meta['capturas'])} imágenes en {cdir}")


# ============================================================================ 2. decodificación

def paso_decodificar(s: Sesion, paso: int = 24, k_rel: float = 0.22, min_frac: float = 0.72) -> None:
    s.log("== decodificación")
    meta = s.jr("captura.json")
    caps = {c["nombre"]: c for c in meta["capturas"]}
    bits = int(meta.get("bits", BITS))
    load = lambda n: cv2.imread(str(s.p(caps[n]["archivo"])), cv2.IMREAD_GRAYSCALE).astype(np.float32)  # noqa: E731
    hh, ww = load("negro").shape
    diffs = {}
    for eje, n in ((0, "x"), (1, "y")):
        for b in range(bits):
            diffs[(eje, b)] = (load(f"{n}{b:02d}n") - load(f"{n}{b:02d}i")).astype(np.float32)
    gruesos = [np.abs(diffs[(e, b)]) for e in (0, 1) for b in range(bits - 3, bits)]
    ref = np.median(np.stack(gruesos), axis=0)
    lit = ref > max(6.0, 0.12 * float(np.percentile(ref, 99.5)))
    lit = cv2.morphologyEx(lit.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
    s.log(f"cámara {ww}x{hh}; iluminados {lit.mean() * 100:.1f}%; contraste grueso mediano {np.median(ref[lit]):.1f}")
    thr = np.maximum(3.0, k_rel * ref)
    skip = {}
    fr_bits = {}
    for eje, n in ((0, "x"), (1, "y")):
        sk = 0
        fr = {b: float((np.abs(diffs[(eje, b)])[lit] > thr[lit]).mean()) for b in range(bits)}
        fr_bits[n] = {str(b): round(v, 3) for b, v in fr.items()}
        while sk < bits - 3 and fr[sk] < min_frac:
            sk += 1
        skip[n] = sk
    s.log(f"bits legibles (fracción de iluminados con contraste): x {fr_bits['x']} → omito {skip['x']}; "
          f"y {fr_bits['y']} → omito {skip['y']}")
    coord = {}
    valido = lit.copy()
    for eje, n in ((0, "x"), (1, "y")):
        g = np.zeros((hh, ww), np.uint16)
        for b in range(skip[n], bits):
            d = diffs[(eje, b)]
            g |= ((d > 0).astype(np.uint16) << np.uint16(b))
            valido &= np.abs(d) > thr
        bn = gray2bin(g)
        sk = skip[n]
        blk = (bn >> sk).astype(np.int32)
        coord[n] = blk
    bx, by = coord["x"], coord["y"]
    sx_, sy_ = 1 << skip["x"], 1 << skip["y"]
    px = bx * sx_ + (sx_ - 1) / 2.0
    py = by * sy_ + (sy_ - 1) / 2.0
    valido &= (px < W) & (py < H)
    s.log(f"decodificados {valido.sum()} px de cámara ({valido.sum() / max(1, lit.sum()) * 100:.1f}% de los iluminados)")
    np.savez_compressed(s.p("mapa_cam_proj.npz"), px=np.where(valido, px, -1).astype(np.float32),
                        py=np.where(valido, py, -1).astype(np.float32), valido=valido, ref=ref.astype(np.float32),
                        skip=np.array([skip["x"], skip["y"]]))
    # imágenes de control
    vis = np.zeros((hh, ww, 3), np.uint8)
    hsv = np.zeros((hh, ww, 3), np.uint8)
    hsv[..., 0] = np.clip(px / W * 170, 0, 179).astype(np.uint8); hsv[..., 1] = 255
    hsv[..., 2] = np.where(valido, 255, 0).astype(np.uint8)
    vis = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    rejilla = (((px.astype(int) // 120) + (py.astype(int) // 120)) % 2 == 0) & valido
    vis[rejilla] = (vis[rejilla] * 0.55).astype(np.uint8)
    cv2.imwrite(str(s.p("decodificado.jpg")), vis)

    # ---- pares subpíxel en una grilla del proyector: centroides por bloque + afín local
    nbx, nby = (W + sx_ - 1) // sx_, (H + sy_ - 1) // sy_
    ys, xs = np.nonzero(valido)
    idx = by[ys, xs] * nbx + bx[ys, xs]
    nb = nbx * nby
    cnt = np.bincount(idx, minlength=nb).astype(np.float64)
    su = np.bincount(idx, xs, minlength=nb); sv = np.bincount(idx, ys, minlength=nb)
    suu = np.bincount(idx, xs * xs.astype(np.float64), minlength=nb); svv = np.bincount(idx, ys * ys.astype(np.float64), minlength=nb)
    ok = cnt >= 1
    cu = np.where(ok, su / np.maximum(cnt, 1), np.nan); cv_ = np.where(ok, sv / np.maximum(cnt, 1), np.nan)
    var = np.where(ok, suu / np.maximum(cnt, 1) - cu ** 2 + svv / np.maximum(cnt, 1) - cv_ ** 2, np.inf)
    esc = math.sqrt(max(1.0, float(np.median(cnt[cnt > 0]))))     # tamaño típico de un bloque en la cámara (px)
    ok &= var < (2.5 * esc + 2) ** 2                                # bloques desparramados = errores de decodificación
    gbx, gby = np.meshgrid(np.arange(nbx), np.arange(nby))
    pcx = (gbx * sx_ + (sx_ - 1) / 2.0).ravel(); pcy = (gby * sy_ + (sy_ - 1) / 2.0).ravel()
    okb = ok.reshape(nby, nbx)
    r = max(3.0 * max(sx_, sy_), 14.0)
    rbx, rby = int(math.ceil(r / sx_)), int(math.ceil(r / sy_))
    nodos_u = np.arange(paso / 2, W, paso); nodos_v = np.arange(paso / 2, H, paso)
    out_p, out_c, out_res, out_n = [], [], [], []
    for qv in nodos_v:
        for qu in nodos_u:
            i0, j0 = int(qu // sx_), int(qv // sy_)
            ja, jb = max(0, j0 - rby), min(nby, j0 + rby + 1)
            ia, ib = max(0, i0 - rbx), min(nbx, i0 + rbx + 1)
            sel = okb[ja:jb, ia:ib]
            if sel.sum() < 8:
                continue
            ii = (np.arange(ja, jb)[:, None] * nbx + np.arange(ia, ib)[None, :])[sel]
            dp = np.stack([pcx[ii] - qu, pcy[ii] - qv], 1)
            dd = np.hypot(dp[:, 0], dp[:, 1])
            m = dd <= r
            if m.sum() < 8:
                continue
            ii, dp = ii[m], dp[m]
            if (dp[:, 0] < 0).sum() < 2 or (dp[:, 0] > 0).sum() < 2 or (dp[:, 1] < 0).sum() < 2 or (dp[:, 1] > 0).sum() < 2:
                continue
            c = np.stack([cu[ii], cv_[ii]], 1)
            wgt = cnt[ii]
            A = np.c_[np.ones(len(dp)), dp]
            for _ in range(3):
                sw = np.sqrt(wgt)[:, None]
                sol, *_ = np.linalg.lstsq(A * sw, c * sw, rcond=None)
                res = np.linalg.norm(A @ sol - c, axis=1)
                mad = max(0.5, 1.4826 * float(np.median(res)))
                keep = res < 3 * mad + 0.5
                if keep.all() or keep.sum() < 8:
                    break
                A, c, wgt = A[keep], c[keep], wgt[keep]
            rr = float(np.sqrt(np.average(res ** 2, weights=np.ones(len(res)))))
            if rr > 2.5:
                continue
            out_p.append((qu, qv)); out_c.append(sol[0]); out_res.append(rr); out_n.append(int(wgt.sum()))
    out_p = np.array(out_p, float); out_c = np.array(out_c, float)
    np.savez_compressed(s.p("pares_2d.npz"), proj=out_p, cam=out_c, res=np.array(out_res), n=np.array(out_n),
                        cam_size=np.array([ww, hh]), skip=np.array([skip["x"], skip["y"]]))
    s.jw("decodificacion.json", {"bits_omitidos": skip, "fraccion_bits": fr_bits, "px_decodificados": int(valido.sum()),
                                 "pares": len(out_p), "paso_px": paso, "residuo_afin_cam_px": stats(out_res),
                                 "camara": [ww, hh]})
    s.log(f"pares proyector↔cámara (grilla {paso} px): {len(out_p)}; residuo afín local en cámara {stats(out_res)}")


# ============================================================================ 3. geometría (rayos ∩ sala)

def rayos_a_sala(cam: Cam, malla: Malla, planos: list, cuv: np.ndarray, tol_plano: float = 0.03) -> dict:
    """Rayos de píxeles de la cámara ∩ (planos de anclas, malla MRUK). Etiqueta: ancla del plano, 'objeto' (malla delante
    de un plano: cosas sobre la mesa), 'malla' o 'nada'."""
    D = cam.rayos(cuv)
    o = cam.o
    tm = malla.interseca(o, D)
    tp = np.full(len(D), np.inf); ip = np.full(len(D), -1)
    for k, pl in enumerate(planos):
        t = rayo_plano(o, D, pl, margen=0.03)
        m = t < tp
        tp[m] = t[m]; ip[m] = k
    etiqueta = np.full(len(D), "nada", dtype=object)
    t = np.full(len(D), np.inf)
    with np.errstate(invalid="ignore"):
        usa_plano = np.isfinite(tp) & (~np.isfinite(tm) | (np.abs(tm - tp) < tol_plano) | (tm > tp))
    for k in np.unique(ip[usa_plano]):
        m = usa_plano & (ip == k)
        etiqueta[m] = planos[k]["etiqueta"]
    t[usa_plano] = tp[usa_plano]
    obj = np.isfinite(tm) & ~usa_plano
    etiqueta[obj & np.isfinite(tp)] = "objeto"
    etiqueta[obj & ~np.isfinite(tp)] = "malla"
    t[obj] = tm[obj]
    X = o + np.where(np.isfinite(t), t, 0)[:, None] * D
    X[~np.isfinite(t)] = np.nan
    return {"X": X, "D": D, "o": o, "etiqueta": etiqueta.astype(str), "t_malla": tm, "t_plano": tp, "plano": ip}


def paso_geometria(s: Sesion, tol_plano: float = 0.03) -> None:
    s.log("== geometría: rayos de la cámara ∩ malla MRUK / planos de anclas")
    pr = np.load(s.p("pares_2d.npz"))
    proj, cuv = pr["proj"], pr["cam"]
    ww = int(pr["cam_size"][0])
    pose = s.jr("cam_pose.json")
    cam = Cam(pose, ww)
    malla = Malla(s.jr("malla.json"))
    planos = [plano_de_ancla(a) for a in malla.anclas if a.get("plano")]
    t0 = time.time()
    g = rayos_a_sala(cam, malla, planos, cuv, tol_plano)
    s.log(f"malla: {len(malla.tri)} triángulos, {np.isfinite(g['t_malla']).sum()}/{len(cuv)} rayos pegan ({time.time() - t0:.1f} s)")
    etiqueta, ip = g["etiqueta"], g["plano"]
    mesas = [(int(((etiqueta == "TABLE") & (ip == k)).sum()), k) for k, pl in enumerate(planos) if pl["etiqueta"] == "TABLE"]
    mesas.sort(reverse=True)
    mesa_k = mesas[0][1] if mesas and mesas[0][0] > 0 else None
    cuenta = {str(e): int((etiqueta == e).sum()) for e in np.unique(etiqueta)}
    s.log(f"etiquetas: {cuenta}; mesa = ancla #{mesa_k}")
    np.savez_compressed(s.p("puntos3d.npz"), proj=proj, cam=cuv, **g)
    info = {"cuenta": cuenta, "mesa_ancla": mesa_k}
    if mesa_k is not None:
        pl = planos[mesa_k]
        info["mesa"] = {"c": pl["c"], "n": pl["n"], "e1": pl["e1"], "e2": pl["e2"], "w": pl["w"], "h": pl["h"]}
    piso = next((pl for pl in planos if pl["etiqueta"] == "FLOOR"), None)
    techo = next((pl for pl in planos if pl["etiqueta"] == "CEILING"), None)
    info["piso_y"] = float(piso["c"][1]) if piso else None
    info["techo_y"] = float(techo["c"][1]) if techo else None
    # primera vista: esquinas de la grilla ArUco de la ventana
    if s.p("aruco_2d.json").exists():
        a = s.jr("aruco_2d.json")
        if a.get("cam"):
            camA = Cam(pose, 1728)
            ga = rayos_a_sala(camA, malla, planos, np.array(a["cam"], float), tol_plano)
            np.savez_compressed(s.p("aruco3d.npz"), proj=np.array(a["proj"], float), cam=np.array(a["cam"], float),
                                ids=np.array([str(i) for i in a["ids"]]), **ga)
            ca = {str(e): int((ga["etiqueta"] == e).sum()) for e in np.unique(ga["etiqueta"])}
            info["aruco"] = {"esquinas": len(a["cam"]), "etiquetas": ca}
            s.log(f"ArUco: {len(a['cam'])} esquinas → {ca}")
    s.jw("geometria.json", info)


# ============================================================================ 4. PnP / calibración del proyector

NOMBRES = ["f", "cx", "cy", "k1", "r0", "r1", "r2", "t0", "t1", "t2"]
PERDIDA = "soft_l1"                     # el bundle usa "huber"
PLANOS = ["TABLE", "FLOOR", "WALL_FACE", "CEILING", "STORAGE", "BED", "COUCH", "SCREEN", "WALL_ART", "DOOR_FRAME",
          "WINDOW_FRAME", "INVISIBLE_WALL_FACE"]


def proyectar_modelo(par, Xr, sx):
    R = cv2.Rodrigues(np.asarray(par[4:7], float))[0]
    Xc = Xr @ R.T + par[7:10]
    z = Xc[:, 2]
    x, y = Xc[:, 0] / z, Xc[:, 1] / z
    d = 1 + par[3] * (x * x + y * y)
    return np.stack([sx * par[0] * x * d + par[1], par[0] * y * d + par[2]], 1), z


def matriz_P(par, sx) -> np.ndarray:
    R = cv2.Rodrigues(np.asarray(par[4:7], float))[0]
    K = np.array([[sx * par[0], 0, par[1]], [0, par[0], par[2]], [0, 0, 1.0]])
    return K @ np.c_[R, par[7:10]]


def epipolar(par, sx, o_r, Dr, pe) -> np.ndarray:
    P = matriz_P(par, sx)
    e = P @ np.r_[o_r, 1.0]
    v = Dr @ P[:, :3].T
    l = np.cross(np.broadcast_to(e, v.shape), v)
    return (l[:, 0] * pe[:, 0] + l[:, 1] * pe[:, 1] + l[:, 2]) / np.maximum(np.hypot(l[:, 0], l[:, 1]), 1e-12)


def residuo_prior(par, prior) -> np.ndarray:
    """Best guess de la posición: altura del proyector sobre la cámara (a lo largo de 'arriba') y distancia horizontal."""
    C = centro_r(par) - prior["o"]
    h = float(C @ prior["up"])
    lat = float(np.linalg.norm(C - h * prior["up"]))
    return np.array([(h - prior["dh"]) / prior["sh"], (lat - prior["dl"]) / prior["sl"]])


def ajustar(par0, sx, Xr, p, libres, o_r=None, Dr=None, pe=None, peso_e=1.0, f_scale=1.5, loss=None, prior=None):
    from scipy.optimize import least_squares
    idx = [NOMBRES.index(n) for n in libres]
    par0 = np.asarray(par0, float)

    def fun(z):
        par = par0.copy(); par[idx] = z
        uv, _ = proyectar_modelo(par, Xr, sx)
        r = [(uv - p).ravel()]
        if Dr is not None and len(Dr):
            r.append(peso_e * epipolar(par, sx, o_r, Dr, pe))
        if prior is not None:
            r.append(residuo_prior(par, prior))
        return np.concatenate(r)

    res = least_squares(fun, par0[idx], loss=loss or PERDIDA, f_scale=f_scale, x_scale="jac", max_nfev=400)
    par = par0.copy(); par[idx] = res.x
    n, k = len(res.fun), len(idx)
    sig = {}
    try:
        J = res.jac
        s2 = float(np.sum(res.fun ** 2) / max(1, n - k))
        cov = np.linalg.pinv(J.T @ J) * s2
        sig = {nm: float(math.sqrt(max(0.0, cov[i, i]))) for i, nm in enumerate(libres)}
        # incertidumbre del centro del proyector (propagación numérica)
        Jc = np.zeros((3, k))
        for i, j in enumerate(idx):
            dp = np.zeros(10); h = 1e-6 * max(1.0, abs(par[j])); dp[j] = h
            Jc[:, i] = (centro_r(par + dp) - centro_r(par - dp)) / (2 * h)
        cc = Jc @ cov @ Jc.T
        sig["C_mm"] = [float(math.sqrt(max(0.0, cc[i, i])) * 1000) for i in range(3)]
    except Exception:  # noqa: BLE001
        pass
    return par, float(res.cost), sig


def reproj(par, sx, Xr, p) -> np.ndarray:
    uv, _ = proyectar_modelo(par, Xr, sx)
    return np.linalg.norm(uv - p, axis=1)


def centro_r(par) -> np.ndarray:
    R = cv2.Rodrigues(np.asarray(par[4:7], float))[0]
    return -R.T @ par[7:10]


def triangular(par, sx, o_r, Dr, p):
    """Punto medio entre el rayo de la cámara y el del proyector (diestro). Devuelve (X, separación m, ángulo °)."""
    x = (p[:, 0] - par[1]) / (sx * par[0]); y = (p[:, 1] - par[2]) / par[0]
    xd, yd = x.copy(), y.copy()
    for _ in range(10):
        r2 = x * x + y * y
        x, y = xd / (1 + par[3] * r2), yd / (1 + par[3] * r2)
    R = cv2.Rodrigues(np.asarray(par[4:7], float))[0]
    dp = np.stack([x, y, np.ones_like(x)], 1) @ R
    dp /= np.linalg.norm(dp, axis=1, keepdims=True)
    Cp = centro_r(par)
    w0 = o_r - Cp
    b = np.einsum("ij,ij->i", Dr, dp)
    d = Dr @ w0
    e = dp @ w0
    den = np.maximum(1 - b * b, 1e-12)
    sa = (b * e - d) / den
    sb = (e - b * d) / den
    P1 = o_r + sa[:, None] * Dr
    P2 = Cp + sb[:, None] * dp
    ang = np.degrees(np.arccos(np.clip(np.abs(b), 0, 1)))
    return (P1 + P2) / 2, np.linalg.norm(P1 - P2, axis=1), ang


def init_dlt(Xr, p):
    from quest.calib_solver import descomponer, dlt
    K, R, t = descomponer(dlt(p, Xr))
    sx = 1.0
    if np.linalg.det(R) < 0:
        R = np.diag([-1.0, 1, 1]) @ R
        sx = -1.0
    f = (abs(K[0, 0]) + abs(K[1, 1])) / 2
    rv = cv2.Rodrigues(R)[0].ravel()
    return np.r_[f, K[0, 2], K[1, 2], 0.0, rv, t], sx


def init_planos(Xr, p, cx0: float | None = None):
    """Candidatos (par, sx) para datos coplanares: K supuesta (cx fijo) + IPPE (dos soluciones) por quiralidad."""
    cx0 = W / 2 if cx0 is None else cx0
    c = Xr.mean(0)
    _, _, Vt = np.linalg.svd(Xr - c)
    e1, e2 = Vt[0], Vt[1]
    Q = np.stack([(Xr - c) @ e1, (Xr - c) @ e2], 1)
    Hm, _ = cv2.findHomography(Q, p, cv2.RANSAC, 5.0)
    cands = []
    # Zhang con cx conocido, skew 0, fx = fy: dos ecuaciones lineales en (B23, B33)
    try:
        h1, h2 = Hm[:, 0], Hm[:, 1]

        def fila(a, b):
            return (a[0] * b[0] + a[1] * b[1] - cx0 * (a[0] * b[2] + a[2] * b[0]), a[1] * b[2] + a[2] * b[1], a[2] * b[2])
        r12 = fila(h1, h2); r11 = fila(h1, h1); r22 = fila(h2, h2)
        A = np.array([[-r12[1], r12[2]], [-(r11[1] - r22[1]), r11[2] - r22[2]]])
        bvec = -np.array([r12[0], r11[0] - r22[0]])
        cy, b33 = np.linalg.solve(A, bvec)
        f2 = b33 - cx0 ** 2 - cy ** 2
        if f2 > 0:
            cands.append((math.sqrt(f2), cy))
    except Exception:  # noqa: BLE001
        pass
    for cy in (-540.0, 0.0, 270.0, 540.0, 810.0, 1080.0, 1350.0, 1620.0):
        for f in (1500.0, 2200.0, 3000.0, 4000.0):
            cands.append((f, cy))
    out = []
    for f, cy in cands:
        K = np.array([[f, 0, cx0], [0, f, cy], [0, 0, 1.0]])
        for sx in (1.0, -1.0):
            pp = p.copy()
            if sx < 0:
                pp[:, 0] = 2 * cx0 - pp[:, 0]
            try:
                n, rvs, tvs, _ = cv2.solvePnPGeneric(Xr.astype(np.float64), pp.astype(np.float64), K, None, flags=cv2.SOLVEPNP_IPPE)
            except cv2.error:
                continue
            for rv, tv in zip(rvs, tvs):
                out.append((np.r_[f, cx0, cy, 0.0, rv.ravel(), tv.ravel()], sx))
    return out, {"c": c, "n": Vt[2]}


def init_aruco(s: Sesion, XA, pA, mesa_c, mesa_n, lado_cam, prior=None):
    """Primera vista: PnP con las esquinas ArUco (cx fijo, o libre con el prior); devuelve (par, sx, info) o None."""
    mejorA = None
    lib = ["f", "cx", "cy", "r0", "r1", "r2", "t0", "t1", "t2"] if prior is not None else ["f", "cy", "r0", "r1", "r2", "t0", "t1", "t2"]
    for par0, sxa in init_planos(XA, pA)[0]:
        try:
            pa, costo, _ = ajustar(par0, sxa, XA, pA, lib, prior=prior)
        except Exception:  # noqa: BLE001
            continue
        if not np.all(np.isfinite(pa)) or not 300 < pa[0] < 20000:
            continue
        lado = np.sign((centro_r(pa) - mesa_c) @ mesa_n) == lado_cam
        eA = float(np.median(reproj(pa, sxa, XA, pA)))
        crit = costo if prior is not None else eA
        if mejorA is None or (lado, -crit) > (mejorA[0], -mejorA[4]):
            mejorA = (lado, eA, pa, sxa, crit)
    if mejorA is None:
        return None
    lado, eA, pa, sxa, _ = mejorA
    return pa, sxa, {"esquinas": int(len(pA)), "f": pa[0], "cy": pa[2], "sx": sxa, "C": centro_r(pa), "reproj_mediana_px": eA,
                     "lado_camara": bool(lado)}


def paso_resolver(s: Sesion) -> dict:
    s.log("== resolver: PnP del proyector")
    d = np.load(s.p("puntos3d.npz"), allow_pickle=False)
    X, proj, etq, D, o = d["X"], d["proj"], d["etiqueta"], d["D"], d["o"]
    geo = s.jr("geometria.json")
    Xr_all = X * FZ
    o_r = o * FZ
    Dr_all = D * FZ
    mesa_n = np.array(geo["mesa"]["n"]) * FZ
    mesa_c = np.array(geo["mesa"]["c"]) * FZ
    lado_cam = np.sign((o_r - mesa_c) @ mesa_n)
    prior = hacer_prior(o_r, np.array([0.0, 1.0, 0.0]))
    inits, aruco_info = [], None
    if s.p("aruco3d.npz").exists():
        A = np.load(s.p("aruco3d.npz"))
        okA = np.isin(A["etiqueta"], ["TABLE", "FLOOR", "WALL_FACE", "STORAGE"]) & np.isfinite(A["X"]).all(1)
        if okA.sum() >= 12:
            r = init_aruco(s, A["X"][okA] * FZ, A["proj"][okA], mesa_c, mesa_n, lado_cam, prior)
            if r is not None:
                pa, sxa, aruco_info = r
                aruco_info["C_app"] = aruco_info.pop("C") * FZ
                s.log(f"primera vista ArUco ({aruco_info['esquinas']} esquinas, cx fijo): f={pa[0]:.0f} cy={pa[2]:.0f} "
                      f"C={np.round(aruco_info['C_app'], 3).tolist()} reproj mediana {aruco_info['reproj_mediana_px']:.2f} px"
                      f"{'' if aruco_info['lado_camara'] else ' (OJO: bajo la mesa)'}")
                inits.append((pa, sxa))
    R = resolver_nucleo(s, Xr_all, Dr_all, o_r, proj, etq, mesa_c, mesa_n, inits, prior=prior)
    return modelo_mundo(s, R, geo, aruco_info)


PRIOR = {"dh": 0.10, "sh": 0.05, "dl": 0.20, "sl": 0.10, "activo": True}   # best guess de Max (m); --sin-prior lo apaga


def hacer_prior(o_r, up) -> dict | None:
    if not PRIOR.get("activo", True):
        return None
    up = np.asarray(up, float) / np.linalg.norm(up)
    return {"o": np.asarray(o_r, float), "up": up, **{k: PRIOR[k] for k in ("dh", "sh", "dl", "sl")}}


def resolver_nucleo(s: Sesion, Xr_all, Dr_all, o_r, proj, etq, mesa_c, mesa_n, inits=(), vista=None, prior=None) -> dict:
    """PnP robusto del proyector en un marco diestro cualquiera (mundo con z invertida, o el de la cámara del Quest).
    Xr_all: punto 3D de cada par (NaN si no hay); Dr_all: rayo de la cámara; o_r: centro de la cámara."""
    planos3d = np.isin(etq, PLANOS) & np.isfinite(Xr_all).all(1)
    Xr, p = Xr_all[planos3d], proj[planos3d]
    historial = []
    if len(p) < 30:
        raise RuntimeError(f"solo {len(p)} pares 3D: la proyección no cae en superficies conocidas de la sala")
    c = Xr.mean(0)
    _, S, Vt = np.linalg.svd(Xr - c, full_matrices=False)
    planitud = float(S[2] / S[0])
    s.log(f"pares 3D: {len(p)} ({', '.join(f'{e}={int((etq[planos3d] == e).sum())}' for e in np.unique(etq[planos3d]))});"
          f" planitud σ3/σ1 = {planitud:.4f}")
    lado_cam = np.sign((o_r - mesa_c) @ mesa_n)
    no_coplanar = planitud > 0.04

    # ---- inicialización
    cands = []
    if no_coplanar:
        try:
            cands.append(init_dlt(Xr, p))
        except Exception as e:  # noqa: BLE001
            s.log(f"DLT falló: {e}")
    rng = np.random.default_rng(0)
    sub = rng.choice(len(p), min(len(p), 700), replace=False)
    cands += list(inits)
    pc, _ = init_planos(Xr, p)
    cands += pc
    mejores = []
    con_cx = no_coplanar or prior is not None
    if prior is not None and not no_coplanar:
        s.log(f"datos coplanares: uso el best guess como prior blando (proyector {prior['dh'] * 100:.0f}±{prior['sh'] * 100:.0f} cm "
              f"sobre la cámara, {prior['dl'] * 100:.0f}±{prior['sl'] * 100:.0f} cm al lado); píxeles cuadrados")
    for par0, sx in cands:
        libres = ["f", "cx", "cy", "r0", "r1", "r2", "t0", "t1", "t2"] if con_cx else ["f", "cy", "r0", "r1", "r2", "t0", "t1", "t2"]
        try:
            par, cost, _ = ajustar(par0, sx, Xr[sub], p[sub], libres, prior=prior)
        except Exception:  # noqa: BLE001
            continue
        if not np.all(np.isfinite(par)) or par[0] < 300 or par[0] > 20000:
            continue
        C = centro_r(par)
        lado = np.sign((C - mesa_c) @ mesa_n)
        e = reproj(par, sx, Xr[sub], p[sub])
        mejores.append((float(np.median(e)), cost, lado == lado_cam, par, sx))
    if not mejores:
        raise RuntimeError("ninguna inicialización convergió")
    mejores.sort(key=lambda m: (not m[2], m[1] if prior is not None else m[0]))
    buenos = [m for m in mejores if m[2]]
    malos = [m for m in mejores if not m[2]]
    if malos and buenos:
        s.log(f"del lado de la cámara: mediana {buenos[0][0]:.2f} px; reflejado (bajo la mesa): {min(m[0] for m in malos):.2f} px → "
              f"me quedo con el lado de la cámara (sx={buenos[0][4]:+.0f})")
    med0, _, ok_lado, par, sx = mejores[0]
    par, _, _ = ajustar(par, sx, Xr, p, ["f", "cx", "cy", "r0", "r1", "r2", "t0", "t1", "t2"] if con_cx
                        else ["f", "cy", "r0", "r1", "r2", "t0", "t1", "t2"], prior=prior)
    if not ok_lado:
        s.log("OJO: ninguna solución quedó del lado de la cámara: revisar signos")
    # ---- separar plano / paralaje / atípicos por triangulación (iterativo): un par es "del plano" si su punto
    # triangulado (cámara + proyector actual) cae a < 8 mm de su punto 3D de la sala; los demás pares con epipolar
    # coherente son paralaje (objetos sobre la mesa, el cuadro, el teclado); el resto, atípicos.
    todos_idx = np.arange(len(proj))
    consist = planos3d.copy()
    par_idx = np.zeros(0, int)
    libres0 = ["f", "cx", "cy", "r0", "r1", "r2", "t0", "t1", "t2"] if con_cx else ["f", "cy", "r0", "r1", "r2", "t0", "t1", "t2"]
    for it in range(4):
        a = dict(o_r=o_r, Dr=Dr_all[par_idx], pe=proj[par_idx]) if len(par_idx) >= 50 else {}
        par, _, _ = ajustar(par, sx, Xr_all[consist], proj[consist], libres0, prior=prior, **a)
        Xt, gap, ang = triangular(par, sx, o_r, Dr_all, proj)
        dlab = np.linalg.norm(Xt - np.where(np.isfinite(Xr_all), Xr_all, 0), axis=1)
        e = np.full(len(proj), np.inf)
        e[planos3d] = reproj(par, sx, Xr_all[planos3d], proj[planos3d])
        lim = max(4.0, 3.5 * 1.4826 * float(np.median(e[consist])))
        nuevo_c = planos3d & ((dlab < 0.008) | (ang < 1.5)) & (e < lim)
        ep = np.abs(epipolar(par, sx, o_r, Dr_all, proj))
        # paralaje = de verdad fuera de su superficie (a > 8 mm del punto 3D de la sala, o sin punto 3D)
        h_tri = ((Xt - mesa_c) @ mesa_n) * lado_cam
        fuera = np.where(etq == "TABLE", np.abs(h_tri) > 0.008, ~planos3d | (dlab > 0.008))
        par_m = ~nuevo_c & (ep < 3.0) & (ang >= 1.5) & (gap < 0.01) & fuera
        cambio = int((nuevo_c != consist).sum())
        consist, par_idx = nuevo_c, todos_idx[par_m]
        s.log(f"  iteración {it}: plano {consist.sum()} · paralaje {len(par_idx)} · atípicos {(~consist & ~par_m).sum()}"
              f" (cambiaron {cambio}); reproj mediana {np.median(e[consist]):.2f} px")
        h_ = {"iteracion": it, "plano": int(consist.sum()), "paralaje": int(len(par_idx)), "cambiaron": cambio,
              "reproj_px": stats(e[consist]), "f": float(par[0]), "C": centro_r(par)}
        if vista is not None:
            h_["por_vista"] = {str(v_): stats(e[consist & (vista == v_)]) for v_ in np.unique(vista) if (consist & (vista == v_)).any()}
        historial.append(h_)
        if cambio == 0 and it > 0:
            break
    Xin, pin = Xr_all[consist], proj[consist]
    if len(par_idx):
        Xt, _, _ = triangular(par, sx, o_r, Dr_all[par_idx], proj[par_idx])
        hmed = float(np.median(np.abs((Xt - mesa_c) @ mesa_n))) * 1000
        if len(par_idx) < 50 or hmed < 15:
            s.log(f"paralaje insuficiente ({len(par_idx)} puntos, |altura| mediana {hmed:.1f} mm): no lo uso")
            par_idx = np.zeros(0, int)
    s.log(f"puntos de paralaje (fuera del plano, coherentes con la epipolar): {len(par_idx)}")
    variantes = {}

    def correr(nombre, libres, con_par):
        a = dict(o_r=o_r, Dr=Dr_all[par_idx], pe=proj[par_idx]) if con_par and len(par_idx) else {}
        p0 = par.copy()
        if "cx" not in libres:
            p0[1] = W / 2
        if "k1" not in libres:
            p0[3] = 0.0
        pz, cost, sig = ajustar(p0, sx, Xin, pin, libres, prior=prior, **a)
        e3 = reproj(pz, sx, Xin, pin)
        ee = np.abs(epipolar(pz, sx, o_r, Dr_all[par_idx], proj[par_idx])) if len(par_idx) else np.zeros(0)
        variantes[nombre] = {"par": pz, "sig": sig, "libres": libres, "paralaje": bool(a), "e3": e3, "ee": ee,
                             "C": centro_r(pz), "prior_sigmas": (residuo_prior(pz, prior).tolist() if prior is not None else None)}
        C = centro_r(pz) * FZ
        s.log(f"  {nombre:<22} f={pz[0]:7.1f}±{sig.get('f', 0):5.1f} cx={pz[1]:7.1f}±{sig.get('cx', 0):5.1f} "
              f"cy={pz[2]:7.1f}±{sig.get('cy', 0):5.1f} k1={pz[3]:+.4f} C=({C[0]:+.3f},{C[1]:+.3f},{C[2]:+.3f})"
              f"±({','.join(f'{v:.0f}' for v in sig.get('C_mm', [0, 0, 0]))})mm "
              f"reproj med {np.median(e3):.2f} p90 {np.percentile(e3, 90):.2f} px"
              + (f" · epipolar med {np.median(ee):.2f} px" if len(ee) else ""))

    base = ["r0", "r1", "r2", "t0", "t1", "t2"]
    correr("A_cx_fijo", ["f", "cy"] + base, False)
    correr("B_libre", ["f", "cx", "cy"] + base, False)
    if len(par_idx) >= 50:
        correr("A_cx_fijo+paralaje", ["f", "cy"] + base, True)
        correr("B_libre+paralaje", ["f", "cx", "cy"] + base, True)
        correr("C_libre+paralaje+k1", ["f", "cx", "cy", "k1"] + base, True)
    else:
        correr("C_libre+k1", ["f", "cx", "cy", "k1"] + base, False)
    # ---- descartar variantes degeneradas (f a más de 15 % de la de cx fijo, o el proyector bajo la mesa)
    fA = variantes["B_libre" if prior is not None else "A_cx_fijo"]["par"][0]
    for k in list(variantes):
        vv = variantes[k]
        Cv = vv["C"]
        if abs(vv["par"][0] - fA) > 0.15 * fA or np.sign((Cv - mesa_c) @ mesa_n) != lado_cam:
            s.log(f"  descarto {k}: degenerada (f={vv['par'][0]:.0f} vs {fA:.0f} con cx fijo)")
            vv["descartada"] = True
    def bien_det(v):
        if v is None or v.get("descartada"):
            return False
        sg = v["sig"]
        return sg.get("cx", 1e9) < 25 and sg.get("cy", 1e9) < 25 and sg.get("f", 1e9) < 0.02 * v["par"][0]
    if no_coplanar:
        caso = "no_coplanar"
        elegido = "B_libre"
    elif prior is not None and len(par_idx) < 50:
        caso = "plano_con_prior"
        elegido = "B_libre"
    elif len(par_idx) >= 50 and bien_det(variantes.get("B_libre+paralaje")):
        caso = "plano_con_paralaje"
        elegido = "B_libre+paralaje"
    elif bien_det(variantes["B_libre"]):
        caso = "plano_oblicuo"
        elegido = "B_libre"
    else:
        caso = "plano_cx_fijo"
        elegido = "A_cx_fijo+paralaje" if "A_cx_fijo+paralaje" in variantes else "A_cx_fijo"
    kk = [k for k in variantes if k.startswith("C_") and not variantes[k].get("descartada")]
    if kk:
        vc, vb = variantes[kk[0]], variantes[elegido]
        k1, sk1 = vc["par"][3], vc["sig"].get("k1", 1e9)
        if caso not in ("plano_cx_fijo",) and abs(k1) > 5 * sk1 and np.median(vc["e3"]) < 0.9 * np.median(vb["e3"]):
            elegido = kk[0]
    v = variantes[elegido]
    par = v["par"]
    s.log(f"caso: {caso} → variante {elegido}")
    final = {"iteracion": "final", "variante": elegido, "reproj_px": stats(v["e3"]), "epipolar_px": stats(v["ee"]),
             "f": float(par[0]), "C": centro_r(par)}
    if vista is not None:
        e_all = reproj(par, sx, Xr_all[consist], proj[consist])
        final["por_vista"] = {str(v_): stats(e_all[vista[consist] == v_]) for v_ in np.unique(vista) if (vista[consist] == v_).any()}
    historial.append(final)
    return {"par": par, "sx": sx, "caso": caso, "elegido": elegido, "variantes": variantes, "v": v, "consist": consist,
            "par_idx": par_idx, "planitud": planitud, "pares_3d": int(len(p)), "inliers": int(len(pin)), "lado_cam": lado_cam,
            "historial": historial, "planos3d": planos3d, "o_r": o_r, "Dr_all": Dr_all, "proj": proj, "mesa_c": mesa_c,
            "mesa_n": mesa_n, "prior": prior}


def modelo_mundo(s: Sesion, R: dict, geo: dict, aruco_info=None, extra: dict | None = None) -> dict:
    """Modelo (par en el mundo con z invertida) → pose en la sala, chequeos y modelo.json."""
    par, sx, caso, elegido, variantes, v = R["par"], R["sx"], R["caso"], R["elegido"], R["variantes"], R["v"]
    planitud, par_idx = R["planitud"], R["par_idx"]
    # ---- pose en la sala (marco de la app)
    Rr = cv2.Rodrigues(par[4:7])[0]
    C_app = centro_r(par) * FZ
    eje_app = Rr[2] * FZ                 # dirección en que crece la profundidad del proyector
    u_app = Rr[0] * FZ * sx              # dirección de +u en la imagen
    piso_y, techo_y = geo.get("piso_y"), geo.get("techo_y")
    mesa_y = float(geo["mesa"]["c"][1]) if geo.get("mesa") else None
    inclin = math.degrees(math.acos(max(-1, min(1, -eje_app[1]))))
    chequeos = {
        "altura_sobre_piso_m": None if piso_y is None else round(float(C_app[1] - piso_y), 4),
        "altura_sobre_mesa_m": None if mesa_y is None else round(float(C_app[1] - mesa_y), 4),
        "bajo_techo_m": None if techo_y is None else round(float(techo_y - C_app[1]), 4),
        "inclinacion_desde_vertical_deg": round(inclin, 2),
        "mira_hacia_abajo": bool(eje_app[1] < 0),
        "sobre_la_mesa": None if mesa_y is None else bool(C_app[1] > mesa_y + 0.2),
        "imagen_espejada": bool(sx < 0),
    }
    s.log(f"proyector: C={np.round(C_app, 4).tolist()} m (sala); eje={np.round(eje_app, 3).tolist()}; {chequeos}")
    if chequeos["sobre_la_mesa"] is False or not chequeos["mira_hacia_abajo"]:
        s.log("OJO: la pose NO queda sobre la mesa mirando hacia abajo: convención o datos malos")
    modelo = {"caso": caso, "variante": elegido, "sx": sx, "par": par, "nombres": NOMBRES, "proyector": [W, H],
              "aruco_primera_vista": aruco_info,
              "K": [[sx * par[0], 0, par[1]], [0, par[0], par[2]], [0, 0, 1]], "k1": par[3],
              "R_diestro": Rr, "t_diestro": par[7:10], "P_app": matriz_P(np.r_[par[:3], 0, par[4:]], sx) @ np.diag([1, 1, -1, 1.0]),
              "C_app": C_app, "eje_app": eje_app, "u_app": u_app, "chequeos": chequeos, "planitud": planitud,
              "pares_3d": R["pares_3d"], "inliers": R["inliers"], "paralaje": int(len(par_idx)),
              "historial_residuo": R["historial"],
              "residuo_px": stats(v["e3"]), "residuo_epipolar_px": stats(v["ee"]), "sigmas": v["sig"],
              "variantes": {k: {"f": vv["par"][0], "cx": vv["par"][1], "cy": vv["par"][2], "k1": vv["par"][3],
                                "C_app": vv["C"] * FZ, "sig": vv["sig"], "reproj": stats(vv["e3"]),
                                "epipolar": stats(vv["ee"]), "paralaje": vv["paralaje"], "descartada": bool(vv.get("descartada"))}
                            for k, vv in variantes.items()}}
    if extra:
        modelo.update(extra)
    # alturas trianguladas de los puntos de paralaje (sobre la mesa)
    if len(par_idx) and "Dr_all" in R:
        Xt, gap, ang = triangular(par, sx, R["o_r"], R["Dr_all"][par_idx], R["proj"][par_idx])
        hs = ((Xt - R["mesa_c"]) @ R["mesa_n"]) * R["lado_cam"] * 1000
        modelo["alturas_paralaje_mm"] = {"n": int(len(hs)), "p10": round(float(np.percentile(hs, 10)), 1),
                                         "mediana": round(float(np.median(hs)), 1), "p90": round(float(np.percentile(hs, 90)), 1)}
        s.log(f"alturas trianguladas de los puntos de paralaje (mm sobre la mesa): {modelo['alturas_paralaje_mm']}")
    if s.p("verdad_sintetica.json").exists():
        vd = s.jr("verdad_sintetica.json")
        Cv = np.array(vd["C_app"])
        cmp = {"df": par[0] - vd["f"], "dcx": par[1] - vd["cx"], "dcy": par[2] - vd["cy"],
               "dC_mm": float(np.linalg.norm(C_app - Cv) * 1000), "dR_deg": rot_deg(Rr, np.array(vd["R"])), "sx_ok": sx == vd["sx"]}
        modelo["vs_verdad"] = cmp
        s.log(f"vs verdad sintética: {json.dumps({k: round(float(v), 3) if not isinstance(v, bool) else v for k, v in cmp.items()})}")
    s.jw("modelo.json", modelo)
    return modelo


# ============================================================================ bundle: pose fija proyector↔Quest

FY = np.diag([1.0, -1.0, 1.0])          # cámara de la app (y arriba) ↔ OpenCV (y abajo)
FIJA = CAPTURES / "quest-cal-fija"
CHECKER = CAPTURES / "quest-cal-checker/checker_corr.json"


def a_quest(pose: dict):
    """Matriz A y origen o: X_q (cámara del Quest, OpenCV diestro) = A·(X_mundo − o)."""
    M = np.array(pose["m4"], float).reshape(4, 4)
    return FY @ M[:3, :3].T, M[:3, 3]


def sesiones_reales() -> list[Path]:
    out = []
    for d in sorted(CAPTURES.glob("quest-cal-2*")):
        if d.name.endswith("-dry") or not (d / "puntos3d.npz").exists() or not (d / "cam_pose.json").exists():
            continue
        out.append(d)
    return out


def vistas_acumuladas(s: Sesion) -> dict:
    """Todas las observaciones (gray code y ArUco de cada sesión, damero) en el marco de la cámara del Quest."""
    Xs, Ds, Ps, Es, Vs = [], [], [], [], []
    ses = sesiones_reales()
    if not ses:
        raise RuntimeError("no hay sesiones reales con puntos3d.npz (corre captura + calcular)")
    ultima = Sesion(ses[-1])
    pose_u = ultima.jr("cam_pose.json")
    A_u, o_u = a_quest(pose_u)
    geo_u = ultima.jr("geometria.json")
    mesa_c = A_u @ (np.array(geo_u["mesa"]["c"]) - o_u)
    mesa_n = A_u @ np.array(geo_u["mesa"]["n"])
    resumen = []
    for d in ses:
        ss = Sesion(d)
        pose = ss.jr("cam_pose.json")
        A, o = a_quest(pose)
        g = ss.jr("geometria.json")
        mc = A @ (np.array(g["mesa"]["c"]) - o)
        dmesa = float(abs((mc - mesa_c) @ mesa_n) * 1000)
        for nombre, f in (("grises", "puntos3d.npz"), ("aruco", "aruco3d.npz")):
            if not ss.p(f).exists():
                continue
            z = np.load(ss.p(f))
            n = len(z["proj"])
            Xs.append((z["X"] - o) @ A.T); Ds.append(z["D"] @ A.T); Ps.append(z["proj"]); Es.append(z["etiqueta"])
            Vs.append(np.full(n, f"{nombre}:{d.name}"))
            resumen.append({"vista": f"{nombre}:{d.name}", "pares": int(n), "mesa_vs_ultima_mm": round(dmesa, 2),
                            "pose_camara": pose.get("fuente"), "dispersion_mm": pose.get("dispersion_mm")})
    if CHECKER.exists():
        cj = json.loads(CHECKER.read_text())
        if tuple(cj.get("ventana", [0, 0])) == (W, H):
            cuv = np.array(cj["internas_camara"], float)
            pj = np.array(cj["internas_contenido"], float) - 0.5          # esquinas en bordes → centros enteros
            # el damero es simétrico (180°): verificar el orden de las esquinas contra la homografía del gray code
            z2 = np.load(ultima.p("pares_2d.npz"))
            Hg, _ = cv2.findHomography(z2["proj"], z2["cam"], cv2.RANSAC, 3.0)
            pred = cv2.perspectiveTransform(pj.reshape(-1, 1, 2), Hg).reshape(-1, 2)
            from scipy.spatial import cKDTree
            dnn, inn = cKDTree(cuv).query(pred)
            e_dir = float(np.median(np.linalg.norm(pred - cuv, axis=1)))
            e_inv = float(np.median(dnn))
            reordenado = not np.array_equal(inn, np.arange(len(inn)))
            s.log(f"  damero vs gray code: en su orden {e_dir:.1f} px; emparejando por vecino más cercano {e_inv:.2f} px"
                  f"{' (el orden de las esquinas de checker_corr.json venía permutado: lo corrijo)' if reordenado else ''}")
            if len(set(inn.tolist())) == len(inn) and e_inv < 3:
                cuv = cuv[inn]
            else:
                s.log("  damero inconsistente con el gray code: no lo uso")
                cuv = None
        else:
            cuv = None
        if cuv is not None:
            cam = Cam(pose_u, 1728)
            malla = Malla(ultima.jr("malla.json"))
            planos = [plano_de_ancla(a) for a in malla.anclas if a.get("plano")]
            g = rayos_a_sala(cam, malla, planos, cuv)
            Xs.append((g["X"] - o_u) @ A_u.T); Ds.append(g["D"] @ A_u.T); Ps.append(pj); Es.append(g["etiqueta"])
            Vs.append(np.full(len(pj), "damero"))
            resumen.append({"vista": "damero_orden", "reordenado": bool(reordenado), "e_vs_grises_px": e_inv})
            resumen.append({"vista": "damero", "pares": int(len(pj)), "fecha": cj.get("fecha"),
                            "etiquetas": {str(e): int((g["etiqueta"] == e).sum()) for e in np.unique(g["etiqueta"])}})
    return {"X": np.vstack(Xs), "D": np.vstack(Ds), "proj": np.vstack(Ps), "etq": np.concatenate(Es), "vista": np.concatenate(Vs),
            "mesa_c": mesa_c, "mesa_n": mesa_n, "ultima": ultima, "resumen": resumen}


def par_a_mundo(par_q: np.ndarray, pose: dict) -> np.ndarray:
    """Modelo en el marco del Quest → modelo en el mundo con z invertida (el de Modelo/exportar)."""
    A, o = a_quest(pose)
    G = A @ np.diag(FZ)                   # X_q = G·X_r − A·o
    g = -A @ o
    Rq = cv2.Rodrigues(par_q[4:7])[0]
    Rw = Rq @ G
    tw = Rq @ g + par_q[7:10]
    return np.r_[par_q[:4], cv2.Rodrigues(Rw)[0].ravel(), tw]


def pose_actual(s: Sesion, respaldo: dict) -> dict:
    try:
        return leer_pose_camara(s, n=4)
    except Exception as e:  # noqa: BLE001
        s.log(f"sin pose en vivo del Quest ({e}): uso la de la última sesión")
        return respaldo


def materializar_fija(s: Sesion, B: dict, pose: dict, ultima: Sesion) -> dict:
    """Escribe en la carpeta fija el modelo en el mundo actual (pose de la cámara de ahora) y lo que exportar/lazo usan."""
    import shutil
    par_q, sx = np.array(B["par_q"], float), float(B["sx"])
    par_w = par_a_mundo(par_q, pose)
    A_u, o_u = a_quest(ultima.jr("cam_pose.json"))
    A_n, o_n = a_quest(pose)
    # geometría (mesa) y pares 3D de la última sesión llevados al mundo de ahora vía el marco del Quest
    geo = ultima.jr("geometria.json")
    mundo = lambda X: (np.asarray(X, float) - o_u) @ A_u.T @ A_n + o_n      # noqa: E731
    dirm = lambda d: np.asarray(d, float) @ A_u.T @ A_n                     # noqa: E731
    if geo.get("mesa"):
        m = geo["mesa"]
        geo["mesa"] = {**m, "c": mundo(np.array(m["c"])[None])[0], "n": dirm(m["n"]), "e1": dirm(m["e1"]), "e2": dirm(m["e2"])}
    s.jw("geometria.json", geo)
    z = dict(np.load(ultima.p("puntos3d.npz")))
    z["X"] = mundo(z["X"]); z["D"] = dirm(z["D"]); z["o"] = o_n
    np.savez_compressed(s.p("puntos3d.npz"), **z)
    s.jw("cam_pose.json", pose)
    for f in ("captura.json", "malla.json", "lazo.json", "lazo_corr.npz", "pixel.json"):
        if ultima.p(f).exists():
            shutil.copy(ultima.p(f), s.p(f))
    R = {"par": par_w, "sx": sx, "caso": B["caso"], "elegido": B["variante"], "variantes": {},
         "v": {"e3": np.array(B["residuos_e3"]), "ee": np.array(B["residuos_ee"]), "sig": B["sigmas"]},
         "planitud": B["planitud"], "par_idx": np.zeros(0, int), "pares_3d": B["pares_3d"], "inliers": B["inliers"],
         "historial": B["historial_residuo"]}
    extra = {"pose_fija": True, "incertidumbre": B["incertidumbre"], "bundle": {k: B[k] for k in ("fecha", "vistas", "pose_quest")},
             "paralaje": B["paralaje"]}
    return modelo_mundo(s, R, geo, B.get("aruco_primera_vista"), extra)


def paso_bundle(s: Sesion, push: bool = False) -> dict:
    """Ajuste conjunto de TODAS las observaciones acumuladas: K del proyector + pose fija respecto del Quest (+ k1),
    Huber, covarianza; escribe la calibración fija (pose_fija: true)."""
    global PERDIDA
    s.log("== bundle: pose fija proyector↔Quest con todas las vistas acumuladas")
    V = vistas_acumuladas(s)
    for r in V["resumen"]:
        if "pares" not in r:
            continue
        s.log(f"  vista {r['vista']}: {r['pares']} pares" + (f" (mesa vs última {r['mesa_vs_ultima_mm']} mm)" if "mesa_vs_ultima_mm" in r else "")
              + (f" {r.get('etiquetas')}" if r.get("etiquetas") else ""))
    o_q = np.zeros(3)
    lado_cam = np.sign((o_q - V["mesa_c"]) @ V["mesa_n"])
    # best guess: Quest ~20 cm a la izquierda (visto por Max) y ~10 cm más abajo que el proyector → el proyector cerca del
    # Quest; se usa solo para elegir entre soluciones equivalentes y se informa la diferencia
    inits = []
    A_u, _ = a_quest(V["ultima"].jr("cam_pose.json"))
    prior = hacer_prior(o_q, A_u @ np.array([0.0, 1.0, 0.0]))
    va = np.char.startswith(V["vista"].astype(str), "aruco") | (V["vista"] == "damero")
    okA = va & np.isin(V["etq"], PLANOS) & np.isfinite(V["X"]).all(1)
    if okA.sum() >= 12:
        r = init_aruco(s, V["X"][okA], V["proj"][okA], V["mesa_c"], V["mesa_n"], lado_cam, prior)
        if r is not None:
            inits.append(r[:2])
            s.log(f"init con ArUco+damero ({okA.sum()} esquinas): f={r[0][0]:.0f} C_quest={np.round(r[2]['C'], 3).tolist()} m "
                  f"reproj mediana {r[2]['reproj_mediana_px']:.2f} px")
    PERDIDA = "huber"
    try:
        R = resolver_nucleo(s, V["X"], V["D"], o_q, V["proj"], V["etq"], V["mesa_c"], V["mesa_n"], inits, vista=V["vista"],
                            prior=prior)
    finally:
        PERDIDA = "soft_l1"
    par_q, sx, v = R["par"], R["sx"], R["v"]
    sig = v["sig"]
    Cq = centro_r(par_q)
    Rq = cv2.Rodrigues(par_q[4:7])[0]
    sig_rot = math.degrees(math.sqrt(sum(sig.get(k, 0.0) ** 2 for k in ("r0", "r1", "r2"))))
    sig_C = sig.get("C_mm", [float("nan")] * 3)
    eje_q = Rq[2]
    # posición del proyector respecto del Quest en palabras: Quest (OpenCV): x derecha, y abajo, z adelante de la cámara
    # sistemática: dispersión entre variantes de modelo razonables (con/sin k1, cx fijo) respecto de la elegida
    otras = [vv for k, vv in R["variantes"].items() if k != R["elegido"] and not vv.get("descartada") and vv.get("paralaje", True)]
    dC = [float(np.linalg.norm(vv["C"] - Cq) * 1000) for vv in otras]
    dR = [rot_deg(cv2.Rodrigues(vv["par"][4:7])[0], Rq) for vv in otras]
    inc = {"sigma_traslacion_mm": {"x": sig_C[0], "y": sig_C[1], "z": sig_C[2],
                                   "norma": float(math.sqrt(sum(v_ ** 2 for v_ in sig_C)))},
           "sistematica_entre_modelos": {"traslacion_mm_max": max(dC) if dC else None, "rotacion_deg_max": max(dR) if dR else None,
                                         "f_px_max": max(abs(vv["par"][0] - par_q[0]) for vv in otras) if otras else None,
                                         "nota": "diferencia máxima entre la variante elegida y las otras (con k1, cx fijo): "
                                                 "cota práctica del error de modelo"},
           "sigma_rotacion_deg": sig_rot, "sigma_f_px": sig.get("f"), "sigma_cx_px": sig.get("cx"), "sigma_cy_px": sig.get("cy"),
           "nota": "1σ de la covarianza del ajuste (Huber, J^T J); no incluye el error de la pose del casco ni del plano MRUK"}
    B = {"fecha": dt.datetime.now().isoformat(timespec="seconds"), "par_q": par_q, "sx": sx, "caso": R["caso"],
         "variante": R["elegido"], "K": [[sx * par_q[0], 0, par_q[1]], [0, par_q[0], par_q[2]], [0, 0, 1]], "k1": par_q[3],
         "proyector": [W, H], "pose_quest": {"marco": "cámara izquierda del Quest (OpenCV: x der, y abajo, z adelante)",
                                              "R_proj_desde_quest": Rq, "t_proj_desde_quest": par_q[7:10],
                                              "centro_proyector_en_quest_m": Cq, "eje_optico_en_quest": eje_q,
                                              "distancia_quest_proyector_m": float(np.linalg.norm(Cq))},
         "mesa_quest": {"c": V["mesa_c"], "n": V["mesa_n"]}, "incertidumbre": inc, "sigmas": sig,
         "historial_residuo": R["historial"], "vistas": V["resumen"], "planitud": R["planitud"], "pares_3d": R["pares_3d"],
         "inliers": R["inliers"], "paralaje": int(len(R["par_idx"])), "residuos_e3": v["e3"], "residuos_ee": v["ee"],
         "prior": (None if prior is None else {"altura_sobre_quest_m": [prior["dh"], prior["sh"]],
                                                "lateral_m": [prior["dl"], prior["sl"]],
                                                "residuo_sigmas": residuo_prior(par_q, prior).tolist(),
                                                "nota": "best guess de Max; solo pesa donde los datos son degenerados (todo en la mesa)"}),
         "variantes": {k: {"f": vv["par"][0], "cx": vv["par"][1], "cy": vv["par"][2], "k1": vv["par"][3], "C_quest": vv["C"],
                           "sig": vv["sig"], "reproj": stats(vv["e3"]), "descartada": bool(vv.get("descartada"))}
                       for k, vv in R["variantes"].items()}}
    s.jw("bundle.json", B)
    s.log(f"pose fija: proyector en el marco del Quest C={np.round(Cq, 4).tolist()} m (|C|={np.linalg.norm(Cq) * 100:.1f} cm), "
          f"σ traslación {np.round(sig_C, 1).tolist()} mm, σ rotación {sig_rot:.3f}°; f={par_q[0]:.1f}±{sig.get('f', 0):.1f} "
          f"cx={par_q[1]:.1f} cy={par_q[2]:.1f} k1={par_q[3]:+.4f}")
    pose = pose_actual(s, V["ultima"].jr("cam_pose.json"))
    materializar_fija(s, B, pose, V["ultima"])
    out = paso_exportar(s, push=push)
    return out


def paso_verificar(s: Sesion, umbral_px: float = 3.0, umbral_mm: float = 2.0) -> dict:
    """Mide el residuo actual de la calibración fija proyectando 20 puntos (5x4) sobre la mesa, sin recalibrar."""
    s.log("== verificar (sin recalibrar)")
    B = s.jr("bundle.json")
    fijar_proyector(*B["proyector"])
    ultima = Sesion(sesiones_reales()[-1])
    pose = pose_actual(s, ultima.jr("cam_pose.json"))
    materializar_fija(s, B, pose, ultima)
    lz = paso_lazo(s, iters=1, grilla=(5, 4), guardar=False)
    h = lz["historial"][0]
    med_px = h["px_proyector"].get("mediana", float("inf"))
    med_mm = h["mm_mesa"].get("mediana", float("inf"))
    ok = med_px <= umbral_px and med_mm <= umbral_mm
    reg = {"fecha": dt.datetime.now().isoformat(timespec="seconds"), "medidos": h["medidos"], "objetivos": h["objetivos"],
           "px_proyector": h["px_proyector"], "mm_mesa": h["mm_mesa"], "umbral_px": umbral_px, "umbral_mm": umbral_mm,
           "ok": bool(ok), "accion": "nada" if ok else "re-estimar: alguien movió el rig o cambió la sala → captura + bundle"}
    with open(s.p("verificaciones.jsonl"), "a") as f:
        f.write(json.dumps(reg, ensure_ascii=False, default=_js) + "\n")
    s.log(f"verificación: mediana {med_px} px del proyector / {med_mm} mm → {'OK' if ok else 'RESIDUO ALTO: ' + reg['accion']}")
    return reg


# ============================================================================ modelo cargado + utilidades de plano

class Modelo:
    def __init__(self, s: Sesion):
        m = s.jr("modelo.json")
        self.m = m
        self.sx = float(m["sx"])
        self.par = np.array(m["par"], float)
        self.P_app = np.array(m["P_app"], float)
        geo = s.jr("geometria.json")
        g = geo["mesa"]
        self.mesa = {k: np.array(v, float) if isinstance(v, list) else v for k, v in g.items()}

    def a_proyector(self, Xapp: np.ndarray, k1: bool = True) -> np.ndarray:
        par = self.par if k1 else np.r_[self.par[:3], 0.0, self.par[4:]]
        uv, z = proyectar_modelo(par, np.asarray(Xapp, float).reshape(-1, 3) * FZ, self.sx)
        uv[z <= 0] = np.nan
        return uv

    def a_mesa(self, uv: np.ndarray, k1: bool = True, h: float = 0.0) -> np.ndarray:
        """Píxeles del proyector → puntos 3D (app) en el plano de la mesa (+h m sobre ella)."""
        uv = np.asarray(uv, float).reshape(-1, 2)
        par = self.par
        f, cx, cy, kk = par[0], par[1], par[2], (par[3] if k1 else 0.0)
        x = (uv[:, 0] - cx) / (self.sx * f); y = (uv[:, 1] - cy) / f
        xd, yd = x.copy(), y.copy()
        for _ in range(15):
            r2 = x * x + y * y
            x, y = xd / (1 + kk * r2), yd / (1 + kk * r2)
        R = cv2.Rodrigues(par[4:7])[0]
        Dr = np.stack([x, y, np.ones_like(x)], 1) @ R
        Cr = centro_r(par)
        n, c = self.mesa["n"] * FZ, self.mesa["c"] * FZ + self.mesa["n"] * FZ * h
        t = ((c - Cr) @ n) / (Dr @ n)
        return (Cr + t[:, None] * Dr) * FZ


def interseca_mesa(cam: Cam, uv: np.ndarray, mesa: dict, h: float = 0.0) -> np.ndarray:
    D = cam.rayos(uv)
    c = mesa["c"] + mesa["n"] * h
    t = ((c - cam.o) @ mesa["n"]) / (D @ mesa["n"])
    return cam.o + t[:, None] * D


def mesa_mm(X: np.ndarray, mesa: dict) -> np.ndarray:
    d = np.asarray(X, float).reshape(-1, 3) - mesa["c"]
    return np.stack([d @ mesa["e1"], d @ mesa["e2"]], 1) * 1000


# ============================================================================ 5. Pixel 9 (referencia métrica)

def geo_pixel(s: Sesion) -> dict | None:
    logs = sorted((REPO / "quest/logs").glob("casco-*.jsonl"))
    ult = None
    for f in logs[-3:]:
        for line in f.read_text().splitlines():
            try:
                e = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if e.get("origen") == "pixel-charuco" and isinstance(e.get("datos"), dict):
                ult = e
    if ult:
        s.log(f"geometría del tablero del Pixel (de quest/logs, {ult.get('_srv')}): {json.dumps({k: ult['datos'].get(k) for k in ('modo', 'marcador_mm', 'cuadro_mm', 'separacion_mm', 'dpr', 'canvas_px')})}")
        return ult["datos"]
    return None


def detectar_pixel(img: np.ndarray, geo: dict):
    dic = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    prm = cv2.aruco.DetectorParameters()
    prm.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    prm.adaptiveThreshWinSizeMin, prm.adaptiveThreshWinSizeMax, prm.adaptiveThreshWinSizeStep = 3, 53, 4
    prm.minMarkerPerimeterRate = 0.01
    det = cv2.aruco.ArucoDetector(dic, prm)
    corners, ids, _ = det.detectMarkers(img)
    if ids is None:
        return None
    ids = ids.ravel()
    if geo.get("modo", "grilla") == "grilla":
        mm, sep = float(geo.get("marcador_mm", 24)) / 1000, float(geo.get("separacion_mm", 5)) / 1000
        obj, imgp, usados = [], [], []
        for c, i in zip(corners, ids):
            if not 40 <= i <= 47:
                continue
            k = i - 40; r, cc = divmod(k, 2)
            x0, y0 = cc * (mm + sep), r * (mm + sep)
            obj += [[x0, y0, 0], [x0 + mm, y0, 0], [x0 + mm, y0 + mm, 0], [x0, y0 + mm, 0]]
            imgp += c.reshape(4, 2).tolist(); usados.append(int(i))
        if not usados:
            return None
        bw, bh = 2 * mm + sep, 4 * mm + 3 * sep
        obj, imgp = np.array(obj), np.array(imgp)
        # centros de marcador (promedio de sus 4 esquinas): el sesgo hacia adentro de las esquinas se cancela
        return {"obj": obj, "img": imgp, "ids": usados, "tablero_m": [bw, bh], "corners": corners, "all_ids": ids,
                "obj_c": obj.reshape(-1, 4, 3).mean(1), "img_c": imgp.reshape(-1, 4, 2).mean(1)}
    sq, mk = float(geo.get("cuadro_mm", 15)) / 1000, float(geo.get("marcador_mm", 11)) / 1000
    board = cv2.aruco.CharucoBoard((4, 7), sq, mk, dic, np.arange(24, 38))
    cd = cv2.aruco.CharucoDetector(board)
    ch_c, ch_ids, _, _ = cd.detectBoard(img)
    if ch_ids is None or len(ch_ids) < 4:
        return None
    objp = board.getChessboardCorners()[ch_ids.ravel()]
    return {"obj": objp, "img": ch_c.reshape(-1, 2), "ids": ch_ids.ravel().tolist(), "tablero_m": [4 * sq, 7 * sq],
            "corners": corners, "all_ids": ids, "obj_c": objp, "img_c": ch_c.reshape(-1, 2)}


def imagen_tablero(geo: dict, pxmm: float = 20.0) -> np.ndarray:
    """Tablero de la página charuco.html (modo grilla) en px a pxmm px/mm, blanco con 4 mm de margen."""
    dic = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    mm, sep = float(geo.get("marcador_mm", 24)), float(geo.get("separacion_mm", 5))
    bw, bh = 2 * mm + sep, 4 * mm + 3 * sep
    img = np.full((int(round((bh + 8) * pxmm)), int(round((bw + 8) * pxmm))), 255, np.uint8)
    lado = int(round(mm * pxmm))
    for r in range(4):
        for c in range(2):
            x0, y0 = int(round((4 + c * (mm + sep)) * pxmm)), int(round((4 + r * (mm + sep)) * pxmm))
            img[y0:y0 + lado, x0:x0 + lado] = dic.generateImageMarker(40 + r * 2 + c, lado)
    return img


def render_pixel_sim(sim: "Sim", geo: dict, base: np.ndarray, espesor_mm: float) -> tuple[np.ndarray, np.ndarray]:
    """Pega el tablero (como lo dibuja el Pixel, plano sobre la mesa) en un cuadro sintético. Devuelve (img, esquinas3d app)."""
    pxmm = 20.0
    tab = imagen_tablero(geo, pxmm)
    m = sim.mesa
    n_r = m["n"] * FZ
    e1_r = m["e1"] * FZ
    e2_r = np.cross(e1_r, n_r)                     # x der, y abajo, z hacia la mesa (diestro)
    X0 = interseca_mesa(sim.cam, np.array([[700.0, 1250.0]]), m, h=espesor_mm / 1000)[0] * FZ
    hpx, wpx = tab.shape
    src = np.array([[0, 0], [wpx, 0], [wpx, hpx], [0, hpx]], float)
    X = np.array([(X0 + e1_r * (u / pxmm / 1000) + e2_r * (v / pxmm / 1000)) * FZ for u, v in src])
    dst = sim.cam.proyectar(X)
    Hm = cv2.getPerspectiveTransform(src.astype(np.float32), dst.astype(np.float32))
    w = cv2.warpPerspective(tab, Hm, (base.shape[1], base.shape[0]), flags=cv2.INTER_AREA, borderValue=0)
    msk = cv2.warpPerspective(np.full_like(tab, 255), Hm, (base.shape[1], base.shape[0]), borderValue=0)
    out = np.where(msk > 128, (0.85 * w + 25).astype(np.uint8), base)
    mm, sep = float(geo.get("marcador_mm", 24)), float(geo.get("separacion_mm", 5))
    bw, bh = (2 * mm + sep) / 1000, (4 * mm + 3 * sep) / 1000
    o = X0 + (e1_r + e2_r) * 0.004
    esq = np.array([(o + e1_r * a + e2_r * b) * FZ for a, b in ((0, 0), (bw, 0), (bw, bh), (0, bh))])
    return out, esq


def analizar_pixel(s: Sesion, img: np.ndarray, geo: dict, pose: dict, espesor_mm: float) -> dict | None:
    c = Cam(pose, img.shape[1])
    r = detectar_pixel(img, geo)
    if r is None:
        s.log("no encuentro el tablero del Pixel en el cuadro (¿página abierta, pantalla encendida, a la vista del Quest?)")
        s.jw("pixel.json", {"detectado": False, "geo": geo})
        return None
    obj, imgp = r["obj"], c.ideal(r["img"])
    K = c.K_cv()
    ok, rv, tv = cv2.solvePnP(obj, imgp, K, None, flags=cv2.SOLVEPNP_IPPE)
    rv, tv = cv2.solvePnPRefineLM(obj, imgp, K, None, rv, tv)
    pr, _ = cv2.projectPoints(obj, rv, tv, K, None)
    e_px = np.linalg.norm(pr.reshape(-1, 2) - imgp, axis=1)
    Rb = cv2.Rodrigues(rv)[0]
    FY = np.diag([1.0, -1.0, 1.0])
    to_w = lambda Xcv: (Xcv @ FY) @ c.R.T + c.o   # noqa: E731  (OpenCV y abajo → cámara de la app → mundo)
    obj_cam = obj @ Rb.T + tv.ravel()
    Xw = to_w(obj_cam)
    bw, bh = r["tablero_m"]
    esq_w = to_w(np.array([[0, 0, 0], [bw, 0, 0], [bw, bh, 0], [0, bh, 0]], float) @ Rb.T + tv.ravel())
    g = s.jr("geometria.json")
    mesa = {k: np.array(v, float) if isinstance(v, list) else v for k, v in g["mesa"].items()}
    h_pnp = (Xw - mesa["c"]) @ mesa["n"] * 1000
    normal_b = ((Rb @ np.array([0, 0, 1.0])) @ FY) @ c.R.T
    incl = math.degrees(math.acos(min(1.0, abs(float(normal_b @ mesa["n"])))))
    # métrica con los centros de marcador (sin sesgo): rayo ∩ (mesa + espesor) vs geometría real del tablero
    oc, icn = r["obj_c"], r["img_c"]
    Xray = interseca_mesa(c, icn, mesa, h=espesor_mm / 1000)
    Xc_pnp = to_w(oc @ Rb.T + tv.ravel())
    d_mm = np.linalg.norm(mesa_mm(Xray, mesa) - mesa_mm(Xc_pnp, mesa), axis=1)
    n = len(obj)
    ii, jj = np.triu_indices(len(oc), 1)
    real = np.linalg.norm(oc[ii] - oc[jj], axis=1) * 1000
    med = np.linalg.norm(Xray[ii] - Xray[jj], axis=1) * 1000
    far = real > 0.5 * real.max()
    escala = float(np.sum(med[far] * real[far]) / np.sum(real[far] ** 2))
    err_dist = med[far] - real[far]
    # ajuste rígido 2D (en el plano de la mesa) del tablero sobre los puntos rayo∩plano: residuo = error de forma (mm)
    A2 = oc[:, :2] * 1000
    B2 = mesa_mm(Xray, mesa)
    ca, cb = A2.mean(0), B2.mean(0)
    U, _, Vt = np.linalg.svd((A2 - ca).T @ (B2 - cb))
    Rr2 = (U @ Vt).T
    if np.linalg.det(Rr2) < 0:
        Rr2 = (U @ np.diag([1, -1]) @ Vt).T
    forma = np.linalg.norm((A2 - ca) @ Rr2.T + cb - B2, axis=1)
    dist_pnp = float(np.linalg.norm((oc @ Rb.T + tv.ravel()).mean(0)))
    dist_ray = float(np.linalg.norm(Xray.mean(0) - c.o))
    out = {"detectado": True, "geo": {k: v for k, v in geo.items() if k != "ua"}, "ids": r["ids"], "esquinas": int(n),
           "pnp_reproj_px": stats(e_px), "altura_pantalla_sobre_mesa_mm_pnp": stats(h_pnp),
           "altura_mediana_mm": float(np.median(h_pnp)), "espesor_supuesto_mm": espesor_mm, "inclinacion_vs_mesa_deg": incl,
           "posicion_rayo_vs_pnp_mm": stats(d_mm), "escala_rayo_plano_vs_real": escala,
           "error_escala_pct": (escala - 1) * 100, "error_distancias_mm": stats(err_dist), "error_forma_rigida_mm": stats(forma),
           "distancia_camara_pnp_m": dist_pnp, "distancia_camara_rayo_m": dist_ray,
           "nota": "escala y forma con centros de marcador (rayo∩mesa+espesor, pose del casco + MRUK); la profundidad del PnP"
                   " de un tablero de ~6x11 cm a ~1 m tiene ±1 % (≈1 cm) de incertidumbre",
           "esquinas_tablero_app": esq_w, "esquinas_detectadas_app": Xw, "esquinas_img": r["img"]}
    s.jw("pixel.json", out)
    vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    cv2.aruco.drawDetectedMarkers(vis, r["corners"], r["all_ids"].reshape(-1, 1))
    for (u, v) in r["img"]:
        cv2.circle(vis, (int(u), int(v)), 2, (0, 255, 0), -1)
    x0, y0 = np.min(r["img"], 0).astype(int) - 60
    x1, y1 = np.max(r["img"], 0).astype(int) + 60
    crop = vis[max(0, y0):y1, max(0, x0):x1]
    cv2.imwrite(str(s.p("pixel_debug.jpg")), cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST) if crop.size else vis)
    s.log(f"Pixel: {n} esquinas (ids {r['ids']}); PnP reproj {stats(e_px)['mediana']} px; pantalla a {np.median(h_pnp):.1f} mm "
          f"sobre la mesa MRUK (esperado ≈ {espesor_mm} mm); inclinación vs mesa {incl:.1f}°; escala rayo∩plano/real = "
          f"{escala:.4f} ({(escala - 1) * 100:+.2f}%), forma {stats(forma)['rms']} mm rms; posición rayo∩plano vs PnP {stats(d_mm)['mediana']} mm; "
          f"distancia cámara {dist_pnp:.3f} m (PnP) vs {dist_ray:.3f} m (rayo∩plano)")
    return out


def paso_pixel(s: Sesion, cuadros: int = 6, espesor_mm: float = PIXEL_ESPESOR_MM) -> dict | None:
    s.log("== Pixel 9: referencia métrica")
    pose = s.jr("cam_pose.json")
    if s.dry:
        geo = {"modo": "grilla", "marcador_mm": 24, "separacion_mm": 5}
        sim = sim_para(s)
        sim.actual = np.zeros((H, W, 3), np.uint8)
        base = sim.render()
        img, esq_v = render_pixel_sim(sim, geo, base, espesor_mm)
        cv2.imwrite(str(s.p("pixel_cuadro.png")), img)
        out = analizar_pixel(s, img, geo, pose, espesor_mm)
        if out:
            err = np.linalg.norm(np.array(out["esquinas_tablero_app"]) - esq_v, axis=1) * 1000
            s.log(f"Pixel sintético: esquinas del tablero vs verdad {stats(err)} mm")
        return out
    geo = geo_pixel(s) or {"modo": "grilla", "marcador_mm": 24, "separacion_mm": 5}
    cam = CamaraQuest(s); cam.elegir_fuente()
    proy = Proyector(s)
    cap = Capturador(s, cam, proy, promedio=cuadros)
    t0 = cap.mostrar(imagen_patron({"tipo": "negro"}), "negro")       # sin luz del proyector sobre la pantalla del Pixel
    f, _ = cap.esperar_cuadro(t0)
    img = np.clip(np.round(f), 0, 255).astype(np.uint8)
    cv2.imwrite(str(s.p("pixel_cuadro.png")), img)
    return analizar_pixel(s, img, geo, pose, espesor_mm)


# ============================================================================ 6. lazo cerrado

def puntos_blob(diff: np.ndarray, min_a: int = 6, max_a: int = 3000):
    d = cv2.GaussianBlur(diff, (0, 0), 1.2)
    thr = max(10.0, 0.35 * float(np.percentile(d, 99.8)))
    m = (d > thr).astype(np.uint8)
    n, lab, st, cen = cv2.connectedComponentsWithStats(m)
    out = []
    for i in range(1, n):
        a = st[i, cv2.CC_STAT_AREA]
        if min_a <= a <= max_a:
            ys, xs = np.nonzero(lab[st[i, 1]:st[i, 1] + st[i, 3], st[i, 0]:st[i, 0] + st[i, 2]] == i)
            w = d[st[i, 1] + ys, st[i, 0] + xs]
            out.append([st[i, 0] + np.average(xs, weights=w), st[i, 1] + np.average(ys, weights=w)])
    return np.array(out, float).reshape(-1, 2)


def componer_corr(corr, P_int, e, sigma=25.0):
    """Remap residual en el espacio del proyector (como superficie_feed.calibrar_lazo): lo previsto en P_int que aterriza
    en P_int + e se dibuja en P_int - e. final(x, y) = img(mapx, mapy)."""
    from scipy.interpolate import griddata
    Pp = P_int - e
    gx, gy = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
    dx = griddata(Pp, e[:, 0], (gx, gy), method="linear"); dy = griddata(Pp, e[:, 1], (gx, gy), method="linear")
    dxn = griddata(Pp, e[:, 0], (gx, gy), method="nearest"); dyn = griddata(Pp, e[:, 1], (gx, gy), method="nearest")
    dx = np.where(np.isnan(dx), dxn, dx).astype(np.float32); dy = np.where(np.isnan(dy), dyn, dy).astype(np.float32)
    dx = cv2.GaussianBlur(dx, (0, 0), sigma); dy = cv2.GaussianBlur(dy, (0, 0), sigma)
    if corr is None:
        return gx + dx, gy + dy
    pmx, pmy = corr
    return (cv2.remap(pmx, gx + dx, gy + dy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE),
            cv2.remap(pmy, gx + dx, gy + dy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE))


def paso_lazo(s: Sesion, iters: int = 3, radio: int = 7, grilla: tuple = (9, 6), guardar: bool = True) -> dict:
    s.log(f"== lazo cerrado ({iters} iteraciones)")
    mod = Modelo(s)
    pose = s.jr("cam_pose.json")
    sim = sim_para(s) if s.dry else None
    camq = CamaraQuest(s, sim); camq.elegir_fuente()
    proy = Proyector(s, sim)
    cap = Capturador(s, camq, proy, promedio=2)
    if s.p("captura.json").exists():
        cap.lat = float(s.jr("captura.json").get("latencia_s") or 1.5)
    f0, _ = camq.cuadro()
    cam = Cam(pose, f0.shape[1])
    mesa = mod.mesa
    # objetivos: grilla en la mesa dentro de la huella del proyector + esquinas de la mesa MRUK + Pixel
    us, vs = np.linspace(150, W - 150, grilla[0]), np.linspace(120, H - 120, grilla[1])
    nodos = np.array([[u, v] for v in vs for u in us])
    X = mod.a_mesa(nodos)
    tipos = ["grilla"] * len(X)
    c, e1, e2, hw, hh = mesa["c"], mesa["e1"], mesa["e2"], mesa["w"] / 2, mesa["h"] / 2
    for sa, sb in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        Xc = c + sa * hw * e1 + sb * hh * e2
        p = mod.a_proyector(Xc[None])[0]
        if np.all(np.isfinite(p)) and 20 < p[0] < W - 20 and 20 < p[1] < H - 20:
            X = np.vstack([X, Xc]); tipos.append("esquina_mesa")
    if s.p("pixel.json").exists():
        pj = s.jr("pixel.json")
        if pj.get("detectado"):
            for Xc in np.array(pj["esquinas_tablero_app"]):
                X = np.vstack([X, Xc]); tipos.append("pixel")
    tipos = np.array(tipos)
    P_int = mod.a_proyector(X)
    # solo objetivos donde la luz cae de verdad sobre la mesa (par decodificado cercano, etiqueta TABLE y que calce)
    d3 = np.load(s.p("puntos3d.npz"))
    pp, et, Xp = d3["proj"], d3["etiqueta"], d3["X"]
    okm = (et == "TABLE") & np.isfinite(Xp).all(1)
    okm[okm] &= np.linalg.norm(mod.a_proyector(Xp[okm]) - pp[okm], axis=1) < 2.5
    sirve = np.ones(len(X), bool)
    for k in range(len(X)):
        if tipos[k] == "pixel" or not np.all(np.isfinite(P_int[k])):
            sirve[k] = np.all(np.isfinite(P_int[k]))
            continue
        dd = np.linalg.norm(pp - P_int[k], axis=1)
        cerca = dd < 30
        sirve[k] = cerca.sum() >= 3 and okm[cerca].mean() > 0.8
    s.log(f"objetivos: {sirve.sum()} de {len(X)} sobre la mesa ({', '.join(f'{t}={int((sirve & (tipos == t)).sum())}' for t in np.unique(tipos))})")
    X, tipos, P_int = X[sirve], tipos[sirve], P_int[sirve]
    uv_cam = cam.proyectar(X)
    corr = None
    hist = []
    negro = imagen_patron({"tipo": "negro"})
    for it in range(iters):
        dots = np.zeros((H, W, 3), np.uint8)
        for (u, v) in P_int:
            cv2.circle(dots, (int(round(u * 8)), int(round(v * 8))), radio * 8, (255, 255, 255), -1, cv2.LINE_AA, shift=3)
        if corr is not None:
            dots = cv2.remap(dots, corr[0], corr[1], cv2.INTER_LINEAR)
        cap.prev_r = None
        g0, _ = cap.esperar_cuadro(cap.mostrar(negro, "lazo_negro"))
        g1, _ = cap.esperar_cuadro(cap.mostrar(dots, f"lazo_puntos_{it}"), min_cambio=0.002)
        obs = puntos_blob(g1 - g0)
        emp = np.full((len(X), 2), np.nan)
        for k, q in enumerate(uv_cam):
            if len(obs) == 0 or not np.all(np.isfinite(q)):
                continue
            dd = np.linalg.norm(obs - q, axis=1)
            j = int(np.argmin(dd))
            if dd[j] < 40:
                emp[k] = obs[j]
        ok = np.isfinite(emp).all(1)
        Xo = np.full_like(X, np.nan)
        Xo[ok] = interseca_mesa(cam, emp[ok], mesa)
        e_mm = np.linalg.norm(mesa_mm(Xo[ok], mesa) - mesa_mm(X[ok], mesa), axis=1)
        p_obs = mod.a_proyector(Xo[ok])
        e_px_vec = p_obs - P_int[ok]
        e_px = np.linalg.norm(e_px_vec, axis=1)
        reg = {"iteracion": it, "objetivos": int(len(X)), "medidos": int(ok.sum()), "blobs": int(len(obs)),
               "px_proyector": stats(e_px), "mm_mesa": stats(e_mm),
               "por_tipo": {t: {"n": int((ok & (tipos == t)).sum()),
                                "mm": stats(np.linalg.norm(mesa_mm(Xo[ok & (tipos == t)], mesa) - mesa_mm(X[ok & (tipos == t)], mesa), axis=1))}
                            for t in np.unique(tipos)}}
        hist.append(reg)
        s.log(f"  iteración {it}: {ok.sum()}/{len(X)} puntos medidos; error {reg['px_proyector']} px proyector · "
              f"{reg['mm_mesa']} mm en la mesa")
        # imagen de verificación
        vis = cv2.cvtColor(np.clip(g1, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        for k in range(len(X)):
            q = uv_cam[k]
            if not np.all(np.isfinite(q)):
                continue
            col = (0, 255, 0) if tipos[k] == "grilla" else ((255, 200, 0) if tipos[k] == "esquina_mesa" else (255, 0, 255))
            cv2.circle(vis, (int(q[0]), int(q[1])), 9, col, 1, cv2.LINE_AA)
            if ok[k]:
                o_ = emp[k]
                cv2.circle(vis, (int(o_[0]), int(o_[1])), 3, (0, 0, 255), -1)
                tip = q + 5 * (o_ - q)
                cv2.arrowedLine(vis, (int(q[0]), int(q[1])), (int(tip[0]), int(tip[1])), (0, 128, 255), 1, cv2.LINE_AA, tipLength=0.2)
        cv2.putText(vis, f"lazo {it}: mediana {reg['px_proyector'].get('mediana', -1)} px proy / {reg['mm_mesa'].get('mediana', -1)} mm"
                    f" (flechas x5; verde=previsto, rojo=medido)", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        cv2.imwrite(str(s.p(f"verificacion_lazo_{it}.jpg")), vis, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if ok.sum() >= 12 and it < iters - 1:
            bueno = e_px < max(15.0, 5 * float(np.median(e_px)))
            corr = componer_corr(corr, P_int[ok][bueno], e_px_vec[bueno])
    proy.apagar()
    out = {"historial": hist, "iteraciones": iters}
    if not guardar:
        return out
    if corr is not None:
        np.savez_compressed(s.p("lazo_corr.npz"), mapx=corr[0].astype(np.float32), mapy=corr[1].astype(np.float32))
        dmax = float(np.max(np.hypot(corr[0] - np.arange(W)[None, :], corr[1] - np.arange(H)[:, None])))
        out["corr_max_px"] = dmax
        out["corr"] = "lazo_corr.npz (mapx, mapy para cv2.remap en el espacio del proyector)"
    s.jw("lazo.json", out)
    return out


# ============================================================================ 7. exportar

def rq_K(M: np.ndarray):
    from scipy.linalg import rq
    K, R = rq(M)
    D = np.diag(np.sign(np.diag(K)))
    K, R = K @ D, D @ R
    return K / K[2, 2], R


def paso_exportar(s: Sesion, push: bool = False) -> dict:
    s.log("== exportar")
    mod = Modelo(s)
    m = mod.m
    geo = s.jr("geometria.json")
    pose = s.jr("cam_pose.json")
    P = np.array(m["P_app"], float)                        # centros enteros, mundo app
    d3 = np.load(s.p("puntos3d.npz"))
    X = d3["X"][np.isfinite(d3["X"]).all(1)]
    P = P / np.linalg.norm(P[2, :3])
    if np.median(np.c_[X, np.ones(len(X))] @ P[2]) < 0:
        P = -P
    T05 = np.array([[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1.0]])
    P_app_bordes = T05 @ P                                # convención de la app: centro del píxel i en i + 0,5
    P_app_bordes = P_app_bordes / np.linalg.norm(P_app_bordes[2, :3])
    K_app, R_app = rq_K(P_app_bordes[:, :3])
    C = -np.linalg.solve(P_app_bordes[:, :3], P_app_bordes[:, 3])
    # chequeo de convención: proyectar con P (sin k1) los pares 3D y comparar con el modelo
    sel = np.isin(d3["etiqueta"], ["TABLE", "FLOOR", "WALL_FACE"]) & np.isfinite(d3["X"]).all(1)
    Xh = np.c_[d3["X"][sel], np.ones(sel.sum())]
    uvp = (Xh @ P_app_bordes.T)
    uvp = uvp[:, :2] / uvp[:, 2:] - 0.5
    e_conv = np.linalg.norm(uvp - mod.a_proyector(d3["X"][sel], k1=False), axis=1)
    e_dat = np.linalg.norm(uvp - d3["proj"][sel], axis=1)
    s.log(f"P de la app (bordes) vs modelo: máx {e_conv.max():.2e} px; vs datos (sin k1): mediana {np.median(e_dat):.2f} px")
    ancla = pose.get("ancla_sala_m4")
    A = np.array(ancla, float).reshape(4, 4) if ancla else np.eye(4)
    P_sala = P_app_bordes @ A
    # H proyector (centros enteros) → mesa (mm, marco de la ancla TABLE)
    gu, gv = np.meshgrid(np.linspace(0, W - 1, 25), np.linspace(0, H - 1, 15))
    uvg = np.stack([gu.ravel(), gv.ravel()], 1)
    Xg = mod.a_mesa(uvg, k1=False)
    mm = mesa_mm(Xg, mod.mesa)
    Hpm, _ = cv2.findHomography(uvg, mm, 0)
    hues = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], float)
    hue_mm = mesa_mm(mod.a_mesa(hues, k1=False), mod.mesa)
    lados = [float(np.linalg.norm(hue_mm[i] - hue_mm[(i + 1) % 4])) for i in range(4)]
    # referencia de la Osmo (2-nov): rect de 758x444 mm en px del proyector
    osmo = {}
    sj = REPO / "captures/dale-20261102-1511/superficie.json"
    if sj.exists():
        so = json.loads(sj.read_text())
        rp = np.array(so.get("rect_proj_manual") or so["rect_proj_tl_tr_br_bl"], float)
        ven = (s.jr("captura.json").get("ventana") if s.p("captura.json").exists() else None) or {}
        off = np.array(ven.get("pos", [10, 1116]), float) - np.array(ven.get("display", [0, 1080]), float)
        rp = rp - off                     # display 1920x1080 del 2-nov → contenido de la ventana (aprox.: ventana sin escalar)
        r_mm = cv2.perspectiveTransform(rp.reshape(-1, 1, 2), Hpm).reshape(-1, 2)
        ladosr = [float(np.linalg.norm(r_mm[i] - r_mm[(i + 1) % 4])) for i in range(4)]
        osmo = {"rect_proj": rp, "rect_mm_con_esta_calib": r_mm, "lados_mm": ladosr,
                "ancho_medio_mm": (ladosr[0] + ladosr[2]) / 2, "alto_medio_mm": (ladosr[1] + ladosr[3]) / 2,
                "osmo_dice_mm": so.get("superficie_mm")}
        s.log(f"referencia Osmo (2-nov, hoja carta): rect {so.get('superficie_mm')} mm; con esta calibración mide "
              f"{osmo['ancho_medio_mm']:.0f} x {osmo['alto_medio_mm']:.0f} mm")
    lazo = s.jr("lazo.json") if s.p("lazo.json").exists() else None
    pix = s.jr("pixel.json") if s.p("pixel.json").exists() else None
    ahora = dt.datetime.now()
    res = m["residuo_px"]
    out = {
        "fecha": ahora.isoformat(timespec="seconds"), "sesion": str(s.dir.relative_to(REPO)) if s.dir.is_relative_to(REPO) else str(s.dir),
        "metodo": "gray code 11+11 bits proyectado por el runtime, cámara passthrough izquierda del Quest 3 (fija), "
                  "rayos ∩ MRUK, PnP (DLT/IPPE + LM robusto, epipolar con paralaje)",
        "caso": m["caso"], "variante": m["variante"], "sintetico": s.dry,
        "proyector": {"ancho": W, "alto": H, "K": m["K"], "distorsion": {"k1": m["k1"], "modelo": "radial k1 (x,y normalizados)"},
                      "imagen_espejada": m["sx"] < 0,
                      "espacio_px": f"contenido de la ventana del proyector {W}x{H} (playground/ventana_proyector.py), no el display",
                      "ventana": (s.jr("captura.json").get("ventana") if s.p("captura.json").exists() else None),
                      "convencion_px": "centros enteros (OpenCV): columna i del PNG mostrado en la ventana = u i"},
        "aruco_primera_vista": m.get("aruco_primera_vista"),
        "pose_sala": {"marco": "mundo de la sala, Spatial SDK (x der, y arriba, z adelante, zurdo, m)",
                      "centro_m": m["C_app"], "eje_optico": m["eje_app"], "u_imagen": m["u_app"],
                      "R_diestro_cv": m["R_diestro"], "t_diestro_cv": m["t_diestro"],
                      "nota_diestro": "X_r = (x, y, -z) del mundo de la app; x_proj = R X_r + t; u = K x (OpenCV, sin k1)"},
        "P_app_centros": P, "chequeos": m["chequeos"], "planitud": m["planitud"],
        "puntos": {"pares_3d": m["pares_3d"], "inliers": m["inliers"], "paralaje": m["paralaje"]},
        "residuos": {"reproyeccion_px": res, "epipolar_px": m["residuo_epipolar_px"], "sigmas": m["sigmas"]},
        "variantes": m["variantes"], "alturas_paralaje_mm": m.get("alturas_paralaje_mm"),
        "H_proj_to_mesa": Hpm, "H_mesa_to_proj": np.linalg.inv(Hpm),
        "mesa": {"marco": "ancla MRUK TABLE: origen en su centro, x_mm = eje X local, y_mm = eje Y local (mm)",
                 "ancla": geo.get("mesa"), "huella_proyector_mm_tl_tr_br_bl": hue_mm, "lados_huella_mm": lados},
        "camara_quest": {"fuente_pose": pose.get("fuente"), "dispersion_mm": pose.get("dispersion_mm"),
                         "dispersion_deg": pose.get("dispersion_deg"), "intrinsecos": pose.get("intr"), "distorsion": pose.get("dist")},
        "referencia_osmo": osmo, "pixel": {k: v for k, v in (pix or {}).items() if k not in ("esquinas_img",)} or None,
        "lazo": lazo, "vs_verdad": m.get("vs_verdad"),
        "pose_fija": bool(m.get("pose_fija", False)), "incertidumbre": m.get("incertidumbre"),
        "historial_residuo": m.get("historial_residuo"),
        "pose_quest": (m.get("bundle") or {}).get("pose_quest"), "vistas": (m.get("bundle") or {}).get("vistas"),
    }
    s.jw("calib_proyector_mesa.json", out)
    s.jw("H_proj_to_mesa.json", {"H_proj_to_mesa": Hpm, "H_mesa_to_proj": np.linalg.inv(Hpm), "unidades": "px del proyector "
                                 "(centros enteros) → mm en el plano de la mesa (marco de la ancla TABLE)", "huella_mm": hue_mm})
    app = {"tipo": "luz_estructurada_quest", "marco": "mundo de la sala (Spatial SDK: x der, y arriba, z adelante, zurdo)",
           "espacio_px": f"contenido de la ventana del proyector {W}x{H} (ventana_proyector.py); bordes: centro del píxel i en i+0,5",
           "pares": m["pares_3d"], "marcadores": 0, "inliers": m["inliers"], "residuo_mediana_px": res.get("mediana"),
           "residuo_p90_px": res.get("p90"), "rms_px": res.get("rms"), "planitud": m["planitud"],
           "reflejado_al_lado_de_la_camara": False, "P": P_app_bordes.ravel().tolist(), "K": K_app.ravel().tolist(),
           "R": R_app.ravel().tolist(), "centro": C.tolist(), "ancho": W, "alto": H,
           "resumen": f"fx={K_app[0, 0]:.1f} fy={K_app[1, 1]:.1f} cx={K_app[0, 2]:.1f} cy={K_app[1, 2]:.1f} skew={K_app[0, 1]:.2f} "
                      f"C=({C[0]:.3f}, {C[1]:.3f}, {C[2]:.3f}) m (gray code + Quest, {m['caso']})",
           "ancla_sala": A.ravel().tolist(), "P_sala": P_sala.ravel().tolist(), "fecha_ms": int(ahora.timestamp() * 1000),
           "k1_no_aplicado": m["k1"], "fuente": "playground/calib_quest_proyector.py",
           "pose_fija": bool(m.get("pose_fija", False)), "incertidumbre": m.get("incertidumbre"),
           "historial_residuo": [{k: v for k, v in h.items() if k != "C"} for h in (m.get("historial_residuo") or [])]}
    (s.p("calib_mesa.json")).write_text(json.dumps(app, indent=1, ensure_ascii=False))
    vista3d(s, mod, pose, out)
    s.log("escrito: calib_proyector_mesa.json, calib_mesa.json (app), H_proj_to_mesa.json, vista3d.png")
    if push and not s.dry:
        empujar(s)
    return out


def empujar(s: Sesion) -> None:
    src = s.p("calib_mesa.json")
    cmds = [[ADB, "-s", ADB_SERIAL, "push", str(src), "/data/local/tmp/calib_mesa.json"],
            [ADB, "-s", ADB_SERIAL, "shell", f"run-as {APP} sh -c 'cp files/calib_mesa.json files/calib_mesa.prev.json 2>/dev/null;"
                                             f" cat /data/local/tmp/calib_mesa.json > files/calib_mesa.json && ls -l files/calib_mesa.json'"]]
    for c in cmds:
        r = subprocess.run(c, capture_output=True, text=True, timeout=60)
        s.log(f"adb: {' '.join(c[3:5])} → rc {r.returncode} {(r.stdout + r.stderr).strip()[:200]}")


def vista3d(s: Sesion, mod: Modelo, pose: dict, out: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cam = Cam(pose, 1728)
    fig = plt.figure(figsize=(11, 8))
    ax = fig.add_subplot(111, projection="3d")
    P3 = lambda X: (np.asarray(X)[..., 0], np.asarray(X)[..., 2], np.asarray(X)[..., 1])  # noqa: E731  (x, z, y-arriba)
    mesa = mod.mesa
    c, e1, e2, hw, hh = mesa["c"], mesa["e1"], mesa["e2"], mesa["w"] / 2, mesa["h"] / 2
    rect = np.array([c - e1 * hw - e2 * hh, c + e1 * hw - e2 * hh, c + e1 * hw + e2 * hh, c - e1 * hw + e2 * hh, c - e1 * hw - e2 * hh])
    ax.plot(*P3(rect), color="saddlebrown", lw=2, label="mesa (MRUK)")
    g = s.jr("geometria.json")
    if g.get("piso_y") is not None:
        y = g["piso_y"]
        sq = np.array([[c[0] - 1, y, c[2] - 1], [c[0] + 1, y, c[2] - 1], [c[0] + 1, y, c[2] + 1], [c[0] - 1, y, c[2] + 1], [c[0] - 1, y, c[2] - 1]])
        ax.plot(*P3(sq), color="gray", lw=1, label="piso")
    C = np.array(out["pose_sala"]["centro_m"])
    hue = mod.a_mesa(np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], float), k1=False)
    for X in hue:
        ax.plot(*P3(np.stack([C, X])), color="orange", lw=1)
    ax.plot(*P3(np.vstack([hue, hue[:1]])), color="orange", lw=2, label="huella del proyector")
    ax.scatter(*P3(C[None]), color="red", s=60, label="proyector")
    fr = cam.rayos(np.array([[0, 0], [1727, 0], [1727, 1727], [0, 1727]], float))
    for d in fr:
        ax.plot(*P3(np.stack([cam.o, cam.o + 0.5 * d])), color="royalblue", lw=1)
    ax.scatter(*P3(cam.o[None]), color="royalblue", s=50, label="cámara Quest")
    d3 = np.load(s.p("puntos3d.npz"))
    X = d3["X"][np.isfinite(d3["X"]).all(1)][::5]
    ax.scatter(*P3(X), s=1, color="green", alpha=0.4)
    ax.set_xlabel("x (m)"); ax.set_ylabel("z (m)"); ax.set_zlabel("y arriba (m)")
    allp = np.vstack([rect, C[None], cam.o[None], hue])
    mid = allp.mean(0); r = (allp.max(0) - allp.min(0)).max() / 2 + 0.1
    ax.set_xlim(mid[0] - r, mid[0] + r); ax.set_ylim(mid[2] - r, mid[2] + r); ax.set_zlim(mid[1] - r, mid[1] + r)
    ch = out["chequeos"]
    ax.set_title(f"Proyector de la mesa: {ch.get('altura_sobre_mesa_m')} m sobre la mesa, {ch.get('altura_sobre_piso_m')} m sobre el piso,"
                 f" inclinación {ch.get('inclinacion_desde_vertical_deg')}°", fontsize=10)
    ax.legend(loc="upper left", fontsize=8)
    ax.view_init(elev=18, azim=-60)
    fig.tight_layout()
    fig.savefig(s.p("vista3d.png"), dpi=110)
    plt.close(fig)


# ============================================================================ CLI

def ultima_sesion() -> Path | None:
    xs = sorted(CAPTURES.glob("quest-cal-*"))
    return xs[-1] if xs else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paso", choices=["todo", "captura", "decodificar", "geometria", "resolver", "pixel", "lazo", "exportar", "calcular",
                                     "bundle", "verificar"])
    ap.add_argument("--sesion", type=Path, default=None)
    ap.add_argument("--dry", action="store_true", help="escena sintética: no toca el proyector, el Quest ni adb")
    ap.add_argument("--push", action="store_true", help="copiar calib_mesa.json a files/ de la app del casco")
    ap.add_argument("--iter-lazo", type=int, default=3)
    ap.add_argument("--sin-pixel", action="store_true")
    ap.add_argument("--sin-lazo", action="store_true")
    ap.add_argument("--ventana", default=None, help="URL de playground/ventana_proyector.py (por defecto busca :8793 y :8792)")
    ap.add_argument("--promedio", type=int, default=3, help="cuadros completos promediados por patrón (sala oscura: 3)")
    ap.add_argument("--sin-prior", action="store_true", help="sin el best guess de posición (solo si hay datos no coplanares)")
    ap.add_argument("--prior-altura", type=float, default=0.10, help="proyector sobre la cámara del Quest (m)")
    ap.add_argument("--prior-lateral", type=float, default=0.20, help="distancia horizontal Quest↔proyector (m)")
    a = ap.parse_args(argv)
    PRIOR.update(activo=not a.sin_prior, dh=a.prior_altura, dl=a.prior_lateral)
    global VENTANA, VIGIA
    if a.ventana:
        VENTANA = a.ventana.rstrip("/")
    if a.paso in ("bundle", "verificar"):
        s = Sesion(a.sesion or FIJA)
        if a.paso == "bundle":
            paso_bundle(s, push=a.push)
        else:
            if buscar_ventana() is None:
                raise SystemExit("no encuentro la ventana del proyector (:8793/:8792)")
            VIGIA = VigiaDCPEXT(s)
            try:
                paso_verificar(s)
            finally:
                VIGIA.cerrar()
        return 0
    if a.sesion is None:
        if a.paso in ("todo", "captura"):
            a.sesion = CAPTURES / f"quest-cal-{dt.datetime.now():%Y%m%d-%H%M}{'-dry' if a.dry else ''}"
        else:
            a.sesion = ultima_sesion()
            if a.sesion is None:
                raise SystemExit("no hay sesiones quest-cal-*: corre 'captura' primero")
    s = Sesion(a.sesion, dry=a.dry or a.sesion.name.endswith("-dry"))
    if s.p("captura.json").exists() and a.paso != "captura" and a.paso != "todo":
        pw = s.jr("captura.json").get("proyector")
        if pw:
            fijar_proyector(*pw)
    pasos = {"todo": ["captura", "decodificar", "geometria", "resolver", "pixel", "lazo", "exportar"],
             "calcular": ["decodificar", "geometria", "resolver", "exportar"]}.get(a.paso, [a.paso])
    for p in pasos:
        if p in ("pixel", "lazo") and not s.dry and VIGIA is None and not (p == "pixel" and a.sin_pixel) \
                and not (p == "lazo" and a.sin_lazo):
            if buscar_ventana() is None:
                raise SystemExit("no encuentro la ventana del proyector (:8793/:8792)")
            VIGIA = VigiaDCPEXT(s)
        if p == "captura":
            paso_captura(s, promedio=a.promedio)
        elif p == "decodificar":
            paso_decodificar(s)
        elif p == "geometria":
            paso_geometria(s)
        elif p == "resolver":
            paso_resolver(s)
        elif p == "pixel" and not a.sin_pixel:
            try:
                paso_pixel(s)
            except Exception as e:  # noqa: BLE001
                s.log(f"Pixel: falló ({e}); sigo sin él")
        elif p == "lazo" and not a.sin_lazo:
            paso_lazo(s, iters=a.iter_lazo)
        elif p == "exportar":
            paso_exportar(s, push=a.push)
    if VIGIA is not None:
        VIGIA.cerrar()
    s.log(f"listo: {s.dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Ajuste a mano del teclado proyectado, pedido de Max 2-nov: «el teclado no está para nada fitted correctamente».

    python -m playground.teclado_manual captures/dale-XXXX [--corner 1] [--runtime http://127.0.0.1:8790]
                                         [--feed http://127.0.0.1:8798] [--gris 200] [--latencia 1.8]
    python -m playground.teclado_manual captures/dale-XXXX --dry [--teclas "2LL0jj"] [--img lienzo.jpg]
    --quad x,y,x,y,x,y,x,y   partir de ese cuadrilátero (TL TR BR BL del lienzo) en vez del de teclado.json; el
                             «antes» que se registra sigue siendo el del archivo

El proyector muestra sobre negro el cuerpo del teclado (borde cian) y sus 104 teclas (plantilla ANSI de
`teclado_grid`, gris claro con separación negra, rótulos Esc, F1, F12, Enter, Space, KP+ y flechas leíbles para Max,
que se sienta al otro lado: rot 180). Max mueve las 4 esquinas del CUERPO igual que en `superficie_manual`; las
esquinas viven en px del LIENZO (las mismas de teclado.json) y la grilla se rehace con `teclado_grid.fit` en cada
tecla. Se proyecta con H_lienzo_to_proj de superficie.json y, si existe, el remap de lazo cerrado (lazo_corr.npz),
igual que la composición del feed. Mientras corre, la capa del feed (/rojo) queda apagada; al salir vuelve.

Teclas (en la ventanita del monitor de Max; arriba/abajo/izquierda/derecha son las de Max, no las del lienzo):
    1 2 3 4        esquina TL TR BR BL del cuerpo en el lienzo (el número grande proyectado dice cuál es)
    0              todo el teclado: las mismas flechas mueven las 4 esquinas juntas
    flechas, h j k l   2 px del lienzo (~0,8 mm) · H J K L 10 px
    i / o          inset (margen cuerpo → teclas) +/-0,002 · I / O +/-0,01
    c              4 s de recorte de control en la ventanita (cámara sin proyección + grilla encima); no se proyecta
    r              volver al inicio de la sesión · s guardar · q guardar y salir · x salir sin guardar (Ctrl-C también)
(`s` es guardar, como en superficie_manual; por eso «mover todo» va con 0 + flechas y no con w a s d.)

Guardar (s / q):
  1. apaga la proyección ~2 s y toma un cuadro limpio del lienzo (/limpio.mjpg del feed);
  2. reescribe <scan>/teclado.json con `fit` del cuadrilátero final, conserva las claves que ya tenía y agrega
     quad_antes, inset_antes, ajuste_manual_px (por esquina, lienzo), ajustado (fecha) y fuente;
  3. escribe <scan>/teclado_ref.jpg con ese cuadro (referencia del tracker del feed; va después del json porque el
     feed rehace el tracker cuando cambia la foto);
  4. agrega o actualiza la entrada de esta sesión en <scan>/ajustes_objetos.json;
  5. GET {feed}/teclado/recargar (si responde 404, lo avisa y sigue: el feed igual relee teclado.json por mtime).

<scan>/ajustes_objetos.json: entrada del modelo global de fit 3D de objetos y relación cámara→objeto. Es una lista
acumulativa, una entrada por sesión de ajuste a mano (re-guardar en la misma sesión la reemplaza):
    objeto              "teclado"
    sesion, fecha       inicio de la sesión y hora del último guardado
    herramienta         "playground.teclado_manual"
    rot, plantilla      orientación del objeto en el lienzo; plantilla ANSI en U (22,5 x 6,5; U = 19,05 mm)
    quad_lienzo_antes / quad_lienzo_despues    TL TR BR BL del cuerpo en px del lienzo
    inset_antes / inset_despues
    ajuste_px / ajuste_mm   por esquina (despues - antes); mm_por_px de superficie.json
    traslacion_media_px / traslacion_media_mm
    pose_antes / pose_despues   centro_mm, angulo_deg (eje Esc→F12 en el lienzo), largo_mm, ancho_mm
    H_lienzo_to_teclado_U   homografía lienzo px → coordenadas del teclado en U (origen en la esquina de Esc,
                            x hacia F12, y hacia Space), inversa de `teclado_grid.units_homography`
    H_lienzo_to_proj        la de superficie.json; lazo_corr: si se aplicó el remap de lazo al proyectar
    camara, H_cam_to_lienzo, quad_cam_antes / quad_cam_despues   (de superficie_feed.json, px de la Osmo 4K)
El lienzo es el plano de la mesa rectificado: la diferencia antes/después de un objeto con altura (teclado ~3 cm)
mide, entre otras cosas, el paralaje cámara→objeto según su posición; con varias entradas (y otros objetos) el
modelo global puede ajustar alturas y sesgos sistemáticos del fit automático.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from . import pad_checker as pc
from . import teclado_fit as tf
from . import teclado_grid as tg

W, H = 1920, 1080                       # proyector
NAMES = ["TL", "TR", "BR", "BL"]
ROTULOS = {"Esc": ("Esc", 0.30), "F1": ("F1", 0.30), "F12": ("F12", 0.30), "Enter": ("Enter", 0.30),
           "Space": ("Space", 0.34), "KP+": ("+", 0.55)}
FLECHAS = {"Up": (0, -1), "Down": (0, 1), "Left": (-1, 0), "Right": (1, 0)}   # en la plantilla (y hacia Max)
CIAN, AMARILLO, NEGRO = (255, 255, 0), (0, 255, 255), (0, 0, 0)
PASOS = {"h": (-2, 0), "l": (2, 0), "k": (0, -2), "j": (0, 2), "H": (-10, 0), "L": (10, 0), "K": (0, -10), "J": (0, 10)}
FLECHAS_TECLADO = {63234: (-2, 0), 63235: (2, 0), 63232: (0, -2), 63233: (0, 2), 2: (-2, 0), 3: (2, 0), 0: (0, -2), 1: (0, 2)}
WIN_W = 1000


# ---------------------------------------------------------------- geometría

def marco_usuario(quad: np.ndarray, rot: int) -> tuple[np.ndarray, np.ndarray]:
    """Vectores unitarios (lienzo) de los ejes de la plantilla: u = Esc→F12, v = fila F→Space (hacia Max)."""
    q = np.asarray(quad, np.float64).reshape(4, 2)
    if rot % 360 == 180:
        q = q[[2, 3, 0, 1]]
    u = (q[1] - q[0]) + (q[2] - q[3]); v = (q[3] - q[0]) + (q[2] - q[1])
    return u / (np.linalg.norm(u) + 1e-9), v / (np.linalg.norm(v) + 1e-9)


def pose_mm(quad: np.ndarray, rot: int, mmpx: float) -> dict:
    q = np.asarray(quad, np.float64).reshape(4, 2)
    u, _ = marco_usuario(q, rot)
    largo = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    ancho = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    return {"centro_mm": (q.mean(0) * mmpx).round(1).tolist(), "angulo_deg": round(math.degrees(math.atan2(u[1], u[0])), 2),
            "largo_mm": round(largo * mmpx, 1), "ancho_mm": round(ancho * mmpx, 1)}


def mapa_proyector(Hlp: np.ndarray, corr: tuple[np.ndarray, np.ndarray] | None) -> tuple[np.ndarray, np.ndarray]:
    """Mapa único proyector px → lienzo px: el feed hace warpPerspective(lienzo, Hlp) y después remap(lazo); aquí
    out[p] = lienzo[Hlp⁻¹·(mapx[p], mapy[p])], una sola interpolación."""
    if corr is not None:
        pts = np.dstack([corr[0], corr[1]]).astype(np.float64)
    else:
        xx, yy = np.meshgrid(np.arange(W, dtype=np.float64), np.arange(H, dtype=np.float64))
        pts = np.dstack([xx, yy])
    L = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), np.linalg.inv(Hlp)).reshape(H, W, 2)
    return L[..., 0].astype(np.float32), L[..., 1].astype(np.float32)


# ---------------------------------------------------------------- dibujo

def texto(img: np.ndarray, s: str, centro, u, v, alto: float, color, grosor: int = 2) -> None:
    """Texto centrado en `centro` con su base a lo largo de u y su «abajo» a lo largo de v (así se lee desde el lado
    de Max). alto en px de la imagen."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw1, th1), _ = cv2.getTextSize(s, font, 1.0, 1)
    fs = max(0.2, alto / max(1, th1))
    (tw, th), base = cv2.getTextSize(s, font, fs, grosor)
    m = grosor + 2
    patch = np.zeros((th + base + 2 * m, tw + 2 * m), np.uint8)
    cv2.putText(patch, s, (m, m + th), font, fs, 255, grosor, cv2.LINE_AA)
    ph, pw = patch.shape
    c = np.asarray(centro, np.float64); u = np.asarray(u, np.float64); v = np.asarray(v, np.float64)
    cy = m + th / 2.0                                     # centro vertical del cuerpo del texto (sin descendentes)
    M = np.array([[u[0], v[0], 0.0], [u[1], v[1], 0.0]])
    M[:, 2] = c - M[:, :2] @ np.array([pw / 2.0, cy])
    corners = np.array([[0, 0], [pw, 0], [pw, ph], [0, ph]], np.float64) @ M[:, :2].T + M[:, 2]
    x0, y0 = np.floor(corners.min(0)).astype(int) - 1; x1, y1 = np.ceil(corners.max(0)).astype(int) + 1
    x0, y0 = max(0, x0), max(0, y0); x1, y1 = min(img.shape[1], x1), min(img.shape[0], y1)
    if x1 <= x0 or y1 <= y0:
        return
    Mr = M.copy(); Mr[:, 2] -= (x0, y0)
    a = cv2.warpAffine(patch, Mr, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR).astype(np.float32)[..., None] / 255.0
    roi = img[y0:y1, x0:x1].astype(np.float32)
    img[y0:y1, x0:x1] = (roi * (1 - a) + np.array(color, np.float32) * a).astype(np.uint8)


def _p(pts) -> np.ndarray:
    return np.round(np.asarray(pts, np.float64) * 16).astype(np.int32).reshape(-1, 1, 2)   # shift=4: subpíxel


def render_lienzo(grid: dict, quad: np.ndarray, sel: int, lw: int, lh: int, gris: int, rot: int) -> np.ndarray:
    """Lo que se proyecta, en px del lienzo: negro, teclas grises con separación negra, borde del cuerpo cian,
    esquina activa con cruz y número grande amarillo (0 = todo el teclado)."""
    img = np.zeros((lh, lw, 3), np.uint8)
    q = np.asarray(quad, np.float64).reshape(4, 2)
    unit = float(grid.get("unit_px") or 48.0)
    u, v = marco_usuario(q, rot)
    for k in grid["keys"]:
        cv2.fillPoly(img, [_p(k["poly_lienzo"])], (gris, gris, gris), cv2.LINE_AA, shift=4)
        cv2.polylines(img, [_p(k["poly_lienzo"])], True, NEGRO, 2, cv2.LINE_AA, shift=4)
    for k in grid["keys"]:
        c = np.array(k["center_lienzo"], np.float64)
        if k["name"] in ROTULOS:
            s, frac = ROTULOS[k["name"]]
            texto(img, s, c, u, v, frac * unit, NEGRO, 2)
        elif k["name"] in FLECHAS:
            d = FLECHAS[k["name"]][0] * u + FLECHAS[k["name"]][1] * v
            a_, b_ = c - d * 0.27 * unit, c + d * 0.27 * unit
            cv2.arrowedLine(img, tuple(np.round(a_ * 16).astype(int)), tuple(np.round(b_ * 16).astype(int)), NEGRO, 3, cv2.LINE_AA, 4, 0.45)
    cv2.polylines(img, [_p(q)], True, CIAN, 3, cv2.LINE_AA, shift=4)
    ctr = q.mean(0)
    for i in range(4):
        p = q[i]; out = (p - ctr) / (np.linalg.norm(p - ctr) + 1e-9)
        activa = sel == i or sel < 0
        if sel == i:
            for e in (q[(i + 1) % 4] - p, q[(i + 3) % 4] - p):       # cruz a lo largo de los dos bordes que llegan
                e = e / (np.linalg.norm(e) + 1e-9); L = 1.6 * unit
                cv2.line(img, tuple(np.round((p - e * L) * 16).astype(int)), tuple(np.round((p + e * L) * 16).astype(int)), AMARILLO, 3, cv2.LINE_AA, 4)
            cv2.circle(img, tuple(np.round(p * 16).astype(int)), 12 * 16, AMARILLO, 2, cv2.LINE_AA, 4)
            pos = np.clip(p + out * 1.45 * unit, 32, (lw - 32, lh - 32))
            texto(img, str(i + 1), pos, u, v, 1.1 * unit, AMARILLO, 5)
        else:
            cv2.circle(img, tuple(np.round(p * 16).astype(int)), 7 * 16, AMARILLO if activa else CIAN, 2, cv2.LINE_AA, 4)
            pos = np.clip(p + out * 0.9 * unit, 20, (lw - 20, lh - 20))
            texto(img, str(i + 1), pos, u, v, 0.5 * unit, AMARILLO if activa else CIAN, 2)
    if sel < 0:                                                          # modo todo: cruz al centro
        for e in (u, v):
            cv2.line(img, tuple(np.round((ctr - e * 1.4 * unit) * 16).astype(int)), tuple(np.round((ctr + e * 1.4 * unit) * 16).astype(int)), AMARILLO, 4, cv2.LINE_AA, 4)
    return img


def rotar_para_max(img: np.ndarray, grid: dict, quad: np.ndarray, rot: int) -> tuple[np.ndarray, dict, np.ndarray]:
    """Imagen del lienzo, grilla y cuadrilátero girados como los ve Max (rot 180) para la ventanita."""
    if rot % 360 != 180:
        return img, grid, np.asarray(quad, np.float64)
    h, w = img.shape[:2]
    f = lambda pts: (np.array([w - 1, h - 1], np.float64) - np.asarray(pts, np.float64)).tolist()   # noqa: E731
    g2 = {**grid, "keys": [{**k, "poly_lienzo": f(k["poly_lienzo"]), "center_lienzo": f(k["center_lienzo"])} for k in grid["keys"]]}
    return cv2.rotate(img, cv2.ROTATE_180), g2, np.array(f(quad))


# ---------------------------------------------------------------- red

def http_get(url: str, timeout: float = 4.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except OSError:
        return 0, b""


def cuadro_mjpeg(url: str, saltar: int = 1, timeout: float = 8.0) -> np.ndarray | None:
    """Un cuadro de un MJPEG multipart (con Content-Length por parte). saltar=1 descarta el primero, que el feed
    manda al tiro y puede ser anterior al pedido."""
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            buf = b""; vistos = 0
            while time.time() - t0 < timeout:
                chunk = r.read1(1 << 16)
                if not chunk:
                    break
                buf += chunk
                while True:
                    i = buf.find(b"Content-Length:")
                    j = buf.find(b"\r\n\r\n", i) if i >= 0 else -1
                    if j < 0:
                        break
                    n = int(buf[i + 15:buf.find(b"\r\n", i)].strip())
                    if len(buf) < j + 4 + n:
                        break
                    data = buf[j + 4:j + 4 + n]; buf = buf[j + 4 + n:]
                    if vistos >= saltar:
                        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                    vistos += 1
    except OSError as e:
        print("mjpeg:", e, flush=True)
    return None


def escribir_atomico(path: Path, data: str | bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data.encode() if isinstance(data, str) else data)
    os.replace(tmp, path)


# ---------------------------------------------------------------- herramienta

class Ajuste:
    def __init__(self, scan: Path, a: argparse.Namespace):
        self.scan, self.a = scan, a
        self.sup = json.loads((scan / "superficie.json").read_text())
        self.lw, self.lh = (int(v) for v in self.sup["lienzo_px"])
        self.Hlp = np.array(self.sup["H_lienzo_to_proj"], np.float64)
        self.mmpx = float(self.sup.get("mm_por_px") or 0)
        self.tpath = scan / "teclado.json"
        self.tec0 = json.loads(self.tpath.read_text())
        self.rot = int(self.tec0.get("rot", 180))
        self.q0 = np.array(self.tec0["quad_lienzo"], np.float64).reshape(4, 2)      # el del archivo («antes»)
        self.inset0 = float(self.tec0.get("inset", 0.012))
        self.q_ini = self.q0.copy()                                                  # donde parte la sesión (r vuelve aquí)
        if a.quad:
            self.q_ini = np.array([float(t) for t in a.quad.split(",")], np.float64).reshape(4, 2)
        self.q, self.inset = self.q_ini.copy(), self.inset0
        self.sel = int(np.clip(a.corner, 0, 4)) - 1                     # -1 = todo
        self.corr = None
        cp = scan / "lazo_corr.npz"
        if cp.exists() and not a.sin_lazo:
            with np.load(cp) as z:
                self.corr = (z["mapx"].astype(np.float32), z["mapy"].astype(np.float32))
        self.mx, self.my = mapa_proyector(self.Hlp, self.corr)
        self.signo = -1.0 if (a.ejes == "max" and self.rot % 360 == 180) else 1.0
        self.fondo: np.ndarray | None = None                              # lienzo limpio (sin proyección)
        self.control: np.ndarray | None = None; self.control_hasta = 0.0
        self.msg = ""
        self.refrescar = lambda: None                                       # la ventanita (la pone main)
        self.sesion = time.strftime("%Y-%m-%d %H:%M:%S")
        qq = np.vstack([self.q0, self.q_ini])
        x0, y0 = np.floor(qq.min(0)).astype(int) - 140; x1, y1 = np.ceil(qq.max(0)).astype(int) + 140
        self.roi = (max(0, x0), max(0, y0), min(self.lw, x1), min(self.lh, y1))

    # ---- estado
    def grid(self, con_proj: bool = False) -> dict:
        return tg.fit(self.q.astype(np.float32), self.Hlp if con_proj else None, self.mmpx, rot=self.rot, inset=round(self.inset, 4))

    def mover(self, dx: float, dy: float) -> None:
        d = np.array([dx, dy], np.float64) * self.signo
        if self.sel < 0:
            self.q += d
        else:
            self.q[self.sel] += d
        self.q = np.clip(self.q, (-200, -200), (self.lw + 200, self.lh + 200))

    # ---- proyector
    def imagen_proyector(self) -> np.ndarray:
        lien = render_lienzo(self.grid(), self.q, self.sel, self.lw, self.lh, self.a.gris, self.rot)
        return cv2.remap(lien, self.mx, self.my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))

    def proyectar(self) -> None:
        out = self.scan / "teclado_manual.png"
        cv2.imwrite(str(out), self.imagen_proyector(), [cv2.IMWRITE_PNG_COMPRESSION, 1])
        try:
            pc.runtime_cmd(self.a.runtime, "overlay", path=str(out.resolve()), seconds=3600)
        except Exception as e:  # noqa: BLE001
            self.msg = f"overlay falló: {e}"; print(self.msg, flush=True)

    def apagar(self) -> None:
        try:
            pc.runtime_cmd(self.a.runtime, "overlay", off=True)
        except Exception as e:  # noqa: BLE001
            print("apagar overlay:", e, flush=True)

    # ---- feed
    def rojo_actual(self) -> bool | None:
        code, body = http_get(self.a.feed + "/status")
        try:
            return bool(json.loads(body)["rojo"]["on"]) if code == 200 else None
        except (ValueError, KeyError, TypeError):
            return None

    def rojo(self, on: bool) -> None:
        code, _ = http_get(f"{self.a.feed}/rojo?on={1 if on else 0}")
        if code != 200:
            print(f"/rojo?on={int(on)} respondió {code or 'sin conexión'}", flush=True)

    def cuadro_limpio(self, reproyectar: bool = True) -> np.ndarray | None:
        """Apaga la proyección, espera la latencia (AirPlay ~1 s + cámara) y toma un cuadro del lienzo limpio."""
        if self.a.dry:
            img = cv2.imread(str(self.a.img or (self.scan / "teclado_ref.jpg")))
            return img
        self.refrescar()
        self.apagar(); time.sleep(self.a.latencia)
        img = cuadro_mjpeg(self.a.feed + "/limpio.mjpg")
        if reproyectar:
            self.proyectar()
        if img is not None and img.shape[:2] != (self.lh, self.lw):
            print("el cuadro limpio no tiene el tamaño del lienzo:", img.shape, flush=True)
            img = cv2.resize(img, (self.lw, self.lh), interpolation=cv2.INTER_AREA)
        return img

    # ---- ventanita
    def recorte_control(self, img: np.ndarray) -> np.ndarray:
        r_img, r_grid, r_q = rotar_para_max(img, self.grid(), self.q, self.rot)
        crop = tf.crop_debug(r_img, r_grid, np.asarray(r_q, np.float32))
        s = WIN_W / crop.shape[1]
        return cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)

    def ventana(self) -> np.ndarray:
        if self.control is not None and time.time() < self.control_hasta:
            top = self.control.copy()
        else:
            self.control = None
            x0, y0, x1, y1 = self.roi
            base = (self.fondo if self.fondo is not None else np.full((self.lh, self.lw, 3), 35, np.uint8)).copy()
            g = self.grid()
            for k in g["keys"]:
                cv2.polylines(base, [_p(k["poly_lienzo"])], True, (0, 165, 255), 1, cv2.LINE_AA, shift=4)
            cv2.polylines(base, [_p(self.q)], True, (0, 220, 0), 2, cv2.LINE_AA, shift=4)
            for i, p in enumerate(self.q):
                act = self.sel == i or self.sel < 0
                cv2.circle(base, tuple(np.round(p * 16).astype(int)), (9 if act else 5) * 16, AMARILLO if act else (0, 220, 0), 2, cv2.LINE_AA, 4)
            crop = base[y0:y1, x0:x1]
            ch, cw = crop.shape[:2]
            if self.rot % 360 == 180:
                crop = cv2.rotate(crop, cv2.ROTATE_180)
            s = WIN_W / cw
            top = cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            for i, p in enumerate(self.q):                                     # números derechos, después de girar
                pp = p - (x0, y0)
                if self.rot % 360 == 180:
                    pp = np.array([cw - 1, ch - 1]) - pp
                pp = pp * s
                cv2.putText(top, str(i + 1), (int(pp[0]) + 10, int(pp[1]) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        bar = np.zeros((92, top.shape[1], 3), np.uint8)
        if self.sel < 0:
            d = (self.q - self.q0).mean(0); que = "TODO el teclado"
        else:
            d = self.q[self.sel] - self.q0[self.sel]; que = f"esquina {self.sel + 1} {NAMES[self.sel]} en ({self.q[self.sel][0]:.0f}, {self.q[self.sel][1]:.0f})"
        unit_mm = self.grid().get("unit_mm_medido")
        l1 = f"{que}  ajuste {d[0]:+.0f},{d[1]:+.0f} px ({d[0] * self.mmpx:+.1f},{d[1] * self.mmpx:+.1f} mm)  inset {self.inset:.3f}  U {unit_mm} mm"
        l2 = "1-4 esquina · 0 todo · flechas/hjkl 2px · HJKL 10px · i/o inset · c control · r inicio · s guardar · q guardar y salir · x salir"
        cv2.putText(bar, l1, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(bar, l2, (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1, cv2.LINE_AA)
        cv2.putText(bar, self.msg[:120], (10, 78), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 255), 1, cv2.LINE_AA)
        return np.vstack([top, bar])

    # ---- teclas
    def tecla(self, k: int) -> str:
        """Aplica una tecla; devuelve 'mover' (cambió la geometría), 'salir', 'salir_sin' o ''."""
        ch = chr(k) if 0 <= k < 256 and k >= 32 else ""
        if ch and ch in "01234":
            self.sel = int(ch) - 1; self.control = None; return "mover"
        if (ch and ch in PASOS) or k in FLECHAS_TECLADO:
            self.mover(*(PASOS.get(ch) or FLECHAS_TECLADO[k])); self.control = None; return "mover"
        if ch in ("i", "o", "I", "O"):
            paso = 0.002 if ch in "io" else 0.01
            self.inset = round(float(np.clip(self.inset + (paso if ch.lower() == "i" else -paso), 0.0, 0.08)), 4); return "mover"
        if ch == "r":
            self.q, self.inset = self.q_ini.copy(), self.inset0; self.msg = "volví al inicio"; return "mover"
        if ch == "c":
            self.msg = "control: apago la proyección ~2 s para ver la cámara limpia"
            img = self.cuadro_limpio()
            if img is None:
                self.msg = "control: no pude leer /limpio.mjpg del feed"; return ""
            self.fondo = img
            self.control = self.recorte_control(img); self.control_hasta = time.time() + 4.0
            cv2.imwrite(str(self.scan / "teclado_manual_crop.jpg"), self.control)
            self.msg = "control 4 s (cámara sin proyección, grilla encima)"; return ""
        if ch == "s":
            self.guardar(); return ""
        if ch == "q" or k == 27:
            return "salir"
        if ch == "x":
            return "salir_sin"
        return ""

    # ---- guardar
    def guardar(self) -> None:
        if self.a.dry:
            print("--dry: no guardo", flush=True); return
        self.msg = "guardando: apago la proyección ~2 s para la foto de referencia"
        ref = self.cuadro_limpio()
        grid = self.grid(con_proj=True)
        ahora = time.strftime("%Y-%m-%d %H:%M:%S")
        tec = {k: v for k, v in self.tec0.items() if k not in grid and k != "tracking"}
        tec.update(grid)
        tec.update({"quad_antes": self.q0.round(1).tolist(), "inset_antes": self.inset0,
                    "ajuste_manual_px": {NAMES[i]: (self.q[i] - self.q0[i]).round(1).tolist() for i in range(4)},
                    "ajustado": ahora, "fuente": "ajuste manual de Max con teclas proyectadas (playground.teclado_manual)",
                    "fuente_antes": self.tec0.get("fuente_antes") or self.tec0.get("fuente")})
        escribir_atomico(self.tpath, json.dumps(tec, indent=1))
        if ref is not None:                                               # después del json: el feed rehace el tracker con la foto
            ok, buf = cv2.imencode(".jpg", ref, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if ok:
                escribir_atomico(self.scan / "teclado_ref.jpg", buf.tobytes())
                self.fondo = ref
        else:
            print("no pude tomar el cuadro limpio: teclado_ref.jpg queda como estaba", flush=True)
        self.registrar(ahora)
        code, _ = http_get(self.a.feed + "/teclado/recargar")
        aviso = "" if code == 200 else f" · /teclado/recargar respondió {code or 'sin conexión'} (el feed igual relee teclado.json)"
        if code != 200:
            print(f"aviso: GET {self.a.feed}/teclado/recargar respondió {code or 'sin conexión'}; el feed relee teclado.json por mtime", flush=True)
        self.msg = f"guardado {ahora[-8:]}" + ("" if ref is not None else " (sin foto de referencia)") + aviso
        print(json.dumps({"guardado": str(self.tpath), "quad_lienzo": tec["quad_lienzo"], "inset": tec["inset"],
                          "ajuste_px": tec["ajuste_manual_px"], "unit_mm": tec.get("unit_mm_medido"), "ref": ref is not None}), flush=True)

    def registrar(self, ahora: str) -> None:
        apath = self.scan / "ajustes_objetos.json"
        try:
            lista = json.loads(apath.read_text()) if apath.exists() else []
            lista = lista if isinstance(lista, list) else []
        except ValueError:
            lista = []
        d = self.q - self.q0
        Hu = tg.units_homography(self.q, self.rot, self.inset)
        Hlu = np.linalg.inv(Hu); Hlu /= Hlu[2, 2]
        e = {"objeto": "teclado", "sesion": self.sesion, "fecha": ahora, "herramienta": "playground.teclado_manual",
             "rot": self.rot, "plantilla": {"tipo": "ANSI 104", "U_mm": tg.U, "ancho_U": tg.TOTAL_W, "alto_U": tg.TOTAL_H},
             "quad_lienzo_antes": self.q0.round(1).tolist(), "quad_lienzo_despues": self.q.round(1).tolist(),
             "inset_antes": self.inset0, "inset_despues": round(self.inset, 4),
             "ajuste_px": {NAMES[i]: d[i].round(1).tolist() for i in range(4)},
             "ajuste_mm": {NAMES[i]: (d[i] * self.mmpx).round(2).tolist() for i in range(4)}, "mm_por_px": self.mmpx,
             "traslacion_media_px": d.mean(0).round(1).tolist(), "traslacion_media_mm": (d.mean(0) * self.mmpx).round(2).tolist(),
             "pose_antes": pose_mm(self.q0, self.rot, self.mmpx), "pose_despues": pose_mm(self.q, self.rot, self.mmpx),
             "H_lienzo_to_teclado_U": Hlu.tolist(), "H_lienzo_to_proj": self.Hlp.tolist(), "lazo_corr": self.corr is not None,
             "lienzo_px": [self.lw, self.lh]}
        try:
            sf = json.loads((self.scan / "superficie_feed.json").read_text())
            Hcl = np.array(sf["H_cam_to_lienzo"], np.float64); Hlc = np.linalg.inv(Hcl)
            e.update({"camara": {"nombre": sf.get("camera"), "px": sf.get("camera_size"), "calibrado": sf.get("calibrado")},
                      "H_cam_to_lienzo": Hcl.tolist(),
                      "quad_cam_antes": cv2.perspectiveTransform(self.q0.reshape(-1, 1, 2), Hlc).reshape(4, 2).round(1).tolist(),
                      "quad_cam_despues": cv2.perspectiveTransform(self.q.reshape(-1, 1, 2), Hlc).reshape(4, 2).round(1).tolist()})
        except (OSError, ValueError, KeyError) as ex:
            e["camara"] = {"error": str(ex)[:120]}
        lista = [x for x in lista if not (x.get("objeto") == "teclado" and x.get("sesion") == self.sesion)] + [e]
        escribir_atomico(apath, json.dumps(lista, indent=1))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("scan")
    p.add_argument("--corner", type=int, default=1, help="esquina inicial 1-4 (TL TR BR BL del lienzo); 0 = todo")
    p.add_argument("--runtime", default="http://127.0.0.1:8790"); p.add_argument("--feed", default="http://127.0.0.1:8798")
    p.add_argument("--gris", type=int, default=200, help="gris de las teclas proyectadas (255 = blanco; la Osmo se satura con blanco)")
    p.add_argument("--latencia", type=float, default=1.8, help="segundos entre apagar la proyección y tomar el cuadro limpio")
    p.add_argument("--ejes", choices=("max", "lienzo"), default="max", help="flechas en la vista de Max (rot 180) o en px del lienzo")
    p.add_argument("--sin-lazo", action="store_true", help="no aplicar lazo_corr.npz")
    p.add_argument("--quad", default=None, help="8 números TL,TR,BR,BL (lienzo) para partir de ahí en vez de teclado.json (solo queda si se guarda)")
    p.add_argument("--dry", action="store_true", help="sin proyectar ni ventana: escribe teclado_manual_dry.png y _dry_crop.jpg")
    p.add_argument("--teclas", default="", help="(--dry) teclas a aplicar antes de renderizar, p. ej. '2LL0jj'")
    p.add_argument("--img", default=None, help="(--dry) imagen del lienzo para el recorte de control (por defecto teclado_ref.jpg)")
    a = p.parse_args(argv)
    scan = Path(a.scan)
    st = Ajuste(scan, a)

    if a.dry:
        for ch in a.teclas:
            st.tecla(ord(ch))
        cv2.imwrite(str(scan / "teclado_manual_dry.png"), st.imagen_proyector())
        img = st.cuadro_limpio()
        if img is not None:
            st.fondo = img
            cv2.imwrite(str(scan / "teclado_manual_dry_crop.jpg"), st.recorte_control(img))
        cv2.imwrite(str(scan / "teclado_manual_dry_ventana.jpg"), st.ventana())
        print(json.dumps({"dry": str(scan / "teclado_manual_dry.png"), "crop": str(scan / "teclado_manual_dry_crop.jpg"),
                          "ventana": str(scan / "teclado_manual_dry_ventana.jpg"), "quad": st.q.round(1).tolist(), "inset": st.inset,
                          "lazo": st.corr is not None}))
        return 0

    if not pc.runtime_alive(a.runtime):
        raise SystemExit(f"el runtime del proyector no responde en {a.runtime}: no proyecto nada (no lo levanto yo)")

    def _term(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _term)
    rojo_antes = st.rojo_actual()
    name = "teclado manual (s guardar, q guardar y salir, x salir)"
    salida = ""
    try:
        print("apago /rojo del feed y tomo un cuadro limpio del teclado (~2 s)…", flush=True)
        st.rojo(False); time.sleep(0.4); st.apagar()                     # el feed pudo mandar un último cuadro
        st.fondo = st.cuadro_limpio(reproyectar=False)
        if st.fondo is None:
            st.msg = "sin cuadro limpio del feed: la ventanita va sin cámara"
        st.proyectar()
        cv2.namedWindow(name, cv2.WINDOW_AUTOSIZE); cv2.moveWindow(name, 80, 80)
        st.refrescar = lambda: (cv2.imshow(name, st.ventana()), cv2.waitKey(1))
        while True:
            cv2.imshow(name, st.ventana())
            k = cv2.waitKeyEx(50)
            if k == -1:
                continue
            teclas = [k]
            while len(teclas) < 60:                                           # junta la repetición de teclas: 1 proyección
                k2 = cv2.waitKeyEx(1)
                if k2 == -1:
                    break
                teclas.append(k2)
            cambio = False
            for kk in teclas:
                r = st.tecla(kk)
                if r in ("salir", "salir_sin"):
                    salida = r; break
                cambio = cambio or r == "mover"
            if salida:
                break
            if cambio:
                st.proyectar()
        if salida == "salir":
            st.guardar()
        else:
            print("salí sin guardar", flush=True)
    finally:
        cv2.destroyAllWindows()
        st.apagar()
        if rojo_antes is not False:
            st.rojo(True)
        else:
            print("el feed tenía /rojo apagado al empezar: lo dejo apagado", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

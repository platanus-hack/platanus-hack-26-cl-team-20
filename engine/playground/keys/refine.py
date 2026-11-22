"""Afinar tecla por tecla con capturas: proyectar el relleno de cada tecla corrido, sacar captura con las dos cámaras
(Osmo arriba, Brio en la pared), medir cuánto coincide lo iluminado con la tecla real y quedarse con el mejor corrimiento.

Pedido de Max (16-oct 03:16): "que vaya tecla por tecla: mover sus cuadrantes, sacar captura, mover cuadrante, sacar
captura, con ambas cámaras, hasta asegurar que quede perfecto, y asegurar un fondo de buen contraste".

    python -m playground.keys.refine captures/scan5                    # en vivo (runtime con overlay si está corriendo)
    python -m playground.keys.refine captures/scan5 --sin-escribir     # mide y reporta, no toca keys.json
    python -m playground.keys.refine captures/scan5 --solo-osmo

Cómo mide (todo en píxeles del proyector, con el mapa proyector→cámara de cada cámara):
1. Fondo de contraste: checker fino azul/oscuro fuera de la banda de teclas; adentro, negro. Captura de referencia D.
2. Banda blanca → captura W. ``W - D`` llevado al proyector = cómo refleja cada punto: blancas claras, negras y
   ranuras oscuras. La silueta real de cada tecla sale de ahí (blanca: componente clara entre ranuras alrededor de su
   punto de toque; negra: componente oscura en su lugar).
3. Relleno de teclas separadas (cada 4ª) corrido (dx, dy) → captura F. Lo iluminado = ``(F - D) / (W - D) > 0,5``
   (normalizado por la reflectancia: sirve igual en negras). IoU por tecla contra su silueta, promedio de las cámaras
   que la ven.
4. Corrimiento global del teclado: grilla gruesa (±12 px, paso 4) y fina (paso 1 alrededor del mejor); es
   concluyente si el óptimo no queda en el borde, el IoU pasa 0,5 y las dos cámaras coinciden (si no, no se escribe
   nada). Luego dx por tecla (−6..6: paso 2 y después ±1) en las negras con IoU < 0,8, de a 4 separadas por captura; cada blanca sigue a sus negras vecinas (la ranura entre
   blancas casi no se ve en cámara: su posición lateral sola no se mide bien). Medición final de todas.
Escribe ``keys.json`` (polígonos corridos y ``ajuste`` por tecla; respaldo en ``keys_antes_refine.json``) y
``keys_refine.json`` (IoU antes/después por tecla y por cámara, grilla global). Capturas privadas: no se guardan salvo
``--guardar`` (en reports/_keys_refine/, borrar después).
"""

from __future__ import annotations

import argparse
import json
import shutil
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

W, H = 1920, 1080
CHECKER_A, CHECKER_B = (200, 120, 0), (40, 20, 0)        # BGR: azul / azul muy oscuro, celdas de 6 px
PAD = 8                                                  # px alrededor de cada tecla para buscar su silueta


# ---------------------------------------------------------------------------------------------- geometría

def forma(t: dict) -> np.ndarray:
    """Polígono que se ilumina: blancas con sus muescas (contorno), negras su polígono."""
    return np.asarray(t["contorno"] if t["color"] == "blanca" and t.get("contorno") else t["poligono"], np.float64)


def correr(t: dict, dx: float, dy: float) -> dict:
    """La tecla corrida (dx, dy) px: todos sus puntos."""
    d = np.array([dx, dy], float)
    out = dict(t)
    for k in ("poligono", "contorno", "cuadrante"):
        if t.get(k):
            out[k] = np.round(np.asarray(t[k], float) + d, 2).tolist()
    for k in ("centro", "punto_toque"):
        if t.get(k):
            out[k] = np.round(np.asarray(t[k], float) + d, 2).tolist()
    if t.get("bbox"):
        b = list(t["bbox"]); out["bbox"] = [round(b[0] + dx, 2), round(b[1] + dy, 2), b[2], b[3]]
    return out


def _fill(mask: np.ndarray, pts, off=(0, 0), val=255) -> None:
    p = np.round((np.asarray(pts, float) - off) * 4).astype(np.int32).reshape(-1, 1, 2)
    cv2.fillPoly(mask, [p], val, cv2.LINE_8, 2)


@dataclass
class ROI:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def shape(self):
        return (self.y1 - self.y0, self.x1 - self.x0)

    @property
    def off(self):
        return (self.x0, self.y0)


def roi_teclas(teclas: list[dict], margen: int = 24) -> ROI:
    P = np.concatenate([np.asarray(t["poligono"], float) for t in teclas])
    x0, y0 = np.floor(P.min(axis=0)).astype(int) - margen; x1, y1 = np.ceil(P.max(axis=0)).astype(int) + margen
    return ROI(max(0, x0), max(0, y0), min(W, x1), min(H, y1))


def banda(teclas: list[dict], pad: int = 12) -> np.ndarray:
    m = np.zeros((H, W), np.uint8)
    for t in teclas:
        _fill(m, t["poligono"])
    return cv2.dilate(m, np.ones((2 * pad + 1, 2 * pad + 1), np.uint8))


def fondo(band: np.ndarray) -> np.ndarray:
    """Checker fino azul/oscuro fuera de la banda (contraste para ver los límites), negro adentro."""
    yy, xx = np.indices((H, W)); c = ((yy // 6 + xx // 6) % 2).astype(bool)
    out = np.zeros((H, W, 3), np.uint8); out[c] = CHECKER_A; out[~c] = CHECKER_B
    out[band > 0] = 0
    return out


def lienzo(base: np.ndarray, teclas: list[dict], corr: dict[int, tuple[float, float]]) -> np.ndarray:
    """Fondo + relleno blanco de las teclas pedidas, cada una con su corrimiento."""
    img = base.copy()
    for t in teclas:
        if t["nota"] in corr:
            dx, dy = corr[t["nota"]]
            m = np.zeros((H, W), np.uint8)
            _fill(m, forma(t) + [dx, dy])
            img[m > 0] = 255
    return img


# ---------------------------------------------------------------------------------------------- cámaras y medición

@dataclass
class CamMap:
    name: str
    mapx: np.ndarray          # recortados a la ROI del proyector
    mapy: np.ndarray
    valid: np.ndarray

    @classmethod
    def load(cls, name: str, npz: Path, roi: ROI) -> "CamMap":
        z = np.load(npz)
        s = (slice(roi.y0, roi.y1), slice(roi.x0, roi.x1))
        return cls(name, z["mapx"][s].astype(np.float32), z["mapy"][s].astype(np.float32), z["valid"][s].astype(bool))

    def al_proyector(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Cuadro de cámara (gris float) → ROI del proyector; y la máscara de píxeles que la cámara ve."""
        g = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        g = g.astype(np.float32)
        out = cv2.remap(g, self.mapx, self.mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=-1)
        ok = self.valid & (out >= 0)
        return np.where(ok, out, 0).astype(np.float32), ok


def _eje_u(teclas: list[dict]) -> np.ndarray:
    bl = [t for t in teclas if t["color"] == "blanca"]
    d = np.asarray(bl[-1]["poligono"][1], float) - np.asarray(bl[0]["poligono"][0], float)
    return d / (np.linalg.norm(d) + 1e-9)


def _correr_img(img: np.ndarray, v) -> np.ndarray:
    M = np.float32([[1, 0, v[0]], [0, 1, v[1]]])
    return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def regiones(teclas: list[dict], roi: ROI, pad: int = PAD + 7) -> dict[int, np.ndarray]:
    """Zona de cada tecla donde se comparan su silueta y lo iluminado (la misma para las dos: así el corte no sesga).

    Negras: hacia los lados y hacia el frente `pad` px, no hacia atrás (atrás está el cuerpo del teclado, tan oscuro
    como la negra: no hay borde que medir). Blancas: solo 3 px hacia los lados y `pad` hacia atrás y adelante. A los
    lados de una blanca hay otra blanca y la ranura entre ellas casi no se ve en cámara, así que su posición lateral la
    dan las muescas de sus negras (dentro de la zona) y la de frente/fondo, sus cantos."""
    eu = _eje_u(teclas)
    out = {}
    for t in teclas:
        reg = np.zeros(roi.shape, np.uint8)
        P = np.asarray(t["poligono"], float)
        fw = P[3] - P[0]; fw = fw / (np.linalg.norm(fw) + 1e-9)
        if t["color"] == "blanca":
            for a in (-3.0, 0.0, 3.0):
                for b in np.linspace(-pad, pad, 7):
                    _fill(reg, forma(t) + a * eu + b * fw, roi.off)
        else:
            for a in np.linspace(-pad, pad, 9):
                for b in np.linspace(0, pad, 4):
                    _fill(reg, P + a * eu + b * fw, roi.off)
        out[t["nota"]] = reg > 0
    return out


def siluetas(refl: np.ndarray, ok: np.ndarray, teclas: list[dict], roi: ROI,
             reg_teclas: dict[int, np.ndarray] | None = None) -> dict[int, np.ndarray]:
    """Silueta real de cada tecla (máscara en la ROI) desde la reflectancia W−D llevada al proyector."""
    band = np.zeros(roi.shape, np.uint8)
    for t in teclas:
        _fill(band, t["poligono"], roi.off)
    vals = refl[(band > 0) & ok]
    if vals.size < 50:
        return {}
    lo, hi = np.percentile(vals, 5), np.percentile(vals, 95)
    n = np.clip((refl - lo) / max(hi - lo, 1e-6), 0, 1)
    eu = _eje_u(teclas)
    vecinos = np.minimum(_correr_img(n, 3 * eu), _correr_img(n, -3 * eu))
    ranura = n < vecinos - 0.06                         # valle angosto a lo largo del teclado = ranura entre blancas
    clara = (n > 0.5) & ~ranura & ok
    oscura = (n < 0.4) & ok
    reg_teclas = reg_teclas or regiones(teclas, roi)
    out = {}
    for t in teclas:
        poly = np.zeros(roi.shape, np.uint8); _fill(poly, forma(t), roi.off)
        poly = poly > 0
        reg = reg_teclas[t["nota"]]
        if t["color"] == "blanca":                      # lo claro de su zona (las muescas oscuras marcan la posición)
            out[t["nota"]] = clara & reg
            continue
        cand = oscura & reg
        nlab, lab = cv2.connectedComponents(cand.astype(np.uint8), connectivity=4)
        if nlab <= 1:
            out[t["nota"]] = poly & ok; continue
        semilla = np.asarray(t.get("punto_toque") or t["centro"], float) - roi.off
        sx, sy = int(round(semilla[0])), int(round(semilla[1]))
        i = lab[sy, sx] if 0 <= sy < lab.shape[0] and 0 <= sx < lab.shape[1] else 0
        if i == 0:                                      # la semilla cayó en una ranura: la componente que más se solapa
            ov = np.bincount(lab[poly].ravel(), minlength=nlab); ov[0] = 0
            i = int(np.argmax(ov))
        out[t["nota"]] = (lab == i) if i else (poly & ok)
    return out


def iluminado(f_proj: np.ndarray, d_proj: np.ndarray, ok: np.ndarray, zona: np.ndarray, minimo: float = 4.0) -> np.ndarray:
    """Lo que el relleno iluminó dentro de `zona`: F−D sobre la mitad de su nivel alto en esa zona (media altura, sin
    sesgo con el desenfoque). No se normaliza por W−D: en el borde de una negra W trae la luz de las blancas vecinas
    (desenfoque de la cámara) y la negra iluminada parecería más angosta."""
    v = (f_proj - d_proj)
    sel = v[zona & ok]
    if sel.size < 10:
        return np.zeros_like(zona)
    alto = float(np.percentile(sel, 95))
    if alto < minimo:
        return np.zeros_like(zona)
    lit = (v > 0.5 * alto) & zona & ok
    return cv2.morphologyEx(lit.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)) > 0


def iou(a: np.ndarray, b: np.ndarray, ok: np.ndarray | None = None) -> float:
    if ok is not None:
        a = a & ok; b = b & ok
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum()) / float(u) if u else 0.0


# ---------------------------------------------------------------------------------------------- salida y cámaras

class OverlayWindow:
    """Con el runtime proyectando, ninguna otra ventana llega al proyector: se le pide mostrar la imagen con el comando
    overlay (misma interfaz que PatternWindow: show/close)."""

    def __init__(self, url: str = "http://127.0.0.1:8790", tmp: Path = Path("reports/_keys_refine")):
        self.url = url.rstrip("/"); self.tmp = tmp; tmp.mkdir(parents=True, exist_ok=True); self.n = 0

    @staticmethod
    def estado(url: str = "http://127.0.0.1:8790") -> str:
        """'apagado' (no hay runtime: ventana propia), 'overlay' (proyectar por él) o 'sin_overlay' (proyecta pero no
        tiene el comando: una ventana propia quedaría tapada). Se prueba pidiendo un overlay de un archivo que no existe:
        no cambia nada y la respuesta dice si el comando existe (el SCHEMA puede no listarlo)."""
        import urllib.error
        url = url.rstrip("/")
        try:
            with urllib.request.urlopen(url + "/api/schema", timeout=2) as r:
                r.read()
        except Exception:  # noqa: BLE001
            return "apagado"
        body = json.dumps({"cmd": "overlay", "args": {"path": "/nonexistent/_probe.png", "seconds": 0}}).encode()
        req = urllib.request.Request(url + "/api/command", body, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                txt = r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            txt = e.read().decode(errors="replace")
        except Exception:  # noqa: BLE001
            return "apagado"
        return "sin_overlay" if "desconocido" in txt else "overlay"

    def _cmd(self, args: dict) -> None:
        body = json.dumps({"cmd": "overlay", "args": args}).encode()
        req = urllib.request.Request(self.url + "/api/command", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=4) as r:
            r.read()

    def show(self, img: np.ndarray, seconds: float = 30.0) -> None:
        self.n += 1
        p = (self.tmp / f"_overlay_{self.n % 2}.png").resolve()
        cv2.imwrite(str(p), img)
        self._cmd({"path": str(p), "seconds": seconds})

    def close(self) -> None:
        try:
            self._cmd({"off": True})
        except Exception:  # noqa: BLE001
            pass


class Grabber:
    """Lee una cámara sin parar en un hilo y guarda el último cuadro con su hora. Así no se acumulan cuadros viejos en
    el búfer de AVFoundation: con lecturas espaciadas, `flush` + `read` devolvía un cuadro de antes del cambio de
    proyección (pasó en la primera corrida en vivo: W salía igual a D)."""

    def __init__(self, cam):
        self.cam = cam
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.frame = None
        self.t = 0.0
        self.error = ""
        self.running = True
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.th.start()

    def _loop(self):
        while self.running:
            try:
                f = self.cam.read()
            except Exception as e:  # noqa: BLE001
                self.error = str(e); time.sleep(0.2); continue
            with self.cond:
                self.frame, self.t = f, time.time()
                self.cond.notify_all()

    def despues_de(self, t_min: float, timeout: float = 5.0) -> np.ndarray | None:
        """El primer cuadro leído después de `t_min` (hora de pared)."""
        fin = time.time() + timeout
        with self.cond:
            while self.t < t_min:
                resto = fin - time.time()
                if resto <= 0:
                    return None
                self.cond.wait(resto)
            return self.frame

    def close(self):
        self.running = False
        self.th.join(timeout=2.0)
        try:
            self.cam.close()
        except Exception:  # noqa: BLE001
            pass


class LiveRig:
    """Salida al proyector (overlay del runtime o ventana propia) + cámaras; `capturar` devuelve {cámara: cuadro} con
    cuadros tomados `settle` s después de mostrar la imagen (latencia overlay→cámara medida: ~0,27 s; la Osmo además
    ajusta la exposición ~0,5 s)."""

    def __init__(self, cams: dict, out, settle: float = 1.0):
        self.grabbers = {n: Grabber(c) for n, c in cams.items()}
        self.out = out; self.settle = settle
        self.n = 0

    def capturar(self, img: np.ndarray) -> dict[str, np.ndarray]:
        self.out.show(img)
        t_min = time.time() + self.settle
        res: dict[str, np.ndarray] = {}
        for nombre, g in self.grabbers.items():
            f = g.despues_de(t_min)
            if f is None:
                print(f"cámara {nombre}: sin cuadro nuevo ({g.error or 'timeout'})")
            else:
                res[nombre] = f
        self.n += 1
        return res

    def close(self):
        try:
            self.out.close()
        finally:
            for g in self.grabbers.values():
                g.close()


# ---------------------------------------------------------------------------------------------- algoritmo

def grupos_separados(idx: list[int], por_grupo: int = 4, sep: int = 6) -> list[list[int]]:
    """Reparte índices de tecla en grupos de hasta `por_grupo` con al menos `sep` teclas de distancia entre sí."""
    grupos: list[list[int]] = []
    for i in sorted(idx):
        for g in grupos:
            if len(g) < por_grupo and all(abs(i - j) >= sep for j in g):
                g.append(i); break
        else:
            grupos.append([i])
    return grupos


class Refiner:
    def __init__(self, teclas: list[dict], maps: dict[str, CamMap], rig, roi: ROI, log=print, min_valid: float = 0.5):
        self.teclas = teclas
        self.maps = maps
        self.rig = rig
        self.roi = roi
        self.log = log
        self.min_valid = min_valid
        self.band = banda(teclas)
        self.base = fondo(self.band)
        self.capturas = 0

    def _proyectar(self, corr: dict[int, tuple[float, float]]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        frames = self.rig.capturar(lienzo(self.base, self.teclas, corr))
        self.capturas += 1
        return {c: self.maps[c].al_proyector(f) for c, f in frames.items() if c in self.maps}

    def referencias(self) -> None:
        """D (solo fondo) y W (banda blanca) por cámara, y las siluetas reales de cada tecla."""
        self.D = self._proyectar({})
        wimg = self.base.copy(); wimg[self.band > 0] = 255
        frames = self.rig.capturar(wimg); self.capturas += 1
        self.W = {c: self.maps[c].al_proyector(f) for c, f in frames.items() if c in self.maps}
        self.sil: dict[str, dict[int, np.ndarray]] = {}
        self.region = regiones(self.teclas, self.roi)
        for c in self.W:
            if c not in self.D:
                continue
            w, okw = self.W[c]; d, okd = self.D[c]
            self.sil[c] = siluetas(w - d, okw & okd, self.teclas, self.roi, self.region)

    def medir(self, corr: dict[int, tuple[float, float]]) -> dict[int, dict[str, float]]:
        """Proyecta las teclas de `corr` corridas y devuelve IoU por tecla y por cámara."""
        F = self._proyectar(corr)
        out: dict[int, dict[str, float]] = {m: {} for m in corr}
        for c, (f, okf) in F.items():
            if c not in self.D or c not in self.W or c not in self.sil:
                continue
            d, okd = self.D[c]; w, okw = self.W[c]
            ok = okf & okd & okw
            for m in corr:
                reg = self.region[m]
                if ok[reg].mean() < self.min_valid or m not in self.sil[c]:
                    continue
                lit = iluminado(f, d, ok, reg)
                out[m][c] = iou(lit, self.sil[c][m] & reg, ok)
        return out

    def depurar(self, carpeta: Path, corr: dict[int, tuple[float, float]] | None = None, nombre: str = "lit") -> None:
        """Imágenes (ROI del proyector, por cámara): reflectancia W−D con siluetas (verde blancas, rojo negras) y los
        polígonos del modelo (azul); y si `corr`, lo iluminado por esa captura (amarillo)."""
        carpeta.mkdir(parents=True, exist_ok=True)
        F = self._proyectar(corr) if corr else {}
        for c in self.W:
            if c not in self.D or c not in self.sil:
                continue
            w, okw = self.W[c]; d, okd = self.D[c]
            r = w - d
            hi = max(float(np.percentile(r[okw & okd], 99)), 1.0)
            vis = cv2.cvtColor(np.clip(r / hi * 255, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
            for t in self.teclas:
                s_ = self.sil[c].get(t["nota"])
                if s_ is None:
                    continue
                cnts, _ = cv2.findContours(s_.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                cv2.drawContours(vis, cnts, -1, (0, 220, 0) if t["color"] == "blanca" else (0, 0, 255), 1)
                p_ = np.round((forma(t) - self.roi.off) * 4).astype(np.int32)
                cv2.polylines(vis, [p_], True, (255, 120, 0), 1, cv2.LINE_AA, 2)
            if c in F:
                f, okf = F[c]
                for m in corr:
                    lit = iluminado(f, d, okf & okd & okw, self.region[m])
                    vis[lit] = (0.5 * vis[lit] + 0.5 * np.array([0, 255, 255])).astype(np.uint8)
            cv2.imwrite(str(carpeta / f"{nombre}_{c}.png"), cv2.resize(vis, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))

    @staticmethod
    def combinar(por_cam: dict[str, float]) -> float | None:
        return float(np.mean(list(por_cam.values()))) if por_cam else None

    def medir_todas(self, corr_fn) -> dict[int, dict[str, float]]:
        """Todas las teclas, de a una de cada 4 por captura (no se tocan entre sí)."""
        res: dict[int, dict[str, float]] = {}
        notas = [t["nota"] for t in self.teclas]
        for r in range(4):
            sel = {m: corr_fn(m) for i, m in enumerate(notas) if i % 4 == r}
            res.update(self.medir(sel))
        return res

    def correr(self, rango: int = 6, paso: int = 2, umbral: float = 0.8, por_grupo: int = 4, sep: int = 6,
               solo_global: bool = False, rango_tecla: int | None = None, depurar: Path | None = None) -> dict:
        t0 = time.time()
        notas = [t["nota"] for t in self.teclas]
        rango_tecla = rango if rango_tecla is None else rango_tecla
        self.referencias()
        if depurar is not None:
            self.depurar(depurar, {m: (0.0, 0.0) for i, m in enumerate(notas) if i % 4 == 0}, "antes")
        antes = self.medir_todas(lambda m: (0.0, 0.0))
        self.log(f"antes: IoU media {np.nanmean([self.combinar(v) or np.nan for v in antes.values()]):.3f}")
        # 1) global con una de cada 4 teclas: grilla gruesa (paso) en ±rango y fina (paso 1) alrededor del mejor
        sel = [m for i, m in enumerate(notas) if i % 4 == 0]
        grilla = []
        por_cam: dict[str, dict[tuple[int, int], float]] = {}
        hechos: dict[tuple[int, int], float] = {}

        def probar(dx: int, dy: int) -> float:
            if (dx, dy) in hechos:
                return hechos[(dx, dy)]
            r = self.medir({m: (dx, dy) for m in sel})
            vals = [self.combinar(v) for v in r.values() if self.combinar(v) is not None]
            s_ = float(np.mean(vals)) if vals else -1.0
            for v in r.values():
                for c, x in v.items():
                    por_cam.setdefault(c, {}).setdefault((dx, dy), [])
                    por_cam[c][(dx, dy)].append(x)
            hechos[(dx, dy)] = s_
            grilla.append({"dx": dx, "dy": dy, "iou": round(s_, 4)})
            return s_

        def mejor_de(cands):
            return max(cands, key=lambda d: (hechos[d], -(abs(d[0]) + abs(d[1]))))

        gruesa = [(dx, dy) for dy in range(-rango, rango + 1, paso) for dx in range(-rango, rango + 1, paso)]
        for d in gruesa:
            probar(*d)
        b0 = mejor_de(gruesa)
        h = max(1, paso // 2)
        fina = [(b0[0] + i, b0[1] + j) for j in range(-h, h + 1) for i in range(-h, h + 1)]
        for d in fina:
            probar(*d)
        gdx, gdy = mejor_de(list(hechos))
        best = (hechos[(gdx, gdy)], gdx, gdy)
        # veredicto: el mejor no puede quedar en el borde de la grilla, el IoU tiene que ser razonable y las cámaras
        # tienen que estar de acuerdo (cada una por su cuenta, en la grilla gruesa)
        cam_best = {c: max(((k, float(np.mean(v))) for k, v in d.items() if k in gruesa), key=lambda kv: kv[1])[0]
                    for c, d in por_cam.items()}
        borde = abs(b0[0]) >= rango or abs(b0[1]) >= rango
        acuerdo = all(abs(cb[0] - b0[0]) <= paso and abs(cb[1] - b0[1]) <= paso for cb in cam_best.values())
        concluyente = (not borde) and best[0] >= 0.5 and acuerdo
        motivos = [m_ for m_, ok_ in (("óptimo en el borde de la grilla", not borde), (f"IoU global {best[0]:.2f} < 0,5", best[0] >= 0.5),
                                      (f"cámaras en desacuerdo {cam_best}", acuerdo)) if not ok_]
        self.log(f"global: dx {gdx:+d} dy {gdy:+d} (IoU {best[0]:.3f}) · por cámara {cam_best} · "
                 f"{'concluyente' if concluyente else 'NO concluyente: ' + '; '.join(motivos)}")
        if depurar is not None:
            self.depurar(depurar, {m: (gdx, gdy) for m in sel}, "global")
        tras_global = self.medir_todas(lambda m: (gdx, gdy))
        # 2) dx por tecla donde no alcanza el umbral. Se buscan las negras (su silueta es nítida: oscuras entre
        #    blancas); cada blanca sigue a sus negras vecinas, porque la ranura entre dos blancas casi no se ve en cámara
        #    y su posición lateral sola no se puede medir bien (las blancas se mueven con el teclado, no solas).
        kdx = {m: 0 for m in notas}
        color = {t["nota"]: t["color"] for t in self.teclas}
        flojas = [i for i, m in enumerate(notas)
                  if color[m] == "negra" and (self.combinar(tras_global.get(m, {})) or 0.0) < umbral]
        if not solo_global and flojas:
            grupos = grupos_separados(flojas, por_grupo, sep)
            self.log(f"{len(flojas)} teclas bajo {umbral}: {len(grupos)} grupos")
            for g in grupos:
                ms = [notas[i] for i in g]
                mejor = {m: (self.combinar(tras_global.get(m, {})) or 0.0, 0) for m in ms}
                for dx in range(-rango_tecla, rango_tecla + 1, 2):
                    if dx == 0:
                        continue
                    r = self.medir({m: (gdx + dx, gdy) for m in ms})
                    for m in ms:
                        s = self.combinar(r.get(m, {}))
                        if s is not None and s > mejor[m][0] + 1e-9:
                            mejor[m] = (s, dx)
                if True:                             # afinar ±1 alrededor del mejor de cada una
                    for sgn in (-1, 1):
                        cand = {m: mejor[m][1] + sgn for m in ms if abs(mejor[m][1] + sgn) <= rango_tecla}
                        if not cand:
                            continue
                        r = self.medir({m: (gdx + cand[m], gdy) for m in cand})
                        for m, dx in cand.items():
                            s = self.combinar(r.get(m, {}))
                            if s is not None and s > mejor[m][0] + 1e-9:
                                mejor[m] = (s, dx)
                for m in ms:
                    kdx[m] = mejor[m][1]
            for m in notas:
                if color[m] == "blanca":
                    vec = [kdx[n] for n in (m - 1, m + 1) if color.get(n) == "negra"]
                    kdx[m] = int(round(float(np.mean(vec)))) if vec else 0
        despues = self.medir_todas(lambda m: (gdx + kdx[m], gdy))
        ajustes = {}
        for m in notas:
            a = self.combinar(antes.get(m, {})); b = self.combinar(despues.get(m, {}))
            ajustes[m] = {"dx": gdx + kdx[m], "dy": gdy, "global": [gdx, gdy], "tecla_dx": kdx[m],
                          "iou_antes": None if a is None else round(a, 4), "iou_despues": None if b is None else round(b, 4),
                          "camaras_antes": {c: round(v, 4) for c, v in antes.get(m, {}).items()},
                          "camaras_despues": {c: round(v, 4) for c, v in despues.get(m, {}).items()}}
        media = lambda d: float(np.nanmean([self.combinar(v) if self.combinar(v) is not None else np.nan for v in d.values()]))
        return {"global": {"dx": gdx, "dy": gdy, "iou": round(best[0], 4), "por_camara": {c: list(v) for c, v in cam_best.items()}},
                "concluyente": concluyente, "motivos": motivos, "grilla": grilla, "ajustes": ajustes,
                "iou_media_antes": round(media(antes), 4), "iou_media_despues": round(media(despues), 4),
                "teclas_afinadas": int(sum(1 for m in notas if kdx[m])), "capturas": self.capturas,
                "segundos": round(time.time() - t0, 1), "camaras": sorted(self.maps)}


def aplicar(keys: dict, rep: dict) -> dict:
    """keys.json con cada tecla corrida según su ajuste (y el ajuste guardado en la tecla)."""
    out = dict(keys)
    teclas = []
    for t in keys["teclas"]:
        a = rep["ajustes"].get(t["nota"]) or rep["ajustes"].get(str(t["nota"]))
        if a is None:
            teclas.append(t); continue
        nt = correr(t, a["dx"], a["dy"])
        nt["ajuste"] = {k: a[k] for k in ("dx", "dy", "global", "tecla_dx", "iou_antes", "iou_despues")}
        teclas.append(nt)
    out["teclas"] = teclas
    out["ajuste_refine"] = {k: rep[k] for k in ("global", "iou_media_antes", "iou_media_despues", "teclas_afinadas",
                                                 "capturas", "camaras")}
    out["ajuste_refine"]["fecha"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return out


# ---------------------------------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m playground.keys.refine", description=__doc__.splitlines()[0])
    ap.add_argument("scan", nargs="?", default="captures/scan5")
    ap.add_argument("--brio-map", default="", help="proj_to_cam.npz de la Brio (por defecto <scan>_brio/proj_to_cam.npz)")
    ap.add_argument("--brio-url", default="http://192.168.1.233:8794")
    ap.add_argument("--solo-osmo", action="store_true")
    ap.add_argument("--solo-brio", action="store_true")
    ap.add_argument("--rotate", type=int, default=270)
    ap.add_argument("--rango", type=int, default=12, help="corrimiento global máximo (px)")
    ap.add_argument("--paso", type=int, default=4, help="paso de la grilla gruesa global; después ±paso/2 de a 1 px")
    ap.add_argument("--rango-tecla", type=int, default=6, help="dx máximo por tecla (px)")
    ap.add_argument("--debug", action="store_true", help="solo referencias y una medición, con imágenes en reports/_keys_refine")
    ap.add_argument("--forzar", action="store_true", help="escribir keys.json aunque el resultado no sea concluyente")
    ap.add_argument("--umbral", type=float, default=0.8)
    ap.add_argument("--por-captura", type=int, default=4)
    ap.add_argument("--settle", type=float, default=1.0, help="espera tras cambiar la proyección (s)")
    ap.add_argument("--solo-global", action="store_true")
    ap.add_argument("--sin-escribir", action="store_true", help="no tocar keys.json (solo el reporte)")
    ap.add_argument("--runtime", default="http://127.0.0.1:8790")
    ap.add_argument("--guardar", action="store_true", help="dejar las capturas en reports/_keys_refine (privadas)")
    a = ap.parse_args(argv)

    scan = Path(a.scan)
    keys = json.loads((scan / "keys.json").read_text())
    teclas = [t for t in keys["teclas"] if t.get("estado") != "fuera"]
    roi = roi_teclas(teclas)
    maps: dict[str, CamMap] = {}
    cams = {}
    from slscan.capture import Camera, PatternWindow, RemoteCamera
    if not a.solo_brio:
        maps["osmo"] = CamMap.load("osmo", scan / "proj_to_cam.npz", roi)
        cams["osmo"] = Camera("osmo", size="3840x2160", rotate=a.rotate, downscale=2)
    if not a.solo_osmo:
        bm = Path(a.brio_map) if a.brio_map else scan.parent / f"{scan.name}_brio" / "proj_to_cam.npz"
        if bm.exists():
            try:
                cams["brio"] = RemoteCamera(a.brio_url, name="brio", lossless=False)
                maps["brio"] = CamMap.load("brio", bm, roi)
            except Exception as e:  # noqa: BLE001
                print(f"Brio no disponible ({e}): sigo con la Osmo")
        else:
            print(f"sin mapa de la Brio en {bm}: sigo con la Osmo")
    if not maps:
        raise SystemExit("sin cámaras")
    est = OverlayWindow.estado(a.runtime)
    if est == "sin_overlay":
        raise SystemExit(f"el runtime en {a.runtime} está corriendo pero no tiene el comando overlay: una ventana propia "
                         "quedaría debajo de la suya. Actualiza el runtime (overlay) o páralo y vuelve a correr.")
    out = OverlayWindow(a.runtime) if est == "overlay" else PatternWindow((W, H), screen="1920x1080")
    print("salida:", "overlay del runtime" if isinstance(out, OverlayWindow) else "ventana propia", "· cámaras:", sorted(maps))
    rig = LiveRig(cams, out, settle=a.settle)
    dbg = Path("reports/_keys_refine") if (a.guardar or a.debug) else None
    try:
        R = Refiner(teclas, maps, rig, roi)
        if a.debug:
            R.referencias()
            R.depurar(dbg, {t["nota"]: (0.0, 0.0) for i, t in enumerate(teclas) if i % 4 == 0}, "antes")
            print(f"imágenes de depuración en {dbg} (privadas: borrar después)")
            return 0
        rep = R.correr(a.rango, a.paso, a.umbral, a.por_captura, solo_global=a.solo_global, rango_tecla=a.rango_tecla,
                       depurar=dbg)
    finally:
        rig.close()
        if not (a.guardar or a.debug):
            shutil.rmtree("reports/_keys_refine", ignore_errors=True)
    rep["scan"] = str(scan)
    (scan / "keys_refine.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print(f"IoU media {rep['iou_media_antes']} → {rep['iou_media_despues']} · global {rep['global']} · "
          f"{rep['teclas_afinadas']} teclas con dx propio · {rep['capturas']} capturas en {rep['segundos']} s")
    if not rep["concluyente"]:
        print("resultado NO concluyente:", "; ".join(rep["motivos"]), "→ no toco keys.json" + (" (--forzar lo escribe igual)" if not a.forzar else ""))
    if not a.sin_escribir and (rep["concluyente"] or a.forzar):
        shutil.copy(scan / "keys.json", scan / "keys_antes_refine.json")
        (scan / "keys.json").write_text(json.dumps(aplicar(keys, rep), ensure_ascii=False, indent=1))
        print(f"→ {scan / 'keys.json'} (respaldo en keys_antes_refine.json) y {scan / 'keys_refine.json'}")
        aj = scan / "ajustes.json"                     # el ajuste fino a mano del teclado ya quedó medido: no sumarlo dos veces
        if aj.exists():
            try:
                d = json.loads(aj.read_text())
                if "keys" in d and any(abs(float(v)) > 1e-9 for v in d["keys"]):
                    print(f"ajustes.json: keys {d['keys']} → [0, 0] (ya incluido en la corrección medida)")
                    d["keys"] = [0.0, 0.0]
                    aj.write_text(json.dumps(d, indent=1))
            except (ValueError, OSError) as e:
                print("ajustes.json:", e)
        try:                                           # que el runtime relea la escena (si lo soporta)
            body = json.dumps({"cmd": "reload_scene", "args": {}}).encode()
            urllib.request.urlopen(urllib.request.Request(a.runtime.rstrip("/") + "/api/command", body,
                                                          {"Content-Type": "application/json"}), timeout=4).read()
            print("runtime: escena recargada")
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

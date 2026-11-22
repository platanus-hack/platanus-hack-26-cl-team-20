"""El cuadrilátero blanco de las teclas: sus 4 esquinas desde las aristas, y las 61 teclas por homografía hacia él.

Pedido de Max (16-oct, por voz): "las teclas, en la foto, son un cuadrilátero blanco; primero hay que detectar las
esquinas; la esquina de abajo está desalineada y eso rompe todo; hay bleed (la luz se desborda fuera de las teclas)".

    python -m playground.keys.quad captures/scan5                      # detecta y compara con keys.json (no escribe)
    python -m playground.keys.quad captures/scan5 --verificar           # + overlay: bleed antes/después con la Osmo
    python -m playground.keys.quad captures/scan5 --verificar --escribir
    python -m playground.keys.quad captures/scan5 --offset              # corrimiento global proyector ↔ mapa

Hallazgo del 16-oct (03:5x): la luz caía (+0,8, −19,9) px respecto del mapa de scan5 en toda la escena (patrón
aleatorio, correlación de fase con la Osmo), mientras el cuadrilátero de scene_pov calzaba con keys.json a ≤1 px en
el frente: lo corrido era la salida del proyector, no las teclas. Eso se corrige con un corrimiento global (o
reescaneando el mapa), no reescribiendo keys.json.

1. Máscara de lo claro y poco saturado (V sobre Otsu, S bajo) cerca del teclado; cierre morfológico (rellena las
   negras y las ranuras); la componente mayor.
2. Cada arista (fondo, frente, canto izquierdo, canto derecho) se ajusta con una recta robusta (Huber) sobre el borde
   de la máscara cerca de la arista aproximada (la de keys.json), y después se refina con subpíxel: perfiles
   perpendiculares del gris, máximo del gradiente con parábola, recta robusta otra vez. Esquinas = intersecciones.
3. Teclas: modelo canónico (36 blancas iguales, negras 2-3 de ancho 7/12 y largo 0,62) → cuadrilátero (homografía).
4. Verificación (overlay del runtime): banda blanca ancha → la zona real de teclas vista por la Osmo; relleno de las
   teclas viejas y de las nuevas → lo iluminado; bleed = iluminado fuera de la zona real / iluminado; también
   cobertura e IoU. Muestra 8 s el contorno nuevo con las 4 esquinas marcadas.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from .detect import ANCHO_NEGRA, LARGO_NEGRA
from .fit import Modelo, teclas_por_homografia

W, H = 1920, 1080
NOMBRES_ESQ = ("atras_izq", "atras_der", "frente_der", "frente_izq")


# ---------------------------------------------------------------------------------------------- rectas

def _recta_huber(pts: np.ndarray) -> np.ndarray:
    """(vx, vy, x0, y0) robusta."""
    return cv2.fitLine(np.asarray(pts, np.float32).reshape(-1, 1, 2), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()


def _interseca(l1, l2) -> np.ndarray:
    vx1, vy1, x1, y1 = l1; vx2, vy2, x2, y2 = l2
    A = np.array([[vx1, -vx2], [vy1, -vy2]], float); b = np.array([x2 - x1, y2 - y1], float)
    t = np.linalg.solve(A, b)[0]
    return np.array([x1 + t * vx1, y1 + t * vy1])


def _dist_recta(pts, l) -> np.ndarray:
    vx, vy, x0, y0 = l
    return np.abs((pts[:, 0] - x0) * vy - (pts[:, 1] - y0) * vx)


def mascara_teclas(img: np.ndarray, quad0: np.ndarray, margen: int = 45, cierre: int = 21) -> np.ndarray:
    """Lo claro y poco saturado cerca del teclado, con las negras y ranuras rellenas; la componente mayor (uint8 0/255)."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    zona = np.zeros(img.shape[:2], np.uint8)
    cv2.fillPoly(zona, [np.round(quad0).astype(np.int32)], 255)
    zona = cv2.dilate(zona, np.ones((2 * margen + 1, 2 * margen + 1), np.uint8))
    v = hsv[..., 2]; sat = hsv[..., 1]
    thr, _ = cv2.threshold(v[zona > 0].reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m = ((v > thr) & (sat < 90) & (zona > 0)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cierre, cierre)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    if n <= 1:
        raise ValueError("no encontré la zona clara de las teclas")
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    out = (lab == i).astype(np.uint8) * 255
    # rellenar huecos internos (reflejos, negras grandes)
    cnts, _ = cv2.findContours(out, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(out, cnts, -1, 255, -1)
    return out


def _subpixel(gris: np.ndarray, linea, p0, p1, n: int = 60, alcance: float = 9.0, min_grad: float = 6.0):
    """Puntos del borde a lo largo de la arista p0→p1 (sin las puntas): máximo del gradiente en perfiles perpendiculares."""
    vx, vy = linea[0], linea[1]
    nrm = np.array([-vy, vx])
    pts = []
    offs = np.arange(-alcance, alcance + 0.5, 0.5)
    for t in np.linspace(0.08, 0.92, n):
        c = p0 + (p1 - p0) * t
        xs = c[0] + offs * nrm[0]; ys = c[1] + offs * nrm[1]
        prof = cv2.remap(gris, xs.astype(np.float32).reshape(1, -1), ys.astype(np.float32).reshape(1, -1),
                         cv2.INTER_LINEAR).ravel().astype(np.float64)
        prof = np.convolve(prof, np.ones(3) / 3, mode="same")
        g = np.abs(np.gradient(prof))
        g[:2] = 0; g[-2:] = 0
        k = int(np.argmax(g))
        if g[k] < min_grad or k == 0 or k == len(g) - 1:
            continue
        a, b, cc = g[k - 1], g[k], g[k + 1]
        den = a - 2 * b + cc
        dk = 0.5 * (a - cc) / den if abs(den) > 1e-9 else 0.0
        o = offs[k] + dk * 0.5
        pts.append(c + o * nrm)
    return np.array(pts)


def _ajustar_arista(borde, gris, p0, p1, subpixel=True, direccion_fija=None, tramo=(0.06, 0.94)):
    """Recta de una arista: borde de la máscara cerca de p0→p1 (dentro de `tramo`), Huber con recorte, y subpíxel.
    Con `direccion_fija` (vector unitario) solo se ajusta el desplazamiento (los cantos son cortos y su dirección la
    da el haz de las teclas)."""
    l0 = _recta_huber(np.array([p0, p1]))
    largo = np.linalg.norm(p1 - p0)
    d = _dist_recta(borde, l0)
    t = ((borde - p0) @ (p1 - p0)) / (largo ** 2)
    sel = borde[(d < 25) & (t > tramo[0]) & (t < tramo[1])]
    if len(sel) < 6:
        raise ValueError(f"arista {np.round(p0).tolist()}→{np.round(p1).tolist()}: pocos puntos de borde ({len(sel)})")

    def ajustar(pts):
        if direccion_fija is None:
            return _recta_huber(pts)
        vx, vy = direccion_fija
        nrm = np.array([-vy, vx])
        off = float(np.median((pts - p0) @ nrm))
        c = p0 + off * nrm
        return np.array([vx, vy, c[0], c[1]])

    l1 = ajustar(sel)
    for _ in range(2):
        dd = _dist_recta(sel, l1)
        sel2 = sel[dd < max(2.0, 2.5 * np.median(dd))]
        if len(sel2) >= 6:
            sel = sel2; l1 = ajustar(sel)
    n_sub = 0
    if subpixel:
        q0 = p0 + (p1 - p0) * tramo[0]; q1 = p0 + (p1 - p0) * tramo[1]
        # q0/q1 proyectados sobre la recta ajustada
        def proy(q):
            v = np.array(l1[:2]); c = np.array(l1[2:])
            return c + ((q - c) @ v) * v
        sp = _subpixel(gris, l1, proy(q0), proy(q1), n=max(16, int(largo * (tramo[1] - tramo[0]) / 6)))
        if len(sp) >= 6:
            l2 = ajustar(sp)
            dd = _dist_recta(sp, l2)
            sp = sp[dd < max(1.0, 2.5 * np.median(dd))]
            if len(sp) >= 6:
                l1 = ajustar(sp); n_sub = len(sp)
    return l1, len(sel), n_sub


def detectar_cuadrilatero(img: np.ndarray, quad0, subpixel: bool = True) -> dict:
    """Esquinas (atrás-izq, atrás-der, frente-der, frente-izq) del área de teclas en `img` (BGR, px del proyector).
    `quad0`: el cuadrilátero aproximado (p. ej. el de keys.json), para orientar las aristas.

    Fondo y frente: rectas libres (largas, muchos puntos). Cantos izquierdo y derecho: cortos, con las negras pegadas
    atrás y el cuerpo del teclado al lado; su dirección es la del haz de las teclas (la de quad0) y solo se ajusta
    su desplazamiento, con el borde de la parte de adelante (35-95 % del largo de la tecla, solo blancas)."""
    quad0 = np.asarray(quad0, float)
    mask = mascara_teclas(img, quad0)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    borde = max(cnts, key=cv2.contourArea).reshape(-1, 2).astype(float)
    gris = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), 1.0)
    al, ar, fr, fl = quad0
    info = []
    l_fondo, n0, s0 = _ajustar_arista(borde, gris, al, ar, subpixel)
    l_frente, n1, s1 = _ajustar_arista(borde, gris, fl, fr, subpixel)
    dl = (fl - al) / np.linalg.norm(fl - al); dr = (fr - ar) / np.linalg.norm(fr - ar)
    l_izq, n2, s2 = _ajustar_arista(borde, gris, al, fl, subpixel, direccion_fija=dl, tramo=(0.35, 0.95))
    l_der, n3, s3 = _ajustar_arista(borde, gris, ar, fr, subpixel, direccion_fija=dr, tramo=(0.35, 0.95))
    for nombre, n_, s_ in (("fondo", n0, s0), ("frente", n1, s1), ("canto_izq", n2, s2), ("canto_der", n3, s3)):
        info.append({"arista": nombre, "puntos_mascara": int(n_), "puntos_subpixel": int(s_)})
    esquinas = np.array([_interseca(l_fondo, l_izq), _interseca(l_fondo, l_der),
                         _interseca(l_frente, l_der), _interseca(l_frente, l_izq)])
    return {"esquinas": esquinas, "lineas": [l.tolist() for l in (l_fondo, l_der, l_frente, l_izq)], "aristas": info,
            "mascara": mask}


def teclas_desde_cuadrilatero(esquinas, img: np.ndarray | None = None, n_teclas: int = 61, primera_nota: int = 36,
                              ancho_negra: float = ANCHO_NEGRA, largo_negra: float = LARGO_NEGRA) -> dict:
    modelo = Modelo(n_teclas, primera_nota, ancho_negra, largo_negra)
    src = np.float32([[0, 0], [modelo.nb, 0], [modelo.nb, 1], [0, 1]])
    Hm = cv2.getPerspectiveTransform(src, np.float32(esquinas)).astype(np.float64)
    return teclas_por_homografia(Hm, modelo, img, 1.0, [], metodo="cuadrilatero", anclaje="4 esquinas del área blanca",
                                 extra={"esquinas": {n: np.round(e, 2).tolist() for n, e in zip(NOMBRES_ESQ, esquinas)}})


def quad_de_keys(keys: dict) -> np.ndarray:
    q = (keys.get("banda") or {}).get("quad")
    if q:
        return np.asarray(q, float)
    t = {k["nota"]: k for k in keys["teclas"]}
    lo, hi = min(t), max(t)
    a = np.asarray(t[lo]["poligono"], float); b = np.asarray(t[hi]["poligono"], float)
    return np.array([a[0], b[1], b[2], a[3]])


# ---------------------------------------------------------------------------------------------- verificación en vivo

def _relleno(teclas: list[dict]) -> np.ndarray:
    m = np.zeros((H, W), np.uint8)
    for t in teclas:
        forma = t["contorno"] if t["color"] == "blanca" and t.get("contorno") else t["poligono"]
        cv2.fillPoly(m, [np.round(np.asarray(forma) * 4).astype(np.int32)], 255, cv2.LINE_8, 2)
    return m


def contorno_con_esquinas(esquinas) -> np.ndarray:
    img = np.zeros((H, W, 3), np.uint8)
    q = np.round(np.asarray(esquinas) * 4).astype(np.int32)
    cv2.polylines(img, [q], True, (255, 255, 255), 2, cv2.LINE_AA, 2)
    for (x, y), n in zip(np.asarray(esquinas), NOMBRES_ESQ):
        cv2.circle(img, (int(round(x)), int(round(y))), 9, (0, 0, 255), 2, cv2.LINE_AA)
        cv2.drawMarker(img, (int(round(x)), int(round(y))), (0, 255, 255), cv2.MARKER_CROSS, 14, 1)
    return img


def verificar(scan: Path, viejas: list[dict], nuevas: list[dict], esquinas, settle: float = 1.0, mostrar_s: float = 8.0,
              guardar: Path | None = None) -> dict:
    """Overlay + Osmo: zona real (banda blanca ancha), relleno viejo y nuevo → bleed, cobertura e IoU de cada uno."""
    from slscan.capture import Camera
    from .refine import CamMap, Grabber, OverlayWindow, ROI
    est = OverlayWindow.estado()
    if est != "overlay":
        raise SystemExit(f"para verificar hace falta el runtime con overlay en :8790 (estado: {est})")
    P = np.concatenate([np.asarray(t["poligono"], float) for t in viejas + nuevas])
    x0, y0 = np.floor(P.min(axis=0)).astype(int) - 60; x1, y1 = np.ceil(P.max(axis=0)).astype(int) + 60
    roi = ROI(max(0, x0), max(0, y0), min(W, x1), min(H, y1))
    cmap = CamMap.load("osmo", scan / "proj_to_cam.npz", roi)
    out = OverlayWindow()
    cam = Grabber(Camera("osmo", size="3840x2160", rotate=270, downscale=2))
    s_ = (slice(roi.y0, roi.y1), slice(roi.x0, roi.x1))

    def cap(img):
        out.show(img); f = cam.despues_de(time.time() + settle)
        return cmap.al_proyector(f)

    try:
        negro = np.zeros((H, W, 3), np.uint8)
        d, okd = cap(negro)
        ancho = np.zeros((H, W), np.uint8)
        cv2.fillPoly(ancho, [np.round(np.asarray(esquinas)).astype(np.int32)], 255)
        ancho = cv2.dilate(ancho, np.ones((91, 91), np.uint8))
        wimg = negro.copy(); wimg[ancho > 0] = 255
        w, okw = cap(wimg)
        lit = {}
        for nombre, ts in (("antes", viejas), ("despues", nuevas)):
            fimg = negro.copy(); fimg[_relleno(ts) > 0] = 255
            f, okf = cap(fimg)
            ok = okd & okw & okf
            v = f - d
            alto = float(np.percentile(v[ok & (_relleno(ts)[s_] > 0)], 90))
            lit[nombre] = (v > 0.5 * alto) & ok
        out.show(contorno_con_esquinas(esquinas), seconds=mostrar_s + 2)
        time.sleep(mostrar_s)
    finally:
        out.close(); cam.close()
        shutil.rmtree(out.tmp, ignore_errors=True)
    # zona real: lo claro de la banda blanca (W−D) llevado al proyector, como imagen BGR para la misma máscara
    r = w - d
    hi = max(float(np.percentile(r[okw & okd], 99)), 1.0)
    g8 = np.clip(r / hi * 255, 0, 255).astype(np.uint8)
    zona_img = cv2.cvtColor(g8, cv2.COLOR_GRAY2BGR)
    quad_roi = np.asarray(esquinas, float) - [roi.x0, roi.y0]
    zona = mascara_teclas(zona_img, quad_roi, margen=40) > 0
    res = {}
    for nombre, l in lit.items():
        ln_ = l.sum(); inter = (l & zona).sum(); uni = (l | zona).sum()
        res[nombre] = {"bleed": round(float((l & ~zona).sum()) / max(1, ln_), 4),
                       "cobertura": round(float(inter) / max(1, zona.sum()), 4),
                       "iou": round(float(inter) / max(1, uni), 4)}
    # la zona real vista ahora por la Osmo también da sus esquinas (contraste con las de scene_pov)
    try:
        viva = detectar_cuadrilatero(zona_img, quad_roi)
        res["esquinas_en_vivo"] = {n: np.round(e + [roi.x0, roi.y0], 2).tolist() for n, e in zip(NOMBRES_ESQ, viva["esquinas"])}
    except Exception as e:  # noqa: BLE001
        res["esquinas_en_vivo"] = f"no se pudo: {e}"
    if guardar is not None:
        guardar.mkdir(parents=True, exist_ok=True)
        vis = zona_img.copy()
        vis[zona] = (0.6 * vis[zona] + 0.4 * np.array([0, 160, 0])).astype(np.uint8)
        for nombre, col in (("antes", (0, 0, 255)), ("despues", (0, 255, 255))):
            cnts, _ = cv2.findContours(lit[nombre].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            cv2.drawContours(vis, cnts, -1, col, 1)
        cv2.imwrite(str(guardar / "quad_verificacion.png"), cv2.resize(vis, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))
    return res


# ---------------------------------------------------------------------------------------------- corrimiento global

def patron_aleatorio(semilla: int = 3, celda: int = 8) -> np.ndarray:
    rng = np.random.default_rng(semilla)
    blk = (rng.random((H // celda, W // celda)) > 0.5).astype(np.uint8) * 255
    return cv2.GaussianBlur(cv2.resize(blk, (W, H), interpolation=cv2.INTER_NEAREST), (0, 0), 1.0)


def corrimiento_por_fase(patron: np.ndarray, visto_pos: np.ndarray, visto_neg: np.ndarray, ok: np.ndarray,
                         zona: tuple[int, int, int, int] | None = None) -> dict:
    """Corrimiento (dx, dy) de lo que se vio respecto del patrón proyectado, en px del proyector (los dos ya llevados
    al proyector con el mapa). `visto_pos`/`visto_neg`: patrón y su negativo (la resta quita la reflectancia).
    Positivo = la luz cae a la derecha/abajo de donde dice el mapa; para corregir hay que dibujar en (−dx, −dy)."""
    x0, y0, x1, y1 = zona or (0, 0, patron.shape[1], patron.shape[0])
    A = patron[y0:y1, x0:x1].astype(np.float32) - 127.5
    B = (visto_pos - visto_neg)[y0:y1, x0:x1].astype(np.float32)
    okz = ok[y0:y1, x0:x1]
    A = A.copy(); A[~okz] = 0
    B = B - (B[okz].mean() if okz.any() else 0.0); B[~okz] = 0
    hw = cv2.createHanningWindow((A.shape[1], A.shape[0]), cv2.CV_32F)
    (dx, dy), resp = cv2.phaseCorrelate(A, B.astype(np.float32), hw)
    return {"dx": round(float(dx), 2), "dy": round(float(dy), 2), "respuesta": round(float(resp), 3),
            "valido": round(float(okz.mean()), 3), "corregir_con": [round(-float(dx), 2), round(-float(dy), 2)]}


def medir_corrimiento(scan: Path, settle: float = 1.0) -> dict:
    """Overlay del runtime + Osmo: patrón aleatorio y su negativo → corrimiento global proyector ↔ mapa del escaneo."""
    from slscan.capture import Camera
    from .refine import CamMap, Grabber, OverlayWindow, ROI
    if OverlayWindow.estado() != "overlay":
        raise SystemExit("hace falta el runtime con overlay en :8790")
    cmap = CamMap.load("osmo", scan / "proj_to_cam.npz", ROI(0, 0, W, H))
    pat = patron_aleatorio()
    out = OverlayWindow(); cam = Grabber(Camera("osmo", size="3840x2160", rotate=270, downscale=2))
    try:
        vistos = []
        for img in (pat, 255 - pat):
            out.show(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
            vistos.append(cmap.al_proyector(cam.despues_de(time.time() + settle)))
    finally:
        out.close(); cam.close()
        shutil.rmtree(out.tmp, ignore_errors=True)          # los PNG del overlay
    (a, oka), (b, okb) = vistos
    res = {"escena": corrimiento_por_fase(pat, a, b, oka & okb)}
    try:
        keys = json.loads((scan / "keys.json").read_text())
        P = np.concatenate([np.asarray(t["poligono"]) for t in keys["teclas"]])
        x0, y0 = np.maximum(P.min(0).astype(int) - 80, 0); x1, y1 = np.minimum(P.max(0).astype(int) + 80, [W, H])
        res["teclado"] = corrimiento_por_fase(pat, a, b, oka & okb, (int(x0), int(y0), int(x1), int(y1)))
    except (OSError, KeyError, ValueError):
        pass
    return res


# ---------------------------------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m playground.keys.quad", description=__doc__.splitlines()[0])
    ap.add_argument("scan", nargs="?", default="captures/scan5")
    ap.add_argument("--imagen", default="", help="foto en px del proyector (por defecto <scan>/scene_pov.png)")
    ap.add_argument("--verificar", action="store_true", help="overlay del runtime + Osmo: bleed antes/después")
    ap.add_argument("--escribir", action="store_true", help="escribir keys.json (respaldo keys_antes_quad.json) y recargar")
    ap.add_argument("--solo-si-mejora", action="store_true", default=True)
    ap.add_argument("--runtime", default="http://127.0.0.1:8790")
    ap.add_argument("--guardar", action="store_true", help="imágenes de depuración en reports/_keys_quad (privadas)")
    ap.add_argument("--offset", action="store_true",
                    help="solo medir el corrimiento global proyector ↔ mapa (patrón aleatorio por overlay, Osmo)")
    a = ap.parse_args(argv)
    scan = Path(a.scan)
    if a.offset:
        r = medir_corrimiento(scan)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        e = r["escena"]
        print(f"la luz cae ({e['dx']:+.1f}, {e['dy']:+.1f}) px de donde dice el mapa (respuesta {e['respuesta']}): "
              f"para corregir, dibujar todo corrido {e['corregir_con']}")
        (scan / "corrimiento.json").write_text(json.dumps({**r, "fecha": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=1))
        return 0
    img = cv2.imread(a.imagen or str(scan / "scene_pov.png"))
    keys = json.loads((scan / "keys.json").read_text())
    q0 = quad_de_keys(keys)
    det = detectar_cuadrilatero(img, q0)
    esq = det["esquinas"]
    nuevo = teclas_desde_cuadrilatero(esq, img, keys.get("n_teclas", 61), keys.get("primera_nota", 36))
    print("esquinas (px del proyector):")
    rep = {"esquinas": {}, "aristas": det["aristas"], "imagen": a.imagen or str(scan / "scene_pov.png")}
    for n, e, e0 in zip(NOMBRES_ESQ, esq, q0):
        dlt = e - e0
        rep["esquinas"][n] = {"nueva": np.round(e, 2).tolist(), "keys_json": np.round(e0, 2).tolist(),
                              "diferencia": np.round(dlt, 2).tolist()}
        print(f"  {n:11s} {e.round(1).tolist()}  (keys.json {e0.round(1).tolist()}, Δ {dlt.round(1).tolist()})")
    dbg = Path("reports/_keys_quad") if a.guardar else None
    if dbg is not None:
        dbg.mkdir(parents=True, exist_ok=True)
        vis = img.copy()
        cv2.polylines(vis, [np.round(q0 * 4).astype(np.int32)], True, (255, 120, 0), 1, cv2.LINE_AA, 2)
        cv2.polylines(vis, [np.round(esq * 4).astype(np.int32)], True, (0, 255, 255), 1, cv2.LINE_AA, 2)
        x0, y0 = np.floor(np.minimum(esq.min(0), q0.min(0))).astype(int) - 30
        x1, y1 = np.ceil(np.maximum(esq.max(0), q0.max(0))).astype(int) + 30
        cv2.imwrite(str(dbg / "quad_scene_pov.png"), cv2.resize(vis[y0:y1, x0:x1], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
    if a.verificar:
        v = verificar(scan, keys["teclas"], nuevo["teclas"], esq, guardar=dbg)
        rep["verificacion"] = v
        print("verificación:", json.dumps(v, ensure_ascii=False))
    (scan / "keys_quad.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    mejora = True
    if "verificacion" in rep:
        va, vd = rep["verificacion"]["antes"], rep["verificacion"]["despues"]
        mejora = vd["iou"] >= va["iou"] and vd["bleed"] <= va["bleed"] + 0.01
        print("mejora:", mejora)
    if a.escribir:
        if not mejora and a.solo_si_mejora:
            print("no escribo keys.json: el cuadrilátero nuevo no mejora bleed/IoU")
            return 0
        shutil.copy(scan / "keys.json", scan / "keys_antes_quad.json")
        nuevo["fuente"] = rep["imagen"]
        (scan / "keys.json").write_text(json.dumps(nuevo, ensure_ascii=False, indent=1))
        print(f"→ {scan / 'keys.json'} (respaldo keys_antes_quad.json) y {scan / 'keys_quad.json'}")
        aj = scan / "ajustes.json"
        if aj.exists():
            d = json.loads(aj.read_text())
            if "keys" in d and any(abs(float(x)) > 1e-9 for x in d["keys"]):
                print(f"ajustes.json: keys {d['keys']} → [0, 0] (las esquinas nuevas ya lo incluyen)")
                d["keys"] = [0.0, 0.0]; aj.write_text(json.dumps(d, indent=1))
        try:
            body = json.dumps({"cmd": "reload_scene", "args": {}}).encode()
            urllib.request.urlopen(urllib.request.Request(a.runtime.rstrip("/") + "/api/command", body,
                                                          {"Content-Type": "application/json"}), timeout=4).read()
            print("runtime: escena recargada")
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

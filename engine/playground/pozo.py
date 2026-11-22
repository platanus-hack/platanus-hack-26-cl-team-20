"""pozo.py: perspectiva forzada dinámica sobre una hoja de papel: un pozo/túnel que baja desde la hoja, dibujado para
el punto de vista del ojo (anamorfosis). Cada vértice 3D bajo la mesa (z<0) se lleva al plano z=0 por el rayo ojo→punto
y se pinta en el lienzo proyectable; el lienzo va al proyector con H_lienzo_to_proj.

Marco de la mesa (mm): origen TL del lienzo, x a lo largo, y hacia abajo del lienzo, z hacia arriba.

Geometría (en el marco local de la hoja: u a lo largo de TL→TR, v de TL→BL, mm):
  * cornisa (ledge) de --cornisa mm al nivel de la mesa, desde el borde de la hoja hasta la boca.
  * boca = rectángulo de la hoja menos la cornisa, o círculo inscrito (--forma circulo).
  * paredes verticales desde z=0 hasta z=-profundidad, con hiladas de ladrillos cada --hilada mm (mortero oscuro).
    Solo se dibujan las paredes cuya cara interior mira al ojo (n·(ojo−p) > 0): las cercanas se ocultan.
  * fondo oscuro con un brillo tenue en el centro.
  Todo el pozo se recorta a la boca (lo que se ve "por el agujero") y a la hoja.

Uso:
  .venv/bin/python -m playground.pozo --ojo 380,-300,450 --hoja 686,209,1233,917 --dry
  .venv/bin/python -m playground.pozo --ojo 380,-300,450 --hoja auto --seconds 20
  .venv/bin/python -m playground.pozo --cabeza http://127.0.0.1:8803 --hoja auto
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

PROJ_W, PROJ_H = 1920, 1080


# ----------------------------------------------------------------------------------------------------------------- utils
def runtime_cmd(url: str, cmd: str, **args) -> dict:
    body = json.dumps({"cmd": cmd, "args": args}).encode()
    req = urllib.request.Request(url + "/api/command", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def get_json(url: str, timeout: float = 2.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None


def hoja_quad(spec: str, feed: str, lienzo_px) -> np.ndarray | None:
    """Esquinas TL TR BR BL de la hoja en px del lienzo. 'auto' = primera hoja de /papel.json; 'x0,y0,x1,y1' = rectángulo."""
    if spec == "auto":
        j = get_json(feed + "/papel.json")
        if not j or not j.get("hoja"):
            return None
        return np.array(j["hoja"]["quad_lienzo"], np.float64)
    x0, y0, x1, y1 = [float(v) for v in spec.split(",")]
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float64)


# ---------------------------------------------------------------------------------------------------------------- escena
class Pozo:
    def __init__(self, quad_px: np.ndarray, mm_px: float, lienzo_px, a):
        self.a = a
        self.mm_px = mm_px
        self.lw, self.lh = lienzo_px
        self.quad_px = quad_px
        q = quad_px * mm_px                                   # esquinas en mm de mesa
        self.tl = q[0]
        eu = q[1] - q[0]; ev = q[3] - q[0]
        self.W = float(np.linalg.norm(eu)); self.Hh = float(np.linalg.norm(ev))
        self.u = eu / self.W; self.v = ev / self.Hh         # ejes locales (unitarios, mm de mesa)
        self.prof = float(a.profundidad)
        c = float(a.cornisa)
        if a.forma == "circulo":
            r = (min(self.W, self.Hh) / 2 - c)
            cx, cy = self.W / 2, self.Hh / 2
            n = 48
            ang = np.linspace(0, 2 * math.pi, n, endpoint=False)
            self.boca = np.stack([cx + r * np.cos(ang), cy + r * np.sin(ang)], 1)      # polígono (sentido horario en u,v)
            self.centro = np.array([cx, cy]); self.radio = r
        else:
            self.boca = np.array([[c, c], [self.W - c, c], [self.W - c, self.Hh - c], [c, self.Hh - c]], np.float64)
            self.centro = np.array([self.W / 2, self.Hh / 2]); self.radio = min(self.W, self.Hh) / 2 - c
        # muros: una cara por arista de la boca, con su normal interior (hacia el centro)
        self.caras = []
        n = len(self.boca)
        s = 0.0
        for i in range(n):
            p0, p1 = self.boca[i], self.boca[(i + 1) % n]
            L = float(np.linalg.norm(p1 - p0)); d = (p1 - p0) / L
            nrm = np.array([-d[1], d[0]])
            if np.dot(self.centro - p0, nrm) < 0:
                nrm = -nrm
            self.caras.append({"p0": p0, "d": d, "L": L, "n": nrm, "s0": s, "i": i})
            s += L
        self.perimetro = s

    # --- marcos
    def local_a_px(self, pts2: np.ndarray) -> np.ndarray:
        pts2 = np.asarray(pts2, np.float64).reshape(-1, 2)
        mm = self.tl[None, :] + pts2[:, :1] * self.u[None, :] + pts2[:, 1:2] * self.v[None, :]
        return mm / self.mm_px

    def ojo_local(self, ojo_mm) -> np.ndarray:
        e = np.asarray(ojo_mm, np.float64)
        d = e[:2] - self.tl
        return np.array([float(d @ self.u), float(d @ self.v), float(e[2])])

    # --- perímetro de la boca por longitud de arco s
    def _cara_en(self, s: float) -> dict:
        s = s % self.perimetro
        for c in self.caras:
            if s < c["s0"] + c["L"] + 1e-9:
                return c
        return self.caras[-1]

    def punto_en(self, s: float) -> np.ndarray:
        c = self._cara_en(s)
        return c["p0"] + c["d"] * ((s % self.perimetro) - c["s0"])

    def normal_en(self, s: float) -> np.ndarray:
        return self._cara_en(s)["n"]

    def tramo(self, sa: float, sb: float) -> np.ndarray:
        """Polilínea sobre el perímetro entre sa y sb (incluye los vértices intermedios)."""
        pts = [self.punto_en(sa)]
        for c in self.caras:
            if sa < c["s0"] < sb:
                pts.append(c["p0"])
        pts.append(self.punto_en(sb))
        return np.array(pts)

    def proyectar(self, P: np.ndarray, E: np.ndarray) -> np.ndarray:
        """Puntos 3D locales (N,3) con z<=0 → punto del plano z=0 (N,2) visto desde el ojo E (z>0)."""
        P = np.asarray(P, np.float64).reshape(-1, 3)
        t = E[2] / np.maximum(E[2] - P[:, 2], 1e-6)
        return E[None, :2] + (P[:, :2] - E[None, :2]) * t[:, None]

    # --- render
    def render(self, ojo_mm) -> np.ndarray:
        a = self.a
        E = self.ojo_local(ojo_mm)
        if E[2] < 50:                                                          # ojo pegado a la mesa: no hay ilusión
            E = np.array([E[0], E[1], 50.0])
        img = np.zeros((self.lh, self.lw, 3), np.uint8)
        f = a.supersample
        if f > 1:
            img = np.zeros((self.lh * f, self.lw * f, 3), np.uint8)

        def poly(pts2_local, color, thickness=-1):
            px = self.local_a_px(pts2_local) * f
            p = np.round(px).astype(np.int32).reshape(-1, 1, 2)
            if thickness < 0:
                cv2.fillPoly(img, [p], color, cv2.LINE_AA)
            else:
                cv2.polylines(img, [p], True, color, thickness, cv2.LINE_AA)

        # 1) cornisa: toda la hoja en piedra clara, con losas
        piedra = np.array(a.color_cornisa, np.float64)
        poly(np.array([[0, 0], [self.W, 0], [self.W, self.Hh], [0, self.Hh]]), piedra.astype(int).tolist())
        # losas de la cornisa: líneas desde la boca hacia afuera cada --losa mm de perímetro
        losa = (piedra * 0.55).astype(int).tolist()
        k = 0.0
        while k < self.perimetro:
            p_in, nrm = self.punto_en(k), self.normal_en(k)
            px = (self.local_a_px(np.array([p_in, p_in - nrm * (a.cornisa + 2)])) * f).round().astype(np.int32)
            cv2.line(img, tuple(px[0]), tuple(px[1]), losa, max(1, f), cv2.LINE_AA)
            k += a.losa
        # 2) interior del pozo en una capa aparte, luego recortado a la boca
        pozo = np.zeros_like(img)

        def ppoly(P3, color):
            q = self.proyectar(P3, E)
            px = self.local_a_px(q) * f
            cv2.fillPoly(pozo, [np.round(px).astype(np.int32).reshape(-1, 1, 2)], color, cv2.LINE_AA)

        # fondo
        D = self.prof
        fondo = np.array(a.color_fondo, np.float64)
        ppoly(np.hstack([self.boca, np.full((len(self.boca), 1), -D)]), fondo.astype(int).tolist())
        # brillo tenue en el fondo (disco)
        ang = np.linspace(0, 2 * math.pi, 40, endpoint=False)
        for rr, kk in ((0.55, 1.6), (0.35, 2.4), (0.18, 3.4)):
            disc = np.stack([self.centro[0] + self.radio * rr * np.cos(ang), self.centro[1] + self.radio * rr * np.sin(ang), np.full(40, -D + 0.5)], 1)
            ppoly(disc, np.clip(fondo * kk, 0, 255).astype(int).tolist())
        # paredes: de abajo hacia arriba (pintor), por hilada; ladrillos por arco de perímetro (s global)
        ladrillo = np.array(a.color_ladrillo, np.float64)
        mortero = np.array(a.color_mortero, np.float64)
        h = float(a.hilada); bw = float(a.ladrillo); g = float(a.junta)
        nh = int(math.ceil(D / h))
        cortes = sorted(c["s0"] for c in self.caras) if len(self.caras) <= 8 else []   # en rectángulos, cortar en esquinas

        def visible(s_mid):
            nrm = self.normal_en(s_mid)
            hacia = E[:2] - self.punto_en(s_mid); n_ = np.linalg.norm(hacia)
            cosv = float(nrm @ (hacia / n_)) if n_ > 1e-6 else 1.0
            return cosv > 0, 0.6 + 0.4 * max(0.0, cosv)

        def banda(sa, sb, z0, z1, color):
            top = self.tramo(sa, sb)
            if len(top) < 2:
                return
            P = np.vstack([np.hstack([top, np.full((len(top), 1), z0)]), np.hstack([top[::-1], np.full((len(top), 1), z1)])])
            ppoly(P, color)

        for r in range(nh - 1, -1, -1):
            z1, z0 = -min(D, (r + 1) * h), -r * h                              # z1 abajo, z0 arriba
            prof_med = -(z0 + z1) / 2
            sombra = a.luz_min + (1 - a.luz_min) * math.exp(-a.caida * prof_med / D)
            off = (bw / 2) if (r % 2) else 0.0
            # mortero: una banda por cara visible
            for c in self.caras:
                vis, cara_k = visible(c["s0"] + c["L"] / 2)
                if vis:
                    banda(c["s0"], c["s0"] + c["L"], z0, z1, np.clip(mortero * sombra * cara_k, 0, 255).astype(int).tolist())
            # ladrillos
            bordes = sorted(set([0.0, self.perimetro] + cortes + [k * bw + off for k in range(int(self.perimetro / bw) + 2) if 0 < k * bw + off < self.perimetro]))
            for sa, sb in zip(bordes[:-1], bordes[1:]):
                if sb - sa <= 2 * g:
                    continue
                vis, cara_k = visible((sa + sb) / 2)
                if not vis:
                    continue
                banda(sa + g, sb - g, z0 - g, z1 + g, np.clip(ladrillo * sombra * cara_k, 0, 255).astype(int).tolist())
        # 3) recorte del pozo a la boca y pegado sobre la cornisa
        mask = np.zeros(img.shape[:2], np.uint8)
        cv2.fillPoly(mask, [np.round(self.local_a_px(self.boca) * f).astype(np.int32).reshape(-1, 1, 2)], 255, cv2.LINE_AA)
        m3 = mask[..., None].astype(np.float32) / 255.0
        img = (img * (1 - m3) + pozo * m3).astype(np.uint8)
        # borde interior de la cornisa (bisel oscuro)
        cv2.polylines(img, [np.round(self.local_a_px(self.boca) * f).astype(np.int32).reshape(-1, 1, 2)], True,
                      (piedra * 0.35).astype(int).tolist(), max(1, int(round(1.5 / self.mm_px * f))), cv2.LINE_AA)
        # 4) recorte a la hoja
        mh = np.zeros(img.shape[:2], np.uint8)
        cv2.fillPoly(mh, [np.round(self.quad_px * f).astype(np.int32).reshape(-1, 1, 2)], 255, cv2.LINE_AA)
        img = cv2.bitwise_and(img, img, mask=mh)
        if f > 1:
            img = cv2.resize(img, (self.lw, self.lh), interpolation=cv2.INTER_AREA)
        return img


def a_proyector(lienzo_img: np.ndarray, H_lp: np.ndarray) -> np.ndarray:
    return cv2.warpPerspective(lienzo_img, H_lp, (PROJ_W, PROJ_H), flags=cv2.INTER_LINEAR)


def guardar_png(path: Path, img: np.ndarray):
    tmp = path.with_suffix(".tmp.png")
    cv2.imwrite(str(tmp), img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    os.replace(tmp, path)


# ------------------------------------------------------------------------------------------------------------------ main
def parse_xyz(s: str) -> np.ndarray:
    return np.array([float(v) for v in s.split(",")], np.float64)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scan", default="captures/dale-20261102-1511")
    p.add_argument("--feed", default="http://127.0.0.1:8798")
    p.add_argument("--runtime", default="http://127.0.0.1:8790")
    p.add_argument("--ojo", default=None, help="x,y,z mm fijo (marco de la mesa)")
    p.add_argument("--cabeza", default=None, help="URL de cabeza.py (p. ej. http://127.0.0.1:8803)")
    p.add_argument("--hoja", default="auto", help="'auto' (de /papel.json) o x0,y0,x1,y1 en px del lienzo")
    p.add_argument("--forma", choices=["rect", "circulo"], default="rect")
    p.add_argument("--profundidad", type=float, default=600.0)
    p.add_argument("--cornisa", type=float, default=15.0)
    p.add_argument("--hilada", type=float, default=40.0, help="alto de cada hilada / anillo (mm)")
    p.add_argument("--ladrillo", type=float, default=70.0, help="largo de ladrillo (mm)")
    p.add_argument("--junta", type=float, default=2.5, help="mortero (mm)")
    p.add_argument("--losa", type=float, default=60.0, help="paso de las losas de la cornisa (mm)")
    p.add_argument("--luz-min", type=float, default=0.08); p.add_argument("--caida", type=float, default=3.0)
    p.add_argument("--color-ladrillo", default="70,95,170", help="BGR"); p.add_argument("--color-mortero", default="35,45,70")
    p.add_argument("--color-cornisa", default="165,175,185"); p.add_argument("--color-fondo", default="18,14,10")
    p.add_argument("--supersample", type=int, default=1)
    p.add_argument("--dry", action="store_true", help="solo escribe PNG en --out, no proyecta")
    p.add_argument("--out", default="reports/pozo")
    p.add_argument("--tag", default=None, help="sufijo de los PNG en --dry")
    p.add_argument("--seconds", type=float, default=20.0, help="duración de la proyección (0 = hasta Ctrl-C)")
    p.add_argument("--hz", type=float, default=10.0)
    p.add_argument("--overlay-s", type=float, default=1.5, help="vigencia de cada overlay en el runtime")
    p.add_argument("--umbral-mm", type=float, default=3.0, help="re-render solo si el ojo se movió más que esto")
    p.add_argument("--sin-rojo", action="store_true", help="no apagar/prender el lazo rojo del feed")
    p.add_argument("--esperar-hoja", type=float, default=15.0, help="s que se espera a que /papel.json tenga hoja")
    a = p.parse_args(argv)
    for k in ("color_ladrillo", "color_mortero", "color_cornisa", "color_fondo"):
        setattr(a, k, [int(v) for v in getattr(a, k).split(",")])

    scan = Path(a.scan)
    sup = json.loads((scan / "superficie.json").read_text())
    H_lp = np.array(sup["H_lienzo_to_proj"], np.float64)
    mm_px = float(sup["mm_por_px"]); lienzo_px = sup["lienzo_px"]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    quad = hoja_quad(a.hoja, a.feed, lienzo_px)
    t_espera = time.time() + a.esperar_hoja
    while quad is None and time.time() < t_espera:                 # la detección de la hoja va y viene: esperar un poco
        time.sleep(0.5); quad = hoja_quad(a.hoja, a.feed, lienzo_px)
    if quad is None:
        print(json.dumps({"error": "sin hoja: /papel.json no tiene hoja o el feed no responde; usa --hoja x0,y0,x1,y1"}), flush=True)
        return 2
    pozo = Pozo(quad, mm_px, lienzo_px, a)
    print(json.dumps({"hoja_px": quad.round(1).tolist(), "hoja_mm": [round(pozo.W, 1), round(pozo.Hh, 1)], "forma": a.forma,
                      "profundidad": a.profundidad, "boca_vertices": len(pozo.boca)}), flush=True)

    def ojo_actual():
        if a.cabeza:
            j = get_json(a.cabeza.rstrip("/") + "/cabeza.json", 0.5)
            if j and j.get("ojo_mm"):
                return np.array(j["ojo_mm"], np.float64), bool(j.get("ok")), j.get("fuente")
            return None, False, None
        return parse_xyz(a.ojo), True, "fijo"

    if a.dry:
        if a.ojo is None and a.cabeza is None:
            print("--dry necesita --ojo o --cabeza"); return 2
        ojo, ok, fuente = ojo_actual()
        if ojo is None:
            print(json.dumps({"error": "cabeza.py no entrega ojo"})); return 2
        tag = a.tag or "_".join(str(int(v)) for v in ojo)
        t = time.time(); lz = pozo.render(ojo); pr = a_proyector(lz, H_lp); dt = time.time() - t
        guardar_png(out / f"pozo_ojo_{tag}_lienzo.png", lz); guardar_png(out / f"pozo_ojo_{tag}_proj.png", pr)
        print(json.dumps({"ojo": ojo.tolist(), "fuente": fuente, "render_ms": round(dt * 1000, 1),
                          "lienzo": str(out / f"pozo_ojo_{tag}_lienzo.png"), "proj": str(out / f"pozo_ojo_{tag}_proj.png")}), flush=True)
        return 0

    if a.ojo is None and a.cabeza is None:
        print("falta --ojo o --cabeza"); return 2
    if not a.sin_rojo:
        get_json(a.feed + "/rojo?on=0")
    live = out / "pozo_live.png"
    t_end = time.time() + a.seconds if a.seconds > 0 else None
    last_ojo = None; last_send = 0.0; n_render = 0; n_send = 0; n_err = 0; periodo = 1.0 / max(a.hz, 0.5)
    try:
        while t_end is None or time.time() < t_end:
            t = time.time()
            ojo, ok, fuente = ojo_actual()
            if ojo is not None and (last_ojo is None or np.linalg.norm(ojo - last_ojo) > a.umbral_mm or t - last_send > a.overlay_s * 0.5):
                if last_ojo is None or np.linalg.norm(ojo - last_ojo) > a.umbral_mm:
                    lz = pozo.render(ojo); pr = a_proyector(lz, H_lp); guardar_png(live, pr); n_render += 1; last_ojo = ojo
                try:
                    runtime_cmd(a.runtime, "overlay", path=str(live.resolve()), seconds=a.overlay_s); n_send += 1; last_send = t
                except Exception as e:  # noqa: BLE001
                    n_err += 1
                    if n_err <= 3:
                        print("overlay:", e, flush=True)
            dt = periodo - (time.time() - t)
            if dt > 0:
                time.sleep(dt)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            runtime_cmd(a.runtime, "overlay", off=True)
        except Exception:  # noqa: BLE001
            pass
        if not a.sin_rojo:
            get_json(a.feed + "/rojo?on=1")
    print(json.dumps({"renders": n_render, "overlays": n_send, "errores": n_err, "ultimo_ojo": None if last_ojo is None else last_ojo.tolist()}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

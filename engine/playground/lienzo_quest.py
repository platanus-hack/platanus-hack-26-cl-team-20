"""Lienzo métrico de la mesa con el Quest 3 como cámara (9-nov-2026): plano de la mesa rectificado a `mm_por_px`
(0,4 por defecto), con Max en el borde de ARRIBA (igual que el lienzo de la Osmo: rot 180 para leer derecho).

    .venv/bin/python -m playground.lienzo_quest construir [--calib captures/quest-cal-fija] [--mm-px 0.4] [--scan captures/mesa-quest]
    .venv/bin/python -m playground.lienzo_quest ver [--calib …] [--img cam_full.jpg]       # lienzo de un cuadro, para mirarlo

Espacios:
  · cámara: cuadro completo de la cámara izquierda del Quest, 1728x1728 (el /cam.mjpg viene a la mitad: x_864 = x_1728/2).
  · mesa: marco del ancla MRUK TABLE en mm (origen en su centro, x = e1, y = e2); en la cámara x va a la derecha e y hacia
    abajo, y Max se sienta arriba (lejos del proyector).
  · lienzo: (i, j) px → mesa (x0 + s·i, y0 + s·j) mm, s = mm_por_px; cubre la caja de la huella del proyector.
  · contenido: la ventana del proyector, 1900x1040 (playground/ventana_proyector.py, :8793).

Qué guarda en <calib>/lienzo_quest.json (+ lienzo_quest.npz):
  · H_lienzo_to_proj: lienzo → contenido (H_mesa_to_proj · T_lienzo→mesa). La salida se compone en el contenido con esta H
    y después la corrección de lazo cerrado lazo_corr.npz (remap en el contenido, de calib_quest_proyector.py lazo).
  · H_cam_to_lienzo: cámara 1728 → lienzo por geometría (pose de la cámara + plano de la mesa, exacto en el plano, sin
    huecos). Es el modo por defecto: el lazo cerrado ya obligó al proyector a caer donde la cámara (con esta misma
    geometría) ve el punto, así que cámara→lienzo geométrico + lazo_corr es la cadena coherente.
  · mapx/mapy (npz): remap denso cámara(1728)→lienzo. Modo `geo`: la misma H muestreada. Modo `denso`: el mapa del gray
    code (cámara→contenido, captures/quest-cal-2*/mapa_cam_proj.npz) compuesto con contenido→mesa, como corrección
    residual suavizada (atípicos > 3 mm fuera: son paralaje de objetos sobre la mesa al momento de la captura); los
    huecos se rellenan por interpolación (convolución normalizada) y lejos de los datos queda la geometría. Úsalo sin
    lazo_corr. En los dos modos se reporta cuánto difieren el gray code y la geometría (mm).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
CALIB = REPO / "captures" / "quest-cal-fija"
LADO_CAM = 1728


def _j(p: Path):
    return json.loads(Path(p).read_text())


def plano_mesa(calib: Path) -> dict:
    g = _j(calib / "geometria.json")["mesa"]
    return {k: np.array(v, float) if isinstance(v, list) else v for k, v in g.items()}


def H_cam_a_mesa(calib: Path) -> np.ndarray:
    """Homografía cámara 1728 → mesa mm por geometría: rayos de la cámara (pose fija + intrínsecos) ∩ plano de la mesa."""
    sys.path.insert(0, str(REPO))
    from playground.calib_quest_proyector import Cam, interseca_mesa, mesa_mm
    cam = Cam(_j(calib / "cam_pose.json"), LADO_CAM)
    mesa = plano_mesa(calib)
    g = np.stack(np.meshgrid(np.linspace(200, 1500, 14), np.linspace(450, 1500, 14)), -1).reshape(-1, 2)
    mm = mesa_mm(interseca_mesa(cam, g, mesa), mesa)
    H, _ = cv2.findHomography(g, mm, 0)
    err = np.linalg.norm(cv2.perspectiveTransform(g.reshape(-1, 1, 2), H).reshape(-1, 2) - mm, axis=1).max()
    if err > 0.01:
        raise RuntimeError(f"la cámara no es pinhole pura (error de la H {err:.3f} mm): falta modelar la distorsión")
    return H


def mapa_denso_de(calib: Path) -> Path | None:
    """El mapa cámara→contenido del gray code más nuevo cuya pose de cámara calza con la de la calibración (≤ 2 mm)."""
    pose = np.array(_j(calib / "cam_pose.json")["m4"], float)
    cands = sorted(REPO.glob("captures/quest-cal-2*/mapa_cam_proj.npz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for c in cands:
        try:
            m4 = np.array(_j(c.parent / "cam_pose.json")["m4"], float)
        except Exception:  # noqa: BLE001
            continue
        if np.linalg.norm(m4[:3, 3] - pose[:3, 3]) < 0.002 and np.abs(m4[:3, :3] - pose[:3, :3]).max() < 0.002:
            return c
    return None


def construir(calib: Path = CALIB, mm_px: float = 0.4, margen_mm: float = 0.0, modo: str = "geo", log=print) -> dict:
    calib = Path(calib)
    Hj = _j(calib / "H_proj_to_mesa.json")
    Hpm = np.array(Hj["H_proj_to_mesa"], float); Hmp = np.linalg.inv(Hpm)
    cal = _j(calib / "calib_proyector_mesa.json")
    PW, PH = int(cal["proyector"]["ancho"]), int(cal["proyector"]["alto"])
    # huella del contenido (bordes de píxel) en la mesa
    esq_p = np.array([[-0.5, -0.5], [PW - 0.5, -0.5], [PW - 0.5, PH - 0.5], [-0.5, PH - 0.5]], float)
    hue = cv2.perspectiveTransform(esq_p.reshape(-1, 1, 2), Hpm).reshape(-1, 2)
    x0, y0 = hue.min(0) - margen_mm; x1, y1 = hue.max(0) + margen_mm
    lw, lh = int(np.ceil((x1 - x0) / mm_px)), int(np.ceil((y1 - y0) / mm_px))
    T = np.array([[mm_px, 0, x0], [0, mm_px, y0], [0, 0, 1.0]])                   # lienzo px (centros) → mesa mm
    Hlp = Hmp @ T
    Hcm = H_cam_a_mesa(calib)
    Hcl = np.linalg.inv(T) @ Hcm
    Hlc = np.linalg.inv(Hcl)
    # remap geométrico cámara→lienzo (para cada px del lienzo, de qué px de la cámara 1728 sale)
    gx, gy = np.meshgrid(np.arange(lw, dtype=np.float32), np.arange(lh, dtype=np.float32))
    pts = np.stack([gx, gy], -1).reshape(-1, 1, 2).astype(np.float64)
    cxy = cv2.perspectiveTransform(pts, Hlc).reshape(lh, lw, 2)
    mapx, mapy = cxy[..., 0].astype(np.float32), cxy[..., 1].astype(np.float32)
    # gray code: cuánto difiere de la geometría (y, en modo denso, la corrección residual suavizada)
    densa = mapa_denso_de(calib); comp = None
    if densa is not None:
        z = np.load(densa); v = z["valido"]
        ys, xs = np.nonzero(v)
        P = np.stack([z["px"][v], z["py"][v]], 1).astype(np.float64)
        m_d = cv2.perspectiveTransform(P.reshape(-1, 1, 2), Hpm).reshape(-1, 2)              # el gray code dice: cae aquí
        m_g = cv2.perspectiveTransform(np.stack([xs, ys], 1).astype(np.float64).reshape(-1, 1, 2), Hcm).reshape(-1, 2)  # la cámara ve aquí
        r = m_d - m_g; e = np.linalg.norm(r, axis=1)
        comp = {"mapa": str(densa.relative_to(REPO)), "pixeles": int(len(e)),
                "mm": {k: round(float(np.percentile(e, q)), 2) for k, q in (("mediana", 50), ("p90", 90), ("p99", 99))},
                "atipicos_3mm_frac": round(float((e > 3).mean()), 4)}
        # campo residual en el lienzo: r(m) donde la cámara ve m_g; atípicos fuera; convolución normalizada σ = 15 mm
        ok = e < 3.0
        Lg = (m_g[ok] - [x0, y0]) / mm_px
        acc = np.zeros((lh, lw, 3), np.float32)
        ii = np.clip(np.round(Lg).astype(int), 0, [lw - 1, lh - 1])
        np.add.at(acc, (ii[:, 1], ii[:, 0], 0), r[ok, 0]); np.add.at(acc, (ii[:, 1], ii[:, 0], 1), r[ok, 1]); np.add.at(acc, (ii[:, 1], ii[:, 0], 2), 1.0)
        sig = 15.0 / mm_px
        acc = cv2.GaussianBlur(acc, (0, 0), sig)
        peso = acc[..., 2]
        rx = np.where(peso > 1e-3, acc[..., 0] / np.maximum(peso, 1e-9), 0); ry = np.where(peso > 1e-3, acc[..., 1] / np.maximum(peso, 1e-9), 0)
        # lejos de los datos la corrección se apaga (peso relativo) y queda la geometría pura
        cobertura = np.clip(peso / max(1e-9, np.percentile(peso[peso > 0], 50)), 0, 1)
        rx, ry = (rx * cobertura).astype(np.float32), (ry * cobertura).astype(np.float32)
        comp["residuo_suave_mm"] = {"max": round(float(np.hypot(rx, ry).max()), 2), "mediana": round(float(np.median(np.hypot(rx, ry)[cobertura > 0.5])), 2)}
        comp["huecos_en_huella_frac"] = round(float((cobertura < 0.5).mean()), 3)
        if modo == "denso":
            # lienzo L (mesa m) ← cámara c con g(c) + r = m  →  c = g⁻¹(m − r)
            mm_ = np.stack([x0 + mm_px * gx - rx, y0 + mm_px * gy - ry], -1).reshape(-1, 1, 2).astype(np.float64)
            cxy = cv2.perspectiveTransform(mm_, np.linalg.inv(Hcm)).reshape(lh, lw, 2)
            mapx, mapy = cxy[..., 0].astype(np.float32), cxy[..., 1].astype(np.float32)
        log(f"gray code vs geometría: mediana {comp['mm']['mediana']} mm, p90 {comp['mm']['p90']} mm, p99 {comp['mm']['p99']} mm "
            f"({comp['atipicos_3mm_frac'] * 100:.1f} % > 3 mm = paralaje de objetos); residuo suave máx {comp['residuo_suave_mm']['max']} mm")
    elif modo == "denso":
        raise RuntimeError("modo denso: no encuentro un mapa_cam_proj.npz con la misma pose de cámara")
    hue_l = (hue - [x0, y0]) / mm_px
    lazo = calib / "lazo_corr.npz"
    info = {
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"), "calib": str(calib.relative_to(REPO)) if calib.is_relative_to(REPO) else str(calib),
        "modo": modo, "lienzo_px": [lw, lh], "mm_por_px": mm_px, "superficie_mm": [round(lw * mm_px, 1), round(lh * mm_px, 1)],
        "origen_mesa_mm": [round(float(x0), 3), round(float(y0), 3)],
        "T_lienzo_to_mesa": T.tolist(), "H_lienzo_to_proj": Hlp.tolist(), "H_proj_to_lienzo": np.linalg.inv(Hlp).tolist(),
        "H_cam_to_lienzo": Hcl.tolist(), "H_cam_to_mesa": Hcm.tolist(), "camara_px": [LADO_CAM, LADO_CAM],
        "salida_px": [PW, PH], "huella_lienzo_tl_tr_br_bl": hue_l.round(1).tolist(), "huella_mm": hue.round(1).tolist(),
        "rect_proj_tl_tr_br_bl": cv2.perspectiveTransform(np.array([[[0, 0]], [[lw - 1, 0]], [[lw - 1, lh - 1]], [[0, lh - 1]]], float), Hlp).reshape(-1, 2).round(1).tolist(),
        "lazo_corr": str(lazo.name) if lazo.exists() else None, "gray_code_vs_geometria": comp,
        "max_en": "arriba (borde y=0 del lienzo): rot 180 para leer derecho, mouse user_edge top",
        "convenciones": "px con centros enteros (OpenCV); cámara = cuadro completo 1728 (x_864 = x_1728/2 por decimación)",
        "fuente": "playground/lienzo_quest.py construir",
    }
    (calib / "lienzo_quest.json").write_text(json.dumps(info, indent=1, ensure_ascii=False))
    np.savez_compressed(calib / "lienzo_quest.npz", mapx=mapx, mapy=mapy)
    log(f"lienzo {lw}x{lh} px a {mm_px} mm/px = {info['superficie_mm'][0]}x{info['superficie_mm'][1]} mm; modo {modo}; "
        f"escrito {calib / 'lienzo_quest.json'} + .npz")
    return info


def cargar(calib: Path = CALIB) -> dict:
    """lienzo_quest.json + maps del npz + lazo_corr (si existe), listo para el feed."""
    calib = Path(calib)
    info = _j(calib / "lienzo_quest.json")
    out = {"info": info, "mapx": None, "mapy": None, "lazo": None}
    if (calib / "lienzo_quest.npz").exists():
        with np.load(calib / "lienzo_quest.npz") as z:
            out["mapx"], out["mapy"] = z["mapx"].astype(np.float32), z["mapy"].astype(np.float32)
    if info.get("lazo_corr") and (calib / info["lazo_corr"]).exists():
        with np.load(calib / info["lazo_corr"]) as z:
            out["lazo"] = (z["mapx"].astype(np.float32), z["mapy"].astype(np.float32))
    return out


def mapa_para(mapx: np.ndarray, mapy: np.ndarray, ancho_cuadro: int) -> tuple[np.ndarray, np.ndarray]:
    """Remap de la cámara 1728 llevado a un cuadro de `ancho_cuadro` px (la app reduce por decimación: x = x_1728 · k)."""
    k = ancho_cuadro / float(LADO_CAM)
    if abs(k - 1) < 1e-9:
        return mapx, mapy
    return (mapx * k).astype(np.float32), (mapy * k).astype(np.float32)


class AlturaMalla:
    """Altura (mm sobre la mesa) de lo que ve un px del lienzo según la malla MRUK de la sala (malla.json de la
    calibración): rayo cámara → punto del plano, contra los triángulos que están sobre la huella (Möller–Trumbore en
    numpy, ~1 ms por decena de rayos). OJO: la malla es la del Space Setup (estática, resolución de varios cm; /depth.json
    de la app es un raycast contra la misma, sin Depth API en Spatial SDK 0.13.2): no ve manos ni objetos que llegaron
    después. Para la punta del dedo da la superficie que tiene debajo, no el dedo."""

    def __init__(self, calib: Path = CALIB):
        calib = Path(calib)
        info = _j(calib / "lienzo_quest.json"); mesa = plano_mesa(calib)
        m = _j(calib / "malla.json")["mruk"]
        P = np.asarray(m["pos"], np.float64).reshape(-1, 3, 3)                       # lista de triángulos (sin índices)
        def a_mesa(X):
            d = X - mesa["c"]
            return np.stack([d @ mesa["e1"], d @ mesa["e2"], d @ mesa["n"]], -1) * 1000.0
        T = a_mesa(P)                                                                  # (n, 3, 3) en mm: x, y, altura
        x0, y0 = info["origen_mesa_mm"]; ancho, alto = info["superficie_mm"]
        lo = T.min(1); hi = T.max(1)
        sel = (hi[:, 0] > x0 - 150) & (lo[:, 0] < x0 + ancho + 150) & (hi[:, 1] > y0 - 150) & (lo[:, 1] < y0 + alto + 150) \
            & (hi[:, 2] > 2.0) & (lo[:, 2] < 800.0)
        self.T = T[sel]
        self.O = a_mesa(np.array(_j(calib / "cam_pose.json")["m4"], float)[:3, 3][None])[0]
        self.Tl2m = np.array(info["T_lienzo_to_mesa"], float)
        self.n = int(sel.sum())

    def altura(self, pts_lienzo) -> list[float | None]:
        pts = np.asarray(pts_lienzo, np.float64).reshape(-1, 2)
        if not len(pts):
            return []
        Pm = pts @ self.Tl2m[:2, :2].T + self.Tl2m[:2, 2]
        D = np.c_[Pm, np.zeros(len(Pm))] - self.O                                     # rayo: O + t·D, t = 1 en la mesa
        if not self.n:
            return [0.0] * len(pts)
        v0, e1, e2 = self.T[:, 0], self.T[:, 1] - self.T[:, 0], self.T[:, 2] - self.T[:, 0]
        out = []
        for d in D:
            pv = np.cross(d, e2); det = np.einsum("ij,ij->i", e1, pv)
            ok = np.abs(det) > 1e-9; inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
            tv = self.O - v0; u = np.einsum("ij,ij->i", tv, pv) * inv
            qv = np.cross(tv, e1); v = (qv @ d) * inv; t = np.einsum("ij,ij->i", e2, qv) * inv
            hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0.05) & (t < 0.999)
            if not hit.any():
                out.append(0.0); continue
            th = float(t[hit].min())
            out.append(round(float((self.O + th * d)[2]), 1))
        return out


def superficie_compat(info: dict) -> dict:
    """Lo que las herramientas viejas leen de <scan>/superficie.json (teclado_fit, teclado_manual, pad_*)."""
    return {"lienzo_px": info["lienzo_px"], "mm_por_px": info["mm_por_px"], "superficie_mm": info["superficie_mm"],
            "H_lienzo_to_proj": info["H_lienzo_to_proj"], "rect_proj_tl_tr_br_bl": info["rect_proj_tl_tr_br_bl"],
            "proyector_px": info["salida_px"], "fuente": f"generado desde {info['calib']}/lienzo_quest.json ({info['fecha']})",
            "nota": "espacio del proyector = contenido de la ventana 1900x1040 (ventana_proyector.py :8793), no el runtime 1920x1080"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("accion", choices=["construir", "ver"])
    p.add_argument("--calib", default=str(CALIB)); p.add_argument("--mm-px", type=float, default=0.4)
    p.add_argument("--margen-mm", type=float, default=0.0); p.add_argument("--modo", choices=["geo", "denso"], default="geo")
    p.add_argument("--scan", default=None, help="carpeta de trabajo del feed: le deja superficie.json compatible")
    p.add_argument("--img", default=None, help="ver: cuadro de la cámara (si no, /cam_full.jpg del Quest por USB)")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    calib = Path(a.calib)
    if a.accion == "construir":
        info = construir(calib, a.mm_px, a.margen_mm, a.modo)
        if a.scan:
            sd = Path(a.scan); sd.mkdir(parents=True, exist_ok=True)
            (sd / "superficie.json").write_text(json.dumps(superficie_compat(info), indent=1, ensure_ascii=False))
            print(f"{sd / 'superficie.json'} escrito")
        return 0
    L = cargar(calib); info = L["info"]; lw, lh = info["lienzo_px"]
    if a.img:
        fr = cv2.imread(a.img)
    else:
        from playground.quest_cam import USB, cuadro_full
        fr, _ = cuadro_full(USB)
    mx, my = mapa_para(L["mapx"], L["mapy"], fr.shape[1])
    lz = cv2.remap(fr, mx, my, cv2.INTER_LINEAR)
    hue = np.array(info["huella_lienzo_tl_tr_br_bl"], np.float64)
    cv2.polylines(lz, [np.round(hue).astype(np.int32).reshape(-1, 1, 2)], True, (0, 200, 255), 2, cv2.LINE_AA)
    for xm in range(0, int(lw * info["mm_por_px"]), 100):                # rejilla de 10 cm
        cv2.line(lz, (int(xm / info["mm_por_px"]), 0), (int(xm / info["mm_por_px"]), lh), (60, 60, 60), 1)
    for ym in range(0, int(lh * info["mm_por_px"]), 100):
        cv2.line(lz, (0, int(ym / info["mm_por_px"])), (lw, int(ym / info["mm_por_px"])), (60, 60, 60), 1)
    out = a.out or str(calib / "lienzo_quest_ver.jpg")
    cv2.imwrite(out, lz); print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

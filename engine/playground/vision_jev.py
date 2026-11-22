"""Agente de visión con Jev (TypeSafe jev-1.14.0 por el Vercel AI Gateway, retención cero), con el mismo contrato que
`playground/vision_vlm.describir` (Sonnet por CLI):

    describir(path, ...) → {"objetos": [{"nombre", "caja": [x0, y0, x1, y1], "centro": [x, y], "confianza"}], "segundos"}

Cajas en px de la imagen de `path` (o de `size`). Además devuelve "jev" con llamadas, tokens, costo y el detalle crudo.
Max autorizó mandar a Jev imágenes de la mesa (8-nov-2026).

Jev no escribe texto: decide. El contrato de imagen (docs.typesafe.ai/images y @ai-sdk/gateway 4.0.138) es
POST /v4/ai/decision-model con stateParts [{type: json, value: {mesa: "[mesa-1]"}}, {type: file, mediaType:
image/jpeg, data: {type: data, data: <base64>}, providerOptions: {typesafe: {reference: "[mesa-1]"}}}] y preguntas
choice / score / boolean / **box** (nativa en Jev 1.14: bordes top/bottom/left/right en fracciones de la imagen, con
confianza; siempre devuelve una caja, aunque el objeto no esté, por eso va con un Noul al lado). Imágenes PNG/JPEG de
280 a 1568 px por lado. Ver jev.decide().

Pasos:
 1. Una llamada sobre el lienzo (≤ `ancho` px): por cada clase del vocabulario de la mesa, Noul «¿hay un X en `mesa`?»
    + Box «el X en `mesa`». Se acepta si P(hay) ≥ `umbral` y la confianza de la caja ≥ `umbral_caja`; entre clases que
    marcan la misma cosa (IoU > 0,5) queda la de mayor P(hay)·confianza.
 2. Otra llamada con todo lo aceptado tapado con el color de la mesa (`rondas_extra`), solo para las clases que se
    repiten (hoja, mouse, cable, libreta, mano) y las que Jev ve pero cuya caja se perdió (p. ej. «teclado» cae sobre
    el teclado del laptop y lo suprime la caja del laptop).
 3. Con el catálogo SAM base de la carpeta (`catalogo_dir`/catalog.json, el que deja segmentacion_base): las regiones
    SAM que ninguna caja explica se nombran con una llamada por recorte (≥ 280 px, con contexto en mm), en paralelo:
    Choice sobre el vocabulario + «fondo» (se descarta) + «otro» (→ «objeto <color>»). Y (`ajustar_sam`, por defecto)
    cada caja de Jev se ajusta a la unión de las máscaras SAM que caen dentro (IoU contra Sonnet 0,83 → 0,87).

    .venv/bin/python -m playground.vision_jev captures/<scan>/items/<fecha>/lienzo_960.jpg [--catalogo captures/<scan>/items/<fecha>]
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from . import jev as jv

# (nombre de salida, en español como los da Sonnet; descripción para Jev; máximo de instancias)
VOCAB: list[tuple[str, str, int]] = [
    ("hoja", "a loose sheet of paper", 3),
    ("libreta", "a closed notebook or booklet", 2),
    ("notas adhesivas", "a pad of sticky notes", 1),
    ("teclado", "a computer keyboard", 1),
    ("mouse", "a computer mouse", 2),
    ("laptop", "a laptop computer", 1),
    ("monitor", "a computer monitor or screen", 1),
    ("teléfono", "a mobile phone", 1),
    ("control", "a game controller or remote control", 1),
    ("cable", "a cable or cord", 2),
    ("vaso", "a drinking glass", 1),
    ("taza", "a mug or cup", 1),
    ("botella", "a bottle", 1),
    ("moledor de hierba", "a round herb grinder", 1),
    ("bolsa", "a bag or pouch", 1),
    ("papel de fumar", "a small pack of rolling papers", 1),
    ("encendedor", "a lighter", 1),
    ("lápiz", "a pen or pencil", 1),
    ("vape", "a vape pen", 1),
    ("cargador", "a charger or power adapter", 1),
    ("audífonos", "headphones or earbuds", 1),
    ("moneda o tapa", "a coin or bottle cap", 1),
    ("cinta adhesiva", "a roll of tape", 1),
    ("mano", "a human hand", 2),
]
COLORES = {"blanco": "white", "negro": "black", "gris": "gray", "plateado": "silver or metallic", "rojo": "red",
           "naranja": "orange", "amarillo": "yellow", "verde": "green", "celeste": "light blue or turquoise", "azul": "blue",
           "morado": "purple", "rosado": "pink", "café": "brown", "transparente": "transparent or clear"}


# ───────────────────────── utilidades ─────────────────────────

def _jpg(img: np.ndarray, calidad: int = 90) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, calidad])   # JPEG baseline (lo que pide TypeSafe)
    if not ok:
        raise RuntimeError("no pude codificar JPEG")
    return buf.tobytes()


def _a_rango(img: np.ndarray) -> np.ndarray:
    """Lleva la imagen a 280-1568 px por lado (escala, y si una proporción extrema lo impide, rellena con el borde)."""
    h, w = img.shape[:2]
    s = min(1.0, jv.IMG_LADO_MAX / max(h, w))
    s = max(s, jv.IMG_LADO_MIN / min(h, w)) if min(h, w) * s < jv.IMG_LADO_MIN else s
    if abs(s - 1.0) > 1e-3:
        img = cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    h, w = img.shape[:2]
    if min(h, w) < jv.IMG_LADO_MIN or max(h, w) > jv.IMG_LADO_MAX:
        img = img[:jv.IMG_LADO_MAX, :jv.IMG_LADO_MAX]
        h, w = img.shape[:2]
        img = cv2.copyMakeBorder(img, 0, max(0, jv.IMG_LADO_MIN - h), 0, max(0, jv.IMG_LADO_MIN - w), cv2.BORDER_REPLICATE)
    return img


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def _dentro(a, b) -> float:
    """Fracción del área de `a` que cae dentro de `b`."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy / max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))


def _caja_norm(b: dict) -> list[float]:
    return [b["left"], b["top"], b["right"], b["bottom"]]


def _q_clase(i: int, desc: str) -> dict:
    return {f"h{i:02d}": {"type": "noul", "instructions": f"Is there {desc} in `mesa` (a top-down photo of a desk)?"},
            f"b{i:02d}": {"type": "box", "instructions": f"{desc} in `mesa`"}}


# ───────────────────────── Jev sobre el lienzo ─────────────────────────

def _ronda(img: np.ndarray, clases: list[int], timeout: float, escala: str = "") -> tuple[list[dict], dict | None]:
    """Una llamada: Noul + Box por clase sobre `img`. → (detecciones crudas en fracciones, respuesta)."""
    qs: dict = {}
    for i in clases:
        qs.update(_q_clase(i, VOCAB[i][1]))
    estado = {"mesa": "[mesa-1]"}
    if escala:
        estado["escala"] = escala
    partes = [{"type": "json", "value": estado}, jv.parte_imagen(_jpg(img), "[mesa-1]")]
    r = jv.decide(partes, qs, timeout=timeout)
    if r is None:
        return [], None
    out = []
    for i in clases:
        b = r["answers"][f"b{i:02d}"]
        if b.get("type") != "box":
            continue
        out.append({"clase": i, "nombre": VOCAB[i][0], "p": r["answers"][f"h{i:02d}"].get("noul", 0.0),
                    "conf": b["confidence"], "caja": _caja_norm(b["box"])})
    return out, r


def _filtrar(dets: list[dict], umbral: float, umbral_caja: float) -> list[dict]:
    """Umbral + supresión entre clases que marcan la misma cosa (queda la de mayor P·conf)."""
    ok = sorted((d for d in dets if d["p"] >= umbral and d["conf"] >= umbral_caja), key=lambda d: -d["p"] * d["conf"])
    out: list[dict] = []
    for d in ok:
        if all(iou(d["caja"], e["caja"]) <= 0.5 for e in out):
            out.append(d)
    return out


def _tapar(img: np.ndarray, cajas: list[list[float]]) -> np.ndarray:
    """Tapa cajas (fracciones) con el color mediano de la imagen (la mesa), para buscar la siguiente instancia."""
    out = img.copy(); h, w = img.shape[:2]
    color = tuple(int(v) for v in np.median(img.reshape(-1, 3), axis=0))
    for c in cajas:
        x0, y0, x1, y1 = int(c[0] * w) - 2, int(c[1] * h) - 2, int(np.ceil(c[2] * w)) + 2, int(np.ceil(c[3] * h)) + 2
        cv2.rectangle(out, (max(0, x0), max(0, y0)), (min(w - 1, x1), min(h - 1, y1)), color, -1)
    return out


# ───────────────────────── catálogo SAM base ─────────────────────────

def _sam_base(catalogo_dir: Path, w: int, h: int) -> list[dict]:
    """Máscaras de catalog.json (SAM automático) reducidas a (w, h), sin la mesa ni lo enorme o diminuto."""
    try:
        cat = json.loads((catalogo_dir / "catalog.json").read_text())
    except (OSError, ValueError):
        return []
    out = []
    for it in cat.get("items", []):
        if it.get("label") in ("mesa",):
            continue
        m = cv2.imread(str(catalogo_dir / it["mask"]), cv2.IMREAD_GRAYSCALE)
        if m is None:
            continue
        sx, sy = w / m.shape[1], h / m.shape[0]
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST) > 127
        a = int(m.sum())
        if a < 0.0007 * w * h or a > 0.15 * w * h:
            continue
        ys, xs = np.nonzero(m)
        out.append({"label": it.get("label"), "m": m, "area": a, "bbox": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
                    "bbox_hd": [v for v in it["bbox"]], "escala_hd": (sx, sy)})
    return out


def _no_explicadas(sam: list[dict], cajas_px: list[list[float]], w: int, h: int, n: int) -> list[dict]:
    """Regiones SAM que ninguna caja aceptada explica (< 35 % de la máscara dentro de alguna caja), sin repetidas."""
    cubierto = np.zeros((h, w), bool)
    for c in cajas_px:
        x0, y0, x1, y1 = (int(round(v)) for v in c)
        cubierto[max(0, y0 - 4):min(h, y1 + 4), max(0, x0 - 4):min(w, x1 + 4)] = True
    tomado = np.zeros((h, w), bool); out = []
    for s in sorted(sam, key=lambda s: -s["area"]):
        if (s["m"] & cubierto).sum() / s["area"] >= 0.35 or (s["m"] & tomado).sum() / s["area"] > 0.5:
            continue
        out.append(s); tomado |= s["m"]
        if len(out) >= n:
            break
    return out


def _ajustar(caja_px: list[float], sam: list[dict]) -> list[float] | None:
    """Unión de las máscaras SAM que caen ≥ 80 % dentro de la caja (holgura 3 %); None si no calza (IoU < 0,6)."""
    x0, y0, x1, y1 = caja_px; mx, my = 0.03 * (x1 - x0) + 3, 0.03 * (y1 - y0) + 3
    X0, Y0, X1, Y1 = int(x0 - mx), int(y0 - my), int(x1 + mx) + 1, int(y1 + my) + 1
    bb = None
    for s in sam:
        m = s["m"]; dentro = m[max(0, Y0):max(0, Y1), max(0, X0):max(0, X1)].sum()
        if dentro / s["area"] >= 0.8:
            b = s["bbox"]
            bb = b if bb is None else [min(bb[0], b[0]), min(bb[1], b[1]), max(bb[2], b[2]), max(bb[3], b[3])]
    if bb is None or iou(bb, caja_px) < 0.6:
        return None
    return [float(v) for v in bb]


MIN_CONF_RECORTE = 0.4
UMBRAL_CAJA_R2 = 0.4          # la 2.ª ronda exige más a la caja (benchmark 8-nov: precisión 73 → 76 % sin perder recall)


def _unir_vecinos(items: list[dict], hueco: float) -> list[dict]:
    """Une recortes del mismo nombre que se tocan (SAM parte un laptop en dos): caja unión, mayor confianza."""
    out: list[dict] = []
    for it in sorted(items, key=lambda x: -x["p"]):
        for o in out:
            a, b = o["caja_px"], it["caja_px"]
            if o["nombre"] == it["nombre"] and a[0] - hueco <= b[2] and b[0] - hueco <= a[2] and a[1] - hueco <= b[3] and b[1] - hueco <= a[3]:
                o["caja_px"] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                break
        else:
            out.append(dict(it))
    return out


def _nombrar_recorte(hd: np.ndarray, s: dict, mmpx: float, wh_hd: tuple[int, int], timeout: float) -> dict | None:
    """Una llamada por región SAM no explicada: recorte del lienzo HD con contexto (≥ 280 px) + Choice nombre y color."""
    sx, sy = s["escala_hd"]
    x0, y0, x1, y1 = s["bbox_hd"]
    bw, bh = x1 - x0, y1 - y0; pad = int(max(24, 0.35 * max(bw, bh)))
    X0, Y0, X1, Y1 = max(0, x0 - pad), max(0, y0 - pad), min(wh_hd[0], x1 + pad), min(wh_hd[1], y1 + pad)
    rec = _a_rango(hd[Y0:Y1, X0:X1])
    cx, cy = (x0 + x1) / 2 / wh_hd[0], (y0 + y1) / 2 / wh_hd[1]
    ctx = (f"Recorte de una foto cenital de un escritorio, centrado en un objeto de unos {bw * mmpx:.0f} × {bh * mmpx:.0f} mm "
           f"(x {cx:.2f}, y {cy:.2f} de la mesa)." if mmpx > 0 else "Recorte de una foto cenital de un escritorio, centrado en un objeto.")
    crit = {f"c{i:02d}": d for i, (_, d, _) in enumerate(VOCAB)}
    crit["fondo"] = "no object: bare desk surface, shadow, stain, reflection or projected light"
    crit["otro"] = "some other object that is not in this list"
    qs = {"nombre": {"type": "choice", "instructions": "What is the object at the center of `recorte`?", "criteria": crit},
          "color": {"type": "choice", "instructions": "Main color of the object at the center of `recorte`?",
                    "criteria": {k: v for k, v in COLORES.items()}}}
    r = jv.decide([{"type": "json", "value": {"recorte": "[obj-1]", "contexto": ctx}}, jv.parte_imagen(_jpg(rec), "[obj-1]")], qs, timeout=timeout)
    if r is None:
        return None
    a = r["answers"]["nombre"]; c = r["answers"]["color"]
    if a["choice"] == "fondo":
        nombre = None
    elif a["choice"] == "otro" or a["confidence"] < MIN_CONF_RECORTE:   # dudoso: mejor genérico que un nombre inventado
        nombre = f"objeto {c['choice']}"
    else:
        nombre = VOCAB[int(a["choice"][1:])][0]
    return {"nombre": nombre, "p": a["confidence"], "color": c["choice"], "caja_px": [float(v) for v in s["bbox"]], "r": r}


# ───────────────────────── describir ─────────────────────────

def describir(path: str | Path, modelo: str | None = None, timeout: float = 20.0, size: tuple[int, int] | None = None, *,
              catalogo_dir: str | Path | None = None, ancho: int = 960, umbral: float = 0.5, umbral_caja: float = 0.2,
              rondas_extra: int = 1, recortes: int = 8, ajustar_sam: bool = True, mm_por_px: float | None = None) -> dict:
    """Mismo formato que vision_vlm.describir. `modelo` se ignora salvo que empiece con "typesafe-ai/" (por defecto
    jev.MODELO_VISION). `timeout` es por llamada. Sin llave o si la primera llamada falla: objetos vacíos + "error"."""
    t0 = time.time()
    p = Path(path)
    img = cv2.imread(str(p))
    if img is None:
        return {"objetos": [], "segundos": 0.0, "error": f"no pude leer {p}"}
    if modelo and str(modelo).startswith("typesafe-ai/"):
        jv.MODELO_VISION = str(modelo)
    size = tuple(size) if size else (img.shape[1], img.shape[0])
    if img.shape[1] > ancho:
        img = cv2.resize(img, (ancho, round(img.shape[0] * ancho / img.shape[1])), interpolation=cv2.INTER_AREA)
    img = _a_rango(img)
    h, w = img.shape[:2]
    llamadas: list[dict] = []
    if not jv.disponible():
        return {"objetos": [], "segundos": round(time.time() - t0, 1), "error": "sin AI_GATEWAY_API_KEY"}

    cdir = Path(catalogo_dir) if catalogo_dir else None
    lienzo_w = 1920
    if cdir:
        try:
            sup = json.loads((cdir.parent.parent / "superficie.json").read_text())
            lienzo_w = int(sup.get("lienzo_px", [1920])[0])
            mm_por_px = float(sup.get("mm_por_px") or 0) if mm_por_px is None else mm_por_px
        except (OSError, ValueError, TypeError):
            pass
    mm_img = (mm_por_px or 0.0) * lienzo_w / w                     # mm por px de la imagen que ve Jev
    escala = f"`mesa` cubre unos {w * mm_img:.0f} × {h * mm_img:.0f} mm de escritorio" if mm_img > 0 else ""

    # 1) vocabulario completo sobre el lienzo
    crudas, r = _ronda(img, list(range(len(VOCAB))), timeout, escala)
    if r is None:
        return {"objetos": [], "segundos": round(time.time() - t0, 1), "error": jv.stats().get("ultimo_error")}
    llamadas.append(r)
    dets = _filtrar(crudas, umbral, umbral_caja)
    sam = _sam_base(cdir, w, h) if cdir else []
    hd = cv2.imread(str(cdir / "lienzo.jpg")) if cdir and (cdir / "lienzo.jpg").exists() else None

    # 2) segunda instancia de las clases que se repiten, y 3) regiones SAM sin explicar: en paralelo
    extra_dets: list[dict] = []
    nombrados: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut_r = None
        # 2.ª ronda: clases que se repiten y ya aparecieron, más las que Jev ve (P ≥ umbral) pero cuya caja se perdió
        # (confianza baja o se la ganó otra clase: «teclado» marca el del laptop): todo lo aceptado queda tapado
        aceptadas = {d["clase"] for d in dets}
        rep = sorted({c for c in aceptadas if VOCAB[c][2] > 1} | {c["clase"] for c in crudas if c["p"] >= umbral and c["clase"] not in aceptadas})
        if rondas_extra > 0 and rep:
            tapada = _tapar(img, [d["caja"] for d in dets])
            fut_r = ex.submit(_ronda, tapada, rep, timeout, escala)
        futs = []
        if sam and recortes > 0:
            fuente = hd if hd is not None else img
            wh_f = (fuente.shape[1], fuente.shape[0])
            for s in _no_explicadas(sam, [[d["caja"][0] * w, d["caja"][1] * h, d["caja"][2] * w, d["caja"][3] * h] for d in dets], w, h, recortes):
                if hd is None:                                     # sin HD: el recorte sale de la imagen reducida
                    s = dict(s, bbox_hd=s["bbox"], escala_hd=(1.0, 1.0))
                futs.append(ex.submit(_nombrar_recorte, fuente, s, (mm_por_px or 0.0) * lienzo_w / wh_f[0], wh_f, timeout))
        if fut_r is not None:
            c2, r2 = fut_r.result()
            if r2 is not None:
                llamadas.append(r2)
                for d in _filtrar(c2, umbral, max(umbral_caja, UMBRAL_CAJA_R2)):
                    if all(iou(d["caja"], e["caja"]) <= 0.3 and _dentro(d["caja"], e["caja"]) < 0.5 for e in dets + extra_dets):
                        d["ronda"] = 2; extra_dets.append(d)
        for f in futs:
            n_ = f.result()
            if n_ is not None:
                llamadas.append(n_.pop("r"))
                if n_["nombre"]:
                    nombrados.append(n_)

    # recortes que quedaron dentro de una caja aceptada (la 2.ª ronda corre en paralelo y puede explicarlos): fuera
    cajas_ok = [[d["caja"][0] * w, d["caja"][1] * h, d["caja"][2] * w, d["caja"][3] * h] for d in dets + extra_dets]
    nombrados = [n_ for n_ in nombrados if all(_dentro(n_["caja_px"], c) < 0.6 for c in cajas_ok)]
    nombrados = _unir_vecinos(nombrados, 0.01 * w)

    # salida en px de `size`
    fx, fy = size[0] / w, size[1] / h
    objetos, detalle = [], []
    for d in dets + extra_dets:
        cpx = [d["caja"][0] * w, d["caja"][1] * h, d["caja"][2] * w, d["caja"][3] * h]
        aj = _ajustar(cpx, sam) if (ajustar_sam and sam) else None
        c = aj or cpx
        objetos.append({"nombre": d["nombre"], "caja": [round(c[0] * fx), round(c[1] * fy), round(c[2] * fx), round(c[3] * fy)],
                        "centro": [round((c[0] + c[2]) / 2 * fx), round((c[1] + c[3]) / 2 * fy)], "confianza": round(d["p"], 2)})
        detalle.append({"nombre": d["nombre"], "fuente": "box" + ("+sam" if aj else ""), "p": d["p"], "conf_caja": d["conf"],
                        "ronda": d.get("ronda", 1), "caja_jev": [round(cpx[0] * fx), round(cpx[1] * fy), round(cpx[2] * fx), round(cpx[3] * fy)]})
    for n_ in nombrados:
        c = n_["caja_px"]
        objetos.append({"nombre": n_["nombre"], "caja": [round(c[0] * fx), round(c[1] * fy), round(c[2] * fx), round(c[3] * fy)],
                        "centro": [round((c[0] + c[2]) / 2 * fx), round((c[1] + c[3]) / 2 * fy)], "confianza": round(n_["p"], 2)})
        detalle.append({"nombre": n_["nombre"], "fuente": "recorte_sam", "p": n_["p"], "color": n_["color"]})
    tokens = sum(r_["usage"]["input_tokens"] for r_ in llamadas)
    costo_gw = [r_.get("costo_gateway_usd") for r_ in llamadas]
    return {"objetos": objetos, "segundos": round(time.time() - t0, 2),
            "jev": {"modelo": llamadas[0]["modelo"], "llamadas": len(llamadas), "tokens_in": tokens,
                    "costo_usd": round(tokens / 1e6 * jv.USD_POR_M_ENTRADA, 7),
                    "costo_gateway_usd": round(sum(c for c in costo_gw if c is not None), 7) if any(c is not None for c in costo_gw) else None,
                    "ms_llamadas": [r_["ms"] for r_ in llamadas], "imagen_px": [w, h], "detalle": detalle,
                    "crudas": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items()} for d in crudas]}}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("imagen"); ap.add_argument("--catalogo", default=None, help="carpeta items/<fecha> con catalog.json (SAM base)")
    ap.add_argument("--ajustar-sam", action="store_true")
    a = ap.parse_args(argv)
    d = describir(a.imagen, catalogo_dir=a.catalogo, ajustar_sam=a.ajustar_sam)
    d.get("jev", {}).pop("crudas", None)
    print(json.dumps(d, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

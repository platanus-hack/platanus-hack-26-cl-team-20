"""Benchmark offline de vision_jev (Jev 1.14 con imagen) contra el agente Sonnet, con los lienzos guardados.

    .venv/bin/python -m playground.vision_jev_bench                       # captures/dale-20261102-1511, todos los refinados
    .venv/bin/python -m playground.vision_jev_bench captures/<scan> --n 10 --sam --preview 6
    --forzar     vuelve a llamar a Jev aunque el resultado esté en la caché del benchmark
    --sam        además segmenta con MobileSAM las cajas de Jev e informa IoU de máscara contra las de Sonnet (lento)
    --tune       umbrales de la 1.ª llamada (P(hay) × confianza de caja) contra Sonnet, sin llamar de nuevo

Referencia por lienzo: agente.json (cajas de Sonnet en px de lienzo_960) y catalog_refinado.json (máscaras SAM que
salieron de esas cajas). Coincidencia = IoU de caja ≥ 0,5 (emparejamiento voraz). Resultados crudos de Jev en
/Volumes/Externo/cache/jev-ref/bench/<fecha>.json; preview en /Volumes/Externo/cache/jev-ref/preview_cajas_jev.jpg.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from . import jev as jv
from . import vision_jev as vj

REPO = Path(__file__).resolve().parent.parent
CACHE = Path("/Volumes/Externo/cache/jev-ref/bench")
PREVIEW = Path("/Volumes/Externo/cache/jev-ref/preview_cajas_jev.jpg")

# nombre → grupo comparable (en orden: lo específico antes que lo genérico)
GRUPOS = [("papel_fumar", ("papel de fumar", "papel de liar", "papel de armar", "papelillo", "cajetilla", "caja de papel", "paquete")),
          ("cable", ("cable", "cargador")),
          ("libreta", ("libreta", "cuaderno", "notas adhesivas", "notas")),
          ("papel", ("hoja", "papel", "página")),
          ("mouse", ("mouse", "ratón")),
          ("teclado", ("teclado",)),
          ("pantalla", ("laptop", "notebook", "monitor", "pantalla", "touchpad")),
          ("telefono", ("teléfono", "telefono", "celular")),
          ("control", ("control",)),
          ("vaso", ("vaso", "taza", "botella")),
          ("moledor", ("moledor",)),
          ("tapa", ("moneda", "tapa")),
          ("bolsa", ("bolsa", "snack")),
          ("mano", ("mano",)),
          ("objeto", ("objeto", "dispositivo"))]


def grupo(nombre: str) -> str:
    n = nombre.lower().replace("_", " ")
    for g, claves in GRUPOS:
        if any(c in n for c in claves):
            return g
    return n


def emparejar(a: list, b: list, umbral: float = 0.5) -> list[tuple[int, int, float]]:
    pares = sorted(((vj.iou(x, y), i, j) for i, x in enumerate(a) for j, y in enumerate(b)), reverse=True)
    ua, ub, out = set(), set(), []
    for v, i, j in pares:
        if v < umbral:
            break
        if i not in ua and j not in ub:
            ua.add(i); ub.add(j); out.append((i, j, v))
    return out


def _sam_sonnet(d: Path, w: int, h: int) -> list[tuple[str, list[float], np.ndarray]]:
    out = []
    for it in json.loads((d / "catalog_refinado.json").read_text())["items"]:
        m = cv2.imread(str(d / it["mask"]), cv2.IMREAD_GRAYSCALE)
        if m is None:
            continue
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST) > 127
        ys, xs = np.nonzero(m)
        if len(xs):
            out.append((it["label"], [float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)], m))
    return out


def _mobilesam():
    md = Path.home() / "hardware/models/slscan-segmentation"
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(md / "python"))
    from .appearance_prepare import MobileSAM
    return MobileSAM(md, "cpu", threads=4)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scan", nargs="?", default=str(REPO / "captures/dale-20261102-1511"))
    ap.add_argument("--n", type=int, default=0, help="máximo de lienzos (0 = todos)")
    ap.add_argument("--forzar", action="store_true"); ap.add_argument("--sam", action="store_true"); ap.add_argument("--tune", action="store_true")
    ap.add_argument("--preview", type=int, default=6, help="lienzos en el preview")
    ap.add_argument("--sin-ajuste", action="store_true", help="describir sin ajustar las cajas a SAM")
    a = ap.parse_args(argv)
    dirs = [p.parent for p in sorted(Path(a.scan).glob("items/*/catalog_refinado.json"))
            if all((p.parent / f).exists() for f in ("agente.json", "lienzo_960.jpg", "catalog.json"))]
    if a.n:
        dirs = dirs[:a.n]
    CACHE.mkdir(parents=True, exist_ok=True)
    if not jv.disponible() and not a.tune:
        print("Jev no disponible: falta AI_GATEWAY_API_KEY"); return 1
    sam_model = _mobilesam() if a.sam else None
    filas, todo = [], []
    for d in dirs:
        f = CACHE / f"{d.name}{'_crudo' if a.sin_ajuste else ''}.json"
        if f.exists() and not a.forzar:
            r = json.loads(f.read_text())
        elif a.tune:
            continue
        else:
            r = vj.describir(d / "lienzo_960.jpg", catalogo_dir=d, ajustar_sam=not a.sin_ajuste)
            f.write_text(json.dumps(r, ensure_ascii=False))
        son = json.loads((d / "agente.json").read_text())
        img = cv2.imread(str(d / "lienzo_960.jpg")); h, w = img.shape[:2]
        jb = [o["caja"] for o in r["objetos"]]; sb = [o["caja"] for o in son.get("objetos", [])]
        m = emparejar(jb, sb)
        nombres_ok = sum(grupo(r["objetos"][i]["nombre"]) == grupo(son["objetos"][j]["nombre"]) for i, j, _ in m)
        ss = _sam_sonnet(d, w, h)
        ms = emparejar(jb, [b for _, b, _ in ss])
        # cajas crudas de Jev (antes del ajuste a SAM), con las mismas respuestas
        aj = [x.get("caja_jev", o["caja"]) for o, x in zip(r["objetos"], r["jev"]["detalle"])]
        maj = emparejar(aj, sb)
        fila = {"fecha": d.name, "s_jev": r["segundos"], "s_sonnet": son.get("segundos"), "n_jev": len(jb), "n_son": len(sb),
                "match": len(m), "iou": float(np.mean([v for *_, v in m])) if m else None, "nombres_ok": nombres_ok,
                "iou_sam": float(np.mean([v for *_, v in ms])) if ms else None, "match_sam": len(ms), "n_sam": len(ss),
                "match_aj": len(maj), "iou_aj": float(np.mean([v for *_, v in maj])) if maj else None,
                "llamadas": r["jev"]["llamadas"], "tokens": r["jev"]["tokens_in"], "usd": r["jev"]["costo_gateway_usd"] or r["jev"]["costo_usd"]}
        if sam_model is not None and m:
            sam_model.set_image(img)
            ious = []
            for i, j, _ in ms:
                mk, _info = sam_model.predict([float(v) for v in jb[i]])
                ref = ss[j][2]
                inter = (mk & ref).sum(); uni = (mk | ref).sum()
                ious.append(inter / uni if uni else 0.0)
            fila["iou_mascara"] = float(np.mean(ious)) if ious else None
        filas.append(fila); todo.append((d, r, son))
        print(f"{d.name}  {fila['s_jev']:5.2f}s/{fila['s_sonnet'] or 0:5.1f}s  jev {fila['n_jev']:2d} son {fila['n_son']:2d}  "
              f"match {fila['match']:2d} IoU {fila['iou'] or 0:.2f}  nombres {nombres_ok}/{len(m)}  vsSAM {fila['iou_sam'] or 0:.2f}  "
              f"crudo {fila['match_aj']} {fila['iou_aj'] or 0:.2f}  {fila['llamadas']} llam {fila['tokens']} tok", flush=True)
    if not filas:
        print("sin lienzos"); return 1

    def med(k):
        v = [f[k] for f in filas if f.get(k) is not None]
        return float(np.mean(v)) if v else None
    n_j = sum(f["n_jev"] for f in filas); n_s = sum(f["n_son"] for f in filas); n_m = sum(f["match"] for f in filas)
    print(f"\n{len(filas)} lienzos · Jev {med('s_jev'):.2f} s (p95 {np.percentile([f['s_jev'] for f in filas], 95):.2f}) vs Sonnet {med('s_sonnet') or 0:.1f} s")
    print(f"objetos Jev {n_j} · Sonnet {n_s} · coinciden (IoU ≥ 0,5) {n_m} → recall vs Sonnet {n_m / max(1, n_s):.0%}, "
          f"precisión vs Sonnet {n_m / max(1, n_j):.0%} · IoU medio {med('iou'):.2f} · mismo nombre {sum(f['nombres_ok'] for f in filas) / max(1, n_m):.0%}")
    print(f"vs máscaras SAM de Sonnet: {sum(f['match_sam'] for f in filas)}/{sum(f['n_sam'] for f in filas)} · IoU caja {med('iou_sam'):.2f}"
          + (f" · IoU máscara (MobileSAM con cajas de Jev) {med('iou_mascara'):.2f}" if a.sam else ""))
    print(f"cajas crudas de Jev (sin ajuste SAM): coinciden {sum(f['match_aj'] for f in filas)} · IoU {med('iou_aj') or 0:.2f}")
    usd = sum(f["usd"] for f in filas); tok = sum(f["tokens"] for f in filas); ll = sum(f["llamadas"] for f in filas)
    print(f"costo US$ {usd:.5f} ({usd / len(filas):.6f}/lienzo, {usd / max(1, n_j):.7f}/objeto) · {ll} llamadas · {tok} tokens "
          f"({tok / len(filas):.0f}/lienzo)")
    (CACHE / "resumen.json").write_text(json.dumps(filas, ensure_ascii=False, indent=1))

    if a.tune:
        print("\numbral P(hay) × conf caja → coinciden / Jev (solo 1.ª llamada)")
        for up in (0.4, 0.5, 0.6, 0.7):
            for uc in (0.0, 0.2, 0.3, 0.4, 0.5):
                tj = tm = 0
                for d, r, son in todo:
                    img_wh = r["jev"]["imagen_px"]
                    dets = vj._filtrar([dict(c) for c in r["jev"]["crudas"]], up, uc)
                    jb = [[c["caja"][0] * img_wh[0], c["caja"][1] * img_wh[1], c["caja"][2] * img_wh[0], c["caja"][3] * img_wh[1]] for c in dets]
                    tj += len(jb); tm += len(emparejar(jb, [o["caja"] for o in son.get("objetos", [])]))
                print(f"  {up:.1f} × {uc:.1f}: {tm:4d} / {tj:4d}  recall {tm / max(1, n_s):.0%}  precisión {tm / max(1, tj):.0%}")

    # preview: lienzos con Jev (verde, nombre) y Sonnet (magenta)
    sel = sorted(todo, key=lambda t: -len(t[1]["objetos"]))[:max(1, a.preview)]
    tiles = []
    for d, r, son in sel:
        im = cv2.imread(str(d / "lienzo_960.jpg"))
        for o in son.get("objetos", []):
            x0, y0, x1, y1 = (int(v) for v in o["caja"]); cv2.rectangle(im, (x0, y0), (x1, y1), (255, 0, 255), 1, cv2.LINE_AA)
        for o, x in zip(r["objetos"], r["jev"]["detalle"]):
            x0, y0, x1, y1 = (int(v) for v in o["caja"]); col = (0, 255, 0) if x["fuente"].startswith("box") else (0, 200, 255)
            cv2.rectangle(im, (x0, y0), (x1, y1), col, 2, cv2.LINE_AA)
            t = o["nombre"].encode("ascii", "replace").decode() + f" {o['confianza']:.2f}"
            cv2.putText(im, t, (x0 + 3, max(12, y0 + 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(im, t, (x0 + 3, max(12, y0 + 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
        cv2.putText(im, f"{d.name}  jev {r['segundos']:.1f}s  sonnet {son.get('segundos') or 0:.0f}s", (8, im.shape[0] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
        tiles.append(cv2.resize(im, (960, 563)))
    while len(tiles) % 2:
        tiles.append(np.zeros_like(tiles[0]))
    grid = np.vstack([np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles), 2)])
    cv2.imwrite(str(PREVIEW), grid, [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(f"preview → {PREVIEW}  (verde = caja Jev, naranjo = recorte SAM nombrado por Jev, magenta = Sonnet)")
    return 0


if __name__ == "__main__":
    t0 = time.time()
    rc = main()
    print(f"({time.time() - t0:.0f} s)")
    sys.exit(rc)

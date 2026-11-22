"""Exporta los fixtures de la percepción para el port a Rust (crate pcx-escena-lab), con lo que responde este Python.

    uv run python tools/exportar_fixtures.py <carpeta destino>

Escribe imágenes (capturas reales y sintéticas, en JPEG calidad 92, como una cámara) y `spec.json` con la verdad y la salida de Python para
cada una. Los tests de Rust exigen calzar con la verdad y con Python: así el port no se aparta de la spec.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

AQUI = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AQUI))

from percepcion import intrusos as I  # noqa: E402
from percepcion import pastillero as P  # noqa: E402
from percepcion import sintetico as S  # noqa: E402
from percepcion import vaso as V  # noqa: E402

CAPTURAS = Path("/Volumes/Externo/superhuman-desk/capturas")
JPG = [cv2.IMWRITE_JPEG_QUALITY, 92]


def r(x, n=2):
    return None if x is None else round(float(x), n)


def grilla_dict(g: P.Grilla) -> dict:
    if not g.ok:
        return {"ok": False}
    return {
        "ok": True,
        "confianza": g.confianza,
        "angulo_deg": g.angulo_deg,
        "paso_mm": g.paso_mm,
        "centro_mm": g.centro_mm,
        "celdas": {c.dir: {"centro_mm": c.centro_mm, "color": c.color} for c in g.celdas},
    }


def main(destino: str):
    d = Path(destino)
    (d / "pastillero").mkdir(parents=True, exist_ok=True)
    (d / "vaso").mkdir(parents=True, exist_ok=True)
    (d / "intrusos").mkdir(parents=True, exist_ok=True)
    spec: dict = {"origen": "escenas/laboratorio (rama max/escena-lab), tools/exportar_fixtures.py", "pastillero": [], "vaso": {}, "intrusos": []}

    # ---- pastillero: capturas reales rectificadas (1 px = 1 mm)
    for k in (1, 2):
        nombre = f"mesa-cenital-mm-{k}.jpg"
        img = cv2.imread(str(CAPTURAS / nombre))
        cv2.imwrite(str(d / "pastillero" / nombre), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        img = cv2.imread(str(d / "pastillero" / nombre))  # lo que verá Rust
        spec["pastillero"].append({"archivo": f"pastillero/{nombre}", "real": True, "verdad_a1_mm": [651, 209], "verdad_angulo_deg": -28, "python": grilla_dict(P.detectar(img))})

    # ---- pastillero: sintéticos a varios giros
    for ang in (-28, 0, 40, 90, 150, -120, 179):
        img, verdad = S.pastillero(angulo_deg=ang, centro_mm=(250, 200), ancho_mm=500, alto_mm=400, semilla=ang % 7)
        nombre = f"sintetico{ang:+04d}.jpg"
        cv2.imwrite(str(d / "pastillero" / nombre), img, JPG)
        img = cv2.imread(str(d / "pastillero" / nombre))
        spec["pastillero"].append({"archivo": f"pastillero/{nombre}", "real": False, "verdad_a1_mm": [r(v) for v in verdad["A1"]], "verdad_d7_mm": [r(v) for v in verdad["D7"]], "python": grilla_dict(P.detectar(img))})

    # ---- rectificación: el cuadro crudo de la cámara + la homografía manual del README de capturas
    crudo = cv2.imread(str(CAPTURAS / "mesa-limpia-1.jpg"))
    cv2.imwrite(str(d / "pastillero" / "mesa-limpia-1.jpg"), crudo, [cv2.IMWRITE_JPEG_QUALITY, 95])
    crudo = cv2.imread(str(d / "pastillero" / "mesa-limpia-1.jpg"))
    src = np.float32([[771, 602], [322, 527], [352, 318], [803, 400]])
    dst = np.float32([[100, 100], [900, 100], [900, 463], [100, 463]])
    H = cv2.getPerspectiveTransform(src, dst)
    mm = P.rectificar(crudo, H.ravel().tolist(), 1000, 563)
    spec["rectificar"] = {"archivo": "pastillero/mesa-limpia-1.jpg", "matrix_px_to_mm": [float(v) for v in H.ravel()], "surface_mm": [1000, 563], "python": grilla_dict(P.detectar(mm))}

    # ---- vaso: calibración (vacío + 50 + 150) y niveles de prueba, líquido de color y casi transparente
    # «coca» imita la Coca-Cola de la demo: casi opaca y oscura.
    series = (
        ("color", (60, 40, 200), 0.55, (0, 10, 30, 56, 80, 120, 170, 220)),
        ("claro", (60, 40, 200), 0.12, (30, 120)),
        ("coca", (18, 28, 52), 0.88, (0, 10, 30, 47, 50, 53, 56, 120, 200)),
    )
    for nombre_serie, color, alfa, niveles in series:
        serie = {"alfa": alfa, "color_bgr": list(color), "vacio": f"vaso/{nombre_serie}-vacio.jpg", "calibracion": {}, "niveles": []}
        vacio = S.vaso(0, color_bgr=color, alfa=alfa, semilla=1).vacio
        cv2.imwrite(str(d / serie["vacio"]), vacio, JPG)
        fotos = {}
        for ml in (50, 150):
            f = S.vaso(ml, color_bgr=color, alfa=alfa, semilla=ml // 50 + 1).cuadro
            ruta = f"vaso/{nombre_serie}-cal-{ml}.jpg"
            cv2.imwrite(str(d / ruta), f, JPG)
            fotos[ml] = cv2.imread(str(d / ruta))
            serie["calibracion"][str(ml)] = ruta
        # Con agua casi transparente la zona automática no sirve (toma una mancha de refracción): se usa la del vaso
        # con colorante, que es el mismo vaso en el mismo encuadre.
        roi = spec["vaso"]["color"]["python_cal"]["roi_px"] if nombre_serie == "claro" else None
        cal = V.calibrar_con_lecturas(cv2.imread(str(d / serie["vacio"])), fotos, roi_px=roi)
        serie["python_cal"] = {"roi_px": cal.roi_px, "puntos": [[r(a), b] for a, b in cal.puntos]}
        for ml in niveles:
            s = S.vaso(ml, color_bgr=color, alfa=alfa, semilla=ml + 7)
            ruta = f"vaso/{nombre_serie}-{ml:03d}.jpg"
            cv2.imwrite(str(d / ruta), s.cuadro, JPG)
            f = cv2.imread(str(d / ruta))
            lec = V.nivel_px(f, cal)
            serie["niveles"].append({"archivo": ruta, "ml": ml, "fila_verdad": r(s.fila_superficie), "python": {"fila_px": r(lec.fila_px), "vacio": lec.vacio, "ml": r(cal.ml(lec.fila_px))}})
        spec["vaso"][nombre_serie] = serie

    # ---- intrusos: la mesa de referencia contra la misma mesa con un celular, luz proyectada o un brazo
    ref = cv2.imread(str(d / "pastillero" / "mesa-cenital-mm-1.jpg"))
    casos = []
    for k, (c, ang) in enumerate((((330, 470), 25), ((560, 120), 0), ((120, 420), 70), ((800, 470), -35))):
        casos.append((f"celular-{k + 1}", I.poner_celular(ref, c, angulo_deg=ang, semilla=k), {"celular_en_mm": list(c)}))
    luz = ref.astype(np.int16)
    luz[300:372, 100:262] += np.array([60, 90, 40], np.int16)  # botón proyectado del tamaño de un teléfono
    luz[100:400, 40:44] += 120
    casos.append(("luz-proyectada", np.clip(luz, 0, 255).astype(np.uint8), {"celular_en_mm": None}))
    bajo = I.poner_celular(ref, (200, 300)).astype(np.int16)
    bajo[250:350, 60:340] += 40
    casos.append(("celular-bajo-luz", np.clip(bajo, 0, 255).astype(np.uint8), {"celular_en_mm": [200, 300]}))
    brazo = ref.copy()
    cv2.fillPoly(brazo, [np.int32([[0, 300], [190, 330], [200, 400], [0, 390]])], (120, 150, 190))
    casos.append(("brazo-desde-el-borde", brazo, {"celular_en_mm": None}))
    for nombre, img, verdad in casos:
        ruta = f"intrusos/{nombre}.jpg"
        cv2.imwrite(str(d / ruta), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        img = cv2.imread(str(d / ruta))
        out = I.detectar(ref, img)
        spec["intrusos"].append(
            {"ref": "pastillero/mesa-cenital-mm-1.jpg", "archivo": ruta, **verdad, "python": [{k: o[k] for k in ("tipo", "centro_mm", "area_mm2", "toca_borde")} for o in out]}
        )

    (d / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1))
    total = sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
    print(f"fixtures en {d}: {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main(sys.argv[1])

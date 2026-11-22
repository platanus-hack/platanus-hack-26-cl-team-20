"""Jev en el laboratorio: una segunda opinión rápida donde la medida determinista puede equivocarse.

- `confirmar_celular(mesa_mm, poligono_mm)`: ¿lo oscuro del tamaño de un teléfono es de verdad un celular? Evita la
  alarma roja por un mouse, una billetera o un estuche oscuro. El conductor pregunta una vez por candidato.
- `vaso_a_la_vista(cuadro_lateral, objetivo_ml)`: ¿el vaso se ve bajo, justo o rebalsado respecto del objetivo? Para
  cuando la cámara del vaso no está calibrada o para dudar de la medida.

Usa el cliente de la mesa, `engine/playground/jev.py` (rama max/motor; otra ruta con `JEV_PY`). La llave va por
referencia: `AI_GATEWAY_API_KEY` del entorno o la que `jev.py` lee de `JEV_ENV_FILE`, solo en memoria; aquí no se
lee ni se guarda. Si falta `jev.py`, httpx, la llave o la red, todo devuelve None y el conductor sigue con lo
determinista. Jev recibe solo un recorte de la vista cenital de la mesa o el cuadro de la cámara del vaso.
"""

from __future__ import annotations

import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _Tarde
from pathlib import Path

import cv2
import numpy as np

RAIZ = Path(__file__).resolve().parents[2]
_jv = None
_cargado = False
# Las preguntas corren aparte y se esperan con un límite de reloj: una red lenta nunca frena al conductor.
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jev-lab")


def _con_limite(fn, limite_s: float):
    try:
        return _pool.submit(fn).result(timeout=limite_s)
    except _Tarde:
        return None
    except Exception:  # noqa: BLE001
        return None


def calentar() -> None:
    """Una pregunta mínima en segundo plano al arrancar: en frío la primera tarda ~9 s; después, ~0,6 s."""
    if disponible():
        gris = np.full((280, 280, 3), 128, np.uint8)
        _pool.submit(_confirmar, gris, [[60, 60], [220, 60], [220, 220], [60, 220]], 15.0)


def _jev():
    """El módulo jev de la mesa, cargado una vez. None si no está o no carga."""
    global _jv, _cargado
    if not _cargado:
        _cargado = True
        ruta = Path(os.environ.get("JEV_PY") or RAIZ / "engine" / "playground" / "jev.py")
        try:
            spec = importlib.util.spec_from_file_location("jev_mesa", ruta)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            _jv = m
        except Exception:  # noqa: BLE001 (sin jev.py o sin httpx: se sigue sin Jev)
            _jv = None
    return _jv


def disponible() -> bool:
    jv = _jev()
    return bool(jv and jv.disponible())


def _jpg(img: np.ndarray, lado_min: int = 280, lado_max: int = 1568) -> bytes:
    """JPEG baseline de 280 a 1568 px por lado (lo que acepta Jev)."""
    h, w = img.shape[:2]
    k = max(1.0, lado_min / min(h, w))
    k = min(k, lado_max / max(h, w))
    if k != 1.0:
        img = cv2.resize(img, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_CUBIC if k > 1 else cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return buf.tobytes() if ok else b""


def recorte(mesa_mm: np.ndarray, poligono_mm, margen_mm: float = 40.0, mm_por_px: float = 1.0) -> np.ndarray | None:
    """El candidato con un margen alrededor, en la vista cenital (1 px = `mm_por_px` mm)."""
    p = np.asarray(poligono_mm, float) / mm_por_px
    m = margen_mm / mm_por_px
    h, w = mesa_mm.shape[:2]
    x0, y0 = int(max(0, p[:, 0].min() - m)), int(max(0, p[:, 1].min() - m))
    x1, y1 = int(min(w, p[:, 0].max() + m)), int(min(h, p[:, 1].max() + m))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return mesa_mm[y0:y1, x0:x1]


def confirmar_celular(mesa_mm: np.ndarray, poligono_mm, limite_s: float = 2.5) -> float | None:
    """Probabilidad de que el candidato sea un celular (0..1). None si Jev no está o no respondió en `limite_s`."""
    return _con_limite(lambda: _confirmar(mesa_mm, poligono_mm, limite_s), limite_s)


def _confirmar(mesa_mm: np.ndarray, poligono_mm, timeout: float) -> float | None:
    jv = _jev()
    if not jv or not jv.disponible():
        return None
    rec = recorte(mesa_mm, poligono_mm)
    if rec is None:
        return None
    h, w = rec.shape[:2]
    partes = [
        {"type": "json", "value": {"recorte": "[recorte-1]", "contexto": f"top-down photo of a lab bench, {w} x {h} mm"}},
        jv.parte_imagen(_jpg(rec), "[recorte-1]"),
    ]
    preguntas = {"celular": {"type": "noul", "instructions": "Is there a mobile phone (a smartphone, screen up or down) in `recorte`?"}}
    r = jv.decide(partes, preguntas, timeout=timeout)
    if r is None:
        return None
    return float(r["answers"]["celular"].get("noul", 0.0))


ZONAS_JEV = {"bajo": "falta", "justo": "listo", "rebalsado": "te_pasaste"}


def vaso_a_la_vista(cuadro: np.ndarray, objetivo_ml: float, limite_s: float = 2.5) -> dict | None:
    """Segunda opinión visual del vaso: {"zona": falta|listo|te_pasaste, "confianza", "probabilidades"} o None."""
    return _con_limite(lambda: _vaso(cuadro, objetivo_ml, limite_s), limite_s)


def _vaso(cuadro: np.ndarray, objetivo_ml: float, timeout: float) -> dict | None:
    jv = _jev()
    if not jv or not jv.disponible() or cuadro is None:
        return None
    n = f"{objetivo_ml:g}"
    partes = [
        {"type": "json", "value": {"vaso": "[vaso-1]", "objetivo": f"{n} ml"}},
        jv.parte_imagen(_jpg(cuadro), "[vaso-1]"),
    ]
    preguntas = {
        "nivel": {
            "type": "choice",
            "instructions": f"`vaso` is a side view of a lab beaker with printed ml marks. Compare the top of the liquid with the {n} ml mark.",
            "criteria": {
                "bajo": f"the liquid is clearly below the {n} ml mark",
                "justo": f"the liquid is at the {n} ml mark (within about 3 ml)",
                "rebalsado": f"the liquid is clearly above the {n} ml mark",
            },
        }
    }
    r = jv.decide(partes, preguntas, timeout=timeout)
    if r is None:
        return None
    a = r["answers"]["nivel"]
    return {"zona": ZONAS_JEV.get(a.get("choice")), "confianza": a.get("confidence"), "probabilidades": a.get("probabilities"), "ms": r.get("ms")}

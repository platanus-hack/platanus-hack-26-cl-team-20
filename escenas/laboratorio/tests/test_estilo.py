"""Reglas físicas de la mesa (MOTOR.md): texto ≥ 28 mm y trazos ≥ 3 mm. 1 px de CSS = 1 mm."""

import re
from pathlib import Path

import cv2

import conductor as C
import hud

AQUI = Path(__file__).parent.parent


def _fuentes_px(css: str):
    for m in re.finditer(r"font(?:-size)?\s*:\s*([^;]+);", css):
        for n in re.findall(r"(?<![\w.-])(\d+(?:\.\d+)?)px", m.group(1)):
            yield float(n), m.group(0)


def test_sin_letra_bajo_28_mm():
    for archivo in ("ui/piezas.css", "tema.css"):
        chicas = [d for n, d in _fuentes_px((AQUI / archivo).read_text()) if n < 28]
        assert not chicas, f"{archivo}: {chicas}"


def test_html_proyectado_sin_tamanos_chicos_en_linea():
    prot = C.cargar_protocolo(AQUI / "escena.md")
    g = C.P.detectar(cv2.imread(str(AQUI / "percepcion/tests/datos/mesa-cenital-mm-1.jpg")))
    e = C.Estado(prot=prot, grilla=g, vaso_mm=prot["vaso"]["posicion_mm"])
    for i in range(len(prot["pasos"])):
        e.i, e.lectura = i, {"ml": 56, "zona": "te_pasaste", "exceso_ml": 6}
        for w in C.vistas(e):
            assert "font-size" not in w["params"]["html"], w["id"]


def test_el_css_proyectado_trae_base_piezas_y_tema():
    css = hud.css()
    assert "--sd-texto: 28px" in css  # base.css
    assert ".lab-medidor" in css  # piezas
    assert "--sd-foco: #f4f4f2" in css
    assert css.rfind("--sd-placa: rgba(0, 0, 0, 0.66)") > css.find(".lab-medidor")  # el tema va al final y manda

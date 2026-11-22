"""Conductor de la escena Laboratorio: corre el protocolo de `escena.md` sobre la mesa por MCP.

Hace lo que necesita ser rápido y exacto, para que el agente no tenga que mirar foto por foto:
- ubica el pastillero (foto limpia de la cámara cenital, o una foto guardada) y lo usa como sistema de coordenadas;
- muestra cada paso, marca la celda y espera el toque, el botón «Listo» o la voz («listo», «atrás», «repite»);
- en los pasos con ml, lee la cámara del vaso varias veces por segundo y avisa «te pasaste»;
- al final deja un registro con lo medido en cada paso.

Uso (ver README.md):
    uv run python conductor.py                                   # mesa real: cenital + cámara del vaso
    uv run python conductor.py --grilla percepcion/tests/datos/mesa-cenital-mm-1.jpg --vaso-sim   # sin hardware
    uv run python conductor.py --solo-previa estado/previa       # sin mesa: escribe las vistas en HTML
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import yaml

import hud
import jev_lab
from mesa_mcp import ErrorMesa
from percepcion import intrusos as I
from percepcion import pastillero as P
from percepcion import sintetico as S
from percepcion import vaso as V

AQUI = Path(__file__).parent
ESTADO = AQUI / "estado"


# --------------------------------------------------------------------------------------------- protocolo


def _yaml_de(ruta: Path) -> str:
    """El protocolo vive en el primer bloque ```yaml de escena.md (o en un .yaml suelto)."""
    texto = Path(ruta).read_text(encoding="utf-8")
    if Path(ruta).suffix.lower() != ".md":
        return texto
    m = re.search(r"^```ya?ml[ \t]*\n(.*?)^```", texto, re.S | re.M)
    if not m:
        raise SystemExit(f"{Path(ruta).name} no tiene un bloque ```yaml con el protocolo.")
    return m.group(1)


def cargar_protocolo(ruta: Path) -> dict:
    """Lee el protocolo y explica en palabras simples lo que esté mal."""
    nombre = Path(ruta).name
    try:
        prot = yaml.safe_load(_yaml_de(ruta)) or {}
    except yaml.YAMLError as e:
        raise SystemExit(f"El protocolo de {nombre} no se puede leer (revisa sangrías y dos puntos): {e}")
    errores = []
    pasos = prot.get("pasos") or []
    if not pasos:
        errores.append("No hay pasos: agrega al menos uno bajo «pasos:».")
    for k, p in enumerate(pasos, 1):
        if not p.get("di"):
            errores.append(f"Paso {k}: falta «di:» (la instrucción).")
        if p.get("celda") and not P.direccion_valida(str(p["celda"])):
            errores.append(f"Paso {k}: la celda «{p['celda']}» no existe; van de A1 a D7.")
        if (p.get("elegir") or p.get("hacia_vaso")) and not p.get("celda"):
            errores.append(f"Paso {k}: «elegir» y «hacia_vaso» necesitan una «celda:».")
        if p.get("desde") or p.get("hasta"):
            if not (p.get("desde") and p.get("hasta")):
                errores.append(f"Paso {k}: un traspaso necesita «desde:» y «hasta:».")
            for clave in ("desde", "hasta"):
                v = str(p.get(clave) or "")
                if v and not (P.direccion_valida(v) or (clave == "hasta" and v.strip().lower() == "vaso")):
                    errores.append(f"Paso {k}: «{clave}: {v}» no es un vial (A1 a D7){' ni «vaso»' if clave == 'hasta' else ''}.")
        if p.get("mezclar_s") is not None and not (0 < float(p["mezclar_s"]) <= 600):
            errores.append(f"Paso {k}: «mezclar_s» va en segundos, entre 1 y 600.")
        if p.get("medir_ml") is not None and not (0 < float(p["medir_ml"]) <= float(prot.get("vaso", {}).get("capacidad_ml", 1000))):
            errores.append(f"Paso {k}: «medir_ml» debe ser mayor que 0 y caber en el vaso.")
    colores = prot.get("pastillero", {}).get("colores")
    if colores is not None and len(colores) != P.N_COLUMNAS:
        errores.append(f"pastillero.colores debe tener {P.N_COLUMNAS} colores, uno por columna.")
    if errores:
        raise SystemExit(f"Hay que arreglar el protocolo de {nombre}:\n- " + "\n- ".join(errores))
    for p in pasos:
        for clave in ("celda", "desde", "hasta"):
            if p.get(clave):
                p[clave] = str(p[clave]).strip().upper()
        if p.get("hacia_vaso") and not p.get("desde"):  # forma antigua: celda + hacia_vaso
            p["desde"], p["hasta"] = p["celda"], "VASO"
    prot["viales"] = {str(k).strip().upper(): str(v) for k, v in (prot.get("viales") or {}).items()}
    prot.setdefault("vaso", {}).setdefault("capacidad_ml", 250)
    prot["vaso"].setdefault("posicion_mm", [525, 470])
    prot.setdefault("panel_mm", [160, 64, 330, 420])
    prot.setdefault("medidor_mm", [500, 64, 210, 330])
    prot.setdefault("ambiente", True)
    prot.setdefault("ambiente_mm", [[880, 140, 110, 230]])  # además, la zona del medidor cuando no se mide
    prot.setdefault("protocolo_mm", [650, 385, 340, 170])
    prot.setdefault("contaminacion", True)
    # La consola pone sus pestañas en una barra a la izquierda: nada de la escena en x < BARRA_MM.
    for clave in ("panel_mm", "medidor_mm", "protocolo_mm"):
        prot[clave] = _fuera_de_la_barra(prot[clave], clave)
    prot["ambiente_mm"] = [_fuera_de_la_barra(r, "ambiente_mm") for r in prot["ambiente_mm"]]
    return prot


BARRA_MM = 150  # ancho de la barra lateral de la consola (SAT, 21-nov 18:58)


def _fuera_de_la_barra(rect, nombre: str) -> list:
    """Un rect que empieza bajo la barra lateral se corre a la derecha (mismo tamaño) y se avisa."""
    x, y, w, h = (float(v) for v in rect)
    if x >= BARRA_MM:
        return list(rect)
    print(f"aviso: {nombre} empezaba en x={x:g} mm, bajo la barra lateral de la consola; lo corro a x={BARRA_MM} mm", file=sys.stderr)
    return [BARRA_MM, y, w, h]


# --------------------------------------------------------------------------------------------- vaso


class VasoSim:
    """La cámara del vaso, simulada: un vertido que sube rápido, frena cerca del objetivo y se pasa `pasarse` ml.

    Las fotos son sintéticas (percepcion/sintetico.py) y pasan por el mismo `Medidor` que la cámara real.
    """

    def __init__(self, pasarse: float = 6.0):
        # Se calibra igual que el vaso real: vacío + dos niveles conocidos, leídos por el mismo detector.
        vacio = S.vaso(0, semilla=1).vacio
        self.cal = V.calibrar_con_lecturas(vacio, {50: S.vaso(50, semilla=2).cuadro, 150: S.vaso(150, semilla=3).cuadro})
        self.pasarse = pasarse
        self.t0 = time.monotonic()
        self.objetivo = 50.0
        self.n = 0

    def empezar(self, objetivo: float):
        self.t0, self.objetivo = time.monotonic(), float(objetivo)

    def ml_verdad(self) -> float:
        t = time.monotonic() - self.t0 - 2.0  # 2 s quieto antes de echar
        if t <= 0:
            return 0.0
        rapido = self.objetivo * 0.75
        t1 = rapido / 9.0  # 9 ml/s al principio
        if t < t1:
            return 9.0 * t
        return min(self.objetivo + self.pasarse, rapido + 2.5 * (t - t1))  # 2,5 ml/s al final

    def cuadro(self) -> np.ndarray:
        self.n += 1
        return S.vaso(self.ml_verdad(), semilla=self.n % 97).cuadro


class VasoCamara:
    """La cámara secundaria de verdad, por `look` con su camera_id."""

    def __init__(self, mesa, camera_id: str, cal: V.Calibracion):
        self.mesa, self.camera_id, self.cal = mesa, camera_id, cal

    def empezar(self, objetivo: float):
        pass

    def cuadro(self) -> np.ndarray:
        return self.mesa.look(self.camera_id, annotate=False)[1]


def cargar_cal_vaso(ruta: Path) -> V.Calibracion:
    d = json.loads(ruta.read_text())
    vacio = cv2.imread(str(ruta.with_name(d["vacio"]))) if d.get("vacio") else None
    return V.Calibracion.desde_dict(d, vacio=vacio)


class Vigia:
    """Busca objetos ajenos en la mesa comparando con la foto de referencia (la mesa preparada, sin proyección).

    `fuente()` devuelve la vista cenital rectificada de ahora (1 px = 1 mm) o None. No pide foto limpia: lo que la escena
    proyecta se ignora (sus rectángulos) y fuera de eso el proyector manda negro.
    """

    def __init__(self, ref_mm: np.ndarray, fuente, periodo_s: float = 1.5, solo_celular: bool = True):
        self.ref, self.fuente, self.periodo_s, self.solo_celular = ref_mm, fuente, periodo_s, solo_celular
        self.ultima: np.ndarray | None = None  # la última vista cenital (para mostrarle el recorte a Jev)

    def mirar(self, ignorar_rects, conocidos) -> list[dict]:
        try:
            ahora = self.fuente()
        except Exception:  # una foto perdida no corta la guía
            return []
        if ahora is None:
            return []
        self.ultima = ahora
        return I.detectar(self.ref, ahora, ignorar_rects_mm=ignorar_rects, ignorar_poligonos_mm=conocidos)


class FuenteSimulada:
    """Para probar sin mesa: la foto de referencia y, entre `desde_s` y `hasta_s` desde el arranque, un celular encima."""

    def __init__(self, ref_mm: np.ndarray, desde_s: float, dura_s: float = 8.0, donde_mm=(330, 470)):
        self.ref, self.t0, self.desde, self.hasta, self.donde = ref_mm, time.monotonic(), desde_s, desde_s + dura_s, donde_mm
        self.con = I.poner_celular(ref_mm, donde_mm, angulo_deg=20)

    def __call__(self):
        t = time.monotonic() - self.t0
        return self.con if self.desde <= t < self.hasta else self.ref


# --------------------------------------------------------------------------------------------- estado y vistas


@dataclass
class Estado:
    prot: dict
    grilla: P.Grilla
    vaso_mm: list[float]
    i: int = 0
    hechas: list[str] = field(default_factory=list)
    registro: dict = field(default_factory=dict)
    aviso: str | None = None
    tono: str = "peligro"  # del aviso: peligro (celda equivocada) o vacío (solo informa)
    mal: str | None = None
    lectura: dict | None = None  # {ml, zona, cerca, exceso_ml}
    reloj_s: float | None = None  # mezclar: segundos que faltan (lo que muestra el panel)
    intrusos: list = field(default_factory=list)  # objetos ajenos en la mesa: el protocolo espera
    mezclado: bool = False
    terminado: bool = False

    @property
    def paso(self) -> dict:
        return self.prot["pasos"][self.i]


def _ambiente(e: Estado, midiendo: bool = False) -> list[dict]:
    """Proteínas en las zonas libres y la lista del protocolo."""
    ws = []
    if e.prot.get("protocolo_mm"):
        ws.append(hud.protocolo(e.prot, e.i, e.prot["protocolo_mm"], terminado=e.terminado))
    if e.prot.get("ambiente"):
        zonas = list(e.prot.get("ambiente_mm") or [])
        if not midiendo:
            zonas.append(e.prot["medidor_mm"])
        # Nunca proteínas sobre la gradilla: la zona que la pisa se salta.
        g = np.array(e.grilla.esquinas_mm) if e.grilla is not None and e.grilla.ok else None
        zonas = [r for r in zonas if g is None or not _se_tocan(r, g)]
        ws += [hud.ambiente(f"lab-ambiente-{k + 1}", r, semilla=k + 7) for k, r in enumerate(zonas)]
    return ws


def _se_tocan(rect, esquinas: np.ndarray, margen: float = 20.0) -> bool:
    x, y, w, h = rect
    return not (esquinas[:, 0].max() + margen < x or esquinas[:, 0].min() - margen > x + w or esquinas[:, 1].max() + margen < y or esquinas[:, 1].min() - margen > y + h)


def vistas(e: Estado) -> list[dict]:
    """Todos los widgets de la escena para el estado actual (el conductor solo manda los que cambian)."""
    nombres = e.prot.get("viales")
    if e.terminado:
        ws = [hud.grilla(e.grilla, hechas=e.hechas, nombres=nombres), hud.cierre(e.prot, e.prot["panel_mm"], e.registro, datetime.now().strftime("%H:%M"))]
        return (hud.alarma(e.intrusos, e.prot["panel_mm"]) + ws[:1] if e.intrusos else ws) + _ambiente(e)
    p = e.paso
    objetivo = [p["celda"]] if p.get("celda") else []
    flecha = (p["desde"], p["hasta"]) if p.get("desde") else None
    ws = []
    if p.get("medir_ml") is None or objetivo or flecha:
        ws.append(hud.grilla(e.grilla, objetivo=objetivo, hechas=e.hechas, mal=e.mal,
                              elegir=bool(p.get("elegir")), flecha=flecha, nombres=nombres))
    lect = e.lectura or {}
    if p.get("elegir"):
        listo = False
    elif p.get("medir_ml") is not None:
        listo = lect.get("zona") == V.LISTO
    elif p.get("mezclar_s") is not None:
        listo = e.mezclado
    else:
        listo = True
    reloj = e.reloj_s if (p.get("mezclar_s") is not None and not e.mezclado) else None
    if e.intrusos:  # mesa contaminada: la alarma reemplaza al panel hasta que saquen el objeto
        ws += hud.alarma(e.intrusos, e.prot["panel_mm"])
    else:
        ws.append(hud.panel(e.prot, e.i, e.prot["panel_mm"], aviso=e.aviso, tono=e.tono, listo_resaltado=listo, reloj_s=reloj))
    if p.get("hasta") == "VASO":
        ws.append(hud.guia(e.grilla.celda(p["desde"]).centro_mm, e.vaso_mm))
    if p.get("medir_ml") is not None:
        ws.append(
            hud.medidor(
                e.prot["medidor_mm"],
                lect.get("ml"),
                float(p["medir_ml"]),
                lect.get("zona", V.FALTA),
                capacidad_ml=float(e.prot["vaso"]["capacidad_ml"]),
                cerca=bool(lect.get("cerca")),
                exceso_ml=lect.get("exceso_ml"),
                vivo=lect.get("ml") is not None or not lect,
            )
        )
    return ws + _ambiente(e, midiendo=p.get("medir_ml") is not None)


# --------------------------------------------------------------------------------------------- conductor


# Eventos con que la voz mueve el protocolo (los emite el agente de voz con `scenes emit`).
VOZ_PASOS = {
    "paso_siguiente": "siguiente",
    "siguiente": "siguiente",
    "paso_anterior": "anterior",
    "anterior": "anterior",
    "repetir": "repetir",
    "paso_repetir": "repetir",
}


class Conductor:
    def __init__(self, mesa, estado: Estado, vaso, log=print, vigia=None, jev: bool = True):
        self.mesa, self.e, self.vaso, self.log = mesa, estado, vaso, log
        self.vigia = vigia  # Vigia: busca objetos ajenos en la mesa (None: no vigila)
        self.jev = jev  # confirmar con Jev que lo oscuro es un celular antes de la alarma (si hay llave)
        self.jev_dicho: list[tuple[list[float], bool]] = []  # (centro_mm, ¿es celular?) ya preguntados
        self.proxima_vigilancia = 0.0
        self.sospechas = 0
        self.enviados: dict[str, str] = {}  # id → firma del último widget mandado
        self.cursor = 0
        self.medidor: V.Medidor | None = None
        self.umbral: V.Umbral | None = None
        self.ultimo_medidor = 0.0
        self.reloj_fin: float | None = None

    # -- proyección
    def mostrar(self, forzar: bool = False):
        if self.reloj_fin is not None and not self.e.mezclado:  # si el panel se vuelve a mandar, el reloj sigue donde iba
            self.e.reloj_s = max(1, int(np.ceil(self.reloj_fin - time.monotonic())))
        ws = vistas(self.e)
        nuevos = [w for w in ws if forzar or self.enviados.get(w["id"]) != json.dumps(w, sort_keys=True)]
        fuera = [i for i in self.enviados if i not in {w["id"] for w in ws}]
        if fuera:
            self.mesa.remove(fuera)
            for i in fuera:
                self.enviados.pop(i, None)
        if nuevos:
            self.mesa.project(nuevos)
            for w in nuevos:
                self.enviados[w["id"]] = json.dumps(w, sort_keys=True)

    # -- pasos
    def entrar(self, i: int):
        e = self.e
        e.i, e.aviso, e.mal, e.lectura = i, None, None, None
        e.mezclado, e.reloj_s, self.reloj_fin = False, None, None
        p = e.paso
        if p.get("mezclar_s") is not None:
            self.reloj_fin = time.monotonic() + float(p["mezclar_s"])
        if p.get("medir_ml") is not None:
            objetivo = float(p["medir_ml"])
            self.medidor = V.Medidor(self.vaso.cal, n=3)
            self.umbral = V.Umbral(objetivo, tolerancia_ml=float(p.get("tolerancia_ml", 3)), histeresis_ml=1.0, cuadros=2)
            self.vaso.empezar(objetivo)
            e.lectura = {"ml": None, "zona": V.FALTA}
        else:
            self.medidor = self.umbral = None
        self.log(f"paso {i + 1}/{len(e.prot['pasos'])}: {p['di']}")
        self.mostrar()

    def avanzar(self):
        e = self.e
        p = e.paso
        if p.get("medir_ml") is not None and e.lectura and e.lectura.get("ml") is not None:
            e.registro[e.i] = f"{round(e.lectura['ml'])} ml"
        elif p.get("desde"):
            e.registro[e.i] = f"{p['desde']} → {p['hasta'].lower() if p['hasta'] == 'VASO' else p['hasta']}"
        elif p.get("mezclar_s") is not None:
            e.registro[e.i] = f"{p.get('celda', '')} {round(float(p['mezclar_s']))} s".strip()
        elif p.get("celda"):
            e.registro[e.i] = p["celda"]
        else:
            e.registro.setdefault(e.i, datetime.now().strftime("%H:%M"))
        for d in (p.get("celda"), p.get("desde"), p.get("hasta")):
            if d and d != "VASO" and d not in e.hechas:
                e.hechas.append(d)
        if e.i + 1 < len(e.prot["pasos"]):
            self.entrar(e.i + 1)
        else:
            e.terminado = True
            self.medidor = self.umbral = None
            self.guardar_registro()
            self.log("protocolo terminado")
            self.mostrar()

    def atras(self):
        if self.e.terminado:
            self.e.terminado = False
        elif self.e.i == 0:
            return
        else:
            self.e.i -= 1
        self.e.registro.pop(self.e.i, None)
        self.entrar(self.e.i)

    def guardar_registro(self):
        (ESTADO / "registros").mkdir(parents=True, exist_ok=True)
        ruta = ESTADO / "registros" / f"{datetime.now():%Y-%m-%d-%H%M%S}.json"
        pasos = [{"paso": k + 1, "di": p["di"], "resultado": self.e.registro.get(k)} for k, p in enumerate(self.e.prot["pasos"])]
        ruta.write_text(json.dumps({"protocolo": self.e.prot.get("nombre"), "pasos": pasos}, ensure_ascii=False, indent=2))
        self.log(f"registro: {ruta}")

    # -- eventos
    def tocar_celda(self, valor: str | None, punto_mm, gatillo: str | None):
        e = self.e
        p = e.paso
        celda = valor if valor and P.direccion_valida(valor) else None
        if gatillo == "dwell" and punto_mm:  # con el dedo manda la punta: es más exacto que la caja del blanco
            celda = P.celda_en(e.grilla, punto_mm) or celda
        if not celda:
            return
        if not p.get("elegir"):
            e.aviso, e.tono = f"Esa es la {celda}, {e.grilla.celda(celda).color}", ""
            self.mostrar()
            return
        if celda == p["celda"]:
            e.aviso, e.mal = None, None
            self.avanzar()
        else:
            e.mal = celda
            e.aviso, e.tono = f"Esa es la {celda}, {e.grilla.celda(celda).color}. Busca la {p['celda']}", "peligro"
            self.mostrar()

    def evento(self, ev: dict):
        ev = ev.get("payload", ev) if isinstance(ev.get("payload"), dict) else ev
        tipo = ev.get("type")
        nombre = ev.get("event") if tipo == "flow_emitted" else None
        if self.e.intrusos and (tipo == "widget_pressed" or nombre in VOZ_PASOS or nombre == "voice_command"):
            self.log(f"  {nombre or 'toque'} ignorado: la mesa está contaminada")
            return
        if tipo == "widget_pressed":
            wid, val = ev.get("widget_id"), ev.get("value")
            self.log(f"toque: {wid} {val} ({ev.get('trigger')})")
            if wid == "lab-paso":
                if val == "listo":
                    self.avanzar()
                elif val == "atras":
                    self.atras()
                elif val == "reiniciar":
                    self.e.terminado, self.e.hechas, self.e.registro = False, [], {}
                    self.entrar(0)
            elif wid == "lab-grilla":
                self.tocar_celda(val, ev.get("point_mm"), ev.get("trigger"))
        elif tipo == "flow_emitted" and nombre in VOZ_PASOS:
            # La voz avanza con eventos propios (el agente no puede emitir voice_command ni toques): paso_siguiente,
            # paso_anterior, repetir.
            self.log(f"voz: {nombre}")
            accion = VOZ_PASOS[nombre]
            if accion == "siguiente":
                self.avanzar()
            elif accion == "anterior":
                self.atras()
            else:
                self.enviados.clear()
                self.mostrar(forzar=True)
        elif tipo == "flow_emitted" and nombre == "voice_command":
            texto = ((ev.get("data") or {}).get("text") or "").lower()
            self.log(f"voz: {texto!r}")
            if any(w in texto for w in ("listo", "siguiente", "sigue", "hecho")):
                self.avanzar()
            elif any(w in texto for w in ("atrás", "atras", "anterior", "vuelve")):
                self.atras()
            elif any(w in texto for w in ("repite", "de nuevo", "otra vez")):
                self.enviados.clear()
                self.mostrar(forzar=True)

    # -- objetos ajenos
    def vigilar(self):
        if not self.vigia or time.monotonic() < self.proxima_vigilancia:
            return
        self.proxima_vigilancia = time.monotonic() + self.vigia.periodo_s
        # Lo proyectado no hace falta ignorarlo: el detector solo mira lo que quedó más oscuro. Se ignora lo conocido
        # (la gradilla con su tapa y el vaso, que la gente mueve al trabajar).
        g = self.e.grilla
        tapa = np.array(g.esquinas_mm) + np.array(g.eje_filas) * np.array([[0], [0], [4], [4]]) * g.paso_mm
        conocidos = [g.esquinas_mm, tapa.tolist()]
        vx, vy = self.e.vaso_mm
        ignorar = [[vx - 60, vy - 60, 120, 120]]
        hallados = [o for o in self.vigia.mirar(ignorar, conocidos) if self.vigia.solo_celular is False or o["tipo"] == "celular"]
        if hallados and self.sospechas >= 1 and not self.e.intrusos:
            hallados = [o for o in hallados if self.confirmar(o)]
        if hallados:
            self.sospechas += 1
            if self.sospechas >= 2 and not self.e.intrusos:
                self.log(f"  objeto contaminado: {[o['tipo'] for o in hallados]}")
            if self.sospechas >= 2:
                self.e.intrusos = hallados
                self.mostrar()
        else:
            self.sospechas = 0
            if self.e.intrusos:
                self.log("  mesa limpia de nuevo")
                self.e.intrusos = []
                self.mostrar()

    def confirmar(self, o: dict) -> bool:
        """¿Es de verdad un celular? Una pregunta a Jev por candidato (los ya preguntados se recuerdan a 25 mm). Sin
        Jev (sin llave, sin red, apagado) o si no es un celular por forma, se queda con lo determinista."""
        if not self.jev or o["tipo"] != "celular" or self.vigia.ultima is None:
            return True
        for c, es in self.jev_dicho:
            if np.hypot(c[0] - o["centro_mm"][0], c[1] - o["centro_mm"][1]) < 25:
                return es
        p = jev_lab.confirmar_celular(self.vigia.ultima, o["poligono_mm"])
        if p is None:
            return True
        es = p >= 0.35
        self.jev_dicho = [*self.jev_dicho[-19:], (o["centro_mm"], es)]
        self.log(f"  Jev: {'es' if es else 'no es'} un celular (p={p:.2f}) en {o['centro_mm']}")
        return es

    # -- mezclar
    def revisar_reloj(self):
        if self.reloj_fin is None or self.e.mezclado or time.monotonic() < self.reloj_fin:
            return
        self.e.mezclado, self.e.reloj_s = True, None
        self.e.aviso, self.e.tono = "Ya está mezclado", "ok"
        self.log("  mezclado")
        self.mostrar()

    # -- medición
    def medir(self):
        if not self.medidor:
            return
        try:
            cuadro = self.vaso.cuadro()
        except Exception as ex:  # una foto perdida no corta la guía
            self.log(f"cámara del vaso: {ex}")
            self.e.lectura = {**(self.e.lectura or {}), "ml": None}
            return
        r = self.medidor.leer(cuadro)
        u = self.umbral.actualizar(r["ml"])
        antes = self.e.lectura or {}
        self.e.lectura = {"ml": r["ml"], **u}
        cambio = u["zona"] != antes.get("zona") or u["cerca"] != antes.get("cerca")
        if u["zona"] != antes.get("zona"):
            self.log(f"  {hud.ml_txt(r['ml'])} ml → {u['zona']}")
        ahora = time.monotonic()
        if cambio or ahora - self.ultimo_medidor >= 0.25:  # un cambio de zona («te pasaste») sale al tiro
            self.ultimo_medidor = ahora
            self.mostrar()

    # -- bucle
    def correr(self, hasta: float | None = None):
        self.cursor = self.mesa.cursor_actual()
        self.entrar(self.e.i)
        tipos = ["widget_pressed", "flow_emitted"]
        cortado = False
        while hasta is None or time.monotonic() < hasta:
            try:
                if cortado:  # volvió la conexión (o el gateway se reinició): se proyecta todo de nuevo
                    self.enviados.clear()
                    self.mostrar(forzar=True)
                    cortado = False
                    self.log("conexión de vuelta")
                self.revisar_reloj()
                self.vigilar()
                espera = 0.0 if self.medidor else (0.5 if (self.reloj_fin is not None and not self.e.mezclado) or self.vigia else 10.0)
                self.cursor, eventos = self.mesa.eventos(self.cursor, tipos, espera_s=espera)
                for ev in eventos:
                    try:
                        self.evento(ev)
                    except ErrorMesa:
                        raise
                    except Exception as ex:  # noqa: BLE001  un evento raro no se lleva los que vienen detrás
                        self.log(f"evento falló, sigo: {ex!r}")
                self.medir()
            except ErrorMesa as ex:  # un corte del gateway (Render despierta, deploy) no bota la guía
                if not cortado:
                    self.log(f"sin mesa, reintento cada 2 s: {ex}")
                cortado = True
                time.sleep(2)
                continue
            except Exception as ex:  # noqa: BLE001  ni una falla del reloj, el vigía o el medidor
                self.log(f"error en la guía, sigo en 2 s: {ex!r}")
                time.sleep(2)
                continue
            if self.medidor:
                time.sleep(0.12)


# --------------------------------------------------------------------------------------------- arranque


def ubicar_pastillero(mesa, prot, args) -> tuple[P.Grilla, np.ndarray, dict | None]:
    """La gradilla, la foto de referencia de la mesa (para ver objetos ajenos después) y los datos de calibración."""
    paleta = prot.get("pastillero", {}).get("colores") or P.PALETA_DEMO
    datos = None
    if args.grilla:
        mm = cv2.imread(args.grilla)
        if mm is None:
            raise SystemExit(f"No pude leer {args.grilla}")
        g = P.detectar(mm, paleta=paleta)
    else:
        datos, foto = mesa.look(args.camara_mesa, annotate=False, clean=True)
        if not datos.get("calibrated"):
            raise SystemExit("La cámara cenital no está calibrada: corre «calibrate» desde la consola y vuelve a intentar.")
        W, H = (int(round(v)) for v in datos.get("surface_mm", [1000, 563]))
        mm = P.rectificar(foto, datos["matrix_px_to_mm"], W, H, lente=datos.get("lens"), image_px=datos.get("image_px"))
        ESTADO.mkdir(exist_ok=True)
        cv2.imwrite(str(ESTADO / "cenital-mm.jpg"), mm)
        g = P.detectar(mm, paleta=paleta)
    if not g.ok:
        raise SystemExit(f"No encontré el pastillero: {g.motivo}")
    ESTADO.mkdir(exist_ok=True)
    (ESTADO / "grilla.json").write_text(json.dumps(g.a_dict(), ensure_ascii=False))
    print(f"pastillero: centro {g.centro_mm} mm, giro {g.angulo_deg}°, paso {g.paso_mm} mm, confianza {g.confianza}")
    return g, mm, datos


def fuente_cenital(mesa, camara, datos):
    """La vista cenital de ahora, rectificada con la calibración del arranque (sin foto limpia: no parpadea)."""
    W, H = (int(round(v)) for v in datos.get("surface_mm", [1000, 563]))

    def ahora():
        _, foto = mesa.look(camara, annotate=False)
        return P.rectificar(foto, datos["matrix_px_to_mm"], W, H, lente=datos.get("lens"), image_px=datos.get("image_px"))

    return ahora


def ubicar_vaso(mesa, prot) -> list[float]:
    """Si el motor publicó un objeto «vaso», se usa ese; si no, la posición del protocolo."""
    try:
        datos, _ = mesa.tool("scene", {})
        for o in datos.get("objects", []):
            if any(k in str(o.get("label", "")).lower() for k in ("vaso", "beaker", "glass", "precipitado")):
                c = o.get("center_mm") or np.mean(o["polygon_mm"], axis=0).tolist()
                print(f"vaso: objeto «{o.get('label')}» en {c}")
                return [float(c[0]), float(c[1])]
    except Exception as ex:
        print(f"scene: {ex}")
    return list(prot["vaso"]["posicion_mm"])


def publicar_pastillero(mesa, g: P.Grilla):
    """Publica el pastillero como objeto del mundo (anchor.object_id: «pastillero»). Ojo: reemplaza los objetos de la
    sesión, así que no usarlo si el motor ya publica los suyos."""
    mesa.tool("scenes", {"action": "objects", "objects": [{"id": "pastillero", "label": "pastillero 7x4", "polygon_mm": g.esquinas_mm, "center_mm": g.centro_mm}]})


def solo_previa(prot, g, carpeta: Path, pasarse: float):
    """Sin mesa: escribe una página HTML por momento de la escena (sobre la foto real de la mesa)."""
    from vista_previa import pagina

    carpeta.mkdir(parents=True, exist_ok=True)
    e = Estado(prot=prot, grilla=g, vaso_mm=list(prot["vaso"]["posicion_mm"]))
    momentos = []
    for i, p in enumerate(prot["pasos"]):
        e.i, e.aviso, e.mal, e.lectura = i, None, None, None
        if p.get("medir_ml") is not None:
            obj = float(p["medir_ml"])
            for nombre, ml, zona, cerca in (("vacio", 0, V.FALTA, False), ("cerca", obj - 6, V.FALTA, True), ("listo", obj, V.LISTO, False), ("pasado", obj + pasarse, V.PASADO, False)):
                e.lectura = {"ml": ml, "zona": zona, "cerca": cerca, "exceso_ml": pasarse if zona == V.PASADO else None}
                momentos.append((f"paso{i + 1}-{nombre}", vistas(e)))
        elif p.get("elegir"):
            momentos.append((f"paso{i + 1}", vistas(e)))
            otra = "C4" if p["celda"] != "C4" else "C5"
            e.mal, e.aviso, e.tono = otra, f"Esa es la {otra}, {g.celda(otra).color}. Busca la {p['celda']}", "peligro"
            momentos.append((f"paso{i + 1}-equivocada", vistas(e)))
        elif p.get("mezclar_s") is not None:
            e.mezclado, e.reloj_s = False, float(p["mezclar_s"])
            momentos.append((f"paso{i + 1}-mezclando", vistas(e)))
            e.mezclado, e.reloj_s, e.aviso, e.tono = True, None, "Ya está mezclado", "ok"
            momentos.append((f"paso{i + 1}-mezclado", vistas(e)))
            e.mezclado = False
        else:
            momentos.append((f"paso{i + 1}", vistas(e)))
        if p.get("medir_ml") is not None:
            e.registro[i] = f"{hud.ml_txt(p['medir_ml'])} ml"
        elif p.get("desde"):
            e.registro[i] = f"{p['desde']} → {'vaso' if p['hasta'] == 'VASO' else p['hasta']}"
        else:
            e.registro[i] = p.get("celda") or "17:42"
        for d in (p.get("celda"), p.get("desde"), p.get("hasta")):
            if d and d != "VASO" and d not in e.hechas:
                e.hechas.append(d)
    e.terminado = True
    momentos.append(("cierre", vistas(e)))
    for nombre, ws in momentos:
        (carpeta / f"{nombre}.html").write_text(pagina(ws), encoding="utf-8")
    print(f"{len(momentos)} vistas en {carpeta}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Conductor de la escena Laboratorio")
    ap.add_argument("--escena", "--protocolo", dest="protocolo", default=str(AQUI / "escena.md"), help="escena.md (o un .yaml con el protocolo)")
    ap.add_argument("--grilla", help="foto cenital rectificada (1 px = 1 mm) en vez de mirar la mesa")
    ap.add_argument("--camara-mesa", help="camera_id de la cenital (por defecto la principal)")
    ap.add_argument("--camara-vaso", help="camera_id de la cámara del vaso")
    ap.add_argument("--vaso-cal", default=str(ESTADO / "vaso.json"), help="calibración del vaso (calibrar_vaso.py)")
    ap.add_argument("--vaso-sim", action="store_true", help="cámara del vaso simulada")
    ap.add_argument("--pasarse", type=float, default=6.0, help="ml que se pasa el vertido simulado")
    ap.add_argument("--publicar-objeto", action="store_true", help="publica el pastillero como objeto (pisa los del motor)")
    ap.add_argument("--solo-previa", help="no usa la mesa: escribe vistas HTML en esta carpeta")
    ap.add_argument("--duracion", type=float, help="segundos y termina (pruebas)")
    ap.add_argument("--sin-contaminacion", action="store_true", help="no vigilar objetos ajenos en la mesa")
    ap.add_argument("--sin-jev", action="store_true", help="no confirmar con Jev que el objeto es un celular")
    ap.add_argument("--simular-celular", type=float, metavar="SEG", help="sin cámara cenital: a los SEG s aparece un celular 8 s")
    args = ap.parse_args(argv)

    prot = cargar_protocolo(Path(args.protocolo))
    if args.solo_previa:
        g = P.detectar(cv2.imread(args.grilla or str(AQUI / "percepcion/tests/datos/mesa-cenital-mm-1.jpg")), paleta=prot["pastillero"]["colores"])
        return solo_previa(prot, g, Path(args.solo_previa), args.pasarse)

    from mesa_mcp import Mesa

    mesa = Mesa()
    g, ref_mm, datos_cal = ubicar_pastillero(mesa, prot, args)
    if args.publicar_objeto:
        publicar_pastillero(mesa, g)
    if args.vaso_sim or not args.camara_vaso:
        if not args.vaso_sim:
            print("sin --camara-vaso: uso la cámara del vaso simulada")
        vaso = VasoSim(pasarse=args.pasarse)
    else:
        ruta = Path(args.vaso_cal)
        if not ruta.exists():
            raise SystemExit(f"Falta la calibración del vaso ({ruta}): corre «uv run python calibrar_vaso.py --camara-vaso {args.camara_vaso}».")
        vaso = VasoCamara(mesa, args.camara_vaso, cargar_cal_vaso(ruta))
    e = Estado(prot=prot, grilla=g, vaso_mm=ubicar_vaso(mesa, prot))
    vigia = None
    if prot.get("contaminacion") and not args.sin_contaminacion:
        if args.simular_celular is not None:
            vigia = Vigia(ref_mm, FuenteSimulada(ref_mm, args.simular_celular))
        elif datos_cal is not None:
            vigia = Vigia(ref_mm, fuente_cenital(mesa, args.camara_mesa, datos_cal))
    c = Conductor(mesa, e, vaso, vigia=vigia, jev=not args.sin_jev)
    if vigia and not args.sin_jev:
        if jev_lab.disponible():
            jev_lab.calentar()
            print("Jev confirma el celular antes de la alarma (hasta 2,5 s; si no responde, alarma por forma)")
        else:
            print("Jev no está (sin jev.py, httpx o llave): alarma solo por forma")
    try:
        c.correr(hasta=time.monotonic() + args.duracion if args.duracion else None)
    except KeyboardInterrupt:
        pass
    finally:
        if c.enviados:
            mesa.remove(list(c.enviados))


if __name__ == "__main__":
    sys.exit(main())

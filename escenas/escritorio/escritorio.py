#!/usr/bin/env python3
"""Escena «Escritorio del vibe coder»: la libreta que contesta y los agentes sobre la mesa.

    uv run escritorio.py montar                  # arma la escena en la mesa (marco de la libreta, botón y panel)
    uv run escritorio.py vivo                    # la escena andando: lee la libreta y contesta, refresca el panel
    uv run escritorio.py leer                    # una lectura (para que un agente la conduzca a mano, ver AGENTE.md)
    uv run escritorio.py responder "línea" ...   # proyecta una respuesta junto a lo último leído
    uv run escritorio.py agentes                 # refresca el panel de agentes una vez
    uv run escritorio.py limpiar                 # borra las respuestas proyectadas

Ensayo sin cámara secundaria (modo sim):
    uv run escritorio.py escribir "¿cómo le pongo a este endpoint?"   # «escribe» en una libreta sintética
    uv run escritorio.py vivo --libreta-sim salida/sim                 # la cámara secundaria es esa carpeta
    uv run escritorio.py vista salida/vista.html                       # todo dibujado en un html, sin mesa

Conexión: PCX_MCP_URL (por defecto http://localhost:9323/mcp) y PCX_USER_TOKEN (botón «Copiar» de la consola).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

import dibujo  # noqa: E402
import jev  # noqa: E402
from percepcion import Lector  # noqa: E402
from percepcion.tinta import marcar, recorte  # noqa: E402

SALIDA = AQUI / "salida"
MEMORIA = SALIDA / "memoria.npz"


def cargar_cfg() -> dict:
    return json.loads((AQUI / "escena.json").read_text())


def log(*partes):
    print(time.strftime("%H:%M:%S"), *partes, flush=True)


def jpg(img, calidad=85, lado_max=1000) -> bytes:
    h, w = img.shape[:2]
    if max(h, w) > lado_max:
        f = lado_max / max(h, w)
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, calidad])[1].tobytes()


# ------------------------------------------------------------------ fuentes de la cámara secundaria


class CamaraMesa:
    """La cámara secundaria de verdad: `look` con su camera_id."""

    def __init__(self, mesa, camara: str):
        self.mesa, self.camara = mesa, camara

    def foto(self):
        r = self.mesa.mirar(self.camara)
        if not r.imagenes:
            raise RuntimeError(f"look no devolvió foto de {self.camara}")
        return cv2.imdecode(np.frombuffer(r.imagenes[0], np.uint8), cv2.IMREAD_COLOR)


class CamaraArchivo:
    """Una imagen fija, o la más nueva de una carpeta (así se «escribe» en sim: se deja caer otra foto)."""

    def __init__(self, ruta: Path):
        self.ruta = Path(ruta)

    def foto(self):
        ruta = self.ruta
        if ruta.is_dir():
            fotos = sorted((p for p in ruta.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")), key=lambda p: p.stat().st_mtime)
            if not fotos:
                raise RuntimeError(f"no hay fotos en {ruta}")
            ruta = fotos[-1]
        img = cv2.imread(str(ruta))
        if img is None:
            raise RuntimeError(f"no pude leer {ruta}")
        return img


# ------------------------------------------------------------------ la escena


class Escritorio:
    def __init__(self, cfg: dict, mesa=None, camara=None, pluma=None, memoria: Path = MEMORIA):
        self.memoria = memoria
        self.cfg = cfg
        self.mesa = mesa
        self.camara = camara
        self.pluma = pluma
        self.escena = cfg.get("escena_mesa", "Escritorio")
        lib = cfg["libreta"]
        self.lector = Lector.cargar(memoria, girar=lib.get("girar", 0))
        self.libreta = dibujo.LibretaEnMesa(lib["esquinas_mm"])
        self.notas: list[str] | None = None  # ids de las notas proyectadas; None = no sé (hay que mirar la mesa)
        self.yo = {"nombre": cfg["agentes"].get("yo", "escritorio"), "estado": "trabajando", "detalle": "mirando tu libreta", "desde": "ahora"}
        self.panel_antes = None
        self.panel_html = None
        self.ultima_foto = None
        self.jev = cfg.get("jev", {}) if cfg.get("jev", {}).get("activo") else {}
        self.hilos = ThreadPoolExecutor(max_workers=2)
        self.jev_ok = jev.disponible() if self.jev else False

    # -------------------------------------------------------------- mesa

    def proyectar(self, widgets):
        r = self.mesa.proyectar(widgets, self.escena)
        if r.get("reserved"):
            log("ojo:", r["reserved"])
        return r

    def ubicar_libreta(self):
        """La libreta del motor (objeto publicado) si existe; si no, las esquinas de escena.json."""
        nombre = self.cfg["libreta"].get("objeto")
        if not nombre or not self.mesa:
            return
        try:
            mundo = self.mesa.mundo(self.escena)
        except Exception as e:  # noqa: BLE001
            log("no pude leer el mundo:", e)
            return
        for o in mundo.get("objects", []):
            if nombre.lower() in str(o.get("label", "")).lower():
                esquinas = dibujo.esquinas_desde_objeto(o)
                if esquinas:
                    girar = self.cfg["libreta"].get("girar_mesa", 0)
                    esquinas = list(np.roll(np.float32(esquinas), -girar, axis=0))
                    self.libreta = dibujo.LibretaEnMesa(esquinas)
                    log(f"libreta: uso el objeto «{o.get('label')}» del motor")
                return

    def montar(self):
        escenas = self.mesa.escenas("list")
        nombres = [s["name"] for s in escenas.get("scenes", [])]
        if self.escena not in nombres:
            self.mesa.escenas("create", name=self.escena)
        else:
            self.mesa.escenas("activate", name=self.escena)
        self.ubicar_libreta()
        self.limpiar()
        self.proyectar([dibujo.marco(self.libreta, self.cfg)] + self.panel(forzar=True))
        log(f"escena «{self.escena}» montada: libreta en {self.libreta.ubicar([0, 0, self.libreta.ancho, self.libreta.alto])}")

    def limpiar(self):
        mundo = self.mesa.mundo(self.escena)
        mias = [w["id"] for w in mundo.get("widgets", []) if w["id"].startswith(f"{dibujo.PREFIJO}:nota") or w["id"] in (
            f"{dibujo.PREFIJO}:marca", f"{dibujo.PREFIJO}:estado")]
        if mias:
            self.mesa.quitar(mias, self.escena)
        self.notas = []
        self.lector.olvidar_respuestas()
        self.lector.guardar(self.memoria)

    def panel(self, forzar=False) -> list[dict]:
        """El widget del panel si cambió algo (agentes.json, mi estado o el minuto); si no, nada."""
        lista = dibujo.leer_agentes(self.cfg)
        clave = json.dumps([lista, self.yo, time.strftime("%H:%M")], ensure_ascii=False)
        if not forzar and clave == self.panel_html:
            return []
        w = dibujo.agentes(self.cfg, lista, self.yo, self.panel_antes)
        self.panel_html = clave
        self.panel_antes = lista + [dict(self.yo)]
        return [w]

    def refrescar_panel(self, forzar=False):
        ws = self.panel(forzar)
        if ws:
            self.proyectar(ws)

    def yo_estado(self, detalle: str, estado: str = "trabajando"):
        self.yo.update(detalle=detalle, estado=estado, desde=time.strftime("%H:%M") if estado == "listo" else "ahora")

    def decir(self, detalle: str, estado: str = "trabajando"):
        self.yo_estado(detalle, estado)
        self.refrescar_panel()

    # -------------------------------------------------------------- leer y contestar

    def mirar(self) -> dict:
        self.ultima_foto = self.camara.foto()
        return self.lector.mirar(self.ultima_foto)

    def senalado(self, m: dict) -> tuple[float, float] | None:
        """Dónde apunta el dedo en la última foto, en coordenadas de la libreta (0..1). Con Jev; si no, None."""
        if not (self.jev_ok and self.jev.get("senalar", True)) or self.ultima_foto is None or not m.get("libreta"):
            return None
        s = jev.senala(self.ultima_foto)
        if not s:
            return None
        quad = np.float32(m["libreta"]["quad_px"])
        h = cv2.getPerspectiveTransform(quad, np.float32([[0, 0], [1, 0], [1, 1], [0, 1]]))
        u, v = cv2.perspectiveTransform(np.float32([[s["centro"]]]), h)[0, 0]
        log(f"jev: señala ({u:.2f}, {v:.2f}) de la libreta, {s['ms']:.0f} ms")
        return (float(u), float(v)) if -0.05 <= u <= 1.05 and -0.05 <= v <= 1.05 else None

    def imagenes_senaladas(self, m: dict, punto: tuple[float, float]) -> list[bytes] | None:
        """La página con el renglón que señaló marcado en rojo, y ese renglón recortado (lo que va al modelo)."""
        pagina = self.lector.pagina_actual()
        if pagina is None or not m.get("pagina_rect"):
            return None
        px, py, pw, ph = m["pagina_rect"]
        u, v = (punto[0] - px) / pw, (punto[1] - py) / ph
        if not (0 <= u <= 1):
            return None
        caja = [0.03, float(np.clip(v - 0.06, 0, 0.88)), 0.94, 0.12]
        return [jpg(marcar(pagina, caja)), jpg(recorte(pagina, caja))]

    def mostrar_leido(self, m: dict):
        """Apenas se decide leer: corchete rojo en lo nuevo y «leyendo» junto al botón. Se ve antes que la respuesta."""
        ws = [dibujo.estado(self.libreta, self.cfg, "leyendo")]
        if m.get("renglones_libreta"):
            ws.append(dibujo.marca(self.libreta, self.cfg, m["renglones_libreta"]))
        self.yo_estado("leyendo tu libreta")
        self.proyectar(ws + self.panel())  # una sola llamada: corchete, «leyendo» y panel a la vez

    def escribir_respuesta(self, lineas: list[str], m: dict | None = None) -> dict | None:
        tam = dibujo.medida_nota(self.libreta, self.cfg, lineas)
        hueco = self.lector.hueco(tam)
        if self.notas is None and self.mesa:  # otra corrida dejó notas en la mesa: se sigue la numeración
            mundo = self.mesa.mundo(self.escena)
            self.notas = sorted((w["id"] for w in mundo.get("widgets", []) if w["id"].startswith(f"{dibujo.PREFIJO}:nota:")),
                                key=lambda i: int(i.rsplit(":", 1)[1]))
        self.notas = self.notas or []
        if hueco is None:  # la página se llenó de respuestas: se borran las viejas y se intenta de nuevo
            if self.notas:
                self.mesa.quitar(self.notas, self.escena)
            self.notas = []
            self.lector.olvidar_respuestas()
            hueco = self.lector.hueco(tam)
        if hueco is None:
            log("no hay lugar libre en la página para contestar")
            return None
        n = int(self.notas[-1].rsplit(":", 1)[1]) + 1 if self.notas else 0
        w = dibujo.nota(self.libreta, self.cfg, n, hueco["rect_libreta"], lineas, hueco["escala"])
        self.notas.append(w["id"])
        self.ultima_nota = w
        self.yo_estado("te contestó en la libreta", "listo")
        # una sola llamada: la nota, el «leyendo» apagado (vacío) y el panel al día
        self.proyectar([w, dibujo.estado(self.libreta, self.cfg, "", vivo=False)] + self.panel())
        return hueco

    def repetir(self) -> bool:
        """Vuelve a escribir la última respuesta (la animación de la mano parte de cero). Pedido del agente de voz."""
        w = getattr(self, "ultima_nota", None)
        if not w:
            return False
        self.mesa.quitar([w["id"]], self.escena)
        self.proyectar([w])
        return True

    def ciclo(self, m: dict | None = None, dicho: str | None = None, por: str = "auto") -> dict | None:
        t0 = time.monotonic()
        m = m or self.mirar()
        # «¿qué es esto?» mientras señala: Jev dice dónde apunta el dedo antes de que la mano se vaya
        punto = self.senalado(m) if jev.es_deictico(dicho) else None
        espera = time.monotonic() + 4
        while m["estado"] == "mano" and time.monotonic() < espera:  # se está terminando de escribir
            time.sleep(0.4)
            m = self.mirar()
        if m["estado"] == "sin_libreta":
            self.proyectar([dibujo.estado(self.libreta, self.cfg, "no veo la libreta", vivo=False)])
            return None
        if m["estado"] != "nuevo" and not dicho:
            self.proyectar([dibujo.estado(self.libreta, self.cfg, "nada nuevo que leer", vivo=False)])
            return None
        self.mostrar_leido(m)
        imagenes = [jpg(m["contexto"])] + ([jpg(m["recorte"])] if m.get("recorte") is not None else [])
        senaladas = self.imagenes_senaladas(m, punto) if punto else None
        if senaladas:
            imagenes, dicho = senaladas, f"{dicho} (mientras lo decía señaló lo que está en el recuadro rojo)"
        # si lee solo, Jev mira en paralelo qué es lo nuevo: un dibujo o algo tachado no se contesta (no suma espera)
        filtro = None
        if por == "auto" and self.jev_ok and m["estado"] == "nuevo" and m.get("contexto") is not None:
            filtro = self.hilos.submit(jev.que_es, m["contexto"])
        t1 = time.monotonic()
        try:
            r = self.pluma.leer(imagenes, dicho)
        except Exception as e:  # noqa: BLE001
            log("el modelo falló:", e)
            self.proyectar([dibujo.estado(self.libreta, self.cfg, "no pude leer, toca de nuevo", vivo=False)])
            self.decir("no pudo leer la libreta", "esperando")
            return None
        t2 = time.monotonic()
        visto = None
        if filtro:
            try:
                visto = filtro.result(timeout=2)
            except Exception:  # noqa: BLE001
                visto = None
        if visto:
            log(f"jev: lo nuevo es «{visto[0]}» ({visto[1]:.2f}, {visto[2]:.0f} ms)")
        if visto and visto[0] in self.jev.get("no_contestar", ()) and visto[1] >= float(self.jev.get("confianza", 0.75)):
            # se da por leído sin escribir encima: un garabato no pide respuesta
            self.proyectar([dibujo.estado(self.libreta, self.cfg, "", vivo=False)])
            self.lector.confirmar()
            self.lector.guardar(self.memoria)
            self.decir(f"vio un {visto[0]}, no contesta" if visto[0] == "dibujo" else "vio algo tachado, no contesta", "listo")
            hueco, r = None, {**r, "respuesta": [], "omitido": visto[0]}
        else:
            hueco = self.escribir_respuesta(r["respuesta"], m)
            self.lector.confirmar()
            self.lector.guardar(self.memoria)
            self.decir("te contestó en la libreta", "listo")
        fila = {"hora": time.strftime("%H:%M:%S"), "por": por, "dicho": dicho, **r, "hueco": hueco,
                "jev": {"que_es": visto, "senala": punto},
                "s_ver": round(t1 - t0, 2), "s_modelo": round(t2 - t1, 2), "s_total": round(time.monotonic() - t0, 2)}
        SALIDA.mkdir(exist_ok=True)
        with open(SALIDA / "registro.jsonl", "a") as f:
            f.write(json.dumps(fila, ensure_ascii=False, default=float) + "\n")
        log(f"leí «{r['leido']}» → {r['respuesta']} ({fila['s_total']} s, modelo {fila['s_modelo']} s)")
        return fila

    # -------------------------------------------------------------- el bucle

    def cursor_actual(self) -> int:
        """Cursor del final, sin paginar el historial: el gateway reubica un cursor fuera de rango (como el laboratorio)."""
        return int(self.mesa.esperar_eventos(10**12, timeout_s=0).get("cursor", 0))

    def leer_eventos(self, eventos: list[dict], activa: bool) -> tuple[tuple | None, bool]:
        """De los eventos de la mesa: si hay que leer (y por qué) y si esta escena sigue a la vista."""
        d = self.cfg["disparo"]
        # eventos propios que emite un agente (el de voz no puede emitir voice_command ni toques): leer o repetir
        ev = d.get("eventos_agente", {})
        leer, repetir = set(ev.get("leer", ["leer_libreta", "responde", "paso_siguiente"])), set(ev.get("repetir", ["repetir"]))
        disparo = None
        for e in eventos:
            p = e.get("payload", e)
            nombre = p.get("event") if p.get("type") == "flow_emitted" else None
            data = p.get("data") or {}
            if nombre in leer | repetir and (data.get("scene") or self.escena) in (self.escena, "*"):
                disparo = ("agente", data.get("text")) if nombre in leer else ("repetir", None)
            elif p.get("type") == "widget_pressed" and p.get("widget_id") == f"{dibujo.PREFIJO}:marco" and d.get("boton", True):
                disparo = ("boton", None)
            elif p.get("type") == "flow_emitted" and p.get("event") == "scene_changed":
                activa = (p.get("data") or {}).get("scene") == self.escena
                log("escena activa:", (p.get("data") or {}).get("scene"))
            elif p.get("type") == "flow_emitted" and p.get("event") == "voice_command" and d.get("voz", True):
                data = p.get("data") or {}
                if data.get("text") and (data.get("scene") or self.escena) == self.escena:
                    disparo = ("voz", data["text"])
        return disparo, activa

    def vivo(self):
        d = self.cfg["disparo"]
        cada = float(d.get("cada_s", 1.0))
        log("calentando el modelo…")
        try:
            log(f"modelo listo en {self.pluma.calentar():.1f} s")
        except Exception as e:  # noqa: BLE001  sin CLI `claude`, sin sesión o sin red: la escena no se cae
            from pluma import PlumaFija

            log(f"AVISO: no pude abrir el modelo ({e!r}). Sigo con las respuestas fijas de escena.json (PlumaFija);"
                " revisa que `claude` esté instalado y con sesión en este Mac.")
            with contextlib.suppress(Exception):
                self.pluma.cerrar()
            self.pluma = PlumaFija(self.cfg, log=log)
        self.montar()
        try:  # lo que ya está escrito al partir cuenta como leído: solo se contesta lo que se escriba desde ahora
            if self.mirar()["estado"] in ("nuevo", "igual"):
                self.lector.confirmar()
                self.lector.guardar(self.memoria)
        except Exception as e:  # noqa: BLE001
            log("todavía no veo la libreta:", e)
        cursor = self.cursor_actual()
        activa = True
        candidato = None  # (pixeles, desde) de tinta nueva que todavía no se lee
        ultimo_panel = 0.0
        log("escena andando. Ctrl+C para salir.")
        while True:
            # una vuelta que falla (gateway caído, 502, timeout) se registra y se reintenta: la escena no se cae
            try:
                r = self.mesa.esperar_eventos(cursor, ["widget_pressed", "flow_emitted"], timeout_s=cada if d.get("auto") else 10)
                cursor = r.get("cursor", cursor)
                disparo, activa = self.leer_eventos(r.get("events", []), activa)
                if not activa:
                    continue
                if time.monotonic() - ultimo_panel > float(self.cfg["agentes"].get("cada_s", 3)):
                    self.refrescar_panel()
                    ultimo_panel = time.monotonic()
                if disparo:
                    log("disparo:", disparo[0], disparo[1] or "")
                    if disparo[0] == "repetir":
                        self.repetir()
                    else:
                        self.ciclo(dicho=disparo[1], por=disparo[0])
                    candidato = None
                    continue
                if not d.get("auto"):
                    continue
                try:
                    m = self.mirar()
                except Exception as e:  # noqa: BLE001
                    log("cámara secundaria:", e)
                    time.sleep(2)
                    continue
                if m["estado"] != "nuevo" or m["pixeles_nuevos"] < d.get("auto_min_pixeles", 250):
                    candidato = None
                    if m["estado"] == "mano":
                        self.decir("mirando cómo escribes")
                    continue
                ahora = time.monotonic()
                if candidato and abs(m["pixeles_nuevos"] - candidato[0]) <= 0.15 * candidato[0]:
                    if ahora - candidato[1] >= float(d.get("quieto_s", 1.2)):
                        self.ciclo(m, por="auto")
                        candidato = None
                else:
                    candidato = (m["pixeles_nuevos"], ahora)
            except Exception as e:  # noqa: BLE001
                log("sin mesa, reintento:", e)
                time.sleep(2)
                self.panel_html = None  # al volver, el panel se proyecta de nuevo
                candidato = None


# ------------------------------------------------------------------ ensayo sin cámara secundaria


def escribir_sim(carpeta: Path, texto: str | None, borrar: bool = False) -> Path:
    """Agrega una línea manuscrita a una libreta sintética y deja la foto nueva en la carpeta."""
    from percepcion import sintetico as s

    carpeta.mkdir(parents=True, exist_ok=True)
    estado = carpeta / "lineas.json"
    lineas = [] if borrar or not estado.exists() else json.loads(estado.read_text())
    if texto:
        lineas.append(texto)
    estado.write_text(json.dumps(lineas, ensure_ascii=False))
    # la letra Hershey no tiene tildes ni signos de apertura: se escriben sin ellos
    plano = [t.translate(str.maketrans("áéíóúñÁÉÍÓÚÑ¿¡", "aeiounAEIOUN  ")).strip() for t in lineas]
    f = s.foto(plano, semilla=len(lineas))
    ruta = carpeta / f"libreta-{len(lineas):02d}-{int(time.time())}.jpg"
    cv2.imwrite(str(ruta), f["img"])
    return ruta


def vista(cfg: dict, destino: Path, foto_libreta: Path | None, lineas: list[str]):
    """Dibuja la escena en un html suelto (mesa negra, libreta sintética debajo), para revisar el diseño sin la mesa."""
    lib = dibujo.LibretaEnMesa(cfg["libreta"]["esquinas_mm"])
    lector = Lector(girar=cfg["libreta"].get("girar", 0))
    widgets = [dibujo.marco(lib, cfg), dibujo.agentes(cfg, dibujo.leer_agentes(cfg), {"nombre": "escritorio", "estado": "listo", "detalle": "te contestó en la libreta", "desde": time.strftime("%H:%M")})]
    hoja = None
    if foto_libreta:
        img = cv2.imread(str(foto_libreta))
        m = lector.mirar(img)
        if m["estado"] == "nuevo":
            widgets.append(dibujo.marca(lib, cfg, m["renglones_libreta"]))
            h = lector.hueco(dibujo.medida_nota(lib, cfg, lineas))
            if h:
                widgets.append(dibujo.nota(lib, cfg, 0, h["rect_libreta"], lineas, h["escala"]))
        from percepcion import rectificar

        hoja = rectificar(img, m["libreta"]["quad_px"]) if m.get("libreta") else None
    destino.parent.mkdir(parents=True, exist_ok=True)
    partes = []
    if hoja is not None:
        ruta_hoja = destino.with_suffix(".hoja.jpg")
        cv2.imwrite(str(ruta_hoja), (hoja * 0.55).astype(np.uint8))  # la hoja en penumbra, como se ve con el proyector
        r = lib.ubicar([0, 0, lib.ancho, lib.alto])
        partes.append(f'<img src="{ruta_hoja.name}" style="position:absolute;left:{r["rect_mm"][0]}px;top:{r["rect_mm"][1]}px;'
                      f'width:{lib.ancho}px;height:{lib.alto}px;transform:rotate({r["rotate_deg"]}deg)">')
    import html as h_

    for w in widgets:
        x, y, ww, hh = w["rect_mm"]
        doc = f"<!doctype html><meta charset=utf-8><style>html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}}{w['params']['css']}</style><body>{w['params']['html']}</body>"
        partes.append(f'<iframe srcdoc="{h_.escape(doc)}" style="position:absolute;left:{x}px;top:{y}px;width:{ww}px;height:{hh}px;border:0;'
                      f'transform:rotate({w["params"]["rotate_deg"]}deg)"></iframe>')
    pagina = (f"<!doctype html><meta charset=utf-8><title>Escritorio · vista</title><body style='margin:0;background:#000'>"
              f"<div style='position:relative;width:1000px;height:563px;background:#1d1c1a;transform:scale(1.6);transform-origin:0 0'>"
              + "".join(partes) + "</div></body>")
    destino.write_text(pagina)
    (destino.with_suffix(".widgets.json")).write_text(json.dumps(widgets, ensure_ascii=False, indent=1))
    return widgets


def diagnostico(cfg: dict, foto, destino: Path) -> dict:
    """Para ajustar en el ensayo con la primera foto real de la cámara secundaria: qué ve la percepción.

    Deja una imagen con tres paneles: la foto con las esquinas encontradas (la «1» tiene que quedar arriba a la izquierda
    tal como lee Matilda; si no, cambia libreta.girar), la libreta de frente con la tinta en rojo, y el hueco donde
    escribiría el agente en azul.
    """
    from percepcion import espacio_libre, mascara_tinta, recorte_pagina, rectificar, ubicar_libreta

    girar = cfg["libreta"].get("girar", 0)
    lib = ubicar_libreta(foto, girar=girar)
    if lib is None:
        cv2.imwrite(str(destino), foto)
        return {"libreta": None, "consejo": "No encontré la libreta: que se vea entera, con algo de borde de mesa alrededor."}
    a = foto.copy()
    q = np.int32(lib["quad_px"])
    cv2.polylines(a, [q], True, (60, 200, 255), 3)
    for i, p in enumerate(q):
        cv2.putText(a, str(i + 1), tuple(int(v) for v in p), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (60, 200, 255), 3)
    hoja = rectificar(foto, lib["quad_px"])
    pagina = lib["paginas"][-1]
    img = recorte_pagina(hoja, pagina)
    tinta = mascara_tinta(img)
    b = img.copy()
    b[tinta > 0] = (40, 40, 230)
    hueco = espacio_libre(tinta, (0.6, 0.15))
    if hueco:
        x, y, w, h = hueco["rect"]
        H, W = tinta.shape
        cv2.rectangle(b, (int(x * W), int(y * H)), (int((x + w) * W), int((y + h) * H)), (230, 140, 40), 3)
    alto = 600
    paneles = [cv2.resize(p, (int(p.shape[1] * alto / p.shape[0]), alto)) for p in (a, b)]
    cv2.imwrite(str(destino), np.hstack(paneles))
    return {
        "libreta": {k: lib[k] for k in ("quad_px", "aspecto", "paginas", "area")},
        "tinta_px": int(np.count_nonzero(tinta)),
        "hueco": hueco,
        "imagen": str(destino),
    }


# ------------------------------------------------------------------ terminal


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comando", choices=["montar", "vivo", "leer", "responder", "agentes", "limpiar", "escribir", "vista", "diagnostico"])
    ap.add_argument("textos", nargs="*", help="líneas de la respuesta (responder), lo que se escribe (escribir) o el html (vista)")
    ap.add_argument("--libreta-sim", type=Path, help="carpeta o foto que hace de cámara secundaria (ensayo sin cámara)")
    ap.add_argument("--fijas", action="store_true", help="contesta con las respuestas fijas de escena.json (sin modelo)")
    ap.add_argument("--dicho", help="lo que se dijo en voz alta (leer)")
    ap.add_argument("--borrar", action="store_true", help="escribir: empieza una libreta en blanco")
    a = ap.parse_args(argv)
    cfg = cargar_cfg()
    import signal

    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))  # cerrar el proceso de claude
    SALIDA.mkdir(exist_ok=True)

    if a.comando == "escribir":
        print(escribir_sim(a.libreta_sim or SALIDA / "sim", " ".join(a.textos) or None, a.borrar))
        return
    if a.comando == "diagnostico":
        if not a.libreta_sim:
            ap.error("diagnostico necesita --libreta-sim <foto de la cámara secundaria>")
        print(json.dumps(diagnostico(cfg, CamaraArchivo(a.libreta_sim).foto(), SALIDA / "diagnostico.jpg"), ensure_ascii=False, default=float, indent=1))
        return
    if a.comando == "vista":
        destino = Path(a.textos[0]) if a.textos else SALIDA / "vista.html"
        foto = CamaraArchivo(a.libreta_sim).foto() if a.libreta_sim else None
        ruta_foto = None
        if foto is not None:
            ruta_foto = SALIDA / "vista-libreta.jpg"
            cv2.imwrite(str(ruta_foto), foto)
        vista(cfg, destino, ruta_foto, cfg["respuesta"]["fijas"][0])
        print(destino)
        return

    from mesa import Mesa
    from pluma import Pluma, PlumaFija

    mesa = Mesa()
    camara = CamaraArchivo(a.libreta_sim) if a.libreta_sim else CamaraMesa(mesa, cfg["libreta"].get("camara", "cam-3"))
    pluma = (PlumaFija if a.fijas else Pluma)(cfg, log=log)
    e = Escritorio(cfg, mesa, camara, pluma)
    try:
        if a.comando == "montar":
            e.montar()
        elif a.comando == "agentes":
            e.refrescar_panel(forzar=True)
        elif a.comando == "limpiar":
            e.limpiar()
        elif a.comando == "vivo":
            e.ubicar_libreta()
            e.vivo()
        elif a.comando == "leer":
            m = e.mirar()
            salida = {k: v for k, v in m.items() if k not in ("recorte", "contexto")}
            if m.get("contexto") is not None:
                (SALIDA / "contexto.jpg").write_bytes(jpg(m["contexto"]))
                salida["contexto"] = str(SALIDA / "contexto.jpg")
            if m.get("recorte") is not None:
                (SALIDA / "recorte.jpg").write_bytes(jpg(m["recorte"]))
                salida["recorte"] = str(SALIDA / "recorte.jpg")
            if m["estado"] == "nuevo":
                e.mostrar_leido(m)
            e.lector.guardar(MEMORIA)
            print(json.dumps(salida, ensure_ascii=False, default=float, indent=1))
        elif a.comando == "responder":
            lineas = [t for t in a.textos if t.strip()]
            if not lineas:
                ap.error("responder necesita al menos una línea")
            hueco = e.escribir_respuesta(lineas)
            e.lector.confirmar()
            e.lector.guardar(MEMORIA)
            e.decir("te contestó en la libreta", "listo")
            print(json.dumps({"hueco": hueco}, default=float))
    except KeyboardInterrupt:
        pass
    finally:
        pluma.cerrar()


if __name__ == "__main__":
    main()

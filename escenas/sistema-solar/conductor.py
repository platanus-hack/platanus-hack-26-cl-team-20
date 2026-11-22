#!/usr/bin/env python3
"""Conductor de la escena Sistema solar: une la percepción con la mesa por el MCP del gateway.

Lo usa el agente que conduce la escena (ver `escena.md`) y también se puede correr a mano. Cada comando es una acción
completa y sigue la gramática común de la mesa (señalar, nombrar, explicar, tocar, hablar):

    uv run python conductor.py armar             # mira la mesa, reconoce Sol, bolas y lámina, y proyecta el sistema
    uv run python conductor.py seguir            # bucle: sigue el canasto y abre la ficha al tocar la lámina
    uv run python conductor.py nombrar sol tierra   # nombrar: hasta 3 etiquetas (sin nada, las quita)
    uv run python conductor.py ficha saturno     # explicar: Lumo vuela a Saturno, lo señala y abre su ficha
    uv run python conductor.py decir saturno "Mis anillos son de hielo y roca, y brillan con el Sol." --corto "Son de hielo y roca"
                                                 # hablar: Lumo vuela a la bola y lo dice con voz + texto corto
    uv run python conductor.py escuchar saturno  # el halo late rápido: «te estoy escuchando»
    uv run python conductor.py callar            # quita burbuja, halo y nombres
    uv run python conductor.py fijar tierra 3    # la bola 3 es la Tierra (los números salen en `estado`)
    uv run python conductor.py ajustar 4 -2      # corre toda la luz 4 mm a la derecha y 2 hacia arriba
    uv run python conductor.py redibujar         # vuelve a proyectar (después de editar tema.css o planetas.json)
    uv run python conductor.py estado            # qué bola es qué, dudas, pose del canasto
    uv run python conductor.py limpiar           # quita todo lo de la escena (y solo lo suyo)

Conexión: `PCX_MCP_URL` (por defecto http://localhost:9321/mcp) y `PCX_USER_TOKEN` (botón «Copiar» de la consola).

Con `--sim` (consola abierta con `?sim=1`): proyecta la foto real de la mesa como fondo para que la cámara simulada vea
el canasto, y `sim-mover <grados> <dx> <dy>` simula que alguien mueve solo el canasto.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

import escena
from percepcion import (
    Cuerpo,
    Pose,
    aplicar_pose,
    asignar_planetas,
    cargar_catalogo,
    detectar_cuerpos,
    espacio_libre,
    movimiento,
    planetas_en_lamina,
    pose_grupo,
    rectificar,
    ubicar_canasto,
    ubicar_laminas,
)
from percepcion.cuerpos import PARAMS as PARAMS_CUERPOS
from percepcion.cuerpos import _rayos, mascara_blanca
from percepcion.lamina import Lamina

AQUI = Path(__file__).resolve().parent
ESTADO = AQUI / "estado" / "estado.json"
MESA = AQUI / "mesa.json"
FOTO_SIM = AQUI.parents[1] / "data" / "mesa" / "2026-11-21" / "mesa-cenital-mm-1.jpg"


def log(*a) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- MCP


class Mesa:
    """Cliente mínimo del MCP del gateway (Streamable HTTP sin estado, Bearer)."""

    def __init__(self, url: str, token: str):
        self.url, self.token, self.n = url, token, 0
        self.lock = threading.Lock()

    def llamar(self, tool: str, args: dict, timeout: float = 60) -> tuple[dict, list[np.ndarray]]:
        with self.lock:
            self.n += 1
            n = self.n
        body = json.dumps({"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": tool, "arguments": args}}).encode()
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        )
        txt = urllib.request.urlopen(req, timeout=timeout).read().decode()
        if not txt.lstrip().startswith("{"):  # respuesta SSE: la última línea data:
            datos = [ln[5:].strip() for ln in txt.splitlines() if ln.startswith("data:")]
            txt = datos[-1] if datos else txt
        msg = json.loads(txt)
        if "error" in msg:
            raise RuntimeError(f"{tool}: {msg['error']}")
        res = msg["result"]
        data, imgs, textos = res.get("structuredContent") or {}, [], []
        for c in res.get("content", []):
            if c.get("type") == "image":
                buf = np.frombuffer(base64.b64decode(c["data"]), np.uint8)
                imgs.append(cv2.imdecode(buf, cv2.IMREAD_COLOR))
            elif c.get("type") == "text":
                textos.append(c.get("text", ""))
                if not data:
                    try:
                        data = json.loads(c["text"])
                    except (json.JSONDecodeError, TypeError):
                        pass
        if res.get("isError"):
            raise RuntimeError(f"{tool}: {' '.join(textos)[:400]}")
        return data, imgs

    def project(self, widgets: list[dict]) -> dict:
        return self.llamar("project", {"request_id": f"solar-{time.time_ns()}", "widgets": widgets}, 30)[0]

    def remove(self, ids: list[str]) -> dict:
        return self.llamar("remove", {"request_id": f"solar-rm-{time.time_ns()}", "ids": ids}, 30)[0]

    def objetos(self, objetos: list[dict]) -> dict:
        return self.llamar("scenes", {"action": "objects", "objects": objetos}, 30)[0]

    def eventos(self, cursor: int, tipos: list[str], timeout_s: float = 20) -> dict:
        return self.llamar("events_wait", {"cursor": cursor, "types": tipos, "timeout_s": timeout_s}, timeout_s + 15)[0]

    def emitir(self, evento: str, data: dict) -> bool:
        """Evento propio para los demás agentes (`scenes` action `emit` de SAT: llega como `flow_emitted` con
        `by: "agent"`; máx. 5 por segundo y 2000 caracteres). Si el gateway todavía no lo tiene, usa un `run_flow` de un
        estado con una acción `emit`. Nunca lanza: devuelve si salió."""
        try:
            self.llamar("scenes", {"action": "emit", "event": evento, "data": data}, 15)
            return True
        except Exception:  # noqa: BLE001
            pass
        try:
            flujo = {"id": f"solar-emit-{time.time_ns()}", "initial": "e", "states": [{"id": "e", "on_enter": [{"type": "emit", "event": evento, "data": data}], "final": True}]}
            self.llamar("run_flow", {"request_id": f"solar-emit-{time.time_ns()}", "flow": flujo}, 15)
            return True
        except Exception as e:  # noqa: BLE001
            log("emit:", e)
            return False


# ---------------------------------------------------------------- estado y ajustes


def leer_json(p: Path, defecto: dict | None = None) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(defecto or {})


def guardar_estado(est: dict) -> None:
    ESTADO.parent.mkdir(exist_ok=True)
    tmp = ESTADO.with_suffix(".tmp")
    tmp.write_text(json.dumps(est, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ESTADO)


def cuerpos_de(est: dict) -> list[Cuerpo]:
    return [Cuerpo(c["tipo"], tuple(c["centro_mm"]), c["radio_mm"], c.get("puntaje", 1), c.get("dientes", 0)) for c in est["cuerpos"]]


def pose_de(est: dict) -> Pose:
    p = est.get("pose") or {}
    return Pose(p.get("theta_deg", 0.0), tuple(p.get("t_mm", (0.0, 0.0))), 0, 0, 0.0, True)


# ---------------------------------------------------------------- fotos


CAMARA = os.environ.get("PCX_CAMARA") or None  # cámara cenital (p. ej. cam-2); None = la principal de la consola


def foto_mm(mesa: Mesa, limpia: bool, sim: bool) -> tuple[np.ndarray, dict]:
    """Foto rectificada (1 px = 1 mm). En sim la cámara no ve los html, así que toda foto es «limpia»."""
    args = {"annotate": False, **({"camera_id": CAMARA} if CAMARA else {})}
    if limpia and not sim:
        args["clean"] = True
    data, imgs = mesa.llamar("look", args, 40)
    if not imgs:
        raise RuntimeError("look no devolvió foto")
    if not data.get("calibrated"):
        raise RuntimeError("La mesa no está calibrada: corre calibrate (botón Calibrar de la consola) y vuelve a armar.")
    sup = data.get("surface_mm") or [1000, 563]
    return rectificar(imgs[0], data["matrix_px_to_mm"], sup, data.get("lens")), data


# ---------------------------------------------------------------- armar


def armar(mesa: Mesa, sim: bool, intro: bool = True) -> dict:
    cat = cargar_catalogo()
    cfg = leer_json(MESA)
    if sim:
        poner_fondo_sim(mesa, 0, (0, 0))
    img, data = foto_mm(mesa, True, sim)
    ESTADO.parent.mkdir(exist_ok=True)
    cv2.imwrite(str(ESTADO.parent / "referencia.jpg"), img)
    canasto = ubicar_canasto(img)
    zona = canasto.zona() if canasto else None
    cuerpos = detectar_cuerpos(img, zona, sol_previo=sol_de_antes())
    if cfg.get("armar", {}).get("solo_en_el_canasto", True):
        cuerpos = dentro_del_canasto(cuerpos, canasto)
    if not [c for c in cuerpos if c.tipo == "bola"]:
        raise RuntimeError("No veo bolas: ¿está el canasto bajo la cámara? (foto en estado/referencia.jpg)")
    sup = data.get("surface_mm") or [1000, 563]
    previo = leer_json(ESTADO) if ESTADO.exists() else {}
    if not previo.get("asignacion") or [round(float(v)) for v in previo.get("surface_mm") or []] != [round(float(v)) for v in sup]:
        previo = {}  # primer armado, o recalibraron: no hay identidad que mantener
    fijos = fijos_a_indices(cfg.get("fijos", {}), cuerpos)
    cuerpos, pegados, fantasmas = mantener_identidad(previo, cuerpos, fijos, cfg)
    asig = asignar_planetas(cuerpos, cat, {**pegados, **fijos})
    if previo:
        # Las dudas de las bolas que conservaron su planeta siguen siendo dudas (Lumo puede seguir preguntando).
        ya = {d["cuerpo"] for d in asig.dudas}
        for d in previo["asignacion"].get("dudas", []):
            i = pegados.get(d.get("asignado"))
            if i is not None and i not in ya and d["asignado"] not in fijos:
                asig.dudas.append({**d, "cuerpo": i})
    laminas = ubicar_laminas(img)
    est = {
        "creado": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sim": sim,
        "surface_mm": sup,
        "canasto": canasto.dict() if canasto else None,
        "cuerpos": [c.dict() for c in cuerpos],
        "asignacion": asig.dict(),
        "fantasmas": fantasmas,
        "laminas": [l.dict() for l in laminas],
        "pose": {"theta_deg": 0.0, "t_mm": [0.0, 0.0]},
        "elegido": previo.get("elegido") if previo.get("elegido") in (asig.planetas.keys() | {"sol"}) else None,
    }
    if asig.sol is not None:
        c = cuerpos[asig.sol]
        if str(asig.sol) in fantasmas and previo.get("sol_contorno_rel"):
            est["sol_contorno_rel"] = previo["sol_contorno_rel"]
        else:
            # Silueta real del engranaje (con dientes), relativa a su centro: la capa visual la usa de máscara del Sol.
            est["sol_contorno_rel"] = [[round(x - c.centro_mm[0], 1), round(y - c.centro_mm[1], 1)] for x, y in contorno_engranaje(img, c)]
        actualizar_poligono_sol(est)
    avisar(est, avisos_de_cobertura(img, canasto, laminas))
    guardar_estado(est)
    if previo:
        # Lo que de verdad salió de la mesa: fuera sus widgets (sus objetos los saca publicar_objetos).
        idos = set(previo["asignacion"].get("planetas", {})) - set(asig.planetas)
        if idos:
            mesa.remove([f"solar-planeta-{p}" for p in idos] + [f"solar-nombre-{p}" for p in idos])
            log("ya no están:", ", ".join(sorted(idos)))
    proyectar_todo(mesa, est, intro=intro)
    resumen(est)
    return est


def mantener_identidad(previo: dict, cuerpos: list[Cuerpo], fijos: dict[str, int], cfg: dict) -> tuple[list[Cuerpo], dict[str, int], dict[str, int]]:
    """Ids estables entre armados: cada cuerpo del armado anterior que se vuelve a ver cerca de donde estaba (en su pose
    seguida) conserva su planeta, y con él su id `solar:<planeta>`, aunque por tamaño hoy tocara otro. Lo que no se ve
    queda una vez como fantasma en su última posición; si tampoco aparece en el armado siguiente, sale de la mesa.
    Lo fijado a mano manda sobre todo esto.

    Devuelve (cuerpos con los fantasmas agregados al final, planeta → índice que se mantiene, {índice: armados sin verlo})."""
    if not previo:
        return cuerpos, {}, {}
    dx, dy = (cfg.get("ajuste", {}).get("desplazamiento_mm") or [0, 0])[:2]
    a = previo["asignacion"]
    antes = {pid: (x - dx, y - dy) for pid, (x, y) in centros_actuales(previo).items()}
    indice = {"sol": a.get("sol"), **a.get("planetas", {})}
    fant_previos = previo.get("fantasmas") or {}
    ocupados = set(fijos.values())
    pares = []
    for pid, (x, y) in antes.items():
        if pid in fijos or indice.get(pid) is None:
            continue
        ref = previo["cuerpos"][indice[pid]]
        for j, c in enumerate(cuerpos):
            if j in ocupados or c.tipo != ref["tipo"]:
                continue
            d = math.dist((x, y), c.centro_mm)
            if d <= 30 and abs(c.radio_mm - ref["radio_mm"]) <= 0.3 * max(c.radio_mm, ref["radio_mm"]) + 3:
                pares.append((d, pid, j))
    pegados: dict[str, int] = {}
    for _, pid, j in sorted(pares):
        if pid not in pegados and j not in pegados.values():
            pegados[pid] = j
    cuerpos = list(cuerpos)
    fantasmas: dict[str, int] = {}
    for pid, (x, y) in antes.items():
        if pid in pegados or pid in fijos or indice.get(pid) is None:
            continue
        if pid == "sol" and any(c.tipo == "engranaje" for c in cuerpos):
            continue  # el Sol se ve en otro lado: es el mismo solar:sol
        sin_ver = int(fant_previos.get(str(indice[pid]), 0)) + 1
        if sin_ver > 1:
            continue  # dos armados seguidos sin verlo: salió de verdad
        ref = previo["cuerpos"][indice[pid]]
        cuerpos.append(Cuerpo(ref["tipo"], (round(x, 1), round(y, 1)), ref["radio_mm"], round(ref.get("puntaje", 1) * 0.5, 3), ref.get("dientes", 0)))
        fantasmas[str(len(cuerpos) - 1)] = sin_ver
        if pid != "sol":
            pegados[pid] = len(cuerpos) - 1
        log(f"no veo {pid}: lo dejo donde estaba por este armado (si en el próximo tampoco aparece, sale)")
    if pegados:
        log(f"mantengo {len(pegados)} planetas en sus bolas por posición")
    return cuerpos, pegados, fantasmas


def contorno_engranaje(img: np.ndarray, c: Cuerpo, n: int = 64) -> list[tuple[float, float]]:
    """Contorno del engranaje en mm de la mesa: `n` rayos desde su centro hasta el borde de la máscara fina (la de
    5 mm, que deja ver los dientes). Sin MobileSAM; si el motor publica `solar:sol` con su máscara, manda esa."""
    fina = mascara_blanca(img, PARAMS_CUERPOS, PARAMS_CUERPOS["apertura_fina_mm"])
    largos = _rayos(fina, c.centro_mm, c.radio_mm * 1.3, n)
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return [(round(float(c.centro_mm[0] + math.cos(a) * r), 1), round(float(c.centro_mm[1] + math.sin(a) * r), 1)) for a, r in zip(ang, largos)]


def actualizar_poligono_sol(est: dict) -> None:
    """`est["sol_poligono_mm"]`: la silueta del engranaje donde está AHORA (centro actual, girada con el canasto)."""
    rel = est.get("sol_contorno_rel")
    sol = centros_actuales(est).get("sol") if est["asignacion"].get("sol") is not None else None
    if not rel or sol is None:
        est.pop("sol_poligono_mm", None)
        return
    t = math.radians(pose_de(est).theta_deg)
    ct, st = math.cos(t), math.sin(t)
    est["sol_poligono_mm"] = [[round(sol[0] + ct * x - st * y, 1), round(sol[1] + st * x + ct * y, 1)] for x, y in rel]


def fijos_a_indices(fijos_mm: dict, cuerpos: list[Cuerpo]) -> dict[str, int]:
    out = {}
    for pid, pos in fijos_mm.items():
        d = [(math.hypot(c.centro_mm[0] - pos[0], c.centro_mm[1] - pos[1]), i) for i, c in enumerate(cuerpos) if c.tipo == "bola"]
        if d and min(d)[0] < 30:
            out[pid] = min(d)[1]
    return out


def planeta_de(pid: str) -> dict:
    cat = cargar_catalogo()
    return cat["sol"] if pid == "sol" else next(p for p in cat["planetas"] if p["id"] == pid)


def centros_actuales(est: dict) -> dict[str, tuple[float, float]]:
    """Centro de cada cuerpo asignado (sol y planetas) en la pose actual, con el ajuste fino de mesa.json."""
    a = est["asignacion"]
    cfg = leer_json(MESA)
    dx, dy = (cfg.get("ajuste", {}).get("desplazamiento_mm") or [0, 0])[:2]
    ids = ([("sol", a["sol"])] if a.get("sol") is not None else []) + list(a["planetas"].items())
    pts = aplicar_pose([est["cuerpos"][i]["centro_mm"] for _, i in ids], pose_de(est))
    pos = est.get("pos") or {}  # cada bola donde se la vio por última vez (si se vio); si no, la pose del canasto
    return {pid: (float((pos.get(str(i)) or p)[0] + dx), float((pos.get(str(i)) or p)[1] + dy)) for (pid, i), p in zip(ids, pts)}


def avisos_de_cobertura(img: np.ndarray, canasto, laminas: list) -> list[str]:
    """Avisos claros cuando algo está cortado o fuera de lo que ve la cámara (lo negro de la vista en mm)."""
    fuera = (img.max(axis=2) <= 8).astype(np.uint8)
    cerca = cv2.dilate(fuera, np.ones((25, 25), np.uint8))
    h, w = fuera.shape
    avisos = []
    if canasto is None:
        avisos.append("No veo el canasto: ponlo entero dentro de lo que ve la cámara (lo negro de estado/referencia.jpg no se ve).")
    else:
        pts = [(int(min(max(x, 0), w - 1)), int(min(max(y, 0), h - 1))) for x, y in canasto.poligono_mm]
        if any(cerca[y, x] or x < 12 or y < 12 or x > w - 13 or y > h - 13 for x, y in pts):
            avisos.append(f"El canasto está cortado por el borde de lo que ve la cámara (centro {tuple(round(v) for v in canasto.centro_mm)} mm): muévelo hacia el centro de la zona que se ve.")
        if min(p[0] for p in pts) < escena.BARRA_MM:
            avisos.append("El canasto queda bajo la barra de pestañas (x < 150 mm): córrelo a la derecha.")
    if not laminas:
        avisos.append("No veo la lámina: ponla entera dentro de lo que ve la cámara, sin nada encima.")
    return avisos


def avisar(est: dict, avisos: list[str]) -> None:
    """Escribe los avisos en el log solo cuando cambian (no en cada foto)."""
    if avisos != est.get("avisos"):
        for a in avisos:
            log("AVISO:", a)
        if not avisos and est.get("avisos"):
            log("avisos resueltos: canasto y lámina a la vista")
        est["avisos"] = avisos


def reservadas(est: dict) -> list[list[float]]:
    """Barra de pestañas y esquina de voz de la consola (`mesa.json` → `interfaz.girada` si la consola tiene flip)."""
    girada = bool(leer_json(MESA).get("interfaz", {}).get("girada", False))
    return escena.zonas_reservadas(tuple(est.get("surface_mm", [1000, 563])), girada)


def sol_de_antes() -> tuple[float, float] | None:
    """Dónde estaba el Sol en el último armado (en su pose más reciente), para la histéresis de `detectar_cuerpos`:
    ahí se lo sigue aceptando con menos evidencia. Solo es una pista para mirar; nunca se dibuja ahí si no se ve."""
    try:
        previo = leer_json(ESTADO) if ESTADO.exists() else None
        if previo and previo.get("asignacion", {}).get("sol") is not None:
            return centros_actuales(previo).get("sol")
    except Exception:  # noqa: BLE001
        pass
    return None


def dentro_del_canasto(cuerpos: list, canasto) -> list:
    """Solo lo que está en el canasto: una bola con el centro bien adentro (la flor del borde no cuenta) y el
    engranaje con el centro a menos de un radio de afuera (suele quedar asomado en una esquina)."""
    if canasto is None:
        return cuerpos
    poly = np.asarray(canasto.poligono_mm, dtype=np.float32)
    out = []
    for c in cuerpos:
        d = cv2.pointPolygonTest(poly, (float(c.centro_mm[0]), float(c.centro_mm[1])), True)
        if d >= (-c.radio_mm if c.tipo == "engranaje" else 0.3 * c.radio_mm):
            out.append(c)
        else:
            log(f"descarto {c.tipo} en ({c.centro_mm[0]:.0f}, {c.centro_mm[1]:.0f}): queda fuera del canasto")
    return out


def centro_orbitas(est: dict) -> tuple[float, float] | None:
    """Dónde va el centro de las órbitas AHORA: el Sol si se ve; si no, el centro del canasto en la pose actual
    (nunca una posición guardada del Sol). Sin canasto, el promedio de las bolas."""
    cs = centros_actuales(est)
    if cs.get("sol") is not None:
        return cs["sol"]
    k = poligono_canasto(est)
    if k is not None:
        return k[1]
    pts = [cs[p] for p in est["asignacion"]["planetas"] if p in cs]
    return tuple(np.mean(pts, axis=0)) if pts else None


def poligono_canasto(est: dict) -> tuple[list[list[float]], tuple[float, float]] | None:
    """El canasto en la pose actual (con el ajuste fino de mesa.json): (polígono, centro) en mm."""
    k = est.get("canasto")
    if not k or not k.get("centro_mm"):
        return None
    dx, dy = (leer_json(MESA).get("ajuste", {}).get("desplazamiento_mm") or [0, 0])[:2]
    pts = aplicar_pose(k["poligono_mm"], pose_de(est)) + np.array([dx, dy])
    x, y = aplicar_pose([k["centro_mm"]], pose_de(est))[0]
    return [[round(float(a), 1), round(float(b), 1)] for a, b in pts], (float(x + dx), float(y + dy))


def widget_orbitas_de(est: dict, intro: bool) -> dict | None:
    """Las órbitas en la pose actual, con la interfaz de la mesa enmascarada. Sin Sol, alrededor del canasto."""
    a = est["asignacion"]
    cs = centros_actuales(est)
    sol = centro_orbitas(est)
    if sol is None or not a["planetas"]:
        return None
    orbitas = []
    for pid in a["planetas"]:
        x, y = cs[pid]
        orbitas.append({"radio_mm": math.dist((x, y), sol), "angulo_deg": math.degrees(math.atan2(y - sol[1], x - sol[0])), "color": planeta_de(pid)["colores"][1]})
    ajuste = leer_json(MESA).get("ajuste", {})
    ancla = "sol" if cs.get("sol") is not None else "canasto"
    return escena.widget_orbitas(orbitas, intro, ajuste.get("orbita_lenta", 1), sol, reservadas(est), ancla)


def widgets_sistema(est: dict, intro: bool) -> list[dict]:
    """Sol, órbitas y un widget por planeta, todos anclados a sus objetos `solar:*`."""
    cfg = leer_json(MESA)
    ajuste = cfg.get("ajuste", {})
    a = est["asignacion"]
    cs = centros_actuales(est)
    dudas = {d["asignado"] for d in a.get("dudas", [])} - set(cfg.get("fijos", {}))
    sol = cs.get("sol")
    out = []
    if sol is not None:
        out.append(escena.widget_sol(est["cuerpos"][a["sol"]]["radio_mm"]))
    centro = sol or tuple(np.mean([cs[p] for p in a["planetas"]], axis=0))
    orden = sorted(a["planetas"], key=lambda p: math.dist(cs[p], centro))
    for i, pid in enumerate(orden):
        p = planeta_de(pid)
        x, y = cs[pid]
        r = est["cuerpos"][a["planetas"][pid]]["radio_mm"]
        hacia_sol = (centro[0] - x, centro[1] - y)
        retraso = (escena.REACCION[min(i, len(escena.REACCION) - 1)] + 0.5) if intro else 0.0
        out.append(escena.widget_planeta(p, r, hacia_sol, pid in dudas, retraso, ajuste))
    orb = widget_orbitas_de(est, intro)
    if orb:
        out.append(orb)
    return out


def objetos_mundo(est: dict) -> list[dict]:
    """Sol, planetas y lámina como objetos del mundo (`solar:sol`, `solar:jupiter`…, `solar:lamina-0`)."""
    a = est["asignacion"]
    cs = centros_actuales(est)

    def circulo(pid: str, label: str, i: int) -> dict:
        x, y = cs[pid]
        c = est["cuerpos"][i]
        r = c["radio_mm"]
        poly = [[round(x + r * math.cos(t), 1), round(y + r * math.sin(t), 1)] for t in np.linspace(0, 2 * math.pi, 24, endpoint=False)]
        return {"id": escena.oid(pid), "label": label, "polygon_mm": poly, "center_mm": [round(x, 1), round(y, 1)], "confidence": float(c.get("puntaje", 1))}

    out = []
    if a.get("sol") is not None:
        sol_obj = circulo("sol", "Sol (engranaje)", a["sol"])
        if est.get("sol_poligono_mm"):
            sol_obj["polygon_mm"] = est["sol_poligono_mm"]  # la silueta con dientes, no un círculo
        out.append(sol_obj)
    k = poligono_canasto(est)
    if k is not None:  # siempre, con id fijo: es un objeto real y ancla las órbitas cuando el Sol no se ve
        out.append({"id": escena.oid("canasto"), "label": "Canasto con los planetas", "polygon_mm": k[0], "center_mm": [round(k[1][0], 1), round(k[1][1], 1)], "confidence": 0.9})
    fant = est.get("fantasmas") or {}
    for pid, i in a["planetas"].items():
        out.append(circulo(pid, f"{planeta_de(pid)['nombre']} (bola de plumavit{'; no la veo ahora' if str(i) in fant else ''})", i))
    for n, l in enumerate(est.get("laminas", [])):
        out.append({"id": escena.oid(f"lamina-{n}"), "label": "Lámina impresa del sistema solar", "polygon_mm": [list(p) for p in l["esquinas_mm"]], "center_mm": list(l["centro_mm"])})
    return out


def publicar_objetos(mesa: Mesa, est: dict) -> None:
    """Reemplaza solo los objetos `solar:*`: los del motor (m01, m02…) y los de otras escenas se conservan.
    Con `PCX_SIN_OBJETOS=1` no publica nada: los `solar:*` los publica el motor (módulo Rust) y los widgets se
    anclan a esos."""
    if os.environ.get("PCX_SIN_OBJETOS") == "1":
        return
    try:
        otros = [o for o in mesa.llamar("scene", {}, 20)[0].get("objects", []) if not str(o.get("id", "")).startswith(escena.PREFIJO)]
    except Exception:  # noqa: BLE001
        otros = []
    keep = [{k: o[k] for k in ("id", "label", "polygon_mm", "center_mm", "bbox_mm", "confidence") if k in o} for o in otros]
    mesa.objetos(keep + objetos_mundo(est))


def proyectar_todo(mesa: Mesa, est: dict, intro: bool = False) -> None:
    actualizar_poligono_sol(est)
    publicar_objetos(mesa, est)  # primero los objetos: los widgets se anclan a ellos
    widgets = widgets_sistema(est, intro)
    if est.get("laminas"):
        lam = est["laminas"][0]
        widgets.append(escena.widget_lamina(lam, planetas_en_lamina(Lamina(**_lam(lam))), est.get("elegido")))
    if est["asignacion"].get("sol") is None:
        mesa.remove(["solar-sol"])  # sin Sol a la vista no queda un Sol viejo pegado donde estaba
    r = mesa.project(widgets)
    if r.get("reserved"):
        # Una bola física pegada a la barra puede asomar unos mm: la interfaz de la mesa queda encima.
        log("aviso:", r["reserved"])
    sol = centro_orbitas(est)
    if sol is not None:
        est["orbitas_en"] = [round(sol[0], 1), round(sol[1], 1)]
        guardar_estado(est)


def _lam(d: dict) -> dict:
    return {**d, "centro_mm": tuple(d["centro_mm"]), "tam_mm": tuple(d["tam_mm"]), "esquinas_mm": [tuple(p) for p in d["esquinas_mm"]]}


def resumen(est: dict) -> None:
    cat = {p["id"]: p for p in cargar_catalogo()["planetas"]}
    a = est["asignacion"]
    cs = est["cuerpos"]
    print(f"Sol: {'engranaje en ' + str(cs[a['sol']]['centro_mm']) if a.get('sol') is not None else 'no lo veo'}")
    for pid, i in sorted(a["planetas"].items(), key=lambda kv: -cs[kv[1]]["radio_mm"]):
        c = cs[i]
        print(f"  bola {i}: {cat[pid]['nombre']:8s} r={c['radio_mm']:5.1f} mm en ({c['centro_mm'][0]:.0f}, {c['centro_mm'][1]:.0f})")
    for d in a.get("dudas", []):
        print(f"  duda: bola {d['cuerpo']} quedó como {cat[d['asignado']]['nombre']}, podría ser {', '.join(cat[o]['nombre'] for o in d['opciones'])} ({d['motivo']})")
    if a.get("sobrantes"):
        print(f"  bolas sin planeta: {a['sobrantes']}")
    print(f"Láminas: {len(est.get('laminas', []))}  ·  pose del canasto: {est['pose']}")


# ---------------------------------------------------------------- acciones del tutor


def volar_lumo(mesa: Mesa, est: dict, pid: str, desde_mm=None) -> None:
    """Lumo vuela hasta la bola `pid` (desde `desde_mm`, o desde donde estaba) y queda posado, anclado a ella."""
    cs = centros_actuales(est)
    if pid not in cs:
        return
    r = radio_de(est, pid)
    off = lugar_lumo(est, pid)
    hasta = (cs[pid][0] + off[0], cs[pid][1] + off[1])
    lumo = est.get("lumo") or {}
    if lumo.get("en") == pid and desde_mm is None:
        return  # ya está ahí
    desde = desde_mm or lumo.get("mm") or cs.get("sol") or hasta
    if math.dist(desde, hasta) > 15:
        mesa.project([escena.widget_lumo_vuelo(tuple(desde), hasta, VUELO_S)])
        time.sleep(VUELO_S + 0.45)
    mesa.project([escena.widget_lumo(pid, r, hacia_afuera(est, pid), off)])
    est["lumo"] = {"en": pid, "mm": [round(hasta[0], 1), round(hasta[1], 1)]}
    guardar_estado(est)


VUELO_S = 1.4


def lugar_lumo(est: dict, pid: str) -> tuple[float, float]:
    """Dónde se posa Lumo junto a la bola `pid`: el primer lugar libre de otras bolas y del Sol, partiendo de costado
    respecto de la dirección hacia afuera (la burbuja sale derecho hacia afuera)."""
    cs = centros_actuales(est)
    a = est["asignacion"]
    radios = {"sol": est["cuerpos"][a["sol"]]["radio_mm"] * 1.3} if a.get("sol") is not None else {}
    radios.update({p: est["cuerpos"][i]["radio_mm"] for p, i in a["planetas"].items()})
    r = radios.get(pid, 20)
    ux, uy = hacia_afuera(est, pid)
    base = math.atan2(uy, ux) + math.pi / 2.6
    w, h = est.get("surface_mm", [1000, 563])
    zonas = reservadas(est)
    respaldo = None  # fuera de la interfaz aunque roce otra bola: mejor que quedar bajo la barra de pestañas
    for k in (0, -1, 1, -2, 2, -3, 3, 4, -4, 5, -5, 6):
        ang = base + k * math.pi / 6
        d = r + 24
        x, y = cs[pid][0] + math.cos(ang) * d, cs[pid][1] + math.sin(ang) * d
        libre = all(math.dist((x, y), cs[q]) > radios[q] + 20 for q in cs if q != pid)
        fuera_ui = not any(zx - 22 < x < zx + zw + 22 and zy - 22 < y < zy + zh + 22 for zx, zy, zw, zh in zonas)
        en_mesa = 20 < x < w - 20 and 24 < y < h - 24
        if libre and fuera_ui and en_mesa:
            return (round(math.cos(ang) * d, 1), round(math.sin(ang) * d, 1))
        if respaldo is None and fuera_ui and en_mesa:
            respaldo = (round(math.cos(ang) * d, 1), round(math.sin(ang) * d, 1))
    return respaldo or escena.posicion_lumo(r, (ux, uy))


def ficha(mesa: Mesa, pid: str, desde_mm=None, avanzar: bool = True) -> None:
    """Explicar: Lumo vuela a la bola, la señala y abre la ficha (tocar otra vez el mismo pasa al dato siguiente;
    con `avanzar=False` repite el mismo dato)."""
    est = leer_json(ESTADO)
    mesa.remove(["solar-burbuja"])  # tema nuevo: lo que se estaba diciendo de otro planeta se va
    if pid in centros_actuales(est):
        mesa.project([escena.widget_foco(pid, radio_mm=radio_de(est, pid))])
        volar_lumo(mesa, est, pid, desde_mm)
        est = leer_json(ESTADO)
    planeta = planeta_de(pid)
    cfg = leer_json(MESA)
    tam = tuple(cfg.get("ficha", {}).get("tam_mm") or escena.FICHA_MM)
    rect = cfg.get("ficha", {}).get("rect_mm") or est.get("ficha_rect")
    if not rect:
        rect = ubicar_ficha(est, tam)
        est["ficha_rect"] = rect
    datos = est.setdefault("dato", {})
    datos[pid] = datos.get(pid, -1) + 1 if est.get("elegido") == pid and avanzar else datos.get(pid, 0)
    est["elegido"] = pid
    guardar_estado(est)
    widgets = [escena.widget_ficha(planeta, rect, datos[pid])]
    if pid in centros_actuales(est):
        widgets.append(escena.widget_foco(pid, radio_mm=radio_de(est, pid)))
    if est.get("laminas"):
        lam = est["laminas"][0]
        widgets.append(escena.widget_lamina(lam, planetas_en_lamina(Lamina(**_lam(lam))), pid))
    mesa.project(widgets)


def ubicar_ficha(est: dict, tam: tuple[float, float]) -> list[float]:
    """Zona libre cerca de la lámina, sin pisar el canasto (ni sus órbitas más cercanas) ni la interfaz de la mesa.
    Si no hay un hueco perfecto, el menos ocupado."""
    img = cv2.imread(str(ESTADO.parent / "referencia.jpg"))
    w, h = est.get("surface_mm", [1000, 563])
    evitar = []
    cs = centros_actuales(est)
    if cs:
        xs = [p[0] for p in cs.values()]
        ys = [p[1] for p in cs.values()]
        evitar.append([min(xs) - 70, min(ys) - 70, max(xs) - min(xs) + 140, max(ys) - min(ys) + 140])
    for l in est.get("laminas", []):
        xs = [p[0] for p in l["esquinas_mm"]]
        ys = [p[1] for p in l["esquinas_mm"]]
        evitar.append([min(xs) - 8, min(ys) - 8, max(xs) - min(xs) + 16, max(ys) - min(ys) + 16])
    # Barra de pestañas (izquierda) y esquina de voz de la consola, con un margen.
    evitar += [[x - 10, y - 10, zw + 20, zh + 20] for x, y, zw, zh in reservadas(est)]
    cerca = tuple(est["laminas"][0]["centro_mm"]) if est.get("laminas") else (w / 2, h / 2)
    r = espacio_libre(img, (int(tam[0]), int(tam[1])), evitar, cerca, forzar=True) if img is not None else None
    return r or [w - tam[0] - 20, h / 2 - tam[1] / 2, tam[0], tam[1]]


def radio_de(est: dict, pid: str) -> float:
    a = est["asignacion"]
    i = a["sol"] if pid == "sol" else a["planetas"][pid]
    return est["cuerpos"][i]["radio_mm"]


def obstaculos_texto(est: dict, centros: dict) -> list[list[float]]:
    """Huellas de los cuerpos y láminas que deben quedar libres al colocar una pista."""
    cajas = []
    for pid, (x, y) in centros.items():
        r = radio_de(est, pid) + 8
        cajas.append([x - r, y - r, 2 * r, 2 * r])
    for lam in est.get("laminas", []):
        xs, ys = zip(*lam["esquinas_mm"])
        cajas.append([min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)])
    return cajas


def zonas_texto(est: dict) -> list[list[float]]:
    girada = bool(leer_json(MESA).get("interfaz", {}).get("girada", False))
    sup = tuple(est.get("surface_mm", (1000, 563)))
    return [*escena.zonas_reservadas(sup, girada), escena.zona_franja(sup, girada)]


def hacia_afuera(est: dict, pid: str) -> tuple[float, float]:
    """Dirección del Sol al planeta en la pose actual (para el Sol mismo: hacia abajo, lejos de las pestañas)."""
    cs = centros_actuales(est)
    if pid == "sol" or "sol" not in cs or pid not in cs:
        return (0.0, 1.0)
    return (cs[pid][0] - cs["sol"][0], cs[pid][1] - cs["sol"][1])


def corto(texto: str, max_letras: int = 66) -> str:
    """Versión proyectable de una respuesta: la primera frase, y si no cabe, cortada en una palabra."""
    frase = re.split(r"(?<=[.!?])\s+", texto.strip())[0]
    if len(frase) <= max_letras:
        return frase
    return frase[: frase.rfind(" ", 0, max_letras)].rstrip(",;:") + "…"


def decir(mesa: Mesa, pid: str, texto: str, texto_corto: str | None = None, con_voz: bool = True, habla_el_planeta: bool = False) -> None:
    """Hablar: Lumo vuela a la bola `pid` y dice `texto` en voz alta; junto a la bola se proyecta `texto_corto` (o la
    primera frase). Con `habla_el_planeta`, el que habla es el planeta, con su propia voz."""
    est = leer_json(ESTADO)
    mesa.project([escena.widget_foco(pid, radio_mm=radio_de(est, pid))])
    if not habla_el_planeta:
        volar_lumo(mesa, est, pid)
        est = leer_json(ESTADO)
    cat = cargar_catalogo()
    quien = planeta_de(pid) if habla_el_planeta else cat.get("guia", {"id": "lumo", "nombre": "Lumo", "voz": {}})
    r = radio_de(est, pid)
    cs = centros_actuales(est)
    seg = max(2.0, len(texto) / 13.5)
    sup = tuple(est.get("surface_mm", (1000, 563)))
    mesa.project([escena.widget_burbuja(quien, texto_corto or corto(texto), r, hacia_afuera(est, pid), cs.get(pid), sup, seg, pid, zonas_texto(est),
                                      objetos=obstaculos_texto(est, cs))])
    if con_voz:
        hablar(quien, texto)


def nombrar(mesa: Mesa, pids: list[str]) -> None:
    """Nombrar: etiquetas de hasta 3 planetas a la vez (las demás se quitan)."""
    est = leer_json(ESTADO)
    cfg = leer_json(MESA)
    dudas = {d["asignado"] for d in est["asignacion"].get("dudas", [])} - set(cfg.get("fijos", {}))
    pids = pids[:3]
    viejos = [f"solar-nombre-{p}" for p in list(est["asignacion"]["planetas"]) + ["sol"] if p not in pids]
    mesa.remove(viejos)
    cs = centros_actuales(est)
    sup = tuple(est.get("surface_mm", (1000, 563)))
    zonas = zonas_texto(est)
    objetos = obstaculos_texto(est, cs)
    mesa.project([escena.widget_nombre(planeta_de(p), radio_de(est, p), hacia_afuera(est, p), p in dudas, cs.get(p), sup, zonas, objetos=objetos) for p in pids])


def escuchar(mesa: Mesa, pid: str) -> None:
    est = leer_json(ESTADO)
    radio = radio_de(est, pid) if pid in centros_actuales(est) else 23
    mesa.project([escena.widget_foco(pid, escuchando=True, radio_mm=radio)])


def callar(mesa: Mesa) -> None:
    mesa.remove(["solar-burbuja", "solar-foco"])
    nombrar(mesa, [])


def pestana(mesa: Mesa, nombre: str) -> None:
    """Crea la pestaña `nombre` en la mesa (o la activa si ya existe): la escena no pisa las de otros."""
    st = mesa.llamar("scenes", {"action": "list"}, 15)[0]
    if nombre in [s.get("name") for s in st.get("scenes", [])]:
        mesa.llamar("scenes", {"action": "activate", "name": nombre}, 15)
    else:
        mesa.llamar("scenes", {"action": "create", "name": nombre}, 15)
    print("pestaña activa:", nombre)


def captura(mesa: Mesa, carpeta: Path) -> None:
    """Lo que ve la cámara (`*-camara.jpg`), la misma foto con lo proyectado dibujado encima (`*-proyectado.jpg`), la
    vista en mm si está calibrada (`*-mm.jpg`) y una línea en `log.txt`."""
    carpeta.mkdir(parents=True, exist_ok=True)
    t = time.strftime("%H%M%S")
    base = {"camera_id": CAMARA} if CAMARA else {}
    data, imgs = mesa.llamar("look", {**base, "annotate": False}, 40)
    calidad = [cv2.IMWRITE_JPEG_QUALITY, 75]
    if imgs:
        cv2.imwrite(str(carpeta / f"{t}-camara.jpg"), imgs[0], calidad)
        if data.get("calibrated"):
            mm = rectificar(imgs[0], data["matrix_px_to_mm"], data.get("surface_mm") or [1000, 563], data.get("lens"))
            cv2.imwrite(str(carpeta / f"{t}-mm.jpg"), mm, calidad)
    _, anotada = mesa.llamar("look", {**base, "annotate": True}, 40)
    if anotada:
        cv2.imwrite(str(carpeta / f"{t}-proyectado.jpg"), anotada[0], calidad)
    est = leer_json(ESTADO)
    a = est.get("asignacion") or {}
    linea = (
        f"{time.strftime('%H:%M:%S')} cámara={data.get('camera_id')} calibrada={data.get('calibrated')} "
        f"sol={'sí' if a.get('sol') is not None else 'no'} planetas={len(a.get('planetas') or {})} dudas={len(a.get('dudas') or [])} "
        f"pose={est.get('pose')} elegido={est.get('elegido')}"
    )
    with open(carpeta / "log.txt", "a", encoding="utf-8") as f:
        f.write(linea + "\n")
    print(linea)


# ---------------------------------------------------------------- pedidos de agentes (escenas/CONTRATO-ASISTENTE.md)

# Lo que un agente (la voz, Claude Code, Codex) le puede pedir a la escena con `scenes` action `emit`. La voz avanza
# con eventos propios: nunca con `voice_command` ni `widget_pressed`, que son solo de lo que pasa físicamente en la mesa.
PEDIDOS = ("preset_accion", "preset_ajustar", "paso_siguiente", "paso_anterior", "repetir")


def orden_planetas(est: dict) -> list[str]:
    """El Sol y los planetas que hay en la mesa, del más cercano al Sol al más lejano (orden real, no por tamaño)."""
    cat = {p["id"]: p for p in cargar_catalogo()["planetas"]}
    pids = sorted(est["asignacion"]["planetas"], key=lambda p: cat.get(p, {}).get("orden", 99))
    return (["sol"] if est["asignacion"].get("sol") is not None else []) + pids


def navegar(mesa: Mesa, paso: int) -> str | None:
    """Paso siguiente o anterior: la ficha del planeta que sigue (o el anterior) desde el Sol hacia afuera."""
    est = leer_json(ESTADO)
    orden = orden_planetas(est)
    if not orden:
        return None
    actual = est.get("elegido")
    pid = orden[(orden.index(actual) + paso) % len(orden)] if actual in orden else orden[0 if paso >= 0 else -1]
    ficha(mesa, pid)
    return pid


def repetir(mesa: Mesa) -> str | None:
    """Repite la ficha del planeta elegido, con el mismo dato (la voz repite lo que dijo)."""
    pid = leer_json(ESTADO).get("elegido")
    if pid:
        ficha(mesa, pid, avanzar=False)
    return pid


def _planeta_valido(est: dict, pid) -> str:
    if pid != "sol" and pid not in est["asignacion"]["planetas"]:
        raise ValueError(f"«{pid}» no está en la mesa; hay: {', '.join(orden_planetas(est))}")
    return pid


def hacer_accion(mesa: Mesa, accion: str, args: dict) -> dict:
    """Una acción de `preset.json` → `acciones`."""
    est = leer_json(ESTADO)
    if accion in ("ir_a", "ficha"):
        ficha(mesa, _planeta_valido(est, args.get("planeta")))
    elif accion == "decir":
        decir(mesa, _planeta_valido(est, args.get("planeta")), str(args["texto"]), args.get("corto"), bool(args.get("con_voz", False)), bool(args.get("habla_el_planeta", False)))
    elif accion == "nombrar":
        nombrar(mesa, [_planeta_valido(est, p) for p in (args.get("planetas") or [])])
    elif accion == "escuchar":
        escuchar(mesa, _planeta_valido(est, args.get("planeta")))
    elif accion == "callar":
        callar(mesa)
    elif accion == "fijar":
        fijar(mesa, str(args["planeta"]), int(args["bola"]))
    elif accion == "redibujar":
        proyectar_todo(mesa, est, intro=True)
    elif accion in ("siguiente", "anterior"):
        return {"planeta": navegar(mesa, 1 if accion == "siguiente" else -1)}
    elif accion == "repetir":
        return {"planeta": repetir(mesa)}
    else:
        raise ValueError(f"no conozco la acción «{accion}»")
    return {"accion": accion}


def ajustar_param(mesa: Mesa, nombre: str, valor) -> dict:
    """Un parámetro de `preset.json` → `params`, validado con su tipo y rango, y aplicado en vivo."""
    p = leer_json(AQUI / "preset.json").get("params", {}).get(nombre)
    if not p:
        raise ValueError(f"no conozco el parámetro «{nombre}»")
    tipo = p.get("tipo")
    if tipo == "numero":
        valor = min(max(float(valor), p.get("min", -math.inf)), p.get("max", math.inf))
    elif tipo == "punto_mm":
        valor = [float(v) for v in (json.loads(valor) if isinstance(valor, str) else valor)][:2]
    elif tipo == "bool":
        valor = valor.strip().lower() in ("1", "true", "si", "sí") if isinstance(valor, str) else bool(valor)
    elif tipo == "color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", str(valor)):
        raise ValueError(f"«{valor}» no es un color #rrggbb")
    destino, _, ruta = str(p.get("aplica", "")).partition(":")
    cfg = leer_json(MESA)
    if destino == "config":
        d = cfg
        *camino, ultimo = ruta.split(".")
        for k in camino:
            d = d.setdefault(k, {})
        d[ultimo] = valor
    elif destino == "css":
        cfg.setdefault("css", {})[ruta] = f"{valor:g}px" if p.get("unidad") == "mm" else (f"{valor:g}" if isinstance(valor, float) else str(valor))
        escena.CSS_VIVO.clear()
        escena.CSS_VIVO.update(cfg["css"])
    else:
        raise ValueError(f"«{nombre}» no dice dónde se aplica")
    MESA.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    est = leer_json(ESTADO)
    if destino == "config" and ruta == "ajuste.desplazamiento_mm":
        publicar_objetos(mesa, est)  # basta con mover los objetos
    elif est:
        proyectar_todo(mesa, est, intro=False)
    return {"param": nombre, "valor": valor}


def atender(mesa: Mesa, evento: str, data: dict) -> None:
    """Hace un pedido de un agente y contesta `preset_hecho {id, ok, resultado | error}`."""
    if data.get("preset") not in (None, "solar"):
        return  # es para otra escena
    try:
        if evento == "preset_accion":
            res = hacer_accion(mesa, str(data.get("accion")), data.get("args") or {})
        elif evento == "preset_ajustar":
            res = ajustar_param(mesa, str(data.get("param")), data.get("valor"))
        elif evento in ("paso_siguiente", "paso_anterior"):
            res = {"planeta": navegar(mesa, 1 if evento == "paso_siguiente" else -1)}
        else:
            res = {"planeta": repetir(mesa)}
        ok, extra = True, {"resultado": res}
    except Exception as ex:  # noqa: BLE001
        ok, extra = False, {"error": str(ex)[:300]}
    log(f"pedido {evento}: {'ok' if ok else extra['error']}")
    mesa.emitir("preset_hecho", {"preset": "solar", "id": data.get("id"), "pedido": evento, "ok": ok, **extra})


_voces: set[str] | None = None


def hablar(planeta: dict, texto: str) -> None:
    """Voz del personaje en el computador que corre el conductor (macOS `say`). No bloquea."""
    global _voces
    cfg = leer_json(MESA).get("voz", {})
    if cfg.get("motor", "say") != "say" or sys.platform != "darwin":
        return
    if _voces is None:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
        _voces = {re.split(r"\s{2,}", ln.strip())[0] for ln in out.splitlines() if ln.strip()}
    v = planeta.get("voz", {})
    voz = v.get("mac") if v.get("mac") in _voces else cfg.get("respaldo", "Paulina")
    subprocess.Popen(["say", "-v", voz, "-r", str(v.get("velocidad", 175)), texto])


def fijar(mesa: Mesa, pid: str, bola: int) -> None:
    """La niña (o quien sea) decide qué planeta es una bola: queda en mesa.json y se vuelve a dibujar."""
    est = leer_json(ESTADO)
    c = est["cuerpos"][bola]
    cfg = leer_json(MESA)
    cfg.setdefault("fijos", {})[pid] = [round(c["centro_mm"][0], 1), round(c["centro_mm"][1], 1)]
    MESA.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cuerpos = cuerpos_de(est)
    est["asignacion"] = asignar_planetas(cuerpos, cargar_catalogo(), fijos_a_indices(cfg["fijos"], cuerpos)).dict()
    guardar_estado(est)
    mesa.remove([f"solar-planeta-{p['id']}" for p in cargar_catalogo()["planetas"]])
    proyectar_todo(mesa, est, intro=False)
    resumen(est)


def ajustar(mesa: Mesa, dx: float, dy: float) -> None:
    """Corre toda la luz unos mm (si cae corrida sobre las bolas). Basta con mover los objetos."""
    cfg = leer_json(MESA)
    a = cfg.setdefault("ajuste", {})
    d = a.get("desplazamiento_mm") or [0, 0]
    a["desplazamiento_mm"] = [round(d[0] + dx, 1), round(d[1] + dy, 1)]
    MESA.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    publicar_objetos(mesa, leer_json(ESTADO))
    print("desplazamiento:", a["desplazamiento_mm"])



# ---------------------------------------------------------------- seguir


def zona_actual(est: dict) -> list[float]:
    """Zona del canasto movida con la pose actual (caja que la contiene, con margen)."""
    if not est.get("canasto"):
        return [0, 0, *est.get("surface_mm", [1000, 563])]
    pts = aplicar_pose(est["canasto"]["poligono_mm"], pose_de(est))
    x0, y0 = pts.min(axis=0) - 60
    x1, y1 = pts.max(axis=0) + 60
    return [float(x0), float(y0), float(x1 - x0), float(y1 - y0)]


def mascara_animada(est: dict, forma: tuple[int, int]) -> np.ndarray:
    """Discos de los planetas y del Sol en la pose actual: ahí la proyección se anima, no cuenta como movimiento."""
    m = np.zeros(forma, np.uint8)
    pts = aplicar_pose([c["centro_mm"] for c in est["cuerpos"]], pose_de(est))
    for (x, y), c in zip(pts, est["cuerpos"]):
        cv2.circle(m, (int(x), int(y)), int(c["radio_mm"] * (1.35 if c["tipo"] == "engranaje" else 1.15)), 255, -1)
    # Las órbitas (trazos que fluyen y el punto que las recorre) y Lumo también se animan.
    cs = centros_actuales(est)
    sol = cs.get("sol")
    if sol is not None:
        for pid, c in cs.items():
            if pid != "sol":
                cv2.circle(m, (int(sol[0]), int(sol[1])), int(math.dist(c, sol)), 255, 16)
    lumo = (est.get("lumo") or {}).get("mm")
    if lumo:
        cv2.circle(m, (int(lumo[0]), int(lumo[1])), 30, 255, -1)
    return m


def reubicar(mesa: Mesa, est: dict, img: np.ndarray, minimo: int = 4, final: bool = False) -> bool:
    """Detecta en `img`, estima la pose respecto de la referencia y, si cambió, mueve los objetos `solar:*`
    (los widgets anclados los siguen solos). Con `final` (el canasto ya quedó quieto) vuelve a proyectar las órbitas,
    para que su máscara de la interfaz de la mesa calce con la nueva posición del Sol."""
    movido = _reubicar(mesa, est, img, minimo, limpia=final)
    if final:
        refrescar_orbitas(mesa)
    return movido


def refrescar_orbitas(mesa: Mesa) -> None:
    """Vuelve a proyectar las órbitas si su centro se corrió (la máscara de la interfaz tiene que calzar)."""
    est2 = leer_json(ESTADO)
    sol = centro_orbitas(est2)
    if sol is not None and math.dist(sol, est2.get("orbitas_en") or sol) > 4:
        orb = widget_orbitas_de(est2, intro=False)
        if orb:
            mesa.project([orb])
        est2["orbitas_en"] = [round(sol[0], 1), round(sol[1], 1)]
        guardar_estado(est2)


def mascara_canasto(est: dict, forma: tuple[int, int], margen_mm: int = 15) -> np.ndarray:
    """El canasto en la pose actual (con un margen chico): el único lugar donde un cambio cuenta como movimiento.
    La gente que pasa alrededor no dispara nada."""
    m = np.zeros(forma, np.uint8)
    if not est.get("canasto"):
        m[:] = 255
        return m
    pts = aplicar_pose(est["canasto"]["poligono_mm"], pose_de(est))
    cv2.fillPoly(m, [np.int32(np.round(pts))], 255)
    return cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margen_mm + 1, 2 * margen_mm + 1)))


def manos_cerca(mesa: Mesa, est: dict, margen_mm: float = 150, reciente_s: float = 1.5) -> bool:
    """¿Hay una mano cerca del canasto? Usa las manos del hand tracking de SAT (`scene` con include_hands, 2 Hz).
    Si la consola no las entrega, no frena nada."""
    try:
        data, _ = mesa.llamar("scene", {"include_hands": True}, 10)
    except Exception:  # noqa: BLE001
        return False
    manos = data.get("hands") or []
    if not manos:
        return False
    x, y, w, h = zona_actual(est)
    ahora = time.time_ns()
    for mano in manos:
        t = mano.get("t_capture_ns")
        if t and abs(ahora - t) < 60e9 and ahora - t > reciente_s * 1e9:
            continue  # vieja (con relojes razonables; si el reloj no cuadra, se la toma como presente)
        tip = mano.get("tip_mm")
        if tip is None or (x - margen_mm <= tip[0] <= x + w + margen_mm and y - margen_mm <= tip[1] <= y + h + margen_mm):
            return True
    return False


def bolas_una_por_una(ref: list[Cuerpo], act: list[Cuerpo], p: Pose) -> dict[str, list[float]]:
    """Dónde quedó cada bola, por separado: la pose del canasto predice dónde debería estar y se toma la detección
    compatible más cercana (hasta 30 mm). Si una bola se sacó del canasto, se busca entre las que sobran (hasta
    150 mm). Las que no se ven quedan fuera del dict (se usa la pose)."""
    pred = aplicar_pose([c.centro_mm for c in ref], p)
    usadas: set[int] = set()
    pos: dict[str, list[float]] = {}

    def compatible(a: Cuerpo, b: Cuerpo) -> bool:
        return a.tipo == b.tipo and abs(a.radio_mm - b.radio_mm) <= 0.3 * max(a.radio_mm, b.radio_mm) + 3

    for tope in (30.0, 150.0):
        for i, c in enumerate(ref):
            if str(i) in pos:
                continue
            cands = [(math.dist(pred[i], b.centro_mm), j) for j, b in enumerate(act) if j not in usadas and compatible(c, b)]
            cands = [x for x in cands if x[0] <= tope]
            if cands:
                _, j = min(cands)
                usadas.add(j)
                pos[str(i)] = [round(act[j].centro_mm[0], 1), round(act[j].centro_mm[1], 1)]
    return pos


def _reubicar(mesa: Mesa, est: dict, img: np.ndarray, minimo: int = 4, limpia: bool = False) -> bool:
    """Pose del canasto entero y posición de cada bola. Con foto limpia, si el canasto se corrió lejos de donde se lo
    esperaba, lo busca en toda la mesa visible, y vuelve a ubicar la lámina."""
    k = ubicar_canasto(img)
    zona = k.zona() if k else zona_actual(est)
    act = detectar_cuerpos(img, zona)
    ref = cuerpos_de(est)
    previa = pose_de(est)
    p = pose_grupo(ref, act, previa=previa)
    if (not p.ok or p.inliers < min(minimo, len(ref))) and limpia:
        act = detectar_cuerpos(img, None)  # se corrió lejos: toda la mesa visible (solo en foto limpia)
        p = pose_grupo(ref, act, previa=previa)
    if limpia:
        laminas = ubicar_laminas(img)
        avisar(est, avisos_de_cobertura(img, k, laminas))
        if laminas:
            nueva, vieja = laminas[0], (est.get("laminas") or [None])[0]
            if not vieja or math.dist(nueva.centro_mm, vieja["centro_mm"]) > 5 or abs(((nueva.angulo_deg - vieja["angulo_deg"]) + 180) % 360 - 180) > 3:
                est["laminas"] = [nueva.dict()]
                guardar_estado(est)
                publicar_objetos(mesa, est)
                mesa.project([escena.widget_lamina(est["laminas"][0], planetas_en_lamina(nueva), est.get("elegido"))])
                log(f"lámina: centro {tuple(round(v) for v in nueva.centro_mm)} mm, {nueva.angulo_deg:+.0f}°")
        guardar_estado(est)
    if not p.ok or p.inliers < min(minimo, len(ref)):
        return False
    pos = bolas_una_por_una(ref, act, p)
    fant = est.get("fantasmas") or {}
    for i in [i for i in fant if i in pos]:
        del fant[i]  # volvió a aparecer: deja de ser fantasma (y su etiqueta vuelve a la normal)
    vieja_pos = est.get("pos") or {}
    mueve_bola = any(math.dist(v, vieja_pos.get(i, v)) > 3 for i, v in pos.items()) or set(pos) != set(vieja_pos)
    cambio = abs(p.theta_deg - previa.theta_deg) > 0.7 or math.hypot(p.t_mm[0] - previa.t_mm[0], p.t_mm[1] - previa.t_mm[1]) > 2.5
    if cambio or mueve_bola:
        est["pose"] = {"theta_deg": p.theta_deg, "t_mm": list(p.t_mm)}
        est["pos"] = pos
        actualizar_poligono_sol(est)
        guardar_estado(est)
        publicar_objetos(mesa, est)
        log(f"canasto:{p.theta_deg:+.1f}°, ({p.t_mm[0]:+.0f}, {p.t_mm[1]:+.0f}) mm · {p.inliers}/{p.total} bolas · error {p.error_mm:.1f} mm · {len(pos)} vistas una por una")
    return True


def escuchar_eventos(mesa: Mesa, parar: threading.Event) -> None:
    """Toques en la lámina → ficha; voz → el planeta elegido escucha (el agente tutor contesta)."""
    cursor = 0
    try:
        cursor = int(mesa.eventos(0, ["widget_pressed"], 0).get("cursor", 0))
    except Exception as e:  # noqa: BLE001
        log("events_wait:", e)
    while not parar.is_set():
        try:
            r = mesa.eventos(cursor, ["widget_pressed", "flow_emitted"], 20)
        except Exception as e:  # noqa: BLE001
            log("events_wait:", e)
            time.sleep(2)
            continue
        try:
            cursor = int(r.get("cursor", cursor))
        except (TypeError, ValueError) as ex:
            log("events_wait: cursor raro:", ex)
        for ev in r.get("events", []):
            # un evento que falla (la mesa no contesta un project, datos raros) se registra y el hilo sigue: si
            # muriera, la lámina, la voz y los pedidos preset_* dejarían de andar sin que nadie lo note
            try:
                e = ev.get("payload") if isinstance(ev.get("payload"), dict) else ev  # el gateway envuelve en payload
                if e.get("type") == "widget_pressed" and e.get("widget_id") == "solar-lamina":
                    pid = e.get("value")
                    log("lámina: tocaron", pid)
                    ficha(mesa, pid, e.get("point_mm"))
                elif e.get("type") == "flow_emitted" and e.get("event") == "voice_command":
                    est = leer_json(ESTADO)
                    log("voz:", (e.get("data") or {}).get("text"))
                    if est.get("elegido"):
                        escuchar(mesa, est["elegido"])
                elif e.get("type") == "flow_emitted" and e.get("event") in PEDIDOS:
                    atender(mesa, e["event"], e.get("data") or {})
            except Exception as ex:  # noqa: BLE001
                log("evento:", ex)


def seguir(mesa: Mesa, sim: bool) -> None:
    est = leer_json(ESTADO)
    if not est:
        sys.exit("Primero: conductor.py armar")
    cfg = leer_json(MESA).get("seguir", {})
    hz, umbral, quieto_n = cfg.get("hz", 3), cfg.get("umbral_mov", 0.045), cfg.get("quieto_cuadros", 4)
    revisar = cfg.get("revisar_cada_s", 0)  # 0 = nunca apagar la proyección solo para revisar
    limpia_min_s, margen_manos = cfg.get("foto_limpia_min_s", 10), cfg.get("manos_margen_mm", 150)
    parar = threading.Event()
    threading.Thread(target=escuchar_eventos, args=(mesa, parar), daemon=True).start()
    log(f"siguiendo el canasto a {hz} fotos/s con cuadros en vivo; foto limpia solo si en vivo no lo reconozco, sin manos cerca y cada {limpia_min_s} s como mucho (Ctrl-C para parar)")
    prev, moviendo, quietos, ultima_revision, errores = None, False, 0, time.time(), 0
    falta_limpia, ultima_limpia = False, 0.0
    try:
        while True:
            t0 = time.time()
            try:
                est = leer_json(ESTADO)
                img, data = foto_mm(mesa, False, sim)
                sup = [round(float(v)) for v in (data.get("surface_mm") or [1000, 563])]
                antes = [round(float(v)) for v in (est.get("surface_mm") or sup)]
                if sup != antes:
                    # Proyector fuera de pantalla completa o recalibraron: la geometría de armar ya no vale.
                    log(f"AVISO: la superficie cambió de {antes[0]}×{antes[1]} a {sup[0]}×{sup[1]} mm (¿proyector fuera de pantalla completa o recalibraron?): vuelvo a armar")
                    armar(mesa, sim, intro=False)
                    prev, moviendo, ultima_revision = None, False, time.time()
                    continue
                if prev is not None and prev.shape != img.shape:
                    prev = None
                zona = zona_actual(est)
                if prev is not None:
                    # Solo cuenta lo que cambia sobre el canasto, fuera de lo que la proyección anima.
                    fuera = (mascara_animada(est, img.shape[:2]) > 0) | (mascara_canasto(est, img.shape[:2]) == 0)
                    a, b = prev.copy(), img.copy()
                    a[fuera] = 0
                    b[fuera] = 0
                    mov = movimiento(a, b, zona)
                    if mov > umbral:
                        if not moviendo:
                            log(f"se mueve ({mov:.2f})")
                        moviendo, quietos = True, 0
                        # En vivo: si la foto con proyección igual deja ver las bolas, se sigue durante el movimiento.
                        reubicar(mesa, est, img, minimo=5)
                    elif moviendo:
                        quietos += 1
                        if quietos >= quieto_n:
                            # Quieto: primero en vivo, sin apagar nada. La foto limpia queda para cuando en vivo no alcanza.
                            if reubicar(mesa, est, img, minimo=4):
                                refrescar_orbitas(mesa)
                            else:
                                falta_limpia = True
                                log("quieto, pero en vivo no reconozco el canasto: foto limpia apenas no haya manos cerca")
                            moviendo = False
                    elif revisar and time.time() - ultima_revision > revisar:
                        falta_limpia = True
                        ultima_revision = time.time()
                    if falta_limpia and not moviendo and time.time() - ultima_limpia >= limpia_min_s and not manos_cerca(mesa, est, margen_manos):
                        limpia, _ = foto_mm(mesa, True, sim)
                        if not reubicar(mesa, est, limpia, final=True):
                            log("no reconozco el canasto en la foto limpia; sigo con la pose anterior (mira los AVISO)")
                        falta_limpia, ultima_limpia, ultima_revision = False, time.time(), time.time()
                        img = limpia
                prev = img
                errores = 0
            except Exception as e:  # noqa: BLE001 — un ciclo malo no tumba el seguimiento
                errores += 1
                log(f"error en el ciclo ({type(e).__name__}: {str(e)[:200]}); sigo")
                prev, moviendo = None, False
                time.sleep(min(5, errores))
            time.sleep(max(0.0, 1 / hz - (time.time() - t0)))
    except KeyboardInterrupt:
        parar.set()
        log("listo")


# ---------------------------------------------------------------- sim


def mover_canasto_en_foto(img: np.ndarray, theta: float, t: tuple[float, float]) -> np.ndarray:
    """Simula que alguien mueve solo el canasto (con sus bolas y el Sol): lo recorta, lo gira `theta` grados en torno
    a su centro, lo traslada `t` mm y tapa el hueco con el color de la mesa. La lámina y lo demás no se mueven."""
    k = ubicar_canasto(img)
    if k is None:
        return img
    mascara = np.zeros(img.shape[:2], np.uint8)
    cv2.fillPoly(mascara, [np.array(k.poligono_mm, np.int32)], 255)
    for c in detectar_cuerpos(img, k.zona()):
        cv2.circle(mascara, (int(c.centro_mm[0]), int(c.centro_mm[1])), int(c.radio_mm * 1.25) + 3, 255, -1)
    mascara = cv2.dilate(mascara, np.ones((9, 9), np.uint8))
    mesa_color = np.median(img[mascara == 0].reshape(-1, 3), axis=0)
    base = img.copy()
    base[mascara > 0] = mesa_color
    base = np.where(mascara[..., None] > 0, cv2.GaussianBlur(base, (31, 31), 0), base)
    m = cv2.getRotationMatrix2D(k.centro_mm, -theta, 1.0)
    m[:, 2] += t
    tam = (img.shape[1], img.shape[0])
    movida = cv2.warpAffine(img, m, tam)
    m_mov = cv2.warpAffine(mascara, m, tam)
    return np.where(m_mov[..., None] > 127, movida, base)


def poner_fondo_sim(mesa: Mesa, theta: float, t: tuple[float, float]) -> None:
    """Proyecta la foto real de la mesa como widget image (la cámara simulada dibuja los image, no los html, así que
    «ve» el canasto real). Con `theta`/`t` mueve solo el canasto, como si la niña lo corriera."""
    img = cv2.imread(str(FOTO_SIM))
    if theta or t[0] or t[1]:
        img = mover_canasto_en_foto(img, theta, t)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    src = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
    h, w = img.shape[:2]
    mesa.project([{"type": "image", "id": "solar-fondo-sim", "src": src, "rect_mm": [0, 0, w, h], "z": -10}])
    # La cámara simulada carga la imagen recién cuando dibuja el primer cuadro: uno de calentamiento.
    mesa.llamar("look", {"annotate": False}, 30)
    time.sleep(0.4)


# ---------------------------------------------------------------- CLI


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim", action="store_true", help="consola con ?sim=1: fondo con la foto real")
    ap.add_argument("--url", default=os.environ.get("PCX_MCP_URL", "http://localhost:9321/mcp"))
    ap.add_argument("--camara", default=os.environ.get("PCX_CAMARA"), help="id de la cámara cenital calibrada (p. ej. cam-2)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("armar")
    sub.add_parser("seguir")
    sub.add_parser("estado")
    sub.add_parser("limpiar")
    sub.add_parser("redibujar")
    s = sub.add_parser("ficha")
    s.add_argument("planeta")
    s = sub.add_parser("decir")
    s.add_argument("planeta")
    s.add_argument("texto", help="lo que dice en voz alta")
    s.add_argument("--corto", help="lo que se proyecta (hasta ~60 letras); si falta, la primera frase")
    s.add_argument("--sin-voz", action="store_true")
    s.add_argument("--habla-el-planeta", action="store_true", help="habla el planeta con su voz, no Lumo")
    s = sub.add_parser("nombrar")
    s.add_argument("planetas", nargs="*", help="hasta 3; sin nada, quita los nombres")
    s = sub.add_parser("escuchar")
    s.add_argument("planeta")
    s = sub.add_parser("callar")
    s = sub.add_parser("fijar")
    s.add_argument("planeta")
    s.add_argument("bola", type=int)
    s = sub.add_parser("ajustar")
    s.add_argument("dx", type=float)
    s.add_argument("dy", type=float)
    s = sub.add_parser("sim-mover")
    s.add_argument("grados", type=float)
    s.add_argument("dx", type=float)
    s.add_argument("dy", type=float)
    s = sub.add_parser("pestana", help="crea o activa la pestaña de la escena en la mesa")
    s.add_argument("nombre", nargs="?", default="Sistema solar")
    s = sub.add_parser("captura", help="guarda lo que ve la cámara, lo proyectado y una línea de log")
    s.add_argument("carpeta", nargs="?", default="prueba-vivo")
    sub.add_parser("siguiente", help="ficha del planeta que sigue desde el Sol")
    sub.add_parser("anterior")
    sub.add_parser("repetir", help="la misma ficha, el mismo dato")
    s = sub.add_parser("param", help="cambia un parámetro de preset.json en vivo")
    s.add_argument("nombre")
    s.add_argument("valor")
    s = sub.add_parser("pedir", help="emite un pedido por la mesa, como lo haría un agente (lo atiende `seguir`)")
    s.add_argument("evento", choices=PEDIDOS)
    s.add_argument("data", nargs="?", default="{}", help='JSON, p. ej. \'{"accion": "ir_a", "args": {"planeta": "saturno"}}\'')
    a = ap.parse_args()
    global CAMARA
    CAMARA = a.camara or None
    escena.CSS_VIVO.update(leer_json(MESA).get("css", {}))

    if a.cmd == "estado":
        est = leer_json(ESTADO)
        if not est:
            sys.exit("Sin estado: corre armar")
        resumen(est)
        return
    token = os.environ.get("PCX_USER_TOKEN")
    if not token:
        sys.exit("Falta PCX_USER_TOKEN (botón «Copiar» de la consola)")
    mesa = Mesa(a.url, token)
    est = leer_json(ESTADO)
    sim = a.sim or bool(est.get("sim"))
    if a.cmd == "armar":
        armar(mesa, sim)
    elif a.cmd == "redibujar":
        escena.recargar()
        proyectar_todo(mesa, est, intro=True)
    elif a.cmd == "seguir":
        seguir(mesa, sim)
    elif a.cmd == "ficha":
        ficha(mesa, a.planeta)
    elif a.cmd == "decir":
        decir(mesa, a.planeta, a.texto, a.corto, not a.sin_voz, a.habla_el_planeta)
    elif a.cmd == "nombrar":
        nombrar(mesa, a.planetas)
    elif a.cmd == "escuchar":
        escuchar(mesa, a.planeta)
    elif a.cmd == "callar":
        callar(mesa)
    elif a.cmd in ("siguiente", "anterior"):
        print(navegar(mesa, 1 if a.cmd == "siguiente" else -1))
    elif a.cmd == "repetir":
        print(repetir(mesa))
    elif a.cmd == "param":
        print(ajustar_param(mesa, a.nombre, a.valor))
    elif a.cmd == "pestana":
        pestana(mesa, a.nombre)
    elif a.cmd == "captura":
        captura(mesa, Path(a.carpeta))
    elif a.cmd == "pedir":
        print("salió" if mesa.emitir(a.evento, json.loads(a.data)) else "no salió")
    elif a.cmd == "fijar":
        fijar(mesa, a.planeta, a.bola)
    elif a.cmd == "ajustar":
        ajustar(mesa, a.dx, a.dy)
    elif a.cmd == "sim-mover":
        poner_fondo_sim(mesa, a.grados, (a.dx, a.dy))
    elif a.cmd == "limpiar":
        ids = [w if isinstance(w, str) else w.get("id") for w in mesa.llamar("scene", {}, 20)[0].get("widgets", [])]
        mesa.remove([i for i in ids if str(i).startswith("solar-")])
        est["asignacion"] = {"planetas": {}, "sol": None}
        est["laminas"] = []
        publicar_objetos(mesa, est)


if __name__ == "__main__":
    main()

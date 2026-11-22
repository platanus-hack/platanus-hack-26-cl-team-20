"""Client tools de la voz según escenas/CONTRATO-ASISTENTE.md: las mismas para todo preset.

El agente no sabe de escenas: lee el manifiesto del preset activo (`preset_leer`) y usa siempre las mismas tools.
Los verbos comunes (MOTOR.md) se resuelven aquí con `project`/`remove` sobre cualquier objeto del catálogo, con el
tema del preset; los pedidos al conductor del preset (`preset_accion`, `preset_ajustar`) viajan como `flow_emitted`
por `run_flow` con una acción `emit`, y el conductor contesta `preset_hecho`.

Todo lo que proyecta la voz lleva el prefijo de id `voz-` (o el id que la escena le reservó, como `lab-nota`), así nunca
pisa los widgets de un conductor. Cada herramienta devuelve texto corto en español: es lo que el LLM lee.
"""

from __future__ import annotations

import asyncio
import html
import itertools
import json
import math
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .escenas import Escena, css
from .mesa import ErrorMesa, Mesa
from .presets import preset_de, resumen_preset

PREFIJO = "voz-"
TONOS = {"ok": ("#F4F4F2", "Bien"), "alerta": ("#D2D2CE", "Ojo"), "peligro": ("#FFFFFF", "Cuidado")}
MESA_ANCHO, BARRA_MM, ESQUINA_MM, MARGEN_MM = 1000, 150, 130, 10   # barra de pestañas a la izquierda, botón de voz arriba a la derecha
_flujos = itertools.count(1)


def S(desc: str, enum: list[str] | None = None) -> dict:
    d = {"type": "string", "description": desc}
    if enum:
        d["enum"] = list(enum)
    return d


@dataclass
class Herramienta:
    nombre: str
    descripcion: str
    fn: Callable[["Contexto", dict], Awaitable[str]]
    props: dict = field(default_factory=dict)
    requeridos: tuple[str, ...] = ()
    timeout: int = 20

    def schema(self) -> dict:
        return {"type": "object", "required": list(self.requeridos), "properties": self.props}


@dataclass
class Contexto:
    mesa: Mesa
    escena: Escena
    eventos: list[dict] = field(default_factory=list)   # los últimos eventos que vio el servidor (más nuevo al final)
    respuestas: dict[str, dict] = field(default_factory=dict)   # preset_hecho por id
    _mundo: dict | None = None

    async def mundo(self) -> dict:
        self._mundo = await self.mesa.tool("scene", {})
        return self._mundo


# ------------------------------------------------------------------------------------------------ utilidades


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def objetos(mundo: dict) -> list[dict]:
    objs = mundo.get("objects")
    if objs is None and isinstance(mundo.get("world"), dict):
        objs = mundo["world"].get("objects")
    if isinstance(objs, dict):
        objs = [{"id": k, **v} if isinstance(v, dict) else {"id": k} for k, v in objs.items()]
    return [o for o in (objs or []) if isinstance(o, dict) and o.get("id")]


def widgets(mundo: dict) -> list[dict]:
    ws = mundo.get("widgets")
    if ws is None and isinstance(mundo.get("world"), dict):
        ws = mundo["world"].get("widgets")
    return [w for w in (ws or []) if isinstance(w, dict) and w.get("id")]


def alto_mesa(mundo: dict) -> float:
    s = mundo.get("surface") or (mundo.get("world") or {}).get("surface") or {}
    return float(s.get("height_mm") or 563)


def resolver(objs: list[dict], nombre: str, prefijo: str = "") -> dict | None:
    """Un objeto por id o por nombre como lo diría una persona («la Tierra», «el vaso», «saturno»)."""
    if not nombre:
        return None
    n = _norm(nombre)
    n_sin_art = re.sub(r"^(el|la|los|las|un|una|al|del) ", "", n)
    by_id = {o["id"]: o for o in objs}
    if nombre in by_id:
        return by_id[nombre]
    if prefijo and prefijo + nombre in by_id:
        return by_id[prefijo + nombre]
    for cand in (n, n_sin_art):
        for o in objs:
            if cand and cand in (_norm(o["id"]), _norm(o.get("label", "")), _norm(o["id"].split(":")[-1])):
                return o
    for o in objs:
        if n_sin_art and n_sin_art in _norm(o.get("label", "")) + " " + _norm(o["id"]):
            return o
    return None


def centro(o: dict) -> tuple[float, float] | None:
    c = o.get("center_mm")
    if isinstance(c, (list, tuple)) and len(c) == 2:
        return float(c[0]), float(c[1])
    pol = o.get("polygon_mm") or []
    if pol:
        return sum(p[0] for p in pol) / len(pol), sum(p[1] for p in pol) / len(pol)
    return None


def radio(o: dict) -> float:
    if o.get("radius_mm"):
        return float(o["radius_mm"])
    c, pol = centro(o), o.get("polygon_mm") or []
    if c and pol:
        return max(math.dist(c, p) for p in pol)
    return 30.0


def junto_a(o: dict, ancho: float, alto: float, alto_mesa_mm: float) -> list[int]:
    """Un rectángulo libre al lado de un objeto (derecha, izquierda, abajo, arriba), dentro de la mesa y sin pisar la
    barra de pestañas (x < 150 mm) ni la esquina del botón de voz."""
    cx, cy = centro(o) or (MESA_ANCHO / 2, alto_mesa_mm / 2)
    r, aire = radio(o), 24
    candidatos = [(cx + r + aire, cy - alto / 2), (cx - r - aire - ancho, cy - alto / 2),
                  (cx - ancho / 2, cy + r + aire), (cx - ancho / 2, cy - r - aire - alto)]

    def dentro(x, y):
        return BARRA_MM + MARGEN_MM <= x and x + ancho <= MESA_ANCHO - MARGEN_MM and MARGEN_MM <= y \
            and y + alto <= alto_mesa_mm - MARGEN_MM \
            and not (x + ancho > MESA_ANCHO - ESQUINA_MM and y < ESQUINA_MM)

    for x, y in candidatos:
        if dentro(x, y):
            return [round(x), round(y), round(ancho), round(alto)]
    x = min(max(cx + r + aire, BARRA_MM + MARGEN_MM), MESA_ANCHO - ancho - MARGEN_MM)
    y = min(max(cy - alto / 2, ESQUINA_MM), alto_mesa_mm - alto)
    return [round(x), round(y), round(ancho), round(alto)]


def texto_widget(w: dict) -> str:
    """Lo que dice un widget, en texto plano (para que la voz sepa qué muestra la escena)."""
    partes = [w.get(k) for k in ("text", "title", "body", "label") if isinstance(w.get(k), str)]
    p = w.get("params") if isinstance(w.get("params"), dict) else {}
    if p.get("html"):
        h = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", p["html"], flags=re.S | re.I)
        partes.append(html.unescape(re.sub(r"<[^>]+>", " ", h)))
    return re.sub(r"\s+", " ", " · ".join(x for x in partes if x)).strip()[:180]


def _propio(e: Escena, wid: str) -> bool:
    return wid.startswith(PREFIJO) or wid == e.panel_id


def _esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def _lista_nombres(objs: list[dict]) -> str:
    if not objs:
        return "la mesa no tiene objetos reconocidos todavía"
    return ", ".join(f"{o.get('label') or o['id']} ({o['id']})" for o in objs[:14])


async def _project(ctx: Contexto, ws: list[dict]) -> dict:
    return await ctx.mesa.tool("project", {"widgets": ws})


def _html(ctx: Contexto, cuerpo: str) -> dict:
    return {"html": cuerpo, "css": css(ctx.escena)}


async def _objeto(ctx: Contexto, nombre: str) -> tuple[dict | None, list[dict], dict]:
    m = await ctx.mundo()
    objs = objetos(m)
    return resolver(objs, nombre, ctx.escena.prefijo), objs, m


# ------------------------------------------------------------------------------------------------ mesa


async def mesa_objetos(ctx: Contexto, a: dict) -> str:
    m = await ctx.mundo()
    objs = objetos(m)
    pref = str(a.get("prefijo") or "").strip()
    if pref:
        objs = [o for o in objs if o["id"].startswith(pref)]
    if not objs:
        return "No hay objetos reconocidos" + (f" con prefijo {pref}" if pref else "") + " en la mesa."
    return "Objetos: " + "; ".join(
        f"{o['id']} = {o.get('label') or '¿?'}" + (f" en {tuple(round(v) for v in centro(o))} mm" if centro(o) else "")
        for o in objs[:30]) + "."


async def mesa_ver(ctx: Contexto, a: dict) -> str:
    """Hoy la voz no recibe imágenes: devuelve lo que la cámara reconoce en esa zona y el resumen de `look`."""
    o, objs, m = await _objeto(ctx, a.get("objeto", "")) if a.get("objeto") else (None, None, await ctx.mundo())
    objs = objs if objs is not None else objetos(m)
    args = {"annotate": False}
    if str(a.get("limpia", "")).lower() in ("true", "1", "si", "sí"):
        args["clean"] = True
    try:
        foto = await ctx.mesa.tool("look", args, timeout=25)
    except ErrorMesa as e:
        foto = {"error": str(e)[:120]}
    partes = []
    if o:
        c, r = centro(o), radio(o)
        cerca = [x for x in objs if x is not o and centro(x) and c and math.dist(centro(x), c) < r + 120]
        partes.append(f"{o.get('label') or o['id']} está en {tuple(round(v) for v in c)} mm, de unos {round(2 * r)} mm.")
        if cerca:
            partes.append("Cerca: " + ", ".join(x.get("label") or x["id"] for x in cerca[:6]) + ".")
    resumen = foto.get("summary") or foto.get("texto") or foto.get("text")
    if resumen:
        partes.append(f"La cámara: {str(resumen)[:400]}")
    elif foto.get("error"):
        partes.append(f"No pude sacar la foto ({foto['error']}).")
    partes.append("(La foto no te llega como imagen: si necesitas leer algo, pídeselo a la persona.)")
    return " ".join(partes)


async def mesa_preguntar(ctx: Contexto, a: dict) -> str:
    """Una pregunta tipada sobre la foto de la mesa, contestada por JEV (ve la imagen; el LLM de la voz no)."""
    from . import vision
    pregunta = str(a.get("pregunta") or "").strip()
    if not pregunta:
        return "Falta la pregunta."
    if not vision.disponible():
        return "Ahora no puedo mirar la foto (JEV no está disponible). Pregúntale a la persona."
    m = await ctx.mundo()
    objs = objetos(m)
    crudo = str(a.get("opciones") or "").strip()
    if crudo.lower() == "objetos":
        opciones = [o.get("label") or o["id"] for o in objs][:24] + ["ninguno"]
    else:
        opciones = [x.strip() for x in crudo.split("|") if x.strip()]
    args = {"annotate": False}
    if str(a.get("limpia", "")).lower() in ("true", "1", "si", "sí"):
        args["clean"] = True
    jpg = vision.foto_de(await ctx.mesa.look_crudo(args))
    if not jpg:
        return "La cámara no me dio foto."
    estado = {"objetos": [{"nombre": o.get("label") or o["id"], "centro_mm": centro(o)} for o in objs[:24]],
              "proyectado": [texto_widget(w) for w in widgets(m) if texto_widget(w)][:8]}
    r = await vision.preguntar(jpg, pregunta, opciones or None, estado)
    if not r:
        return "No pude mirar la foto ahora (JEV no respondió). Pregúntale a la persona."
    if "choice" in r:
        seguro = "seguro" if r["confidence"] >= 0.7 else "no muy seguro"
        return f"En la foto: {r['choice']} ({seguro}, {r['confidence']:.0%})."
    p = r["si"]
    return f"En la foto: {'sí' if p >= 0.5 else 'no'} ({p:.0%} de que sí)."


def describir_evento(ev: dict) -> str | None:
    """Un evento de la mesa en una frase (o None si no importa a la voz)."""
    t = ev.get("type")
    d = ev.get("data") or {}
    if t == "widget_pressed":
        return f"tocaron {ev.get('value') or ev.get('widget_id')} ({ev.get('widget_id')})"
    if t == "object_appeared":
        o = ev.get("object") or d
        return f"apareció {o.get('label') or o.get('id') or o.get('object_id')}"
    if t == "object_removed":
        return f"se fue {ev.get('label') or ev.get('object_id') or d.get('label')}"
    if t == "object_moved":
        return None
    if t == "flow_emitted":
        e = ev.get("event")
        if e == "voice_command":
            return f"dijeron por el botón de voz: «{d.get('text')}»" if d.get("text") else None
        if e == "scene_changed":
            return f"cambió la pestaña a {d.get('scene')}"
        if e == "nivel":
            ml = d.get("ml")
            if ml is None:
                return "el vaso no se ve bien"
            s = f"vaso: {round(ml)} ml"
            if d.get("objetivo_ml") is not None:
                s += f" de {round(d['objetivo_ml'])}"
            if d.get("estado"):
                s += f", {str(d['estado']).replace('_', ' ')}"
            if d.get("exceso_ml"):
                s += f" (sobran {round(d['exceso_ml'])} ml)"
            return s
        if e == "preset_hecho":
            return None
        if e in ("libreta_nueva", "libreta_escrita"):
            return "Matilda escribió algo nuevo en la libreta" + (f": «{d['texto']}»" if d.get("texto") else "")
        if e == "solar_armado":
            return f"el sistema solar quedó armado ({len(d.get('planetas') or [])} planetas)"
        if e == "canasto_movido":
            return "movieron el canasto; la luz lo siguió"
        if e == "canasto_perdido":
            return "el canasto dejó de verse"
        if e == "contaminacion":
            return "hay algo que no debería estar en la mesa: " + ", ".join(d.get("objetos") or []) if d.get("hay") else None
        return f"{e}: {json.dumps(d, ensure_ascii=False)[:160]}"
    return None


async def mesa_eventos(ctx: Contexto, a: dict) -> str:
    frases = [f for f in (describir_evento(e) for e in ctx.eventos[-12:]) if f]
    return ("Lo último que pasó en la mesa: " + "; ".join(frases) + ".") if frases else "No ha pasado nada nuevo."


# ------------------------------------------------------------------------------------------------ preset


async def preset_leer(ctx: Contexto, a: dict) -> str:
    m = await ctx.mundo()
    p = preset_de(a.get("preset") or m.get("scene") or ctx.escena.pestana, ctx.escena)
    partes = []
    if m.get("scene"):
        hay = [n.get("name") if isinstance(n, dict) else str(n) for n in (m.get("scenes") or [])]
        partes.append(f"Pestaña activa: {m['scene']}" + (f" (hay: {', '.join(hay)})" if hay else "") + ".")
    partes.append(resumen_preset(p) if p else
                  "Este preset no trae manifiesto (preset.json): usa los verbos comunes sobre los objetos de la mesa.")
    objs = objetos(m)
    pref = (p or {}).get("id")
    propios_preset = [o for o in objs if pref and o["id"].startswith(pref + ":")]
    if objs:
        partes.append("Objetos ahora: " + "; ".join(f"{o['id']} = {o.get('label') or '¿?'}"
                                                    for o in (propios_preset or objs)[:20]) + ".")
    ws = widgets(m)
    ajenos = [f"{w['id']}: «{texto_widget(w)}»" for w in ws if not _propio(ctx.escena, w["id"]) and texto_widget(w)]
    if ajenos:
        partes.append("Lo que muestra la escena (no lo toques): " + "; ".join(ajenos[:10]) + ".")
    propios = [w["id"] for w in ws if _propio(ctx.escena, w["id"])]
    if propios:
        partes.append("Lo tuyo proyectado: " + ", ".join(propios) + ".")
    return "\n".join(partes)


async def _emitir(ctx: Contexto, evento: str, data: dict) -> str:
    """Un flow_emitted propio: `scenes emit` (la mesa le pone by:"agent" y la escena) o, si el nodo no lo tiene,
    `run_flow` con una acción emit (contrato §3). Devuelve el id del pedido (`voz-…`, así el oído no se lo cuenta a
    sí mismo)."""
    pid = f"voz-{int(time.time())}-{next(_flujos)}"
    data = {**data, "id": pid, "de": "voz"}
    try:
        await ctx.mesa.tool("scenes", {"action": "emit", "event": evento, "data": data}, timeout=15)
    except ErrorMesa as e:
        if "emit" not in str(e) and "action" not in str(e):
            raise
        flujo = {"id": pid, "initial": "e", "cleanup": False,
                 "states": [{"id": "e", "final": True, "on_enter": [{"type": "emit", "event": evento, "data": data}]}]}
        await ctx.mesa.tool("run_flow", {"flow": flujo}, timeout=20)
    return pid


async def _esperar_hecho(ctx: Contexto, pid: str, segundos: float = 6.0) -> dict | None:
    fin = time.monotonic() + segundos
    while time.monotonic() < fin:
        if pid in ctx.respuestas:
            return ctx.respuestas.pop(pid)
        await asyncio.sleep(0.25)
    return None


async def _pedido(ctx: Contexto, evento: str, data: dict, que: str) -> str:
    try:
        pid = await _emitir(ctx, evento, data)
    except ErrorMesa as e:
        return (f"La mesa todavía no deja mandarle pedidos al preset ({str(e)[:100]}). Hazlo con los verbos comunes "
                "(señalar, nombrar, explicar, medir, avisar).")
    r = await _esperar_hecho(ctx, pid)
    if r is None:
        return f"Pedí {que}; el conductor no confirmó todavía (puede que no esté corriendo)."
    if r.get("ok") is False:
        return f"El preset no pudo: {r.get('error') or 'sin detalle'}."
    res = r.get("resultado")
    return f"Hecho: {que}." + (f" {json.dumps(res, ensure_ascii=False)[:200]}" if res else "")


PASOS = {"siguiente": "paso_siguiente", "anterior": "paso_anterior", "repetir": "repetir"}


async def paso(ctx: Contexto, a: dict) -> str:
    """Avanzar, volver o repetir el paso del conductor con un evento propio (no voice_command: la mesa lo reserva)."""
    orden = _norm(a.get("orden") or "")
    orden = {"listo": "siguiente", "sigue": "siguiente", "atras": "anterior", "vuelve": "anterior",
             "repite": "repetir", "otra vez": "repetir"}.get(orden, orden)
    if orden not in PASOS:
        return "La orden es siguiente, anterior o repetir."
    m = ctx._mundo or await ctx.mundo()
    p = preset_de(m.get("scene") or ctx.escena.pestana, ctx.escena)
    try:
        await _emitir(ctx, PASOS[orden], {"preset": (p or {}).get("id") or ctx.escena.preset_id})
    except ErrorMesa as e:
        return (f"La mesa todavía no recibe pasos de la voz ({str(e)[:90]}). Pídele a la persona que toque el botón "
                "del paso en la mesa.")
    return f"Pedí {PASOS[orden].replace('_', ' ')} al conductor."


def _args(a: dict) -> dict:
    v = a.get("args")
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            d = json.loads(v)
            return d if isinstance(d, dict) else {"valor": d}
        except ValueError:
            return {"texto": v}
    return {}


async def preset_accion(ctx: Contexto, a: dict) -> str:
    accion = str(a.get("accion") or "").strip()
    if not accion:
        return "Falta la acción."
    m = ctx._mundo or await ctx.mundo()
    p = preset_de(m.get("scene") or ctx.escena.pestana, ctx.escena)
    if p and accion not in (p.get("acciones") or {}):
        return f"El preset no tiene la acción «{accion}». Tiene: {', '.join(p.get('acciones') or {}) or 'ninguna'}."
    return await _pedido(ctx, "preset_accion", {"preset": (p or {}).get("id") or ctx.escena.preset_id,
                                                "accion": accion, "args": _args(a)}, accion)


async def preset_ajustar(ctx: Contexto, a: dict) -> str:
    param = str(a.get("param") or "").strip()
    m = ctx._mundo or await ctx.mundo()
    p = preset_de(m.get("scene") or ctx.escena.pestana, ctx.escena)
    if p and param not in (p.get("params") or {}):
        return f"El preset no tiene el parámetro «{param}». Tiene: {', '.join(p.get('params') or {}) or 'ninguno'}."
    valor = a.get("valor")
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except ValueError:
            pass
    return await _pedido(ctx, "preset_ajustar", {"preset": (p or {}).get("id") or ctx.escena.preset_id,
                                                 "param": param, "valor": valor}, f"{param} = {valor}")


# ------------------------------------------------------------------------------------------------ verbos comunes


async def senalar(ctx: Contexto, a: dict) -> str:
    o, objs, _ = await _objeto(ctx, a.get("objeto", ""))
    if not o:
        return f"No encontré «{a.get('objeto')}» en la mesa. Hay: {_lista_nombres(objs)}."
    color = TONOS[a["tono"]][0] if a.get("tono") in TONOS else ctx.escena.color
    w = {"type": "halo", "id": f"{PREFIJO}foco", "anchor": {"object_id": o["id"]}, "color": color, "width_mm": 6,
         "pulse_hz": 0, "z": 20, "radius_mm": round(radio(o) + 10)}
    await _project(ctx, [w])
    return f"Halo sobre {o.get('label') or o['id']}."


async def nombrar(ctx: Contexto, a: dict) -> str:
    o, objs, _ = await _objeto(ctx, a.get("objeto", ""))
    if not o:
        return f"No encontré «{a.get('objeto')}». Hay: {_lista_nombres(objs)}."
    texto = " ".join(str(a.get("texto") or "").split()[:3]) or (o.get("label") or o["id"])
    w = {"type": "label", "id": f"{PREFIJO}nombre-{re.sub(r'[^a-z0-9]+', '-', o['id'].lower())}",
         "anchor": {"object_id": o["id"], "offset_mm": [0, round(radio(o) + 26)]},
         "text": texto, "size_mm": 28, "color": "#F4F4F2", "bg": "#000000A0", "z": 18}
    await _project(ctx, [w])
    return f"Etiqueta «{texto}» junto a {o.get('label') or o['id']}."


async def explicar(ctx: Contexto, a: dict) -> str:
    titulo, texto = str(a.get("titulo") or "").strip(), str(a.get("texto") or "").strip()
    if not texto and not titulo:
        return "Falta el texto del panel."
    rect = list(ctx.escena.panel_mm)
    if a.get("cerca_de") and ctx.escena.panel_id != "lab-nota":
        o, _, m = await _objeto(ctx, a["cerca_de"])
        if o:
            rect = junto_a(o, rect[2], rect[3], alto_mesa(m))
    if ctx.escena.panel_id == "lab-nota":   # la nota del laboratorio es una línea de 28 mm
        cuerpo = ("<div class='sd-panel' style='height:100%;padding:6px 16px;display:flex;align-items:center'>"
                  f"<p class='wui-pista'><span class='wui-linea'>{_esc((titulo + ': ' if titulo else '') + texto)}</span></p></div>")
    else:
        cuerpo = ("<section class='sd-panel' style='height:100%;overflow:hidden'>"
                  + (f"<h1>{_esc(titulo)}</h1>" if titulo else "") + f"<p class='wui-pista'><span class='wui-linea'>{_esc(texto)}</span></p></section>")
    w = {"type": "effect", "shader": "html", "id": ctx.escena.panel_id, "z": 16, "rect_mm": rect,
         "params": _html(ctx, cuerpo)}
    try:
        if a.get("segundos"):
            w["ttl_s"] = max(3.0, min(120.0, float(a["segundos"])))
    except (TypeError, ValueError):
        pass
    await _project(ctx, [w])
    return "Panel proyectado."


async def medir(ctx: Contexto, a: dict) -> str:
    valor, unidad = str(a.get("valor") or "").strip(), str(a.get("unidad") or "").strip()
    if not valor:
        return "Falta el valor."
    m = await ctx.mundo()
    o = resolver(objetos(m), a.get("objeto", ""), ctx.escena.prefijo) if a.get("objeto") else None
    rect = junto_a(o, 220, 120, alto_mesa(m)) if o else list(ctx.escena.aviso_mm[:2]) + [220, 120]
    cuerpo = (f"<div class='sd-panel' style='height:100%'><div class='sd-dato'>{_esc(valor)}"
              f"<small>{_esc(unidad)}</small></div>"
              + (f"<p class='sd-tenue'>{_esc(a.get('etiqueta'))}</p>" if a.get("etiqueta") else "") + "</div>")
    w = {"type": "effect", "shader": "html", "id": f"{PREFIJO}dato", "z": 17, "rect_mm": rect,
         "params": _html(ctx, cuerpo)}
    await _project(ctx, [w])
    return f"Dato {valor} {unidad} proyectado."


async def avisar(ctx: Contexto, a: dict) -> str:
    tono = a.get("tono") if a.get("tono") in TONOS else "alerta"
    texto = str(a.get("texto") or "").strip()
    if not texto:
        return "Falta el texto del aviso."
    cuerpo = (f"<div class='sd-aviso' data-tono='{tono}' style='height:100%'><b>{TONOS[tono][1]}.</b> "
              f"{_esc(texto)}</div>")
    w = {"type": "effect", "shader": "html", "id": f"{PREFIJO}aviso", "z": 22, "rect_mm": list(ctx.escena.aviso_mm),
         "params": _html(ctx, cuerpo), "ttl_s": 10}
    await _project(ctx, [w])
    return f"Aviso ({tono}) por 10 s."


async def llevar(ctx: Contexto, a: dict) -> str:
    m = await ctx.mundo()
    objs = objetos(m)
    o1 = resolver(objs, a.get("desde", ""), ctx.escena.prefijo)
    o2 = resolver(objs, a.get("hasta", ""), ctx.escena.prefijo)
    if not o1 or not o2:
        return f"No encontré «{a.get('desde') if not o1 else a.get('hasta')}». Hay: {_lista_nombres(objs)}."
    c1, c2 = centro(o1), centro(o2)
    d = math.dist(c1, c2) or 1.0
    ux, uy = (c2[0] - c1[0]) / d, (c2[1] - c1[1]) / d
    r1, r2 = radio(o1) + 8, radio(o2) + 8
    w = {"type": "arrow", "id": f"{PREFIJO}flecha", "from_mm": [round(c1[0] + ux * r1), round(c1[1] + uy * r1)],
         "to_mm": [round(c2[0] - ux * r2), round(c2[1] - uy * r2)], "color": ctx.escena.color, "width_mm": 6, "z": 19}
    await _project(ctx, [w])
    return f"Flecha de {o1.get('label') or o1['id']} a {o2.get('label') or o2['id']}."


async def callar(ctx: Contexto, a: dict) -> str:
    que = a.get("que") or "todo"
    ids = [w["id"] for w in widgets(await ctx.mundo()) if _propio(ctx.escena, w["id"])]
    filtros = {"foco": lambda i: i == f"{PREFIJO}foco", "panel": lambda i: i == ctx.escena.panel_id,
               "aviso": lambda i: i == f"{PREFIJO}aviso", "nombres": lambda i: i.startswith(f"{PREFIJO}nombre-"),
               "flecha": lambda i: i == f"{PREFIJO}flecha", "dato": lambda i: i == f"{PREFIJO}dato"}
    ids = [i for i in ids if filtros.get(que, lambda i: True)(i)]
    if not ids:
        return "No había nada tuyo proyectado."
    await ctx.mesa.tool("remove", {"ids": ids})
    return f"Quité {len(ids)} cosa(s)."


async def cambiar_pestana(ctx: Contexto, a: dict) -> str:
    nombre = str(a.get("nombre") or "").strip()
    lista = await ctx.mesa.tool("scenes", {"action": "list"})
    nombres = [n.get("name") if isinstance(n, dict) else n for n in (lista.get("scenes") or [])]
    elegido = next((n for n in nombres if _norm(n) == _norm(nombre)), None) or \
        next((n for n in nombres if _norm(nombre) and _norm(nombre) in _norm(n)), None)
    if not elegido:
        return f"No hay una pestaña «{nombre}». Hay: {', '.join(nombres) or 'ninguna'}."
    await ctx.mesa.tool("scenes", {"action": "activate", "name": elegido})
    return f"Pestaña «{elegido}» activa."


# ------------------------------------------------------------------------------------------------ catálogo

OBJ = S("El objeto, como lo dirías («la Tierra», «el vaso», «la libreta») o su id de mesa_objetos.")

CATALOGO: list[Herramienta] = [
    Herramienta("preset_leer", "Lee el preset activo: para qué es, sus acciones, sus parámetros con su valor, sus "
                "eventos, los objetos que hay y lo que la escena ya muestra (pasos, medidas). Úsala al partir y cuando "
                "cambie la pestaña. No dice nada en voz alta.", preset_leer,
                {"preset": S("Opcional: id o nombre del preset; vacío = el de la pestaña activa.")}),
    Herramienta("mesa_objetos", "El catálogo de objetos de la mesa: id, nombre y posición en mm.", mesa_objetos,
                {"prefijo": S("Opcional: solo los de un preset, p. ej. «solar:» o «lab:».")}),
    Herramienta("mesa_ver", "Mira la mesa con la cámara, entera o en torno a un objeto: qué hay cerca y el resumen de "
                "la cámara.", mesa_ver,
                {"objeto": S("Opcional: el objeto a mirar."),
                 "limpia": S("«true» para apagar un instante lo proyectado y mirar sin luz encima.", ["true", "false"])}),
    Herramienta("mesa_preguntar", "Mira la foto de la mesa y contesta UNA pregunta concreta sobre lo que se ve ahora: "
                "qué señala o toca la persona, si el vaso se ve rebalsado, si hay algo escrito en la libreta, qué hay "
                "en la mano. Mejor con opciones (elige una, es más confiable); sin opciones contesta sí o no con su "
                "probabilidad. Tarda ~1 s.",
                mesa_preguntar,
                {"pregunta": S("La pregunta, concreta: «¿Qué planeta está señalando con el dedo?», «¿El líquido pasó la "
                               "línea de 50 ml?»."),
                 "opciones": S("Opcional: opciones separadas por «|» (p. ej. «Saturno|Júpiter|ninguno»), o «objetos» "
                               "para elegir entre los objetos de la mesa. Vacío = pregunta de sí o no."),
                 "limpia": S("«true» para mirar sin la luz proyectada encima.", ["true", "false"])},
                ("pregunta",), timeout=25),
    Herramienta("mesa_eventos", "Lo último que pasó en la mesa: toques, objetos que aparecen o se van, medidas, "
                "cambios de pestaña.", mesa_eventos),
    Herramienta("preset_accion", "Pide al preset una de sus acciones declaradas (las lista preset_leer), p. ej. "
                "ir_a {planeta}, marcar_celda {celda}, siguiente_paso. El conductor de la escena la hace y confirma.",
                preset_accion,
                {"accion": S("Nombre exacto de la acción del preset."),
                 "args": S("Argumentos en JSON, p. ej. {\"planeta\": \"solar:saturno\"}. Vacío si no lleva.")},
                ("accion",)),
    Herramienta("paso", "Avanza, vuelve o repite el paso de la escena cuando la persona lo pide («listo», «sigue», "
                "«atrás», «repite»). El conductor de la escena lo hace.", paso,
                {"orden": S("siguiente, anterior o repetir", ["siguiente", "anterior", "repetir"])}, ("orden",)),
    Herramienta("preset_ajustar", "Cambia en vivo un parámetro del preset (los lista preset_leer con su rango).",
                preset_ajustar, {"param": S("Nombre exacto del parámetro."), "valor": S("El valor nuevo.")},
                ("param", "valor")),
    Herramienta("senalar", "Señala una cosa de la mesa con un halo que la sigue si se mueve («esta»). Hay un solo halo "
                "tuyo: señalar otra cosa lo mueve.", senalar,
                {"objeto": OBJ, "tono": S("Opcional: ok, alerta o peligro si el halo además avisa.",
                                          ["ok", "alerta", "peligro"])}, ("objeto",)),
    Herramienta("nombrar", "Etiqueta corta (máximo 3 palabras) pegada a una cosa de la mesa.", nombrar,
                {"objeto": OBJ, "texto": S("La etiqueta; vacío = el nombre del objeto.")}, ("objeto",)),
    Herramienta("explicar", "Panel con la idea que estás diciendo, resumida en una o dos frases. Un panel a la vez: "
                "uno nuevo reemplaza al anterior.", explicar,
                {"texto": S("Una o dos frases cortas."), "titulo": S("Opcional: 2 a 4 palabras."),
                 "cerca_de": S("Opcional: objeto junto al que va el panel."),
                 "segundos": S("Opcional: cuánto dura; vacío = hasta que lo cambies o calles.")}, ("texto",)),
    Herramienta("medir", "Un número grande con su unidad junto a una cosa (ml, cm, minutos).", medir,
                {"valor": S("El número, p. ej. 42."), "unidad": S("La unidad, p. ej. ml."),
                 "objeto": S("Opcional: junto a qué objeto."), "etiqueta": S("Opcional: qué es, en 2 o 3 palabras.")},
                ("valor", "unidad")),
    Herramienta("avisar", "Aviso con tono: ok (bien hecho), alerta (ojo) o peligro (detente). Dura 10 segundos.",
                avisar, {"tono": S("ok, alerta o peligro", ["ok", "alerta", "peligro"]),
                         "texto": S("El aviso, muy corto.")}, ("tono", "texto")),
    Herramienta("llevar", "Flecha de una cosa a otra («de aquí a allá»), de borde a borde.", llevar,
                {"desde": OBJ, "hasta": OBJ}, ("desde", "hasta")),
    Herramienta("callar", "Quita lo que tú proyectaste (nunca lo de la escena).", callar,
                {"que": S("todo, foco, panel, aviso, nombres, flecha o dato",
                          ["todo", "foco", "panel", "aviso", "nombres", "flecha", "dato"])}),
    Herramienta("cambiar_pestana", "Cambia la pestaña (escena) activa de la mesa.", cambiar_pestana,
                {"nombre": S("Nombre de la pestaña, p. ej. «Sistema solar», «Laboratorio», «Escritorio».")},
                ("nombre",)),
]

POR_NOMBRE = {h.nombre: h for h in CATALOGO}


def de_escena(e: Escena) -> list[Herramienta]:
    return list(CATALOGO)


def client_config(h: Herramienta) -> dict:
    """tool_config de ElevenLabs para una client tool (la ejecuta la página, que llama al servidor local)."""
    return {
        "type": "client",
        "name": h.nombre,
        "description": h.descripcion,
        "parameters": h.schema(),
        "expects_response": True,
        "response_timeout_secs": h.timeout,
        "execution_mode": "immediate",
        "pre_tool_speech": "off",
    }


async def ejecutar(ctx: Contexto, nombre: str, args: dict) -> str:
    h = POR_NOMBRE.get(nombre)
    if not h:
        return f"No conozco la herramienta {nombre}."
    try:
        return await h.fn(ctx, dict(args or {}))
    except ErrorMesa as e:
        return f"La mesa no respondió bien: {str(e)[:200]}"
    except Exception as e:  # noqa: BLE001 (una tool nunca bota la conversación)
        return f"Algo falló en {nombre}: {type(e).__name__}"

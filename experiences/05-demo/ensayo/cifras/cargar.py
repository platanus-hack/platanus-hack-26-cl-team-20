"""Carga las cifras de la demo de WUI en sus pestañas de la mesa. Solo `python3`, sin instalar nada.

  python3 cargar-v2.py --probar      # no se conecta: muestra lo que haría
  python3 cargar-v2.py estado        # solo lee: qué pestañas hay y qué cifras tiene cada una
  python3 cargar-v2.py --crear       # PRIMERA VEZ: crea las pestañas que falten (Inicio, Escritorio, Cierre) y carga
  python3 cargar-v2.py               # carga o repone las cifras SIN cambiar de pestaña (se puede repetir)
  python3 cargar-v2.py quitar solar  # quita una cifra: inicio, escritorio, solar, laboratorio, cierre o todo

Variables: PCX_MCP_URL y PCX_USER_TOKEN (las mismas que usan las escenas; el token nunca se imprime).

Es la misma llamada que hace el escritorio de Max (escenas/escritorio/mesa.py:49-88): JSON-RPC `tools/call`.
Diferencias con cargar.py (13-cifras): ver cambios.md. SIN PROBAR contra la mesa real.

Códigos de salida: 0 todo cargado y comprobado · 1 quedó algo sin cargar · 2 no se tocó nada.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import socket
import sys
import unicodedata
import urllib.error
import urllib.request
import uuid
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PREFIJO = "guion-"  # este script solo crea y borra ids que empiezan así
# Pestañas que este script puede crear. Las del solar y el laboratorio NO: las crea el operador con su escena.
SE_PUEDEN_CREAR = ("inicio", "escritorio", "cierre")
# Nombres posibles de cada pestaña. Solar: «Sistema solar» (camino A) o «solar» (camino B).
CANDIDATOS = {"inicio": ["Inicio"], "escritorio": ["Escritorio"], "solar": ["Sistema solar", "solar"],
              "laboratorio": ["Laboratorio"], "cierre": ["Cierre"]}
ESPERA_S = float(os.environ.get("PCX_ESPERA_S", "20"))  # el gateway espera a la consola 15 s (gateway.py:29-35)
_ids = itertools.count(1)


class ErrorMesa(Exception):
    pass


def llamar(url: str, token: str, tool: str, args: dict, timeout: float = ESPERA_S) -> dict:
    cuerpo = json.dumps({"jsonrpc": "2.0", "id": next(_ids), "method": "tools/call",
                         "params": {"name": tool, "arguments": args}}).encode()
    req = urllib.request.Request(url, data=cuerpo, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"})
    try:
        texto = urllib.request.urlopen(req, timeout=timeout).read().decode()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ErrorMesa("la mesa rechazó el token (HTTP 401). Aprieta «Copiar» en la consola y exporta de nuevo PCX_USER_TOKEN.") from None
        raise ErrorMesa(f"la mesa contestó HTTP {e.code}") from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        raise ErrorMesa(f"no hay respuesta de la mesa en esa dirección ({getattr(e, 'reason', e)})") from None
    datos = [ln[5:].strip() for ln in texto.splitlines() if ln.startswith("data:")]  # respuesta JSON o SSE
    try:
        r = json.loads(datos[-1] if datos else texto)
        if not isinstance(r, dict):
            raise ValueError
    except ValueError:
        raise ErrorMesa("la respuesta no es de la mesa (¿la dirección termina en /mcp?)") from None
    if "error" in r:
        raise ErrorMesa(f"{tool}: {r['error']}")
    res = r.get("result") or {}
    out = res.get("structuredContent")
    if out is None:
        for c in res.get("content", []):
            if c.get("type") == "text":
                try:
                    out = json.loads(c["text"])
                except ValueError:
                    out = {"texto": c["text"]}
    out = out if isinstance(out, dict) else {}
    if res.get("isError"):
        raise ErrorMesa(f"{tool}: {out.get('error', out)}")
    return out


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s).strip().casefold())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def leer_bloques(ruta: Path) -> dict[str, list[dict]]:
    """clave (inicio, escritorio, solar, laboratorio, cierre) → widgets. Se niega a cargar algo que no sea una cifra."""
    bloques = {}
    for b in json.loads(ruta.read_text(encoding="utf-8"))["bloques"]:
        clave = norm(b["escena"])
        if clave not in CANDIDATOS:
            raise ErrorMesa(f"bloque desconocido en el JSON: {b['escena']}")
        for w in b["widgets"]:
            if not str(w.get("id", "")).startswith(PREFIJO) or w.get("type") != "effect" or w.get("shader") != "html" or "js" in w.get("params", {}):
                raise ErrorMesa(f"widget inesperado en el JSON: {w.get('id')} (solo se cargan html con id «{PREFIJO}…» y sin script)")
        bloques[clave] = b["widgets"]
    return bloques


def main() -> int:
    ap = argparse.ArgumentParser(description="Carga las cifras de la demo en la mesa.")
    ap.add_argument("accion", nargs="?", default="cargar", choices=["cargar", "quitar", "estado"])
    ap.add_argument("bloque", nargs="?", help="con quitar: inicio, escritorio, solar, laboratorio, cierre o todo")
    ap.add_argument("--crear", action="store_true", help="crea las pestañas Inicio, Escritorio y Cierre si faltan (la mesa cambia de pestaña 1 s)")
    ap.add_argument("--solar", help="nombre exacto de la pestaña del sistema solar (si no, busca «Sistema solar» y «solar»)")
    ap.add_argument("--laboratorio", help="nombre exacto de la pestaña del laboratorio (si no, «Laboratorio»)")
    ap.add_argument("--json", default=str(AQUI / "cifras-final.json"), help="archivo de cifras")
    ap.add_argument("--probar", action="store_true", help="no se conecta: muestra el plan")
    a = ap.parse_args()

    try:
        bloques = leer_bloques(Path(a.json))
    except (OSError, ValueError, KeyError, ErrorMesa) as e:
        print(f"NO SE TOCÓ NADA. No pude leer las cifras ({a.json}): {e}")
        return 2
    candidatos = {k: list(v) for k, v in CANDIDATOS.items()}
    if a.solar:
        candidatos["solar"] = [a.solar]
    if a.laboratorio:
        candidatos["laboratorio"] = [a.laboratorio]
    claves = list(bloques)
    if a.accion == "quitar":
        pedido = norm(a.bloque or "")
        if pedido not in claves + ["todo"]:
            print("NO SE TOCÓ NADA. Indica qué quitar: " + ", ".join(claves) + " o todo")
            return 2
        claves = claves if pedido == "todo" else [pedido]

    if a.probar:
        for k in claves:
            print(f"{a.accion} {k}: pestaña {' o '.join('«' + c + '»' for c in candidatos[k])} · ids {[w['id'] for w in bloques[k]]}")
        print("(--probar: no me conecté a la mesa)")
        return 0

    url = os.environ.get("PCX_MCP_URL", "").strip()
    token = os.environ.get("PCX_USER_TOKEN", "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not url or not token:
        print("NO SE TOCÓ NADA. Faltan PCX_MCP_URL o PCX_USER_TOKEN (botón «Copiar» de la consola; mismo bloque que las terminales de las escenas).")
        return 2
    if not url.startswith(("http://", "https://")) or any(c.isspace() for c in token):
        print("NO SE TOCÓ NADA. PCX_MCP_URL debe empezar con http:// o https://, y el token no lleva espacios ni saltos de línea.")
        return 2
    print(f"mesa: {url} · token de {len(token)} caracteres")

    def tool(nombre: str, args: dict) -> dict:
        return llamar(url, token, nombre, args)

    # 1) Solo lectura: qué pestañas hay. Si esto falla, no se ha tocado nada.
    try:
        estado = tool("scenes", {"action": "list"})
    except ErrorMesa as e:
        print(f"NO SE TOCÓ NADA. {e}")
        return 2
    hay = [s["name"] if isinstance(s, dict) else str(s) for s in estado.get("scenes", [])]
    activa = estado.get("scene")
    if estado.get("console_outdated"):
        print("OJO: la consola dice que corre código viejo; recárgala si algo se ve raro.")
    print(f"pestañas: {', '.join(hay)} · a la vista: «{activa}»")

    def existentes(clave: str) -> list[str]:
        """Pestañas reales de un bloque (nombre exacto; si no, el mismo nombre sin mayúsculas ni tildes)."""
        out = []
        for c in candidatos[clave]:
            real = c if c in hay else next((h for h in hay if norm(h) == norm(c)), None)
            if real and real not in out:
                out.append(real)
        return out

    def ids_en(pestana: str) -> list[str]:
        ws = tool("scene", {"scene": pestana}).get("widgets", [])
        return [w.get("id") if isinstance(w, dict) else w for w in ws]

    if a.accion == "estado":
        for k in claves:
            for p in existentes(k) or [None]:
                if p is None:
                    print(f"  {k:<12} FALTA la pestaña ({' o '.join(candidatos[k])})")
                    continue
                try:
                    en_mesa = ids_en(p)
                except ErrorMesa as e:
                    print(f"  {k:<12} «{p}»: no pude leer ({e})")
                    continue
                faltan = [w["id"] for w in bloques[k] if w["id"] not in en_mesa]
                print(f"  {k:<12} «{p}»: {'cifra puesta' if not faltan else 'SIN cifra (' + ', '.join(faltan) + ')'} · {len(en_mesa)} widgets en total")
        return 0

    if a.accion == "quitar":
        malos = 0
        for k in claves:
            for p in existentes(k):
                try:
                    r = tool("remove", {"request_id": f"cifras-{uuid.uuid4().hex}", "scene": p, "ids": [w["id"] for w in bloques[k]]})
                    print(f"  {k}: quité de «{p}» {r.get('removed', [])}")
                except ErrorMesa as e:
                    malos += 1
                    print(f"  {k}: NO pude quitar de «{p}»: {e}")
        return 1 if malos else 0

    # 2) Pestañas que faltan. Sin --crear no se cambia nada: crear una pestaña la deja a la vista (console/node.js:721-723).
    por_crear = [candidatos[k][0] for k in claves if k in SE_PUEDEN_CREAR and not existentes(k)]
    if por_crear and not a.crear:
        print(f"NO SE TOCÓ NADA. Faltan las pestañas: {', '.join(por_crear)}.\n"
              "Para crearlas: python3 cargar-v2.py --crear\n"
              "(la mesa cambia de pestaña 1 segundo y vuelve; hazlo SIN el sistema solar ni el laboratorio corriendo)")
        return 2
    malos = 0
    if por_crear:
        try:
            for nombre in por_crear:
                tool("scenes", {"action": "create", "name": nombre})
                hay.append(nombre)
                print(f"pestaña creada: «{nombre}»")
        except ErrorMesa as e:
            malos += 1
            print(f"FALLÓ al crear pestañas: {e}")
        finally:  # pase lo que pase, volver a la pestaña que estaba a la vista
            vuelta = False
            for _ in range(2):
                try:
                    vuelta = tool("scenes", {"action": "activate", "name": activa}).get("scene") == activa
                    if vuelta:
                        break
                except ErrorMesa:
                    pass
            print(f"de vuelta en «{activa}»" if vuelta else f"OJO: NO pude volver a «{activa}». Haz clic en esa pestaña en la consola.")
            malos += 0 if vuelta else 1

    # 3) Cargar cada bloque en su pestaña (project con scene: no cambia la pestaña a la vista) y comprobar leyendo.
    for k in claves:
        destinos = existentes(k)
        if not destinos:
            malos += 1
            como = "Repite con --crear." if k in SE_PUEDEN_CREAR else "Créala con su escena (paso 2 de la hoja del operador) y repite este comando."
            print(f"  {k:<12} SIN CARGAR: no existe la pestaña {' ni '.join('«' + c + '»' for c in candidatos[k])}. {como}")
            continue
        for p in destinos:
            esperados = [w["id"] for w in bloques[k]]
            try:
                r = tool("project", {"request_id": f"cifras-{uuid.uuid4().hex}", "scene": p, "widgets": bloques[k]})
                puestos = ids_en(p)
            except ErrorMesa as e:
                malos += 1
                print(f"  {k:<12} «{p}»: FALLÓ ({e}). Consulta con: python3 cargar-v2.py estado")
                continue
            faltan = [i for i in esperados if i not in puestos]
            malos += 1 if faltan else 0
            print(f"  {k:<12} «{p}»: {'cargada y comprobada' if not faltan else 'NO QUEDÓ: ' + ', '.join(faltan)}"
                  + (" · AVISO: pisa la barra o el botón de voz: " + str(r["reserved"]) if r.get("reserved") else "")
                  + (" · OJO: la ventana del proyector está cerrada" if r.get("projector_open") is False else ""))
    print("LISTO: todas las cifras están en su pestaña." if not malos else f"INCOMPLETO: {malos} cosa(s) sin resolver (ver arriba).")
    return 1 if malos else 0


if __name__ == "__main__":
    sys.exit(main())

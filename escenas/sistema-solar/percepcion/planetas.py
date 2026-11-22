"""Qué planeta es cada bola: por tamaño relativo, con las dudas a la vista y lo que se fijó a mano.

Regla: la bola más grande se lleva el planeta más grande del catálogo, la segunda el segundo, y así. Si dos bolas miden
casi lo mismo (menos de `tolerancia` de diferencia), esa decisión queda como duda: el tutor puede preguntarle a la niña o
cualquiera puede fijarla en `mesa.json` (sin programar). Mientras nadie la fije, la duda se desempata sin mirar el tamaño:
dentro del grupo, la bola más cerca del Sol (el engranaje) se lleva el planeta más interior.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .tipos import Asignacion, Cuerpo

CATALOGO = Path(__file__).resolve().parent.parent / "planetas.json"


def cargar_catalogo(ruta: str | Path = CATALOGO) -> dict:
    return json.loads(Path(ruta).read_text(encoding="utf-8"))


def _elegir(catalogo: dict, n: int) -> list[dict]:
    """Los n planetas que entran: primero prioridad 1; si sobran bolas, 2 y después 3. Si faltan, los más grandes."""
    todos = sorted(catalogo["planetas"], key=lambda p: (p.get("prioridad", 1), -p["radio_km"]))
    base = [p for p in todos if p.get("prioridad", 1) == 1]
    if n <= len(base):
        return sorted(base, key=lambda p: -p["radio_km"])[:n]
    return todos[:n]


def asignar_planetas(
    cuerpos: list[Cuerpo],
    catalogo: dict,
    fijos: dict[str, int] | None = None,
    tolerancia: float = 0.08,
) -> Asignacion:
    """`fijos`: planeta → índice de cuerpo decidido a mano (o por la niña). Lo demás se reparte por tamaño.

    Devuelve índices sobre `cuerpos` (la salida de `detectar_cuerpos`).
    """
    fijos = dict(fijos or {})
    sol = next((i for i, c in enumerate(cuerpos) if c.tipo == "engranaje"), None)
    bolas = [i for i, c in enumerate(cuerpos) if c.tipo == "bola"]
    ids = {p["id"] for p in catalogo["planetas"]}
    fijos = {k: v for k, v in fijos.items() if k in ids and v in bolas}
    elegidos = _elegir(catalogo, len(bolas))
    # Lo fijado a mano manda, aunque ese planeta no hubiera entrado por prioridad.
    for pid in fijos:
        if pid not in {p["id"] for p in elegidos}:
            extra = next(p for p in catalogo["planetas"] if p["id"] == pid)
            elegidos = sorted(elegidos, key=lambda p: (p.get("prioridad", 1), p["radio_km"]))[1:] + [extra]
    libres_bolas = sorted((i for i in bolas if i not in fijos.values()), key=lambda i: -cuerpos[i].radio_mm)
    libres_planetas = sorted((p for p in elegidos if p["id"] not in fijos), key=lambda p: -p["radio_km"])
    planetas = dict(fijos)
    for i, p in zip(libres_bolas, libres_planetas):
        planetas[p["id"]] = i
    sobrantes = libres_bolas[len(libres_planetas) :]

    dudas = []
    radios = np.array([cuerpos[i].radio_mm for i in libres_bolas[: len(libres_planetas)]])
    grupo_planetas = libres_planetas[: len(radios)]
    j = 0
    while j < len(radios):
        k = j
        while k + 1 < len(radios) and radios[k] / max(radios[k + 1], 1e-6) - 1 < tolerancia:
            k += 1
        if k > j:  # bolas j..k casi iguales: cualquiera de esos planetas podría ser cualquiera de esas bolas
            bolas_g = libres_bolas[j : k + 1]
            planetas_g = grupo_planetas[j : k + 1]
            # Desempate sin tamaño: la bola más cerca del Sol se lleva el planeta más interior (orden del Sol hacia
            # afuera), así el canasto queda como un sistema solar de verdad. Sin Sol, queda el orden por tamaño.
            if sol is not None:
                s = np.asarray(cuerpos[sol].centro_mm, dtype=float)
                bolas_g = sorted(bolas_g, key=lambda i: float(np.hypot(*(np.asarray(cuerpos[i].centro_mm) - s))))
                planetas_g = sorted(planetas_g, key=lambda p: p.get("orden", 99))
            grupo = [p["id"] for p in planetas_g]
            for b, p in zip(bolas_g, planetas_g):
                planetas[p["id"]] = b
                dudas.append(
                    {
                        "cuerpo": b,
                        "asignado": p["id"],
                        "opciones": grupo,
                        "motivo": f"{len(grupo)} bolas miden casi lo mismo ({radios[k]:.0f} a {radios[j]:.0f} mm de radio)",
                        "desempate": "más cerca del Sol = planeta más interior" if sol is not None else "tamaño",
                    }
                )
        j = k + 1
    return Asignacion(planetas=planetas, sol=sol, dudas=dudas, sobrantes=sobrantes)

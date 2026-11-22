"""Las tres escenas de la historia de Matilda: un agente de ElevenLabs por escena, con su voz, su ritmo y su zona.

Lo que cambia entre escenas es la personalidad y dónde puede proyectar; los gestos (señalar, nombrar, explicar,
avisar, llevar) son los mismos de MOTOR.md. Todo lo que proyecta la voz lleva el prefijo de id `voz-` (o el id que la
escena le reservó, como `lab-nota`), así nunca pisa los widgets de un conductor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PROMPTS = AQUI.parent / "prompts"
REPO = AQUI.parents[2]


@dataclass(frozen=True)
class Escena:
    clave: str                    # solar | lab | escritorio
    agente: str                   # nombre del agente en ElevenLabs
    pestana: str                  # nombre de la pestaña (escena) en la mesa
    prefijo: str                  # prefijo de ids de objetos de la escena (solar:, lab:, desk:)
    preset_id: str                # id del preset.json (= prefijo sin «:»)
    voz: str                      # voice_id
    voces_respaldo: tuple[str, ...]
    primer_mensaje: str
    turno: str                    # patient | normal | eager
    velocidad: float
    estabilidad: float
    panel_mm: tuple[int, int, int, int]       # zona libre para «explicar» (x, y, ancho, alto)
    aviso_mm: tuple[int, int, int, int]       # zona libre para «avisar»
    panel_id: str = "voz-panel"
    tema: Path | None = None
    asr: tuple[str, ...] = ()
    ignorar: tuple[str, ...] = ()             # palabras que no interrumpen (asentir mientras habla)
    color: str = "#f4f4f2"
    publico: bool = False                     # sin autenticación (se conecta con el agent_id); decisión de Max
    # Frenos (Max, q51): orígenes permitidos (por hostname, sin puerto), corte por silencio y bursting.
    origenes: tuple[str, ...] = ()
    silencio_s: float = -1
    bursting: bool = True

    @property
    def prompt_md(self) -> Path:
        return PROMPTS / f"{self.clave}.md"


COMUN_ASR = ("WUI", "Superhuman Desk", "Matilda", "mesa", "proyector", "listo", "atrás", "repite")
ASENTIR = ("ya", "ajá", "sí", "mm", "ok", "dale", "aha", "okey")

ESCENAS: dict[str, Escena] = {
    "solar": Escena(
        clave="solar",
        agente="Superhuman Desk · Lumo",
        pestana="Sistema solar",
        prefijo="solar:",
        preset_id="solar",
        voz="8XmnJFXynxUN7hZH7q3a",            # Vale Chile («Vale» fue deshabilitada por su dueña; «Cami» no sirve en agentes)
        voces_respaldo=("6Gr4AVmTax1pMJO0lHRK", "nU2Od41FnF7WAvt16wiO"),   # Catalina, Paulina
        primer_mensaje="¡Hola! Soy Lumo, una lucecita que vive en tu mesa. ¿Armamos el sistema solar juntas?",
        turno="patient",
        velocidad=0.95,
        estabilidad=0.45,
        panel_mm=(640, 380, 340, 160),
        aviso_mm=(640, 300, 340, 64),
        tema=REPO / "escenas" / "sistema-solar" / "tema.css",
        asr=("Lumo", "Mercurio", "Venus", "Tierra", "Marte", "Júpiter", "Saturno", "Urano", "Neptuno", "Sol", "Luna",
             "órbita", "anillos", "planeta", "canasto", "lámina"),
        color="#f4f4f2",
        publico=True,                          # Max, q50 (21-nov 19:46): «lumo publico siempre»; el id no va al repo
        origenes=("localhost", "127.0.0.1", "pcx-gateway.onrender.com"),   # q51: solo nuestras páginas
        silencio_s=60,
        bursting=False,
    ),
    "lab": Escena(
        clave="lab",
        agente="Superhuman Desk · Laboratorio",
        pestana="Laboratorio",
        prefijo="lab:",
        preset_id="lab",
        voz="NDx3GTwfaeouBvRv4NrB",            # Ignacio: chileno, ritmo pausado
        voces_respaldo=("prblQcKOdF08ozhxP2mk", "GJid0jgRsqjUy21Avuex"),
        primer_mensaje="Hola, Matilda. Estoy contigo en el protocolo. Cuando quieras, partimos.",
        turno="normal",
        velocidad=0.92,
        estabilidad=0.7,
        panel_mm=(160, 490, 470, 60),          # el `lab-nota` que la escena le reserva al agente (x ≥ 150: barra de pestañas)
        aviso_mm=(650, 490, 330, 60),
        panel_id="lab-nota",
        tema=REPO / "escenas" / "laboratorio" / "tema.css",
        asr=("pastillero", "vaso", "ml", "mililitros", "muestra", "celda", "gotario", "líquido A", "B3", "D7",
             "protocolo", "dilución"),
        color="#f4f4f2",
    ),
    "escritorio": Escena(
        clave="escritorio",
        agente="Superhuman Desk · Escritorio",
        pestana="Escritorio",
        prefijo="desk:",
        preset_id="desk",
        voz="Tm8bVnJtlhPQUOb8KzSd",            # Fernando: chileno, relajado y natural
        voces_respaldo=("M1BTnEdXPMEslfxGUtu1", "V6h72IgxuOTdEifuJ9lY"),
        primer_mensaje="Aquí estoy. ¿En qué vamos?",
        turno="eager",
        velocidad=1.05,
        estabilidad=0.6,
        panel_mm=(560, 12, 300, 140),          # arriba, entre la barra de pestañas y el botón de voz
        aviso_mm=(404, 420, 176, 64),
        tema=REPO / "escenas" / "escritorio" / "tema.css",
        asr=("libreta", "endpoint", "rama", "PR", "deploy", "agente", "orquestador", "Claude", "Codex", "API",
             "migración", "revisor"),
        color="#f4f4f2",
    ),
}


def escena(clave: str) -> Escena:
    clave = {"sistema-solar": "solar", "laboratorio": "lab", "desk": "escritorio"}.get(clave, clave)
    if clave not in ESCENAS:
        raise KeyError(f"escena desconocida: {clave} (hay {', '.join(ESCENAS)})")
    return ESCENAS[clave]


def css(e: Escena) -> str:
    """base.css + tema.css de la escena (si existe), para los widgets html."""
    base = (REPO / "motor" / "sistema" / "base.css")
    out = base.read_text(encoding="utf-8") if base.exists() else ""
    if e.tema and e.tema.exists():
        out += "\n" + e.tema.read_text(encoding="utf-8")
    return out

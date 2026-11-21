"""Three discoverable effects, sharing a prepared texture and its real pixel mask."""
import importlib
from .. import vector_animation

# The runtime reloads plugins, not helpers; refresh this shared implementation
# when a plugin is reloaded so an already-running projector gets the same code.
VectorAnimation = importlib.reload(vector_animation).VectorAnimation


class VectorDrawing(VectorAnimation):
    name = "vector_drawing"
    label = "Contorno vectorial"
    mode = "drawing"
    aliases = ("vector trace", "dibujar cuadro", "outline vectorial")
    description = ("Dibuja los trazos por longitud. progress=-1 anima, 0..1 fija el avance; "
                   "progress=1 deja todos los contornos. Usa máscara real preparada, nunca el quad del objeto.")


class VectorFill(VectorAnimation):
    name = "vector_fill"
    label = "Relleno vectorial"
    mode = "fill"
    aliases = ("relleno vectorial", "pintar cuadro")
    description = "Rellena regiones por color y orden; source_colors=true conserva la paleta de la pintura."


class VectorSketch(VectorAnimation):
    name = "vector_pencil"
    label = "Boceto vectorial"
    mode = "sketch"
    params = {**VectorAnimation.params,"min_area":1200.,"max_regions":24,"hatching":False,"detail":2.}
    aliases = ("boceto", "vector_sketch", "sketch", "lápiz")
    description = ("Boceto de contornos mayores. min_area filtra regiones y huecos pequeños; max_regions limita densidad; "
                   "detail simplifica el trazo. hatching=true habilita rayado opcional con hatch_spacing.")

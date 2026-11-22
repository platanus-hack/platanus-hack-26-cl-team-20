"""Reusable variants for walls, cushions and the rabbit."""
import importlib
from .. import pixel_animation
PixelAnimation = importlib.reload(pixel_animation).PixelAnimation


class SurfaceLines(PixelAnimation):
    name = "surface_lines"
    label = "Líneas de lienzo sobre pared"
    aliases = ("pared con líneas", "pared lienzo")
    description = "Ondas orgánicas lentas guiadas por la luz de la textura limpia; spacing, width, speed y amount."
    mode = "lines"


class ContourBreathe(PixelAnimation):
    name = "contour_breathe"
    label = "Contorno que respira"
    aliases = ("cojines respirando", "respirar contorno")
    description = "Borde que respira hacia dentro de la máscara, con respuesta al bajo."


class RabbitOutline(PixelAnimation):
    name = "rabbit_outline"
    label = "Conejo con contorno y orejas vivas"
    aliases = ("conejo vivo", "orejas vivas")
    description = "Contorno animado y respiración independiente de las orejas, dentro de sus máscaras."
    mode = "rabbit"

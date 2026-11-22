"""Percepción de la escena Sistema solar: funciones puras (imagen de entrada, resultado de salida).

Todo trabaja sobre la vista cenital rectificada a la mesa, con 1 px = 1 mm (ver `rectificar`). Así los radios y los
centros salen directo en mm y el resultado sirve para `project` sin conversiones.

- `rectificar`: foto de la cámara + homografía de `look` → vista cenital en mm.
- `ubicar_canasto`: el canasto (zona con textura de mimbre), para buscar las bolas solo ahí.
- `detectar_cuerpos`: bolas de plumavit y el engranaje (el Sol), con centro y radio en mm.
- `asignar_planetas`: a cada bola su planeta por tamaño relativo, con las dudas explícitas y lo fijado a mano.
- `pose_grupo`: movimiento rígido del canasto entero entre dos cuadros (rotación + traslación), a partir de las bolas.
- `ubicar_laminas` y `planetas_en_lamina`: la lámina impresa y dónde está cada planeta dibujado.
- `espacio_libre`, `movimiento`: dónde cabe una ficha sin tapar nada, y si algo se está moviendo.
- `al_limpiar(lienzo, ctx)`: el contrato de MOTOR.md (foto limpia → objetos `solar:*`, eventos y datos).

Nada de esto toca la red ni el hardware: lo conecta el nodo de cómputo del motor (h19) o `conductor.py`.
"""

from .tipos import Asignacion, Cuerpo, Lamina, Pose
from .rectificar import rectificar, px_a_mm
from .canasto import Canasto, ubicar_canasto
from .cuerpos import detectar_cuerpos
from .planetas import asignar_planetas, cargar_catalogo
from .pose import aplicar_pose, pose_grupo
from .lamina import cargar_plantilla, lamina_a_mesa, mesa_a_lamina, planetas_en_lamina, ubicar_laminas
from .mesa import espacio_libre, movimiento
from .motor import al_limpiar

__all__ = [
    "Asignacion",
    "Canasto",
    "Cuerpo",
    "Lamina",
    "Pose",
    "al_limpiar",
    "aplicar_pose",
    "asignar_planetas",
    "cargar_catalogo",
    "cargar_plantilla",
    "detectar_cuerpos",
    "espacio_libre",
    "lamina_a_mesa",
    "mesa_a_lamina",
    "movimiento",
    "planetas_en_lamina",
    "pose_grupo",
    "px_a_mm",
    "rectificar",
    "ubicar_canasto",
    "ubicar_laminas",
]

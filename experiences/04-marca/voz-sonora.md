# Voz sonora de WUI

Estado: primera versión para evaluación. Define los cuatro sonidos cortos que acompañan la interacción. El código vive en `engine/playground/audio/wui_cues.py`; los archivos renderizados están en `engine/playground/audio/cues/`.

## Para qué sirve el audio

La señal visual pinta sobre el objeto y la mesa. El sonido confirma que la señal ocurrió sin pedir que la persona mire al anclaje. Funciona como refuerzo táctil-auditivo: la vista está donde está la tarea, el oído confirma el paso. Por eso cada cue es corto, monocromático (una sola nota perceptual) y silencioso al terminar.

## Los cuatro cues

| Cue | Momento del paso | Duración | Timbre | Cuándo se dispara |
| --- | --- | --- | --- | --- |
| `aparicion` | Entra la señal | 0,42 s | Tap suave, glide 880 → 660 Hz, aire tenue | Al spawn de una señal de Punto o al inicio de la pista |
| `guia` | La pista se asienta | 0,88 s | Sostenido 440 Hz con vibrato 5,5 Hz y aire | Cuando la pista termina de formarse y el texto queda legible |
| `confirmacion` | Resultado positivo del paso | 0,56 s | Dos tapitos, segundo medio tono más alto (C5 → D5) | Al detectar que el objeto quedó donde debía (p. ej. «Pieza colocada») |
| `recuperacion` | WUI pide que vuelva el objeto | 1,20 s | Dos pulsos suaves a 330 Hz | Al perder el objeto durante un paso activo |

Los tiempos están alineados con la paleta de movimiento de la lámina 09 (0,42 · 0,88 · 1,28 · 1,72 s). `aparicion` y `guia` reciclan los dos primeros tiempos; `confirmacion` y `recuperacion` usan tiempos propios porque pertenecen a estados y no a la aparición de la tinta.

## Reglas

- Un solo cue a la vez. Si un cue nuevo se dispara mientras otro sigue sonando, el saliente se desvanece en 40 ms y arranca el nuevo. Nada se superpone.
- Volumen RMS consistente. Todos los cues se normalizan con −3 dB de headroom para evitar clipping en parlantes de sede.
- Sin melodía reconocible. Los cuatro cues se pueden identificar por timbre y duración, no por una secuencia de notas memorable. La marca es monocromática también en audio.
- `confirmacion` es el único cue con dos eventos consecutivos. Marca un cierre.
- `recuperacion` es el único cue con pulso repetido. Marca una espera activa.
- Ningún cue pasa de 1,20 s. Si un paso dura más, el silencio es parte del lenguaje.

## Lo que no debe pasar

- No agregar reverb saturada ni delay largo; la sede tiene luz ambiente y el sistema trabaja sobre la mesa, no sobre un auditorio.
- No usar `guia` para estados equivocados (p. ej. al perder el objeto), rompe la asociación aprendida.
- No usar música de fondo. El silencio entre cues es parte de la voz.
- No reproducir cues desde el navegador del dashboard del operador. El audio sale por el altavoz físico que está junto a la mesa.

## Cómo se usa desde el motor

```python
from playground.audio.wui_cues import play

play("aparicion")       # no bloquea; devuelve el Popen por si hay que matarlo
play("guia", blocking=True)
```

El selector elige el reproductor según plataforma: `afplay` en macOS, `paplay` o `aplay` en Linux. Si el host no tiene ninguno disponible, `play()` retorna `None` sin romper, útil para test unitarios.

Para engancharlos a eventos del `Effect`, la propuesta inicial es llamar `play("aparicion")` dentro del primer `update` del efecto `touch_marks`, `play("guia")` al finalizar la envolvente de aparición de `contour_glow`, `play("confirmacion")` cuando `scene.state` marca un paso completado, y `play("recuperacion")` cuando el objeto desaparece durante un paso activo. Ese enganche se discute con Max y Manu antes de escribir código en `scene.py`.

## Parámetros técnicos

- Sample rate: 44 100 Hz, mono, 16-bit PCM.
- Formato en disco: WAV sin comprimir. Pesos actuales: aparicion 36 kB, guia 76 kB, confirmacion 43 kB, recuperacion 103 kB.
- Síntesis pura numpy + módulo `wave` de Python. No depende de `sounddevice`, `pyaudio` ni bibliotecas con drivers.
- Regeneración: `python -m playground.audio.wui_cues`. La rutina reescribe los cuatro WAVs con seed fijo, así que el resultado es determinista entre máquinas.

## Pendientes

- Falta la lámina 10 del brandbook en Figma con la tabla de cues, el diagrama de tiempos y un reproductor embebido.
- Falta el hook en `scene.py` o `showcase.py` para disparar `play(cue)` desde eventos reales. Se decide con Max y Manu antes de implementarlo.
- Falta una prueba A/B en la sede: comparar un paso con cue y el mismo paso sin cue, mirando si el gesto del usuario llega antes al objeto.

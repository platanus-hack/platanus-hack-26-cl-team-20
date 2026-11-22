# Cómo entra WUI a lo que proyecta el engine

Estado: propuesta para Pablo. Describe dónde insertar la identidad WUI en el motor sin reescribir efectos ni bloquear el trabajo de Max y Manu. Pendiente de aprobación.

## Lectura actual del motor

Cada efecto en `engine/playground/effects/*.py` declara sus propios `params`. El color aparece como `[r, g, b]` en RGB, el ancho como `width_mm`, y el ritmo como `speed`, `beat_sync` o constantes internas. El dibujo final pasa por `fxdraw.py` con cuatro primitivas: `halo`, `outline`, `fill`, `keys`. El lienzo trabaja en milímetros de la mesa y convierte a píxeles del proyector. No hay paleta central, ni curvas de movimiento compartidas, ni correspondencia explícita con Punto / Línea / Arco / Contorno de la lámina 06.

Hoy esto se puede leer como diez marcas distintas conviviendo en la misma mesa. La adaptación consiste en darle al motor un vocabulario corto, poner los efectos a leer de ese vocabulario y dejar que cada uno elija dentro de un rango acotado.

## Qué se agrega al repo

Tres piezas pequeñas, en el mismo `engine/playground/`. Ninguna cambia la API pública de `Effect` ni rompe la galería.

### 1. `effects/wui_tokens.py`

Módulo nuevo con los valores canónicos de la marca.

- Paleta monocromática expuesta en RGB: `TINTA`, `GRAFITO`, `GRIS_MEDIO`, `NIEBLA`, `LUZ`.
- Semántica de señal: `SEÑAL_ACTIVA = LUZ`, `SEÑAL_LATENTE = GRIS_MEDIO`, `SEÑAL_ERROR = TINTA` sobre fondo claro invertido cuando el estado lo pida.
- Formas canónicas de la lámina 06 como constantes: `FORMA_PUNTO`, `FORMA_LINEA`, `FORMA_ARCO`, `FORMA_CONTORNO`. Cada una con `radio_mm`, `grosor_mm` y `dash` opcional. Son recomendaciones, no clases.
- Tipografía proyectada: `TIPO_INSTRUCCION`, `TIPO_CONFIRMACION`, `TIPO_ETIQUETA`, con familia (Instrument Sans), peso y tamaño en milímetros de mesa.
- Jerarquía de contrastes aprobada para proyección, calculada sobre fondo oscuro de sala.

### 2. `effects/wui_motion.py`

Curvas y tiempos derivados de la lámina 09.

- `TIEMPOS_TINTA = (0.42, 0.88, 1.28, 1.72)` segundos, exportado como diccionario con nombre: `entra`, `ondula`, `forma`, `asienta`.
- `ease_tinta_asienta(t)` implementa el `cubic-bezier(0.16, 1, 0.3, 1)` de la regla de texto.
- `pulso_estado(t, estado)` devuelve una envolvente para el estado del paso: `apareciendo`, `activo`, `recuperacion`, `completado`. Cada estado tiene amplitud y duración distintas.
- `ondulacion_trazo(t, largo_mm, crestas=2)` para la regla de texto: produce desplazamientos verticales acotados a una fracción del ancho de línea del wordmark.

### 3. `effects/wui_preset.py`

Mezclador que mapea el vocabulario anterior al `params` de cada efecto.

- Funciones `preset_punto()`, `preset_linea()`, `preset_arco()`, `preset_contorno()` que devuelven un diccionario parcial de `params` listo para pasar a cualquier efecto compatible.
- Función `aplicar_wui(efecto, forma, estado)` que inyecta color, grosor y ritmo según la forma y el estado. Deja intactos los `params` propios del efecto (audio, beat_sync, segmentación).
- Un registro `COMPATIBILIDAD` con qué efectos admiten cada forma. `contour_glow`, `control_glow` y `marco_respira` son los primeros candidatos de Línea y Contorno; `touch_marks` y `bass_ripple` son los candidatos de Punto; la lista crece cuando mapeemos más.

## Qué se cambia dentro de los efectos

Un cambio pequeño por efecto, no una reescritura. Cada efecto conserva su firma y su `params`, pero los valores por defecto se calculan llamando a `wui_preset`. Un operador puede seguir sobreescribiendo cualquier parámetro desde el dashboard. El beneficio es que al spawnear un efecto nuevo sobre un objeto, sus defaults ya hablan el idioma de la marca.

Secuencia sugerida:

1. `contour_glow` como forma Contorno, con grosor de línea del wordmark y color Niebla.
2. `touch_marks` como forma Punto, con `SEÑAL_ACTIVA` y ritmo `pulso_estado(_, 'apareciendo')`.
3. `control_glow` como forma Arco, con el mismo trazo residual que la pista de la lámina 07.

Si los tres funcionan en el montaje, mapeamos el siguiente lote. Si no, corregimos el preset antes de tocar más efectos.

## Qué queda fuera de este alcance

- Motion para el logotipo proyectado al abrir WUI sobre la mesa; se puede derivar del mismo módulo pero depende de calibración y no es parte de esta propuesta.
- UI del dashboard del operador (`dashboard_ui/`). Esa es otra reskin, con otro tiempo y otros usuarios; no entra acá.
- Landing del repo privado. Va por el segundo agente cuando tengamos el clon local.
- Efectos de audio que dependen de color saturado (`leopardo_fucsia`, `ddj_skin`, `juego_memoria`). La marca puede vivir con una actividad coloreada cuando el paso lo pida; en ese caso el efecto queda etiquetado como «actividad», no como señal del sistema.

## Riesgos y cómo mitigamos

- El motor está en desarrollo activo. Introducir módulos nuevos sin coordinar con Max y Manu puede chocar con cambios en marcha. Mitigación: antes de merge, muestro los tres módulos y el primer efecto remapeado como PR aislado y les pido feedback de treinta minutos.
- Proyección sobre fondos variables rompe contrasts previstos. Mitigación: el token `SEÑAL_ACTIVA` no asume un fondo único; `wui_preset` elige grosor y halo según la luz medida en la escena si está disponible, y conserva un fallback alto contraste.
- Instrument Sans como fuente proyectada pesa el render. Mitigación: cacheamos glifos en un atlas al cargar el efecto si llegamos a usar tipografía en proyección directa; si no, se queda en la UI del operador y en la pista de Figma.

## Próximo paso

Si apruebas:

1. Escribo los tres módulos nuevos en `engine/playground/effects/` sin tocar efectos existentes.
2. Mapeo `contour_glow` como primer caso y lo pruebo sintéticamente con el runtime en modo `sin runtime` del dashboard.
3. Muestro el diff a Max y Manu antes de seguir con los otros dos.

Si rechazas o quieres otro recorte, lo anoto y lo cerramos antes de tocar código.

# Rendimiento de los efectos preparados

La segmentación y el vectorizado son offline. El runtime consume máscaras,
texturas y bundles ya preparados; no ejecuta SAM ni vuelve a trazar la foto.

El provider de apariencia conserva rutas resueltas y validaciones por versión de
archivo (mtime nanosegundos, tamaño, inode y dispositivo). Sigue verificando las
versiones en cada consulta y rechazando assets fuera del catálogo o con hashes
incorrectos. Cambiar el manifiesto, una máscara o una textura invalida la caché.

Los efectos de píxeles aceptan `resolution=0.5` por defecto, limitado a 384 px en
el lado mayor del raster de trabajo. `resolution=1` aumenta el raster de objetos
pequeños; el límite de 384 permanece. Se escala el resultado y se recorta con la
máscara nativa antes de componer: bajar el raster no amplía el objeto ni rellena
sus huecos. Las texturas de tigre precalculan forma, color, sombreado y ancho de
las rayas; solo animan su fase. Los vectores conservan sus capas y trazos completos
ya rasterizados. La tela conserva su simulación y renderer acotados anteriores.

`scene_regions` recarga el helper de apariencia al hacer hot reload. Los efectos
conservan IDs y parámetros. El arreglo de `EffectRegistry.changed()` evita que
un archivo que desaparece momentáneamente entre `glob` y `stat` durante un
merge/guardado mate el render. Las revisiones de plugins quedan dentro del lock
del runtime. Este último cambio requiere reiniciar el proceso: recargar plugins
no sustituye una instancia existente de `Runtime`.

## Medición

Usar una composición fija, 1920×1080 y dt=1/30; calentar 5 cuadros, medir
`update` y `draw` por separado durante 30 cuadros y luego 60 ticks completos sin
profiler. El primer cuadro incluye lectura/validación/preparación y no representa
el costo sostenido. El profiler se ejecuta en una pasada aparte de 10 cuadros.
No ejecutar pytest o varios benchmarks simultáneos con la medición final.

La medida offline es capacidad de render, no fps efectivos del proyector. En
vivo, tomar el delta de `stats.ticks` dividido por tiempo monotónico durante al
menos 5 segundos; verificar también instancias vivas, errores y que los ticks
avancen. Un `stats.fps` antiguo puede quedar congelado si el hilo ya murió.

Composición de 12 para el benchmark: provider, contorno del cuadro, tela, dos
orejas de tigre, cuerpo leopardo, máscara viva, tres paredes orgánicas, lápiz de
la columna y respiración de un cojín. La composición reducida usa los primeros
siete. Evidencia privada: `reports/scene_regions/<scan>/performance-*.json`,
`performance-composition-12.json` y `benchmark_effects.py`. La tabla incluye ms por efecto,
media/p95 del tick y fuente de la composición.

Pruebas: recorte nativo con resolución reducida, cambio de resolución sin
reiniciar, invalidación de assets/cache, rechazo de symlink fuera del catálogo,
animación de rayas/orejas, y reemplazo de plugin entre glob/stat sin perder el
render. Las pruebas generales del runtime y de los demás efectos siguen activas.

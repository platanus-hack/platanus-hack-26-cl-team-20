# Escena automática después de `dale`

Desde la raíz del repo, con el runtime 8790 ya cargando el scan nuevo:

```sh
.venv/bin/python -m playground.auto_scene --semantic-python /opt/homebrew/bin/python3.14 --activate
```

`dale` sigue siendo responsable de calibrar, escanear y cargar la geometría nueva.
Este comando toma `scene.scan_dir` del runtime, prepara la escena una vez en CPU y
activa sus efectos. No abre cámaras ni ventanas. El runtime necesita arrancar con
la versión que incluye `activate_prepared`; el hot reload de efectos no añade
comandos a un proceso anterior.

Para preparar sin cambiar la proyección, pasar el directorio y omitir `--activate`:

```sh
.venv/bin/python -m playground.auto_scene captures/<scan> --semantic-python /opt/homebrew/bin/python3.14
```

Se puede reintentar únicamente la activación usando `--prepared <prepared.json>`
junto a `--activate`. `--runtime`, `--models` y `--max-objects` son configurables.
El límite por defecto es 10 objetos; acepta 1–24.

## Qué prepara

1. Apariencia limpia a partir de las correspondencias del scan, o `scene_pov.png`
   si faltan los mapas. Conserva los originales y hashes de procedencia.
2. MobileSAM con grilla automática de 24×14 puntos, sin cajas escritas a mano.
   Filtra confianza, estabilidad y duplicados. Conserva todas las propuestas
   aceptadas y una vista numerada, aunque la composición use menos objetos.
3. Grounding DINO tiny local propone categorías genéricas y cajas. MobileSAM
   produce la forma final de esas detecciones. Las descripciones contradictorias
   de una misma región quedan sin etiqueta. Sin clasificador disponible, usa
   máscaras automáticas con el tipo `unknown` y deja la causa en el reporte.
4. Selecciona máscaras disjuntas, priorizando objetos identificados con mayor
   confianza sobre fondos. Los IDs dependen del scan y de la máscara, sin nombres,
   coordenadas ni IDs heredados de la escena anterior. Vectoriza cada máscara.
5. Prepara una composición: dibujo progresivo para cuadros, líneas suaves para
   paredes, pulso para máscaras decorativas, lápiz para columnas y respiración de
   contorno para cojines, peluches, plantas y objetos desconocidos. Las plantas
   tienen contorno verde. Estos presets se pueden ajustar después en el runtime.

Los segmentos pueden incluir partes de un objeto y regiones de una pintura; el
conteo no equivale a cantidad de objetos físicos. Las etiquetas son hipótesis,
no identificaciones verificadas por una persona. `objects.png` y `objects.json`
permiten revisar la selección. El fallback conserva regiones desconocidas en vez
de inventar nombres.

## Activación y evidencia

Todo queda bajo `reports/scene_regions/<scan>/auto-<fecha>-<id>/`: apariencia,
`all-segments.png`, `all-projection.png`, máscaras automáticas, `semantic.json`,
`segmentation.json`, máscaras seleccionadas, bundles, `objects.png`,
`projection.png` y `prepared.json`.

La preparación no sustituye el catálogo activo. `activate_prepared` valida hashes,
rutas, scan actual, tamaño nativo y ausencia de overlay activo. Construye y dibuja
la composición antes de reemplazar los efectos; si falla, restaura el manifiesto
y mantiene las instancias anteriores. Repetir el mismo artefacto es idempotente.
Una escena métrica se rechaza: esta cadena produce máscaras en píxeles registrados
del proyector. Audio, controles y desplazamiento de salida se conservan.

## Dependencias locales

No instala paquetes ni descarga modelos al ejecutar. MobileSAM usa
`~/hardware/models/slscan-segmentation/{mobile_sam_image_encoder.onnx,sam_mask_decoder_single.onnx}`
y ONNX Runtime, instalado en el entorno de preparación o en el subdirectorio
`python/` de esa carpeta. NumPy, OpenCV y SciPy ya son dependencias del proyecto.

Las etiquetas corren en un subproceso separado: `--semantic-python`, o
`SL_SCAN_SEMANTIC_PYTHON`, o `python3` del PATH. En este Mac reutilizan Torch y PIL
del Python 3.14 del sistema, Transformers 4.57.6 y dependencias pequeñas en
`~/hardware/models/slscan-segmentation/semantic-python/` (103 MiB). El checkpoint
Grounding DINO tiny ya existía en la caché Hugging Face; no se descargaron pesos.
El worker exige archivos locales y modo offline. No añade Torch al entorno del
runtime. Tras preparar, el renderer sólo consume PNG y bundles vectoriales.

Pruebas: grilla sin prompts de objetos, deduplicación, selección disjunta,
ambigüedad semántica, fallback al fallar etiquetas, activación/idempotencia,
rollback, rechazo de scan/hash/tamaño/overlay incorrectos y recorte nativo.

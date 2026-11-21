# Apariencia limpia y regiones vectoriales

Preparación offline para escenas legacy en píxeles del proyector. No produce geometría métrica,
no modifica capturas, mapas, rig ni `dale`. Los efectos consumen cachés; nunca corren SAM por frame.

`playground.appearance_prepare clean SCAN OUTPUT` vuelve a muestrear la foto original usando
correspondencias graycode con consenso local. Conserva la traslación del mapa existente, marca
soporte e interpolación y solo rellena huecos chicos. Las zonas fuera del soporte quedan negras.
El resultado es una mejora visual, no una nueva calibración validada físicamente.

```sh
python -m playground.appearance_prepare clean captures/ESCENA reports/scene_regions/ESCENA/clean-v1
python -m playground.appearance_prepare segment reports/scene_regions/ESCENA/clean-v1/appearance.png prompts.json reports/scene_regions/ESCENA/masks-v1 --models /ruta/externa/modelos --backend cpu
python -m playground.appearance_prepare publish reports/scene_regions/ESCENA reports/scene_regions/ESCENA/clean-v1/appearance.png reports/scene_regions/ESCENA/masks-revisadas labels.json --vectors vectors-v1
```

La preparación opcional usa los exports ONNX de [Acly/MobileSAM](https://huggingface.co/Acly/MobileSAM)
(MIT): encoder 26,9 MiB y decoder 15,7 MiB. ONNX Runtime se instala fuera del repo y se agrega al
PYTHONPATH solo al preparar. CPU funciona; CoreML se puede solicitar explícitamente, sin asumir
que todo el modelo corre en GPU. No es una dependencia del runtime.

`prompts.json` mapea ids a `box: [x0,y0,x1,y1]`, `positive: [[x,y],...]` y `negative: [[x,y],...]`.
Las cajas guían al modelo; no son máscaras. Revisar cada PNG antes de publicar. Los objetos
ocluidores deben restarse de los de atrás; orejas pueden ser máscaras hijas del conejo.
`labels.json` mapea ids a `name`, `kind`, `aliases`, `texture` y `method` descriptivos.

`manifest.json` vincula hashes de fotos/mapas originales, textura limpia, máscaras y bundles.
Los assets deben quedar bajo el catálogo. Una fuente o máscara cambiada produce error explícito,
sin aplicar silenciosamente una máscara desalineada. Cada nueva publicación usa otra carpeta
vectorial y reemplaza el manifiesto atómicamente. Fotografías, modelos y máscaras son privados y
no van en git. Guardar prompts y revisiones junto al catálogo privado.

## Runtime

Activar `scene_regions` sin target para exponer los objetos preparados, también después de
`reload_scene`. Este provider no dibuja. `scene_texture`, `tela_viva` y los vectores resuelven
la misma textura limpia con `appearance.texture_path`; tela y vectores toman además el bundle
del objeto, vinculado a esa textura. `texture_source` en su estado permite auditar la ruta efectiva.
Sobre un target segmentado, el recorte de la textura es por píxel y sin halo exterior.

| Efecto | Uso | Parámetros principales |
|---|---|---|
| `vector_drawing` | Contornos progresivos | `speed`, `width`, `color`, `order`, `progress`, `audio` |
| `vector_fill` | Rellenos progresivos | `source_colors`, `order`, `progress`, `intensity` |
| `vector_pencil` | Contornos mayores, sin rayado por defecto | `min_area=1200`, `max_regions=24`, `detail=2`, `hatching=false`, `jitter`, `width`, `progress` |
| `vector_blobs` | Colores grandes con hue y bloom | `min_area`, `hue_speed`, `bloom`, `bloom_radius`, `min_saturation`, `audio`, `audio_gain` |
| `surface_lines` | Ondas lentas guiadas por la luz, sin grilla | `spacing`, `width`, `speed`, `amount`, `color` |
| `contour_breathe` | Borde de cojines que respira hacia dentro | `width`, `amount`, `speed`, `audio` |
| `rabbit_outline` | Contorno vivo y orejas independientes | `width`, `amount`, `speed`, `color`, `audio` |
| `mask_pulse` | Rellenos de máscara azteca pulsantes | los de blobs más `pulse_speed` |

`min_area` usa píxeles de la textura original; `hue_speed` son vueltas/segundo. El bloom se calcula
a resolución reducida y se recorta al final contra la máscara nativa. Colores de parámetros son
RGB. `progress=-1` anima, `progress=1` muestra todos los vectores. `audio="bass"` sigue el bajo;
`audio=""` desactiva la respuesta. Todas las variantes respetan huecos y máscara de objeto.
En pencil, `min_area` también elimina agujeros pequeños del trazo y `detail` simplifica en píxeles;
`hatching=true` recupera el rayado opcional. Las ondas de pared siguen la iluminación suavizada
de la apariencia limpia, sin convertir la textura de piedra ni sus detalles en una grilla.

Los vectores rechazan transformaciones del canvas y nudges posteriores: hay que volver a preparar
la alineación. Bordes y siluetas de malla 3D quedan para el siguiente encargo, separado del modo legacy.

Validación: tests sintéticos de correspondencias espurias, hashes, agujeros, límites y respuesta
al audio; renders de los assets reales se guardan junto al catálogo como evidencia privada.

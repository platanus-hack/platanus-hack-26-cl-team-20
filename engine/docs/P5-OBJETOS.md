# P5 · Objetos genéricos sobre malla

Entrega h10, orquestador Creativo. Solo procesamiento local, sin abrir cámara,
proyector ni ventanas. No instala modelos ni envía imágenes a servicios externos.

```sh
# Desde el worktree, usando el entorno existente del proyecto:
PY=/Users/max/hardware/sl-scanner/.venv/bin/python

# Sintético completo: ejecuta el segmentador geométrico P5 real sobre la malla fixture.
$PY -m slscan pipeline run /tmp/p5-synthetic-source --synthetic \
  --out reports/pipeline/p5-demo

# Productos métricos P3. Salida nueva, fuera de la carpeta de entradas.
$PY -m slscan objects /ruta/p3/mesh.json --rig /ruta/rig.json \
  --camera osmo --out reports/pipeline/objetos-01

# Agregar predicciones SAM/VLM previamente calculadas en el PC:
$PY -m slscan objects /ruta/p3/mesh.json --rig /ruta/rig.json \
  --camera osmo --predictions /ruta/predicciones/predictions.json \
  --out reports/pipeline/objetos-02
```

Si `mesh.json` no tiene `rig_sha256`, se verifica la cadena mesh → cloud → rig.
`--cloud /ruta/cloud.json` permite leer el cloud de otra generación/directorio P6.
Mundo, evidencia, tamaños, hashes, índices y assets se verifican antes de publicar.
No se aceptan predicciones de otra foto, resolución o cámara. Las entradas se leen
sin modificar; la salida debe estar vacía y fuera de la carpeta de la malla.

En P6, `segment` depende de `mesh` y `validate_rig`. Los productos `objects.json`
importados mantienen su camino anterior y exigen los hashes de ambas dependencias.
Si no hay producto importado, se ejecuta P5. Un bundle opcional se busca en
`scan/products/segment/predictions.json`, con todos sus PNG relativos en esa carpeta;
P6 hashea esos archivos para invalidar caché. Los parámetros entran por
`Runner(options={"objects": {"noise_mm": 0.5}, "object_camera": "osmo"})`.

## Geometría y visibilidad

- RANSAC sobre centroides 3D, umbral `noise_mm × plane_sigma` en mm; orientación
  libre, sin asumir mesa horizontal. Por defecto 0,5 × 3 mm es un parámetro de
  ingeniería, **no ruido medido**; ajustarlo a la incertidumbre de P3.
- Componentes por aristas compartidas, continuidad de normales y contraste RGB
  local simétrico entre colores. Las costuras UV se unen por coordenadas idénticas;
  no se unen superficies cercanas que no comparten arista. No hay reglas de color
  o posición que reconozcan objetos históricos.
- Las máscaras SAM se asignan solo a caras visibles en cámara. Cada píxel se
  desdistorsiona con K/dist calibrados y se prueba contra los triángulos con
  profundidad perspectiva; la cara cercana gana. Las máscaras pequeñas tienen
  prioridad sobre máscaras envolventes de SAM; no hay doble pertenencia de caras.
- La pertenencia exige al menos 60% de los píxeles visibles de una cara dentro de
  la máscara. La resolución del recorte queda limitada por los triángulos: no se
  subdividen caras que cruzan un borde SAM. Los componentes pueden dividir una
  instancia SAM en varios parches por desconexión o discontinuidad geométrica.
- Se repite visibilidad desde el proyector con su propia pose y distorsión. El
  PNG binario es la máscara autoritativa. `projector_regions_px` conserva islas y
  huecos; `projector_contour_px` es solo la isla mayor para consumidores anteriores.
- Se rechaza inversión de distorsión con residuo mayor a 0,001 px. Caras detrás del
  lente o que lo cruzan quedan omitidas y contadas. No se completan dorsos, huecos
  de reconstrucción ni objetos ausentes. Todos los objetos son `partial` o
  `unobserved`, nunca una afirmación de objeto físico completo.

Los IDs incluyen el hash de la malla y las caras: son estables en esa generación,
no son tracking entre escaneos. Cambiar de escena no hereda etiquetas ni efectos.
Sin SAM, salen parches geométricos `unknown`; separar forma no equivale a reconocer
el objeto. El umbral de borde RGB puede subdividir una textura detallada.

## Worker del PC y semántica

En el PC, con el código disponible y pesos **ya locales**:

```sh
python -m slscan.object_worker C:/scan/color.png --camera osmo \
  --checkpoint C:/models/sam_vit_b.pth \
  --vlm-path C:/models/Qwen2.5-VL-3B-Instruct \
  --out C:/reports/predicciones-01
```

El worker usa [SAM AutomaticMaskGenerator](https://github.com/facebookresearch/segment-anything)
en CUDA. Libera SAM antes de cargar el VLM. El adaptador visual usa
[Qwen2.5-VL en Transformers](https://huggingface.co/docs/transformers/model_doc/qwen2_5_vl),
solo directorio local, `local_files_only=True`, modo offline y sin código remoto.
Necesita torch CUDA, torchvision/segment_anything y, para semántica, transformers/Pillow
compatibles en el PC; no se instalaron ni comprobaron aquí. La configuración 3B
es una candidata para probar, no un benchmark de memoria/calidad de la RTX 4070.
No hay traslado SSH automático ni descarga de pesos al Mac.

El VLM recibe un recorte con fondo neutral fuera de la máscara, en memoria. Devuelve
JSON limitado: tipo, material aparente, superficie, descripción, confianza y efectos
sugeridos del catálogo. Una respuesta inválida deja `unknown` y registra el fallo;
un error de ejecución/modelo falla el worker sin simular una respuesta. Hasta 128
propuestas por corrida; las omitidas se cuentan en el bundle. Sin `--vlm-path`, SAM
funciona pero no se inventan etiquetas. Los materiales visuales no son medidas PBR.

Jev, según la memoria local de ReUp indicada en el encargo, es el decisor entre
opciones tipadas. La división propuesta es **VLM local describe → catálogo filtra
por capacidades → Jev puede elegir entre esas rutas**. Esta entrega no llama Jev:
no hay autorización para enviar fotos de la casa a la nube. La selección local
determinista del catálogo sí funciona sin él.

`StubSegmenter` inyecta máscaras sintéticas para probar el contrato completo. Está
prohibido publicarlo con evidencia grabada/en vivo. Para corregir una etiqueta
localmente, editar únicamente `semantic` del bundle y marcar `semantic_model` como
`manual-local`; volver a segmentar en otra salida. La imagen y las máscaras siguen
ligadas por hash. Ninguna corrección semántica modifica pose, escala o contornos.

## Contrato de efectos para P4

`objects.json` sigue versión 1.0, con campos opcionales validados en el esquema:
`semantic`, `capabilities`, `effect_routes`, `projector_mask`, `projector_regions_px`
y `geometry`. El nuevo contrato `object_predictions` describe la entrada del worker.
`face_indices`, `bbox_mm`, `plane`, confianza/observación y hash de mesh siguen vigentes.
La confianza SAM y la semántica están separadas; ninguna acredita precisión métrica.

| Tipo/superficie | Ruta | Capacidades requeridas |
|---|---|---|
| Todos, incluido unknown | `surface_light` | mesh |
| Cuadro / pantalla | `flat_texture` | mesh, textured, planar |
| Planta | `foliage` | mesh, normals |
| Pared lisa / papel | `canvas` | mesh, textured, planar |
| Pared con textura | `relief` | mesh, normals |
| Jarrón | `ceramic_glaze` | mesh, normals |
| Libro | `page_texture` | mesh, textured |
| Cojín / sofá | `fabric_wave` | mesh, textured |

Cada ruta declara operación (`uv_texture`, `normal_light`, `scene_lighting` o
`material_modulation`), caras de destino, máscara, coordenadas mesh UV/world mm y
`changes_geometry=false`. Son **descriptores para el renderer P4**, no plugins 2D ni
shaders nuevos ejecutados por esta entrega. P4 debe enlazar sus operaciones a las
caras/UV de la malla identificada por hash y recortar usando la máscara visible.
El tipo solo se usa con confianza semántica ≥0,6, umbral no calibrado empíricamente;
los efectos sugeridos también se filtran por las capacidades realmente disponibles.

`scene_from_scan` prioriza `objects.json` incluso si está vacío. `scene_from_objects`
permite apuntar al directorio publicado por P6. Bbox/plano quedan en mm; el contorno
legado cenital queda vacío, y caras, máscaras y rutas van en `SceneObject.extra`.
`Scene.coordinate_space="world_mm"` bloquea nudges, offsets y spawn de efectos del
Canvas 2D. El runtime no agrega el OHM ficticio, no lee `salida.json` y limpia efectos
heredados al recargar una escena métrica. La proyección 3D corresponde a P4.

## Verificación

Pruebas específicas/integración: **56 passed** (`test_objects`, `test_pipeline`,
`test_playground`). Incluyen dos planos a profundidades distintas, rotación del mundo,
superficies ocultas, distorsión contrastada con intersecciones rayo/plano independientes,
máscaras solapadas, huecos/islas, rig/imagen/máscara obsoletos, ocho rutas semánticas,
contratos, caché y recarga sin objetos heredados. Stub SAM/VLM sobre las dos superficies:
IoU de máscara **1,0 por superficie** contra las máscaras de verdad sintética.
Esto prueba transporte/recorte con máscaras conocidas, **no precisión de SAM**.

La corrida CLI sintética está en `reports/pipeline/p5-demo/pipeline.json`, fuera de
git (112 KB). Máscara de proyector sintética inspeccionada visualmente. No se
generaron fotos privadas ni se copiaron capturas. Se reutilizó `.venv` del proyecto;
no se instalaron paquetes.

Suite completa ejecutada una vez al cierre:
`/Users/max/hardware/sl-scanner/.venv/bin/python -m pytest tests -q -p no:cacheprovider`:
**323 passed, 3 skipped, 2 warnings, 62,74 s**. Skips de fixtures locales de escenas
no copiadas al worktree; warnings existentes de deprecación websockets/ElevenLabs.
`git diff --check`, compilación de los módulos nuevos y ayuda de ambos CLI sin errores.

**No verificado:** SAM CUDA y VLM con pesos reales, rendimiento/calidad en el PC,
dos capturas físicas incluida la escena nueva, IoU de objetos reales y borde máximo
≤2 mm. Al revisar `captures/` no había rig.json ni mesh.json métricos. La fase física
requiere esos productos P2/P3 y la prueba de contornos negros sobre blanco de P4.

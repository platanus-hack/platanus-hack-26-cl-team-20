# P6 · Contratos y pipeline local

Trabajo sin hardware. No abre cámara, proyector, navegador ni ventanas. Los scans son
solo lectura; `--out` debe quedar fuera del scan. Nada sale de la máquina.

```sh
python -m slscan pipeline run /tmp/demo-slscan --synthetic --out reports/pipeline/demo
python -m slscan pipeline serve reports/pipeline/demo --port 8796
# Abrir por separado http://127.0.0.1:8796 cuando corresponda.
python -m slscan pipeline run /ruta/captures/scan5 --out reports/pipeline/scan5
python -m slscan pipeline schema rig
python -m slscan pipeline validate /ruta/rig.json
```

El entrypoint existente es `python -m slscan`; si el ambiente tiene el alias `slscan`,
los argumentos son idénticos. Usa NumPy/OpenCV/SciPy y JSON Schema/FastAPI/Uvicorn del
entorno existente. No requiere Open3D, modelos descargados ni dependencias de frontend.

## Contratos v1.0 para P2–P7

Fuente canónica: `slscan.contracts.SCHEMAS`, JSON Schema draft 2020-12, exportable con
`pipeline schema <artefacto>`. Se permiten extensiones de campos; se rechazan versiones
desconocidas, campos obligatorios ausentes, NaN e infinito. `validate(data, kind)` valida
JSON y coherencia geométrica. `read_json(path, kind)` y `atomic_json(path, data)` leen y
publican documentos. `header(kind, evidence, units, input_hashes)` da los campos comunes:

```python
from slscan import contracts as c
base = c.header('rig', 'recorded', 'mm', {'calibration.json': c.digest(calibration_path)})
# Añadir campos de SCHEMAS['rig']; luego c.atomic_json(out, data).
```

Campos comunes: `schema_version="1.0"`, `artifact`, `evidence` (`synthetic`, `recorded`,
`live`), `units` (`mm`, `px`, `relative`) e `input_hashes` (nombre → SHA-256 de bytes).
`evidence` describe el origen del artefacto, no una garantía de precisión. Procesar una
foto histórica no la convierte en prueba física vigente. Hashes se calculan por streaming.

| Artefacto | Contenido y consumidor |
|---|---|
| `manifest.json` | Sesión, revisión, `rig_sha256`, mundo, dispositivos con tamaño/rotación/downscale efectivos y estado de exposición/WB/foco, patrones y timestamps. P2/P3 producen, todos consumen. `acquisition_known=false` bloquea compatibilidad métrica. |
| `rig.json` | `format="slscan-rig/2"`; dispositivos por nombre con K, dist OpenCV, R/t y `T_device_from_world` 4×4 consistentes, pose estimada, parámetros libres/fijos, incertidumbre, residuos por vista, rango y estado métrico. Mantiene los campos que lee `Rig.load`. P2 produce. |
| `matches.npz` | Metadata JSON `artifact="matches"`; arrays descritos abajo. P3 produce/consume. |
| `cloud.json` | Referencias SHA-256 a `cloud.ply` y `cloud_quality.npz`; hash del rig/matches, mundo, cantidades y cobertura. P3 produce. |
| `mesh.json` | Referencias a `mesh.ply`, `mesh.npz` y textura opcional; hash de cloud, UV, cantidades y `texture_kind` (`appearance`, `albedo`, `none`). P3 produce. |
| `scene.json` | Mundo persistente, dispositivos completos, hash del rig, referencias a mesh/materials, conversión explícita de ejes, estado y rango métrico. P4 produce. |
| `objects.json` | Hash de mesh, cantidad de caras e IDs genéricos con índices, contornos en proyector, bbox mm, plano opcional y confianza/observación. P5 produce. |
| `radiometry.json` | Estados, respuesta por canal, mezcla RGB, saturación, gamut y referencia; puede describir archivos bajo `radiometry/` por assets. P7 produce. |
| `lights.json` | Estados y luces con posición mm o solo dirección, intensidad relativa, temperatura opcional, confianza e incertidumbre. P7 produce. |
| `materials.json` | Hash mesh/UV; mapas albedo/normal/roughness/specular/absorption con espacio de color y máscara de confianza. `appearance` nunca acredita albedo. P7 produce. |
| `materials/fit.npz` | Contrato auxiliar `pbr_fit`: valores flotantes por texel, confianza por parámetro, residual, condicionamiento y propietario de cara UV. P7 produce. |
| `pipeline.json` | Ejecución, estados por etapa, hashes, duraciones, cantidades, unidades, evidencia, progreso, motivos y dependencias. P6 produce. |
| `validation.json` | Máximo/RMS/p95, muestras, cobertura, incertidumbre, independencia, alcance, protocolo y estado físico. Nunca aprobar por RMS/p95 solamente. P4/P7 producen, P6 publica. |

Un asset es `{ "path": "archivo relativo al JSON", "sha256": "64 hex" }`. Un producto
debe incluir sus assets referenciados; rutas externas y traversal no se admiten al importar.
Los reportes no embeben imágenes. Las previews derivadas quedan solo en la salida local.

### NPZ sin pickle

`metadata` es un array Unicode escalar con JSON, cargado con `allow_pickle=False`.
API: `write_npz(path, metadata, arrays)` / `metadata, arrays = read_npz(path)`.
Los contratos auxiliares `cloud_quality` y `mesh_arrays` evitan autorreferencias de hashes.

| NPZ | Arrays obligatorios |
|---|---|
| matches | `camera_xy`, `projector_xy`: float N×2; `color_rgb`: uint8 N×3; `confidence`: float N [0,1]; `source_code`: int N×2; `source_bit`: int N (-1 si no disponible); `measured`, `interpolated`, `occluded`, `invalid`: bool N, exactamente una por muestra. |
| cloud_quality | `xyz_mm`: float N×3; `color_rgb`: uint8 N×3; `sample_index`: int N dentro de S; `uncertainty_mm`: float N; `reprojection_px`: float N×2 (cámara/proyector); `rejection_reason`: int S, 0 aceptado. Metadata declara `rejection_codes`, `point_count`, `sample_count`. |
| mesh_arrays | `vertices_mm`: float N×3; `faces`: int F×3; `uv`: float N×2 [0,1]; `sample_index`: int N; `measured`: bool N. Metadata declara mundo y cantidades. Duplicar vértices en costuras UV. |

Píxeles: `opencv_integer_centers` (centro del primer píxel en 0,0). Subpíxeles son
float; no sumar 0,5 de forma implícita. Cámara x derecha, y abajo, z adelante.
`X_device=R@X_world+t`; centro `-R.T@t`. Mundo persistente, mm. Render usa una conversión
explícita (fixture: diag(1,-1,-1)). UV top-left: (0,0) arriba a la izquierda.
Albedo almacenado sRGB; normal tangente OpenGL y roughness como datos lineales.

El adaptador grabado conserva muestras inválidas fuera del proyector con `invalid=true`;
las muestra en los conteos y nunca las vuelve medidas. Un caché legado queda etiquetado
`legacy-cache-unversioned`; su confianza binaria no se inventa como confianza por bit.

## Etapas, extensión y reanudación

`preflight → decode → validate_rig → triangulate → mesh → segment /
radiometry_materials → register_render → verify`. La implementación mantiene un DAG:
segmentación no bloquea materiales, y verificación requiere escena y objetos.
P5 agrega `validate_rig` como dependencia directa de `segment`: las máscaras se
calculan con poses e intrínsecos calibrados. Ver [P5-OBJETOS.md](P5-OBJETOS.md) para
el worker local SAM/VLM, los campos semánticos y las rutas UV/lighting.

Estados: `pending`, `running`, `passed`, `failed`, `blocked`, `unverified`.
`passed` significa procesamiento completo. `unverified` puede entregar un artefacto
completo que todavía no acredita medición física/PBR. `blocked` explica la dependencia.
Un sintético completo termina `unverified`, con `physical_verified=false`.

P2/P3 pueden implementar un adaptador en Python sin tocar CLI/UI:

```python
from slscan.pipeline import Runner, Result
from slscan.pipeline_adapters import recorded_adapters

def triangulate(ctx):
    matches = ctx.artifact('decode', 'matches.npz')
    rig = ctx.artifact('validate_rig', 'rig.json')
    # Procesar; escribir solamente en ctx.work.
    # ctx.metadata('cloud', ...) incluye evidencia y hashes de entradas.
    # ctx.progress(0.5, 'Triangulando') comprueba cancelación y publica progreso.
    return Result(['cloud.json', 'cloud.ply', 'cloud_quality.npz'], counts={'measured': n})

adapters = recorded_adapters()
adapters['triangulate'] = triangulate
Runner(scan, output, rig=rig_path, adapters=adapters).run()
```

También se importan productos previamente calculados en `scan/products/<etapa>/`.
El JSON principal debe enlazar mediante `input_hashes` **todos** los outputs directos
de las dependencias: claves `decode/matches.npz`, `decode/decode_x.png`,
`validate_rig/rig.json`, etc. Se obtienen de `pipeline.json` (hashes de contenido;
no se requiere `fingerprint`). Una discrepancia bloquea, nunca reutiliza el producto.
No se ejecutan comandos arbitrarios provenientes de manifests.

La huella incluye contenido de entradas, configuración, código Python y dependencias.
Incluye el código del adaptador; parámetros externos/cierres deben declararse en
`Runner(options=...)` para entrar en la huella. El reporte del rig separa hash de
intrínsecos/tamaño y hash de poses; ninguno acredita por sí solo compatibilidad física.
Cambiar K/pose/frame/decoder/geometría/captura invalida descendientes. Las salidas
también se verifican contra hash antes de reanudar. Etapas fallidas, bloqueadas,
interrumpidas o con archivos incompletos se vuelven a ejecutar. Cada corrida tiene ID
nuevo, compartido por CLI/SSE/UI. El historial de artefactos tiene generaciones inmutables;
solo los referenciados en el reporte actual son servidos por HTTP. No se borran capturas.

Escritura JSON/NPZ atómica, publicación de directorio completo por rename y lock de
proceso por salida. SIGINT/`cancel` deja el reporte recuperable. Si el proceso muere sin
cleanup, el siguiente run ignora `running`; nunca promociona el staging incompleto.
Salida CLI: 0 procesado (puede estar sin verificación física), 1 fallo, 2 bloqueo, 130 cancelado.

## Alcance de esta entrega

El sintético ejecuta triangulación y malla de grilla pequeña con UV, dispositivos,
objeto genérico y contratos de luces/materiales. Estos últimos se declaran sin estimar;
no se fabrican resultados PBR. En grabados, se ejecuta decode real y se valida compatibilidad
del rig. Los algoritmos métricos/segmentación/materiales/render físico pertenecen a P2–P7;
si sus adaptadores o productos no están integrados, esas etapas quedan bloqueadas.
Esto permite integrar cada paquete sin cambiar la interfaz ni confundir previews con 2 mm.

P7 agrega el replay `pbr/session.json` para `radiometry_materials` y su experimento
independiente `slscan pbr synthetic`. La etapa registrada estima solo lo observable,
publica máscaras por parámetro y valida una lámpara reservada; absorción permanece
sin observar. Guía, formato de bundle y límites: [P7-PBR.md](P7-PBR.md).

## Verificación de P6 · 17-oct-2026

- `python -m pytest tests/test_pipeline.py -q -p no:cacheprovider`: **26 passed**.
  Incluye todos los contratos/exportación de esquemas, recorrido sintético, cache,
  cambio de K, archivos ausentes/corruptos, cancelación/reinicio, cambio de escena,
  exclusión de escritores concurrentes, productos con hashes obsoletos, resolución
  incompatible, máscaras/índices inválidos, límite HTTP de artefactos y SSE/reconexión.
- Suite completa: **283 passed, 3 skipped, 2 warnings, 60,60 s**. Los tres omitidos
  requieren escenas locales no copiadas al worktree (`test_keys`, `test_visuals_v3`,
  `test_web`). Warnings existentes de websockets/ElevenLabs. El nuevo monitor tiene
  tests propios que sí se ejecutaron; la inspección de scan5 fue por ruta absoluta.
- Replay de **scan5 grabado**, 44 frames: **208.937 correspondencias dentro del
  proyector; 567 inválidas; cero interpoladas**. Decode en 2,78 s en este ensayo.
  Sin rig: `validate_rig` y geometría posterior quedan bloqueadas, sin abrir hardware
  ni alterar entradas. Las previews privadas generadas para esta comprobación se
  eliminan al cerrar; el reporte local conserva métricas y hashes, sin fotos embebidas.
- Chrome **headless**, sin ventanas: UI/SSE con nueve etapas, nube de 96 puntos,
  malla, dos dispositivos y objetos/materiales sintéticos. Escritorio y ancho móvil
  de 390 px sin desborde; cero excepciones JS observadas. Cambio de reporte → texto
  visible en DOM: **168,3 ms** en una medición local (no una garantía de latencia bajo carga).
  `node --check` y `git diff --check` sin errores.

API local de solo lectura: `/api/pipeline`, `/api/events` (snapshots cada 200 ms,
reconexión con estado completo), `/api/preview` (vista reducida a 5.000 vértices) y
`/api/artifacts/<ruta-publicada>`. La UI marca si la geometría es una vista reducida.
El servidor liga únicamente `127.0.0.1`; no lanza navegador automáticamente.

`physical_verified=true` exige evidencia no sintética, checks independientes
`sheet_dimensions`, `planarity`, `object_outlines`, máximo más incertidumbre ≤2 mm,
cobertura completa del alcance declarado y `protocol` con tres poses reservadas,
tres repeticiones inmóviles, dimensiones de referencia, ROI prefijada y sin aplicar
correcciones diagnósticas. El validador revisa la consistencia del reporte: quien
produce las mediciones sigue siendo responsable de su procedencia experimental.

**No verificado en vivo:** tolerancia física de 2 mm, calibración del setup actual,
materiales PBR medidos y render físico de P4. El sintético no acredita ninguno de ellos.

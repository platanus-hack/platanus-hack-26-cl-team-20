# P3 · Reconstrucción métrica y UV, offline

Implementación en `slscan/metric_recon.py`, rama `codex/p3-malla`. Usa NumPy,
SciPy/OpenCV existentes. No abre dispositivos, ventanas ni servidores. Los crudos
son de solo lectura. No se aplica un rig nuevo a scan5 por tener el mismo tamaño.

```sh
python -m slscan reconstruct /ruta/sesion/osmo \
  --rig /ruta/rig.json --manifest /ruta/sesion/manifest.json \
  --camera osmo --out reports/p3/sesion --skip-bits 0
```

La salida debe ser nueva y estar fuera del scan. Publicación completa por rename
desde staging; un fallo no deja un directorio final parcial. Devuelve 0 si procesó
puntos y 2 si todos fueron rechazados. Procesado siempre queda **sin verificación
física**: ni un filtro ni una simulación acreditan el máximo de 2 mm.

## Contrato con P2/P6

Se leyó `codex/p6-pipeline/slscan/contracts.py` y su `docs/PIPELINE.md`, sin copiarlos
ni modificarlos. Si `slscan.contracts` está integrado se usan `header`, `write_npz`,
`atomic_json` y `validate`; en esta rama independiente se escriben los mismos
campos/arrays. Metadata NPZ: JSON Unicode escalar, `allow_pickle=False`.

El manifiesto sigue P6: `schema_version=1.0`, `artifact=manifest`, `evidence`,
`units`, `input_hashes`, `session_id`, `code_revision`, `rig_sha256`, `world`,
`pixel_convention=opencv_integer_centers`, `devices`, `patterns`,
`acquisition_known=true`. El frame de `devices.<camera>` declara tamaño efectivo,
rotación, downscale y controles de exposición/foco/WB. Además requiere
`rig_compatibility="verified_static_session"`, una afirmación del productor de
adquisición de que esa sesión corresponde a ese rig. No deducirla del nombre del
archivo ni crearla retroactivamente para pasar la validación.

`input_hashes` debe contener todos los crudos y `color.png` si existe, por ruta
relativa al manifiesto o basename. Se compara el contenido, resolución y hash del
rig antes de publicar. P2 debe invalidar esa compatibilidad si cambian montaje,
zoom, foco, keystone, recorte, rotación o resolución. Un hash registra procedencia;
no detecta por sí solo movimiento físico. Se acepta la clase `Rig` existente en mm;
la validación del rig v2 y observabilidad física pertenece a P2/P6.

| Archivo | Contrato / contenido |
|---|---|
| `matches.npz` | `matches`, unidades px; camera_xy/projector_xy, RGB uint8, confidence, source_code/source_bit y máscaras bool N exclusivas. Extras: cells, sigma_px, count, source_index, source_match, bit_contrast y máscaras camera_* completas. |
| `cloud.ply` | XYZ en mm, RGB; solo muestras aceptadas, sin flips implícitos. |
| `cloud_quality.npz` | `cloud_quality`: puntos aceptados, incertidumbre, reproyección y sample_index; rejection_reason bitmask para **todos** los candidatos. Extras conservan XYZ/diagnósticos previos, incluso NaN en candidatos degenerados. |
| `cloud.json` | `cloud`: hashes PLY/calidad/matches/rig, mundo y cobertura explícita de celdas del proyector. |
| `mesh.ply` | Vértices mm, normales, RGB, s/t, triángulos binarios little-endian. |
| `mesh.npz` | `mesh_arrays`: vertices_mm, faces, uv, sample_index, measured. Extras normals, color_rgb, grid_sample, texture_valid. |
| `mesh.json` | `mesh`: assets relativos y hashes, `texture_kind=appearance` o `none`, UV top_left_0_1. |
| `appearance.png` | Hardlink local a `color.png`, sin duplicar sus bytes. Solo lectura: modificarlo también modificaría la entrada. Requiere el mismo filesystem; si no, falla sin copiar. |
| `reconstruction.json` | Contrato `validation`, siempre physical_verified=false/status=unverified. processing_state passed/blocked, máximo/RMS/p95 antes/después, rechazo, tiempos y cantidades. |

Los arrays `camera_measured/invalid/rejected/unobserved` tienen tamaño de cámara;
las máscaras de procedencia principales de P6 tienen N muestras. Cada candidato
agrupado es medido aunque luego falle triangulación. `source_match[y,x]` enlaza los
píxeles originales con el candidato; −1 significa que no contribuye. source_code
guarda el representante entero del bloque y cells su índice en la grilla.
source_bit=−1 porque la correspondencia usa varios bits; el orden completo está
en decoder_stats.bit_order y bit_contrast. Contraste no es probabilidad calibrada.

No se fabrican oclusiones: sin evidencia independiente, `occluded=false` y los
fallos de decodificación son no observados, no superficies conocidas. Tampoco
hay relleno; interpolated es falso. Los índices del cloud/mesh apuntan a matches,
y no se compactan sin ese enlace. Rechazos pueden tener más de un bit; sus
conteos no son una partición sumable.

Integración P6: `measured_matches → triangulate_measured → grid_mesh →
export_reconstruction`. El wrapper `reconstruct_recorded` vuelve a decodificar
crudos y valida sesión. Los adaptadores P6 pueden usar estas funciones y añadir
sus hashes de dependencias al argumento `inputs` del export; todavía no se
registran automáticamente en el Runner de otra rama. No se tocaron `rig.py`,
`metric.py`, `capture.py`, ni el runtime. CLI añade solo el subcomando reconstruct.

## Geometría, limpieza y textura

- Centros OpenCV enteros. Un bloque de paso s se representa geométricamente como
  `origen+(extensión_real−1)/2`: para s=4 es 1,5, no 2 ni 2,5. Los bloques parciales
  al borde usan su extensión real. La UV es `(camera_xy+0.5)/camera_size`, origen
  **superior izquierdo**, según P6; ese 0,5 normaliza texeles y no mueve rayos.
- Decoder `slscan-gray/2`: valida bits contiguos, pares, límites y tamaños. La
  codificación uint16 se conserva para consumidores legados; la ruta métrica hace
  la conversión única a centro float. Cachés sin versión/tamaño son rechazados;
  re-decodificar crudos, nunca corregirlos con un offset supuesto. Los caminos
  legados `metric mesh` y `scan` no se promocionan como esta ruta certificable.
- Un código genera un candidato. Se usa mediana en cámara y muestra real cercana
  para el color inicial. Se rechazan grupos con radio >6 px o islas desconectadas
  (8 vecinos), sin seleccionar la isla conveniente. El contraste por bit se
  conserva opcionalmente en el decoder; reconstruct lo solicita siempre.
- Se triangulan rayos undistorted en mundo fijo: `Xdev=R Xworld+t`. Filtros por
  defecto: contraste mínimo 0,02; reproyección máxima 1 px en cada dispositivo;
  ángulo mínimo 1°; separación máxima de rayos 2 mm; profundidad 50–5000 mm en
  ambos dispositivos; sigma principal máximo 2 mm. Son parámetros explícitos,
  no umbrales ajustados contra hojas de evaluación.
- Sigma se propaga por diferencias centrales de 0,001 px en float64, con
  sigma de cámara 0,25 px y cuantización del proyector `extensión/√12`. No se divide
  por el número de píxeles agrupados. **No incluye covarianza del rig, sesgo de
  óptica ni movimiento**; reporta correspondence_only. Sigma≤2 no significa
  error máximo≤2. Si no alcanza, faltará un experimento de phase shift con gamma
  y movimiento controlados; no está implementado ni sustituido por interpolación.
- Malla por quads completos de celdas vecinas. Límites por defecto: arista 50 mm,
  salto de profundidad 20 mm en ambos dispositivos, ángulo entre caras vecinas
  60°. Se cortan pliegues/discontinuidades y quedan huecos. No Poisson/Delaunay ni
  remuestreo que convierta datos rellenados en evidencia. Normales/winding miran
  a la cámara; los vértices sin caras tienen normal cero.
- UV sobre la imagen **distorsionada original** de cámara, sin undistortion doble.
  `texture_valid` compara profundidad en colisiones del mismo texel. Solo certifica
  muestras visibles; no prueba visibilidad dentro de cada triángulo frente a un
  oclusor subpíxel no observado. P4 debe respetar la máscara y comprobar z-buffer
  al renderizar. Color/RGB de una foto es apariencia, no albedo sin iluminación.

La cobertura publicada es fracción de celdas de todo el proyector y densidad sobre
área triangulada. La ROI mate común prefijada se declara ausente hasta recibirla;
no se afirma 90% de superficie ni se achica una ROI para mejorar resultados.
`paper_metrics` mide cuatro lados, dos diagonales y máximo/RMS/p95 de distancia al
plano, sin reescalar. Plano ajustado mide planitud, no posición absoluta. Requiere
las tres poses físicas reservadas y cobertura/ incertidumbre antes de aprobar la
sección 5 del análisis; `passed_local` sintético no es una certificación de sala.

## Bloque radiométrico diseñado, no adquirido

`slscan/radiometry.py`: `plan`, `pattern_image` y `plan_manifest`, sin hardware.
P2 reutiliza su transporte/cámara; no se crea un segundo controlador. El plan se
anexa a la **misma sesión estática**, separado del Gray y sin nombres `[AA-BB...]`.
24 patrones de ajuste: R/G/B/gris a 32,64,96,144,192,240; seis bases negras entre
bloques; cuatro controles reservados; una referencia gris al inicio/final. Total:
36 estados, mínimo 108 frames estables (3 por estado). Duración solo tras medir
latencia/estabilidad, sin prometer un settle arbitrario.

Registrar exposición/foco/WB solicitado y readback, timestamps, referencia,
saturación, señal baja, estabilidad y hashes. Sin controles bloqueados, la
captura sigue sin verificar hasta validar compensación con parche. Brackets
conocidos entre bloques completos; guías apagadas durante exposición; rechazar
movimiento o deriva no explicada. La salida de plan usa contrato radiometry,
response_kind=unobserved: mezcla RGB cero es placeholder **no aplicable**, no
calibración. P7 ajusta solo fit y evalúa held_out; no infiere nuevas direcciones
de luz ni reflectancia absoluta desde estas rampas. No se adquirieron tomas.

## Verificación

Tests focales: decoder/límites/legacy, bloques parciales, agrupación, mundo genérico
con distorsión, profundidad positiva en ambos dispositivos, rayos degenerados,
ruido e incertidumbre, huecos/saltos/pliegues, UV, oclusión de texeles, PLY leído
por módulos independientes, hoja carta sintética y CLI offline con hardware
prohibido. El sintético de CLI produce **2.112 puntos y 4.042 triángulos** en Z=600 mm.
La hoja carta sintética recupera lados/diagonales y plano con máximo <0,000001 mm.
Es un control matemático sin ruido, no rendimiento físico.

Se validaron los tres NPZ y cloud.json/mesh.json contra el módulo real de contratos
de P6, cargado en solo lectura desde su worktree. La prueba usa el mismo export,
incluida textura local, y no instala dependencias ni abre hardware.
También pasaron reconstruction.json (validation) y el plan radiométrico. Demo
local revisable en `reports/p3/demo/output/`, fuera de git: 456 KB para entradas,
salidas y metadata; máximo de distancia al plano sintético 3,41×10⁻¹³ mm.
Se comprobaron hashes de todas las entradas después de exportar y que la textura
comparte bytes sin modificarlos.

Regresión grabada scan5/osmo, crudos originales de solo lectura, skip_bits=2:
209.504 válidos legados → 208.937 dentro de imagen (567 descartados); 87.913 grupos
aceptables respaldados por 181.329 píxeles; 27.608 píxeles rechazados por agrupación.
Confianza mínima 0,02353 y mediana 0,05098. Coordenadas de proyector 1,5–1917,5 / 
1,5–1077,5. El re-decode coincide exactamente con el caché en códigos dentro de
límites. Decode+agrupación: 6,0 s en este Mac; contraste por bit ocupa 37.324.800
bytes en memoria, sin guardar crudos nuevos. **No existe captures/rig.json** al
hacer esa prueba: no se trianguló scan5 ni se afirmó precisión real.

Para correr pruebas desde este worktree:

```sh
/Users/max/hardware/sl-scanner/.venv/bin/python -m pytest \
  tests/test_graycode.py tests/test_recon.py tests/test_metric_recon.py \
  tests/test_radiometry.py -q -p no:cacheprovider
/Users/max/hardware/sl-scanner/.venv/bin/python -m pytest tests -q -p no:cacheprovider
```

Suite completa, ejecutada una vez al cierre: **274 passed, 3 skipped, 2 warnings,
57,55 s**. Los tres skips son pruebas de escenas locales sin captures en este
worktree; las capturas originales se verificaron por separado en solo lectura.
Warnings existentes de deprecación de websockets/ElevenLabs. `git diff --check`
sin errores. No se instalaron paquetes ni se hicieron pruebas en vivo.

Pendiente del rig/sesión P2: nube/malla real y hojas de evaluación ≤2 mm. Pendiente
de P7: adquisición radiométrica, ajuste y PBR medido. Ninguno se considera aprobado
por entregar el código o los archivos sintéticos.

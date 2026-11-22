# P7 · lámparas fijas y materiales relativos, sin hardware

Entrega offline: guía de captura, estabilidad on/off, localización de luces puntuales,
respuesta RGB/brillo, ajuste por texel y re-render bajo una lámpara reservada.
Usa NumPy/SciPy/OpenCV del entorno existente. No abre cámaras, proyectores, voz,
ventanas ni red. No mueve lámparas ni modifica capturas. La salida siempre lleva
`physical_verified=false`; no certifica los 2 mm geométricos de P2/P3.

```sh
python -m slscan pbr plan --lamps pie escritorio techo --held-out techo
python -m slscan pbr synthetic --out reports/p7-demo --width 12 --height 8
python -m slscan pbr fit /ruta/bundle/session.json --out /ruta/salida/scan
```

La salida debe ser nueva/vacía y estar fuera del bundle de entrada. El ejemplo genera
`input/` y `scan/`, más `summary.json`. El fixture sintético de P6 sigue siendo su
fixture de contratos sin estimación; este comando ejecuta el experimento inverso P7.

## Captura y guía

`pbr_protocol.capture_plan` reutiliza los 24 patrones fit RGB/gris de P3, cuatro
controles reservados, bases y repeticiones. Agrega estados de lámparas separadas por
apagado total. Una lámpara se reserva antes de capturar. La misma instrucción sirve
como texto proyectado y voz: el transporte P2 debe mostrarla/decirla y apagar la guía
antes de medir. No se implementa otro transporte ni se encienden interruptores por API.

`StabilityGate` recibe frames lineales alineados, timestamps crecientes y una ROI
estable. Exige cambio real respecto del estado anterior y cuatro frames consecutivos
estables; rechaza saturación, controles no validados, guía encendida y cambios locales.
Para la primera base se usa `require_change=False`. Los umbrales iniciales son parámetros,
no una latencia supuesta. El caller registra timestamps y valida alineación/movimiento;
la estabilidad fotométrica por sí sola no acredita que toda la escena siga inmóvil.

Fijar exposición, WB y foco. Si la Osmo no permite bloquearlos, un parche que recibe
la lámpara que se prende **no basta para distinguir luz de autoexposición**.
`normalize_reference` acepta únicamente dos o más niveles de referencia con iluminación
independientemente estable y comprueba consistencia del gain por canal. Alternativa:
brackets conocidos o cámara manual; exposición corta para el emisor y otra para superficies.
No se deduce una respuesta lineal de PNG/sRGB ni de guardar tres frames iguales.

`isolate` normaliza exposiciones conocidas, resta la media de bases anterior/posterior
y excluye saturación/deriva por texel. El modelo de referencia debe fijarse antes de ver
la foto reservada. Sol variable, rebotes, flicker y tone mapping no lineal pueden invalidarlo.

## Luces y observabilidad

`emitter_regions` detecta candidatos brillantes por diferencia on/off en el bracket corto,
desdistorsiona con K/dist y devuelve rayos mundo, saturación y tamaño angular. La extensión
angular sugiere puntual/área; varios candidatos pueden corresponder a un grupo o reflejos.
La asociación al emisor/interruptor debe quedar validada en el bundle. No identifica la
tecnología de una ampolleta a partir de su color ni atribuye profundidad a un píxel.

`locate_light` ajusta profundidad sobre el rayo y potencia RGB usando parches mates de
geometría/reflectancia relativa conocida, separados de los materiales evaluados. Cuatro
inicios de profundidad, residual robusto y Jacobiano detectan ambigüedad distancia/potencia.
La escala queda fijada por esa referencia relativa; no son lúmenes. Se publica desviación
de profundidad **condicional** a rayo/geometría/referencia, no un intervalo completo ni 2 mm.
Una hoja blanca no es referencia espectral certificada.

Fuentes extensas/grupos se conservan como `area`/`group`, `direction_only`, potencia no
observada. Su BRDF no se fuerza con el modelo puntual: este primer solver no integra una
distribución emisora de área desconocida. Lo mismo ocurre con fuentes ambiguas, lejanas
o mal explicadas por el modelo puntual. En una casa con pocas direcciones puede quedar
todo o parte del material sin estimar. No se pide mover las lámparas para ocultarlo.

Temperatura: aproximación McCamy desde RGB lineal sRGB calibrado, solo para fuentes próximas
al blanco en el rango admitido. Sin calibración de color devuelve `null`; no es temperatura
física/espectrometría. La fórmula está documentada en
[Colour Science](https://www.colour-science.org/api/0.3.4/html/_modules/colour/temperature/cct.html).

## Ajuste y salidas

`uv_geometry` toma directamente vértices/caras/UV P3. Muestrea centros de texel, conserva
huecos, rechaza islas solapadas con geometría distinta y respeta winding. `sample_camera_to_uv`
proyecta con la cámara calibrada y usa z-buffer/propietario de cara de P5 para excluir
oclusiones. Su muestreo nearest es conservador; no rellena ni mezcla bordes.

Modelo: dieléctrico opaco isotrópico, difuso Lambert y especular GGX/Smith/Schlick,
`alpha=roughness²`, F0 escalar. La distribución/masking sigue el modelo descrito en
[PBRT](https://www.pbr-book.org/4ed/Reflection_Models/Roughness_Using_Microfacet_Theory).
El ajuste robusto por texel estima RGB difuso, dos pendientes de normal, roughness y F0,
con dos inicializaciones. No cambia la malla. Usa exclusivamente estados `fit` y evalúa
rango angular, Jacobiano, desviaciones y residual. Mínimo de este solver conjunto: cuatro
muestras útiles; no implementa el solver Lambert reducido de tres luces.

- `scan/lights.json`: posiciones/direcciones, estados, tipo, potencia, temperatura y límites.
- `scan/radiometry.json`: transferencia efectiva `b + A p**gamma`, mezcla RGB, validación
  reservada, saturación y gamut acotado. Es por parche; se puede llamar por región.
- `scan/materials.json`: hash de malla, UV, significado y assets de todos los mapas.
- `scan/materials/{albedo,normal,roughness,specular,absorption}.png`: PNG de 16 bits;
  albedo sRGB, restantes lineales. Normal tangente OpenGL (+U, −V), UV top-left.
- `*_confidence.png`: máscaras separadas por parámetro. `confidence.png` es la intersección
  de los cuatro parámetros de reflexión, **no incluye absorción**. Valores sin confianza
  son preview/ajuste ambiguo y no deben aplicarse como medición.
- `materials/fit.npz`: contrato auxiliar `pbr_fit` v1, sin pickle, floats antes de cuantizar,
  máscaras, muestras, condicionamiento, error y propietario de cara por texel.
- `validation.json` y `materials/held_out_{photo,render,error,valid}.png`: error RGB/luminancia,
  media/p95/máximo, cobertura, por objeto y error por texel. Normalización prefijada.

**Absorción no observada:** el archivo contiene cero como placeholder, confianza cero y
`apply=false`. No se transforma `1-albedo` en una medición física. RGB reflejado desde
una vista no identifica transmisión, espesor ni absorción espectral/volumétrica. Tampoco
se recupera desplazamiento bump métrico integrando estas normales: eso requeriría otra
validación geométrica. Materiales siempre `partial` en este paquete.

La lámpara reservada se calibra solo sobre los parches separados. Su foto de los materiales
no interviene en inicialización, parámetros ni selección del ajuste. La cobertura de validación
excluye texeles no observables y se publica, incluyendo casos sin ninguna muestra válida.
Un buen re-render no demuestra unicidad de la BRDF.

## Bundle de replay e integración P6

`session.json` v1 contiene `evidence`, `encoding="linear_rgb"`, `atlas_shape=[H,W]`,
`camera`, `noise`, `radiometric_range` prefijado y assets `{path,sha256}` de `mesh`, `rig`
y `frames`. Todos deben estar dentro del bundle. Mesh/rig/mundo/evidencia/hashes deben
coincidir. La malla debe incluir `rig_sha256`; no se usa un rig nuevo sobre fotos históricas
sin comprobar compatibilidad. `create_bundle` es un ejemplo completo ejecutable del formato.

Los campos `controls_validated`, `alignment_validated` y `reference_disjoint` son declaraciones
obligatorias del productor de captura, no verificaciones mágicas. `reference_status` debe ser
`linear_exposure_normalized_stable_unsaturated`. El caller tiene que conservar la evidencia
de esas comprobaciones en su manifest P2. `color_calibrated=false` conserva temperatura null.

Cada estado lleva ID único, `role=fit|held_out`, `type=point|area|group|directional|diffuse`
y tres `exposure_ratios` para on/base anterior/base posterior. Exactamente una lámpara reservada.
`frames.npz`, sin pickle:

| Array | Forma y significado |
|---|---|
| `on`, `off_before`, `off_after` | L×H×W×3, floats RGB lineales antes de normalizar exposición |
| `valid` | L×H×W bool: visibilidad/alineación válida, sin relleno |
| `reference_xyz`, `reference_normal`, `reference_reflectance` | C×3, parches mates separados, mm/normales unitarias/reflectancia relativa |
| `reference_signal` | L×C×3, diferencias lineales ya normalizadas y comprobadas |
| `emitter_origin`, `emitter_ray` | L×3, origen en mm/rayo unitario de emisor asociado |
| `ramp_patch` | 36×3, RGB observado según `radiometry.plan()` |
| `object_ids` | H×W enteros opcionales para agregación por objeto |

P6 usa automáticamente `pbr/session.json` si no hay producto precomputado en
`products/radiometry_materials/`. El bundle puede contener copias pequeñas de metadatos
y malla; las capturas originales se mantienen en solo lectura. La etapa comprueba hash
contra la malla actual, publica progreso y deja todos los assets en staging validado por P6.
Los hashes de todos los archivos del bundle invalidan la caché al cambiar una toma o control.
Producto precomputado sigue teniendo prioridad. Faltan capturas → `blocked`, sin abrir hardware.

## Verificación y límites de la escena sintética

El sintético tiene cuatro paneles inclinados con materiales coloreados, textura fina,
microvariación de normales, siete lámparas puntuales fijas de ajuste y una reservada;
emisores visibles generados como imágenes, referencias mates separadas, potencias cálidas/frías,
ruido, una sombra enmascarada y un texel saturado. Usa un renderer directo analítico;
no simula transporte global de una sala, vidrio, tela anisótropa ni emisión de área compleja.
La verdad está en un archivo separado que el solver no lee. Son más direcciones de las
que puede haber disponibles en una casa real; el test con solo dos y luces de área exige
que el sistema responda sin estimar BRDF.

Tests cubren también exposición/WB, flicker, deriva de ambiente, ambigüedad de profundidad,
luces paralelas, detección angular de área, gamut, mezclas reservadas, UV/OpenGL, muestreo
calibrado, rutas/hash/evidencia y no fuga de la foto reservada. Se valida el contrato completo
de assets publicado por el adaptador P6. No se ha probado en Blender ni en vivo.

### Medición local · 17-oct-2026

Comando de arriba ejecutado con atlas 12×8; informe y comparación visual en
`reports/p7-final/summary.json` y `reports/p7-final/inspection.png`, fuera de git.

| Medida sintética | Resultado |
|---|---:|
| Luces localizadas desde emisores y referencias separadas | 8/8 |
| Error de posición máximo / distancia a origen de escena | 0,073% |
| Error angular mediano de normales observables | 0,100° |
| MAE roughness observable | 0,0276 |
| MAE RGB albedo observable | 0,00240 |
| Cobertura albedo / normal / roughness / F0 | 93,8% / 96,9% / 72,9% / 65,6% |
| Validación reservada: cobertura conjunta | 58/96 texeles, 60,4% |
| MAE / p95 / máximo luminancia reservada | 0,0137% / 0,0347% / 0,1023% del rango fijo 1 |
| Absorción observada | 0% |

El rango 1 es la escala lineal nominal del fixture, fijada antes de evaluar; en captura
real se debe usar la diferencia medida entre referencia alta y base. Los errores bajos
corresponden a un generador directo del mismo modelo BRDF; no demuestran robustez a
rebotes/materiales distintos de ese modelo. Se inspeccionaron localmente albedo, normal,
roughness, F0, confianza y foto/render/error reservados. No se abrió ninguna ventana.

`tests/test_pbr.py`: **20 passed, 21,56 s** en la comprobación final, incluida exclusión
de caras traseras y tipo de fuente inferido desde extensión angular. La primera corrida
focalizada P7 + radiometría + pipeline dio **45 passed** antes de esos últimos casos.

Suite completa ejecutada una vez: **342 passed, 3 skipped, 2 warnings, 311,18 s**.
Los omitidos necesitan capturas locales no copiadas al worktree; los warnings son las
deprecaciones existentes de websockets/ElevenLabs. La suite se inició antes del último
ajuste de caras traseras/tipo inferido; por eso se volvió a correr P7 después de esos
cambios (los 20 tests anteriores). `git diff --check` sin errores. No se instalaron
dependencias; los dos experimentos locales ocupan menos de 1 MiB en total.

---
name: pcx-calibrar
description: "Calibrar y diagnosticar la alineación cámara, proyector y mesa de pcx: elegir entre calibración rápida (marcadores y homografía), precisa (gray code y LUT que corrige relieve) o verificación; reaccionar al aviso de deriva calibration_drift; preparar la escena (contraste, luz ambiente, exposición) y diagnosticar por qué lo proyectado cae corrido. Úsala siempre que lo proyectado no calce con los objetos, se haya movido el proyector, la cámara o la mesa, llegue un evento calibration_drift, scene no traiga transforms, se monte el rig por primera vez, o alguien pregunte por precisión en mm, homografía, LUT, gray code o la tool calibrate."
license: MIT
compatibility: Requiere el servidor MCP de pcx conectado; calibrar de verdad requiere el montaje físico (proyector y cámara).
metadata:
  author: pcx
  version: "0.1.0"
---

# pcx-calibrar: que la luz caiga donde debe

La calibración produce las **transformaciones** entre marcos: `cam:<id> → table_mm` (para entender lo que ve la cámara) y `table_mm → proj:<id>` (para pintar en mm). Sin ellas, los mm no significan nada: el renderer centra la superficie en la salida y los widgets anclados no caen sobre los objetos.

## ¿Está calibrado?

Llama `scene` y mira `transforms`:

```json
"transforms": [
  {"from_frame": "table_mm",  "to_frame": "proj:main", "kind": "homography", "matrix": [ …9 ], "version": 3, "residual_mm": 1.8},
  {"from_frame": "cam:top",   "to_frame": "table_mm",  "kind": "homography", "matrix": [ …9 ], "version": 2},
  {"from_frame": "proj:main", "to_frame": "cam:top",   "kind": "lut", "version": 4, "residual_mm": 0.6, "lut": { … }}
]
```

- Lista vacía → nunca se calibró (o se perdió el estado): hay que calibrar antes de proyectar anclado.
- El nodo guarda **una sola transformación por par** (`from_frame`, `to_frame`): cada `set_transform` reemplaza a la anterior de ese par. No verás una `homography` y una `lut` a la vez para `table_mm → proj:main`.
- `precise` publica su LUT en el sentido `proj:<id> → cam:<id>` (para que la percepción vea sin paralaje) junto con una homografía aproximada; el renderer sigue pintando con `table_mm → proj:<id>`. Si llegara una `lut` para ese par, el renderer la prefiere sobre la homografía.
- `residual_mm` es el error estimado. Bajo 2 mm está bien para etiquetas y halos.

## Qué modo usar

| Modo | Qué hace | Cuándo | Cifras |
|---|---|---|---|
| `quick` | Proyecta una grilla de 40 marcadores (patrón e inverso), ajusta una homografía con RANSAC | Superficie plana; primer montaje; después de un `calibration_drift` | ~0,4 s de captura; ~0,8 mm p50 en simulación |
| `precise` | Gray code en streaming; LUT densa `proj → cam` que corrige relieve y lente | Hay objetos altos, una viga, superficie no plana, o se necesita < 1 mm | ~4 s en buenas condiciones; 29,5 s en el montaje real de bajo contraste; ~0,45 mm p50 |
| `verify` | Proyecta 12 cuadrados de 36 mm y mide dónde caen | Chequeo de rutina; antes de una demo; cuando dudas | ~0,4 s; emite `calibration_drift` si la mediana supera 2 mm |

Por qué importa `precise`: una homografía describe **un plano**. Sobre una caja de 90 mm de alto, la homografía sola erró 13,8 mm; la LUT, 0,5 mm (27 veces menos). En el techo con viga, una homografía única dio 9,1 px de error y una por plano, 0,8 px.

Orden recomendado en un montaje nuevo: `quick` → probar → `precise` solo si hay relieve o no alcanza → `verify` cada vez que alguien toque el rig.

## Pedir una calibración

```json
// calibrate
{"request_id": "calib-quick-20261121-1", "mode": "quick"}
// → {"status": "queued", "mode": "quick", "message": "calibración encolada; la ejecuta el proceso de calibración (set_transform al terminar)"}
```

`camera_id` y `projector_id` son opcionales (ids en `devices`).

**Lee bien la respuesta.** La tool no calibra en la llamada: encola el pedido y lo ejecuta el proceso de calibración del nodo, que al terminar entrega el resultado con `set_transform`. `queued` no es «listo». Comprueba el resultado:

1. Avisa a la persona y despeja la mesa (ver preparación).
2. Llama `calibrate`.
3. Vuelve a `scene` a los pocos segundos: `transforms` debe traer una `version` mayor. Por el gateway el plazo de la tool es de 180 s.
4. Si `transforms` no cambia, no hay proceso de calibración atendiendo la cola en ese nodo. En el estado actual del repo el cableado de `pcx up` con calibración está pendiente: dilo tal cual y pide correrla en la máquina del montaje (`node/crates/pcx-calib/README.md`, ejemplo `calib_hw`), en vez de reintentar.
5. Cierra con una prueba visual (abajo).

Durante la calibración el proyector muestra patrones a pantalla completa que **tapan la escena**: no proyectes ni lances flujos hasta que termine, y no la pidas en medio de una interacción.

## Preparar la escena

La calibración compara lo que proyecta con lo que la cámara ve. Todo lo que baje el contraste entre «proyector encendido» y «apagado» la empeora. En el montaje real el contraste medido fue de solo 1,16:1 con luz fuerte: funcionó, pero promediando patrón e inverso y tardando 29,5 s en vez de los ~3,8 s que toma `precise` en simulación (unas 8 veces más).

- **Baja la luz ambiente.** Apaga las luces sobre la mesa, cierra cortinas. Es lo que más ayuda.
- **Ninguna fuente de luz dentro del encuadre** de la cámara (lámparas, ventanas, pantallas): la exposición automática se ajusta a ellas y aplasta el patrón.
- **Fija exposición y balance de blancos** si la cámara lo permite. Con exposición automática la imagen cambia entre patrones. (La C920 por AVFoundation no deja fijarlos: bug B9.)
- **Superficie mate y clara.** Los brillos (vidrio, barniz, metal) reflejan el proyector como un punto saturado.
- **Mesa despejada y manos fuera** para `quick` y `verify`: los marcadores deben verse. Para `precise` deja los objetos fijos que quieras mapear, pero nada que se mueva.
- **Que la cámara vea toda el área proyectada**, con margen.
- **Rig firme.** Un trípode que vibra o un cable que tira arruina la captura; después de calibrar, cualquier golpe la invalida.
- **Marcadores grandes.** La única medición en hardware es con marcadores ArUco 4×4 (no con los `pcx6` de `quick`): a ~74 px de cámara se detectaron 16 de 24; a ~29 px, 16 de 60. Tómalo como indicio de que el tamaño en la imagen importa: si la cámara está lejos o tiene poca resolución, acércala.
- **Escala en mm.** Los mm reales salen de una referencia métrica: marcadores impresos de tamaño conocido sobre la mesa (mejor repartidos) o un factor mm por px del proyector. Sin referencia, la escala es estimada: las posiciones relativas sirven, las medidas absolutas no.

## Aviso de deriva

`verify` (y cualquier proceso que la corra periódicamente) emite:

```json
{"type": "calibration_drift", "from_frame": "table_mm", "to_frame": "proj:main", "residual_mm": 13.9, "threshold_mm": 2.0}
```

Significa que el proyector, la cámara o la mesa se movieron. Qué hacer:

1. **Dilo ya**: lo proyectado puede estar corrido `residual_mm` milímetros. No sigas una guía que depende de apuntar con precisión.
2. Pausa lo que apunte a objetos chicos. Lo que no depende de la posición (paneles, preguntas) puede seguir.
3. Pide recalibrar: `quick` si la superficie es plana; `precise` si se usaba LUT.
4. Confirma con `verify`.

Escúchalo con `events_wait` y `types: ["calibration_drift"]` cuando corras guías largas. Un residuo apenas sobre el umbral con algo alto encima de la mesa puede ser falso: un cuadrado de verificación que cae sobre una caja mide mal por paralaje.

## Prueba visual (cierra siempre con esto)

Proyecta marcas en posiciones conocidas y mira:

```json
// project
{"request_id": "calib-prueba-1", "widgets": [
  {"type": "halo",  "id": "cal-borde",  "polygon_mm": [[10, 10], [790, 10], [790, 490], [10, 490]], "color": "#00E5FF", "pulse_hz": 0, "ttl_s": 60},
  {"type": "label", "id": "cal-centro", "anchor": {"point_mm": [400, 250]}, "text": "+ centro (400, 250)", "size_mm": 12, "ttl_s": 60}
]}
```

(Ajusta a `scene.surface`.) Luego `look`: el borde debe coincidir con el borde real de la superficie y la grilla anotada. Mejor aún, ancla un halo a un objeto real (`anchor.object_id`) y revisa que lo rodee. Pregunta a la persona: es quien ve la mesa de verdad. Borra las marcas al terminar.

## Diagnóstico

| Síntoma | Causa probable | Qué revisar o hacer |
|---|---|---|
| Todo corrido lo mismo | Rig movido | `verify`; luego `quick` |
| Calza en la mesa pero no sobre objetos altos | Homografía sola (un plano) | `precise` (LUT) |
| Calza al centro y se abre hacia los bordes | Distorsión de lente sin corregir | `precise` |
| Escala mal (todo más grande o más chico) | Sin referencia métrica | Marcadores impresos de tamaño conocido |
| `transforms` vacío tras calibrar | Nadie ejecutó la cola | Correr la calibración en la máquina del montaje |
| Pocos marcadores vistos en `quick` | Contraste bajo, marcadores chicos, objetos tapando | Bajar luz, despejar, acercar la cámara |
| `precise` tarda mucho o reintenta | Contraste bajo o latencia inestable | Bajar luz, fijar exposición |
| Cobertura de LUT baja | La cámara no ve parte de lo proyectado | Reencuadrar la cámara |
| No se ve nada proyectado | Sin renderer conectado | `/health` → `renderers` debe ser ≥ 1 |
| `look` no muestra la cámara real | Captura no cableada (cámara `sim`) | `devices` con `kind: "camera"` |
| Deriva recurrente | Soporte flojo, vibración, calor | Fijar el rig |

Comandos útiles en la máquina del nodo:

```bash
curl -s localhost:9200/health        # renderers conectados, latencia de render, nube
curl -s localhost:9200/state -H "Authorization: Bearer $PCX_TOKEN"   # `calibrations` = total de pedidos de calibrate desde el arranque (no es una cola: nunca baja)
```

La calibración en hardware deja informes `report_*.json` con `quick.markers_seen`, `residual_mm`, `precise.stream` (`latency_ms`, `retried`), `decode.valid/lit`, `lut_coverage`, `check_cam_px` y `verify.err_mm`: pídelos cuando algo no cuadre.

## Qué no hacer

- No calibres sin avisar: tapa la mesa con patrones y corta lo que estaba pasando.
- No reintentes en bucle si `transforms` no cambia: el problema es de cableado o de escena, no de suerte.
- No des medidas en mm como exactas si no hay referencia métrica o hay deriva pendiente.
- No inventes transformaciones a mano para «corregir» un corrimiento: recalibra.

# P2 · Rig métrico cámara–proyector

## Flujo vigente: escena sin hoja (17-oct, cambio de las 19:41)

**No se requiere A4 ni tablero.** El nuevo punto de entrada es
`python -m slscan autocalib SESION --out SESION/rig.json`.
La descripción de hojas más abajo documenta el método anterior, que permanece
disponible para comparación. El flujo de h3 usa el modo de escena de
[P2-AUTOCALIB.md](P2-AUTOCALIB.md), con contrato JSONL, escala externa y límites.

Frases para Max, listas para proyectar:

1. «Deja la mesa y los objetos quietos.»
2. «No hace falta hoja ni tablero.»
3. «Deja el proyector fijo. No cambies foco ni zoom.»
4. «Voy a proyectar franjas. No muevas nada durante la captura.»
5. «Ahora mueve solo la cámara hacia un lado. Cambia un poco el ángulo.»
6. «Deja la cámara quieta. Confirma para seguir.»
7. «Repite desde otro lado. Mantén la misma escena a la vista.»
8. «Elige un tramo largo y rígido. Mide entre dos puntos claros.»
9. «Marca esos mismos dos puntos en la imagen.»
10. «Estoy ajustando el rig. La precisión aún debe comprobarse.»

El cambio de posición solo aplica a `--views 2` o `--views 3`; no se cuentan
tres secuencias estáticas como tres puntos de vista. Un único par puede producir
un diagnóstico, pero el rango insuficiente bloquea su publicación. No iniciar
captura hasta la confirmación de h1 para q21.

---

## Método anterior con referencia plana

`metric calib` ajusta conjuntamente focales independientes, centro óptico,
distorsión de ambos dispositivos, relación cámara–proyector y poses de una hoja
física. Usa las esquinas físicas en mm y las observaciones de ChArUco proyectado:
intersecta cada rayo del proyector con el plano y reproyecta en la cámara. Los
puntos métricos derivados de homografías sirven solo para inicializar.

La salida es **un rig candidato**. Ningún RMS de calibración, simulación o mapa
interpolado certifica ≤2 mm. El control físico sigue el brief y la sección 5 de
`ANALISIS-pipeline-3d.md`. No usar el rig nuevo sobre scan5 sin comprobar que
montaje, óptica y adquisición coinciden.

## Uso sin hardware

Desde este worktree, con el entorno existente (sin instalar paquetes):

```sh
/Users/max/hardware/sl-scanner/.venv/bin/python -m pytest tests/test_joint.py tests/test_metric_session.py tests/test_metric_validation.py -q
/Users/max/hardware/sl-scanner/.venv/bin/python -m slscan metric calib reports/p2-session-a \
  --out captures/rig.json --report reports/p2-rig-report.json --html reports/p2-rig.html
```

La segunda orden lee observaciones, jamás abre cámara/proyector. Requiere
`pose_NN/observation.json` v1; train/holdout se asignan antes del ajuste. Un frame
incompatible, pose rechazada o ID duplicado produce error; no se salta datos.
Un ajuste degenerado/no convergente/en límites o con RMS cámara >0,5 px o
proyector >1 px entrega reporte y código 2, sin publicar el rig. Un control
reservado fallido también bloquea su publicación. Código 0 significa candidato
procesado, siempre `metric_status=unverified`.

`--distortion brown5` libera k1,k2,p1,p2,k3 después de las etapas de focal/centro
y k1. `--distortion k1` es un modelo restringido explícito, con los otros cuatro
coeficientes en cero. No elegir modelo mirando los controles reservados. Después
de corregir o elegir modelo con evidencia de evaluación, reservar nuevas poses.
`--restarts 2` prueba inicios perturbados; `--repeat 8` resamplea **vistas enteras**.
El Jacobiano escalado, diversidad de normales, parámetros en límites y todas las
fallas de bootstrap quedan en el reporte. La covarianza local es condicional al
modelo y no es una cota física total. `pixel_sensitivity` propaga numéricamente
ruido de correspondencias a XYZ: es una sigma, excluye error del rig y patrón.

`--solver legacy` conserva el calibrador histórico y formato `/1` solo para
regresión/inicialización. `metric capture --legacy` conserva su captura histórica.
Ninguno acredita el ajuste conjunto nuevo.

## Turno real: pasos para Max

Antes de abrir dispositivos, h1 debe confirmar que h3 liberó T749 y Osmo. El
encargo h6 exige pedir ese turno con `orq-creativo ask --urgent`. No lanzar un
segundo visor encima del runtime. Este flujo usa exclusivamente
`PatternWindow(screen="1920x1080")`; el runtime debe estar detenido/liberado por
su dueño. No abre ventanas en pantalla interna ni Sidecar.

1. Poner Osmo en modo webcam. Mantener cámara y T749 inmóviles durante ambas
   calibraciones y controles. Desactivar auto-keystone, zoom variable y cambios
   de foco; anotar cualquier bloqueo que no se pueda verificar. Mantener luz
   ambiente estable. No se necesita impresora.
2. Usar **A4 de 210×297 mm**, sin confundir con carta 215,9×279,4. Adherirla a
   soporte rígido plano, mate y más oscuro que la hoja, sin doblarla ni tapar las
   esquinas. Registrar tolerancia de corte, planitud y método de medición. Una
   dimensión nominal sin incertidumbre permite ensayar, no aprobar 2 mm.
3. Confirmar T749 a 1920×1080 físico y lógico, sin escalado ni corrección de salida
   por objeto. El programa rechaza resolución distinta; el zoom/keystone óptico
   requiere confirmación física. Cámara solicitada 3840×2160, downscale 2,
   rotación 270°: frame efectivo **1080×1920**. `scan` usa los mismos defaults.
4. Primer ensayo: cuatro poses de entrenamiento y tres reservadas. Para la
   sesión definitiva: 10 train más tres holdout, prefijadas. Hacer una pose
   frontal, inclinar ±20–30° en ambos ejes, variar centro/bordes y dos o tres
   profundidades dentro del volumen a escanear. La diversidad importa más que
   contar poses. Mantener las cuatro esquinas visibles y dentro de la proyección.
5. Apoyar la hoja y soltarla antes de cada captura. El agente avanza cuando Max
   confirma que está quieta. Se captura blanco, Gray code, ChArUco que se adapta
   al interior de la hoja y blanco final. La guía/patrón aparece solo en T749.
   No mover la hoja hasta la confirmación de fin. Se rechaza movimiento de
   esquinas >0,5 px, deriva de blanco >3%, saturación >5% o menos de seis esquinas
   proyectadas. Estos umbrales están prefijados, no son un certificado métrico.
6. Las tres últimas poses quedan reservadas: cerca/centro, lejos/borde e
   inclinada en otra zona. Nunca entran al bundle ni a la selección de parámetros.
7. Repetir la calibración completa en una carpeta nueva, manteniendo el rig
   quieto. Repetir tres veces las capturas de control de cada pose reservada.
   Ambas calibraciones deben cumplir los mismos controles. Si algo se mueve,
   invalidar el rig y volver a adquirir, sin nudges por objeto.

Orden de captura, **solo después del turno confirmado**:

```sh
/Users/max/hardware/sl-scanner/.venv/bin/python -m slscan metric capture \
  --display-confirmed --camera osmo --cam-size 3840x2160 --downscale 2 --rotate 270 \
  --screen 1920x1080 --size 1920x1080 --paper-mm 210x297 \
  --poses 10 --holdout 3 --settle 2 --out reports/p2-session-a
```

El settle de 2 s es un inicio conservador, **no una latencia medida**. Antes de la
sesión definitiva medir latencia/estabilidad y ajustar. Por pose hay 44 patrones
Gray más las tomas blanca/ChArUco; presupuestar alrededor de dos minutos con
settle 2 s, más movimiento humano. No prometer tiempos de scan5. Los controles
de exposición/WB/foco registran lo que acepta/reporta el backend, con
`physically_verified=false`; la comparación inicial/final detecta solo parte de
la deriva, no prueba bloqueo continuo ni ausencia de movimiento intermedio.

Las capturas privadas se guardan en `reports/`, fuera de git. Solo se borran los
PNG Gray temporales de esta sesión nueva tras decodificar, salvo `--keep-images`.
Antes de abrir hardware se exige 1 GiB libre (2 GiB con `--keep-images`).
No se duplican ni modifican capturas históricas. Conservar las fotos de la sesión
hasta revisar localmente detección de la hoja y controles; al cerrar esa revisión,
el agente elimina esas fotos temporales y conserva observaciones/reportes, sin
borrar insumos históricos. No hacer `git add reports/`.

## Verificación ≤2 mm

`evaluate_reserved` triangula **observaciones proyectadas**, ajusta un plano libre
sin dimensiones prefijadas, e intersecta los rayos de las esquinas físicas con
ese plano. Compara cuatro lados y dos diagonales; evalúa planitud de todos los
puntos sin filtros ni reescalado. Publica máximo, RMS, p95, cantidades y fracción
rechazada (cero: no oculta outliers). Máximo >2 mm falla; sin cota de incertidumbre
queda inconcluso aunque el residuo sea pequeño. Un control local pasado no
promociona el rig automáticamente.

Para la reproyección física, tomar blanco y luego **blanco con contornos negros**.
Detectar el borde físico en la primera y centro de línea en la segunda, con
muestreo/ROI prefijados. `metric_validation.outline_on_plane` convierte ambos
conjuntos a mm intersectando rayos calibrados con la superficie local y mide
distancias bidireccionales, sin corregir traslación/escala/homografía. Máximo más
incertidumbre ≤2 mm y cobertura completa son necesarios. Bordes no observados
quedan inconclusos. La extracción de los bordes reales y el render pertenecen a
P4: esta entrega prepara la primitiva y protocolo, no inventa fotos ni residuos.

Publicar controles de ambas calibraciones y tres repeticiones, con la zona y
profundidad verificadas, referencia y su incertidumbre. Una hoja no certifica
error absoluto de toda la sala. El estado sigue sin verificar hasta contar con
esa evidencia completa. Si falla: revisar resolución/display, saturación, foco,
movimiento, observabilidad y baseline; diagnosticar residuos espaciales globales,
sin aplicar una homografía correctiva ni mover objetos para ocultar el error.

## Contrato con P3/P4/P6

Salida joint: `format=slscan-rig/2`, `schema_version=1.0`, `artifact=rig`,
`evidence`, `units=mm`, `input_hashes`, `world`, `pixel_convention=opencv_integer_centers`,
`metric_status`, `validated_range=null`. Conserva `devices.{name}.{K,dist,R,t,size}`
y agrega `T_device_from_world`, `pose_estimated`, `model`, `uncertainty`,
`residuals_by_view`. La cámara fija el mundo por convención (`pose_estimated=false`);
el proyector sí tiene pose estimada. La pose de cada hoja queda en el reporte.
`X_device=R@X_world+t`; centro óptico `-R.T@t`; OpenCV x derecha, y abajo, z adelante.

`extra.metric_report` conserva ajuste, restricciones, reinicios, bootstrap por
vista, residuos espaciales, sensibilidad y controles reservados;
`extra.provenance` conserva SHA-256 de cada observación. JSON se escribe por
rename atómico y rechaza NaN. No se copia ni modifica `contracts.py` de P6: se
emiten los mismos campos, contrastados con su validador. Una vez integrado P6,
`python -m slscan pipeline validate captures/rig.json` hace la validación común.
`rig-plane-observation`, `rig-capture-session`, `rig-calibration-report` y
`rig-heldout-validation` son reportes auxiliares P2, no nombres de SCHEMAS de P6.

El patrón nuevo usa centros de píxel OpenCV (sin +0,5 implícito). El detector
ChArUco sobre la imagen enviada reproduce sus coordenadas a <0,05 px en test.
El flujo legado Gray tiene otra convención histórica; se usa solo para ubicar
aproximadamente la hoja, nunca como residuo del ajuste nuevo. P3 debe mantener
sus correspondencias compatibles con los centros de píxel del rig.

## Evidencia de esta entrega

Tests geométricos sintéticos: recuperación sin ruido <0,1 mm y <0,01 px,
inicialización sin el rig verdadero, ruido, sesgo no geométrico, degeneración,
resolución incompatible, falta de puntos, controles reservados sin escala libre,
proyección local a distintas profundidades, cobertura e incertidumbre faltantes.
Las pruebas de captura simulan hardware; no acreditan captura óptica real.
Resultados numéricos y estado físico se registran en el reporte de cierre.

Verificación del 17-oct-2026 en el Mac, usando el entorno existente:

- Suite completa: **273 passed, 3 skipped, 2 warnings**, 66,77 s. Los omitidos
  dependen de capturas locales no copiadas al worktree; warnings de deprecación
  de websockets/ElevenLabs. Tests específicos después de endurecer entradas y
  guía proyectada: 8 de sesión y 5 de joint, todos pasan.
- Benchmark geométrico determinista sin ruido: 10 train y 3 holdout,
  inicialización sin el rig verdadero. Máximo XYZ **7,28e-10 mm** (redondeado hacia
  arriba), RMS cámara 4,45e-12 px, proyector 5,42e-12 px, máximo dimensional
  reservado 2,25e-11 mm. Esto comprueba consistencia numérica del modelo ideal,
  no la precisión de un sensor. Ocho bootstrap de ocho completados.
- `reports/p2-synthetic/rig.json` pasó el `validate(..., 'rig')` de P6, leído
  desde su worktree autorizado. `summary.json` y `calibration.json` contienen
  resultado/procedencia. Están ignorados por git; no se escribió un rig sintético
  en `captures/rig.json`.
- **Sin toma real ni aprobación física de 2 mm.** Turno T749/Osmo solicitado a
  Creativo en q21. Disco fluctuó entre 200 MiB y 1,5 GiB; se agregó preflight,
  sin borrar datos ni temporales de otras unidades.

La semántica de proyección/distorsión sigue la documentación primaria de
[OpenCV](https://docs.opencv.org/4.12.0/d9/d0c/group__calib3d.html). El helper de
undistortion contempla la firma local de OpenCV 5 y la de OpenCV 4.

# Visor auxiliar de calibración

URL local usada para la demostración: **http://127.0.0.1:8797/**.
Está servido desde el worktree P2. El servidor nunca abre cámara ni proyector.
Usa Three.js local y SSE; no depende del runtime de mapping ni ocupa su pantalla.

```sh
/Users/max/hardware/sl-scanner/.venv/bin/python -m slscan metric viewer \
  --simulate --state reports/p2-viewer-demo --port 8797
```

Abrir esa URL en Chrome. La primera ejecución descarga solamente Three.js
0.160.0 y OrbitControls públicos en la carpeta local `vendor`; luego el navegador
obtiene código, imágenes y eventos únicamente desde localhost. No hay nuevas
dependencias de Python, npm, pesos ni instalaciones de navegador.

## Qué se ve

- Preparación de cada pose, blanco, los 44 patrones Gray, ChArUco, esquinas
  detectadas, ajuste y controles reservados. Se muestran patrón e imagen de cámara.
- Sala sintética con proyección del patrón en 3D, hoja y soporte, objetos con
  distintas profundidades, planos estimados que se acumulan y frustums de cámara
  y proyector. La referencia verdadera se puede ocultar y está etiquetada.
- Órbita libre y vista desde la cámara estimada. La vista geométrica de cámara
  es **rectificada**; la foto lateral sí incluye distorsión. Los frustums usan
  rayos con distorsión. No se presenta la cámara de navegación como cámara física.
- Residuos por vista en píxeles, baseline estimado y controles reservados en mm.
  Los botones de poses permiten inspeccionar imágenes anteriores; los controles
  reservados tienen borde discontinuo. “Volver al paso actual” retoma el seguimiento.
- Pausa, continuación y repetición de simulación con ruido, desenfoque y tiro
  diferentes. Cambiar un valor afecta la siguiente corrida, no altera datos
  mientras se están ajustando. Un fallo aparece con su causa y permite repetir.

El estado se recupera tras reconectar el navegador. Las imágenes son previews
JPEG limitadas a 1000 px de lado mayor; el solver usa las observaciones detectadas
sobre los frames nativos, no esos JPEG. Las coordenadas de cada pose y su
procedencia se guardan en `runs/<run_id>/observations/`. Se limita el historial del
visor a 100 eventos.

## Qué simula y qué no

El benchmark anterior era algebraico: puntos sobre planos, K/distorsión/poses
conocidos, ruido en coordenadas de esquinas y casos degenerados. No era una
simulación completa de sensor. Los tests legados de imágenes eran independientes.

Ahora `rig_simulator.py` intersecta rayos de la cámara con superficies de una sala
3D: muro, piso, cubo, panel, soporte y hoja A4. Resuelve oclusión en cámara y
sombras por visibilidad desde el proyector. Después muestrea el raster nativo del
proyector usando sus intrínsecos y distorsión Brown. El backend produce las fotos;
la escena Three.js es una previsualización geométrica de los mismos dispositivos,
superficies y patrón, no un segundo sensor ni un renderer PBR de referencia.

Defaults de simulación, **ilustrativos, no medidos en T749/Osmo**:

| Componente | Modelo |
|---|---|
| Resolución | Cámara efectiva 1080×1920; proyector nativo 1920×1080 |
| Tiro | Nominal fx/ancho = 1,1; editable de 0,8 a 1,6 |
| Óptica | Focales independientes, centro óptico descentrado, Brown5 en ambos dispositivos, baseline de ~245 mm |
| Sensor | Ruido gaussiano de lectura de 1,2 DN y aproximación gaussiana de ruido dependiente de señal |
| Blur | Gaussiano de 0,65 px en cámara y 0,5 px en proyector |
| Radiometría | Albedos mates, ambiente, caída con distancia/incidencia, viñeteo, gamma 2 y variación aleatoria de exposición de 0,8% |
| Cuantización | Fotos de 8 bits y Gray decodificado con `skip_bits=2` |
| Latencia | Modelo de 90 ms: si la espera es menor, retorna el patrón anterior; en la corrida normal se espera virtualmente suficiente |

No incluye Bayer/demosaicing ni el ISP de la captura 4K de Osmo, rolling shutter,
compresión de webcam, autofoco, autoexposición con su lazo real, ruido espacial
calibrado, rebotes, materiales especulares o respuesta espectral medida. La
latencia no es una medición ni se infiere del tiempo de cómputo. Los efectos de
ruido/blur son una aproximación explícita y ajustable, no un gemelo del hardware.

Las cuatro esquinas físicas se detectan en la imagen blanca; ChArUco se detecta
en la imagen proyectada, con el detector real. Se renderizan y decodifican los
44 patrones. Se conserva en memoria un recorte de la hoja para cada frame Gray;
la extensión del recorte proviene del generador solo para limitar memoria, no
se inyecta al solver como coordenadas medidas. La homografía Gray solo sitúa el
patrón adaptativo. El ajuste no recibe K, distorsión ni poses verdaderos.

Se acumulan ocho poses train y tres holdout. A partir de cuatro train, se ejecuta
una estimación provisional conjunta con modelo `k1` explícitamente restringido
para rapidez. El ajuste definitivo `metric calib` conserva Brown5, reinicios y
bootstrap del paquete P2. Los planos verdaderos son solo referencia visual;
los azules se construyen con los R/t estimados. No se usa una nube sintética
ideal para aparentar detección sobre la foto.

## La misma pantalla durante captura real

Primero iniciar el observador, sin `--simulate`:

```sh
python -m slscan metric viewer --state reports/p2-live --port 8798
```

**Solo después de la aprobación de q21 y liberación T749/Osmo**, agregar
`--viewer-state reports/p2-live` al comando de [captura P2](P2-RIG.md). Ejemplo
preparado, todavía no ejecutado en hardware:

```sh
python -m slscan metric capture --display-confirmed \
  --poses 10 --holdout 3 --paper-mm 210x297 --settle 2 \
  --out reports/p2-session-a --viewer-state reports/p2-live
python -m slscan metric calib reports/p2-session-a \
  --out captures/rig.json --report reports/p2-rig-report.json \
  --viewer-state reports/p2-live
```

`capture_sequence(..., observe=callback)` entrega al observador exactamente el
frame que acaba de guardar. La adquisición real y simulada usan `Journal`,
previews y el mismo contrato de estados/SSE. En real no aparece geometría
verdadera inventada. Tras cuatro poses se intenta una estimación provisional;
si falla, conserva las observaciones y muestra el motivo. El ajuste final
actualiza los planos y residuos del visor. No se puede mezclar evidencia real y
sintética al reanudar un Journal.

El visor de captura es de solo lectura: sus endpoints no tienen control de
hardware. Solo la instancia `--simulate` acepta pausa/repetición; exige mismo
origen. Sirve únicamente HTML/JS/CSS conocidos, las bibliotecas locales y JPEG
de preview; nunca rutas arbitrarias, Python ni archivos de sesión privados.

## Validación y límites

Se verificaron en Chrome headless con WebGL las vistas de órbita y cámara a
1440×1080, y responsive a 390×844: sin errores JS ni desborde horizontal. Se
inspeccionaron screenshots localmente, todos de la sala sintética. Tests nuevos
cubren imagen→detección, Gray realmente renderizado→decode, ruido entre frames,
latencia que devuelve un frame anterior, hoja dentro de ambos FOV, publicación de
previews, aislamiento de archivos/origen y callback real con frame guardado.

La suite completa pasó con **280 tests, 3 omitidos** por datos locales y dos
warnings de dependencias existentes. Una corrida completa de 11 poses obtuvo
RMS de ~0,15 px cámara/~0,20 px proyector, máximo dimensional reservado ~1,04 mm
y máximo de planitud ~0,65 mm. Estos
controles quedan **inconclusos**, porque no se ha aportado una cota de incertidumbre;
su procedencia es sintética. No acreditan ni sustituyen los controles físicos
≤2 mm. La toma real permanece pendiente de q21.

La geometría del visor usa matrices y órbita de
[Three.js](https://threejs.org/docs/pages/PerspectiveCamera.html), con cambio
explícito de base OpenCV→render `diag(1,-1,-1)` y frustum asimétrico desde K.

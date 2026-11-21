# P4 · Escena registrada, sin hardware

`slscan/registered.py` publica una escena métrica autocontenida desde contratos
P2/P3/P6. `playground/web/registered.html` usa Three.js 0.160.0 para navegar y
renderizar desde el proyector calibrado. No abre navegador, cámara ni ventanas.

```sh
python -m slscan.registered build --rig /ruta/rig.json --mesh /ruta/mesh.json \
  --objects /ruta/objects.json --materials /ruta/materials.json \
  --out reports/registered/escena
python -m slscan.registered serve reports/registered/escena/scene.json --port 8798
# Visor: http://127.0.0.1:8798/web/registered.html
```

Objetos y materiales son opcionales. Si mesh.json no contiene `rig_sha256`, pasar
`--cloud /ruta/cloud.json`: se comprueba la cadena mesh → cloud → rig. La salida
debe ser nueva y estar fuera de las carpetas de entrada. No se mutan capturas ni
texturas hardlink de P3. Se validan versiones, hashes, mundo, poses, conteos,
índices, máscara de textura y rutas relativas. Un fallo deja staging sin publicar
para inspección. Un rig real requiere poses estimadas; evidencia sintética jamás
se promociona a precisión física. Se conserva el estado original del rig aparte
del estado de la escena, que permanece sin verificar.

En un runtime existente, cargar explícitamente mediante su `/api/command`:

```json
{"cmd":"registered_scene","args":{"path":"/ruta/scene.json"}}
```

Eso deja la salida negra y activa el modo métrico. El botón «Enviar al runtime»
transporta PNG por `/api/registered/frame?scene_sha256=...`. Solo admite 1920×1080
en frame, rig y canvas; sin resize, nudges, `salida.json`, offsets ni efectos 2D.
En el transporte existente a `PatternWindow(screen="1920x1080")` se exige también
ese tamaño real de ventana. La ruta `serve` nueva no abre ese transporte físico.
Si los frames vencen o se detiene el envío, permanece negro. No vuelve a efectos
legados por timeout. Reload de escena invalida frames de otro hash.

## Geometría y render

- Mundo OpenCV persistente en mm; `Xdevice = R Xworld + t`. Render mundo usa
  `B = diag(1,-1,-1)` y la vista `B Tdevice_from_world B`. Centro `−Rᵀt`, sin poses
  supuestas. El frustum usa fx/fy/cx/cy, no un FOV simétrico. Se rechaza skew no cero.
- Centros enteros OpenCV. Los límites raster son −0,5 y tamaño−0,5; el +0,5 se
  aplica exclusivamente al pasar de centro de píxel a coordenada de framebuffer.
- Dos cámaras independientes: navegación con OrbitControls y proyección calibrada.
  Cámara física y proyector tienen marcadores y frustums **rectificados**. El warp
  de distorsión se aplica solamente a la salida. Clipping: 1–20.000 mm.
- Render ideal con profundidad y margen óptico; mapa inverso float32 por píxel:
  salida distorsionada → rayo sin distorsión → UV del render ideal. OpenCV soporta
  4/5/8/12/14 coeficientes (racional, prisma y tilt). Se comprueba el roundtrip de
  todos los píxeles a <0,01 px y se rechaza una lente no invertible. Margen limitado
  a 4× los píxeles originales y 8192 por dimensión; cálculo en bloques de 32 filas.
- Render target y canvas de salida tienen tamaño nativo y DPR=1. CSS, navegación
  y DPR del dispositivo solo modifican la presentación. El shader convierte
  lineal → sRGB al final; UV de P3 top-left, textura `flipY=false`.
- Solo caras cuyos tres vértices son medidos. Las caras con textura inválida
  conservan profundidad y aparecen grises. El z-buffer del proyector genera
  máscaras por objeto: las caras ajenas siguen ocluyendo, pintadas negras.
  «Blanco con contornos negros» traza la silueta visible del objeto seleccionado;
  «Blanco de referencia» ilumina todo el frame para la primera foto.
- Apariencia fotográfica no es albedo. PBR opcional usa un único material global
  con albedo/normal OpenGL/roughness y luz de preview explícita. El contrato v1 no
  asigna caras a múltiples materiales: en ese caso no se inventa una asignación.
  No hay compensación radiométrica aplicada sin un adaptador validado de P7.

El visor legado sigue disponible en `/web/`; no se promociona su relieve relativo
a métrico. El visor de P2 se revisó como referencia de las convenciones y cámaras;
no se modificó su rama ni su servidor.

## Verificación offline por objeto

```sh
python -m slscan.verify_registered /ruta/packet.json --out reports/validation.json
```

El packet contiene rutas `scene`, `white`, `outlined` relativas al packet,
`scene_sha256`, `camera`, `evidence`, y afirmaciones comprobadas `stationary`,
`exposure_verified`, `controls_prefixed`. Ambas fotos deben tener exactamente el
frame calibrado. Por objeto requiere:

```json
{
  "id":"panel_1",
  "samples_px":[[100,80],[100,90],[100,100]],
  "normals_px":[[1,0],[1,0],[1,0]],
  "surface_plane_world":[0,0,1,-1000],
  "sample_lengths_mm":[10,10,10],
  "uncertainty_mm":0.3,
  "held_out":true
}
```

Es un ejemplo de estructura, **no controles ni incertidumbre reales**. Fijar
controles, normales, longitudes representadas y plano local antes de evaluar.
La foto blanca localiza el borde físico; blanco menos foto de contorno localiza
el centro de la línea negra. Perfiles ambiguos, ausentes o fuera de imagen quedan
faltantes. `extraction` opcional fija radius_px=8, step_px=0.25, min_edge=8 y
min_darkening=12; no reajustarlos después para mejorar el reporte. Se intersectan
rayos calibrados con el plano de cada superficie y se calcula distancia
bidireccional a polilíneas, sin unir huecos. Un objeto no plano requiere controles
separados sobre sus parches locales; no usar una escala global px→mm.

Publica máximo/RMS/p95, longitud cubierta, faltantes e incertidumbre. Solo un
check local con cobertura completa, controles reservados y máximo+incertidumbre
≤2 mm puede pasar; sintético queda `unverified`. Traslación/similitud/afín/homografía
son diagnósticos sin aplicar correcciones ni atribuir automáticamente la causa a
baseline/FOV. Fotos y datos geométricos originales no se embeben en el reporte.

`physical_verified` permanece **false**: faltan dimensiones/planitud de referencia,
tres poses físicas reservadas y tres repeticiones inmóviles. Si hubo movimiento,
corresponde relocalizar o repetir el scan, nunca mover cada objeto para compensar.

## Integración y pruebas

Adaptador opt-in P6: `adapters['register_render'] = slscan.registered.pipeline_adapter`.
Publica bundle y hashes de dependencias; no declara render GPU ejecutado. P6 no
tiene `segment` como dependencia de `register_render`, por eso ese adaptador no
incorpora objetos potencialmente obsoletos. Para verificación con P5, usar `build
--objects` con hashes vinculados o añadir explícitamente esa dependencia al DAG.

```sh
python -m pytest tests/test_registered.py -q -p no:cacheprovider
SLSCAN_THREE_VENDOR=/ruta/vendor python -m pytest tests/test_registered_browser.py -q
python -m pytest tests -q -p no:cacheprovider
```

Prueba de navegador opcional: Chrome headless y carpeta local con
`three.module.js` y `OrbitControls.js` de Three.js 0.160.0. No descarga paquetes ni
abre ventanas; usa servidor loopback y perfil temporal propios. La UI habitual
conserva el CDN fijado del visor existente. `SLSCAN_BROWSER_REPORT` guarda solo
captura **sintética** y métricas en la carpeta indicada.

Verificado en sintético: 4.096 puntos por matrices JS, más 2.048 con
`THREE.Vector3.project` real, máximo **9,38×10⁻¹³ px** frente a OpenCV en la prueba
headless. Los cinco modelos de distorsión se validan en todos los píxeles de sus
fixtures, incluidos bordes, con error float32 <0,001 px. GPU: textura orientada,
oclusor conservado, máscara comparada con geometría analítica, DPR=2 invariante y
PNG 1920×1080 GPU → HTTP → runtime → PNG idéntico. No se verificó display físico,
latencia de proyección, escena actual ni máximo físico de 2 mm.

Cierre del 17-oct-2026: focales + visor legado **21 passed, 1 skipped**; suite
completa ejecutada una vez al cierre **316 passed, 3 skipped, 2 warnings, 75,22 s**.
Los skips requieren capturas locales ausentes en este worktree; los warnings son
deprecaciones existentes de websockets/ElevenLabs. Chrome headless sí se ejecutó
en ambas corridas con su prueba GPU. `node --check` y `git diff --check` sin errores.
Demo, captura sintética y métricas quedan en `reports/p4/` fuera del commit (1,4 MB,
incluida la copia local de Three para las pruebas). No se copiaron fotos de escena.

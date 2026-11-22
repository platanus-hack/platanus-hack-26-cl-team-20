---
id: 010
title: iPad Pro (LiDAR) como cuarto sensor RGB-D fusionado con las otras 3 cámaras
kind: tool
from: secretaria
status: open
created: 2026-10-15T19:35
priority: 8        # Max no la ordenó; propuesta: después de 009 (comparte la fusión multicámara)
---
Palabras de Max: su iPad Pro tiene dos lentes y un punto: "Esa es otra cámara más." (relato de h1; el modelo 2020 con LiDAR).

**Qué.** Sumar el iPad Pro como cuarto sensor del playground, y el primero que da **profundidad medida** (LiDAR) además
de color. Su feed RGB-D entra a la fusión de 009 junto con la FX3 (cenital), la webcam (pared) y la Osmo Pocket (proyector).

**Hardware (de lo que dice Max; sin verificar el modelo exacto).** iPad Pro con dos lentes (gran angular y ultra
gran angular) y sensor LiDAR: es el modelo de 2020 o uno posterior. Falta confirmar si es de 11" o 12,9" y la
generación, y que iPadOS esté al día. Se verifica en Ajustes > General > Información.

**Para qué.**
1. **Densificar la geometría.** El LiDAR da un mapa de profundidad directo, sin depender de luz estructurada ni de
   superficies con textura. Completa huecos del escaneo (zonas que el proyector no alcanza, superficies brillantes,
   hojas de plantas) y sirve de semilla y de control de escala de la reconstrucción (008, 009).
2. **Tracking de manos y personas.** Con profundidad se obtiene la altura del dedo sobre la mesa (touch, 004; teclas, 006),
   y la posición 3D de cuerpo y manos sin triangular, mejor oclusión y menos confusión entre objetos.
3. **Verdad de referencia.** Contrastar las medidas de la fusión de las otras cámaras contra profundidad medida.

**Límites a tener presentes (de conocimiento general del sensor; no probados aquí).** El mapa de profundidad del
LiDAR del iPad es de baja resolución (del orden de cientos de píxeles por lado, se rellena con el color), con
alcance de unos pocos metros, ruido en superficies muy brillantes u oscuras, y no es para patrones finos ni
precisión de submilímetro como la luz estructurada. Su aporte es completitud y robustez, no reemplazar el escaneo.

**Opciones de captura (a decidir por h3).**
- **A. Record3D** (app de iOS) con su librería Python oficial `record3d`: transmite RGB y profundidad por USB (modo
  USB Streaming, mejor calidad) o por Wi-Fi (profundidad con compresión con pérdida). El Mac o el PC la reciben en
  Python. Es la opción más rápida de probar. En macOS/Windows pide iTunes o su soporte USB; en Linux, `libusbmuxd`.
  Hay una app distinta con nombre parecido (Record3DStream); hay que usar la oficial. Licencia y precio: sin revisar.
- **B. App propia con ARKit** (Swift): control total (resolución, confianza de profundidad, poses de cámara por
  cuadro, sincronía), mandando por WebSocket o UDP al PC. Más trabajo y requiere Xcode y cuenta de desarrollador de Max.
- **C. App de escaneo (por ejemplo Polycam o 3D Scanner App)** exportando nubes de puntos o mallas: sirve para
  captura estática (un modelo de la sala) pero no en vivo. Útil como línea base, no como sensor vivo.
- **D. Solo RGB** (NDI HX Camera o similar): sin LiDAR, aportaría solo otra vista de color. No cumple el pedido.

**Cómo.**
- Marco común: el iPad entra con su pose (ARKit da pose por cuadro, lo que sirve para fusionar aunque se mueva) y se
  registra al marco de la mesa, igual que las otras 3 cámaras (009).
- Sincronía: estampar tiempo en cada cuadro y alinear con las otras cámaras (hoy no hay reloj común; el enlace
  inalámbrico agrega retraso, ver 005).
- Montaje: trípode o soporte fijo, sin tapar el área de proyección, preferentemente cargando por cable (el
  streaming por USB también carga).

**Cómo se ve.** `pg_cameras` lista el iPad como cuarta cámara con rol `rgbd`, resolución de color y de profundidad,
fps y pose; `pg_snapshot` o una vista combinada muestra color y profundidad; un objeto aparece en
`pg_scene_objects` con malla más densa que antes; el dedo sobre la mesa entrega altura medida.

**Criterio de listo.**
- El iPad entrega RGB y profundidad en vivo al Mac o al PC (opción elegida y anotada), con fps y latencia medidos.
- Se registra al marco de la mesa con error en mm medido contra puntos conocidos, y se suma a las otras 3 cámaras.
- Con el iPad, un objeto (por ejemplo el cocodrilo) tiene malla/nube más completa que sin él (comparación en cifras: puntos
  o cobertura).
- La altura de un dedo sobre la mesa se mide con error en mm anotado (insumo para 004 y 006).
- Si el iPad se desconecta o se mueve, el sistema sigue con las otras cámaras y avisa.

**Dudas / choques.**
- Confirmar el modelo (11" o 12,9", generación) y la versión de iPadOS.
- ¿Prefiere Max instalar una app existente (Record3D) o una propia con ARKit? Lo primero es rápido; lo segundo
  más preciso y sincronizado.
- No hay forma de que el iPad vea el patrón del proyector como luz estructurada y a la vez se use su LiDAR: el LiDAR
  usa luz infrarroja propia y no interfiere con el proyector visible. No lo probé.
- Sin app instalada ni cable a la vista, esto queda como diseño; no se verificó nada en el equipo de Max.
- Nada se compra: la opción A puede tener costo de la app; se consulta con Max antes.

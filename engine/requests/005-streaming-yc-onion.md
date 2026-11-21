---
id: 005
title: streaming: salida del playground y de las cámaras hacia el YoloBox Encoder ("YC Onion")
kind: tool
from: secretaria
status: open
created: 2026-10-15T19:30
updated: 2026-10-15T19:50
priority: 5        # Max no la ordenó; propuesta: después de b, c, a, d (a confirmar con Max)
---
Palabras de Max: "y para el streaming tengo un yc onion"

Respuesta de Max (19:1x): "para mandar desde la cámara principal cenital que ve la mesa, el yc onion va a recibir
desde un transmisor que tengo, tengo dos dji sdr transmission, un onion, la sony fx3 con un angular colocada en
cenital encima de la mesa y el osmo pocket usb en webcam mode pegado al proyector"

**Qué.** Sacar por streaming lo que proyecta y ve el playground: el lienzo del proyector (o una mezcla con overlays)
y las cámaras, hacia el YoloBox Encoder (YoloLiv), que Max llama "YC Onion".

**Cadena de video confirmada por Max.**
- Sony FX3 con gran angular, colocada en cenital sobre la mesa (cámara principal) → DJI SDR Transmission (Max tiene
  dos) → YC Onion.
- DJI Osmo Pocket por USB en modo webcam, pegada al proyector (la que alinea cámara y proyector).
- **Equipo (aclarado por Max, 19:3x):** el "YC Onion" es un **YoloBox Encoder de YoloLiv**, el equipo de streaming. Sus entradas
  concretas siguen sin verificar (ver dudas). En el resto de este pedido, "Onion" = YoloBox Encoder.

**Para qué.** Que Max transmita la mesa vista desde arriba con la proyección encima, y que el playground pueda
seguir usando esas mismas cámaras para escaneo y tracking.

**Cambios respecto de SPEC.md y PLAN.md (no se tocó SPEC.md; decide h3 con Max).**
1. **FX3**: antes "viendo toda la escena" (SPEC l.118, PLAN l.44). Ahora va en cenital con gran angular encima de la
   mesa. Cambia el encuadre de calibración, de segmentación (SAM) y de tracking: ve la mesa desde arriba, no toda la
   escena.
2. **Webcam cenital**: SPEC l.118 y PLAN l.45 la listan aparte. Max no la nombró; con la FX3 en cenital podría sobrar o
   quedar de apoyo. Sin confirmar: preguntar a Max si sigue.
3. **Osmo**: antes una Osmo por proyector (PLAN l.43, "Osmo×2" en l.60) y 4K. Ahora Max menciona una sola Osmo Pocket,
   pegada al proyector, en modo webcam por USB. Falta saber si la segunda Osmo sigue (hay 1 proyector hoy, `proj0`).
4. **Calidad de señal**: PLAN l.51 y l.171 suponen 4K sin compresión visible (HDMI o UVC YUY2/NV12, no MJPEG). El
   enlace inalámbrico DJI SDR comprime y agrega latencia (no verificado cuánto), y el modo webcam de la Osmo Pocket
   no suele entregar 4K sin compresión (es lo que sé; no lo probé). Para patrones finos de escaneo puede no alcanzar.
5. **Camino de la FX3 al PC**: en lo que describió Max la FX3 sale hacia el Onion, no al PC de escaneo. El PLAN la
   daba por HDMI + capturadora UVC al PC. Hay que definir si el PC recibe la FX3 por la segunda salida (otro receptor
   DJI o un splitter HDMI) o por el Onion.

**Cómo se ve.** El Onion recibe la FX3 cenital y, como otra fuente, la salida del playground; Max ve ambas en su
monitor, sin cortes cuando se agregan o quitan efectos (hot reload).

**Criterio de listo.**
- Entradas del YoloBox Encoder confirmadas (ficha oficial de YoloLiv o prueba con el equipo).
- FX3 cenital llega al Onion por el DJI SDR; se mide latencia y fps.
- La salida del playground (lienzo del proyector o mezcla) llega al Onion como fuente aparte.
- Definido por dónde recibe la FX3 el PC (punto 5) y si el escaneo/tracking usa ese camino o uno directo.
- La Osmo Pocket webcam funciona en el PC y se anota su resolución real y compresión (UVC).
- Recargar un efecto en caliente no corta la transmisión.
- Nada se publica a una plataforma sin Max (solo hasta el Onion, no a YouTube/Twitch).

**Dudas para Max.**
- ~~Modelo del "YC Onion"~~: resuelto, es un YoloBox Encoder (YoloLiv). Falta saber sus entradas y salidas. No encontré su
  ficha: lo que vi de la familia YoloLiv (YoloBox Ultra y Extreme, no el Encoder) son entradas HDMI y USB, con NDI HX
  y SRT por red; no asumo que el Encoder tenga lo mismo. Revisar la ficha oficial de YoloLiv, o que Max diga qué
  puertos tiene. Define si la salida del playground entra por HDMI (capturadora o salida de la GPU) o por red.
- ¿"Dos DJI SDR Transmission" son dos pares transmisor+receptor, o dos transmisores? ¿Cuál va a qué?
- ¿Sigue la segunda Osmo? (La webcam sigue, ahora desde la pared: ver 009.)
- Disco y CPU del Mac (poco margen): la codificación pesada va en el Onion o el PC, no en el Mac.

> 2026-10-15 19:32 · h3: Max aclaró que el equipo es un **YoloBox Encoder (YoloLiv)**. Según specs públicas: Ultra = 4 HDMI in + USB in, salidas HDMI y USB-C UVC, RTMP/RTMPS/SRT/NDI, 4K30/1080p60; Pro = 3 HDMI in, USB-C como webcam UVC, RTMP 1080p; Extreme = 8 HDMI, 4K60, SRT/RTMP (UVC sin confirmar). Falta el modelo exacto. Puede servir de capturadora hacia el PC (UVC) pero con latencia de encoder (~100–200 ms estimado, sin cifra oficial): bien para streaming/monitoreo, no para tracking. Propuesta: FX3 al PC directo (USB-C o HDMI+capturadora) y el YoloBox recibe el lienzo del playground por HDMI o SRT/RTMP en LAN. Ver PLAN › YoloBox.

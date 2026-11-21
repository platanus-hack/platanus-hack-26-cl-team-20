---
id: 014
title: los 3 monitores como superficies del playground y piano roll que cae desde las pantallas hasta las teclas
kind: visual
from: secretaria
status: open
created: 2026-10-15T19:55
priority: 4        # Max no la ordenó; propuesta: después de 006 y 012 (depende de ambos)
---
Palabras de Max: "el teclado sí se puede conectar a midi al pc y además 2 de los 3 monitores los puedo poner justo
sobre las teclas y el otro sobre la mesa, entonces podemos usar todos los monitores en full screen y que las teclas
que vienen estilo piano rolling midi scroll pasen de la pantalla y se proyecten a las teclas"

**Corrección de Max (19:4x):** "el otro monitor sobre la ddj". El tercer monitor va **sobre la DDJ-SB2**, no sobre la
mesa. El pedido queda así: **2 monitores sobre el teclado y 1 sobre la DDJ-SB2.** Donde antes decía "monitor sobre la
mesa", ahora es el monitor de la DDJ.

**Qué.** Usar los 3 monitores del escritorio como superficies más del playground, en pantalla completa: **2 sobre el
teclado y 1 sobre la DDJ-SB2** (así lo dijo Max). En los dos de arriba del teclado corre un **piano roll de notas que
caen** (estilo Synthesia: bloques que bajan hacia las teclas) y, cuando llegan al borde de la pantalla, **siguen sin
corte** como luz proyectada sobre la tecla que les corresponde. Las notas pasan de la pantalla al proyector como una sola animación.

**Para qué.** Tocar el teclado con las notas viniendo hacia las teclas, aprendiendo o interpretando una pieza, y
que la escena (monitores + proyección) se vea como una sola superficie continua.

**Cómo (propuesta; lo decide h3).**
1. **Los monitores son objetos de la escena** (`kind: screen`, ya hay uno, `pantalla`): cada uno con su plano, tamaño,
   resolución y pose en el marco 3D de la mesa; cada uno es un "proyector" más con su lienzo, en fullscreen, desde el
   PC. Se calibran junto a las teclas (006) y a los proyectores (012) en el mismo espacio 3D, con ArUco/ChArUco
   mostrado en cada pantalla y visto por las cámaras (009).
2. **Piano roll:** fuente de las notas que vienen: un clip de Ableton (el MCP `ableton` tiene `get_clip_notes`), un
   archivo MIDI o lo que se toca. Cada nota cae en el carril de su tecla (el mapeo tecla↔nota viene de 006), con
   largo = duración, color = pista o velocidad.
3. **Continuidad en la junta pantalla→teclas:** una sola "línea de tiempo" en mm/segundo para todo el recorrido; el
   bisel del monitor y la distancia entre pantalla y teclas son "espacio que se recorre" sin dibujar (la nota
   desaparece detrás del bisel y reaparece en la tecla en el momento exacto). Misma velocidad y sin salto de fase al
   cruzar.
4. **Sobre las teclas (proyector):** el bloque se estira o se ilumina sobre la tecla, con el contorno de cada
   tecla (006); al tocarla (MIDI al PC) cambia de color, o se marca acierto o error.
5. **El monitor sobre la DDJ-SB2 (el tercero):** superficie para lo que acompaña a ese controlador: por ejemplo forma
   de onda y fase de cada deck, indicadores del valor de cada perilla/fader (leídos por MIDI o visión, 003), el beat
   y el tempo. También puede mostrar efectos de la galería (`frame_feed`, `texture_grid`). Su calibración 3D lo
   relaciona con la DDJ, para que lo que muestre y lo que el proyector ilumine sobre los controles se vean como una sola
   superficie (misma lógica de cruce que en el teclado, cuando haga falta). Qué debe mostrar lo define Max.

**Cómo se ve.** Las notas caen por las dos pantallas encima del teclado, pasan el borde y se apoyan sobre cada tecla
como luz; la tecla se ilumina cuando debe sonar y cambia al tocarla. `pg_state` lista los 3 monitores como
superficies con pose y los efectos que corren en cada uno; `pg_spawn piano_roll` (o `keys_light`, ya nombrado en PLAN)
con `source` (clip, archivo, MIDI en vivo), `speed`, `lead_time`.

**Criterio de listo.**
- Los 3 monitores (2 sobre el teclado, 1 sobre la DDJ-SB2) en fullscreen como superficies del playground, cada uno con pose calibrada en el marco de la mesa y
  error medido en mm.
- Una nota conocida (por ejemplo Do central) cae por la pantalla y aterriza en la tecla correcta (verificado a ojo y
  con foto de `pg_snapshot`); vale para al menos 12 teclas.
- El cruce pantalla→tecla no tiene saltos de velocidad ni de tiempo visibles: la nota llega a la tecla a la hora de
  la nota (error medido en ms contra el MIDI del teclado).
- Una nota tocada en el teclado se marca en la proyección (acierto/error).
- El efecto es de la galería, con parámetros y hot reload.

**Riesgos y dudas.**
- **Salidas de video: faltan.** La RTX 4070 tiene 4 salidas (según Max; no verifiqué el modelo exacto) y se
  necesitan **3 monitores + 2 proyectores = 5** (el segundo proyector es el handheld, ver 012). Opciones para
  decidir con h3 y Max: (a) uno de los dos va al **Mac** (por ejemplo el monitor de la DDJ, con su contenido
  mandado por red o renderizado en el Mac; ahí el MIDI de la DDJ y Ableton ya están en el Mac, lo que ayuda), (b) usar la salida de video de la placa madre del PC si la CPU tiene
  gráficos integrados, (c) una segunda tarjeta o un adaptador USB (con más latencia), (d) el handheld por
  inalámbrico, que no ocupa salida de la GPU. Cada una tiene costo en latencia o en dinero; **nada se compra sin Max.**
  Lo que necesitamos de Max: qué salidas de video exactas tiene la GPU y la placa del PC, y qué proyector es el handheld.
- **Sincronía entre pantallas y proyector:** cada salida tiene su propio refresco y retraso; el cruce sin corte
  exige medir y compensar esas diferencias (cámara viendo el cruce, o fotodiodo).
- Si algún monitor se mueve, la calibración 3D se rehace (robustez que pidió Max a las 18:00).
- **Posición de los monitores:** "justo sobre las teclas" puede ser encima (colgados o en alto, frente al músico) o
  tras el teclado; el piano roll solo funciona si caen hacia las teclas. Confirmar con Max la geometría exacta antes de calibrar.
- Depende de 006 (teclas detectadas y MIDI), 003 y 001 (la DDJ como dispositivo y sus controles), 012 (segundo proyector y salidas), 011 (dónde corre el render) y 009 (cámaras).

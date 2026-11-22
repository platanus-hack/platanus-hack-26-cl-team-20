---
id: 045
title: portales con perspectiva por observador (ventanas y túneles hacia otros mundos, una por persona según su mirada)
kind: visual
from: secretaria
status: open
created: 2026-10-17T18:55
priority: 9        # Max no la ordenó; depende de P4 y del tracking de personas. Propuesta: después de la base de cámaras (009) y personas
---
Palabras de Max (17-oct, 18:4x): "tengo una idea increíble para la interactividad: quiero un modo en donde se puedan
crear ventanas/portales hacia otros mundos, túneles por así decirlo. En un mapping normal no se pueden proyectar
cosas en 3D porque no se sabe dónde está el observador, y menos con mucha gente. Pero en este engine, como ya
tenemos multicámara y 3D world understanding general, podemos usar eye tracking y head tracking para hacer un best
guess del gaze vector de cada participante de la sala y la posición de su mirada, por lo tanto pueden ver a través de
la ventana de manera interactiva. Obvio, si otra persona la mira la va a ver distorsionada, pero puede haber n
ventanas para n personas y cada uno tener su gaze"

**Qué.** Un modo en que una superficie real de la malla (pared, mesa, un cuadro, una zona cualquiera) se vuelve una
**ventana o portal** a un mundo 3D virtual (un túnel, un paisaje, una sala). Lo que se proyecta no es una imagen plana: es
el mundo visto **desde los ojos de la persona que mira**, de modo que al moverse ella ve paralaje real, como si de
verdad hubiera un hueco en la pared. Hay **n ventanas para n personas**, cada una con la perspectiva de su dueño.

**Para qué.** Meter profundidad 3D de verdad en el mapping, que normalmente es imposible porque el proyector no sabe
dónde está el observador. Aquí sí lo sabemos: tenemos multicámara (009), comprensión 3D de la escena (P4) y
tracking de personas.

**Cómo (propuesta; la concreta h3).**
1. **Posición de cabeza y ojos de cada persona en 3D**, en el marco de la mesa (del que ya viven mallas y
   dispositivos): tracking de personas multicámara, con cabeza y cara.
2. **Vector de mirada (gaze) por persona:** pose de la cabeza como base fiable y dirección de los ojos como
   refinamiento (con librerías abiertas; por ejemplo estimadores de gaze y malla facial con iris, a elegir por h3; sin verificar cuáles
   rinden a la distancia de la sala). El resultado es un "best guess" con incertidumbre, como dice Max, no una medida exacta.
3. **Asignación ventana↔persona por mirada:** se lanza un rayo desde los ojos de cada persona a lo largo de su
   gaze; el portal que el rayo toca (sobre la malla de la escena) pasa a ser la ventana de esa persona y se renderiza para ella.
4. **Render fuera de eje (off-axis):** para cada ventana, se calcula el frustum asimétrico definido por el ojo del
   observador y las 4 esquinas (o el contorno) de la superficie, se renderiza el mundo virtual con esa cámara y se
   proyecta sobre la superficie real con la calibración del proyector. Desde el punto del observador, la imagen
   proyectada coincide con lo que vería a través de un hueco. Misma matemática que las pantallas con head tracking
   y las salas CAVE.
5. **Varias personas en la misma ventana:** ver "degradación" abajo.
6. Efecto de la galería (`portal`) con parámetros: superficie (objeto, plano o región de la malla), mundo (túnel,
   cielo, mar, otros), observador (persona por id o "el que la mire"), profundidad del mundo, suavizado de movimiento.

**Degradación cuando dos o más personas miran la misma ventana.** Una superficie física solo puede mostrar una imagen
por punto, así que **no se puede ser correcto para dos puntos de vista distintos a la vez**; hay que decidir cómo degradar,
y h3 elige y prueba con Max (cada una se anota con lo que sacrifica):
- **Prioridad a uno** (el más cercano, el que la abrió, o Max): ese ve bien y los demás ven distorsión. Es lo que
  Max asumió ("si otra persona la mira la va a ver distorsionada").
- **Punto de vista compartido** (promedio ponderado de las cabezas): nadie lo ve perfecto, todos lo ven razonable.
- **Reducir la profundidad:** el mundo se aplana a medida que aumentan los observadores, porque lo lejano se deforma
  menos con el cambio de punto de vista; un túnel muy profundo se desfigura más que uno corto.
- **Separar por superficie:** si hay otra superficie disponible (o el segundo proyector, 012), se abre una ventana
  aparte para el segundo observador.
- Visibilizar la degradación (por ejemplo borde que cambia de color) para que Max la vea y la pueda ajustar.
- Fuera de alcance: gafas obturadoras o polarizadas para ver dos imágenes a la vez (haría falta comprar hardware;
  no se compra nada sin Max).

**Cómo se ve.** Max se para frente a un muro con un portal abierto: ve un túnel que se hunde y, al mover la cabeza, el
túnel cambia de perspectiva como si fuera real. Otra persona, en otra ventana, ve su propio túnel con su propio punto de
vista. Si ambas miran el mismo, una ve bien y la otra ve deformación (o la degradación elegida). `pg_state` lista
las ventanas, la persona de cada una y su estado (`activa`, `compartida`, `sin observador`); `pg_spawn portal` crea una
sobre un objeto o plano; `pg_set_param` cambia mundo o profundidad.

**Criterio de listo (primera etapa).**
- Una ventana sobre una superficie plana, con un observador (la cabeza de Max) rastreado en 3D. Al mover la cabeza,
  un objeto virtual "detrás" de la ventana mantiene su posición en el espacio: error angular medido (por ejemplo en grados)
  desde el punto de vista del observador contra una marca física, y anotado.
- **Latencia de movimiento a luz** (mover la cabeza → la imagen proyectada se actualiza) medida y anotada, con predicción
  de pose si hace falta. Es el criterio duro: si es alta, la ventana "nada" y rompe la ilusión.
- Dos ventanas a la vez con dos personas, cada una con su perspectiva; la asignación por mirada se verifica
  (cada persona ve su ventana bien y la otra distorsionada), con una foto desde cada punto de vista.
- Dos personas mirando la misma ventana: la degradación elegida funciona y queda anotada qué se sacrifica.
- Si se pierde el tracking de una persona, su ventana se congela o se apaga con suavidad; sin saltos bruscos.
- Gaze: se anota el error real del vector de mirada a la distancia de la sala (si solo la cabeza es fiable, la ventana se
  gobierna por cabeza y el gaze queda como refinamiento).

**Dependencias.**
- **P4:** escena registrada en un marco mundo persistente, con superficies de malla y calibración de proyector
  (según `docs/PIPELINE.md`, P4 produce `scene.json`). Sin esa registración la ventana no cae donde debe.
- **Tracking de personas** (cabeza, ojos, varias personas, identidad entre vistas): 009 y lo de personas del PLAN.
- Proyector calibrado con distorsión de lente (012) y baja latencia Mac↔PC (011).

**Dudas / riesgos.**
- **Eye tracking a distancia con cámaras de sala es poco fiable** (los ojos se ven pequeños y la mirada se confunde); por
  eso la base es la cabeza y el gaze afina. Anotado en el criterio de listo.
- **Una persona ve con dos ojos:** el render sale desde un solo punto (el centro de la cabeza o el ojo dominante), así
  que hay una pequeña inconsistencia estereoscópica; no se corrige sin gafas.
- **Oclusión:** el cuerpo del observador o de otra persona puede tapar el haz del proyector sobre la ventana; hay
  que ver con el segundo proyector (012) o con la posición de los proyectores.
- **Privacidad:** esto sigue las cabezas y miradas de todos los asistentes de la sala, incluyendo invitados. Qué se
  guarda (nada, por defecto: solo el estado en vivo) y cómo se avisa lo decide Max.
- **Luz en los ojos:** proyectar contenido brillante hacia donde está la cara; límite de brillo lo decide Max (mismo
  punto que 008 y 013).
- Se parece a `frame_feed` (plano rectificado a 4 esquinas) pero con perspectiva por observador y mundo 3D; puede
  reutilizar la rectificación de superficies.

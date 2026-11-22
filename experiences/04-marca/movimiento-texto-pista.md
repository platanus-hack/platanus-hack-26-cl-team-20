# Movimiento del texto en la pista

Estado: propuesta para evaluación visual. Reemplaza el tiempo del ensayo actual «Tinta en movimiento» del nodo `60-2` por un ritmo empatado con los cuatro momentos de la lámina 09. Pendiente de aprobación de Pablo y de ser reflejada en Figma.

## Por qué

El ensayo actual demuestra que la metáfora de tinta se puede extender del logotipo a la pista. El movimiento es coherente con «La tinta toma forma» de la lámina 09, pero tiene dos diferencias que lo hacen sentir desacoplado del logo: amplitud de ondulación más grande que las curvas internas del logotipo, y una deformación de las letras que ocupa un tiempo mayor que el del gesto equivalente en la firma. Empatarlo al mismo ritmo cierra el sistema: una sola gramática de tinta para marca y pista.

## Regla

Cuatro tiempos sobre una duración total de 1,72 s. Los tiempos son los mismos de la lámina 09.

| Tiempo | Qué ocurre | Correlato en logo |
| --- | --- | --- |
| 0,00 → 0,42 s | Entra el trazo horizontal desde la izquierda. Cerrado y sin texto visible. | Entra el trazo |
| 0,42 → 0,88 s | El trazo se ondula de lado a lado y las letras surgen deformadas desde su interior. Amplitud máxima acotada al medio alto de la caja x del texto. | Se abre la W |
| 0,88 → 1,28 s | La ondulación recorre el texto de izquierda a derecha y lo asienta por segmentos. La primera mitad del texto queda legible al final del tramo. | Se forma la U |
| 1,28 → 1,72 s | El texto termina de asentarse. El trazo se retrae hacia la izquierda y queda como guion corto, anclado al inicio del texto. | La i cierra el gesto |

Después del segundo 1,72 s el texto permanece quieto durante todo el paso. La señal residual —el guion a la izquierda— es parte del anclaje, no del movimiento. No vuelve a ondular salvo que el paso cambie.

## Parámetros de la ondulación

- Amplitud vertical del trazo en el tramo 0,42–0,88 s: igual o menor que el ancho de una línea del wordmark en lámina 03.
- Longitud de onda: dos crestas por el ancho total del texto como referencia inicial. Si el texto crece más allá de siete palabras, mantener la densidad de crestas constante, no multiplicar.
- Deformación de las letras: solo torsión del trazo, sin cambio de ancho ni de altura efectiva. Las letras no reducen su x-height al deformarse.
- Easing: entrada con easing suave, asentamiento con easing lento tipo `cubic-bezier(0.16, 1, 0.3, 1)` para que el final no quede seco.

## Qué no debe pasar

- El texto no debe quedar temblando después de 1,72 s.
- La ondulación no debe cruzar el cuerpo de la pieza anclada. Si el objeto está a la izquierda, el inicio del trazo se toca con el borde del punto y nunca lo invade.
- El trazo residual no desaparece durante el paso. Si desaparece se pierde el anclaje con el objeto.
- No usar este movimiento en «Pieza colocada». La confirmación tiene su propio tiempo, más corto y sin ondulación.
- No usar este movimiento en «Pon la pieza a la vista». El aviso de recuperación aparece sin anclaje y sin deformación, para marcar la diferencia de estado.

## Dónde vive

- Lámina 07 · Lenguaje de interacción (`11-2634`): la regla entra como nota corta en «Se sitúa → Responde», con la tabla de tiempos simplificada a una línea.
- Lámina 09 · Movimiento del logotipo (`11-2710`): se añade una sección breve «La misma tinta en la pista», con dos o tres fotogramas tomados de la captura local y la misma tabla de tiempos.
- Nodo de ensayo `60-2` (Tinta en movimiento): se ajusta amplitud y duración a lo anterior. Se conserva el nodo como referencia de ensayo hasta que la regla esté aprobada.

## Evidencia de referencia

- Captura local de los cuatro tiempos actuales: [`wui-texto-tinta-prueba-figma.gif`](/Users/pabli/.codex/visualizations/2026/11/21/01a20010-7b1d-7912-8a7a-3daadb4bbe24/wui-texto-tinta-prueba-figma.gif).
- Lámina 09 con los cuatro momentos del logo: [`wui-09-movimiento-del-logotipo.jpg`](/Users/pabli/.codex/visualizations/2026/11/21/01a20010-7b1d-7912-8a7a-3daadb4bbe24/wui-09-movimiento-del-logotipo.jpg).
- Reposo del flujo en «Del gesto a la pista»: [`wui-del-gesto-a-la-pista.jpg`](/Users/pabli/.codex/visualizations/2026/11/21/01a20010-7b1d-7912-8a7a-3daadb4bbe24/wui-del-gesto-a-la-pista.jpg).

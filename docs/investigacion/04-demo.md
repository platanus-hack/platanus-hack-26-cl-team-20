# 04 · Cinco momentos «wow» para la demo

La historia sigue el dolor recomendado en 02: **las instrucciones viven lejos de las manos**. Una estación de armado tamaño A3 donde la luz guía, verifica y ataja errores. Supuestos de 03: área A3, la cámara verifica con la proyección apagada un instante o con el halo alrededor de la pieza y no encima (lo proyectado cambia la apariencia y confunde al detector, ver ProCap en 01), fondo negro, toque por dwell o pinch, VLM solo para nombrar. La sala se ve mal desde atrás, así que cada momento se filma de cerca y va a la pantalla grande.

Kit sugerido: unos 10 pasos con bloques tipo LEGO/Duplo grandes y de colores distintos. El estudio de Funk (2015) usó bloques Duplo, lo detecta bien la cámara y nadie del público lo arma de memoria.

| # | Momento (de menos a más) | Qué ve el jurado | Qué necesita del engine (contrato) | Respaldo si falla |
|---|---|---|---|---|
| 1 | **La mesa se ubica sola** | Se proyecta un patrón ArUco, se apaga, y un marco calza exacto con el borde de la estación. «Proyector de 50 lucas + teléfono». | Calibración por homografía; `POST /layers` con ancla a zona fija. | Homografía guardada de la mañana (no se movió el rig). |
| 2 | **La mesa sabe qué hay** | Se vacían las piezas sobre la mesa y cada una queda contorneada y con su nombre («2×4 rojo», «eje gris») en ~1–3 s. | `GET /scene` (label, polígono); VLM para nombrar y tracker rápido; `POST /layers` con `anchor: object_id`. | Escena prehecha con las piezas del kit en posiciones marcadas. |
| 3 | **La luz te guía** | Paso 1: halo sobre la pieza correcta y una flecha o silueta donde va. La persona la pone, deja el dedo en «listo» y la mesa avanza. | Zona tocable (dwell); `GET /events` con toque y «objeto movido a zona X»; capas por paso. | Avance con tecla o clicker; las capas son las mismas. |
| 4 | **Te ataja el error** (el momento del dolor) | Alguien del público toma a propósito la pieza equivocada: se marca en rojo y el agente dice «esa es la azul, busca la roja». Los errores son de color, no de tamaño, porque el detector distingue mejor colores. Contador en vivo: errores con papel vs. con guía. | `/events` objeto aparece o desaparece por zona; lógica de verificación del paso (pieza esperada vs. vista); el agente redacta la explicación. | Video del A/B grabado antes (papel vs. guía, errores contados) + error forzado con escena prehecha. |
| 5 | **Cualquier objeto, otra guía** | Un objeto de la mochila de alguien (audífonos, un cargador): el agente lo reconoce y en segundos arma una guía nueva encima («así se enrolla el cable sin dañarlo»). Cierre de 30 s: «el mismo engine arma un escritorio» con un cambio de modo. | `POST /mode`; VLM con vocabulario abierto; el agente genera los pasos como capas ancladas. | Dos o tres objetos «del público» ensayados con guía cacheada. |

## Cómo se conecta con la rúbrica

- **Impacto:** el 4 es la prueba medible (errores contados) y conecta con la cifra de −80 % de errores (Chrysler + OPS, n=10) y con los 8.876 mantenedores que le faltan a la minería chilena (ver 02).
- **Técnica:** el 1, el 2 y el 3 muestran calibración, reconocimiento y toque en vivo.
- **Originalidad y ambición:** el 5, objetos arbitrarios sin marcadores + agente que compone la UI. Es lo que no tiene nadie en 01.
- **Ejecución:** cada momento tiene una escena prehecha; el 4 se puede contar aunque falle en vivo.

## Lo que hay que decidir con el equipo

- El kit concreto (bloques tipo LEGO vs. algo más «industrial», como un conector o una placa con tornillos): los bloques son más fáciles de detectar, el industrial cuenta mejor la historia.
- Si el A/B papel vs. guía se hace en vivo (2 voluntarios, unos 90 s) o se muestra grabado y en vivo va solo el error forzado.
- Bajar las luces del sector de la mesa: sin eso, el riesgo #1 (contraste) se come los momentos 2–4.

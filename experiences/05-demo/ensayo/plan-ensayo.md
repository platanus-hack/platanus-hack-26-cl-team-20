# Plan del ensayo · WUI · domingo 22-nov

Estado: borrador para el ensayo; nada probado en la mesa.
Actualizado: domingo 22-nov-2026, 04:30.
Responsable: Matilda Roberts.


**Hechos:** 06:00 se congelan los deploys y hay 2 ensayos; demo 08:30 (3 min + 2 de preguntas). Nadie fijó la hora de juntarse. Guion: `guion-3min-wui.md`. Decisiones con `va-o-no-va.md`; tiempos en `planilla-cronometro.md`.
**Regla de oro: manos fuera a las 07:50.** De 07:50 a 08:15, traslado y montaje (25 min, supuesto: nadie dijo si la mesa se mueve; si no se mueve, es margen). 08:15 a 08:30: margen de 15 min.

| Rol | Quién | Qué hace en el ensayo |
|---|---|---|
| **Max** | Max | Narra. Decide si hay duda |
| **Matilda** | Matilda | Actúa sin hablar. Si aún no llega, otra persona hace sus acciones en las pruebas técnicas |
| **Vale** | Vale | Sobrina (toca Saturno). Fuera del solar: cronometra la voz de Max |
| **Operador** | por definir | Terminal y pestañas; lanza cada escena; mira los mensajes de error |
| **Cronometrista** | por definir | Cronómetro del celular (botón «Vuelta»), llena la planilla, enciende el celular que graba |
| **Ayudante** | por definir | Pasa y recibe los objetos en los dos cambios (y mueve la cámara de lado, si es una sola) |

**Reglas:** (1) Nada de código después de las 06:00 (sí se pueden cambiar dos ajustes de `escritorio/escena.json`: la cámara, línea 10, y `auto`, línea 25): los parches (si Max decide aplicar alguno) se aplican antes o no se aplican. (2) Una prueba, una anotación: si falla, se anota el plan B y se sigue. (3) El celular graba en su soporte desde el primer bloque: cada prueba buena es un video de respaldo. (4) Las cifras se prueban dentro de cada escena, no al final.

## Si empezamos 06:00

| Hora | Bloque | Responsables | Qué se hace | Decisión |
|---|---|---|---|---|
| antes de 06:00 | Llegar | Matilda | Llega a la sede (salió a las 00:28) | — |
| antes de 06:00 | Parches | Max | Aplica 02c y 04, o ninguno (01, 03 y 05 ya están en el código) | Con o sin parches |
| 06:00–06:10 | **0 · Armar** | Operador, cronometrista | Mesa limpia, recalibrar, cinta de papel en cada objeto, 3 juegos de objetos en orden. ¿Una cámara lateral o dos? | Dónde va la cámara lateral |
| 06:10–06:30 | **1 · Laboratorio (lo más riesgoso)** | Operador, Matilda, cronometrista | Calibrar el vaso (vacío, 50, 150 ml). Lanzar con la cámara real y el protocolo de 2 pasos. **Servir 3 veces hasta ~60 ml** con jarra medidora. Tocar A2 3 veces. Cifra «1 de cada 5» y pestaña «Cierre» con «+160 mil». En paralelo, Vale cronometra a Max leyendo el guion | Vaso en vivo o termina en el A2 |
| 06:30–06:45 | **2 · Sistema solar** | Operador, Matilda, Vale | Armar. Mover el canasto 3 veces. Vale toca Saturno 3 veces: ¿sale la ficha?, ¿habla Lumo? Cifra «1 de cada 3» | Toque y voz: en vivo o a mano |
| 06:45–06:55 | **3 · Escritorio** | Operador, Matilda | Con `--fijas`. Escribir «¿endpoint cobros?» y tocar «Responde» 3 veces (hoja nueva y reiniciar entre pruebas). Pestaña «Inicio» y cifra «+56 %» | Libreta en vivo o sin libreta |
| 06:55–07:07 | **4 · Los dos cambios** | Matilda, operador, cronometrista | Cada cambio 3 veces, de punta a punta, con el arranque de la escena nueva | Orden de la mesa auxiliar |
| 07:07–07:15 | Pausa | Max, cronometrista | Leer la planilla. Elegir planes B | Qué va en vivo |
| 07:15–07:22 | **Ensayo 1 completo** | Todos | 3 min sin parar; plan B en vivo si algo falla | Anotar |
| 07:22–07:30 | Ajustar | Max | Si pasó de 3:00, cortes 1 a 4 en orden | Cortes |
| 07:30–07:37 | **Ensayo 2 completo, grabado** | Todos | Con los planes B ya elegidos | Anotar |
| 07:37–07:50 | Decidir y congelar | Max, Matilda | Recorrer `va-o-no-va.md` fila por fila. Videos al computador de la pantalla grande | **Congelado** |

## Si empezamos 07:00

| Hora | Bloque | Qué se hace (lo mínimo) |
|---|---|---|
| 07:00–07:05 | 0 · Armar | Mesa limpia, recalibrar, objetos en orden |
| 07:05–07:17 | 1 · Laboratorio | Calibrar vaso; **servir 2 veces**; A2 una vez; cifra |
| 07:17–07:25 | 2 · Sistema solar | Saturno 2 veces con voz; canasto 1 vez; cifra |
| 07:25–07:30 | 3 · Escritorio | «Responde» 2 veces; «Inicio» y cifra |
| 07:30–07:37 | **Ensayo 1 completo, grabado** | Los cambios se miden aquí, no aparte |
| 07:37–07:44 | **Ensayo 2 completo** | Con cortes y planes B aplicados |
| 07:44–07:50 | Decidir y congelar | `va-o-no-va.md` |

Con 07:00 no hay tiempo para repetir nada: lo que falle una vez va con su plan B.

## Después de congelar (las dos versiones)

- Nadie desconecta cámaras ni cables, nadie cambia código.
- 08:15: recalibrar en el lugar de la demo; probar una vez la libreta y el «Inicio». Si la mesa o la cámara de lado se movieron: calibrar el vaso de nuevo (3 llenados) o el laboratorio termina en el vial A2 (corte 4).
- Reiniciar el escritorio justo antes: con `--fijas` contesta en orden y debe partir en la primera respuesta.
- La voz de Lumo es el comando `decir`, escrito en T5 sin Enter (hoja del operador, paso 11). No se conecta la voz de ElevenLabs.

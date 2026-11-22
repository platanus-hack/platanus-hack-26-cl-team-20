# Va o no va · WUI · ensayo del 22-nov

Estado: borrador para el ensayo; nada probado en la mesa.
Actualizado: domingo 22-nov-2026, 04:30.
Responsable: Matilda Roberts.


Cada fila se marca ✓ o ✗ con un cronómetro o mirando. Si es ✗, se hace lo de la última columna, **sin discutirlo**. Max decide solo si hay empate. Los planes B salen de `guion-3min-wui.md`.

| Prueba | Cómo se mide | Va si… | ✓/✗ | Si no pasa |
|---|---|---|---|---|
| **Demo completa** | Cronómetro: de la primera palabra de Max al final de «…canvas de nuestros agentes.» | **3:00 o menos** | | Cortes en orden hasta bajar de 3:00: **1** Lumo dice solo su primera frase (−4 s) · **2** sacar «Te acompaña en lo personal, lo educativo y lo productivo.» (−4 s) · **3** sin canasto (−7 s) · **4** laboratorio termina en el vial A2 (−20 s) |
| **Cambio 1** | De «En la tarde» a ver los planetas | **20 s o menos** | | Max dice la segunda frase igual; el operador deja los planetas en posición fija |
| **Cambio 2** | De «En la noche» a ver el vial marcado | **25 s o menos** | | Si no encuentra el pastillero: manos fuera y T4 ↑ Enter una vez. Si tampoco: video (y se dice que es grabado) o Max pasa al cierre |
| **Vaso: medición** | Echar 50 ml medidos con jarra. Leer el número de la mesa | Marca **47 a 53**, en 3 de 3 | | El laboratorio termina en el vial A2 (corte 4). Max no dice la última frase. Si hay video bueno, se muestra y se dice que es grabado |
| **Vaso: «Te pasaste»** | Servir hasta ~60 ml. Contar desde que empieza a servir | Sale en **12 s o menos** (las dos frases de Max más 6 s), mientras todavía sirve, 3 de 3, y la escena no desaparece | | Igual que la fila anterior |
| **Tocar A2** | Del dedo quieto sobre A2 al paso 2 «Pon 50 ml…» | **3 s o menos** | | El operador manda `pedir paso_siguiente` desde T5, una sola vez. Matilda no toca «Listo» |
| **Escritorio: respuesta** | De tocar «Responde» a la primera línea escrita. Debe decir «/v1/cobros» | **10 s o menos**, 3 de 3 | | Max dice «y la respuesta le llega al papel» y pasa al cambio 1 |
| **Escritorio: ¿lee solo?** | Mirar si la respuesta sale antes de tocar «Responde», o si sale «nada nuevo que leer» | No pasa | | Si la respuesta ya salió, Matilda **no** toca el botón. Si pasa 2 de 3 veces: poner `"auto": false` en `escritorio/escena.json`, línea 25, relanzar T1, y desde ahí Matilda toca siempre «Responde» |
| **Canasto** | Canasto quieto → planetas sobre sus bolas | **2 s o menos**, 3 de 3 | | Matilda no mueve el canasto; Max salta esa frase (corte 3) |
| **Toque en Saturno** | Del dedo quieto de Vale a ver la ficha y a Lumo volando | **3 s o menos**, 3 de 3 | | El operador da Enter en T5 igual (`decir`): Lumo vuela y habla, sin ficha |
| **Voz de Lumo** | Escuchar desde el fondo de la sala | Se oye y termina antes de **13 s** desde el toque | | No se oye: Max dice «Sus anillos son millones de pedacitos de hielo y roca.» Se oye pero se pasa: corte 1 |
| **4 cifras** | Mirar cada una en su escena desde 2 m | Se lee y no tapa libreta, lámina ni vaso | | Esa cifra no se proyecta. Queda en la slide de fuentes |
| **«Inicio» y «Cierre»** | El operador las muestra con una acción | Se ven, 2 de 2 | | Sin «Inicio»: parte en el escritorio. Sin «Cierre»: queda «Te pasaste». Max no cambia su texto |
| **Programas vivos** | Tras cada ensayo completo, el operador mira cada terminal: ¿se cerró? (Un «sin mesa, reintento» o «error en la guía» que se repite unos segundos y para está bien: se recuperó) | Ninguno se cerró | | Escritorio caído en la demo: plan B del escritorio. Toques del solar muertos: ficha a mano. Se relanza después, nunca en medio |

**Origen de cada número:** 3:00, 20 s, 25 s, 10 s, 13 s y los cortes: tabla y planes B del guion. 47–53: meta 50 ml y tolerancia 3 ml del preset (`laboratorio/escena.md:109-110`); «Te pasaste» sale sobre 54 ml durante 2 lecturas (`conductor.py:357`, `percepcion/vaso.py:268-288`). 6 s y 3 s: pausas del guion para servir y para tocar el vial. 2 s: pausa del guion tras mover el canasto. 3 s del toque en Saturno: el vuelo de Lumo dura 1,4 s (`sistema-solar/conductor.py:591`) y la voz debe caber en los 13 s.

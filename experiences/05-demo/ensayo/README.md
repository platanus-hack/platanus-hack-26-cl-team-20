# Ensayo de la demo de 3 minutos

Estado: borrador para el ensayo del domingo 22-nov; **nada se ha probado en la mesa**.
Actualizado: domingo 22-nov-2026, 04:30.
Responsable: Matilda Roberts.
Decisión pendiente: que Max acepte o no los cambios al guion (`cambios-para-max.md`); operador y cronometrista por definir.

Para qué: que cada persona llegue al ensayo con su papel. Todo se revisó leyendo el código de las escenas de Max (PR #28, `max/escenas-a-main`, commit `11d1dd9`); ningún comando se ejecutó en la mesa.

Sustituye, para el ensayo, a [`../guion-3min.md`](../guion-3min.md) y [`../hoja-de-escenario.md`](../hoja-de-escenario.md), que quedan como antecedente (tenían el nombre anterior del proyecto y el botón «Responde» al lado equivocado).

## Por dónde partir

| Quién | Qué abrir |
|---|---|
| Max | [`cambios-para-max.md`](cambios-para-max.md) (13 frases, antes → después) y [`hoja-max.md`](hoja-max.md) |
| Matilda | [`hoja-matilda.md`](hoja-matilda.md) |
| Vale y todos | [`hoja-de-escenario.md`](hoja-de-escenario.md): qué objeto va dónde y quién lo cambia |
| Quien opere el computador | [`hoja-operador.md`](hoja-operador.md) y [`cifras/como-cargar.md`](cifras/como-cargar.md) |
| Quien cronometre | [`planilla-cronometro.md`](planilla-cronometro.md) |
| Todos, al decidir | [`plan-ensayo.md`](plan-ensayo.md) y [`va-o-no-va.md`](va-o-no-va.md) |

## Qué hay

- [`guion-3min-wui.md`](guion-3min-wui.md): el guion completo. 266 palabras, 2:51 en el papel (`python3 contar.py guion-3min-wui.md`); lo realista es 3:05 a 3:15 y trae 4 cortes preparados.
- [`laboratorio-demo-2-pasos.yaml`](laboratorio-demo-2-pasos.yaml): el protocolo de Max sin los tres pasos del medio, para que quepa en 3 minutos. Nunca ejecutado.
- [`cifras/`](cifras/): las 4 cifras para proyectar y las pestañas «Inicio» y «Cierre». El cargador se probó solo contra una mesa imitada.
- [`tarjeta-preguntas.md`](tarjeta-preguntas.md) y [`tarjeta-preguntas-camino-a.md`](tarjeta-preguntas-camino-a.md): respuestas cortas para el jurado. La segunda es para el caso en que cada escena corre con su propio programa y el motor en Rust no corre en la mesa.
- [`videos-de-respaldo.md`](videos-de-respaldo.md): qué grabar en el ensayo por si algo falla en vivo.

## Lo que solo se resuelve en la mesa

- Si hay una cámara de lado o dos (libreta y vaso).
- Si el sistema solar va con el programa de Max que sigue el canasto o con el motor.
- Si la voz de Lumo se oye en la sala (el comando solo suena en un Mac).
- La hora de juntarse.

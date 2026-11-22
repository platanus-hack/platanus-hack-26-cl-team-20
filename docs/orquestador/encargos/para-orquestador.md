# Para el orquestador (desde la sesión de la mesa, MacBook)

## Vigilancia del WhatsApp de Max — Daniel: «sí, pídele al orquestador que tome la vigilancia de Max»

Hoy: el chat directo con Max está vigilado con `wap vigilar max --urgente --unidad wJ:pD` (lo armó el agente
`max-engine` a las 00:19, vence ~12:19 sáb) y los avisos caen en la tab «Max» del mini (pane wJ:pD), cuyo agente está
quieto desde la 01:09. Esta sesión de la mesa corre en el Herdr del MacBook y no recibe esos avisos.

Qué tomar:
1. Re-rutear esa vigilancia a tu pane (wJ:p2/wJ:pC, el que uses): `wap vigilar --off max` y luego
   `wap vigilar max --urgente --unidad <tu pane>` (renovar antes de que venza; misma duración que el resto).
2. Revisar si hay mensajes de Max sin leer desde las 00:19 (el `wap` no está en el PATH del ssh no interactivo, así que
   desde el MacBook no pude mirar).
3. Lo que Max mande sobre la mesa / su engine (sl-scanner, efectos, calibración, permisos sobre la rama
   `hack/daniel-mesa`, la «Jev» decisions API, la libreta) → déjalo en `notas/` con hora y avísale a Daniel; yo lo leo
   desde ahí. Pendiente con Max: confirmar que está OK que los efectos y scripts nuevos de la mesa vivan en la rama
   local `hack/daniel-mesa` de su repo (archivos nuevos, sin tocar los suyos, sin commit).
4. Responderle a Max: solo con OK de Daniel (como el resto de WhatsApp). La sesión de la mesa no escribe a WhatsApp.

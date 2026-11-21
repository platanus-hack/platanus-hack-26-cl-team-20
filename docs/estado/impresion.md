# Impresión 3D — ESTADO (agente de impresión, MacBook)

## Impresoras (sede)
- A2L OF 2201 — 192.168.4.7 — modo nube, cuenta de la organización. El envío por red lo hace Daniel desde Studio.
- A2L bod 80 26 — 192.168.4.9.
- Ambas con PLA naranja (sáb ~03:00).

## Piezas
| Pieza | Archivo | Impresora | Tiempo | Gramos | Resultado |
|---|---|---|---|---|---|
| Llavero PH26/G20 | ~/Downloads/platanus-3d/prueba/llavero_ph26.* | — | — | — | Descartado (Daniel: «fome») |
| Torre con giro (jarrón) | `~/Downloads/platanus-3d/prueba/torre_giro_A2L_jarron.3mf` | A2L OF 2201 | 35 min 35 s | 5,55 g | Laminada en Bambu Studio con A2L de 0,4 mm, Bambu PLA Basic y modo jarrón en espiral. Voladizo máx. calculado ≈40° (<45°). |

## Notas
- La placa laminada quedó guardada en `~/Downloads/platanus-3d/prueba/torre_giro_A2L_jarron.gcode.3mf`. Bambu Studio estimó 28 min 41 s para imprimir la pieza y 35 min 35 s en total.
- Daniel confirmó A2L OF 2201 (`192.168.4.7`) como destino e ingresó el código. Tras reintentar con su SN y modelo A2L, Dispositivo muestra «No printer». La ventana de envío queda abierta en el MacBook con «Send» deshabilitado.
- La IP `192.168.4.7` responde a ping y acepta conexiones TCP en 8883 y 990. Bambu Studio tiene acceso a la red local, el firewall permite conexiones entrantes y la app escucha en UDP 2021. Queda pendiente verificar el modo actual de la impresora y seleccionar el destino en Studio.
- Siguiente: soporte del proyector T749 + webcam cenital; falta que Daniel mande medidas o fotos.

# Escena 3 · Laboratorio

Corriendo en la mesa con el montaje del 22-nov (capturas `capturas/lab-*.jpg`). **El montaje está anotado a mano** en
`lab-modelo.json` sobre la foto limpia `out/lab-norm.png`: si se mueve un objeto hay que corregir su rectángulo ahí.

Qué hace hoy: compartimentos de la caja organizadora en colores con lista de inventario al lado; cuadritos de la caja
de viales iluminados uno a uno; tiras azul (T1–T4) y morada (P1–P4) con su código afuera y Jev leyendo cuáles están
abiertas; pipeta (lápiz azul) seguida en cada cuadro, con su punta; pasos del protocolo con número junto al objeto y
barra «Paso n de N»; error «Vial incorrecto» si la pipeta entra en otro vial; vaso con meta y nivel medido como altura
del líquido contra el vaso vacío inicial (Jev verifica si hay líquido); alerta de teléfono; ficha del plátano; foto del
chip a su lado; botones «Iniciar experimento» y «Hablar con el laboratorio».


La mesa conoce el inventario de la caja organizadora, alumbra lo que se le pide y una voz formal guía a quien trabaja.

- **Encender:** tecla **L** en el dashboard (o `curl "http://127.0.0.1:9301/start?escena=lab"`). **X** apaga.
- **Voz:** agente de ElevenLabs «WUI · Laboratorio (mesa v2.1)», copia del agente de laboratorio de Max (misma voz
  pausada) con prompt y herramientas de esta mesa. Se crea o actualiza con `lab_agente.py`. Trata de usted, guía a
  Matilda y conversa con ella. Parte desde el botón «Hablar con el laboratorio» junto a la libreta.

## Qué espera la escena en la mesa

| Objeto | Para qué | Requisito |
|---|---|---|
| Caja organizadora, abierta | Inventario y «¿dónde está…?» | Entera dentro de la proyección, tapa fuera de cuadro o abierta hacia atrás, ojalá derecha respecto de la mesa. |
| Caja de viales de colores | Se rotula y se alumbra; ahí vuelve la tira | Fuera de la organizadora. |
| Una tira de viales fuera de su caja | Transferencia con pipeta de un compartimento a otro | Sola, sin tocar otros objetos, con sus dos compartimentos abiertos a la vista. |
| Pipeta | Paso 1 del protocolo | Sobre la mesa, separada. |
| Vaso con líquido oscuro | Parte del experimento: meta de llenado y nivel en vivo | De pie, sin objetos pegados; a su izquierda (vista de cámara) va el medidor. |
| Libreta abierta | Notas, pasos del protocolo y botón de voz | A 4 cm o más del borde de la persona. |
| Teléfono | NO va en la mesa al partir | Cuando Matilda lo deja, salta la alerta en menos de medio segundo y la voz le pide retirarlo. |

Sin la caja organizadora la escena funciona, pero sin inventario.

## Lo nuevo de esta versión

- **Tira y pipeta:** la tira se divide en compartimentos T1…Tn (T1 a la izquierda de la persona). `transferir` marca
  origen en azul, destino en verde y un arco con gotas que viajan de uno a otro, con el volumen.
- **Vaso integrado:** `meta_vaso` fija hasta dónde llenar; junto al vaso hay un medidor en vivo que se pone verde al
  llegar («listo») y rojo si se pasa.
- **Teléfono:** se busca en cada cuadro como una mancha oscura rectangular de unos 15 × 7,5 cm fuera de la caja. Tres
  cuadros seguidos y aparece el contorno rojo con «Objeto ajeno al experimento · retire el teléfono», suena un aviso y,
  si la conversación está abierta, la voz se entera. Al retirarlo se apaga.

## Lo que hay que anotar cuando la caja esté montada

`lab-modelo.json` trae un inventario **provisional** (tomado de una descripción anterior de la caja): filas A, B, C con
3, 4 y 3 compartimentos. Con la caja real en la mesa hay que ajustar ahí: cuántos compartimentos tiene cada fila, qué
hay en cada uno y cuántas unidades, y `espejo` si las columnas salen al revés. Lo mismo el `protocolo` (pasos y qué
alumbra cada uno) y la `tira`: cuántos compartimentos tiene y cuáles están abiertos.

## Herramientas de la voz

`ver_mesa`, `inventario`, `buscar` (alumbra el compartimento), `alumbrar` (compartimento por código u objeto por
nombre), `apagar_luz`, `transferir`, `meta_vaso`, `nivel_vaso`, `protocolo`, `paso` (alumbra y deja la instrucción en la libreta),
`escribir_libreta`, `borrar_libreta`.

## Cómo detecta

Foto limpia al encender, Grounding DINO + SAM con `prompts-lab.json`, y vuelve a detectar cada 25 s con la proyección
encendida, sin apagar nada; lo que no se movió conserva su máscara. La rejilla de la caja sale del rectángulo que
ajusta a su silueta. No hay seguimiento continuo de la caja como el de los platos del sistema solar.

## Pendiente

- Probar todo en la mesa: detección de la caja transparente, calce de la rejilla y lectura del nivel.
- Seguimiento en tiempo real de la caja, y rejilla corregida por su relieve con luz estructurada.
- Detectar si la pipeta realmente pasó por el origen y el destino (hoy la mesa guía, pero no verifica).

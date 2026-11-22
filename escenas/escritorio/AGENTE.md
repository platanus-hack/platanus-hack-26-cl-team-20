# Escritorio del vibe coder · cómo conducir la escena

Este archivo es para el agente que conduce la escena (Claude Code, Codex u otro, conectado a la mesa por MCP). Si la
escena la corre `escritorio.py vivo`, no necesitas hacer nada de esto: el director ya lo hace solo. Úsalo cuando quieras
que un agente la conduzca a mano, o para entender qué pasa por dentro.

## Qué hay en la mesa

- Matilda, CTO y vibe coder, en su escritorio: laptop, teclado, mouse y una **libreta**.
- Una **cámara secundaria** mira la libreta de cerca. La cámara cenital mira la mesa entera y es la que está calibrada.
- A la izquierda de la libreta hay un botón proyectado, «Responde».
- A un costado, el panel «Tus agentes»: lo que hacen sus agentes ahora, con su estado (trabajando, esperando, listo).

## La escena, paso a paso

1. **Montar.** `uv run escritorio.py montar`. Crea o activa la escena «Escritorio» de la mesa, proyecta las marcas de
   corte donde va la libreta, el botón y el panel de agentes.
2. **Esperar a que Matilda escriba.** Con `events_wait` (tipos `widget_pressed` y `flow_emitted`) esperas:
   - `widget_pressed` con `widget_id: "escritorio:marco"` y `value: "responde"`: tocó el botón;
   - `flow_emitted` con `event: "voice_command"`: dijo algo en voz alta (`data.text`).
3. **Leer.** `uv run escritorio.py leer`. Mira la libreta con la cámara secundaria, encuentra lo que se escribió desde la
   última lectura y deja dos imágenes en `salida/`: `contexto.jpg` (la página con un recuadro rojo en lo nuevo) y
   `recorte.jpg` (solo lo nuevo). Al mismo tiempo proyecta el corchete rojo junto a lo nuevo y la palabra «leyendo»,
   para que Matilda vea al tiro que la escuchaste. Devuelve un JSON: `estado` es `nuevo`, `igual`, `mano` (hay una
   mano encima: espera un segundo y vuelve a leer) o `sin_libreta`.
4. **Pensar.** Mira las dos imágenes y contesta como una nota al margen: lee `voz.md`, ahí está el tono. Dos o tres
   líneas de menos de 26 caracteres.
5. **Contestar.** `uv run escritorio.py responder "primera línea" "segunda línea"`. Busca un hueco libre en la página,
   cerca de lo que ella escribió, y lo escribe a mano con luz, línea por línea. No escribe encima de respuestas
   anteriores; si la página se llenó, borra las viejas.

## Reglas

- Proyecta solo con los ids que empiezan por `escritorio:`. No toques widgets de otras escenas.
- Nunca proyectes nada que no esté pensado para que lo lea el público: es una demo en vivo.
- Si la lectura falla (`sin_libreta` o el modelo no entiende), di poco: «no alcanzo a leer, ¿lo repasas?».
- La escena es de la mesa entera: si alguien cambia de escena con las pestañas (`scene_changed`), deja de leer hasta que
  vuelva a «Escritorio».

## Si quieres hacerlo con las tools MCP directo

Todo lo de arriba se puede hacer con `look` (con `camera_id` de la cámara secundaria), `project` (widgets `effect` con
`shader: "html"`) y `remove`. Las plantillas html están en `plantillas/` y `dibujo.py` muestra cómo se ubican sobre la
libreta: un rectángulo en mm con `rotate_deg`, donde 1 px de CSS es 1 mm de la mesa.

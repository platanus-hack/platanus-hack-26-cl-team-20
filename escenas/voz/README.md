# Voz de las escenas de WUI (ElevenLabs)

Una voz que conversa con la persona en la mesa y **mueve la escena** por el MCP de la sesión. Se le puede
interrumpir, respeta los turnos y habla español de Chile. Un agente por escena, cada uno con su voz y su ritmo:

| Escena | Agente en ElevenLabs | Voz | Turno |
|---|---|---|---|
| Sistema solar | «Superhuman Desk · Lumo» | Vale (chilena, alegre) | paciente |
| Laboratorio | «Superhuman Desk · Laboratorio» | Ignacio (chileno, pausado) | normal |
| Escritorio | «Superhuman Desk · Escritorio» | Fernando (chileno, relajado) | rápido |

El producto se llama **WUI** («WUI · El mundo es tu interfaz»). Los agentes de ElevenLabs conservan **a propósito**
el nombre interno «Superhuman Desk · …», igual que el tag `superhuman-desk`, las carpetas `~/.config/superhuman-desk`
y `~/.local/state/superhuman-desk`, los nombres de paquete y las variables `SD_*`: `publicar` busca los agentes por
ese nombre y ese tag, y no se republican la noche de la demo.

Las tools son las del [contrato del asistente](../CONTRATO-ASISTENTE.md), iguales en toda escena: `preset_leer`,
`preset_accion`, `preset_ajustar`, `mesa_objetos`, `mesa_ver`, `mesa_eventos` y los verbos comunes de `MOTOR.md`
(`senalar`, `nombrar`, `explicar`, `medir`, `avisar`, `llevar`, `callar`), más `cambiar_pestana`. Todo lo que proyecta
la voz lleva ids `voz-*` (o `lab-nota` en el laboratorio): nunca pisa los widgets de un conductor.

```
página React (@elevenlabs/react) ──client tools──▶ servidor local :9325 ──MCP──▶ gateway de la mesa
   ▲  │  ConversationProvider, interrupciones            │  llave ElevenLabs y token de la sesión se quedan aquí
   │  └─ URL firmada (texto) / token WebRTC (voz) ◀───────┤
   └──── SSE «hablar / contexto / escena» ◀── oído: events_wait + eventos.jsonl del motor (nivel, libreta…)
```

## Correr

```sh
cd escenas/voz
uv run python -m sdvoz publicar                 # crea o actualiza los 3 agentes y sus tools (idempotente; --dry-run)
# mesa simulada (gateway local en :9324 con PCX_OPEN_SESSIONS=1):
uv run python -m sdvoz mesa-sim                 # sesión nueva; el enlace de la consola ?sim=1 queda en ~/.config/superhuman-desk/mesa-sim.url
uv run python -m sdvoz sim-mesa                 # pestañas y objetos de prueba (solo para la sim)
# mesa real: el token de la sesión va por variable, nunca pegado
PCX_USER_TOKEN=… uv run python -m sdvoz mesa --gateway https://<gateway>
uv run python -m sdvoz servir                   # http://127.0.0.1:9325 · modo texto
uv run python -m sdvoz servir --voz             # además modo voz (micrófono): solo con el OK de Max
uv run python -m sdvoz probar solar "¿Cuál es el planeta más grande?"    # prueba en texto, sin navegador
```

`SD_PRESETS` (carpetas separadas por «:») dice dónde buscar los `preset.json` si no están en `escenas/` de este
checkout. La página se compila con `cd web && npm install && npm run build` (todo en el disco externo).

## En la página

- Elige la escena y parte («Hablar con …»). Con «Seguir la pestaña», cuando la mesa cambia de pestaña la página corta
  y parte con el agente (y la voz) de la escena nueva.
- Modo texto para probar sin micrófono. En modo voz, la tecla **M** silencia el micrófono: sirve para narrar la demo
  sin que el agente lo tome como turno (además, el agente usa `skip_turn` cuando la persona no le habla a él).
- Lo que pasa en la mesa llega solo: un toque, un objeto nuevo, la libreta con tinta o el nivel del vaso. Lo que pide
  reacción («te pasaste») va como mensaje y el agente contesta; lo demás va como contexto, sin hablar.

## Probado (21-nov)

- Modo texto contra la consola `?sim=1`: Lumo señala, nombra, explica y reacciona a un toque en la lámina; al cambiar
  la pestaña a Laboratorio la página cambió de agente, y con un `nivel` de 56 ml sobre 50 dijo «Te pasaste: van
  cincuenta y seis mililitros, sobran seis. Para ahí.» con un halo de alerta en el vaso. El escritorio da opciones de
  nombres reales.
- Con la consola de `daniel/consola` (19:40): `paso` sale por `scenes emit` real (`paso_siguiente`, `repetir`, con
  `by:"agent"`), el oído no se cuenta sus propios eventos, y nada de la voz cae en la barra de pestañas (x < 150 mm)
  ni en el botón de voz. `mesa_preguntar` (JEV sobre la foto) acertó «Saturno» con 70 % en 0,6 s; el sí/no fue menos
  confiable con la foto chica de la sim, por eso la tool pide opciones.
- En la mesa de SAT (20:21 a 20:23, C920 lateral): Lumo conectó, saludó, escuchó y se dejó interrumpir; las tools
  respondieron en 0,6 a 1,4 s. No hay foto que confirme que el halo de «Muéstrame Saturno» cayó sobre la bola.
- Ajuste tras esa prueba: los objetos que salían y volvían en cada re-armado del motor («se fue Sol», «aparecieron
  Venus y Marte») ya no se comentan. El oído cuenta altas y bajas por nombre y solo avisa lo que se mantiene 5 s
  (`SD_OBJETOS_ESTABLE_S`); lo descartado queda en el registro como `"ignorados"`.

## Pendiente de otros

- Los conductores tienen que atender los eventos de la voz: `paso_siguiente`, `paso_anterior`, `repetir` (con
  `data.preset`) y `preset_accion` / `preset_ajustar` (contrato §3), y contestar `preset_hecho {id, ok}`. La voz los
  emite con `scenes emit` y, si el nodo no lo tiene, con `run_flow`.
- El motor no publica al gateway los eventos de los ScenePerceptor; la voz los lee de `~/.pcx/motor/**/eventos.jsonl`
  (o `SD_MOTOR_EVENTOS`).
- `mesa_ver` no le pasa la foto al LLM de la voz (las client tools devuelven texto).

## Acceso

«Superhuman Desk · Lumo» es **público** (decisión de Max, q50, 21-nov 19:46): se conecta con su `agent_id`, sin llave.
El id **no va en el repo** (el repo de entrega es público): va en `SD_VOZ_AGENT_ID`. En el Mac de SAT viene de
Doppler (proyecto `platanus-hack-26`, config `dev`: `SD_VOZ_AGENT_ID` junto con `ELEVENLABS_API_KEY` y
`AI_GATEWAY_API_KEY`), así que todo comando de la voz va como `doppler run -p platanus-hack-26 -c dev -- <comando>`.
Los valores nunca se escriben en un archivo, un chat ni un commit. Laboratorio y Escritorio son privados (URL firmada o token desde el
servidor local). `publicar` respeta eso (`publico` en `sdvoz/escenas.py`); para cambiarlo a mano sin republicar:
`uv run python -m sdvoz acceso solar privado|publico`. Límites de cada agente: 30 min por conversación, 3 a la vez,
120 al día.

## Secretos

La llave de ElevenLabs se lee de `$ELEVENLABS_API_KEY`, `~/.elevenlabs/api_key` o el llavero, y no sale del Mac. El
token de la sesión de la mesa vive en `~/.config/superhuman-desk/voz.json` (600). La página solo recibe una URL firmada
o un token de conversación de corta duración.

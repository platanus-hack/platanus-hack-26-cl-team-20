# Correr Lumo en la mesa de SAT (su Mac, con Doppler)

> ## ⛔ NO corras `publicar` desde el Mac de SAT
>
> Lumo ya está publicado y es uno solo para todos. `publicar` desde otro Mac **pisa a Lumo** con lo que tenga ese
> checkout (el 21-nov a las 20:09 lo dejó con una voz deshabilitada y sin frenos). Para hablar con Lumo basta con
> `servir` dentro de `doppler run` (paso 4). Si algo de Lumo hay que cambiar, se pide a la voz (h24) por el orquestador.

Lumo es la voz de la escena Sistema solar: conversa con quien está en la mesa (se le puede interrumpir), mueve la
proyección por el MCP de la sesión y reacciona sola a lo que pasa en la mesa (toques, objetos, eventos).

**Modo público (q50):** el agente vive en la cuenta de Max y está abierto sin autenticación, solo para las páginas de
`localhost`, `127.0.0.1` y `pcx-gateway.onrender.com`, con corte a los 60 s de silencio.
La página se conecta con el `agent_id` de Lumo y las client tools van al MCP de tu sesión por un servidor local. El
`agent_id` no va en el repo (el repo de entrega es público): lo trae Doppler (proyecto `platanus-hack-26`, config
`dev`) junto con `ELEVENLABS_API_KEY`, y cada comando de la voz va dentro de
`doppler run -p platanus-hack-26 -c dev -- …`. Así nunca corre sin el id y no se crea un agente nuevo en la cuenta
de Max.
El token de tu sesión nunca sale de tu Mac ni va al navegador.

## 1. Código

```sh
cd <tu clon de satelerd/platanus-hack-26>
git fetch origin
git worktree add --detach ../hack26-voz origin/main   # desde main, aparte del checkout de la consola
cd ../hack26-voz/escenas/voz
```

Para actualizar después, dentro de ese worktree: `git fetch origin && git checkout --detach origin/main` (no `git pull`).
Requisitos: `uv`, Python 3.12+, Node 18+ y Chrome.

## 2. Dependencias y página

```sh
uv sync
(cd web && npm install && npm run build)
```

## 3. Agente y sesión de la mesa (por variable, sin pegar nada en un chat)

El `agent_id` de Lumo y la llave de ElevenLabs vienen juntos de Doppler: no se exportan a mano ni se pegan en ningún
lado. Para revisar que estén, sin ver los valores:

```sh
doppler run -p platanus-hack-26 -c dev -- sh -c 'echo "agent_id=${#SD_VOZ_AGENT_ID} llave=${#ELEVENLABS_API_KEY} caracteres"'
```

La sesión de la mesa sí va por variable:

```sh
export PCX_GATEWAY=https://<tu gateway>     # con o sin /mcp al final
export PCX_USER_TOKEN=…             # el token de usuario de la sesión de tu mesa
```

Si el acceso a la sesión te llegó como archivo (JSON, el comando `claude mcp add … Bearer …` o el enlace `/mesa#s=…`):
`uv run python -m sdvoz mesa --desde-archivo <ruta>` lo guarda en la config (600) sin imprimirlo, y ya no hace falta
`PCX_GATEWAY` ni `PCX_USER_TOKEN`.

Nunca corras `publicar` (ver arriba): con la llave de Doppler pisaría al Lumo de todos. `servir` no crea ni cambia
agentes.

## 4. Levantar el servidor (con voz)

```sh
doppler run -p platanus-hack-26 -c dev -- uv run python -m sdvoz servir --voz   # http://127.0.0.1:9325
```

`--voz` habilita el micrófono en la página. Debe correr en la misma terminal donde exportaste las variables del paso 3.

## 5. Mesa y micrófono de la C920 (cam-3, lateral)

1. En la consola de la mesa, deja activa la pestaña **Sistema solar**.
2. En Chrome abre: `http://127.0.0.1:9325/?escena=solar&modo=voz&mic=C920`
3. Aprieta **Ver nombres de micrófonos** y acepta el permiso. Debe quedar elegida la C920 («pedido: «C920» ✓»); si no,
   elígela en la lista **Micrófono** (el nombre de la cámara lateral, cam-3).
4. Deja marcado **Seguir la pestaña** y aprieta **Hablar con Lumo**. Saluda y llama `preset_leer`.
5. Tecla **M**: silencia o abre el micrófono (para narrar sin que Lumo lo tome como turno).

## 6. Verificar

```sh
curl -s http://127.0.0.1:9325/api/estado
```

Esperado: `mesa.sesion: true`, `mesa.oido: "escuchando"` y en `escenas`, la de `solar` con `publicado: true` (sale del
`SD_VOZ_AGENT_ID`). En la página, al hablarle («Lumo, muéstrame Saturno»), el registro muestra `tool senalar …` con su
resultado y en la mesa aparece un halo sobre Saturno (si el motor publicó los objetos `solar:*`; si no hay objetos,
Lumo lo dice y sigue conversando). Los avisos «mesa» (toques, objetos, cambios de pestaña) aparecen en el registro y
Lumo reacciona sola. Los objetos solo se avisan si el cambio se mantiene 5 s: lo que sale y vuelve en un re-armado
no se comenta (`SD_OBJETOS_ESTABLE_S` cambia la ventana). Registro completo: `~/.local/state/superhuman-desk/voz.jsonl`
(los cambios descartados quedan como `"ignorados"`).

## Si algo falla

| Síntoma | Qué hacer |
|---|---|
| Error al «Hablar con Lumo» (auth, 401/403, «Access denied») | Abre la página como `http://127.0.0.1:9325` o `http://localhost:9325` (otro origen no está permitido). Si sigue, avisa a la voz (h24) por el orquestador. |
| `mesa.oido` dice error o `sesion: false` | Revisa `PCX_GATEWAY` y `PCX_USER_TOKEN` en la terminal de `servir` y reinícialo. |
| «Voz (bloqueada)» en la página | Falta `--voz` en `servir`. |
| No aparece la C920 | Permiso de micrófono de Chrome para 127.0.0.1, y «Ver nombres de micrófonos» otra vez. |
| Lumo dice que no puede pedirle cosas al preset | Normal si el conductor solar no atiende `preset_accion`: Lumo usa señalar, nombrar y explicar. |
| Lumo habla encima de la presentación | Tecla M (micrófono silenciado); además Lumo usa `skip_turn` cuando no le hablan a ella. |

## Alternativa: con tu propia llave de ElevenLabs, en OTRA cuenta

Solo con una cuenta de ElevenLabs distinta de la de Max, y **sin `doppler run`**: la llave de Doppler es la de la
cuenta de Max, y ahí `publicar` pisa al Lumo de todos o crea uno nuevo. Con tu propia llave en
`export ELEVENLABS_API_KEY=…` y sin `SD_VOZ_AGENT_ID`, crea el agente en esa cuenta y sigue desde el paso 4 (también
sin `doppler run`):

```sh
uv run python -m sdvoz publicar --escena solar   # agentes como código (equivale a `elevenlabs agents push`); idempotente
```

Si la voz no existe en tu cuenta: `SD_VOZ_SOLAR=<voice_id de una voz tuya en español>` antes de `publicar`. Para ver
el payload sin tocar la red: `--dry-run`.

Más detalle (arquitectura, las tres escenas, tools): [README.md](README.md).

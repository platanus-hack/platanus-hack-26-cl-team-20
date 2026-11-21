# Agente de voz «Playground» (ElevenLabs)

Una voz tranquila, estilo guía de meditación, que acompaña a Max mientras se construye el playground: le cuenta cada
avance en vivo, recoge su feedback para quien programa y opera la escena cuando él lo pide (efectos, parámetros,
modos, audio reactivo, luces, MIDI, tempo, ideas).

```
Mac de Max: micrófono y parlantes del MacBook ⇄ voz.py (SDK elevenlabs, sounddevice) ⇄ agente «Playground» (nube)
                │ client tools (corren aquí)                     ▲ avances (contextual update + aviso)
                ▼                                                │
         runtime :8790 · requests/ · orq-creativo note h3    avances.jsonl ◀── h3 / agentes: `avance "…"`
```

Sin navegador, sin webhooks y sin túnel: las herramientas son *client tools* que ejecuta el proceso de voz en el Mac.
La vía por túnel (webhooks hacia un puente local, como la secretaria) queda como opcional, más abajo.

| Archivo | Qué hace |
|---|---|
| `voz.py` | la conversación nativa: sesión con reconexión, entrega de avances sin pisar a Max, dormir y despertar |
| `audio.py` | `AudioInterface` del SDK con `sounddevice` (MacBook por nombre), semidúplex, detector de voz adaptativo |
| `avances.py` | cola `avances.jsonl` y las notas de h3 en orq-creativo |
| `tools.py` | catálogo de 19 herramientas (17 `pg_*` + `feedback` + `decision`) y sus handlers |
| `agent.py` | crea o actualiza el agente por la API (client tools o webhooks), con verificación; `--dry-run` |
| `procs.py` | levanta runtime y captura de audio desde el checkout principal, con los pidfiles del MCP |
| `prompt.md` | prompt del sistema (español de Chile, relajado); `{{ESCENA}}` se llena al publicar |
| `config.py` | rutas, config y secretos por referencia |
| `server.py`, `tunel.py`, `app/` | vía por túnel (opcional) y página web local para hablar |

## Agente

- «Playground», id en `~/.config/sl-scanner/secretaria/config.json` (`agente_id`). Auth habilitada (sin enlace
  público), sin grabar voz, retención 30 días, hasta 60 conversaciones al día.
- Voz «Secretaria · Catalina (CL)» con `eleven_v4` expresivo, velocidad 0,9 y estabilidad 0,65 (pedido de Max por voz,
  16-oct). Si una voz tiene moderación en vivo, ElevenLabs la rechaza para agentes: `publicar` prueba `voces_respaldo`.
- No corta por silencio (`silence_end_call_timeout` −1), no rellena con muletillas y puede quedarse callada
  (`skip_turn`). El primer mensaje se puede reemplazar por sesión: al reconectar o despertar no saluda.

## Usar

```bash
.venv/bin/python -m playground.secretaria publicar           # crea o actualiza el agente (modo local)
.venv/bin/python -m playground.secretaria voz --fondo         # conversación en vivo en segundo plano
.venv/bin/python -m playground.secretaria avance "el halo ya sigue al cocodrilo" --nivel cambio
.venv/bin/python -m playground.secretaria avance "calibrando la cenital, van 3 de 8"          # nivel trabajo
.venv/bin/python -m playground.secretaria avance "¿halo turquesa o dorado?" --opciones "turquesa|dorado" --pregunta q16
.venv/bin/python -m playground.secretaria avance --orquestador   # las notas nuevas de h3 (orq-creativo) a la cola
.venv/bin/python -m playground.secretaria estado
.venv/bin/python -m playground.secretaria voz --parar
```

`voz` en primer plano muestra lo que se dice. Opciones: `--duplex` (con audífonos: Max la puede interrumpir),
`--dormir-tras 10` (minutos sin nada antes de colgar; 0 = nunca), `--con-orquestador 90` (lee las notas de h3 cada
90 s), `--sin-saludo`.

**Permiso de micrófono**: la primera vez macOS pide permiso de micrófono para la app que lanza Python (Terminal,
iTerm, Ghostty…). Si se negó, el micrófono entrega silencio exacto y `voz.jsonl` trae un aviso; se arregla en
Ajustes del Sistema › Privacidad y seguridad › Micrófono.

### Cómo se comporta

- **Avances**: quien trabaja escribe `avance "…"` (una línea en `~/.local/state/sl-scanner/secretaria/avances.jsonl`).
  La voz lo entrega como *contextual update* + un aviso del sistema para que lo cuente en una o dos frases, cuando
  Max no está hablando, ella tampoco y no le debe una respuesta. Si hay charla o música constante en la sala, a los
  60 s entra igual en la primera pausa de ella. Avances escritos con la voz apagada se cuentan al volver (hasta 15 min
  de antigüedad).
- **Feedback**: cuando Max opina, pide un cambio o dice «mándaselo», ella usa `feedback`: queda textual en
  `feedback.jsonl` (una línea JSON con hora y texto), en `requests/` (formato de las ideas, kind `fix` por defecto) y
  como `orq-creativo note h3 "Feedback de Max por voz (textual): …"`. Para leerlo en vivo:
  `tail -f ~/.local/state/sl-scanner/secretaria/feedback.jsonl`.
- **Decisiones**: si un avance trae `--opciones`, ella pregunta; lo que Max elige va con `decision` a
  `feedback.jsonl` (tipo `decision`), como nota a h3 y, si trae `--pregunta qN`, como `orq-creativo answer qN …
  --de persona --send`.
- **Colgar y despertar**: si Max se despide («chao», «corta la llamada») ella cuelga (y si el LLM se olvida de
  colgar, la voz cuelga sola al terminar la despedida); también tras `--dormir-tras` minutos sin nada. Dormida no se
  paga: el próximo avance la despierta sin saludo.
- **Cortes**: si se cae la conexión, reconecta con espera creciente (1 s → 30 s) y sin saludar.
- **Eco**: PortAudio no cancela eco. Por defecto es semidúplex: mientras ella habla (y 0,4 s después) al micrófono se
  le manda silencio, así no se escucha a sí misma; Max no la puede interrumpir mientras habla (frases cortas). Con
  audífonos, `--duplex`.
- **Runtime y cola**: el runtime (y la captura de audio) se levantan desde el checkout principal (`runtime_repo`,
  aunque este código corra en un worktree) con los mismos pidfiles que el MCP `playground`; ideas y feedback van a
  `requests/` de ese checkout, con candado (dos ideas dichas juntas no comparten número).
- **Registro**: todo en `~/.local/state/sl-scanner/secretaria/voz.jsonl` (lo dicho, herramientas, avances, estado y
  un latido cada 20 s con micrófono, piso de ruido, si está sonando y cuánto sonó).

## Herramientas

`python -m playground.secretaria herramientas` lista el catálogo.

| Herramienta | Para |
|---|---|
| `pg_estado`, `pg_que_proyectas` | qué hay y qué se ve (efectos en palabras, % del lienzo encendido, color dominante, captura) |
| `pg_efectos`, `pg_objetos`, `pg_buscar_objeto` | galería, objetos de la escena, medidas |
| `pg_lanzar_efecto`, `pg_apagar_efecto`, `pg_ajustar` | spawn / kill / set_param; acepta «el halo», «el del cocodrilo», colores por nombre |
| `pg_modo` | touch, marimba, dibujo, ninguno |
| `pg_audio_reactivo` | prende o apaga la captura de audio (`playground.audio`) y dice qué efectos la siguen |
| `pg_luz` | foco SmallRig por Bluetooth (estado, encender, apagar, intensidad, Kelvin, color); DMX pendiente |
| `pg_midi`, `pg_enlazar` | OHM RGB: estado, conectar, controles, simular; enlazar perillas a parámetros o al bpm |
| `pg_tempo` | bpm y tocando |
| `pg_idea`, `pg_ideas` | dejar ideas en `requests/` y ver en qué van |
| `feedback`, `decision` | lo que Max opina o elige, a h3 (ver arriba) |
| `pg_runtime` | estado, levantar, reiniciar, recargar efectos |

**Modos** (touch, marimba, dibujo): el runtime todavía no los tiene. `pg_modo` usa `{"cmd": "mode", "args": {"mode":
"touch|marimba|dibujo|none", "on": true}}` si existe; si no, lanza o apaga un efecto de la galería que lo haga (por
nombre: touch/táctil, marimba, draw/dibujo/paint); si no hay, responde «pendiente» y ella ofrece dejar la idea.

**Audio reactivo**: con el runtime que trae `audio` en su estado (master, 16-oct), `pg_audio_reactivo` levanta o apaga
`python -m playground.audio run` (mezcladora QX1222 › loopback › micrófono del Mac). En un runtime viejo responde
«pendiente».

## Vía por túnel (opcional)

Webhooks de ElevenLabs → túnel propio de Cloudflare → puente `127.0.0.1:8793` (`server.py`) → los mismos handlers.
Sirve para hablarle desde fuera del Mac. Al abrirse por primera vez, desde este entorno el permiso de Claude Code
bloqueó el túnel público («External Ingress Tunnel»): lo tiene que correr Max o autorizarlo.

```bash
.venv/bin/python -m playground.secretaria up            # puente + túnel en segundo plano; publica en modo tunel
.venv/bin/python -m playground.secretaria down
.venv/bin/python -m playground.secretaria publicar --modo local   # volver a la voz nativa
```

- El túnel por defecto es rápido (`cloudflared tunnel --url http://127.0.0.1:8793`): sin cuenta ni login, subdominio al
  azar de trycloudflare.com, distinto del de la secretaria; la URL cambia al reabrir y se republica sola. Para una URL
  fija: `cloudflared tunnel login` (Max, en el navegador), `cloudflared tunnel create sl-scanner-playground`,
  `cloudflared tunnel route dns sl-scanner-playground playground.<zona>` y en la config
  `"tunel": {"modo": "named", "nombre": "sl-scanner-playground", "hostname": "playground.<zona>"}`.
- Todo POST exige `X-Playground-Token` (token en `~/.config/sl-scanner/secretaria/token`, 600; en ElevenLabs como
  secreto del workspace `PLAYGROUND_PUENTE_TOKEN`). Probar sin pegar el token:
  `python -m playground.secretaria probar pg_estado '{"motivo":"x"}' [--tunel]`; sin token responde 401.
- Página web local para hablar (:8794, nunca por el túnel): `python -m playground.secretaria hablar`.

## Secretos (nada en el repo)

- Llave de ElevenLabs: `$ELEVENLABS_API_KEY`, si no el archivo `~/.elevenlabs/api_key` (la misma cuenta que la
  secretaria), si no el llavero (`api_key_llavero` en la config). Nunca se imprime ni se registra.
- Config: `~/.config/sl-scanner/secretaria/config.json` (600): ids de agente y herramientas, voz, modo.

## Pendientes

- Salida de la voz por la mesa de audio del PC Windows (pedido de Max por voz, 16-oct): hace falta un receptor de
  audio en el PC (en la sesión interactiva) y enviarle el PCM por la LAN; mientras, suena por el MacBook.
- Modos touch / marimba / dibujo en el runtime (touch = pedido 004).
- Luces: el SmallRig necesita el protocolo BLE aprendido; DMX espera la interfaz USB-DMX.
- Ableton: la voz fija el bpm del playground; el tempo de Live va por el MCP `ableton`.
- LaunchAgent para que la voz arranque con el Mac (opcional; no está instalado).
- Costo: ElevenLabs cobra por minuto de conversación abierta, hable o no; por eso duerme a los 10 min sin nada.

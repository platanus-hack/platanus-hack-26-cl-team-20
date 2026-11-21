# Informe técnico: interacción, audio, MIDI, luces y secretaria por voz (sl-scanner b8fecff)

## 1. Propósito y funcionalidades

- **touchsynth**: convierte la mesa en una superficie táctil. Detecta toques con la Osmo cenital (en el Mac) y con la Brio, que corre MediaPipe en el PC Windows, y fusiona ambas. Los toques llegan a instrumentos que viven dentro del runtime:
  - croc_synth / objeto_synth: zonas que suenan.
  - croc_alive / objeto_vivo: patas animadas, respiración y escamas.
  - touch_draw (dibujo), marimba y linterna (relieve iluminado por la mano).
  - toque_onda, mano_luz, juego_memoria (tipo Simón) y mirada.
- **audio**: captura en el Mac y calcula bandas, RMS, golpes, BPM y rasgos por canal a 50 Hz. Lo publica en `scene.audio`.
- **devices**: registro de dispositivos MIDI (OHM RGB y Korg EK-50) con controles normalizados entre 0 y 1, y *bindings* control → parámetro.
- **pianohero**: Guitar-Hero proyectado sobre las teclas reales del EK-50. Es específico de esta escena.
- **lights**: foco SmallRig por BLE. Su protocolo nunca se aprendió, así que es un stub.
- **secretaria**: agente de voz «Playground» sobre ElevenLabs Conversational AI. Cuenta avances del trabajo, recoge feedback y opera el runtime con 20 herramientas.
- **windows/**: puente MIDI del PC al Mac (Korg), receptor de notas que suena en el PC y utilidades de diagnóstico.

## 2. Arquitectura interna

**Toque (proceso `touchsynth live`, separado del runtime)**
- `osmo_hands.PovMapper` (`osmo_hands.py:40-64`) remapea cada cuadro de la Osmo a la vista del proyector (POV) usando `proj_to_cam.npz` del gray code. El factor es 1/3 en vivo (640×360), `__main__.py:35`. Resultado: las detecciones ya salen en px del proyector, sin homografías.
- `ProjectionModel` (`osmo_hands.py:74-163`) predice la cámara como `B + A·(p/255)^γ`.
- `OsmoHands` (`213-511`) produce las máscaras de primer plano y sombra, y extrae las puntas.
- `fusion.BrioContact` (`fusion.py:54-110`) convierte las manos de MediaPipe en `BrioObs` (px del proyector, contacto, altura). Depende de `playground/hands/calib.py` y `hands/model.palm_size`, fuera de mi alcance.
- `fusion.Fusion` (`156-388`) hace asociación, tracking, histéresis de contacto y genera los eventos.
- `live.Live` (`live.py:138-419`) orquesta hilos y la comunicación HTTP con el runtime. Usa `hands.bridge.TouchBridge` y `hands.client.HandsClient`, también fuera de alcance.

**Instrumentos (dentro del runtime)**
- La base `Instrument` (`instrument.py:22-63`) define `hit / handle / step / render / resolve / status / close`.
- `registry.Registry` y `TouchState` se cuelgan de `scene.__dict__` (`registry.py:167-180`). Por eso sobreviven al hot reload de los efectos.
- `effectkit.InstrumentEffectMixin._run` (`effectkit.py:33-42`) es el pegamento con `playground/effects/*`. Por ejemplo, `croc_synth.py` es una subclase de `ObjetoSynthEffect`.
- Geometría: `scan.ScanData` (`scan.py:55-201`) lee workbench.json, segments.json, height_proj.npy y proj_to_cam.npz. Si falta la carpeta, usa una escena sintética.

**Sonido**
- `music.MidiOut` es un singleton por proceso (`music.py:250-266`) con varios destinos (`sound.py`): `MidoPort` (puerto virtual «sl-scanner touch» o IAC), `MacSynth` (sounddevice), `UdpMidi` (PC, UDP 8797) y `SshMidi` (ssh que lanza `midi_play.py --stdin`).
- `Notes` (`music.py:271-327`) cuenta notas por (canal, nota), aplica largo mínimo de 0,12 s y programa los note-off.

**Audio**
- `capture.run` hace captura → `MultiAnalyzer` → `Publisher` (POST httpx).
- `features.py` no tiene dependencias y lo usan el runtime y los efectos.
- `mapping.py` / `mapping.json` definen qué canal mueve qué parte (efecto `channel_react`).

**Dispositivos**
- `DeviceRegistry` con fuentes `SmartDeckSource`, `DirectMidiSource`, `BridgeMidiSource`, `KeyboardSource` y `FakeSource` (`devices/midi.py`, `keyboard.py`).

**Secretaria**
- `config` (rutas y secretos por referencia), `agent` (publica el agente vía la REST de ElevenLabs), `tools` (catálogo y handlers que llaman a `/api/*` del runtime).
- `voz` (sesión, entrega de avances, dormir/despertar), `audio` (AudioInterface), `avances` (cola jsonl).
- `procs` (levanta el runtime y la captura de audio), `server`/`tunel` (vía webhooks), `app/index.html` (WebRTC en el navegador).

**Flujo de datos del toque**
1. Osmo (USB, Mac) → `CamReader` → `OsmoHands.detect_frame` → `OsmoTip[]` → `Fusion.update_osmo`.
2. Brio (PC) → WS `:8793` → `HandsClient` → `BrioContact.observe` → `Fusion.update_brio`.
3. `Fusion.step` → evento → `TouchBridge.send` → POST `/api/command {"cmd":"touch"}`.
4. `Runtime.touch` → `TouchState.add` (asigna seq) y `emit("touch")` a SSE.
5. En el tick: efecto → `Registry.pump` → `owner()` → `Instrument.handle` → `Notes.on` → `MidiOut.send` (sonido) y `render` sobre el lienzo (luz).

**Flujo de datos del audio**
1. PortAudio (callback) → cola → `MultiAnalyzer.process` → POST `audio` a 50 Hz.
2. `Runtime.set_audio` reemplaza un dict, sin lock.
3. En cada tick, `_tick_audio` arma `scene.audio` (la mezcla más `ch[]`) y aplica `audio_binds`.
4. Los efectos leen `audio_level(scene.audio, "bass" | "ch2.rms" | "kick"…)`.

**Bindings (punto b)**
- Hay dos sistemas paralelos con el mismo `Binding(control, target, lo, hi).map(v) = lo + (hi−lo)·v` (`devices/midi.py:72-80`):
  - **MIDI** (`DeviceRegistry.bindings`): se aplica al llegar el evento, en el hilo de la fuente (`midi.py:104-124`).
  - **Audio** (`Runtime.audio_binds`): se aplica en cada tick (`runtime.py:291-311`); `bpm` y `onsets` se leen como valor crudo.
- Ambos terminan en `Runtime._apply_binding` (`runtime.py:253-263`):
  - `effect:<iid>:<param>` hace `inst.set(param, value)`, es decir `effect.p[param] = value`, y el efecto lo lee en su próximo `update`.
  - `beat:bpm` y `beat:playing` modifican `scene.beat`.
- El destino se refiere a un id de instancia (`e3`). `pg_enlazar` de la voz y `pg_bind` del MCP resuelven nombres («halo») a ids (`tools.py:605-619`).
- Los bindings no se persisten.

**Dependencias que salen del alcance**: `playground/runtime.py`, `scene.py`, `effects/*` (`base.Effect.set`, `croc_synth`, `objeto_synth`, `pianohero`, `bass_*`, `channel_react`…), `hands/*` (`bridge`, `client`, `calib`, `model`), `requests_queue.RequestQueue`, `keys/fit.py`, `slscan.capture.Camera` / `PatternWindow`, `windows/hands_service.py`, `mcp_server.py`.

## 3. Ejecución y concurrencia

**Proceso `touchsynth live`** (todos los hilos son daemon):
- `cam-reader`: lee la Osmo en 3840×2160, rota 270° y reduce ×2 (`live.py:209`). Guarda solo el último cuadro con `Condition` (`79-125`).
- `osmo`: detección por cuadro nuevo, luego `Fusion.step` (`281-298`).
- `brio`: espera los mensajes del WS (`336-347`); `HandsClient` tiene su propio hilo.
- `fusion-watchdog`: a 10 Hz suelta toques de cámaras calladas y publica `touchsynth en_vivo` cada 1 s. Es *pull*: el runtime devuelve el flag `recalibrar` (`349-363`, `registry.py:133-135`).
- `runtime-frames`: baja `/api/frame.png` cada 0,12 s, unos 8 Hz (`271-279`).
- `touch-bridge`: cola de 256 que bota «move» si se llena y nunca «down/up».
- El hilo principal imprime el estado.
- La fusión está protegida por `flock`.
- El comando `hands` se manda **síncrono** dentro de `_step` a ~14 Hz, con timeout de 0,3 s (`live.py:170-175`). Por eso bloquea el hilo de detección si el runtime está lento.

**Runtime** (fuera de alcance, verificado en extractos):
- Tick a 30 fps por defecto (`runtime.py:125`). En cada tick drena `_inputs` (deque de 4000) hacia `scene.inputs`, toma una foto de `scene.controls`, corre `_tick_audio` y luego `update/draw` de cada efecto bajo `self.lock` (`403-421`).
- Los instrumentos dibujan y mandan MIDI **en el hilo del tick**.
- Las fuentes MIDI llaman a `DeviceRegistry.handle` desde su propio hilo (rtmidi callback, asyncio de SmartDeck, rx UDP). Ese camino aplica los bindings sin tomar el lock del runtime (`midi.py:119-121` → `runtime.py:253-263`).
- `MidiOut` tiene un hilo `midi-busca-mesa` que reintenta cada 60 s. `MacSynth` usa un callback de PortAudio con bloques de 256 a 44,1 kHz (~5,8 ms). `SshMidi` tiene un hilo lector de stdout.
- La API HTTP corre en hilos de uvicorn.

**Proceso de audio**: callback de PortAudio (bloque = hop = sr/50, 960 muestras a 48 kHz) → `queue(256)` → bucle principal que analiza y publica solo si `qsize < 3` (`capture.py:166-224`). El runtime consume a 30 Hz, así que algunos paquetes se pisan; `onsets` es un contador y no se pierde, y `beat` decae con τ = 0,12 s.

**Secretaria `voz`**:
- Hilo principal con el bucle de sesiones (`voz.py:273-303`).
- `voz-avances`, que vigila cada 0,5 s (`259-265`).
- Hilos del SDK de ElevenLabs (WebSocket y client tools, que según el comentario de `tools.py:333` pueden correr en paralelo).
- Callback de entrada de PortAudio (bloques de 250 ms, 16 kHz, int16) y el hilo `voz-salida`, que escribe de a 100 ms (`audio.py:27-29,177-194`).

**En memoria** (tamaños aproximados o inferidos):

| Estructura | Tamaño |
|---|---|
| `TouchState.log` | ≤ 2000 dicts (`registry.py:143`), del orden de 1 MB |
| `Registry.claims` | ≤ 512 |
| Eventos del runtime | ≤ 500 |
| `OsmoHands.proj_hist` | 64 × 640×360×3 uint8 ≈ 44 MB |
| Caché `_term` | float32 de 2,8 MB por cuadro proyectado que siga en el historial; puede llegar a ~177 MB (inferido de `osmo_hands.py:314-327`) |
| `bg`, `term_ref`, `B`, `A` | float32 de 2,8 MB c/u |
| `PovMapper` | 2 mapas float32 de 640×360 |
| `HeightField` de linterna / mano_luz | 6 arrays de 270×480 float32 ≈ 3 MB |
| Máscaras de `CrocZones` / `CrocRig` | del tamaño de la bbox del objeto |
| Audio | `TempoEstimator` 400 floats, historial de flux 50, cola ≤ 256 bloques |
| `MidiOut.log` | 64 |
| `DeviceRegistry.events` | ≤ 200 |
| Secretaria | `out_q` sin límite (PCM), `pending` (avances) |

## 4. Comunicación e interfaces

| Enlace | Protocolo y puerto | Formato |
|---|---|---|
| live → runtime | HTTP `127.0.0.1:8790` POST `/api/command` (urllib, sin keep-alive) | `{"cmd":"touch","args":ev}`; `hands`; `overlay` (ruta de PNG local: supone el mismo host); `touchsynth en_vivo`; `spawn`/`kill` |
| live ← runtime | GET `/api/frame.png` (~8 Hz), `/api/schema` | PNG 1920×1080 |
| PC → Mac (Brio) | WS `ws://192.168.1.233:8793` (`hands_service.py`) | `{"type":"hands","t","seq","size","fps","det_ms","detector","hands":[{id,handed,score,landmarks[21],tip_index,pinch,pinch_ratio}]}` a 15–30 Hz; ping/pong de reloj; HTTP :8794 con `/frame.jpg`, `/frame.png`, `/health` |
| PC ↔ Mac (Korg) | UDP 8796 | Mac→PC `{"k":"hello","t","id"}` cada 1 s. PC→Mac `{"k":"m","s":seq,"t","b":[st,d1,d2]}`, `{"k":"hb",…}` cada 1 s, `{"k":"pong"}`. Suscripción con TTL de 10 s y filtro de IP privada (`midi_bridge.py:14-17,40`; `keyboard.py:163-201`). Pérdidas detectadas por `seq` |
| Mac → PC (sonido) | UDP 8797 | `{"v":1,"m":[[st,d1,d2]]}`, ping/pong (`sound.py:7-8`). El firewall está cerrado |
| Mac → PC (sonido, respaldo) | ssh `"Roberto Mardones@192.168.1.233"` con PowerShell `midi_play.py --stdin` | JSON por línea; responde `{"listo"}`, `{"pong"}`, `{"fin"}` (`sound.py:97-106`). En el PC suena el «Microsoft GS Wavetable Synth» con programas GM 1:89, 2:12, 3:98 |
| OHM | WS `ws://127.0.0.1:8090` (SmartDeck, app externa `~/ohmrgb`) o rtmidi directo | `{"type":"midi","payload":{input_type,id,row,col,value,state}}` |
| Ableton | puerto MIDI virtual «sl-scanner touch» o IAC | canales 1 cocodrilo, 2 marimba, 3 dibujo/onda, 4 memoria |
| audio → runtime | HTTP POST a 50 Hz con keep-alive | `{"cmd":"audio","args":{"features":{sub,bass,mid,high,rms,beat,onsets,bpm,ch:[…],tc,seq},"meta":{…} cada 1 s}}`; al salir, `{"stop":true}` |
| voz ↔ ElevenLabs | WSS con URL firmada (`requires_auth=True`) y REST `api.elevenlabs.io` para publicar (agentes, herramientas, secretos, token de conversación) | PCM16 mono 16 kHz |
| voz → runtime | httpx a `/api/command`, `/api/state`, `/api/frame.png` | — |
| ElevenLabs → Mac (opcional) | webhooks por túnel cloudflared → `127.0.0.1:8793/playground/h/<tool>` con `X-Playground-Token` y `X-Conversacion` | — |
| Página local | `127.0.0.1:8794/app`, WebRTC con token de conversación; valida Host/Origin | — |
| voz → orquestador | subprocess `~/bin/orq-creativo note h3 …` / `answer qN … --send` / `show h3 --json` | — |

Los puertos 8793 y 8794 se usan a la vez para el servicio de manos del PC y para la secretaria del Mac. Son hosts distintos, pero se presta a confusión.

**Modelo de eventos de toque (punto a)** (`fusion.py:345-355`):
- Campos: `{"id":"fN","state":"down|move|up","x","y" (px del proyector, 0,1),"space":"proj","projector":"proj0","mm":null,"hand","handed","trigger":"sombra|presencia|pinch|apoyo","t","source":"osmo|brio|fusion","height"}`.
- `velocity` (0..1) va solo en down; `reason:"mano perdida"` en los up por pérdida.
- El runtime agrega `seq`, valores por defecto (`id`, `t`, `projector`, `source="api"`) y el estado `tap` (down+up). Acepta `target:"croc:3"` o el nombre de un objeto en vez de x,y (`runtime.py:480-512`).
- Destinatarios: los instrumentos consumen por `seq` desde el log, SSE `/api/events` emite `touch`, y el MCP lo lee con `pg_touch list` / `pg_gestures` (polling de `/api/state`).
- Reparto: en el down el toque lo reclama el instrumento de mayor `priority` cuyo `hit()` da True (memoria 40 > cocodrilo 30 > marimba 20 > dibujo 10). Los move/up van solo a ese dueño. Los `observes_touch` (croc_alive, onda) ven todos los toques (`registry.py:43-74`).
- Las manos (`hands`) no son toques: `{id,x,y (centro de la palma),tip,height,dir,source,touching,t}` a ~14 Hz, guardadas en `TouchState.hands`. Solo se emite evento cuando cambia el conjunto de ids (`runtime.py:518-537`).
- La fuente de los landmarks es MediaPipe Tasks HandLandmarker **en el PC Windows** (Brio, `windows/hands_service.py`). En el Mac no hay MediaPipe en este camino: la Osmo usa visión clásica (diferencia contra la predicción + morfología).

## 5. Algoritmos, costo y latencia

**Detección con la Osmo (modelo «híbrido» en vivo)** (`osmo_hands.py:329-395`):
- Predicción: `fondo + g·(A·p^γ − A·p_ref^γ)`, con exposición global `e` (mediana de cámara/predicción) y ganancia `g` estimada de Δcámara/Δproyectado entre cuadros seguidos sin mano (EMA 0,7/0,3).
- Compara contra 2 cuadros proyectados en la ventana de latencia de 30–400 ms, más el último. Toma el residuo mínimo y descarta píxeles saturados.
- Umbral de primer plano: `thr 22 + 0,08·brillo`.
- Sombra: primer plano con menos del 30 % de la luz predicha donde debía llegar más de 30 niveles.
- Morfología open 3 / close 9, agrupación con dilatación de 25, hasta 4 manos.
- Punta: punto del contorno más lejano a la entrada del brazo por la banda del borde. Exige alcance ≥ 60 px.
- Contacto: `gap` = distancia punta↔punta de su sombra, que es ~0 al tocar. Ventana de sombra de 45 px de POV.
- El fondo se adapta con `accumulateWeighted` (α 0,03) fuera de la mano. Si el primer plano supera el 35 % durante 20 cuadros, reaprende el fondo.
- Calibración inicial: 4 tableros complementarios por `overlay`, ~5 s sin manos; B = mínimo y W = máximo por píxel (`live.py:225-269`, `osmo_hands.py:107-128`).
- Costo: el README dice 9 ms por cuadro. Mi microbenchmark sintético (entrada 1920×1080, POV 640×360, 3 comparaciones) dio **p50 19 ms, p90 23 ms**.

**Fusión** (`fusion.py:200-343`):
- Asociación greedy Osmo↔Brio (< 120 px; usa la palma si existe) y pistas por vecino más cercano (`max_jump` 160).
- EMA 0,6, deadband 2,5 px, histéresis de 2 cuadros para down y 2 para up, pérdida a 0,25 s, cámaras «vivas» si mandaron algo en los últimos 0,35 s.
- Contacto: Brio (pinch o tamaño de palma con ratio < 1,06 / 1,12) > sombra (gap < 12 px para entrar, < 22 para mantener, memoria de 0,5 s) > «presencia» (dedo quieto 0,3 s, moviéndose < 15 px).
- Velocidad: caída de altura o del gap en 0,3 s.
- Costo de `step`: ~1 µs (medido, caso trivial).

**Latencia del toque** (estimada, **no hay medición de punta a punta**): cámara Osmo (sin medir) + 9–20 ms de detección + 1 cuadro de histéresis + 1–3 ms de HTTP + 0–33 ms de espera del tick + ~9 ms de ventana SDL + proyector. Inferido: unos 100–200 ms. Para el sonido hay que sumar el GS Wavetable, ~50–100 ms según `touchsynth/README.md:124`. MacSynth agrega ~6 ms de bloque más la latencia del dispositivo.

**Otros algoritmos**
- Instrumentos:
  - Zonas por PCA del contorno; la cabeza es la punta más ancha (`croc.py:29-104`).
  - Las patas salen de una mediana móvil del perfil lateral (scipy) y las escamas de un cKDTree hexagonal (`alive.py:50-143`).
  - Marimba en el rectángulo libre más grande, buscado con imágenes integrales (`scan.py:214-244`).
- Linterna: Lambert + cono + sombras por *ray marching* de 12 pasos a 480×270. Medí **8,4 ms por luz**.
- Audio (`analysis.py`):
  - Bandas IIR Butterworth SOS con estado (sub 25–60, bajo 60–180, medios 180–2k, agudos 2k–8k).
  - AGC en dB: techo que baja 3 dB/s, piso que sube 0,5 dB/s, ventana de 24 dB.
  - Onset en 30–180 Hz con umbral = máx(6 dB, mediana + 3·MAD de 1 s) y refractario de 100 ms; `beat` decae con τ = 0,12 s.
  - BPM por autocorrelación de 8 s con prior log-normal en 120, cada 0,5 s.
  - Medí **0,59 ms por cuadro con 2 canales** (p99 0,85).
  - Latencia según el README: ~60 ms de sonido a luz (ADC 21,5 ms + bloque 10 + análisis 0,3 + POST ~1 + tick ~18 + SDL 9), más el retardo del proyector (`audio/README.md:60-72`).
- Pianohero: el juicio usa la hora de llegada de la nota, no la del cuadro, y `offset_ms` compensa la latencia (`game.py:139-184`). El modelo es correcto. Los niveles simplifican la canción (manos, polifonía, solo blancas, rango) en `songs.py:380-419`. El puente por Wi-Fi da 2–5 ms de ida y vuelta, con picos de 100–200 ms (README).

## 5b. Secretaria en detalle (punto c)

**Stack**
- ElevenLabs Conversational AI. LLM `claude-sonnet-5-5` configurado dentro de ElevenLabs, temperatura 0,4, `reasoning_effort` low (`agent.py:120-163`, `config.py:52`).
- TTS `eleven_v4` expresivo (etiquetas [warmly], [softly]…), velocidad 0,9, estabilidad 0,65, voz chilena (voice ID hardcodeado en `config.py:50` con respaldo en `:51`). ASR con palabras clave del dominio (`agent.py:33-35`), idioma `es`.
- Turnos: `turn_timeout` 30, `silence_end_call_timeout` −1, duración máxima 3600 s.
- Plataforma: auth habilitada, sin grabar voz, retención 30 días, concurrencia 2, 60 conversaciones al día. Se puede reemplazar `first_message` por sesión.
- Herramientas de sistema: `end_call` y `skip_turn`.
- Cliente: SDK Python `elevenlabs` 2.79.0, `Conversation` con `requires_auth=True` (la llave se lee por referencia: env → archivo → llavero; nunca va a logs) y `AudioInterface` propia (`SoundDeviceAudio`).

**Dos modos de herramientas** (`agent.py:5-9`)
- `local` (por defecto): **client tools** que ejecuta el propio proceso de voz → `Toolbox.call` → `coerce` → handler → httpx al runtime local.
- `tunel`: **webhooks** de ElevenLabs → cloudflared (quick: trycloudflare, URL al azar que se republica al reabrir; o named) → FastAPI `127.0.0.1:8793` con token → el mismo `Toolbox`.
- `publicar` es idempotente: hace upsert de herramientas y agente, prueba voces de respaldo si una tiene moderación en vivo y verifica lo que quedó (`agent.py:233-301`).

**Herramientas** (20; `tools.py:731-836`; ? = opcional; timeout 20 s salvo que se indique):
- `pg_estado(motivo)`
- `pg_que_proyectas(motivo)`: efectos en palabras, % del lienzo encendido y color dominante (desde el PNG), y guarda un snapshot.
- `pg_efectos(motivo)`
- `pg_objetos(tipo?∈{todos,table,screen,frame,prop,instrument,person})`
- `pg_buscar_objeto(nombre)`
- `pg_lanzar_efecto(efecto, objeto?, parametros? JSON-string)`
- `pg_mover(objeto, direccion?∈{arriba,abajo,izquierda,derecha}, pasos?, dx?, dy?)`: pasos de 4 px; usa el comando `nudge`.
- `pg_apagar_efecto(efecto?, todos?:bool)`
- `pg_ajustar(efecto, parametro, valor)`
- `pg_modo(modo∈{touch,marimba,dibujo,ninguno}, activo:bool)`
- `pg_audio_reactivo(activo:bool, sensibilidad?:number, dispositivo?)` [30 s]
- `pg_luz(accion∈{estado,encender,apagar,ajustar}, luz?, intensidad?:number, temperatura_k?:int, color?)` [40 s]
- `pg_midi(accion∈{estado,conectar,controles,simular}, dispositivo?, fuente?, tipo_control?, control?, valor?:number)`
- `pg_enlazar(control, destino?, minimo?, maximo?, quitar?:bool)`
- `pg_tempo(bpm?:number 20–300, tocando?:bool)`
- `pg_idea(titulo, detalle, tipo∈{visual,sound,tool,fix})`
- `pg_ideas(estado?)`
- `feedback(texto, tipo?)` [40 s]
- `decision(opcion, pregunta?)` [40 s]
- `pg_runtime(accion∈{estado,levantar,reiniciar,recargar_efectos})` [40 s]

Los errores vuelven como `{"error": …}`, nunca como excepción. Resuelven nombres de forma difusa: «el halo» → `contour_glow`, el objeto → instancia, colores por nombre (`tools.py:149-253`).

**Prompt** (`prompt.md`)
- Persona «Playground»: guía calmada, estilo meditación, español de Chile. Una o dos frases; no lee ids, rutas ni números largos.
- Cuenta los avances «ahora» cuando llega el aviso del sistema.
- Usa `feedback` con las palabras textuales de Max y `decision` cuando hay opciones.
- Nunca inventa estados (consulta `pg_estado`); hace una acción por turno sin pedir permiso para lo reversible; ofrece `pg_idea` para lo que está «pendiente».
- Silencio con `skip_turn`; cuelga cuando Max se despide.
- `{{ESCENA}}` se reemplaza al publicar por la galería y los objetos (`agent.py:72-97`).
- Límites: no programa, no envía nada hacia afuera y no dice secretos.

**Pedidos y avances**
- `pg_idea` → `RequestQueue(requests/ del checkout principal).add`, con candado entre hilos (`tools.py:632-639`).
- `feedback` → `feedback.jsonl` + `requests/` (kind fix) + `orq-creativo note h3 "…"` (`tools.py:674-683`).
- `decision` → `feedback.jsonl` + `orq-creativo answer qN … --send` + nota (`685-706`).
- Avances: `avance "…" --nivel cambio|trabajo --opciones A|B --pregunta qN` agrega una línea a `avances.jsonl` (`avances.py:41-55`). `Tail` lee con offset persistido y descarta avances de más de 15 min.
- La voz entrega con `send_contextual_update` («datos, no órdenes») + `send_user_message("(aviso del sistema: …)")` (`avances.py:111-127`, `voz.py:199-210`), solo si `can_speak` (`voz.py:186-197`):
  - sesión viva hace más de 1 s;
  - ella no está sonando;
  - Max no habló en los últimos 1,5 s (se ignora si el avance espera más de 60 s);
  - no hay una respuesta pendiente a Max (10 s);
  - no hay un avance anterior aún sin contar (15 s).
- Opcional: lee las notas de h3 de `orq-creativo show --json` cada N s.

**Sesión**
- Reconecta con espera creciente de 1 a 30 s y sin saludar.
- Duerme tras 10 min sin actividad (costo por minuto). Un avance la despierta.
- Respaldo de `end_call`: una regex de despedida cuelga 1,5 s después de que ella termina de hablar (`voz.py:32-33,245-250`).
- Audio semidúplex: silencia el micrófono mientras suena y 0,4 s después. VAD con piso adaptativo y aviso si el micrófono entrega ceros (permisos de macOS) (`audio.py:147-175`).

**Procesos** (`procs.py`)
- `start`: `<runtime_repo>/.venv/bin/python -m …` con `start_new_session`, log y pidfile compartidos con el MCP (`playground-<puerto>.pid`, `audio-<puerto>.pid`).
- `stop`: `killpg` SIGTERM, espera 5 s y luego SIGKILL.
- `RuntimeClient.ensure` levanta el runtime solo si está apagado (`tools.py:82-89`).
- `pg_audio_reactivo` lanza `playground.audio run` y espera hasta ~8 s a que el runtime reciba audio.
- La CLI (`voz --fondo`, `up`/`down`) usa sus propios pidfiles.

**Latencia por turno**: no hay medición en el repo ni logs en esta máquina. Componentes inferidos: bloques de micrófono de 250 ms, ASR + LLM + TTS en la nube y herramientas locales de pocos ms. Las excepciones son `pg_que_proyectas` (PNG ~43 ms + snapshot), `pg_audio_reactivo` (hasta 8 s) y `pg_luz` (BLE, de segundos a timeouts de 40–60 s).

## 6. Persistencia y supuestos de entorno

**Archivos**
- Escaneo `captures/scan5/{workbench.json, segments.json, height_proj.npy, proj_to_cam.npz, scene_pov.png, keys.json}` (`scan.py:32`, `layout.py:27`) y `captures/brio_calib.json`. **`captures/` no está en el snapshot**, así que todo cae a los respaldos sintéticos.
- `playground/audio/mapping.json`, recargado por mtime.
- `~/.local/state/sl-scanner/lights.json`.
- `~/.local/state/sl-scanner/playground-<puerto>.pid/.log` y `audio-<puerto>.*`.
- Secretaria: `~/.config/sl-scanner/secretaria/{config.json, token}` (0600) y `~/.local/state/sl-scanner/secretaria/{voz,llamadas,rechazos,procesos,publicar,tunel,http}.jsonl`, `feedback.jsonl`, `avances.jsonl` + `.offset`, `avances.orq-h3.ts`, `proyectando.png`, `tunel.cloudflared.log`.
- La cola `requests/` del checkout se comparte con el runtime.
- Los bindings son **solo en memoria** (no se persisten).

**Valores hardcodeados**
- PC `192.168.1.233`, usuario ssh `Roberto Mardones`, `C:/sl-scanner` (`sound.py:32,97-98`; `keyboard.py:31`; `touchsynth/__main__.py:40`); `--to 192.168.1.38` (Mac) en la documentación.
- Puertos 8790, 8793, 8794, 8796, 8797, 8090.
- Osmo 3840×2160 con rotación 270; lienzo 1920×1080 asumido en muchos módulos (`effectkit.py:10`, `scan.py:26`, `interactivo.py:30`, `layout.py:26`).
- Dispositivos de audio «MacBook», «QX1222/USB Audio CODEC», «GS Wavetable».
- `~/ohmrgb`, `~/bin/orq-creativo` (unidad `h3`), `~/.elevenlabs/api_key`, voice IDs en `config.py:50-51`, LLM `claude-sonnet-5-5` (`config.py:52`), `cliente_js` en `~/.local/share/secretaria/...`.
- `runtime_repo` se calcula cortando `/.claude/worktrees/` de la ruta (`config.py:46`).

## 7. Estado real

**Verificado por tests: 168 pasan, 1 se salta** (detalle al final).

| Componente | Estado | Evidencia |
|---|---|---|
| Lazo Osmo → toques → MIDI | Probado en vivo solo en tandas de menos de 20 s, **sin una mano tocando a propósito**; umbrales (`thr`, `gap_touch`) y latencia sin afinar; auto-exposición de la Osmo sin bloquear | `touchsynth/README.md:114-117` |
| Fusión Osmo + Brio | **Nunca probada en vivo**: la Brio apunta al piso y el servicio de manos no está instalado | `README:112-113` |
| Sonido por el PC | Probado por ssh (el receptor arrancó en 0,6 s); la regla de firewall UDP 8797 no existe | `README:121-124` |
| EK-50 | **No llega ninguna nota al PC** (Windows MIDI Services); el puente solo se probó con `--fake` | `pianohero/README.md:166-167` |
| Pianohero | Funciona con `--demo` | — |
| Luces BLE | Stub: el protocolo no se aprendió y `pg_luz` siempre responde error | `lights/ble.py:158-160`, `secretaria/README.md:144` |
| OHM con MIDI directo | **Roto**: `DirectMidiSource` nombra los controles `cc{N}`/`note{N}`, pero el OHM tiene `knobN`/`pad.r.c`/`btnN`, así que `handle` bota todo. El «learn» que promete `ohm.py:6` no existe (verificado con grep) | `midi.py:313-320`, `midi.py:109-110`, `runtime.py:197` |
| Audio | Funciona con el micrófono del Mac; el Mac no ve la QX1222; no hay entradas multicanal | `audio/README.md:24-26,57-58` |
| Secretaria | Modo local (client tools) es el principal; túnel opcional y bloqueado por permisos en su momento; `pg_modo`/`pg_audio_reactivo` traen caminos de respaldo para runtimes viejos | — |

Pendientes declarados de la secretaria: voz por la mesa del PC, LaunchAgent y luces.

Hay deriva entre doc y código: el README de la secretaria dice 19 herramientas y hay 20, porque se agregó `pg_mover`.

Específicos de escena: croc_synth, croc_alive, marimba, linterna, pianohero, `mapping.json` (Buda, DDJ, Circuit), OHM. Ya están generalizados: objeto_synth, objeto_vivo, toque_onda, mano_luz, memoria y mirada. La mirada se simula por comando; no hay cámara de ojos.

## 8. Rendimiento y cuellos de botella

1. **`/api/frame.png`**: medí ~43 ms por codificación PNG de 1920×1080. A 8 Hz son ~35 % de un núcleo en el runtime, más la decodificación en live. Además, el modelo de proyección queda con 120 ms de granularidad temporal y con timestamps aproximados (la hora del GET, no la del render).
2. Todo el render y el MIDI corren en un solo hilo Python a 30 fps (26,8 fps medidos con proyector, `audio/README.md:69`). Linterna suma ~8 ms por luz además del resize y `cv2.add` a pantalla completa; croc_alive compone en float32 con `warpAffine` por pata (objeto_vivo lo resuelve con una LUT uint8, «~4× más barato»).
3. HTTP por evento de toque sin keep-alive (`TouchBridge._post` usa urllib). El `hands` síncrono en el hilo de detección (`live.py:170-175`).
4. Detección de la Osmo: 9–20 ms en CPU (numpy y OpenCV) con varias pasadas float32 a 640×360.
5. `Registry.pump` recorre el log completo (hasta 2000 eventos) por instrumento y por tick (`registry.py:63`).
6. Si la sesión ssh del PC se traba, `SshMidi._write` puede bloquear el tick (`sound.py:171`).
7. BLE abre una conexión por comando (`ble.py:177`), del orden de segundos.
8. Las herramientas de la secretaria piden `/api/state` completo casi siempre.

## 9. Deuda técnica, bugs y riesgos

**Bugs y fallos de diseño**
- OHM directo roto (sección 7). `SmartDeckSource` mapea `knob{CC}` sin `cc_map` (`midi.py:229-232`, `runtime.py:193`), probablemente desalineado con `knob0..15` (inferido).
- `Fusion._contact`: `shadow_seen` es global. Basta con ver una sombra una vez para desactivar para siempre el modo «presencia» en zonas sin luz (`fusion.py:253`).
- Los bindings de audio pisan el parámetro **en cada tick** (`runtime.py:305-311`) y chocan con `set_param` y con los bindings MIDI. No hay arbitraje ni suavizado, y el mapeo es lineal y sin tipos (un parámetro entero recibe float). Los destinos por id quedan colgando si la instancia muere.
- Los bindings MIDI se aplican fuera del lock del runtime.
- `Registry.owner` reasigna dueño si llega un move/up de un id desconocido o recortado (al pasar de 512 borra 256) (`registry.py:45-53`).
- Caché `_term` potencialmente grande (sección 3).
- `voz.connected()` depende de atributos privados del SDK (`_conversation_id`, `_ws`) (`voz.py:183-184`).
- `--quiet` del puente MIDI se ignora en parte (`midi_bridge.py:280`).
- La página `/app` (WebRTC) no registra client tools, así que en modo local las herramientas no funcionan desde el navegador (inferido de `index.html:61-70`).
- Aviso de deprecación de `websockets.legacy` con elevenlabs 2.79.0.
- Contratos especulativos en las herramientas (`mode`, `audio reactive`) con caminos de respaldo para runtimes viejos (`tools.py:11-19,504-567`): código muerto creciente.

**Seguridad**
- `RuntimeClient.ensure` **levanta el runtime solo** ante cualquier llamada a herramienta (`tools.py:82-89`).
- `pg_runtime reiniciar` y `procs.stop` hacen `killpg` según un pidfile, con riesgo de reutilización de PID (`procs.py:36-74`).
- `decision` ejecuta `orq-creativo answer … --send` con texto del LLM (lista de argumentos, sin shell). **Contradice** el prompt, que dice que «nada sale hacia afuera».
- Modo túnel: el puente queda público en trycloudflare con token compartido en un archivo 0600 (también guardado como secreto del workspace en ElevenLabs); el token se compara en tiempo constante (`server.py:36-40`). Expone acciones con efectos laterales (procesos, archivos en `requests/`). La URL se enmascara en los logs.
- UDP 8796/8797 y el WS 8793 del PC solo filtran por IP privada o tailnet, sin autenticación: cualquiera en la LAN puede inyectar MIDI (incluso controles con bindings) o notas.
- La API del runtime en :8790 no tiene autenticación (fuera de alcance; todo depende de ella).
- ssh con `BatchMode` y usuario e IP en el código.
- Positivo: la llave de ElevenLabs nunca se registra en logs (`config.py:126-150`), y el dry-run no lee secretos. `docs/VOZ-Y-CONFIGURACION.md` confirma que esta copia no incluye credenciales.

## 10. Relevancia para «agente con contexto físico»

**Esencial y reutilizable**
- **Detección en la vista del proyector**: remapear la cámara al espacio del proyector y predecir la cámara a partir de lo proyectado. Es lo que permite detectar manos sobre interfaces que cambian.
- **Contacto por la sombra** (gap): permite toque con una sola cámara cenital.
- Pero ninguno de los dos está validado con toques reales.
- El **modelo de eventos de toque** con id, seq, espacio, fuente, trigger, velocidad y altura, más `TouchState` consumido por seq y `resolve(target)`, que permite «tocar por nombre» desde un agente (ideal para pruebas).
- El **enrutamiento por prioridad e hit-test** (`Registry`) es casi un sistema de widgets proyectados. La interfaz `Instrument` es buena para un «widget proyectado» (hit, handle, step, render, status, resolve).
- Fusión con degradación explícita y `status()` legible.
- `audio/analysis.py` completo (barato y limpio).

**Antecedente directo del agente (secretaria)**
- Catálogo de herramientas con JSON Schema, `coerce`, errores devueltos como datos, respuestas cortas para voz y resolución difusa de nombres (`resolve_effect` / `resolve_instance`).
- Patrón de **contextual update + aviso** para empujar eventos físicos a la conversación, con arbitraje de turno (`can_speak`).
- Semidúplex y VAD adaptativo. Dormir para no pagar minutos.
- Carencia clave: **el agente no percibe toques, manos ni cámara**. `pg_que_proyectas` solo describe el lienzo (porcentaje encendido y color dominante). No tiene herramienta de toque ni de mirada, y `pg_touch`/`pg_gestures` existen solo en el MCP.

**Reutilizable con cambios**: `MidiOut`/`Notes` si se quiere sonido, bindings, `DeviceRegistry`, `capture.py`, el juego de memoria (como demo de interfaz proyectada con HUD).

**Prescindible**: pianohero, cocodrilo, marimba, linterna (salvo como demo), BLE, `midi_play`/ssh, SmartDeck/OHM, orq-creativo, túnel, página web.

## 11. Recomendaciones para la reescritura

**Bus de entrada unificado**
- Eventos tipados (`TouchDown/Move/Up`, `HandPose`, `Gesture`, `Dwell`, `ControlChange`, `NoteOn/Off`, `AudioFeatures`, `Utterance`, `Gaze`) con:
  - hora de **captura** monotónica y sincronizada (el servicio de manos ya trae ping/pong);
  - fuente y espacio (px del proyector, mm, objeto o widget impactado).
- Transporte en un solo host con pub/sub (ZeroMQ o NATS, o asyncio dentro del proceso) más un ring buffer con seq. Nada de HTTP por evento.
- Conservar la regla «se botan move, nunca down/up».

**Datos y procesos**
- **Memoria compartida** para el cuadro proyectado, con su hora real de presentación, y el cuadro de cámara. Elimina el PNG a 8 Hz y mejora el modelo de latencia.
- Percepción en su propio proceso y render en GPU. Los instrumentos/widgets solo reciben eventos ya ruteados. MIDI y sonido fuera del hilo de render, con colas no bloqueantes.

**Interacción**
- Capa de **widgets/regiones** registrables por el agente (hit-test por prioridad / z-order), con callbacks de evento.
- Bindings como grafo declarativo con curva, suavizado, prioridad y persistencia.

**Agente**
- LLM con tool use directo (Claude, con schemas pydantic) y voz como capa intercambiable: ElevenLabs o STT/TTS en streaming.
- Herramientas tipadas: `get_scene()`, `get_camera_view()` (imagen para visión), `project(widget_spec)`, `highlight(object)`, `wait_for_touch(region, timeout)`, `events_since(seq)`, `set_param`.
- Los eventos físicos relevantes se empujan como *contextual updates* resumidos, no a 30 Hz.
- Quitar el autostart de procesos y la ejecución de CLIs externos desde las herramientas; autenticación en la API local.

**Prioridad de validación**: medir de punta a punta la latencia y la tasa de acierto del toque con mano real (gap, `thr`, auto-exposición de la Osmo) antes de construir encima.

**Descartar** IPs y usuarios hardcodeados (pasar a un registro de dispositivos en la config), el doble sistema de bindings y los respaldos para «runtime viejo» en las herramientas.

---

## Archivos leídos y tests

**Leídos completos**: todos los del alcance.
- `playground/touchsynth/` (19): `__init__`, `__main__`, `README.md`, `alive`, `croc`, `effectkit`, `fusion`, `instrument`, `interactivo`, `linterna`, `live`, `marimba`, `music`, `objetos`, `osmo_hands`, `registry`, `scan`, `sketch`, `sound`.
- `playground/audio/` (8): `__init__`, `__main__`, `README.md`, `analysis`, `capture`, `features`, `mapping.json`, `mapping.py`.
- `playground/devices/` (4): `__init__`, `keyboard`, `midi`, `ohm`.
- `playground/pianohero/` (6): `__init__`, `__main__`, `README.md`, `game`, `layout`, `songs`.
- `playground/lights/` (2): `__init__`, `ble`.
- `playground/secretaria/` (14): `__init__`, `__main__`, `agent`, `audio`, `avances`, `config`, `procs`, `prompt.md`, `README.md`, `server`, `tools`, `tunel`, `voz`, `app/index.html`.
- `windows/midi_bridge.py`, `midi_list.py`, `midi_ping.py`, `midi_play.py`; `docs/VOZ-Y-CONFIGURACION.md`.

**Leídos parcialmente, fuera de alcance**: `playground/runtime.py` (touch, hands, audio, bind, tick, SCHEMA), `hands/bridge.py` (encabezado y envío), `hands/client.py` (grep), `windows/hands_service.py` (protocolo), `effects/base.py`, `effects/croc_synth.py`, `mcp_server.py` (`pg_touch`, `pg_gestures`, `pg_bind`). No quedó ningún archivo del alcance sin leer.

**Tests** (`.venv`, `-q -x`):

| Archivo | Resultado |
|---|---|
| test_touchsynth | 40 pasan |
| test_audio | 29 pasan |
| test_devices | 4 pasan |
| test_pianohero | 26 pasan |
| test_secretaria | 24 pasan |
| test_secretaria_voz | 17 pasan (2 avisos de deprecación de websockets.legacy) |
| test_interactividad | 14 pasan, 1 se salta («sin captures/scan5») |
| test_croc_alive | 14 pasan |

Todo corre con fakes o escena sintética, sin hardware.

**Mediciones propias** (script en `/tmp/lector_bench/bench.py`, datos sintéticos, fuera del repo):
- Osmo híbrido: p50 19,1 ms, p90 22,8 ms.
- `Fusion.step`: ~1 µs.
- Linterna: 8,4 ms por luz.
- `MultiAnalyzer` con 2 canales: 0,59 ms por cuadro (p99 0,85).
- PNG 1920×1080: 42,7 ms por codificación.

# Piano Hero proyectado (Korg EK-50)

Las notas bajan por el carril de cada tecla real hacia la línea de las teclas, y se tocan en el Korg EK-50. El carril
es el polígono exacto de la tecla (keys.json) prolongado hacia atrás, hacia el Buda, pasando por encima del
cocodrilo. Cuando aciertas, la tecla entera se ilumina con el color de la precisión: verde si es perfecto, lima si
está bien, ámbar si es ok. Lo que tocaste sube y se desvanece. Si fallas, se pone rojo. Puntaje, combo, precisión y
progreso van sobre la hoja blanca (si no está, sobre la torre). La línea de las teclas se llena con el avance de la
canción. Al final aparece un resumen con estrellas.

## Correr

**1. Puente MIDI en el PC** (el EK-50 está por USB en el PC; el Mac no lo ve):

```
# desde el Mac (el proceso vive mientras dure la sesión ssh)
ssh "Roberto Mardones@192.168.1.233" 'cd C:\sl-scanner; .venv\Scripts\python.exe windows\midi_bridge.py'
# sin teclado: notas sintéticas (escala de Do)
ssh "Roberto Mardones@192.168.1.233" 'cd C:\sl-scanner; .venv\Scripts\python.exe windows\midi_bridge.py --fake'
```

Para copiar el puente al PC sin pisar nada más: `Remote(...).sync(Path('.'), dirs=('windows/midi_bridge.py',))`
(ver `slscan/remote.py`), o `slscan remote sync` si toca sincronizar todo.

**2. Juego en el Mac** (proyecta en el display de 1920x1080, nunca en el interno ni en el Sidecar):

```
.venv/bin/python -m playground.pianohero --level 1 --song oda              # espera una tecla para empezar
.venv/bin/python -m playground.pianohero --level 2 --song elisa --practice
.venv/bin/python -m playground.pianohero --level 3 --song ~/Music/algo.mid
.venv/bin/python -m playground.pianohero --list                            # canciones y niveles
.venv/bin/python -m playground.pianohero --level 2 --song oda --demo       # se toca solo (sin teclado)
.venv/bin/python -m playground.pianohero --demo --no-window --seconds 20 --snapshot reports/ph.png
```

- `--midi auto` (por defecto): si el Korg está enchufado al Mac, usa rtmidi directo. Si no, escucha el puente en UDP
  8796 y cada segundo le manda un `hello` al PC (192.168.1.233:8796; para otra IP, `--bridge host:puerto` o
  `$MIDI_BRIDGE`). También hay `direct`, `bridge` y `fake`.
- Teclas: `--keys` es opcional. Busca `captures/scan5/keys.json` y, si no está, `scan2`. Si el keys.json tiene
  confianza bajo 0,5, ajusta el modelo del teclado al vuelo (unos 2 s). Para dejarlas fijas:
  `python -m playground.keys captures/scan5`.
- La API queda en `:8790` si el puerto está libre. La secretaria lo maneja con `pg_pianohero` (MCP) y lo ve con
  `pg_state` y `pg_snapshot`.
- En la ventana del proyector: `espacio` pausa o sigue, `r` reinicia, `1`/`2`/`3` cambian el nivel, `p` activa la
  práctica, `s` pasa a la siguiente canción, `+`/`-` mueven el offset de a 10 ms y `q` o `esc` salen.
- Para empezar basta con tocar cualquier tecla. Esa primera nota no cuenta.

Desde el MCP: `pg_pianohero start level 2 song elisa`, `pg_pianohero pause`, `pg_pianohero state`,
`pg_pianohero practice practice true`, `pg_pianohero offset offset_ms 40` y `pg_pianohero stop`.

## Niveles

| | Fácil (1) | Medio (2) | Difícil (3) |
|---|---|---|---|
| Manos | solo la melodía (derecha) | melodía y bajo simple a la izquierda | las dos, completas |
| Notas a la vez | 1 | 1 por mano; la izquierda separada ≥ 0,45 s | acordes de hasta 3 por mano |
| Teclas | solo blancas (E D# E se toca E D E), rango ≤ 16 semitonos | blancas y negras, rango ≤ 2 octavas | todo |
| Tempo | 0,6× | 0,85× | 1× |
| Ventana perfecto / bien / ok | ±75 / 150 / 240 ms | ±55 / 110 / 170 ms | ±35 / 70 / 110 ms |
| Tiempo en el carril | 4 s | 3 s | 2,2 s |
| Tecla equivocada | no corta el combo | corta el combo | corta el combo |

- **Puntaje**: perfecto vale 300, bien 200 y ok 100, multiplicado por ×2 desde el combo 10, ×3 desde el 20 y ×4
  desde el 30.
- **Precisión**: se pondera perfecto 1, bien 0,7, ok 0,4 y fallo 0.
- **Estrellas**: 3 con 95 %, 2 con 80 % y 1 con 60 %.
- **Práctica**: el reloj se detiene en cada nota hasta que la tocas, y la tecla que esperas parpadea. No hay fallos
  por tiempo; la precisión es aciertos ÷ toques.

## Canciones

Hay cuatro incluidas, generadas por código y sin descargas. Están escritas con su arreglo completo y cada nivel las
simplifica:

- `oda`: Oda a la Alegría, con acordes en la izquierda.
- `estrellita`: Estrellita.
- `elisa`: Para Elisa, el tema A dos veces, con los arpegios de la izquierda.
- `escala`: escala de Do, para calibrar.

Con un `.mid` cualquiera:

- Se lee con mido, con el mapa de tempos completo y sin la batería.
- La mano sale de la pista (con 2 pistas, la más aguda es la derecha) o de la altura (de Do4 hacia arriba es la
  derecha).
- Se transpone por octavas a C2..C7 y no se mueve si ya cabe.
- Cada nivel lo simplifica con la misma función que a las incluidas: manos, polifonía, separación mínima, solo
  blancas, rango y sin choques en una misma tecla.

## Latencia

El juicio usa la hora de llegada de cada nota, no la del cuadro, así que los fps no influyen. `offset_ms` compensa
el retraso de proyector más puente: si el proyector muestra tarde, uno toca tarde. El resumen final trae el promedio
de adelanto o atraso y un `offset` sugerido. Para medirlo, toca la canción `escala` en nivel 2 y usa el valor que
sugiere con `--offset-ms`.

El puente PC → Mac por Wi-Fi da una ida y vuelta de 2 a 5 ms, con picos de 100 a 200 ms en el primer segundo (ahorro
de energía del Wi-Fi). En la prueba del 16-oct no se perdió ningún paquete.

## Diagnóstico del EK-50 (no llegan notas al PC)

Estado al 16-oct, 03:00: el PC ve el teclado (`USB\VID_0944&PID_013F`, "Generic USB MIDI", entrada
`Generic USB MIDI 0` y salida `Generic USB MIDI 1`), el puerto se abre y el puente se conecta al Mac, pero no llega
ningún mensaje. El PC tiene **Windows 11 build 26300 (Insider) con Windows MIDI Services**: el servicio `midisrv`
está corriendo y los puertos WinMM que usa rtmidi pasan por él (transporte KS, "MIDIU_KSA_…").

1. **En el PC**: correr `windows\midi_bridge.py --diag --seconds 20` mientras alguien toca teclas, mueve la rueda de
   pitch y las perillas. El diagnóstico muestra el sistema, el dispositivo USB, el servicio MIDI, los puertos, si el
   puerto abre y todo lo que llega, incluidos reloj y active sensing. Además manda Do-Mi-Sol al teclado.
2. **¿Sonaron las tres notas de prueba en el EK-50?**
   - Si sonaron, el USB y el driver funcionan hacia el teclado. El problema está en que el teclado no transmite, o en
     la entrada del servicio MIDI. Seguir con el paso 3.
   - Si no sonaron, revisar cable y puerto (otro puerto USB del PC, sin hub, y otro cable USB-B) y desenchufar y
     enchufar con el teclado encendido.
3. **En el teclado** (manual del EK-50, secciones MIDI y Global): revisar los ajustes MIDI. Esto incluye el preset o
   modo MIDI, el canal de transmisión de la parte que suena con el teclado (Upper o Keyboard), que la transmisión no
   esté apagada y Local Control. Probar también apagar y prender el teclado con el USB ya conectado.
4. **En Windows** (MIDI Services, el stack nuevo):
   - Si la entrada llega al servicio pero no a WinMM, es un problema del stack. Para comprobarlo se puede probar con
     la consola de Windows MIDI Services (`midi endpoint list`, `midi endpoint monitor`). Hoy no está instalada:
     instalarla necesita el OK de Max.
   - Reiniciar el servicio: `Restart-Service midisrv`, en PowerShell como administrador (también necesita el OK de
     Max).
   - Otra prueba sin código: ver si una app WinMM conocida (MIDI-OX, Ableton) recibe notas. Si tampoco recibe, el
     problema no es python ni rtmidi.
5. **Atajo que no depende del PC**: enchufar el EK-50 al Mac (cable USB-B a USB-C).
   - En el Mac, `.venv/bin/python -c "import mido; print(mido.get_input_names())"` tiene que mostrarlo.
   - Después, `python -m playground.pianohero --midi direct`, o `auto`, que lo encuentra solo.

## Puente persistente en el PC (tarea programada: no está creada, necesita el OK de Max)

Los procesos lanzados por ssh mueren al cerrar la sesión. Para que el puente arranque solo al iniciar la sesión de
Roberto, hay que correr esto en el PC, en PowerShell como Administrador (mismo patrón que `windows/autologin.ps1`):

```powershell
$py = "C:\sl-scanner\.venv\Scripts\python.exe"; $user = $env:USERNAME; $domain = $env:COMPUTERNAME
$action = New-ScheduledTaskAction -Execute $py -Argument "windows\midi_bridge.py --quiet" -WorkingDirectory "C:\sl-scanner"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$domain\$user"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "sl-scanner-midi-bridge" -Action $action -Trigger $trigger -Settings $settings -User "$domain\$user" -RunLevel Limited -Force | Out-Null
Start-ScheduledTask -TaskName "sl-scanner-midi-bridge"
```

El firewall de Windows ya deja pasar los `hello` del Mac al UDP 8796: se probó el 16-oct. Si algún día los bloquea,
hay dos salidas. La primera es abrir el UDP 8796 solo para LAN y tailnet:
`New-NetFirewallRule -Name sl-scanner-midi -Direction Inbound -Protocol UDP -LocalPort 8796 -RemoteAddress @("LocalSubnet","100.64.0.0/10") -Action Allow`.
La segunda es que el puente empuje al Mac con `--to 192.168.1.38`, lo que solo usa tráfico saliente del PC.

## Archivos

- `songs.py`: notas, melodías incluidas, `.mid`, transposición y niveles (`simplify`).
- `game.py`: reloj, juicio, puntaje, práctica y resumen. No dibuja ni toca hardware.
- `layout.py`: carriles desde keys.json, línea de golpe, HUD y teclado sintético de respaldo.
- `__main__.py`: CLI. Runtime propio, efecto, teclado, ventana en el proyector y API.
- `playground/effects/pianohero.py`: el dibujo, con hot reload. El juego sobrevive a la recarga.
- `playground/devices/keyboard.py`: el EK-50 como dispositivo, la fuente del puente UDP y la fuente auto
  (local o puente).
- `windows/midi_bridge.py`: puente en el PC, con `--fake`, `--diag` y `--list`.
- `playground/keys/fit.py`: ajuste por modelo (ECC) para teclados con perspectiva fuerte, como en scan5.
- `playground/keys/refine.py`: afinado tecla por tecla con capturas (Osmo y Brio, overlay del runtime) que corrige
  keys.json; solo escribe si el resultado es concluyente.
- `playground/keys/quad.py`: el cuadrilátero blanco de las teclas por sus 4 aristas, las 61 teclas por homografía
  hacia él, la verificación de bleed por overlay y `--offset`, que mide el corrimiento global del proyector.
- Tests: `tests/test_pianohero.py` y `tests/test_keys.py` (el ajuste por modelo).

## Qué falta

- **Notas reales del EK-50**: no llega ninguna al PC. Ver el diagnóstico de arriba. El puente funciona: se probó con
  `--fake` PC → Mac y con el puerto real abierto.
- **Teclas de scan5**: salen del ajuste por modelo (correlación 0,83). Se verificaron con la Osmo el 16-oct: la luz
  cubre justo el área de teclas de C2 a C7 y cada negra queda con su contorno. La cámara ve ~10 px por tecla, así
  que no se puede medir el error subpíxel. Si el teclado o el proyector se mueven, hay que reescanear y correr
  `python -m playground.keys captures/scanN`.
- **Corrimiento global del proyector**: el 16-oct la luz caía (+0,8, −19,9) px respecto del mapa de scan5 (medido con
  `python -m playground.keys.quad captures/scan5 --offset`). Mientras no se corrija en la salida del runtime o se
  reescanee, todo lo proyectado (carriles y teclas incluidos) cae ~20 px más arriba de lo que corresponde.
- **Medir la latencia real** del proyector más el puente para fijar `offset_ms`. La canción `escala` sirve para eso.
- **Notas largas**: hoy se juzga solo el ataque; mantener la tecla apretada no suma.
- **Sonido**: el juego no toca nada. El EK-50 suena solo; si se apaga Local Control, se puede mandar el MIDI a
  Ableton.
- **Tarea programada del puente en el PC**: espera el OK de Max (ver arriba).

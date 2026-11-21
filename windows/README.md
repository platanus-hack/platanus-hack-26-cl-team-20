# PC Windows: motor del escáner, controlado por SSH desde el Mac

Arquitectura decidida (15-oct): **el PC Windows con GPU es el servidor**; el Mac es el control. Dos canales:

1. **SSH por Tailscale** para administrar: sincronizar código, correr comandos, ver logs. Lo deja listo `setup.ps1`.
2. **Backend `slserver`** (FastAPI + WebSocket, fase F5+) que corre en el PC y expone cámaras, calibración, tracking y
   render en vivo; el Mac y el playground web hablan con él. Esto se levanta por SSH cuando llegue su fase.

Todo lo pesado (capturas 4K, modelos, Open3D, torch) vive en el PC. En el Mac no se guarda nada grande.

## Lo que hace Max en el PC, una vez

1. Instalar el driver NVIDIA oficial (y DaVinci/Osmo si la Osmo va por USB como webcam UVC).
2. Copiar esta carpeta `windows\` al PC (USB o AirDrop a un correo propio; sin publicar nada).
3. Abrir **PowerShell como Administrador** y correr:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
cd <carpeta windows>
.\setup.ps1 -PublicKey "<la llave pública del Mac, una línea>"
```

   La llave está en el Mac: `cat ~/.ssh/id_ed25519.pub`. Es pública, se puede copiar sin cuidado.

4. Entrar a la tailnet de SmartUp: `& "C:\Program Files\Tailscale\tailscale.exe" up` y aceptar en el navegador con la
   cuenta de Max. El script imprime la IP y el nombre.
5. Avisarle a la unidad h3 el nombre del PC en la tailnet.

## Desde el Mac, después

```
cd ~/hardware/sl-scanner
.venv/bin/python -m slscan remote doctor --host <nombre-tailscale>     # GPU, cámaras, displays, puertos DMX
.venv/bin/python -m slscan remote sync   --host <nombre-tailscale>     # copia slscan/, tests/, windows/ a C:\sl-scanner
.venv/bin/python -m slscan remote run    --host <nombre-tailscale> "python -m pytest tests -q"
```

`remote sync` empaqueta el código con `tar` por SSH (Windows trae `tar.exe`); no usa git ni ningún servicio externo.

## Qué queda en el PC

```
C:\sl-scanner\
  .venv\          Python 3.12 (uv) con torch CUDA, OpenCV, Open3D…
  slscan\ tests\ windows\     código sincronizado desde el Mac
  captures\       capturas crudas 4K (PNG) por sesión
  reports\        salidas, nubes, mallas, visores
```

## Sonido de la mesa táctil (`midi_play.py`)

Las notas del cocodrilo, la marimba y el dibujo (playground/touchsynth) suenan en el PC con el "Microsoft GS
Wavetable Synth", por el dispositivo de audio predeterminado de Windows. Para que salga por la mesa: Configuración >
Sistema > Sonido > Salida > **"Altavoces (USB Audio CODEC)"** (la QX1222USB). El runtime del Mac lanza el receptor
solo, por ssh (`midi_play.py --stdin`), y se cierra con él; no hay que dejar nada corriendo. Probar a mano:
`python windows\midi_play.py --list` (puertos) y `--test` (3 notas). Si Max prefiere el receptor siempre prendido
por UDP 8797: regla de firewall y tarea programada (en el docstring de `midi_play.py`), con su OK.

## Hardware que el PC va a manejar

- 2 proyectores (cada uno en un display propio), cada uno con una Osmo pegada al lente.
- Sony FX3 (vía capturadora HDMI o USB streaming) viendo toda la escena; webcam cenital.
- Foco DMX (interfaz USB-DMX, FTDI) y la barra RGB manual (se trackea por cámara, no se controla).
- Los cabezales móviles de los proyectores: si tienen DMX, por la misma interfaz; si son manuales, se calibra su pose
  por escaneo en cada orientación.

Nada de esto se instala ni configura en remoto hasta que Max confirme que `setup.ps1` corrió bien.

## Puente MIDI del Korg EK-50 (Piano Hero)

`windows/midi_bridge.py` lee el teclado (entrada `Generic USB MIDI 0`) y manda cada mensaje al Mac por UDP 8796
(JSON, un datagrama por mensaje). `--fake` genera notas sin teclado, `--diag` hace un diagnóstico paso a paso y
`--list` muestra los puertos. Del lado del Mac lo recibe `playground/devices/keyboard.py`. Cómo correrlo, el
diagnóstico (hoy no llegan notas del EK-50 al PC) y la tarea programada para dejarlo persistente (no está creada:
necesita el OK de Max): `playground/pianohero/README.md`.

# Sistema solar con el motor (camino B): runbook para el agente de SAT

Lo corre el agente de SAT en su Mac, en orden. Es el camino principal: el motor (`pcx motor`, Rust, con MobileSAM) ve
la mesa, sigue los objetos y publica `solar:*` con la silueta real. La capa visual de h21 dibuja anclada a esos
objetos. El `seguir` de Python (CORRER-EN-MESA.md) queda solo de respaldo.

**Reglas mientras corre:**
- No tocar cables (cámara, proyector, hub) con el motor andando: no reabre la cámara. Si se desconectó algo, Ctrl-C al
  motor, reconectar y volver al paso 4.
- No hacer merge de `prueba/solar-vivo` en nada (trae 8e99344, que rompe la detección).
- Compilar con `-j 2`. Un solo cargo a la vez.
- La cámara se elige por nombre (`Osmo`), nunca por número: los `cam-N` cambian cada vez que se reconecta algo.

## 0. Antes que nada: frenar lo viejo y mandarnos una foto

1. Parar todo lo que quede del camino A: el `seguir` de Python y el bucle de capturas (Ctrl-C). Hay un cliente que
   llama a `look` con `cam-4` cada 5 s y falla: también se para (`ps aux | grep conductor`, y Ctrl-C en su terminal).
2. La foto que pidió Max, para que veamos qué ve hoy la cámara y qué sale por el proyector. Con las tools MCP de la
   consola:
   - `devices {kind: "camera"}` y anotar el `id` de la cámara cuyo `meta.label` dice **Osmo**;
   - `look {camera_id: <ese id>, annotate: false}` y `look {camera_id: <ese id>, annotate: true}`;
   - guardar las dos imágenes como `HHMMSS-osmo.jpg` y `HHMMSS-osmo-proyectado.jpg` en `escenas/sistema-solar/prueba-vivo/`
     y compartirlas por un canal directo. **Nunca con `git add`, commit ni push**: en las fotos pueden salir personas.
     `prueba-vivo/` está en el `.gitignore` de la escena.

## 1. Traer el código (worktree nuevo, sin tocar los otros)

Las escenas (`escenas/sistema-solar`, con su capa visual) están en `main`. Lo que `main` todavía no tiene es el motor
con las escenas registradas (PR #22) y la percepción nueva (PR #20). Hasta que entren, se mergean encima.

**Hoy (22-nov, `main` sin #20 ni #22):**

```sh
cd ~/code/platanus-hack-26
git fetch origin
git worktree add --detach wt-solar-motor origin/main
cd wt-solar-motor
git merge --no-edit origin/max/motor-escenas        # PR #22: el motor registra desk, lab y solar (trae pcx-escena-solar al día)
git merge --no-edit origin/max/percepcion-motor-max  # PR #20: pre-escaneo, N0/N1 en color, deriva compensada
git log --oneline -1
```

Los dos merges sobre `main` salen limpios (revisado con `git merge-tree` el 22-nov a las 03:00). Si alguno pide resolver
conflictos, parar y avisar.

**Si `main` ya tiene #20 y #22** (los dos PR aparecen como merged en GitHub), sin merges:

```sh
cd ~/code/platanus-hack-26
git fetch origin
git worktree add --detach wt-solar-motor origin/main
cd wt-solar-motor
```

## 2. Compilar

```sh
cd node
cargo build -j 2 --release -p pcxd --features onnx
ls -la target/release/pcx        # o $CARGO_TARGET_DIR/release/pcx si lo tienes definido
```

Los pesos de MobileSAM tienen que estar en `~/.pcx/models` (`mobile_sam_image_encoder.onnx` y
`sam_mask_decoder_single.onnx`).

## 3. Ver las cámaras por nombre

```sh
./target/release/pcx cameras
```

Tiene que aparecer la Osmo (OsmoPocket3). Si no aparece, revisar el cable **antes** de arrancar el motor.

## 4. Motor con la Osmo

```sh
./target/release/pcx motor --camera Osmo --lock-exposure
```

- `--camera` acepta una subcadena única del nombre. Si dice que hay varias, usar más letras del nombre que mostró
  `pcx cameras`.
- Imprime una URL `https://pcx-gateway.onrender.com/?motor=ws://127.0.0.1:9230&motor_token=…`. Abrirla en el Chrome de
  la consola (o agregar `motor` y `motor_token` a la URL de la sesión que ya está abierta).
- Mirar al arrancar:
  - `welcome.models.sam` en `true` («cascada + MobileSAM de …»);
  - `welcome.scenes` incluye `solar`;
  - `welcome.cameras` muestra la Osmo como `cenital`.

## 5. Calibrar de nuevo (la Osmo no está calibrada)

1. En la consola, dejar la Osmo como cámara principal (cenital).
2. Por MCP: `calibrate {request_id: "solar-HHMM", mode: "precise", camera_id: <id de la Osmo>}`.
3. Esperado: 130 puntos o más de 144 y mediana de 1 mm o menos (el QA de las 22:58 dio 142/144 con 0,3 mm).
   El canasto puede quedarse en la mesa: calibrate descarta los puntos que caen sobre objetos altos.
4. Después, en el visor, «Rectificar la cámara a la mesa» tiene que quedar activo, sin el aviso «no está calibrada».

## 6. Pestaña `solar`

Por MCP: `scenes {action: "create", name: "solar"}`. Si ya existe: `scenes {action: "activate", name: "solar"}`.

- El nombre tiene que ser exactamente **solar**, en minúscula. Con ese nombre, la consola le pide al motor el módulo
  `solar` (no sirven «Sistema solar» ni «Solar»).
- Al activarla, el motor hace un ciclo. En `scene` (por MCP) tienen que aparecer `solar:canasto`, `solar:sol`,
  `solar:<planeta>` (mercurio … neptuno) y `solar:lamina-0` si la lámina está a la vista.

## 7. Dibujar (capa visual de h21)

La capa visual ya está en `main`: se corre desde el mismo worktree del paso 1, sin otro worktree ni merges.

```sh
cd ~/code/platanus-hack-26/wt-solar-motor/escenas/sistema-solar
uv sync
export PCX_MCP_URL="$(claude mcp get pcx | sed -n 's/^ *URL: *//p' | head -1)"
export PCX_USER_TOKEN="$(claude mcp get pcx | sed -n 's/.*Bearer \([^" ]*\).*/\1/p' | head -1)"
echo "url=${PCX_MCP_URL:-FALTA} token=${#PCX_USER_TOKEN} caracteres"   # nunca imprimir el token
export PCX_SIN_OBJETOS=1 PCX_CAMARA=Osmo
uv run python visual/conductor_visual.py armar
```

- **No se corre `seguir`**: los objetos los mueve el motor y los widgets anclados los siguen solos.
- Para volver a dibujar: `uv run python visual/conductor_visual.py redibujar`. Para volver a armar: `armar`, nunca
  `limpiar && armar`.
- Si stderr dice «el motor no publica ningún solar:<planeta>», revisar que la pestaña se llame exactamente `solar`
  (paso 6) y que el motor tenga cámara.
- Si dice «en la foto de Python no veo bolas; sigo igual», está bien: en este camino manda lo que publica el motor.

## 8. Qué mirar en la mesa

- **Planetas:** cada uno cae sobre su bola, con la forma de la bola (la silueta SAM), no un círculo corrido. El Sol
  aparece con los dientes del engranaje.
- **Solo lo del canasto:** las franjas del borde (de unos 1000×30 mm) y los objetos sueltos no salen como `solar:*`.
  Si aparecen en `scene` como `m01`, `m02`, … son del Pipeline base, no de la escena: avisar (lo ve h19).
- **Mover el canasto unos 10 cm:** después de una foto limpia (unos 0,8 s de apagón), los planetas vuelven a caer sobre
  las bolas y llega el evento `canasto_movido`.
- **Sacar una bola del canasto:** su `solar:<planeta>` sigue un ciclo más y sale recién al segundo ciclo sin verla
  (no hay «se fue» y «volvió» por una mano o un reflejo).
- **Log del motor:** `stages_ms.scene` en cada `status`. Si aparece `scene_failed`, copiar la línea.

## 9. Capturas para nosotros

En `wt-solar-motor/escenas/sistema-solar`:

```sh
uv run python conductor.py --camara Osmo captura
```

Guarda en `prueba-vivo/` de la escena lo que ve la cámara, la vista en mm (si está calibrada) y lo proyectado. Una
captura después del paso 7, otra con el canasto movido y otra con la mano encima. Se comparten por un canal directo,
**nunca con commit ni push**: en las fotos pueden salir personas (`prueba-vivo/` está en el `.gitignore`).

## Si algo falla

| Síntoma | Qué hacer |
|---|---|
| `pcx motor` no encuentra la Osmo | Ctrl-C, revisar el cable y `pcx cameras`; nunca reconectar con el motor andando |
| `welcome.models.sam = false` | Faltan los pesos en `~/.pcx/models` o se compiló sin `--features onnx` |
| No aparece ningún `solar:*` | Pestaña mal nombrada (paso 6), o la escena no está en `welcome.scenes` (falta #22 en `main` o su merge del paso 1) |
| Los planetas caen corridos | Repetir `calibrate precise` (paso 5) y mandar captura (paso 9) |
| Falta una bola | Mandar captura: el umbral de blanco es relativo a la mesa, y con otra luz puede necesitar ajuste |

# Seguridad del nodo pcx

Amenazas: agente MCP o contenido físico malicioso, páginas web contra localhost (CSRF, DNS rebinding),
fallos de percepción, concurrencia, disco lento y caída abrupta del core.
Garantía central: una acción de domótica con confirmación solo corre si una persona vio ESA acción y la aprobó.

Resuelto:
- Sin token de fábrica: `mcp`, `console`, `debug` y `hands` son secretos distintos de 256 bits, generados al
  primer arranque en el directorio de datos (archivos 0600, directorio 0700, `O_NOFOLLOW`); overrides `PCX_*_TOKEN`.
  Comparación en tiempo constante. El token del agente no sirve en `/console/*` ni en `/debug/*`.
- `/debug/*` solo con `pcx serve --debug` y su token; sus toques son siempre `api` y nunca confirman.
- Una sola confirmación activa: se proyecta la cabeza de la cola (máx. 8, «+N en cola»); el botón lleva su
  `confirmation_id` y el toque solo aprueba esa, con su mismo digest.
- El toque que aprueba exige: worker de manos autenticado, `touch_down` nuevo dentro del botón, posterior a que el
  renderer avisó `presented` (más 500 ms), y dwell completo de 1,5 s (también el pinch). Un dedo ya apoyado,
  uno que entra deslizando o uno que sigue tras resolver la anterior no aprueban.
- El resumen lo arma el core: `dominio.servicio → destinos` (entity/area/device validados como identificadores;
  si no, se deniega). Los demás datos del agente van citados y truncados tras `datos:`; no pueden reemplazarlo.
- Origen físico: solo cuentan los sobres ZeroMQ `["pcx-auth", hands.token, sobre]` (así los envía `pcx worker
  hands`; `pcx up` le pasa la RUTA del archivo 0600 por `PCX_HANDS_TOKEN_FILE`, nunca argv) o `ingest_hands`.
  El campo `source` no concede nada. `widget_pressed` externo se rechaza siempre: solo lo genera el core.
- Widgets del agente ocultos durante una confirmación; ids `pcx:` reservados (proyección, update, flujos, clear).
- Imágenes: `asset:<nombre>`, `data:image/(png|jpeg|webp);base64` o ruta que canonicalizada queda dentro de
  `PCX_ASSETS_DIR`. Sin URLs, `..`, `~`, SVG, absolutas ajenas ni symlinks hacia fuera.
- HTTP: `Host` debe ser 127.0.0.1, localhost o [::1] con el puerto del core (421 si no; cubre rebinding en GET).
  `Origin` solo el propio o la lista exacta `PCX_ALLOWED_ORIGINS`, con CORS restringido; el resto 403.
- Cola en dos carriles: el crítico (down/up, objetos, comandos, `presented`, `blanked`, confirmaciones) espera cupo
  y nunca descarta; el descartable lleva `touch_move`, `object_moved`, `frame_meta`, manos y estadísticas.
- Actor y SQLite en hilo propio; deduplicación y snapshot atómicos con el ack; instancia única; pánico = salida 70.
- Gateway: `look` no se guarda salvo `PCX_KEEP_LOOK=1`; miniaturas caducan a las 24 h.
- P1 de pcx-shm, pcx-hands, pcx-objects y pcx-calib (ver historial de la rama).

Límite conocido — agente con shell del mismo usuario: puede leer `console.token` o `hands.token` (0600 no aísla
dentro de la misma cuenta) y confirmar por consola o inyectar toques. Mitigación actual: archivos 0600 en
directorio 0700 y roles separados. Pendiente: llavero del SO opcional y correr el agente en otra cuenta o sandbox.
**No conectar el ejecutor real de Home Assistant hasta cerrar esto** (hoy `home_exec` es un stub).

Pendiente además: token y tope de suscriptores en `/render` (cualquier proceso local puede enviar `presented`;
ticket para renderers web), caducidad de toques inactivos, OAuth, validación en Windows y con hardware.

## Pendientes tras la re-verificación de Codex (PR #9, sáb 21-nov 16:50)
Codex no encontró cómo aprobar domótica sin una mano real teniendo solo el token MCP. El PR se mergeó con estos P1 de defensa en profundidad abiertos (detalle en el comentario del PR #9):
1. **Replay de manos autenticadas:** el multipart `pcx-auth` lleva un secreto estático, sin sesión, contador ni caducidad (`bus.rs:58`, `world.rs:1143`). Falta firmar con contador y caducidad y rechazar duplicados.
2. **El worker entrega su token a un core impostor:** un PULL que ocupe antes el endpoint lo recibe (`worker.rs:83,95`). Falta un endpoint privado por usuario y autenticar al servidor, o un canal heredado del supervisor.
3. **Mismo UID:** `console.token` y `hands.token` son legibles por cualquier proceso del mismo usuario (`security.rs:109`, `node.rs:129`). Falta correr el agente en otra cuenta o en un sandbox.
4. **`/render` sin credencial:** un `presented` con una revisión futura habilita el botón (`server.rs:118`, `world.rs:1285`). No fabrica una mano por sí solo.
5. **El dwell se puede completar sin mano:** si se pierde el `TouchUp` o la cámara se detiene, el dwell termina igual (`worker.rs:234`, `world.rs:1195`). Falta una entrega fiable de Down/Up y que el contacto caduque si no se renueva.
6. **El resumen de la confirmación puede quedar recortado:** caben 8 destinos de 96 caracteres en un panel de 230×80 mm (`world.rs:98,845`). Falta rechazar o paginar lo que no cabe.
- P2: DoS de la cola de confirmaciones y de las expiraciones al inundar `/render`.

"""Texto de `/agente`: los pasos que sigue un agente de código para dejar WUI andando en el computador de la persona."""

MOTOR_PENDIENTE = """## 1. Motor local — PENDIENTE, sáltate este paso

El motor local (percepción en Rust con los modelos incluidos, escucha en `127.0.0.1:9230`) todavía no tiene un
instalador publicado: el `install.sh` existe en el repositorio pero aún no hay una URL pública ni un release del que
bajar el paquete. No inventes un comando ni compiles desde el código fuente.

WUI funciona sin el motor: la cámara, la calibración y las manos corren en el navegador de la persona. Sigue con el
paso 2 y dile a la persona, en una línea, que el motor local queda pendiente."""

MOTOR = """## 1. Instala y levanta el motor local

Es la percepción (objetos y manos) corriendo en este computador, con los modelos incluidos. macOS y Linux:

```sh
curl -fsSL "{install}" | sh -s -- --yes --skills --start
```

Verifica (todas las líneas deben decir `[ok]`; un `[aviso]` de cámara es aceptable):

```sh
curl -fsSL "{install}" | sh -s -- --check
curl -fsS http://127.0.0.1:9230/v1/health
```

Si tu herramienta de shell mata los procesos en segundo plano al terminar, corre el comando como tarea de fondo. Si el
instalador falla (Mac Intel sin paquete, release no disponible, checksum que no coincide), no compiles ni desactives
verificaciones: avísale a la persona y sigue con el paso 2, porque WUI funciona sin el motor (todo corre en el
navegador). El instalador imprime una URL de emparejamiento con `motor=` y `motor_token=`: guarda esos dos parámetros
para el paso 3."""

MESA_CON_MOTOR = """
Si instalaste el motor en el paso 1, agrega sus dos parámetros antes del `#`:
`{base}/mesa?motor=ws://127.0.0.1:9230&motor_token=<token del motor>#s=...`
"""

TEXTO = """# WUI (World User Interface): instrucciones para agentes

Eres un agente de código (Claude Code, Codex u otro). La persona quiere usar WUI: una mesa con una cámara y un
proyector que tú ves y sobre la que proyectas por MCP. Sigue los pasos en orden y verifica cada uno antes de seguir.

- No le pidas a la persona tokens, claves ni contraseñas: nada de esto los necesita.
- La sesión del paso 2 trae dos llaves. Son secretas: no las escribas en archivos del repositorio ni las pegues en
  otros sitios.
- Háblale a la persona en español, con mensajes cortos.

{motor}

## 2. Crea una mesa nueva

```sh
curl -fsS -X POST {base}/api/sessions
```

Responde un JSON con `session_id`, `node_id`, `node_token` y `user_token`. Guárdalos en variables de tu shell.
Si responde 403, este servidor no permite sesiones abiertas; si responde 429, espera un rato. En ambos casos avísale a
la persona y detente.

## 3. Ábrele la mesa a la persona

Arma esta URL con los valores del paso 2 (lo que va después de `#` no viaja al servidor):

```
{base}/mesa#s=<session_id>.<node_token>.<user_token>
```
{mesa_motor}
Ábrela en Chrome o Edge en este mismo computador (macOS: `open -a "Google Chrome" "<url>"`; Linux:
`google-chrome "<url>"`; Windows: `start chrome "<url>"`). Si no puedes abrir un navegador, pásale la URL a la persona
y pídele que la abra ella. Safari y Firefox no sirven para elegir la pantalla del proyector.

## 4. Pídele tres cosas a la persona

Díselas una por una y espera a que confirme:

1. **Cámaras:** en el paso «Cámaras» de la mesa, apretar «Activar cámaras» y aceptar el permiso. Si hay más de una,
   apretar «Usar como cenital» en la que mira la mesa desde arriba.
2. **Pantalla del proyector:** en el paso «Proyector», elegir la pantalla del proyector y apretar «Lanzar proyección».
   El navegador pide permiso para manejar ventanas: aceptarlo. Debe aparecer un patrón de prueba sobre la mesa.
3. **Micrófono:** apretar «Activar micrófono» y aceptar el permiso (sirve para el botón de voz de la mesa).

Opcional: con «Sumar un celular» la mesa muestra un QR; el teléfono que lo abre queda como una cámara más
(`cel-1`), útil para mostrar otro ángulo (`look` con `camera_id: "cel-1"`).

## 5. Conecta el MCP con la llave de esa mesa

La llave es el `user_token` del paso 2. El servidor se llama `pcx` (nombre interno, no lo cambies).

Claude Code:

```sh
claude mcp remove pcx 2>/dev/null; claude mcp add --transport http pcx {base}/mcp --header "Authorization: Bearer <user_token>"
```

Codex:

```sh
export PCX_USER_TOKEN=<user_token>; codex mcp remove pcx 2>/dev/null; codex mcp add pcx --url {base}/mcp --bearer-token-env-var PCX_USER_TOKEN
```

Otro cliente: MCP por Streamable HTTP en `{base}/mcp` con el header `Authorization: Bearer <user_token>`.

Si las herramientas `pcx` no aparecen en tu sesión actual, tu cliente necesita reiniciarse para cargar un MCP nuevo:
pídele a la persona que te reinicie y que te pegue de nuevo este enlace. La mesa queda abierta y la sesión sigue
sirviendo.

## 6. Comprueba y saluda

```sh
curl -fsS {base}/api/nodes -H "Authorization: Bearer <user_token>"
```

El nodo `web-<session_id>` debe aparecer con `"connected": true`. Si no, la mesa no está abierta en el navegador.

Después, por MCP:

1. `scene`: la superficie proyectable en mm y lo que hay en el mundo.
2. `look`: una foto de la mesa. Si dice `calibrated=false`, llama a `calibrate` (unos 10 s, proyecta marcas).
3. `project`: un saludo corto al centro, y otro `look` para confirmar que se ve.

Cuéntale a la persona qué ves y pregúntale qué quiere hacer. Para ver la mesa en vivo desde otro dispositivo, la
mesa tiene «Copiar enlace para ver».
"""


def texto(base: str, install_url: str | None) -> str:
    return TEXTO.format(
        base=base,
        motor=MOTOR.format(install=install_url) if install_url else MOTOR_PENDIENTE,
        mesa_motor=MESA_CON_MOTOR.format(base=base) if install_url else "",
    )

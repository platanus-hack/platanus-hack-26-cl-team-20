# Despliegue de pcx · verificado el 21-11-2026

## Render: una operación de alta

Prerrequisito del integrador: publicar este commit en `codex/deploy` en GitHub; esta entrega **no hace push**. Si se integra en otra rama, cambiar `branch` en `render.yaml` antes de crear el Blueprint.
1. Render Dashboard → **New → Blueprint** → conectar el repositorio → rama `codex/deploy` → Blueprint Path `render.yaml` → revisar los tres recursos en Virginia.
2. En ese mismo formulario cargar `PCX_USER_TOKEN`, `PCX_NODE_TOKEN` y `PCX_CONSOLE_TOKEN`: tres valores distintos, cada uno generado con `openssl rand -hex 32`; guardarlos en el gestor de secretos. Crear/aplicar el Blueprint y esperar a que `pcx-gateway` esté **Live**.
3. Copiar la URL pública del servicio (no asumir que el nombre estará disponible). `DATABASE_URL` y `REDIS_URL` se enlazan automáticamente por red privada; no cargarlos a mano. No hay OAuth: los agentes reciben únicamente el token de usuario; el de consola es exclusivo de personas.

Docker fija Python 3.12 y `uv.lock`, comparte entorno con Compose y empaqueta `contracts/` sin instalación editable; usuario UID 10001, sin secretos ni uv en runtime. El contexto es la raíz, filtrada por `.dockerignore`.
`PORT` lo entrega Render; `/health` verifica DB. Una instancia/un worker: Redis no distribuye sockets ni llamadas pendientes. `autoDeployTrigger: checksPass` espera CI; `.github/workflows/gateway.yml` ejecuta pytest en `codex/deploy`. Sin checks, Render no autodespliega; si se cambia de rama, actualizar también el workflow.
Cada deploy corta WSS: cierre gracioso ≤20 s, límite Render 30 s, nodo reconecta con backoff; las acciones en vuelo pueden fallar. Evitar deploys durante la demo. Véase [Blueprint spec](https://render.com/docs/blueprint-spec).

## Verificar y conectar

Desde la raíz del repo, con `uv` instalado y los mismos tokens exportados desde el gestor de secretos:
```sh
export PCX_GATEWAY_URL=https://HOST.onrender.com
curl --fail --show-error --silent "$PCX_GATEWAY_URL/health" # {"ok":true,...}
./scripts/smoke-cloud.sh # necesita PCX_USER_TOKEN y PCX_NODE_TOKEN; PCX_NODE_ID=demo-node por defecto
claude mcp add --transport http pcx "$PCX_GATEWAY_URL/mcp" --header "Authorization: Bearer $PCX_USER_TOKEN"
codex mcp add pcx --url "$PCX_GATEWAY_URL/mcp" --bearer-token-env-var PCX_USER_TOKEN
```
Ejecutar smoke **antes del nodo real**: escribe estado sintético persistente y rechaza un nodo ya conectado; el snapshot real lo reemplaza. Sale distinto de cero ante error y cierra el fake al terminar.
OpenCode **v2**, agregar a `opencode.json` (sustituir HOST; en v1, `pcx` va directamente bajo `mcp`):
```json
{"mcp":{"servers":{"pcx":{"type":"remote","url":"https://HOST.onrender.com/mcp","headers":{"Authorization":"Bearer {env:PCX_USER_TOKEN}"},"oauth":false}}}}
```
Lanzar los clientes con `PCX_USER_TOKEN` en su entorno; comprobar `claude mcp list`, `codex mcp list` o `opencode mcp list` y pedir `scene`. Referencias: [Claude](https://code.claude.com/docs/en/mcp), [Codex](https://developers.openai.com/codex/mcp), [OpenCode](https://opencode.ai/docs/mcp-servers/).
En el computador del nodo, con `pcx` instalado y **el mismo** `PCX_NODE_TOKEN` exportado:
```sh
export PCX_CLOUD_URL=wss://HOST.onrender.com/node PCX_NODE_ID=demo-node
pcx serve # core + enlace nube; pcx up para el montaje con pcx.toml ya configurado
```
Verificar `connected_nodes:1` en `/health` y `scene` por MCP; los IDs deben coincidir con Render.

## Costo y rollback

USD/mes aproximados, [tarifas oficiales](https://render.com/pricing): web `starter` **7**, Postgres `basic-256mb` **6** + 1 GB **0,30**, Key Value `256mb` **10** = **23,30/mes**. Workspace Hobby **0**; impuestos, tráfico excedente y aumentos de capacidad aparte. Todos los recursos elegidos son pagos y no duermen; Free no sirve para WSS continuo.
Presupuesto USD 50: unos **USD 30,60 por 40 días**, margen **USD 19,40**, sin extras. Confirmar créditos en el mismo workspace Hobby antes del alta; sin scaling ni previews, disco fijo de 1 GB. Limitar gasto de builds a USD 0 y revisar Billing: no hay tope de cómputo. Los alias `starter`/`basic-256mb` siguen válidos ([planes](https://render.com/docs/compute-plans)).
Rollback: servicio → **Events → deploy sano → Rollback**; verificar `/health`, reconexión y `scene`. Render desactiva autodeploy tras rollback: conservarlo apagado hasta corregir/revertir el commit y luego reactivar `checksPass`/sincronizar Blueprint.
Rollback de código no revierte DB: mantener compatibilidad con `schema_meta.version=2`; ante una futura migración incompatible restaurar backup/PITR en otra DB y cambiar su enlace. No borrar la DB ni cambiar los tokens para hacer rollback (su rotación requiere actualización explícita en DB).

## Desarrollo local y evidencia

Con Docker disponible mañana: exportar los tres tokens y `PCX_POSTGRES_PASSWORD=$(openssl rand -hex 32)`; ejecutar `docker compose -f cloud/compose.yaml up --build -d`. Gateway en `http://127.0.0.1:9215`, Postgres 16 + Valkey privados, healthchecks en los tres; detener con `docker compose -f cloud/compose.yaml down` (conserva DB).
Sin Docker: `cd cloud/gateway && PORT=9215 uv run python -m pcx_gateway`, con tokens exportados, `DATABASE_URL`/`REDIS_URL` ausentes para SQLite/memoria; smoke desde otra terminal con `PCX_GATEWAY_URL=http://127.0.0.1:9215`; detener gateway con Ctrl-C.
Prueba ejecutada con `uv run` en 9215, SQLite temporal, tokens efímeros; salida real:
```text
PASS /health
PASS MCP tools/list (11 tools)
PASS fake node hello/welcome + snapshot + /api/nodes connected
PASS MCP look round trip via fake node
PASS fake node disconnected (no background process)
SMOKE OK
PASS local gateway stopped
```
`cd cloud/gateway && uv run pytest`: **19 passed in 5.68s**. No procesos vivos ni despliegues. Docker daemon apagado: **no se construyeron ni arrancaron imágenes**; Compose no se probó en ejecución.
Validación: `docker compose ... config --quiet`, `bash -n scripts/smoke-cloud.sh`, Hadolint vía `uvx --from hadolint-bin hadolint cloud/gateway/Dockerfile`, sin hallazgos; instalación de producción no editable comprobada fuera de Docker.
Blueprint validado con `jsonschema.Draft202012Validator` + PyYAML contra [JSON Schema oficial](https://render.com/schema/render.yaml.json); repetir descargando ese schema y validando `yaml.safe_load(open("render.yaml"))`. Esto valida estructura, no capacidad/cuotas de la cuenta.

## Otras infras

Latencia indicada por proximidad geográfica, **sin RTT medido desde Chile**; precios orientativos, sin impuestos.

| Infra | WebSockets largos / Chile | Postgres, costo y facilidad |
| --- | --- | --- |
| Fly.io | VM con WS persistente y autostop desactivado; [regiones oficiales](https://fly.io/docs/reference/regions/): **`gru` São Paulo sí; `scl` Santiago no figura**. Menor distancia esperada que Virginia. | Managed Postgres disponible en `gru`; [Developer USD 4/mes, Basic USD 38/mes](https://fly.io/pricing/) + almacenamiento, VM y red. Más configuración de Machines/red que Blueprint. |
| Railway | [WS sin límite de duración](https://docs.railway.com/networking/public-networking/specs-and-limits); desactivar serverless/sleep. [Virginia, sin región sudamericana](https://docs.railway.com/deployments/regions): latencia esperada similar a Render. | [Postgres mantenido; HA/backups/PITR configurables](https://docs.railway.com/databases/postgresql). [Hobby USD 5 / Pro 20](https://docs.railway.com/pricing/plans), incluyen ese consumo; excedente por recursos. Muy fácil, total variable. |
| Google Cloud Run | [WS máximo 60 min y reconexión](https://cloud.google.com/run/docs/triggering/websockets); fijar una instancia y evitar tráfico entre revisiones. [Santiago `southamerica-west1`](https://cloud.google.com/run/docs/locations): menor distancia esperada. | Cloud SQL Postgres gestionado aparte; WS abierto mantiene facturación de instancia activa + DB/red. Cotizar por región/uso; IAM, SQL y límites añaden trabajo para mañana. |
| Vercel (solo frontend) | CDN apropiada para landing/consola. Sus [WS sí existen, pero terminan al máximo de duración de Functions](https://vercel.com/docs/functions/websockets); no trasladar aquí el gateway con estado en proceso. | Postgres mediante proveedor/integración aparte; frontend Hobby sin costo dentro de límites, planes pagos/consumo según uso. Fácil; el backend sigue en Render. |

**Recomendación:** Render Virginia para la demo: USD 23,30/mes estimados, WSS persistente, DB/Key Value enlazados y un Blueprint ya validado. Evaluar Fly `gru` después si mediciones desde Chile justifican menor latencia y el trabajo de migración; conservar las interacciones físicas críticas en el nodo.

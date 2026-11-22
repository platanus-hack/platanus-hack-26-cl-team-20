# Render: precios, créditos y plan para USD 50 (pcx) — 21-nov-2026

Fuentes consultadas el 21-nov-2026: [pricing](https://render.com/pricing), [compute-plans](https://render.com/docs/compute-plans), [free](https://render.com/docs/free), [faq](https://render.com/docs/faq), [blueprint-spec](https://render.com/docs/blueprint-spec), [deploys](https://render.com/docs/deploys), [websocket](https://render.com/docs/websocket), [team-members](https://render.com/docs/team-members), [projects](https://render.com/docs/projects), [outbound-bandwidth](https://render.com/docs/outbound-bandwidth), [build-pipeline](https://render.com/docs/build-pipeline), [postgresql-creating-connecting](https://render.com/docs/postgresql-creating-connecting), [render-dashboard](https://render.com/docs/render-dashboard).
Base: `render.yaml` de la rama `manu/plataforma` (web `starter`, Key Value `starter`, Postgres `basic-256mb`, todo en `virginia`).

## 1. Precios vigentes
**Cobro**: el cómputo y el almacenamiento se prorratean **por segundo** («If a service is active for ten seconds… you are billed only for those ten seconds», faq). El plan del workspace es una suscripción plana mensual. Abajo, hora ≈ mes/730.

| Servicio | Plan (ID nuevo / nombre antiguo) | CPU / RAM | USD/mes | USD/h | Notas |
|---|---|---|---|---|---|
| Web service | `free` | 0,1 / 512 MB | 0 | 0 | Se duerme tras **15 min** sin tráfico entrante (cuentan los mensajes WS); despierta en ~1 min; 750 h/mes por workspace; reinicios en cualquier momento |
| Web / worker | `0.5c-512mb` / `starter` | 0,5 / 512 MB | 7 | 0,0096 | No se duerme. Background worker: mismos precios, sin plan free |
| Web / worker | `1c-2g` / `standard` | 1 / 2 GB | 25 | 0,034 | |
| Web / worker | `2c-4g` / `pro` | 2 / 4 GB | 85 | 0,116 | |
| Postgres | `free` | 0,1 / 256 MB | 0 | 0 | 1 GB fijo; **expira a los 30 días** (+14 días de gracia y se borra); sin backups; 1 por workspace |
| Postgres | `0.1c-256mb` / `basic-256mb` | 0,1 / 256 MB | 6 | 0,0082 | + almacenamiento **USD 0,30/GB-mes**; por defecto crea **15 GB** (= USD 4,50/mes) |
| Postgres | `0.5c-1g` / `basic-1gb` | 0,5 / 1 GB | 19 | 0,026 | Disco por defecto 15 GB |
| Key Value | `free` | 25 MB, 50 conexiones | 0 | 0 | Solo en memoria: pierde datos al reiniciar; 1 por workspace |
| Key Value | `256mb` / `starter` | 256 MB, 200–250 conexiones | 10 | 0,0137 | Persistencia en disco (AOF) incluida |
| Key Value | `1g` / `standard` | 1 GB | 20 | 0,027 | |
| Static site | — | CDN | 0 | 0 | Gratis; consume ancho de banda y minutos de build |
| Cron job | `0.5c-512mb` | 0,5 / 512 MB | — | USD 0,00016/min | Solo mientras corre |
| Disco persistente | — | — | 0,25/GB | — | Desactiva los deploys sin caída |

| Workspace | Hobby | Pro | Scale |
|---|---|---|---|
| Cuota | USD 0 + cómputo | **USD 25/mes** + cómputo | USD 499/mes + cómputo |
| Asientos | **1** (no se puede invitar) | Ilimitados (sin cobro por usuario) | Ilimitados |
| Ancho de banda incluido | 5 GB; luego USD 0,15/GB | 25 GB | 1 TB |
| Minutos de build | 500; luego USD 5/1000 | 1000 | 5000 |
| Proyectos / entornos | Ilimitados / 2 por proyecto | Ilimitados | Ilimitados |
| Autoscaling / previews multiservicio | No / no (solo previews de un servicio) | Sí / sí | Sí / sí |

## 2. Créditos
- **Dónde se canjean: no hay fuente oficial.** La documentación de Render no describe un campo para ingresar códigos; la página Billing (menú del workspace → Billing) muestra plan, método de pago, cargos del mes, facturas y uso incluido ([render-dashboard](https://render.com/docs/render-dashboard)). Otras promociones de Render se reclamaron con un enlace en un portal de créditos aparte. Revisar las instrucciones del portal de la hackathon (`hack.platan.us/26-cl/hacker/…`, como el canje de Anthropic) y, si piden un código, buscar «Credits/Redeem» en Billing del **workspace que pagará**.
- **Alcance**: Render factura **por workspace** (faq), así que el crédito debería quedar en el workspace donde se aplica. Los servicios no se pueden mover entre workspaces (faq): **canjear y desplegar en el mismo**.
- **Tarjeta**: crear un workspace pago pide método de pago (team-members); al agregar la tarjeta, Render cobra USD 1 de verificación y lo reembolsa (pricing). Sin verificar si el crédito evita la tarjeta para cómputo pago: asumir que sí la piden.
- **Al agotarse**: sin documentación específica; lo previsible es que el excedente se cobre a la tarjeta. **Render no ofrece tope de gasto para cómputo**; solo hay *spend limit* para minutos de build (Workspace Settings → Build Pipeline). Los avisos por correo cubren el uso incluido (banda, minutos, horas free), no un presupuesto.
- **Compañeros sin pagar asientos**: Hobby tiene 1 asiento y Pro cuesta USD 25/mes (la mitad del crédito). Recomendado: **workspace Hobby de una sola persona**; el resto despliega con `git push` (auto-deploy del Blueprint) y ve logs pidiéndolos o vía la CLI/API key del dueño. Pasar a Pro solo si hace falta el dashboard compartido (Pro: miembros sin costo por usuario).

## 3. Plan de gasto (créditos válidos hasta el 31-dic ≈ 40 días desde hoy)
| Opción | Servicios → plan | USD/día | **48 h** | **1 mes** | 40 días (al 31-dic) | Margen al 31-dic |
|---|---|---|---|---|---|---|
| Mínima | web `starter` 7 + Postgres `free` + Key Value `free` + static | 0,23 | **0,46** | **7,00** | 9,2 | 40,8 |
| **Recomendada** | web `starter` 7 + Postgres `0.1c-256mb` 6 con **1 GB** 0,30 + Key Value `256mb` 10 + static 0 | 0,77 | **1,53** | **23,30** | 30,6 | **19,4** |
| Recomendada + agente | lo anterior + worker `pcx-agent` `starter` 7 | 1,00 | **1,99** | **30,30** | 39,8 | 10,2 |
| Holgada | web `1c-2g` 25 + Postgres `0.5c-1g` 19 + 5 GB 1,50 + Key Value `256mb` 10 + worker `starter` 7 | 2,05 | **4,11** | **62,50** | 82 (no alcanza) | se agota ~15-dic |
Extras previsibles: banda < 5 GB (miniaturas ≤ 512 KiB, WS pequeños) = 0; builds con uv ~2 min × deploy, < 500 min = 0. Si se agrega Pro: +25/mes.
- La mínima sirve para el fin de semana, pero el Postgres free **expira el 21-dic** y el Key Value free pierde la presencia en cada reinicio.
- El `render.yaml` actual sin `diskSizeGB` cuesta **27,50/mes** (15 GB de Postgres): 4,20 de más.
- Después del evento: bajar la holgada a la recomendada, o suspender servicios (el cobro por segundo se detiene).

## 4. Configuración recomendada
- **Workspace**: Hobby, una sola persona como dueña; crear un proyecto `pcx` con entorno `production` (Hobby permite 2 entornos por proyecto; logs y métricas unificados solo si todo está en la misma región).
- **Región**: mantener `virginia` (no se puede cambiar después). Los servicios estáticos van por CDN global. Sin fuente oficial de latencia: desde Santiago, la costa este de EE. UU. suele tener ~110–140 ms de RTT y Oregon ~170–200 ms (estimación; medir con `curl -w %{time_connect}` antes de crear). Ohio es equivalente a Virginia.
- **Evitar cargos sorpresa**: sin bloque `scaling` (autoscaling requiere Pro, igual que las previews multiservicio); `numInstances: 1`; sin campo `previews` en servicios (deshabilita previews de PR, que se cobran igual que el servicio base); *spend limit* de build en USD 0; nada de discos persistentes; `storageAutoscalingEnabled` en `false` (valor por defecto).
- **Alertas**: no hay alertas de presupuesto; revisar Billing → «accrued usage charges» una vez al día. Las notificaciones por correo/Slack cubren fallos de deploy y servicios suspendidos.

**Cambios concretos al `render.yaml`** (no editado):
1. `databases[pcx-db]`: agregar `diskSizeGB: 1` (ahorra 4,20/mes; el valor debe ser 1 o múltiplo de 5 y solo se puede aumentar). Opcional: `plan: 0.1c-256mb` (mismo plan con el ID nuevo).
2. `pcx-gateway`: agregar `autoDeployTrigger: checksPass` (o `off` el domingo durante la demo) para que un push no corte los WSS en vivo. Opcional: `plan: 0.5c-512mb`.
3. `pcx-events`: opcional `plan: 256mb` (ID nuevo de `starter`, USD 10). Para la opción mínima: `plan: free` (los datos se pierden al reiniciar y no migran al subir de plan).
4. Agregar el static site: `type: web`, `runtime: static`, `buildCommand`, `staticPublishPath` (sin `plan` ni `region`).
5. Si se usa el agente: `type: worker`, `name: pcx-agent`, `plan: starter`, `region: virginia`, `numInstances: 1`, `autoDeployTrigger` igual al del gateway y `buildFilter` propio.
6. Opcional: envolver todo en `projects: [{name: pcx, environments: [{name: production, services: […], databases: […]}]}`. No declarar ningún recurso dos veces.
7. Mantener: `numInstances: 1`, sin `scaling`, `ipAllowList: []` en Postgres y Key Value (obligatorio en Key Value), `maxShutdownDelaySeconds: 30` (≥ 20 s de cierre de la app) y `buildFilter`.

## 5. Riesgos
| Riesgo | Detalle (fuente) | Mitigación |
|---|---|---|
| Corte de WSS en cada deploy o restart | Render levanta la instancia nueva, le pasa el tráfico y **60 s después** manda SIGTERM a la vieja; luego SIGKILL tras `maxShutdownDelaySeconds` (deploys). Durante ~60–90 s **conviven dos procesos**: el nodo sigue en el viejo y las llamadas MCP nuevas llegan al nuevo, que lo ve offline | No desplegar durante la demo (`autoDeployTrigger: off`); el nodo ya reconecta con backoff; reintentar las tools tras «nodo offline» |
| Mantenimiento de la plataforma | Render reemplaza instancias periódicamente (websocket) | Heartbeat de 10 s y reconexión ya implementados |
| Cold start (plan free) | 15 min sin tráfico → ~1 min para despertar; los heartbeats WS lo mantienen despierto solo si hay un nodo conectado; reinicios arbitrarios | Usar `starter` (0,46 USD por 48 h) |
| Postgres free | Expira a los 30 días, 1 GB, sin backups | Usar `0.1c-256mb` con 1 GB |
| Key Value free | En memoria; pierde datos en reinicios y al subir de plan | `256mb` si la presencia importa; el gateway funciona sin Redis (memoria) |
| Starter con 512 MB | FastAPI + asyncpg cabe; picos de miniaturas podrían acercarse al límite | Vigilar Metrics; subir a `1c-2g` (cambio sin caída, solo redeploy) |
| Crédito insuficiente o no aplicado | Sin tope de gasto de cómputo; se cobra a la tarjeta | Confirmar en Billing que el crédito aparece **antes** de crear servicios pagos; revisar cargos diarios |
| Banda > 5 GB (Hobby) | Con tarjeta: 0,15/GB; sin tarjeta: se suspenden los servicios hasta fin de mes | Improbable con este tráfico |

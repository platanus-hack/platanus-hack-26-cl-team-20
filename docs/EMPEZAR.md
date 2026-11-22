# pcx: cómo verlo, correrlo y desplegarlo (estado sábado 21-nov, tarde)

## Qué hay andando
- **Desplegado:** https://pcx-gateway.onrender.com (Render, plan free: se duerme tras 15 min sin uso y tarda ~1 min en
  despertar). Un solo servicio sirve la consola (`/`), el visor (`/ver/<token>`), el MCP (`/mcp`) y el WSS de nodos.
- **La mesa de Daniel** corre con el navegador como nodo: webcam cenital + proyector T749, calibración automática,
  manos en vivo (MediaPipe), UI en HTML proyectada y toque por permanencia del dedo.
- **Ver en vivo:** el enlace `/ver/…` (solo lectura) lo entrega el botón «Copiar enlace para ver» de la consola.
  Muestra la cámara rectificada a la mesa con lo proyectado encima y el registro del agente.

## Correrlo en tu computador
```sh
git clone git@github.com:satelerd/platanus-hack-26.git && cd platanus-hack-26 && git checkout daniel/consola
cd cloud/gateway && PORT=9215 PCX_OPEN_SESSIONS=1 uv run python -m pcx_gateway
# Chrome o Edge: http://localhost:9215/ → permitir cámara → elegir pantalla → Lanzar → Calibrar
# «Copiar» entrega el comando para conectar Claude Code, Codex u otro cliente MCP a esa sesión
```
Motor local (Rust, protocolo v0.1): abre el enlace que imprime `pcx up` (`…/?motor=ws://127.0.0.1:9230&motor_token=…`)
o pega URL y token en «Motor local». Para probar sin el motor ni cámara, el motor falso:
```sh
cd cloud/gateway && uv run python -m tools.fake_motor --token secreto
# http://localhost:9215/mesa?sim=1&motor=ws://127.0.0.1:9230&motor_token=secreto → Lanzar → «Percibir ahora»
```
Tests: `cd cloud/gateway && uv run pytest` (incluye los tests JS de la consola si hay `node`). La consola son archivos estáticos sin build en
`cloud/gateway/src/pcx_gateway/console/`. Estado y decisiones: `ESTADO-consola.md` (raíz).

## Desplegar
Render despliega solo con cada push a la rama **`daniel/consola`** de `satelerd/platanus-hack-26` (Blueprint
`render.yaml`, 1–2 min). Todo el equipo tiene permiso de escritura ahí. Cada deploy corta la conexión de los nodos unos
segundos: hay que recargar la consola y relanzar la ventana del proyector. No desplegar durante una demo.

## Repos
- `satelerd/platanus-hack-26` (privado): donde se trabaja y de donde despliega Render.
- `platanus-hack/platanus-hack-26-cl-team-20` (oficial, público), rama `pcx`: espejo del anterior sin las
  transcripciones de reuniones. Lo sincroniza el orquestador de Daniel; no empujar directo a esa rama.

## Qué falta (por dónde seguir)
1. **Percepción rápida:** hoy los objetos se siguen desde afuera con Grounding DINO + MobileSAM (~12 s por ciclo,
   `tools/mesa-agente/`). Falta portar la foto limpia y la cascada de `seguidor_v4` (sl-scanner, rama
   `hack/daniel-mesa`) al nodo, o correrla en un nodo de cómputo aparte (diseño en `ESTADO-consola.md`).
2. **Motor Rust local**: la consola ya habla el protocolo motor v0 (`ESTADO-consola.md`, «Motor local»); falta
   probarlo con el motor real y el proyector.
3. Los PR abiertos de Manu (#6 plugin, #7 modos, #9 seguridad, #10 instaladores, #11 pcx-agent, #12 catálogo) no están
   integrados con `daniel/consola`.
4. Datos para probar sin la mesa: `data/mesa/2026-11-21/`.

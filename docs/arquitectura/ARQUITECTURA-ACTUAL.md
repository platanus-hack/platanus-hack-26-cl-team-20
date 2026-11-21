# Arquitectura actual de pcx (sáb 21-nov, ~03:00) — para revisores y agentes

Reemplaza en lo que contradiga a `03-ARQUITECTURA-PROPUESTA.md`. Decisiones tomadas por Manu.

## Producto
Contexto físico para agentes de IA: proyector + cámaras (mesa), luego cámaras de la casa y domótica (Home Assistant). Cualquier agente (Claude Code, Codex, OpenCode, Claude Desktop) se conecta por MCP. Instalable por una persona en **macOS, Windows y Linux**. Hackathon Platanus Hack 26 (termina dom 22-nov ~09:00; demo de 5 min; rúbrica: técnico 25 %, ambición 20 %, ejecución 20 %, impacto 20 %, originalidad 15 %).

## Topología
- **Nodo local** (computador de la persona): dueño del estado físico. Todo en **Rust**, un binario `pcx` + `pcx-renderer`.
- **Cerebro en Render**: refleja el estado (patrón reported/desired), agente, memoria, MCP remoto. Render está «al centro» también en la demo (decisión explícita; se aceptó el riesgo de Wi-Fi).
- Conexión: el nodo abre **una WSS saliente** a Render (Hello → Welcome{last_cursor} → StateSnapshot → replay; heartbeat; `stream_id`, `HistoryGap`). Gateway en una sola instancia.

## Nodo (Rust, workspace en la raíz)
| Crate | Rol |
|---|---|
| `pcxd` | binario `pcx`: CLI (up, mcp, calibrate), supervisor de procesos |
| `pcx-core` | actor de escritor único: mundo en mm (`table_mm`), widgets, comandos, eventos con seq, `run_flow` (máquina de estados local), ask/dwell/hit-test, MCP local (rmcp, Streamable HTTP + stdio), WS `/render`, enlace a la nube, **SQLite** (log de eventos, snapshot, catálogo de objetos, calibraciones, flujos, dispositivos, auditoría, HomePolicy) |
| `pcx-shm` | anillo de memoria compartida (último cuadro gana, seqlock), layout compatible con lector Python |
| `pcx-capture` | adaptadores: uvc (nokhwa), replay, mjpeg (Quest/camserve), celular por web (getUserMedia → WS) |
| `pcx-hands` | MediaPipe palm+landmarks en ONNX (`ort`), tracking, One Euro, toque (pinch, apoyo, dwell), episodios de manos |
| `pcx-objects` | cascada de Daniel: diff limpio → NCC → features rígidas → Grounding DINO + MobileSAM (ONNX) → LLM (Claude por HTTP) desempata/registra |
| `pcx-calib` | rápida (marcadores + homografía), precisa (gray code rápido, LUT denso que corrige paralaje), verificación (residuo en mm, «rig movido»), fotos limpias con `blank` |
| `renderer` (`pcx-renderer`) | wgpu + vello + parley; widgets declarativos en mm → textura → homografía/LUT en shader; efectos WGSL con hot reload validado por naga; 1,4–7,8 ms/cuadro |
- Transporte interno: memoria compartida para cuadros; canales en proceso y ZeroMQ (crate `zeromq`, puro Rust) para workers externos; WS core↔renderer.
- Sin OpenCV del sistema; dependencias que compilen solas en los 3 SO.

## Contratos
`contracts/`: modelos Python (msgspec) → JSON Schema → crate Rust `pcx-contracts` + tipos TS. v2 tras revisión de Codex (versionado, request_id, HomePolicy con confirmación verificable, Transform discriminado, blank/blanked).

## Render
| Servicio | Tipo |
|---|---|
| `pcx-gateway` (Python FastAPI) | MCP remoto (token; OAuth después), REST, WSS nodos, confirmaciones domótica, tool `history` |
| `pcx-web` | static site: landing, consola, scripts de instalación |
| `pcx-agent` | background worker: agente propio con Claude (`claude-opus-5-5`) |
| Postgres | usuarios, espacios, nodos, historial/memoria, auditoría |
| Key Value | presencia, pub/sub, colas |

## Distribución
Página web → `curl … | sh` / `irm … | iex`; binarios por GitHub Releases (cargo-dist/dist o workflow propio); `pcx up` vincula el nodo a la cuenta con código/QR; MCP remoto por URL + token (comandos para Claude Code/Codex/OpenCode); celulares por QR sin instalar.

## Equipo y hardware
2 devs + muchos agentes (Claude Code y Codex). El montaje real (proyector T749 + webcam USB) está en el Mac de Daniel; su equipo avanza en paralelo sobre el engine de Max en la rama `mesa` (cascada de objetos, escaneo gray code en 4,4 s, lienzo rectificado a 0,2 px). Las pruebas de hardware las corre el orquestador de Daniel con instrucciones por WhatsApp.

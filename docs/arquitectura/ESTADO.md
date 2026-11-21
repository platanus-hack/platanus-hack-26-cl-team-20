# Estado de pcx (sáb 21-nov, ~04:15)

## Hecho y probado
| Componente | Tests | Notas |
|---|---|---|
| `contracts` v2.1 (Python → Schema → Rust/TS) | 103 Py + Rust | Revisado por Codex (10 hallazgos aplicados) |
| `pcx-core` + `pcxd` (binario `pcx`) | 35 | Actor de escritor único, MCP (rmcp, HTTP + stdio), SQLite, flujos, enlace a la nube, supervisor; e2e con cliente MCP real |
| `pcx-renderer` | 13 | 60 fps en el proyector real; 1,4–8 ms/cuadro headless |
| `pcx-shm` + `pcx-capture` | 17 | Anillo 0,23 ms en hardware; interop con Python |
| `pcx-hands` | 17 | ~1 px vs MediaPipe; 30 fps en vivo, mano en 95–97 % de cuadros |
| `pcx-objects` | 19 | Cascada de Daniel niveles 0–2 en Rust; 3–5 con ONNX/Claude (modelo DINO sin descargar) |
| `pcx-calib` | 9 | Simulación 0,5 mm; **hardware real: 96 % cobertura, 5 planos, 0,8 px** |
| `cloud/gateway` | 19 | Implementado por Codex, revisado por Claude; probado contra Postgres real |
| `web/` (landing) | Lighthouse 99–100 | 3 heroes, tema cálido/frío, pausa de movimiento |
| **Workspace Rust** | **112 en verde** | |

Hardware real (T749 + C920): latencia de punta a punta **215 ms** (engine original 313 ms). Detalle: `docs/pruebas/hardware-manu/`.

## En curso
- Codex: arreglos del renderer (selección de pantalla por nombre real, mensaje `pattern`, imágenes solo locales).

## Pendiente (orden)
1. **B · Cableado**: `pcx up` con todo junto + captura nativa por SO (bug B6: la C920 no abre en macOS con nokhwa) + ONNX en proceso aislado (cuelgue de CoreML al arrancar).
2. **C · Seguridad/robustez**: hallazgos de las revisiones (endpoint de debug, token por defecto, supervisor por SO, idempotencia).
3. **E · E2E en hardware**: calibrar → Claude Code proyecta por MCP → la cámara verifica.
4. Profundidad métrica (triangulación proyector-cámara; falta una medida de escala).
5. Despliegue en Render, consola, instaladores multiplataforma, Home Assistant, ESP32-CAM.

## Decisiones abiertas (de Manu/equipo)
Caso de demo · nombre del producto · tema y hero de la landing · proyector Android / ESP32-CAM.

# Arquitectura de pcx

De sl-scanner (engine de Max) a pcx: contexto físico para agentes de IA. Manu decide; Claude y Codex implementan y se revisan entre sí.

![Vista de sistema](diagramas/1-sistema.png)

![Nodo local en Rust](diagramas/2-nodo.png)

| Archivo | Qué es |
|---|---|
| [ESTADO.md](ESTADO.md) | **Estado actual**: qué está hecho, probado y pendiente |
| [ARQUITECTURA-ACTUAL.md](ARQUITECTURA-ACTUAL.md) | Arquitectura vigente (decisiones tomadas) |
| [diagramas/arquitectura-pcx.pdf](diagramas/arquitectura-pcx.pdf) | Diagramas UML de componentes: sistema y nodo |
| [01-ENGINE-ACTUAL.md](01-ENGINE-ACTUAL.md) | Análisis del engine de Max: qué funciona, mediciones, bugs |
| [02-CASOS-DE-USO.md](02-CASOS-DE-USO.md) | Capacidades (C1–C9) y casos de uso (A–G) |
| [03-ARQUITECTURA-PROPUESTA.md](03-ARQUITECTURA-PROPUESTA.md) | Propuesta inicial (histórica; manda ARQUITECTURA-ACTUAL) |
| [PROTOCOLO-ESCENA.md](PROTOCOLO-ESCENA.md) | Protocolo renderer ↔ core (v0; vigente en `contracts/`) |
| [informes/](informes/) | 8 informes de lectura completa del engine de Max |

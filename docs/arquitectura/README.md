# Arquitectura: de sl-scanner a la nueva versión

Carpeta de trabajo de Manu (con Claude y subagentes) para refactorizar el engine de Max hacia una arquitectura técnicamente sólida. Forma de trabajo: Claude y los subagentes proponen y ejecutan; **las decisiones las valida Manu**.

| Archivo | Qué es | Estado |
|---|---|---|
| [01-ENGINE-ACTUAL.md](01-ENGINE-ACTUAL.md) | Qué es y qué hace hoy el engine de Max, qué funciona de verdad, mediciones, bugs y qué conservar | Listo |
| [02-CASOS-DE-USO.md](02-CASOS-DE-USO.md) | Capacidades del engine (C1–C9) y casos de uso candidatos (A–G) | Listo; el caso de la demo lo elige el equipo |
| [03-ARQUITECTURA-PROPUESTA.md](03-ARQUITECTURA-PROPUESTA.md) | Propuesta con decisiones D1–D10, memoria, latencia, seguridad y spikes | **Por validar** |
| `04-PLAN.md` | Plan por fases con agentes implementadores y revisores | Pendiente de validar 03 |
| [informes/](informes/) | Los 8 informes de lectura completa del engine, con citas `ruta:línea` | Referencia |

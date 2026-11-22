# WUI · World UI

<img src="./project-logo.png" alt="Logo de WUI" width="200" />

Platanus Hack 26: Santiago · team-20 · Track 🌎 Physical World

**El motor que saca a los agentes de IA de la pantalla con realidad proyectada.**

Con una cámara y un proyector, el agente ve los objetos reales, los sigue en milímetros y proyecta sobre ellos lo que la persona necesita, según lo que pide o lo que está haciendo. Está diseñado para acompañarte en lo personal, lo educativo y tu productividad.

## Qué le da el motor a un agente

- **Ver** los objetos reales y dónde está cada uno, en milímetros.
- **Seguir** cada objeto: lo proyectado se mueve con él.
- **Proyectar** texto, marcas y guías sobre los objetos y la superficie.
- **Esperar** un toque o una frase de la persona.
- **Recordar** lo que pasó en ese espacio.

## Cómo funciona

1. **Motor local:** un nodo en Rust en tu computador, con la cámara y el proyector.
2. **Gateway en la nube:** servidor MCP remoto, al que se conectan los nodos y los agentes.
3. **Agentes:** cualquier cliente MCP (Claude Code, Codex u otro) usa herramientas como `scene`, `look`, `project`, `ask` y `events_wait`.

## Probarlo

https://pcx-gateway.onrender.com

## Equipo

- Daniel Sateler ([@satelerd](https://github.com/satelerd))
- Manuel Alejandro Blanco Contreras ([@mblancoc](https://github.com/mblancoc))
- Matilda Roberts ([@fforttuna](https://github.com/fforttuna))
- Maximiliano Mardones ([@maxmardones](https://github.com/maxmardones))
- Pablo Moraga ([@salvatierrapm](https://github.com/salvatierrapm))

🗺️ Plan de alto nivel (Manu, 18:00), según lo decidido a las 17:16

EL PRODUCTO
Cada persona instala en su compu un MOTOR LOCAL (Rust). El motor ve la mesa con la cámara, entiende qué objetos hay y dónde están, y le dice a la WEB APP qué proyectar y en qué coordenadas. La web app lo dibuja en la 2.ª pantalla, que es el proyector. Los agentes de IA controlan todo por MCP, con la llave de su sesión.

cámara → motor local (tu compu) → web app (navegador) → proyector
              ↑                          ↑
  lo instala tu agente desde       sesión con tu llave (servidor)
  un link de la landing            presets: clase, lab, escritorio

QUIÉN HACE QUÉ
• Daniel: web y servidores. Web app (consola + 2.ª pantalla), sesiones con llave, gateway/MCP en Render, deploy.
• Max: modelos y tracking (SAM, cascada, ids estables, catálogo), percepción propia de cada escena (vaso en ml, pastillero, libreta) y las interfaces que se proyectan.
• Manu: que todo se una. El motor local que se descarga (servidor local, cámara, foto limpia, empaquetado con los pesos), su conexión con la web app y los servidores, los contratos entre piezas y la instalación por agente desde la landing.
• Pablo y Matilda: presets o entornos (clase, lab, escritorio) guardados en la sesión, y el guion de la demo.

CÓMO SE UNE (contratos)
• Motor ↔ web app: protocolo v0 por WebSocket local (ws://127.0.0.1:9230). Viajan coordenadas y objetos en mm, no video. Incluye «apaga el proyector para la foto limpia», manos presentes/ausentes y objetos incrementales. La URL es configurable: si el compu es malo, mañana puede apuntar a un motor remoto vía proxy.
• Percepción ↔ motor: una interfaz Rust (`Perceptor`). Max la implementa y Manu la hospeda. La base (cascada + SAM con los arreglos del diagnóstico) la deja lista un agente de Manu en `feat/percepcion-motor` y desde ahí la toma Max.
• Web app ↔ servidor: lo de hoy (sesiones, WSS, MCP). Lo nuevo va a contracts/ v2.2.

HITOS
• 19:30 · Protocolo y contratos cerrados. PR de motor.js para Daniel. Max toma la percepción.
• 21:00 · Motor local corriendo en la mesa con foto limpia real. Cámara fija y recalibrada.
• 23:00 · Objetos re-ubicados en <1 s en la mesa (hoy ~12 s).
• 01:00 · CORTE: si la percepción Rust no corre en la mesa, la demo usa el seguidor actual con los arreglos rápidos. Bundle v0.1 con pesos y setup por agente probado en un compu limpio.
• 03:00 · Presets corriendo sobre la sesión.
• 06:00 · Congelamos deploys y ensayamos 2 veces.
• 08:30 · Demo.

⚠️ YA
• Fijar cámara y proyector: la cámara se corre ~0,75 mm/min. Recalibrar justo antes de la demo.
• Que la rama desplegable sea `main` (con CI) y nada de deploys durante la demo.

Plan detallado por persona y diagramas: docs/plan/ en manu/plataforma.

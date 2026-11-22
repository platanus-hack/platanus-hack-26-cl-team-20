# Encargo: investigador — Platanus Hack 26

Eres el agente de investigación del equipo de Daniel en la hackathon Platanus Hack 26 (track Physical World). Te coordina el orquestador (tab «Orquestador»). Lee primero `PLAN.md` (secciones «El proyecto», «Feedback del mentor» y «Rúbrica») y `repo/docs/CONTRATO.md`.

## Contexto en una línea
Engine que con un proyector barato y cámaras (webcam, teléfono) mapea una superficie, reconoce objetos y proyecta interfaces interactivas encima, controlado por agentes. El engine lo trae Max; todavía no vemos su código. El mentor objetó que falta un dolor concreto y foco.

## Qué necesito (en este orden, entregando cada parte apenas esté)
1. **Arte previo y competencia** (`investigacion/01-arte-previo.md`): proyectos y productos de realidad aumentada proyectada e interfaces sobre mesa (por ejemplo Dynamicland/Folk Computer, Paper Programs, Lightform, RoomAlive/IllumiRoom, Sony Xperia Touch, Lumen de Arvind Sanjeev, y los sistemas industriales de instrucciones de trabajo proyectadas tipo LightGuide o Arkite). Para cada uno: qué hace, hardware, precio si se sabe, qué NO hace. Cierra con: qué sería realmente nuevo en lo nuestro y qué no conviene vender como novedad.
2. **Dolor y mercado** (`investigacion/02-dolor.md`): evidencia concreta, con cifras y fuentes, para 2 o 3 dolores candidatos: (a) guía de armado/mantención/capacitación sobre el objeto real, (b) educación (sala de clases, pupitre), (c) escritorio de trabajo con agentes. Qué cuesta hoy resolverlo, quién paga, y cuál se demuestra mejor en 5 minutos frente a un jurado. Recomienda uno.
3. **Técnica práctica para 30 horas** (`investigacion/03-tecnica.md`): calibración proyector-cámara (homografía sobre plano vs. luz estructurada/código Gray; librerías abiertas que funcionen hoy), detección de toque con cámara RGB (manos con MediaPipe, sombras, segunda cámara lateral), segmentación y tracking en tiempo real (familia Segment Anything y alternativas livianas), latencias realistas en un MacBook, y cómo lograr buen contraste con un proyector barato y luz ambiente. Para cada tema: la opción más simple que funciona, la opción ambiciosa y el riesgo.
4. **Ideas de demo** (`investigacion/04-demo.md`): 5 momentos «wow» ordenados de menos a más, cada uno con qué necesita del engine.

## Reglas
- Verifica en la web; no respondas de memoria. Cita la URL de cada dato. Si algo no lo pudiste confirmar, dilo.
- Corto y denso: cada archivo cabe en una pantalla o dos, con tablas donde sirva.
- No escribas código del proyecto ni toques `repo/`. Solo investigación en `investigacion/`.
- Nada de SmartUp, nada destructivo, no gastes plata, no escribas a terceros. Español chileno.
- Puedes usar subagentes internos tuyos para paralelizar las búsquedas, pero no abras panes ni tabs nuevos en Herdr.

## Al terminar cada archivo
Deja `investigacion/RESUMEN.md` actualizado con 5 líneas: lo más importante que encontraste y qué recomiendas.

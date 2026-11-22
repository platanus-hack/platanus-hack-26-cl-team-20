# Mesa v2 (experimento del 21–22 nov)

Percepción y proyección en **mm reales de la mesa**, en Python, por fuera del motor Rust. Proyecta a través de la consola
(`localhost:9217`, sesión de la mesa) con widgets `html`. Nada de esto está en `main` todavía.

Python: `~/code/platanus-hack-26/sl-scanner/.venv/bin/python`. Cliente MCP de la sesión: `pcxl.py` (scratchpad del agente).

## Piezas

| Archivo | Qué hace |
|---|---|
| `calibrar.py` | Un comando: pantalla completa → `calibrate precise` → foto 4K → bordes del tablero + iPhone XR → `fp-metric.json` → luz estructurada → relieve. ~130 s (88 s son el `calibrate` de la consola). `--sin-sl` la salta. |
| `fp_geo.py`, `fp_metric.py` | Bordes del tablero izquierdo (rectángulo) + iPhone XR (150,9 × 75,7 mm) → homografía px→mm reales y canvas↔mm. |
| `fp_rect.py` | Cámara normalizada: vista cenital en mm reales, solo la mesa. Se adapta a 4K o 1080p. |
| `pipeline.py una\|vivo [--progresivo]` | Normaliza, toma las cajas del agente (`out/vlm.json`) o de Grounding DINO, SAM **por recorte** de cada objeto, publica etapas en `out/`. `--progresivo` prende cada contorno en la mesa apenas sale. |
| `modelos.py` | Paso general objeto → modelo 3D paramétrico (esfera, disco, cilindro, prisma). Alturas por forma, por perspectiva o supuesto de clase (queda marcado cuál). |
| `proyectar3d.py` | Planetas como esferas trazadas por rayos (WebGL) sobre las bolas, luz desde el Sol. |
| `todo.py` | Secuencia completa: calibrar → checkerboard → contornos progresivos → modelos → planetas. |
| `skin_vivo.py <estilo>` | Foto limpia → cuenta regresiva 10 s → cronómetro → imagen generada proyectada con su tiempo. Prompts en `skin/prompts/`. |
| `capas.py` | Capas que siguen objetos: libreta (recorte del skin), lámina solar (su foto + marco animado), caja de viales (rejilla 5×4 animada). |
| `frente.sh` | Deja la ventana de proyección de la mesa en pantalla completa y al frente. |
| `dashboard.html` | `http://127.0.0.1:9300/dashboard.html`: etapas, cámara normalizada en vivo, máscaras, relieve, objetos. |

## Seguimiento sin parpadeo (`capas.py`)
- Mientras hay movimiento (manos, sombras) no se toca nada.
- Cuando la mesa queda quieta ~0,5 s se compara con la referencia. Si la zona de un objeto sigue distinta, se mide.
- La medición usa un **foco de luz negra** solo sobre esa zona; si el objeto no está ahí, un negro corto de mesa completa.
- Las capas animadas no cuentan como movimiento: para ellas se mira un anillo alrededor.
- Detectores sobre foto limpia: papel = poca saturación y claro; lámina = rectángulo oscuro ~165×107; viales = bloque de
  color saturado (centro y giro) con el tamaño real de la caja.

## Generación de imagen (skin)
- Google `gemini-3-pro-image` 4K (5504×3072): 48,9 s. `gemini-3.1-flash-image`: 13,3 s a 1376×768; 20–22 s a 2752×1536.
- OpenAI vía `codex exec` (ver `skin/gen_openai.sh`). Vertex usa la sesión de `gcloud` (proyecto `sateler-agents`).

## Límites conocidos
- La luz estructurada no da alturas métricas: cámara y proyector están casi alineados. Falta una segunda vista.
- La caja organizadora (transparente) no tiene detector confiable: SAM toma solo la tapa y no se sigue.
- El seguimiento reubica al soltar el objeto (2–4 s), no mientras se arrastra. No usa las manos todavía.
- Las bolas chicas del sistema solar quedan corridas 1–2 cm (máscaras malas).
- El registro `out/vlm.json` son cajas puestas por el agente mirando la foto: si se reordena la mesa, quedan viejas.
- La consola y el motor Rust siguen en 720p; el marco métrico real no está portado a ellos.

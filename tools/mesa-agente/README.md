# mesa-agente: scripts con los que el orquestador operó la mesa el sábado 21-nov

Prototipos de una tarde, no producto. Sirven de referencia para portar al nodo (Rust o navegador).

| Archivo | Qué hace |
|---|---|
| `pcx.py` | Cliente mínimo del MCP remoto: `PCX_USER_TOKEN=… python3 pcx.py <tool> '<json>'`; guarda las fotos de `look`. |
| `pintura.py` | Bucle: foto limpia → Grounding DINO (servicio local :8805, `scripts/detector_dino.py` de sl-scanner) → MobileSAM por caja → un solo effect `html` con un SVG en mm: relleno por silueta, planetas con color, grilla viva sobre el pastillero y cartel. |
| `sam_seguir.py` | Lo mismo pero solo contornos (`halo` con `polygon_px`). |
| `seguir2.py` | Seguimiento barato por template matching en la vista cenital rectificada; falla si reordenan la mesa. |
| `homo.py` | Homografía manual de 4 puntos cámara→mm (la reemplazó `calibrate`). |

Dependencias: el venv de `sl-scanner` (opencv, onnxruntime) y los modelos de `~/hardware/models/slscan-segmentation`
(MobileSAM en ONNX). `H.npy` es la matriz `matrix_px_to_mm` que devuelve `calibrate`, guardada con numpy.

Límites medidos: DINO en CPU ≈ 3–4 s por foto, ciclo completo ≈ 12 s; objetos chicos (taza, cucharas) entran y salen;
no hay foto limpia de verdad (se quita la pintura 0,7 s antes de fotografiar) ni cascada barata→cara como en
`seguidor_v4`. Eso es lo que hay que portar para que sea rápido.

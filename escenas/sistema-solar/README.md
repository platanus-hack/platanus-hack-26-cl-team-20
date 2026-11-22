# escenas/sistema-solar

Preset de la escena Sistema solar de WUI. Qué es y cómo se usa: [`escena.md`](escena.md).

| Archivo | Qué es |
|---|---|
| `escena.md` | El preset en palabras: objetos, comandos, momentos de la demo, cómo habla Lumo, cómo adaptarla. |
| `tema.css` | Estilo de la escena (solo tokens), encima de `sistema-base.css`. |
| `sistema-base.css` | Copia de `motor/sistema/base.css` (max/motor). Si el repo trae el original, se usa ese. |
| `planetas.json` | Catálogo editable: nombres, tamaños reales, colores, datos para niños, voces, y Lumo. |
| `plantilla_lamina.json` | Dónde está dibujado cada planeta en la lámina impresa (coordenadas de la lámina). |
| `mesa.json` | Ajustes de la mesa: planetas fijados a mano, corrimiento de la luz, ficha, seguimiento, voz. |
| `escena.py` | Widgets que se proyectan (funciones puras). |
| `conductor.py` | Une percepción y mesa por el MCP: armar, seguir, nombrar, ficha, decir, fijar… |
| `vista.py` | La escena sin mesa: HTML con la foto de referencia y los mismos widgets. |
| `percepcion/` | Percepción en Python, pura y con tests: es la referencia (spec) del módulo en Rust. |

## Percepción

Todo sobre la vista cenital rectificada (1 px = 1 mm).

- `ubicar_canasto`: la zona con textura de mimbre más grande.
- `detectar_cuerpos`: blanco liso (apertura de 11 mm que borra el mimbre), máximos de la transformada de distancia,
  perfil de 180 rayos (redondez, círculo ajustado, contraste con el entorno). El engranaje se separa por la rugosidad
  del borde (~0,05 del radio contra ≤ 0,025 en las bolas). En las 4 capturas reales: el Sol y 9 de 9 bolas, sin falsos.
- `asignar_planetas`: por rango de tamaño, con dudas explícitas (bolas a menos de 8 % de diferencia) y fijos a mano.
- `pose_grupo`: rotación y traslación del canasto entero a partir de sus bolas (RANSAC exhaustivo de pares + Kabsch).
  Aguanta bolas tapadas, cosas nuevas en la mesa y giros de 180°.
- `ubicar_laminas` y `planetas_en_lamina`: rectángulo oscuro de ~165×107 mm, orientado por el lado del Sol impreso.
- `al_limpiar(lienzo, ctx)`: el contrato de `MOTOR.md` (foto limpia → objetos `solar:*`, eventos y datos).

```sh
uv sync && uv run pytest     # 30+ tests sobre data/mesa/2026-11-21 y movimientos sintéticos
```

Decisión de Manu (21-nov, 18:23): la percepción por escena corre en Rust dentro del motor. Este Python queda como
referencia: el port vive en `node/crates/pcx-escena-solar` (rama `max/percepcion-solar`).

## Probar sin hardware

```sh
cd cloud/gateway && PORT=9321 PCX_OPEN_SESSIONS=1 uv run python -m pcx_gateway
# Chrome: http://localhost:9321/?sim=1 → Calibrar → Copiar (token)
cd escenas/sistema-solar && export PCX_USER_TOKEN=…
uv run python conductor.py --sim armar
uv run python conductor.py --sim seguir &
uv run python conductor.py --sim sim-mover 20 40 30   # simula que alguien gira y corre el canasto
```

En sim, la cámara simulada no ve los widgets html (solo los `image`), así que el conductor proyecta la foto real de la
mesa como `image` de fondo y la cámara «ve» el canasto. Medido así el 21-nov: giro de 20° recuperado como +20,0° con
10 de 10 cuerpos y 0,4 mm de error.

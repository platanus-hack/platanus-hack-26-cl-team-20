# Escena Laboratorio · WUI

La mesa guía a Matilda por un protocolo de laboratorio: el pastillero 7×4 es el sistema de coordenadas (A1…D7), la
segunda cámara mide los ml del vaso en vivo y avisa «te pasaste». Qué pasa en la mesa y cómo se cambia está en
**`escena.md`** (el preset); el estilo, en **`tema.css`**. Esto es para quien la corre o la integra.

## Archivos

| Archivo | Qué es |
|---|---|
| `escena.md` | El preset: objetos, protocolo editable (bloque yaml) e instrucciones para el agente |
| `tema.css` | Tokens del sistema común con el estilo clínico de la escena |
| `ui/piezas.css` | Piezas propias (grilla del pastillero, escala del vaso, marcas de registro), solo con tokens |
| `ui/base.css` | Copia de `motor/sistema/base.css` (max/motor @ 002e67e); se usa la del repo si existe |
| `hud.py` | Estado → widgets html (`lab-grilla`, `lab-paso`, `lab-guia`, `lab-medidor`) |
| `conductor.py` | Corre el protocolo por MCP: toques, voz, ml en vivo, registro. Resiste cortes del gateway |
| `calibrar_vaso.py` | Calibra la cámara del vaso: vacío + 50 ml + 150 ml; la zona del vaso sale sola |
| `mesa_mcp.py` | Cliente MCP mínimo (biblioteca estándar) |
| `vista_previa.py` | Las vistas sobre la foto real de la mesa, en HTML, sin mesa |
| `percepcion/` | Funciones puras + contrato del motor (`al_limpiar`, `en_vivo`) y sus tests |
| `tools/exportar_fixtures.py` | Exporta imágenes + salida de Python para los tests del port a Rust |

## Correr

```sh
cd escenas/laboratorio
UV_CACHE_DIR=/Volumes/Externo/.uv-cache uv sync          # numpy, opencv, pyyaml
uv run pytest -q                                          # 76 tests, sin hardware

# Sin mesa: una página HTML por momento de la escena, sobre la foto real
uv run python conductor.py --solo-previa estado/previa

# Contra el gateway local con la consola simulada (http://localhost:9322/?sim=1)
PCX_MCP_URL=http://localhost:9322/mcp PCX_USER_TOKEN=<llave> \
  uv run python conductor.py --grilla percepcion/tests/datos/mesa-cenital-mm-1.jpg --vaso-sim --simular-celular 20

# En la mesa
uv run python calibrar_vaso.py --camara-vaso <id>       # una vez
PCX_USER_TOKEN=<llave> uv run python conductor.py --camara-vaso <id>
```

`--publicar-objeto` publica el pastillero como objeto del mundo; no usarlo si el motor ya publica los suyos (el último
que escribe gana).

## Percepción

Funciones puras (imagen → resultado), con la convención de direcciones atada al objeto:

- `pastillero.detectar(lienzo_mm, paleta=…)` → `Grilla` con 28 celdas (`dir`, `centro_mm`, `poligono_mm`, `color`).
  Busca el bloque de colores (excluye los tonos cálidos del canasto y la mesa), ajusta un rectángulo, desliza la grilla
  7×4 columna a columna y se queda con el corrimiento y el sentido que mejor calzan con la paleta. Probado en las 4
  capturas reales (A1 ±1 mm, giro −28°) y en sintéticas a cualquier giro (error ≤ 4 mm).
- `pastillero.celda_en(grilla, punto_mm)` → la celda bajo la punta del dedo.
- `intrusos.detectar(ref_mm, ahora_mm)` → objetos ajenos (`celular` u `objeto`). Solo cuenta lo que quedó más oscuro
  que la referencia: el proyector solo suma luz, así que lo proyectado nunca dispara la alarma y no hace falta foto
  limpia. Un celular es un rectángulo lleno de ~7×15 cm que no toca el borde (un brazo entra desde el borde). El
  conductor alarma tras dos fotos seguidas y deja el protocolo en pausa.
- `vaso.nivel_px(cuadro, cal)`, `Calibracion.ml(px)`, `calibrar_con_lecturas(vacio, {ml: foto})`, `Medidor` (mediana de
  3) y `Umbral` (falta / listo / te_pasaste, con histéresis de 1 ml y 2 cuadros de confirmación). Error ≤ 3 ml de 10 a
  220 ml en sintéticas; menos de 30 ms por cuadro (hay test).
- Contrato del motor (`percepcion/__init__.py`, MOTOR.md): `al_limpiar` publica `lab:pastillero` y las celdas en
  `datos`; `en_vivo` publica `datos.vaso` y un evento `nivel` solo cuando cambia algo visible. Necesita
  `memoria["vaso_cal"]` y `memoria["objetivo_ml"]`.

Por decisión de Manu (18:23) esta percepción va en Rust dentro del motor (`node/crates/pcx-escena-lab`, rama
`max/percepcion-lab`). Este Python queda como referencia y spec, con los mismos fixtures.

## Lo que no está probado

- La cámara secundaria y un vaso reales: todavía no hay fotos del vaso. Todo lo del vaso está probado con imágenes
  sintéticas; la calibración está lista para la primera foto real.
- Toques con dedo real sobre las celdas (en sim se probó con clic; el dedo usa `point_mm` de la punta).
- El celular sobre la mesa real: probado con celulares sintéticos pegados sobre la captura real. Un celular claro
  (blanco boca abajo) no oscurece la mesa y no se detecta; en la demo, pantalla hacia arriba o funda oscura.
- La Coca-Cola de verdad: probado con un líquido sintético del mismo color (error ≤ 2 ml).
- Con agua transparente la zona del vaso no sale sola: hay que pasar `--roi` a `calibrar_vaso.py`.
- El estilo nuevo con texto de 28 mm no se ha visto proyectado ni renderizado: se cambió con el Mac sin memoria para
  renders. El estilo anterior (texto chico) sí se probó en la consola simulada de punta a punta.

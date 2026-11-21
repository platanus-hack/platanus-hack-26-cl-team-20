# Escena de ejemplo sin equipos

`scene.json` se genera con `playground.scene.demo_scene()`: mesa, cuadro, pantalla
ficticia, cocodrilo y teclado. No contiene fotografías, personas ni capturas.
Sus medidas son inventadas y no calibran un proyector real.

Desde la raíz: `python scripts/demo.py`. En el dashboard elige `contour_glow` sobre
cocodrilo o `texture_grid` sobre mesa. No requiere GPU, modelos, voz ni credenciales.
Para verla sin abrir el navegador automáticamente:

```sh
python -m playground serve --scan '' --scene examples/demo/scene.json
python -m playground.dashboard
```

Ejecuta cada comando en una terminal distinta. Para detenerlos, Ctrl+C en cada una.
La demo no trae las fotos/máscaras necesarias para tela, estampados o vectores reales.

Para una prueba aislada, el lanzador también acepta `--no-browser`, `--runtime-port`
y `--dashboard-port`. Por ejemplo: `python scripts/demo.py --no-browser --runtime-port
18790 --dashboard-port 18795`. Elige dos puertos libres.

# Cómo se corre la demo (tres escenas)

Orden: **Escritorio → Sistema solar → Laboratorio**. Cada escena tiene dos tiempos, para que al mostrarla aparezca al instante.

1. **Preparar** (botón «Preparar siguiente» o tecla P): la escena arranca sin proyectar nada. Escritorio saca la foto
   limpia y segmenta todo; Sistema solar empieza a rastrear los platos; Laboratorio carga su montaje y empieza a leer
   tapas, vaso y pipeta. Con «Mesa lista» el controlador lanza la escena automáticamente cuando aparece la señal de preparación.
   El modo manual espera «Dale».
2. **Dale** (botón o tecla D / Enter): animación de arranque (el logotipo WUI se forma en 1,72 s y se recoge en una firma fija en la esquina superior izquierda) y la escena aparece ya armada.
3. **Terminar escena** (botón o tecla T): apaga todo y limpia la proyección, para cambiar los objetos de la mesa.

Control: `doppler run -p platanus-hack-26 -c dev -- ../sl-scanner/.venv/bin/python mesa_ctl.py` y el dashboard en
`http://127.0.0.1:9300/dashboard.html`. Teclas E / S / L preparan una escena específica sin seguir el orden.

Guion sugerido: con la mesa de escritorio puesta, Preparar y Dale. Terminar, cambiar a los platos, Preparar (ya rastrea)
y Dale. Terminar, montar el laboratorio, Preparar y Dale.

Detalle de cada escena: [ESCRITORIO.md](ESCRITORIO.md), [SOLAR.md](SOLAR.md), [LAB.md](LAB.md).

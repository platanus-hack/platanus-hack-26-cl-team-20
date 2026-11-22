# Escena 1 · Escritorio

Primera escena lista de WUI sobre la mesa final (1406 × 706 mm). La mesa reconoce lo que hay encima y proyecta una
interfaz sobre cada objeto. Capturas en `capturas/escritorio-0515-*.jpg`.

## Encender y apagar

La mesa parte apagada. El control es `mesa_ctl.py` (servicio local en `:9301`):

```
doppler run -p platanus-hack-26 -c dev -- ../sl-scanner/.venv/bin/python mesa_ctl.py
```

Con el dashboard abierto (`http://127.0.0.1:9300/dashboard.html`): tecla **E** enciende Escritorio, tecla **X** apaga
todo. También `curl http://127.0.0.1:9301/start` y `/stop`.

Antes, una vez por montaje: `calibrar.py` (cámara ↔ proyector, mm reales y luz estructurada, ~2 min). La pestaña
`/mesa` de la consola debe quedar como pestaña activa de su ventana: en segundo plano Chrome frena las manos a 1 cuadro/s.

## Qué hace cada objeto

| Objeto | Qué se proyecta |
|---|---|
| MacBook | «Aprobar» y «Rechazar» a los lados del trackpad, luz sobre las teclas, la pregunta del agente y una línea hacia el panel «Tus agentes». |
| Libreta | Una página con las instrucciones y el lienzo del gato; la otra con un menú para encerrar una opción con lápiz. Botón «Comenzar juego» del lado de la persona. |
| Lata | Calco de su gráfica que se enciende en verde por oleadas y orbes en órbita pegada a ella. |
| Taza | Contorno limpio. |
| Mouse | Zonas de botones y rueda, y un menú radial (Copiar, Pegar, Deshacer, Captura). |
| Teléfono | Aviso «Batería 23 % · conéctalo al cargador» que titila y recorrido ortogonal hasta el cargador. |
| Plátano | Brillo amarillo que respira y el logo de Platanus envuelto en su curva. |
| Espacio libre | Silueta de una mano: al posar la mano graba una nota de voz. |

## Piezas

| Archivo | Rol |
|---|---|
| `mesa_ctl.py` | Enciende y apaga la escena (teclas del dashboard). |
| `escritorio_auto.py` | Detecta al encender (foto limpia) y vuelve a detectar cuando un objeto se mueve, sin apagar la proyección. |
| `pipeline.py` | Foto 4K → vista rectificada en mm → Grounding DINO → SAM por recorte → `out/objetos.json`. |
| `altura.py` | Encaje de objetos con altura medido con luz estructurada (`afin_sl`, `poli_sl`). |
| `escritorio.py` | Compone la escena: capas, colisiones, rutas ortogonales, capas por objeto. Escribe `out/zonas.json`. |
| `manos_capa.py` | Cursores de las dos manos, resaltado de objetos y zonas sensibles (posar la mano 0,2 s). |
| `interaccion.py` | Reacciones a los toques: respuesta del agente, inicio del gato, nota de voz (ElevenLabs). |
| `gato.py`, `opciones.py` | Gato y menú de la libreta, leídos con Jev. El gato no consulta a Jev hasta «Comenzar juego». |

## Decisiones que importan

- **Sin parpadeo.** Nada apaga la proyección para detectar. La escena nueva entra con otro id de widget y recién
  entonces sale la vieja (`ESC_ID`, `out/esc-id.txt`). El motor Rust debe estar detenido: tiene su propio apagón.
- **Rearmado solo con manos.** Una revisión se arma después de un movimiento grande (una mano que entra) y espera a
  que la mano salga. Las animaciones propias no la disparan.
- **Máscaras estables.** Lo que no se movió conserva su máscara anterior, tomada en limpio; solo lo movido o nuevo usa
  la detección hecha con la luz encendida.
- **Encaje medido, no estimado.** Para taza, lata, mouse y plátano el escaneo de luz estructurada dice a dónde apuntar
  el proyector para cada punto del objeto. Vale mientras el objeto no se mueva; al moverlo se usa una estimación hasta
  el próximo escaneo (`calibrar.py`).
- **Teclado detectado.** El bloque de teclas negras del MacBook se detecta en la foto y de él salen el giro, el cuerpo
  y el trackpad. Si no se encuentra, se estima desde la huella.
- **Objetos curvos.** Para el plátano SAM parte de su zona amarilla (el centro de su caja cae fuera de él) y el filtro
  de forma es más permisivo.

## Pendiente

- Probar con manos reales: zonas sensibles, dos manos a la vez y la nota de voz de punta a punta.
- El cable del cargador se detecta como una mancha grande.
- Al mover taza, lata o plátano el encaje exacto se pierde hasta el próximo escaneo.

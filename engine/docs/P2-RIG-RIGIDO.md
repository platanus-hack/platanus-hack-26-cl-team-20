# Rig rígido: Osmo y T749 se mueven juntos

`autocalib --motion rigid --views 3` mantiene una sola óptica por dispositivo y
una sola transformación Osmo→T749. No reutiliza K de una sesión anterior. El zoom
actual debe permanecer fijo durante todas las vistas y el scan posterior.
El montaje debe fijar también la **orientación óptica**: si el gimbal cambia la
orientación de la Osmo respecto al proyector al moverlo, este modelo deja de ser
válido aunque ambos estén sujetos a la misma base. Comprobar esa condición antes
de iniciar; el código no bloquea ni verifica el gimbal.

## Qué hace Max

1. Dejar mesa, teclado, libros, taza y cuadros quietos, con textura visible y
   distintas profundidades. Elegir un encuadre común a las tres posiciones.
2. Capturar la primera posición con todo el conjunto quieto.
3. Al aviso, mover **cámara y proyector juntos** unos 20–30 cm hacia un lado de
   la posición inicial. Variar altura 5–10 cm y apuntar al mismo grupo de objetos
   (aproximadamente 10–15°). Dejar quieto y confirmar.
4. Al segundo aviso, llevar el conjunto unos 20–30 cm al otro lado de la posición
   inicial: aproximadamente 40–60 cm desde la segunda posición. Variar algo la
   altura y apuntar a los mismos objetos. Esta tercera posición queda como
   posición de trabajo. Confirmar y no mover después del ajuste.

Las distancias son una guía de diversidad geométrica, no una medida usada por
el solver. Mantener la mayor parte de la escena común; las tres vistas deben
ver los mismos objetos iluminados. Un desplazamiento pequeño, solo giro, escena
plana o falta de textura puede dejar el ajuste insuficientemente condicionado.
Si la escena se mueve o cambia la relación cámara–proyector, repetir la sesión.
H3 controla el hardware y solicita cada confirmación; este desarrollo no lo abre.

## Contrato y geometría

```sh
python -m slscan autocalib SESION --out SESION/rig.json \
  --display-confirmed --motion rigid --views 3 \
  --scale teclas:MEDIDA_MM --scale-pixels x1,y1,x2,y2
```

Medir un segmento real largo y reconocer sus extremos en la foto de la primera
vista. `teclas` no infiere un tamaño estándar. Alternativamente `huincha:MM` sin
extremos significa distancia entre centros ópticos, no distancia entre carcasas.
La escala y su incertidumbre son independientes de la estimación óptica.

El evento de movimiento conserva `step=move_camera` para el wrapper existente,
pero agrega `motion=rigid` y `msg` con la instrucción completa. **Narrar `msg`**;
no usar una frase fija que indique mover solo la cámara. Espera `continue\n` en
stdin. EOF cancela y cierra ambos dispositivos. Se guarda una foto uniforme por
vista además del mapa Gray. Estas fotos son necesarias para el replay rígido.

Para cada pose `j`, con transformaciones mundo→dispositivo:

```
R_proj_j = R_camera_to_projector @ R_camera_j
t_proj_j = R_camera_to_projector @ t_camera_j + t_camera_to_projector
```

El ajuste usa todos los puntos de cámara y proyector de todas las vistas, comparte
intrínsecos, estima XYZ y poses, fija cámara 0 y norma del baseline. La observabilidad
excluye los priors. Reserva 20% de tracks y comprueba reproyección **también en
los proyectores de las demás vistas**, no solo en cámaras. No sustituye el
seguimiento por códigos Gray compartidos: al mover el proyector esos códigos
iluminan superficies diferentes.

`osmo` y el único `proj` publicado corresponden a la última posición. El mundo
sigue siendo `osmo_reference`, primera vista; las poses intermedias son auxiliares.
P6 recibe `slscan-rig/2`, mm, hashes de fotos y mapas, `metric_status=unverified`.
No se publica si falla rango, convergencia, profundidad, relieve o reproyección.

## Una sola vista

**Una longitud medida solo elimina el gauge de escala.** No agrega suficientes
restricciones para resolver simultáneamente focales, centros principales y
distorsiones libres de este modelo. Un plano sin dimensiones conocidas tampoco.
Detectar un teclado no determina su tamaño, planitud ni tolerancias.

Una alternativa operativa de una vista es disponer de **intrínsecos independientes
recién calibrados de ambos dispositivos al zoom y modo actuales**, más una escala
externa. Con correspondencias no degeneradas se estima pose relativa y profundidad.
La K anterior al cambio de zoom no sirve como esa restricción.

Un rectángulo de dimensiones conocidas podría aportar restricciones métricas si
se identifican sus esquinas y se reduce el modelo óptico (por ejemplo centro
principal y distorsión conocidos). No vuelve observable automáticamente el
modelo completo implementado aquí; un único plano aporta una homografía por
dispositivo. Usar un teclado así exige medidas reales y verificar ese modelo.
Este cambio implementa el camino de varias posiciones, no un calibrador de teclado.

Referencia: las restricciones de movimiento rígido, intrínsecos y escena aportan
información distinta; hay movimientos críticos que conservan ambigüedades
([Zisserman et al., 1998](https://robots.ox.ac.uk/~vgg/publications/1998/Zisserman98/)).
Las restricciones geométricas de planos conocidos requieren información adicional
de la escena ([Liebowitz, 2001](https://robots.ox.ac.uk/~vgg/publications/2001/Liebowitz01/)).

## Replay reproducible

```sh
python -m slscan autocalib-simulate SESION --motion rigid --camera-scale .5
python -m slscan autocalib SESION --replay --out SESION/rig.json \
  --scale huincha:302.24162519325414 --scale-uncertainty-mm 0
python -m slscan.rigid_validation SESION --rig SESION/rig.json
```

Sala amueblada raycast, con texturas procedurales estables en superficies para
evaluar seguimiento. Las texturas son una condición favorable explícita; no
demuestran que la sala física tenga detalle suficiente. Los patrones Gray se
renderizan, escriben, vuelven a leer, decodifican y guardan como NPZ. Se conservan
las tres fotos uniformes; las 132 imágenes Gray temporales se eliminan sin
`--keep-images`. La verdad va en un JSON separado que el estimador no lee.
La escala exacta del ejemplo es sintética; no acredita una medición con huincha.

Resultado a 540×960: 82 tracks físicos, 65 ajustados y 17 reservados; rango
27/27, 98 evaluaciones, RMS cámara 0,265 px y proyector agregado 0,716 px.
Contra las intersecciones verdaderas de cámara 0, sin alineación libre: XYZ
reservado RMS **10,53 mm**, p95 **14,11 mm**, máximo **14,66 mm**. El centro de
la cámara final queda a 1,27 mm y su orientación a 0,041° de la verdad.
**No cumple ≤2 mm**, incluso usando baseline sintético exacto. Estos valores
miden este escenario favorable, no el hardware. El evaluador separado lee la
verdad solamente después de que existe `rig.json`.

Ensayo nativo 1080×1920: 99 tracks, 79 ajustados y 20 reservados; rango 27/27,
RMS cámara 0,273 px/proyector 0,408 px. XYZ reservado RMS 11,90 mm,
p95 13,73 mm, máximo 13,74 mm. Tampoco cumple 2 mm.

Regresiones: 18 pruebas de calibración/replay pasan; 15 pruebas de solver y
adaptador pasan tras agregar el rechazo de rig inmóvil. El resto de la suite:
279 pasan, 3 omitidas; falla `test_mcp_server_starts_runtime_and_lists_tools`
porque el puerto 8795 está ocupado por otro proceso (`address already in use`).
No se detuvo el servicio ajeno para forzar ese test.

La precisión física ≤2 mm sigue pendiente de controles externos, repetibilidad y
estabilidad del conjunto. Ni un RMS bajo ni rango numérico completo la certifican.

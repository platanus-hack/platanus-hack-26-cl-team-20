# Tela viva · h13

`tela_viva` proyecta la foto real del cuadro sobre una tela simulada. Una cara,
mano o esfera oculta empuja desde atrás. Los bordes y agujeros se fijan a la
máscara de píxeles verificada del bundle de h12. Nunca usa el quad del runtime.

Activar por la API local del playground:

```json
{"cmd":"spawn","args":{"effect":"tela_viva","target":"cuadro","params":{"forma":"cara","fuerza":1,"velocidad":1,"sombreado":1,"rigidez":0.65,"audio":"bass"}}}
```

El resultado devuelve un `id`. Para editar o retirar **esa instancia**:

```json
{"cmd":"set_param","args":{"id":"ID_DEVUELTO","key":"forma","value":"mano"}}
{"cmd":"kill","args":{"id":"ID_DEVUELTO"}}
```

| Parámetro | Rango / comportamiento |
|---|---|
| `forma` | `cara`, `mano`, `esfera` |
| `fuerza` | 0–2; 1 por defecto. 0 retira el cuerpo y deja relajar la tela. |
| `velocidad` | 0–3; 1 por defecto. 0 detiene el movimiento del cuerpo; la tela sigue resolviendo fuerzas. |
| `sombreado` | 0–2; 1 por defecto. Intensidad del relieve por normales. |
| `rigidez` | 0–1; 0.65 por defecto. Dureza de los enlaces de tela. |
| `audio` | `""` apagado; `"bass"` empuja según el bajo. También acepta los canales del playground. |
| `audio_gain` | 0–4; 1 por defecto. Ganancia del empuje por audio. |
| `capa` | 10 por defecto. Reemplaza los píxeles de su máscara para poder producir sombras sobre capas previas. |
| `bundle` | Opcional: ruta al `trace.json` de h12. |

La textura se resuelve mediante `playground.appearance.texture_path`, el mismo provider
que usan `scene_texture` y los vectores. Cuando hay catálogo preparado, el bundle viene
del `vector_bundle` del objeto y corresponde a la apariencia limpia. Sin catálogo se
conserva el fallback `reports/vectortrace/<nombre-del-scan>/cuadro/trace.json` vinculado
a `scene_pov.png`. Se verifican hashes de foto y máscara, target, dimensiones
y origen. Solo acepta escenas `legacy` en píxeles nativos. Si hay un ajuste del
objeto, hay que preparar una máscara alineada nuevamente. Una foto/máscara ausente
o inválida produce `effect_error`; no inventa un contorno alternativo.

La tela usa posiciones XYZ, velocidades amortiguadas, resortes de retorno,
restricciones PBD estructurales y diagonales y colisión unilateral con el cuerpo.
Paso fijo de 1/60 s, hasta dos subpasos por frame para evitar acumular trabajo
cuando la pantalla se atrasa; el reloj del cuerpo conserva el tiempo descartado.
El cuerpo se retira cada
6.25 s a velocidad 1; el bass agrega penetración, no solo brillo. Las normales
salen de la malla deformada. Se invierte el desplazamiento proyectado para
transportar UV de la textura resuelta por el provider. `texture_source` en el estado
del efecto muestra la ruta realmente utilizada.

El render es una aproximación 2.5D para una superficie plana: no resuelve
autocolisiones, desgarros, pliegues que se cruzan ni sombras físicas del ambiente.
La malla tiene hasta 65×65 partículas y el sombreado/UV hasta 512 px en su lado
mayor; se amplía a la resolución del proyector y se aplica la máscara exacta al
final. La franja exterior conserva la foto original. No necesita GPU, cámaras,
modelos descargados ni dependencias nuevas.

Pruebas: `python -m pytest tests/test_tela_viva.py -q`. Cubren colisiones,
bordes fijos, agujeros, no fuga fuera de máscara, foto en reposo, audio geométrico,
retirada con inercia, paso fijo, parámetros extremos, datos obsoletos e integración
con el runtime. Las capturas y mediciones del cuadro real se guardan localmente en
`reports/cloth/`; no se incluyen fotos privadas en Git. Una captura del runtime
verifica la salida digital, no acredita la ilusión ni la alineación física vistas
desde la posición del espectador.

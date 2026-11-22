# WUI Card

Tarjeta rígida de 200 × 150 × 1,6 mm pensada para imprimirse en 3D y usarse como ancla visual, calibrador físico y lienzo para proyección. El wordmark atraviesa la tarjeta como stencil, cuatro markers esquineros dan referencia al pipeline de visión, y tres huecos reservados reciben información proyectada.

## Qué contiene

| Elemento | Dónde | Para qué |
| --- | --- | --- |
| Wordmark WUI cut-out | centrado arriba, 120 mm de ancho | lectura humana inmediata; ancla el lado largo de la tarjeta |
| Marker Punto | esquina superior izquierda | identificación de corner + anclaje CV |
| Marker Línea | esquina superior derecha | identificación de corner + anclaje CV |
| Marker Arco | esquina inferior izquierda | identificación de corner + anclaje CV |
| Marker Contorno | esquina inferior derecha | identificación de corner + anclaje CV |
| Ventana batería | centro superior-medio | el proyector pinta el nivel |
| Ventana notificación | medio-izquierda | el proyector pinta notificaciones breves |
| Ventana tiempo | medio-derecha | el proyector pinta hora o temporizador |

Las dimensiones físicas de la tarjeta (200 × 150 mm) son escala conocida y constante en el mundo. Junto con las cuatro posiciones de los markers esquineros alcanzan para resolver `cv2.solvePnP` sin necesidad de un tablero de calibración adicional.

## Markers sin islas

Un marker ArUco o AprilTag estándar deja regiones negras aisladas al cortarlo en stencil: cada cuadrito interior es una isla flotante. La tarjeta evita ese problema cambiando el esquema de codificación: en lugar de un patrón de bits internos, cada esquina tiene una **forma única** sacada de la lámina 06 del manual WUI.

- **Punto (TL).** Un círculo central de 10 mm con cuatro satélites de 3 mm en N, S, E, O a 8 mm del centro. Como los círculos no particionan el plano, nunca quedan islas.
- **Línea (TR).** Tres barras horizontales de 14 × 1,5 mm separadas 4 mm. Material de tarjeta a ambos lados de cada barra se queda conectado.
- **Arco (BL).** Una media luna de radio exterior 9 mm y radio interior 6 mm, abierta hacia arriba. La apertura evita atrapar material en el centro.
- **Contorno (BR).** Un bracket cuadrado 16 × 16 mm con grosor 2 mm y una abertura de 4 mm hacia la derecha. La abertura conecta el interior con el exterior.

El detector CV aprovecha que cada esquina tiene un fingerprint geométrico propio. Flujo sugerido en el pipeline:

1. Umbralizar la imagen y segmentar los cuatro agujeros más grandes que quedan dentro de la bounding box de la tarjeta.
2. Clasificar cada uno por momentos de Hu o por un match contra un template de 64 × 64 px.
3. Resolver qué esquina es cuál y sacar sus centroides.
4. `cv2.solvePnP` sobre las cuatro posiciones conocidas de los centroides (`(18, 18)`, `(182, 18)`, `(18, 132)`, `(182, 132)` en milímetros de tarjeta) → matriz extrínseca.

## Qué dejamos abierto

- El wordmark cortado deja un punto (de la i) separado visualmente del asta. Como el punto está rodeado por material a 180° en los otros tres lados (arriba, izquierda, derecha, más la franja de 7 mm entre punto y asta), sigue conectado a la estructura de la tarjeta y no forma isla.
- Las ventanas de información son placeholders. Dimensiones iniciales: batería 24 × 12, notificación ⌀14, tiempo 30 × 10. Si una experiencia pide más ventanas, se agregan al archivo `.scad` sin romper la calibración (los markers esquineros permanecen).
- Si una esquina queda en sombra durante la demo (mano, taza sobre la mesa), el pipeline puede caer a tres markers y seguir resolviendo PnP con un supuesto de planitud. Documentar ese fallback cuando exista.

## Impresión

- Material recomendado: PLA mate negro o PETG blanco. Si se imprime negro, pintar los huecos con un reverso claro (hoja Niebla pegada por detrás) para que el proyector siga pintando ventanas visibles. Si se imprime blanco, los huecos son pasantes y se ven directamente contra la mesa.
- Alturas de capa: 0,16–0,20 mm. Rellenos: 20 % rectilíneo. Sin soportes (tarjeta plana).
- Primera capa a 0,24 mm con `elephant foot compensation` 0,1 mm para que los markers no crezcan.
- Si el filamento es translúcido, agregar una capa de papel Niebla detrás para preservar el contraste.

## Archivos

- `card.scad` · modelo OpenSCAD paramétrico. Variables arriba del archivo.
- `card-plan.svg` · vista superior a escala 1:1 para revisar antes de imprimir. Niebla es material; Tinta son los cortes.
- Pendiente: STL generado desde OpenSCAD cuando esté el CLI disponible o se abra el archivo en la app.

## Cómo generar el STL

```
openscad -o card.stl card.scad
```

Si no hay CLI, abrir `card.scad` en OpenSCAD.app, `F5` para previsualizar, `F6` para renderizar, y `File → Export → Export as STL…`.

# Soporte Waveshare ESP32-S3-Touch-LCD-7 Rev 1.2 — NOTAS

Estado: **borrador sin verificar** (cotas marcadas abajo). Fuente de verdad: `waveshare-stand.scad`.

## Diseño
Dos escuadras laterales idénticas (**imprimir ×2**), una por cada par de pestañas M3 (izquierdo y derecho).
Cada escuadra: columna paralela a la pantalla, que se apoya en las dos pestañas mediante tacos, más la base en la mesa y un puntal trasero.
- Inclinación 18° desde la vertical (`tilt`) y canto inferior del vidrio a 25 mm de la mesa (`lift`), para alcanzar RESET/BOOT con el dedo desde abajo.
- La barra va a 15 mm de la espalda del chasis (`bar_z`). Entre los dos tacos queda un túnel libre: por ahí pasan los USB-C, que salen justo entre las pestañas de ese lado.
- El centro de la espalda queda abierto: microSD, batería PH2.0, JST, bornes, RESET/BOOT y la tira FPC quedan libres.
- Como las escuadras son independientes, la separación horizontal de 126,20 no influye en el calce. Solo importa la vertical (65,65), y los agujeros llevan ranura de ±1 mm por si acaso.
- Se imprime sin soportes: el perfil va plano sobre la cama (espesor 12 mm). Los agujeros quedan horizontales, con forma de gota y la punta hacia arriba.

## Medidas usadas
| Cota | Valor | Fuente |
|---|---|---|
| Vidrio | 192,96 × 110,76, R7,88 | PDF + PNG Rev1.2 |
| Vidrio → espalda chasis (TP+LCD) | 5,00 | PDF |
| Patrón M3 | 126,20 × 65,65 | PNG Rev1.2. En la foto real, la vertical ≈ 65,6 (escalada con el vidrio) |
| Canto inferior vidrio → M3 inferior | 21,58 | PDF girado 180° (el PDF está dado vuelta respecto al PNG). Foto real ≈ 21,9. **Sin verificar** |
| Canto izq. vidrio (vista trasera) → M3 izq. | 34,91 | 192,96 − 31,85 − 126,20 (PDF girado). **Sin verificar**, solo afecta al fantasma |
| Altura de la pestaña M3 sobre el chasis | 2,0 | **SUPUESTO**. Rev1.2 trae pestañas de chapa (foto), no los bosses de 8 mm del PDF antiguo. **MEDIR** |
| Chasis → PCB | 3,5 | PDF (8,50 − 5,00), revisión antigua. Borrador |
| PCB, USB-C | ~108,6 × 73,8, USB-C en u≈22 y 37 | escalado del PNG. Borrador, solo fantasma |
| Altura USB-C sobre la PCB | 3,25 | STEP (PCB antigua) |

**STEP:** contiene **solo la PCB de la revisión antigua** (106,01 × 73,01, 1,6 de espesor). No trae vidrio, chasis ni pestañas/insertos M3. Busqué los centros M3 en el STEP como pidió el líder: no existen ahí. Hay cilindros de r 1,2–1,7, pero son pads y vías de la PCB. De ahí solo saqué las alturas de componentes: USB-C +3,25 y conectores laterales +8,3 (en la Rev1.2 esos conectores van en otro lado).

## Tornillería
- 4× **M3×12 cabeza cilíndrica (DIN 912)**, entrando por atrás. Avellanado Ø6,5 y agujero pasante Ø3,6 (tolerancia 0,3 por lado).
- Bajo la cabeza quedan 9 mm de plástico, lo que deja ~3 mm de rosca en la pestaña. Si la pestaña es más alta o más baja, cambia `tab_h` (el asiento se recalcula solo) o el largo del tornillo (`screw_len`).

## Impresión
- `waveshare-stand-bracket.stl`: imprimir 2 veces, de lado, tal como sale. PLA, 0,2 mm, 3 perímetros, relleno 20 %.
- `waveshare-stand-test.stl`: **pieza de prueba**. Son los dos tacos completos (agujero ranurado y asiento de cabeza) unidos por una costilla de 5×5. Sirve para validar los 65,65, las ranuras, el asiento y el largo del tornillo antes de imprimir las escuadras.
- Huella de cada escuadra: ~117 × 107 mm, espesor 12. Las dos caben juntas en la cama de 256.

## Laminado (PrusaSlicer 2.7.4 CLI, perfil genérico: cama 256, PLA, 0,2 mm, boquilla 0,4, 3 perímetros, 20 %, sin soportes)
| Pieza | Tiempo estimado | Filamento |
|---|---|---|
| Escuadra ×1 | 1 h 37 min | 6,75 m ≈ 20 g |
| Escuadras ×2 (una placa) | 3 h 16 min | 13,5 m ≈ 40 g |
| Pieza de prueba | 34 min | 1,88 m ≈ 6 g |

Los tiempos son con velocidades genéricas de PrusaSlicer; en la A1, con el perfil de Bambu, deberían salir bastante menores. La pieza de prueba debería bajar de ~15 min, pero eso no está verificado. Los gramos están calculados desde los metros (PLA 1,24 g/cm³), porque el perfil genérico no los reporta.

## Archivos
- `render/vista-frente.png`, `render/vista-atras.png` y `render/vista-lado.png`: conjunto con el fantasma de la pantalla (vidrio, PCB aprox. y enchufes USB-C).
- `render/pieza-escuadra.png` y `render/pieza-prueba.png`: las piezas tal como van en la cama.
- Copia de STL, PNG, .scad y NOTAS en el MacBook: `~/Downloads/platanus-3d/waveshare-stand/`.

## Falta verificar (con pie de metro)
1. Altura real de la pestaña M3 sobre el chasis y profundidad de rosca: define `tab_h` y el largo del tornillo.
2. Que el vertical sea 65,65 y no 63,65 (la ranura de ±1 absorbe 65,65 ± 1; si fuera 63,65, cambia `m3_dy`).
3. Canto inferior del vidrio → M3 inferior (21,58): define la altura real del vidrio sobre la mesa.
4. Que el enchufe USB-C con su cable pase bajo la barra (túnel de 15 mm desde el chasis). Si un enchufe es muy grueso, sube `bar_z`.

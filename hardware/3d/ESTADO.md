# 3D — estado

Actualizado: vie 20-nov 22:20 (líder 3D). **Cerrado**: el equipo eligió el track Physical World (idea de Max); los cases bajan de prioridad. No se abren piezas nuevas.

| Pieza | Estado | Dónde |
|---|---|---|
| Badge: plantilla de calce | STL listo, lamina OK. 4 g; PrusaSlicer estima 36 min con velocidades Prusa por defecto, en A1 debería ser ~12–15 min | `badge-case/plantilla_calce.stl` · Mac `~/Downloads/platanus-3d/badge/` |
| Badge: bandeja v0 frente abierto | **borrador sin verificar**, STL listo, lamina OK sin soportes. 6 g; ~42 min (Prusa), ~15–20 min en A1 | `badge-case/bandeja_v0.stl` · ídem |
| Waveshare 7": soporte inclinado (2 escuadras + pieza de prueba) | **borrador sin verificar**, STL listos y laminados. Escuadras ×2: ~40 g, 3 h 16 min (Prusa genérico; menos en la A1). Prueba: 6 g, 34 min. Tornillos 4× M3×12 | `waveshare-stand/` (detalle en NOTAS.md) · Mac `~/Downloads/platanus-3d/waveshare-stand/` |

## Badge Plataboy 26

**El repo no está público.** `platanus-hack/plataboy26` da 404 (con y sin auth), igual que otras variantes; la búsqueda en GitHub no lo encuentra. Volver a mirar después de las 23:00.

**Medidas = estimadas por foto** (`badge-case/ref/frente.jpg`, `atras.jpg`), ±1,5 mm en XY, nada en Z. Todas en `badge-case/badge_params.scad`.
- Escala: cuerpo del botón 6×6 mm ≈ 16,3–16,7 px/mm. El módulo OLED da 27,4 mm, lo que cuadra con 27,3 (típico). La pila de 20 mm da ~32 mm de ancho, pero esa foto está movida y ladeada.
- Placa ≈ **34 × 46 mm**, esquinas R≈2,5.
- **3 agujeros, no 5:** dos arriba en las esquinas y uno abajo al centro. Los 4 «de esquina» del frente son del módulo OLED.
- El OLED sobresale ~1,8 mm sobre el borde de arriba. El interruptor queda en el borde izquierdo y el header en el derecho, ambos a 6–11 mm del borde superior.
- La pila va centrada atrás, a ~14 mm del borde inferior.
- Supuestas, sin medir: altura del portapilas (5 mm), espesor de la PCB (1,6), altura de los botones (5) y del vidrio OLED (9).

**Diseño v0:** bandeja trasera con el frente abierto, así que botones y OLED no dependen de Z. La placa entra a presión y la retienen 4 dientes. Tiene postes bajo los 3 agujeros (guía para M2 autorroscante opcional), ventana Ø23 para sacar la pila, cortes para el interruptor, el header y el OLED, y un ojal de lanyard abajo al centro, donde está el agujero original. Mide ~38,2 × 50,2 × 10,4 mm.

### Lo que necesito de Daniel (badge)
1. **Foto cenital, frente y atrás**, con la placa sobre una regla o una tarjeta bancaria (85,6 × 54 mm) en el mismo plano. Desde lejos y con zoom para que no se deforme. O imprime la plantilla de calce (8 min): pon el badge encima y saca la foto. Las reglas de 1 mm en los 4 lados corrigen la perspectiva.
2. **Con pie de metro**, si hay en la sede:
   - ancho × alto de la PCB
   - espesor de la PCB
   - del dorso del portapilas al vidrio del OLED (grosor total)
   - de la PCB a la punta de los botones
   - diámetro de los agujeros
3. **Hacia dónde sale la pila** del portapilas (¿se empuja de lado o se levanta?).
4. **URL exacta** de la serigrafía trasera, o los archivos del badge si están en el Discord.

```
   vista frontal, cotas a pedir
   ┌─────────────(A ancho)──────────────┐
   │ o                                o │  ← agujeros: Ø y distancia a los bordes
   │ [sw]   ┌──── OLED ────┐     [hdr] │
   │        └──────────────┘            │  (B alto)
   │     ▢          ▢  A                │
   │  ▢     ▢     B                     │
   │     ▢                              │
   │                o                   │
   └────────────────────────────────────┘
   lado:  [portapilas]▓PCB▓[botón]  [OLED vidrio]  → grosor total y altura de los botones
```

## Waveshare ESP32-S3-Touch-LCD-7 Rev 1.2

Fuentes oficiales en `waveshare-stand/ref/`: plano PNG Rev 1.2 (con y sin touch), PDF mecánico y **STEP oficial** (del zip `ESP32-S3-Touch-LCD-7in0_Drawing.zip` de la wiki).
- Vidrio 192,96 × 110,76, esquinas R7,88 (PDF). Área visible 154,88 × 86,72.
- Chasis metálico 165,72 × 97,60. Espesor TP+LCD = 5,00 (PDF).
- 4× M3: patrón 126,20 × 65,65 (PNG Rev 1.2; la foto real lo confirma ≈65,6). El STEP solo trae la PCB antigua, sin chasis ni M3. Los agujeros van ranurados ±1 mm.
- Por medir: altura de la pestaña M3 (supuesta 2 mm; define el largo del tornillo) y del canto inferior del vidrio al M3 inferior (21,58 según el PDF).
- En la Rev 1.2, los USB-C y la microSD quedan en el borde de la PCB que en vista trasera está a la izquierda, no abajo como decía el encargo. Manda el plano y la foto real.

## Impresoras y slicer
- A la vista en la sede (fotos en `_ref-impresoras/`): Bambu A1 (256³), Bambu A2L, Creality con gabinete y una Creality K-series.
- MacBook: PrusaSlicer 2.7.4 (sin perfiles Bambu; se usa para validar que laminen y estimar tiempo) y **Bambu Studio 2.09 instalado** (21:55) en /Applications/BambuStudio.app. Para imprimir: abrir el STL en Bambu Studio → impresora A1, PLA, 0,2 mm, sin soportes.

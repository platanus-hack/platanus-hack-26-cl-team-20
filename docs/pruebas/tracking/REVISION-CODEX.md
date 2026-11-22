**Los fallos más claros están alrededor de MobileSAM: captura sin sincronización, filtros destructivos, seguimiento sin histéresis y trabajo repetido.** No encontré un P0 ni evidencia suficiente para atribuirlo a una normalización incorrecta.

Revisión estática mediante `git show`; sin ejecutar modelos ni cámara. Referencias: **D** = `origin/daniel/consola`, **M** = `origin/max/motor`, **R** = `origin/manu/plataforma`.

1. **P1 — La foto “limpia” puede contener proyección anterior.**  
   **D:** `tools/mesa-agente/pintura.py:69–71`; `node/crates/pcx-core/src/tools.rs:133–140`. Se elimina solamente `pintura`, se esperan 0,7 s y `look` devuelve `latest`, sin exigir un timestamp posterior al apagado ni comprobar presentación. Otros widgets siguen visibles. `sam_seguir.py:21` ni siquiera apaga sus halos. **Arreglo:** apagón global confirmado por el renderer, esperar un cuadro nuevo y verificar un marcador óptico, como **M:** `engine/scripts/seguidor_v4.py:190–213`. Estáticamente se demuestra falta de garantía de frescura, **no una cola concreta del driver**. `ESCANEO-RAPIDO.md:34–40` documenta cuadros anteriores al cambio y latencia de ≈0,32 s.

2. **P1 — La caché de embeddings de v4 está rota.**  
   **M:** `engine/scripts/seguidor_v4.py:220–224`. `self._sam_img is not id(col)` compara identidad de objetos `int`, no sus valores: vuelve a ejecutar el encoder para cada caja. **Arreglo:** guardar la referencia y comparar `self._sam_image is not col`, o usar un identificador de captura; invalidarla cuando otro proceso de segmentación cambie el embedding. **R:** `node/crates/pcx-objects/src/tracker.rs:775–777` sí usa `sam_ready` por revisión. Este bug afecta a v4; `pintura.py:83` ya codifica una sola vez por foto.

3. **P1 — Los filtros eliminan precisamente objetos pequeños y partes delgadas.**  
   **D:** `tools/mesa-agente/pintura.py:78,86–90`, `sam_seguir.py:28,35–39`; **M:** `engine/scripts/seguidor_v4.py:225`. Se rechazan cajas de menos de 350/500 px² y contornos de menos de 200/300 px²; después hay apertura 3×3, o 5×5 en v4. Una cuchara puede perder el mango y quedar debajo del umbral. Elegir únicamente el contorno mayor descarta fragmentos restantes. **Arreglo:** quitar la apertura fija para objetos finos, filtrar componentes por evidencia temporal y escala física, y simplificar con tolerancia en mm. **R:** `node/crates/pcx-objects/src/tracker.rs:780` reproduce la apertura 5×5.

4. **P1 — No hay identidad estable ni tolerancia a una detección perdida.**  
   **D:** `tools/mesa-agente/sam_seguir.py:26,33,41–48`: los IDs `s{k}` dependen del ranking de confianza; cambiar el orden intercambia identidades. `pintura.py:83–93` reconstruye toda la escena sin conservar objetos anteriores. `seguir2.py:34–40` también desaparece con un solo fallo. **Arreglo:** asociación uno a uno por clase, distancia, IoU y apariencia; IDs persistentes y estados confirmado/temporalmente perdido, con varios fallos antes de retirar. **M:** `engine/scripts/seguidor_v4.py:476–478` y **R:** `node/crates/pcx-objects/src/tracker.rs:515–518` conservan el ocultamiento inmediato.

5. **P1 — El nivel 0 impide recuperar ciertos objetos pequeños u ocultos.**  
   **M:** `engine/scripts/seguidor_v4.py:421–426,625`: apertura 5×5 y retorno con menos de 500 píxeles cambiados, incluso existiendo objetos ocultos. Después `R=C` (`:614`) absorbe el cambio ignorado. La verificación periódica puede rescatarlos, pero por defecto espera 45 s (`:628`). **R:** `node/crates/pcx-objects/src/tracker.rs:329–344` hereda el retorno antes de buscar ocultos. **Arreglo:** comprobar cambios relativos por objeto y reintentar ocultos con temporizador independiente del área global cambiada.

6. **P1 — La conversión a mm omite la corrección de lente.**  
   **D:** `tools/mesa-agente/pintura.py:11–12`, `seguir2.py:10`, `homo.py:2–7`. Aplican directamente la homografía a píxeles distorsionados; `DY=-18` solo compensa una traslación. `lente.py:40–47` calcula coordenadas corregidas y guarda `K/dist`, pero estos consumidores no las usan. **Arreglo:** `undistortPoints(..., P=K)` → homografía ajustada con esos mismos puntos corregidos → mm. Recalcular también la homografía: corregir puntos y conservar una matriz ajustada sobre píxeles crudos mezcla espacios.

7. **P1 — Faltan deduplicación y exclusividad en la cascada DINO.**  
   **M:** `engine/scripts/detector_dino.py:32–38`, `seguidor_v4.py:379–395,454–458`. El servicio devuelve cajas sin NMS; dos cajas del mismo objeto pueden producir candidatos casi iguales y activar innecesariamente el LLM. Además, `libre` solo aporta una bonificación: no impide asignar la misma máscara a dos objetos. **R:** `node/crates/pcx-objects/src/onnx.rs:124–125`, `tracker.rs:779–789` mantiene ambos problemas. **Arreglo:** NMS por categoría canónica, deduplicación por IoU de máscaras y asignación global uno a uno con rechazo de regiones ya ocupadas.

8. **P2 — El remapeo de etiquetas DINO inventa correspondencias.**  
   **M:** `engine/scripts/detector_dino.py:35–37`. `max()` por palabras compartidas siempre asigna algún prompt, incluso sin coincidencias; artículos y adjetivos comunes generan empates. **Arreglo:** asignar usando spans de tokens y sus probabilidades, rechazando resultados sin correspondencia. **R:** `node/crates/pcx-objects/src/onnx.rs:61–107` ya utiliza spans. Los umbrales Python —caja configurable, texto 0,2— están conectados correctamente; bajarlos indiscriminadamente no arregla este error.

9. **P2 — Se fuerza un punto positivo no verificado y se ignora la calidad SAM.**  
   **D:** `tools/mesa-agente/pintura.py:85`, `sam_seguir.py:34`; **M:** `engine/scripts/seguidor_v4.py:384–393`. El centro de la caja puede caer fuera de una cuchara diagonal o entre partes del objeto. La IoU predicha se devuelve, pero no interviene en la aceptación. **Arreglo:** empezar con caja sola; añadir positivos provenientes de una máscara previa fiable. Usar IoU predicha y estabilidad para reintentar o conservar el track, evitando otro umbral duro de desaparición.

10. **P2 — Parte importante de los ~12 s está programada explícitamente.**  
    **D:** `tools/mesa-agente/pintura.py:95` añade **6 s** por defecto después de procesar. Cada `call()` crea un subprocess (`:10`) y `pcx.py:4–8` usa el gateway remoto por defecto. **M:** `engine/scripts/seguidor_v4.py:372–374` ejecuta DINO por prompt distinto. **Arreglo:** sustituir el sueño fijo por planificación por eventos, conexión local persistente y una inferencia DINO con todos los prompts necesarios. Eliminar esos 6 s mejora la frecuencia; no acelera la inferencia.

11. **P2 — Incompatibilidad RGB/BGR entre escaneo rápido y seguidor antiguo.**  
    **M:** `engine/scripts/escaneo_rapido.py:203,224–234` guarda `color` en BGR; `seguidor_objetos.py:220–221` lo interpreta como RGB, mientras el cuadro vivo usa BGR (`:250–252`). Cambian los pesos de gris y los gradientes del template. **Arreglo:** declarar el orden de canales en el formato y convertir consistentemente; para mapas del escaneo rápido, usar `COLOR_BGR2GRAY`.

**Sobre el contrato MobileSAM, no confundir ausencia de código externo con un bug:**

- **D/M:** `engine/playground/appearance_prepare.py:137–179` usa el export **Acly**, redimensiona el lado largo a 1024, convierte BGR→RGB y entrega HWC `float32` en 0–255. La normalización de píxel —mean `[123.675,116.28,103.53]`, std `[58.395,57.12,57.375]`— y padding abajo/derecha corresponden al encoder con preprocesado integrado. Los binarios externos no quedan verificados por esta lectura; no añadir normalización duplicada.
- Las cajas se escalan según las dimensiones redimensionadas y usan esquinas con labels **2/3**. No hay `-1`: debe añadirse únicamente si lo exige el export concreto, no por asumir que todos los exports comparten contrato.
- `mask_input` es cero `1×1×256×256`, `has_mask_input=0` y `orig_im_size=(H,W)`: correctos.
- Se carga `sam_mask_decoder_single.onnx`: tomar la máscara 0 es coherente con salida única. El umbral es **logit > 0**, correctamente. El tamaño original se delega al decoder; conviene validar su shape. Si se cambia a multimask, seleccionar por IoU predicha y restaurar tamaño antes de umbralizar. Rust reproduce este contrato en **R:** `node/crates/pcx-objects/src/onnx.rs:152–185`, `models.rs:53–58`.
- DINO Python delega el posproceso al processor con `(H,W)` (`detector_dino.py:30–31`); Rust convierte correctamente `cxcywh` normalizado a `xyxy` (`onnx.rs:115–120`).

**Las tres mejoras de mayor impacto:**

1. Captura limpia sincronizada y verificable, con timestamp y marcador óptico.
2. Tracks persistentes con histéresis, asociación exclusiva y posprocesado que conserve objetos finos.
3. Un embedding SAM y una inferencia DINO por captura cuando sean necesarios; corregir la caché, deduplicar candidatos y eliminar los 6 s de espera fija.
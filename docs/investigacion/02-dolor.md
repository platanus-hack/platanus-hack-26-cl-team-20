# 02 — ¿Qué dolor atacamos?

Tres candidatos evaluados con búsqueda web (20-nov-2026). Lo que no pude leer en la fuente original va marcado **[snippet]**. Lo que no encontré va como **sin confirmar**.

## (a) Guía de armado / mantención sobre el objeto real

| Dato | Cifra | Fuente |
|---|---|---|
| Proyección in-situ vs. instructivo con fotos (15 operarios con discapacidad cognitiva, taller protegido) | hasta **3× más rápido** y hasta **50 % menos errores** | [Funk et al., ASSETS 2015 (PDF)](https://www.medien.ifi.lmu.de/pubdb/publications/pub/funk2015usinginsitu/funk2015usinginsitu.pdf) |
| Chrysler + OPS Solutions 2014, ensayo del proveedor (n=10 operarios armando engranajes y cadenas) | errores **−80 %** (también reporta menos tiempo de ciclo y más throughput, pero las cifras no están en el texto legible; −38 % y +80 % sin confirmar) | [AREA](https://thearea.org/augmented-reality-lowers-errors-in-automotive-manufacturing/) · [AD&P](https://adandp.media/articles/projected-reality-delivers-real-benefits) |
| Luxottica con LightGuide (caso publicado por el proveedor) | errores de secuencia −19 %, capacitación −29 % | [casestudies.com](https://www.casestudies.com/company/lightguide/case-study/luxottica-customer-case-study) |
| Estudio académico de AR espacial (n=16) | la ganancia está en **errores, no en tiempo**; más útil en tareas difíciles | [Uva et al. 2018, preprint](https://iris.poliba.it/bitstream/11589/123984/10/Evaluating%20the%20effectiveness-pre-print.pdf) [snippet; % sin confirmar] |
| En contra: in-situ percibido como lento u obstrusivo por otro grupo de operarios | cualitativo | [Kosch et al. 2017](https://www.unibw.de/usable-security-and-privacy/publikationen/pdf/kosch2017ubicomp.pdf/@@download/file/kosch2017ubicomp.pdf) [snippet] |
| Chile: la gran minería necesita **>34.000 trabajadores a 2032** (los trabajadores en edad de retiro equivalen al 80 % de esa demanda); **8.876 mantenedores mecánicos** es el déficit más alto de la lista (luego 5.052 operadores de equipos móviles) | [DF, feb-2024, estudio CMM-Eleva](https://www.df.cl/empresas/mineria/mineras-necesitaran-mas-de-34-000-trabajadores-a-2032-por-jubilaciones-y) |
| EE.UU.: 3,8 M empleos en manufactura a 2033, de los cuales 1,9 M podrían quedar sin cubrir | [Deloitte/MI 2024](https://www2.deloitte.com/us/en/pages/about-deloitte/articles/press-releases/us-manufacturing-could-need-new-employees-by-2033.html) [snippet] |
| Costo de la mala calidad: ~15 % de las ventas (rango 5–35 %). Sin cifra aislada para el error humano en ensamblaje | [IISE](https://iise.org/details.aspx?id=22118) [snippet] |

**Costo hoy:** LightGuide y Arkite no publican precios, se cotizan ([G2/Arkite](https://www.g2.com/products/arkite/pricing)): **sin confirmar**. HoloLens 2 costaba USD 3.500 y está descontinuado; Dynamics 365 Guides se retira el 31-dic-2026 ([UploadVR](https://www.uploadvr.com/microsoft-discontinuing-hololens-2/), [ArborXR](https://arborxr.com/dynamics-hololens-replacement)). Las smart glasses cuestan USD 2.000–3.500 por equipo ([framesixty](https://framesixty.com/ar-for-manufacturing/), blog de proveedor). **Quién paga:** jefe de planta, gerente de calidad o de capacitación. Es una inferencia, sin fuente. Brecha de manufactura en Chile/Latam: **sin confirmar**.

## (b) Educación (sala, pupitre, liceo técnico)

| Dato | Cifra | Fuente |
|---|---|---|
| EMTP en Chile | **>170.000 alumnos**, 301 de 346 comunas, 35 especialidades; el plan Somos TP anuncia una nueva norma de equipamiento, sin montos | [Mineduc, ago-2026](https://www.mineduc.cl/ministra-maria-paz-arzola-presento-el-plan-somos-tp/) |
| Programa de equipamiento EMTP | ~$27.310 millones (presupuesto 2014) | [DIPRES](https://www.dipres.gob.cl/597/articles-141228_r_ejecutivo_institucional.pdf) [snippet] |
| Evidencia del análogo más cercano (pizarras interactivas, Londres) | "failed to find evidence of any impact … on attainment" | [Moss et al. 2007, RR816 (PDF)](https://warwick.ac.uk/fac/soc/ces/research/centre/teaching/thinkingaloud/interactivewhiteboards/rr816.pdf) |
| A favor | +16 percentiles, asociado al uso de pizarras | [ASCD](https://ascd.org/el/articles/teaching-with-interactive-whiteboards) [snippet] |
| Costo actual | panel interactivo de 75": USD ~1.900–2.400 con precio educacional, lista USD 3.155–5.333 | [BenQ, lista 2026](https://www.benq.com/content/dam/edu/en-us/aljp%20price%20list%20110525.pdf) [snippet] |

**Quién paga:** Mineduc, sostenedores o SLEP, con ciclos de compra largos (inferencia). La evidencia del análogo es **negativa**: la tecnología sola no mueve los resultados.

## (c) Escritorio de trabajo con agentes

| Dato | Cifra | Fuente |
|---|---|---|
| Cambio entre apps | ~1.200 veces al día; ~4 h a la semana reorientándose (~9 % de la jornada), n=137 en 3 empresas Fortune 500 | [HBR 2022](https://store.hbr.org/product/how-much-time-and-energy-do-we-waste-toggling-between-applications/H0777V) [snippet] |
| Interrupciones | una cada 2 min, 275 al día | [Microsoft WTI 2025](https://www.microsoft.com/en-us/worklab/work-trend-index/2025-the-year-the-frontier-firm-is-born) [snippet] |
| Retomar la tarea interrumpida | 23 min 15 s en promedio | [Gallup, entrevista a la investigadora](https://news.gallup.com/businessjournal/23146/too-many-interruptions-work.aspx) [snippet] |

**Problema:** hay evidencia de que el dolor existe, pero **ninguna** de que proyectar sobre el escritorio lo resuelva mejor que una pantalla. La solución actual es un monitor más o la app que ya existe, a costo casi cero. **Quién paga:** el propio trabajador o TI (inferencia).

## Brillo (objeción 3)

Los proyectores de menos de USD 100 miden **~40–274 lúmenes ANSI** reales, aunque anuncien miles (un AuKing que anuncia 9.500 midió 136) ([TechGearLab](https://techgearlab.com/reviews/small-and-home-office/projector/auking-mini-1080p), [SmartHomeHookup](https://www.thesmarthomehookup.com/test_install/best-projectors-under-100-2025-edition/)). Uno de ~50 lucas probablemente esté más abajo: 03 supone **50–150 ANSI** (estimación, sin confirmar para el modelo exacto). Cálculo propio (lux = lúmenes / m²): sobre un **A3 (0,125 m²)** eso son **~400–2.200 lux** según el proyector, y sobre 1 m² son ~50–270 lux. Una oficina tiene 300–500 lux (EN 12464-1, ver 03). Contraste sobre A3 con 400 lx: **~2:1 a 6:1**. Conclusión: **solo el área chica es viable**. Una estación A3 se ve con luces bajas, un escritorio entero queda lavado y una sala o pizarra no se ve. **Hay que medir el proyector real el sábado** (luxómetro del teléfono × área).

## Comparativa

| Dolor | Cifra clave + fuente | Costo solución actual | Quién paga | Prueba en vivo (5 min) | Brillo | Riesgo |
|---|---|---|---|---|---|---|
| **(a) Armado** | −80 % errores (Chrysler/OPS, n=10, ensayo del proveedor) · hasta −50 % errores y 3× velocidad (Funk 2015, n=15 con discapacidad cognitiva) | sistemas industriales cotizados (sin confirmar); HoloLens USD 3.500, descontinuado | planta o calidad (inferencia) | 2 personas del público, primer intento, mismo kit de ~10 pasos: una con instructivo en papel y otra con la guía proyectada. Se **cuentan los errores** y se cronometra | **Viable solo en A3 y con luces bajas** (~2:1–6:1 con luz de oficina); medir | precio de la competencia sin confirmar; el beneficio en tiempo es discutido; Kosch 2017 encontró rechazo de operarios con experiencia |
| (b) Educación | 0 impacto en notas (Moss 2007) · 170k alumnos EMTP (Mineduc) | panel 75" USD ~2–5k | Estado o sostenedor | alumno resuelve un ejercicio con y sin la superficie: el resultado es poco medible en 5 min | **Mal** en sala o pizarra, regular en pupitre | evidencia negativa; venta lenta |
| (c) Escritorio | ~1.200 cambios de app al día (HBR) | ~0 (monitor o app) | usuario o TI | tarea de oficina con y sin la superficie: ruidoso, gana la pantalla | **Regular**: superficie grande | no hay evidencia de que proyectar ayude; el jurado preguntará "¿por qué no una pantalla?" |

## Recomendación: (a), guía de armado proyectada que verifica cada paso

1. **Dolor:** errores de armado en novatos y una brecha real de técnicos (8.876 mantenedores mecánicos faltantes en la minería chilena a 2032). La demo muestra lo que cambia: el mismo kit armado por un novato con papel y con la guía, contando errores en vivo. Para la cifra de referencia (−80 %), aclarar que viene de un ensayo del proveedor con n=10.
2. **Foco:** una sola tarea, un kit de unos 10 pasos sobre una estación (por ejemplo, un ensamble de piezas o un conector). Nada de "engine para cualquier superficie".
3. **Brillo:** proyectar solo sobre la estación (~A3), con fondo negro y las luces del sector apagadas. Es el único de los tres candidatos que cabe en un área chica, y esa es la respuesta a la objeción.
4. **Lógica:** la cámara ve la pieza y su posición. El sistema concluye si el paso es correcto, si es la pieza equivocada o si falta algo. Su acción es avanzar, marcar en rojo y bloquear, o explicar con el agente LLM ("esa es la M4, va la M3"). Además registra el tiempo y los errores por operario.

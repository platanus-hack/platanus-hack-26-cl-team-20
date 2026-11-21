# 03 · Técnica: mesa proyector + cámara

Fecha: 2026-11-20. La mantención está consultada hoy en la API de GitHub (`pushed_at` / último release). En este archivo, **"propio"** significa estimación o cálculo mío, y **"s/c"** significa sin confirmar.

## 1. Calibración proyector-cámara

| Más simple | Ambiciosa | Riesgo |
|---|---|---|
| Proyectar ArUco/ChArUco a pantalla completa, detectarlo con `cv2.aruco.ArucoDetector` y aplicar `findHomography` + `warpPerspective`. Usa 1 frame; tarda segundos en correr y ~1–2 h en implementar (propio). Solo sirve para el plano de la mesa. [ChArUco docs](https://docs.opencv.org/4.x/d2/df7/charuco__detection_8markdown.html); OpenCV **5.1.0, 2026-11-06** ([release](https://github.com/opencv/opencv/releases/tag/5.1.0)) | Código Gray con `cv2.structured_light.GrayCodePattern`: la cantidad de patrones depende de la resolución ([tutorial](https://docs.opencv.org/4.x/db/d56/tutorial_capture_graycode_pattern.html)), ~44 imágenes para 1280×720 (propio). La calibración completa Moreno-Taubin está en [kamino410/procam-calibration](https://github.com/kamino410/procam-calibration) (MIT; mínimo 1 pose, recomienda >5). **Su último push es de 2022-03: está abandonado.** Tiempo: decenas de segundos por pose y horas de depuración (propio, s/c). opencv_contrib tiene push 2026-11-05 ([repo](https://github.com/opencv/opencv_contrib)) | Si se mueve el proyector o la cámara, hay que recalibrar (dejar una tecla para eso). Con luz ambiente y un proyector débil, el Gray code decodifica mal (propio). El keystone digital del proyector mete error (propio) |

## 2. Detección de toque con cámara RGB

MediaPipe Hand Landmarker (**v1.2.3, 2026-11-11**, [releases](https://github.com/google-ai-edge/mediapipe/releases)) entrega una z **relativa a la muñeca**, no a la mesa ([hands.md](https://github.com/google-ai-edge/mediapipe/blob/master/docs/solutions/hands.md)), así que **no sabe si el dedo toca**.

| Más simple | Ambiciosa | Riesgo |
|---|---|---|
| **Dwell o pinch**: punta del índice (landmark 8) → homografía. Toque = quedarse quieto 300–800 ms (umbral propio, s/c) o juntar pulgar e índice. Variante intermedia: un **teléfono lateral rasante**, donde toca si la punta queda a menos de N px de la línea de la mesa (propio; no encontré paper con RGB rasante, s/c) | **Sombra del dedo**: dedo y sombra convergen al tocar (Wilson, *PlayAnywhere*, UIST'05, [MSR](https://www.microsoft.com/en-us/research/?p=304277)). La patente da ~3–4 mm de precisión ([US8611667](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/8611667)). Otras referencias: ShadowTouch 99,1% con LED en la muñeca, citado en [EgoTouch](https://arxiv.org/html/2509.01786v1); PressureVision++ (RGB) 89,3% y EclipseTouch (IR) 98% ([tabla](https://arxiv.org/html/2509.03430v1)); DIRECT (con cámara de profundidad) >99% ([figlab](https://www.figlab.com/research/2016/direct)) | La sombra depende de la geometría y de la luz: VIRTOS advierte que separar mano y sombra cuesta con luz no controlada ([2014](https://www.scitepress.org/Papers/2014/47286/47286.pdf)). Con una sola cámara cenital hay falsos toques al flotar, así que la UI tiene que ser tolerante (botones grandes + confirmación) |

## 3. Segmentación, tracking y nombres de objetos

| Modelo | Velocidad reportada | Mantención |
|---|---|---|
| **YOLO26 / YOLOE-26** (Ultralytics; YOLOE acepta clases por texto, [docs](https://docs.ultralytics.com/models/yoloe)) | yolo26n **detección** en M4 Pro: 82,6 FPS (MPS) y 170,6 FPS (MLX) según [yolo-mlx](https://pypi.org/project/yolo-mlx/), paquete de terceros; el [blog de webAI](https://www.webai.com/blog/running-yolo26-natively-on-apple-silicon-with-mlx) da 124,9 FPS. Variante seg y CoreML en Mac: s/c ([docs CoreML](https://docs.ultralytics.com/integrations/coreml)) | **v8.4.216, 2026-11-20** ([releases](https://github.com/ultralytics/ultralytics/releases)); trae ByteTrack y BoT-SORT integrados ([track](https://docs.ultralytics.com/modes/track)) |
| SAM 3 / 3.1 (segmenta por texto + tracking) | ~30 ms/imagen en **H200**, 840M parámetros ([paper](https://arxiv.org/html/2511.16719v1)). En MacBook: s/c, probablemente inviable en tiempo real | Push 2026-11-17 ([repo](https://github.com/facebookresearch/sam3)) |
| **EdgeTAM** (tracking estilo SAM 2) | 16 FPS en iPhone 15 Pro Max ([CVPR'25](https://openaccess.thecvf.com/content/CVPR2025/html/Zhou_EdgeTAM_On-Device_Track_Anything_Model_CVPR_2025_paper.html)); disponible en [HF Transformers](https://huggingface.co/docs/transformers/model_doc/edgetam) | Push 2026-01-27 ([repo](https://github.com/facebookresearch/EdgeTAM)) |
| VLM para nombrar | Gemini devuelve `box_2d` [ymin,xmin,ymax,xmax] en 0–1000 ([docs](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/bounding-box-detection)). A Claude hay que pedirle **píxeles** [x1,y1,x2,y2] y reescalar a la imagen enviada ([docs](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates)). Latencia de 1–5 s (s/c) | APIs vigentes |

Descartados para el loop en vivo: EfficientTAM y SAM 2.1 (lentos en Mac, s/c), EdgeSAM/MobileSAM (click → máscara, solo si se necesita una máscara fina), Grounding DINO (165 ms en GPU L4), OWLv2, YOLO-World y FastSAM (repos estancados desde 2024–2025).

| Más simple | Ambiciosa | Riesgo |
|---|---|---|
| YOLO26-seg (o YOLOE-26 con texto) + ByteTrack para todos los frames. **El VLM se llama solo cuando aparece un objeto nuevo o quieto**, y sus cajas se asocian a los tracks por IoU | Las cajas del VLM se usan como prompt de EdgeTAM o SAM 3.1 para obtener máscaras finas | Los objetos de escritorio fuera de COCO obligan a usar YOLOE o el VLM. Además, la UI proyectada sobre el objeto cambia su apariencia y confunde al detector (propio) |

## 4. Latencia motion-to-photon en MacBook

| Etapa | Valor |
|---|---|
| Webcam USB | ~100 ms de cámara + USB, **medido en Linux** ([Open Robotics](https://discourse.openrobotics.org/t/where-does-latency-in-webrtc-video-streaming-come-from-an-analysis/54566)). **En macOS, la webcam en modo 4K mete 1–2 s** ([Ecamm](https://support.ecamm.com/en/articles/5704596-significant-delay-when-using-a-usb-webcam-in-4k-mode)) |
| Teléfono como cámara (Continuity / app por Wi-Fi) | s/c |
| MediaPipe Hand Landmarker | 17 ms CPU / 12 ms GPU en **Pixel 6** ([Google](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker)). En Mac: s/c |
| YOLO26n (detección) | ~6–12 ms en M4 Pro ([yolo-mlx](https://pypi.org/project/yolo-mlx/)); seg: s/c |
| Render + 1 frame a 60 Hz | 16–33 ms (propio) |
| Input lag de proyector mini barato | 26–46 ms medidos (AuKing: 45 ms) ([TechGearLab TMY](https://www.techgearlab.com/reviews/small-and-home-office/projector/tmy-mini), [AuKing](https://techgearlab.com/reviews/small-and-home-office/projector/auking-mini-1080p), [SmartHomeHookup](https://www.thesmarthomehookup.com/?p=1987)) |
| **Total** | **~120–200 ms** (suma propia, sin medir) |

**Literatura.** Las pantallas táctiles de la época rondaban ~100 ms (Dietz, MSR, vía [Tom's Hardware](https://www.tomshardware.com/news/microsoft-touch-screen-research,14984.html); el [video MSR](https://www.microsoft.com/en-us/research/video/applied-sciences-group-high-performance-touch) muestra la demo de 10 ms vs 1 ms). Los equipos comerciales llegaban hasta 125 ms, y el rendimiento al arrastrar mejora hasta los 10 ms ([Jota et al. CHI'13](https://dblp.uni-trier.de/rec/conf/chi/JotaNDW13.html)). En el *land-on* de un arrastre, la gente no distingue 1 de 64 ms ([Annett et al. GI'14](https://webdocs.cs.ualberta.ca/%7Ewfb/publications/C-2014-GI-Latency.pdf)). Deber et al. CHI'15 estudia los umbrales para tap vs. arrastre, pero no pude leer las cifras ([ref](https://bibtex.github.io/CHI-2015-DeberJFW.html)).

| Más simple | Ambiciosa | Riesgo |
|---|---|---|
| Webcam a 720p, buffer de 1 frame (leer siempre el último), interacciones de **tap** y botones grandes. **Que el tap funcione bien a ~150 ms es inferencia propia, s/c** | Captura nativa (AVFoundation) + modelos en CoreML/MLX + proyector "game mode" para acercarse a ~80 ms (propio, s/c) | Las colas cámara → Python → render pueden doblar la latencia. El arrastre y el dibujo se van a ver con efecto "goma elástica". Hay que medirlo filmando un cronómetro |

## 5. Contraste: proyector barato y luz de oficina

"LED lumens" no es una medida estándar: equivale a ANSI × 1,3–2,4 ([BenQ](https://www.benq.com/en-us/knowledge-center/knowledge/ansi-light-source-and-led-lumens-how-led-projectors-with-3000-lumens-are-a-marketing-ploy.html)). Ejemplos: un CocoBear de "4000 lm LED" declara 200 ANSI ([TINA](https://truthinadvertising.org/articles/what-you-should-know-about-home-projectors/)), y un Wanbo midió 422 de 900 prometidos ([TechRadar](https://www.techradar.com/televisions/projectors/if-this-cheap-portable-projector-is-as-impressively-bright-as-promised-it-could-be-a-serious-rival-to-xgimi-samsung-and-anker)). **Para uno de USD 50–60, supongan 50–150 ANSI (propio, s/c).** En oficina, la norma EN 12464-1 pide 500 lx en el puesto y 300 lx general ([Trilux](https://trilux.com/en/lighting-practice/indoor-lighting/specific-lighting-requirements/lighting-of-offices-and-vdu-workstations/illuminance), [AV.se](https://www.av.se/en/indoor-environment/light-and-lighting/office-lighting/)).

Cálculo propio sobre blanco mate: E_proy = lm / m², y contraste ≈ (E_proy + E_amb) / E_amb.

| ANSI | Área | E_proy | Con 400 lx | Con 100 lx (luces bajas) |
|---|---|---|---|---|
| 100 | A3 (0,125 m²) | 800 lx | **3:1** | 9:1 |
| 100 | A2 (0,25 m²) | 400 lx | 2:1 | 5:1 |
| 50 | A3 | 400 lx | 2:1 | 5:1 |
| 50 | A2 | 200 lx | **1,5:1** | 3:1 |

| Más simple | Ambiciosa | Riesgo |
|---|---|---|
| Área **A3** (rinde el doble que A2), proyector cerca, papel o cartulina blanco mate y luces del sector apagadas. UI con fondo negro, colores saturados (amarillo, cian, blanco), trazos ≥4–6 px y texto grande. Diseñar para ~800×480, porque el nativo real es probablemente 480p/720p (s/c) | Pantalla gris o ALR. El gris ayuda algo con luz moderada, pero sigue siendo difusa ([Draper](https://blog.draperinc.com/2018/03/alr-clearing-confusion/)). La ALR rechaza luz fuera del eje ([Draper](https://blog.draperinc.com/2019/06/white-paper-how-ambient-light-rejection-works/)), pero con luz y proyector ambos cenitales es dudoso que sirva (propio) | **Es el riesgo #1 de la demo.** Si no se pueden bajar las luces de la sala, la imagen se ve lavada. Llevar lámpara de escritorio o una caja/toldo negro |

## Plan mínimo para 30 horas

- **Calibración**: ArUco proyectado → `findHomography`, con una tecla para recalibrar. Nada de Gray code.
- **Manos**: MediaPipe Hand Landmarker + homografía de la punta del índice. **Toque = dwell o pinch.** Si sobra tiempo, un teléfono lateral rasante.
- **Objetos**: YOLO26-seg o YOLOE-26 + ByteTrack (Ultralytics, MPS/MLX), más Gemini o Claude **solo para nombrar** objetos nuevos. Evitar SAM 3, Grounding DINO, OWLv2 y FastSAM en el loop.
- **Cámara**: webcam USB a 720p (nunca 4K en macOS) con buffer de 1 frame. Teléfono solo como cámara secundaria.
- **Render**: fullscreen en el proyector, fondo negro, botones grandes, sin arrastre fino.
- **Día 1**: medir los lúmenes reales (luxómetro del teléfono: lux × m²) y la latencia total (filmar un cronómetro) antes de diseñar features.

## 3 riesgos principales

1. **Contraste**: con 50–100 ANSI y 400 lx queda en 1,5–3:1, y la demo se ve lavada. Mitigación: A3, luces bajas, fondo negro.
2. **Toque falso**: MediaPipe no sabe la altura del dedo. Mitigación: dwell/pinch o cámara lateral, y UI tolerante.
3. **Latencia y cadena de cámara**: ~120–200 ms, más el bug de 4K en macOS y las colas de Python. Mitigación: 720p, buffer de 1, solo taps.

---
id: 046
title: normales de personas en tiempo real (DAViD) y luces que salen de los dedos
kind: visual
from: h1 (Max por WhatsApp Notas, 17-nov 09:50)
status: done
created: 2026-11-17T09:55
---
Max mandó este reel y pidió «ver de implementar esto en el pipeline de video mapping»:
https://www.instagram.com/reel/Df0PNE-qf5t/ (cacheflowe, R&D en Hovercraft Studio).

**Qué muestra el reel**
- Modelo **DAViD de Microsoft**: estima profundidad, recorte (matte) y **normales** de personas en tiempo real desde
  una cámara común. Está entrenado con personas, así que la normal es muy detallada en cuerpo y cara.
- Con la normal, una imagen 2D se ilumina como si fuera 3D: luces direccionales sobre la persona.
- En el demo, cada dedo índice es un **foco**: la luz sale del dedo y alumbra la cara y la ropa según la normal.
  Shader en TouchDesigner; pose de manos con MediaPipe en ONNX; 60 fps en un portátil con 4090 (CUDA/TensorRT).

**Qué queremos en el playground**
1. Una capa nueva de la escena: para cada persona que ve la cámara, normal + profundidad + recorte, al ritmo de la
   cámara. Se parece a `depth_mono.py` (Depth Anything V2 sobre una foto), pero en vivo y con normales.
2. Un efecto `relight_people` que use esa normal: focos virtuales que salen de los dedos (la pose de manos ya existe
   en `hands/`), color y apertura por parámetro, y que pueda seguir el beat (`pg_beat`).
3. Dos salidas posibles, a probar las dos:
   - en pantalla: la imagen de la cámara reiluminada (como el reel);
   - proyectada sobre la persona: el proyector pinta la luz donde la normal dice que pega, calibrado con la
     cámara (como ya se hace con objetos).

**Antes de programar, verificar**
- Licencia de DAViD (pesos y código) para uso nuestro.
- Si corre en el Mac (MPS o CoreML) a una tasa usable, o si va a la torre Linux con GPU. El reel usa CUDA.
- Qué tamaño de modelo da 25 fps o más con la cámara actual.

Cuando haya una primera prueba, dejar un `pg_snapshot` y un video corto para que Max lo vea.

---
**Nota h17 (17-nov 11:00)** · primera versión lista, probada sin cámara ni proyector (video grabado). Detalle en
`playground/people/README.md`.

- **Licencia**: modelos y código de DAViD son **MIT** (uso comercial ok). El dataset es CDLA-2.0; no lo usamos.
- **Mac M4**: onnxruntime no sirve (~1 s por cuadro en CPU; su CoreML toma 244 de 850 nodos). Convertido acá a
  CoreML fp16 para el Neural Engine (error 0,1° contra el original): **normal sola a 384 px ≈ 40-57 ms (18-25 fps)**;
  normal + recorte ≈ 6-10 fps; las tres ≈ 4-5 fps. En vivo (normal cada cuadro, recorte cada 3, manos): ~10 fps.
- **25 fps con normal + recorte + profundidad pide GPU NVIDIA**: el PC NEW-WHITE-4K (RTX 4070), no la torre Linux
  (PC de 2013). Estaba apagado: **sin medir**; estimado ~22 fps con los tres modelos base o ~30 fps con el
  multitarea ViT-L en TensorRT fp16.
- **Hecho**: capa `playground/people/live.py` (proceso aparte: el runtime sigue a ~25 fps; fuentes `video:`,
  `url:` de la Brio, `cam:`), efecto **`relight_people`** con hot reload (el modelo no se recarga al guardar):
  focos en cada índice, color por mano, `apertura`, `radio`, `intensidad`, `beat`; salidas `pantalla` y `proyector`.
  CLI `python -m playground.people bench|export|offline`. 9 tests nuevos (`tests/test_people.py`).
- **Para ver**: `reports/david/046_antes_despues.mp4` (16 s, cámara | reiluminada), `046_capas.mp4` (cámara,
  normal, recorte, resultado), `046_proyector.mp4` (solo la luz) y `pg_snapshot_pantalla.png` (runtime propio en el
  puerto 8797). Video de prueba: Bharatanatyam, Wikimedia Commons, CC BY 3.0 (crédito en el video).
- **Pendiente con Max y el equipo**: probar con persona real y la Brio (`fuente url:…`); la salida `proyector` usa la
  homografía de la mesa (`brio_calib.json`), que para una persona de pie es aproximada (el snapshot de proyector sale
  deformado por eso): bien hecho pide la calibración procam con profundidad. Medir en la RTX 4070. Sin sombras
  proyectadas todavía.

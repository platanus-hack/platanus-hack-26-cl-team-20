# Personas: normal, recorte y luces desde los dedos (pedido 046)

DAViD (Microsoft, ICCV 2025) estima por cada persona que ve la cámara su **normal**, su **recorte** y su
**profundidad relativa**. Con la normal la imagen 2D se ilumina como si fuera 3D; cada dedo índice es un foco.

```
cámara / video ─▶ live.PeopleLayer (hilo)            ─▶ efecto relight_people (hot reload)
                    DAViD normal (cada cuadro)            dedos -> focos (relight.finger_lights)
                    DAViD recorte (cada N cuadros)        luz por píxel (relight.light_map, ~6 ms)
                    manos MediaPipe (hands_mp)            pantalla: cámara reiluminada | proyector: solo la luz
```

| Archivo | Qué hace |
|---|---|
| `david.py` | modelos (ONNX MIT en `/Volumes/Externo/hf-cache/david`), exportación a CoreML fp16, `David.infer`, `bench` |
| `relight.py` | sombreado: Lambert envolvente + Blinn, cono del foco, caída, destello en la punta; salidas pantalla/proyector |
| `hands_mp.py` | manos MediaPipe en modo VIDEO con EMA en las puntas |
| `live.py` | capa compartida por fuente (`video:` / `url:` / `cam:`); sobrevive al hot reload del efecto |
| `__main__.py` | `bench`, `export`, `offline` (antes/después en mp4) |
| `../effects/relight_people.py` | el efecto del playground |

## Licencia

Modelos y runtime de DAViD: **MIT** (README y model cards del repo `microsoft/DAViD`). El dataset SynthHuman es
CDLA-Permissive-2.0 (no lo usamos). Uso comercial permitido; mantener el aviso MIT si se redistribuyen los pesos.

## Velocidad medida (17-nov)

**Mac M4, Neural Engine (CoreML fp16, convertido acá; error vs. el ONNX 0,1°):**

| Modelo | 384 px | 512 px |
|---|---|---|
| normal sola | 40–57 ms (≈ 18–25 fps) | 63–65 ms (≈ 15 fps) |
| normal + recorte | 100–160 ms (≈ 6–10 fps) | 150–200 ms |
| las tres | 190–245 ms (≈ 4–5 fps) | ≈ 200–245 ms |

Los rangos dependen de qué más usa el Neural Engine en ese momento. onnxruntime en el Mac: CPU ~1 s por cuadro; su
proveedor CoreML toma 244 de 850 nodos y queda más lento. A 256 px la conversión fp16 se desvía 9°: no usar.

En vivo en el Mac (`live.py`): normal en cada cuadro + recorte cada 3 + manos ≈ 8–12 fps a 384 px. **25 fps en el
Mac solo con la normal**; con recorte y profundidad hace falta GPU NVIDIA.

**PC NEW-WHITE-4K (RTX 4070, torch CUDA ya instalado)** — la máquina con GPU del proyecto; la "torre Linux" es un
PC de 2013 y no sirve para esto. El 17-nov estaba apagado: **estimación sin medir**: con TensorRT fp16, base a
512 px ≈ 15 ms por modelo (normal + recorte + profundidad ≈ 45 ms, ≈ 22 fps) y el multitarea ViT-L (las tres en una
pasada, 390 GMAC) ≈ 30–35 ms (≈ 30 fps). El reel corre a 60 fps en una 4090 de portátil. Medir con `bench --backend
onnx --providers cuda` cuando esté prendido.

## Correr

```sh
PY=/Volumes/Externo/venvs/david/bin/python          # onnxruntime + coremltools + mediapipe (+ torch para exportar)
$PY -m playground.people export --tasks normal,alpha,depth --size 384     # una vez (~1-2 min por modelo)
$PY -m playground.people bench --tasks normal,alpha --size 384
$PY -m playground.people offline captures/david_test/bharatanatyam_960.mp4 --start 21 --dur 7 --out reports/david/a
```

El runtime del playground (`.venv`, ya con coremltools) usa los `.mlpackage` exportados: no necesita torch.

En el playground:

```
pg_spawn relight_people                                         # video de prueba en bucle, salida en pantalla
pg_set_param <id> fuente "url:http://192.168.1.233:8794/frame.jpg?w=960"   # la Brio del PC
pg_set_param <id> salida proyector                              # solo la luz, con captures/brio_calib.json
pg_set_param <id> color [255,120,40]   /  apertura 60  /  beat 0.6
```

## Límites y pendientes

- **Salida proyector**: usa la homografía Brio->proyector del plano de la mesa (`brio_calib.json`). Para una persona
  de pie delante de la mesa es aproximada (paralaje); bien hecho pide la calibración procam (Osmo + proyector) y
  llevar cada píxel con su profundidad. Sin probar con proyector ni persona real (encargo: sin hardware sin Max).
- Sin sombras proyectadas (la mano no tapa la luz sobre la cara). Siguiente paso: marcha de rayos corta en la
  profundidad.
- Suavizado temporal (EMA 0,35) deja una estela leve en manos rápidas.
- Las manos usan el índice; otros dedos: `which=(4, 8, 12, 16, 20)` en `finger_lights`.
- Video de prueba: "Double hand gestures lesson in Bharatanatyam dance (2019)", Wikimedia Commons, CC BY 3.0
  (`captures/david_test/`, fuera de git).

# Demo del techo (prototipo de hardware)

Demo en vivo hecha el sáb 21-nov con el proyector T749 + webcam C920 apuntando a un techo con viga, usando el stack Rust de pcx (`pcx-calib`, `pcx-renderer`, `pcx-shm`, `pcx-hands`).

- **Calibración** `pcx-calib` modo preciso (gray code + ArUco, patrón − inverso promediado por contraste bajo): 29,5 s, LUT 96 % de cobertura, **5 planos** detectados; error 0,8 px con homografía por plano (9,1 px con una sola).
- **Escenas en bucle (22 s)**: viga trazada con luz + etiqueta, ondas sobre la rejilla, barrido que revela los planos, foco que sigue la mano.
- **Revisado por Codex**: sin errores de ejes; 3 observaciones documentadas (escala mm inventada, interpolación junto a la viga, viga asumida entre planos 0 y 1).

Correr: `PCX_QA_DIR=<scripts de captura> ./start.sh` · detener: `./stop.sh`. La captura usa provisoriamente un lector AVFoundation propio (`c920cap`) porque `pcx-capture uvc` aún no abre la C920 en macOS (bug B6, ver `docs/pruebas/hardware-manu/01-smoke.md`). Evidencia: `docs/pruebas/hardware-manu/evidencia/`.

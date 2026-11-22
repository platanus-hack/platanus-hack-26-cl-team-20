# Dashboard del show

URL: http://127.0.0.1:8795 (una pestaña normal de Chrome; nada fullscreen, nada en el proyector).

Levantarlo, desde la raíz del repo:

    .venv/bin/python -m playground.dashboard          # --port 8795 por defecto

Qué hace:

- **Nueva vista**: lanza `python -m playground.dale` (opciones: vistas 1/2/3, escala `huincha:MM`, escena de efectos
  final, ensayo sintético, sin runtime) y muestra los pasos en vivo. Cuando el dale espera (proyector quieto o mover la
  cámara) aparece la instrucción grande y **Continuar** (crea `~/.local/state/sl-scanner/dale.continue`).
  **Cancelar** mata el dale y sus hijos. También sigue `dale.estado.json`, así ve un dale lanzado por la secretaria.
- **Efectos**: objetos del runtime (:8790) con sus efectos compatibles (prender, apagar, sliders y color en vivo),
  escenas del showcase y «Apagar todo». Si el runtime está apagado lo dice y sigue funcionando.
- Miniatura de lo proyectado (1 s) y el visor del pipeline P6 (:8796) en un iframe.

Ensayo sin hardware: marca «ensayo sintético» + «sin runtime» y aprieta Nueva vista, o por curl:

    curl -X POST -H 'Content-Type: application/json' -d '{"synthetic":true,"no_runtime":true}' http://127.0.0.1:8795/api/dale
    curl -N 'http://127.0.0.1:8795/api/dale/events?hasta_fin=1'

API: `POST /api/dale` · `POST /api/continuar` · `POST /api/cancelar` · `GET /api/dale` · `GET /api/dale/events` (SSE) ·
`GET /api/escena` · `POST /api/spawn|kill|param` · `GET|POST /api/showcase` · `GET /api/frame.png` · `GET /api/pipeline`.

# Eventos de pcx: referencia

Fuente: `contracts/src/pcx_contracts/events.py`. Cada elemento de `events_wait.events` es un sobre:

```json
{"type": "widget_pressed", "v": 2, "seq": 4182, "t_capture_ns": 1795231660101000000, "t_pub_ns": 1795231660119000000,
 "source": "core", "node_id": "mesa-1",
 "payload": {"type": "widget_pressed", "widget_id": "siguiente", "value": "next", "trigger": "dwell", "point_mm": [525, 362.5]}}
```

`seq` es monótono por `stream_id` y es el cursor. `t_capture_ns` es el cuadro de cámara que originó el evento (`null` si no viene de una cámara).

| `type` | Campos del `payload` | Cuándo |
|---|---|---|
| `object_appeared` | `object{id, label, confidence, polygon_mm, center_mm, bbox_mm, since_ns, last_seen_ns}` | Aparece un objeto |
| `object_moved` | `object_id`, `center_mm`, `polygon_mm?` | Un objeto cambió de lugar |
| `object_removed` | `object_id`, `label?` | Un objeto salió de la mesa |
| `touch_down` / `touch_move` / `touch_up` | `touch{id, phase, point_mm, widget_id?, object_id?, trigger}` | Toque crudo; el nodo completa `widget_id` y `object_id` bajo el punto |
| `widget_pressed` | `widget_id`, `value?`, `trigger`, `point_mm?` | Toque **confirmado** sobre un widget tocable |
| `dwell` | `point_mm`, `progress` (0..1), `widget_id?`, `object_id?` | Progreso de permanencia (muy frecuente) |
| `ask_answered` | `request_id`, `answer?`, `index?`, `timed_out` | Se respondió o venció un `ask` |
| `flow_state_changed` | `flow_id`, `request_id`, `from_state?`, `to_state?`, `cause?` | Un flujo cambió de estado; `to_state: null` = terminó |
| `flow_emitted` | `flow_id?`, `request_id?`, `event`, `data` | Un flujo emitió un evento propio (`emit`) |
| `calibration_drift` | `from_frame`, `to_frame`, `residual_mm`, `threshold_mm` | La verificación detectó que el rig se movió |
| `hands_gone` | `t_capture_ns`, `episode_start_ns?`, `last_hand_ns?` | Ya no hay manos sobre la mesa |
| `device_changed` | `device{id, kind, adapter, caps, status, meta}` | Un dispositivo se conectó o cambió |
| `confirmation_requested` | `confirmation_id`, `request_id`, `action_digest`, `summary`, `via`, `expires_ns` | El nodo espera que una persona confirme |
| `confirmation_resolved` | `confirmation_id`, `request_id`, `action_digest`, `approved`, `via` (`touch`, `console`, `timeout`) | La confirmación se resolvió |
| `home_call_done` | `request_id`, `confirmation_id?`, `status` (`done`, `failed`, `denied`, `expired`, `rejected`), `error?` | Desenlace de un `home` |
| `blanked` | `request_id`, `projector_id`, `t_start_ms`, `t_end_ms` | El proyector estuvo oscuro (foto limpia) |
| `frame_meta` | `camera_id`, `seq`, `t_capture_ns`, `w`, `h`, `segment?`, `format`, `stride?` | Cuadro nuevo en memoria compartida (no llega por `events_wait`: el nodo no lo guarda en el log) |

## Filtros útiles para `types`

| Quiero saber… | `types` |
|---|---|
| Qué botón tocaron | `["widget_pressed"]` |
| Si movieron o sacaron algo | `["object_appeared", "object_moved", "object_removed"]` |
| Cómo va un flujo | `["flow_state_changed", "flow_emitted"]` |
| Si se aprobó una acción de domótica | `["confirmation_resolved", "home_call_done"]` |
| Si hay que recalibrar | `["calibration_drift"]` |

Evita pedir `touch_move` y `dwell` salvo que vayas a dibujar algo con ellos: llegan decenas por segundo.

## Resultado de `events_wait`

```json
{"stream_id": "…", "cursor": 4182, "events": [ … ], "gap": null}
```

`gap{stream_id, from, to, reason}` aparece cuando el cursor pedido es anterior a lo que el nodo conserva (retención del log local: 100 000 eventos o 24 horas). Trátalo como «me perdí cosas»: relee `scene`.

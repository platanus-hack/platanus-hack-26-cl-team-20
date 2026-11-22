#!/usr/bin/env python3
"""Worker de percepción mínimo para pcx: publica un objeto por la ingesta ZeroMQ y escucha los eventos del nodo.

No lee cámaras: sirve de plantilla y de prueba de humo del bus. Con `pcx serve` corriendo:

    uv run --with pyzmq python worker_ejemplo.py                       # usa PCX_INGEST / PCX_EVENTS o los defaults
    uv run --with pyzmq python worker_ejemplo.py --id caja --x 300 --y 200

El core acepta el sobre (`Envelope` v2) en JSON o en msgpack con nombres de campo. Renumera `seq`: el que mandas
solo sirve para ti.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import zmq

WINDOWS = sys.platform.startswith("win")
INGEST = os.environ.get("PCX_INGEST") or ("tcp://127.0.0.1:9206" if WINDOWS else "ipc:///tmp/pcx/ingest.sock")
EVENTS = os.environ.get("PCX_EVENTS") or ("tcp://127.0.0.1:9207" if WINDOWS else "ipc:///tmp/pcx/events.sock")


def envelope(payload: dict, *, seq: int, source: str, node_id: str, t_capture_ns: int | None = None) -> bytes:
    """Sobre común: `type` debe ser igual a `payload.type` y `v` debe ser 2."""
    return json.dumps({
        "type": payload["type"], "v": 2, "seq": seq, "t_capture_ns": t_capture_ns, "t_pub_ns": time.time_ns(),
        "source": source, "node_id": node_id, "payload": payload,
    }).encode()


def object_appeared(oid: str, label: str, cx: float, cy: float, w: float, h: float) -> dict:
    now = time.time_ns()
    return {"type": "object_appeared", "object": {
        "id": oid, "label": label, "confidence": 0.9,
        "polygon_mm": [[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2], [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]],
        "center_mm": [cx, cy], "bbox_mm": [cx - w / 2, cy - h / 2, w, h],
        "since_ns": now, "last_seen_ns": now,
    }}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", default="caja")
    ap.add_argument("--label", default=None)
    ap.add_argument("--x", type=float, default=400)
    ap.add_argument("--y", type=float, default=250)
    ap.add_argument("--node-id", default=os.environ.get("PCX_NODE_ID", "mesa-1"))
    ap.add_argument("--listen-s", type=float, default=2.0, help="segundos escuchando el PUB de eventos")
    a = ap.parse_args()

    ctx = zmq.Context.instance()
    sub = ctx.socket(zmq.SUB)
    sub.connect(EVENTS)
    sub.setsockopt_string(zmq.SUBSCRIBE, "object_")  # prefijo del tipo de evento; "" = todos
    push = ctx.socket(zmq.PUSH)
    push.connect(INGEST)
    time.sleep(0.3)  # deja que conecten (PUB/SUB pierde lo anterior a la suscripción)

    t_capture = time.time_ns()  # en un worker real: el t_capture_ns del cuadro que originó la detección
    seq = 1
    push.send(envelope(object_appeared(a.id, a.label or a.id, a.x, a.y, 80, 80), seq=seq,
                       source="perception-ejemplo", node_id=a.node_id, t_capture_ns=t_capture))
    seq += 1
    push.send(envelope({"type": "object_moved", "object_id": a.id, "center_mm": [a.x + 40, a.y]}, seq=seq,
                       source="perception-ejemplo", node_id=a.node_id, t_capture_ns=time.time_ns()))
    print(f"enviado a {INGEST}: object_appeared + object_moved de {a.id!r}")

    poller = zmq.Poller()
    poller.register(sub, zmq.POLLIN)
    deadline = time.time() + a.listen_s
    while time.time() < deadline:
        if poller.poll(200):
            topic, raw = sub.recv_multipart()  # [tipo, Envelope en msgpack]
            print(f"evento del nodo: {topic.decode()} ({len(raw)} bytes msgpack)")
    push.close(linger=500)
    sub.close(linger=0)
    return 0


if __name__ == "__main__":
    sys.exit(main())

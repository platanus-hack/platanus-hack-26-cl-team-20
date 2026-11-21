"""Read-only loopback monitor; SSE snapshots follow atomic pipeline reports."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
import numpy as np

from . import contracts as c
from .pipeline import LABELS


def read_report(output):
    try:
        return c.read_json(Path(output) / "pipeline.json", "pipeline")
    except FileNotFoundError:
        return None


def current_asset(output, report, rel):
    """Only complete outputs of this run are visible, including after restart."""
    root = Path(output).resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or report is None:
        raise HTTPException(404, "Artefacto no publicado en esta ejecución")
    for stage in report["stages"].values():
        if stage["state"] in ("passed", "unverified") and rel in stage["outputs"]:
            if path.is_file() and c.digest(path) == stage["outputs"][rel]:
                return path
    raise HTTPException(404, "Artefacto ausente, obsoleto o modificado")


async def event_stream(output, disconnected, interval=.2):
    last, heartbeat = None, 0
    while not await disconnected():
        try:
            report = read_report(output)
            data = {"report": report, "labels": LABELS}
        except (OSError, ValueError) as e:
            data = {"report": None, "error": f"Reporte no disponible: {type(e).__name__}", "labels": LABELS}
        encoded = json.dumps(data, allow_nan=False)
        if encoded != last:
            # A full snapshot makes reconnect safe even after missed events or
            # a different run; no ever-growing event queue or image payloads.
            yield f"event: pipeline\ndata: {encoded}\n\n"
            last, heartbeat = encoded, 0
        elif heartbeat >= 50:
            yield ": keepalive\n\n"
            heartbeat = 0
        heartbeat += 1
        await asyncio.sleep(interval)


def build_app(output):
    root = Path(output).resolve()
    static = Path(__file__).parent / "pipeline_ui"
    app = FastAPI(title="SL scan · pipeline", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/")
    def index():
        return FileResponse(static / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/static/{name}")
    def files(name: str):
        if name not in ("app.js", "style.css"):
            raise HTTPException(404)
        return FileResponse(static / name, headers={"Cache-Control": "no-store"})

    @app.get("/api/pipeline")
    def state():
        return {"report": read_report(root), "labels": LABELS}

    @app.get("/api/events")
    def events(request: Request):
        return StreamingResponse(event_stream(root, request.is_disconnected), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/artifacts/{rel:path}")
    def artifact(rel: str):
        path = current_asset(root, read_report(root), rel)
        return FileResponse(path, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/preview")
    def preview():
        report = read_report(root)
        out = {"run_id": report["run_id"] if report else None, "points": [], "faces": [],
               "devices": [], "objects": [], "lights": [], "materials": [], "units": "mm",
               "sampled": False, "measured": [], "uv": []}
        if report is None:
            return out
        def locate(stage, name):
            r = report["stages"].get(stage, {})
            for rel in r.get("outputs", {}):
                if Path(rel).name == name:
                    return current_asset(root, report, rel)
            return None
        rigpath = locate("validate_rig", "rig.json")
        if rigpath:
            rig = c.read_json(rigpath)
            for d in rig["devices"].values():
                R, t, K = np.asarray(d["R"]), np.asarray(d["t"]), np.asarray(d["K"])
                center = -R.T @ t
                corners = np.array([[0, 0], [d["size"][0]-1, 0],
                                    [d["size"][0]-1, d["size"][1]-1], [0, d["size"][1]-1]], float)
                import cv2
                rays = cv2.undistortPoints(corners[:, None, :], K, np.asarray(d["dist"]))[:, 0]
                frustum = np.column_stack([rays * 100., np.full(4, 100.)]) @ R + center
                out["devices"].append(dict(name=d["name"], kind=d["kind"], center=center.tolist(), frustum=frustum.tolist()))
        meshpath = locate("mesh", "mesh.npz")
        cloudpath = locate("triangulate", "cloud_quality.npz")
        if meshpath:
            _, a = c.read_npz(meshpath)
            step = max(1, int(np.ceil(len(a["vertices_mm"]) / 5000)))
            out.update(points=a["vertices_mm"][::step].tolist(), measured=a["measured"][::step].tolist(),
                       uv=a["uv"][::step].tolist(), sampled=step > 1)
            if step == 1:
                out["faces"] = a["faces"][:10000].tolist()
        elif cloudpath:
            _, a = c.read_npz(cloudpath)
            step = max(1, int(np.ceil(len(a["xyz_mm"]) / 5000)))
            out.update(points=a["xyz_mm"][::step].tolist(), measured=[True] * len(a["xyz_mm"][::step]), sampled=step > 1)
        for stage, name, key in (("segment", "objects.json", "objects"),
                                 ("radiometry_materials", "lights.json", "lights"),
                                 ("radiometry_materials", "materials.json", "materials")):
            p = locate(stage, name)
            if p:
                out[key] = c.read_json(p)[key]
        return out

    return app

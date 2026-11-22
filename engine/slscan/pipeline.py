"""Replay-only pipeline orchestration. No acquisition or projector side effects.

Adapters receive a staging directory; only a successful, validated result is
published. The report is the authoritative list of currently usable artifacts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import fcntl
import inspect
import json
from pathlib import Path
import tempfile
import threading
import time
import uuid

from . import contracts as c

DEPENDENCIES = {
    "preflight": (), "decode": ("preflight",), "validate_rig": ("preflight", "decode"),
    "triangulate": ("decode", "validate_rig"), "mesh": ("triangulate",),
    "segment": ("mesh", "validate_rig"), "radiometry_materials": ("mesh",),
    "register_render": ("mesh", "validate_rig", "radiometry_materials"),
    "verify": ("register_render", "segment"),
}
LABELS = {"preflight": "Entradas", "decode": "Gray code y correspondencias",
          "validate_rig": "Cámara y proyector", "triangulate": "Nube medida",
          "mesh": "Malla y textura", "segment": "Objetos",
          "radiometry_materials": "Luces y materiales", "register_render": "Escena registrada",
          "verify": "Verificación física"}


class Blocked(RuntimeError):
    """An input or independent package is required, not a processing failure."""


class Cancelled(RuntimeError):
    pass


@dataclass
class Result:
    paths: list[str]
    counts: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    state: str = "passed"
    reason: str = ""
    units: str = "mm"


@dataclass
class Context:
    runner: "Runner"
    stage: str
    work: Path
    inputs: dict

    @property
    def source(self):
        return self.runner.source

    @property
    def evidence(self):
        return self.runner.evidence

    @property
    def options(self):
        return self.runner.options

    def check_cancelled(self):
        if self.runner.cancel.is_set():
            raise Cancelled("Ejecución cancelada; reanudar para continuar.")

    def progress(self, fraction, message=""):
        self.check_cancelled()
        s = self.runner.report["stages"][self.stage]
        s.update(progress=max(0., min(1., fraction)), reason=message)
        self.runner.publish()

    def artifact(self, stage, name):
        for rel in self.runner.report["stages"][stage]["outputs"]:
            if Path(rel).name == name:
                return self.runner.output / rel
        raise Blocked(f"Falta {stage}/{name}.")

    def metadata(self, kind, units="mm", **values):
        return {**c.header(kind, self.evidence, units, self.inputs), **values}


def now():
    return datetime.now(timezone.utc).isoformat()


def code_hash():
    root = Path(__file__).parent
    return c.canonical_hash({p.name: c.digest(p) for p in sorted(root.glob("*.py"))})


def code_revision():
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent,
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def adapter_hash(adapter):
    if adapter is None:
        return c.canonical_hash(None)
    try:
        filename = inspect.getsourcefile(adapter)
        return c.canonical_hash({"source": inspect.getsource(adapter),
                                 "module": c.digest(filename) if filename else None})
    except (OSError, TypeError):
        # Opaque callable: process identity intentionally prevents unsafe reuse.
        return c.canonical_hash(repr(adapter))


class Runner:
    def __init__(self, source, output, *, synthetic=False, rig=None, options=None,
                 adapters=None, on_event=None, cancel=None):
        self.source, self.output = Path(source).resolve(), Path(output).resolve()
        if self.output == self.source or self.output.is_relative_to(self.source):
            raise ValueError("La salida debe estar fuera del scan; las capturas se leen sin modificarlas.")
        self.rig = Path(rig).resolve() if rig else self.source / "rig.json"
        self.evidence = "synthetic" if synthetic else "recorded"
        self.options = {"camera": "osmo", "projector_size": [1920, 1080], "skip_bits": 2,
                        "threshold": 5, **(options or {})}
        if Path(self.options["camera"]).name != self.options["camera"] or self.options["camera"] in (".", ".."):
            raise ValueError("camera debe ser un nombre local de dispositivo")
        self.synthetic = synthetic
        self.adapters = adapters
        self.on_event = on_event
        self.cancel = cancel or threading.Event()
        self.report = None

    def publish(self):
        self.report["updated_at"] = now()
        c.atomic_json(self.output / "pipeline.json", self.report)
        if self.on_event:
            self.on_event(json.loads(json.dumps(self.report)))

    def _source_hashes(self, stage):
        if self.synthetic:
            names = ["synthetic.json"]
        elif stage == "preflight":
            names = ["manifest.json"]
            manifest = self.source / "manifest.json"
            if manifest.exists():
                try:
                    refs = json.loads(manifest.read_text()).get("input_hashes", {})
                    names += [n for n in refs if (self.source / n).resolve().is_relative_to(self.source)]
                except (ValueError, AttributeError):
                    pass
        elif stage == "decode":
            folder = self.source / self.options["camera"]
            files = sorted(folder.glob("*.png")) + sorted(folder.glob("*.yaml")) + [folder / "map.npz"]
            names = [str(p.relative_to(self.source)) for p in files]
        elif stage == "validate_rig":
            names = ["manifest.json"]
        else:
            # Package handoffs are opt-in, hashed in full and require provenance.
            folder = self.source / "products" / stage
            names = [str(p.relative_to(self.source)) for p in sorted(folder.rglob("*")) if p.is_file()]
            if stage == "radiometry_materials":
                bundle = self.source / "pbr"
                names += [str(p.relative_to(self.source)) for p in sorted(bundle.rglob("*")) if p.is_file()]
        hashes = {name: c.digest(self.source / name) for name in names if (self.source / name).is_file()}
        if stage == "validate_rig" and self.rig.is_file():
            hashes["rig.json"] = c.digest(self.rig)
        return hashes

    def _inputs(self, stage):
        inputs = self._source_hashes(stage)
        for dep in DEPENDENCIES[stage]:
            record = self.report["stages"][dep]
            for name, value in record["outputs"].items():
                inputs[f"{dep}/{Path(name).name}"] = value
            if record["fingerprint"]:
                inputs[f"{dep}/fingerprint"] = record["fingerprint"]
        return inputs

    def _intact(self, record):
        if record.get("state") not in ("passed", "unverified") or not record.get("outputs"):
            return False
        for rel, sha in record["outputs"].items():
            path = (self.output / rel).resolve()
            if not path.is_relative_to(self.output) or not path.is_file() or c.digest(path) != sha:
                return False
        return True

    def run(self):
        self.output.mkdir(parents=True, exist_ok=True)
        with (self.output / ".pipeline.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise RuntimeError("Ya hay una ejecución escribiendo en esta salida.") from e
            return self._run_locked()

    def _run_locked(self):
        from .pipeline_adapters import recorded_adapters, synthetic_adapters
        adapters = self.adapters or (synthetic_adapters() if self.synthetic else recorded_adapters())
        previous = {}
        try:
            previous = c.read_json(self.output / "pipeline.json", "pipeline")
        except (OSError, ValueError):
            pass
        revision, implementation = code_revision(), code_hash()
        t = now()
        self.report = {**c.header("pipeline", self.evidence), "run_id": str(uuid.uuid4()),
                       "session_id": self.source.name, "status": "running", "started_at": t,
                       "updated_at": t, "code_revision": revision, "source": str(self.source),
                       "physical_verified": False, "cancelled": False, "stages": {}}
        for stage, deps in DEPENDENCIES.items():
            self.report["stages"][stage] = dict(state="pending", dependencies=list(deps), fingerprint=None,
                inputs={}, outputs={}, duration_s=0., counts={}, metrics={}, units="mm", evidence=self.evidence,
                reason="", cached=False, progress=0.)
        # Clear every old output reference before publishing this run; UI never sees
        # a previous scene while a changed source is being processed.
        self.publish()
        changed = set()
        try:
            for stage in DEPENDENCIES:
                r = self.report["stages"][stage]
                if self.cancel.is_set():
                    raise Cancelled("Ejecución cancelada.")
                inputs = self._inputs(stage)
                fingerprint = c.canonical_hash({"inputs": inputs, "stage": stage, "options": self.options,
                    "implementation": implementation, "adapter": adapter_hash(adapters.get(stage)),
                    "source": str(self.source), "evidence": self.evidence})
                r.update(inputs=inputs, fingerprint=fingerprint)
                unmet = [d for d in DEPENDENCIES[stage] if self.report["stages"][d]["state"] not in ("passed", "unverified")]
                if unmet:
                    r.update(state="blocked", reason="Requiere: " + ", ".join(unmet))
                    changed.add(stage)
                    self.publish()
                    continue
                old = previous.get("stages", {}).get(stage, {})
                if (previous.get("source") == str(self.source) and old.get("fingerprint") == fingerprint
                        and not changed.intersection(DEPENDENCIES[stage]) and self._intact(old)):
                    r.update(old, cached=True)
                    self.publish()
                    continue
                changed.add(stage)
                r.update(state="running")
                self.publish()
                started = time.monotonic()
                try:
                    artifacts = self.output / "artifacts" / stage
                    artifacts.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(prefix=".building-", dir=artifacts) as temp:
                        ctx = Context(self, stage, Path(temp), inputs)
                        if stage not in adapters:
                            raise Blocked(f"Falta adaptador para {stage}.")
                        result = adapters[stage](ctx)
                        ctx.check_cancelled()
                        if result.state not in ("passed", "unverified") or not result.paths:
                            raise ValueError("Adapter must produce complete artifacts, passed or unverified.")
                        for rel in result.paths:
                            path = (ctx.work / rel).resolve()
                            if not path.is_relative_to(ctx.work) or not path.is_file():
                                raise ValueError(f"Invalid adapter output: {rel}")
                        c.validate_files(ctx.work, result.paths)
                        # Unique immutable generation; no deletion of earlier artifacts.
                        dest = artifacts / uuid.uuid4().hex
                        Path(temp).rename(dest)
                        r.update(outputs={str((dest / rel).relative_to(self.output)): c.digest(dest / rel)
                                          for rel in result.paths}, counts=result.counts, metrics=result.metrics,
                                 state=result.state, reason=result.reason, units=result.units, progress=1.)
                except Blocked as e:
                    r.update(state="blocked", reason=str(e), outputs={})
                except Cancelled:
                    r.update(state="blocked", reason="Cancelado; artefactos no publicados.", outputs={})
                    raise
                except Exception as e:
                    r.update(state="failed", reason=f"{type(e).__name__}: {e}", outputs={})
                finally:
                    r["duration_s"] = time.monotonic() - started
                    self.publish()
        except (Cancelled, KeyboardInterrupt):
            self.report["cancelled"] = True
            for r in self.report["stages"].values():
                if r["state"] in ("pending", "running"):
                    r.update(state="blocked", reason="Cancelado; reanudar para continuar.", outputs={})
        except Exception as e:
            # Hashing/read failures also need a terminal, resumable report.
            current = self.report["stages"][stage]
            current.update(state="failed", reason=f"{type(e).__name__}: {e}", outputs={})
            for r in self.report["stages"].values():
                if r["state"] in ("pending", "running"):
                    r.update(state="blocked", reason=f"Interrumpido por fallo en {stage}.", outputs={})
        states = [r["state"] for r in self.report["stages"].values()]
        self.report["status"] = next((s for s in ("failed", "blocked", "unverified") if s in states), "passed")
        # Physical status comes exclusively from the validation contract, never
        # from successful processing or a synthetic geometry tolerance.
        verification = self.report["stages"]["verify"]
        if verification["outputs"]:
            path = next((p for p in verification["outputs"] if Path(p).name == "validation.json"), None)
            if path:
                self.report["physical_verified"] = c.read_json(self.output / path)["physical_verified"]
        self.publish()
        return self.report


def add_parser(subparsers):
    p = subparsers.add_parser("pipeline", help="pipeline reproducible sin hardware y monitor local")
    sub = p.add_subparsers(dest="pipeline_command", required=True)
    run = sub.add_parser("run", help="procesar un scan grabado o un caso sintético")
    run.add_argument("scan")
    run.add_argument("--out", help="salida fuera del scan (default: reports/pipeline/<scan>)")
    run.add_argument("--rig")
    run.add_argument("--synthetic", action="store_true", help="adaptadores sintéticos; nunca acredita precisión física")
    run.add_argument("--camera", default="osmo")
    run.add_argument("--projector-size", default="1920x1080")
    run.add_argument("--skip-bits", type=int, default=2)
    run.add_argument("--threshold", type=int, default=5)
    run.set_defaults(fn=cmd_run)
    serve = sub.add_parser("serve", help="UI/SSE local de una carpeta de salida")
    serve.add_argument("output")
    serve.add_argument("--port", type=int, default=8796)
    serve.set_defaults(fn=cmd_serve)
    schema = sub.add_parser("schema", help="imprimir un JSON Schema versionado")
    schema.add_argument("artifact", choices=sorted(c.SCHEMAS))
    schema.set_defaults(fn=lambda args: print(json.dumps(c.SCHEMAS[args.artifact], indent=2)) or 0)
    check = sub.add_parser("validate", help="validar contrato JSON o matches NPZ")
    check.add_argument("path")
    check.set_defaults(fn=cmd_validate)


def cmd_validate(args):
    try:
        if Path(args.path).suffix == ".npz":
            c.read_npz(args.path)
        else:
            c.read_json(args.path)
        print("Contrato válido")
        return 0
    except (ValueError, OSError, KeyError) as e:
        print(f"Contrato inválido: {e}")
        return 1


def cmd_run(args):
    output = args.out or str(Path("reports/pipeline") / Path(args.scan).name)
    previous = {}
    def progress(report):
        for name, r in report["stages"].items():
            state = (r["state"], r["cached"])
            if previous.get(name) != state and r["state"] != "pending":
                print(f"[{report['run_id'][:8]}] {name}: {r['state']}"
                      f"{' (cache)' if r['cached'] else ''} {r['reason']}", flush=True)
                previous[name] = state
    try:
        size = [int(v) for v in args.projector_size.lower().split("x")]
        if len(size) != 2 or min(size) <= 0 or not 0 <= args.skip_bits <= 10 or args.threshold < 0:
            raise ValueError("Resolución, skip-bits o threshold inválidos")
        report = Runner(args.scan, output, synthetic=args.synthetic, rig=args.rig,
                        options=dict(camera=args.camera, projector_size=size, skip_bits=args.skip_bits,
                                     threshold=args.threshold), on_event=progress).run()
    except (ValueError, OSError, RuntimeError) as e:
        print(str(e))
        return 1
    print(f"Reporte: {Path(output).resolve() / 'pipeline.json'}\nVerificado físicamente: {report['physical_verified']}")
    return 130 if report["cancelled"] else (1 if report["status"] == "failed" else 2 if report["status"] == "blocked" else 0)


def cmd_serve(args):
    from .pipeline_web import build_app
    import uvicorn
    print(f"Pipeline local: http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(build_app(args.output), host="127.0.0.1", port=args.port, log_level="warning")
    return 0

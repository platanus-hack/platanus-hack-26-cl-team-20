"""Escena viva: objetos con geometría, contorno, textura y tipo, personas, luces, proyectores.

Hoy se llena con una escena demo (mesa, pantalla, cuadro, cocodrilo, teclado) para desarrollar
efectos sin hardware. En el PC se llena desde el escáner (mallas por objeto de F5) y el tracking.
Todo en el marco de la mesa, en mm. El lienzo de un proyector es una vista ortográfica de la mesa
(canvas_px_per_mm) hasta que llegue la calibración real (render con K y distorsión en el PC).
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np

from .audio.features import audio_zero


@dataclass
class SceneObject:
    id: str
    name: str
    kind: str                       # table | screen | frame | prop | instrument | person | light
    bbox: list[float]               # [xmin, ymin, zmin, xmax, ymax, zmax] mm, marco de la mesa
    contour: list[list[float]]      # polígono (x, y) mm visto desde arriba
    plane: list[float] | None = None     # [nx, ny, nz, d] si es plano (mesa, pantalla, cuadro)
    texture: str = "mate"           # etiqueta de textura: mate | brillante | madera | tela | vidrio | piel
    albedo: list[float] = field(default_factory=lambda: [0.6, 0.6, 0.6])
    pose: list[float] = field(default_factory=lambda: [0, 0, 0, 0, 0, 0])   # x y z rx ry rz (mm, rad)
    tracked: bool = False
    velocity: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    extra: dict = field(default_factory=dict)

    def center(self) -> np.ndarray:
        b = self.bbox
        return np.array([(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2])


@dataclass
class Person:
    id: str
    joints: dict[str, list[float]]    # nombre -> [x, y, z] mm
    gesture: str | None = None        # p.ej. "open_hand", "point", "wave"


@dataclass
class Scene:
    objects: dict[str, SceneObject] = field(default_factory=dict)
    people: dict[str, Person] = field(default_factory=dict)
    lights: dict[str, dict] = field(default_factory=dict)
    projectors: dict[str, dict] = field(default_factory=dict)
    table_mm: tuple[float, float] = (1600.0, 800.0)     # ancho, fondo de la mesa (x, y)
    beat: dict = field(default_factory=lambda: {"bpm": 120.0, "phase": 0.0, "bar": 0.0, "playing": False})
    camera_frame: np.ndarray | None = None             # último cuadro de la cámara general (BGR), si hay
    controls: dict[str, dict] = field(default_factory=dict)   # device -> control -> {value, state, t} (lo llena el runtime)
    audio: dict = field(default_factory=audio_zero)           # sub bass mid high rms beat onsets bpm live (lo llena el runtime)
    inputs: list[dict] = field(default_factory=list)          # eventos de dispositivos desde el cuadro anterior, en orden
                                                              # ({device, control, type, value, state, t}); lo llena el runtime
    t: float = 0.0
    projector_px: bool = False      # True = coordenadas en píxeles del proyector (escena del escaneo), y hacia abajo
    scan_dir: str | None = None     # carpeta del escaneo del que salió (para ajustes y recarga)
    coordinate_space: str = "legacy"  # world_mm uses calibrated P4 rendering, never the 2D Canvas
    gaze: dict = field(default_factory=dict)   # mirada: {x, y (px del proyector), t, source, objeto?}; comando "gaze"
                                               # (hoy simulada; mañana la cámara de ojos manda lo mismo)

    def by_kind(self, kind: str) -> list[SceneObject]:
        return [o for o in self.objects.values() if o.kind == kind]

    def find(self, query: str) -> SceneObject | None:
        q = query.lower().strip()
        for o in self.objects.values():
            if q == o.id or q in o.name.lower() or q == o.kind or q in [a.lower() for a in o.extra.get("aliases", [])]:
                return o
        return None

    def nudge(self, query: str, dx: float, dy: float) -> SceneObject | None:
        """Corre un objeto (contorno, bbox y sus polígonos extra: teclas, platos) dx, dy en sus unidades y lo deja
        anotado en <scan_dir>/ajustes.json para que sobreviva a la recarga. Es el 'un poquito más abajo' de Max."""
        if self.coordinate_space == "world_mm":
            raise ValueError("Escena métrica: corregir calibración/relocalización, no mover objetos")
        o = self.find(query)
        if o is None:
            return None
        o.contour = [[x + dx, y + dy] for x, y in o.contour]
        o.bbox = [o.bbox[0] + dx, o.bbox[1] + dy, o.bbox[2], o.bbox[3] + dx, o.bbox[4] + dy, o.bbox[5]]
        for k in o.extra.get("keys", []) or []:
            for name in ("poligono", "cuadrante", "contorno"):
                if k.get(name):
                    k[name] = [[x + dx, y + dy] for x, y in k[name]]
            for name in ("centro", "punto_toque"):
                if k.get(name):
                    k[name] = [k[name][0] + dx, k[name][1] + dy]
        if o.extra.get("jogs"):
            o.extra["jogs"] = [[j[0] + dx, j[1] + dy, j[2]] for j in o.extra["jogs"]]
        for name in ("quad", "outline"):
            if o.extra.get(name):
                o.extra[name] = [[x + dx, y + dy] for x, y in o.extra[name]]
        if o.extra.get("jog_ellipses"):
            o.extra["jog_ellipses"] = [[e[0] + dx, e[1] + dy, *e[2:]] for e in o.extra["jog_ellipses"]]
        if o.extra.get("split_y") is not None:
            o.extra["split_y"] = o.extra["split_y"] + dy
        if o.extra.get("faces"):
            o.extra["faces"] = {k: [[x + dx, y + dy] for x, y in v] for k, v in o.extra["faces"].items()}
        adj = o.extra.setdefault("ajuste", [0.0, 0.0]); adj[0] += dx; adj[1] += dy
        if self.scan_dir:
            p = Path(self.scan_dir) / "ajustes.json"
            d = json.loads(p.read_text()) if p.exists() else {}
            d[o.id] = [round(adj[0], 1), round(adj[1], 1)]
            p.write_text(json.dumps(d, indent=1))
        return o

    def to_json(self) -> dict:
        return {
            "objects": {k: asdict(v) for k, v in self.objects.items()},
            "people": {k: asdict(v) for k, v in self.people.items()},
            "lights": self.lights, "projectors": self.projectors, "table_mm": list(self.table_mm),
            "beat": self.beat, "audio": self.audio, "t": self.t,
            "projector_px": self.projector_px, "scan_dir": self.scan_dir,
            "coordinate_space": self.coordinate_space, "gaze": dict(self.gaze),
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_json(), indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "Scene":
        d = json.loads(Path(path).read_text())
        s = cls(table_mm=tuple(d.get("table_mm", (1600, 800))))
        s.objects = {k: SceneObject(**v) for k, v in d.get("objects", {}).items()}
        s.people = {k: Person(**v) for k, v in d.get("people", {}).items()}
        s.lights, s.projectors, s.beat = d.get("lights", {}), d.get("projectors", {}), d.get("beat", s.beat)
        s.coordinate_space = d.get("coordinate_space", "legacy")
        s.projector_px, s.scan_dir = d.get("projector_px", False), d.get("scan_dir")
        return s


def _rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def demo_scene() -> Scene:
    """Mesa de 160x80 cm con pantalla, cuadro, cocodrilo de hule y teclado. Medidas plausibles."""
    s = Scene()
    s.objects["table"] = SceneObject("table", "mesa", "table", [0, 0, -20, 1600, 800, 0], _rect(0, 0, 1600, 800),
                                     plane=[0, 0, 1, 0], texture="madera", albedo=[0.55, 0.42, 0.3])
    s.objects["screen"] = SceneObject("screen", "pantalla", "screen", [1100, 650, 0, 1700, 680, 340],
                                      _rect(1100, 650, 1700, 680), plane=[0, -1, 0, 650], texture="vidrio",
                                      albedo=[0.05, 0.05, 0.05])
    s.objects["frame"] = SceneObject("frame", "cuadro", "frame", [200, 760, 100, 500, 775, 320],
                                     _rect(200, 760, 500, 775), plane=[0, -1, 0, 760], texture="mate",
                                     albedo=[0.9, 0.85, 0.7], extra={"width_mm": 300, "height_mm": 220})
    croc = [[x, y] for x, y in [(600, 300), (700, 280), (820, 300), (900, 330), (960, 330), (980, 350), (900, 370),
                                 (820, 380), (700, 390), (620, 370), (580, 340)]]
    s.objects["croc"] = SceneObject("croc", "cocodrilo", "instrument", [580, 280, 0, 980, 390, 90], croc,
                                    texture="piel", albedo=[0.25, 0.5, 0.2], tracked=True)
    s.objects["keys"] = SceneObject("keys", "teclado", "instrument", [150, 120, 0, 1050, 300, 60],
                                    _rect(150, 120, 1050, 300), plane=[0, 0, 1, 60], texture="mate",
                                    albedo=[0.9, 0.9, 0.9], extra={"keys": 61, "first_note": 36})
    s.projectors["proj0"] = {"size": [1920, 1080], "head": {"pan": 0.0, "tilt": -35.0}, "status": "sin calibrar"}
    s.lights["dmx0"] = {"kind": "dmx", "rgb": [1, 1, 1], "cct": 5600, "pos": [800, -300, 900], "status": "sin conectar"}
    s.lights["bar0"] = {"kind": "tracked", "rgb": [1, 0.8, 0.6], "cct": 3200, "pos": None, "status": "sin trackear"}
    return s


def _poly_bbox(poly, z0=0.0, z1=60.0):
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    return [min(xs), min(ys), z0, max(xs), max(ys), z1]


def scene_from_scan(scan: str | Path) -> Scene:
    """Escena real desde un escaneo (captures/scanN): segments.json + workbench.json + keys.json, en píxeles del
    proyector (1 unidad = 1 px, y hacia abajo), con los ajustes finos de ajustes.json aplicados. Ids compatibles con
    la demo (croc, keys) más alias en español para la secretaria."""
    scan = Path(scan)
    if (scan / "objects.json").is_file():
        return scene_from_objects(scan / "objects.json")
    rd = lambda n: json.loads((scan / n).read_text()) if (scan / n).exists() else {}
    seg, wb, keys = rd("segments.json"), rd("workbench.json"), rd("keys.json")
    adj = rd("ajustes.json")
    s = Scene(table_mm=(1920.0, 1080.0)); s.projector_px = True; s.scan_dir = str(scan)

    def rect(b):
        x, y, w, h = b; return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]

    def add(oid, name, kind, poly, aliases=(), z=(0.0, 60.0), texture="mate", albedo=(0.6, 0.6, 0.6), tracked=False, **extra):
        poly = [[float(x), float(y)] for x, y in poly]
        o = SceneObject(oid, name, kind, _poly_bbox(poly, *z), poly, texture=texture, albedo=list(albedo), tracked=tracked,
                        extra={"aliases": list(aliases), **extra})
        s.objects[oid] = o
        return o

    add("table", "mesa", "table", [[0, 0], [1920, 0], [1920, 1080], [0, 1080]], aliases=("mesa", "escritorio"), z=(-20, 0),
        texture="mate", albedo=(0.9, 0.9, 0.9))
    s.objects["table"].plane = [0, 0, 1, 0]
    from . import scan_geom                      # contornos finos desde la foto (scene_pov.png), si está
    sd = scan_geom.load(scan)
    pint_key = "buda" if "buda" in seg else ("cuadro" if "cuadro" in seg else None)   # el cuadro grande de la escena
    if pint_key:
        q = seg[pint_key].get("quad") or rect(seg[pint_key]["bbox"])
        fine = scan_geom.buda_outline(sd) if sd is not None else None
        extra = {"quad": fine["quad"], "outline": fine["outline"], "quad_seg": q} if fine else {"quad": q}
        oid, name = ("buda", "Buda (cuadro)") if pint_key == "buda" else ("cuadro", "cuadro")
        add(oid, name, "frame", extra["quad"], aliases=("cuadro", "frame", "pintura", "tapiz", "buda"), z=(0, 5),
            texture="tela", albedo=(0.8, 0.6, 0.3), **extra)
    croc = wb.get("cocodrilo") or seg.get("cocodrilo")
    if croc:
        add("croc", "cocodrilo", "instrument", croc.get("contour") or rect(croc["bbox"]), aliases=("cocodrilo", "croc", "lagarto"),
            z=(0, 90), texture="piel", albedo=(0.55, 0.7, 0.5), tracked=True)
    tec = wb.get("teclado") or seg.get("teclado")
    if tec or keys.get("teclas"):
        if tec and tec.get("rect"):
            import cv2
            (cx, cy), (rw, rh), ang = tec["rect"]; poly = cv2.boxPoints(((cx, cy), (rw, rh), ang)).tolist()
        else:
            poly = rect(tec["bbox"]) if tec else rect(keys.get("bbox", [0, 0, 1, 1]))
        add("keys", "teclado", "instrument", poly, aliases=("teclado", "piano", "keys", "korg", "ek-50"), z=(0, 60), albedo=(0.9, 0.9, 0.9),
            keys=keys.get("teclas", []), first_note=keys.get("primera_nota", 36), n_keys=keys.get("n_teclas", 61),
            pitch_px=keys.get("paso_px") or (tec or {}).get("pitch_px"))
    ddj = seg.get("ddj")
    if ddj and ddj.get("bbox"):
        lay = scan_geom.ddj_layout(sd) if sd is not None else None
        if lay:                                  # platos como elipses de la foto; jogs = círculo del semieje mayor
            jogs = [[round(j["c"][0], 1), round(j["c"][1], 1), round(max(j["axes"]) / 2, 1)] for j in lay["jogs"]]
            ell = [[round(j["c"][0], 1), round(j["c"][1], 1), round(j["axes"][0], 1), round(j["axes"][1], 1), round(j["angle"], 1)]
                   for j in lay["jogs"]]
            add("ddj", "DDJ (controlador DJ)", "instrument", lay["body"], aliases=("ddj", "dj", "platos", "jogs", "pioneer"),
                z=(0, 50), albedo=(0.1, 0.1, 0.1), jogs=jogs, jog_ellipses=ell, jogs_seg=ddj.get("jogs", []))
        else:
            add("ddj", "DDJ (controlador DJ)", "instrument", rect(ddj["bbox"]), aliases=("ddj", "dj", "platos", "jogs", "pioneer"),
                z=(0, 50), albedo=(0.1, 0.1, 0.1), jogs=ddj.get("jogs", []))
    for oid, name, al in (("ohm", "OHM RGB", ("ohm", "livid", "pads")), ("circuit", "Circuit Tracks", ("circuit", "novation")),
                          ("torre", "torre (PC blanco y parlante)", ("torre", "pc", "parlante")), ("papel", "hoja carta", ("hoja", "papel", "carta"))):
        if oid in seg and seg[oid].get("bbox"):
            poly = seg[oid].get("quad") or rect(seg[oid]["bbox"])
            fine = None
            if oid == "papel" and sd is not None:
                fine = scan_geom.papel_quad(sd)            # la hoja blanca, no la pantalla entera
            elif oid == "torre" and sd is not None:
                fine = scan_geom.torre_faces(sd)           # contorno real + corte entre caras (workbench.json)
            if isinstance(fine, dict):
                poly = fine["contour"]
            elif fine:
                poly = fine
            o = add(oid, name, "prop" if oid in ("torre", "papel") else "instrument", poly, aliases=al,
                    z=(0, 400 if oid == "torre" else 40), albedo=(0.95, 0.95, 0.95) if oid == "papel" else (0.2, 0.2, 0.2))
            if oid == "papel":
                if fine:
                    o.extra["quad"] = poly                  # esquinas de la hoja (papel_canvas las usa tal cual)
                if seg[oid].get("mm"):
                    o.extra["mm"] = seg[oid]["mm"]
            if oid == "torre" and isinstance(fine, dict):
                o.extra["split_y"] = fine["split_y"]
                if fine.get("faces"):
                    o.extra["faces"] = fine["faces"]
    for oid, (dx, dy) in adj.items():
        if oid in s.objects:
            o = s.objects[oid]; o.extra["ajuste"] = [0.0, 0.0]
            s.scan_dir, keep = None, s.scan_dir          # aplicar sin volver a escribir el archivo
            s.nudge(oid, dx, dy); s.scan_dir = keep
    s.projectors["proj0"] = {"size": [1920, 1080], "status": f"calibrado ({scan.name})", "scan": str(scan)}
    return s


def scene_from_objects(path: str | Path) -> Scene:
    """Generic metric scene. No legacy objects, pixel/mm mixing or per-object nudges.

    P4 consumes face_indices, UV routes and exact masks from extra. The legacy
    top-down contour is intentionally empty: it cannot represent this geometry.
    """
    from slscan import contracts as c
    from slscan.objects import asset_path
    path = Path(path)
    data = c.read_json(path, "objects")
    mesh_path = path.parent / "mesh.json"
    if mesh_path.exists() and c.digest(mesh_path) != data["mesh_sha256"]:
        raise ValueError("Objects refer to a different mesh")
    scene = Scene(scan_dir=str(path.parent), coordinate_space="world_mm")
    for item in data["objects"]:
        if item.get("projector_mask"):
            asset_path(path.parent, item["projector_mask"])
        semantic = item.get("semantic", {})
        scene.objects[item["id"]] = SceneObject(
            id=item["id"], name=item["label"] or "objeto sin identificar", kind=item["label"] or "unknown",
            bbox=[*item["bbox_mm"][0], *item["bbox_mm"][1]], contour=[], plane=item["plane"],
            texture=semantic.get("material", "unknown"), extra={
                **{k: item[k] for k in ("face_indices", "capabilities", "semantic", "effect_routes",
                                       "projector_contour_px", "projector_regions_px", "projector_mask") if k in item},
                "mesh_sha256": data["mesh_sha256"], "world": data.get("world"),
                "coordinate_space": "world_mm", "observation": item["observation"]})
    return scene


def animate_demo(scene: Scene, dt: float) -> None:
    """Mueve el cocodrilo en círculo y marca el beat: así los efectos ven una escena viva."""
    scene.t += dt
    c = scene.objects.get("croc")
    if c is not None:
        cx, cy = 780 + 120 * math.cos(scene.t * 0.5), 335 + 60 * math.sin(scene.t * 0.5)
        dx, dy = cx - (c.bbox[0] + c.bbox[3]) / 2, cy - (c.bbox[1] + c.bbox[4]) / 2
        c.bbox = [c.bbox[0] + dx, c.bbox[1] + dy, c.bbox[2], c.bbox[3] + dx, c.bbox[4] + dy, c.bbox[5]]
        c.contour = [[x + dx, y + dy] for x, y in c.contour]
        c.velocity = [dx / max(dt, 1e-3), dy / max(dt, 1e-3), 0.0]
    b = scene.beat
    beats = scene.t * b["bpm"] / 60.0
    b["phase"] = beats % 1.0
    b["bar"] = (beats / 4.0) % 1.0
    b["playing"] = True

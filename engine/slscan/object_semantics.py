"""Local, typed semantic descriptions and declarative P4 material/light routes.

No requests are made here. Jev may select a route from this catalogue after a
visual model has described the object. A description is not a PBR measurement.
"""
from __future__ import annotations

import math

TYPES = ("unknown", "picture", "screen", "plant", "vase", "book", "cushion",
         "sofa", "wall", "table", "paper")
SURFACES = ("unknown", "smooth", "textured", "foliage", "fabric")
MATERIALS = ("unknown", "paint", "paper", "wood", "fabric", "ceramic", "glass",
             "metal", "plastic", "organic", "stone")
# Renderer operations, not names of legacy 2D plugins. All target mesh faces.
CATALOG = {
    "surface_light": ("scene_lighting", {"mesh"}),
    "flat_texture": ("uv_texture", {"mesh", "textured", "planar"}),
    "foliage": ("normal_light", {"mesh", "normals"}),
    "canvas": ("uv_texture", {"mesh", "textured", "planar"}),
    "relief": ("normal_light", {"mesh", "normals"}),
    "ceramic_glaze": ("material_modulation", {"mesh", "normals"}),
    "page_texture": ("uv_texture", {"mesh", "textured"}),
    "fabric_wave": ("uv_texture", {"mesh", "textured"}),
}


def describe(value=None, *, source="none"):
    """Reject malformed model output; retain uncertainty rather than guessing."""
    value = {} if value is None else value
    if not isinstance(value, dict):
        raise ValueError("Semantic response must be a JSON object")
    kind = value.get("type", "unknown")
    material = value.get("material", "unknown")
    surface = value.get("surface", "unknown")
    confidence = float(value.get("confidence", 0))
    if (kind not in TYPES or material not in MATERIALS or surface not in SURFACES
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError("Invalid semantic type/material/surface/confidence")
    text = value.get("description", "")
    if not isinstance(text, str) or len(text) > 1000:
        raise ValueError("Description must be text up to 1000 characters")
    suggestions = value.get("suggested_effects", [])
    if not isinstance(suggestions, list) or any(x not in CATALOG for x in suggestions):
        raise ValueError("Unknown suggested effect")
    return dict(type=kind, material=material, surface=surface, confidence=confidence,
                description=text, source=source, suggested_effects=suggestions,
                status="unknown" if kind == "unknown" or confidence < .6 else "proposed",
                material_is_measured=False)


def routes(semantic, capabilities):
    """Intersect semantic intent with actual geometry/UV capabilities."""
    wanted = ["surface_light"]
    if semantic["status"] != "unknown":
        wanted += {"picture": ["flat_texture"], "screen": ["flat_texture"],
                   "paper": ["canvas"], "plant": ["foliage"], "vase": ["ceramic_glaze"],
                   "book": ["page_texture"], "cushion": ["fabric_wave"],
                   "sofa": ["fabric_wave"]}.get(semantic["type"], [])
        if semantic["type"] == "wall":
            wanted += {"smooth": ["canvas"], "textured": ["relief"]}.get(semantic["surface"], [])
        wanted += semantic["suggested_effects"]
    out = []
    for name in dict.fromkeys(wanted):
        operation, needs = CATALOG[name]
        if needs <= set(capabilities):
            out.append(dict(effect=name, operation=operation, requires=sorted(needs),
                            target="face_indices", mask="projector_mask", coordinate_space="mesh_uv"
                            if operation == "uv_texture" else "world_mm",
                            changes_geometry=False, material_parameters="artistic_not_measured"))
    return out

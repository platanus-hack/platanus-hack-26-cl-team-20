"""Diseño reproducible del bloque RGB/gris/base, sin abrir dispositivos.

P2 ejecuta el plan con su único transporte/cámara; P7 ajusta respuesta y PBR.
Los patrones no tienen nombres Gray y no entran a load_sequence/decode.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class RadiometricPattern:
    index: int
    name: str
    rgb: tuple[int, int, int]
    role: str              # base | fit | held_out | repeat
    stable_frames: int = 3


def plan(levels=(32, 64, 96, 144, 192, 240), stable_frames=3) -> list[RadiometricPattern]:
    """24 patrones fit por defecto, controles independientes y deriva inicio/final.

    No se cambia exposición dentro del bloque. Un bracket es otro bloque completo
    con exposición conocida y su propio manifest. No se supone settle fijo.
    """
    if (not levels or any(not isinstance(x, int) or x <= 0 or x > 255 for x in levels)
            or len(set(levels)) != len(levels) or stable_frames < 3):
        raise ValueError("niveles únicos enteros en [1,255]; al menos 3 frames estables")
    result = []

    def add(name, rgb, role):
        result.append(RadiometricPattern(len(result), name, tuple(rgb), role, stable_frames))

    add("base_start", (0, 0, 0), "base")
    add("reference_start", (128, 128, 128), "repeat")
    for channel, direction in (("r", (1, 0, 0)), ("g", (0, 1, 0)), ("b", (0, 0, 1)), ("gray", (1, 1, 1))):
        for value in levels:
            add(f"{channel}_{value:03d}", [v * value for v in direction], "fit")
        add(f"base_after_{channel}", (0, 0, 0), "base")
    fit = {p.rgb for p in result if p.role == "fit"}
    for i, rgb in enumerate(((80, 80, 80), (176, 176, 176), (64, 144, 192), (192, 96, 32))):
        if rgb in fit:
            raise ValueError("niveles de ajuste se superponen con controles reservados")
        add(f"held_out_{i}", rgb, "held_out")
    add("reference_end", (128, 128, 128), "repeat")
    add("base_end", (0, 0, 0), "base")
    return result


def pattern_image(spec: RadiometricPattern, size: tuple[int, int]) -> np.ndarray:
    """BGR uint8 para el transporte existente PatternWindow.show/overlay."""
    w, h = size
    if w <= 0 or h <= 0:
        raise ValueError("tamaño inválido")
    return np.broadcast_to(np.array(spec.rgb[::-1], np.uint8), (h, w, 3)).copy()


def plan_manifest(capture_session_id: str, rig_sha256: str, *, evidence="synthetic", levels=(32, 64, 96, 144, 192, 240)):
    if not capture_session_id or not rig_sha256:
        raise ValueError("se requiere sesión y hash de rig")
    if evidence not in ("synthetic", "recorded", "live"):
        raise ValueError("evidence inválida")
    specs = plan(levels)
    return dict(schema_version="1.0", artifact="radiometry", evidence=evidence,
        units="relative", input_hashes={}, digital_encoding="digital_number_8bit",
        status="planned_not_acquired", session_id=capture_session_id,
        rig_sha256=rig_sha256, channel_order="RGB", patterns=[asdict(p) for p in specs],
        response_kind="unobserved", states=[], channel_response=[], mixing_rgb=[[0., 0., 0.]] * 3,
        mixing_rgb_status="unobserved_placeholder_do_not_apply", saturation_mask=None,
        gamut={"status": "unobserved"}, reference={"status": "reference_patch_required"},
        minimum_frames=sum(p.stable_frames for p in specs),
        acquisition_requirements={
            "transport": "reuse_P2; guides_off_during_exposure",
            "static_session": True, "ambient": "unchanged; controlled_lamps_off",
            "timing": "measured_latency_then_stable_frames; record_show_and_capture_timestamps",
            "camera_controls": "record_requested_and_readback_exposure_focus_white_balance",
            "unlocked_controls": "unverified_until_reference_patch_compensation_is_validated",
            "bracketing": "known_exposure_between_complete_blocks_only",
            "per_frame": ["file", "sha256", "timestamp", "exposure", "focus", "white_balance",
                          "reference_patch", "saturation_mask", "low_signal_mask", "stability_score"],
            "before_after": "compare_base_and_reference; reject_motion_or_unexplained_drift"},
        limitations=["digital input is not luminance", "camera response must be measured before physical subtraction",
                     "RGB ramp does not add lighting directions", "appearance is not measured albedo",
                     "P7 fits response with fit only and validates on held_out"])

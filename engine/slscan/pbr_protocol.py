"""Hardware-free capture guidance and gates for fixed household lamps.

The caller displays/speaks instructions, blanks the guide, and feeds frames.
This module never opens a camera, display, audio device or network connection.
"""
from __future__ import annotations

from dataclasses import asdict
import numpy as np

from .radiometry import plan


def capture_plan(lamp_ids, held_out):
    ids = list(lamp_ids)
    if len(ids) != len(set(ids)) or held_out not in ids or len(ids) < 2:
        raise ValueError("Unique lamps and one reserved lamp required")
    steps = [dict(id="all_off_start", role="base", lamp=None,
                  instruction="Deja todo quieto. Apaga las lámparas controlables.")]
    for lamp in ids:
        steps.extend([
            dict(id=f"{lamp}_on", role="held_out" if lamp == held_out else "fit", lamp=lamp,
                 instruction=f"Prende solo {lamp}, sin moverla."),
            dict(id=f"{lamp}_off", role="base", lamp=None,
                 instruction=f"Apaga {lamp}. Deja todo quieto.")])
    return dict(version=1, status="planned_not_acquired", lamps=steps,
                radiometry=[asdict(p) for p in plan()],
                guide_channels=["projected_text", "voice"], guide_off_during_measurement=True,
                projector_rgb_during_lamps=[0, 0, 0], minimum_stable_frames=4,
                controls="fixed exposure, focus and WB; otherwise validated reference compensation",
                emitter_bracket="short known exposure for unsaturated source; separate surface block",
                holdout="lamp power from separate reference patches; never evaluated material texels",
                motion="caller must check alignment; intensity stability alone does not certify no motion")


class StabilityGate:
    """Require an actual spatial lighting change, then consecutive stable frames.

    Inputs are aligned linear RGB [0,1], normalized to a fixed exposure/WB scale.
    Comparing frame differences rather than only means detects local movement.
    Saturated emitter pixels may be excluded with the supplied fixed ROI mask.
    """
    def __init__(self, baseline, *, mask=None, frames=4, change=0.015, tolerance=0.002, require_change=True):
        self.baseline = np.asarray(baseline, float)
        self.mask = np.ones(self.baseline.shape[:-1], bool) if mask is None else np.asarray(mask, bool)
        if (self.baseline.ndim != 3 or self.baseline.shape[-1] != 3 or
                self.mask.shape != self.baseline.shape[:-1] or not self.mask.any() or frames < 3):
            raise ValueError("RGB baseline, nonempty ROI and >=3 stable frames required")
        if not np.isfinite(self.baseline).all():
            raise ValueError("Nonfinite baseline")
        self.frames, self.change, self.tolerance = frames, change, tolerance
        self.require_change = require_change
        self.previous, self.count, self.last_time = None, 0, -np.inf

    def feed(self, frame, timestamp, *, guides_off=True, controls_valid=True):
        frame = np.asarray(frame, float)
        if frame.shape != self.baseline.shape or not np.isfinite(frame).all():
            raise ValueError("Frame shape/values differ")
        if not np.isfinite(timestamp) or timestamp <= self.last_time:
            raise ValueError("Strictly increasing finite timestamps required")
        self.last_time = timestamp
        roi = frame[self.mask]
        changed = not self.require_change or float(np.mean(np.abs(roi - self.baseline[self.mask]))) >= self.change
        stable = self.previous is not None and np.max(np.abs(roi - self.previous)) <= self.tolerance
        safe = guides_off and controls_valid and np.max(roi) < .995 and np.min(roi) >= 0
        self.count = (self.count + 1 if stable else 1) if changed and safe else 0
        self.previous = roi.copy()
        return dict(state="ready" if self.count >= self.frames else "settling" if changed else "waiting_change",
                    stable_frames=self.count, capture=self.count >= self.frames, timestamp=timestamp,
                    controls_valid=bool(controls_valid), guides_off=bool(guides_off))


def isolate(on, off_before, off_after, *, exposure=(1., 1., 1.), drift_tolerance=.003):
    """Known linear exposure ratios only; reject drifting ambient instead of fitting it."""
    if len(exposure) != 3 or not np.isfinite(exposure).all() or min(exposure) <= 0:
        raise ValueError("Known positive exposure ratios required")
    raw = [np.asarray(x, float) for x in (on, off_before, off_after)]
    if any(x.shape != raw[0].shape or not np.isfinite(x).all() for x in raw):
        raise ValueError("Aligned finite linear RGB required")
    a, b, c = [x / e for x, e in zip(raw, exposure)]
    drift = np.max(np.abs(b - c), axis=-1)
    valid = (drift <= drift_tolerance) & np.all(raw[0] < .995, axis=-1)
    valid &= np.all(raw[1] < .995, axis=-1) & np.all(raw[2] < .995, axis=-1)
    signal = a - (b + c) / 2
    valid &= np.all(signal >= -drift_tolerance, axis=-1)
    return np.maximum(signal, 0), valid, drift


def normalize_reference(frame, measured_patches, expected_patches, *, tolerance=.02):
    """Validate a per-channel exposure/WB gain against independently stable patches.

    A patch lit by the lamp being switched is NOT a stable exposure reference.
    Two or more unsaturated patches must have known unchanged illumination and
    span different brightnesses. Nonlinear tone mapping fails the consistency test.
    """
    measured, expected = np.asarray(measured_patches, float), np.asarray(expected_patches, float)
    if (measured.shape != expected.shape or measured.ndim != 2 or measured.shape[1] != 3 or len(measured) < 2
            or not np.isfinite(measured).all() or not np.isfinite(expected).all()
            or np.any(measured <= .01) or np.any(measured >= .98) or np.any(expected <= .01)
            or np.any(np.ptp(expected, axis=0) < .02)):
        raise ValueError("Two stable unsaturated reference patches at distinct levels required")
    ratios = measured / expected
    gain = np.median(ratios, axis=0)
    if np.max(np.abs(ratios/gain-1)) > tolerance:
        raise ValueError("Reference does not support linear exposure/WB compensation")
    return np.asarray(frame, float)/gain, gain

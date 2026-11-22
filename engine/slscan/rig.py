"""Formato de calibración del rig: `rig.json`, en mm, para cámaras y proyector.

Un proyector se describe igual que una cámara (una cámara al revés). Cada dispositivo
tiene tamaño en píxeles, K, coeficientes de distorsión, pose (R, t: mundo -> dispositivo)
y rms de la calibración. El mundo es `rig.world` (por defecto la primera cámara).

También lee los YAML de sltk (`procam_*.yaml`, OpenCV FileStorage) y los convierte.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path

import cv2
import numpy as np


def undistort_points(pixels, K, dist):
    """Converged inverse lens mapping on both OpenCV 4 and 5."""
    pixels = np.ascontiguousarray(pixels, dtype=float).reshape(-1, 1, 2)
    criteria = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 40, 1e-12)
    if hasattr(cv2, 'undistortPointsIter'):
        return cv2.undistortPointsIter(pixels, K, dist, None, None, criteria)
    return cv2.undistortPoints(pixels, K, dist, criteria=criteria)


@dataclass
class Device:
    name: str
    kind: str                      # "camera" | "projector"
    size: tuple[int, int]          # (ancho, alto) px
    K: np.ndarray                  # 3x3
    dist: np.ndarray               # (n,) 5 u 8 coeficientes OpenCV
    R: np.ndarray = field(default_factory=lambda: np.eye(3))   # mundo -> dispositivo
    t: np.ndarray = field(default_factory=lambda: np.zeros(3))  # mm
    rms: float | None = None
    gamma: float | None = None     # solo proyector: respuesta medida (F1)

    # --- derivados ---
    def fov_deg(self) -> tuple[float, float]:
        """Angular spans through principal axes, including offset and distortion."""
        w, h = self.size
        cx, cy = self.K[0, 2], self.K[1, 2]
        pixels = np.array([[-.5, cy], [w-.5, cy], [cx, -.5], [cx, h-.5]], float)
        xy = undistort_points(pixels, self.K, self.dist).reshape(-1, 2)
        rays = np.column_stack((xy, np.ones(4)))
        rays /= np.linalg.norm(rays, axis=1)[:, None]
        return tuple(float(np.degrees(np.arccos(np.clip(rays[i] @ rays[i+1], -1, 1)))) for i in (0, 2))

    def P(self) -> np.ndarray:
        """Matriz de proyección 3x4 = K [R | t]."""
        return self.K @ np.hstack([self.R, self.t.reshape(3, 1)])

    def center_world(self) -> np.ndarray:
        """Centro óptico en coordenadas del mundo."""
        return -self.R.T @ self.t

    def to_json(self) -> dict:
        transform = np.eye(4)
        transform[:3, :3], transform[:3, 3] = self.R, self.t.ravel()
        d = {
            "name": self.name, "kind": self.kind, "size": list(self.size),
            "K": self.K.tolist(), "dist": self.dist.ravel().tolist(),
            "R": self.R.tolist(), "t": self.t.ravel().tolist(),
            "rms": self.rms, "fov_deg": list(self.fov_deg()),
            "T_device_from_world": transform.tolist(),
        }
        if self.gamma is not None:
            d["gamma"] = self.gamma
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Device":
        return cls(
            name=d["name"], kind=d["kind"], size=tuple(d["size"]),
            K=np.array(d["K"], float), dist=np.array(d["dist"], float),
            R=np.array(d.get("R", np.eye(3).tolist()), float),
            t=np.array(d.get("t", [0, 0, 0]), float),
            rms=d.get("rms"), gamma=d.get("gamma"),
        )


@dataclass
class Rig:
    devices: dict[str, Device]
    world: str = "cam0"
    units: str = "mm"
    notes: str = ""
    extra: dict = field(default_factory=dict)   # p.ej. rectificación de sltk

    # --- acceso ---
    def cameras(self) -> list[Device]:
        return [d for d in self.devices.values() if d.kind == "camera"]

    def projector(self) -> Device:
        pros = [d for d in self.devices.values() if d.kind == "projector"]
        if len(pros) != 1:
            raise ValueError(f"se esperaba 1 proyector, hay {len(pros)}")
        return pros[0]

    def __getitem__(self, name: str) -> Device:
        return self.devices[name]

    # --- io ---
    def save(self, path: str | Path) -> None:
        data = {
            "format": "slscan-rig/1", "units": self.units, "world": self.world,
            "notes": self.notes,
            "devices": {k: v.to_json() for k, v in self.devices.items()},
        }
        report = self.extra.get('metric_report', {})
        if self.extra.get('pixel_convention') == 'opencv_integer_centers':
            data.update(format='slscan-rig/2', schema_version='1.0', artifact='rig',
                evidence=self.extra.get('evidence','recorded'),
                coordinate_convention='opencv_x_right_y_down_z_forward',
                pixel_convention='opencv_integer_centers', validated_range=None,
                metric_status='inconclusive' if report.get('status')=='rejected' else 'unverified',
                input_hashes={v['path']:v['sha256'] for v in self.extra.get('provenance',{}).get('observations',[])})
            for name, device in data['devices'].items():
                device.update(pose_estimated=name!=self.world,
                    model={'free':['fx','fy','cx','cy',report.get('model',{}).get('distortion','unknown')],
                           'fixed':report.get('model',{}).get('fixed',[])},
                    uncertainty=report.get('uncertainty',{}),
                    residuals_by_view=report.get('per_view',[]))
        if self.extra:
            data["extra"] = {k: (v.tolist() if isinstance(v, np.ndarray) else v)
                             for k, v in self.extra.items()}
        path = Path(path)
        temporary = path.with_suffix(path.suffix+'.tmp')
        temporary.write_text(json.dumps(data, indent=2, allow_nan=False))
        temporary.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> "Rig":
        path = Path(path)
        if path.suffix.lower() in (".yaml", ".yml", ".xml"):
            return cls.from_sltk_yaml(path)
        data = json.loads(path.read_text())
        if data.get('format') == 'slscan-rig/2':
            if data.get('schema_version') != '1.0' or data.get('pixel_convention') != 'opencv_integer_centers':
                raise ValueError('contrato de rig no compatible')
            for name, d in data['devices'].items():
                T = np.asarray(d['T_device_from_world'])
                if T.shape!=(4,4) or not (np.allclose(T[:3,:3],d['R']) and
                    np.allclose(T[:3,3],d['t']) and np.allclose(T[3],[0,0,0,1])):
                    raise ValueError(f'{name}: T_device_from_world inconsistente con R/t')
        devs = {k: Device.from_json(v) for k, v in data["devices"].items()}
        extra = {k: np.array(v) if isinstance(v, list) else v
                 for k, v in data.get("extra", {}).items()}
        if data.get('format') == 'slscan-rig/2':
            extra.setdefault('evidence',data['evidence'])
            extra.setdefault('pixel_convention',data['pixel_convention'])
        return cls(devices=devs, world=data.get("world", "cam0"),
                   units=data.get("units", "mm"), notes=data.get("notes", ""), extra=extra)

    # --- sltk ---
    @classmethod
    def from_sltk_yaml(cls, path: str | Path) -> "Rig":
        """Lee `procam_*.yaml` de sltk: cam_K, cam_kc, pro_K, pro_kc, R, T, R1, R2, P1, P2."""
        fs = cv2.FileStorage(str(path), cv2.FILE_STORAGE_READ)
        if not fs.isOpened():
            raise FileNotFoundError(path)

        def mat(name):
            node = fs.getNode(name)
            return None if node.empty() else node.mat()

        def num(name):
            node = fs.getNode(name)
            return None if node.empty() else float(node.real())

        cam_size = tuple(int(v) for v in mat("cam_size").ravel())
        pro_size = tuple(int(v) for v in mat("pro_size").ravel())
        R = mat("R")
        T = mat("T").ravel()
        cam = Device("cam0", "camera", cam_size, mat("cam_K"), mat("cam_kc").ravel(),
                     np.eye(3), np.zeros(3), num("cam_rms"))
        # sltk: X_pro = R X_cam + T  (stereoCalibrate con la cámara como primer dispositivo)
        pro = Device("proj", "projector", pro_size, mat("pro_K"), mat("pro_kc").ravel(),
                     R, T, num("pro_rms"))
        extra = {}
        for k in ("R1", "R2", "P1", "P2", "Q", "E"):
            m = mat(k)
            if m is not None:
                extra[k] = m
        if num("stereo_rms") is not None:
            extra["stereo_rms"] = num("stereo_rms")
        fs.release()
        return cls(devices={"cam0": cam, "proj": pro}, world="cam0",
                   notes=f"importado de sltk {Path(path).name}", extra=extra)


def stereo_rectify(a: Device, b: Device) -> dict[str, np.ndarray]:
    """R1, R2, P1, P2, Q de OpenCV para el par (a, b), con a como referencia.

    Reproduce lo que sltk guarda en el YAML (CALIB_ZERO_DISPARITY, alpha por defecto).
    """
    # pose relativa: X_b = R_ab X_a + t_ab
    R_ab = b.R @ a.R.T
    t_ab = (b.t - R_ab @ a.t).reshape(3, 1)
    R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(
        a.K, a.dist, b.K, b.dist, a.size, R_ab, t_ab, flags=cv2.CALIB_ZERO_DISPARITY)
    return {"R1": R1, "R2": R2, "P1": P1, "P2": P2, "Q": Q}

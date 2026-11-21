"""Chequeo del PC Windows (motor del escáner). Imprime JSON: GPU, Python, cámaras, displays, puertos serie (DMX), disco.

Se corre allá:  C:\\sl-scanner\\.venv\\Scripts\\python.exe windows\\doctor.py
o desde el Mac: .venv/bin/python -m slscan remote doctor --host <nombre>
Solo lee; no cambia nada.
"""

from __future__ import annotations

import ctypes
import json
import platform
import shutil
import subprocess
import sys


def gpu() -> dict:
    out = {"nvidia_smi": None, "torch": None}
    if shutil.which("nvidia-smi"):
        try:
            q = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used",
                                "--format=csv,noheader"], capture_output=True, text=True, timeout=20)
            out["nvidia_smi"] = [l.strip() for l in q.stdout.strip().splitlines()]
        except Exception as e:  # noqa: BLE001
            out["nvidia_smi"] = f"error: {e}"
    try:
        import torch
        out["torch"] = {"version": torch.__version__, "cuda": torch.version.cuda,
                        "available": torch.cuda.is_available(),
                        "devices": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]}
    except Exception as e:  # noqa: BLE001
        out["torch"] = f"no: {e}"
    return out


def cameras(max_index: int = 10) -> list[dict]:
    try:
        import cv2
    except Exception as e:  # noqa: BLE001
        return [{"error": f"opencv: {e}"}]
    found = []
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    for i in range(max_index):
        cap = cv2.VideoCapture(i, backend)
        if cap.isOpened():
            ok, f = cap.read()
            if ok and f is not None:
                found.append({"index": i, "width": f.shape[1], "height": f.shape[0],
                              "fps": cap.get(cv2.CAP_PROP_FPS), "backend": cap.getBackendName()})
        cap.release()
    return found


def displays() -> list[dict]:
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    mons = []

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    proc_t = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(RECT), ctypes.c_double)

    def cb(h, hdc, r, data):
        rc = r.contents
        mons.append({"index": len(mons), "x": rc.left, "y": rc.top, "w": rc.right - rc.left, "h": rc.bottom - rc.top})
        return 1

    user32.EnumDisplayMonitors(0, 0, proc_t(cb), 0)
    return mons


def serial_ports() -> list[dict]:
    try:
        from serial.tools import list_ports
    except Exception as e:  # noqa: BLE001
        return [{"error": f"pyserial: {e}"}]
    return [{"device": p.device, "description": p.description, "vid": p.vid, "pid": p.pid,
             "dmx_candidate": bool(p.vid == 0x0403)}      # FTDI: Enttec Open DMX / DMX USB Pro
            for p in list_ports.comports()]


def main() -> int:
    du = shutil.disk_usage("C:\\" if sys.platform == "win32" else "/")
    report = {
        "host": platform.node(), "os": platform.platform(), "python": sys.version.split()[0],
        "disk_free_gb": round(du.free / 1e9, 1), "disk_total_gb": round(du.total / 1e9, 1),
        "gpu": gpu(), "displays": displays(), "cameras": cameras(), "serial_ports": serial_ports(),
    }
    for mod in ("cv2", "numpy", "scipy", "serial", "open3d"):
        try:
            m = __import__(mod)
            report.setdefault("modules", {})[mod] = getattr(m, "__version__", "ok")
        except Exception as e:  # noqa: BLE001
            report.setdefault("modules", {})[mod] = f"no: {type(e).__name__}"
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

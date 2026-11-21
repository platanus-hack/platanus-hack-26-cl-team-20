"""Luz por Bluetooth LE con bleak (macOS CoreBluetooth, Windows WinRT). Síncrono hacia afuera.

El protocolo de la SmallRig RC 100 no es público: `lights.json` guarda lo que se aprenda por equipo:

    {"smallrig": {"address": "…", "name": "RC100B…", "write_char": "0000ffe1-…",
                  "commands": {"on": "aa01…", "off": "aa00…", "intensity": {"fmt": "aa02{v:02x}", "max": 100},
                               "cct": {"fmt": "aa03{k:04x}", "min": 2700, "max": 6500}}}}

Mientras no haya protocolo, `set` responde con lo que falta en vez de inventar bytes.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

LIGHTS_FILE = Path(os.environ.get("PLAYGROUND_STATE", Path.home() / ".local" / "state" / "sl-scanner")) / "lights.json"


def _run(coro, timeout: float = 60.0):
    """Corre una corutina en un hilo con su propio loop (bleak no comparte loop con el runtime)."""
    out: dict = {}

    def target():
        try:
            out["r"] = asyncio.run(asyncio.wait_for(coro, timeout))
        except Exception as e:  # noqa: BLE001
            out["e"] = e
    th = threading.Thread(target=target, daemon=True); th.start(); th.join(timeout + 5)
    if "e" in out:
        raise out["e"]
    return out.get("r")


def _bt_error(e: Exception) -> dict:
    msg = str(e)
    if "turned off" in msg or "powered on" in msg or "POWERED_OFF" in msg:
        return {"error": "Bluetooth apagado en esta máquina: hay que encenderlo (Ajustes › Bluetooth) antes de buscar el foco."}
    if "unauthorized" in msg.lower():
        return {"error": "esta app no tiene permiso de Bluetooth (Ajustes › Privacidad › Bluetooth)."}
    return {"error": msg}


def scan(seconds: float = 8.0, match: str = "") -> dict:
    """Equipos BLE a la vista, ordenados por señal. match filtra por nombre (p.ej. 'rc', 'smallrig')."""
    try:
        from bleak import BleakScanner
    except ImportError:
        return {"error": "falta bleak (uv pip install bleak)"}

    async def go():
        found = {}

        def cb(d, adv):
            found[d.address] = {"address": d.address, "name": d.name or adv.local_name or "", "rssi": adv.rssi,
                                "services": list(adv.service_uuids)[:4],
                                "manufacturer": {str(k): v.hex()[:32] for k, v in adv.manufacturer_data.items()}}
        s = BleakScanner(cb)
        await s.start(); await asyncio.sleep(seconds); await s.stop()
        devs = sorted(found.values(), key=lambda d: -(d["rssi"] or -200))
        if match:
            devs = [d for d in devs if match.lower() in d["name"].lower()]
        return devs
    try:
        return {"devices": _run(go(), seconds + 15)}
    except Exception as e:  # noqa: BLE001
        return _bt_error(e)


def dump_gatt(address: str, read_values: bool = True) -> dict:
    """Servicios y características del equipo (solo lectura)."""
    try:
        from bleak import BleakClient
    except ImportError:
        return {"error": "falta bleak"}

    async def go():
        async with BleakClient(address, timeout=20) as c:
            out = []
            for svc in c.services:
                chars = []
                for ch in svc.characteristics:
                    item = {"uuid": ch.uuid, "props": list(ch.properties), "desc": ch.description}
                    if read_values and "read" in ch.properties:
                        try:
                            item["value"] = (await c.read_gatt_char(ch)).hex()
                        except Exception as e:  # noqa: BLE001
                            item["value"] = f"err {e}"
                    chars.append(item)
                out.append({"service": svc.uuid, "desc": svc.description, "characteristics": chars})
            return {"address": address, "connected": True, "services": out}
    try:
        return _run(go(), 45)
    except Exception as e:  # noqa: BLE001
        return _bt_error(e)


def write_raw(address: str, char_uuid: str, data_hex: str, response: bool = True) -> dict:
    """Escribe bytes crudos en una característica (para aprender el protocolo con el foco a la vista)."""
    try:
        from bleak import BleakClient
    except ImportError:
        return {"error": "falta bleak"}

    async def go():
        async with BleakClient(address, timeout=20) as c:
            await c.write_gatt_char(char_uuid, bytes.fromhex(data_hex), response=response)
            return {"ok": True, "wrote": data_hex, "char": char_uuid}
    try:
        return _run(go(), 40)
    except Exception as e:  # noqa: BLE001
        return _bt_error(e)


def _load() -> dict:
    try:
        return json.loads(LIGHTS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(d: dict) -> None:
    LIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LIGHTS_FILE.write_text(json.dumps(d, indent=2))


class BleLight:
    """Una luz BLE con protocolo aprendido en lights.json."""

    def __init__(self, light_id: str = "smallrig"):
        self.id = light_id
        self.cfg = _load().get(light_id, {})

    def learn(self, **fields) -> dict:
        d = _load(); d.setdefault(self.id, {}).update({k: v for k, v in fields.items() if v is not None}); _save(d)
        self.cfg = d[self.id]
        return self.cfg

    def status(self) -> dict:
        missing = [k for k in ("address", "write_char", "commands") if k not in self.cfg]
        return {"light": self.id, "config": self.cfg, "ready": not missing,
                "missing": missing, "file": str(LIGHTS_FILE)}

    def _cmd(self, key: str, **kw) -> str | None:
        c = (self.cfg.get("commands") or {}).get(key)
        if c is None:
            return None
        if isinstance(c, str):
            return c
        return c["fmt"].format(**kw)

    def set(self, on: bool | None = None, intensity: float | None = None, cct: int | None = None,
            rgb: list[int] | None = None) -> dict:
        st = self.status()
        if not st["ready"]:
            return {"error": f"protocolo del foco no aprendido todavía: faltan {st['missing']} en {LIGHTS_FILE}. "
                             f"Pasos: pg_light scan → pg_light dump → aprender comandos (ver playground/lights)."}
        results = []
        cmds = self.cfg["commands"]
        if on is not None:
            results.append(("on" if on else "off", self._cmd("on" if on else "off")))
        if intensity is not None and "intensity" in cmds:
            mx = cmds["intensity"].get("max", 100)
            results.append(("intensity", self._cmd("intensity", v=int(round(max(0, min(1, intensity)) * mx)))))
        if cct is not None and "cct" in cmds:
            lo, hi = cmds["cct"].get("min", 2700), cmds["cct"].get("max", 6500)
            results.append(("cct", self._cmd("cct", k=int(max(lo, min(hi, cct))))))
        if rgb is not None and "rgb" in cmds:
            results.append(("rgb", self._cmd("rgb", r=rgb[0], g=rgb[1], b=rgb[2])))
        done = []
        for name, data in results:
            if data is None:
                done.append({name: "sin comando"}); continue
            r = write_raw(self.cfg["address"], self.cfg["write_char"], data)
            done.append({name: r})
            if "error" in r:
                break
        return {"light": self.id, "applied": done}

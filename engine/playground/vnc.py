"""Cliente VNC propio (RFB 3.3/3.8) para controlar el PC desde el Mac: captura, clic, tecla.

Sin dependencias fuera de numpy/cv2. Autenticación VNC (tipo 2: desafío de 16 bytes cifrado con DES y la
contraseña con los bits de cada byte invertidos, como hacen todos los servidores VNC). Encoding Raw.

    python -m playground vnc screenshot --out reports/pc_desktop.png
    python -m playground vnc click --x 100 --y 100
    python -m playground vnc key --text "hola"

Host y contraseña: `VNC_HOST` (host:puerto, por defecto 192.168.1.233:5900) y `VNC_PASSWORD`; si no está, se lee
del llavero del Mac (`security find-generic-password -a max -s sl-scanner-vnc -w`). Nunca se imprime.
"""

from __future__ import annotations

import os
import socket
import struct
import subprocess
import time

import numpy as np

DEFAULT_HOST = "192.168.1.233:5900"
KEYCHAIN = ("max", "sl-scanner-vnc")


# ----------------------------------------------------------------------------- DES (solo para el desafío VNC)

_PC1 = [57, 49, 41, 33, 25, 17, 9, 1, 58, 50, 42, 34, 26, 18, 10, 2, 59, 51, 43, 35, 27, 19, 11, 3, 60, 52, 44, 36,
        63, 55, 47, 39, 31, 23, 15, 7, 62, 54, 46, 38, 30, 22, 14, 6, 61, 53, 45, 37, 29, 21, 13, 5, 28, 20, 12, 4]
_PC2 = [14, 17, 11, 24, 1, 5, 3, 28, 15, 6, 21, 10, 23, 19, 12, 4, 26, 8, 16, 7, 27, 20, 13, 2, 41, 52, 31, 37, 47, 55,
        30, 40, 51, 45, 33, 48, 44, 49, 39, 56, 34, 53, 46, 42, 50, 36, 29, 32]
_IP = [58, 50, 42, 34, 26, 18, 10, 2, 60, 52, 44, 36, 28, 20, 12, 4, 62, 54, 46, 38, 30, 22, 14, 6, 64, 56, 48, 40, 32,
       24, 16, 8, 57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3, 61, 53, 45, 37, 29, 21, 13, 5, 63, 55, 47,
       39, 31, 23, 15, 7]
_FP = [40, 8, 48, 16, 56, 24, 64, 32, 39, 7, 47, 15, 55, 23, 63, 31, 38, 6, 46, 14, 54, 22, 62, 30, 37, 5, 45, 13, 53,
       21, 61, 29, 36, 4, 44, 12, 52, 20, 60, 28, 35, 3, 43, 11, 51, 19, 59, 27, 34, 2, 42, 10, 50, 18, 58, 26, 33, 1,
       41, 9, 49, 17, 57, 25]
_E = [32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9, 8, 9, 10, 11, 12, 13, 12, 13, 14, 15, 16, 17, 16, 17, 18, 19, 20, 21, 20, 21,
      22, 23, 24, 25, 24, 25, 26, 27, 28, 29, 28, 29, 30, 31, 32, 1]
_P = [16, 7, 20, 21, 29, 12, 28, 17, 1, 15, 23, 26, 5, 18, 31, 10, 2, 8, 24, 14, 32, 27, 3, 9, 19, 13, 30, 6, 22, 11, 4,
      25]
_SHIFTS = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]
_S = [
    [14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7, 0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
     4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0, 15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13],
    [15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10, 3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5,
     0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15, 13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9],
    [10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8, 13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
     13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7, 1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12],
    [7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15, 13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
     10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4, 3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14],
    [2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9, 14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
     4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14, 11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3],
    [12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11, 10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
     9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6, 4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13],
    [4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1, 13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
     1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2, 6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12],
    [13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7, 1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
     7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8, 2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11],
]


def _bits(data: bytes) -> list[int]:
    return [(b >> (7 - i)) & 1 for b in data for i in range(8)]


def _unbits(bits: list[int]) -> bytes:
    return bytes(sum(bits[i + j] << (7 - j) for j in range(8)) for i in range(0, len(bits), 8))


def _perm(bits: list[int], table: list[int]) -> list[int]:
    return [bits[i - 1] for i in table]


def _subkeys(key: bytes) -> list[list[int]]:
    k = _perm(_bits(key), _PC1)
    c, d = k[:28], k[28:]
    out = []
    for s in _SHIFTS:
        c, d = c[s:] + c[:s], d[s:] + d[:s]
        out.append(_perm(c + d, _PC2))
    return out


def _f(r: list[int], k: list[int]) -> list[int]:
    e = [a ^ b for a, b in zip(_perm(r, _E), k)]
    out = []
    for i in range(8):
        chunk = e[i * 6:(i + 1) * 6]
        row = (chunk[0] << 1) | chunk[5]
        col = (chunk[1] << 3) | (chunk[2] << 2) | (chunk[3] << 1) | chunk[4]
        v = _S[i][row * 16 + col]
        out += [(v >> 3) & 1, (v >> 2) & 1, (v >> 1) & 1, v & 1]
    return _perm(out, _P)


def des_encrypt_block(key: bytes, block: bytes) -> bytes:
    bits = _perm(_bits(block), _IP)
    left, right = bits[:32], bits[32:]
    for k in _subkeys(key):
        left, right = right, [a ^ b for a, b in zip(left, _f(right, k))]
    return _unbits(_perm(right + left, _FP))


def vnc_auth_response(password: str, challenge: bytes) -> bytes:
    """Respuesta al desafío VNC: DES con la clave = contraseña (8 bytes) con los bits de cada byte invertidos."""
    key = (password.encode("latin-1")[:8]).ljust(8, b"\0")
    key = bytes(int(f"{b:08b}"[::-1], 2) for b in key)
    return b"".join(des_encrypt_block(key, challenge[i:i + 8]) for i in range(0, 16, 8))


# ----------------------------------------------------------------------------- cliente

def keychain_password() -> str:
    pw = os.environ.get("VNC_PASSWORD")
    if pw:
        return pw
    r = subprocess.run(["security", "find-generic-password", "-a", KEYCHAIN[0], "-s", KEYCHAIN[1], "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("sin contraseña VNC: ni VNC_PASSWORD ni la entrada sl-scanner-vnc del llavero")
    return r.stdout.strip()


class VNC:
    def __init__(self, host: str | None = None, password: str | None = None, timeout: float = 10.0):
        h = host or os.environ.get("VNC_HOST", DEFAULT_HOST)
        self.host, _, port = h.partition(":")
        self.port = int(port or 5900)
        self.password = password
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.width = self.height = 0
        self.name = ""
        self.bpp = 32

    def _recv(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("VNC: conexión cerrada")
            buf += chunk
        return buf

    def connect(self) -> dict:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        server_ver = self._recv(12)                         # b"RFB 003.008\n"
        minor = int(server_ver[8:11])
        self.sock.sendall(b"RFB 003.008\n" if minor >= 8 else b"RFB 003.003\n")
        if minor >= 7:
            n = self._recv(1)[0]
            if n == 0:
                raise ConnectionError("VNC: " + self._recv(struct.unpack(">I", self._recv(4))[0]).decode(errors="replace"))
            types = list(self._recv(n))
            sec = 2 if 2 in types else (1 if 1 in types else None)
            if sec is None:
                raise ConnectionError(f"VNC: tipos de seguridad no soportados {types}")
            self.sock.sendall(bytes([sec]))
        else:
            sec = struct.unpack(">I", self._recv(4))[0]
        if sec == 2:
            challenge = self._recv(16)
            self.sock.sendall(vnc_auth_response(self.password or keychain_password(), challenge))
        if sec in (1, 2) and minor >= 3:
            if not (sec == 1 and minor < 8):
                result = struct.unpack(">I", self._recv(4))[0]
                if result != 0:
                    if minor >= 8:
                        try:
                            reason = self._recv(struct.unpack(">I", self._recv(4))[0]).decode(errors="replace")
                        except Exception:  # noqa: BLE001
                            reason = ""
                    else:
                        reason = ""
                    raise PermissionError(f"VNC: autenticación rechazada {reason}".strip())
        self.sock.sendall(b"\x01")                          # shared
        init = self._recv(24)
        self.width, self.height, self.bpp = struct.unpack(">HHB", init[:5])
        name_len = struct.unpack(">I", init[20:24])[0]
        self.name = self._recv(name_len).decode(errors="replace")
        # formato fijo: 32 bpp, 24 depth, little endian, true colour, max 255, shifts 16/8/0 (BGRX)
        self.sock.sendall(b"\x00\x00\x00\x00" + struct.pack(">BBBBHHHBBB", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0) + b"\x00\x00\x00")
        self.sock.sendall(struct.pack(">BBH", 2, 0, 1) + struct.pack(">i", 0))   # encodings: Raw
        self.bpp = 32
        return {"host": f"{self.host}:{self.port}", "size": [self.width, self.height], "name": self.name}

    def close(self) -> None:
        if self.sock:
            self.sock.close(); self.sock = None

    def screenshot(self, incremental: bool = False, retries: int = 2) -> np.ndarray:
        """Framebuffer completo como imagen BGR (h, w, 3). TightVNC manda la primera actualización en negro
        (todavía no capturó la pantalla): si sale toda en cero se vuelve a pedir."""
        img = self._update(incremental)
        for _ in range(retries):
            if img.max() > 0:
                break
            time.sleep(0.2)
            img = self._update(incremental)
        return img

    def _update(self, incremental: bool = False) -> np.ndarray:
        self.sock.sendall(struct.pack(">BBHHHH", 3, int(incremental), 0, 0, self.width, self.height))
        img = np.zeros((self.height, self.width, 3), np.uint8)
        got = 0
        deadline = time.time() + max(self.timeout, 20)
        while got == 0 and time.time() < deadline:
            mtype = self._recv(1)[0]
            if mtype == 0:                                  # FramebufferUpdate
                self._recv(1)
                n_rects = struct.unpack(">H", self._recv(2))[0]
                for _ in range(n_rects):
                    x, y, w, h, enc = struct.unpack(">HHHHi", self._recv(12))
                    if enc == 0:
                        data = self._recv(w * h * 4)
                        arr = np.frombuffer(data, np.uint8).reshape(h, w, 4)
                        img[y:y + h, x:x + w] = arr[..., :3]           # B G R X con shifts 16/8/0 little-endian
                        got += 1
                    elif enc == -223:                                    # DesktopSize
                        self.width, self.height = w, h
                        img = np.zeros((h, w, 3), np.uint8)
                    else:
                        raise ValueError(f"VNC: encoding no soportado {enc}")
            elif mtype == 1:                                # SetColourMapEntries
                _, first, n = struct.unpack(">BHH", self._recv(5)); self._recv(6 * n)
            elif mtype == 2:                                # Bell
                pass
            elif mtype == 3:                                # ServerCutText
                self._recv(3); self._recv(struct.unpack(">I", self._recv(4))[0])
        return img

    def pointer(self, x: int, y: int, buttons: int = 0) -> None:
        self.sock.sendall(struct.pack(">BBHH", 5, buttons, x, y))

    def click(self, x: int, y: int, button: int = 1, hold_s: float = 0.05) -> None:
        self.pointer(x, y, 0); time.sleep(0.03)
        self.pointer(x, y, 1 << (button - 1)); time.sleep(hold_s)
        self.pointer(x, y, 0)

    def key(self, keysym: int, down: bool) -> None:
        self.sock.sendall(struct.pack(">BBHI", 4, int(down), 0, keysym))

    def type_text(self, text: str) -> None:
        for ch in text:
            ks = ord(ch) if ord(ch) < 0x100 else 0x01000000 | ord(ch)
            if ch == "\n":
                ks = 0xFF0D
            self.key(ks, True); self.key(ks, False); time.sleep(0.02)


def main(argv=None) -> int:
    import argparse
    import cv2
    p = argparse.ArgumentParser(prog="playground vnc")
    p.add_argument("action", choices=["info", "screenshot", "click", "key"])
    p.add_argument("--host"); p.add_argument("--out", default="reports/pc_desktop.png")
    p.add_argument("--x", type=int, default=0); p.add_argument("--y", type=int, default=0)
    p.add_argument("--button", type=int, default=1); p.add_argument("--text", default="")
    a = p.parse_args(argv)
    v = VNC(a.host)
    info = v.connect()
    try:
        if a.action == "info":
            print(info)
        elif a.action == "screenshot":
            img = v.screenshot(); cv2.imwrite(a.out, img); print(a.out, img.shape[1], "x", img.shape[0])
        elif a.action == "click":
            v.click(a.x, a.y, a.button); print(f"clic en ({a.x},{a.y})")
        elif a.action == "key":
            v.type_text(a.text); print(f"{len(a.text)} teclas")
    finally:
        v.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Captura: cámaras, ventana de patrones en el display del proyector, latencia y captura guiada.

Todo con OpenCV (AVFoundation en macOS). Los displays se ubican con CoreGraphics para
abrir la ventana de patrones a pantalla completa en el proyector. Las capturas se guardan
en PNG sin compresión.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np


# ----------------------------------------------------------------------------- displays

def list_displays() -> list[dict]:
    """[{index, x, y, w, h}] en puntos; en macOS vía CoreGraphics, si no, un display en 0,0."""
    if sys.platform != "darwin":
        return [{"index": 0, "x": 0, "y": 0, "w": 1920, "h": 1080}]
    cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))

    class Rect(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]

    cg.CGDisplayBounds.restype = Rect
    cg.CGDisplayIsMain.restype = ctypes.c_int
    cg.CGDisplayIsBuiltin.restype = ctypes.c_int
    n = ctypes.c_uint32(0); ids = (ctypes.c_uint32 * 16)()
    cg.CGGetActiveDisplayList(16, ids, ctypes.byref(n))
    out = []
    for i in range(n.value):
        r = cg.CGDisplayBounds(ids[i])
        out.append({"index": i, "x": int(r.x), "y": int(r.y), "w": int(r.w), "h": int(r.h),
                    "pixel_w": int(cg.CGDisplayPixelsWide(ids[i])), "pixel_h": int(cg.CGDisplayPixelsHigh(ids[i])),
                    "main": bool(cg.CGDisplayIsMain(ids[i])), "builtin": bool(cg.CGDisplayIsBuiltin(ids[i]))})
    return out


SIDECAR = (1194, 756)       # el iPad por Sidecar: ahí está la terminal de Max, nunca se proyecta ahí


def is_reserved(d: dict) -> bool:
    """Display donde vive la terminal de Max: el interno del MacBook o el Sidecar (1194x756).
    El proyector puede quedar como 'principal' (pasa cuando el Mac entra en espejo con el Sidecar): eso sí se permite."""
    return bool(d.get("builtin")) or (d["w"], d["h"]) == SIDECAR


def resolve_screen(spec, allow_main: bool = False) -> int:
    """Índice de display desde un número, 'WxH' (p.ej. '1920x1080' = T749, '1024x768' = PJ588D) o 'WxH@X,Y' (origen
    del display en puntos, para cuando hay varios monitores del mismo tamaño: el Projector-6325 por AirPlay vive en
    28,1080 junto a dos monitores 1920x1080). La variable SLSCAN_SCREEN reemplaza cualquier spec 'WxH' que venga del
    código (dale, recalib, radiometría, runtime). Nunca el interno ni el Sidecar (ahí está la terminal de Max) salvo
    allow_main. Con varios candidatos y sin origen, error: antes se tomaba el primero y podía ser el monitor de Max."""
    disp = list_displays()
    is_index = isinstance(spec, int) or str(spec).isdigit()
    env = os.environ.get("SLSCAN_SCREEN", "").strip()
    if env and not is_index:
        spec = env
        is_index = spec.isdigit()
    if is_index:
        idx = int(spec)
    else:
        s = str(spec).lower()
        origin = None
        if "@" in s:
            s, o = s.split("@", 1)
            origin = tuple(int(float(v)) for v in o.split(","))
        w, h = (int(v) for v in s.split("x"))
        cands = [d for d in disp if d["w"] == w and d["h"] == h and not is_reserved(d)]
        if origin is not None:
            cands = [d for d in cands if (d["x"], d["y"]) == origin]
        if not cands:
            raise RuntimeError(f"no hay un display externo de {spec} (hay: {[(d['index'], d['w'], d['h'], d['x'], d['y']) for d in disp]})")
        if len(cands) > 1:
            raise RuntimeError(f"hay {len(cands)} displays de {w}x{h}: {[(d['index'], d['x'], d['y']) for d in cands]}; "
                               f"usa 'WxH@X,Y' o SLSCAN_SCREEN para elegir el proyector")
        idx = cands[0]["index"]
    d = disp[min(idx, len(disp) - 1)]
    if is_reserved(d) and not allow_main:
        raise RuntimeError(f"el display {idx} es el interno o el Sidecar (la terminal de Max): no se usa para patrones")
    return idx


def place_on_builtin(name: str, w: int = 960, h: int = 600) -> None:
    """Mueve una ventana de guía a la pantalla interna del MacBook (nunca al display principal si es el iPad)."""
    disp = list_displays()
    target = next((d for d in disp if d["builtin"]), None) or next((d for d in disp if not d["main"]), disp[0])
    cv2.imshow(name, np.zeros((h // 2, w // 2, 3), np.uint8)); cv2.waitKey(30)
    cv2.resizeWindow(name, w, h); cv2.moveWindow(name, target["x"] + 40, target["y"] + 60); cv2.waitKey(30)


LOG = Path(os.environ.get("PLAYGROUND_STATE", Path.home() / ".local" / "state" / "sl-scanner")) / "pattern.log"


def _log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


class PatternWindow:
    """Ventana SIN bordes que cubre exactamente el display `screen` (SDL/pygame). No se cierra sola: solo con
    close() o la tecla q. En macOS OpenCV no logra pantalla completa real (deja la barra de título): por eso SDL."""

    def __init__(self, size: tuple[int, int], screen=1, fullscreen: bool = True, name: str = "slscan-pattern",
                 allow_main: bool = False):
        self.size = size
        self.name = name
        disp = list_displays()
        screen = resolve_screen(screen, allow_main)
        d = disp[min(screen, len(disp) - 1)]
        self.display = d
        self.backend = "sdl"
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{d['x']},{d['y']}"
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        # sin Spaces: el fullscreen "de Spaces" de macOS deja en negro las otras pantallas (el iPad de Max con su
        # terminal) cuando "pantallas con espacios separados" está apagado; el fullscreen clásico no.
        os.environ["SDL_VIDEO_MAC_FULLSCREEN_SPACES"] = "0"
        # el fullscreen clásico se minimiza solo cuando otra ventana toma el foco (quedaba el proyector en negro
        # mientras otro proceso abría algo): que se quede puesto
        os.environ["SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS"] = "0"
        # SIEMPRE con vsync: presentar con "immediate swap" (sin sincronía vertical) sobre el proyector por HDMI
        # provocó dos kernel panics del controlador de pantalla externa (DCPEXT1 isr_immediate_swap_failed, 8-nov).
        # El hint hace que cualquier renderer que SDL cree (incluido el del framebuffer de la ventana) lleve
        # PRESENTVSYNC; además set_mode pide vsync explícito (requiere SCALED) y el bucle se limita al refresco.
        os.environ["SDL_RENDER_VSYNC"] = "1"
        os.environ["SDL_VIDEO_DOUBLE_BUFFER"] = "1"
        import pygame
        self.pg = pygame
        pygame.display.init()
        # si el proyector quedó como display principal, la barra de menú y el Dock de macOS tapan una ventana sin
        # bordes: con FULLSCREEN SDL los esconde. En un display secundario basta NOFRAME.
        # NUNCA fullscreen de macOS: el fullscreen clásico dejaba en negro la pantalla interna del Mac (y el iPad en
        # espejo) mientras calibraba. Ventana sin bordes del tamaño exacto del display. Para que la barra de menú no
        # quede sobre el proyector, el proyector no debe ser el display principal: si lo es y hay interno, se
        # reordena con displayplacer (interno principal en (0,0), proyector a su derecha) y se guarda el rollback.
        if d.get("main"):                       # CoreGraphics no marca 'builtin' cuando el interno está en espejo con el Sidecar
            d = _make_builtin_main(disp, d) or d
            os.environ["SDL_VIDEO_WINDOW_POS"] = f"{d['x']},{d['y']}"
        flags = pygame.NOFRAME if fullscreen else 0
        w, h = (d["w"], d["h"]) if fullscreen else (size[0] // 2, size[1] // 2)
        try:
            self.win = pygame.display.set_mode((w, h), flags | pygame.SCALED, display=screen if fullscreen else 0, vsync=1)
            self.vsync = True
        except Exception as e:  # noqa: BLE001
            _log(f"set_mode con vsync falló ({e!r}); reintento sin SCALED (el hint SDL_RENDER_VSYNC sigue activo)")
            self.win = pygame.display.set_mode((w, h), flags, display=screen if fullscreen else 0)
            self.vsync = False
        self.refresh_hz = self._refresh_hz(d)
        self._t_flip = 0.0
        _log(f"vsync={'sí' if self.vsync else 'solo hint'} refresco={self.refresh_hz:.0f} Hz")
        pygame.display.set_caption(name)
        try:
            pygame.mouse.set_visible(False)
        except Exception:  # noqa: BLE001
            pass
        self.win_size = (w, h)
        _log(f"ventana SDL {w}x{h} en display {screen} ({d['x']},{d['y']}) fullscreen={fullscreen}")
        if fullscreen:
            self._raise_above_menubar()
        self.show(np.zeros((size[1], size[0]), np.uint8))

    def _raise_above_menubar(self) -> None:
        """Con «pantallas con espacios separados» cada display tiene barra de menú y tapa los 24 px de arriba de
        una ventana sin bordes (se vio en el Projector-6325 el 2-nov). Subir el nivel de la NSWindow por sobre la
        barra de menú la deja proyectar completa, sin fullscreen de macOS y sin tocar los displays de Max."""
        if sys.platform != "darwin":
            return
        try:
            import objc  # pyobjc
            from AppKit import NSMainMenuWindowLevel
            ptr = self.pg.display.get_wm_info().get("window")
            if not ptr:
                _log("sin NSWindow en wm_info: la barra de menú puede tapar arriba"); return
            if not isinstance(ptr, int):                 # pygame entrega un PyCapsule con el puntero a la NSWindow
                import ctypes
                ctypes.pythonapi.PyCapsule_GetName.restype = ctypes.c_char_p
                ctypes.pythonapi.PyCapsule_GetName.argtypes = [ctypes.py_object]
                ctypes.pythonapi.PyCapsule_GetPointer.restype = ctypes.c_void_p
                ctypes.pythonapi.PyCapsule_GetPointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
                name = ctypes.pythonapi.PyCapsule_GetName(ptr)
                ptr = ctypes.pythonapi.PyCapsule_GetPointer(ptr, name)
            win = objc.objc_object(c_void_p=ptr)
            win.setLevel_(int(NSMainMenuWindowLevel) + 1)
            win.setHidesOnDeactivate_(False)
            self._nswin = win
            _log("ventana por sobre la barra de menú")
        except Exception as e:  # noqa: BLE001
            _log(f"no pude subir la ventana sobre la barra de menú: {e!r}")

    def _refresh_hz(self, d: dict) -> float:
        """Frecuencia de refresco del display elegido (CoreGraphics); 60 si no se sabe (o si informa 0, como AirPlay)."""
        if sys.platform != "darwin":
            return 60.0
        try:
            import Quartz
            _, ids, _ = Quartz.CGGetActiveDisplayList(16, None, None)
            for i in ids:
                b = Quartz.CGDisplayBounds(i)
                if (int(b.origin.x), int(b.origin.y)) == (int(d["x"]), int(d["y"])):
                    hz = Quartz.CGDisplayModeGetRefreshRate(Quartz.CGDisplayCopyDisplayMode(i))
                    return float(hz) if hz and hz > 1 else 60.0
        except Exception as e:  # noqa: BLE001
            _log(f"refresco desconocido: {e!r}")
        return 60.0

    # --- el display puede desaparecer (AirPlay que se corta): nunca caer sobre los monitores de trabajo -------------
    def display_present(self) -> bool:
        """True si sigue existiendo un display con el mismo origen y tamaño que el que se pidió al abrir la ventana."""
        if sys.platform != "darwin":
            return True
        try:
            import Quartz
            d = self.display
            _, ids, _ = Quartz.CGGetActiveDisplayList(16, None, None)
            for i in ids:
                b = Quartz.CGDisplayBounds(i)
                if (int(b.origin.x), int(b.origin.y), int(b.size.width), int(b.size.height)) == (int(d["x"]), int(d["y"]), int(d["w"]), int(d["h"])):
                    return True
            return False
        except Exception as e:  # noqa: BLE001
            _log(f"display_present falló: {e!r}")
            return True

    def hide(self) -> None:
        """Saca la ventana de pantalla (orderOut): no se dibuja en ningún display mientras el proyector no está."""
        win = getattr(self, "_nswin", None)
        if win is not None and not getattr(self, "_oculta", False):
            try:
                win.orderOut_(None); self._oculta = True; _log("proyector ausente: ventana oculta")
            except Exception as e:  # noqa: BLE001
                _log(f"hide falló: {e!r}")

    def unhide(self) -> None:
        """Vuelve a mostrar la ventana en el origen del proyector (coordenadas Cocoa: y medido desde abajo del display principal)."""
        win = getattr(self, "_nswin", None)
        if win is not None and getattr(self, "_oculta", False):
            try:
                from AppKit import NSScreen
                d = self.display
                main_h = NSScreen.screens()[0].frame().size.height
                win.setFrameOrigin_((float(d["x"]), float(main_h - (d["y"] + d["h"]))))
                win.orderFront_(None); self._oculta = False; _log("proyector de vuelta: ventana visible")
            except Exception as e:  # noqa: BLE001
                _log(f"unhide falló: {e!r}")

    @property
    def oculta(self) -> bool:
        return bool(getattr(self, "_oculta", False))

    def show(self, img: np.ndarray) -> None:
        try:
            if img.ndim == 2:
                img = np.repeat(img[..., None], 3, axis=2)
            if (img.shape[1], img.shape[0]) != self.win_size:
                img = cv2.resize(img, self.win_size, interpolation=cv2.INTER_NEAREST)
            rgb = np.ascontiguousarray(img[..., ::-1])               # BGR -> RGB
            surf = self.pg.surfarray.make_surface(rgb.swapaxes(0, 1))
            self.win.blit(surf, (0, 0))
            # nunca más de un flip por período de refresco, aunque el vsync no se haya aplicado
            hz = max(1.0, float(getattr(self, "refresh_hz", 60.0)))
            espera = (getattr(self, "_t_flip", 0.0) + 1.0 / hz) - time.monotonic()
            if espera > 0:
                time.sleep(espera)
            self.pg.display.flip()
            self._t_flip = time.monotonic()
            self.pg.event.pump()
        except Exception as e:  # noqa: BLE001
            _log(f"show falló: {e!r}")
            raise

    def solid(self, level: int) -> None:
        self.show(np.full((self.size[1], self.size[0]), level, np.uint8))

    def show_test(self) -> None:
        w, h = self.size
        img = np.zeros((h, w, 3), np.uint8)
        cv2.rectangle(img, (0, 0), (w - 1, h - 1), (255, 255, 255), 2)
        for x in range(0, w, 100):
            cv2.line(img, (x, 0), (x, h), (80, 80, 80), 1)
        for y in range(0, h, 100):
            cv2.line(img, (0, y), (w, y), (80, 80, 80), 1)
        cv2.circle(img, (w // 2, h // 2), 40, (0, 200, 255), 3)
        cv2.putText(img, f"{w}x{h}  display {self.display['index']}", (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)
        self.show(img)

    def wait_key(self, ms: int = 0) -> int:
        """Espera `ms` (0 = hasta una tecla) y devuelve la tecla (ord) o 255. Mantiene la ventana viva."""
        t0 = time.time()
        while True:
            for ev in self.pg.event.get():
                if ev.type == self.pg.KEYDOWN:
                    return ev.key if ev.key < 256 else 255
            if ms and (time.time() - t0) * 1000 >= ms:
                return 255
            time.sleep(0.01)

    def close(self) -> None:
        try:
            # con FULLSCREEN en macOS, display.quit() solo no suelta la pantalla: el cuadro viejo seguía proyectado
            # mientras otro proceso abría su ventana (se vio en las capturas del banco de trabajo con el ChArUco encima)
            self.win.fill((0, 0, 0)); self.pg.display.flip(); self.pg.event.pump()
            self.pg.display.quit(); self.pg.quit(); time.sleep(0.5)
            _log("ventana SDL cerrada")
        except Exception as e:  # noqa: BLE001
            _log(f"close: {e!r}")


class _CvPatternWindow:
    """Respaldo con OpenCV (en macOS deja barra de título)."""

    def __init__(self, size: tuple[int, int], screen=1, fullscreen: bool = True, name: str = "slscan-pattern",
                 allow_main: bool = False):
        self.size = size
        self.name = name
        disp = list_displays()
        screen = resolve_screen(screen, allow_main)
        d = disp[min(screen, len(disp) - 1)]
        self.display = d
        cv2.namedWindow(name, cv2.WINDOW_NORMAL | cv2.WINDOW_GUI_NORMAL)
        # en macOS la ventana tiene que existir (imshow) antes de moverla; el fullscreen toma el display donde está
        cv2.imshow(name, np.zeros((size[1] // 4, size[0] // 4), np.uint8)); cv2.waitKey(50)
        cv2.resizeWindow(name, size[0] // 2, size[1] // 2)
        cv2.moveWindow(name, d["x"] + 20, d["y"] + 20); cv2.waitKey(200)
        cv2.moveWindow(name, d["x"] + 20, d["y"] + 20); cv2.waitKey(100)
        if fullscreen:
            cv2.setWindowProperty(name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN); cv2.waitKey(200)
        self.show(np.zeros((size[1], size[0]), np.uint8))

    def show(self, img: np.ndarray) -> None:
        if img.shape[1] != self.size[0] or img.shape[0] != self.size[1]:
            img = cv2.resize(img, self.size, interpolation=cv2.INTER_NEAREST)
        cv2.imshow(self.name, img)
        cv2.waitKey(1)

    def solid(self, level: int) -> None:
        self.show(np.full((self.size[1], self.size[0]), level, np.uint8))

    def show_test(self) -> None:
        w, h = self.size
        img = np.zeros((h, w, 3), np.uint8)
        cv2.rectangle(img, (0, 0), (w - 1, h - 1), (255, 255, 255), 2)
        for x in range(0, w, 100):
            cv2.line(img, (x, 0), (x, h), (80, 80, 80), 1)
        for y in range(0, h, 100):
            cv2.line(img, (0, y), (w, y), (80, 80, 80), 1)
        cv2.circle(img, (w // 2, h // 2), 40, (0, 200, 255), 3)
        cv2.putText(img, f"{w}x{h}  display {self.display['index']}", (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)
        self.show(img)

    def wait_key(self, ms: int = 0) -> int:
        return cv2.waitKey(ms) & 0xFF

    def close(self) -> None:
        try:
            cv2.destroyWindow(self.name)
        except cv2.error:
            pass


# ----------------------------------------------------------------------------- cámaras

def list_cameras(max_index: int = 8) -> list[dict]:
    out = []
    backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
    for i in range(max_index):
        cap = cv2.VideoCapture(i, backend)
        if not cap.isOpened():
            cap.release(); continue
        ok, frame = cap.read()
        if ok and frame is not None:
            out.append({"index": i, "width": frame.shape[1], "height": frame.shape[0],
                        "fps": cap.get(cv2.CAP_PROP_FPS), "backend": cap.getBackendName()})
        cap.release()
    return out


def resolve_camera(spec, max_index: int = 8) -> int:
    """Índice de cámara desde un número o un nombre. 'osmo': la Osmo Pocket en modo webcam es la única que
    reporta cuadro vertical (1080x1920). Los índices de AVFoundation cambian al enchufar cosas: resolver cada vez."""
    if isinstance(spec, int) or str(spec).isdigit():
        return int(spec)
    # h1 20-nov (hackathon): SLSCAN_CAMERA=<índice> fuerza una webcam cualquiera en vez de la Osmo. Revertir: borrar estas 3 líneas.
    if os.environ.get("SLSCAN_CAMERA", "").strip().isdigit():
        return int(os.environ["SLSCAN_CAMERA"].strip())
    backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
    want = str(spec).lower()
    # OJO: el orden de índices de OpenCV NO es el de `ffmpeg -list_devices` (2-nov: la Osmo era [2] en ffmpeg y 0 en
    # OpenCV), así que no se resuelve por nombre; la Osmo se reconoce por cuadro vertical o 25 fps.
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, backend)
        if not cap.isOpened():
            cap.release(); continue
        w, h, fps = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT), cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        found.append((i, w, h, fps))
    if want in ("osmo", "pocket", "vertical"):
        # Osmo Pocket 3 en modo webcam: cuadro vertical si está parada; horizontal a 25 fps (las otras van a 15/30/60)
        for i, w, h, fps in found:
            if h > w:
                return i
        for i, w, h, fps in found:
            if abs(fps - 25) < 0.5:
                return i
    raise RuntimeError(f"no encontré una cámara que calce con '{spec}' entre {found} (¿Osmo en modo webcam?)")


class Camera:
    def __init__(self, index, size: str | None = None, name: str | None = None, rotate: int = 0, downscale: int = 1):
        # downscale: la Osmo a 1920x1080 entrega la imagen encajonada (607x658 px útiles dentro del cuadro);
        # a 3840x2160 llena el cuadro, así que se pide 4K y se reduce 2x para seguir trabajando a 1080x1920
        self.downscale = max(1, int(downscale))
        backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
        index = resolve_camera(index)
        self.index = index
        self.name = name or f"cam{index}"
        self.rotate = rotate % 360          # 90, 180, 270: la Osmo Pocket entrega el cuadro vertical
        self.requested_size = size
        self.cap = cv2.VideoCapture(index, backend)
        if not self.cap.isOpened():
            raise RuntimeError(f"no se pudo abrir la cámara {index}")
        if size:
            w, h = (int(v) for v in size.split("x"))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w); self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        # intento de exposición manual; no todas las cámaras lo aceptan por OpenCV
        self.control_requests = {}
        for name, prop, value in [('auto_exposure',cv2.CAP_PROP_AUTO_EXPOSURE,.25),
                                  ('auto_wb',cv2.CAP_PROP_AUTO_WB,0),
                                  ('autofocus',cv2.CAP_PROP_AUTOFOCUS,0)]:
            accepted = bool(self.cap.set(prop,value))
            self.control_requests[name] = {'requested':value,'accepted':accepted,
                                          'reported':float(self.cap.get(prop)),
                                          'physically_verified':False}

    def metadata(self):
        """Backend reports are evidence of requests, not proof of optical locks."""
        code = int(self.cap.get(cv2.CAP_PROP_FOURCC))
        return {'requested_size':self.requested_size,'reported_raw_size':[
                int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))],
                'effective_size':list(self.size),'rotate':self.rotate,'downscale':self.downscale,
                'fourcc':''.join(chr((code >> (8*i))&255) for i in range(4)),
                'fps_reported':float(self.cap.get(cv2.CAP_PROP_FPS)),
                'controls':self.control_requests,'lock_status':'unverified'}

    def read(self) -> np.ndarray:
        for _ in range(5):
            ok, f = self.cap.read()
            if ok and f is not None:
                if self.downscale > 1:
                    f = cv2.resize(f, (f.shape[1] // self.downscale, f.shape[0] // self.downscale), interpolation=cv2.INTER_AREA)
                if self.rotate == 90:
                    f = cv2.rotate(f, cv2.ROTATE_90_CLOCKWISE)
                elif self.rotate == 180:
                    f = cv2.rotate(f, cv2.ROTATE_180)
                elif self.rotate == 270:
                    f = cv2.rotate(f, cv2.ROTATE_90_COUNTERCLOCKWISE)
                return f
        raise RuntimeError(f"cámara {self.index} sin cuadros")

    def flush(self, n: int = 3) -> None:
        for _ in range(n):
            self.cap.grab()

    @property
    def size(self) -> tuple[int, int]:
        w, h = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)) // self.downscale, int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) // self.downscale
        return (h, w) if self.rotate in (90, 270) else (w, h)

    def close(self) -> None:
        self.cap.release()


def _make_builtin_main(disp: list[dict], target: dict) -> dict | None:
    """Deja el display interno como principal (origen 0,0) y el proyector a su derecha, con displayplacer.
    Guarda el estado previo en captures/displayplacer_rollback_<hora>.txt. Devuelve el display del proyector reubicado."""
    import shutil, subprocess, time as _t
    if not shutil.which("displayplacer"):
        _log("displayplacer no está: el proyector sigue como principal (barra de menú visible)"); return None
    try:
        cur = subprocess.run(["displayplacer", "list"], capture_output=True, text=True, timeout=20).stdout
        line = [l for l in cur.splitlines() if l.startswith("displayplacer ")]
        if line:
            Path("captures").mkdir(exist_ok=True)
            Path(f"captures/displayplacer_rollback_{_t.strftime('%H%M')}.txt").write_text(line[-1] + "\n")
        ids = {}
        blocks = cur.split("Persistent screen id: ")[1:]
        for b in blocks:
            pid = b.split("\n", 1)[0].strip()
            res = next((l.split("Resolution: ")[1].strip() for l in b.splitlines() if "Resolution:" in l), "")
            w, h = (int(v) for v in res.split("x")) if "x" in res else (0, 0)
            ids[(w, h)] = (pid, "Type: MacBook built in screen" in b or "built in" in b.lower())
        builtin = next((v for k, v in ids.items() if v[1]), None); proj = ids.get((target["w"], target["h"]))
        if not builtin or not proj:
            _log(f"no pude identificar interno/proyector en displayplacer: {list(ids)}"); return None
        bw, bh = next(k for k, v in ids.items() if v[1])
        cmd = ["displayplacer", f"id:{builtin[0]} origin:(0,0) degree:0", f"id:{proj[0]} origin:({bw},0) degree:0"]
        subprocess.run(cmd, capture_output=True, text=True, timeout=30); _t.sleep(2.0)
        _log("displayplacer: interno principal, proyector a la derecha")
        for dd in list_displays():
            if dd["w"] == target["w"] and dd["h"] == target["h"] and not dd.get("builtin"):
                return dd
    except Exception as e:  # noqa: BLE001
        _log(f"displayplacer falló: {e!r}")
    return None


class RemoteCamera:
    """Cámara servida por HTTP (windows/camserve.py o windows/hands_service.py en el PC): misma interfaz que Camera.
    index = 'http://192.168.1.233:8794'. read() baja /frame.png (sin pérdida, para gray code); flush() no hace nada
    porque el servidor siempre entrega el último cuadro."""

    def __init__(self, url: str, name: str | None = None, rotate: int = 0, lossless: bool = True, timeout: float = 5.0):
        import urllib.request
        self.name = name or "remota"; self.rotate = rotate % 360; self.timeout = timeout
        self._open = urllib.request.urlopen
        # URL directa a una imagen (p. ej. el Pixel con IP Webcam: http://<pixel>:8080/shot.jpg, o DroidCam
        # http://<pixel>:4747/cam/1/frame.jpg): sin /health, cada read() baja esa URL
        low = url.lower().split("?")[0]
        self.direct = low.endswith((".jpg", ".jpeg", ".png"))
        if self.direct:
            self.url = url; self.path = ""
            f = self.read()
            self._size = (f.shape[1], f.shape[0])
        else:
            self.url = url.rstrip("/"); self.path = "/frame.png" if lossless else "/frame.jpg"
            h = self.health()
            if not h.get("camera_open"):
                raise RuntimeError(f"cámara remota {self.url} no abre: {h}")
        self.index = url

    def health(self) -> dict:
        import json
        if getattr(self, "direct", False):
            return {"camera_open": True, "frame": list(getattr(self, "_size", (0, 0))), "direct": self.url}
        with self._open(self.url + "/health", timeout=self.timeout) as r:
            return json.loads(r.read())

    def read(self) -> np.ndarray:
        for _ in range(3):
            try:
                with self._open(self.url + self.path, timeout=self.timeout) as r:
                    f = cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_COLOR)
            except Exception:  # noqa: BLE001
                f = None
            if f is not None:
                if self.rotate == 90:
                    f = cv2.rotate(f, cv2.ROTATE_90_CLOCKWISE)
                elif self.rotate == 180:
                    f = cv2.rotate(f, cv2.ROTATE_180)
                elif self.rotate == 270:
                    f = cv2.rotate(f, cv2.ROTATE_90_COUNTERCLOCKWISE)
                return f
        raise RuntimeError(f"cámara remota {self.url} sin cuadros")

    def flush(self, n: int = 3) -> None:
        return None

    @property
    def size(self) -> tuple[int, int]:
        w, h = self.health().get("frame") or (0, 0)
        return (h, w) if self.rotate in (90, 270) else (w, h)

    def close(self) -> None:
        return None


def open_camera(index, size: str | None = None, name: str | None = None, rotate: int = 0, downscale: int = 1):
    """Camera local, o RemoteCamera si index es una URL http(s)://."""
    if isinstance(index, str) and index.startswith(("http://", "https://")):
        return RemoteCamera(index, name=name, rotate=rotate)
    return Camera(index, size=size, name=name, rotate=rotate, downscale=downscale)


def auto_exposure_check(win: PatternWindow, cam: Camera, settle: float = 1.5) -> dict:
    """Detecta auto-exposición: con pantalla blanca y negra, una cámara fija cambia su media mucho
    más que una con auto-exposición activa (que compensa). Devuelve medias y veredicto."""
    win.solid(255); time.sleep(settle); cam.flush(5); white = cam.read().mean()
    win.solid(0); time.sleep(settle); cam.flush(5); black = cam.read().mean()
    ratio = (white + 1) / (black + 1)
    return {"white_mean": float(white), "black_mean": float(black), "ratio": float(ratio),
            "manual_ok": bool(ratio > 1.5)}


def measure_latency(win: PatternWindow, cam: Camera, trials: int = 10, timeout: float = 2.0) -> dict:
    """Latencia display -> cámara: tiempo desde mostrar blanco hasta que el cuadro lo refleja."""
    lat = []
    # la proyección suele cubrir solo parte del cuadro: se mide en los píxeles que cambian entre negro y blanco
    win.solid(0); time.sleep(1.0); cam.flush(10)
    dark_img = cv2.cvtColor(cam.read(), cv2.COLOR_BGR2GRAY).astype(np.float32)
    win.solid(255); time.sleep(1.5); cam.flush(10)
    light_img = cv2.cvtColor(cam.read(), cv2.COLOR_BGR2GRAY).astype(np.float32)
    diff = light_img - dark_img
    mask = diff > max(20.0, 0.5 * float(np.percentile(diff, 99)))
    if mask.sum() < 500:
        return {"error": "la cámara no ve la proyección (ningún píxel cambia entre negro y blanco)",
                "max_diff": float(diff.max())}
    dark = float(dark_img[mask].mean()); light = float(light_img[mask].mean())
    thr = dark + 0.5 * (light - dark)
    per_trial = []
    for k in range(trials):
        win.solid(0); time.sleep(0.8); cam.flush(5)
        t0 = time.perf_counter()
        win.solid(255)
        series = []                       # (t, media en la zona proyectada) durante `timeout` s
        while time.perf_counter() - t0 < timeout:
            f = cam.read()
            series.append((time.perf_counter() - t0, float(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)[mask].mean())))
        vals = np.array([v for _, v in series]); ts = np.array([t for t, _ in series])
        lo, hi = vals[:2].mean(), vals.max()
        rise = ts[np.argmax(vals > lo + 0.5 * (hi - lo))] if hi > lo + 5 else float("nan")
        per_trial.append(float(rise)); lat.append(rise)
    lat = np.array([v for v in lat if np.isfinite(v)])
    if lat.size == 0:
        return {"error": "sin subida de brillo medible en la zona proyectada (¿auto-exposición?)", "per_trial": per_trial}
    lat = lat * 1000
    fps = cam.cap.get(cv2.CAP_PROP_FPS) or 30
    return {"trials": trials, "per_trial_ms": [round(v * 1000, 1) if np.isfinite(v) else None for v in per_trial],
            "latency_ms_mean": float(lat.mean()), "latency_ms_std": float(lat.std()),
            "latency_ms_max": float(lat.max()), "camera_fps": float(fps),
            "settle_ms_recommended": float(lat.max() + 2 * 1000 / fps)}


# ----------------------------------------------------------------------------- captura guiada

def focus_measure(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def exposure_report(gray: np.ndarray) -> dict:
    return {"mean": float(gray.mean()), "clipped_hi": float((gray >= 250).mean()), "clipped_lo": float((gray <= 5).mean())}


class CoverageGrid:
    """Cobertura del encuadre: celdas de una grilla que ya tuvieron esquinas del tablero."""

    def __init__(self, size: tuple[int, int], cells=(6, 4)):
        self.size = size; self.cells = cells
        self.hits = np.zeros((cells[1], cells[0]), int)

    def cell_of(self, pts: np.ndarray) -> np.ndarray:
        cx = np.clip((pts[:, 0] / self.size[0] * self.cells[0]).astype(int), 0, self.cells[0] - 1)
        cy = np.clip((pts[:, 1] / self.size[1] * self.cells[1]).astype(int), 0, self.cells[1] - 1)
        return np.stack([cx, cy], axis=1)

    def new_cells(self, pts: np.ndarray) -> int:
        c = np.unique(self.cell_of(pts), axis=0)
        return int(sum(self.hits[y, x] == 0 for x, y in c))

    def add(self, pts: np.ndarray) -> None:
        for x, y in np.unique(self.cell_of(pts), axis=0):
            self.hits[y, x] += 1

    def fraction(self) -> float:
        return float((self.hits > 0).mean())

    def draw(self, img: np.ndarray) -> None:
        h, w = img.shape[:2]
        cw, ch = w / self.cells[0], h / self.cells[1]
        for y in range(self.cells[1]):
            for x in range(self.cells[0]):
                col = (0, 160, 0) if self.hits[y, x] else (0, 0, 200)
                cv2.rectangle(img, (int(x * cw), int(y * ch)), (int((x + 1) * cw) - 1, int((y + 1) * ch) - 1), col, 1)


def guided_board_capture(cams: list[Camera], board, out: Path, target: int = 25, names: list[str] | None = None,
                         stable_px: float = 1.0, stable_s: float = 0.6, min_focus: float = 30.0,
                         preview_width: int = 960) -> int:
    """Captura guiada: muestra las cámaras, valida tablero, foco, exposición y cobertura, y guarda sola.

    Guarda `out/view_NNNN/<cam>.png` cuando todas las cámaras ven el tablero, está quieto
    `stable_s` segundos, enfocado, sin saturación, y aporta cobertura nueva. Espacio: forzar
    una toma. q: terminar. Devuelve el número de vistas guardadas.
    """
    names = names or [c.name for c in cams]
    for c, n in zip(cams, names):
        c.name = n
    cov = {c.name: CoverageGrid(c.size) for c in cams}
    last_pts: dict[str, tuple[np.ndarray, float]] = {}
    saved = 0
    win = "slscan-captura-guiada"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    place_on_builtin(win, 980, 620)
    last_save = 0.0
    while saved < target:
        frames = {c.name: c.read() for c in cams}
        dets = {}
        panels = []
        all_ok = True
        hint = ""
        for c in cams:
            f = frames[c.name]
            gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            d = board.detect(gray)
            dets[c.name] = d
            vis = f.copy()
            cov[c.name].draw(vis)
            status = []
            ok = True
            if d is None:
                status.append("sin tablero"); ok = False; hint = hint or "muestra el tablero entero a todas las cámaras"
            else:
                pts = d["img"]
                for p in pts.astype(int):
                    cv2.circle(vis, tuple(p), 6, (0, 255, 255), 2)
                x0, y0 = pts.min(0).astype(int); x1, y1 = pts.max(0).astype(int)
                roi = gray[max(0, y0):y1 + 1, max(0, x0):x1 + 1]
                fm = focus_measure(roi) if roi.size else 0
                ex = exposure_report(roi) if roi.size else {"clipped_hi": 1, "mean": 0, "clipped_lo": 0}
                status.append(f"{len(pts)} esquinas  foco {fm:.0f}  media {ex['mean']:.0f}")
                if fm < min_focus:
                    ok = False; hint = hint or "desenfocado: acerca o aleja el tablero, o fija el foco"
                if ex["clipped_hi"] > 0.02:
                    ok = False; hint = hint or "sobreexpuesto: baja la exposición"
                if ex["mean"] < 40:
                    ok = False; hint = hint or "muy oscuro: sube la exposición o la luz"
                prev = last_pts.get(c.name)
                now = time.time()
                if prev is not None and len(prev[0]) == len(pts) and np.abs(prev[0] - pts).max() < stable_px:
                    stable_for = now - prev[1]
                else:
                    last_pts[c.name] = (pts, now); stable_for = 0
                if stable_for < stable_s:
                    ok = False; hint = hint or "quieto…"
                new = cov[c.name].new_cells(pts)
                if new == 0 and cov[c.name].fraction() < 1.0:
                    ok = False; hint = hint or "mueve el tablero a una zona roja (y acércalo a los bordes)"
                status.append(f"cobertura {100*cov[c.name].fraction():.0f}%")
            all_ok &= ok
            cv2.putText(vis, f"{c.name}: " + " · ".join(status), (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (0, 255, 0) if ok else (0, 0, 255), 2)
            scale = preview_width / vis.shape[1]
            panels.append(cv2.resize(vis, None, fx=scale, fy=scale))
        canvas = np.vstack(panels)
        cv2.putText(canvas, f"vistas {saved}/{target}   {hint or 'ok, capturando…'}   [espacio] forzar  [q] salir",
                    (12, canvas.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.imshow(win, canvas)
        key = cv2.waitKey(30) & 0xFF
        if key == ord("q"):
            break
        force = key == ord(" ")
        if (all_ok or force) and all(d is not None for d in dets.values()) and time.time() - last_save > 1.0:
            folder = out / f"view_{saved:04d}"; folder.mkdir(parents=True, exist_ok=True)
            for c in cams:
                cv2.imwrite(str(folder / f"{c.name}.png"), frames[c.name])
                cov[c.name].add(dets[c.name]["img"])
            saved += 1; last_save = time.time()
            last_pts.clear()
    cv2.destroyWindow(win)
    return saved


def capture_sequence(win: PatternWindow, cams: list[Camera], specs, pattern_fn, out: Path, settle_s: float,
                     flush: int = 3, log=None, observe=None) -> None:
    """Muestra cada patrón, espera `settle_s` y guarda un PNG por cámara con el nombre de sltk."""
    out.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        win.show(pattern_fn(spec))
        time.sleep(settle_s)
        for c in cams:
            c.flush(flush)
            f = c.read()
            folder = out / c.name; folder.mkdir(exist_ok=True)
            cv2.imwrite(str(folder / f"{spec.tag}.png"), f)
            if observe is not None:
                observe(spec, c, f)
        if log:
            log(f"  {spec.tag}")

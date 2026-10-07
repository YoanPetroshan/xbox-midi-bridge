"""Reads the Xbox controller through the SDL2 Game Controller API (pygame).

pygame runs in a separate, windowless process. Why:
  * On macOS both SDL and Qt want the main Cocoa thread; separate processes don't clash.
  * The process is never frontmost, so if it receives data at all, it also works
    while Lightkey is in front (SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1).

Pipe protocol:
  child → parent: ("devices", [names]), ("connected", name, sdl_mapping, info),
                  ("disconnected",), ("state", buttons_tuple, axes_tuple, touch_down),
                  ("error", text)
  parent → child: ("prefer", name|None), ("led", r, g, b), ("quit",)

Touchpad, gyro and light bar are not exposed by pygame, so they go straight to the
SDL library pygame already loaded (see SdlExtras).
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
import threading
import time

import ctypes
import ctypes.util

from i18n import tr
from mapping import AXIS_IDS, BUTTON_IDS, GYRO_IDS, TOUCH_IDS, TRIGGER_IDS

POLL_HZ = 250

# Indices in SDL_GameControllerButton / SDL_GameControllerAxis (SDL 2.0.14+).
SDL_BUTTON_INDEX = {
    "a": 0, "b": 1, "x": 2, "y": 3,
    "back": 4, "guide": 5, "start": 6,
    "leftstick": 7, "rightstick": 8,
    "leftshoulder": 9, "rightshoulder": 10,
    "dpup": 11, "dpdown": 12, "dpleft": 13, "dpright": 14,
    "misc1": 15,
    "paddle1": 16, "paddle2": 17, "paddle3": 18, "paddle4": 19,
    "touchpad": 20,
}
SDL_AXIS_INDEX = {
    "leftx": 0, "lefty": 1, "rightx": 2, "righty": 3,
    "lefttrigger": 4, "righttrigger": 5,
}


# SDL_GameControllerType values that are PlayStation pads (PS3, PS4, PS5).
SDL_PLAYSTATION_TYPES = (3, 4, 7)
SDL_SENSOR_GYRO = 2
GYRO_FULL_SCALE = 5.0  # rad/s that count as full deflection (≈ 286°/s)


class SdlExtras:
    """Direct calls into the SDL2 library pygame has loaded: type, touchpad, gyro, LED.

    The library is located by its path among the images already loaded in this process,
    so the calls reach the same SDL instance (and the same open controller) as pygame.
    Everything degrades to "not available" if anything is missing.
    """

    def __init__(self):
        self.sdl = None
        path = self._loaded_sdl_path()
        if not path:
            return
        try:
            sdl = ctypes.CDLL(path)
            sdl.SDL_WasInit.restype = ctypes.c_uint32
            if not sdl.SDL_WasInit(0x2000):  # SDL_INIT_GAMECONTROLLER: same instance as pygame?
                return
            vp, i = ctypes.c_void_p, ctypes.c_int
            sdl.SDL_JoystickGetDeviceInstanceID.restype = i
            sdl.SDL_GameControllerFromInstanceID.restype = vp
            sdl.SDL_GameControllerFromInstanceID.argtypes = [i]
            for fn, args in (("SDL_GameControllerGetType", [vp]),
                             ("SDL_GameControllerHasButton", [vp, i]),
                             ("SDL_GameControllerGetNumTouchpads", [vp]),
                             ("SDL_GameControllerHasSensor", [vp, i]),
                             ("SDL_GameControllerSetSensorEnabled", [vp, i, i]),
                             ("SDL_GameControllerHasLED", [vp])):
                f = getattr(sdl, fn)
                f.restype, f.argtypes = i, args
            sdl.SDL_GameControllerGetTouchpadFinger.restype = i
            sdl.SDL_GameControllerGetTouchpadFinger.argtypes = [
                vp, i, i, ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
            sdl.SDL_GameControllerGetSensorData.restype = i
            sdl.SDL_GameControllerGetSensorData.argtypes = [vp, i, ctypes.POINTER(ctypes.c_float), i]
            sdl.SDL_GameControllerSetLED.restype = i
            sdl.SDL_GameControllerSetLED.argtypes = [vp, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8]
            self.sdl = sdl
        except (OSError, AttributeError):
            self.sdl = None

    @staticmethod
    def _loaded_sdl_path() -> str | None:
        try:
            dyld = ctypes.CDLL(ctypes.util.find_library("System"))
            dyld._dyld_image_count.restype = ctypes.c_uint32
            dyld._dyld_get_image_name.restype = ctypes.c_char_p
            dyld._dyld_get_image_name.argtypes = [ctypes.c_uint32]
            for n in range(dyld._dyld_image_count()):
                name = (dyld._dyld_get_image_name(n) or b"").decode(errors="replace")
                if os.path.basename(name).startswith("libSDL2-2.0"):
                    return name
        except (OSError, AttributeError):
            pass
        return None

    def open(self, device_index: int):
        """SDL_GameController* of an already opened controller (pygame opened it)."""
        if not self.sdl:
            return None
        iid = self.sdl.SDL_JoystickGetDeviceInstanceID(device_index)
        return self.sdl.SDL_GameControllerFromInstanceID(iid) or None

    def info(self, ptr, name: str) -> dict:
        info = {"family": _family_from_name(name), "has": [], "touchpad": False,
                "gyro": False, "led": False}
        info["sdl_direct"] = bool(self.sdl and ptr)  # shown by --diag
        if not (self.sdl and ptr):
            return info
        if self.sdl.SDL_GameControllerGetType(ptr) in SDL_PLAYSTATION_TYPES:
            info["family"] = "playstation"
        info["has"] = [b for b, idx in SDL_BUTTON_INDEX.items()
                       if self.sdl.SDL_GameControllerHasButton(ptr, idx)]
        info["touchpad"] = self.sdl.SDL_GameControllerGetNumTouchpads(ptr) > 0
        if self.sdl.SDL_GameControllerHasSensor(ptr, SDL_SENSOR_GYRO):
            info["gyro"] = self.sdl.SDL_GameControllerSetSensorEnabled(ptr, SDL_SENSOR_GYRO, 1) == 0
        info["led"] = bool(self.sdl.SDL_GameControllerHasLED(ptr))
        return info

    def touch(self, ptr) -> tuple[bool, float, float]:
        st, x, y, pr = ctypes.c_uint8(), ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
        if self.sdl.SDL_GameControllerGetTouchpadFinger(ptr, 0, 0, st, x, y, pr) != 0:
            return False, 0.0, 0.0
        return bool(st.value), x.value, y.value

    def gyro(self, ptr) -> tuple[float, float]:
        """(pitch, yaw) angular velocity in rad/s."""
        data = (ctypes.c_float * 3)()
        if self.sdl.SDL_GameControllerGetSensorData(ptr, SDL_SENSOR_GYRO, data, 3) != 0:
            return 0.0, 0.0
        return data[0], data[1]

    def set_led(self, ptr, r: int, g: int, b: int) -> None:
        self.sdl.SDL_GameControllerSetLED(ptr, r, g, b)


def _family_from_name(name: str) -> str:
    n = (name or "").lower()
    return "playstation" if any(k in n for k in ("ps5", "ps4", "ps3", "dualsense", "dualshock")) else "xbox"


def _setup_sdl_env() -> None:
    os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"
    os.environ["SDL_MAC_BACKGROUND_APP"] = "1"  # no Dock icon for the reader process
    os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
    # Over Bluetooth a DualSense / DualShock 4 starts in a "simple" report mode that leaves out
    # the Mic button, the touchpad position, the gyro and light bar control. These hints make
    # SDL switch it to the full report mode as soon as it is opened (SDL_hidapi_ps5.c / ps4.c).
    os.environ["SDL_JOYSTICK_HIDAPI_PS5_RUMBLE"] = "1"
    os.environ["SDL_JOYSTICK_HIDAPI_PS4_RUMBLE"] = "1"


def _hide_from_dock() -> None:
    """The reader process is not an app: no Dock icon and no menu.

    SDL initialises the video subsystem (needed for events), and inside an .app bundle
    macOS then registers it as a second application. NSApplicationActivationPolicyProhibited
    prevents that. Uses ctypes to avoid a PyObjC dependency.
    """
    if sys.platform != "darwin":
        return
    try:
        import ctypes
        import ctypes.util

        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        send = objc.objc_msgSend
        send.restype = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        app = send(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))
        send_long = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(
            ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value)
        send_long(app, objc.sel_registerName(b"setActivationPolicy:"), 2)  # Prohibited
    except Exception:
        pass


def _normalize_axis(axis_id: str, raw: int) -> float:
    if axis_id in TRIGGER_IDS:
        return max(0.0, min(1.0, raw / 32767.0))
    if axis_id in TOUCH_IDS or axis_id in GYRO_IDS:
        return 0.0  # not an SDL axis; filled in from SdlExtras
    v = max(-1.0, min(1.0, raw / 32767.0))
    # SDL reports Y negative when pushed up; flip it so up = +.
    return -v if axis_id in ("lefty", "righty") else v


def reader_main(conn, poll_hz: int = POLL_HZ) -> None:
    """Loop in the child process."""
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C is handled by the parent
    _setup_sdl_env()

    def send(msg):
        try:
            conn.send(msg)
        except (BrokenPipeError, ConnectionResetError, OSError):
            raise SystemExit(0)  # the app is gone: stop quietly
    try:
        import pygame
        from pygame._sdl2 import controller as sdlc

        _hide_from_dock()
        pygame.init()
        sdlc.init()
        _hide_from_dock()  # SDL may have reset the policy during video init
    except Exception as e:  # pragma: no cover - environment dependent
        send(("error", tr("pygame/SDL failed to start: {}", "pygame/SDL не стартира: {}").format(e)))
        return

    extras = SdlExtras()
    period = 1.0 / poll_hz
    prefer: str | None = None
    ctrl = None
    cptr = None  # SDL_GameController* for the extras
    info: dict = {}
    touch_pos = (0.5, 0.5)  # last finger position (held after the finger lifts)
    last_state = None
    last_devices = None
    rescan = True
    next_rescan = 0.0
    device_events = {
        getattr(pygame, n) for n in (
            "JOYDEVICEADDED", "JOYDEVICEREMOVED",
            "CONTROLLERDEVICEADDED", "CONTROLLERDEVICEREMOVED", "CONTROLLERDEVICEREMAPPED",
        ) if hasattr(pygame, n)
    }

    def close_ctrl():
        nonlocal ctrl, last_state, cptr, info
        if ctrl is not None:
            try:
                ctrl.quit()
            except Exception:
                pass
            ctrl = None
            cptr = None
            info = {}
            last_state = None
            send(("disconnected",))

    def device_name(i: int) -> str:
        # pygame has name_forindex, pygame-ce (used in the .app build) does not.
        try:
            return sdlc.name_forindex(i) or tr("Controller {}", "Контролер {}").format(i)
        except AttributeError:
            return pygame.joystick.Joystick(i).get_name() or tr("Controller {}", "Контролер {}").format(i)

    def controller_indices() -> list[int]:
        out = []
        for i in range(pygame.joystick.get_count()):
            try:
                if sdlc.is_controller(i):
                    out.append(i)
            except Exception:
                pass
        return out

    def list_devices():
        out = []
        for i in controller_indices():
            try:
                out.append(device_name(i))
            except Exception as e:
                out.append(tr("Controller {}", "Контролер {}").format(i))
                send(("error", tr("Device name {}: {}", "Име на устройство {}: {}").format(i, e)))
        return out

    while True:
        t0 = time.perf_counter()
        try:
            pending = []
            while conn.poll():
                pending.append(conn.recv())
        except (EOFError, OSError):
            return  # the app is gone (quit or killed): stop quietly
        for msg in pending:
            if msg[0] == "quit":
                close_ctrl()
                return
            if msg[0] == "led":
                if cptr is not None and info.get("led"):
                    try:
                        extras.set_led(cptr, *msg[1:4])
                    except Exception:
                        pass
                continue
            if msg[0] == "prefer":
                if msg[1] != prefer:
                    prefer = msg[1]
                    close_ctrl()
                    rescan = True

        for e in pygame.event.get():
            if e.type in device_events:
                rescan = True

        if ctrl is not None:
            try:
                attached = ctrl.attached()
            except Exception:
                attached = False
            if not attached:
                close_ctrl()
                rescan = True

        if rescan or t0 >= next_rescan:
            rescan = False
            next_rescan = t0 + 1.0
            devices = list_devices()
            if devices != last_devices:
                last_devices = devices
                send(("devices", devices))
            if ctrl is None and devices:
                idx_list = controller_indices()
                pick = idx_list[0] if idx_list else None
                if prefer:
                    for i, name in zip(idx_list, devices):
                        if name == prefer:
                            pick = i
                            break
                try:
                    if pick is None:
                        raise RuntimeError("no valid index")
                    ctrl = sdlc.Controller(pick)
                    try:
                        mapping = ctrl.get_mapping()
                    except Exception:
                        mapping = {}
                    try:
                        cptr = extras.open(pick)
                        info = extras.info(cptr, ctrl.name)
                    except Exception:
                        cptr, info = None, extras.info(None, ctrl.name)
                    send(("connected", ctrl.name, mapping, info))
                except Exception as e:
                    ctrl = None
                    send(("error", tr("Could not open the controller: {}", "Контролерът не можа да се отвори: {}").format(e)))

        if ctrl is not None:
            try:
                buttons = tuple(bool(ctrl.get_button(SDL_BUTTON_INDEX[b])) for b in BUTTON_IDS)
                axes = {a: _normalize_axis(a, ctrl.get_axis(SDL_AXIS_INDEX[a]))
                        for a in AXIS_IDS if a in SDL_AXIS_INDEX}
                down = False
                if cptr is not None and info.get("touchpad"):
                    down, x, y = extras.touch(cptr)
                    if down:
                        touch_pos = (x, 1.0 - y)  # SDL y grows downwards; up = +
                axes["touchx"], axes["touchy"] = touch_pos
                if cptr is not None and info.get("gyro"):
                    pitch, yaw = extras.gyro(cptr)
                    # Rounded so a controller lying still doesn't flood the pipe with noise.
                    axes["gyropitch"] = round(max(-1.0, min(1.0, pitch / GYRO_FULL_SCALE)), 3)
                    axes["gyroyaw"] = round(max(-1.0, min(1.0, yaw / GYRO_FULL_SCALE)), 3)
                axes = tuple(axes.get(a, 0.0) for a in AXIS_IDS)
                state = (buttons, axes, down)
                if state != last_state:
                    last_state = state
                    send(("state", buttons, axes, down))
            except Exception:
                close_ctrl()
                rescan = True

        dt = time.perf_counter() - t0
        if dt < period:
            time.sleep(period - dt)


class ReaderProcess:
    """Manages the child process from the app side."""

    def __init__(self, poll_hz: int = POLL_HZ):
        self.poll_hz = poll_hz
        self.proc: mp.Process | None = None
        self.conn = None
        self._send_lock = threading.Lock()

    def start(self, prefer: str | None = None) -> None:
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe()
        self.conn = parent
        self.proc = ctx.Process(target=reader_main, args=(child, self.poll_hz),
                                name="XboxMidiBridge-reader", daemon=True)
        self.proc.start()
        child.close()
        self.prefer(prefer)

    def prefer(self, name: str | None) -> None:
        self.send(("prefer", name))

    def send(self, msg: tuple) -> None:
        # Called from the GUI thread (prefer) and the engine thread (led).
        with self._send_lock:
            if self.conn:
                try:
                    self.conn.send(msg)
                except (OSError, BrokenPipeError):
                    pass

    def poll(self) -> list[tuple]:
        """All pending messages (non-blocking)."""
        out = []
        if not self.conn:
            return out
        try:
            while self.conn.poll():
                out.append(self.conn.recv())
        except (EOFError, OSError):
            out.append(("error", tr("The controller process stopped unexpectedly.", "Процесът за контролера спря неочаквано.")))
            self.conn = None
        return out

    def alive(self) -> bool:
        return bool(self.proc and self.proc.is_alive())

    def stop(self) -> None:
        if self.conn:
            try:
                self.conn.send(("quit",))
            except (OSError, BrokenPipeError):
                pass
        if self.proc:
            self.proc.join(timeout=1.5)
            if self.proc.is_alive():
                self.proc.terminate()
        self.proc = None
        self.conn = None

"""See and drive iClone's own windows (main window, dialogs) through Win32.

Captures use PrintWindow, so they show iClone even when another application covers
it, and never include other applications. Clicks and keys are posted as window
messages to iClone, so the user's mouse cursor and focus are not touched. These
tools do not need the iClone main thread, so they also work while a modal dialog
is open.
"""
import base64
import ctypes
import os
import struct
import tempfile
import threading
import zlib
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

_user32.GetWindowDC.restype = wintypes.HDC
_user32.GetWindowDC.argtypes = [wintypes.HWND]
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.ChildWindowFromPointEx.restype = wintypes.HWND
_user32.ChildWindowFromPointEx.argtypes = [wintypes.HWND, wintypes.POINT, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.StretchBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
_gdi32.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
_gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]

_PW_RENDERFULLCONTENT = 2
_SRCCOPY = 0x00CC0020
_HALFTONE = 4
_GA_ROOT = 2
_CWP_SKIPINVISIBLE = 1
_WM_MOUSEMOVE, _WM_LBUTTONDOWN, _WM_LBUTTONUP, _WM_LBUTTONDBLCLK = 0x0200, 0x0201, 0x0202, 0x0203
_WM_RBUTTONDOWN, _WM_RBUTTONUP = 0x0204, 0x0205
_WM_KEYDOWN, _WM_KEYUP, _WM_CHAR = 0x0100, 0x0101, 0x0102
_MK_LBUTTON, _MK_RBUTTON = 0x0001, 0x0002
_KEYS = {
    "enter": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12,
}
_KEYS.update({"f%d" % index: 0x6F + index for index in range(1, 13)})

# Geometry of the last capture, so click() can take coordinates in that image.
_last_capture = {}


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND), ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]


def _rect(hwnd):
    rect = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def window_rect(hwnd):
    return _rect(hwnd)


def root_window(hwnd):
    return _user32.GetAncestor(hwnd, _GA_ROOT) or hwnd


def _text(function, hwnd):
    buffer = ctypes.create_unicode_buffer(512)
    function(hwnd, buffer, 512)
    return buffer.value


def windows():
    """iClone's visible top-level windows, topmost first."""
    found = []
    pid = os.getpid()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _param):
        owner = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and _user32.IsWindowVisible(hwnd) and _text(_user32.GetClassNameW, hwnd) != "SysShadow":
            left, top, right, bottom = _rect(hwnd)
            if right - left > 20 and bottom - top > 20:
                found.append({"hwnd": int(hwnd), "title": _text(_user32.GetWindowTextW, hwnd), "class": _text(_user32.GetClassNameW, hwnd),
                              "rect": {"left": left, "top": top, "width": right - left, "height": bottom - top}})
        return True

    _user32.EnumWindows(visit, 0)
    return found


def _resolve(args):
    """Pick a window by hwnd or title fragment; default is iClone's topmost one (an open dialog wins)."""
    candidates = windows()
    if not candidates:
        raise RuntimeError("No visible iClone window")
    if args.get("hwnd"):
        hwnd = int(args["hwnd"])
        if not any(window["hwnd"] == hwnd for window in candidates):
            raise ValueError("hwnd %s is not a visible iClone window (see list_windows)" % hwnd)
        return hwnd
    if args.get("title"):
        needle = args["title"].lower()
        for window in candidates:
            if needle in window["title"].lower():
                return window["hwnd"]
        raise ValueError("No iClone window title contains %r (see list_windows)" % args["title"])
    return candidates[0]["hwnd"]


def _png(width, height, bgra):
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    stride = width * 3
    raw = b"".join(b"\x00" + bytes(rgb[row * stride:(row + 1) * stride]) for row in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def _grab(hwnd, crop, max_width):
    """Render hwnd with PrintWindow, crop to a screen rectangle and scale down to max_width."""
    left, top, right, bottom = _rect(hwnd)
    width, height = right - left, bottom - top
    crop_left, crop_top, crop_right, crop_bottom = crop or (left, top, right, bottom)
    crop_width, crop_height = crop_right - crop_left, crop_bottom - crop_top
    scale = min(1.0, float(max_width) / crop_width)
    out_width, out_height = max(1, int(crop_width * scale)), max(1, int(crop_height * scale))
    window_dc = _user32.GetWindowDC(hwnd)
    full_dc, small_dc = _gdi32.CreateCompatibleDC(window_dc), _gdi32.CreateCompatibleDC(window_dc)
    full_bitmap = _gdi32.CreateCompatibleBitmap(window_dc, width, height)
    small_bitmap = _gdi32.CreateCompatibleBitmap(window_dc, out_width, out_height)
    full_old = None
    try:
        full_old = _gdi32.SelectObject(full_dc, full_bitmap)
        small_old = _gdi32.SelectObject(small_dc, small_bitmap)
        if not _user32.PrintWindow(hwnd, full_dc, _PW_RENDERFULLCONTENT):
            raise RuntimeError("PrintWindow failed for window %s" % hwnd)
        _gdi32.SetStretchBltMode(small_dc, _HALFTONE)
        _gdi32.StretchBlt(small_dc, 0, 0, out_width, out_height, full_dc, crop_left - left, crop_top - top, crop_width, crop_height, _SRCCOPY)
        # GetDIBits needs the bitmap deselected from its DC.
        _gdi32.SelectObject(small_dc, small_old)
        info = _BitmapInfo(ctypes.sizeof(_BitmapInfo), out_width, -out_height, 1, 32, 0, 0, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(out_width * out_height * 4)
        _gdi32.GetDIBits(small_dc, small_bitmap, 0, out_height, pixels, ctypes.byref(info), 0)
    finally:
        if full_old:
            _gdi32.SelectObject(full_dc, full_old)
        _gdi32.DeleteObject(full_bitmap)
        _gdi32.DeleteObject(small_bitmap)
        _gdi32.DeleteDC(full_dc)
        _gdi32.DeleteDC(small_dc)
        _user32.ReleaseDC(hwnd, window_dc)
    _last_capture.update({"hwnd": int(hwnd), "left": crop_left, "top": crop_top, "scale": scale})
    return out_width, out_height, _png(out_width, out_height, pixels.raw)


def capture(hwnd, crop=None, max_width=1280, path=None, timeout=10):
    """Capture on a worker thread so a hung iClone UI thread cannot block the server forever."""
    result = {}

    def work():
        try:
            result["value"] = _grab(hwnd, crop, max_width)
        except Exception as error:
            result["error"] = error

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError("iClone did not repaint the window in time (its UI thread may be busy)")
    if "error" in result:
        raise result["error"]
    width, height, png = result["value"]
    path = path or os.path.join(tempfile.gettempdir(), "mc-iclone8-mcp-capture.png")
    with open(path, "wb") as stream:
        stream.write(png)
    return {"path": path, "width": width, "height": height, "_image": {"data": base64.b64encode(png).decode("ascii"), "mimeType": "image/png"}}


def list_windows(_args):
    return {"windows": windows()}


def capture_window(args):
    hwnd = _resolve(args)
    result = capture(hwnd, max_width=int(args.get("max_width", 1280)), path=args.get("path"))
    result["hwnd"] = hwnd
    result["title"] = _text(_user32.GetWindowTextW, hwnd)
    result["note"] = "click() takes x/y in this image's pixels"
    return result


def _lparam(x, y):
    return (y & 0xFFFF) << 16 | (x & 0xFFFF)


def _target_at(root, screen_x, screen_y):
    """Deepest visible child window under a screen point, starting from iClone's window."""
    hwnd = root
    while True:
        point = wintypes.POINT(screen_x, screen_y)
        _user32.ScreenToClient(hwnd, ctypes.byref(point))
        child = _user32.ChildWindowFromPointEx(hwnd, point, _CWP_SKIPINVISIBLE)
        if not child or child == hwnd:
            return hwnd, point.x, point.y
        hwnd = child


def click(args):
    if not _last_capture:
        raise ValueError("Capture a window first (capture_window or capture_viewport); x/y are pixels of that image")
    scale = _last_capture["scale"]
    screen_x = _last_capture["left"] + int(round(float(args["x"]) / scale))
    screen_y = _last_capture["top"] + int(round(float(args["y"]) / scale))
    root = root_window(_last_capture["hwnd"])
    hwnd, x, y = _target_at(root, screen_x, screen_y)
    position = _lparam(x, y)
    button = args.get("button", "left")
    if button not in ("left", "right"):
        raise ValueError("button must be left or right")
    down, up, flag = (_WM_LBUTTONDOWN, _WM_LBUTTONUP, _MK_LBUTTON) if button == "left" else (_WM_RBUTTONDOWN, _WM_RBUTTONUP, _MK_RBUTTON)
    _user32.PostMessageW(hwnd, _WM_MOUSEMOVE, 0, position)
    _user32.PostMessageW(hwnd, down, flag, position)
    _user32.PostMessageW(hwnd, up, 0, position)
    if args.get("double") and button == "left":
        _user32.PostMessageW(hwnd, _WM_LBUTTONDBLCLK, flag, position)
        _user32.PostMessageW(hwnd, up, 0, position)
    return {"status": "ok", "window": int(hwnd), "class": _text(_user32.GetClassNameW, hwnd), "screen": {"x": screen_x, "y": screen_y}}


def _focus_window(args):
    hwnd = _resolve(args)
    info = _GuiThreadInfo(cbSize=ctypes.sizeof(_GuiThreadInfo))
    thread = _user32.GetWindowThreadProcessId(hwnd, None)
    if _user32.GetGUIThreadInfo(thread, ctypes.byref(info)) and info.hwndFocus:
        return info.hwndFocus
    return hwnd


def send_keys(args):
    """Type text and/or press keys (e.g. "enter", "esc", "ctrl+a") in the focused control of an iClone window."""
    hwnd = _focus_window(args)
    for character in args.get("text") or "":
        _user32.PostMessageW(hwnd, _WM_CHAR, ord(character), 1)
    pressed = []
    for combo in args.get("keys") or []:
        parts = [part.strip().lower() for part in combo.split("+")]
        codes = []
        for part in parts:
            if part in _KEYS:
                codes.append(_KEYS[part])
            elif len(part) == 1 and part.isalnum():
                codes.append(ord(part.upper()))
            else:
                raise ValueError("Unknown key %r in %r" % (part, combo))
        for code in codes:
            _user32.PostMessageW(hwnd, _WM_KEYDOWN, code, 1)
        for code in reversed(codes):
            _user32.PostMessageW(hwnd, _WM_KEYUP, code, 0xC0000001)
        pressed.append(combo)
    return {"status": "ok", "window": int(hwnd), "typed": args.get("text") or "", "keys": pressed}


def register(registry):
    window = {"hwnd": {"type": "integer"}, "title": {"type": "string", "description": "Part of the window title"}}
    registry["list_windows"] = {"handler": list_windows, "description": "Lists iClone's visible top-level windows (main window, dialogs, panels), topmost first, with hwnd, title and screen rect.", "inputSchema": {"type": "object", "properties": {}}}
    registry["capture_window"] = {"handler": capture_window, "description": "Captures an iClone window as an image, even when covered by other apps. Defaults to iClone's topmost window, so an open dialog is captured first. Use it to see dialogs, panels and the whole app before clicking.", "inputSchema": {"type": "object", "properties": dict(window, max_width={"type": "integer", "minimum": 64}, path={"type": "string"})}}
    registry["click"] = {"handler": click, "description": "Clicks in iClone at x/y pixels of the most recent capture_window/capture_viewport image. Sent as window messages: the user's mouse is not moved. Capture again afterwards to check the result.", "inputSchema": {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "button": {"type": "string", "enum": ["left", "right"]}, "double": {"type": "boolean"}}, "required": ["x", "y"]}}
    registry["send_keys"] = {"handler": send_keys, "description": "Types text and/or presses keys (e.g. enter, esc, tab, ctrl+a, f2) in the focused control of an iClone window (default: topmost).", "inputSchema": {"type": "object", "properties": dict(window, text={"type": "string"}, keys={"type": "array", "items": {"type": "string"}})}}

import time

import RLPy
from tools import ui
from tools.common import current_frame, frame_time


def get_render_settings(_args):
    """Return the output dimensions from iClone's current render settings."""
    result = RLPy.RGlobal.GetScreenSize(0, 0)
    if not isinstance(result, (tuple, list)) or len(result) < 3:
        raise RuntimeError("Unexpected response from iClone render settings")
    if result[0] != RLPy.RStatus.Success:
        raise RuntimeError("iClone could not read the render size")
    return {"width": int(result[1]), "height": int(result[2]), "note": "Other render options use the current iClone project settings."}


def render_video(args):
    """Start iClone's video rendering with its already configured project settings."""
    if args.get("confirm") != "RENDER_VIDEO_WITH_CURRENT_SETTINGS":
        raise ValueError("Set confirm to RENDER_VIDEO_WITH_CURRENT_SETTINGS to start rendering")
    result = RLPy.RGlobal.RenderVideo()
    if result != RLPy.RStatus.Success:
        raise RuntimeError("iClone could not start video rendering")
    return {"status": "started", "message": "iClone started rendering with the current project render settings."}


def _viewport_widget():
    from PySide2 import QtWidgets
    from shiboken2 import wrapInstance
    window = wrapInstance(int(RLPy.RUi.GetMainWindow()), QtWidgets.QMainWindow)
    # CCoreWnd is the native 3D view inside the central widget.
    views = [widget for widget in window.findChildren(QtWidgets.QWidget) if widget.metaObject().className() == "CCoreWnd" and widget.isVisible()]
    if not views:
        raise RuntimeError("iClone 3D viewport widget not found")
    return max(views, key=lambda widget: widget.width() * widget.height())


def capture_viewport(args):
    """Capture the 3D viewport (optionally at a frame) as an image an agent can look at."""
    from dispatch import run

    def prepare():
        if "frame" in args:
            RLPy.RGlobal.SetTime(frame_time(args["frame"]))
        RLPy.RGlobal.ForceViewportUpdate()
        return int(_viewport_widget().winId()), current_frame()

    # The capture itself runs off the main thread: PrintWindow needs that thread free to repaint.
    hwnd, frame = run(prepare)
    # The viewport repaints asynchronously; give it a few main-loop cycles before grabbing.
    for _cycle in range(3):
        time.sleep(0.1)
        run(lambda: None)
    root = ui.root_window(hwnd)
    result = ui.capture(root, crop=ui.window_rect(hwnd), max_width=int(args.get("max_width", 960)), path=args.get("path"))
    result["frame"] = frame
    return result


def register(registry):
    registry["capture_viewport"] = {"handler": capture_viewport, "description": "Captures the iClone 3D viewport (current camera, optionally at a frame) and returns it as an image, to check what a pose or scene looks like. Works even when iClone is covered by other windows; click() afterwards takes x/y in this image.", "inputSchema": {"type": "object", "properties": {"frame": {"type": "integer", "minimum": 0}, "max_width": {"type": "integer", "minimum": 64}, "path": {"type": "string"}}}}
    registry["get_render_settings"] = {"handler": get_render_settings, "main_thread": True, "description": "Reads the dimensions from iClone's current render settings.", "inputSchema": {"type": "object", "properties": {}}}
    registry["render_video"] = {"handler": render_video, "main_thread": True, "description": "Starts video rendering with the current iClone project settings. Requires explicit confirmation.", "inputSchema": {"type": "object", "properties": {"confirm": {"type": "string", "description": "Exact value: RENDER_VIDEO_WITH_CURRENT_SETTINGS"}}, "required": ["confirm"]}}

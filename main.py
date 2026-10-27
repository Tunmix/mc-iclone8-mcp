import json
import importlib
import os
import threading

rl_plugin_info = {"ap": "iClone", "ap_version": "8.0"}

try:
    import RLPy
    from PySide2 import QtCore, QtWidgets
    QtCore.QStringListModel
    from shiboken2 import wrapInstance
    _ICLONE_AVAILABLE = True
except ImportError:
    RLPy = None
    QtCore = QtWidgets = wrapInstance = None
    _ICLONE_AVAILABLE = False

_server = None
_dialog = None
_callbacks = []
_lock = threading.RLock()


def _config():
    try:
        with open(os.path.join(os.path.dirname(__file__), "mcp_config.json"), "r") as stream:
            return json.load(stream)
    except Exception:
        return {"server": {"host": "127.0.0.1", "port": 8766}}


def _tool_registry():
    # Reload tool modules when the server starts. iClone keeps Python modules in
    # memory after a stop/start cycle, while a developer may have updated them
    # on disk. This makes the Start button load the current plugin code.
    # Every module in tools/ with a register() function is picked up, so the
    # reload_tools tool also sees modules added while iClone is running.
    folder = os.path.join(os.path.dirname(__file__), "tools")
    names = sorted(name[:-3] for name in os.listdir(folder) if name.endswith(".py") and not name.startswith("_"))
    # Shared helpers are reloaded first so dependent modules bind their new code.
    names = [name for name in ("common", "objects") if name in names] + [name for name in names if name not in ("common", "objects")]
    tools = {}
    for name in names:
        module = importlib.reload(importlib.import_module("tools." + name))
        if hasattr(module, "register"):
            module.register(tools)
    return tools


def start_server():
    global _server
    with _lock:
        if _server and _server.running:
            return "http://%s:%d/mcp" % (_server.host, _server.port)
        from dispatch import start as start_dispatch
        import mcp_handler
        from server import MCPServer
        importlib.reload(mcp_handler)
        start_dispatch()
        config = _config()["server"]
        candidate = MCPServer(mcp_handler.MCPHandler(_tool_registry(), _tool_registry), config["host"], config["port"])
        candidate.start()
        _server = candidate
        return "http://%s:%d/mcp" % (candidate.host, candidate.port)


def stop_server():
    global _server
    with _lock:
        candidate, _server = _server, None
        if candidate:
            candidate.stop()
            return True
        return False


if _ICLONE_AVAILABLE:
    class DialogCallback(RLPy.RDialogCallback):
        def __init__(self):
            RLPy.RDialogCallback.__init__(self)

        def OnDialogClose(self):
            # Closing the window only drops the UI; the server keeps running
            # until the Stop button is used or iClone exits.
            global _dialog
            _dialog = None
            return True


def show_dialog():
    global _dialog
    if not _ICLONE_AVAILABLE:
        raise RuntimeError("mc-iclone8-mcp must be loaded inside iClone 8")
    if _dialog is not None:
        _dialog.Show()
        return
    _dialog = RLPy.RUi.CreateRDialog()
    _dialog.SetWindowTitle("mc-iclone8-mcp")
    callback = DialogCallback()
    _callbacks.append(callback)
    _dialog.RegisterEventCallback(callback)
    dialog = wrapInstance(int(_dialog.GetWindow()), QtWidgets.QDialog)
    layout = dialog.layout()
    label = QtWidgets.QLabel("iClone 8 MCP server\nPort 8766\nDocumented API; commands run on the iClone main thread.")
    layout.addWidget(label)
    with _lock:
        running = _server is not None and _server.running
        url = "http://%s:%d/mcp" % (_server.host, _server.port) if running else None
    status = QtWidgets.QLabel("STATUS: listening on " + url if running else "STATUS: stopped")
    layout.addWidget(status)
    start_button = QtWidgets.QPushButton("Start")
    stop_button = QtWidgets.QPushButton("Stop")
    layout.addWidget(start_button)
    layout.addWidget(stop_button)

    def start_from_ui():
        try:
            status.setText("STATUS: listening on " + start_server())
        except Exception as error:
            status.setText("STATUS: error - " + str(error))

    def stop_from_ui():
        status.setText("STATUS: stopped" if stop_server() else "STATUS: already stopped")

    start_button.clicked.connect(start_from_ui)
    stop_button.clicked.connect(stop_from_ui)
    _dialog.Show()


def initialize_plugin():
    if not _ICLONE_AVAILABLE:
        raise RuntimeError("mc-iclone8-mcp must be loaded inside iClone 8")
    main_window = wrapInstance(int(RLPy.RUi.GetMainWindow()), QtWidgets.QMainWindow)
    menu = main_window.menuBar().findChild(QtWidgets.QMenu, "mc_iclone8_mcp_menu")
    if menu is None:
        menu = wrapInstance(int(RLPy.RUi.AddMenu("mc-iclone8-mcp", RLPy.EMenu_Plugins)), QtWidgets.QMenu)
        menu.setObjectName("mc_iclone8_mcp_menu")
    action = menu.addAction("Open MCP server")
    action.triggered.connect(show_dialog)
    # The server runs for the whole iClone session; the dialog's Stop/Start still work.
    QtWidgets.QApplication.instance().aboutToQuit.connect(stop_server)
    try:
        start_server()
    except Exception as error:
        print("mc-iclone8-mcp: server did not start automatically: %s" % error)


def run_script():
    if not _ICLONE_AVAILABLE:
        raise RuntimeError("mc-iclone8-mcp must be loaded inside iClone 8")
    initialize_plugin()

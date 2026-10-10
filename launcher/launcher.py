"""OmniSportsSwitch launcher: one double-click starts everything and lives in the system tray.

  * starts the daemon with no console window (nothing to close by accident), unless it is already running
  * opens the Edge test profile with the extension loaded, unless it is already running
  * a tray icon shows the daemon's state and offers Start / Stop / Restart, Open Edge, Open the logs, Quit

The launcher does not contain the daemon: it starts `.venv\\Scripts\\python.exe -m daemon.server` of this project, so the
.exe must sit inside the project folder (or a folder below it, such as dist\\). It is built by scripts\\build_launcher.ps1.

    OmniSportsSwitch.exe            start everything and show the tray icon
    OmniSportsSwitch.exe --status   print the state as JSON and exit (for checks)
    OmniSportsSwitch.exe --no-edge  do not open Edge
"""
import ctypes
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import threading
import time

DAEMON_PORT = 8765
EDGE_DEBUG_PORT = 9222
CREATE_NO_WINDOW = 0x08000000
EDGE_PATHS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
DAEMON_START_WAIT_S = 90   # the models take a while to load


def app_dir() -> pathlib.Path:
    """The folder of the .exe (or of this file when run from source)."""
    return pathlib.Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent


def find_root(start: pathlib.Path | None = None) -> pathlib.Path | None:
    """The project folder: the nearest folder (this one or a parent) that holds daemon\\server.py and the .venv."""
    folder = (start or app_dir()).resolve()
    for candidate in [folder, *list(folder.parents)[:4]]:
        if (candidate / "daemon" / "server.py").exists() and (candidate / ".venv" / "Scripts" / "python.exe").exists():
            return candidate
    return None


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def daemon_command(root: pathlib.Path) -> list[str]:
    return [str(root / ".venv" / "Scripts" / "python.exe"), "-m", "daemon.server"]


def edge_executable() -> str | None:
    return next((p for p in EDGE_PATHS if os.path.exists(p)), None)


def edge_command(root: pathlib.Path, edge: str, local_app_data: str | None = None) -> list[str]:
    """The same command line as scripts\\start_test_edge.ps1: a separate profile, the extension preloaded, debug port."""
    profile = pathlib.Path(local_app_data or os.environ.get("LOCALAPPDATA", "")) / "OmniSportsSwitch" / "edge-profile"
    return [edge, f"--remote-debugging-port={EDGE_DEBUG_PORT}", f"--user-data-dir={profile}",
            f"--load-extension={root / 'extension'}", "--disable-features=DisableLoadExtensionCommandLineSwitch",
            "--no-first-run", "--restore-last-session"]


def listening_pid(port: int) -> int | None:
    """The process id that listens on a local TCP port (from netstat), or None."""
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW).stdout
    for line in out.splitlines():
        m = re.match(rf"\s*TCP\s+\S+:{port}\s+\S+\s+LISTENING\s+(\d+)", line)
        if m:
            return int(m.group(1))
    return None


class Launcher:
    def __init__(self, root: pathlib.Path):
        self.root = root
        self.process: subprocess.Popen | None = None   # the daemon, when this launcher started it
        self.message = ""

    # ---- daemon -------------------------------------------------------------------------------------------
    def daemon_running(self) -> bool:
        return port_open(DAEMON_PORT)

    def start_daemon(self) -> bool:
        if self.daemon_running():
            return True
        self.process = subprocess.Popen(daemon_command(self.root), cwd=self.root, creationflags=CREATE_NO_WINDOW,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + DAEMON_START_WAIT_S
        while time.time() < deadline:
            if self.process.poll() is not None:
                self.message = "The daemon stopped right after starting (see logs\\daemon.log)."
                return False
            if self.daemon_running():
                return True
            time.sleep(0.5)
        self.message = "The daemon did not start in time (see logs\\daemon.log)."
        return False

    def stop_daemon(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        pid = listening_pid(DAEMON_PORT)   # started elsewhere (for example by start_all.ps1)
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=CREATE_NO_WINDOW)

    def restart_daemon(self) -> bool:
        self.stop_daemon()
        time.sleep(1.5)
        return self.start_daemon()

    # ---- Edge ---------------------------------------------------------------------------------------------
    def edge_running(self) -> bool:
        return port_open(EDGE_DEBUG_PORT)

    def start_edge(self) -> bool:
        if self.edge_running():
            return True
        edge = edge_executable()
        if edge is None:
            self.message = "Microsoft Edge was not found."
            return False
        subprocess.Popen(edge_command(self.root, edge), creationflags=CREATE_NO_WINDOW)
        return True

    # ---- helpers ------------------------------------------------------------------------------------------
    def status(self) -> dict:
        return {"root": str(self.root), "daemon": self.daemon_running(), "edge": self.edge_running(), "message": self.message}

    def open_logs(self, name: str | None = None) -> None:
        target = self.root / "logs" / name if name else self.root / "logs"
        if target.exists():
            os.startfile(target)   # noqa: S606  (opens the folder or file in its normal program)


def single_instance() -> bool:
    """False if another launcher is already running (a named Windows mutex)."""
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, "OmniSportsSwitchLauncher")
    return ctypes.windll.kernel32.GetLastError() != 183   # ERROR_ALREADY_EXISTS


def make_icon_image():
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, 62, 62), fill=(21, 101, 192, 255))
    draw.polygon([(24, 16), (24, 48), (50, 32)], fill=(255, 255, 255, 255))   # a play triangle
    return image


def run_tray(launcher: Launcher, open_edge: bool) -> None:
    import pystray

    def in_thread(function):
        return lambda icon, item: threading.Thread(target=function, daemon=True).start()

    def notify(text: str) -> None:
        try:
            icon.notify(text, "OmniSportsSwitch")
        except Exception:
            pass

    def start_all():
        notify("Starting the daemon (about 15 seconds)...")
        ok = launcher.start_daemon()
        if open_edge:
            launcher.start_edge()
        notify("Ready: track your tabs with Ctrl+Shift+Y." if ok else launcher.message)

    def restart():
        notify("Restarting the daemon...")
        notify("Daemon restarted." if launcher.restart_daemon() else launcher.message)

    def stop():
        launcher.stop_daemon()
        notify("Daemon stopped.")

    def quit_leave(icon_, item):
        icon_.stop()

    def quit_stop(icon_, item):
        launcher.stop_daemon()
        icon_.stop()

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: f"Daemon: {'running' if launcher.daemon_running() else 'stopped'}", None, enabled=False),
        pystray.MenuItem(lambda item: f"Edge test profile: {'open' if launcher.edge_running() else 'closed'}", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start daemon", in_thread(lambda: (launcher.start_daemon(), notify("Daemon running.")))),
        pystray.MenuItem("Restart daemon", in_thread(restart)),
        pystray.MenuItem("Stop daemon", in_thread(stop)),
        pystray.MenuItem("Open Edge (test profile)", in_thread(launcher.start_edge)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open events log", lambda icon_, item: launcher.open_logs("events.log")),
        pystray.MenuItem("Open logs folder", lambda icon_, item: launcher.open_logs()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit (leave the daemon running)", quit_leave),
        pystray.MenuItem("Quit and stop the daemon", quit_stop),
    )
    icon = pystray.Icon("OmniSportsSwitch", make_icon_image(), "OmniSportsSwitch", menu)
    threading.Thread(target=start_all, daemon=True).start()
    icon.run()


def main(argv: list[str]) -> int:
    root = find_root()
    if "--status" in argv:
        if root is None:
            print(json.dumps({"error": "project folder not found"}))
            return 1
        print(json.dumps(Launcher(root).status()))
        return 0
    if root is None:
        ctypes.windll.user32.MessageBoxW(0, "Put OmniSportsSwitch.exe inside the OmniSportsSwitch project folder "
                                            "(the one with the daemon folder and the .venv).", "OmniSportsSwitch", 0x10)
        return 1
    if not single_instance():
        return 0
    run_tray(Launcher(root), open_edge="--no-edge" not in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

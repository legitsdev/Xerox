from __future__ import annotations

import os
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path


APP_NAME = "xerox"


class LaunchPreflightError(RuntimeError):
    def __init__(self, message: str, recovery: str | None = None):
        super().__init__(message)
        self.recovery = recovery


def package_root() -> Path:
    return Path(__file__).resolve().parent


def repo_root() -> Path:
    return package_root().parent


def engine_script_path() -> Path:
    return repo_root() / "cloner.py"


def venv_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / ".venv"


def venv_python_path(root: Path | None = None) -> Path:
    current_root = root or repo_root()
    if sys.platform == "win32":
        return current_root / ".venv" / "Scripts" / "python.exe"
    return current_root / ".venv" / "bin" / "python"


def install_command_hint() -> str:
    if sys.platform == "win32":
        return r"Run .\install.ps1 in PowerShell or install.bat in Command Prompt."
    return "Run ./install.sh from the repository root."


def playwright_install_command_hint() -> str:
    if sys.platform == "win32":
        return r".\.venv\Scripts\python.exe -m playwright install chromium"
    return "./.venv/bin/python -m playwright install chromium"


def launch_command_hint() -> str:
    if sys.platform == "win32":
        return r"Launch with .\xerox.ps1 in PowerShell or xerox.bat in Command Prompt."
    return "Launch with ./xerox from the repository root."


def default_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_NAME


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_path(path: Path) -> None:
    target = str(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", target])
    elif sys.platform == "win32":
        os.startfile(target)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", target])


def open_url(url: str) -> None:
    webbrowser.open(url, new=2)


def find_available_port(preferred: int = 4173) -> int:
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No free port available in probe range")

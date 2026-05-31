from __future__ import annotations

import atexit
import functools
import http.server
import os
import socket
import socketserver
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock, Thread


APP_NAME = "xerox"


class LaunchPreflightError(RuntimeError):
    def __init__(self, message: str, recovery: str | None = None):
        super().__init__(message)
        self.recovery = recovery


@dataclass
class PreviewServerRecord:
    root: Path
    port: int
    server: socketserver.TCPServer
    thread: Thread
    base_url: str | None = None
    saved_pages: dict[str, str] = field(default_factory=dict)


class PreviewRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str | None = None, base_url: str | None = None, saved_pages: dict[str, str] | None = None, **kwargs):
        self.preview_root = Path(directory or ".").resolve()
        self.base_url = base_url or ""
        self.saved_pages = saved_pages or {}
        super().__init__(*args, directory=directory, **kwargs)

    def _canonicalize_url(self, url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path or "/"
        return urllib.parse.urlunparse(
            parsed._replace(
                scheme=parsed.scheme.lower(),
                netloc=parsed.netloc.lower(),
                path=path,
                fragment="",
            )
        )

    def _is_probable_page_path(self, path: str) -> bool:
        lowered = (path or "/").lower()
        suffix = Path(lowered).suffix
        if suffix in {
            ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
            ".avif", ".apng", ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp4", ".webm",
            ".mp3", ".wav", ".m4a", ".mov", ".json", ".webmanifest", ".map", ".xml", ".txt",
        }:
            return False
        if lowered.startswith("/api/") or lowered == "/api":
            return False
        return True

    def _resolve_saved_page(self, request_path: str, query: str) -> str | None:
        if not self.base_url:
            return None
        absolute_url = urllib.parse.urljoin(self.base_url, request_path or "/")
        if query:
            parsed = urllib.parse.urlparse(absolute_url)
            absolute_url = urllib.parse.urlunparse(parsed._replace(query=query))
        canonical_url = self._canonicalize_url(absolute_url)
        return self.saved_pages.get(canonical_url)

    def _build_origin_url(self, request_path: str, query: str) -> str | None:
        if not self.base_url:
            return None
        absolute_url = urllib.parse.urljoin(self.base_url, request_path or "/")
        if query:
            parsed = urllib.parse.urlparse(absolute_url)
            absolute_url = urllib.parse.urlunparse(parsed._replace(query=query))
        return absolute_url

    def _handle_preview_request(self, head_only: bool = False) -> bool:
        parsed = urllib.parse.urlsplit(self.path)
        request_path = parsed.path or "/"

        if request_path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return True

        if "_rsc" in urllib.parse.parse_qs(parsed.query, keep_blank_values=True):
            self.send_response(204)
            self.end_headers()
            return True

        saved_page = self._resolve_saved_page(request_path, parsed.query)
        if saved_page:
            self.path = "/" + saved_page.lstrip("/")
            if head_only:
                super().do_HEAD()
            else:
                super().do_GET()
            return True

        translated = Path(self.translate_path(request_path))
        if translated.exists():
            if head_only:
                super().do_HEAD()
            else:
                super().do_GET()
            return True

        if self._is_probable_page_path(request_path):
            redirect_url = self._build_origin_url(request_path, parsed.query)
            if redirect_url:
                self.send_response(302)
                self.send_header("Location", redirect_url)
                self.end_headers()
                return True

        return False

    def do_GET(self) -> None:
        if self._handle_preview_request(head_only=False):
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if self._handle_preview_request(head_only=True):
            return
        super().do_HEAD()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


class ThreadingPreviewServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class PreviewServerManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._servers: dict[Path, PreviewServerRecord] = {}
        atexit.register(self.close_all)

    def ensure_url(self, root: Path, entry_file: str, base_url: str | None = None, saved_pages: dict[str, str] | None = None) -> str:
        preview_root = root.expanduser().resolve()
        record = self._ensure_server(preview_root, base_url=base_url, saved_pages=saved_pages)
        return f"http://127.0.0.1:{record.port}/{entry_file.lstrip('/')}"

    def _ensure_server(self, root: Path, base_url: str | None = None, saved_pages: dict[str, str] | None = None) -> PreviewServerRecord:
        with self._lock:
            record = self._servers.get(root)
            if record and record.thread.is_alive():
                record.base_url = base_url or record.base_url
                if saved_pages is not None:
                    record.saved_pages = dict(saved_pages)
                return record

            handler = functools.partial(
                PreviewRequestHandler,
                directory=str(root),
                base_url=base_url,
                saved_pages=dict(saved_pages or {}),
            )
            port = find_ephemeral_port()
            server = ThreadingPreviewServer(("127.0.0.1", port), handler)
            thread = Thread(
                target=server.serve_forever,
                name=f"xerox-preview-{port}",
                daemon=True,
            )
            thread.start()
            record = PreviewServerRecord(
                root=root,
                port=port,
                server=server,
                thread=thread,
                base_url=base_url,
                saved_pages=dict(saved_pages or {}),
            )
            self._servers[root] = record

        wait_for_port("127.0.0.1", record.port)
        return record

    def close_all(self) -> None:
        with self._lock:
            records = list(self._servers.values())
            self._servers.clear()

        for record in records:
            try:
                record.server.shutdown()
            except OSError:
                pass
            try:
                record.server.server_close()
            except OSError:
                pass


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


def find_ephemeral_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(host: str, port: int, timeout: float = 1.5) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            try:
                sock.connect((host, port))
                return
            except OSError:
                pass
        time.sleep(0.05)


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

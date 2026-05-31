#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MIN_VERSION = (3, 10)


def format_command(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def log(message: str) -> None:
    print(f"[xerox install] {message}", flush=True)


def fail(message: str, exit_code: int = 1) -> None:
    print(f"[xerox install] {message}", file=sys.stderr, flush=True)
    raise SystemExit(exit_code)


def venv_python_path(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def run(command: list[str], cwd: Path, hint: str | None = None) -> None:
    log(format_command(command))
    try:
        subprocess.run(command, cwd=str(cwd), check=True)
    except subprocess.CalledProcessError as exc:
        if hint:
            print(f"[xerox install] {hint}", file=sys.stderr, flush=True)
        raise SystemExit(exc.returncode) from exc


def smoke_check(venv_python: Path, repo_root: Path) -> None:
    smoke_code = """
from pathlib import Path
import tempfile
from xerox_app.preflight import ensure_runtime_ready
from xerox_app.web import create_app

tmp_dir = Path(tempfile.mkdtemp(prefix='xerox-smoke-'))
app = create_app(tmp_dir)
assert app.title == 'xerox'
ensure_runtime_ready()
print('smoke-ok')
""".strip()
    try:
        run([str(venv_python), "-c", smoke_code], cwd=repo_root)
        run([str(venv_python), "-m", "xerox", "--help"], cwd=repo_root)
    finally:
        for temp_dir in Path(tempfile.gettempdir()).glob("xerox-smoke-*"):
            shutil.rmtree(temp_dir, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap the Xerox local environment.")
    parser.add_argument("--shell", default="unix", help="User shell hint for completion output.")
    parser.add_argument("--launcher", default="./xerox", help="Launcher command to print after install.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if sys.version_info < MIN_VERSION:
        fail(
            f"Python {MIN_VERSION[0]}.{MIN_VERSION[1]}+ is required. Current interpreter: "
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        )

    repo_root = Path(__file__).resolve().parent.parent
    venv_dir = repo_root / ".venv"

    log(f"Using Python: {sys.executable}")
    venv_python = venv_python_path(venv_dir)
    if venv_dir.exists() and not venv_python.exists():
        log(f"Removing incomplete virtual environment: {venv_dir}")
        shutil.rmtree(venv_dir, ignore_errors=True)

    if not venv_dir.exists():
        run([sys.executable, "-m", "venv", str(venv_dir)], cwd=repo_root)
    else:
        log(f"Reusing existing virtual environment: {venv_dir}")

    if not venv_python.exists():
        fail(f"Virtual environment is incomplete: {venv_python} was not created.")

    run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], cwd=repo_root)
    run([str(venv_python), "-m", "pip", "install", "-e", "."], cwd=repo_root)
    run(
        [str(venv_python), "-m", "playwright", "install", "chromium"],
        cwd=repo_root,
        hint=(
            "Playwright browser install failed. On some Linux systems you may need extra system "
            "packages; follow the error above, install the missing packages, then rerun this installer."
        ),
    )
    smoke_check(venv_python, repo_root)

    log("Install complete.")
    print()
    print("Next step:")
    print(f"  {args.launcher}")
    print()
    print("This will start the Xerox web UI and open it in your browser.")


if __name__ == "__main__":
    main()

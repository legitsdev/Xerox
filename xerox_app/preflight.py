from __future__ import annotations

import importlib.util
from pathlib import Path

from .runtime import (
    LaunchPreflightError,
    engine_script_path,
    install_command_hint,
    package_root,
    playwright_install_command_hint,
)


REQUIRED_MODULES = ("fastapi", "uvicorn", "requests", "bs4", "playwright")


def _missing_modules() -> list[str]:
    return [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]


def _check_playwright_browser() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - defensive import path
        raise LaunchPreflightError(
            "The Xerox runtime dependencies are incomplete.",
            f"{install_command_hint()} This will reinstall the missing Python packages. ({exc})",
        ) from exc

    try:
        with sync_playwright() as playwright:
            executable_path = Path(playwright.chromium.executable_path)
            if not executable_path.exists():
                raise LaunchPreflightError(
                    "Playwright Chromium is not installed yet.",
                    f"{install_command_hint()} This will install the required browser runtime.",
                )
    except LaunchPreflightError:
        raise
    except Exception as exc:  # pragma: no cover - defensive browser probe
        message = str(exc).strip()
        raise LaunchPreflightError(
            "Playwright Chromium is not ready.",
            f"{install_command_hint()} If the problem persists, run `{playwright_install_command_hint()}` from the repo root. ({message})",
        ) from exc


def ensure_runtime_ready() -> None:
    if not engine_script_path().exists():
        raise LaunchPreflightError(
            "The Xerox engine file `cloner.py` is missing.",
            "Restore the repository contents and try again.",
        )

    ui_dir = package_root() / "ui"
    if not ui_dir.exists():
        raise LaunchPreflightError(
            "The Xerox UI assets are missing.",
            "Restore the repository contents and try again.",
        )

    missing = _missing_modules()
    if missing:
        joined = ", ".join(missing)
        raise LaunchPreflightError(
            f"The Xerox runtime dependencies are incomplete: {joined}.",
            f"{install_command_hint()} This will create the environment and install all required packages.",
        )

    _check_playwright_browser()

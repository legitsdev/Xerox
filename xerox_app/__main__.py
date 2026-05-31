from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from .preflight import ensure_runtime_ready
from .runtime import LaunchPreflightError, default_data_dir, ensure_dir, find_available_port, open_url


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the Xerox local web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface for the local server.")
    parser.add_argument("--port", type=int, default=4173, help="Preferred port for the local server.")
    parser.add_argument("--data-dir", default="", help="Override Xerox data directory.")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open the browser.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        ensure_runtime_ready()
    except LaunchPreflightError as exc:
        print(f"xerox: {exc}", file=sys.stderr)
        if exc.recovery:
            print(exc.recovery, file=sys.stderr)
        raise SystemExit(1)

    import uvicorn

    from .web import create_app

    data_dir = ensure_dir((default_data_dir() if not args.data_dir else Path(args.data_dir)).expanduser().resolve())
    port = find_available_port(args.port or 4173)

    app = create_app(data_dir)
    app.state.base_url = f"http://{args.host}:{port}"

    if not args.no_open:
        threading.Timer(0.8, lambda: open_url(app.state.base_url)).start()

    uvicorn.run(app, host=args.host, port=port, log_level="info")


if __name__ == "__main__":
    main()

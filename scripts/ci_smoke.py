#!/usr/bin/env python3
from __future__ import annotations

import argparse
import functools
import http.server
import json
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


def read_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def post_empty(url: str) -> dict:
    request = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def read_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode("utf-8", errors="ignore")


def resolve_final_url(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.geturl()


def tail_text(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    return text[-limit:]


def terminate_process(process: subprocess.Popen[bytes | str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


class ThreadingServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def wait_for_json(url: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return read_json(url)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for JSON endpoint: {url}")


def create_local_origin(root: Path) -> str:
    assets_dir = root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    origin_host = "127.0.0.1"
    port = find_free_port(origin_host)
    origin = f"http://{origin_host}:{port}"

    (root / "style.css").write_text(
        "body{font-family:sans-serif;background:#fafafa;color:#111;}#hero{font-size:28px;}#from-style{display:block;}",
        encoding="utf-8",
    )
    (root / "late.css").write_text(
        "#late-style-check{display:block;color:#0a7f3f;} body{outline:2px solid #0a7f3f;}",
        encoding="utf-8",
    )
    (assets_dir / "pixel.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12'><rect width='12' height='12' fill='#0a7f3f'/></svg>",
        encoding="utf-8",
    )
    (root / "more").write_text(
        "<!doctype html><html><head><title>More</title></head><body><h1>More page</h1></body></html>",
        encoding="utf-8",
    )
    (root / "bootstrap.js").write_text(
        (
            "window.__smoke_bootstrap = true;\n"
            "setTimeout(() => {\n"
            "  const link = document.createElement('link');\n"
            "  link.rel = 'stylesheet';\n"
            "  link.href = "
            + json.dumps(f"{origin}/late.css")
            + ";\n"
            "  link.dataset.late = '1';\n"
            "  document.head.appendChild(link);\n"
            "  const marker = document.createElement('div');\n"
            "  marker.id = 'late-style-check';\n"
            "  marker.textContent = 'late-style-ready';\n"
            "  document.body.appendChild(marker);\n"
            "}, 60);\n"
        ),
        encoding="utf-8",
    )
    (root / "index.html").write_text(
        f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Xerox Smoke Target</title>
    <link rel="stylesheet" href="/style.css" />
    <script defer src="/bootstrap.js"></script>
  </head>
  <body>
    <h1 id="hero">Xerox Smoke Target</h1>
    <p id="from-style">styled text</p>
    <img alt="pixel" src="/assets/pixel.svg" />
    <a id="more" href="/more">More</a>
  </body>
</html>
""",
        encoding="utf-8",
    )

    handler = functools.partial(QuietHandler, directory=str(root))
    server = ThreadingServer((origin_host, port), handler)
    return origin, server


def verify_preview(preview_url: str, origin: str) -> None:
    from playwright.sync_api import sync_playwright

    console_events: list[tuple[str, str]] = []
    bad_responses: list[tuple[int, str]] = []
    failed_requests: list[tuple[str, str]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("console", lambda msg: console_events.append((msg.type, msg.text)))
        page.on("requestfailed", lambda req: failed_requests.append((req.url, str(req.failure))))
        page.on("response", lambda resp: bad_responses.append((resp.status, resp.url)) if resp.status >= 400 else None)

        page.goto(preview_url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(300)

        title = page.title()
        hero_text = page.locator("#hero").inner_text()
        late_href = page.locator("link[data-late='1']").get_attribute("href")
        marker_text = page.locator("#late-style-check").inner_text()

        if title != "Xerox Smoke Target":
            raise RuntimeError(f"Unexpected preview title: {title}")
        if hero_text != "Xerox Smoke Target":
            raise RuntimeError(f"Unexpected preview hero: {hero_text}")
        if late_href not in {"./late.css", "late.css"}:
            raise RuntimeError(f"Runtime asset rewrite failed for dynamic stylesheet: {late_href}")
        if marker_text != "late-style-ready":
            raise RuntimeError("Dynamic runtime marker did not appear in preview.")

        problematic_console = [item for item in console_events if item[0] in {"error", "warning"}]
        if problematic_console:
            raise RuntimeError(f"Unexpected preview console output: {problematic_console}")
        if bad_responses:
            raise RuntimeError(f"Unexpected preview 4xx/5xx responses: {bad_responses}")
        if failed_requests:
            raise RuntimeError(f"Unexpected failed preview requests: {failed_requests}")

        browser.close()

    redirect_probe = urllib.parse.urljoin(preview_url, "more")
    if resolve_final_url(redirect_probe) != f"{origin}/more":
        raise RuntimeError("Unsaved page route did not redirect to the original page.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cross-platform Xerox launcher smoke test.")
    parser.add_argument("--python", required=True, help="Interpreter path used to launch `python -m xerox`.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4310)
    parser.add_argument("--data-dir", default=".tmp-ci")
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    data_dir = (repo_root / args.data_dir).resolve()
    log_path = data_dir / "launcher.log"
    origin_root = data_dir / "origin"

    if data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    origin_root.mkdir(parents=True, exist_ok=True)

    origin, origin_server = create_local_origin(origin_root)
    origin_runner = threading.Thread(target=origin_server.serve_forever, daemon=True)
    origin_runner.start()

    command = [
        args.python,
        "-m",
        "xerox",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--data-dir",
        str(data_dir),
        "--no-open",
    ]

    print(f"[ci smoke] launching: {' '.join(command)}", flush=True)
    with log_path.open("w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=str(repo_root),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )

    base_url = f"http://{args.host}:{args.port}"
    deadline = time.time() + args.timeout
    meta_payload: dict | None = None
    history_payload: dict | list | None = None

    try:
        while time.time() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    "Xerox launcher exited before the API became ready.\n"
                    f"{tail_text(log_path)}"
                )
            try:
                meta_payload = read_json(f"{base_url}/api/meta")
                history_payload = read_json(f"{base_url}/api/history")
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                time.sleep(1)

        if meta_payload is None or history_payload is None:
            raise RuntimeError(
                "Timed out waiting for Xerox API.\n"
                f"{tail_text(log_path)}"
            )

        app_name = meta_payload.get("app") or meta_payload.get("name")
        if app_name != "xerox":
            raise RuntimeError(f"Unexpected /api/meta payload: {meta_payload}")
        if not isinstance(history_payload, (list, dict)):
            raise RuntimeError(f"Unexpected /api/history payload: {history_payload}")
        if isinstance(history_payload, dict) and not isinstance(history_payload.get("items"), list):
            raise RuntimeError(f"Unexpected /api/history payload: {history_payload}")

        job = post_json(
            f"{base_url}/api/jobs",
            {"url": f"{origin}/index.html", "mode": "page"},
        )
        job_id = job.get("id")
        if not job_id:
            raise RuntimeError(f"Create job did not return an id: {job}")

        job_deadline = time.time() + args.timeout
        while time.time() < job_deadline:
            current_job = read_json(f"{base_url}/api/jobs/{job_id}")
            if current_job.get("status") in {"done", "failed"}:
                job = current_job
                break
            time.sleep(1)
        else:
            raise RuntimeError("Timed out waiting for clone job to finish.")

        if job.get("status") != "done":
            raise RuntimeError(f"Clone job failed: {job}")
        if job.get("entry_file") != "index.html":
            raise RuntimeError(f"Unexpected entry file: {job.get('entry_file')}")
        if int(job.get("saved_pages_count") or 0) != 1:
            raise RuntimeError(f"Unexpected saved_pages_count: {job.get('saved_pages_count')}")
        if int(job.get("downloaded_resources_count") or 0) < 3:
            raise RuntimeError(
                f"Unexpected downloaded_resources_count: {job.get('downloaded_resources_count')}"
            )

        history_after = read_json(f"{base_url}/api/history")
        history_items = history_after.get("items", []) if isinstance(history_after, dict) else []
        if not any(item.get("id") == job_id for item in history_items):
            raise RuntimeError("Finished job was not written to history.")

        found_links = read_text(f"{base_url}/api/jobs/{job_id}/found-links.txt")
        expected_links = {f"{origin}/index.html", f"{origin}/more"}
        if not expected_links.issubset(set(line.strip() for line in found_links.splitlines() if line.strip())):
            raise RuntimeError(f"Unexpected found_links export: {found_links}")

        preview_payload = post_empty(f"{base_url}/api/jobs/{job_id}/open-site")
        preview_url = preview_payload.get("url", "")
        if not preview_url.startswith("http://127.0.0.1:") or preview_url.startswith(base_url):
            raise RuntimeError(f"Unexpected preview url: {preview_payload}")

        preview_html = read_text(preview_url)
        if "Xerox Smoke Target" not in preview_html:
            raise RuntimeError("Preview HTML did not contain expected title text.")

        verify_preview(preview_url, origin)

        print(f"[ci smoke] meta ok: {meta_payload}", flush=True)
        print(f"[ci smoke] history ok: {history_payload}", flush=True)
        print(f"[ci smoke] clone ok: {job_id}", flush=True)
    finally:
        terminate_process(process)
        origin_server.shutdown()
        origin_server.server_close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ci smoke] failure: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)

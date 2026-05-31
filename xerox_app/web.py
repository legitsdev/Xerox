from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .runtime import PreviewServerManager, default_data_dir, open_path, open_url, repo_root
from .state import JobManager


UI_DIR = Path(__file__).resolve().parent / "ui"


def create_app(data_dir: Path | None = None) -> FastAPI:
    resolved_data_dir = (data_dir or default_data_dir()).expanduser().resolve()
    manager = JobManager(resolved_data_dir)

    app = FastAPI(title="xerox", version=__version__)
    app.state.manager = manager
    app.state.data_dir = resolved_data_dir
    app.state.preview_manager = PreviewServerManager()

    app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")

    @app.get("/favicon.ico")
    def favicon() -> FileResponse:
        return FileResponse(UI_DIR / "cobalt" / "favicon.png")

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        return {
            "name": "xerox",
            "version": __version__,
            "data_dir": str(resolved_data_dir),
            "repo_root": str(repo_root()),
            "python": sys.executable,
            "history_count": len(manager.list_history()),
        }

    @app.get("/api/history")
    def history() -> dict[str, Any]:
        items = manager.list_history()
        running = sum(1 for item in items if item["status"] == "running")
        return {"items": items, "running": running, "total": len(items)}

    @app.post("/api/jobs")
    async def create_job(request: Request) -> JSONResponse:
        payload = await request.json()
        requested_url = str(payload.get("url", "")).strip()
        mode = str(payload.get("mode", "page")).strip().lower()
        if not requested_url:
            raise HTTPException(status_code=400, detail="URL is required")
        if mode not in {"page", "crawl", "site"}:
            raise HTTPException(status_code=400, detail="Mode must be page or crawl")
        job = manager.create_job(requested_url, mode)
        return JSONResponse(job)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> JSONResponse:
        job = manager.get(job_id, include_text=True)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return JSONResponse(job)

    @app.get("/api/jobs/{job_id}/found-links.txt")
    def found_links(job_id: str) -> FileResponse:
        target_path = manager.export_found_links(job_id)
        if not target_path or not target_path.exists():
            raise HTTPException(status_code=404, detail="Job not found")
        return FileResponse(target_path, media_type="text/plain", filename="found_links.txt")

    @app.post("/api/jobs/{job_id}/open-folder")
    def open_folder(job_id: str) -> dict[str, str]:
        job = manager.get(job_id, include_text=False)
        if not job or not job.get("output_dir"):
            raise HTTPException(status_code=404, detail="Job not found")
        open_path(Path(job["output_dir"]))
        return {"status": "ok"}

    @app.post("/api/jobs/{job_id}/open-site")
    def open_site(job_id: str) -> dict[str, str]:
        job = manager.get(job_id, include_text=False)
        entry_file = job.get("entry_file") if job else None
        output_dir = job.get("output_dir") if job else None
        if not job or not entry_file or not output_dir:
            raise HTTPException(status_code=404, detail="Job entry file was not found")

        preview_url = app.state.preview_manager.ensure_url(
            Path(output_dir),
            entry_file,
            base_url=job.get("final_url") or job.get("requested_url"),
            saved_pages=job.get("saved_pages") or {},
        )
        open_url(preview_url)
        return {"status": "ok", "url": preview_url}

    @app.get("/outputs/{job_id}/{asset_path:path}")
    def output_file(job_id: str, asset_path: str) -> FileResponse:
        job = manager.get(job_id, include_text=False)
        if not job or not job.get("output_dir"):
            raise HTTPException(status_code=404, detail="Job not found")

        output_root = Path(job["output_dir"]).resolve()
        target_path = (output_root / asset_path).resolve()
        if output_root not in target_path.parents and output_root != target_path:
            raise HTTPException(status_code=403, detail="Path escapes output directory")
        if not target_path.exists() or not target_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(target_path)

    return app

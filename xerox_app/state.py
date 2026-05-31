from __future__ import annotations

import json
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any
from uuid import uuid4

from bs4 import BeautifulSoup

from .runtime import engine_script_path, ensure_dir


ALLOWED_MODES = {"page", "crawl", "site"}
GENERIC_EXIT_ERROR = "Clone process exited with a non-zero code."
URL_ATTRS = ("href", "src", "action", "data-url", "data-href", "poster")
SRCSET_ATTRS = ("srcset", "imagesrcset")


@dataclass
class JobRecord:
    id: str
    requested_url: str
    mode: str
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    final_url: str | None = None
    output_dir: str | None = None
    report_path: str | None = None
    result_path: str | None = None
    log_path: str | None = None
    entry_file: str | None = None
    entry_path: str | None = None
    saved_pages: dict[str, str] = field(default_factory=dict)
    saved_pages_count: int = 0
    downloaded_resources_count: int = 0
    clone_stats: dict[str, Any] = field(default_factory=dict)
    return_code: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "requested_url": self.requested_url,
            "mode": self.mode,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "final_url": self.final_url,
            "output_dir": self.output_dir,
            "report_path": self.report_path,
            "result_path": self.result_path,
            "log_path": self.log_path,
            "entry_file": self.entry_file,
            "entry_path": self.entry_path,
            "saved_pages": self.saved_pages,
            "saved_pages_count": self.saved_pages_count,
            "downloaded_resources_count": self.downloaded_resources_count,
            "clone_stats": self.clone_stats,
            "return_code": self.return_code,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "JobRecord":
        return cls(**payload)


class JobManager:
    def __init__(self, data_dir: Path):
        self.data_dir = ensure_dir(data_dir.expanduser().resolve())
        self.jobs_dir = ensure_dir(self.data_dir / "jobs")
        self.history_path = self.data_dir / "history.json"
        self.lock = Lock()
        self.jobs: dict[str, JobRecord] = {}
        self.threads: dict[str, Thread] = {}
        self._load_history()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _load_history(self) -> None:
        if not self.history_path.exists():
            return
        try:
            rows = json.loads(self.history_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rows = []
        for row in rows:
            record = JobRecord.from_dict(row)
            if record.status in {"queued", "running"}:
                record.status = "aborted"
                record.finished_at = record.finished_at or self._now()
                record.error = record.error or "Xerox UI was restarted while this job was still running."
            if record.error == GENERIC_EXIT_ERROR:
                record.error = None
            self.jobs[record.id] = record
        self._persist_history()

    def _persist_history(self) -> None:
        ordered = sorted(self.jobs.values(), key=lambda item: item.created_at, reverse=True)
        self.history_path.write_text(
            json.dumps([record.to_dict() for record in ordered], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list_history(self) -> list[dict[str, Any]]:
        with self.lock:
            ordered = sorted(self.jobs.values(), key=lambda item: item.created_at, reverse=True)
            return [self._serialize(record, include_text=False) for record in ordered]

    def get(self, job_id: str, include_text: bool = True) -> dict[str, Any] | None:
        with self.lock:
            record = self.jobs.get(job_id)
            if not record:
                return None
            return self._serialize(record, include_text=include_text)

    def export_found_links(self, job_id: str) -> Path | None:
        with self.lock:
            record = self.jobs.get(job_id)
            if not record:
                return None
            job_root = self.jobs_dir / job_id
            target_path = job_root / "found_links.txt"
            ordered_links = self._collect_found_links(record)
            target_path.write_text(
                ("\n".join(ordered_links) + "\n") if ordered_links else "",
                encoding="utf-8",
            )
            return target_path

    def create_job(self, requested_url: str, mode: str) -> dict[str, Any]:
        normalized_mode = "crawl" if mode == "site" else mode
        if normalized_mode not in {"page", "crawl"}:
            raise ValueError("Mode must be page or crawl")

        job_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
        job_root = ensure_dir(self.jobs_dir / job_id)
        output_dir = job_root / "site"
        report_path = job_root / "site_report.txt"
        result_path = job_root / "result.json"
        log_path = job_root / "job.log"

        record = JobRecord(
            id=job_id,
            requested_url=requested_url.strip(),
            mode=normalized_mode,
            status="queued",
            created_at=self._now(),
            output_dir=str(output_dir),
            report_path=str(report_path),
            result_path=str(result_path),
            log_path=str(log_path),
        )

        with self.lock:
            self.jobs[job_id] = record
            self._persist_history()

        thread = Thread(target=self._run_job, args=(job_id,), daemon=True)
        self.threads[job_id] = thread
        thread.start()
        return self.get(job_id, include_text=True)  # type: ignore[return-value]

    def _append_log(self, record: JobRecord, line: str) -> None:
        log_path = Path(record.log_path)
        with log_path.open("a", encoding="utf-8") as file_handle:
            file_handle.write(line + "\n")

    def _read_text(self, path: str | None) -> str:
        if not path:
            return ""
        file_path = Path(path)
        if not file_path.exists():
            return ""
        return file_path.read_text(encoding="utf-8", errors="ignore")

    def _normalize_export_url(self, value: str, base_url: str | None = None) -> str | None:
        raw_value = str(value or "").strip()
        if not raw_value or raw_value.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            return None
        resolved = urllib.parse.urljoin(base_url or "", raw_value)
        parsed = urllib.parse.urlparse(resolved)
        if parsed.scheme not in {"http", "https"}:
            return None
        return urllib.parse.urlunparse(parsed._replace(fragment=""))

    def _extract_urls_from_soup(self, soup: BeautifulSoup, base_url: str | None = None) -> set[str]:
        urls: set[str] = set()
        for attr in URL_ATTRS:
            for tag in soup.find_all(attrs={attr: True}):
                value = self._normalize_export_url(str(tag.get(attr, "")).strip(), base_url)
                if value:
                    urls.add(value)
        for attr in SRCSET_ATTRS:
            for tag in soup.find_all(attrs={attr: True}):
                raw_value = str(tag.get(attr, "")).strip()
                if not raw_value:
                    continue
                for item in raw_value.split(","):
                    candidate = item.strip().split(" ", 1)[0].strip()
                    candidate = self._normalize_export_url(candidate, base_url)
                    if candidate:
                        urls.add(candidate)
        return urls

    def _collect_found_links(self, record: JobRecord) -> list[str]:
        ordered_links: list[str] = []
        seen: set[str] = set()

        def push(url: str | None) -> None:
            normalized = self._normalize_export_url(url or "")
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            ordered_links.append(normalized)

        for url in [record.requested_url, record.final_url, *record.saved_pages.keys()]:
            push(url)

        if not record.output_dir:
            return ordered_links

        output_root = Path(record.output_dir)
        if not output_root.exists():
            return ordered_links

        for page_url, file_name in sorted(record.saved_pages.items()):
            html_path = output_root / file_name
            if not html_path.exists():
                continue
            html_text = html_path.read_text(encoding="utf-8", errors="ignore")
            soup = BeautifulSoup(html_text, "html.parser")
            for extracted_url in sorted(self._extract_urls_from_soup(soup, page_url)):
                push(extracted_url)

        return ordered_links

    def _collect_clone_stats(self, record: JobRecord) -> dict[str, Any]:
        if not record.output_dir:
            return {}
        output_root = Path(record.output_dir)
        if not output_root.exists():
            return {}

        page_entries: list[tuple[str, str]] = []
        seen_files: set[str] = set()
        for page_url, file_name in record.saved_pages.items():
            if not file_name or file_name in seen_files:
                continue
            seen_files.add(file_name)
            page_entries.append((page_url, file_name))
        if not page_entries:
            return {}

        stats = {
            "pages": record.saved_pages_count or len(page_entries),
            "assets": record.downloaded_resources_count,
            "html_lines": 0,
            "buttons": 0,
            "anchors": 0,
            "forms": 0,
            "images": 0,
            "urls": 0,
        }
        unique_urls = {url for url in record.saved_pages.keys() if url}
        if record.requested_url:
            unique_urls.add(record.requested_url)
        if record.final_url:
            unique_urls.add(record.final_url)

        for page_url, file_name in page_entries:
            html_path = output_root / file_name
            if not html_path.exists():
                continue
            html_text = html_path.read_text(encoding="utf-8", errors="ignore")
            stats["html_lines"] += len(html_text.splitlines())
            soup = BeautifulSoup(html_text, "html.parser")
            stats["buttons"] += len(soup.find_all("button"))
            stats["anchors"] += len(soup.find_all("a"))
            stats["forms"] += len(soup.find_all("form"))
            stats["images"] += len(soup.find_all("img"))
            unique_urls.update(self._extract_urls_from_soup(soup, page_url))

        stats["urls"] = len(unique_urls)
        return stats

    def _build_log_summary(self, record: JobRecord) -> str:
        stats = record.clone_stats or {}
        if not stats:
            return ""
        lines = [
            "",
            "[summary] Mini clone summary",
            f"[summary] pages: {stats.get('pages', 0)}",
            f"[summary] assets: {stats.get('assets', 0)}",
            f"[summary] html lines: {stats.get('html_lines', 0)}",
            f"[summary] buttons: {stats.get('buttons', 0)}",
            f"[summary] anchors: {stats.get('anchors', 0)}",
            f"[summary] forms: {stats.get('forms', 0)}",
            f"[summary] images: {stats.get('images', 0)}",
            f"[summary] urls: {stats.get('urls', 0)}",
        ]
        return "\n".join(lines)

    def _serialize(self, record: JobRecord, include_text: bool) -> dict[str, Any]:
        if record.status in {"done", "failed"} and not record.clone_stats:
            record.clone_stats = self._collect_clone_stats(record)
        payload = record.to_dict()
        payload["output_exists"] = bool(record.output_dir and Path(record.output_dir).exists())
        if include_text:
            logs_text = self._read_text(record.log_path)
            summary_text = self._build_log_summary(record)
            if summary_text and "[summary] Mini clone summary" not in logs_text:
                logs_text = (logs_text.rstrip() + "\n" + summary_text).strip()
            payload["logs_text"] = logs_text
            payload["report_text"] = self._read_text(record.report_path)
        return payload

    def _derive_error_from_logs(self, record: JobRecord) -> str | None:
        logs_text = self._read_text(record.log_path)
        for line in reversed(logs_text.splitlines()):
            cleaned = line.strip()
            if not cleaned or cleaned.startswith("[summary]"):
                continue
            if cleaned.lower().startswith("traceback"):
                continue
            return cleaned
        return None

    def _run_job(self, job_id: str) -> None:
        with self.lock:
            record = self.jobs[job_id]
            record.status = "running"
            record.started_at = self._now()
            self._persist_history()

        command = [
            sys.executable,
            "-u",
            str(engine_script_path()),
            record.requested_url,
            "--mode",
            "site" if record.mode == "crawl" else record.mode,
            "--output-dir",
            record.output_dir or "",
            "--report-file",
            record.report_path or "",
            "--json-file",
            record.result_path or "",
        ]

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(engine_script_path().parent),
        )

        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip("\n")
            with self.lock:
                record = self.jobs[job_id]
                self._append_log(record, line)

        return_code = process.wait()

        with self.lock:
            record = self.jobs[job_id]
            record.return_code = return_code
            summary = {}
            result_path = Path(record.result_path or "")
            if result_path.exists():
                try:
                    summary = json.loads(result_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    summary = {}
            record.final_url = summary.get("final_url") or record.final_url
            record.output_dir = summary.get("output_dir") or record.output_dir
            record.report_path = summary.get("report_path") or record.report_path
            record.entry_file = summary.get("entry_file") or record.entry_file
            record.entry_path = summary.get("entry_path") or record.entry_path
            record.saved_pages = summary.get("saved_pages") or record.saved_pages
            record.saved_pages_count = summary.get("saved_pages_count", record.saved_pages_count)
            record.downloaded_resources_count = summary.get(
                "downloaded_resources_count",
                record.downloaded_resources_count,
            )
            record.clone_stats = summary.get("clone_stats") or self._collect_clone_stats(record)
            record.status = "done" if return_code == 0 else "failed"
            record.finished_at = self._now()
            record.error = None if return_code == 0 else summary.get("error") or self._derive_error_from_logs(record)
            self._persist_history()

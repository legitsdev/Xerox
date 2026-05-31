#!/usr/bin/env python3
"""
Абсолютный клонер сайта (Playwright Edition)
Сохраняет статический снимок страницы или набора страниц вместе с локальными CSS/JS/шрифтами/изображениями.
"""

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
from collections import deque
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Comment
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
ACCEPT_LANGUAGE = "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
OUT_DIR = "cloned_site"
ASSETS_SUBDIR = "assets"
ASSETS_DIR = os.path.join(OUT_DIR, ASSETS_SUBDIR)
REPORT_PATH = "site_report.txt"
MAX_ASSET_BYTES = 15 * 1024 * 1024
DOWNLOAD_BUDGET_SECONDS = 60
RECURSIVE_JS_BUDGET_SECONDS = 45
CHALLENGE_BUDGET_SECONDS = 18
MAX_ASSET_COUNT = 240
MAX_PAGE_COUNT = 80
MAX_BUTTON_PROBES = 16
CRAWL_BUDGET_SECONDS = 180
SCROLL_PAUSE_MS = 400

ASSET_EXTENSIONS = {
    ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".avif", ".apng", ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp4", ".webm",
    ".mp3", ".wav", ".m4a", ".mov", ".json", ".webmanifest", ".map"
}
URL_LIKE_ATTRS = (
    "src", "href", "data-src", "data-lazy-src", "data-original", "data-url",
    "data-href", "poster"
)
SRCSET_ATTRS = ("srcset", "imagesrcset")
PAGE_URL_ATTRS = ("href", "action", "data-href", "data-url")
TRACKING_HINTS = (
    "analytics", "tracking", "track", "telemetry", "metrics", "beacon",
    "clarity", "doubleclick", "googletagmanager", "google-analytics", "segment",
    "hotjar", "intercom", "privacycompliance", "sentry"
)
SOCIAL_IMAGE_META_KEYS = {
    "og:image", "og:image:url", "twitter:image", "twitter:image:src", "msapplication-tileimage"
}
EXTENSION_BY_CONTENT_TYPE = {
    "application/javascript": ".js",
    "application/json": ".json",
    "application/manifest+json": ".webmanifest",
    "application/octet-stream": ".bin",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
    "font/otf": ".otf",
    "font/ttf": ".ttf",
    "font/woff": ".woff",
    "font/woff2": ".woff2",
    "image/avif": ".avif",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
    "text/css": ".css",
    "text/javascript": ".js",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}
JS_NUMERIC_LITERAL_RE = r"\d+(?:e[+-]?\d+)?"
JS_EMBEDDED_ASSET_EXT_RE = (
    r"(?:m?js|css|png|jpe?g|gif|svg|webp|avif|apng|ico|woff2?|ttf|eot|otf|"
    r"mp4|webm|mp3|wav|m4a|mov|json|webmanifest|map)"
)
LOCAL_HTML_TRACE_PATTERNS = (
    (re.compile(r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?"), ""),
    (re.compile(r"\b(?:127\.0\.0\.1|localhost)(?::\d+)?\b"), ""),
    (re.compile(r"file:///[^\"'<>\\s]+"), ""),
    (re.compile(r"/Users/[^\"'<>\\s]+"), ""),
    (re.compile(r"[A-Za-z]:\\\\[^\"'<>\\s]+"), ""),
)
ANTI_BOT_MARKERS = (
    'window["bobcmn"]',
    "failureconfig",
    "/tspd/",
    "serviceName:'waf',code:'user_blocked'",
    'serviceName:"waf",code:"user_blocked"',
    "support id",
    "__cf_chl",
    "cf-browser-verification",
)
INLINE_NAVIGATION_PATTERNS = (
    re.compile(r"""(?:window\.)?location(?:\.href)?\s*=\s*["']([^"']+)""", re.I),
    re.compile(r"""(?:window\.)?open\(\s*["']([^"']+)""", re.I),
    re.compile(r"""\b(?:router|Router)\.(?:push|replace)\(\s*["']([^"']+)""", re.I),
    re.compile(r"""\bnavigate\(\s*["']([^"']+)""", re.I),
)
BUTTON_NAV_SELECTOR = "button, [role='button'], [role='link'], [onclick], [data-href], [data-url]"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": STEALTH_USER_AGENT, "Accept-Language": ACCEPT_LANGUAGE})


def parse_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def reset_output_dir():
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    os.makedirs(ASSETS_DIR, exist_ok=True)


def configure_runtime_paths(output_dir=None, report_path=None):
    global OUT_DIR, ASSETS_DIR, REPORT_PATH

    if output_dir:
        OUT_DIR = output_dir
        ASSETS_DIR = os.path.join(OUT_DIR, ASSETS_SUBDIR)
    if report_path:
        REPORT_PATH = report_path


def reset_http_session():
    SESSION.cookies.clear()


def is_skippable_url(url):
    if not url:
        return True
    lowered = url.strip().lower()
    return lowered.startswith(("data:", "blob:", "javascript:", "mailto:", "tel:", "#"))


def normalize_url(url, base_url):
    if is_skippable_url(url):
        return None
    return urllib.parse.urljoin(base_url, url)


def canonicalize_url(url):
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


def is_loopback_host(url):
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return False
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_url_with_path(base_url, path):
    parsed = urllib.parse.urlparse(base_url)
    return urllib.parse.urlunparse(parsed._replace(path=path, params="", query="", fragment=""))


def resolve_asset_candidates(raw_url, base_url, fallback_base_url=None, js_mode=False):
    if is_skippable_url(raw_url):
        return []

    candidates = []
    seen = set()

    def push(candidate_url):
        if not candidate_url or candidate_url in seen:
            return
        seen.add(candidate_url)
        candidates.append(candidate_url)

    if raw_url.startswith(("http://", "https://")):
        push(raw_url)
    else:
        primary_url = resolve_js_dependency_url(raw_url, base_url) if js_mode else normalize_url(raw_url, base_url)
        push(primary_url)

    if fallback_base_url:
        if raw_url.startswith("/"):
            push(normalize_url(raw_url, fallback_base_url))
        elif raw_url.startswith(("static/", "_next/", "assets/", "fonts/", "img/", "images/", "media/")):
            push(build_url_with_path(fallback_base_url, "/" + raw_url.lstrip("/")))

    return candidates


def find_downloaded_asset_url(raw_url, base_url, downloaded, fallback_base_url=None, js_mode=False):
    for candidate_url in resolve_asset_candidates(
        raw_url,
        base_url,
        fallback_base_url=fallback_base_url,
        js_mode=js_mode,
    ):
        if candidate_url in downloaded:
            return candidate_url
    return None


def download_first_available_asset(
    raw_url,
    base_url,
    downloaded,
    asset_metadata,
    fallback_base_url=None,
    js_mode=False,
    referer="",
):
    for candidate_url in resolve_asset_candidates(
        raw_url,
        base_url,
        fallback_base_url=fallback_base_url,
        js_mode=js_mode,
    ):
        metadata = dict(asset_metadata.get(candidate_url, {}))
        if referer and not metadata.get("referer"):
            metadata["referer"] = referer
        if metadata:
            asset_metadata[candidate_url] = metadata
        local_path = download_file(candidate_url, downloaded, asset_metadata)
        if local_path:
            return candidate_url, local_path
    return None, None


def local_asset_ref(from_local_path, to_local_path):
    from_dir = os.path.dirname(from_local_path) or "."
    rel_path = os.path.relpath(to_local_path, start=from_dir)
    return rel_path.replace(os.sep, "/")


def explicit_relative_ref(path):
    if not path or path.startswith(("/", "./", "../", "#", "?")):
        return path
    return f"./{path}"


def is_same_origin_url(url, page_host):
    return urllib.parse.urlparse(url).netloc.lower() == page_host


def has_known_extension(url):
    path = urllib.parse.urlparse(url).path.lower()
    _, ext = os.path.splitext(path)
    return ext in ASSET_EXTENSIONS


def is_probable_page_url(url):
    parsed = urllib.parse.urlparse(url)
    path = (parsed.path or "/").lower()
    _, ext = os.path.splitext(path)
    if ext and ext not in {".html", ".htm"} and ext in ASSET_EXTENSIONS:
        return False
    if path == "/api" or path.startswith("/api/"):
        return False
    if path.startswith("/cdn-cgi/") or path.startswith("/graphql"):
        return False
    if "/feed/" in path or path.endswith("/atom") or path.endswith("/rss") or path.endswith(".xml"):
        return False
    return True


def normalize_page_url(url, base_url, page_host):
    abs_url = normalize_url(url, base_url)
    if not abs_url:
        return None
    abs_url = canonicalize_url(abs_url)
    if not is_same_origin_url(abs_url, page_host):
        return None
    if not is_probable_page_url(abs_url):
        return None
    return abs_url


def guess_extension(url, content_type=""):
    path = urllib.parse.urlparse(url).path
    _, ext = os.path.splitext(path)
    if ext and len(ext) <= 10:
        return ext
    return EXTENSION_BY_CONTENT_TYPE.get(content_type, ".bin")


def decode_js_numeric_literal(literal):
    try:
        return str(int(literal))
    except ValueError:
        return str(int(float(literal)))


def alias_paths_for_url(url):
    parsed = urllib.parse.urlparse(url)
    path = parsed.path or ""
    aliases = set()

    normalized_path = path.lstrip("/")
    if normalized_path and "." in Path(normalized_path).name:
        aliases.add(normalized_path)

    if "/_next/" in path:
        next_suffix = path[path.index("/_next/") :]
        aliases.add(next_suffix.lstrip("/"))
        if next_suffix.startswith("/_next/static/"):
            aliases.add(next_suffix[len("/_next/") :].lstrip("/"))

    if "/static/" in path:
        static_suffix = path[path.index("/static/") :]
        aliases.add(static_suffix.lstrip("/"))

    return {alias for alias in aliases if alias and "." in Path(alias).name}


def write_alias_files(url, source_file_path, overwrite=False):
    for alias in alias_paths_for_url(url):
        alias_path = os.path.join(OUT_DIR, alias)
        alias_dir = os.path.dirname(alias_path)
        if alias_dir:
            os.makedirs(alias_dir, exist_ok=True)
        if overwrite or not os.path.exists(alias_path):
            shutil.copy2(source_file_path, alias_path)


def local_path_candidates_for_url(url, downloaded):
    candidates = []
    if url in downloaded:
        candidates.append(downloaded[url])
    candidates.extend(sorted(alias_paths_for_url(url)))
    seen = set()
    ordered = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(candidate)
    return ordered


def alias_file_paths_for_local_path(local_path, downloaded):
    aliases = set()
    for url, mapped_local_path in downloaded.items():
        if mapped_local_path == local_path:
            aliases.update(alias_paths_for_url(url))
    return sorted(aliases)


def local_ref_for_url(url, downloaded, current_local_path=None, prefer_alias=False):
    candidates = local_path_candidates_for_url(url, downloaded)
    if not candidates:
        return None

    if prefer_alias:
        alias_candidates = [candidate for candidate in candidates if candidate != downloaded.get(url)]
        if alias_candidates:
            candidates = alias_candidates

    target_path = candidates[0]
    if current_local_path:
        return local_asset_ref(current_local_path, target_path)
    return target_path.replace(os.sep, "/")


EMBEDDED_ASSET_URL_RE = re.compile(
    r'(?P<quote>["\'])(?P<url>(?:https?://|/|\./|\.\./|static/|_next/|assets/)[^"\']+\.[A-Za-z0-9]{1,10}(?:\?[^"\']*)?)(?P=quote)'
)


def rewrite_embedded_asset_urls(
    text,
    base_url,
    downloaded,
    current_local_path=None,
    prefer_alias=False,
    fallback_base_url=None,
    js_mode=False,
):
    rewritten = text

    for original_url, target_local_path in downloaded.items():
        if original_url in rewritten:
            local_ref = local_ref_for_url(
                original_url,
                downloaded,
                current_local_path=current_local_path,
                prefer_alias=prefer_alias,
            )
            local_ref = explicit_relative_ref(local_ref)
            rewritten = rewritten.replace(original_url, local_ref or target_local_path)

    def replace_match(match):
        raw_url = match.group("url")
        resolved_url = find_downloaded_asset_url(
            raw_url,
            base_url,
            downloaded,
            fallback_base_url=fallback_base_url,
            js_mode=js_mode,
        )
        if not resolved_url:
            return match.group(0)

        local_ref = local_ref_for_url(
            resolved_url,
            downloaded,
            current_local_path=current_local_path,
            prefer_alias=prefer_alias,
        )
        if not local_ref:
            return match.group(0)

        local_ref = explicit_relative_ref(local_ref)
        quote = match.group("quote")
        return f"{quote}{local_ref}{quote}"

    return EMBEDDED_ASSET_URL_RE.sub(replace_match, rewritten)


def is_tracking_asset(url, resource_type="", content_type="", content_length=0):
    parsed = urllib.parse.urlparse(url)
    haystack = f"{parsed.netloc}{parsed.path}?{parsed.query}".lower()
    if resource_type in {"beacon", "ping", "cspviolationreport"}:
        return True
    if (
        resource_type == "image"
        and has_known_extension(url)
        and not parsed.query
        and content_type != "image/gif"
    ):
        return False
    if any(hint in haystack for hint in TRACKING_HINTS):
        return True
    if content_type == "image/gif" and not has_known_extension(url) and parsed.query:
        return True
    if content_length and content_length <= 80 and content_type.startswith("image/"):
        return True
    return False


def is_hidden_or_tracking_iframe(tag, page_host):
    src = tag.get("src", "")
    abs_src = normalize_url(src, f"https://{page_host}") if src else None
    class_and_id = " ".join(
        str(value) for value in [tag.get("class", []), tag.get("id", ""), tag.get("name", "")]
    ).lower()
    width = parse_int(tag.get("width"))
    height = parse_int(tag.get("height"))

    if "thirdparty" in class_and_id or "privacy" in class_and_id or "cookie" in class_and_id:
        return True
    if width and height and width <= 5 and height <= 5:
        return True
    if abs_src and is_tracking_asset(abs_src):
        return True
    if abs_src:
        host = urllib.parse.urlparse(abs_src).netloc.lower()
        if host and host != page_host and not host.endswith("." + page_host):
            style = (tag.get("style") or "").lower()
            if "display:none" in style or "visibility:hidden" in style or "position:absolute" in style:
                return True
    return False


def safe_html_filename(url):
    parsed = urllib.parse.urlparse(url)
    path = parsed.path or "/"
    if path.endswith("/"):
        path = path + "index"
    path = path.strip("/") or "index"
    filename = re.sub(r"[^a-zA-Z0-9_.-]", "_", path.replace("/", "_")) or "index"
    if parsed.query:
        filename += "__q_" + hashlib.md5(parsed.query.encode("utf-8")).hexdigest()[:8]
    if not filename.endswith(".html"):
        filename += ".html"
    return filename


def collect_asset_from_response(response, collected_assets):
    url = response.url
    if is_skippable_url(url):
        return

    resource_type = response.request.resource_type
    content_type = (response.headers.get("content-type") or "").split(";")[0].lower()
    content_length = parse_int(response.headers.get("content-length"))

    if is_tracking_asset(url, resource_type, content_type, content_length):
        return

    should_collect = False
    if resource_type in {"stylesheet", "script", "font", "media"}:
        should_collect = True
    elif resource_type == "image":
        should_collect = has_known_extension(url) or (
            content_type.startswith("image/") and content_type != "image/gif" and content_length != 0
        )
    elif has_known_extension(url):
        should_collect = True

    if should_collect:
        collected_assets[url] = {
            "resource_type": resource_type,
            "content_type": content_type,
            "content_length": content_length,
            "referer": response.request.headers.get("referer", ""),
        }


def download_file(url, downloaded, asset_metadata):
    if url in downloaded:
        return downloaded[url]

    metadata = asset_metadata.get(url, {})
    if is_tracking_asset(
        url,
        metadata.get("resource_type", ""),
        metadata.get("content_type", ""),
        metadata.get("content_length", 0),
    ):
        return None

    try:
        headers = {}
        referer = metadata.get("referer", "")
        if referer:
            headers["Referer"] = referer
        response = SESSION.get(url, timeout=(5, 15), stream=True, headers=headers or None)
        response.raise_for_status()
    except requests.RequestException:
        return None

    final_url = response.url
    content_type = (response.headers.get("content-type") or metadata.get("content_type") or "").split(";")[0].lower()
    content_length = parse_int(response.headers.get("content-length"), metadata.get("content_length", 0))
    if content_length and content_length > MAX_ASSET_BYTES:
        response.close()
        return None

    ext = guess_extension(final_url, content_type)
    filename = hashlib.md5(final_url.encode("utf-8")).hexdigest()[:10] + ext
    file_path = os.path.join(ASSETS_DIR, filename)

    total_written = 0
    try:
        with open(file_path, "wb") as file_handle:
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total_written += len(chunk)
                if total_written > MAX_ASSET_BYTES:
                    raise ValueError("asset too large")
                file_handle.write(chunk)
    except (OSError, ValueError):
        response.close()
        try:
            os.remove(file_path)
        except OSError:
            pass
        return None
    finally:
        response.close()

    rel_path = os.path.join(ASSETS_SUBDIR, filename)
    downloaded[url] = rel_path
    downloaded[final_url] = rel_path
    write_alias_files(url, file_path)
    write_alias_files(final_url, file_path)
    asset_metadata[url] = {
        "resource_type": metadata.get("resource_type", ""),
        "content_type": content_type,
        "content_length": total_written,
        "referer": referer,
    }
    asset_metadata[final_url] = asset_metadata[url]
    return rel_path


def extract_css_urls(css_text):
    found_urls = []

    for match in re.finditer(r"url\(([^)]+)\)", css_text):
        url_value = match.group(1).strip().strip("'\"")
        if not is_skippable_url(url_value):
            found_urls.append(url_value)

    for match in re.finditer(r"@import\s+(?:url\()?[\"']?([^\"')\s]+)", css_text):
        url_value = match.group(1).strip().strip("'\"")
        if not is_skippable_url(url_value):
            found_urls.append(url_value)

    return found_urls


def rewrite_css_urls(css_text, base_url, downloaded, current_local_path=None, fallback_base_url=None):
    def replace_url(match):
        raw = match.group(1).strip().strip("'\"")
        resolved_url = find_downloaded_asset_url(
            raw,
            base_url,
            downloaded,
            fallback_base_url=fallback_base_url,
        )
        if resolved_url:
            rewritten = local_ref_for_url(resolved_url, downloaded, current_local_path=current_local_path)
            return f"url({rewritten})"
        return match.group(0)

    def replace_import(match):
        raw = match.group(1).strip().strip("'\"")
        resolved_url = find_downloaded_asset_url(
            raw,
            base_url,
            downloaded,
            fallback_base_url=fallback_base_url,
        )
        if resolved_url:
            rewritten = local_ref_for_url(resolved_url, downloaded, current_local_path=current_local_path)
            return match.group(0).replace(raw, rewritten, 1)
        return match.group(0)

    css_text = re.sub(r"url\(([^)]+)\)", replace_url, css_text)
    css_text = re.sub(r"@import\s+(?:url\()?[\"']?([^\"')\s]+)", replace_import, css_text)
    return css_text


def refresh_alias_copies(local_path, downloaded):
    full_local_path = os.path.join(OUT_DIR, local_path)
    for alias_local_path in alias_file_paths_for_local_path(local_path, downloaded):
        alias_full_path = os.path.join(OUT_DIR, alias_local_path)
        alias_dir = os.path.dirname(alias_full_path)
        if alias_dir:
            os.makedirs(alias_dir, exist_ok=True)
        shutil.copy2(full_local_path, alias_full_path)


def write_alias_text_versions(local_path, downloaded, text_builder):
    for alias_local_path in alias_file_paths_for_local_path(local_path, downloaded):
        alias_full_path = os.path.join(OUT_DIR, alias_local_path)
        alias_dir = os.path.dirname(alias_full_path)
        if alias_dir:
            os.makedirs(alias_dir, exist_ok=True)
        with open(alias_full_path, "w", encoding="utf-8") as file_handle:
            file_handle.write(text_builder(alias_local_path))


def sync_session_from_context(context):
    for cookie in context.cookies():
        SESSION.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )


def rewrite_js_urls(js_text, base_url, downloaded, current_local_path, fallback_base_url=None):
    return rewrite_embedded_asset_urls(
        js_text,
        base_url,
        downloaded,
        current_local_path=current_local_path,
        prefer_alias=True,
        fallback_base_url=fallback_base_url,
        js_mode=True,
    )


def extract_js_asset_urls(js_text):
    patterns = [
        rf'["\'](\/_next\/static\/[^"\']+\.{JS_EMBEDDED_ASSET_EXT_RE})["\']',
        rf'["\'](\/static\/[^"\']+\.{JS_EMBEDDED_ASSET_EXT_RE})["\']',
        rf'["\'](static\/[^"\']+\.{JS_EMBEDDED_ASSET_EXT_RE})["\']',
        rf'["\']((?:\/_app\/immutable|_app\/immutable)\/[^"\']+\.{JS_EMBEDDED_ASSET_EXT_RE})["\']',
        rf'["\']((?:\.\.?\/)+(?:assets|chunks|nodes|entry|workers)\/[^"\']+\.{JS_EMBEDDED_ASSET_EXT_RE})["\']',
        rf'["\']((?:assets|chunks|nodes|entry|workers)\/[^"\']+\.{JS_EMBEDDED_ASSET_EXT_RE})["\']',
        rf'["\'](https?:\/\/[^"\']+\.{JS_EMBEDDED_ASSET_EXT_RE}(?:\?[^"\']*)?)["\']',
    ]
    found_urls = set()
    for pattern in patterns:
        for match in re.finditer(pattern, js_text):
            found_urls.add(match.group(1))
    return sorted(found_urls)


def extract_next_runtime_asset_maps(js_text):
    js_map = {}
    css_map = {}

    for match in re.finditer(r"\.u=function\((\w+)\)\{return (.*?)\},p\.", js_text, re.S):
        body = match.group(2)
        if "static/chunks/" not in body:
            continue

        for direct_match in re.finditer(r'["\'](static/chunks/[^"\']+\.js)["\']', body):
            rel_path = direct_match.group(1)
            id_match = re.search(r"static/chunks/(\d+)[.-]", rel_path)
            if id_match:
                js_map[id_match.group(1)] = rel_path

        for chunk_id, chunk_hash in re.findall(
            rf"({JS_NUMERIC_LITERAL_RE}):\"([0-9a-f]{{8,}})\"",
            body,
        ):
            decoded_id = decode_js_numeric_literal(chunk_id)
            js_map[decoded_id] = f"static/chunks/{decoded_id}.{chunk_hash}.js"

    for match in re.finditer(r"\.miniCssF=function\((\w+)\)\{return (.*?)\},p\.", js_text, re.S):
        body = match.group(2)
        if "static/css/" not in body:
            continue

        for direct_match in re.finditer(r'["\'](static/css/[^"\']+\.css)["\']', body):
            rel_path = direct_match.group(1)
            id_match = re.search(r"static/css/(\d+)[.-]", rel_path)
            if id_match:
                css_map[id_match.group(1)] = rel_path

        for chunk_id, chunk_hash in re.findall(
            rf"({JS_NUMERIC_LITERAL_RE}):\"([0-9a-f]{{8,}})\"",
            body,
        ):
            css_map[decode_js_numeric_literal(chunk_id)] = f"static/css/{chunk_hash}.css"

    return js_map, css_map


def extract_dynamic_chunk_ids(js_text):
    chunk_ids = set()
    for chunk_id in re.findall(rf"\.e\(({JS_NUMERIC_LITERAL_RE})\)", js_text):
        chunk_ids.add(decode_js_numeric_literal(chunk_id))
    return chunk_ids


def resolve_js_dependency_url(nested_url, base_url):
    if is_skippable_url(nested_url):
        return None

    if nested_url.startswith(("http://", "https://", "/")):
        return normalize_url(nested_url, base_url)

    parsed_base = urllib.parse.urlparse(base_url)
    base_path = parsed_base.path or ""

    if nested_url.startswith("static/"):
        if "/_next/" in base_path:
            prefix = base_path[: base_path.index("/_next/") + len("/_next/")]
            return build_url_with_path(base_url, urllib.parse.urljoin(prefix, nested_url))
        if "/static/" in base_path:
            prefix = base_path[: base_path.index("/static/") + 1]
            return build_url_with_path(base_url, urllib.parse.urljoin(prefix, nested_url))

    return normalize_url(nested_url, base_url)


def extract_inline_navigation_urls(script_text):
    found_urls = set()
    for pattern in INLINE_NAVIGATION_PATTERNS:
        for match in pattern.finditer(script_text or ""):
            if not is_skippable_url(match.group(1)):
                found_urls.add(match.group(1))
    return found_urls


def collect_attr_urls(tag, base_url, collected_urls):
    for attr_name in URL_LIKE_ATTRS:
        if tag.has_attr(attr_name):
            abs_url = normalize_url(tag[attr_name], base_url)
            if abs_url:
                collected_urls.add(abs_url)

    for attr_name in SRCSET_ATTRS:
        if tag.has_attr(attr_name):
            for item in tag[attr_name].split(","):
                parts = item.strip().split()
                if not parts:
                    continue
                abs_url = normalize_url(parts[0], base_url)
                if abs_url:
                    collected_urls.add(abs_url)


def collect_dom_asset_urls(soup, base_url):
    collected_urls = set()

    for link in soup.find_all("link"):
        rel_values = {value.lower() for value in link.get("rel", [])}
        href = link.get("href")
        as_attr = (link.get("as") or "").lower()
        if href and (
            "stylesheet" in rel_values
            or "icon" in rel_values
            or "apple-touch-icon" in rel_values
            or "manifest" in rel_values
            or as_attr in {"style", "script", "font", "image"}
            or has_known_extension(urllib.parse.urljoin(base_url, href))
        ):
            abs_url = normalize_url(href, base_url)
            if abs_url:
                collected_urls.add(abs_url)
        for attr_name in SRCSET_ATTRS:
            if link.has_attr(attr_name):
                for item in link[attr_name].split(","):
                    parts = item.strip().split()
                    if not parts:
                        continue
                    abs_url = normalize_url(parts[0], base_url)
                    if abs_url:
                        collected_urls.add(abs_url)

    for tag in soup.find_all(["script", "img", "source", "video", "audio", "embed"]):
        collect_attr_urls(tag, base_url, collected_urls)

    for meta in soup.find_all("meta"):
        key = (meta.get("property") or meta.get("name") or "").lower()
        if key in SOCIAL_IMAGE_META_KEYS:
            abs_url = normalize_url(meta.get("content"), base_url)
            if abs_url:
                collected_urls.add(abs_url)

    for style in soup.find_all("style"):
        css_text = style.get_text()
        if css_text:
            for css_url in extract_css_urls(css_text):
                abs_url = normalize_url(css_url, base_url)
                if abs_url:
                    collected_urls.add(abs_url)

    for tag in soup.find_all(style=True):
        for css_url in extract_css_urls(tag["style"]):
            abs_url = normalize_url(css_url, base_url)
            if abs_url:
                collected_urls.add(abs_url)

    return collected_urls


def collect_dom_page_urls(soup, base_url, page_host):
    collected_urls = set()

    for tag in soup.find_all(["a", "area", "form"]):
        for attr_name in ("href", "action"):
            if tag.has_attr(attr_name):
                abs_url = normalize_page_url(tag[attr_name], base_url, page_host)
                if abs_url:
                    collected_urls.add(abs_url)

    for tag in soup.find_all(True):
        for attr_name in ("data-href", "data-url"):
            if tag.has_attr(attr_name):
                abs_url = normalize_page_url(tag[attr_name], base_url, page_host)
                if abs_url:
                    collected_urls.add(abs_url)

        onclick_text = tag.get("onclick")
        if onclick_text:
            for nav_url in extract_inline_navigation_urls(onclick_text):
                abs_url = normalize_page_url(nav_url, base_url, page_host)
                if abs_url:
                    collected_urls.add(abs_url)

    return collected_urls


def prioritize_asset_urls(dom_asset_urls, collected_assets):
    resource_rank = {
        "stylesheet": 0,
        "font": 1,
        "image": 2,
        "script": 3,
        "media": 4,
    }
    all_urls = set(dom_asset_urls) | set(collected_assets.keys())

    def sort_key(url):
        metadata = collected_assets.get(url, {})
        return (
            0 if url in dom_asset_urls else 1,
            resource_rank.get(metadata.get("resource_type", ""), 5),
            0 if has_known_extension(url) else 1,
            url,
        )

    return sorted(all_urls, key=sort_key)


def html_looks_like_bot_gate(html):
    if not html:
        return False
    lowered = html.lower()
    return any(marker.lower() in lowered for marker in ANTI_BOT_MARKERS)


def wait_for_bot_gate_resolution(page, start_url):
    started_at = time.time()
    start_url = canonicalize_url(start_url)

    while time.time() - started_at < CHALLENGE_BUDGET_SECONDS:
        current_url = canonicalize_url(page.url)
        try:
            html = page.content()
        except PlaywrightError:
            break

        if not html_looks_like_bot_gate(html):
            if current_url != start_url or len(html) > 5000:
                break
            if page.title():
                break

        page.wait_for_timeout(1000)

    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except PlaywrightTimeoutError:
        pass


def settle_page(page, enable_hover=True):
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass

    page.wait_for_timeout(1200)
    wait_for_bot_gate_resolution(page, page.url)
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(SCROLL_PAUSE_MS)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(SCROLL_PAUSE_MS)
    except PlaywrightError:
        pass

    if enable_hover:
        hover_targets = page.query_selector_all("[data-tooltip], [data-toggle='tooltip'], [data-bs-toggle='tooltip'], [title]")
        for element in hover_targets[:6]:
            try:
                element.hover(timeout=400)
                page.wait_for_timeout(80)
            except PlaywrightTimeoutError:
                pass

    for _ in range(3):
        try:
            page.keyboard.press("Escape")
        except PlaywrightError:
            break

    try:
        page.mouse.move(1, 1)
    except PlaywrightError:
        pass
    page.wait_for_timeout(500)


def download_assets_for_page(dom_asset_urls, downloaded, collected_assets):
    download_started = time.time()
    for asset_url in prioritize_asset_urls(dom_asset_urls, collected_assets)[:MAX_ASSET_COUNT]:
        if time.time() - download_started > DOWNLOAD_BUDGET_SECONDS:
            break
        download_file(asset_url, downloaded, collected_assets)


def localize_css_dependencies(downloaded, asset_metadata, site_url=None):
    pending_css_urls = [url for url, local_path in downloaded.items() if local_path.endswith(".css")]
    processed_css_urls = set()

    while pending_css_urls:
        css_url = pending_css_urls.pop(0)
        if css_url in processed_css_urls:
            continue

        local_path = downloaded.get(css_url)
        if not local_path:
            continue

        full_local_path = os.path.join(OUT_DIR, local_path)
        if not os.path.exists(full_local_path):
            continue

        with open(full_local_path, "r", encoding="utf-8", errors="ignore") as file_handle:
            css_text = file_handle.read()

        for nested_url in extract_css_urls(css_text):
            downloaded_url, nested_local_path = download_first_available_asset(
                nested_url,
                css_url,
                downloaded,
                asset_metadata,
                fallback_base_url=site_url,
                referer=css_url,
            )
            if downloaded_url and nested_local_path and nested_local_path.endswith(".css"):
                pending_css_urls.append(downloaded_url)

        rewritten_css = rewrite_css_urls(
            css_text,
            css_url,
            downloaded,
            current_local_path=local_path,
            fallback_base_url=site_url,
        )
        if rewritten_css != css_text:
            with open(full_local_path, "w", encoding="utf-8") as file_handle:
                file_handle.write(rewritten_css)
            write_alias_text_versions(
                local_path,
                downloaded,
                lambda alias_local_path: rewrite_css_urls(
                    css_text,
                    css_url,
                    downloaded,
                    current_local_path=alias_local_path,
                    fallback_base_url=site_url,
                ),
            )
        else:
            refresh_alias_copies(local_path, downloaded)

        processed_css_urls.add(css_url)


def localize_js_dependencies(downloaded, asset_metadata, site_url=None):
    def js_processing_priority(url):
        lowered = url.lower()
        if "webpack" in lowered or "runtime" in lowered:
            return (0, lowered)
        if "/pages/" in lowered or "main-" in lowered or "framework" in lowered:
            return (1, lowered)
        return (2, lowered)

    pending_js_urls = sorted(
        {url for url, local_path in downloaded.items() if local_path.endswith(".js")},
        key=js_processing_priority,
    )
    processed_js_urls = set()
    runtime_js_map = {}
    runtime_css_map = {}
    runtime_js_base_url = None
    runtime_css_base_url = None
    pending_chunk_ids = set()
    started_at = time.time()

    while pending_js_urls:
        if time.time() - started_at > RECURSIVE_JS_BUDGET_SECONDS:
            break

        js_url = pending_js_urls.pop(0)
        if js_url in processed_js_urls:
            continue

        local_path = downloaded.get(js_url)
        if not local_path:
            continue

        full_local_path = os.path.join(OUT_DIR, local_path)
        if not os.path.exists(full_local_path):
            continue

        with open(full_local_path, "r", encoding="utf-8", errors="ignore") as file_handle:
            js_text = file_handle.read()

        js_map, css_map = extract_next_runtime_asset_maps(js_text)
        if js_map:
            runtime_js_map.update(js_map)
            runtime_js_base_url = js_url
        if css_map:
            runtime_css_map.update(css_map)
            runtime_css_base_url = js_url

        pending_chunk_ids.update(extract_dynamic_chunk_ids(js_text))

        candidate_urls = set(extract_js_asset_urls(js_text))
        resolved_chunk_ids = set()
        for chunk_id in sorted(pending_chunk_ids):
            if runtime_js_base_url and chunk_id in runtime_js_map:
                candidate_urls.add(resolve_js_dependency_url(runtime_js_map[chunk_id], runtime_js_base_url))
                resolved_chunk_ids.add(chunk_id)
            if runtime_css_base_url and chunk_id in runtime_css_map:
                candidate_urls.add(resolve_js_dependency_url(runtime_css_map[chunk_id], runtime_css_base_url))
                resolved_chunk_ids.add(chunk_id)
        pending_chunk_ids.difference_update(resolved_chunk_ids)

        for nested_url in sorted(candidate_urls):
            downloaded_url, nested_local_path = download_first_available_asset(
                nested_url,
                js_url,
                downloaded,
                asset_metadata,
                fallback_base_url=site_url,
                js_mode=True,
                referer=js_url,
            )
            if downloaded_url and nested_local_path and nested_local_path.endswith(".js"):
                pending_js_urls.append(downloaded_url)

        rewritten_js = rewrite_js_urls(
            js_text,
            js_url,
            downloaded,
            current_local_path=local_path,
            fallback_base_url=site_url,
        )
        if rewritten_js != js_text:
            with open(full_local_path, "w", encoding="utf-8") as file_handle:
                file_handle.write(rewritten_js)
            write_alias_text_versions(
                local_path,
                downloaded,
                lambda alias_local_path: rewrite_js_urls(
                    js_text,
                    js_url,
                    downloaded,
                    current_local_path=alias_local_path,
                    fallback_base_url=site_url,
                ),
            )
        else:
            refresh_alias_copies(local_path, downloaded)

        processed_js_urls.add(js_url)


def rewrite_srcset(value, base_url, downloaded):
    parts = []
    for item in value.split(","):
        tokens = item.strip().split()
        if not tokens:
            continue
        abs_url = normalize_url(tokens[0], base_url)
        if abs_url and abs_url in downloaded:
            local_ref = local_ref_for_url(abs_url, downloaded, prefer_alias=True)
            if local_ref:
                tokens[0] = local_ref
        parts.append(" ".join(tokens))
    return ", ".join(parts)


def is_framework_html_page(soup):
    if soup.find(id="__next"):
        return True
    for tag in soup.find_all(["script", "link"]):
        raw_value = tag.get("src") or tag.get("href") or ""
        if "_next/" in raw_value or 'data-precedence="next"' in str(tag):
            return True
    return False


def rewrite_html_page_refs(soup, base_url, saved_pages):
    if not saved_pages:
        return

    base_host = urllib.parse.urlparse(base_url).netloc.lower()

    for tag in soup.find_all(["a", "area", "form"]):
        for attr_name in ("href", "action"):
            if not tag.has_attr(attr_name):
                continue
            raw_value = tag[attr_name]
            if not isinstance(raw_value, str) or is_skippable_url(raw_value):
                continue

            abs_url = normalize_url(raw_value, base_url)
            if not abs_url:
                continue

            parsed = urllib.parse.urlparse(abs_url)
            canonical_url = canonicalize_url(abs_url)
            local_path = saved_pages.get(canonical_url)
            if local_path:
                tag[attr_name] = local_path + (f"#{parsed.fragment}" if parsed.fragment else "")
            elif parsed.netloc.lower() == base_host:
                tag[attr_name] = abs_url

    for tag in soup.find_all(True):
        for attr_name in ("data-href", "data-url"):
            if not tag.has_attr(attr_name):
                continue
            raw_value = tag[attr_name]
            if not isinstance(raw_value, str) or is_skippable_url(raw_value):
                continue

            abs_url = normalize_url(raw_value, base_url)
            if not abs_url:
                continue

            parsed = urllib.parse.urlparse(abs_url)
            canonical_url = canonicalize_url(abs_url)
            local_path = saved_pages.get(canonical_url)
            if local_path:
                tag[attr_name] = local_path + (f"#{parsed.fragment}" if parsed.fragment else "")
            elif parsed.netloc.lower() == base_host:
                tag[attr_name] = abs_url


def build_runtime_stabilizer_script(base_url, saved_pages):
    return ""


def rewrite_html_assets(soup, base_url, downloaded, page_host, saved_pages=None, current_page_local_path=None):
    preserve_framework_markup = is_framework_html_page(soup)
    runtime_stabilizer = build_runtime_stabilizer_script(base_url, saved_pages or {})
    if runtime_stabilizer:
        head = soup.head
        if head is None:
            head = soup.new_tag("head")
            if soup.html:
                soup.html.insert(0, head)
            else:
                soup.insert(0, head)
        runtime_script = soup.new_tag("script")
        runtime_script.string = runtime_stabilizer
        head.insert(0, runtime_script)

    for link in soup.find_all("link"):
        if preserve_framework_markup:
            continue
        href = link.get("href")
        abs_href = normalize_url(href, base_url) if href else None
        rel_values = {value.lower() for value in link.get("rel", [])}
        as_attr = (link.get("as") or "").lower()
        is_asset_link = bool(
            rel_values & {"stylesheet", "icon", "apple-touch-icon", "manifest"}
            or as_attr in {"style", "script", "font", "image"}
        )

        if rel_values & {"preconnect", "dns-prefetch"}:
            link.decompose()
            continue
        if abs_href and abs_href in downloaded:
            local_ref = local_ref_for_url(abs_href, downloaded, prefer_alias=True)
            if local_ref:
                link["href"] = local_ref
        elif is_asset_link and abs_href:
            link.decompose()
            continue
        elif rel_values & {"prefetch", "modulepreload"} and abs_href and abs_href not in downloaded:
            link.decompose()
            continue
        elif rel_values & {"prefetch"} and abs_href and is_tracking_asset(abs_href):
            link.decompose()
            continue

        for attr_name in SRCSET_ATTRS:
            if link.has_attr(attr_name):
                link[attr_name] = rewrite_srcset(link[attr_name], base_url, downloaded)

    for meta in soup.find_all("meta"):
        key = (meta.get("property") or meta.get("name") or "").lower()
        if key in SOCIAL_IMAGE_META_KEYS and meta.has_attr("content"):
            abs_url = normalize_url(meta["content"], base_url)
            if abs_url and abs_url in downloaded:
                local_ref = local_ref_for_url(abs_url, downloaded, prefer_alias=True)
                if local_ref:
                    meta["content"] = local_ref
            elif abs_url:
                meta.decompose()

    for style in soup.find_all("style"):
        css_text = style.get_text()
        if css_text:
            style.string = rewrite_css_urls(css_text, base_url, downloaded, fallback_base_url=base_url)

    for tag in soup.find_all(style=True):
        tag["style"] = rewrite_css_urls(tag["style"], base_url, downloaded, fallback_base_url=base_url)

    for tag in soup.find_all(["script", "img", "source", "video", "audio", "iframe", "embed"]):
        if preserve_framework_markup:
            if tag.name == "iframe" and is_hidden_or_tracking_iframe(tag, page_host):
                tag.decompose()
            elif tag.name == "script":
                continue
        for attr_name in URL_LIKE_ATTRS:
            if not tag.has_attr(attr_name):
                continue
            abs_url = normalize_url(tag[attr_name], base_url)
            if abs_url and abs_url in downloaded:
                local_ref = local_ref_for_url(abs_url, downloaded, prefer_alias=True)
                if local_ref:
                    tag[attr_name] = local_ref
            elif tag.name == "script" and abs_url and is_tracking_asset(abs_url):
                tag.decompose()
                break

        if tag.name == "iframe" and is_hidden_or_tracking_iframe(tag, page_host):
            tag.decompose()
            continue

        for attr_name in SRCSET_ATTRS:
            if tag.has_attr(attr_name):
                tag[attr_name] = rewrite_srcset(tag[attr_name], base_url, downloaded)

    # Inline runtime/config scripts often contain absolute asset URLs that should also be localized.
    if not preserve_framework_markup:
        for script in soup.find_all("script"):
            if script.get("src"):
                continue
            script_text = script.string or script.get_text()
            if not script_text:
                continue
            rewritten_text = rewrite_embedded_asset_urls(
                script_text,
                base_url,
                downloaded,
                current_local_path=current_page_local_path,
                prefer_alias=True,
                fallback_base_url=base_url,
            )
            rewritten_text = re.sub(r'"assetPrefix"\s*:\s*"https?://[^"]+"', '"assetPrefix":""', rewritten_text)
            if rewritten_text != script_text:
                script.string = rewritten_text

    # Page-route rewriting is deferred to the runtime stabilizer after hydration.


def sanitize_html_output(soup):
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    for meta in soup.find_all("meta"):
        http_equiv = (meta.get("http-equiv") or "").lower()
        if http_equiv in {"content-security-policy", "content-security-policy-report-only"}:
            meta.decompose()
            continue
        if (meta.get("name") or "").lower() == "generator":
            meta.decompose()


def serialize_clean_html(soup, preserve_local_refs=False):
    html = str(soup)
    if not preserve_local_refs:
        for pattern, replacement in LOCAL_HTML_TRACE_PATTERNS:
            html = pattern.sub(replacement, html)
    return html


def generate_report(url, downloaded, saved_pages, mode):
    report_lines = [
        "=== SITE CLONE REPORT (Playwright) ===",
        f"URL: {url}",
        f"Режим: {mode}",
        f"Дата: {time.ctime()}",
        f"Сохранено страниц: {len(saved_pages)}",
        f"Скачано ресурсов: {len(set(downloaded.values()))}",
        "",
        "--- Список сохранённых HTML-страниц ---",
    ]

    for page_url, local_path in sorted(saved_pages.items()):
        report_lines.append(f"  {page_url}  ->  {local_path}")

    report_lines.extend([
        "",
        "--- Список всех полученных файлов ---",
    ])

    seen_pairs = set()
    for original_url, local_path in sorted(downloaded.items()):
        pair = (original_url, local_path)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        report_lines.append(f"  {original_url}  ->  {local_path}")

    report_dir = os.path.dirname(REPORT_PATH)
    if report_dir:
        os.makedirs(report_dir, exist_ok=True)

    with open(REPORT_PATH, "w", encoding="utf-8") as file_handle:
        file_handle.write("\n".join(report_lines))

    return REPORT_PATH


def write_local_launchers(entry_file):
    if not entry_file:
        return {}

    launcher_py_name = "open_clone.py"
    launcher_sh_name = "open_clone.sh"
    launcher_command_name = "open_clone.command"
    launcher_bat_name = "open_clone.bat"
    help_name = "HOW_TO_OPEN.txt"

    launcher_py_path = os.path.join(OUT_DIR, launcher_py_name)
    launcher_sh_path = os.path.join(OUT_DIR, launcher_sh_name)
    launcher_command_path = os.path.join(OUT_DIR, launcher_command_name)
    launcher_bat_path = os.path.join(OUT_DIR, launcher_bat_name)
    help_path = os.path.join(OUT_DIR, help_name)

    launcher_py = f"""#!/usr/bin/env python3
from __future__ import annotations

import http.server
import socket
import socketserver
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRY_FILE = {entry_file!r}


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class CloneHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)


class ThreadingServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    port = find_free_port()
    url = f"http://127.0.0.1:{{port}}/{{ENTRY_FILE}}"
    print(f"Opening cloned site at {{url}}")
    webbrowser.open(url)
    with ThreadingServer(("127.0.0.1", port), CloneHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
"""

    launcher_sh = """#!/bin/sh
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
  exec python3 open_clone.py
fi
exec python open_clone.py
"""

    launcher_bat = """@echo off
cd /d "%~dp0"
py -3 open_clone.py 2>nul
if %errorlevel%==0 goto :eof
python open_clone.py
"""

    help_text = f"""Do not open {entry_file} with file:// in the browser.

Modern sites often use ES modules and runtime chunks. Browsers commonly block them on file:// origins.

Open the clone through a tiny local HTTP server instead:

macOS:
  double-click open_clone.command
  or run: python3 open_clone.py

Linux:
  run: python3 open_clone.py
  or: sh open_clone.sh

Windows:
  double-click open_clone.bat
  or run: py -3 open_clone.py
"""

    with open(launcher_py_path, "w", encoding="utf-8") as file_handle:
        file_handle.write(launcher_py)
    with open(launcher_sh_path, "w", encoding="utf-8") as file_handle:
        file_handle.write(launcher_sh)
    with open(launcher_command_path, "w", encoding="utf-8") as file_handle:
        file_handle.write(launcher_sh)
    with open(launcher_bat_path, "w", encoding="utf-8") as file_handle:
        file_handle.write(launcher_bat)
    with open(help_path, "w", encoding="utf-8") as file_handle:
        file_handle.write(help_text)

    for executable_path in [launcher_py_path, launcher_sh_path, launcher_command_path]:
        try:
            os.chmod(executable_path, 0o755)
        except OSError:
            pass

    return {
        "launcher_python": os.path.abspath(launcher_py_path),
        "launcher_shell": os.path.abspath(launcher_sh_path),
        "launcher_command": os.path.abspath(launcher_command_path),
        "launcher_bat": os.path.abspath(launcher_bat_path),
        "launcher_help": os.path.abspath(help_path),
    }


def build_result_summary(requested_url, final_url, mode, downloaded, saved_pages, report_path):
    entry_file = saved_pages.get(final_url) or next(iter(saved_pages.values()), None)
    launcher_files = write_local_launchers(entry_file)
    return {
        "requested_url": requested_url,
        "final_url": final_url,
        "mode": mode,
        "output_dir": os.path.abspath(OUT_DIR),
        "report_path": os.path.abspath(report_path),
        "entry_file": entry_file,
        "entry_path": os.path.abspath(os.path.join(OUT_DIR, entry_file)) if entry_file else None,
        "saved_pages": saved_pages,
        "saved_pages_count": len(saved_pages),
        "downloaded_resources_count": len(set(downloaded.values())),
        **launcher_files,
    }


def write_result_summary(result_path, summary):
    if not result_path:
        return
    result_dir = os.path.dirname(result_path)
    if result_dir:
        os.makedirs(result_dir, exist_ok=True)
    with open(result_path, "w", encoding="utf-8") as file_handle:
        json.dump(summary, file_handle, ensure_ascii=False, indent=2)


def collect_button_navigation_candidates(page):
    return page.evaluate(
        """
        () => {
          const selector = "button, [role='button'], [role='link'], [onclick], [data-href], [data-url]";
          const navHint = /(pricing|feature|product|solution|doc|about|contact|blog|career|download|get started|learn more|support|help|faq|team|company|privacy|terms|login|log in|sign in|register|sign up)/i;
          const skipHint = /(cookie|consent|accept|dismiss|close|expand|collapse|search|filter|sort|play|pause|mute|share|theme|language|menu)/i;
          const handlerHint = /(location|navigate|router|pushstate|replacestate|open\\()/i;
          const isVisible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width >= 8 && rect.height >= 8 && style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
          };

          document.querySelectorAll("[data-cloner-nav-id]").forEach((el) => el.removeAttribute("data-cloner-nav-id"));

          let index = 0;
          const candidates = [];
          for (const el of document.querySelectorAll(selector)) {
            if (!isVisible(el)) continue;
            if (el.matches("[disabled], [aria-disabled='true']")) continue;

            const text = [el.innerText, el.getAttribute("aria-label"), el.getAttribute("title")]
              .filter(Boolean)
              .join(" ")
              .replace(/\\s+/g, " ")
              .trim();
            const onclick = (el.getAttribute("onclick") || "").toLowerCase();
            const hrefish = el.getAttribute("href") || el.getAttribute("data-href") || el.getAttribute("data-url") || "";
            const role = (el.getAttribute("role") || "").toLowerCase();
            const type = (el.getAttribute("type") || "").toLowerCase();
            const inNav = Boolean(el.closest("nav, header, footer, [role='navigation']"));
            const navLike = Boolean(hrefish || role === "link" || handlerHint.test(onclick) || inNav || navHint.test(text));

            if (!navLike) continue;
            if (type === "submit" && !hrefish && !onclick) continue;
            if (skipHint.test(text) && !hrefish && !handlerHint.test(onclick)) continue;

            const id = String(index++);
            el.setAttribute("data-cloner-nav-id", id);
            candidates.push({ id, text: text.slice(0, 80), hrefish });
          }

          return candidates;
        }
        """
    )


def discover_button_navigation_urls(context, page_url, page_host, max_button_probes):
    if max_button_probes <= 0:
        return set()

    seed_page = context.new_page()
    try:
        seed_page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
        settle_page(seed_page, enable_hover=False)
        candidates = collect_button_navigation_candidates(seed_page)
    finally:
        seed_page.close()

    discovered_urls = set()
    for candidate in candidates[:max_button_probes]:
        probe_page = context.new_page()
        popup_page = None
        try:
            probe_page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
            settle_page(probe_page, enable_hover=False)
            collect_button_navigation_candidates(probe_page)

            locator = probe_page.locator(f'[data-cloner-nav-id="{candidate["id"]}"]')
            if locator.count() != 1:
                continue

            before_url = canonicalize_url(probe_page.url)
            existing_pages = list(context.pages)
            try:
                locator.click(timeout=2000)
            except PlaywrightError:
                continue

            try:
                probe_page.wait_for_load_state("domcontentloaded", timeout=5000)
            except PlaywrightTimeoutError:
                pass
            probe_page.wait_for_timeout(1200)

            new_pages = [page for page in context.pages if page not in existing_pages]
            if new_pages:
                popup_page = new_pages[-1]
                try:
                    popup_page.wait_for_load_state("domcontentloaded", timeout=5000)
                except PlaywrightTimeoutError:
                    pass
                popup_page.wait_for_timeout(1200)

            target_page = popup_page or probe_page
            after_url = canonicalize_url(target_page.url)
            if after_url != before_url:
                normalized_url = normalize_page_url(after_url, before_url, page_host)
                if normalized_url:
                    discovered_urls.add(normalized_url)
        finally:
            if popup_page is not None:
                try:
                    popup_page.close()
                except PlaywrightError:
                    pass
            probe_page.close()

    return discovered_urls


def capture_page_snapshot(page, url):
    main_response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
    settle_page(page)

    raw_html = None
    response_url = canonicalize_url(main_response.url) if main_response is not None else canonicalize_url(url)
    if main_response is not None:
        try:
            raw_html = main_response.text()
        except (PlaywrightTimeoutError, PlaywrightError):
            raw_html = None

    live_html = page.content()
    current_page_url = canonicalize_url(page.url)
    page_host = urllib.parse.urlparse(current_page_url).netloc.lower()
    use_raw_html = bool(raw_html) and current_page_url == response_url and not html_looks_like_bot_gate(raw_html)
    source_html = raw_html if use_raw_html else live_html
    soup = BeautifulSoup(source_html, "html.parser")
    live_soup = BeautifulSoup(live_html, "html.parser")
    dom_asset_urls = collect_dom_asset_urls(soup, current_page_url) | collect_dom_asset_urls(live_soup, current_page_url)
    dom_page_urls = collect_dom_page_urls(soup, current_page_url, page_host) | collect_dom_page_urls(live_soup, current_page_url, page_host)

    return {
        "page_host": page_host,
        "page_url": current_page_url,
        "soup": soup,
        "dom_asset_urls": dom_asset_urls,
        "dom_page_urls": dom_page_urls,
    }


def clone_site(url, mode="page", max_pages=MAX_PAGE_COUNT, button_probes=MAX_BUTTON_PROBES):
    requested_url = canonicalize_url(url)
    reset_output_dir()
    reset_http_session()
    downloaded = {}
    collected_assets = {}
    page_records = {}
    page_aliases = {}
    root_url = requested_url
    root_host = urllib.parse.urlparse(root_url).netloc.lower()
    pending_pages = deque([root_url])
    visited_pages = set()
    crawl_started = time.time()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=STEALTH_USER_AGENT,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": ACCEPT_LANGUAGE},
        )
        context.add_init_script(
            """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
Object.defineProperty(navigator, 'language', {get: () => 'ru-RU'});
Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {}, app: {} };
            """
        )
        context.on("response", lambda response: collect_asset_from_response(response, collected_assets))

        while pending_pages:
            if mode == "site":
                if len(page_records) >= max_pages:
                    break
                if time.time() - crawl_started > CRAWL_BUDGET_SECONDS:
                    break

            target_url = pending_pages.popleft()
            if target_url in visited_pages:
                continue

            print(f"[page] Загружаем {target_url}...", flush=True)
            page = context.new_page()
            try:
                page_data = capture_page_snapshot(page, target_url)
            finally:
                sync_session_from_context(context)
                page.close()

            current_page_url = page_data["page_url"]
            current_page_host = page_data["page_host"]
            if not page_records:
                root_host = current_page_host

            page_aliases[target_url] = current_page_url
            visited_pages.add(target_url)
            visited_pages.add(current_page_url)

            if current_page_url not in page_records:
                page_records[current_page_url] = page_data
                print(f"[assets] Собираем ресурсы для {current_page_url}", flush=True)
                download_assets_for_page(page_data["dom_asset_urls"], downloaded, collected_assets)

            if mode != "site":
                break

            discovered_pages = set(
                next_url for next_url in page_data["dom_page_urls"] if is_same_origin_url(next_url, root_host)
            )
            discovered_pages.update(
                discover_button_navigation_urls(context, current_page_url, root_host, button_probes)
            )

            for next_url in sorted(discovered_pages):
                if next_url not in visited_pages and next_url not in pending_pages:
                    pending_pages.append(next_url)

        browser.close()

    site_url = page_aliases.get(root_url, root_url)
    localize_css_dependencies(downloaded, collected_assets, site_url=site_url)
    print("[css] Локализация CSS зависимостей завершена", flush=True)
    localize_js_dependencies(downloaded, collected_assets, site_url=site_url)
    print("[js] Локализация JS зависимостей завершена", flush=True)

    saved_pages = {page_url: safe_html_filename(page_url) for page_url in page_records}
    for alias_url, target_page_url in page_aliases.items():
        if target_page_url in saved_pages:
            saved_pages[alias_url] = saved_pages[target_page_url]

    primary_saved_pages = {page_url: saved_pages[page_url] for page_url in page_records}
    for page_url, page_data in page_records.items():
        soup = page_data["soup"]
        rewrite_html_assets(
            soup,
            page_url,
            downloaded,
            page_data["page_host"],
            saved_pages,
            current_page_local_path=saved_pages[page_url],
        )
        sanitize_html_output(soup)

        html_path = os.path.join(OUT_DIR, saved_pages[page_url])
        with open(html_path, "w", encoding="utf-8") as file_handle:
            file_handle.write(serialize_clean_html(soup, preserve_local_refs=is_loopback_host(page_url)))
        print(f"[save] Сохранено {html_path}", flush=True)

    entry_url = page_aliases.get(root_url, root_url)
    report_path = generate_report(entry_url, downloaded, primary_saved_pages, mode)
    summary = build_result_summary(requested_url, entry_url, mode, downloaded, primary_saved_pages, report_path)
    print(f"Готово! Сайт сохранён в '{OUT_DIR}/', отчёт в '{report_path}'.")
    return summary


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Клонер страницы/сайта на Playwright.")
    parser.add_argument("url", help="Стартовый URL для клонирования.")
    parser.add_argument("--mode", choices=("page", "site"), default="page", help="page: одна страница, site: обход внутренних страниц.")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGE_COUNT, help="Максимум страниц для режима site.")
    parser.add_argument("--button-probes", type=int, default=MAX_BUTTON_PROBES, help="Сколько навигационных кнопок проверять на каждой странице в режиме site.")
    parser.add_argument("--output-dir", default=OUT_DIR, help="Куда сохранять локальную копию сайта.")
    parser.add_argument("--report-file", default=REPORT_PATH, help="Куда сохранять текстовый отчёт.")
    parser.add_argument("--json-file", default="", help="Куда сохранять JSON-сводку результата.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    configure_runtime_paths(output_dir=args.output_dir, report_path=args.report_file)
    summary = clone_site(args.url, mode=args.mode, max_pages=max(1, args.max_pages), button_probes=max(0, args.button_probes))
    write_result_summary(args.json_file, summary)

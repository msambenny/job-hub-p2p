"""Shared HTTP probing and page fetch for job URL validation and scraping."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

DEFAULT_TIMEOUT = 20
MAX_PROBE_BYTES = 256_000
RETRY_STATUS_CODES = {429, 503}
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 0.75

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

CONFIG_PATH = Path(__file__).resolve().parents[1] / "references" / "url_hosts.json"
HEAD_FALLBACK_STATUS_CODES = {400, 403, 405, 501}


def load_url_host_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {"hosts": {}, "shortener_hosts": [], "login_wall_snippets": []}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def normalize_host(netloc: str) -> str:
    host = netloc.lower().strip()
    if host.startswith("www."):
        return host[4:]
    return host


def host_profile(url: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_url_host_config()
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    hosts = config.get("hosts", {})
    if netloc in hosts:
        return hosts[netloc]
    bare = normalize_host(netloc)
    for key, profile in hosts.items():
        if normalize_host(key) == bare:
            return profile
    return {}


def prefer_get_for_url(url: str, config: dict[str, Any] | None = None) -> bool:
    return bool(host_profile(url, config).get("prefer_get"))


def redirect_chain(response: requests.Response) -> list[str]:
    chain = [item.url for item in response.history]
    if response.url and (not chain or chain[-1] != response.url):
        chain.append(response.url)
    return chain


def shortener_warnings(url: str, chain: list[str], config: dict[str, Any] | None = None) -> list[str]:
    config = config or load_url_host_config()
    shorteners = {normalize_host(host) for host in config.get("shortener_hosts", [])}
    warnings: list[str] = []
    start_host = normalize_host(urlparse(url).netloc)
    if start_host in shorteners:
        warnings.append(f"Apply link uses a shortener host ({urlparse(url).netloc}).")
    for hop in chain:
        hop_host = normalize_host(urlparse(hop).netloc)
        if hop_host in shorteners and hop_host != start_host:
            warnings.append(f"Redirect passed through shortener ({urlparse(hop).netloc}).")
    return warnings


def discourage_warnings(url: str, config: dict[str, Any] | None = None) -> list[str]:
    profile = host_profile(url, config)
    if profile.get("discourage"):
        reason = profile.get("reason") or f"Host {urlparse(url).netloc} is discouraged for scraping."
        return [reason]
    return []


def detect_login_wall(html: str, config: dict[str, Any] | None = None) -> str | None:
    config = config or load_url_host_config()
    lowered = html.lower()
    for snippet in config.get("login_wall_snippets", []):
        if snippet.lower() in lowered:
            return f"Page content looks like a login or access wall ({snippet!r})."
    if len(lowered.strip()) < 200 and "job" not in lowered and "career" not in lowered:
        return "Page body is very small and does not look like a job posting."
    return None


def parse_url_or_error(url: str) -> tuple[urlparse | None, str | None]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, f"Invalid URL: {url}"
    return parsed, None


def _request(
    method: str,
    url: str,
    *,
    timeout: int,
    stream: bool = False,
) -> requests.Response:
    last_exc: requests.RequestException | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.request(
                method,
                url,
                headers=DEFAULT_HEADERS,
                timeout=timeout,
                allow_redirects=True,
                stream=stream,
            )
            if response.status_code in RETRY_STATUS_CODES and attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                response.close()
                continue
            return response
        except requests.RequestException as exc:
            last_exc = exc
            if attempt >= MAX_RETRIES:
                raise
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    if last_exc:
        raise last_exc
    raise RuntimeError("request failed without exception")


def _read_limited_text(response: requests.Response, limit: int = MAX_PROBE_BYTES) -> str:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=16_384):
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if total >= limit:
            break
    response.close()
    raw = b"".join(chunks)
    encoding = response.encoding or response.apparent_encoding or "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def probe_url(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Check reachability with host-aware GET/HEAD strategy and content heuristics."""
    config = load_url_host_config()
    _, parse_error = parse_url_or_error(url)
    if parse_error:
        return {
            "url": url,
            "valid": False,
            "status_code": None,
            "final_url": None,
            "fetch_method": None,
            "redirect_chain": [],
            "warnings": [],
            "flags": [],
            "error": parse_error,
        }

    warnings = discourage_warnings(url, config) + []
    flags: list[str] = []
    if warnings:
        flags.append("discouraged_host")

    method = "GET" if prefer_get_for_url(url, config) else "HEAD"
    body = ""

    try:
        if method == "HEAD":
            head_response = _request("HEAD", url, timeout=timeout)
            status_code = head_response.status_code
            final_url = head_response.url
            chain = redirect_chain(head_response)
            head_response.close()
            if status_code in HEAD_FALLBACK_STATUS_CODES:
                method = "GET"
                get_response = _request("GET", url, timeout=timeout, stream=True)
                body = _read_limited_text(get_response)
                status_code = get_response.status_code
                final_url = get_response.url
                chain = redirect_chain(get_response)
        else:
            get_response = _request("GET", url, timeout=timeout, stream=True)
            body = _read_limited_text(get_response)
            status_code = get_response.status_code
            final_url = get_response.url
            chain = redirect_chain(get_response)
    except requests.RequestException as exc:
        return {
            "url": url,
            "valid": False,
            "status_code": None,
            "final_url": None,
            "fetch_method": method,
            "redirect_chain": [],
            "warnings": warnings,
            "flags": flags,
            "error": f"Failed to reach URL: {exc}",
        }

    warnings.extend(shortener_warnings(url, chain, config))

    if urlparse(url).netloc.lower() != urlparse(final_url).netloc.lower():
        flags.append("cross_domain_redirect")

    error: str | None = None
    valid = status_code == 200

    if status_code != 200:
        error = f"Expected status 200, got {status_code}"
        valid = False
    elif method == "GET" or body:
        if not body and method == "GET":
            body = ""
        wall = detect_login_wall(body, config) if body else None
        if wall:
            error = wall
            valid = False
            flags.append("login_or_access_wall")

    return {
        "url": url,
        "valid": valid,
        "status_code": status_code,
        "final_url": final_url,
        "fetch_method": method,
        "redirect_chain": chain,
        "warnings": warnings,
        "flags": flags,
        "error": error,
    }


def fetch_page(url: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[str, str, dict[str, Any]]:
    """Download HTML for scraping. Raises requests.HTTPError on non-success."""
    _, parse_error = parse_url_or_error(url)
    if parse_error:
        raise ValueError(parse_error)

    response = _request("GET", url, timeout=timeout, stream=False)
    try:
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding
        meta = {
            "final_url": response.url,
            "redirect_chain": redirect_chain(response),
            "status_code": response.status_code,
        }
        return response.text, response.url, meta
    finally:
        response.close()


def suggest_better_apply_url(url: str) -> str | None:
    """Return guidance when the URL host is a poor scrape target."""
    profile = host_profile(url)
    if profile.get("discourage"):
        return profile.get("reason")
    host = normalize_host(urlparse(url).netloc)
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return "Prefer the employer careers or short apply link instead of LinkedIn."
    return None

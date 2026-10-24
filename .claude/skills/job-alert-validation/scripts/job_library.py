"""Shared helpers for job_postings/ library, trust warnings, and duplicates."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from url_fetch import load_url_host_config, normalize_host

NEAR_DUPLICATE_RATIO = 0.85

SUSPICIOUS_APPLY_HOSTS = {
    "forms.gle",
    "docs.google.com",
    "drive.google.com",
    "t.me",
    "telegram.me",
    "wa.me",
    "chat.whatsapp.com",
    "bit.ly",
    "tinyurl.com",
}

GENERIC_CAREERS_LABELS = {
    "careers",
    "jobs",
    "apply",
    "recruit",
    "hiring",
    "talent",
    "workday",
    "greenhouse",
    "lever",
    "icims",
    "smartrecruiters",
}


def parse_posted_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    match = re.search(r"(\d{4}-\d{2}-\d{2})", str(value))
    if match:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    return None


def posting_freshness(job: dict[str, Any], today: date, max_age_days: int) -> dict[str, Any]:
    posted = parse_posted_date(job.get("posted_date") or job.get("posting_date"))
    if posted is None:
        return {
            "fresh": False,
            "posted_date": job.get("posted_date") or "",
            "age_days": None,
            "reason": "Missing posted_date; cannot confirm the posting is within the freshness window",
        }
    age_days = (today - posted).days
    if age_days < 0:
        return {
            "fresh": True,
            "posted_date": posted.isoformat(),
            "age_days": age_days,
            "reason": "Posting date is in the future",
        }
    if age_days > max_age_days:
        return {
            "fresh": False,
            "posted_date": posted.isoformat(),
            "age_days": age_days,
            "reason": f"Posted {age_days} days ago (limit is {max_age_days} days)",
        }
    return {
        "fresh": True,
        "posted_date": posted.isoformat(),
        "age_days": age_days,
        "reason": f"Posted {age_days} days ago",
    }


def load_job_records(jobs_dir: Path) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    if not jobs_dir.is_dir():
        return jobs
    for path in sorted(jobs_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        payload["_source_file"] = str(path)
        payload["_source_path"] = path
        jobs.append(payload)
    return jobs


def normalize_job_url(url: str | None) -> str:
    if not url or not str(url).strip():
        return ""
    parsed = urlparse(str(url).strip())
    host = normalize_host(parsed.netloc)
    path = parsed.path.rstrip("/") or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme.lower()}://{host}{path}{query}".lower()


def normalize_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def company_title_key(job: dict[str, Any]) -> str:
    company = normalize_label(str(job.get("company_name") or ""))
    title = normalize_label(str(job.get("job_title") or ""))
    if not company and not title:
        return ""
    return f"{company}|{title}"


def job_urls(job: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("apply_link", "source_url"):
        normalized = normalize_job_url(job.get(key))
        if normalized:
            urls.append(normalized)
    return urls


def job_fingerprint_text(job: dict[str, Any]) -> str:
    skills = job.get("skills") or []
    if isinstance(skills, list):
        skill_text = " ".join(str(item) for item in skills)
    else:
        skill_text = str(skills)
    parts = [
        job.get("company_name"),
        job.get("job_title"),
        job.get("location"),
        job.get("description"),
        skill_text,
    ]
    return " ".join(str(part) for part in parts if part).strip().lower()


def text_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def company_name_tokens(company_name: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", company_name.lower())
    return [token for token in raw if token not in {"ltd", "limited", "inc", "corp", "pvt", "private"}]


def host_matches_company(host: str, company_name: str) -> bool:
    if not host or not company_name:
        return True
    bare_host = normalize_host(host)
    host_compact = normalize_label(bare_host.split(".")[0] if bare_host else "")
    tokens = company_name_tokens(company_name)
    if not tokens:
        return True
    for token in tokens:
        if len(token) < 3:
            continue
        if token in bare_host or token in host_compact:
            return True
        if host_compact and token in host_compact:
            return True
    return False


def apply_domain_warnings(
    company_name: str,
    apply_url: str | None,
    *,
    final_url: str | None = None,
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    check_url = final_url or apply_url
    if not check_url:
        return warnings

    parsed = urlparse(check_url)
    host = normalize_host(parsed.netloc)
    if not host:
        return warnings

    config = load_url_host_config()
    shorteners = {normalize_host(item) for item in config.get("shortener_hosts", [])}
    if host in shorteners:
        return warnings

    if host in SUSPICIOUS_APPLY_HOSTS:
        warnings.append(
            {
                "code": "suspicious_apply_host",
                "message": f"Apply link host ({parsed.netloc}) is often used for informal or unverified postings.",
            }
        )

    company = str(company_name or "").strip()
    if company and not host_matches_company(host, company):
        label_hit = any(label in host for label in GENERIC_CAREERS_LABELS)
        if not label_hit:
            warnings.append(
                {
                    "code": "company_domain_mismatch",
                    "message": (
                        f"Apply host ({parsed.netloc}) does not obviously match company "
                        f"\"{company}\". Confirm this is the official careers page."
                    ),
                }
            )

    start_host = normalize_host(urlparse(apply_url or "").netloc) if apply_url else ""
    end_host = host
    if start_host and end_host and start_host != end_host and start_host not in shorteners:
        if not host_matches_company(end_host, company):
            warnings.append(
                {
                    "code": "redirect_domain_mismatch",
                    "message": (
                        f"Link redirects from {start_host} to {end_host}; "
                        "verify the final destination is the employer site."
                    ),
                }
            )

    return warnings


def find_duplicate_postings(
    job: dict[str, Any],
    jobs_dir: Path,
    *,
    exclude_path: Path | None = None,
    near_duplicate_ratio: float = NEAR_DUPLICATE_RATIO,
) -> list[dict[str, Any]]:
    duplicates: list[dict[str, Any]] = []
    candidate_urls = set(job_urls(job))
    candidate_key = company_title_key(job)
    candidate_text = job_fingerprint_text(job)

    for existing in load_job_records(jobs_dir):
        existing_path = existing.get("_source_path")
        if exclude_path and existing_path == exclude_path:
            continue
        path_str = str(existing_path or existing.get("_source_file") or "")

        for url in job_urls(existing):
            if url in candidate_urls:
                duplicates.append(
                    {
                        "reason": "same_apply_url",
                        "path": path_str,
                        "detail": url,
                    }
                )
                break

        if candidate_key and company_title_key(existing) == candidate_key:
            duplicates.append(
                {
                    "reason": "same_company_and_title",
                    "path": path_str,
                    "detail": candidate_key,
                }
            )
            continue

        if candidate_text:
            existing_text = job_fingerprint_text(existing)
            ratio = text_similarity(candidate_text, existing_text)
            if ratio >= near_duplicate_ratio:
                duplicates.append(
                    {
                        "reason": "near_duplicate_text",
                        "path": path_str,
                        "detail": f"similarity={ratio:.2f}",
                    }
                )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in duplicates:
        key = (item["reason"], item["path"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def build_trust_report(
    job: dict[str, Any],
    *,
    apply_url: str | None = None,
    final_url: str | None = None,
    jobs_dir: Path | None = None,
    exclude_path: Path | None = None,
) -> dict[str, Any]:
    domain_warnings = apply_domain_warnings(
        str(job.get("company_name") or ""),
        apply_url or job.get("apply_link") or job.get("source_url"),
        final_url=final_url,
    )
    duplicates: list[dict[str, Any]] = []
    if jobs_dir is not None:
        duplicates = find_duplicate_postings(job, jobs_dir, exclude_path=exclude_path)

    messages = [item["message"] for item in domain_warnings]
    messages.extend(
        f"Possible duplicate ({item['reason']}): {item['path']}" for item in duplicates
    )
    return {
        "domain_warnings": domain_warnings,
        "duplicates": duplicates,
        "warnings": messages,
        "has_warnings": bool(messages),
    }


def summarize_library(jobs_dir: Path, *, today: date | None = None, max_age_days: int = 15) -> dict[str, Any]:
    today = today or date.today()
    entries: list[dict[str, Any]] = []
    stale_paths: list[str] = []
    for job in load_job_records(jobs_dir):
        path_str = str(job.get("_source_file") or "")
        freshness = posting_freshness(job, today, max_age_days)
        entries.append(
            {
                "path": path_str,
                "company_name": job.get("company_name") or "",
                "job_title": job.get("job_title") or "",
                "location": job.get("location") or "",
                "apply_link": job.get("apply_link") or "",
                "posted_date": freshness.get("posted_date") or job.get("posted_date") or "",
                "fresh": freshness["fresh"],
                "age_days": freshness.get("age_days"),
                "freshness_reason": freshness.get("reason"),
            }
        )
        if not freshness["fresh"]:
            stale_paths.append(path_str)
    entries.sort(key=lambda item: (not item["fresh"], item.get("age_days") is None, item.get("age_days") or 0))
    return {
        "jobs_dir": str(jobs_dir),
        "today": today.isoformat(),
        "max_age_days": max_age_days,
        "count": len(entries),
        "fresh_count": sum(1 for item in entries if item["fresh"]),
        "stale_count": len(stale_paths),
        "stale_paths": stale_paths,
        "jobs": entries,
    }


def housekeep_library(
    jobs_dir: Path,
    *,
    today: date | None = None,
    max_age_days: int = 15,
    remove_stale: bool = False,
    dedupe: bool = False,
    remove_duplicates: bool = False,
) -> dict[str, Any]:
    today = today or date.today()
    summary = summarize_library(jobs_dir, today=today, max_age_days=max_age_days)
    removed: list[str] = []
    if remove_stale:
        for path_str in summary["stale_paths"]:
            path = Path(path_str)
            if path.is_file():
                path.unlink()
                removed.append(path_str)

    duplicate_groups: list[dict[str, Any]] = []
    if dedupe:
        seen_keys: dict[str, str] = {}
        for job in load_job_records(jobs_dir):
            path_str = str(job.get("_source_file") or "")
            key = company_title_key(job) or normalize_job_url(job.get("apply_link"))
            if not key:
                continue
            if key in seen_keys:
                duplicate_groups.append(
                    {"key": key, "kept": seen_keys[key], "duplicate": path_str}
                )
                if remove_duplicates:
                    path = Path(path_str)
                    if path.is_file():
                        path.unlink()
                        removed.append(path_str)
            else:
                seen_keys[key] = path_str

    return {
        **summary,
        "remove_stale": remove_stale,
        "dedupe": dedupe,
        "remove_duplicates": remove_duplicates,
        "removed_paths": removed,
        "duplicate_groups": duplicate_groups,
    }

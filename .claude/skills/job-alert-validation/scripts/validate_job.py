#!/usr/bin/env python3
"""Run URL validation, scraping, comparison, optional save, and WhatsApp formatting."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from compare_job import compare_jobs
from format_whatsapp import format_whatsapp_group_message
from job_library import build_trust_report
from save_job import save_job_record
from validate_url import validate_url
from web_scraping import scrape_job_from_html, scrape_job_url

SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOBS_DIR = SKILL_ROOT / "job_postings"


def load_json(path: str) -> dict[str, Any]:
    if path == "-":
        payload = json.load(sys.stdin)
    else:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Job JSON must be an object")
    return payload


def resolve_posting_url(job: dict[str, Any], override: str | None) -> str | None:
    if override and override.strip():
        return override.strip()
    for key in ("apply_link", "source_url"):
        value = job.get(key)
        if value and str(value).strip():
            return str(value).strip()
    return None


def prepare_record_for_save(
    user_job: dict[str, Any],
    scraped: dict[str, Any] | None,
    final_url: str | None,
) -> dict[str, Any]:
    record = dict(user_job)
    if final_url and not record.get("source_url"):
        record["source_url"] = final_url
    if scraped and not record.get("apply_link") and scraped.get("apply_link"):
        record["apply_link"] = scraped["apply_link"]
    return record


def run_job_validation(
    user_job: dict[str, Any],
    *,
    url: str | None = None,
    scraped_job: dict[str, Any] | None = None,
    timeout: int = 20,
    save: bool = False,
    jobs_dir: Path | None = None,
    overwrite: bool = False,
    include_whatsapp: bool = False,
) -> tuple[dict[str, Any], int]:
    posting_url = resolve_posting_url(user_job, url)
    result: dict[str, Any] = {
        "posting_url": posting_url,
        "url_validation": None,
        "scraped": None,
        "comparison": None,
        "trust": None,
        "saved_path": None,
        "whatsapp": None,
    }

    if scraped_job is None:
        if not posting_url:
            result["error"] = "No apply_link or source_url in job JSON; pass --url or --scraped-json"
            return result, 2

        url_check = validate_url(posting_url, timeout=timeout)
        result["url_validation"] = url_check
        if not url_check.get("valid"):
            return result, 3

        scraped_job = scrape_job_url(posting_url, timeout=timeout, require_valid=False)
        final_url = url_check.get("final_url") or posting_url
    else:
        final_url = posting_url

    result["scraped"] = scraped_job
    comparison = compare_jobs(user_job, scraped_job)
    result["comparison"] = comparison

    exit_code = 0 if comparison["matches"] else 1

    record = prepare_record_for_save(user_job, scraped_job, final_url)
    trust_dir = jobs_dir if (jobs_dir is not None or save) else None
    result["trust"] = build_trust_report(
        record,
        apply_url=posting_url,
        final_url=final_url,
        jobs_dir=trust_dir or DEFAULT_JOBS_DIR,
    )

    if save and comparison["matches"]:
        saved_path = save_job_record(
            record,
            jobs_dir or DEFAULT_JOBS_DIR,
            overwrite=overwrite,
        )
        result["saved_path"] = str(saved_path)
        result["trust"] = build_trust_report(
            record,
            apply_url=posting_url,
            final_url=final_url,
            jobs_dir=jobs_dir or DEFAULT_JOBS_DIR,
            exclude_path=Path(saved_path),
        )

    if include_whatsapp and comparison["matches"]:
        record = prepare_record_for_save(user_job, scraped_job, final_url)
        result["whatsapp"] = format_whatsapp_group_message(record)

    return result, exit_code


def run_job_validation_from_html(
    user_job: dict[str, Any],
    html: str,
    page_url: str,
    *,
    save: bool = False,
    jobs_dir: Path | None = None,
    overwrite: bool = False,
    include_whatsapp: bool = False,
) -> tuple[dict[str, Any], int]:
    scraped_job = scrape_job_from_html(html, page_url)
    return run_job_validation(
        user_job,
        url=page_url,
        scraped_job=scraped_job,
        save=save,
        jobs_dir=jobs_dir,
        overwrite=overwrite,
        include_whatsapp=include_whatsapp,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a job posting URL, scrape, compare to user JSON, optionally save and format WhatsApp."
    )
    parser.add_argument("user_json", help="Path to restructured user job JSON, or '-' for stdin")
    parser.add_argument("--url", help="Posting URL (default: apply_link or source_url from JSON)")
    parser.add_argument(
        "--scraped-json",
        help="Use existing scraped JSON instead of fetching (offline / tests)",
    )
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds")
    parser.add_argument(
        "--save",
        action="store_true",
        help="When comparison matches, write job JSON to job_postings/",
    )
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=DEFAULT_JOBS_DIR,
        help=f"Directory for --save (default: {DEFAULT_JOBS_DIR})",
    )
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing an existing saved job file")
    parser.add_argument(
        "--whatsapp",
        action="store_true",
        help="Include copy-ready WhatsApp text in JSON output when comparison matches",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    try:
        user_job = load_json(args.user_json)
        scraped_job = load_json(args.scraped_json) if args.scraped_json else None
        result, exit_code = run_job_validation(
            user_job,
            url=args.url,
            scraped_job=scraped_job,
            timeout=args.timeout,
            save=args.save,
            jobs_dir=args.jobs_dir,
            overwrite=args.overwrite,
            include_whatsapp=args.whatsapp,
        )
        indent = 2 if args.pretty else None
        print(json.dumps(result, indent=indent, ensure_ascii=False))
        return exit_code
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

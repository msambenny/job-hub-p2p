#!/usr/bin/env python3
"""Save a validated job record to the local job_postings/ library."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import jsonschema

from job_library import build_trust_report

SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOBS_DIR = SKILL_ROOT / "job_postings"
SCHEMA_PATH = SKILL_ROOT / "schemas" / "job_schema.json"


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def slugify(value: str, fallback: str = "job") -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return text[:60] or fallback


def default_filename(job: dict[str, Any]) -> str:
    company = slugify(str(job.get("company_name") or ""), "company")
    title = slugify(str(job.get("job_title") or ""), "role")
    posted = str(job.get("posted_date") or "").strip()[:10]
    if not posted:
        posted = date.today().isoformat()
    return f"{company}_{title}_{posted}.json"


def validate_job_record(job: dict[str, Any], schema: dict[str, Any] | None = None) -> None:
    jsonschema.validate(instance=job, schema=schema or load_schema())


def save_job_record(
    job: dict[str, Any],
    jobs_dir: Path | None = None,
    *,
    filename: str | None = None,
    overwrite: bool = False,
    validate: bool = True,
) -> Path:
    if validate:
        validate_job_record(job)

    target_dir = jobs_dir or DEFAULT_JOBS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    name = filename or default_filename(job)
    if not name.endswith(".json"):
        name = f"{name}.json"

    path = target_dir / name
    if path.exists() and not overwrite:
        raise FileExistsError(f"Job file already exists: {path}")

    path.write_text(json.dumps(job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_json(path: str) -> dict[str, Any]:
    if path == "-":
        payload = json.load(sys.stdin)
    else:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Job JSON must be an object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Save a validated job JSON file under job_postings/ (local only, gitignored)."
    )
    parser.add_argument("job_json", help="Path to job JSON, or '-' for stdin")
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=DEFAULT_JOBS_DIR,
        help=f"Directory for saved jobs (default: {DEFAULT_JOBS_DIR})",
    )
    parser.add_argument("--filename", help="Override output filename (default: derived from company/title/date)")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing file with the same name")
    parser.add_argument("--skip-schema", action="store_true", help="Do not validate against job_schema.json")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print result JSON on stdout")
    args = parser.parse_args()

    try:
        job = load_json(args.job_json)
        path = save_job_record(
            job,
            args.jobs_dir,
            filename=args.filename,
            overwrite=args.overwrite,
            validate=not args.skip_schema,
        )
        trust = build_trust_report(
            job,
            apply_url=str(job.get("apply_link") or job.get("source_url") or ""),
            jobs_dir=args.jobs_dir,
            exclude_path=path,
        )
        result = {"saved": True, "path": str(path), "trust": trust}
        indent = 2 if args.pretty else None
        print(json.dumps(result, indent=indent, ensure_ascii=False))
        return 0
    except (OSError, json.JSONDecodeError, ValueError, jsonschema.ValidationError, FileExistsError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

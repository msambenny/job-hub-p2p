#!/usr/bin/env python3
"""List and summarize jobs stored under job_postings/."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from job_library import summarize_library

SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOBS_DIR = SKILL_ROOT / "job_postings"


def main() -> int:
    parser = argparse.ArgumentParser(description="Index local job_postings/ JSON files.")
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=DEFAULT_JOBS_DIR,
        help=f"Folder of job JSON files (default: {DEFAULT_JOBS_DIR})",
    )
    parser.add_argument("--max-age-days", type=int, default=15)
    parser.add_argument("--fresh-only", action="store_true", help="Only include fresh postings")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    result = summarize_library(args.jobs_dir, max_age_days=args.max_age_days)
    if args.fresh_only:
        result["jobs"] = [item for item in result["jobs"] if item["fresh"]]
        result["count"] = len(result["jobs"])
    indent = 2 if args.pretty else None
    print(json.dumps(result, indent=indent, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

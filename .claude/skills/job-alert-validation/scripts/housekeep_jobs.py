#!/usr/bin/env python3
"""Report or clean stale / duplicate jobs in job_postings/."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from job_library import housekeep_library

SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOBS_DIR = SKILL_ROOT / "job_postings"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Housekeeping for local job_postings/ (stale removal and optional dedupe)."
    )
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=DEFAULT_JOBS_DIR,
        help=f"Folder of job JSON files (default: {DEFAULT_JOBS_DIR})",
    )
    parser.add_argument("--max-age-days", type=int, default=15)
    parser.add_argument(
        "--remove-stale",
        action="store_true",
        help="Delete postings older than max-age-days or missing posted_date",
    )
    parser.add_argument(
        "--dedupe",
        action="store_true",
        help="Report duplicate company+title or apply URL entries",
    )
    parser.add_argument(
        "--remove-duplicates",
        action="store_true",
        help="With --dedupe, delete later duplicates (keeps first file seen)",
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    if args.remove_duplicates and not args.dedupe:
        print(json.dumps({"error": "--remove-duplicates requires --dedupe"}), file=sys.stderr)
        return 1

    result = housekeep_library(
        args.jobs_dir,
        max_age_days=args.max_age_days,
        remove_stale=args.remove_stale,
        dedupe=args.dedupe or args.remove_duplicates,
        remove_duplicates=args.remove_duplicates,
    )

    indent = 2 if args.pretty else None
    print(json.dumps(result, indent=indent, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

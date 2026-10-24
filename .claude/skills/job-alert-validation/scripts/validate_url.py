#!/usr/bin/env python3
"""Validate that a job posting URL is reachable and returns HTTP 200."""

from __future__ import annotations

import argparse
import json
import sys

from url_fetch import DEFAULT_TIMEOUT, probe_url, suggest_better_apply_url


def validate_url(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    result = probe_url(url, timeout=timeout)
    hint = suggest_better_apply_url(url)
    if hint and hint not in result["warnings"]:
        result["warnings"].append(hint)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check whether a URL is reachable and returns HTTP 200."
    )
    parser.add_argument("url", help="URL to validate")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )
    args = parser.parse_args()

    result = validate_url(args.url, timeout=args.timeout)
    indent = 2 if args.pretty else None
    print(json.dumps(result, indent=indent, ensure_ascii=False))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

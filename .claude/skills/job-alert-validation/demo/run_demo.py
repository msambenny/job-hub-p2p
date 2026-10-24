#!/usr/bin/env python3
"""Offline end-to-end demo: genuine alert, suspicious alert, then resume matching.

Uses local HTML pages instead of live URLs and a temporary job library, so it is
safe to run repeatedly and never touches job_postings/.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

DEMO_DIR = Path(__file__).resolve().parent
SKILL_ROOT = DEMO_DIR.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from match_resume import match_resume  # noqa: E402
from validate_job import run_job_validation_from_html  # noqa: E402

SCENARIOS = [
    ("Genuine alert", "genuine_alert.json", "genuine_page.html"),
    ("Suspicious alert", "suspicious_alert.json", "suspicious_page.html"),
]


def render(text: str, today: date) -> str:
    """Fill date placeholders so the demo stays fresh whatever day it runs."""
    return (
        text.replace("{{POSTED}}", (today - timedelta(days=2)).isoformat())
        .replace("{{DEADLINE}}", (today + timedelta(days=30)).isoformat())
    )


def banner(title: str) -> None:
    print(f"\n{'=' * 64}\n  {title}\n{'=' * 64}")


def print_validation(result: dict[str, Any], exit_code: int) -> None:
    comparison = result["comparison"]
    summary = comparison["summary"]
    print(f"Apply link : {result['posting_url']}")
    print(
        f"Comparison : {summary['matched']} matched, {summary['partial']} partial, "
        f"{summary['mismatched']} mismatched (of {summary['total_compared']} fields)"
    )
    for item in comparison["fields"]:
        if item["status"] in {"mismatch", "missing_on_site"}:
            mark = "⚠" if item["field"] in summary["salary_warnings"] else "✗"
            print(f"  {mark} {item['field']}: alert says {item['user_value']!r}, page has {item['scraped_value']!r}")
    if summary["critical_mismatches"]:
        print(f"Critical   : {', '.join(summary['critical_mismatches'])}")
    for warning in result["trust"]["warnings"]:
        print(f"  ⚠ {warning}")

    if exit_code == 0:
        print(f"\nVERDICT: ✅ Verified — saved to {Path(result['saved_path']).name}")
        print("\n--- Copy-ready WhatsApp message ---")
        print(result["whatsapp"], end="")
        print("-----------------------------------")
    else:
        print("\nVERDICT: ❌ Do not share or apply — the alert does not match its posting page.")


def print_matches(result: dict[str, Any]) -> None:
    print(f"Resume     : {Path(result['resume']).name}")
    print(f"Matches    : {result['match_count']} (jobs posted within {result['max_age_days']} days)")
    for match in result["matches"]:
        terms = ", ".join(match["matched_terms"][:8])
        print(f"  ★ {match['job_title']} @ {match['company_name']} — score {match['score']}")
        print(f"    matched on: {terms}")
        print(f"    apply: {match['apply_link']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jobs-dir", type=Path, help="Keep saved jobs here instead of a temp folder")
    parser.add_argument(
        "--resume",
        type=Path,
        default=SKILL_ROOT / "examples" / "backend_resume.txt",
        help="Resume to match against the demo library",
    )
    parser.add_argument("--json", action="store_true", help="Also print the full JSON for each step")
    args = parser.parse_args()

    today = date.today()
    with tempfile.TemporaryDirectory() as tmp:
        jobs_dir = args.jobs_dir or Path(tmp)
        for step, (title, alert_name, page_name) in enumerate(SCENARIOS, start=1):
            banner(f"Step {step}: {title}")
            job = json.loads(render((DEMO_DIR / "alerts" / alert_name).read_text(encoding="utf-8"), today))
            html = render((DEMO_DIR / "pages" / page_name).read_text(encoding="utf-8"), today)
            result, exit_code = run_job_validation_from_html(
                job,
                html,
                job["apply_link"],
                save=True,
                jobs_dir=jobs_dir,
                overwrite=True,
                include_whatsapp=True,
            )
            print_validation(result, exit_code)
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False))

        banner(f"Step {len(SCENARIOS) + 1}: Resume matching")
        matches = match_resume(args.resume, jobs_dir, today=today)
        print_matches(matches)
        if args.json:
            print(json.dumps(matches, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

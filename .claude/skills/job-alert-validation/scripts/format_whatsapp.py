#!/usr/bin/env python3
"""Format validated job details as a WhatsApp group message (copy-ready, not sent)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _line(emoji: str, label: str, value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    return f"{emoji} *{label}:* {text}"


def _contact(job: dict[str, Any]) -> str:
    parts = [
        _text(job.get("contact_name")),
        _text(job.get("contact_whatsapp")),
        _text(job.get("contact_email")),
    ]
    return " · ".join(part for part in parts if part)


def _salary(job: dict[str, Any]) -> str:
    low = _text(job.get("salary_min"))
    high = _text(job.get("salary_max"))
    if low and high and low != high:
        return f"{low} – {high}"
    return low or high


def format_whatsapp_group_message(job: dict[str, Any]) -> str:
    header = [
        _line("🏢", "Company", job.get("company_name")),
        _line("📋", "Opportunity", job.get("job_title")),
        _line("🗂", "Department", job.get("department")),
        _line("💼", "Type", job.get("job_type")),
        _line("🏠", "Work mode", job.get("work_mode")),
        _line("📍", "Location", job.get("location")),
    ]
    blocks = ["\n".join(line for line in header if line)]

    who = [
        _line("🎓", "Education", job.get("education")),
        _line("🧑‍💻", "Experience", job.get("experience")),
        _line("🛠", "Skills", job.get("skills")),
    ]
    who = [line for line in who if line]
    if who:
        blocks.append("*Who can apply*\n" + "\n".join(who))

    description = _text(job.get("description"))
    if description:
        blocks.append(f"*About*\n{description}")

    details = [
        _line("💰", "Salary", _salary(job)),
        _line("🎁", "Benefits", job.get("benefits")),
        _line("⏰", "Deadline", job.get("application_deadline")),
        _line("📨", "Contact", _contact(job)),
        _line("🔗", "Apply", job.get("apply_link")),
        _line("📝", "Note", job.get("custom_note")),
    ]
    details = [line for line in details if line]
    if details:
        blocks.append("\n".join(details))

    return "\n\n".join(block for block in blocks if block).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print a WhatsApp-group-ready job alert. Does not send the message."
    )
    parser.add_argument("job_json", help="Path to validated job JSON, or '-' for stdin")
    args = parser.parse_args()

    try:
        payload = json.load(sys.stdin) if args.job_json == "-" else json.load(open(args.job_json, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    print(format_whatsapp_group_message(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Compare user-provided job details against scraped website content."""

from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

COMPARISON_FIELDS = [
    "company_name",
    "job_title",
    "department",
    "job_type",
    "work_mode",
    "location",
    "salary_min",
    "salary_max",
    "benefits",
    "experience",
    "education",
    "description",
    "application_deadline",
    "contact_name",
    "contact_whatsapp",
    "contact_email",
    "apply_link",
]

CRITICAL_FIELDS = {"company_name", "job_title", "location"}
WARNING_ONLY_FIELDS = {"salary_min", "salary_max"}
MATCH_THRESHOLD = 0.6


def normalize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(normalize(item) for item in value if normalize(item))
    text = str(value).lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def load_json(path: str) -> dict[str, Any]:
    if path == "-":
        return json.load(sys.stdin)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


SALARY_TOLERANCE = 0.05
SALARY_MULTIPLIERS = [
    (r"\b(crores?|cr)\b", 10_000_000),
    (r"\b(lpa|lakhs?|lacs?|l)\b", 100_000),
    (r"\b(million|mn|m)\b", 1_000_000),
    (r"\b(k)\b", 1_000),
]
SALARY_CURRENCIES = [
    (r"₹|\binr\b|\brs\.?|\blpa\b|\blakhs?\b|\blacs?\b|\bcrores?\b", "INR"),
    (r"\$|\busd\b", "USD"),
    (r"€|\beur\b", "EUR"),
    (r"£|\bgbp\b", "GBP"),
]


def parse_salary(value: Any) -> tuple[float, str | None] | None:
    """Parse '12 LPA', '1200000 INR', '₹12,00,000' or '$90k' into (amount, currency)."""
    text = normalize(value)
    number = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    if not number:
        return None
    amount = float(number.group(0).replace(",", ""))
    rest = text[number.end():]
    for pattern, multiplier in SALARY_MULTIPLIERS:
        if re.match(r"\s*" + pattern, rest):
            amount *= multiplier
            break
    currency = next((code for pattern, code in SALARY_CURRENCIES if re.search(pattern, text)), None)
    return amount, currency


def compare_salary(user_value: Any, scraped_value: Any) -> float | None:
    """Return 1.0/0.0 when both salaries parse to amounts, else None to fall back to text."""
    user = parse_salary(user_value)
    scraped = parse_salary(scraped_value)
    if not user or not scraped:
        return None
    (user_amount, user_currency), (scraped_amount, scraped_currency) = user, scraped
    if user_currency and scraped_currency and user_currency != scraped_currency:
        return 0.0
    if scraped_amount == 0:
        return 1.0 if user_amount == 0 else 0.0
    return 1.0 if abs(user_amount - scraped_amount) / scraped_amount <= SALARY_TOLERANCE else 0.0


def compare_skills(user_skills: list[Any], scraped_skills: list[Any]) -> dict[str, Any]:
    user = {normalize(skill) for skill in user_skills if normalize(skill)}
    scraped = {normalize(skill) for skill in scraped_skills if normalize(skill)}

    if not user:
        return {
            "field": "skills",
            "user_value": list(user_skills),
            "scraped_value": list(scraped_skills),
            "status": "skipped",
            "score": None,
            "note": "No skills provided by user",
        }

    overlap = user & scraped
    partial = [
        skill
        for skill in user
        if skill not in overlap
        and any(similarity(skill, scraped_skill) >= MATCH_THRESHOLD for scraped_skill in scraped)
    ]
    matched = len(overlap) + len(partial)
    score = matched / len(user)

    if score >= MATCH_THRESHOLD:
        status = "match"
    elif score > 0:
        status = "partial"
    else:
        status = "mismatch"

    return {
        "field": "skills",
        "user_value": list(user_skills),
        "scraped_value": list(scraped_skills),
        "status": status,
        "score": round(score, 2),
        "note": f"{len(overlap)} exact and {len(partial)} fuzzy matches out of {len(user)}",
    }


def compare_field(field: str, user_value: Any, scraped_value: Any) -> dict[str, Any]:
    user_text = normalize(user_value)
    scraped_text = normalize(scraped_value)

    if not user_text:
        return {
            "field": field,
            "user_value": user_value,
            "scraped_value": scraped_value,
            "status": "skipped",
            "score": None,
            "note": "Not provided by user",
        }

    if not scraped_text:
        return {
            "field": field,
            "user_value": user_value,
            "scraped_value": scraped_value,
            "status": "missing_on_site",
            "score": 0.0,
            "note": "Field not found on the job page",
        }

    salary_score = compare_salary(user_value, scraped_value) if field in WARNING_ONLY_FIELDS else None
    if salary_score is not None:
        return {
            "field": field,
            "user_value": user_value,
            "scraped_value": scraped_value,
            "status": "match" if salary_score else "mismatch",
            "score": salary_score,
            "note": "Compared as normalized amounts",
        }

    score = similarity(user_text, scraped_text)
    if field == "description" and score < MATCH_THRESHOLD:
        score = max(score, similarity(user_text[:200], scraped_text))

    if score >= MATCH_THRESHOLD:
        status = "match"
    elif score >= 0.35:
        status = "partial"
    else:
        status = "mismatch"

    return {
        "field": field,
        "user_value": user_value,
        "scraped_value": scraped_value,
        "status": status,
        "score": round(score, 2),
        "note": None,
    }


def compare_jobs(user_data: dict[str, Any], scraped_data: dict[str, Any]) -> dict[str, Any]:
    fields = [compare_field(field, user_data.get(field), scraped_data.get(field)) for field in COMPARISON_FIELDS]
    fields.append(compare_skills(user_data.get("skills", []), scraped_data.get("skills", [])))

    actionable = [item for item in fields if item["status"] != "skipped"]
    mismatches = [item for item in actionable if item["status"] in {"mismatch", "missing_on_site"}]
    salary_warnings = [item for item in mismatches if item["field"] in WARNING_ONLY_FIELDS]
    blocking = [item for item in mismatches if item["field"] not in WARNING_ONLY_FIELDS]
    critical_mismatches = [
        item for item in blocking if item["field"] in CRITICAL_FIELDS and user_data.get(item["field"])
    ]

    matches = all(
        item["status"] in {"match", "partial", "skipped"} or item["field"] in WARNING_ONLY_FIELDS
        for item in fields
    )
    if critical_mismatches:
        matches = False

    return {
        "matches": matches,
        "summary": {
            "total_compared": len(actionable),
            "matched": sum(1 for item in actionable if item["status"] == "match"),
            "partial": sum(1 for item in actionable if item["status"] == "partial"),
            "mismatched": len(blocking),
            "salary_warnings": [item["field"] for item in salary_warnings],
            "critical_mismatches": [item["field"] for item in critical_mismatches],
        },
        "fields": fields,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare user job details against scraped website content."
    )
    parser.add_argument("user_json", help="Path to user job JSON, or '-' for stdin")
    parser.add_argument("scraped_json", help="Path to scraped job JSON from web_scraping.py")
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )
    args = parser.parse_args()

    try:
        user_data = load_json(args.user_json)
        scraped_data = load_json(args.scraped_json)
        result = compare_jobs(user_data, scraped_data)
        indent = 2 if args.pretty else None
        print(json.dumps(result, indent=indent, ensure_ascii=False))
        return 0 if result["matches"] else 1
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

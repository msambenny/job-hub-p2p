#!/usr/bin/env python3
"""Scrape job posting details from a URL for job-alert validation."""

from __future__ import annotations

import argparse
import json
import re
import sys
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from url_fetch import DEFAULT_TIMEOUT, fetch_page, probe_url

EMPLOYMENT_TYPE_MAP = {
    "FULL_TIME": "Full-Time",
    "PART_TIME": "Part-Time",
    "CONTRACTOR": "Contract",
    "TEMPORARY": "Temporary",
    "INTERN": "Internship",
    "VOLUNTEER": "Volunteer",
    "PER_DIEM": "Per Diem",
    "OTHER": "Other",
}


def empty_job_data(url: str) -> dict[str, Any]:
    return {
        "company_name": "",
        "job_title": "",
        "department": "",
        "job_type": "",
        "work_mode": "",
        "location": "",
        "salary_min": "",
        "salary_max": "",
        "benefits": "",
        "experience": "",
        "education": "",
        "skills": [],
        "description": "",
        "application_deadline": "",
        "posted_date": "",
        "contact_name": "",
        "contact_whatsapp": "",
        "contact_email": "",
        "apply_link": url,
        "custom_note": "",
        "source_url": url,
        "raw_text": "",
    }


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_labeled_value(text: str, labels: list[str]) -> str:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?:{label_pattern})\s*[:\-]\s*(.+?)(?:\n|$)",
        text,
        flags=re.IGNORECASE,
    )
    return clean_text(match.group(1)) if match else ""


def parse_json_ld_blocks(soup: BeautifulSoup) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if isinstance(payload, list):
            blocks.extend(item for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict):
            if "@graph" in payload and isinstance(payload["@graph"], list):
                blocks.extend(item for item in payload["@graph"] if isinstance(item, dict))
            else:
                blocks.append(payload)

    return blocks


def is_job_posting(block: dict[str, Any]) -> bool:
    job_type = block.get("@type", "")
    if isinstance(job_type, list):
        return any("JobPosting" in str(item) for item in job_type)
    return "JobPosting" in str(job_type)


def first_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, list):
        for item in value:
            text = first_string(item)
            if text:
                return text
    if isinstance(value, dict):
        for key in ("name", "value", "@value", "streetAddress", "addressLocality"):
            if key in value:
                text = first_string(value[key])
                if text:
                    return text
    return ""


def parse_location(job_location: Any) -> str:
    if not job_location:
        return ""

    if isinstance(job_location, list):
        parts = [parse_location(item) for item in job_location]
        return "; ".join(part for part in parts if part)

    if isinstance(job_location, str):
        return clean_text(job_location)

    if isinstance(job_location, dict):
        address = job_location.get("address", job_location)
        if isinstance(address, dict):
            parts = [
                first_string(address.get("streetAddress")),
                first_string(address.get("addressLocality")),
                first_string(address.get("addressRegion")),
                first_string(address.get("postalCode")),
                first_string(address.get("addressCountry")),
            ]
            return ", ".join(part for part in parts if part)
        return first_string(address)

    return ""


def parse_salary(base_salary: Any) -> tuple[str, str]:
    if not base_salary or not isinstance(base_salary, dict):
        return "", ""

    value = base_salary.get("value", base_salary)
    if isinstance(value, dict):
        minimum = first_string(value.get("minValue"))
        maximum = first_string(value.get("maxValue"))
        if not minimum and not maximum:
            minimum = first_string(value.get("value"))
        suffix = first_string(base_salary.get("currency"))
        unit = first_string(value.get("unitText"))
        if unit:
            suffix = f"{suffix} per {unit.lower()}".strip()
        if suffix:
            if minimum:
                minimum = f"{minimum} {suffix}"
            if maximum:
                maximum = f"{maximum} {suffix}"
        return minimum, maximum

    amount = first_string(value)
    currency = first_string(base_salary.get("currency"))
    if amount and currency:
        amount = f"{amount} {currency}"
    return amount, amount


def infer_work_mode(text: str, job_location_type: Any = None) -> str:
    location_type = str(job_location_type or "").upper()
    if "TELECOMMUTE" in location_type or "REMOTE" in location_type:
        return "Remote"

    lowered = text.lower()
    if re.search(r"\b(remote|work from home|wfh)\b", lowered):
        return "Remote"
    if re.search(r"\bhybrid\b", lowered):
        return "Hybrid"
    if re.search(r"\b(on[- ]site|in[- ]office|office based)\b", lowered):
        return "On-site"
    return ""


def extract_emails(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)


def extract_phones(text: str) -> list[str]:
    patterns = [
        r"\+?\d[\d\s().-]{8,}\d",
        r"\b\d{10}\b",
    ]
    numbers: list[str] = []
    for pattern in patterns:
        numbers.extend(re.findall(pattern, text))
    return numbers


def extract_skills(text: str) -> list[str]:
    skill_section = re.search(
        r"(?:skills|requirements|qualifications)\s*[:\-]?\s*(.+?)(?:\n\n|\Z)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not skill_section:
        return []

    chunk = skill_section.group(1)
    candidates = re.split(r"[,;\n•\-]+", chunk)
    skills = [clean_text(item) for item in candidates if 2 < len(clean_text(item)) <= 40]
    return skills[:15]


def extract_from_json_ld(block: dict[str, Any], data: dict[str, Any]) -> None:
    data["job_title"] = data["job_title"] or first_string(block.get("title"))
    data["description"] = data["description"] or clean_text(block.get("description"))
    data["application_deadline"] = data["application_deadline"] or first_string(
        block.get("validThrough")
    )
    data["posted_date"] = data["posted_date"] or first_string(block.get("datePosted"))[:10]
    data["department"] = data["department"] or first_string(block.get("occupationalCategory"))

    organization = block.get("hiringOrganization", {})
    if isinstance(organization, dict):
        data["company_name"] = data["company_name"] or first_string(organization.get("name"))
        data["department"] = data["department"] or first_string(organization.get("department"))

    employment_type = block.get("employmentType")
    if employment_type:
        mapped = EMPLOYMENT_TYPE_MAP.get(str(employment_type).upper(), str(employment_type))
        data["job_type"] = data["job_type"] or mapped

    data["location"] = data["location"] or parse_location(block.get("jobLocation"))

    salary_min, salary_max = parse_salary(block.get("baseSalary"))
    data["salary_min"] = data["salary_min"] or salary_min
    data["salary_max"] = data["salary_max"] or salary_max

    benefits = block.get("jobBenefits")
    if benefits:
        if isinstance(benefits, list):
            data["benefits"] = data["benefits"] or ", ".join(
                first_string(item) for item in benefits if first_string(item)
            )
        else:
            data["benefits"] = data["benefits"] or first_string(benefits)

    qualifications = block.get("qualifications") or block.get("experienceRequirements")
    data["experience"] = data["experience"] or first_string(qualifications)

    education = block.get("educationRequirements")
    data["education"] = data["education"] or first_string(education)

    skills = block.get("skills")
    if isinstance(skills, list):
        parsed_skills = [first_string(item) for item in skills if first_string(item)]
        if parsed_skills:
            data["skills"] = parsed_skills

    contact = block.get("applicationContact") or block.get("contactPoint")
    if isinstance(contact, dict):
        data["contact_name"] = data["contact_name"] or first_string(contact.get("name"))
        data["contact_email"] = data["contact_email"] or first_string(contact.get("email"))
        data["contact_whatsapp"] = data["contact_whatsapp"] or first_string(contact.get("telephone"))

    apply_link = first_string(block.get("url")) or first_string(block.get("directApply"))
    if apply_link.startswith("http"):
        data["apply_link"] = apply_link

    work_mode = infer_work_mode(
        " ".join(
            filter(
                None,
                [data["description"], data["location"], data["job_type"]],
            )
        ),
        block.get("jobLocationType"),
    )
    data["work_mode"] = data["work_mode"] or work_mode


def extract_from_meta(soup: BeautifulSoup, data: dict[str, Any]) -> None:
    title = soup.find("title")
    if title and not data["job_title"]:
        data["job_title"] = clean_text(title.get_text())

    meta_fields = {
        "og:title": "job_title",
        "og:description": "description",
        "og:site_name": "company_name",
        "description": "description",
    }

    for attr, field in meta_fields.items():
        if data[field]:
            continue
        tag = soup.find("meta", attrs={"property": attr}) or soup.find("meta", attrs={"name": attr})
        if tag and tag.get("content"):
            data[field] = clean_text(tag["content"])


def extract_from_html(soup: BeautifulSoup, data: dict[str, Any]) -> None:
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()

    page_text = clean_text(soup.get_text(separator="\n"))
    data["raw_text"] = page_text
    # Keep line breaks so "Label: value" lookups stop at the end of their line.
    lines = (clean_text(line) for line in soup.get_text(separator="\n").splitlines())
    line_text = "\n".join(line for line in lines if line)

    if not data["description"]:
        main = soup.find("main") or soup.find("article") or soup.body
        if main:
            data["description"] = clean_text(main.get_text(separator="\n"))[:5000]

    if not data["department"]:
        data["department"] = extract_labeled_value(
            line_text, ["department", "team", "division", "business unit"]
        )

    if not data["benefits"]:
        data["benefits"] = extract_labeled_value(
            line_text, ["benefits", "perks", "what we offer", "compensation & benefits"]
        )

    if not data["skills"]:
        data["skills"] = extract_skills(page_text)

    emails = extract_emails(page_text)
    phones = extract_phones(page_text)

    if emails and not data["contact_email"]:
        data["contact_email"] = emails[0]
    if phones and not data["contact_whatsapp"]:
        data["contact_whatsapp"] = phones[0]

    if not data["contact_name"]:
        data["contact_name"] = extract_labeled_value(
            line_text, ["contact", "hiring manager", "recruiter", "contact person"]
        )

    if not data["work_mode"]:
        data["work_mode"] = infer_work_mode(page_text)


def scrape_job_from_html(html: str, page_url: str) -> dict[str, Any]:
    """Parse job fields from HTML without a network request (used by tests and scrape_job_url)."""
    soup = BeautifulSoup(html, "html.parser")
    data = empty_job_data(page_url)

    for block in parse_json_ld_blocks(soup):
        if is_job_posting(block):
            extract_from_json_ld(block, data)

    extract_from_meta(soup, data)
    extract_from_html(soup, data)

    apply_tags = soup.find_all("a", href=True)
    for tag in apply_tags:
        label = clean_text(tag.get_text()).lower()
        href = urljoin(page_url, tag["href"])
        if any(keyword in label for keyword in ("apply", "application", "careers")):
            data["apply_link"] = href
            break

    return data


def scrape_job_url(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    *,
    require_valid: bool = True,
) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")

    if require_valid:
        validation = probe_url(url, timeout=timeout)
        if not validation["valid"]:
            detail = validation["error"] or f"URL validation failed with status {validation['status_code']}"
            if validation.get("warnings"):
                detail = f"{detail} Warnings: {'; '.join(validation['warnings'])}"
            raise ValueError(detail)

    html, final_url, _meta = fetch_page(url, timeout=timeout)
    return scrape_job_from_html(html, final_url)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape job posting details from a URL and print JSON output."
    )
    parser.add_argument("url", help="Job posting URL to scrape")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Scrape without running validate_url first (not recommended)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )
    args = parser.parse_args()

    try:
        result = scrape_job_url(
            args.url,
            timeout=args.timeout,
            require_valid=not args.skip_validation,
        )
        indent = 2 if args.pretty else None
        print(json.dumps(result, indent=indent, ensure_ascii=False))
        return 0
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(json.dumps({"error": f"Failed to fetch URL: {exc}"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

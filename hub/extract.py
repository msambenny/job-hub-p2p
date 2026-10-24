"""Turn free-form WhatsApp text into structured job and seeker records.

Claude does the extraction when an API credential is available (the same
"restructure the alert" step the job-alert-validation skill asks Claude to do).
Without one, a "Label: value" parser handles structured posts, including posts
already formatted by format_whatsapp.py.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any, Protocol

log = logging.getLogger(__name__)

JOB_FIELDS = [
    "company_name", "job_title", "department", "job_type", "work_mode", "location",
    "salary_min", "salary_max", "benefits", "experience", "education", "description",
    "application_deadline", "posted_date", "contact_name", "contact_whatsapp",
    "contact_email", "apply_link", "custom_note",
]

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

# Signals the skill tells reviewers to call out. "hard" ones send a post to
# moderation even when the posting page matches.
SCAM_PATTERNS: list[tuple[str, str, bool]] = [
    (r"\b(registration|processing|training|security|joining)\s+(fee|charges?|deposit)\b", "Asks for a fee", True),
    (r"\brefundable\b", "Mentions a 'refundable' payment", True),
    (r"\b(upi|gpay|google pay|phonepe|paytm)\b", "Asks for payment via UPI/wallet", True),
    (r"\bpay\s+(rs\.?|inr|₹)?\s*\d+", "Asks the applicant to pay", True),
    (r"\b(limited seats|urgent hiring|hurry)\b", "High-pressure wording", False),
    (r"\bno interview\b", "Promises a job without interview", False),
]

PERSONAL_EMAIL = re.compile(r"@(gmail|yahoo|outlook|hotmail|rediffmail)\.", re.IGNORECASE)


class Extractor(Protocol):
    def extract_job(self, text: str) -> dict[str, Any]: ...
    def extract_profile(self, text: str) -> dict[str, Any]: ...


# ---------------------------------------------------------------- rule based --

LABELS = {
    "company": "company_name", "company name": "company_name", "employer": "company_name",
    "opportunity": "job_title", "role": "job_title", "position": "job_title",
    "title": "job_title", "job title": "job_title", "designation": "job_title",
    "department": "department", "team": "department",
    "type": "job_type", "job type": "job_type", "employment type": "job_type",
    "work mode": "work_mode", "mode": "work_mode",
    "location": "location", "place": "location", "city": "location",
    "salary": "salary", "ctc": "salary", "stipend": "salary", "package": "salary",
    "benefits": "benefits", "perks": "benefits",
    "experience": "experience", "exp": "experience",
    "education": "education", "qualification": "education", "eligibility": "education",
    "skills": "skills", "skill": "skills", "tech stack": "skills",
    "about": "description", "description": "description", "responsibilities": "description",
    "deadline": "application_deadline", "last date": "application_deadline",
    "apply by": "application_deadline",
    "posted": "posted_date", "posted on": "posted_date", "posted date": "posted_date",
    "contact": "contact", "hr": "contact_name", "contact name": "contact_name",
    "whatsapp": "contact_whatsapp", "email": "contact_email", "mail": "contact_email",
    "apply": "apply_link", "apply link": "apply_link", "link": "apply_link", "url": "apply_link",
    "note": "custom_note",
}

LINE_RE = re.compile(r"^[^\w*]*\*?\s*([A-Za-z][A-Za-z ]{1,24}?)\s*:?\*?\s*[:\-–]\s*(.+)$")


def _clean(value: str) -> str:
    return value.replace("*", "").strip()


def blank_job() -> dict[str, Any]:
    job: dict[str, Any] = {name: "" for name in JOB_FIELDS}
    job["skills"] = []
    return job


def parse_labeled_job(text: str) -> dict[str, Any]:
    job = blank_job()
    description: list[str] = []
    in_about = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            in_about = False
            continue
        if _clean(line).lower() in {"about", "who can apply"}:
            in_about = _clean(line).lower() == "about"
            continue
        match = LINE_RE.match(line)
        field = LABELS.get(_clean(match.group(1)).lower()) if match else None
        if not match or not field:
            if in_about:
                description.append(_clean(line))
            continue
        in_about = False
        value = _clean(match.group(2))
        if field == "salary":
            parts = re.split(r"\s+[–-]\s+|\s+to\s+", value, maxsplit=1)
            job["salary_min"] = parts[0].strip()
            job["salary_max"] = parts[1].strip() if len(parts) > 1 else parts[0].strip()
        elif field == "skills":
            job["skills"] = [item.strip() for item in re.split(r"[,/|;]", value) if item.strip()]
        elif field == "contact":
            for part in [item.strip() for item in re.split(r"·|\|", value) if item.strip()]:
                if "@" in part:
                    job["contact_email"] = part
                elif sum(ch.isdigit() for ch in part) >= 8:
                    job["contact_whatsapp"] = part
                else:
                    job["contact_name"] = part
        elif field == "apply_link":
            url = URL_RE.search(value)
            job["apply_link"] = url.group(0) if url else value
        else:
            job[field] = value
    if description and not job["description"]:
        job["description"] = " ".join(description)
    if not job["apply_link"]:
        url = URL_RE.search(text)
        if url:
            job["apply_link"] = url.group(0)
    return job


def looks_like_job_post(text: str) -> bool:
    job = parse_labeled_job(text)
    has_core = bool(job["company_name"] or job["job_title"])
    keywords = re.search(r"\b(hiring|vacancy|opening|job|role|position|internship|apply)\b", text, re.I)
    return bool(URL_RE.search(text) and (has_core or keywords)) or (has_core and bool(job["apply_link"]))


def parse_profile_text(text: str) -> dict[str, Any]:
    profile = {"skills": [], "desired_roles": [], "locations": [], "work_modes": [], "summary": text.strip()}
    for line in text.splitlines():
        if ":" not in line:
            continue
        label, value = (part.strip() for part in line.split(":", 1))
        items = [item.strip() for item in re.split(r"[,/|;]", value) if item.strip()]
        key = label.lower()
        if key in {"skills", "skill", "tech stack"}:
            profile["skills"] = items
        elif key in {"role", "roles", "looking for", "desired role", "desired roles"}:
            profile["desired_roles"] = items
        elif key in {"location", "locations", "city", "cities"}:
            profile["locations"] = items
        elif key in {"mode", "work mode", "work modes"}:
            profile["work_modes"] = items
    return profile


class RuleExtractor:
    """Deterministic fallback used without an API key and in tests."""

    def extract_job(self, text: str) -> dict[str, Any]:
        job = parse_labeled_job(text)
        job["scam_signals"] = []
        return job

    def extract_profile(self, text: str) -> dict[str, Any]:
        return parse_profile_text(text)


# ------------------------------------------------------------------- Claude --

JOB_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **{name: {"type": "string"} for name in JOB_FIELDS},
        "skills": {"type": "array", "items": {"type": "string"}},
        "is_job_post": {"type": "boolean"},
        "scam_signals": {"type": "array", "items": {"type": "string"}},
    },
    "required": [*JOB_FIELDS, "skills", "is_job_post", "scam_signals"],
    "additionalProperties": False,
}

PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "skills": {"type": "array", "items": {"type": "string"}},
        "desired_roles": {"type": "array", "items": {"type": "string"}},
        "locations": {"type": "array", "items": {"type": "string"}},
        "work_modes": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["skills", "desired_roles", "locations", "work_modes", "summary"],
    "additionalProperties": False,
}

JOB_SYSTEM = """You restructure job alerts that people send to a WhatsApp job hub.

Fill every field from the message. When the message does not give a field, use "" (or [] for lists). Never invent values: no guessed salaries, dates, links or contacts.
- job_type: Full-Time, Part-Time, Contract, Internship, ... ; work_mode: On-site, Hybrid or Remote.
- application_deadline and posted_date: ISO YYYY-MM-DD. If the message has no posted date, use {today}.
- apply_link: the URL applicants should use, exactly as written.
- is_job_post: false when the message is not a job or internship opening (a question, a greeting, a resume, ...).
- scam_signals: short phrases for anything an applicant should be warned about: registration or "refundable" fees, payment or UPI requests, salary unrealistic for the experience asked, a personal email domain for a large employer, pressure tactics. Leave it empty when there are none. An official exam fee on the employer's own domain is not a scam signal."""

PROFILE_SYSTEM = """You turn a job seeker's message or resume into a matching profile for a job alert service.
Use only what the text says. skills: concrete skills and tools. desired_roles: job titles they want or have held. locations: cities or regions they want to work in. work_modes: any of On-site, Hybrid, Remote that they accept. summary: two or three sentences describing their background and what they are looking for."""


class ClaudeExtractor:
    """Structured extraction with Claude, falling back to rules on any failure."""

    def __init__(self, model: str, client: Any | None = None, fallback: Extractor | None = None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.fallback = fallback or RuleExtractor()

    def _call(self, system: str, text: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        import anthropic

        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": text}],
                thinking={"type": "adaptive"},
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.APIConnectionError as exc:
            log.warning("Claude unreachable, using rule-based extraction: %s", exc)
            return None
        except anthropic.APIStatusError as exc:
            log.warning("Claude API error %s, using rule-based extraction: %s", exc.status_code, exc.message)
            return None
        if response.stop_reason in {"refusal", "max_tokens"}:
            log.warning("Claude stopped with %s, using rule-based extraction", response.stop_reason)
            return None
        body = next((block.text for block in response.content if block.type == "text"), "")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            log.warning("Claude returned non-JSON output, using rule-based extraction")
            return None

    def extract_job(self, text: str) -> dict[str, Any]:
        system = JOB_SYSTEM.format(today=date.today().isoformat())
        job = self._call(system, text, JOB_SCHEMA)
        if job is None:
            return self.fallback.extract_job(text)
        return job

    def extract_profile(self, text: str) -> dict[str, Any]:
        profile = self._call(PROFILE_SYSTEM, text, PROFILE_SCHEMA)
        if profile is None:
            return self.fallback.extract_profile(text)
        return profile


def make_extractor(model: str, use_llm: bool) -> Extractor:
    if not use_llm:
        return RuleExtractor()
    try:
        return ClaudeExtractor(model)
    except Exception as exc:  # missing package or credentials
        log.warning("Claude extraction disabled (%s); using rule-based extraction", exc)
        return RuleExtractor()


# ------------------------------------------------------------- scam signals --


def scam_signals(text: str, job: dict[str, Any]) -> tuple[list[str], bool]:
    """Return (signals, has_hard_signal) from rules plus any the extractor reported."""
    signals: list[str] = []
    hard = False
    for pattern, message, is_hard in SCAM_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            signals.append(message)
            hard = hard or is_hard
    email = str(job.get("contact_email") or "")
    if email and PERSONAL_EMAIL.search(email):
        signals.append(f"Contact uses a personal email domain ({email})")
    for item in job.get("scam_signals") or []:
        if item and item not in signals:
            signals.append(str(item))
            hard = True
    return signals, hard

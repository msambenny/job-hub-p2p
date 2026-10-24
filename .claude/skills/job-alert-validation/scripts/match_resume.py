#!/usr/bin/env python3
"""Match an uploaded resume against jobs in job_postings/, skipping posts older than 15 days."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from job_library import load_job_records, parse_posted_date, posting_freshness

MAX_AGE_DAYS = 15
MATCH_THRESHOLD = 0.35
STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "your", "you", "are",
    "was", "were", "have", "has", "had", "job", "role", "team", "work",
}


def read_resume(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("Install pypdf to parse PDF resumes: pip install pypdf") from exc
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    return path.read_text(encoding="utf-8", errors="ignore")


def tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+#.]{1,}", text.lower())
    return {word for word in words if word not in STOPWORDS and len(word) > 2}


def job_blob(job: dict[str, Any]) -> str:
    skills = job.get("skills") or []
    if isinstance(skills, list):
        skill_text = " ".join(str(item) for item in skills)
    else:
        skill_text = str(skills)
    parts = [
        job.get("job_title"),
        job.get("department"),
        job.get("education"),
        job.get("experience"),
        job.get("description"),
        skill_text,
    ]
    return " ".join(str(part) for part in parts if part)


def score_resume_against_job(resume_text: str, job: dict[str, Any]) -> dict[str, Any]:
    resume_tokens = tokenize(resume_text)
    job_tokens = tokenize(job_blob(job))
    overlap = sorted(resume_tokens & job_tokens)

    title = str(job.get("job_title") or "").strip()
    title_tokens = tokenize(title)
    title_score = (
        len(title_tokens & resume_tokens) / max(len(title_tokens), 1) if title_tokens else 0.0
    )

    skill_score = (len(overlap) / max(len(job_tokens), 1)) if job_tokens else 0.0
    score = round(min(1.0, (0.55 * skill_score) + (0.45 * title_score)), 2)
    applies = score >= MATCH_THRESHOLD or len(title_tokens & resume_tokens) >= 2

    return {
        "applies": applies,
        "score": score,
        "matched_terms": overlap[:20],
        "job_title": title,
        "company_name": job.get("company_name") or "",
    }


def normalize_filter(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def job_matches_filters(
    job: dict[str, Any],
    *,
    company: str | None,
    location: str | None,
    work_mode: str | None,
) -> bool:
    if company:
        needle = normalize_filter(company)
        if needle and needle not in normalize_filter(str(job.get("company_name") or "")):
            return False
    if location:
        needle = normalize_filter(location)
        haystack = normalize_filter(str(job.get("location") or ""))
        if needle and needle not in haystack:
            return False
    if work_mode:
        needle = normalize_filter(work_mode)
        if needle and needle not in normalize_filter(str(job.get("work_mode") or "")):
            return False
    return True


def match_resume(
    resume_path: Path,
    jobs_dir: Path,
    *,
    today: date | None = None,
    max_age_days: int = MAX_AGE_DAYS,
    min_score: float = MATCH_THRESHOLD,
    company: str | None = None,
    location: str | None = None,
    work_mode: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    resume_text = read_resume(resume_path)
    if not resume_text.strip():
        raise ValueError(f"No text extracted from resume: {resume_path}")

    matches: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for job in load_job_records(jobs_dir):
        freshness = posting_freshness(job, today, max_age_days)
        result = {
            "source_file": job.get("_source_file"),
            "company_name": job.get("company_name") or "",
            "job_title": job.get("job_title") or "",
            "location": job.get("location") or "",
            "work_mode": job.get("work_mode") or "",
            "apply_link": job.get("apply_link") or "",
            "freshness": freshness,
        }
        if not freshness["fresh"]:
            skipped.append(result)
            continue
        if not job_matches_filters(job, company=company, location=location, work_mode=work_mode):
            result["reason"] = "Job does not match the requested filters"
            skipped.append(result)
            continue
        scored = score_resume_against_job(resume_text, job)
        result.update(scored)
        if scored["score"] < min_score and not scored["applies"]:
            result["reason"] = f"Score {scored['score']} is below min_score {min_score}"
            skipped.append(result)
            continue
        if scored["applies"]:
            matches.append(result)
        else:
            result["reason"] = "Resume does not match this job well enough"
            skipped.append(result)

    matches.sort(key=lambda item: item.get("score", 0), reverse=True)
    if limit is not None and limit > 0:
        matches = matches[:limit]

    return {
        "resume": str(resume_path),
        "jobs_dir": str(jobs_dir),
        "today": today.isoformat(),
        "max_age_days": max_age_days,
        "min_score": min_score,
        "filters": {
            "company": company or "",
            "location": location or "",
            "work_mode": work_mode or "",
        },
        "match_count": len(matches),
        "matches": matches,
        "skipped": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find jobs in job_postings/ that fit a resume and were posted within 15 days."
    )
    parser.add_argument("resume", help="Path to resume (.pdf, .txt, .md)")
    parser.add_argument(
        "--jobs-dir",
        default=str(Path(__file__).resolve().parents[1] / "job_postings"),
        help="Folder of job JSON files (default: job_postings/)",
    )
    parser.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS)
    parser.add_argument("--min-score", type=float, default=MATCH_THRESHOLD)
    parser.add_argument("--company", help="Only match jobs whose company name contains this text")
    parser.add_argument("--location", help="Only match jobs whose location contains this text")
    parser.add_argument("--work-mode", help="Only match jobs with this work mode (Remote, Hybrid, etc.)")
    parser.add_argument("--limit", type=int, help="Return at most N matches after ranking by score")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    try:
        result = match_resume(
            Path(args.resume),
            Path(args.jobs_dir),
            max_age_days=args.max_age_days,
            min_score=args.min_score,
            company=args.company,
            location=args.location,
            work_mode=args.work_mode,
            limit=args.limit,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    indent = 2 if args.pretty else None
    print(json.dumps(result, indent=indent, ensure_ascii=False))
    return 0 if result["match_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

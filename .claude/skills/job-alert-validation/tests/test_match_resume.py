from __future__ import annotations

from datetime import date

import pytest

from job_library import parse_posted_date, posting_freshness
from match_resume import score_resume_against_job


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-03-20", date(2026, 3, 20)),
        ("20-03-2026", date(2026, 3, 20)),
        ("Posted on 2026-03-20T10:00:00Z", date(2026, 3, 20)),
        ("", None),
        ("not-a-date", None),
    ],
)
def test_parse_posted_date(raw: str, expected: date | None) -> None:
    assert parse_posted_date(raw) == expected


def test_posting_freshness_within_window() -> None:
    job = {"posted_date": "2026-03-25"}
    result = posting_freshness(job, today=date(2026, 4, 5), max_age_days=15)

    assert result["fresh"] is True
    assert result["age_days"] == 11


def test_posting_freshness_rejects_missing_date() -> None:
    result = posting_freshness({}, today=date(2026, 4, 5), max_age_days=15)

    assert result["fresh"] is False
    assert "Missing posted_date" in result["reason"]


def test_posting_freshness_rejects_stale_posting() -> None:
    job = {"posted_date": "2026-02-01"}
    result = posting_freshness(job, today=date(2026, 4, 5), max_age_days=15)

    assert result["fresh"] is False
    assert result["age_days"] > 15


def test_score_resume_against_job_finds_overlap() -> None:
    resume = "Python AWS PostgreSQL backend engineer Bengaluru"
    job = {
        "job_title": "Backend Engineer",
        "skills": ["Python", "AWS", "PostgreSQL"],
        "description": "Build APIs with Python on AWS.",
        "education": "B.Tech",
        "experience": "2 years",
        "department": "Platform",
    }

    scored = score_resume_against_job(resume, job)

    assert scored["applies"] is True
    assert scored["score"] >= 0.35
    assert "python" in scored["matched_terms"]

from __future__ import annotations

import json
from pathlib import Path

from job_library import (
    apply_domain_warnings,
    build_trust_report,
    find_duplicate_postings,
    host_matches_company,
    summarize_library,
)


def test_host_matches_company_acme() -> None:
    assert host_matches_company("careers.acmelabs.example", "Acme Labs") is True


def test_domain_mismatch_flags_unknown_host() -> None:
    warnings = apply_domain_warnings(
        "Acme Labs",
        "https://totally-unrelated-scam.example/jobs/1",
    )
    codes = {item["code"] for item in warnings}
    assert "company_domain_mismatch" in codes


def test_find_duplicate_by_apply_url(tmp_path: Path) -> None:
    job = {
        "company_name": "Acme Labs",
        "job_title": "Backend Engineer",
        "apply_link": "https://careers.acmelabs.example/jobs/backend-engineer",
        "description": "Build APIs",
    }
    existing = tmp_path / "acme_backend_2026-03-20.json"
    existing.write_text(json.dumps(job), encoding="utf-8")

    duplicates = find_duplicate_postings(job, tmp_path)
    assert any(item["reason"] == "same_apply_url" for item in duplicates)


def test_trust_report_includes_duplicate_warning(tmp_path: Path) -> None:
    job = {
        "company_name": "Acme Labs",
        "job_title": "Backend Engineer",
        "apply_link": "https://careers.acmelabs.example/jobs/backend-engineer",
        "posted_date": "2026-03-20",
    }
    (tmp_path / "existing.json").write_text(json.dumps(job), encoding="utf-8")

    report = build_trust_report(job, jobs_dir=tmp_path)
    assert report["has_warnings"] is True
    assert any("duplicate" in message.lower() for message in report["warnings"])


def test_summarize_library_counts_fresh_and_stale(tmp_path: Path) -> None:
    fresh = {
        "company_name": "Acme",
        "job_title": "Engineer",
        "posted_date": "2026-10-15",
    }
    stale = {
        "company_name": "Old Co",
        "job_title": "Analyst",
        "posted_date": "2026-01-01",
    }
    (tmp_path / "fresh.json").write_text(json.dumps(fresh), encoding="utf-8")
    (tmp_path / "stale.json").write_text(json.dumps(stale), encoding="utf-8")

    summary = summarize_library(tmp_path, today=__import__("datetime").date(2026, 10, 21))
    assert summary["count"] == 2
    assert summary["fresh_count"] == 1
    assert summary["stale_count"] == 1

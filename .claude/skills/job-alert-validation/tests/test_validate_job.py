from __future__ import annotations

import json
from pathlib import Path

from validate_job import run_job_validation, run_job_validation_from_html

FIXTURES = Path(__file__).resolve().parent / "fixtures"
JOBS = FIXTURES / "jobs"
PAGES = FIXTURES / "pages"


def _load_job(name: str) -> dict:
    return json.loads((JOBS / name).read_text(encoding="utf-8"))


def test_offline_validation_with_scraped_fixture_matches() -> None:
    user = _load_job("user_job.json")
    scraped = _load_job("scraped_job.json")

    result, code = run_job_validation(user, scraped_job=scraped)

    assert code == 0
    assert result["comparison"]["matches"] is True


def test_offline_html_fixture_runs_comparison() -> None:
    user = _load_job("user_job.json")
    html = (PAGES / "json_ld_job.html").read_text(encoding="utf-8")
    page_url = user["apply_link"]

    result, _code = run_job_validation_from_html(user, html, page_url)

    assert result["scraped"]["company_name"] == "Acme Labs"
    assert result["scraped"]["job_title"] == "Backend Engineer"
    assert result["comparison"] is not None
    assert result["comparison"]["summary"]["total_compared"] > 0


def test_trust_report_present_on_offline_run() -> None:
    user = _load_job("user_job.json")
    scraped = _load_job("scraped_job.json")

    result, _code = run_job_validation(user, scraped_job=scraped)

    assert result["trust"] is not None
    assert "warnings" in result["trust"]


def test_save_when_fixture_scraped_json_matches(tmp_path: Path) -> None:
    user = _load_job("user_job.json")
    scraped = _load_job("scraped_job.json")

    result, code = run_job_validation(
        user,
        scraped_job=scraped,
        save=True,
        jobs_dir=tmp_path,
    )

    assert code == 0
    assert result["saved_path"] is not None
    assert Path(result["saved_path"]).exists()


def test_critical_mismatch_returns_exit_code_one() -> None:
    user = _load_job("user_job.json")
    scraped = _load_job("scraped_job.json")
    user["company_name"] = "Wrong Company"

    result, code = run_job_validation(user, scraped_job=scraped)

    assert code == 1
    assert result["comparison"]["matches"] is False


def test_missing_url_without_scraped_returns_code_two() -> None:
    user = {"job_title": "Engineer"}

    result, code = run_job_validation(user)

    assert code == 2
    assert "error" in result

from __future__ import annotations

import json
from pathlib import Path

import pytest

from save_job import default_filename, save_job_record


def test_default_filename_uses_company_title_and_date() -> None:
    job = {
        "company_name": "Acme Labs",
        "job_title": "Backend Engineer",
        "posted_date": "2026-03-20",
    }
    assert default_filename(job) == "acme-labs_backend-engineer_2026-03-20.json"


def test_save_job_record_writes_json(tmp_path: Path) -> None:
    job = json.loads(
        (Path(__file__).parent / "fixtures" / "jobs" / "user_job.json").read_text(encoding="utf-8")
    )
    path = save_job_record(job, tmp_path, filename="acme-test.json")
    assert path.exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["company_name"] == "Acme Labs"


def test_save_job_record_refuses_overwrite(tmp_path: Path) -> None:
    job = {"company_name": "Acme", "job_title": "Engineer", "posted_date": "2026-01-01"}
    save_job_record(job, tmp_path, filename="dup.json")
    with pytest.raises(FileExistsError):
        save_job_record(job, tmp_path, filename="dup.json")

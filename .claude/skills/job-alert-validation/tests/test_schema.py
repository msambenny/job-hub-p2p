from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "job_schema.json"


@pytest.fixture(scope="session")
def job_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_fixture_jobs_validate_against_schema(job_schema: dict) -> None:
    jobs_dir = FIXTURES / "jobs"
    for path in jobs_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.validate(instance=payload, schema=job_schema)


def test_schema_covers_scraper_empty_job_fields(job_schema: dict) -> None:
    from web_scraping import empty_job_data

    sample = empty_job_data("https://example.com/job")
    for key in sample:
        assert key in job_schema["properties"], f"Schema missing field: {key}"

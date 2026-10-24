from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

from web_scraping import (
    empty_job_data,
    extract_from_html,
    extract_from_meta,
    extract_labeled_value,
    is_job_posting,
    parse_json_ld_blocks,
    scrape_job_from_html,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pages"


def scrape_fixture(name: str) -> dict:
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return scrape_job_from_html(html, "https://example.com/jobs/1")


def test_json_ld_fixture_extracts_core_fields() -> None:
    data = scrape_fixture("json_ld_job.html")

    assert data["job_title"] == "Backend Engineer"
    assert data["company_name"] == "Acme Labs"
    assert data["posted_date"] == "2026-03-20"
    assert data["application_deadline"] == "2026-04-30"
    assert "Bengaluru" in data["location"]
    assert data["job_type"] == "Full-Time"
    assert data["work_mode"] == "Remote"
    assert "Python" in data["skills"]


def test_meta_only_fixture_uses_open_graph() -> None:
    data = scrape_fixture("meta_only.html")

    assert data["job_title"] == "Product Manager"
    assert data["company_name"] == "Contoso Careers"
    assert "B2B SaaS" in data["description"]


def test_messy_html_extracts_labeled_fields() -> None:
    page_text = """
Department: Revenue Operations
Benefits: Stock options, flexible hours
Skills: Excel, Salesforce, communication
Contact: Alex Morgan
"""
    assert extract_labeled_value(page_text, ["department"]) == "Revenue Operations"
    assert extract_labeled_value(page_text, ["benefits"]) == "Stock options, flexible hours"
    assert extract_labeled_value(page_text, ["contact"]) == "Alex Morgan"

    html = (FIXTURES / "messy_html.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    data = empty_job_data("https://example.com/job")
    extract_from_html(soup, data)

    assert data["description"] or data["raw_text"]


def test_is_job_posting_detects_schema_type() -> None:
    html = (FIXTURES / "json_ld_job.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    blocks = parse_json_ld_blocks(soup)

    assert any(is_job_posting(block) for block in blocks)


def test_extract_from_meta_does_not_overwrite_existing_title() -> None:
    html = (FIXTURES / "meta_only.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    data = empty_job_data("https://example.com/job")
    data["job_title"] = "Existing Title"

    extract_from_meta(soup, data)

    assert data["job_title"] == "Existing Title"


def test_json_ld_numeric_salary_is_extracted() -> None:
    html = (FIXTURES / "json_ld_job.html").read_text(encoding="utf-8")
    data = scrape_job_from_html(html, "https://careers.acmelabs.example/jobs/backend-engineer")

    assert data["salary_min"] == "1200000 INR per year"
    assert data["salary_max"] == "1800000 INR per year"


def test_labeled_values_stop_at_line_end() -> None:
    html = "<html><body><p>Department: Platform</p><p>Benefits: Health insurance</p></body></html>"
    data = scrape_job_from_html(html, "https://example.com/jobs/1")

    assert data["department"] == "Platform"
    assert data["benefits"] == "Health insurance"

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from hub import reuse
from hub.channels import Inbound, RecordingChannel, parse_whatsapp_webhook, verify_signature
from hub.config import Settings
from hub.extract import RuleExtractor, parse_labeled_job, scam_signals
from hub.store import NEEDS_LINK, PENDING_REVIEW, PUBLISHED, REJECTED, Store
from hub.workflow import JobHub

from web_scraping import scrape_job_from_html  # vendored script, on sys.path via hub.reuse

DEMO = reuse.SKILL_ROOT / "demo"
TODAY = date.today()
ADMIN = "910000000009"
POSTER = "910000000001"
SEEKER = "910000000002"

GENUINE_URL = "https://careers.acmelabs.example/jobs/backend-engineer"
SUSPICIOUS_URL = "https://forms.gle/AbCdEf123456"

GENUINE_POST = f"""🏢 *Company:* Acme Labs
📋 *Opportunity:* Backend Engineer
🗂 *Department:* Platform
💼 *Type:* Full-Time
🏠 *Work mode:* Hybrid
📍 *Location:* Bengaluru, India
🎓 *Education:* B.Tech or equivalent
🧑‍💻 *Experience:* 2+ years Python
🛠 *Skills:* Python, AWS, PostgreSQL
*About*
Build APIs and backend services for our data platform.

💰 *Salary:* 12 LPA – 18 LPA
🔗 *Apply:* {GENUINE_URL}"""

SUSPICIOUS_POST = f"""Company: Google
Role: Software Engineer
Location: Bengaluru, India
Mode: Remote
Skills: Python, Java
Urgent hiring! Pay refundable registration fee of Rs 499 to confirm your slot.
Apply: {SUSPICIOUS_URL}"""


def render(text: str) -> str:
    return text.replace("{{POSTED}}", (TODAY - timedelta(days=2)).isoformat()).replace(
        "{{DEADLINE}}", (TODAY + timedelta(days=30)).isoformat()
    )


PAGES = {
    GENUINE_URL: render((DEMO / "pages" / "genuine_page.html").read_text(encoding="utf-8")),
    SUSPICIOUS_URL: render((DEMO / "pages" / "suspicious_page.html").read_text(encoding="utf-8")),
}


@pytest.fixture
def hub(tmp_path: Path) -> JobHub:
    settings = Settings(data_dir=tmp_path, admins=[ADMIN], use_llm=False)

    def offline_validator(record):
        url = record.get("apply_link")
        if not url:
            return reuse.run_job_validation(record, jobs_dir=settings.jobs_dir)
        if url not in PAGES:
            return {"url_validation": {"valid": False, "status_code": 404}, "trust": None}, 3
        scraped = scrape_job_from_html(PAGES[url], url)
        return reuse.run_job_validation(
            record, url=url, scraped_job=scraped, jobs_dir=settings.jobs_dir, include_whatsapp=True
        )

    return JobHub(settings, Store(":memory:"), RecordingChannel(), RuleExtractor(), offline_validator)


def send(hub: JobHub, sender: str, text: str = "", **extra) -> list[dict]:
    channel: RecordingChannel = hub.channel  # type: ignore[assignment]
    start = len(channel.sent)
    hub.handle(Inbound(f"m{len(channel.sent)}-{sender}-{hash(text)}", sender, text=text, **extra))
    return channel.sent[start:]


def bodies(messages: list[dict], to: str) -> str:
    return "\n".join(item.get("body", "") for item in messages if item["to"] == to)


def subscribe_seeker(hub: JobHub) -> None:
    send(hub, SEEKER, "JOIN")
    send(
        hub,
        SEEKER,
        (reuse.SKILL_ROOT / "examples" / "backend_resume.txt").read_text(encoding="utf-8")
        + "\nLocation: Bengaluru\nMode: Hybrid, Remote",
    )


def test_genuine_post_is_published_and_matching_seeker_is_notified(hub: JobHub) -> None:
    subscribe_seeker(hub)
    out = send(hub, POSTER, "POST " + GENUINE_POST)

    [job] = hub.store.jobs()
    assert job["status"] == PUBLISHED, job["validation"]
    assert Path(job["path"]).is_file()
    assert "is verified and live. 1 matching" in bodies(out, POSTER)
    alert = bodies(out, SEEKER)
    assert "New job for you" in alert and "*Company:* Acme Labs" in alert
    assert hub.store.was_notified(SEEKER, job["id"])


def test_seeker_joining_later_gets_backfilled(hub: JobHub) -> None:
    send(hub, POSTER, "POST " + GENUINE_POST)
    send(hub, SEEKER, "JOIN")
    out = send(hub, SEEKER, "Skills: Python, AWS, PostgreSQL\nRoles: Backend Engineer\nLocation: Bengaluru")
    assert "Backend Engineer" in bodies(out, SEEKER)
    assert "New job for you" in bodies(out, SEEKER)


def test_seeker_filtered_by_location_is_not_notified(hub: JobHub) -> None:
    send(hub, SEEKER, "JOIN Skills: Python, AWS\nRoles: Backend Engineer\nLocation: Chennai\nMode: On-site")
    out = send(hub, POSTER, "POST " + GENUINE_POST)
    assert not bodies(out, SEEKER)


def test_suspicious_post_goes_to_moderator_and_can_be_rejected(hub: JobHub) -> None:
    subscribe_seeker(hub)
    out = send(hub, POSTER, "POST " + SUSPICIOUS_POST)

    [job] = hub.store.jobs()
    # The demo page disagrees on critical fields, so it is rejected outright.
    assert job["status"] == REJECTED
    assert "does not match" in bodies(out, POSTER)
    assert not bodies(out, SEEKER)


def test_scam_signals_hold_a_matching_post_for_review(hub: JobHub) -> None:
    subscribe_seeker(hub)
    post = GENUINE_POST + "\n📝 *Note:* Pay refundable registration fee of Rs 499 via UPI"
    out = send(hub, POSTER, "POST " + post)

    [job] = hub.store.jobs()
    assert job["status"] == PENDING_REVIEW
    assert "Review job" in bodies(out, ADMIN)
    assert not bodies(out, SEEKER)

    out = send(hub, ADMIN, f"APPROVE {job['id']}")
    assert hub.store.get_job(job["id"])["status"] == PUBLISHED
    assert "New job for you" in bodies(out, SEEKER)


def test_moderator_reject_notifies_poster(hub: JobHub) -> None:
    send(hub, POSTER, "POST " + GENUINE_POST + "\nNote: registration fee applies")
    [job] = hub.store.jobs()
    out = send(hub, ADMIN, f"REJECT {job['id']} Fee-based posting")
    assert hub.store.get_job(job["id"])["status"] == REJECTED
    assert "Fee-based posting" in bodies(out, POSTER)


def test_non_admin_cannot_approve(hub: JobHub) -> None:
    out = send(hub, POSTER, "APPROVE 1")
    assert "Welcome to Job Hub" in bodies(out, POSTER)


def test_missing_link_is_requested_then_resumed(hub: JobHub) -> None:
    post = GENUINE_POST.replace(f"🔗 *Apply:* {GENUINE_URL}", "")
    out = send(hub, POSTER, "POST " + post)
    [job] = hub.store.jobs()
    assert job["status"] == NEEDS_LINK
    assert "Reply with the apply link" in bodies(out, POSTER)

    send(hub, POSTER, GENUINE_URL)
    assert hub.store.get_job(job["id"])["status"] == PUBLISHED


def test_broken_link_is_rejected(hub: JobHub) -> None:
    send(hub, POSTER, "POST " + GENUINE_POST.replace(GENUINE_URL, "https://careers.acmelabs.example/gone"))
    [job] = hub.store.jobs()
    assert job["status"] == REJECTED
    assert "HTTP status: 404" in job["reason"]


def test_unreachable_site_is_held_as_unverified(hub: JobHub) -> None:
    hub.validator = lambda record: ({"url_validation": {"valid": False, "status_code": None, "error": "timeout"}}, 3)
    send(hub, POSTER, "POST " + GENUINE_POST)
    [job] = hub.store.jobs()
    assert job["status"] == PENDING_REVIEW
    assert "unverified" in job["reason"]


def test_same_job_posted_twice_is_rejected(hub: JobHub) -> None:
    send(hub, POSTER, "POST " + GENUINE_POST)
    send(hub, "910000000003", "POST " + GENUINE_POST)
    statuses = sorted(job["status"] for job in hub.store.jobs())
    assert statuses == [PUBLISHED, REJECTED]


def test_stop_unsubscribes(hub: JobHub) -> None:
    subscribe_seeker(hub)
    send(hub, SEEKER, "STOP")
    out = send(hub, POSTER, "POST " + GENUINE_POST)
    assert not bodies(out, SEEKER)


def test_template_used_outside_service_window(hub: JobHub) -> None:
    subscribe_seeker(hub)
    hub.store.update_user(SEEKER, last_inbound_at="2000-01-01T00:00:00+00:00")
    out = send(hub, POSTER, "POST " + GENUINE_POST)
    [alert] = [item for item in out if item["to"] == SEEKER]
    assert alert["type"] == "template"
    assert alert["params"] == ["Backend Engineer", "Acme Labs", GENUINE_URL]


def test_alerts_are_rate_limited(hub: JobHub) -> None:
    hub.settings.max_alerts_per_day = 0
    subscribe_seeker(hub)
    out = send(hub, POSTER, "POST " + GENUINE_POST)
    assert not bodies(out, SEEKER)


def test_duplicate_webhook_delivery_is_ignored(hub: JobHub) -> None:
    hub.handle(Inbound("same-id", POSTER, text="HELP"))
    hub.handle(Inbound("same-id", POSTER, text="HELP"))
    assert len(hub.channel.sent) == 1  # type: ignore[attr-defined]


def test_resume_upload(hub: JobHub) -> None:
    send(hub, POSTER, "POST " + GENUINE_POST)
    channel: RecordingChannel = hub.channel  # type: ignore[assignment]
    channel.media["media-1"] = (reuse.SKILL_ROOT / "examples" / "backend_resume.txt").read_bytes()
    out = send(hub, SEEKER, media_id="media-1", filename="resume.txt", mime_type="text/plain")
    assert "You're subscribed" in bodies(out, SEEKER)
    assert "New job for you" in bodies(out, SEEKER)


def test_expire_stale(hub: JobHub) -> None:
    send(hub, POSTER, "POST " + GENUINE_POST)
    [job] = hub.store.jobs()
    hub.today = lambda: TODAY + timedelta(days=30)
    assert hub.expire_stale() == [job["id"]]
    assert not Path(job["path"]).exists()


def test_parse_labeled_job_reads_whatsapp_format() -> None:
    job = parse_labeled_job(GENUINE_POST)
    assert job["company_name"] == "Acme Labs"
    assert job["job_title"] == "Backend Engineer"
    assert job["skills"] == ["Python", "AWS", "PostgreSQL"]
    assert job["salary_min"] == "12 LPA" and job["salary_max"] == "18 LPA"
    assert job["apply_link"] == GENUINE_URL
    assert job["description"].startswith("Build APIs")


def test_scam_signals() -> None:
    signals, hard = scam_signals(SUSPICIOUS_POST, {"contact_email": "hr.google@gmail.com"})
    assert hard
    assert any("fee" in item.lower() for item in signals)
    assert any("personal email" in item for item in signals)
    assert scam_signals(GENUINE_POST, {}) == ([], False)


def test_parse_whatsapp_webhook_and_signature() -> None:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": SEEKER, "profile": {"name": "Asha"}}],
                            "messages": [
                                {"from": SEEKER, "id": "wamid.1", "type": "text", "text": {"body": "JOIN"}},
                                {
                                    "from": SEEKER,
                                    "id": "wamid.2",
                                    "type": "document",
                                    "document": {"id": "m1", "filename": "cv.pdf", "mime_type": "application/pdf"},
                                },
                            ],
                        }
                    }
                ]
            }
        ]
    }
    first, second = parse_whatsapp_webhook(payload)
    assert (first.text, first.name) == ("JOIN", "Asha")
    assert (second.media_id, second.filename) == ("m1", "cv.pdf")
    assert parse_whatsapp_webhook({"entry": [{"changes": [{"value": {"statuses": [{}]}}]}]}) == []

    body = json.dumps(payload).encode()
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature("secret", body, good)
    assert not verify_signature("secret", body, "sha256=bad")
    assert not verify_signature("secret", body, None)


class _Block:
    def __init__(self, text: str):
        self.type, self.text = "text", text


class _FakeMessages:
    def __init__(self, stop_reason: str, text: str):
        self.stop_reason, self.text, self.calls = stop_reason, text, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("Resp", (), {"stop_reason": self.stop_reason, "content": [_Block(self.text)]})()


def _fake_client(stop_reason: str, text: str):
    messages = _FakeMessages(stop_reason, text)
    return type("Client", (), {"beta": type("Beta", (), {"messages": messages})()})(), messages


def test_claude_extractor_uses_structured_output_and_falls_back_on_refusal() -> None:
    from hub.extract import ClaudeExtractor

    payload = {**parse_labeled_job(GENUINE_POST), "is_job_post": True, "scam_signals": []}
    client, messages = _fake_client("end_turn", json.dumps(payload))
    job = ClaudeExtractor("claude-opus-5-5", client=client).extract_job(GENUINE_POST)
    assert job["company_name"] == "Acme Labs"
    call = messages.calls[0]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default"

    client, _ = _fake_client("refusal", "")
    job = ClaudeExtractor("claude-opus-5-5", client=client).extract_job(GENUINE_POST)
    assert job["company_name"] == "Acme Labs"  # rule-based fallback

"""The job hub agent: routes WhatsApp messages and runs the poster, seeker,
moderation and matching workflows on top of the job-alert-validation scripts.

    poster ──► extract (Claude) ──► validate_job (URL, scrape, compare, trust)
                                        │
             ┌──────────────┬──────────┼───────────────┬───────────────┐
         no link        mismatch    unreachable /     verified        verified
         ask poster      reject     scam / warnings    clean ─► publish ─► notify matching seekers
                                    ─► moderators ─► APPROVE / REJECT

    seeker ──► resume or profile text ──► extract profile (Claude) ──► subscribe ──► backfill matches
"""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from . import reuse
from .channels import Channel, Inbound
from .config import Settings, normalize_phone
from .extract import URL_RE, Extractor, looks_like_job_post, scam_signals
from .store import (
    EXPIRED,
    NEEDS_LINK,
    PENDING_REVIEW,
    PUBLISHED,
    REJECTED,
    Store,
    within_service_window,
)

log = logging.getLogger(__name__)

Validator = Callable[[dict[str, Any]], tuple[dict[str, Any], int]]

HELP = """👋 *Welcome to Job Hub*

*Have a job to share?*
Send *POST* followed by the job details and apply link. Every post is checked against the employer's page before it goes out.

*Looking for a job?*
Send *JOIN*, then upload your resume (PDF) or describe yourself, for example:
Skills: Python, AWS
Roles: Backend Engineer
Location: Bengaluru
Mode: Hybrid, Remote

*Other commands*
*JOBS* – current matches for you
*MYPOSTS* – status of jobs you posted
*STOP* – stop job alerts
*HELP* – this menu"""

ADMIN_HELP = """
*Moderator commands*
*PENDING* – posts waiting for review
*APPROVE <id>* – publish a post
*REJECT <id> <reason>* – reject a post"""

COMMAND_RE = re.compile(r"^\s*([A-Za-z]+)\b[ \t]*[:\-]?\s*(.*)$", re.DOTALL)


@dataclass
class Decision:
    status: str
    reason: str = ""


class JobHub:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        channel: Channel,
        extractor: Extractor,
        validator: Validator | None = None,
        today: Callable[[], date] = date.today,
    ):
        self.settings = settings
        self.store = store
        self.channel = channel
        self.extractor = extractor
        self.validator = validator or self._validate_online
        self.today = today
        settings.jobs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ entrypoint --

    def handle(self, message: Inbound) -> None:
        if message.message_id and not self.store.mark_processed(message.message_id):
            return
        sender = normalize_phone(message.sender)
        user = self.store.touch_user(sender, message.name)

        if message.media_id:
            self._handle_resume(user, message)
            return

        text = message.text.strip()
        if not text:
            self.reply(sender, "I can read text messages and PDF resumes. Send *HELP* to see what I can do.")
            return

        match = COMMAND_RE.match(text)
        command = match.group(1).upper() if match else ""
        rest = match.group(2).strip() if match else ""
        is_admin = sender in self.settings.admins

        handlers: dict[str, Callable[[], None]] = {
            "HELP": lambda: self._help(sender, is_admin),
            "HI": lambda: self._help(sender, is_admin),
            "HELLO": lambda: self._help(sender, is_admin),
            "MENU": lambda: self._help(sender, is_admin),
            "POST": lambda: self._start_post(user, rest),
            "JOIN": lambda: self._join(user, rest),
            "START": lambda: self._join(user, rest),
            "SUBSCRIBE": lambda: self._join(user, rest),
            "PROFILE": lambda: self._set_profile(user, rest) if rest else self._join(user, ""),
            "STOP": lambda: self._stop(user),
            "UNSUBSCRIBE": lambda: self._stop(user),
            "JOBS": lambda: self._list_matches(user),
            "MYPOSTS": lambda: self._my_posts(user),
        }
        if is_admin:
            handlers.update(
                {
                    "PENDING": lambda: self._pending(sender),
                    "APPROVE": lambda: self._approve(sender, rest),
                    "REJECT": lambda: self._reject(sender, rest),
                }
            )
        if command in handlers:
            handlers[command]()
            return

        state = user["state"]
        if state == "awaiting_post":
            self.store.set_state(sender)
            self._process_post(sender, text)
        elif state == "awaiting_link" and (link := URL_RE.search(text)) and len(text) - len(link.group(0)) < 40:
            self._resume_with_link(user, link.group(0))
        elif state == "awaiting_profile":
            self._set_profile(user, text)
        elif looks_like_job_post(text):
            self._process_post(sender, text)
        else:
            self._help(sender, is_admin)

    def reply(self, to: str, body: str) -> None:
        self.channel.send_text(to, body)

    # --------------------------------------------------------------- posters --

    def _help(self, sender: str, is_admin: bool) -> None:
        self.reply(sender, HELP + (ADMIN_HELP if is_admin else ""))

    def _start_post(self, user: dict[str, Any], rest: str) -> None:
        if rest:
            self.store.set_state(user["phone"])
            self._process_post(user["phone"], rest)
            return
        self.store.set_state(user["phone"], "awaiting_post")
        self.reply(user["phone"], "Send the job details in your next message, including the apply link.")

    def _process_post(self, poster: str, text: str) -> None:
        self.reply(poster, "🔎 Checking this job against the employer's page…")
        record = self.extractor.extract_job(text)
        if record.pop("is_job_post", True) is False:
            self.reply(poster, "That doesn't look like a job opening. Send *HELP* to see what I can do.")
            return
        signals, hard_signal = scam_signals(text, record)
        record.pop("scam_signals", None)
        record = self._complete_record(record, text)
        job_id = self.store.add_job(poster, record, NEEDS_LINK, validation={"scam_signals": signals})
        self._validate_and_route(job_id, signals, hard_signal)

    def _complete_record(self, record: dict[str, Any], text: str) -> dict[str, Any]:
        record = {key: value for key, value in record.items() if not key.startswith("_")}
        record["skills"] = [str(item) for item in record.get("skills") or []]
        for key, value in list(record.items()):
            if key != "skills":
                record[key] = "" if value is None else str(value)
        if not reuse.parse_posted_date(record.get("posted_date")):
            record["posted_date"] = self.today().isoformat()
        record["raw_text"] = text
        return record

    def _resume_with_link(self, user: dict[str, Any], url: str) -> None:
        job_id = int(user["state_data"].get("job_id", 0))
        job = self.store.get_job(job_id)
        self.store.set_state(user["phone"])
        if not job or job["status"] != NEEDS_LINK:
            self.reply(user["phone"], "I couldn't find the post waiting for a link. Please send it again with *POST*.")
            return
        record = dict(job["record"], apply_link=url)
        self.store.update_job(job_id, record=record)
        signals, hard = scam_signals(record.get("raw_text", ""), record)
        self.reply(user["phone"], "🔎 Thanks, checking the link…")
        self._validate_and_route(job_id, signals, hard)

    def _validate_online(self, record: dict[str, Any]) -> tuple[dict[str, Any], int]:
        return reuse.run_job_validation(
            record,
            timeout=self.settings.validation_timeout,
            jobs_dir=self.settings.jobs_dir,
            include_whatsapp=True,
        )

    def _validate_and_route(self, job_id: int, signals: list[str], hard_signal: bool) -> None:
        job = self.store.get_job(job_id)
        assert job is not None
        record = job["record"]
        result, code = self.validator(record)
        validation = {
            "exit_code": code,
            "scam_signals": signals,
            "url_validation": result.get("url_validation"),
            "comparison_summary": (result.get("comparison") or {}).get("summary"),
            "trust": result.get("trust"),
            "error": result.get("error", ""),
        }
        if result.get("url_validation") and result["url_validation"].get("final_url"):
            record = dict(record, source_url=record.get("source_url") or result["url_validation"]["final_url"])
        self.store.update_job(job_id, record=record, validation=validation)

        decision = self._decide(result, code, signals, hard_signal)
        poster = job["poster"]
        if decision.status == NEEDS_LINK:
            self.store.set_state(poster, "awaiting_link", {"job_id": job_id})
            self.store.update_job(job_id, status=NEEDS_LINK, reason=decision.reason)
            self.reply(poster, f"📎 Job #{job_id}: {decision.reason} Reply with the apply link.")
        elif decision.status == REJECTED:
            self.store.update_job(job_id, status=REJECTED, reason=decision.reason)
            self.reply(poster, f"❌ Job #{job_id} was not shared.\n{decision.reason}")
        elif decision.status == PENDING_REVIEW:
            self.store.update_job(job_id, status=PENDING_REVIEW, reason=decision.reason)
            self.reply(
                poster,
                f"⏳ Job #{job_id} needs a quick review by a moderator before it is shared.\n{decision.reason}",
            )
            self._notify_moderators(job_id)
        else:
            self._publish(job_id)

    def _decide(self, result: dict[str, Any], code: int, signals: list[str], hard_signal: bool) -> Decision:
        if code == 2:
            return Decision(NEEDS_LINK, "The post has no apply link.")
        if code == 3:
            check = result.get("url_validation") or {}
            if check.get("status_code"):
                return Decision(
                    REJECTED,
                    f"The job link appears invalid (HTTP status: {check['status_code']}). "
                    "Please send a working link from the employer's site.",
                )
            return Decision(
                PENDING_REVIEW,
                "The employer's site could not be reached to verify it, so the post is *unverified*.",
            )
        if code == 1:
            summary = (result.get("comparison") or {}).get("summary") or {}
            critical = summary.get("critical_mismatches") or []
            fields = [
                item["field"]
                for item in (result.get("comparison") or {}).get("fields", [])
                if item.get("status") in {"mismatch", "missing_on_site"}
            ]
            detail = ", ".join(critical or fields) or "several fields"
            return Decision(REJECTED, f"The post does not match the employer's page ({detail}).")

        trust = result.get("trust") or {}
        same_url = [item for item in trust.get("duplicates") or [] if item.get("reason") == "same_apply_url"]
        if same_url:
            return Decision(REJECTED, "This job has already been shared on the hub.")
        reasons: list[str] = []
        if hard_signal:
            reasons.append("Possible scam signals: " + "; ".join(signals) + ".")
        reasons.extend(trust.get("warnings") or [])
        if reasons:
            return Decision(PENDING_REVIEW, "\n".join(f"⚠ {item}" for item in reasons))
        return Decision(PUBLISHED)

    def _notify_moderators(self, job_id: int, recipients: list[str] | None = None) -> None:
        job = self.store.get_job(job_id)
        if not job:
            return
        record = job["record"]
        body = (
            f"🛡 *Review job #{job_id}* from +{job['poster']}\n"
            f"{record.get('job_title')} @ {record.get('company_name')}\n"
            f"🔗 {record.get('apply_link')}\n\n{job['reason']}\n\n"
            f"Reply *APPROVE {job_id}* or *REJECT {job_id} <reason>*."
        )
        for admin in self.settings.admins if recipients is None else recipients:
            self.reply(admin, body)

    def _publish(self, job_id: int) -> None:
        job = self.store.get_job(job_id)
        assert job is not None
        record = {key: value for key, value in job["record"].items() if key != "raw_text"}
        try:
            path = reuse.save_job_record(record, self.settings.jobs_dir)
        except FileExistsError:
            self.store.update_job(job_id, status=REJECTED, reason="Duplicate of a job already on the hub")
            self.reply(job["poster"], f"❌ Job #{job_id} is already on the hub, so it was not shared again.")
            return
        self.store.update_job(job_id, status=PUBLISHED, path=str(path), reason="")
        notified = self.fan_out(job_id)
        self.reply(
            job["poster"],
            f"✅ Job #{job_id} is verified and live. {notified} matching job seeker(s) notified so far.\n\n"
            + reuse.format_whatsapp_group_message(record),
        )

    def _my_posts(self, user: dict[str, Any]) -> None:
        jobs = self.store.jobs(poster=user["phone"])[:10]
        if not jobs:
            self.reply(user["phone"], "You haven't posted any jobs yet. Send *POST* to share one.")
            return
        labels = {
            PUBLISHED: "✅ live",
            PENDING_REVIEW: "⏳ in review",
            NEEDS_LINK: "📎 needs link",
            REJECTED: "❌ rejected",
            EXPIRED: "⌛ expired",
        }
        lines = [
            f"#{job['id']} {job['record'].get('job_title')} @ {job['record'].get('company_name')} – "
            f"{labels.get(job['status'], job['status'])}"
            for job in jobs
        ]
        self.reply(user["phone"], "*Your posts*\n" + "\n".join(lines))

    # ------------------------------------------------------------ moderation --

    def _pending(self, admin: str) -> None:
        jobs = self.store.jobs(status=PENDING_REVIEW)
        if not jobs:
            self.reply(admin, "No posts waiting for review.")
            return
        for job in jobs[:10]:
            self._notify_moderators(job["id"], [admin])

    def _moderated_job(self, admin: str, rest: str) -> tuple[dict[str, Any] | None, str]:
        match = re.match(r"#?(\d+)\s*(.*)", rest, re.DOTALL)
        if not match:
            self.reply(admin, "Usage: *APPROVE <id>* or *REJECT <id> <reason>*")
            return None, ""
        job = self.store.get_job(int(match.group(1)))
        if not job or job["status"] != PENDING_REVIEW:
            self.reply(admin, f"Job #{match.group(1)} is not waiting for review.")
            return None, ""
        return job, match.group(2).strip()

    def _approve(self, admin: str, rest: str) -> None:
        job, _ = self._moderated_job(admin, rest)
        if job:
            self._publish(job["id"])
            self.reply(admin, f"Job #{job['id']} published.")

    def _reject(self, admin: str, rest: str) -> None:
        job, reason = self._moderated_job(admin, rest)
        if not job:
            return
        reason = reason or "A moderator could not verify this posting."
        self.store.update_job(job["id"], status=REJECTED, reason=reason)
        self.reply(job["poster"], f"❌ Job #{job['id']} was not shared.\n{reason}")
        self.reply(admin, f"Job #{job['id']} rejected.")

    # --------------------------------------------------------------- seekers --

    def _join(self, user: dict[str, Any], rest: str) -> None:
        if rest:
            self._set_profile(user, rest)
            return
        if user["profile_text"]:
            self.store.update_user(user["phone"], subscribed=True)
            self.reply(user["phone"], "🔔 Job alerts are on. Send *PROFILE* with new details to update your profile.")
            self._backfill(user["phone"])
            return
        self.store.set_state(user["phone"], "awaiting_profile")
        self.reply(
            user["phone"],
            "Upload your resume as a PDF, or describe what you're looking for:\n"
            "Skills: Python, AWS\nRoles: Backend Engineer\nLocation: Bengaluru\nMode: Hybrid, Remote",
        )

    def _handle_resume(self, user: dict[str, Any], message: Inbound) -> None:
        suffix = Path(message.filename).suffix.lower() or (".pdf" if "pdf" in message.mime_type else ".txt")
        if suffix not in {".pdf", ".txt", ".md"}:
            self.reply(user["phone"], "Please send your resume as a PDF or text file.")
            return
        try:
            data = self.channel.download_media(message.media_id)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / f"resume{suffix}"
                path.write_bytes(data)
                text = reuse.read_resume(path)
        except Exception as exc:  # network errors, unreadable PDFs
            log.warning("Could not read resume from %s: %s", user["phone"], exc)
            self.reply(user["phone"], "Sorry, I couldn't read that file. Try another PDF or send your details as text.")
            return
        if not text.strip():
            self.reply(user["phone"], "That file has no readable text (is it a scan?). Send your details as text instead.")
            return
        self._set_profile(user, text)

    def _set_profile(self, user: dict[str, Any], text: str) -> None:
        profile = self.extractor.extract_profile(text)
        prefs = {
            "skills": profile.get("skills") or [],
            "desired_roles": profile.get("desired_roles") or [],
            "locations": profile.get("locations") or [],
            "work_modes": profile.get("work_modes") or [],
        }
        profile_text = "\n".join(
            part
            for part in [
                text,
                "Skills: " + ", ".join(prefs["skills"]) if prefs["skills"] else "",
                "Roles: " + ", ".join(prefs["desired_roles"]) if prefs["desired_roles"] else "",
            ]
            if part
        )
        self.store.update_user(user["phone"], profile_text=profile_text, prefs=prefs, subscribed=True)
        self.store.set_state(user["phone"])
        summary = [
            ("Skills", prefs["skills"]),
            ("Roles", prefs["desired_roles"]),
            ("Locations", prefs["locations"]),
            ("Work mode", prefs["work_modes"]),
        ]
        lines = [f"*{label}:* {', '.join(values)}" for label, values in summary if values]
        self.reply(
            user["phone"],
            "🔔 You're subscribed. I'll message you when a verified job matches your profile.\n"
            + ("\n".join(lines) + "\n" if lines else "")
            + "Send *STOP* to pause alerts.",
        )
        self._backfill(user["phone"])

    def _stop(self, user: dict[str, Any]) -> None:
        self.store.update_user(user["phone"], subscribed=False)
        self.reply(user["phone"], "🔕 Job alerts are off. Send *JOIN* to turn them back on.")

    def _list_matches(self, user: dict[str, Any]) -> None:
        if not user["profile_text"]:
            self.reply(user["phone"], "Send *JOIN* first so I know what jobs to look for.")
            return
        matches = self.matches_for(self.store.get_user(user["phone"]))  # type: ignore[arg-type]
        if not matches:
            self.reply(user["phone"], "No verified jobs match your profile right now. I'll message you when one does.")
            return
        lines = [
            f"★ {job['record'].get('job_title')} @ {job['record'].get('company_name')} "
            f"({score['score']:.2f})\n🔗 {job['record'].get('apply_link')}"
            for job, score in matches[:10]
        ]
        self.reply(user["phone"], "*Jobs that match you*\n\n" + "\n\n".join(lines))

    # -------------------------------------------------------------- matching --

    def score(self, record: dict[str, Any], seeker: dict[str, Any]) -> dict[str, Any] | None:
        """Return the score for a seeker/job pair, or None when it should not be sent."""
        freshness = reuse.posting_freshness(record, self.today(), self.settings.max_age_days)
        if not freshness["fresh"]:
            return None
        if not self._prefs_allow(record, seeker["prefs"]):
            return None
        scored = reuse.score_resume_against_job(seeker["profile_text"], record)
        if not scored["applies"]:
            return None
        return scored

    @staticmethod
    def _prefs_allow(record: dict[str, Any], prefs: dict[str, Any]) -> bool:
        norm = reuse.normalize_filter
        job_mode = norm(record.get("work_mode"))
        job_location = norm(record.get("location"))
        modes = [norm(item) for item in prefs.get("work_modes") or [] if norm(item)]
        if modes and job_mode and not any(mode in job_mode or job_mode in mode for mode in modes):
            return False
        locations = [norm(item) for item in prefs.get("locations") or [] if norm(item)]
        if locations and job_location and "remote" not in job_mode:
            if not any(loc in job_location or job_location in loc for loc in locations):
                return False
        return True

    def matches_for(self, seeker: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        results = []
        for job in self.store.jobs(status=PUBLISHED):
            scored = self.score(job["record"], seeker)
            if scored:
                results.append((job, scored))
        results.sort(key=lambda item: item[1]["score"], reverse=True)
        return results

    def fan_out(self, job_id: int) -> int:
        """Notify every subscribed seeker the new job matches. Returns how many were sent."""
        job = self.store.get_job(job_id)
        if not job or job["status"] != PUBLISHED:
            return 0
        sent = 0
        for seeker in self.store.subscribers():
            if seeker["phone"] == job["poster"] or self.store.was_notified(seeker["phone"], job_id):
                continue
            scored = self.score(job["record"], seeker)
            if scored and self._notify(seeker, job, scored):
                sent += 1
        return sent

    def _backfill(self, phone: str) -> None:
        seeker = self.store.get_user(phone)
        if not seeker:
            return
        sent = 0
        for job, scored in self.matches_for(seeker):
            if sent >= self.settings.backfill_limit:
                break
            if not self.store.was_notified(phone, job["id"]) and self._notify(seeker, job, scored):
                sent += 1

    def _notify(self, seeker: dict[str, Any], job: dict[str, Any], scored: dict[str, Any]) -> bool:
        phone = seeker["phone"]
        if self.store.alerts_sent_since(phone) >= self.settings.max_alerts_per_day:
            return False
        record = job["record"]
        try:
            if within_service_window(seeker["last_inbound_at"]):
                terms = ", ".join(scored["matched_terms"][:6])
                body = (
                    f"🔔 *New job for you* (match {scored['score']:.2f}"
                    + (f"; {terms}" if terms else "")
                    + ")\n\n"
                    + reuse.format_whatsapp_group_message(record)
                )
                self.channel.send_text(phone, body)
            else:
                # Outside the 24h window WhatsApp only delivers pre-approved templates.
                self.channel.send_template(
                    phone,
                    self.settings.wa_alert_template,
                    self.settings.wa_template_language,
                    [
                        str(record.get("job_title") or "a role"),
                        str(record.get("company_name") or "a company"),
                        str(record.get("apply_link") or ""),
                    ],
                )
        except Exception as exc:
            log.warning("Alert to %s failed: %s", phone, exc)
            return False
        self.store.record_notification(phone, job["id"], scored["score"])
        return True

    # ---------------------------------------------------------- housekeeping --

    def expire_stale(self) -> list[int]:
        """Mark published jobs older than the freshness window as expired."""
        expired = []
        for job in self.store.jobs(status=PUBLISHED):
            if not reuse.posting_freshness(job["record"], self.today(), self.settings.max_age_days)["fresh"]:
                self.store.update_job(job["id"], status=EXPIRED)
                if job["path"]:
                    Path(job["path"]).unlink(missing_ok=True)
                expired.append(job["id"])
        return expired

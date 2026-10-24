"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _list(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


@dataclass
class Settings:
    data_dir: Path = Path("data")
    # Phone numbers (international format, digits only) allowed to moderate posts.
    admins: list[str] = field(default_factory=list)
    model: str = "claude-opus-5-5"
    use_llm: bool = True
    max_age_days: int = 15
    max_alerts_per_day: int = 5
    backfill_limit: int = 3
    validation_timeout: int = 20

    # WhatsApp Cloud API
    wa_token: str = ""
    wa_phone_number_id: str = ""
    wa_verify_token: str = ""
    wa_app_secret: str = ""
    wa_api_version: str = "v21.0"
    # Approved template used when a seeker is outside the 24-hour service window.
    wa_alert_template: str = "job_match_alert"
    wa_template_language: str = "en"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "job_postings"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "hub.sqlite3"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            data_dir=Path(env.get("HUB_DATA_DIR", "data")),
            admins=[normalize_phone(item) for item in _list(env.get("HUB_ADMINS"))],
            model=env.get("HUB_MODEL", "claude-opus-5-5"),
            use_llm=env.get("HUB_USE_LLM", "1") not in {"0", "false", "no"},
            max_age_days=int(env.get("HUB_MAX_AGE_DAYS", "15")),
            max_alerts_per_day=int(env.get("HUB_MAX_ALERTS_PER_DAY", "5")),
            backfill_limit=int(env.get("HUB_BACKFILL_LIMIT", "3")),
            validation_timeout=int(env.get("HUB_VALIDATION_TIMEOUT", "20")),
            wa_token=env.get("WHATSAPP_TOKEN", ""),
            wa_phone_number_id=env.get("WHATSAPP_PHONE_NUMBER_ID", ""),
            wa_verify_token=env.get("WHATSAPP_VERIFY_TOKEN", ""),
            wa_app_secret=env.get("WHATSAPP_APP_SECRET", ""),
            wa_api_version=env.get("WHATSAPP_API_VERSION", "v21.0"),
            wa_alert_template=env.get("WHATSAPP_ALERT_TEMPLATE", "job_match_alert"),
            wa_template_language=env.get("WHATSAPP_TEMPLATE_LANGUAGE", "en"),
        )


def normalize_phone(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())

"""Messaging channels. WhatsApp Cloud API is the production channel; the
recording channel backs the simulator and tests. Another platform only needs
to implement the Channel protocol and an inbound parser."""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import requests

log = logging.getLogger(__name__)

MAX_TEXT = 4096  # WhatsApp text body limit


@dataclass
class Inbound:
    message_id: str
    sender: str
    name: str = ""
    text: str = ""
    # For document messages (resume uploads).
    media_id: str = ""
    filename: str = ""
    mime_type: str = ""


class Channel(Protocol):
    def send_text(self, to: str, body: str) -> None: ...
    def send_template(self, to: str, template: str, language: str, params: list[str]) -> None: ...
    def download_media(self, media_id: str) -> bytes: ...


def clip(body: str) -> str:
    return body if len(body) <= MAX_TEXT else body[: MAX_TEXT - 1] + "…"


class WhatsAppCloudChannel:
    def __init__(self, token: str, phone_number_id: str, api_version: str = "v21.0", timeout: int = 20):
        self.base = f"https://graph.facebook.com/{api_version}"
        self.phone_number_id = phone_number_id
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"
        self.timeout = timeout

    def _post(self, payload: dict[str, Any]) -> None:
        response = self.session.post(
            f"{self.base}/{self.phone_number_id}/messages",
            json={"messaging_product": "whatsapp", **payload},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            log.error("WhatsApp send failed (%s): %s", response.status_code, response.text[:500])
            response.raise_for_status()

    def send_text(self, to: str, body: str) -> None:
        self._post({"to": to, "type": "text", "text": {"body": clip(body), "preview_url": True}})

    def send_template(self, to: str, template: str, language: str, params: list[str]) -> None:
        self._post(
            {
                "to": to,
                "type": "template",
                "template": {
                    "name": template,
                    "language": {"code": language},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": value[:1024]} for value in params],
                        }
                    ],
                },
            }
        )

    def download_media(self, media_id: str) -> bytes:
        meta = self.session.get(f"{self.base}/{media_id}", timeout=self.timeout)
        meta.raise_for_status()
        blob = self.session.get(meta.json()["url"], timeout=self.timeout)
        blob.raise_for_status()
        return blob.content


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Check Meta's X-Hub-Signature-256 header. Skipped when no secret is configured."""
    if not app_secret:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def parse_whatsapp_webhook(payload: dict[str, Any]) -> list[Inbound]:
    """Extract user messages from a WhatsApp Cloud API webhook; ignores status callbacks."""
    messages: list[Inbound] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            names = {
                contact.get("wa_id"): (contact.get("profile") or {}).get("name", "")
                for contact in value.get("contacts") or []
            }
            for message in value.get("messages") or []:
                sender = message.get("from", "")
                item = Inbound(message_id=message.get("id", ""), sender=sender, name=names.get(sender, ""))
                kind = message.get("type")
                if kind == "text":
                    item.text = (message.get("text") or {}).get("body", "")
                elif kind == "document":
                    document = message.get("document") or {}
                    item.media_id = document.get("id", "")
                    item.filename = document.get("filename", "")
                    item.mime_type = document.get("mime_type", "")
                    item.text = document.get("caption", "")
                elif kind == "button":
                    item.text = (message.get("button") or {}).get("text", "")
                elif kind == "interactive":
                    interactive = message.get("interactive") or {}
                    reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
                    item.text = reply.get("title", "")
                else:
                    item.text = ""
                messages.append(item)
    return messages


@dataclass
class RecordingChannel:
    """Keeps outgoing messages in memory (simulator, tests)."""

    sent: list[dict[str, Any]] = field(default_factory=list)
    media: dict[str, bytes] = field(default_factory=dict)

    def send_text(self, to: str, body: str) -> None:
        self.sent.append({"to": to, "type": "text", "body": clip(body)})

    def send_template(self, to: str, template: str, language: str, params: list[str]) -> None:
        self.sent.append({"to": to, "type": "template", "template": template, "params": params})

    def download_media(self, media_id: str) -> bytes:
        return self.media[media_id]

    def to(self, phone: str) -> list[dict[str, Any]]:
        return [item for item in self.sent if item["to"] == phone]

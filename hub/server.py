"""WhatsApp Cloud API webhook server.

GET  /webhook  – Meta's verification handshake
POST /webhook  – inbound messages; acknowledged at once, processed on a worker thread
GET  /healthz  – liveness
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .channels import Inbound, parse_whatsapp_webhook, verify_signature
from .config import Settings
from .workflow import JobHub

log = logging.getLogger(__name__)


class Worker(threading.Thread):
    """Single consumer, so SQLite writes and per-user conversation state stay ordered."""

    def __init__(self, hub: JobHub):
        super().__init__(daemon=True, name="job-hub-worker")
        self.hub = hub
        self.inbox: queue.Queue[Inbound] = queue.Queue()

    def run(self) -> None:
        while True:
            message = self.inbox.get()
            try:
                self.hub.handle(message)
            except Exception:
                log.exception("Failed to handle message %s", message.message_id)
            finally:
                self.inbox.task_done()


def make_handler(settings: Settings, worker: Worker) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: str = "", content_type: str = "text/plain") -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path == "/healthz":
                self._send(200, "ok")
                return
            if url.path != "/webhook":
                self._send(404)
                return
            query = {key: values[0] for key, values in parse_qs(url.query).items()}
            if (
                query.get("hub.mode") == "subscribe"
                and settings.wa_verify_token
                and query.get("hub.verify_token") == settings.wa_verify_token
            ):
                self._send(200, query.get("hub.challenge", ""))
            else:
                self._send(403, "verification failed")

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/webhook":
                self._send(404)
                return
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if not verify_signature(settings.wa_app_secret, body, self.headers.get("X-Hub-Signature-256")):
                self._send(401, "bad signature")
                return
            try:
                payload: dict[str, Any] = json.loads(body or b"{}")
            except json.JSONDecodeError:
                self._send(400, "invalid json")
                return
            for message in parse_whatsapp_webhook(payload):
                worker.inbox.put(message)
            self._send(200, "ok")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            log.info("%s %s", self.address_string(), format % args)

    return Handler


def serve(hub: JobHub, settings: Settings, host: str = "0.0.0.0", port: int = 8080) -> None:
    worker = Worker(hub)
    worker.start()
    server = ThreadingHTTPServer((host, port), make_handler(settings, worker))
    log.info("Job Hub listening on http://%s:%s/webhook", host, port)
    server.serve_forever()

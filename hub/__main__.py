"""Command line entry point: python -m hub {serve,simulate,expire,status}."""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys

from .channels import Inbound, RecordingChannel, WhatsAppCloudChannel
from .config import Settings, normalize_phone
from .extract import make_extractor
from .store import Store
from .workflow import JobHub


def build_hub(settings: Settings, channel=None) -> JobHub:
    if channel is None:
        if not (settings.wa_token and settings.wa_phone_number_id):
            sys.exit("Set WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID (see .env.example).")
        channel = WhatsAppCloudChannel(
            settings.wa_token, settings.wa_phone_number_id, settings.wa_api_version
        )
    return JobHub(
        settings,
        Store(settings.db_path),
        channel,
        make_extractor(settings.model, settings.use_llm),
    )


def simulate(settings: Settings) -> None:
    """Chat with the hub in the terminal. Prefix a line with a phone number to switch user:
    '+911111111111 POST ...'. Messages sent by the hub are printed instead of delivered."""
    channel = RecordingChannel()
    hub = build_hub(settings, channel)
    counter = itertools.count(1)
    current = settings.admins[0] if settings.admins else "910000000001"
    print(f"Simulating as +{current}. Start a line with +<number> to switch user; Ctrl-D to quit.")
    for line in sys.stdin:
        line = line.rstrip("\n")
        if line.startswith("+"):
            number, _, line = line.partition(" ")
            current = normalize_phone(number)
        if line.startswith("@resume "):
            path = line.removeprefix("@resume ").strip()
            media_id = f"local-{next(counter)}"
            with open(path, "rb") as handle:
                channel.media[media_id] = handle.read()
            message = Inbound(f"sim-{next(counter)}", current, media_id=media_id, filename=path)
        else:
            # Allow multi-line posts typed with literal "\n".
            message = Inbound(f"sim-{next(counter)}", current, text=line.replace("\\n", "\n"))
        start = len(channel.sent)
        hub.handle(message)
        for item in channel.sent[start:]:
            body = item.get("body") or f"[template {item['template']}] {item['params']}"
            print(f"\n→ +{item['to']}\n{body}\n")


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m hub", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    serve_cmd = sub.add_parser("serve", help="Run the WhatsApp webhook server")
    serve_cmd.add_argument("--host", default="0.0.0.0")
    serve_cmd.add_argument("--port", type=int, default=8080)
    sub.add_parser("simulate", help="Chat with the hub in the terminal (nothing is sent)")
    sub.add_parser("expire", help="Expire published jobs older than the freshness window")
    sub.add_parser("status", help="Print job and subscriber counts")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()

    if args.command == "serve":
        from .server import serve

        serve(build_hub(settings), settings, args.host, args.port)
    elif args.command == "simulate":
        simulate(settings)
    elif args.command == "expire":
        hub = build_hub(settings, RecordingChannel())
        print(json.dumps({"expired": hub.expire_stale()}))
    elif args.command == "status":
        store = Store(settings.db_path)
        counts: dict[str, int] = {}
        for job in store.jobs():
            counts[job["status"]] = counts.get(job["status"], 0) + 1
        print(json.dumps({"jobs": counts, "subscribers": len(store.subscribers())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

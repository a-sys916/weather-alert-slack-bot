"""Posts formatted messages to a Slack Incoming Webhook.

v1 deliberately uses a webhook (not a bot token + chat.postMessage) because
it is the lowest-setup option: the user pastes one URL into a GitHub Actions
secret and is done, no Slack app review or OAuth flow required. A bot token
would allow richer features later (posting in threads, editing messages,
reacting) -- see README.md for how to upgrade if that's ever wanted.

If SLACK_WEBHOOK_URL is not set, messages are printed to stdout instead of
posted anywhere. This is intentionally kept as a permanent feature (not just
a temporary dev hack) so the bot is easy to test locally without needing a
real webhook.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from . import config

logger = logging.getLogger(__name__)

WEBHOOK_ENV_VAR = "SLACK_WEBHOOK_URL"


def is_dry_run() -> bool:
    return not os.environ.get(WEBHOOK_ENV_VAR)


def send_message(blocks: list[dict], fallback_text: str) -> bool:
    """Sends one Slack message. Returns True on success (or in dry-run mode)."""
    webhook_url = os.environ.get(WEBHOOK_ENV_VAR)

    payload = {"text": fallback_text, "blocks": blocks}

    if not webhook_url:
        print("=" * 70)
        print("[DRY RUN] SLACK_WEBHOOK_URL is not set -- printing message instead of posting:")
        print("=" * 70)
        print(fallback_text)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("=" * 70)
        return True

    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": config.REQUEST_USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status != 200:
                logger.error("Slack webhook returned HTTP %s: %s", resp.status, body)
                return False
            return True
    except urllib.error.HTTPError as exc:
        logger.error("Slack webhook HTTP error %s: %s", exc.code, exc.read())
        return False
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.error("Slack webhook network error: %s", exc)
        return False

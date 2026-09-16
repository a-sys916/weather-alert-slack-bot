#!/usr/bin/env python3
"""Entry point: one poll cycle of JMA disaster-prevention data -> Slack.

Fetch -> parse -> diff against saved state -> format -> send to Slack ->
save updated state. Designed to be invoked repeatedly (every 10-15 minutes)
by cron or a GitHub Actions scheduled workflow -- see
.github/workflows/weather-alerts.yml.

Run locally with: python main.py
Without SLACK_WEBHOOK_URL set, the formatted message is printed to stdout
instead of posted to Slack (see jma_bot/slack_notifier.py).
"""

from __future__ import annotations

import logging
import sys

from jma_bot import config, formatter, jma_client, slack_notifier, state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


def run_once() -> int:
    logger.info("Loading area code master ...")
    area_master = jma_client.load_area_master()
    office_codes = area_master.office_codes()
    logger.info("Monitoring %d office(s)%s", len(office_codes), " (filtered)" if config.OFFICE_CODE_FILTER else " (all of Japan)")

    logger.info("Fetching current warnings/advisories ...")
    warning_entries, report_datetimes = jma_client.fetch_all_warnings(area_master)
    logger.info("Fetched %d active warning/advisory entries nationwide", len(warning_entries))

    logger.info("Fetching active typhoons ...")
    typhoons = jma_client.fetch_all_typhoons()
    logger.info("Fetched %d active tropical cyclone(s)", len(typhoons))

    logger.info("Fetching early warning (早期注意情報) outlook ...")
    early_warning_entries, early_warning_report_datetimes = jma_client.fetch_all_early_warnings(area_master)
    logger.info("Fetched %d early-warning entries nationwide", len(early_warning_entries))

    saved_state = state.load_state()

    warning_changes = state.diff_warnings(warning_entries, saved_state)
    typhoon_changes = state.diff_typhoons(typhoons, saved_state)
    all_early_warning_changes = state.diff_early_warnings(early_warning_entries, saved_state)

    # Only "高" (high) likelihood is worth interrupting for -- "中" (medium)
    # fires too often to be useful, and a cleared entry (level=None) never
    # matches either, so "risk went away" updates stay silent too. The full,
    # unfiltered diff above is still what gets saved to state, so dedup keeps
    # working correctly regardless of this notification-only filter.
    early_warning_changes = [c for c in all_early_warning_changes if c.level in config.NOTIFY_EARLY_WARNING_LEVELS]

    logger.info(
        "%d warning change(s), %d typhoon change(s), %d early-warning change(s) to notify (%d before 高-only filter)",
        len(warning_changes),
        len(typhoon_changes),
        len(early_warning_changes),
        len(all_early_warning_changes),
    )

    if not warning_changes and not typhoon_changes and not early_warning_changes:
        logger.info("Nothing new to notify. Done.")
        state.save_state(saved_state)
        return 0

    report_time = None
    if warning_changes:
        relevant_offices = {c.office_code for c in warning_changes}
        relevant_times = [report_datetimes[o] for o in relevant_offices if o in report_datetimes]
        if relevant_times:
            report_time = max(relevant_times)

    early_warning_report_time = None
    if early_warning_changes:
        relevant_offices = {c.office_code for c in early_warning_changes}
        relevant_times = [early_warning_report_datetimes[o] for o in relevant_offices if o in early_warning_report_datetimes]
        if relevant_times:
            early_warning_report_time = max(relevant_times)

    blocks: list[dict] = []
    blocks.extend(formatter.build_warning_blocks(warning_changes, area_master, report_time))
    if blocks and typhoon_changes:
        blocks.append({"type": "divider"})
    blocks.extend(formatter.build_typhoon_blocks(typhoon_changes))
    if blocks and early_warning_changes:
        blocks.append({"type": "divider"})
    blocks.extend(formatter.build_early_warning_blocks(early_warning_changes, area_master, early_warning_report_time))

    fallback_text = formatter.build_fallback_text(warning_changes, typhoon_changes, early_warning_changes)

    ok = slack_notifier.send_message(blocks, fallback_text)
    if not ok:
        logger.error("Failed to send Slack notification; state will NOT be saved so we retry next run.")
        return 1

    state.save_state(saved_state)
    logger.info("Notification sent and state saved.")
    return 0


if __name__ == "__main__":
    sys.exit(run_once())

"""Turns warning/typhoon changes into a Slack Block Kit message.

Design goal: answer "いつ・どこ地方・どの程度" (when / where / how severe) at a
glance, using emoji to make severity level scannable without reading text.
"""

from __future__ import annotations

import datetime

from . import config
from .jma_client import AreaMaster
from .state import TyphoonChange, WarningChange

CHANGE_TYPE_LABEL = {
    "new": "新規発表",
    "level_change": "レベル変化",
    "cancelled": "解除",
}


def _now_jst_str() -> str:
    jst = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.now(jst).strftime("%Y-%m-%d %H:%M JST")


def _warning_line(change: WarningChange, area_master: AreaMaster) -> str:
    emoji = config.LEVEL_EMOJI.get(change.level, "⚪")
    office_name = area_master.office_name(change.office_code)
    area_name = area_master.area_name(change.area_code)
    # Some prefectures have no sub-division (their only class10 area shares
    # the prefecture's own name), so avoid printing e.g. "大阪府 大阪府" twice.
    where = office_name if area_name == office_name else f"{office_name} {area_name}"
    tag = CHANGE_TYPE_LABEL.get(change.change_type, change.change_type)

    # change.name (e.g. "大雨警報") already spells out the level as a suffix,
    # so we don't repeat change.level again in the text -- the emoji plus the
    # name itself both convey severity (注意報 < 警報 < 特別警報).
    line = f"{emoji} *{change.name}* ｜ {where} ｜ {tag}"
    if change.change_type == "level_change" and change.previous_level:
        line += f"（{change.previous_level} → {change.level}）"
    if change.additions:
        line += f" ｜ {'・'.join(change.additions)}"
    return line


def build_warning_blocks(changes: list[WarningChange], area_master: AreaMaster, report_time: str | None) -> list[dict]:
    """Builds Slack blocks for a batch of warning/advisory changes.

    `report_time` should be the いつ (when) to display -- callers pass along
    a JMA reportDatetime string when they have one, otherwise the current
    time is shown as a fallback.
    """
    if not changes:
        return []

    when = report_time or _now_jst_str()
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "気象警報・注意報の更新", "emoji": True},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"いつ: {when}"}],
        },
        {"type": "divider"},
    ]

    # Show the most severe / most actionable changes first.
    def sort_key(c: WarningChange):
        severity = config.LEVEL_RANK.get(c.level, 0)
        cancelled = 1 if c.change_type == "cancelled" else 0
        return (cancelled, -severity)

    for change in sorted(changes, key=sort_key):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _warning_line(change, area_master)}})

    return blocks


def _typhoon_summary_lines(change: TyphoonChange) -> list[str]:
    info = change.info
    category_label = config.TYPHOON_CATEGORY_LABELS.get(info.category, info.category or "不明")
    lines = [f"分類: {category_label}（識別番号 {info.event_id} / {info.typhoon_number}）"]

    analysis = next((p for p in info.track if p.label_jp == "実況"), None)
    if analysis and analysis.center:
        lat, lon = analysis.center
        lines.append(f"現在位置（実況）: 北緯{lat}度 東経{lon}度（{analysis.valid_time or '時刻不明'}）")

    forecast_points = [p for p in info.track if p.label_jp != "実況"]
    if forecast_points:
        lines.append("進路予報:")
        for p in forecast_points:
            pos = f"北緯{p.center[0]}度 東経{p.center[1]}度" if p.center else "位置未定"
            storm_note = "（暴風域あり）" if p.has_storm_warning_area else ""
            lines.append(f"　・{p.label_jp}（{p.valid_time or '時刻不明'}）: {pos}{storm_note}")

    return lines


def build_typhoon_blocks(changes: list[TyphoonChange]) -> list[dict]:
    if not changes:
        return []

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "台風情報の更新", "emoji": True},
        },
    ]
    for change in changes:
        tag = "新規" if change.change_type == "new" else "更新"
        header = f"🌀 *{tag}* ｜ 発表: {change.info.forecast_issue_time or change.info.issue_time or '時刻不明'}"
        body = "\n".join(_typhoon_summary_lines(change))
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"{header}\n{body}"}})
        blocks.append({"type": "divider"})

    if blocks and blocks[-1] == {"type": "divider"}:
        blocks.pop()

    return blocks


def build_fallback_text(warning_changes: list[WarningChange], typhoon_changes: list[TyphoonChange]) -> str:
    """Plain-text summary for Slack notification previews / accessibility."""
    parts = []
    if warning_changes:
        parts.append(f"気象警報・注意報 更新 {len(warning_changes)}件")
    if typhoon_changes:
        parts.append(f"台風情報 更新 {len(typhoon_changes)}件")
    return " / ".join(parts) if parts else "JMA防災情報の更新"

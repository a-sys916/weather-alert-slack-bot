"""Turns warning/typhoon changes into a Slack Block Kit message.

Design goal: answer "いつ・どこ地方・どの程度" (when / where / how severe) at a
glance, using emoji to make severity level scannable without reading text.
"""

from __future__ import annotations

import datetime
import math

from . import config
from .jma_client import AreaMaster
from .state import EarlyWarningChange, TyphoonChange, WarningChange

CHANGE_TYPE_LABEL = {
    "new": "新規発表",
    "level_change": "レベル変化",
    "cancelled": "解除",
}

EARLY_WARNING_CHANGE_LABEL = {
    "new": "新規",
    "level_change": "変化",
    "cleared": "解消",
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


def _relative_day_label(iso_str: str | None) -> str:
    """Turns an ISO datetime into a rough Japanese day label (今日/明日/明後日/...).

    Early-warning entries only need a rough "いつ" -- the underlying data
    itself is a likelihood over a multi-hour/day window, not a precise
    event time, so a precise timestamp would be false precision here.
    """
    if not iso_str:
        return "時期不明"
    try:
        target = datetime.datetime.fromisoformat(iso_str).date()
    except ValueError:
        return "時期不明"

    jst = datetime.timezone(datetime.timedelta(hours=9))
    today = datetime.datetime.now(jst).date()
    delta = (target - today).days
    if delta <= 0:
        return "今日"
    if delta == 1:
        return "明日"
    if delta == 2:
        return "明後日"
    if delta <= 7:
        return f"{delta}日後（{target.month}/{target.day}）"
    return f"{target.month}/{target.day}"


def _early_warning_line(change: EarlyWarningChange, area_master: AreaMaster) -> str:
    office_name = area_master.office_name(change.office_code)
    area_name = area_master.area_name(change.area_code)
    where = office_name if area_name == office_name else f"{office_name} {area_name}"
    category_label = config.EARLY_WARNING_CATEGORY_LABELS.get(change.category, change.category)
    tag = EARLY_WARNING_CHANGE_LABEL.get(change.change_type, change.change_type)

    if change.change_type == "cleared":
        return f"👀 *{category_label}の警報級の可能性* ｜ {where} ｜ {tag}（前回: {change.previous_level}）"

    emoji = config.LIKELIHOOD_EMOJI.get(change.level, "👀")
    when = _relative_day_label(change.earliest_time)
    line = f"{emoji} *{category_label}の警報級の可能性（{change.level}）* ｜ {where} ｜ {tag} ｜ {when}"
    if change.change_type == "level_change" and change.previous_level:
        line += f"（{change.previous_level} → {change.level}）"
    return line


def build_early_warning_blocks(
    changes: list[EarlyWarningChange], area_master: AreaMaster, report_time: str | None
) -> list[dict]:
    """Builds Slack blocks for 早期注意情報 (警報級の可能性) changes.

    Visually distinct from build_warning_blocks on purpose: different emoji
    (👀/🔎, never the 🟡🟠🔴 used for an actually-issued advisory/warning) and
    an explicit note that this is only a likelihood, not an issued warning --
    conflating the two would be actively misleading for a disaster bot.
    """
    if not changes:
        return []

    when = report_time or _now_jst_str()
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🔎 早期注意情報（警報級の可能性）", "emoji": True},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"発表: {when} ｜ まだ警報・注意報は発表されていません。数日先の可能性についての早めの参考情報です。",
                }
            ],
        },
        {"type": "divider"},
    ]

    def sort_key(c: EarlyWarningChange):
        cleared = 1 if c.change_type == "cleared" else 0
        rank = config.LIKELIHOOD_RANK.get(c.level, 0)
        return (cleared, -rank)

    for change in sorted(changes, key=sort_key):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _early_warning_line(change, area_master)}})

    return blocks


def _haversine_km(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Great-circle distance in km between two (lat, lon) points in degrees.

    Sanity check (hand-checkable): 那覇 (26.2124, 127.6809) to 東京
    (35.6812, 139.7671) comes out to ~1554km, matching the commonly-cited
    ~1550km Naha-Tokyo distance.
    """
    r = 6371.0  # mean Earth radius, km
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _nearest_region(point: tuple[float, float]) -> tuple[str, float]:
    """Returns (region_name, distance_km) for the closest REGION_REFERENCE_POINTS entry."""
    return min(
        ((name, _haversine_km(point, ref)) for name, ref in config.REGION_REFERENCE_POINTS.items()),
        key=lambda pair: pair[1],
    )


def _typhoon_summary_lines(change: TyphoonChange) -> list[str]:
    """Human-readable summary of a typhoon's position + forecast track.

    Raw lat/lon isn't meaningful to a non-technical reader, so each point is
    instead shown as "distance to the nearest major region" (via
    _nearest_region / haversine distance to config.REGION_REFERENCE_POINTS),
    plus a rough 接近中/遠ざかっている trend for forecast points, computed by
    comparing each point's distance to its own nearest region against the
    *previous* chronological point's distance to that *same* region. This is
    a simple point-to-point heuristic, not a full vector/bearing analysis --
    typhoon tracks can be non-monotonic, but this is good enough for a quick
    "is it heading this way" read. A 1km tolerance avoids flip-flopping on
    negligible float noise between two nearly-equal distances.
    """
    info = change.info
    category_label = config.TYPHOON_CATEGORY_LABELS.get(info.category, info.category or "不明")
    lines = [f"分類: {category_label}（識別番号 {info.event_id} / {info.typhoon_number}）"]

    analysis = next((p for p in info.track if p.label_jp == "実況"), None)
    if analysis and analysis.center:
        region, dist = _nearest_region(analysis.center)
        lines.append(f"現在位置（実況）: {region}地方まで約{dist:.0f}km（{analysis.valid_time or '時刻不明'}）")

    forecast_points = [p for p in info.track if p.label_jp != "実況"]
    if forecast_points:
        lines.append("進路予報:")
        previous_point = analysis
        for p in forecast_points:
            if not p.center:
                lines.append(f"　・{p.label_jp}（{p.valid_time or '時刻不明'}）: 位置未定")
                previous_point = p
                continue

            region, dist = _nearest_region(p.center)
            trend = ""
            if previous_point and previous_point.center:
                prev_dist = _haversine_km(previous_point.center, config.REGION_REFERENCE_POINTS[region])
                if dist < prev_dist - 1:
                    trend = "（接近中）"
                elif dist > prev_dist + 1:
                    trend = "（遠ざかっている）"

            storm_note = "・暴風域あり" if p.has_storm_warning_area else ""
            lines.append(f"　・{p.label_jp}（{p.valid_time or '時刻不明'}）: {region}地方まで約{dist:.0f}km{trend}{storm_note}")
            previous_point = p

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


def build_fallback_text(
    warning_changes: list[WarningChange],
    typhoon_changes: list[TyphoonChange],
    early_warning_changes: list[EarlyWarningChange] | None = None,
) -> str:
    """Plain-text summary for Slack notification previews / accessibility."""
    parts = []
    if warning_changes:
        parts.append(f"気象警報・注意報 更新 {len(warning_changes)}件")
    if typhoon_changes:
        parts.append(f"台風情報 更新 {len(typhoon_changes)}件")
    if early_warning_changes:
        parts.append(f"早期注意情報 更新 {len(early_warning_changes)}件")
    return " / ".join(parts) if parts else "JMA防災情報の更新"

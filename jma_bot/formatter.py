"""Turns warning/typhoon changes into a Slack Block Kit message.

Design goal: answer "いつ・どこ地方・どの程度" (when / where / how severe) at a
glance, using emoji to make severity level scannable without reading text.
"""

from __future__ import annotations

import datetime
import logging
import math

from . import config
from .jma_client import AreaMaster
from .state import EarlyWarningChange, TyphoonChange, WarningChange

logger = logging.getLogger(__name__)

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


def _region_for_office(office_code: str) -> str:
    """Looks up which of the 9 major regions an office code belongs to.

    Falls back to config.UNKNOWN_REGION_LABEL (rather than crashing or
    dropping the change) if OFFICE_TO_REGION is ever missing an office code
    that shows up in real data -- e.g. after a future JMA area reorganization.
    """
    region = config.OFFICE_TO_REGION.get(office_code)
    if region is None:
        logger.warning("no region mapping for office code %s; using %r bucket", office_code, config.UNKNOWN_REGION_LABEL)
        return config.UNKNOWN_REGION_LABEL
    return region


def _group_by_region(changes: list, sort_key) -> list[tuple[str, list]]:
    """Buckets `changes` by region, sorted within each region by `sort_key`.

    Returns (region_label, region_changes) pairs in the fixed geographic
    order 北海道→...→沖縄 (config.REGION_ORDER), skipping empty regions, with
    any config.UNKNOWN_REGION_LABEL bucket last.
    """
    buckets: dict[str, list] = {}
    for change in changes:
        buckets.setdefault(_region_for_office(change.office_code), []).append(change)

    sections = []
    for region in [*config.REGION_ORDER, config.UNKNOWN_REGION_LABEL]:
        region_changes = buckets.get(region)
        if not region_changes:
            continue
        sections.append((region, sorted(region_changes, key=sort_key)))
    return sections


def _render_region_sections(base_blocks: list[dict], sections: list[tuple[str, list[str]]]) -> list[dict]:
    """Renders (region_label, [display_line, ...]) sections into Slack blocks.

    Each region gets a bold-text header block, separated by dividers, in the
    same lightweight section/divider style used elsewhere in this file.
    Stops well short of Slack's ~50-blocks-per-message limit
    (config.MAX_BLOCKS_PER_SECTION) and appends a short summary line instead
    of silently truncating or risking a rejected payload -- a truncated but
    delivered safety notification beats a rejected one.
    """
    blocks = list(base_blocks)
    total_lines = sum(len(lines) for _, lines in sections)
    rendered_lines = 0
    truncated = False

    for i, (region, lines) in enumerate(sections):
        # +1 for this region's own header block, +1 more if it needs a
        # leading divider -- if there's no room left for even that, stop.
        if len(blocks) + (2 if i > 0 else 1) > config.MAX_BLOCKS_PER_SECTION:
            truncated = True
            break
        if i > 0:
            blocks.append({"type": "divider"})
        label = region if region == config.UNKNOWN_REGION_LABEL else f"{region}地方"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{label}*"}})

        for line in lines:
            if len(blocks) >= config.MAX_BLOCKS_PER_SECTION:
                truncated = True
                break
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": line}})
            rendered_lines += 1
        if truncated:
            break

    if truncated:
        omitted = total_lines - rendered_lines
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"…ほか{omitted}件、詳細はGitHub Actionsのログをご確認ください"}
                ],
            }
        )
    return blocks


def _merged_where(office_name: str, area_names: list[str]) -> str:
    # Some prefectures have no sub-division (their only class10 area shares
    # the prefecture's own name), so avoid printing e.g. "大阪府 大阪府" twice.
    if len(area_names) == 1 and area_names[0] == office_name:
        return office_name
    unique_names = list(dict.fromkeys(area_names))  # dedupe, keep first-seen order
    if len(unique_names) == 1:
        return f"{office_name} {unique_names[0]}"
    return f"{office_name}（{'・'.join(unique_names)}）"


def _merge_warning_changes(changes: list[WarningChange]) -> list[tuple[WarningChange, list[str]]]:
    """Groups changes that differ only by area into one (change, area_codes) entry.

    Two changes merge only if they share office_code + name + level +
    change_type + additions, and -- for a level_change -- the same
    previous_level too (a 警報→特別警報 change must never be merged with an
    unrelated 注意報→警報 change just because they land on the same line).
    Input order is preserved (callers pass already severity-sorted changes),
    so grouping doesn't disturb the existing sort order.
    """
    groups: dict[tuple, tuple[WarningChange, list[str]]] = {}
    order: list[tuple] = []
    for change in changes:
        key = (change.office_code, change.name, change.level, change.change_type, change.previous_level, tuple(change.additions))
        if key not in groups:
            groups[key] = (change, [])
            order.append(key)
        groups[key][1].append(change.area_code)
    return [groups[key] for key in order]


def _warning_line(change: WarningChange, area_codes: list[str], area_master: AreaMaster) -> str:
    emoji = config.LEVEL_EMOJI.get(change.level, "⚪")
    office_name = area_master.office_name(change.office_code)
    area_names = [area_master.area_name(code) for code in area_codes]
    where = _merged_where(office_name, area_names)
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
    time is shown as a fallback. Changes are grouped into per-region (地方)
    sections, and within each region, same-prefecture/same-warning entries
    covering different sub-areas are merged into one line.
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

    sections = [
        (region, [_warning_line(change, area_codes, area_master) for change, area_codes in _merge_warning_changes(region_changes)])
        for region, region_changes in _group_by_region(changes, sort_key)
    ]

    return _render_region_sections(blocks, sections)


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


def _merge_early_warning_changes(changes: list[EarlyWarningChange]) -> list[tuple[EarlyWarningChange, list[str]]]:
    """Same idea as _merge_warning_changes, for EarlyWarningChange.

    Merges only entries that share office_code + category + level +
    change_type + previous_level + earliest_time -- the earliest_time check
    means entries about different forecast days never get merged, even if
    everything else matches.
    """
    groups: dict[tuple, tuple[EarlyWarningChange, list[str]]] = {}
    order: list[tuple] = []
    for change in changes:
        key = (
            change.office_code,
            change.category,
            change.level,
            change.change_type,
            change.previous_level,
            change.earliest_time,
        )
        if key not in groups:
            groups[key] = (change, [])
            order.append(key)
        groups[key][1].append(change.area_code)
    return [groups[key] for key in order]


def _early_warning_line(change: EarlyWarningChange, area_codes: list[str], area_master: AreaMaster) -> str:
    office_name = area_master.office_name(change.office_code)
    area_names = [area_master.area_name(code) for code in area_codes]
    where = _merged_where(office_name, area_names)
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
    Grouped into per-region (地方) sections the same way build_warning_blocks
    is, with same-prefecture/same-day entries merged into one line.
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

    sections = [
        (
            region,
            [_early_warning_line(change, area_codes, area_master) for change, area_codes in _merge_early_warning_changes(region_changes)],
        )
        for region, region_changes in _group_by_region(changes, sort_key)
    ]

    return _render_region_sections(blocks, sections)


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

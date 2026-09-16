"""Static configuration: JMA endpoints, warning code table, and filters.

Everything a future maintainer is likely to want to tweak (which warning
types to notify on, which regions to watch, how long to wait between polls)
lives in this one file as clearly-named constants.
"""

from __future__ import annotations

import pathlib

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
AREA_MASTER_PATH = PROJECT_ROOT / "data" / "area_master.json"
STATE_PATH = PROJECT_ROOT / "state" / "seen.json"

# ---------------------------------------------------------------------------
# JMA endpoints
#
# IMPORTANT: JMA rolled out a new "防災気象情報" data format on 2026-05-29
# (JMA press release: 新たな防災気象情報の運用について
#  https://www.jma.go.jp/jma/press/2512/16a/20251216_taikeiseiri.html ,
#  see also https://www.jma.go.jp/jma/kishou/know/bosai/keiho-update2026/index.html).
#
# As of this writing (2026-09-16) the OLD endpoints below are still reachable
# but are FROZEN -- they keep returning whatever was current the moment the
# new system went live and never update:
#   - https://www.jma.go.jp/bosai/warning/data/warning/{office}.json
#   - https://www.jma.go.jp/bosai/information/data/typhoon.json
# Both were verified live and confirmed stale (reportDatetime stuck at
# 2026-05-2x) while building this bot. Do not use them.
#
# The endpoints actually used below were found by inspecting the live network
# requests made by the official JMA pages (www.jma.go.jp/bosai/warning/ and
# www.jma.go.jp/bosai/typhoon/) on 2026-09-16, and were confirmed to return
# current data at that time.
# ---------------------------------------------------------------------------

AREA_MASTER_URL = "https://www.jma.go.jp/bosai/common/const/area.json"

# Per-prefecture/office warning & advisory status. {office} is a 6-digit JMA
# office code (e.g. "130000" = Tokyo), taken from data/area_master.json.
# Returns a JSON ARRAY of the last few issued bulletins for that office (not
# just one current snapshot) -- see jma_client.fetch_office_warnings() for
# why we take the entry with the latest reportDatetime as "current".
WARNING_URL_TEMPLATE = "https://www.jma.go.jp/bosai/warning/data/r8/{office}.json"

# List of currently-tracked tropical cyclones (typhoons + tropical
# depressions). Each entry has a "tropicalCyclone" eventId (e.g. "TC2630"),
# a "category" (e.g. "TD" = tropical depression, "TY" = typhoon), and an
# "issue" timestamp.
TYPHOON_LIST_URL = "https://www.jma.go.jp/bosai/typhoon/data/targetTc.json"

# Per-typhoon position + forecast track. {event_id} comes from
# TYPHOON_LIST_URL above.
TYPHOON_FORECAST_URL_TEMPLATE = "https://www.jma.go.jp/bosai/typhoon/data/{event_id}/forecast.json"

REQUEST_TIMEOUT_SECONDS = 20
REQUEST_USER_AGENT = "weather-alert-slack-bot/1.0 (+https://github.com/; contact=miyoshi@entaku.co.jp)"

# ---------------------------------------------------------------------------
# Region filter (v1: empty = monitor all of Japan)
#
# To restrict monitoring to specific prefectures/offices later, put their
# 6-digit office codes here, e.g. ["130000", "140000"] for Tokyo + Kanagawa.
# Office codes are listed in data/area_master.json under "offices".
# ---------------------------------------------------------------------------

OFFICE_CODE_FILTER: list[str] = []

# ---------------------------------------------------------------------------
# Warning/advisory code table
#
# JMA's warning JSON identifies each phenomenon by a short numeric "code",
# not by name. This table was verified (not recalled from memory) via:
#   - Cross-referencing multiple independent Japanese developer write-ups of
#     the long-standing JMA code scheme (e.g. qiita.com/burifl/items/
#     d1e87eec7c28464879b6 and related community documentation of
#     bosai/warning JSON), which consistently list codes 02-26 and 32-37.
#   - Cross-checking those codes against LIVE data pulled from
#     https://www.jma.go.jp/bosai/warning/data/r8/130000.json on 2026-09-16:
#       code "10" appeared under a headline "注意報を解除します" (advisory
#         cancelled) -> consistent with 10 = 大雨注意報.
#       code "14" appeared with additions ["竜巻","突風","ひょう"] under a
#         thunderstorm headline -> consistent with 14 = 雷注意報 (lightning
#         advisory, which in the new system bundles tornado/gust/hail as
#         "additions").
#       code "20" appeared under a headline mentioning 濃霧 (dense fog) ->
#         consistent with 20 = 濃霧注意報.
#     This gives confidence the basic warning/advisory numbering was carried
#     over unchanged into the new (2026-05-29+) data format.
#   - Some codes observed live (e.g. "29", seen on a jointly-issued river
#     flood bulletin) could NOT be confidently mapped from public sources and
#     are deliberately left out of this table. Because NOTIFY_WARNING_CODES
#     below is a whitelist, any unmapped code is simply ignored rather than
#     mis-labelled -- safe by construction.
#
# Format: code -> (Japanese name, category, level)
#   category: one of "heavy_rain", "storm", "storm_snow", "flood", "high_tide",
#             "high_wave", "other" -- used to build NOTIFY_WARNING_CODES below.
#   level: "注意報" (advisory) / "警報" (warning) / "特別警報" (emergency warning)
# ---------------------------------------------------------------------------

WARNING_CODE_TABLE: dict[str, tuple[str, str, str]] = {
    # -- 警報 (warnings) --
    "02": ("暴風雪警報", "storm_snow", "警報"),
    "03": ("大雨警報", "heavy_rain", "警報"),
    "04": ("洪水警報", "flood", "警報"),
    "05": ("暴風警報", "storm", "警報"),
    "06": ("大雪警報", "other", "警報"),
    "07": ("波浪警報", "high_wave", "警報"),
    "08": ("高潮警報", "high_tide", "警報"),
    # -- 注意報 (advisories) --
    "10": ("大雨注意報", "heavy_rain", "注意報"),
    "12": ("大雪注意報", "other", "注意報"),
    "13": ("風雪注意報", "storm_snow", "注意報"),
    "14": ("雷注意報", "other", "注意報"),
    "15": ("強風注意報", "storm", "注意報"),
    "16": ("波浪注意報", "high_wave", "注意報"),
    "17": ("融雪注意報", "other", "注意報"),
    "18": ("洪水注意報", "flood", "注意報"),
    "19": ("高潮注意報", "high_tide", "注意報"),
    "20": ("濃霧注意報", "other", "注意報"),
    "21": ("乾燥注意報", "other", "注意報"),
    "22": ("なだれ注意報", "other", "注意報"),
    "23": ("低温注意報", "other", "注意報"),
    "24": ("霜注意報", "other", "注意報"),
    "25": ("着氷注意報", "other", "注意報"),
    "26": ("着雪注意報", "other", "注意報"),
    # -- 特別警報 (emergency warnings) --
    "32": ("大雨特別警報", "heavy_rain", "特別警報"),
    "33": ("暴風雪特別警報", "storm_snow", "特別警報"),
    "34": ("大雪特別警報", "other", "特別警報"),
    "35": ("暴風特別警報", "storm", "特別警報"),
    "36": ("波浪特別警報", "high_wave", "特別警報"),
    "37": ("高潮特別警報", "high_tide", "特別警報"),
}

# Categories relevant to "heavy rain / storm / typhoon" per the project brief
# (大雨, 暴風, 暴風雪, 洪水, 高潮, 波浪 and their 特別警報 variants). Edit this
# list to widen/narrow what the bot notifies about -- e.g. remove
# "storm_snow" if winter-storm advisories are too noisy, or add "other" with
# a code like "06" (大雪警報) if heavy-snow alerts should be included too.
NOTIFY_CATEGORIES = {"heavy_rain", "storm", "storm_snow", "flood", "high_tide", "high_wave"}

# The actual set of warning codes the bot will notify on, derived from
# NOTIFY_CATEGORIES. Kept as an explicit constant (rather than only computing
# it inline) so it's easy to inspect/override directly if needed.
NOTIFY_WARNING_CODES: set[str] = {
    code for code, (_, category, _) in WARNING_CODE_TABLE.items() if category in NOTIFY_CATEGORIES
}

# Visual severity ranking + emoji, used by the formatter. Higher = more severe.
LEVEL_RANK = {"注意報": 1, "警報": 2, "特別警報": 3}
LEVEL_EMOJI = {"注意報": "🟡", "警報": "🟠", "特別警報": "🔴"}

# ---------------------------------------------------------------------------
# Typhoon category labels (from targetTc.json's "category" field)
# ---------------------------------------------------------------------------

TYPHOON_CATEGORY_LABELS = {
    "TD": "熱帯低気圧",
    "TS": "台風（熱帯暴風雨相当）",
    "STS": "台風（強い熱帯暴風雨相当）",
    "TY": "台風",
}

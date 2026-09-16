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

# 早期注意情報 (警報級の可能性) -- likelihood that a warning-level event will
# occur in the next few days, published well before any actual 注意報/警報 is
# issued. Found by inspecting the live JS (probability/js/*.js, minified) of
# https://www.jma.go.jp/bosai/probability/ (linked from the "早期注意情報" tile
# on https://www.jma.go.jp/bosai/#pattern=default and from map.html
# #contents=probability) on 2026-09-16, then confirmed by fetching it directly:
#   https://www.jma.go.jp/bosai/probability/data/probability/r8/130000.json
# returned live, current data (reportDatetime matched the day of testing).
# Same {office} 6-digit code as WARNING_URL_TEMPLATE, and the same "r8"
# endpoint style, which is a good sign it's on the same current (post
# 2026-05-29) system rather than a frozen leftover.
#
# Response shape (verified live): a JSON array of exactly 2 report objects --
#   [0] short-range outlook: ~next 2 days, timeDefines in 6-hour (PT6H) steps.
#   [1] weekly outlook: days 3-7 ahead, timeDefines in 1-day (P1D) steps.
# Both have the same per-area shape: timeSeries[0].areas[] keyed by class10
# area code (same codes as data/area_master.json / WARNING_URL_TEMPLATE),
# each with a "properties" list of {"type": <phenomenon name>, "probabilities":
# [...]} parallel to that report's timeDefines. Observed "type" values (see
# EARLY_WARNING_TYPE_CATEGORY below) differ slightly in wording between [0]
# and [1] for the same phenomenon (e.g. "大雨の警報級の可能性" vs "雨の警報級の
# 可能性"). Each probability value observed live was either "" (no elevated
# risk), "中" (medium), or "高" (high) -- confirmed against the page's own JS,
# which maps exactly these two non-empty strings to its "high"/"medium" CSS
# classes and treats anything else as unstyled/absent.
EARLY_WARNING_URL_TEMPLATE = "https://www.jma.go.jp/bosai/probability/data/probability/r8/{office}.json"

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

# ---------------------------------------------------------------------------
# 早期注意情報 (警報級の可能性) category table
#
# Maps the raw "type" string from EARLY_WARNING_URL_TEMPLATE's JSON to one of
# our own category keys, mirroring the WARNING_CODE_TABLE / NOTIFY_CATEGORIES
# pattern above so this is easy to extend to other phenomena later. MVP scope
# (per project brief) is 大雨 (heavy rain) only -- the other phenomena JMA
# publishes here (土砂災害/landslide, 雪/snow, 風(風雪)/wind, 波/wave, 潮位/tide)
# are listed but deliberately left unmapped for now.
# ---------------------------------------------------------------------------

EARLY_WARNING_TYPE_CATEGORY: dict[str, str] = {
    "大雨の警報級の可能性": "heavy_rain",  # short-range (~2日先) product's label
    "雨の警報級の可能性": "heavy_rain",  # weekly (3-7日先) product's label for the same risk
    # -- unmapped for now; add an entry here (and to
    #    NOTIFY_EARLY_WARNING_CATEGORIES below) to start notifying on it --
    # "土砂災害の警報級の可能性": "landslide",
    # "雪の警報級の可能性": "snow",
    # "風（風雪）の警報級の可能性": "wind",
    # "波の警報級の可能性": "wave",
    # "潮位の警報級の可能性": "tide",
}

# Which categories to actually notify on (MVP: heavy rain only).
NOTIFY_EARLY_WARNING_CATEGORIES = {"heavy_rain"}

NOTIFY_EARLY_WARNING_TYPES: set[str] = {
    type_ for type_, category in EARLY_WARNING_TYPE_CATEGORY.items() if category in NOTIFY_EARLY_WARNING_CATEGORIES
}

EARLY_WARNING_CATEGORY_LABELS = {"heavy_rain": "大雨"}

# A handful of areas in this product are reported at a coarser grouping than
# our class10 area master (data/area_master.json, built from JMA's official
# area.json) knows about -- e.g. Tokyo's early-warning data uses a single
# merged "伊豆諸島" (130100) instead of the north/south split (130020/130030)
# used by WARNING_URL_TEMPLATE. These names were NOT guessed: they were
# cross-verified against JMA's own (already relied upon elsewhere) weekly
# forecast endpoint -- https://www.jma.go.jp/bosai/forecast/data/forecast/
# {office}.json -- which uses the same merged-area codes with an explicit
# "name" field. Checked live on 2026-09-16 for every such code seen in a
# nationwide probability/r8 fetch (020200, 030100, 070100, 130100); add more
# here if a future run logs an unrecognized area code for this product.
EARLY_WARNING_AREA_NAME_OVERRIDES: dict[str, str] = {
    "020200": "下北・三八上北",
    "030100": "沿岸",
    "070100": "中通り・浜通り",
    "130100": "伊豆諸島",
}

# Likelihood ranking + emoji. Deliberately NOT reusing LEVEL_EMOJI (🟡🟠🔴,
# which mean an advisory/warning has actually been issued) -- these emoji are
# for a "might happen in a few days" heads-up and must look clearly less
# urgent than an issued warning.
LIKELIHOOD_RANK = {"中": 1, "高": 2}
LIKELIHOOD_EMOJI = {"中": "👀", "高": "🔎"}

# ---------------------------------------------------------------------------
# Representative reference points for rough typhoon-distance display
#
# One approximate city/coordinate per major region, used only to say things
# like "沖縄地方まで約320km" instead of raw lat/lon in typhoon notifications.
# These are NOT authoritative regional boundaries or JMA-defined centroids --
# just well-known city coordinates picked for readability.
# ---------------------------------------------------------------------------

REGION_REFERENCE_POINTS: dict[str, tuple[float, float]] = {
    "北海道": (43.0642, 141.3469),  # 札幌
    "東北": (38.2682, 140.8694),  # 仙台
    "関東": (35.6812, 139.7671),  # 東京
    "中部": (35.1815, 136.9066),  # 名古屋
    "近畿": (34.6937, 135.5023),  # 大阪
    "中国": (34.3853, 132.4553),  # 広島
    "四国": (33.8392, 132.7657),  # 松山
    "九州": (33.5904, 130.4017),  # 福岡
    "沖縄": (26.2124, 127.6809),  # 那覇
}

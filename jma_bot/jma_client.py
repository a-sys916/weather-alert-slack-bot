"""Fetches and parses JMA's public (unofficial) disaster-prevention JSON feeds.

These endpoints are not an official, documented, or supported API -- they are
the same JSON files JMA's own web pages (www.jma.go.jp/bosai/...) fetch from
client-side JavaScript. They can change shape or move without notice (indeed,
JMA moved both the warning and typhoon endpoints in May 2026 -- see the long
comment in config.py). Every network call here is wrapped so that one bad
response (timeout, 404, unexpected JSON shape) is logged and skipped instead
of crashing the whole poll cycle.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import config

logger = logging.getLogger(__name__)


class JmaFetchError(Exception):
    """Raised internally for a single failed request; callers catch it and move on."""


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": config.REQUEST_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise JmaFetchError(f"HTTP {exc.code} fetching {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise JmaFetchError(f"network error fetching {url}: {exc}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise JmaFetchError(f"invalid JSON from {url}: {exc}") from exc


# ---------------------------------------------------------------------------
# Area master (offices / class10 regions -> Japanese names)
# ---------------------------------------------------------------------------


@dataclass
class AreaMaster:
    offices: dict  # office code -> {"name": ..., "enName": ...}
    class10s: dict  # class10 code -> {"name": ..., "enName": ..., "parent": office code}

    def office_name(self, code: str) -> str:
        return self.offices.get(code, {}).get("name", code)

    def area_name(self, code: str) -> str:
        """Best-effort human-readable name for any area code we might see."""
        if code in self.class10s:
            return self.class10s[code]["name"]
        if code in self.offices:
            return self.offices[code]["name"]
        if code in config.EARLY_WARNING_AREA_NAME_OVERRIDES:
            return config.EARLY_WARNING_AREA_NAME_OVERRIDES[code]
        return code  # fall back to the raw code rather than crash

    def office_codes(self) -> list[str]:
        codes = list(self.offices.keys())
        if config.OFFICE_CODE_FILTER:
            codes = [c for c in codes if c in config.OFFICE_CODE_FILTER]
        return sorted(codes)


def load_area_master() -> AreaMaster:
    """Loads the checked-in area code master (see scripts/fetch_area_master.py).

    We deliberately do NOT fetch this from the network on every run: it is
    ~250KB and essentially static (JMA only touches it for rare municipal
    reorganizations), so re-downloading it every 15 minutes would be wasteful
    and adds an extra failure point for no benefit.
    """
    with open(config.AREA_MASTER_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return AreaMaster(offices=data["offices"], class10s=data["class10s"])


# ---------------------------------------------------------------------------
# Warnings / advisories
# ---------------------------------------------------------------------------


@dataclass
class WarningEntry:
    office_code: str
    area_code: str  # class10 area code, e.g. "130010"
    warning_code: str  # e.g. "03"
    status: str  # "発表" / "継続" / "解除" / etc.
    additions: list = field(default_factory=list)


def fetch_office_warnings(office_code: str) -> tuple[str | None, list[WarningEntry]]:
    """Fetches the current warning/advisory state for one office.

    Returns (report_datetime, entries). Only currently-active or
    just-cancelled warnings are included (areas with
    "発表警報・注意報はなし" contribute no entries).

    Why we take "the latest array element" as current state: this endpoint
    (see WARNING_URL_TEMPLATE in config.py) returns a JSON array of the last
    few bulletins issued for this office, not a single merged snapshot. Each
    bulletin element was observed (2026-09-16, live data) to already list
    every class10 area for the office -- including areas with no active
    warning at all -- which indicates each element IS a complete snapshot at
    that report's issue time, not a partial diff. So the element with the
    latest reportDatetime is simply the most up-to-date complete snapshot.
    """
    url = config.WARNING_URL_TEMPLATE.format(office=office_code)
    try:
        payload = _get_json(url)
    except JmaFetchError as exc:
        logger.warning("skipping office %s: %s", office_code, exc)
        return None, []

    if not isinstance(payload, list) or not payload:
        logger.warning("skipping office %s: unexpected warning payload shape", office_code)
        return None, []

    try:
        latest = max(payload, key=lambda item: item.get("reportDatetime", ""))
        report_datetime = latest.get("reportDatetime")
        class10_items = latest.get("warning", {}).get("class10Items", [])

        entries: list[WarningEntry] = []
        for area in class10_items:
            area_code = area.get("areaCode")
            for kind in area.get("kinds", []):
                code = kind.get("code")
                status = kind.get("status")
                if not code or not status:
                    continue  # "発表警報・注意報はなし" entries have no code
                entries.append(
                    WarningEntry(
                        office_code=office_code,
                        area_code=area_code,
                        warning_code=code,
                        status=status,
                        additions=kind.get("additions", []),
                    )
                )
        return report_datetime, entries
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("skipping office %s: could not parse warning JSON (%s)", office_code, exc)
        return None, []


def fetch_all_warnings(area_master: AreaMaster) -> tuple[list[WarningEntry], dict[str, str]]:
    """Fetches and merges warning entries across every monitored office.

    Errors for individual offices are logged and skipped so that e.g. one
    office's endpoint being briefly unavailable doesn't take down the whole
    nationwide poll. Returns (entries, report_datetime_by_office) -- the
    latter lets the formatter show a meaningful "いつ" (when) even though
    different offices' bulletins are issued at different times.
    """
    all_entries: list[WarningEntry] = []
    report_datetimes: dict[str, str] = {}
    for office_code in area_master.office_codes():
        report_dt, entries = fetch_office_warnings(office_code)
        if report_dt:
            report_datetimes[office_code] = report_dt
        all_entries.extend(entries)
    return all_entries, report_datetimes


# ---------------------------------------------------------------------------
# Early warning (早期注意情報 / 警報級の可能性)
# ---------------------------------------------------------------------------


@dataclass
class EarlyWarningEntry:
    """One area's current likelihood state for one notify-worthy category.

    `level` is the highest likelihood ("高" or "中") found anywhere across
    the combined short-range (~2 days) + weekly (3-7 days) outlook windows,
    and `earliest_time` is the first timeDefine at which that level appears
    -- enough for the formatter to show a rough "いつ" (e.g. 明後日) without
    reproducing JMA's whole day-by-day table.
    """

    office_code: str
    area_code: str
    category: str  # e.g. "heavy_rain"
    level: str  # "高" | "中"
    earliest_time: str | None


def fetch_office_early_warnings(office_code: str) -> tuple[str | None, list[EarlyWarningEntry]]:
    """Fetches 早期注意情報 (警報級の可能性) for one office.

    See EARLY_WARNING_URL_TEMPLATE in config.py for how this endpoint was
    found and verified, and for the payload shape. We only look at
    categories in config.NOTIFY_EARLY_WARNING_CATEGORIES (MVP: heavy rain);
    everything else in the payload is ignored.
    """
    url = config.EARLY_WARNING_URL_TEMPLATE.format(office=office_code)
    try:
        payload = _get_json(url)
    except JmaFetchError as exc:
        logger.warning("skipping office %s early-warning: %s", office_code, exc)
        return None, []

    if not isinstance(payload, list) or not payload:
        logger.warning("skipping office %s early-warning: unexpected payload shape", office_code)
        return None, []

    report_datetime: str | None = None
    # (area_code, category) -> [(timeDefine, level), ...]
    occurrences: dict[tuple[str, str], list[tuple[str, str]]] = {}

    try:
        for part in payload:
            report_datetime = part.get("reportDatetime") or report_datetime
            for ts in part.get("timeSeries", []):
                time_defines = ts.get("timeDefines", [])
                for area in ts.get("areas", []):
                    area_code = area.get("code")
                    for prop in area.get("properties", []):
                        category = config.EARLY_WARNING_TYPE_CATEGORY.get(prop.get("type"))
                        if category is None or category not in config.NOTIFY_EARLY_WARNING_CATEGORIES:
                            continue
                        for time_str, level in zip(time_defines, prop.get("probabilities", [])):
                            if level not in config.LIKELIHOOD_RANK:
                                continue  # "" (no elevated risk) or an unmapped value
                            occurrences.setdefault((area_code, category), []).append((time_str, level))
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("skipping office %s early-warning: could not parse (%s)", office_code, exc)
        return None, []

    entries: list[EarlyWarningEntry] = []
    for (area_code, category), points in occurrences.items():
        best_level = max((level for _, level in points), key=lambda lv: config.LIKELIHOOD_RANK[lv])
        earliest_time = min(t for t, lv in points if lv == best_level)
        entries.append(EarlyWarningEntry(office_code, area_code, category, best_level, earliest_time))

    return report_datetime, entries


def fetch_all_early_warnings(area_master: AreaMaster) -> tuple[list[EarlyWarningEntry], dict[str, str]]:
    """Same idea as fetch_all_warnings, but for 早期注意情報 (警報級の可能性)."""
    all_entries: list[EarlyWarningEntry] = []
    report_datetimes: dict[str, str] = {}
    for office_code in area_master.office_codes():
        report_dt, entries = fetch_office_early_warnings(office_code)
        if report_dt:
            report_datetimes[office_code] = report_dt
        all_entries.extend(entries)
    return all_entries, report_datetimes


# ---------------------------------------------------------------------------
# Typhoons
# ---------------------------------------------------------------------------


@dataclass
class TyphoonTrackPoint:
    label_jp: str  # e.g. "実況" / "予報　６９時間後"
    advanced_hours: int
    valid_time: str | None
    center: tuple | None  # (lat, lon)
    has_storm_warning_area: bool


@dataclass
class TyphoonInfo:
    event_id: str
    typhoon_number: str
    category: str  # "TD" / "TS" / "STS" / "TY"
    issue_time: str | None  # from targetTc.json
    forecast_issue_time: str | None  # from forecast.json's "title" part
    track: list[TyphoonTrackPoint]


def fetch_active_typhoon_ids() -> list[dict]:
    """Returns the list of currently-tracked systems from targetTc.json.

    Each item looks like:
      {"tropicalCyclone": "TC2630", "typhoonNumber": "a", "category": "TD",
       "issue": "2026-09-16T13:15:00+09:00"}
    """
    try:
        payload = _get_json(config.TYPHOON_LIST_URL)
    except JmaFetchError as exc:
        logger.warning("could not fetch active typhoon list: %s", exc)
        return []
    if not isinstance(payload, list):
        logger.warning("unexpected typhoon list payload shape")
        return []
    return payload


def fetch_typhoon_forecast(event_id: str) -> list[dict] | None:
    """Returns the raw forecast.json payload for one typhoon event, or None on error."""
    url = config.TYPHOON_FORECAST_URL_TEMPLATE.format(event_id=event_id)
    try:
        payload = _get_json(url)
    except JmaFetchError as exc:
        logger.warning("skipping typhoon %s: %s", event_id, exc)
        return None
    if not isinstance(payload, list):
        logger.warning("skipping typhoon %s: unexpected forecast payload shape", event_id)
        return None
    return payload


def fetch_all_typhoons() -> list[TyphoonInfo]:
    """Fetches every currently-active tropical cyclone with its forecast track."""
    results: list[TyphoonInfo] = []
    for item in fetch_active_typhoon_ids():
        event_id = item.get("tropicalCyclone")
        if not event_id:
            continue
        forecast = fetch_typhoon_forecast(event_id)
        track: list[TyphoonTrackPoint] = []
        forecast_issue_time = None
        if forecast:
            try:
                for part in forecast:
                    if part.get("part") == "title":
                        forecast_issue_time = part.get("issue", {}).get("JST")
                        continue
                    label = part.get("part", {})
                    label_jp = label.get("jp", "") if isinstance(label, dict) else str(label)
                    center = part.get("center")
                    track.append(
                        TyphoonTrackPoint(
                            label_jp=label_jp,
                            advanced_hours=part.get("advancedHours", 0),
                            valid_time=part.get("validtime", {}).get("JST"),
                            center=tuple(center) if center else None,
                            has_storm_warning_area="stormWarningArea" in part,
                        )
                    )
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("could not parse forecast track for %s: %s", event_id, exc)

        results.append(
            TyphoonInfo(
                event_id=event_id,
                typhoon_number=item.get("typhoonNumber", ""),
                category=item.get("category", ""),
                issue_time=item.get("issue"),
                forecast_issue_time=forecast_issue_time,
                track=track,
            )
        )
    return results

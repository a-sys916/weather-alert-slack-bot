"""Dedup/state tracking so we don't repeat the same Slack notification forever.

State is a small JSON file (state/seen.json) keyed by "office:area:warning
code" for weather warnings and by typhoon eventId for typhoons. Only three
kinds of events are considered notification-worthy:

  1. A warning/advisory is newly announced for an area we haven't already
     notified about (or notified about and it was later cleared).
  2. Its level changes (注意報 -> 警報 -> 特別警報, in either direction).
  3. It is cancelled (解除) after we had previously notified about it.

Everything else (an unchanged "継続" status on a run where nothing changed)
is deliberately silent.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from . import config
from .jma_client import TyphoonInfo, WarningEntry

logger = logging.getLogger(__name__)


def load_state() -> dict:
    if not config.STATE_PATH.exists():
        return {"warnings": {}, "typhoons": {}}
    try:
        with open(config.STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("could not read state file (%s), starting fresh: %s", config.STATE_PATH, exc)
        return {"warnings": {}, "typhoons": {}}
    data.setdefault("warnings", {})
    data.setdefault("typhoons", {})
    return data


def save_state(state: dict) -> None:
    config.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(config.STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def _warning_key(entry: WarningEntry) -> str:
    return f"{entry.office_code}:{entry.area_code}:{entry.warning_code}"


@dataclass
class WarningChange:
    change_type: str  # "new" | "level_change" | "cancelled"
    office_code: str
    area_code: str
    warning_code: str
    name: str
    level: str
    previous_level: str | None
    status_raw: str
    additions: list = field(default_factory=list)


def diff_warnings(entries: list[WarningEntry], state: dict) -> list[WarningChange]:
    """Compares freshly-fetched warning entries against saved state.

    Mutates `state["warnings"]` in place to reflect the new current state
    (callers should save `state` afterwards). Returns the list of changes
    worth notifying about.
    """
    changes: list[WarningChange] = []
    seen_keys: set[str] = set()

    for entry in entries:
        if entry.warning_code not in config.NOTIFY_WARNING_CODES:
            continue  # not a heavy-rain/storm/flood/typhoon-relevant category

        name, _category, level = config.WARNING_CODE_TABLE.get(entry.warning_code, (entry.warning_code, "other", "?"))
        key = _warning_key(entry)
        previous = state["warnings"].get(key)

        if entry.status == "解除":
            if previous is not None:
                changes.append(
                    WarningChange(
                        change_type="cancelled",
                        office_code=entry.office_code,
                        area_code=entry.area_code,
                        warning_code=entry.warning_code,
                        name=name,
                        level=level,
                        previous_level=previous.get("level"),
                        status_raw=entry.status,
                        additions=entry.additions,
                    )
                )
                del state["warnings"][key]
            # else: cancelling something we never notified about -> ignore
            continue

        seen_keys.add(key)
        if previous is None:
            changes.append(
                WarningChange(
                    change_type="new",
                    office_code=entry.office_code,
                    area_code=entry.area_code,
                    warning_code=entry.warning_code,
                    name=name,
                    level=level,
                    previous_level=None,
                    status_raw=entry.status,
                    additions=entry.additions,
                )
            )
        elif previous.get("level") != level:
            changes.append(
                WarningChange(
                    change_type="level_change",
                    office_code=entry.office_code,
                    area_code=entry.area_code,
                    warning_code=entry.warning_code,
                    name=name,
                    level=level,
                    previous_level=previous.get("level"),
                    status_raw=entry.status,
                    additions=entry.additions,
                )
            )
        # (else: still active, same level -> no change, stay silent)

        state["warnings"][key] = {
            "status": entry.status,
            "level": level,
            "warning_code": entry.warning_code,
        }

    return changes


@dataclass
class TyphoonChange:
    change_type: str  # "new" | "updated"
    event_id: str
    info: TyphoonInfo


def diff_typhoons(typhoons: list[TyphoonInfo], state: dict) -> list[TyphoonChange]:
    """Same idea as diff_warnings but for tropical cyclones.

    A typhoon is notify-worthy when it's newly tracked, or when JMA has
    issued a new forecast bulletin for it (forecast_issue_time changed) or
    its category changed (e.g. TD -> TY). Prunes typhoons that are no longer
    in the active list (dissipated / left the area of responsibility).
    """
    changes: list[TyphoonChange] = []
    active_ids = {t.event_id for t in typhoons}

    for typhoon in typhoons:
        previous = state["typhoons"].get(typhoon.event_id)
        if previous is None:
            changes.append(TyphoonChange(change_type="new", event_id=typhoon.event_id, info=typhoon))
        elif (
            previous.get("forecast_issue_time") != typhoon.forecast_issue_time
            or previous.get("category") != typhoon.category
        ):
            changes.append(TyphoonChange(change_type="updated", event_id=typhoon.event_id, info=typhoon))

        state["typhoons"][typhoon.event_id] = {
            "issue_time": typhoon.issue_time,
            "forecast_issue_time": typhoon.forecast_issue_time,
            "category": typhoon.category,
        }

    # Drop typhoons JMA is no longer tracking so the state file doesn't grow forever.
    for stale_id in list(state["typhoons"].keys()):
        if stale_id not in active_ids:
            del state["typhoons"][stale_id]

    return changes

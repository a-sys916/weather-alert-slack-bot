#!/usr/bin/env python3
"""Refresh data/area_master.json from JMA's official area code master.

Why this exists: https://www.jma.go.jp/bosai/common/const/area.json maps every
JMA area code (office / class10 / class15 / class20) to its Japanese name.
It is ~250KB and changes only rarely (e.g. municipal mergers), so instead of
downloading it on every bot run we fetch it once here and check a trimmed
copy into the repo. The bot only needs two levels:

  - "offices"  : prefecture-level codes (e.g. 130000 = 東京都) -- used to know
                 which per-office warning JSON files to poll.
  - "class10s" : primary sub-division codes (e.g. 130010 = 東京地方) -- this is
                 the granularity JMA's warning/warning class10Items use, and
                 it is a readable "region" name for Slack notifications.

Re-run this script (`python scripts/fetch_area_master.py`) if JMA adds/renames
an office or region and the bot starts showing raw codes instead of names.
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

AREA_JSON_URL = "https://www.jma.go.jp/bosai/common/const/area.json"
OUTPUT_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "area_master.json"


def main() -> int:
    print(f"Fetching {AREA_JSON_URL} ...")
    req = urllib.request.Request(AREA_JSON_URL, headers={"User-Agent": "weather-alert-slack-bot/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        full = json.load(resp)

    trimmed = {
        "offices": {
            code: {"name": v["name"], "enName": v.get("enName", "")}
            for code, v in full.get("offices", {}).items()
        },
        "class10s": {
            code: {"name": v["name"], "enName": v.get("enName", ""), "parent": v.get("parent", "")}
            for code, v in full.get("class10s", {}).items()
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(trimmed, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT_PATH} ({len(trimmed['offices'])} offices, {len(trimmed['class10s'])} class10 regions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

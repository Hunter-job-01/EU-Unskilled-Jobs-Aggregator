#!/usr/bin/env python3
"""EU27 coverage matrix: catalogue vs observed runs (source-expansion).

Combines config/source_registry.json (catalogue, names only) with
data/source_health.json and data/run_status.json (observed runs). Never
claims live coverage from catalogue entries; a country with no healthy
observed source is uncovered, not "empty".
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from src.config import load_config
from src.eu_location import EU27
from src.source_health import load_health
from src.source_registry import coverage_summary, load_registry

CATEGORY_GROUPS = [
    ("official", {"official", "official_data", "official_sector",
                  "official_gateway", "official_pan_europe"}),
    ("general/local", {"general", "classifieds", "classifieds_sector"}),
    ("sector", {"sector"}),
    ("agency/employer", {"agency", "employer"}),
    ("aggregator/ATS", {"aggregator", "ats_route", "remote_low_fit"}),
]


def main() -> int:
    registry = load_registry()
    cfg = load_config()
    health = load_health(cfg["storage"].get("data_dir", "data"))
    summary = coverage_summary(registry)

    print("EU27 source catalogue (research {}, names, not live coverage)".format(
        registry["research_date"]))
    print("  total {} | active-existing {} | probe-candidates {} | terms-review {} | "
          "permission/onboarding {} | manual-only {} | excluded {}".format(
              summary["total"], summary["by_gate"].get("active_existing", 0),
              summary["by_gate"].get("probe_then_owner_approval", 0),
              summary["by_gate"].get("terms_review", 0),
              summary["by_gate"].get("permission_or_onboarding", 0),
              summary["by_gate"].get("manual_only", 0),
              summary["by_gate"].get("excluded", 0)))

    by_country = {}
    for s in registry["sources"]:
        for code in s.get("countries") or []:
            by_country.setdefault(code, []).append(s)

    latest = health.get("latest_by_source_country") or {}
    print("\n{:<4} {:<9} {:<13} {:<7} {:<15} {:<14} {}".format(
        "CC", "official", "general", "sector", "agency/employer", "agg/ATS", "observed source runs"))
    for code in sorted(EU27):
        entries = by_country.get(code, [])
        cols = []
        for label, cats in CATEGORY_GROUPS:
            cols.append(str(sum(1 for s in entries if s.get("category") in cats)))
        observed = []
        for key, rec in sorted(latest.items()):
            src, cc = key.split("|", 1)
            if cc == code:
                mark = {"ok": "+", "partial": "~", "failed": "!", "skipped": "-"}.get(
                    rec.get("status"), "?")
                observed.append("{}{}".format(src, mark))
        print("{:<4} {:<9} {:<13} {:<7} {:<15} {:<14} {}".format(
            code, *cols, ", ".join(observed) if observed else "(none observed)"))
    print("\nLegend: + ok, ~ partial, ! failed, - skipped. Columns count CATALOGUE")
    print("entries per category, not live connectors. (none observed) = no run")
    print("recorded in data/source_health.json yet - uncovered, not empty.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

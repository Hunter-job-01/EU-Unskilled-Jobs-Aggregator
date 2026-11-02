#!/usr/bin/env python3
"""One small READ-ONLY live probe per documented free candidate
(source-expansion Phase 6).

Opt-in and isolated: requires --live, writes only to --out (default
probe-output/), never touches data/, stored jobs, notified flags,
deployed config or the notifier. Each probe records the exact URL, time,
request count, raw rows, accepted/quarantined/rejected counts and
errors. A probe proves endpoint behaviour only - it is not a tested
production adapter run and zero rows is inconclusive, not a verdict.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/", 2)[0])

import requests

from src.config import load_config
from src.eu_location import classify_location

UA = {"User-Agent": "EU-Unskilled-Jobs-Aggregator one-off research probe (contact: repo owner)"}
TIMEOUT = 30


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def probe_arbeitnow():
    url = "https://www.arbeitnow.com/api/job-board-api?page=1"
    started = _stamp()
    resp = requests.get(url, headers=UA, timeout=TIMEOUT)
    data = resp.json()
    rows = data.get("data") or []
    counts = {"rows": len(rows), "eu": 0, "non_eu": 0, "quarantined": 0}
    samples = []
    for row in rows:
        v = classify_location(location_text=str(row.get("location") or ""),
                              remote=bool(row.get("remote")))
        key = "eu" if v.status == "eu" else ("non_eu" if v.status == "non_eu" else "quarantined")
        counts[key] += 1
        if len(samples) < 5:
            samples.append({"title": row.get("title"), "location": row.get("location"),
                            "remote": row.get("remote"), "verdict": v.status,
                            "country": v.country})
    return {"source": "arbeitnow", "url": url, "auth": "none", "requests": 1,
            "started_utc": started, "http_status": resp.status_code,
            "counts": counts, "samples": samples, "errors": []}


def probe_sweden():
    url = "https://jobsearch.api.jobtechdev.se/search"
    started = _stamp()
    resp = requests.get(url, params={"q": "lagerarbetare", "limit": 10, "offset": 0},
                        headers=UA, timeout=TIMEOUT)
    data = resp.json()
    hits = data.get("hits") or []
    counts = {"rows": len(hits), "eu_se": 0, "other": 0}
    samples = []
    for hit in hits:
        wa = hit.get("workplace_address") or {}
        cc = wa.get("country_code")
        structured = cc if isinstance(cc, str) and cc.isalpha() else wa.get("country")
        v = classify_location(structured_country=structured,
                              location_text=str(wa.get("municipality") or ""))
        counts["eu_se" if (v.status == "eu" and v.country == "SE") else "other"] += 1
        if len(samples) < 5:
            samples.append({"title": hit.get("headline"),
                            "workplace_country": wa.get("country"),
                            "verdict": v.status, "country": v.country})
    return {"source": "sweden_jobsearch", "url": resp.url, "auth": "none (re-confirm: docs mention a key)",
            "requests": 1, "started_utc": started, "http_status": resp.status_code,
            "counts": counts, "samples": samples, "errors": []}


def probe_czech_metadata():
    # Metadata + schema headers only, no bulk download of the 185MB export.
    started = _stamp()
    errors = []
    meta = {}
    for label, url in (("schema", "https://data.mpsv.cz/od/soubory/volna-mista/volna-mista.schema.json"),
                       ("distribution_head", "https://data.mpsv.cz/od/soubory/volna-mista/volna-mista.json")):
        try:
            if label == "schema":
                resp = requests.get(url, headers=UA, timeout=TIMEOUT)
                payload = resp.json()
                meta["schema_status"] = resp.status_code
                meta["schema_title"] = payload.get("title")
            else:
                resp = requests.head(url, headers=UA, timeout=TIMEOUT, allow_redirects=True)
                meta["distribution_status"] = resp.status_code
                meta["distribution_bytes"] = resp.headers.get("content-length")
        except Exception as exc:  # noqa: BLE001 - probe records, never crashes
            errors.append("{}: {}".format(label, exc))
    return {"source": "czech_mpsv", "url": "https://data.mpsv.cz/web/data/volna-mista-za-celou-cr",
            "auth": "none", "requests": 2, "started_utc": started, "metadata": meta,
            "note": "headers/schema only; adapter streams the export, enabled stays false",
            "errors": errors}


def probe_latvia_metadata():
    started = _stamp()
    errors = []
    meta = {}
    try:
        resp = requests.get("https://data.gov.lv/dati/eng/api/3/action/package_show",
                            params={"id": "vakances"}, headers=UA, timeout=TIMEOUT)
        meta["status"] = resp.status_code
        payload = resp.json()
        result = payload.get("result") or {}
        resources = result.get("resources") or []
        meta["resources"] = [{"name": r.get("name"), "format": r.get("format"),
                              "url": r.get("url"),
                              "last_modified": r.get("last_modified") or r.get("created")}
                             for r in resources[:10]]
        csvs = [r for r in resources if str(r.get("format") or "").upper() == "CSV" and r.get("url")]
        if csvs:
            newest = max(csvs, key=lambda r: str(r.get("last_modified") or r.get("created") or ""))
            head = requests.get(str(newest["url"]), headers=UA, timeout=TIMEOUT,
                                stream=True)
            first = next(head.iter_lines(decode_unicode=True), "")
            head.close()
            meta["csv_header"] = str(first)[:400]
            meta["csv_probe_url"] = str(newest["url"])
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
    return {"source": "latvia_nva", "url": "data.gov.lv CKAN package_show/vakances + header read",
            "auth": "none", "requests": 2, "started_utc": started,
            "metadata": meta,
            "note": "metadata + header row only; full freshness check runs in the adapter",
            "errors": errors}


PROBES = {"arbeitnow": probe_arbeitnow, "sweden_jobsearch": probe_sweden,
          "czech_mpsv": probe_czech_metadata, "latvia_nva": probe_latvia_metadata}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--live", action="store_true",
                   help="actually perform the read-only network probes")
    p.add_argument("--sources", default=",".join(PROBES),
                   help="comma-separated subset of: " + ", ".join(PROBES))
    p.add_argument("--out", default="probe-output")
    args = p.parse_args(argv)
    load_config()  # validates the repo config the probes relate to
    if not args.live:
        print("Dry run. Probes are opt-in; re-run with --live to execute.")
        return 0
    import os
    os.makedirs(args.out, exist_ok=True)
    reports = []
    for name in [s.strip() for s in args.sources.split(",") if s.strip()]:
        probe = PROBES.get(name)
        if probe is None:
            print("unknown probe: {}".format(name))
            return 2
        print("probing {} ...".format(name))
        try:
            report = probe()
        except Exception as exc:  # noqa: BLE001 - probe failure is data
            report = {"source": name, "started_utc": _stamp(),
                      "errors": ["probe crashed: {}".format(exc)]}
        reports.append(report)
        time.sleep(2)  # polite spacing between distinct services
    path = "{}/probe-{}.json".format(args.out, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(reports, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("wrote {}".format(path))
    for r in reports:
        print("  {}: {}".format(r["source"], r.get("counts") or r.get("metadata") or r["errors"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

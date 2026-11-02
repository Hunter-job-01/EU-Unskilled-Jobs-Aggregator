"""Latvia NVA public-sector vacancy CSV adapter (official open data, CC0).

Validated against the live catalogue on 2026-11-02 via the CKAN API:
  package:  vakances (https://data.gov.lv/dati/eng/dataset/vakances)
  resource: daily CSV, comma-delimited, columns include Vakances_Nr,
            Aktualizacijas_datums, Vakances_nosaukums, Vieta,
            Vakances_paplasinats_apraksts (a cvvp.nva.gov.lv link),
            Alga_no / Alga_lidz (salary range)
The adapter resolves the CURRENT resource URL from the package metadata
on every run (the filename carries the export date), then validates the
newest row's freshness. This dataset is NARROWER than the whole Latvian
job market (public sector) and rows name no employer (only a
registration number) and no country in the address - the feed's
national scope is used as dataset-country evidence and recorded as such.

Disabled by default (sources.latvia_nva.enabled).
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone

from ..eu_location import classify_location
from ..models import Job
from .base import ScrapeResult, ScraperError

log = logging.getLogger(__name__)

PACKAGE_API = "https://data.gov.lv/dati/eng/api/3/action/package_show"


class LatviaNvaScraper:
    name = "latvia_nva"
    COUNTRY = "LV"

    def __init__(self, cfg: dict, http):
        self.cfg = cfg
        self.http = http
        sc = (cfg.get("sources") or {}).get("latvia_nva") or {}
        self.enabled = bool(sc.get("enabled", False))
        self.package_id = str(sc.get("package_id") or "vakances")
        self.max_rows = int(sc.get("max_rows", 20000))
        self.max_stale_days = int(sc.get("max_stale_days", 14))

    def _resolve_csv_url(self) -> str:
        """Current CSV resource URL from the dataset's own metadata."""
        resp = self.http.get(PACKAGE_API, params={"id": self.package_id})
        payload = resp.json()
        resources = ((payload or {}).get("result") or {}).get("resources") or []
        csvs = [r for r in resources
                if str(r.get("format") or "").upper() == "CSV" and r.get("url")]
        if not csvs:
            raise ScraperError("no CSV resource listed in package metadata")
        newest = max(csvs, key=lambda r: str(r.get("last_modified") or r.get("created") or ""))
        return str(newest["url"])

    @staticmethod
    def _parse_day(value: str):
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    def scrape_country(self, country_code: str, max_jobs: int) -> ScrapeResult:
        country_code = country_code.upper()
        result = ScrapeResult(source=self.name, country=country_code)
        if not self.enabled:
            result.status = "skipped"
            result.notes.append("disabled in config (sources.latvia_nva.enabled: false)")
            return result
        if country_code != self.COUNTRY:
            result.status = "skipped"
            result.notes.append("the NVA dataset covers Latvia only")
            return result
        try:
            csv_url = self._resolve_csv_url()
        except (ScraperError, ValueError, AttributeError) as exc:
            result.status = "failed"
            result.errors.append("metadata resolution failed: {}".format(exc))
            return result
        result.stats["csv_url"] = csv_url
        try:
            resp = self.http.get(csv_url)
        except ScraperError as exc:
            result.status = "failed"
            result.errors.append("download failed: {}".format(exc))
            return result
        text = resp.content.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        rows = []
        for row in reader:
            rows.append(row)
            if len(rows) >= self.max_rows:
                break
        result.stats["rows_returned"] = len(rows)
        if not rows:
            result.status = "failed"
            result.errors.append("CSV contained no data rows")
            return result
        newest = None
        for row in rows:
            day = self._parse_day(row.get("Aktualizacijas_datums"))
            if day and (newest is None or day > newest):
                newest = day
        if newest is None:
            result.notes.append("no parseable row dates; freshness unverified")
        else:
            age = (datetime.now(timezone.utc).date() - newest).days
            result.stats["newest_row_age_days"] = age
            if age > self.max_stale_days:
                result.notes.append(
                    "feed is stale: newest row is {} days old (limit {})".format(
                        age, self.max_stale_days))
        for row in rows:
            if result.fetched >= max_jobs:
                break
            title = str(row.get("Vakances_nosaukums") or "").strip()
            location = str(row.get("Vieta") or "").strip()
            url = str(row.get("Vakances_paplasinats_apraksts") or "").strip()
            verdict = classify_location(
                structured_country=None, location_text=location, dataset_country="LV")
            if verdict.status == "non_eu":
                result.stats["non_eu_rejects"] = result.stats.get("non_eu_rejects", 0) + 1
                continue
            reg = str(row.get("Iestades_registracijas_numurs") or "").strip()
            if verdict.quarantined or not title or not url or not reg:
                # Hidden/unidentified employer (no registration number) is
                # quarantined like a hidden employer elsewhere: without it,
                # uid(company|title|country) could merge distinct institutions.
                result.stats["quarantined"] = result.stats.get("quarantined", 0) + 1
                continue
            salary = ""
            if row.get("Alga_no") or row.get("Alga_lidz"):
                salary = "alga {}-{} EUR".format(row.get("Alga_no") or "?",
                                                 row.get("Alga_lidz") or "?")
            job = Job(
                title=title,
                # Rows carry an institution registration number, not a name.
                # Surface it as a labelled identifier (never an invented name)
                # so uid(company|title|country) stays distinct per institution.
                company="LV iestādes reģ. nr. {}".format(reg),
                country="LV",
                source=self.name,
                source_id=str(row.get("Vakances_Nr") or url),
                url=url,
                description=("[NVA evidence] kategorija: {}; {}; iestādes reģ. nr. {}".format(
                    row.get("Vakances_kategorija") or "?", salary, reg)).strip(),
                location=location,
                posted_date=(lambda d: d.isoformat() if d else None)(
                    self._parse_day(row.get("Aktualizacijas_datums"))),
            )
            result.jobs.append(job)
        return result

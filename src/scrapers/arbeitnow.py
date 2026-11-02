"""Arbeitnow public job-board API adapter (documented free route, no key).

Endpoint: GET https://www.arbeitnow.com/api/job-board-api?page=N
The same operator also runs a separate UK API; only the EU board above
is used here, and every row still passes EU27 location grounding.
Remote rows need an explicit EU country constraint in their location
text; "remote" alone is quarantined, never admitted as EU-located.

Disabled by default (sources.arbeitnow.enabled). Rows are fetched once
per process run and reused across country queries, so a full-EU run
costs one bounded page sequence, not one per country.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..eu_location import EU27, classify_location
from ..models import Job
from .base import ScrapeResult, ScraperError, polite_sleep, strip_html

log = logging.getLogger(__name__)

API_URL = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowScraper:
    name = "arbeitnow"

    def __init__(self, cfg: dict, http):
        self.cfg = cfg
        self.http = http
        sc = (cfg.get("sources") or {}).get("arbeitnow") or {}
        self.enabled = bool(sc.get("enabled", False))
        self.max_pages = int(sc.get("max_pages", 2))
        self.api_url = str(sc.get("api_url") or API_URL)
        self.req_delay = tuple(sc.get("delay_between_requests_sec",
                                      cfg["batch"].get("delay_between_requests_sec", [1.5, 4.0])))
        self._rows = None  # fetched rows shared across country queries

    def _fetch_rows(self, result: ScrapeResult) -> list:
        rows = []
        pages_done = 0
        for page in range(1, self.max_pages + 1):
            try:
                resp = self.http.get(self.api_url, params={"page": page})
                data = resp.json()
            except ScraperError as exc:
                result.errors.append("page {}: {}".format(page, exc))
                break
            except (ValueError, AttributeError) as exc:
                result.errors.append("page {}: unparsable response: {}".format(page, exc))
                break
            batch = data.get("data") if isinstance(data, dict) else None
            if not isinstance(batch, list):
                result.errors.append("page {}: unexpected schema (no data list)".format(page))
                break
            rows.extend(batch)
            pages_done += 1
            if not batch:
                break
            polite_sleep(self.req_delay)
        result.stats["pages_fetched"] = pages_done
        result.stats["rows_returned"] = len(rows)
        return rows

    def scrape_country(self, country_code: str, max_jobs: int) -> ScrapeResult:
        country_code = country_code.upper()
        result = ScrapeResult(source=self.name, country=country_code)
        if not self.enabled:
            result.status = "skipped"
            result.notes.append("disabled in config (sources.arbeitnow.enabled: false)")
            return result
        if country_code not in EU27:
            result.status = "skipped"
            result.notes.append("{} is outside the EU27 allowlist".format(country_code))
            return result
        if self._rows is None:
            self._rows = self._fetch_rows(result)
        else:
            result.notes.append("reused {} rows fetched earlier this run".format(len(self._rows)))
        seen = set()
        for row in self._rows:
            if result.fetched >= max_jobs:
                break
            verdict = classify_location(
                structured_country=None,
                location_text=str(row.get("location") or ""),
                remote=bool(row.get("remote")),
            )
            if verdict.status == "non_eu":
                result.stats["non_eu_rejects"] = result.stats.get("non_eu_rejects", 0) + 1
                continue
            if verdict.quarantined:
                result.stats["quarantined"] = result.stats.get("quarantined", 0) + 1
                continue
            if verdict.country != country_code:
                continue
            url = str(row.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            created = row.get("created_at")
            try:
                posted = datetime.fromtimestamp(int(created), tz=timezone.utc).date().isoformat()
            except (TypeError, ValueError, OSError, OverflowError):
                posted = None
            job = Job(
                title=str(row.get("title") or "").strip(),
                company=str(row.get("company_name") or "").strip(),
                country=country_code,
                source=self.name,
                source_id=str(row.get("slug") or url),
                url=url,
                description=strip_html(row.get("description") or "")[:20000],
                location=str(row.get("location") or "").strip(),
                posted_date=posted,
            )
            if job.title:
                result.jobs.append(job)
        if result.errors:
            result.status = "partial" if result.jobs else "failed"
        return result

"""Shared HTTP plumbing: user-agent rotation, exponential backoff,
optional proxy fallback (never required), and polite randomized delays."""
from __future__ import annotations

import html
import itertools
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
]


class ScraperError(Exception):
    """A source request failed after all retries."""


@dataclass
class ScrapeResult:
    """Outcome of one scraper x one country. Status: ok | partial | failed | skipped."""

    source: str
    country: str
    status: str = "ok"
    jobs: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    # Optional honest counters for source-health reporting (Phase 1
    # observability). Keys an adapter did not measure stay absent.
    stats: dict = field(default_factory=dict)

    @property
    def fetched(self) -> int:
        return len(self.jobs)

    def to_status(self) -> dict:
        return {"status": self.status, "fetched": self.fetched,
                "errors": self.errors[:10], "notes": self.notes[:10]}


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    """Remove tags, unescape entities and collapse whitespace."""
    if not text:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", str(text)))
    return re.sub(r"\s+", " ", text).strip()


def polite_sleep(delay_range) -> None:
    """Randomized polite delay; (0, 0) disables sleeping (tests)."""
    lo, hi = delay_range
    if hi and hi > 0:
        time.sleep(random.uniform(lo, hi))


class HttpClient:
    """requests wrapper with UA rotation, backoff and proxy fallback.

    Order per request: direct attempts with exponential backoff; after the
    first failure, optionally retry through configured proxies; finally one
    more direct attempt. Proxies are a fallback only - the system works
    without them and never crashes when they fail.
    """

    def __init__(self, batch_cfg: dict, proxy_cfg: dict = None, session=None):
        self.timeout = float(batch_cfg.get("request_timeout_sec", 30))
        self.retries = int(batch_cfg.get("retries", 3))
        self.backoff_base = float(batch_cfg.get("backoff_base_sec", 2.0))
        self.backoff_max = float(batch_cfg.get("backoff_max_sec", 45.0))
        self.session = session or requests.Session()
        self.proxies = []
        proxy_cfg = proxy_cfg or {}
        if proxy_cfg.get("enabled"):
            raw = os.environ.get(proxy_cfg.get("env_var", "EJA_PROXIES"), "")
            self.proxies = [p.strip() for p in raw.split(",") if p.strip()]
            if not self.proxies:
                log.info("proxies enabled but %s is empty; continuing direct-only",
                         proxy_cfg.get("env_var", "EJA_PROXIES"))

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        proxy_cycle = itertools.cycle(self.proxies) if self.proxies else None
        last_exc = None
        for attempt in range(1, self.retries + 1):
            headers["User-Agent"] = random.choice(USER_AGENTS)
            proxies = None
            if attempt > 1 and proxy_cycle is not None:
                proxy = next(proxy_cycle)
                proxies = {"http": proxy, "https": proxy}
                log.info("retry %d via proxy fallback for %s", attempt, url)
            try:
                resp = self.session.request(
                    method, url, headers=headers, timeout=self.timeout,
                    proxies=proxies, **kwargs,
                )
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise ScraperError("transient HTTP {}".format(resp.status_code))
                resp.raise_for_status()
                return resp
            except Exception as exc:  # noqa: BLE001 - isolation by design
                last_exc = exc
                delay = min(self.backoff_max, self.backoff_base * (2 ** (attempt - 1)))
                delay += random.uniform(0, 1)
                log.info("attempt %d failed for %s (%s); sleeping %.1fs",
                         attempt, url, exc, delay)
                time.sleep(delay)
        raise ScraperError(
            "{} {} failed after {} attempts: {}".format(method, url, self.retries, last_exc)
        )

    def post_json(self, url: str, payload: dict, params: dict = None) -> dict:
        resp = self.request("POST", url, json=payload, params=params)
        return resp.json()

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

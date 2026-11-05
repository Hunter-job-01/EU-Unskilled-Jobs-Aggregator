"""Normalized job vacancy model shared by all scrapers and storage."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Job:
    """A single normalized vacancy.

    Scoring/pipeline fields (uid, score, signals, ...) are filled in by
    src.scorer. Provenance fields (source, source_id, url) are filled by
    the originating scraper and never overwritten by later merges.
    """

    title: str
    company: str
    country: str                 # ISO-3166 alpha-2, upper case
    source: str                  # e.g. "eures", "jobspy_indeed"
    url: str
    source_id: str = ""
    description: str = ""
    location: str = ""
    posted_date: Optional[str] = None  # ISO date "yyyy-mm-dd" or None
    languages: list = field(default_factory=list)
    # pipeline fields
    uid: str = ""
    title_en: str = ""
    title_ar: str = ""
    summary_ar: str = ""
    glossary: list = field(default_factory=list)  # honest term glosses, never claims
    translated: bool = False
    score: int = 0
    signals: list = field(default_factory=list)
    matched_keywords: list = field(default_factory=list)
    excluded: bool = False
    exclusion_reason: str = ""
    # Phase 4 (Telegram digest) state: preserved across re-scrapes by
    # JobStore.merge, updated only via JobStore.mark_notified.
    notified_telegram: bool = False
    notified_at: Optional[str] = None
    # Link provenance: empty provenance = URL came straight from the source
    # scrape. "reconstructed_from_source_key" = rebuilt from the posting key
    # (see jobspy_scraper); url_verified stays False until such a link is
    # live-checked, so the dashboard never shows it as a plain apply link.
    url_provenance: str = ""
    url_verified: Optional[bool] = None
    source_review: dict = field(default_factory=dict)
    # v6 lifecycle/dead-link state. Owned by JobStore.update_lifecycle;
    # fresh scrape objects carry an empty dict, merge preserves the
    # stored record's state. Keys: status ("active"/"deactivated"),
    # missed_runs, dead_checks, deactivated_at, deactivation_reason,
    # reactivations.
    lifecycle: dict = field(default_factory=dict)
    first_seen: str = ""
    last_seen: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

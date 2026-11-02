"""Dynamic reach: locate the official application page when URLs change.

Given a company name, country and an injected search function, rank
candidate URLs so the official employer page wins over aggregators and
lookalikes. The search function is injected (tests use canned results);
this module never scrapes a search engine by itself and never follows a
route whose only evidence is a name similarity guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

SearchFn = Callable[[str], list]  # query -> [url, ...]

_AGGREGATORS = ("indeed.", "linkedin.", "glassdoor.", "jooble.",
                "pracuj.", "stepstone.", "monster.", "europa.eu/eures")


def _tokens(name: str) -> list:
    return [t for t in re.split(r"[^a-z0-9]+", name.lower()) if len(t) > 2]


@dataclass
class DiscoveryResult:
    chosen_url: str = ""
    confidence: str = "none"       # high | medium | none
    candidates: list = field(default_factory=list)
    reason: str = ""


def find_official_page(company: str, country: str, search: SearchFn,
                       extra_query: str = "") -> DiscoveryResult:
    query = "{} {} careers vacancies official site".format(company, country)
    if extra_query:
        query += " " + extra_query
    try:
        urls = search(query) or []
    except Exception as exc:
        return DiscoveryResult(reason="search failed: {}".format(type(exc).__name__))
    tokens = _tokens(company)
    scored = []
    for url in urls:
        low = url.lower()
        if any(a in low for a in _AGGREGATORS):
            continue  # aggregators are never the official page
        hits = sum(1 for t in tokens if t in low)
        scored.append((hits, url))
    result = DiscoveryResult(candidates=[u for _, u in scored])
    if not scored:
        result.reason = "no non-aggregator candidates"
        return result
    scored.sort(key=lambda x: -x[0])
    best_hits, best = scored[0]
    if tokens and best_hits >= max(1, len(tokens) // 2 + len(tokens) % 2):
        result.chosen_url = best
        result.confidence = "high" if best_hits == len(tokens) else "medium"
        result.reason = "domain/title matched {}/{} company tokens".format(best_hits, len(tokens))
    else:
        result.reason = "no candidate matched company name closely enough"
    return result

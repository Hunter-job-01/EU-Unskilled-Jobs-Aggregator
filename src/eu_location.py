"""EU27 job-location grounding: allowlist, country-name normalisation and
location verdicts for incoming vacancies.

Rules implemented here (source-expansion Phase 3):

* Only these 27 ISO codes are EU job locations:
  AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE
* A structured country field returned by the source outranks free text.
* The search/query country is a lead, never sole proof of location.
* Multilingual country names and recognised aliases resolve to ISO codes.
* Non-EU jobs (UK, Switzerland, Norway, Iceland, Liechtenstein, Egypt,
  Gulf, ...) are rejected even on a European board.
* Unknown, contradictory or unconstrained-remote locations are
  quarantined, never silently admitted or silently dropped.

Verdict statuses:
    "eu"                 - credible EU27 country evidence
    "non_eu"             - credible non-EU evidence only (reject)
    "contradictory"      - conflicting country evidence (quarantine)
    "remote_unverified"  - remote without a clear EU country constraint
    "unknown"            - no usable country evidence (quarantine)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .keywords import fold_text

EU27 = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
})

QUARANTINE_STATUSES = frozenset({"unknown", "contradictory", "remote_unverified"})

# ISO-3166 alpha-3 -> alpha-2 for the codes that appear in API payloads.
ISO3_TO_ISO2 = {
    "AUT": "AT", "BEL": "BE", "BGR": "BG", "HRV": "HR", "CYP": "CY",
    "CZE": "CZ", "DNK": "DK", "EST": "EE", "FIN": "FI", "FRA": "FR",
    "DEU": "DE", "GRC": "GR", "HUN": "HU", "IRL": "IE", "ITA": "IT",
    "LVA": "LV", "LTU": "LT", "LUX": "LU", "MLT": "MT", "NLD": "NL",
    "POL": "PL", "PRT": "PT", "ROU": "RO", "SVK": "SK", "SVN": "SI",
    "ESP": "ES", "SWE": "SE",
    # non-EU leads seen in payloads
    "GBR": "GB", "CHE": "CH", "NOR": "NO", "ISL": "IS", "LIE": "LI",
    "EGY": "EG", "USA": "US",
}

# Folded country names/aliases -> ISO alpha-2. EU27 states get their
# English, endonym and major working-language names; non-EU entries are
# the rejection leads most often seen on European boards.
COUNTRY_ALIASES = {
    # EU27
    "austria": "AT", "osterreich": "AT", "rakousko": "AT", "autriche": "AT",
    "belgium": "BE", "belgie": "BE", "belgique": "BE", "belgien": "BE",
    "bulgaria": "BG", "bulgarija": "BG", "bulgarie": "BG", "bulgarien": "BG",
    "croatia": "HR", "hrvatska": "HR", "kroatien": "HR", "croatie": "HR",
    "cyprus": "CY", "kypros": "CY", "kibris": "CY", "chypre": "CY", "zypern": "CY",
    "czech republic": "CZ", "czechia": "CZ", "cesko": "CZ", "ceska republika": "CZ",
    "tschechien": "CZ", "tchequie": "CZ",
    "denmark": "DK", "danmark": "DK", "danemark": "DK",
    "estonia": "EE", "eesti": "EE", "estonie": "EE", "estland": "EE",
    "finland": "FI", "suomi": "FI", "finlande": "FI", "finnland": "FI",
    "france": "FR", "frankreich": "FR", "francie": "FR", "ranska": "FR",
    "germany": "DE", "deutschland": "DE", "allemagne": "DE", "nemecko": "DE",
    "niemcy": "DE", "duitsland": "DE", "germania": "DE",
    "greece": "GR", "ellada": "GR", "grece": "GR", "griechenland": "GR",
    "recko": "GR", "kreikka": "GR",
    "hungary": "HU", "magyarorszag": "HU", "hongrie": "HU", "ungarn": "HU",
    "madarsko": "HU", "unkari": "HU",
    "ireland": "IE", "eire": "IE", "irlande": "IE", "irland": "IE", "irsko": "IE",
    "italy": "IT", "italia": "IT", "italie": "IT", "italien": "IT", "taliansko": "IT",
    "latvia": "LV", "latvija": "LV", "lettonie": "LV", "lettland": "LV", "lotyssko": "LV",
    "lithuania": "LT", "lietuva": "LT", "lituanie": "LT", "litauen": "LT", "litva": "LT",
    "luxembourg": "LU", "luxemburg": "LU", "letzebuerg": "LU",
    "malta": "MT",
    "netherlands": "NL", "nederland": "NL", "holland": "NL", "pays bas": "NL",
    "niederlande": "NL", "nizozemsko": "NL",
    "poland": "PL", "polska": "PL", "pologne": "PL", "polen": "PL", "polsko": "PL",
    "portugal": "PT", "portugalsko": "PT",
    "romania": "RO", "roumanie": "RO", "rumanien": "RO", "rumunsko": "RO",
    "slovakia": "SK", "slovensko": "SK", "slowakei": "SK", "slovaquie": "SK",
    "slovenia": "SI", "slovenija": "SI", "slowenien": "SI", "slovenie": "SI",
    "spain": "ES", "espana": "ES", "espagne": "ES", "spanien": "ES",
    "spanelsko": "ES", "espanja": "ES",
    "sweden": "SE", "sverige": "SE", "suede": "SE", "schweden": "SE",
    "svedorszag": "SE", "svedsko": "SE", "ruotsi": "SE",
    # non-EU rejection leads
    "united kingdom": "GB", "great britain": "GB", "england": "GB",
    "scotland": "GB", "wales": "GB", "uk": "GB",
    "switzerland": "CH", "schweiz": "CH", "suisse": "CH", "svycarsko": "CH",
    "norway": "NO", "norge": "NO", "norsko": "NO",
    "iceland": "IS", "island": "IS",
    "liechtenstein": "LI",
    "egypt": "EG", "misr": "EG",
    "united states": "US", "usa": "US", "u s a": "US",
    "saudi arabia": "SA", "united arab emirates": "AE", "uae": "AE",
    "qatar": "QA", "kuwait": "KW",
}

# Vague labels that must never count as a work-location country.
VAGUE_LOCATION_LABELS = frozenset({
    "europe", "european union", "eu", "emea", "worldwide", "remote",
    "anywhere", "global", "multiple", "various", "international",
    "european", "central europe", "eastern europe", "western europe",
})

_TOKEN_SPLIT = re.compile(r"[,;/|()\[\]\-]+")


@dataclass
class LocationVerdict:
    status: str                      # eu | non_eu | contradictory | remote_unverified | unknown
    country: Optional[str] = None    # ISO alpha-2 when status == "eu" or "non_eu"
    evidence: list = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.status == "eu"

    @property
    def quarantined(self) -> bool:
        return self.status in QUARANTINE_STATUSES


def normalize_country(value) -> Optional[str]:
    """Resolve a code or (multilingual) name to an ISO alpha-2 code.

    Returns the alpha-2 code for any recognised country (EU or not), else
    None. Accepts alpha-2, alpha-3 and folded aliases.
    """
    if not value:
        return None
    text = fold_text(str(value)).strip()
    if not text:
        return None
    upper = text.upper()
    if len(upper) == 2 and upper.isalpha():
        if upper == "UK":
            return "GB"
        if upper in EU27 or upper in {"GB", "CH", "NO", "IS", "LI",
                                      "EG", "US", "SA", "AE", "QA", "KW"}:
            return upper
        return None
    if len(upper) == 3 and upper in ISO3_TO_ISO2:
        return ISO3_TO_ISO2[upper]
    return COUNTRY_ALIASES.get(text)


def _countries_in_text(location_text: str) -> list:
    """Distinct country codes named in free location text, in order."""
    if not location_text:
        return []
    folded = fold_text(location_text)
    if not folded:
        return []
    found = []
    for chunk in _TOKEN_SPLIT.split(folded):
        chunk = chunk.strip()
        if not chunk or chunk in VAGUE_LOCATION_LABELS:
            continue
        code = normalize_country(chunk)
        if code and code not in found:
            found.append(code)
            continue
        # "Berlin Germany" style: try the trailing tokens as a name.
        words = chunk.split()
        for n in (3, 2, 1):
            if len(words) >= n:
                code = normalize_country(" ".join(words[-n:]))
                if code and code not in found:
                    found.append(code)
                    break
    return found


def classify_location(*, structured_country=None, location_text: str = "",
                      search_country: Optional[str] = None,
                      dataset_country: Optional[str] = None,
                      remote: bool = False) -> LocationVerdict:
    """Ground one vacancy's work location.

    structured_country: a country field returned by the source (code or
        name). Outranks free text when present.
    location_text: free-form location string returned by the source.
    search_country: the query country used to find the vacancy. A lead
        only - recorded as evidence, never sole proof.
    dataset_country: the authoritative scope of a country-scoped
        official national dataset (e.g. a national public-sector feed).
        Stronger than a query lead, weaker than a per-record structured
        country: used when a record has no country of its own, and a
        contradicting text country still wins (contradictory).
    remote: the source flags the vacancy as remote. Remote jobs need a
        clear EU country constraint; "Europe/worldwide" is not one.
    """
    evidence = []
    struct = normalize_country(structured_country)
    if structured_country and struct is None:
        evidence.append("unrecognised structured country: {!r}".format(structured_country))
    if struct:
        evidence.append("structured country -> {}".format(struct))
    text_hits = _countries_in_text(location_text)
    if text_hits:
        evidence.append("location text {!r} -> {}".format(location_text, ",".join(text_hits)))
    if search_country:
        evidence.append("search country lead: {}".format(search_country))

    if struct:
        if text_hits and any(c != struct for c in text_hits):
            return LocationVerdict("contradictory", struct,
                                   evidence + ["structured/text country conflict"])
        if struct in EU27:
            return LocationVerdict("eu", struct, evidence)
        return LocationVerdict("non_eu", struct, evidence)

    if len(text_hits) > 1:
        eu_hits = [c for c in text_hits if c in EU27]
        if len(eu_hits) == 1 and len(text_hits) == len(eu_hits):
            return LocationVerdict("eu", eu_hits[0], evidence)
        return LocationVerdict("contradictory", None,
                               evidence + ["multiple distinct countries in location text"])
    if len(text_hits) == 1:
        code = text_hits[0]
        if code in EU27:
            return LocationVerdict("eu", code, evidence)
        return LocationVerdict("non_eu", code, evidence)

    if remote:
        return LocationVerdict("remote_unverified", None,
                               evidence + ["remote without explicit EU country constraint"])
    dataset = normalize_country(dataset_country)
    if dataset and dataset in EU27:
        evidence.append("dataset scope -> {}".format(dataset))
        return LocationVerdict("eu", dataset, evidence)
    return LocationVerdict("unknown", None, evidence + ["no usable country evidence"])

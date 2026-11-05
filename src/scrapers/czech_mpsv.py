"""Czech MPSV open vacancy dataset adapter (official daily export).

Validated against the live schema on 2026-11-02:
  distribution: https://data.mpsv.cz/od/soubory/volna-mista/volna-mista.json
  schema:       https://data.mpsv.cz/od/soubory/volna-mista/volna-mista.schema.json
  records:      top-level "polozky" array, pretty-printed, ~185 MB/day
                (no compression, no smaller distributions), so the
                adapter STREAMS the response and stops after max_records.

Useful evidence fields preserved per vacancy: cizinecMimoEu (explicit
consent to employ non-EU foreigners), zamestnaneckaKarta (employee-card
route), azylant, salary range, weekly hours and language requirements.
Most records carry urlAdresa: null; those vacancies are quarantined
(no link invented) while their counts stay visible in the run stats.
Records with a hidden employer or no usable workplace are likewise
quarantined, never completed from the dataset country alone.
"""
from __future__ import annotations

import json
import logging
import re

from ..eu_location import classify_location
from ..models import Job
from .base import ScrapeResult, ScraperError

log = logging.getLogger(__name__)

DISTRIBUTION_URL = "https://data.mpsv.cz/od/soubory/volna-mista/volna-mista.json"


def iter_polozky(text_stream):
    """Yield parsed vacancy objects from the streamed "polozky" array
    without loading the whole export into memory."""
    started = False
    buf = ""
    depth = 0
    obj_start = None
    in_string = False
    escape = False
    for chunk in text_stream:
        buf += chunk
        if not started:
            m = re.search(r'"polozky"\s*:\s*\[', buf)
            if not m:
                buf = buf[-64:]
                continue
            buf = buf[m.end():]
            started = True
        i = 0
        while i < len(buf):
            ch = buf[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                i += 1
                continue
            if ch == '"':
                in_string = True
            elif obj_start is None:
                if ch == "{":
                    obj_start = i
                    depth = 1
                elif ch == "]":
                    return
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    text = buf[obj_start:i + 1]
                    obj_start = None
                    try:
                        yield json.loads(text)
                    except ValueError:
                        log.warning("skipping unparsable polozky record")
                    buf = buf[i + 1:]
                    i = -1
            i += 1
        if obj_start is not None:
            # keep the partial object and rescan it from its start with
            # clean scanner state on the next chunk
            buf = buf[obj_start:]
            obj_start = None
            depth = 0
            in_string = False
            escape = False
        else:
            buf = ""  # only array separators remained


class CzechMpsvScraper:
    name = "czech_mpsv"
    COUNTRY = "CZ"

    def __init__(self, cfg: dict, http):
        self.cfg = cfg
        self.http = http
        sc = (cfg.get("sources") or {}).get("czech_mpsv") or {}
        self.enabled = bool(sc.get("enabled", False))
        self.distribution_url = str(sc.get("distribution_url") or DISTRIBUTION_URL)
        self.max_records = int(sc.get("max_records", 5000))
        self.min_mapped_ratio = float(sc.get("min_mapped_ratio", 0.5))

    @staticmethod
    def _workplace_text(record: dict) -> str:
        misto = record.get("mistoVykonuPrace") or {}
        if misto.get("adresaText"):
            return str(misto["adresaText"]).strip()
        names = [str(p.get("nazev")).strip() for p in (misto.get("pracoviste") or [])
                 if isinstance(p, dict) and p.get("nazev")]
        return "; ".join(names)

    def _map_record(self, record: dict) -> Job:
        profese = record.get("pozadovanaProfese") or {}
        zamestnavatel = record.get("zamestnavatel") or {}
        flags = []
        for key in ("cizinecMimoEu", "zamestnaneckaKarta", "azylant", "modraKarta"):
            if key in record:
                flags.append("{}={}".format(key, bool(record.get(key))))
        mzda_od = record.get("mesicniMzdaOd")
        mzda_do = record.get("mesicniMzdaDo")
        if mzda_od is not None or mzda_do is not None:
            flags.append("mzda {}-{} Kc".format(mzda_od, mzda_do))
        if record.get("pocetHodinTydne"):
            flags.append("{}h/tyden".format(record["pocetHodinTydne"]))
        description = "[MPSV evidence] " + "; ".join(flags) if flags else ""
        return Job(
            title=str(profese.get("cs") or "").strip(),
            company=str(zamestnavatel.get("nazev") or "").strip(),
            country="CZ",
            source=self.name,
            source_id=str(record.get("referencniCislo") or record.get("id") or ""),
            url=self._normalized_url(record.get("urlAdresa")),
            description=description,
            location=self._workplace_text(record),
            posted_date=str(record.get("datumVlozeni") or "")[:10] or None,
        )

    @staticmethod
    def _normalized_url(raw) -> str:
        """urlAdresa often lacks a scheme ("www.pco.cz"). Add https:// to
        domain-shaped values; anything else is unusable link text and the
        record is quarantined by the no-link rule downstream."""
        url = str(raw or "").strip()
        if not url:
            return ""
        if url.lower().startswith(("http://", "https://")):
            return url
        if re.match(r"^[\w.-]+\.[a-z]{2,}(/\S*)?$", url, re.IGNORECASE):
            return "https://" + url
        return ""

    def scrape_country(self, country_code: str, max_jobs: int) -> ScrapeResult:
        country_code = country_code.upper()
        result = ScrapeResult(source=self.name, country=country_code)
        if not self.enabled:
            result.status = "skipped"
            result.notes.append("disabled in config (sources.czech_mpsv.enabled: false)")
            return result
        if country_code != self.COUNTRY:
            result.status = "skipped"
            result.notes.append("the MPSV dataset covers Czechia only")
            return result
        try:
            resp = self.http.get(self.distribution_url, stream=True)
        except ScraperError as exc:
            result.status = "failed"
            result.errors.append("download failed: {}".format(exc))
            return result
        try:
            records = iter_polozky(resp.iter_content(
                chunk_size=65536, decode_unicode=True))
            mapped = quarantined = 0
            seen = set()
            for idx, record in enumerate(records):
                if idx >= self.max_records or result.fetched >= max_jobs:
                    break
                job = self._map_record(record)
                if not job.title:
                    continue  # schema drift guard below counts mapped rows
                mapped += 1
                if not job.company or not job.url or not job.location:
                    quarantined += 1  # hidden employer / no link / no workplace
                    continue
                if job.source_id in seen:
                    continue
                seen.add(job.source_id)
                verdict = classify_location(
                    structured_country=None, location_text=job.location,
                    dataset_country="CZ")
                if verdict.status == "non_eu":
                    result.stats["non_eu_rejects"] = result.stats.get("non_eu_rejects", 0) + 1
                    continue
                if verdict.quarantined or verdict.country != "CZ":
                    quarantined += 1
                    continue
                result.jobs.append(job)
            result.stats["rows_streamed"] = idx + 1 if mapped or quarantined else 0
            result.stats["quarantined"] = quarantined
        except Exception as exc:  # noqa: BLE001 - one broken source never crashes a run
            result.status = "failed"
            result.errors.append("stream parse failed: {}".format(exc))
            return result
        if not mapped:
            result.status = "failed"
            result.errors.append("no records mapped to a title; schema needs re-validation")
            return result
        ratio = mapped / float(mapped + quarantined) if (mapped + quarantined) else 0.0
        if mapped and ratio < self.min_mapped_ratio and not result.jobs:
            result.status = "failed"
            result.errors.append("mapped ratio too low; schema needs re-validation")
            return result
        return result

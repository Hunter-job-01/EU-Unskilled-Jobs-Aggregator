"""Deduplication hashing, signal tagging, title translation and the
0-100 relevance scoring engine.

The score is RELEVANCE evidence (does this vacancy look like entry-level,
foreign-worker-friendly work worth manual review?), not a legal verdict
on sponsorship or on a specific candidate's eligibility.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timezone

from .keywords import KeywordMatrix, fold_text
from .models import Job

log = logging.getLogger(__name__)


def dedup_hash(company: str, title: str, country: str) -> str:
    """Deterministic unique id: sha256(company|normalized title|country).

    The same vacancy seen across runs or sources collapses to one record.
    """
    def _norm(value: str) -> str:
        # keep only letters/digits: "Sp. z o.o." and "sp z oo" hash alike
        return re.sub(r"[^a-z0-9]", "", fold_text(value))

    base = "{}|{}|{}".format(_norm(company), _norm(title), _norm(country))
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def _parse_iso_day(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class Scorer:
    def __init__(self, cfg: dict, registry: dict = None):
        self.cfg = cfg
        self.scoring = cfg["scoring"]
        self.matrix = KeywordMatrix(cfg["keywords"])
        self.tiers = {int(t): members for t, members in cfg["tiers"].items()}
        # Optional source registry for per-source applicant-eligibility
        # labels (exclusion for operator-restricted, unknown-label else).
        self.registry = registry

    def tier_of(self, country: str):
        for tier, members in self.tiers.items():
            if country in members:
                return tier
        return None

    def score(self, job: Job, today: date = None) -> Job:
        """Populate uid/score/signals/exclusion fields in place."""
        today = today or datetime.now(timezone.utc).date()
        job.uid = dedup_hash(job.company, job.title, job.country)
        s = self.scoring
        text = "{}\n{}".format(job.title or "", job.description or "")
        signals = []
        matched = []

        # Source-level applicant eligibility (registry evidence) runs
        # FIRST: an operator restriction excludes regardless of keywords;
        # an unknown status only labels, never changes the score.
        unknown_elig = False
        agency_origin = False
        if self.registry is not None:
            from .source_eligibility import source_eligibility, UNKNOWN_VALUES, RESTRICTED_PREFIX
            elig = source_eligibility(self.registry, job.source).lower()
            if elig.startswith(RESTRICTED_PREFIX):
                job.excluded = True
                job.exclusion_reason = (
                    "source currently does not serve applicants residing "
                    "outside the EU (operator-stated, recorded in registry)")
                job.signals = ["source_eligibility_restricted"]
                job.score = 0
                return job
            unknown_elig = elig in UNKNOWN_VALUES
            from .source_eligibility import source_category
            agency_origin = source_category(self.registry, job.source) == "agency"

        disq = self.matrix.match_disqualifiers(text)
        if disq:
            job.excluded = True
            job.exclusion_reason = "disqualifier: {}".format(disq[0])
            job.score = 0
            return job

        # EURES `availableLanguages` describes the portal offer languages, not
        # the posting text language: CY/LT postings carry languages=["en"]
        # while their titles are Greek/Lithuanian, which excluded every
        # fetched job ("no unskilled role keyword match"). Always union the
        # country's configured languages so native keywords are searched too.
        match_langs = list(job.languages or [])
        for lang in (self.cfg.get("countries", {}).get(job.country, {}) or {}).get("languages", []):
            if lang not in match_langs:
                match_langs.append(lang)
        roles = self.matrix.match_roles(text, match_langs or None)
        negative = self.matrix.match_negative(text)
        found_signals = self.matrix.match_signals(text)
        matched.extend(roles)
        no_exp_hits = found_signals.get("no_exp_or_training") or []

        if not roles and negative:
            job.excluded = True
            job.exclusion_reason = "skilled/senior role without unskilled keywords: {}".format(
                ", ".join(negative[:3])
            )
            job.score = 0
            return job
        if not roles and not no_exp_hits:
            job.excluded = True
            job.exclusion_reason = "no unskilled role keyword match"
            job.score = 0
            return job

        # Base qualification: an unskilled role keyword OR an explicit
        # "no experience / training provided" mention (Phase 4), provided
        # no disqualifier or skilled/senior vocabulary matched above.
        points = float(s["base_role_points"])
        if roles:
            extra = min(len(roles) - 1, 0) if len(roles) <= 1 else len(roles) - 1
            points += min(extra * s["extra_role_keyword_points"], s["extra_role_keyword_cap"])
            signals.append("unskilled_role_match")

        if no_exp_hits:
            points += s.get("no_exp_points", 15)
            signals.append("no_exp_or_training_mention")
            matched.extend(no_exp_hits)

        if "permit" in found_signals:
            points += s["permit_points"]
            signals.append("permit_keyword_mention")
            matched.extend(found_signals["permit"])
        if "housing" in found_signals:
            points += s["housing_points"]
            signals.append("housing_keyword_mention")
            matched.extend(found_signals["housing"])
        if "language_flex" in found_signals:
            points += s["language_flex_points"]
            signals.append("language_flex_keyword_mention")
            matched.extend(found_signals["language_flex"])
        if "agency_relocation" in found_signals:
            # Text-evidence signal: the posting itself mentions agency or
            # relocation words. A mention is evidence, never a benefit claim.
            points += s["agency_relocation_points"]
            signals.append("agency_relocation_text_mention")
            matched.extend(found_signals["agency_relocation"])
        elif self.registry is not None and agency_origin:
            # Origin fact: the SOURCE is a verified employment agency. This
            # says nothing about relocation, housing or permits.
            points += s["agency_relocation_points"]
            signals.append("employment_agency_source")

        if negative:  # heavy penalty but role keywords still matched
            points -= s["negative_role_penalty"]
            signals.append("skilled_role_keyword_penalty")

        tier = self.tier_of(job.country)
        multiplier = s["tier_multipliers"].get(tier, 1.0)
        points *= multiplier
        if tier:
            signals.append("tier_{}_country".format(tier))

        posted = _parse_iso_day(job.posted_date)
        if posted:
            age = (today - posted).days
            for rule in s.get("recency", []):
                if 0 <= age <= int(rule["max_days"]):
                    points += int(rule["points"])
                    signals.append("fresh_posting")
                    break

        if unknown_elig:
            signals.append("source_eligibility_unknown")
        job.score = max(0, min(100, int(round(points))))
        job.signals = signals
        job.matched_keywords = sorted(set(matched))
        return job


# ---------------------------------------------------------------------
# Lightweight title translation (keyless, graceful fallback).
# ---------------------------------------------------------------------
_EN_HINTS = {
    "warehouse", "worker", "operator", "picker", "packer", "production",
    "factory", "cleaner", "sorter", "loader", "helper", "the", "and",
    "for", "with", "required", "job", "staff", "kitchen", "porter",
    "employee", "operative", "assistant", "driver", "delivery",
    "logistics", "packaging", "technician",
}


_NON_EN_RX = re.compile(
    r"maszyn|produkcji|pracownik|magazynier|pakowacz|okleiniark|drukark|"
    r"wtryskark|operatork|operator/ka|ansvarlig|fakturering|lagermedarbejder|"
    r"magazijnmedewerker|siivooja|medewerker|mitarbeiter|lagerhelfer",
    re.I)


def looks_english(title: str) -> bool:
    if not title:
        return True
    try:
        title.replace("\u2013", "-").replace("\u2014", "-").encode("ascii")
    except UnicodeEncodeError:
        return False
    words = set(re.findall(r"[a-z]+", title.lower()))
    # Polish/Danish/Dutch/Finnish role words next to a shared token such as
    # "operator" made foreign titles pass as English (2026-11-03 audit).
    if _NON_EN_RX.search(title):
        return False
    return bool(words & _EN_HINTS)


def translate_titles(jobs, cfg: dict, min_score: int = None) -> int:
    """Translate non-English titles in place. Returns count translated.

    Quota discipline (score runs BEFORE translation in main): only jobs
    that survived scoring are worth an HTTP request. Excluded jobs and
    jobs below the effective minimum score are skipped, and the per-run
    request cap is spent on the highest-scoring jobs first. The cutoff
    is the caller's effective ``min_score`` (main passes the run's
    resolved value, from --min-score or scoring.min_score_default);
    when the helper is used standalone it falls back to
    ``translation.min_score`` (default 40). English-looking titles are
    copied into ``title_en`` for free - no request, no cap. Uses
    deep-translator's keyless Google endpoint; any failure (network,
    rate limit, library change) leaves the original title untouched;
    the pipeline never breaks on translation.
    """
    tcfg = cfg.get("translation", {}) or {}
    if not tcfg.get("enabled", True):
        return 0
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        log.warning("deep-translator not installed; skipping title translation")
        return 0

    # Publication containment (pipeline policy 2026-11-03): unattended
    # free-model EN output is NOT published by default. Automatic gates
    # passed real garbles (2026-11-03 audit), so gate-pass alone is not
    # reviewed quality; model output stays EMPTY for manual semantic
    # review. Source-original English passthroughs still copy for free.
    publish_model = bool(tcfg.get("publish_model_output_en", False))
    gate = min_score if min_score is not None else tcfg.get("min_score", 40)
    candidates = [
        job for job in jobs
        if not job.excluded and (gate is None or job.score >= int(gate))
    ]
    candidates.sort(key=lambda job: -job.score)  # cap goes to the top first

    cap = int(tcfg.get("max_per_run", 200))
    target = tcfg.get("target", "en")
    translator = GoogleTranslator(source="auto", target=target)
    done = 0
    attempted = 0
    throttled = False
    for job in candidates:
        title = (job.title or "").strip()
        if not title:
            continue
        if looks_english(title):
            job.title_en = title  # free: no request, no cap
            continue
        if not publish_model:
            continue  # held: no unattended free-model EN publication
        if throttled:
            continue  # leave title_en empty: a source-title copy is not a translation

        if attempted >= cap:
            continue  # cap counts every HTTP attempt, including failures
        try:
            attempted += 1
            translated = translator.translate(title)
        except Exception as exc:  # noqa: BLE001 - fallback by design
            log.info("translation failed for %r: %s", title[:60], exc)
            # Leave title_en empty: stamping the source title would fake a
            # translation and permanently hide the job from the Gemini
            # fallback gate (which treats identical-to-source as untranslated).
            reason = str(exc).lower()
            if any(marker in reason for marker in ("too many requests", "429", "rate limit")):
                throttled = True
                log.warning("Translation throttled; no more requests in this run")
            continue
        if translated and translated.strip():
            if translated.strip().lower() == title.lower():
                # No-op round-trip (source returned unchanged): stamping it
                # would display the source language as if it were English.
                continue
            job.title_en = translated
            job.translated = True
            done += 1
        # Empty translator output: leave title_en empty (see above).
    return done


def needs_en_translation(job) -> bool:
    """True when a scored job still lacks a real English title.

    Reload-safe (storage does not persist ``translated``): a job qualifies
    when its title is not English-looking AND title_en is either empty or
    identical to the source title (legacy failure-copies from before the
    honest-empty fix). A genuinely translated title_en is never re-requested.
    """
    title = (getattr(job, "title", "") or "").strip()
    if not title or looks_english(title):
        return False
    te = (getattr(job, "title_en", "") or "").strip()
    return not te or te == title

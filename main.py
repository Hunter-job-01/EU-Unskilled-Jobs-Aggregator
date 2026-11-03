#!/usr/bin/env python3
"""EU-Unskilled-Jobs-Aggregator - Phase 4 CLI runner.

Examples:
    python main.py                          # tier 1 (daily batch), all sources
    python main.py --tier 3                 # weekly full tier-3 scan
    python main.py --tier all               # all 27 EU states
    python main.py --countries PL,CZ,DE     # explicit country batch
    python main.py --sources eures          # EURES only
    python main.py --max-jobs 30 --min-score 40
    python main.py --no-notify            # skip the Telegram digest
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0])  # repo root on path

from src.config import ConfigError, load_config, resolve_countries
from src.keywords import KeywordMatrix
from src.notifier import send_digest, ensure_state_file
from src.scorer import Scorer, translate_titles
from src import source_health
from src.scrapers import (
    AbMiddenScraper, AnyworkAnywhereScraper, ArbeitnowScraper, AtsPublicScraper,
    CoveboScraper, CzechMpsvScraper, EuresScraper, GoodmorningScraper,
    HobijScraper, HttpClient, JobSpyScraper, LatviaNvaScraper, NlJobsScraper,
    OttoWorkforceScraper, PlCbopScraper, SwedenJobSearchScraper, polite_sleep,
)
from src.storage import JobStore

log = logging.getLogger("aggregator")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eu-unskilled-jobs-aggregator",
        description="Scrape, score and store entry-level blue-collar EU jobs.",
    )
    p.add_argument("--tier", choices=["1", "2", "3", "all"], default="1",
                   help="country tier to run (default: 1)")
    p.add_argument("--countries", default=None,
                   help="comma-separated ISO codes, overrides --tier (e.g. PL,CZ,DE)")
    p.add_argument("--sources", default="eures,jobspy",
                   help="comma-separated sources: eures, jobspy (default: both)")
    p.add_argument("--max-jobs", type=int, default=50,
                   help="max jobs per country per source (default: 50)")
    p.add_argument("--min-score", type=int, default=None,
                   help="minimum relevance score to store "
                        "(default: scoring.min_score_default)")
    p.add_argument("--no-translate", action="store_true",
                   help="skip title translation for this run")
    p.add_argument("--no-notify", action="store_true",
                   help="skip the Telegram digest for this run")
    p.add_argument("--config", default=None, help="path to config.yaml")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        cfg = load_config(args.config)
        country_list = resolve_countries(
            cfg, tier=args.tier,
            countries=args.countries.split(",") if args.countries else None,
        )
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    requested = {s.strip().lower() for s in args.sources.split(",") if s.strip()}
    known_sources = {"eures", "jobspy", "arbeitnow", "sweden_jobsearch",
                     "czech_mpsv", "latvia_nva", "ats_public",
                     "ottoworkforce", "anyworkanywhere", "pl_cbop",
                     "nl_jobs", "hobij", "covebo", "ab_midden",
                     "goodmorning"}
    unknown = requested - known_sources
    if unknown:
        log.error("unknown source(s): %s (valid: %s)", ", ".join(sorted(unknown)),
                  ", ".join(sorted(known_sources)))
        return 2

    min_score = args.min_score
    if min_score is None:
        min_score = int(cfg["scoring"].get("min_score_default", 20))
    if args.no_translate:
        cfg.setdefault("translation", {})["enabled"] = False

    try:
        matrix = KeywordMatrix(cfg["keywords"])
        from src.source_registry import load_registry
        try:
            scorer = Scorer(cfg, registry=load_registry())
        except Exception:  # registry unreadable: degrade to no eligibility labels
            scorer = Scorer(cfg)
    except (ConfigError, ValueError) as exc:
        log.error("invalid keyword configuration: %s", exc)
        return 2
    http = HttpClient(cfg["batch"], cfg.get("proxies"))
    scrapers = []
    if "eures" in requested:
        scrapers.append(EuresScraper(cfg, http, matrix))
    if "jobspy" in requested:
        try:
            scrapers.append(JobSpyScraper(cfg, matrix))
        except ValueError as exc:
            log.error("%s", exc)
            return 2
    # Opt-in expansion sources. Each is disabled in config by default and
    # then reports itself "skipped" with a visible note - never silently.
    if "arbeitnow" in requested:
        scrapers.append(ArbeitnowScraper(cfg, http))
    if "sweden_jobsearch" in requested:
        scrapers.append(SwedenJobSearchScraper(cfg, http, matrix))
    if "czech_mpsv" in requested:
        scrapers.append(CzechMpsvScraper(cfg, http))
    if "latvia_nva" in requested:
        scrapers.append(LatviaNvaScraper(cfg, http))
    if "ats_public" in requested:
        scrapers.append(AtsPublicScraper(cfg, http))
    if "ottoworkforce" in requested:
        scrapers.append(OttoWorkforceScraper(cfg, http))
    if "anyworkanywhere" in requested:
        scrapers.append(AnyworkAnywhereScraper(cfg, http))
    if "pl_cbop" in requested:
        scrapers.append(PlCbopScraper(cfg, http))
    if "nl_jobs" in requested:
        scrapers.append(NlJobsScraper(cfg, http))
    if "hobij" in requested:
        scrapers.append(HobijScraper(cfg, http))
    if "covebo" in requested:
        scrapers.append(CoveboScraper(cfg, http))
    if "ab_midden" in requested:
        scrapers.append(AbMiddenScraper(cfg, http))
    if "goodmorning" in requested:
        scrapers.append(GoodmorningScraper(cfg, http))

    store = JobStore(cfg["storage"])
    ensure_state_file(store, cfg)
    started = time.time()
    statuses = {}
    scored_jobs = []
    totals = {"fetched": 0, "excluded": 0, "below_min_score": 0}

    print("Running countries: {} | sources: {} | max-jobs/country/source: {}".format(
        ", ".join(country_list), ", ".join(s.name for s in scrapers), args.max_jobs))

    health_records = []
    for idx, country in enumerate(country_list):
        statuses[country] = {}
        for scraper in scrapers:
            t0 = time.time()
            result = scraper.scrape_country(country, args.max_jobs)
            duration = time.time() - t0
            statuses[country][scraper.name] = result.to_status()
            totals["fetched"] += result.fetched
            kept = 0
            for job in result.jobs:
                scorer.score(job)
                if job.excluded:
                    totals["excluded"] += 1
                    continue
                if job.score < min_score:
                    totals["below_min_score"] += 1
                    continue
                scored_jobs.append(job)
                kept += 1
            health_records.append(
                source_health.record_from_result(result, kept, duration))
            line = "  {} {:<7} {:<8} fetched={:<4} kept={}".format(
                country, scraper.name, result.status, result.fetched, kept)
            print(line)
            for err in result.errors[:3]:
                print("      ! {}".format(err))
        if idx < len(country_list) - 1:
            polite_sleep(tuple(cfg["batch"].get("delay_between_countries_sec", [5, 12])))

    # Eligible set + cap priority use the run's effective min_score.
    translated = translate_titles(scored_jobs, cfg, min_score=min_score)
    from src.job_translation import (annotate_glossary, compose_summary_ar,
                                     translate_title_mt, valid_translation)
    ar_gate = int(cfg.get("translation", {}).get("min_score", 45))
    ar_candidates = [j for j in scored_jobs
                     if not j.excluded and j.score >= ar_gate and j.title]
    # Deterministic layer always runs: glossary glosses + structured summary.
    for job in ar_candidates:
        country_meta = cfg.get("countries", {}).get(job.country, {}) or {}
        langs = country_meta.get("languages", [])
        annotate_glossary(job, langs[0] if langs else "")
        job.summary_ar = compose_summary_ar(job)
    # Offline title layer (Argos CPU + reviewed Arabic vocab): runs after
    # the keyless Google route and fills titles that route could not
    # (failures, throttling, cap). Highest score first. A missing
    # argostranslate install skips the layer explicitly, never silently.
    from src.offline_translation import translate_titles_offline
    off_en, off_ar = translate_titles_offline(ar_candidates, cfg)
    if off_en or off_ar:
        log.info("offline titles: %d en, %d ar", off_en, off_ar)
    # title_ar layer 1: one batched Gemini free call (keyed, bounded,
    # contact-scrubbed) covering up to 40 titles at once.
    ar_done = off_ar
    mt_enabled = bool(cfg.get("translation", {}).get("enabled", False))
    # Publication containment (pipeline policy 2026-11-03): unattended
    # free-model ARABIC output is NOT published either - same policy for
    # all languages. Arabic publishes only from the reviewed TITLE_AR_VOCAB
    # (offline layer above); the Gemini batch and the keyless layer-2
    # route stay off unless explicitly re-enabled.
    publish_model_ar = bool(cfg.get("translation", {}).get("publish_model_output_ar", False))
    todo = [j for j in ar_candidates if not j.title_ar][:40] if (mt_enabled and publish_model_ar) else []
    # title_en Gemini fallback: non-English titles the keyless route left
    # untranslated (it is 429-throttled on both sandbox and hosted egress).
    # One Translator instance => the per-run call cap is SHARED by both
    # language batches; quota-stop in either stops the other.
    en_todo = []
    publish_model_en = bool(cfg.get("translation", {}).get("publish_model_output_en", False))
    if mt_enabled and publish_model_en:
        from src.scorer import needs_en_translation
        # Reload-safe gate: empty OR identical-to-source title_en (legacy
        # failure-copies included) - the keyless route can no longer hide
        # jobs from this fallback by stamping the source title.
        en_todo = [j for j in ar_candidates if needs_en_translation(j)][:40]
    if todo or en_todo:
        try:
            from src.gemini_translation import Translator
            tr = Translator(max_calls=5)
            if tr.key:
                if todo:
                    result, _status = tr("\n".join(j.title for j in todo))
                    lines = [l for l in (result or "").splitlines() if l.strip()]
                    if len(lines) == len(todo):
                        for job, line in zip(todo, lines):
                            if valid_translation(job.title, line):
                                job.title_ar = line.strip()
                                ar_done += 1
                if en_todo and not tr.stopped and tr.remaining > 0:
                    result, _status = tr("\n".join(j.title for j in en_todo), target="en")
                    lines = [l for l in (result or "").splitlines() if l.strip()]
                    if len(lines) == len(en_todo):
                        for job, line in zip(en_todo, lines):
                            if valid_translation(job.title, line):
                                job.title_en = line.strip()
        except Exception:
            pass  # fall through to per-title route
    # title_ar layer 2: keyless web route, same quota cap as title_en.
    if any(not j.title_ar for j in todo):
        for job in todo:
            if job.title_ar:
                continue
            out = translate_title_mt(job.title, "ar", cfg)
            if out:
                job.title_ar = out
                ar_done += 1
    added, updated = store.merge(scored_jobs)
    pruned = store.prune()
    active = store.save()
    exported = store.export_csv(cfg["tiers"])

    # Phase 4: Telegram digest. Runs INSIDE main.py, before the
    # workflows' git commit step, so the notified_telegram flags it
    # persists to data/active_jobs.json travel in the same data commit.
    # Graceful no-op without TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID or
    # when telegram.enabled is false; delivery failures are logged and
    # never fail the run (unsent jobs stay pending for the next run).
    notified = 0
    if args.no_notify:
        log.info("Telegram digest skipped (--no-notify)")
    else:
        notified = send_digest(store, cfg)

    run_status = {
        "last_run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_sec": round(time.time() - started, 1),
        "parameters": {
            "tier": args.tier, "countries": country_list,
            "sources": sorted(requested), "max_jobs": args.max_jobs,
            "min_score": min_score,
        },
        "totals": dict(totals, translated_titles=translated, translated_ar=ar_done, new=added,
                       updated=updated, pruned=pruned, active=active,
                       notified_telegram=notified),
        "countries": statuses,
    }
    store.write_run_status(run_status)

    # Source-health observability (Phase 1): a separate additive file so
    # failed/blocked/uncovered sources stay visible in coverage reports.
    # Never touches job state or notification flags.
    source_health.update_health(
        store.data_dir,
        "tier={} sources={}".format(args.tier, ",".join(sorted(requested))),
        health_records)

    print("\nDone in {:.1f}s".format(time.time() - started))
    print("  fetched={fetched} excluded={excluded} below_min={below_min_score} "
          "new={new} updated={updated} pruned={pruned} active={active} "
          "notified={notified_telegram}".format(**run_status["totals"]))
    top = sorted(scored_jobs, key=lambda j: -j.score)[:5]
    if top:
        print("  top this run:")
        for j in top:
            print("    {:>3}  [{}] {} - {} ({})".format(
                j.score, j.country, (j.title_en or j.title)[:60], j.company[:30], j.source))
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
from src.scrapers import EuresScraper, HttpClient, JobSpyScraper, polite_sleep
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
    unknown = requested - {"eures", "jobspy"}
    if unknown:
        log.error("unknown source(s): %s (valid: eures, jobspy)", ", ".join(sorted(unknown)))
        return 2

    min_score = args.min_score
    if min_score is None:
        min_score = int(cfg["scoring"].get("min_score_default", 20))
    if args.no_translate:
        cfg.setdefault("translation", {})["enabled"] = False

    try:
        matrix = KeywordMatrix(cfg["keywords"])
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

    store = JobStore(cfg["storage"])
    ensure_state_file(store, cfg)
    started = time.time()
    statuses = {}
    scored_jobs = []
    totals = {"fetched": 0, "excluded": 0, "below_min_score": 0}

    print("Running countries: {} | sources: {} | max-jobs/country/source: {}".format(
        ", ".join(country_list), ", ".join(s.name for s in scrapers), args.max_jobs))

    for idx, country in enumerate(country_list):
        statuses[country] = {}
        for scraper in scrapers:
            result = scraper.scrape_country(country, args.max_jobs)
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
            line = "  {} {:<7} {:<8} fetched={:<4} kept={}".format(
                country, scraper.name, result.status, result.fetched, kept)
            print(line)
            for err in result.errors[:3]:
                print("      ! {}".format(err))
        if idx < len(country_list) - 1:
            polite_sleep(tuple(cfg["batch"].get("delay_between_countries_sec", [5, 12])))

    # Eligible set + cap priority use the run's effective min_score.
    translated = translate_titles(scored_jobs, cfg, min_score=min_score)
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
        "totals": dict(totals, translated_titles=translated, new=added,
                       updated=updated, pruned=pruned, active=active,
                       notified_telegram=notified),
        "countries": statuses,
    }
    store.write_run_status(run_status)

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

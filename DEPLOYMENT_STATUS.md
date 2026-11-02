# Deployment preparation status - November 2, 2026

Not deployed yet. No hosted tier timings or Telegram delivery verified.

- Original offline suite: 162 passed, 4 opt-in live tests skipped.
- EURES live smoke: PL/DE/NL, 50 results each, 93.6 seconds total,
  country-source health all OK. Dedup/scoring/pruning retained 72 active jobs.
- Title translation was throttled; originals preserved. Added stop-on-throttle
  behavior and cap on all translation attempts, including failures.
- Single bounded Indeed/Poland live probe: 3 jobs, OK, 2.6 seconds.
- These local measurements do not establish GitHub runner availability.
- Fixed JobSpy DataFrame error metadata handling so blocked sources do not
  become false healthy zero-result outcomes.
- Added queue:max for scraper/deployment concurrency and initial honest
  empty notifier state. All five runtime files remain trackable.
- No fabricated job records, no enabled LinkedIn, no configured proxy.
- Final cadence remains provisional until hosted per-tier measurement.

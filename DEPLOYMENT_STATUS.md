# Deployment status - November 3, 2026

Deployed and running hosted. This file supersedes the November 2
"not deployed yet" preparation note.

- Hosted runs on GitHub Actions are live: tier-3 rotation 03:37 UTC,
  manual tier-1 05:58 UTC and scheduled tier-1 06:13 UTC on
  November 3 (data commits c9d6b32, 37dd8c5, a39b53e).
- Scheduled runs are gated by the SCHEDULES_ENABLED repository
  variable; manual workflow_dispatch remains available at any time.
- Dashboard published on GitHub Pages:
  https://hunter-job-01.github.io/EU-Unskilled-Jobs-Aggregator/
- CI green on main (6660280): 431 passed, 4 skipped.
- Dataset snapshot November 3, 08:13 EET: 1477 active jobs
  (jobspy 886, EURES 560, sweden_jobsearch 31); 610 fetched in the
  tier-1 run (92 new, 296 updated, 7 pruned, 187 excluded,
  19 below minimum score).
- Title translation: keyless route remains throttled (0 titles that
  run); stop-on-throttle guard active. Gemini batch and Arabic layer
  are score-gated and capped per run.
- Telegram delivery still unverified: notified_telegram = 0 across the
  store; activation depends on hosted secrets that cannot be confirmed
  from the repository.
- Five runtime data files remain trackable. No fabricated job records,
  no enabled LinkedIn, no configured proxy, $0 cost.

# Source expansion (2026-11-02) - what was applied from the owner-supplied registry

Owner instruction: review the supplied `eu27-job-sources` research archive and
apply its implementation prompt to THIS running project. What the prompt itself
allows was applied; what it gates behind further approval stays gated.

## Applied now (code + tests, no live activation)

- `config/source_registry.json` - the researched 141-entry catalogue adapted to
  repo conventions. Research fields kept verbatim; added `countries`,
  `languages`, `connector_type`, `repo_adapter`, `activation_gate`,
  `terms_checked_on`, `last_probe`, `last_successful_run`. No credentials.
  It is a catalogue of NAMES with evidence, not live coverage.
- `src/source_registry.py` - loader + validator (unique IDs, valid URLs, exact
  EU27 allowlist, every source disabled, implemented adapters gated).
- `src/eu_location.py` - EU27 location grounding: exact 27-state allowlist,
  multilingual country-name resolution (EN + endonyms + working languages),
  ISO alpha-3 mapping, structured-field-over-text precedence, non-EU rejection
  (UK/CH/NO/IS/LI/EG/Gulf even on European boards), and quarantine states for
  unknown / contradictory / unconstrained-remote locations. Search country is
  a lead, never proof; official country-scoped datasets may contribute
  dataset-scope evidence, recorded as such.
- `src/dedup.py` - corroborated identity evidence (canonical URL with only
  known tracking params stripped, source+source_id, description hash + same
  employer + same country) vs weak title+employer+country collisions that are
  flagged for review and NEVER auto-merged. Existing uids / notified / pruned
  flags are untouched.
- `src/source_health.py` + `data/source_health.json` - every run now records
  per source-country status, fetched/kept, errors, notes and honest counters
  in a separate additive file. Failed/blocked/skipped sources stay visible;
  job state files are untouched.
- `scripts/coverage_report.py` - EU27 catalogue matrix + observed source runs.
- `scripts/probe_new_sources.py` - opt-in (`--live`) read-only probes into an
  isolated output dir; never touches data/, config, or the notifier.
- New adapters, ALL `enabled: false` in `config/config.yaml`:
  - `arbeitnow` (documented free no-key API; EU board only, UK excluded;
    remote rows quarantined without an explicit EU country)
  - `sweden_jobsearch` (Platsbanken/JobTech; Sweden only)
  - `czech_mpsv` (official daily export; streamed, never loads the ~185MB
    export whole; preserves cizinecMimoEu / zamestnaneckaKarta / azylant /
    salary / hours evidence; records without URL, employer or workplace are
    quarantined, not invented)
  - `latvia_nva` (public-sector CSV; current resource resolved from CKAN
    metadata each run; feed freshness validated against its own newest row)
  - `ats_public` (Greenhouse/Lever/SmartRecruiters PUBLIC postings of
    owner-reviewed tenants only; empty tenant list = no-op; never INTERNAL
    postings or privileged access)

## Live probe results (2026-11-02, read-only, isolated output)

- arbeitnow: 1 request, 300 rows -> 28 EU, 20 non-EU rejected, 252 quarantined
  (mostly unconstrained remote). Entry-level yield to be measured over time.
- sweden_jobsearch: 10/10 hits grounded to Sweden after mapping the API's
  numeric country_code to its `country` label. No key required today; the
  getting-started text still mentions one - re-confirm before enabling.
- czech_mpsv: schema endpoint 200 ("volna-mista"), distribution 200,
  184,986,429 bytes - streaming is required, not optional.
- latvia_nva: current CSV `vakances-2026-11-02.csv`, modified 2026-11-01
  23:02 UTC - genuinely fresh at probe time (metadata "daily" claims were
  verified against the resource, not trusted).

## NOT done (prompt gates these behind explicit owner approval)

- No source enabled in deployed config; `sources.*` all `enabled: false`.
- No schedule, notifier, hosting, existing config value or stored job changed.
- No account creation, API keys, registrations (Adzuna/Jooble/Careerjet/VDAB/
  France Travail/Finland) - permission/onboarding backlog stays visible in
  the registry. LinkedIn stays off (express permission required). Indeed/
  Glassdoor/HelloWork unchanged (unofficial library support is not operator
  permission). InfoJobs Italy stays excluded (closing 2025-12-31).
- ATS tenants list is empty: employer boards are added only after the owner
  reviews them with evidence URLs.

## Tests

Baseline before this change: 238 passed / 4 skipped (live marked).
After: 288 passed / 4 skipped. Installed python-jobspy verified: 1.4.1
(latest; the `>=1.4.1,<2` range currently resolves to exactly 1.4.1).

## Rollback

Code only; no state or config behaviour depends on it (all new sources
disabled). To roll back: `git revert <this commit>` - data files, notified
flags and schedules are unaffected. To remove just one adapter later, delete
its `sources.<name>` block and its `src/scrapers/<name>.py`; main.py only
instantiates requested sources.

## Next steps that need owner approval

1. Review the probe report and approve enabling specific sources (config diff
   shown before activation). Sweden/Czech/Latvia/Arbeitnow are the
   probe-verified candidates.
2. Provide reviewed employer boards (careers-page URLs) for the ATS route.
3. Decide whether any permission/onboarding source (Adzuna evaluation key,
   Jooble country key, ...) is worth its quota limits.

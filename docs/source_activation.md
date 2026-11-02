# Source activation table (2026-11-03)

Honest per-source state. Levels, in order: catalog_only (name recorded,
no code) -> probe_ok (live route verified once) -> adapter_tested
(fixture + unit tests) -> enabled (config flag on, runs in pipeline).
A source being enabled says NOTHING about whether an Egypt-resident
applicant is eligible for its listings - see the eligibility column.

| source_id | adapter | state | enabled | applicant_from_egypt | evidence date |
|---|---|---|---|---|---|
| eures | eures_scraper | active_existing | yes | not assessed per posting | - |
| jobspy_indeed | jobspy_scraper | active_existing | yes | not assessed per posting | - |
| arbeitnow | arbeitnow | active_existing | yes | not assessed per posting | - |
| sweden_jobsearch | sweden_jobsearch | active_existing | yes | not assessed per posting | - |
| czech_mpsv | czech_mpsv | active_existing (weekly only) | yes | not assessed per posting | 2026-11-02 |
| latvia_nva | latvia_nva | active_existing | yes | not assessed per posting | - |
| ats_public | ats_public | adapter present | NO (tenants list empty) | n/a | - |
| nl_otto_workforce | ottoworkforce | probe + fixture + live smoke | YES | unknown_not_assessed | 2026-11-02 |
| eu_anyworkanywhere | anyworkanywhere | probe + fixture + live smoke | YES | unknown_not_assessed | 2026-11-02 |
| pl_cbop | pl_cbop | probe + fixture + live smoke | YES | unknown_not_assessed | 2026-11-02 |
| pl_ewl | none | catalog_only | NO | restricted_non_eu_resident_current (operator stated 2026-11-02 it is temporarily not serving citizens residing outside the EU) | 2026-11-02 |
| de_ba (Bundesagentur fur Arbeit) | none | access unavailable | NO | not assessed | 2026-11-02 |

Notes:
- de_ba: documented public API returned 403 from our egress on all
  bounded attempts. Recorded as access-unavailable; retried only if an
  official changed endpoint or new external signal appears. No
  route-hopping around the denial.
- nl_otto_workforce fixes country at NL by source design (Dutch agency
  board) and records that assumption in scrape stats.
- eu_anyworkanywhere takes country per posting from JSON-LD
  jobLocation.addressCountry; expired postings (validThrough) skipped.
- pl_cbop search results carry no full description; OHP-posted rows are
  routed through a labour office and labelled as such.
- ~131 further registry entries remain catalog_only with their own
  per-source notes; they are names, not live coverage.

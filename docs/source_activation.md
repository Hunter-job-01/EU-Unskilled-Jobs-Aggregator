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

## Probe wave 2026-11-03 (plain HTTP, one-off research UA)

Listing-level positives:
- nl_jobs: vacancy pages carry JobPosting JSON-LD; sitemap lists /jobs/ urls -> probe_ok, adapter candidate
- nl_covebo / nl_hobij / nl_ab_midden / eu_goodmorning: /vacatures listing pages 200 with 19-62 job links; vacancy-page parsing pending
- at_ams / be_vdab / be_leforem / be_actiris / hr_hzz x2 / ee_tootukassa / fi_job_market / pt_iefponline / si_ess_zrsz / eu_adecco / eu_grafton(.cz) / eu_manpower(.com) / pl_infopraca / lv_ss / at_allejobs / pl_gremi: homepage 200, no landing anti-bot; listing endpoint discovery pending
- dk_workindenmark / dk_jobnet: SPA shells; JSON endpoint discovery pending

Blocked / unavailable from probe egress (kept disabled, no route-hopping):
- de_ba_jobsuche: API 403 on all bounded variants (2026-11-02)
- ie_jobsireland: 403; lt_uzt: 403 + Cloudflare; mt_jobsplus: 200 but Cloudflare markers
- eu_pickingjobs: 403; pl_gowork: 403 + Cloudflare
- nl_werk_nl / bg_az / lt_work_in_lithuania / eu_myrobin: connection failure or repeated timeout
- eu_seasonalworkers: /jobs search is JS-driven, no plain-HTTP links

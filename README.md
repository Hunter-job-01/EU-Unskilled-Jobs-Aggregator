# EU-Unskilled-Jobs-Aggregator

Continuously collects **entry-level / no-experience vacancies** -
warehouse, production, packing, order picking, factory, logistics,
sorting, loading, cleaning, agriculture, farms, greenhouses, food
processing, hospitality, hotels, kitchen help, housekeeping, laundry,
retail stocking, delivery help and general labour - across **all 27 EU
member states**, scores them for relevance to non-EU job seekers, and
keeps a single deduplicated dataset.

> **Phase 4 of 4.** This repository contains the runnable core (config
> matrix, keyword/scoring engine, EURES + JobSpy scrapers, storage and
> CLI), the automation layer (scheduled GitHub Actions workflows, CI
> and Dependabot), the static GitHub Pages dashboard (`docs/` +
> `pages.yml`) and the Telegram digest (`src/notifier.py`, below).

**Phase 4 expansion (Part A), in brief.** The multilingual matrix now
covers 22 language codes - `en, pl, de, cs, sk, lt, hu, ro, nl, es,
it, fr, pt, sv, da, fi, el, bg, hr, sl, et, lv` - the primary
official/working languages of all 27 member states, for both the broad
unskilled roles above *and* a dedicated `no_exp_or_training` signal
("no experience required" / "training provided" phrasing). Honest
scope note: these are the 22 codes from the expansion brief, not
literally every official EU language - Irish (`ga`) and Maltese (`mt`)
are not keyword languages; Ireland and Malta are queried with their
configured languages (`en`), which matches how vacancies there are
actually posted. That signal is negation-exempt by design - these
phrases are positive *because* they negate a requirement, so cues like
"no", "ohne", "bez", "sin" never suppress them - earns a +15 score
bonus, and can qualify a posting for base points on its own when no
role keyword matched (never when skilled/senior vocabulary or a
disqualifier matched). It lives in the signals, not the role keywords,
so "senior accountant - no experience needed" still scores zero. The
dashboard gained a matching "No experience / Training provided" filter
checkbox and chip.

Signal matching is negation-aware with finite, sentence-scoped heuristics
(prefix cues with a word-distance scope and requirement-marker cutoffs,
suffix denials like "sponsorship not available", hyphenated "non-EU"
excluded, self-affirming phrases like "housing provided" protected): a
posting that explicitly denies a benefit does not earn that signal, while
genuine flexibility phrasing ("no language required") still counts. These
are heuristics, not full NLP - unusual phrasing can still mislead, which
is one more reason a score is a review hint, not a verdict.

## Honest limitations - read before trusting the output

- **A score is relevance evidence, not legal eligibility.** Keyword
  signals such as `permit_keyword_mention`, `housing_keyword_mention`,
  `agency_or_relocation_mention` or `no_exp_or_training_mention` mean
  the *posting text mentions* these things. They do **not** prove the employer sponsors work permits, and
  they say nothing about whether candidates applying from Egypt (or any
  specific non-EU country) will be accepted. Verify each vacancy
  directly with the employer or agency before acting on it.
- **EURES** is queried through the public unauthenticated JSON API used
  by the official portal's own frontend
  (`https://europa.eu/eures/api/jv-searchengine/...`). It is not an
  officially supported API: no published rate limits, no stability
  guarantee. The scraper is deliberately polite and degrades gracefully.
- **JobSpy/Indeed covers only 18 of the 27 EU states** (verified against
  python-jobspy 1.4.1): AT, BE, CZ, DK, FI, FR, DE, GR, HU, IE, IT, LU,
  NL, PL, PT, RO, ES, SE. BG, HR, CY, EE, LV, LT, MT, SI and SK are
  **skipped** by the JobSpy source and served by EURES only. So the
  JobSpy-unsupported tier-1 states (LT, SK) are not under-sampled next
  to PL/CZ/DE, EURES compensates there with a wider keyword set and
  deeper pagination (`batch.eures_boost`, extensible to any country).
  Indeed also blocks datacenter IPs aggressively; failures are isolated
  per country and logged in `data/run_status.json`.
- **LinkedIn via JobSpy** is implemented (opt-in: set
  `batch.jobspy_sites: [indeed, linkedin]`). LinkedIn has no per-country
  domain in JobSpy, so those queries are scoped by the country name as a
  location string. LinkedIn blocks datacenter traffic hardest: a live
  probe from a datacenter network returned zero rows. Expect it to
  contribute little; it stays off by default. Source restrictions are
  not bypassed with proxies or other workarounds.
- **Title translation** uses a keyless free endpoint via
  `deep-translator`; it is best-effort and capped per run. Scoring runs
  BEFORE translation, and only non-excluded jobs at or above the run's
  effective minimum score (`--min-score`, else
  `scoring.min_score_default`; `translation.min_score`, default 40, when
  the helper is used standalone) spend a translation request - highest
  score first, so the cap is never wasted on low-value or excluded
  postings. Every attempt counts toward the cap, including failures. A
  throttle response stops further translation requests for that run.
  Untranslated titles keep their original text.
- **Proxies are a fallback only.** Requests go direct first; free
  proxies are unreliable and frequently blocked by Cloudflare. Nothing
  depends on them.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python main.py                          # tier 1 (PL, CZ, DE, LT, HU, RO, SK, NL)
python main.py --tier 2                 # ES, IT, FR, BE, PT, IE
python main.py --tier 3                 # remaining 13 states
python main.py --tier all               # all 27 (slow, polite delays)
python main.py --countries PL,DE,NL     # explicit batch
python main.py --sources eures          # skip JobSpy
python main.py --max-jobs 30 --min-score 40
python main.py --no-translate -v
python main.py --no-notify            # skip the Telegram digest
```

## Output

- `data/active_jobs.json` - the single deduplicated store
  (`company + normalized title + country` hash), pruned of postings
  older than 30 days on every run. Each record also carries the Phase 4
  notification state (`notified_telegram`, `notified_at`), preserved
  across re-scrapes; `JobStore.get_unnotified_top_jobs(min_score,
  limit=15)` and `JobStore.mark_notified(uids)` are the digest helpers.
- `data/pruned_uids.json` - rolling tombstone registry of pruned UIDs
  (last 5,000 / 60 days, both configurable). While a UID is remembered,
  a still-live posting re-scraped after pruning is not re-ingested as
  brand new, so it cannot re-trigger downstream notifications. Once a
  tombstone expires or is evicted, an old still-live posting can
  reappear once on a later run - the accepted trade-off for a bounded,
  git-friendly file.
- `data/latest_export.csv` - everything active, sorted by score.
- `data/telegram_state.json` - daily digest gate (`last_digest_utc`)
  plus per-job delivery failure counts (Phase 4).
- `data/run_status.json` - last run: timestamp, parameters, per
  country/source success, partial, skipped or failed with error detail.

One failing country or source never crashes the run.

## Automation (Phase 2)

Everything under `.github/` runs on GitHub's free tier for public
repositories. Nothing is self-hosted and nothing costs money.

### Workflows

| Workflow | File | Trigger | What it does |
| --- | --- | --- | --- |
| Daily priority scrape | `.github/workflows/scrape_daily_priority.yml` | `schedule` + `workflow_dispatch` | Tier 1 twice daily, Tier 2 every 2 days, Tier 3 rotating subset daily (every Tier-3 country at least every 4 days) |
| Weekly full EU scan | `.github/workflows/scrape_weekly_full_eu.yml` | `schedule` (Sunday 03:07 UTC) + `workflow_dispatch` | All 27 countries, tiers 1-3 sequentially |
| CI tests | `.github/workflows/ci_tests.yml` | `push` + `pull_request` (ignoring `data/**`) | Offline `pytest` suite |
| Pages deploy | `.github/workflows/pages.yml` | `workflow_run` (both scrapers, success only) + `push` to main (`docs/**`, `data/**`) + `workflow_dispatch` | Assembles and deploys the static dashboard |

**Schedules are UTC and fire on the default branch only.** GitHub does
not run `schedule` triggers on other branches, and it can delay
scheduled runs by minutes under load - do not treat the cron times as
exact. GitHub also **disables scheduled workflows automatically after
60 days of no repository activity**; any push (or a manual re-enable in
the Actions tab) restarts them.

*Why the Tier-2 cadence is not a cron expression:* POSIX cron cannot
say "every 48 hours". The usual approximation `*/2` in the day-of-month
field fires on odd dates (1, 3, ..., 31), so after the 31st it fires
again on the 1st - two runs on consecutive days. The daily workflow
therefore wakes every day at 04:41 UTC and a gate step runs the Tier-2
scrape only on odd Unix-epoch days (`floor(now/86400) % 2 == 1`), which
is an 48-hour eligibility pattern (actual execution remains best-effort). Skipped days exit successfully without
touching the network or the repository. The resolver identifies which
cron fired by reading `schedule` from the event payload
(`$GITHUB_EVENT_PATH`), never by interpolating event data into shell.

*Why Tier 3 also rotates daily:* a weekly-only scan leaves 13 countries
stale for up to 7 days. The daily workflow additionally fires at 02:53
UTC and the resolver partitions the 13 Tier-3 countries (read from
`config/config.yaml`, never hardcoded) into 4 epoch-day groups, so each
Tier-3 country is scraped at least every 4 days - staggered away from
the Tier-1/Tier-2 runs and in addition to the Sunday full EU sweep.
Every EU country is covered by automation; none waits a full week.

### What gets committed, and the honest cost

Each scraper run commits the data files back to the repository, but
only when their content actually changed (`git add -A -- data/` +
`git diff --cached --quiet` guard; `-A` staging also tolerates files
that do not exist yet, like `data/pruned_uids.json` before the first
prune, and picks up deletions - `.gitignore` keeps `data/.cache/` out).
The five tracked runtime files:

- `data/active_jobs.json` - the deduplicated store
- `data/latest_export.csv` - the latest CSV export
- `data/run_status.json` - the last-run audit trail
- `data/pruned_uids.json` - the prune tombstone registry
- `data/telegram_state.json` - once-per-day digest gate and failure counters

The tombstone file must be committed alongside the store: it is the
only thing that stops a still-live posting re-scraped after its 30-day
pruning from being re-ingested as brand new (the Phase 1 prune /
re-ingestion fix). If it lived only on the runner, every scheduled run
would start with an empty registry and the fix would silently break.

**Git-history growth, stated plainly.** The original design goal was
"zero repo bloat" - no data files in Git at all, archives only as
Artifacts. Committing the five latest-overwrite files was an explicit
later request, and it changes that trade-off: the *working tree* stays
small (each file is overwritten in place, never snapshotted per day),
but *Git history* still grows by one diff per committing run (roughly
15 Tier-1/Tier-2 runs a week plus 4 weekly-scan commits, each the size
of the jobs churn). `active_jobs.json` churns the most because postings
enter and leave. This is the accepted cost of having the live dataset
readable straight from the repo (which the Phase 3 dashboard will rely
on). If history size ever becomes a problem, the remedies are: squash
the `data:` commits periodically (`git rebase`/`git filter-repo` and a
force-push), or move the data files to a dedicated orphan branch and
point the workflows at it. Historical archives deliberately stay out of
Git: the daily workflow uploads `latest_export.csv` + `run_status.json`
as Artifacts (14-day retention) and the weekly workflow uploads a full
snapshot bundle (30-day retention).

**Race safety.** Both scraper workflows share one repo-wide concurrency
group (`data-scrape-pipeline`, `cancel-in-progress: false`, `queue: max`),
so daily, weekly and manual runs never write `data/` at the same time.
Up to 100 pending runs are retained rather than silently replacing a
pending country/tier batch. GitHub processes the queue by when runs
start waiting, not necessarily trigger time. The weekly
matrix additionally runs tiers strictly sequentially
(`max-parallel: 1`, `fail-fast: false`). Every committing job checks out
the branch's *current head* (`ref: github.ref_name`) rather than the
trigger-time SHA - without this, weekly tiers 2 and 3 would scrape
against the store as it was before tier 1's commit and their commits
would rebase-conflict on the same JSON files. After committing, the
step rebases onto anything that landed mid-run and pushes. The failure
modes are handled differently on purpose: a rebase *conflict* is
deterministic, so the step aborts and fails honestly on the first
attempt (retrying the identical conflict cannot succeed, and nothing is
ever force-overwritten); only a push *rejection* (a commit landing
between rebase and push) is retried, bounded to 3 attempts.
`permissions: contents: write` is set at the workflow level; the bot
commits as `github-actions[bot]` and data commits carry `[skip ci]`.

> **Protected branches:** if you add a branch protection rule to the
> default branch, the `GITHUB_TOKEN` push will be rejected unless the
> rule allows the GitHub Actions bot to bypass it. Either exempt the
> bot, or switch the commit step to a pull-request flow.

### Running a scrape manually

Actions tab -> workflow -> **Run workflow**. Inputs:

- `tier` (`1`, `2`, `3`, `all`) - which country tier to scrape.
- `countries` - optional comma-separated ISO codes (`PL,CZ,DE`),
  overrides `tier`. Whitespace is stripped first (`PL, DE` works), then
  the value is validated against the strict ISO shape before use.
- `sources` - `eures,jobspy`, `eures` or `jobspy`.
- `max_jobs` - per country per source, 1-200 (default 50).
- `min_score` - 0 means "use `scoring.min_score_default`", otherwise
  1-100.
- `no_translate` - skip title translation for the run.

All inputs reach the shell only through environment variables (never
inline `${{ ... }}` interpolation inside `run:` blocks) and are
validated in a resolver step before the scraper sees them; invalid
values fail the run loudly. `tests/test_workflows.py` asserts this
property so a future edit cannot quietly reintroduce the injection
path.

### CI

`ci_tests.yml` runs the offline unit suite (scoring, storage, scrapers,
keywords, workflow YAML) on every push and pull request. Events that
touch only `data/**` (i.e. the bot's own data commits) are ignored via
`paths-ignore`, so automation never burns CI minutes on itself. Live
network tests stay opt-in (`pytest -m live`) and never run in CI.

### Dependabot (self-maintenance)

`.github/dependabot.yml` opens weekly update PRs for:

- **pip** - scoped to the five pinned packages: `python-jobspy` and
  `deep-translator` (both track moving external endpoints and must stay
  fresh), plus `requests`, `PyYAML` and `pytest`.
- **github-actions** - keeps `actions/checkout`, `actions/setup-python`
  and `actions/upload-artifact` on current majors.

The pins are deliberately a major or two behind the newest releases
where the user asked for a specific version (e.g. `setup-python@v5`);
Dependabot will propose the bumps as ordinary PRs, and CI runs on each
one. Dependency PRs never touch `data/**`, so each one gets a full CI
run before merge.

### Free-tier fit

- Verified against official GitHub documentation on November 2, 2026:
  standard hosted runners in public repositories are free with unlimited
  runner minutes. This project uses `ubuntu-latest`, not paid larger runners.
  No private-repository quota is assumed. First real hosted measurements
  (November 2, 2026, 50 jobs/country/source, title translation skipped):
  Tier 1 4m29s, Tier 2 3m03s, Tier 3 5m42s. Scraper-only times were
  245.7s / 165.6s / 325.5s. All 27 EURES checks and all 18 supported
  Indeed checks reported OK; 9 unsupported Indeed countries skipped.
  This run retained 982 active records after scoring/pruning, not 982
  independently verified available vacancies. Future source health varies.
  The requested cadence is enabled through `SCHEDULES_ENABLED=true`.
  `run_status.json` also retains timestamped `latest_by_country` checks
  across tiers; top-level countries/totals still describe the last run.
- Free artifact storage is 500 MB and cache storage is 10 GB per repository.
  Daily artifacts retain 14 days, weekly snapshots 30 days. Dataset history
  still grows in Git even when the live JSON is bounded.
- Pages published-site maximum is 1 GB, with a soft bandwidth limit of
  100 GB/month and 10-minute deployment timeout. The soft 10-builds/hour
  limit does not apply to custom Actions publication workflows.
- Free pricing does not waive GitHub fair-use terms. Avoid disproportionate
  burden and use Actions only for this repository's software and published
  project output. Cron delivery is best-effort, can be delayed or dropped,
  and public schedules may be disabled after 60 days without repo activity.
- Official references:
  https://docs.github.com/en/actions/concepts/billing-and-usage
  https://docs.github.com/en/actions/reference/limits
  https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits
  https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency
  https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
  https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features
- Every job sets `timeout-minutes` (120 daily / 240 weekly per tier /
  20 CI), far below GitHub's 6-hour per-job ceiling, so a hung scraper
  is killed instead of burning the ceiling.
- Artifact retention is capped (14 days daily, 30 days weekly) so
  storage does not accumulate silently.

## Dashboard (Phase 3)

`docs/` holds a zero-build, zero-dependency static dashboard
(`index.html` + `style.css` + `app.js`, vanilla HTML5/CSS3/JS) that
reads the committed data files directly.

### What it shows

- **Bilingual titles** - `title_en` prominent, the original
  local-language `title` beneath it.
- **Badges & chips** - country flag + ISO code, tier, source, a
  colour-coded score badge, and chips for matched signals
  (`no_exp_or_training_mention`, `permit_keyword_mention`,
  `housing_keyword_mention`, `language_flex_keyword_mention`,
  `agency_or_relocation_mention`, `fresh_posting`). Signal chips are labelled as *mentions*: a keyword
  match is a review hint, not proof an employer provides the benefit.
  The dashboard carries the same disclaimer as this README, and the
  score is presented as a relevance hint, never as eligibility.
- **Filters & sorting** - instant search (title, translated title,
  company, location, matched keywords), country filter with job counts,
  tier / source filters, minimum-score slider, "posting must mention"
  signal checkboxes (AND semantics), and sorting by score, newest
  (`posted_date` / `first_seen`) or country priority (tier, then name).
- **Automation status banner** - last run time, parameters and totals
  from `run_status.json`, plus per-country/source health with error
  details. Honest limitation, shown on the page: `run_status.json`
  reflects the *most recent* run only, and the weekly scan overwrites
  it after each sequential tier - so right after a weekly run the
  banner shows that scan's last tier, not the health of all 27
  countries. Cross-run history lives in the weekly snapshot artifacts.
- **Direct apply links & CSV export** - external job URLs are validated
  (http/https only) and open with `rel="noopener noreferrer"`; the CSV
  button downloads `data/latest_export.csv`. All rendering uses
  `textContent` - scraped posting text is untrusted and never becomes
  markup.

### How auto-update works

Data commits pushed by the scraper bots use `GITHUB_TOKEN`, and
GitHub deliberately does not let such commits trigger `on: push`
workflows. `pages.yml` therefore listens to `workflow_run` for both
scraper workflows (`completed` on `main`, success conclusion only) -
the documented exception that fires even for token-started runs. It
also redeploys on human pushes touching `docs/**` or `data/**` and on
manual dispatch. Each deploy:

1. checks out the default branch's current head (never artifacts from
   the triggering run - nothing untrusted is executed or published),
2. copies `docs/*` and the `data/` files into `_site/` so relative
   `fetch('data/...')` works under any Pages base path, and
3. deploys via the official `configure-pages` / `upload-pages-artifact`
   / `deploy-pages` actions (`permissions: pages: write, id-token:
   write`, one queued deployment at a time).

Two activation caveats: `workflow_run` only fires while `pages.yml`
exists on the **default branch**, and GitHub Pages must be switched to
"GitHub Actions" as the source (one-time, below).

### Activation (one-time, manual)

1. Push this repository to GitHub (default branch `main`).
2. Repo **Settings -> Pages -> Build and deployment -> Source:
   "GitHub Actions"**.
3. Done. The next successful scheduled scrape (or a manual *Run
   workflow* on "Deploy dashboard to GitHub Pages") publishes the site
   at the URL shown on the deploy job. Before the first scrape commits
   data, the dashboard renders its empty state instead of failing.

### Local preview

The deploy serves the three dashboard files with `data/` as a sibling
directory, so a faithful preview mirrors that layout:

```bash
mkdir -p /tmp/site/data
cp docs/index.html docs/style.css docs/app.js docs/favicon.svg /tmp/site/
cp data/*.json data/*.csv /tmp/site/data/ 2>/dev/null || true
python -m http.server -d /tmp/site 8000
# open http://localhost:8000/
```

## Telegram digest (Phase 4)

`src/notifier.py` sends a daily HTML digest of the best new vacancies
to a Telegram chat through the Bot API (`sendMessage`), at the end of
every `main.py` run - **before** the workflows' git commit, so the
`notified_telegram` flags it sets are committed to
`data/active_jobs.json` in the same data commit and no job is ever
digested twice.

### What it sends

- Jobs with `notified_telegram == false` and `score >=
  telegram.min_score` (default 50), highest score first, capped at
  `telegram.max_jobs_per_digest` (default 15) per run, optionally
  restricted to `telegram.focus_countries`.
- Per job: country flag + ISO code, score (`[85/100]`), English title
  with the original beneath it, company, location, signal chips
  (`🎓 No Exp / Training`, `🏠 Housing`, `🪪 Permit`, `🗣️ Lang
  Flex`, `🏢 Agency`) and the direct apply link. All dynamic
  text is `html.escape()`d; pages stay under 3,800 characters with a
  polite delay between them, and HTTP 429 answers honour Telegram's
  Retry-After (bounded retries).
- `JobStore.mark_notified(uids)` runs per delivered page, only after
  Telegram accepted it - a failed or crashed digest never marks an
  unsent job, so it is retried on a later run.

### Setup (one-time)

1. In Telegram, message **@BotFather** -> `/newbot`, pick a name; he
   replies with the bot token.
2. Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and read your
   chat's `chat.id` from the JSON.
3. In the GitHub repo: **Settings -> Secrets and variables -> Actions
   -> New repository secret**, add `TELEGRAM_BOT_TOKEN` (the token) and
   `TELEGRAM_CHAT_ID` (the chat id). Both workflows already pass them
   to the scraper step through the environment - never through shell -
   so they never appear in logs or in this repository.
4. Optional: tune `telegram:` in `config/config.yaml` (`enabled`,
   `min_score`, `max_jobs_per_digest`, `focus_countries`,
   `dashboard_url`, `message_delay_sec`). Set `dashboard_url` to the
   Pages URL once the dashboard is live.

**Graceful no-op:** until both secrets exist (or with
`telegram.enabled: false`, or `--no-notify` on the CLI), the notifier
logs one info line and exits 0 - automated runs never fail because the
bot is not configured, and no message is ever sent to an unresolved
audience.

**Daily cadence, stated exactly.** `main.py` runs several times a day
(Tier 1 twice daily, Tier 2 every two days, the Tier-3 rotation), but
the digest goes out **at most once per UTC day**: the first successful
digest of the day records `last_digest_utc` in
`data/telegram_state.json` (committed with the other data files) and
later runs that day skip. If a digest fails completely, no gate is
written and the next run retries the same jobs the same day.

**Delivery correctness.** `mark_notified` runs per delivered page, only
after Telegram accepted that page. Send failures are classified:
outages, 5xx, auth/config errors (401/403/404) and exhausted 429 rate
limits are *transient* - the digest pauses, nothing is counted, and the
same jobs retry on a later run. HTTP 400 is classified by Telegram's
error description: "chat not found", a bad chat id, a blocked bot or
missing rights are *configuration* problems (transient, logged, never
counted); only a rejection of the message content itself ("can't parse
entities", "message is too long", empty text, wrong entity - retried
once automatically as plain text first, plus 413) counts against
the jobs on that page; after 3 such digests a poisoned posting is
skipped - left unnotified, logged for manual review - so it can never
starve every lower-scored job. One unavoidable duplicate window
remains: Telegram only tells us a message was accepted, and a crash
between acceptance and the `mark_notified` write (or between that
write and the workflow's data commit) loses the mark, so that job may
appear in one later digest. Within that crash window, a failure can
only duplicate, not lose, a notification - that is a statement about
the crash window, not an overall delivery guarantee: jobs quarantined
after repeated permanent rejections are deliberately left undelivered
(and visible in `telegram_state.json` for manual review), and the Bot
API offers no idempotency keys to close the duplicate window.

## Tests

```bash
pip install -r requirements-dev.txt
pytest                 # offline unit tests (incl. .github YAML validation)
pytest -m live         # live smoke checks against EURES/Indeed (network)
```

## Layout

```
.github/workflows/        daily + weekly scrapers, CI tests, Pages deploy
.github/dependabot.yml    weekly pip & action updates
docs/                     static dashboard (index.html, style.css, app.js)
config/config.yaml        27 countries, tiers, 22-language keyword &
                          signal matrix, scoring weights, batch limits,
                          Telegram digest settings
src/keywords.py           diacritics-insensitive multilingual matching
src/scorer.py             dedup hash, signal tags, 0-100 scoring, translation
src/scrapers/base.py      UA rotation, backoff, optional proxy fallback
src/scrapers/eures_scraper.py
src/scrapers/jobspy_scraper.py
src/storage.py            active_jobs.json / CSV / run_status.json
src/notifier.py           Telegram digest (Phase 4)
main.py                   CLI runner (scrape, score, store, notify)
tests/                    offline unit tests (incl. workflow YAML and
                          frontend validation) + opt-in live smoke tests
```


### Multiple digest destinations

Set the repository secret `TELEGRAM_CHAT_IDS` to a comma-separated list of
verified destination IDs for delivery to both a private bot chat and a
channel. Keep `TELEGRAM_BOT_TOKEN` as a repository secret. The legacy
single `TELEGRAM_CHAT_ID` remains supported when the plural setting is absent.

Each destination has its own UTC daily gate, delivered-UID registry and
per-job failure counter under `telegram_state.json`'s `destinations` map.
Success in one chat cannot suppress a failed or newly added destination.
In multi-destination mode the per-job legacy global notification flag is
not used to decide delivery. Only destinations explicitly chosen by the
owner should be configured; channel posting rights must be verified.

## Private evidence-gated notifications

The public dashboard is a broad job collection, **not a personalized shortlist**.
Private Telegram delivery now requires complete reviewed source evidence in
addition to a broad relevance score. Unknown or missing requirements do not pass.
A permit mention is not sponsorship; an existing-work-permit requirement is not
an offer to sponsor an overseas applicant. Training is not a waiver of language,
licence or education requirements. Results are not padded to a fixed count.

Private fit configuration uses repository secrets `JOB_FIT_PROFILE`,
`JOB_FIT_REVIEWS`, and `JOB_FIT_STATE_KEY`. Never commit a real CV, profile,
review ledger, plaintext delivery state, or fit outcomes. The private profile
contains qualification criteria only, no name/contact details. Delivery state is
authenticated-encrypted before persistence; existing audience gates and accepted
job marks are preserved. Missing/invalid profile/key/state means no send, with no
generic fallback. Do not rotate the encryption key without decrypting/migrating
existing state first. Back up the key privately before changing it.

Evidence reviews cover role, spoken language/level, licences, experience,
education and authorization with exact source quotations. Preferred, waived,
mandatory and unknown conditions are distinct. A reviewed source must be HTTPS,
publicly accessible, unchanged and checked within 24 hours. New, changed, blocked
or unreviewed vacancies are withheld. At most30 reviewed detail pages are checked
per run with a two-second delay; redirects/challenges are not bypassed.

**Coverage limitation:** the GitHub scraper does not autonomously prove complete
eligibility from arbitrary multilingual job text. Positive matches require a
separate fresh evidence review. Empty private review coverage intentionally sends
zero vacancies, not generic jobs disguised as tailored matches. Operating an
ongoing review/discovery service is separate work from this protective gate.
Candidate-specific review work must stay private and preserve these boundaries.

### Unattended requirement triage

Automatic mode no longer waits for a manual review ledger. It checks up to30
rotating candidates/run against full EURES detail JSON (including mandatory
licences and required versus desired language levels) or public detail HTML.
Known conflicts are excluded. Clear complete English evidence can be matched
without manual review; unsupported/incomplete conditions are uncertain, not fit.
The user may receive up to5 clearly marked uncertain vacancies per digest with
unresolved requirement domains named in Arabic. Inaccessible pages are marked
unverified. Source links/original titles remain. No second same-day digest is
forced when mode changes. A match is preliminary, never a visa/acceptance promise.

Positive natural-language extraction currently supports explicit English wording;
structured and limited multilingual conflict checks cover more languages. This
is not universal parsing of all EU job requirements. Local Arabic role labels
are category labels, **not full translations**. The prior unauthenticated Google
translation route was throttled and is not bypassed. Full translation remains a
separate capability to validate before enabling.

### Full Arabic translation (local CPU)

The replacement translation path uses public Helsinki-NLP Marian models locally
on the GitHub runner, not the throttled Google endpoint. Public model files may
be downloaded/cached, but no CV or private criteria are submitted for translation.
Source→English→Arabic pivoting can change meaning. Translation is display-only,
never authority for eligibility. The full Arabic text and full original text
are paired in a private UTF-8 text document digest with links and status marks.
All model input tokens are processed in windows; unsupported languages, failed
models and incomplete source pages are explicitly labelled, not silently cut.
A failed document upload leaves jobs unnotified; successful acceptance preserves
per-destination daily gates. No extra same-day digest is forced.

Full translation is enabled only after the hosted CPU/model smoke check passes.
Languages use dedicated source→English pairs where available; a language in the
configured set is not a guarantee that its public model can be loaded or that
its output is accurate. Always check the original requirements. This changes the
private digest presentation, not the broad public dashboard's language.

Validation outcome: model download/inference works on GitHub, but full real-job
translation quality did not pass (dropped or changed numeric/qualification text
and distorted employer/location wording). Full Arabic translation therefore
remains **OFF**, despite green infrastructure smoke checks. Those checks prove
execution, not semantic accuracy. Scheduled scrapes do not install heavy model
dependencies while this feature is off. Automatic job triage/uncertain labels
remain active. Full translation is a known remaining outcome, not completed.

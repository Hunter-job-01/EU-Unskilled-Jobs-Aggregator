"""Phase 2: structural validation of the GitHub automation files.

These tests parse the workflow YAML and dependabot.yml and assert the
expected triggers, permissions and safety properties. They run offline
in the normal suite.

PyYAML 1.1 pitfall: the unquoted key ``on:`` parses as boolean True, so
trigger lookups must check both ``on`` and ``True``.
"""
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
DAILY = WORKFLOWS / "scrape_daily_priority.yml"
WEEKLY = WORKFLOWS / "scrape_weekly_full_eu.yml"
CI = WORKFLOWS / "ci_tests.yml"
DEPENDABOT = REPO_ROOT / ".github" / "dependabot.yml"

CRON_FIELD = re.compile(r"^(\*|\*/\d+|\d+|\d+-\d+|\d+(,\d+)*|\*/\d+(,\d+)*)$")


def load_yaml(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def triggers(doc):
    """The `on:` mapping, tolerating YAML 1.1 parsing it as True."""
    assert isinstance(doc, dict), "workflow document must be a mapping"
    on = doc.get("on", doc.get(True))
    assert on is not None, "workflow is missing its `on:` trigger block"
    return on


def all_run_steps(doc):
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            if "run" in step:
                yield step


def assert_valid_cron(expr):
    fields = expr.split()
    assert len(fields) == 5, f"cron '{expr}' must have exactly 5 fields"
    for field in fields:
        for part in field.split(","):
            assert CRON_FIELD.match(part), f"bad cron field '{part}' in '{expr}'"


# ---------------------------------------------------------------- files

def test_automation_files_exist():
    for path in (DAILY, WEEKLY, CI, DEPENDABOT):
        assert path.is_file(), f"missing {path.relative_to(REPO_ROOT)}"


@pytest.mark.parametrize("path", [DAILY, WEEKLY, CI, DEPENDABOT],
                         ids=lambda p: p.name)
def test_yaml_is_valid(path):
    doc = load_yaml(path)
    assert isinstance(doc, dict) and doc, f"{path.name} did not parse to a mapping"


# ------------------------------------------------- daily priority run

def test_daily_triggers():
    on = triggers(load_yaml(DAILY))
    crons = [entry["cron"] for entry in on["schedule"]]
    assert len(crons) == 3, \
        "expected Tier-1, Tier-2 gate and Tier-3 rotation crons"
    for expr in crons:
        assert_valid_cron(expr)
    assert any(len(e.split()[1].split(",")) == 2 for e in crons), \
        "Tier 1 must run twice daily (two hours in the cron hour field)"
    dispatch = on["workflow_dispatch"]
    for name in ("tier", "countries", "sources", "max_jobs", "min_score", "no_translate"):
        assert name in dispatch["inputs"], f"missing manual input '{name}'"


def test_daily_tier3_rotation_covers_every_tier3_country():
    """Phase 4 scheduling: a daily rotating Tier-3 slot so no EU country
    waits a whole week. The resolver must key the rotation off the epoch
    day, read the tier list from config.yaml (never a hardcoded copy),
    and pass the subset via --countries."""
    on = triggers(load_yaml(DAILY))
    crons = [entry["cron"] for entry in on["schedule"]]
    assert "53 2 * * *" in crons, "missing the daily Tier-3 rotation cron"
    run_blocks = "\n".join(step["run"] for step in all_run_steps(load_yaml(DAILY)))
    assert "'53 2 * * *')" in run_blocks, "resolver must handle the rotation cron"
    assert "rotation" in run_blocks
    assert "config/config.yaml" in run_blocks, \
        "rotation must read tiers from config.yaml, not a hardcoded list"
    # The embedded python one-liner partitions tier 3 into 4 disjoint
    # epoch-day groups covering all 13 countries.
    import subprocess
    cfg = yaml.safe_load((REPO_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    tiers = cfg["tiers"]
    tier3 = list(tiers.get("3") or tiers[3])
    groups = [subprocess.run(
        ["python", "-c",
         "import sys, yaml; c = yaml.safe_load(open('config/config.yaml', encoding='utf-8')); "
         "tiers = c['tiers']; t3 = list(tiers.get('3') or tiers[3]); "
         "g = int(sys.argv[1]) % 4; print(','.join(t3[g::4]))", str(day)],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    ).stdout.strip().split(",") for day in range(4)]
    flat = [c for group in groups for c in group]
    assert sorted(flat) == sorted(tier3), \
        f"rotation groups must partition tier 3: {groups}"
    assert all(group for group in groups), "no rotation group may be empty"


@pytest.mark.parametrize("path", [DAILY, WEEKLY], ids=lambda p: p.name)
def test_scrape_steps_receive_telegram_secrets_via_env(path):
    """Phase 4: the digest needs TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.
    They must reach the run only through the environment (secrets
    context), never interpolated into the shell, so an unset secret is a
    harmless empty string and the notifier no-ops cleanly."""
    doc = load_yaml(path)
    steps = [s for s in all_run_steps(doc)
             if 'python main.py "${ARGS[@]}"' in s["run"]]
    assert steps, f"{path.name} has no scraper run step"
    for step in steps:
        env = step.get("env") or {}
        assert env.get("TELEGRAM_BOT_TOKEN") == "${{ secrets.TELEGRAM_BOT_TOKEN }}", \
            "bot token must come from secrets via env"
        assert env.get("TELEGRAM_CHAT_ID") == "${{ secrets.TELEGRAM_CHAT_ID }}", \
            "chat id must come from secrets via env"
        for run in (step["run"],):
            assert "secrets.TELEGRAM" not in run, \
                "secrets must never be interpolated into the shell"


def test_daily_permissions_and_runtime():
    doc = load_yaml(DAILY)
    assert doc["permissions"]["contents"] == "write"
    job = doc["jobs"]["scrape"]
    steps = job["steps"]
    setup = next(s for s in steps if str(s.get("uses", "")).startswith("actions/setup-python@"))
    assert setup["uses"] == "actions/setup-python@v5"
    assert str(setup["with"]["python-version"]) == "3.11"
    assert setup["with"]["cache"] == "pip"
    upload = next(s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact@"))
    assert upload["with"]["retention-days"] == 14
    assert "data/latest_export.csv" in upload["with"]["path"]
    assert "data/run_status.json" in upload["with"]["path"]


def _commit_steps(path):
    doc = load_yaml(path)
    return [s for s in all_run_steps(doc) if "git commit" in s["run"]]


@pytest.mark.parametrize("path", [DAILY, WEEKLY], ids=lambda p: p.name)
def test_commit_guard_and_safe_staging(path):
    """Commit only on real changes; stage data/ with `git add -A` so a
    not-yet-existing file (e.g. pruned_uids.json before the first prune)
    cannot fail the step with a pathspec error, and deletions are picked
    up. .gitignore keeps data/.cache/ out of the glob."""
    commits = _commit_steps(path)
    assert commits, f"{path.name} has no commit step"
    for step in commits:
        run = step["run"]
        assert "git diff --cached --quiet" in run, "commit must be guarded by a diff check"
        assert "git add -A -- data/" in run,             "stage via `git add -A -- data/`; explicit pathspecs fail fatally on missing files"
        assert "git add data/active_jobs.json" not in run,             "explicit per-file pathspec staging is the missing-tombstone bug"
        assert "git pull --rebase" in run, "push must rebase first to survive races"


@pytest.mark.parametrize("path", [DAILY, WEEKLY], ids=lambda p: p.name)
def test_rebase_conflict_fails_honestly(path):
    """A rebase conflict is deterministic: the identical conflict must NOT
    be retried, and nothing may be force-overwritten. Only push rejections
    (a commit landing between rebase and push) may retry, bounded."""
    for step in _commit_steps(path):
        run = step["run"]
        assert "git rebase --abort" in run
        conflict_branch = re.search(
            r"if ! git pull --rebase.*?git rebase --abort.*?exit 1", run, re.S)
        assert conflict_branch,             "rebase conflict must abort and exit 1 immediately, not loop"
        assert "--force" not in run and "push -f" not in run,             "never force-push over a conflict"
        assert re.search(r"for attempt in 1 2 3\b", run),             "push-rejection retry loop must stay bounded (3 attempts)"


@pytest.mark.parametrize("path,jobs", [
    (DAILY, ["scrape"]),
    (WEEKLY, ["scrape-tier", "snapshot"]),
], ids=lambda v: v if isinstance(v, Path) else ",".join(v))
def test_checkout_uses_fresh_branch_head(path, jobs):
    """Jobs that commit (or read post-commit state) must check out the
    branch's current head, not the trigger-time SHA - otherwise weekly
    matrix tiers 2/3 scrape against the pre-tier-1 store and their
    commits rebase-conflict on the same JSON files."""
    doc = load_yaml(path)
    for job in jobs:
        checkouts = [s for s in doc["jobs"][job]["steps"]
                     if str(s.get("uses", "")).startswith("actions/checkout@")]
        assert checkouts, f"{path.name}:{job} has no checkout step"
        for step in checkouts:
            assert step.get("with", {}).get("ref") == "${{ github.ref_name }}",                 f"{path.name}:{job} must checkout github.ref_name (fresh head)"


def test_daily_countries_input_tolerates_spaces():
    """Phase 3 hardening fix 2 companion: a manually entered 'PL, DE'
    must be normalized (whitespace stripped, env-var only) before the
    strict ISO validation, instead of failing or word-splitting."""
    doc = load_yaml(DAILY)
    resolve = next(s for s in all_run_steps(doc) if "IN_COUNTRIES" in s["run"])
    assert "tr -d '[:space:]'" in resolve["run"], \
        "strip whitespace from the countries input before validating"
    assert "${{ inputs.countries }}" not in resolve["run"], \
        "inputs must reach the shell only via the IN_COUNTRIES env var"


def test_data_commits_skip_ci_and_rebase_before_push():
    """Phase 3 hardening fix 3: bot data commits carry [skip ci] and the
    push is always preceded by a rebase onto the CURRENT branch (never a
    hardcoded main - manual dispatch branch intent must survive)."""
    for path in (DAILY, WEEKLY):
        for step in _commit_steps(path):
            run = step["run"]
            assert "[skip ci]" in run, f"{path.name}: data commits must skip CI"
            rebase_at = run.rfind("git pull --rebase")
            push_at = run.rfind("git push")
            assert -1 < rebase_at < push_at, \
                f"{path.name}: rebase must happen before the push"
            assert 'origin main' not in run, \
                f"{path.name}: never rebase onto a hardcoded main - use the current branch"


def test_daily_every_two_days_gate():
    doc = load_yaml(DAILY)
    resolve = next(s for s in all_run_steps(doc) if "86400" in s["run"])
    assert "% 2" in resolve["run"], "Tier 2 needs the epoch-parity 48-hour gate"
    assert "skip=true" in resolve["run"]


def test_scraper_workflows_serialize_on_shared_concurrency_group():
    """Phase 3 hardening fix 1: daily + weekly + manual runs share one
    repo-wide lock so they queue instead of racing on data/ commits."""
    for path in (DAILY, WEEKLY):
        doc = load_yaml(path)
        assert doc["concurrency"]["group"] == "data-scrape-pipeline", \
            f"{path.name} must share the repo-wide data-scrape-pipeline group"
        assert doc["concurrency"]["cancel-in-progress"] is False


# ------------------------------------------------------- weekly scan

def test_weekly_triggers_and_matrix():
    doc = load_yaml(WEEKLY)
    on = triggers(doc)
    crons = [entry["cron"] for entry in on["schedule"]]
    assert len(crons) == 1
    assert_valid_cron(crons[0])
    assert crons[0].split()[4] == "0", "weekly scan must run on Sunday (weekday 0)"
    assert "workflow_dispatch" in on
    job = doc["jobs"]["scrape-tier"]
    assert job["strategy"]["max-parallel"] == 1, "tiers must run sequentially"
    assert job["strategy"]["fail-fast"] is False
    assert sorted(job["strategy"]["matrix"]["tier"]) == ["1", "2", "3"]
    assert job["timeout-minutes"] <= 360, "must stay under GitHub's 6h job limit"


def test_weekly_snapshot_artifact():
    doc = load_yaml(WEEKLY)
    snap = doc["jobs"]["snapshot"]
    assert snap["needs"] == "scrape-tier"
    upload = next(s for s in snap["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@"))
    assert upload["with"]["retention-days"] == 30


# ---------------------------------------------------------------- CI

def test_ci_triggers_and_scope():
    doc = load_yaml(CI)
    on = triggers(doc)
    for event in ("push", "pull_request"):
        assert event in on, f"CI must trigger on {event}"
        assert "data/**" in on[event]["paths-ignore"], \
            f"data-only commits must not trigger CI on {event}"
    assert doc["permissions"]["contents"] == "read"
    steps = doc["jobs"]["test"]["steps"]
    assert any("pytest" in s.get("run", "") for s in steps)
    assert not any("-m live" in s.get("run", "") for s in steps), \
        "live network tests must stay opt-in and out of CI"


# --------------------------------------------------------- dependabot

def test_dependabot_config():
    doc = load_yaml(DEPENDABOT)
    assert doc["version"] == 2
    eco = {u["package-ecosystem"]: u for u in doc["updates"]}
    assert "pip" in eco and "github-actions" in eco
    assert eco["pip"]["schedule"]["interval"] == "weekly"
    assert eco["github-actions"]["schedule"]["interval"] == "weekly"
    allowed = {a["dependency-name"] for a in eco["pip"]["allow"]}
    assert {"python-jobspy", "deep-translator"} <= allowed, \
        "python-jobspy and deep-translator must stay fresh"


# ------------------------------------------------- shell-injection guard

@pytest.mark.parametrize("path", [DAILY, WEEKLY, CI], ids=lambda p: p.name)
def test_no_raw_input_interpolation_in_run_blocks(path):
    """github.event.inputs.* / inputs.* must reach shell only via env vars."""
    doc = load_yaml(path)
    for step in all_run_steps(doc):
        assert "inputs." not in step["run"], \
            f"{path.name}: a run block interpolates inputs directly (injection risk)"


@pytest.mark.parametrize("path", [DAILY, WEEKLY, CI], ids=lambda p: p.name)
def test_input_values_reach_shell_only_through_env(path):
    doc = load_yaml(path)
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            env = step.get("env", {})
            for var, val in env.items():
                if isinstance(val, str) and "inputs." in val:
                    assert re.fullmatch(r"\$\{\{\s*inputs\.[a-z_]+\s*\}\}", val), \
                        f"env {var} mixes inputs with other text: {val!r}"


# ------------------------------------------------- bash syntax of run blocks

@pytest.mark.parametrize("path", [DAILY, WEEKLY, CI], ids=lambda p: p.name)
def test_run_blocks_are_valid_bash(path, tmp_path):
    """Every run: script must pass `bash -n` (no stray GitHub expressions,
    no syntax errors). Skipped where bash is unavailable."""
    import shutil
    import subprocess
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    doc = load_yaml(path)
    scripts = list(all_run_steps(doc))
    assert scripts, f"{path.name} has no run steps at all"
    for idx, step in enumerate(scripts):
        script = tmp_path / f"step{idx}.sh"
        script.write_text(step["run"])
        proc = subprocess.run([bash, "-n", str(script)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, \
            f"{path.name} step {step.get('name', idx)}: bash -n failed:\n{proc.stderr}"


def test_all_27_countries_are_on_an_automated_schedule():
    """Part A.3: every EU country is scraped by automation without
    timeouts - Tier 1 twice daily, Tier 2 every 2 days, Tier 3 on the
    daily rotation plus the Sunday full sweep. The tiers partition the
    configured countries, and each tier has a live scheduled path."""
    cfg = yaml.safe_load((REPO_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    tiers = {str(k): list(v) for k, v in cfg["tiers"].items()}
    flat = [c for members in tiers.values() for c in members]
    assert sorted(flat) == sorted(cfg["countries"].keys())
    assert len(flat) == 27
    on_daily = triggers(load_yaml(DAILY))
    crons = [e["cron"] for e in on_daily["schedule"]]
    assert "23 5,17 * * *" in crons   # tier 1, twice daily
    assert "41 4 * * *" in crons      # tier 2, 48h-gated
    assert "53 2 * * *" in crons      # tier 3, daily rotation
    on_weekly = triggers(load_yaml(WEEKLY))
    assert any(e["cron"] == "7 3 * * 0" for e in on_weekly["schedule"]), \
        "the weekly full 27-country sweep must remain scheduled"
    matrix_tiers = load_yaml(WEEKLY)["jobs"]["scrape-tier"]["strategy"]["matrix"]["tier"]
    assert sorted(matrix_tiers) == ["1", "2", "3"]


@pytest.mark.parametrize('path', [DAILY, WEEKLY])
def test_scraper_queue_keeps_pending_runs(path):
    assert load_yaml(path)['concurrency']['queue'] == 'max'


def test_weekly_validation_does_not_reject_empty_scheduled_inputs():
    steps = load_yaml(WEEKLY)['jobs']['scrape-tier']['steps']
    validation = next(s for s in steps if s.get('name') == 'Validate manual inputs')
    assert validation['if'] == "github.event_name == 'workflow_dispatch'"


def test_pages_can_read_current_repository():
    doc = load_yaml(WORKFLOWS / 'pages.yml')
    assert doc['permissions']['contents'] == 'read'
    checkout = next(s for s in doc['jobs']['deploy']['steps'] if s.get('uses', '').startswith('actions/checkout@'))
    assert checkout['with']['ref'] == 'main'


# ------------------------------------------------- force-channel guard


def _force_guard_lines():
    lines = DAILY.read_text(encoding="utf-8").splitlines()
    guard = [l.strip() for l in lines
             if l.strip().startswith('[ -n "$TELEGRAM_CHANNEL_ID" ]')]
    member = [l.strip() for l in lines
              if l.strip().startswith("printf '%s' \"$TELEGRAM_CHAT_IDS\"")]
    assert guard, "force-channel guard missing the TELEGRAM_CHANNEL_ID check"
    assert member, "force-channel guard missing the membership check"
    return guard[0], member[0]


def test_force_channel_membership_check_handles_dash_leading_ids():
    # Regression: channel ids start with -100; without `-e` grep parses the
    # pattern as options and the guard fails even for a valid destination.
    _, member = _force_guard_lines()
    assert "grep -qxF -e" in member, \
        "dash-leading channel ids require grep -e so the id is not parsed as options"


@pytest.mark.parametrize("chat_ids,channel_id,ok", [
    ("5754931747,-1004256976824", "-1004256976824", True),
    ("5754931747, -1004256976824", "-1004256976824", True),
    ("5754931747,-1004256976824", "-1009999999999", False),
    ("5754931747", "-1004256976824", False),
    ("5754931747,-1004256976824", "", False),
])
def test_force_channel_guard_executes_fail_closed(chat_ids, channel_id, ok):
    # Runs the exact guard lines shipped in the workflow: arms only when the
    # exact channel id is one of the configured destinations, fails closed
    # on a missing secret or a non-destination id.
    import os
    import subprocess
    guard, member = _force_guard_lines()
    env = dict(os.environ, TELEGRAM_CHAT_IDS=chat_ids,
               TELEGRAM_CHANNEL_ID=channel_id)
    result = subprocess.run(["bash", "-c", guard + "\n" + member],
                            env=env, capture_output=True, text=True)
    assert (result.returncode == 0) == ok, result.stderr

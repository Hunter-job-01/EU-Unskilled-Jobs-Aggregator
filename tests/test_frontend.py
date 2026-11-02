"""Phase 3: structural validation of the static dashboard and the
GitHub Pages deployment workflow.

Offline tests: they verify the frontend files exist, bind to the real
JSON schemas the pipeline writes, stay free of injection-prone patterns,
and that pages.yml is wired to redeploy automatically after scheduled
scrapes. They never run the workflows or publish anything.
"""
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
PAGES = WORKFLOWS / "pages.yml"
DAILY = WORKFLOWS / "scrape_daily_priority.yml"
WEEKLY = WORKFLOWS / "scrape_weekly_full_eu.yml"
DOCS = REPO_ROOT / "docs"
INDEX = DOCS / "index.html"
STYLE = DOCS / "style.css"
APPJS = DOCS / "app.js"

JOB_DATACLASS_FIELDS = {
    "title", "company", "country", "source", "url", "source_id",
    "description", "location", "posted_date", "languages", "uid",
    "title_en", "translated", "score", "signals", "matched_keywords",
    "excluded", "exclusion_reason", "notified_telegram", "notified_at",
    "first_seen", "last_seen",
}

RUN_STATUS_KEYS = {"last_run_utc", "duration_sec", "parameters",
                   "totals", "countries"}


def load_yaml(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def triggers(doc):
    on = doc.get("on", doc.get(True))
    assert on is not None, "workflow is missing its `on:` trigger block"
    return on


# ------------------------------------------------------------- files

def test_frontend_files_exist():
    for path in (INDEX, STYLE, APPJS, PAGES):
        assert path.is_file(), f"missing {path.relative_to(REPO_ROOT)}"


def test_no_placeholders_anywhere():
    for path in (INDEX, STYLE, APPJS, PAGES):
        text = path.read_text(encoding="utf-8")
        for marker in ("TODO", "FIXME", "XXX", "lorem ipsum"):
            assert marker.lower() not in text.lower(), \
                f"{path.name} still contains placeholder marker {marker!r}"


# ------------------------------------------- pages workflow wiring

def test_pages_yaml_is_valid():
    doc = load_yaml(PAGES)
    assert isinstance(doc, dict) and doc


def test_pages_deploys_after_scheduled_scrapes():
    """The critical auto-update guarantee: GITHUB_TOKEN data commits do
    not trigger on:push, so pages.yml must listen to workflow_run for
    BOTH scraper workflows, on the default branch, on completion."""
    on = triggers(load_yaml(PAGES))
    wr = on["workflow_run"]
    daily_name = load_yaml(DAILY)["name"]
    weekly_name = load_yaml(WEEKLY)["name"]
    assert daily_name in wr["workflows"], "pages.yml must listen for the daily scraper"
    assert weekly_name in wr["workflows"], "pages.yml must listen for the weekly scraper"
    assert "completed" in wr["types"]
    assert "main" in wr["branches"], "only upstream runs on the default branch may deploy"


def test_pages_also_deploys_on_push_and_manual_dispatch():
    on = triggers(load_yaml(PAGES))
    assert "main" in on["push"]["branches"]
    paths = on["push"]["paths"]
    assert "docs/**" in paths and "data/**" in paths
    assert "workflow_dispatch" in on, "manual redeploy must stay possible"


def test_pages_only_deploys_successful_upstream_runs():
    doc = load_yaml(PAGES)
    condition = str(doc["jobs"]["deploy"]["if"])
    assert "workflow_run.conclusion" in condition and "success" in condition, \
        "a failed scraper run must not deploy"


def test_pages_permissions_and_concurrency():
    doc = load_yaml(PAGES)
    assert doc["permissions"]["pages"] == "write"
    assert doc["permissions"]["id-token"] == "write"
    assert doc["permissions"].get("contents") == "read", \
        "checkout needs read access, never repository contents: write"
    assert doc["concurrency"]["group"] == "pages"
    assert doc["concurrency"]["cancel-in-progress"] is False


def test_pages_uses_current_official_actions():
    uses = [step.get("uses", "")
            for step in load_yaml(PAGES)["jobs"]["deploy"]["steps"]]
    assert "actions/configure-pages@v6" in uses
    assert "actions/upload-pages-artifact@v5" in uses
    assert "actions/deploy-pages@v5" in uses


def test_pages_never_executes_run_artifacts_or_secrets():
    """The deploy must build from a checkout of the default branch, never
    from artifacts produced by the triggering (untrusted-context) run,
    and must not touch repository secrets."""
    text = PAGES.read_text(encoding="utf-8")
    assert "download-artifact" not in text
    assert "secrets." not in text
    doc = load_yaml(PAGES)
    for step in doc["jobs"]["deploy"]["steps"]:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            assert step.get("with", {}).get("ref") == "main", \
                "checkout must use current trusted main, not a run-supplied ref"


def test_pages_assembles_site_with_data():
    doc = load_yaml(PAGES)
    runs = [s.get("run", "") for s in doc["jobs"]["deploy"]["steps"]]
    assemble = next(r for r in runs if "_site" in r)
    for f in ("docs/index.html", "docs/style.css", "docs/app.js", "docs/favicon.svg"):
        assert f in assemble, f"assemble step must copy {f}"
    for f in ("active_jobs.json", "run_status.json", "latest_export.csv"):
        assert f in assemble, f"assemble step must publish data/{f}"
    assert ".nojekyll" in assemble
    upload = next(s for s in doc["jobs"]["deploy"]["steps"]
                  if str(s.get("uses", "")).startswith("actions/upload-pages-artifact@"))
    assert upload["with"]["path"].rstrip("/") == "_site"


def test_pages_run_blocks_are_valid_bash(tmp_path):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    doc = load_yaml(PAGES)
    scripts = [s["run"] for s in doc["jobs"]["deploy"]["steps"] if "run" in s]
    assert scripts
    for idx, script_text in enumerate(scripts):
        script = tmp_path / f"step{idx}.sh"
        script.write_text(script_text)
        proc = subprocess.run([bash, "-n", str(script)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, f"pages.yml step {idx}: bash -n failed:\n{proc.stderr}"


def test_pages_has_no_input_interpolation_in_run_blocks():
    doc = load_yaml(PAGES)
    for step in doc["jobs"]["deploy"]["steps"]:
        if "run" in step:
            assert "inputs." not in step["run"]


# --------------------------------------------- JSON field bindings

def test_app_binds_only_real_job_fields():
    """Every job.<field> the dashboard reads must exist on the Job
    dataclass - a typo here silently renders nothing in production."""
    src = APPJS.read_text(encoding="utf-8")
    used = set(re.findall(r"\bjob\.([a-z_]+)", src))
    unknown = used - JOB_DATACLASS_FIELDS
    assert not unknown, f"app.js reads non-existent Job fields: {unknown}"
    for required in ("title", "title_en", "company", "country", "location",
                     "posted_date", "first_seen", "score", "signals",
                     "matched_keywords", "url", "excluded"):
        assert required in used, f"app.js must bind job.{required}"


def test_app_binds_real_run_status_keys():
    src = APPJS.read_text(encoding="utf-8")
    main_src = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    for key in RUN_STATUS_KEYS:
        assert f'"{key}"' in src or f"'{key}'" in src or f".{key}" in src, \
            f"app.js must render run_status.{key}"
        assert key in main_src, f"run_status key {key} must exist in main.py"
    # per-country health entries bind the ScrapeResult.to_status() shape
    for key in ("status", "fetched", "errors"):
        assert key in src
        base_src = (REPO_ROOT / "src" / "scrapers" / "base.py").read_text(encoding="utf-8")
        assert f'"{key}"' in base_src


def test_signal_chip_names_match_scorer():
    """Checkbox values and chip keys must be real signal names the scorer
    emits - the scorer is the source of truth."""
    scorer_src = (REPO_ROOT / "src" / "scorer.py").read_text(encoding="utf-8")
    real_signals = set(re.findall(r'signals\.append\("([^"]+)"\)', scorer_src))
    for expected in ("permit_keyword_mention", "housing_keyword_mention",
                     "language_flex_keyword_mention",
                     "agency_relocation_text_mention", "employment_agency_source",
                     "fresh_posting"):
        assert expected in real_signals, \
            f"{expected} is not a real scorer signal - check scorer.py"
    index = INDEX.read_text(encoding="utf-8")
    checkbox_values = set(re.findall(
        r'<input type="checkbox"[^>]*value="([^"]+)"', index))
    assert checkbox_values, "index.html must ship signal checkboxes"
    assert checkbox_values <= real_signals, \
        f"checkbox values must be real scorer signals: {checkbox_values - real_signals}"
    app = APPJS.read_text(encoding="utf-8")
    for sig in checkbox_values | {"fresh_posting"}:
        assert sig in app, f"app.js must render a chip for {sig}"


def test_source_filter_values_match_scrapers():
    """The Source filter is data-driven: app.js builds options from the
    loaded jobs and labels them via SOURCE_LABELS. Every label key must be
    a real scraper source id; the select itself ships only the default
    'All sources' option in index.html."""
    index = INDEX.read_text(encoding="utf-8")
    default_opts = re.findall(
        r'<select id="filter-source"[^>]*>\s*<option value="([^"]*)">', index)
    assert default_opts == [""], "filter-source must ship only the default option"
    app = APPJS.read_text(encoding="utf-8")
    labels_block = re.search(r'var SOURCE_LABELS = \{(.*?)\};', app, re.S)
    assert labels_block, "app.js must define SOURCE_LABELS"
    label_keys = set(re.findall(r'^\s*([a-z_]+):', labels_block.group(1), re.M))
    assert {"eures", "jobspy_indeed", "jobspy_linkedin"} <= label_keys
    # every label key is a source id some enabled scraper can emit
    registry = json.loads((REPO_ROOT / "config" / "source_registry.json").read_text(encoding="utf-8"))
    known = {"eures", "jobspy_indeed", "jobspy_linkedin", "ats_public"}
    known |= {s.get("repo_adapter") for s in registry.get("sources", []) if isinstance(s, dict)}
    unknown = label_keys - known
    assert not unknown, f"SOURCE_LABELS keys with no matching source: {unknown}"
    # eures scraper sets source=self.name="eures"; jobspy sets
    # source="jobspy_{site}" - see src/scrapers/.
    for src_file, needle in (("eures_scraper.py", 'name = "eures"'),
                             ("jobspy_scraper.py", 'source="jobspy_{}".format(site)')):
        text = (REPO_ROOT / "src" / "scrapers" / src_file).read_text(encoding="utf-8")
        assert needle in text


def test_country_meta_matches_config():
    cfg = yaml.safe_load((REPO_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    tiers = {str(k): v for k, v in cfg["tiers"].items()}
    tier_of = {c: int(t) for t, members in tiers.items() for c in members}
    app = APPJS.read_text(encoding="utf-8")
    found = re.findall(
        r"(\w{2}): \{ name: '([^']+)',\s+tier: (\d) \}", app)
    entries = {code: (name, tier) for code, name, tier in found}
    assert len(entries) == 27, "COUNTRY_META must cover all 27 EU states"
    for code, (name, tier) in entries.items():
        assert code in cfg["countries"], f"{code} not in config.yaml"
        assert cfg["countries"][code]["name"] == name, f"{code} name mismatch"
        assert tier_of[code] == int(tier), f"{code} tier mismatch"
    for code in cfg["countries"]:
        assert code in entries, f"{code} missing from COUNTRY_META"


# --------------------------------------------- safety properties

def test_no_innerhtml_anywhere():
    """Scraped posting text is untrusted: rendering must use textContent
    only, never innerHTML."""
    assert ".innerHTML" not in APPJS.read_text(encoding="utf-8")


def test_external_links_are_validated_and_isolated():
    app = APPJS.read_text(encoding="utf-8")
    assert "noopener noreferrer" in app
    assert "'_blank'" in app
    assert "https:" in app and "http:" in app, \
        "URL validation must restrict protocols"
    assert "javascript:" not in app.lower()


def test_frontend_has_no_external_dependencies():
    """Zero build steps, zero CDN: all assets are the three local files."""
    index = INDEX.read_text(encoding="utf-8")
    assert not re.search(r'<script[^>]+src="https?://', index)
    assert not re.search(r'<link[^>]+href="https?://', index)
    app = APPJS.read_text(encoding="utf-8")
    assert not re.search(r"fetch\(\s*['\"]https?://", app), \
        "fetches must stay relative so any Pages base path works"
    assert not re.search(r"fetch\(\s*['\"]/", app), \
        "leading-slash fetches break on project Pages (base /<repo>/)"


def test_truthful_labelling():
    """Signals are keyword mentions, not proof of provision - the UI must
    say so, and the score must be presented as a hint, not eligibility."""
    index = INDEX.read_text(encoding="utf-8")
    assert "not" in index and "proof" in index
    assert "mention" in index.lower()
    app = APPJS.read_text(encoding="utf-8")
    assert "Work permit mentioned" in app
    assert "Housing mentioned" in app


def test_run_status_limitation_is_disclosed():
    """run_status.json reflects the LAST run only (after a weekly scan:
    its last tier). The banner must say this instead of implying
    all-country health."""
    app = APPJS.read_text(encoding="utf-8")
    assert "most recent run only" in app
    assert "last tier" in app


# --------------------------------------------- html / css / js sanity

class _IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "id" and value:
                self.ids.add(value)


def test_all_js_element_ids_exist_in_html():
    parser = _IdCollector()
    parser.feed(INDEX.read_text(encoding="utf-8"))
    app = APPJS.read_text(encoding="utf-8")
    wanted = set(re.findall(r"getElementById\('([^']+)'\)", app))
    assert wanted, "app.js must look up elements"
    missing = wanted - parser.ids
    assert not missing, f"app.js references ids missing from index.html: {missing}"


def test_css_braces_balanced():
    css = STYLE.read_text(encoding="utf-8")
    assert css.count("{") == css.count("}") and css.count("{") > 0


def test_app_js_syntax_via_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available for a JS syntax check")
    proc = subprocess.run([node, "--check", str(APPJS)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"node --check failed:\n{proc.stderr}"


def test_no_exp_filter_checkbox_and_chip():
    """Phase 4 frontend: the 'No experience / Training provided' filter
    checkbox binds the real scorer signal and app.js renders its chip."""
    index = INDEX.read_text(encoding="utf-8")
    assert 'id="sig-noexp"' in index
    assert 'value="no_exp_or_training_mention"' in index
    assert "No experience / Training provided" in index
    app = APPJS.read_text(encoding="utf-8")
    assert "no_exp_or_training_mention" in app
    assert "No experience / Training" in app

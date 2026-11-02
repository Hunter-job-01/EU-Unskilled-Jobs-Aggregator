"""Consolidated CI: four workflows total, focused checks live in
ci_tests.yml, and the public tree carries no secret-shaped values."""
import importlib.util
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _load_gate_module():
    spec = importlib.util.spec_from_file_location(
        "verify_private_gate", REPO_ROOT / "scripts" / "verify_private_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_four_workflow_files():
    names = sorted(p.name for p in WORKFLOWS.glob("*.yml"))
    assert names == ["ci_tests.yml", "pages.yml",
                     "scrape_daily_priority.yml", "scrape_weekly_full_eu.yml"]


def test_ci_tests_contains_all_focused_checks():
    doc = yaml.safe_load((WORKFLOWS / "ci_tests.yml").read_text(encoding="utf-8"))
    assert set(doc["jobs"]) == {"test", "focused-checks"}
    steps = doc["jobs"]["focused-checks"]["steps"]
    runs = [s.get("run", "") for s in steps]
    for script in ("check_offline_translation.py", "check_gemini_translation.py",
                   "check_automatic_requirements.py", "verify_private_gate.py"):
        assert any(script in r for r in runs), script
    # the manual dispatch path still runs every check
    detect = next(s for s in steps if s.get("id") == "changes")
    assert "workflow_dispatch" in detect["run"]


def test_secret_dependent_checks_skip_pull_requests():
    doc = yaml.safe_load((WORKFLOWS / "ci_tests.yml").read_text(encoding="utf-8"))
    steps = doc["jobs"]["focused-checks"]["steps"]
    for name in ("Check guarded free Arabic translation",
                 "Check automatic requirement extraction",
                 "Verify private notification gate"):
        step = next(s for s in steps if s.get("name") == name)
        assert "pull_request" in step["if"]
    gate = next(s for s in steps if s.get("name") == "Verify private notification gate")
    assert set(gate["env"]) == {"JOB_FIT_PROFILE", "JOB_FIT_REVIEWS", "JOB_FIT_STATE_KEY"}


def test_public_tree_has_no_secret_shaped_values():
    module = _load_gate_module()
    assert module.scan_public_tree(REPO_ROOT) == []


def test_secret_patterns_catch_shapes():
    module = _load_gate_module()
    assert module.SECRET_PATTERNS["telegram_bot_token"].search("token " + "9" * 9 + ":" + "A" * 35)
    assert module.SECRET_PATTERNS["gemini_api_key"].search("key " + "AI" + "za" + "B" * 35)
    assert module.SECRET_PATTERNS["private_key_block"].search("-----BEGIN " + "PRIVATE KEY" + "-----")
    assert not module.SECRET_PATTERNS["telegram_bot_token"].search("chat id 1234567890")

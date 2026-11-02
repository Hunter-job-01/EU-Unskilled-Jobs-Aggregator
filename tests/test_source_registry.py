import copy

import pytest

from src.eu_location import EU27
from src.source_registry import (ACTIVATION_GATES, RegistryError,
                                 load_registry, validate_registry,
                                 coverage_summary)


@pytest.fixture(scope="module")
def registry():
    return load_registry()


def test_registry_loads_and_validates(registry):
    assert len(registry["sources"]) == 143  # 141 catalogue + nl_otto_workforce + eu_anyworkanywhere (2026-11-02)
    assert registry["catalog_is_not_deployment"] is True
    assert registry["default_new_sources_enabled"] is False


def test_unique_ids_and_urls(registry):
    ids = [s["source_id"] for s in registry["sources"]]
    assert len(ids) == len(set(ids))
    for s in registry["sources"]:
        assert s["url"].startswith(("http://", "https://"))


def test_allowlist_exact(registry):
    assert set(registry["eu27_allowlist"]) == set(EU27)


def test_all_new_sources_disabled(registry):
    assert all(not s["automation_enabled"] for s in registry["sources"])


def test_closed_source_stays_excluded(registry):
    closed = [s for s in registry["sources"] if s["source_id"] == "it_infojobs_italy_closed"]
    assert closed and closed[0]["activation_gate"] == "excluded"


def test_implemented_adapters_gated(registry):
    gated = [s for s in registry["sources"] if s.get("repo_adapter")]
    assert len(gated) == 11  # 7 expansion adapters + 2 ATS + eures + indeed
    for s in gated:
        assert s["activation_gate"] in ("probe_then_owner_approval", "active_existing")


def test_validator_rejects_bad_scope(registry):
    bad = copy.deepcopy(registry)
    bad["sources"][0]["country_scope"] = "GB"
    with pytest.raises(RegistryError):
        validate_registry(bad)


def test_validator_rejects_enabled_source(registry):
    bad = copy.deepcopy(registry)
    bad["sources"][0]["automation_enabled"] = True
    with pytest.raises(RegistryError):
        validate_registry(bad)


def test_validator_rejects_duplicate_ids(registry):
    bad = copy.deepcopy(registry)
    bad["sources"][1]["source_id"] = bad["sources"][0]["source_id"]
    with pytest.raises(RegistryError):
        validate_registry(bad)


def test_coverage_summary_counts(registry):
    summary = coverage_summary(registry)
    assert summary["total"] == 143  # includes nl_otto_workforce
    assert set(summary["by_gate"]) <= ACTIVATION_GATES
    assert summary["by_gate"].get("excluded") == 1

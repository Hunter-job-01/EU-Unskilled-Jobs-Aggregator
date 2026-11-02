"""Loader and validator for config/source_registry.json
(source-expansion Phase 2).

The registry is a researched source CATALOGUE with evidence - not
deployed configuration. Every entry keeps the research fields untouched;
repository integration facts live in added fields (countries, languages,
connector_type, repo_adapter, activation_gate, last_probe,
last_successful_run). No credentials are ever stored here.

Hard invariants enforced on load (violations raise RegistryError):
* unique source_id values
* valid http(s) URLs
* country_scope is one EU27 code or EU_FILTER
* exactly the 27-member EU allowlist
* automation_enabled is false for every entry (activation happens only
  through config.yaml with explicit owner approval)
* implemented adapters are only referenced by entries whose
  activation_gate allows a future probe
"""
from __future__ import annotations

import json
import os

from .eu_location import EU27

REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "source_registry.json")

KNOWN_CATEGORIES = frozenset({
    "official", "official_data", "official_sector", "official_gateway",
    "official_pan_europe", "general", "sector", "classifieds",
    "classifieds_sector", "aggregator", "agency", "ats_route", "employer",
    "remote_low_fit", "excluded_closed",
})

# adapter module name -> activation gate that permits probing it
IMPLEMENTED_ADAPTERS = frozenset({
    "arbeitnow", "sweden_jobsearch", "czech_mpsv", "latvia_nva", "ats_public",
    "ottoworkforce", "anyworkanywhere", "pl_cbop", "nl_jobs",
})

ACTIVATION_GATES = frozenset({
    "active_existing",          # already running in the deployed pipeline
    "probe_then_owner_approval",  # documented free route; needs live probe + approval
    "terms_review",             # manual/terms review before any connector
    "permission_or_onboarding", # operator permission, key or onboarding required
    "manual_only",              # no supported retrieval route established
    "excluded",                 # closed or explicitly excluded
})


class RegistryError(ValueError):
    """The source registry failed validation."""


def load_registry(path: str = None) -> dict:
    path = path or REGISTRY_PATH
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    validate_registry(data)
    return data


def validate_registry(data: dict) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise RegistryError("registry must be an object with a 'sources' list")
    allow = data.get("eu27_allowlist")
    if sorted(allow or []) != sorted(EU27):
        raise RegistryError("eu27_allowlist must be exactly the 27 EU member states")
    seen = set()
    for entry in data["sources"]:
        sid = entry.get("source_id")
        if not sid or sid in seen:
            raise RegistryError("duplicate or missing source_id: {!r}".format(sid))
        seen.add(sid)
        url = entry.get("url") or ""
        if not url.startswith(("http://", "https://")):
            raise RegistryError("{}: invalid url {!r}".format(sid, url))
        for ev_url in entry.get("evidence_urls") or []:
            if not str(ev_url).startswith(("http://", "https://")):
                raise RegistryError("{}: invalid evidence url {!r}".format(sid, ev_url))
        scope = entry.get("country_scope")
        if scope != "EU_FILTER" and scope not in EU27:
            raise RegistryError("{}: invalid country_scope {!r}".format(sid, scope))
        if entry.get("category") not in KNOWN_CATEGORIES:
            raise RegistryError("{}: unknown category {!r}".format(sid, entry.get("category")))
        if entry.get("automation_enabled"):
            raise RegistryError("{}: automation_enabled must stay false in the catalogue".format(sid))
        for code in entry.get("countries") or []:
            if code not in EU27:
                raise RegistryError("{}: countries has non-EU code {!r}".format(sid, code))
        gate = entry.get("activation_gate")
        if gate not in ACTIVATION_GATES:
            raise RegistryError("{}: unknown activation_gate {!r}".format(sid, gate))
        adapter = entry.get("repo_adapter")
        if adapter and adapter not in IMPLEMENTED_ADAPTERS | {"eures", "jobspy_indeed"}:
            raise RegistryError("{}: unknown repo_adapter {!r}".format(sid, adapter))
        if adapter in IMPLEMENTED_ADAPTERS and gate != "probe_then_owner_approval":
            raise RegistryError(
                "{}: implemented adapter requires gate probe_then_owner_approval".format(sid))
        if "key" in json.dumps(entry.get("credentials", {})).lower():
            raise RegistryError("{}: credentials must never be stored here".format(sid))


def coverage_summary(registry: dict) -> dict:
    """Catalogue counts by state. Names, not live coverage claims."""
    summary = {"total": 0, "by_gate": {}, "by_category": {}, "by_country": {}}
    for entry in registry["sources"]:
        summary["total"] += 1
        gate = entry.get("activation_gate") or "unknown"
        summary["by_gate"][gate] = summary["by_gate"].get(gate, 0) + 1
        cat = entry.get("category") or "unknown"
        summary["by_category"][cat] = summary["by_category"].get(cat, 0) + 1
        for code in entry.get("countries") or []:
            summary["by_country"][code] = summary["by_country"].get(code, 0) + 1
    return summary

"""Source-level applicant-eligibility labelling from the registry.

Distinct from keyword signals: this records what a SOURCE is known to do
for an applicant residing outside the EU (the owner resides in Egypt),
based on registry evidence - never inferred from scraping success.

Labels:
- restricted_*  -> job is excluded with an honest reason (operator stated
  it does not currently serve residents outside the EU)
- unknown/not_assessed -> job keeps its score but carries the signal
  "source_eligibility_unknown"; no points change, no permit/sponsor claim
- anything else / unlisted source -> no label added

A technical adapter being live NEVER implies eligibility.
"""
from __future__ import annotations

RESTRICTED_PREFIX = "restricted"
UNKNOWN_VALUES = {"unknown_not_assessed", "not_assessed", "unknown"}


def source_eligibility(registry: dict, source_id: str) -> str:
    """Return the registry's applicant_from_egypt value for a source, or
    "" when the source is not listed."""
    for entry in (registry or {}).get("sources", []):
        if entry.get("source_id") == source_id:
            return str(entry.get("applicant_from_egypt") or "")
    return ""


def apply_to_job(job, registry: dict) -> None:
    """Mutate job in place: exclude restricted, label unknown."""
    value = source_eligibility(registry, getattr(job, "source", ""))
    if not value:
        return
    low = value.lower()
    if low.startswith(RESTRICTED_PREFIX):
        job.excluded = True
        job.exclusion_reason = (
            "source currently does not serve applicants residing outside "
            "the EU (operator-stated, recorded in registry)")
        if "source_eligibility_restricted" not in job.signals:
            job.signals.append("source_eligibility_restricted")
    elif low in UNKNOWN_VALUES:
        if "source_eligibility_unknown" not in job.signals:
            job.signals.append("source_eligibility_unknown")

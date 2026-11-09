"""One-time uid re-key migration for the non-Latin dedup collision.

Background (2026-11-09): dedup_hash's ASCII-only normalization stripped
non-Latin titles (Cyrillic, Greek, ...) to an empty key, so every
pure-non-Latin-titled posting from one company+country collapsed into a
single record that flip-flopped identity across runs
(319357318bee1b6d: t.me/polandworkportal/32236 Poznan -> 32239 Zielona
Gora, with a third posting's body mixed in). The scorer fix gives those
titles a Unicode fallback; this module re-keys the EXISTING
collision-bucket records to their corrected uids so future re-scrapes
merge into them instead of creating duplicate records, and carries
digest delivery marks so nothing is redelivered or double-counted.

The committed spec file (data/uid_migration_2026-11-09.json) is the
explicit old -> new provenance record. Because a collision bucket can
flip identity BETWEEN the audit snapshot and a later load, the spec
carries the audited identity (title, company, country, source_id, url)
and apply_to_store re-keys ONLY when the record currently under the old
uid still matches that identity exactly; a changed bucket is HELD (left
in place, logged, counted) rather than assigned an audited uid that now
belongs to a different posting.

Application is idempotent and conflict-safe; it never creates or
deletes postings, only re-keys.
"""

import json
import logging
import os

log = logging.getLogger(__name__)

SPEC_NAME = "uid_migration_2026-11-09.json"

IDENTITY_FIELDS = ("title", "company", "country", "source_id", "url")

# Fields derived from a specific posting's content. A flip-flopped bucket
# may have composed them for a DIFFERENT posting than the one it holds
# now, so they are cleared on re-key and recomposed by the normal
# pipeline passes (summary: translation pass at score >= min_score;
# titles: argos/gemini title passes).
DERIVED_FIELDS_CLEARED = ("summary_ar", "title_en", "title_ar")


def load_spec(data_dir: str) -> dict:
    """Return {old_uid: spec_entry} from the committed spec, or {} when
    no spec exists. Entries without a valid new_uid are rejected."""
    path = os.path.join(data_dir, SPEC_NAME)
    try:
        with open(path, encoding="utf-8") as fh:
            spec = json.load(fh)
    except FileNotFoundError:
        return {}
    except (ValueError, OSError) as exc:
        log.error("uid migration spec unreadable (%s); skipped", exc)
        return {}
    migrations = spec.get("migrations") or {}
    out = {}
    for old, entry in migrations.items():
        if not isinstance(entry, dict):
            log.error("uid migration spec entry invalid (%r); skipped", old)
            continue
        new = entry.get("new_uid")
        if not old or not new or old == new:
            log.error("uid migration spec entry invalid (%r -> %r); skipped",
                      old, new)
            continue
        out[old] = entry
    return out


def _entry_new_uid(entry):
    """Spec entries are dicts; a plain string is accepted for callers
    that only hold the bare mapping (no identity validation possible)."""
    return entry.get("new_uid") if isinstance(entry, dict) else entry


def _identity_matches(rec: dict, entry: dict) -> bool:
    """True when the live record still IS the audited posting. Only
    fields present in the spec entry are compared."""
    for field in IDENTITY_FIELDS:
        if field in entry and (rec.get(field) or "") != (entry.get(field) or ""):
            return False
    return True


def apply_to_store(store, spec: dict) -> dict:
    """Re-key collision-bucket records in a loaded JobStore. Idempotent.

    With a full spec entry the record's current identity must match the
    audited title/company/country/source_id/url; a bucket that flipped
    after the audit is HELD (not re-keyed) so the audited new uid is
    never assigned to a different posting.

    summary_ar/title_en/title_ar are cleared on every re-keyed record:
    they are content-derived and a bucket may have flip-flopped before
    the identity guard existed, so carried derivations cannot be trusted
    to match the current posting. Eligible records recompose
    deterministically on the next pipeline passes.
    """
    counts = {"migrated": 0, "absent": 0, "conflict": 0, "held": 0,
              "summary_cleared": 0, "tombstones_migrated": 0}
    records = getattr(store, "_store", None) or {}
    tombstones = getattr(store, "_tombstones", None) or {}
    for old, entry in (spec or {}).items():
        new = _entry_new_uid(entry)
        if not new:
            continue
        rec = records.get(old)
        if rec is None:
            counts["absent"] += 1
            continue
        if isinstance(entry, dict) and not _identity_matches(rec, entry):
            counts["held"] += 1
            log.warning("uid migration %s HELD: record identity changed "
                        "since audit (bucket flip); not assigning audited "
                        "uid %s to a different posting", old, new)
            continue
        if new in records:
            counts["conflict"] += 1
            log.warning("uid migration %s -> %s skipped: new uid already "
                        "exists; bucket left in place", old, new)
            continue
        records[new] = records.pop(old)
        for field in DERIVED_FIELDS_CLEARED:
            if records[new].get(field):
                records[new][field] = ""
                if field == "summary_ar":
                    counts["summary_cleared"] += 1
        counts["migrated"] += 1
        if old in tombstones:
            tombstones[new] = tombstones.pop(old)
            counts["tombstones_migrated"] += 1
    if counts["migrated"] or counts["held"]:
        log.info("uid migration: %s", counts)
    return counts


def _carry_marks(marks: dict, mapping: dict) -> int:
    moved = 0
    for old, new in mapping.items():
        if old in marks and new not in marks:
            marks[new] = marks[old]
            moved += 1
    return moved


def apply_to_notifier_state(state: dict, spec: dict):
    """Carry uid-keyed digest state old -> new. Idempotent, never
    clobbers an existing new-uid value.

    Covers every uid-keyed shape in the notifier state: top-level
    notified_uids and failures, and the same two maps inside each
    destinations.<name> entry. last_digest_utc is date-keyed and needs
    no carry. Delivery days are preserved exactly so a re-keyed posting
    is neither redelivered nor double-counted, and failure counts keep
    their retry-suppression meaning.

    Runs on every state load, so no separate persistence is needed: the
    carried marks ride the normal mark_notified write-back, whose
    active-uid filter keeps the (now active) new uid and drops the old.
    If no mark_notified happens, the next load simply re-carries from
    the still-persisted old marks - no loss.
    """
    mapping = {old: _entry_new_uid(entry) for old, entry in (spec or {}).items()}
    mapping = {old: new for old, new in mapping.items() if new}
    if not isinstance(state, dict) or not mapping:
        return state, 0
    moved = _carry_marks(state.setdefault("notified_uids", {}), mapping)
    failures = state.get("failures")
    if isinstance(failures, dict):
        moved += _carry_marks(failures, mapping)
    destinations = state.get("destinations")
    if isinstance(destinations, dict):
        for dest in destinations.values():
            if isinstance(dest, dict):
                moved += _carry_marks(dest.setdefault("notified_uids", {}), mapping)
                dfail = dest.get("failures")
                if isinstance(dfail, dict):
                    moved += _carry_marks(dfail, mapping)
    return state, moved

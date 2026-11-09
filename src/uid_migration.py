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
explicit old -> new provenance record. Application is idempotent and
conflict-safe; it never creates or deletes postings, only re-keys.
"""

import json
import logging
import os

log = logging.getLogger(__name__)

SPEC_NAME = "uid_migration_2026-11-09.json"


def load_spec(data_dir: str) -> dict:
    """Return the {old_uid: new_uid} mapping, or {} when no spec exists."""
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
        new = (entry or {}).get("new_uid") if isinstance(entry, dict) else entry
        if not old or not new or old == new:
            log.error("uid migration spec entry invalid (%r -> %r); skipped",
                      old, new)
            continue
        out[old] = new
    return out


def apply_to_store(store, mapping: dict) -> dict:
    """Re-key collision-bucket records in a loaded JobStore. Idempotent.

    summary_ar is cleared on every re-keyed record: summaries are
    body-derived and a bucket may have flip-flopped before the identity
    guard existed, so a carried summary cannot be trusted to match the
    current body. Eligible records (score >= translation.min_score)
    recompose deterministically on the next translation pass.
    """
    counts = {"migrated": 0, "absent": 0, "conflict": 0,
              "summary_cleared": 0, "tombstones_migrated": 0}
    if not mapping:
        return counts
    records = getattr(store, "_store", None)
    if records is None:
        records = store.load()
    for old, new in mapping.items():
        rec = records.get(old)
        if rec is None:
            counts["absent"] += 1  # already migrated or pruned: no-op
            continue
        if new in records:
            # Never overwrite an existing posting: keep both, flag loudly.
            counts["conflict"] += 1
            log.error("uid migration conflict: %s -> %s already exists; "
                      "record NOT moved", old, new)
            continue
        if (rec.get("summary_ar") or "").strip():
            rec["summary_ar"] = ""
            counts["summary_cleared"] += 1
        records[new] = rec
        del records[old]
        counts["migrated"] += 1
    # Tombstones travel too: a pruned bucket uid must not re-ingest under
    # its old key, and its prune history belongs to the new key.
    tomb = getattr(store, "_tombstones", None) or {}
    for old, new in mapping.items():
        if old in tomb and new not in tomb:
            tomb[new] = tomb.pop(old)
            counts["tombstones_migrated"] += 1
    if counts["migrated"] or counts["tombstones_migrated"]:
        log.info("uid migration applied: %s", counts)
    return counts


def apply_to_notifier_state(state: dict, mapping: dict):
    """Carry digest delivery marks old -> new so re-keyed postings are
    neither redelivered nor double-counted. Returns (state, moved).

    Applied on every notifier state load (idempotent), so correctness
    does not depend on persistence timing; normal mark_notified
    persistence carries the marks forward on the next send.
    """
    moved = 0
    if not mapping or not isinstance(state, dict):
        return state, moved

    def _mv(sent):
        nonlocal moved
        for old, new in mapping.items():
            if old in sent and new not in sent:
                sent[new] = sent[old]
                moved += 1

    sent = state.get("notified_uids")
    if isinstance(sent, dict):
        _mv(sent)
    dests = state.get("destinations")
    if isinstance(dests, dict):
        for dest in dests.values():
            dsent = (dest or {}).get("notified_uids")
            if isinstance(dsent, dict):
                _mv(dsent)
    return state, moved

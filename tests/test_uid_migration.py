"""v6: non-Latin dedup collision fix + one-time uid re-key migration."""
import json
from datetime import date

from src.models import Job
from src.scorer import dedup_hash
from src.storage import JobStore
from src.uid_migration import (apply_to_notifier_state, apply_to_store,
                               load_spec)


def store_cfg(tmp_path):
    return {
        "data_dir": "data",
        "active_jobs_file": "active_jobs.json",
        "export_csv": "latest_export.csv",
        "run_status_file": "run_status.json",
        "retention_days": 30,
    }


# ---------------------------------------------------------------------
# dedup_hash Unicode fallback
# ---------------------------------------------------------------------

def test_dedup_distinct_cyrillic_postings_get_distinct_uids():
    a = dedup_hash("Poland Work Portal", "❤️Зелена Гура, Сулехов♥️", "PL")
    b = dedup_hash("Poland Work Portal", "😍Познань и Тарнуво Подгурне😍", "PL")
    assert a != b


def test_dedup_same_cyrillic_posting_stable_across_runs():
    a = dedup_hash("Poland Work Portal", "❤️Зелена Гура, Сулехов♥️", "PL")
    b = dedup_hash("Poland Work Portal", "❤️Зелена Гура, Сулехов♥️", "PL")
    assert a == b


def test_dedup_distinct_greek_postings_get_distinct_uids():
    a = dedup_hash("Atrium hotels", "Εργάτης Αποθήκης", "GR")
    b = dedup_hash("Atrium hotels", "Υπάλληλος Υποδοχής", "GR")
    assert a != b


def test_dedup_latin_titles_unchanged_no_uid_churn():
    # The fallback must NOT change any uid that the ASCII rule already
    # distinguishes: pure Latin and mixed Latin/Cyrillic titles keep
    # their historical hash exactly.
    assert dedup_hash("ACME", "Warehouse worker", "PL") == \
        dedup_hash("ACME", "Warehouse  worker", "PL")
    mixed = dedup_hash("Poland Work Portal", "Познань ❤️Склад одягу Swarzędz", "PL")
    # ASCII rule keeps only the Latin part: same as a Latin-only title
    # folding to the same key.
    assert mixed == dedup_hash("Poland Work Portal", "Swarzędz", "PL")


def test_dedup_empty_title_stable():
    assert dedup_hash("ACME", "", "PL") == dedup_hash("ACME", "", "PL")


# ---------------------------------------------------------------------
# store re-key migration
# ---------------------------------------------------------------------

def _bucket_store(tmp_path, title="❤️Зелена Гура, Сулехов♥️", summary="ملخص"):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    job = Job(title=title, company="Poland Work Portal", country="PL",
              source="telegram_polandworkportal",
              url="https://t.me/polandworkportal/32239",
              source_id="polandworkportal/32239",
              uid=dedup_hash("Poland Work Portal", title, "PL"), score=50)
    store.merge([job], today=date(2026, 11, 9))
    # Simulate the pre-fix state: the record lives under the collision
    # bucket uid (empty ASCII norm) with a carried summary.
    new_uid = job.uid
    old_uid = "319357318bee1b6d"
    store._store[old_uid] = store._store.pop(new_uid)
    store._store[old_uid]["summary_ar"] = summary
    return store, old_uid, new_uid


def test_migration_rekeys_and_preserves_record(tmp_path):
    store, old_uid, new_uid = _bucket_store(tmp_path)
    counts = apply_to_store(store, {old_uid: new_uid})
    assert counts["migrated"] == 1
    assert old_uid not in store._store
    rec = store._store[new_uid]
    assert rec["url"] == "https://t.me/polandworkportal/32239"
    assert rec["source_id"] == "polandworkportal/32239"
    assert rec["score"] == 50


def test_migration_cleans_possibly_stale_summary(tmp_path):
    store, old_uid, new_uid = _bucket_store(tmp_path)
    counts = apply_to_store(store, {old_uid: new_uid})
    assert counts["summary_cleared"] == 1
    assert store._store[new_uid]["summary_ar"] == ""


def test_migration_idempotent_when_already_applied(tmp_path):
    store, old_uid, new_uid = _bucket_store(tmp_path)
    apply_to_store(store, {old_uid: new_uid})
    counts = apply_to_store(store, {old_uid: new_uid})
    assert counts["migrated"] == 0 and counts["absent"] == 1
    assert new_uid in store._store


def test_migration_never_overwrites_existing_new_uid(tmp_path):
    store, old_uid, new_uid = _bucket_store(tmp_path)
    occupant = Job(title="❤️Зелена Гура, Сулехов♥️", company="Poland Work Portal",
                   country="PL", source="telegram_polandworkportal",
                   url="https://t.me/polandworkportal/32239",
                   source_id="polandworkportal/32239", uid=new_uid, score=55)
    store.merge([occupant], today=date(2026, 11, 9))
    store._store[new_uid]["score"] = 55
    counts = apply_to_store(store, {old_uid: new_uid})
    assert counts["conflict"] == 1 and counts["migrated"] == 0
    assert store._store[new_uid]["score"] == 55  # existing record untouched
    assert old_uid in store._store  # bucket left in place for review


def test_migration_moves_tombstones(tmp_path):
    store, old_uid, new_uid = _bucket_store(tmp_path)
    store._tombstones[old_uid] = "2026-11-01"
    counts = apply_to_store(store, {old_uid: new_uid})
    assert counts["tombstones_migrated"] == 1
    assert store._tombstones.get(new_uid) == "2026-11-01"
    assert old_uid not in store._tombstones


# ---------------------------------------------------------------------
# notifier delivery-mark carry
# ---------------------------------------------------------------------

def test_notifier_marks_carry_without_replay_or_loss():
    state = {"notified_uids": {"319357318bee1b6d": "2026-11-06",
                               "unrelated0000000": "2026-11-05"},
             "destinations": {"main": {"notified_uids": {"319357318bee1b6d": "2026-11-06"}}}}
    mapping = {"319357318bee1b6d": "ef9d9dd6d05f6910"}
    state, moved = apply_to_notifier_state(state, mapping)
    assert moved == 2
    # same delivery day preserved: no replay, no fresh-digest eligibility
    assert state["notified_uids"]["ef9d9dd6d05f6910"] == "2026-11-06"
    assert state["notified_uids"]["319357318bee1b6d"] == "2026-11-06"
    assert state["notified_uids"]["unrelated0000000"] == "2026-11-05"
    assert state["destinations"]["main"]["notified_uids"]["ef9d9dd6d05f6910"] == "2026-11-06"
    # idempotent
    state, moved2 = apply_to_notifier_state(state, mapping)
    assert moved2 == 0


def test_notifier_carry_never_clobbers_existing_mark():
    state = {"notified_uids": {"old0000000000000": "2026-11-01",
                               "new0000000000000": "2026-11-08"}}
    state, moved = apply_to_notifier_state(state, {"old0000000000000": "new0000000000000"})
    assert moved == 0
    assert state["notified_uids"]["new0000000000000"] == "2026-11-08"


# ---------------------------------------------------------------------
# spec loading
# ---------------------------------------------------------------------

def test_load_spec_missing_file_is_empty(tmp_path):
    assert load_spec(str(tmp_path)) == {}


def test_load_spec_rejects_invalid_entries(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "uid_migration_2026-11-09.json").write_text(json.dumps({
        "migrations": {"good00000000000": {"new_uid": "new0000000000000"},
                       "self000000000000": {"new_uid": "self000000000000"},
                       "bad": {"no_new": True}}}))
    spec = load_spec(str(tmp_path / "data"))
    assert spec == {"good00000000000": "new0000000000000"}

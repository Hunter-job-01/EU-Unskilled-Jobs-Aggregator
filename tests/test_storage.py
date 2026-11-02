import json
from datetime import date, timedelta

from src.models import Job
from src.storage import JobStore


def store_cfg(tmp_path):
    return {
        "data_dir": "data",
        "active_jobs_file": "active_jobs.json",
        "export_csv": "latest_export.csv",
        "run_status_file": "run_status.json",
        "retention_days": 30,
    }


def make_job(uid_seed, **kw):
    base = dict(title=f"Magazynier {uid_seed}", company="ACME", country="PL",
                source="eures", url=f"https://example.test/{uid_seed}",
                uid=f"uid{uid_seed}", score=70)
    base.update(kw)
    return Job(**base)


def test_merge_dedup_and_seen_dates(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    today = date(2026, 11, 2)
    job = make_job(1)
    added, updated = store.merge([job], today=today)
    assert (added, updated) == (1, 0)
    job.score = 80
    added, updated = store.merge([job], today=today + timedelta(days=2))
    assert (added, updated) == (0, 1)
    rec = store._store["uid1"]
    assert rec["first_seen"] == "2026-11-02"
    assert rec["last_seen"] == "2026-11-04"
    assert rec["score"] == 80


def test_prune_removes_old_postings(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    today = date(2026, 11, 2)
    old = make_job(1, posted_date="2026-09-01", first_seen="2026-09-01")
    fresh = make_job(2, posted_date="2026-10-30")
    store.merge([old, fresh], today=today)
    removed = store.prune(today=today)
    assert removed == 1
    assert "uid2" in store._store and "uid1" not in store._store


def test_save_and_csv_export(tmp_path):
    tiers = {1: ["PL"], 2: ["DE"], 3: ["SE"]}
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    store.merge([make_job(1, score=90), make_job(2, score=40)])
    assert store.save() == 2
    data = json.loads((tmp_path / "data" / "active_jobs.json").read_text())
    assert len(data) == 2
    assert store.export_csv(tiers) == 2
    lines = (tmp_path / "data" / "latest_export.csv").read_text().splitlines()
    assert lines[0].startswith("uid,score")
    assert "90" in lines[1]  # highest score first


def test_run_status_written(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    store.write_run_status({"last_run_utc": "2026-11-02T00:00:00+00:00", "totals": {}})
    payload = json.loads((tmp_path / "data" / "run_status.json").read_text())
    assert payload["last_run_utc"].startswith("2026-11-02")


# ---------------------------------------------------------------------
# Fix 1: prune / re-ingestion loop - tombstone registry
# ---------------------------------------------------------------------

def test_pruned_jobs_not_reingested_on_later_merge(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    day1 = date(2026, 9, 1)
    old = make_job(1, posted_date="2026-09-01")
    store.merge([old], today=day1)
    store.save()

    # 40 days later the posting is past retention and gets pruned...
    day40 = day1 + timedelta(days=40)
    store2 = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    assert store2.prune(today=day40) == 1
    store2.save()
    tombstones = json.loads((tmp_path / "data" / "pruned_uids.json").read_text())
    assert "uid1" in tombstones

    # ...but it is still live on the source and scraped again on day 41:
    # merge must NOT re-add it as a brand-new job.
    rescraped = make_job(1, posted_date="2026-09-01")
    added, updated = store2.merge([rescraped], today=day40 + timedelta(days=1))
    assert (added, updated) == (0, 0)
    assert "uid1" not in store2._store

    # A genuinely new job in the same batch is unaffected.
    added, updated = store2.merge([make_job(2)], today=day40 + timedelta(days=1))
    assert (added, updated) == (1, 0)
    assert "uid2" in store2._store


def test_tombstone_registry_is_capped_and_expires(tmp_path):
    cfgd = store_cfg(tmp_path)
    cfgd["tombstone_max_uids"] = 2
    cfgd["tombstone_days"] = 60
    store = JobStore(cfgd, base_dir=str(tmp_path))
    today = date(2026, 11, 2)
    jobs = [make_job(i, posted_date="2026-08-01") for i in range(4)]
    store.merge(jobs, today=today)
    assert store.prune(today=today) == 4
    assert len(store._tombstones) == 2  # capped at tombstone_max_uids

    # Entries older than tombstone_days are expired on the next prune.
    later = today + timedelta(days=61)
    store.prune(today=later)
    assert store._tombstones  # uid9's prune below keeps the file non-empty
    store.merge([make_job(9, posted_date="2026-08-01")], today=later)
    store.prune(today=later)
    assert all(date.fromisoformat(d) >= later - timedelta(days=61)
               for d in store._tombstones.values())


# ---------------------------------------------------------------------
# Fix 3: Telegram notification state tracking
# ---------------------------------------------------------------------

def test_notification_state_preserved_across_merges(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    today = date(2026, 11, 2)
    store.merge([make_job(1, score=90), make_job(2, score=50)], today=today)
    assert store.mark_notified(["uid1"]) == 1
    rec = store._store["uid1"]
    assert rec["notified_telegram"] is True
    assert rec["notified_at"]

    # The same job is re-scraped in a later run: the fresh Job object
    # carries notified_telegram=False, but merge must keep the state.
    added, updated = store.merge([make_job(1, score=95)], today=today + timedelta(days=1))
    assert (added, updated) == (0, 1)
    rec = store._store["uid1"]
    assert rec["notified_telegram"] is True
    assert rec["notified_at"]
    assert rec["score"] == 95
    assert rec["last_seen"] == "2026-11-03"


def test_get_unnotified_top_jobs_filters_and_orders(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    store.merge([make_job(1, score=90), make_job(2, score=50),
                 make_job(3, score=30), make_job(4, score=99, excluded=True)])
    store.mark_notified(["uid2"])

    top = store.get_unnotified_top_jobs(min_score=40, limit=15)
    assert [r["uid"] for r in top] == ["uid1"]  # uid2 notified, uid3 below min, uid4 excluded

    limited = store.get_unnotified_top_jobs(min_score=0, limit=2)
    assert [r["uid"] for r in limited] == ["uid1", "uid3"]  # score order, limit honored


def test_mark_notified_persists_to_disk(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    store.merge([make_job(1)], today=date(2026, 11, 2))
    assert store.mark_notified(["uid1", "unknown-uid"]) == 1
    fresh = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    rec = fresh.load()["uid1"]
    assert rec["notified_telegram"] is True
    assert rec["notified_at"]


def test_status_keeps_latest_country_health_across_tiers(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    store.write_run_status({"last_run_utc": "2026-11-02T10:00:00Z", "countries": {"PL": {"eures": {"status": "ok"}}}})
    store.write_run_status({"last_run_utc": "2026-11-02T11:00:00Z", "countries": {"ES": {"eures": {"status": "partial"}}}})
    status = json.loads(open(store.status_path).read())
    assert set(status["countries"]) == {"ES"}
    assert set(status["latest_by_country"]) == {"PL", "ES"}
    assert status["latest_by_country"]["PL"]["checked_at_utc"] == "2026-11-02T10:00:00Z"

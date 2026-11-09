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


def test_translation_fields_preserved_across_merges(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    today = date(2026, 11, 2)
    store.merge([make_job(1, score=90)], today=today)
    rec = store._store["uid1"]
    rec["title_en"] = "Warehouse worker"
    rec["title_ar"] = "عامل مستودع"
    rec["summary_ar"] = "ملخص"

    # Later run re-scrapes the same job without re-translating (quota
    # stop / throttle / missing key): the fresh record must not blank
    # the stored translations.
    added, updated = store.merge([make_job(1, score=91)], today=today + timedelta(days=1))
    assert (added, updated) == (0, 1)
    rec = store._store["uid1"]
    assert rec["title_en"] == "Warehouse worker"
    assert rec["title_ar"] == "عامل مستودع"
    assert rec["summary_ar"] == "ملخص"


def test_translation_fields_dropped_when_source_title_changes(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    today = date(2026, 11, 2)
    store.merge([make_job(1, score=90)], today=today)
    rec = store._store["uid1"]
    rec["title_en"] = "Warehouse worker"
    rec["title_ar"] = "عامل مستودع"

    changed = make_job(1, score=91)
    changed.title = changed.title + " (m/w/d)"
    added, updated = store.merge([changed], today=today + timedelta(days=1))
    assert (added, updated) == (0, 1)
    rec = store._store["uid1"]
    assert rec["title_en"] == ""
    assert rec["title_ar"] == ""


def test_source_mismatch_review_tracks_exact_content(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    job = make_job(1, description="wrong content")
    store.merge([job])
    flag = {"status": "destination_mismatch", "checked_on": "2026-11-04"}
    store._store[job.uid]["source_review"] = flag
    store.merge([job])
    assert store._store[job.uid]["source_review"] == flag
    job.description = "new source evidence content"
    store.merge([job])
    assert store._store[job.uid]["source_review"] == {}
    assert store._store[job.uid]["description"] == job.description
# ---------------------------------------------------------------------
# Fix 4: summary_ar must not survive posting identity drift
# ---------------------------------------------------------------------

def test_summary_ar_dropped_on_identity_drift_same_title(tmp_path):
    # Repost drift: same title, same uid, but a different URL + body
    # (the Primark JR-16151 -> JR-16177 collapse). Title-derived
    # translations stay valid; the body-derived summary must not cross.
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    today = date(2026, 11, 2)
    store.merge([make_job(1, score=90, description="Rio part-time body")], today=today)
    rec = store._store["uid1"]
    rec["title_en"] = "Warehouse worker"
    rec["title_ar"] = "عامل مستودع"
    rec["summary_ar"] = "ملخص قديم"

    drifted = make_job(1, score=91, description="Montijo full-time body")
    drifted.url = "https://example.test/1-reposted"
    added, updated = store.merge([drifted], today=today + timedelta(days=1))
    assert (added, updated) == (0, 1)
    rec = store._store["uid1"]
    assert rec["title_en"] == "Warehouse worker"
    assert rec["title_ar"] == "عامل مستودع"
    assert rec["summary_ar"] == ""


def test_summary_ar_dropped_when_only_body_changes(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    today = date(2026, 11, 2)
    store.merge([make_job(1, description="old body")], today=today)
    store._store["uid1"]["summary_ar"] = "ملخص قديم"
    store.merge([make_job(1, description="edited body")], today=today + timedelta(days=1))
    assert store._store["uid1"]["summary_ar"] == ""


def test_summary_ar_preserved_when_identity_unchanged(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    today = date(2026, 11, 2)
    store.merge([make_job(1, description="same body")], today=today)
    store._store["uid1"]["summary_ar"] = "ملخص"
    store.merge([make_job(1, description="same body")], today=today + timedelta(days=1))
    assert store._store["uid1"]["summary_ar"] == "ملخص"


def test_summary_guard_preventive_only_for_already_drifted_record(tmp_path):
    # Exact 86f repro: the stored record ALREADY carries the current
    # posting identity (url/body/source/source_id) next to a stale
    # summary derived from the pre-drift body. A same-identity refresh
    # preserves the field - the guard prevents FUTURE drift carry-over
    # only; cleaning the existing-stale state is the reviewed fill's job.
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    today = date(2026, 11, 2)
    job = make_job(1, description="current Montijo body")
    store.merge([job], today=today)
    rec = store._store["uid1"]
    rec["title_en"] = "Warehouse worker"
    rec["summary_ar"] = "ملخص قديم من وصف Rio"
    rec["source_review"] = {}

    added, updated = store.merge(
        [make_job(1, score=91, description="current Montijo body")],
        today=today + timedelta(days=1))
    assert (added, updated) == (0, 1)
    rec = store._store["uid1"]
    assert rec["summary_ar"] == "ملخص قديم من وصف Rio"  # fill cleanup owns this
    assert rec["title_en"] == "Warehouse worker"


def test_description_keep_bound_to_unchanged_url(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    today = date(2026, 11, 2)
    store.merge([make_job(1, description="a much longer original body text")],
                today=today)
    # Same posting (url unchanged), thinner re-scrape: keep the rich body.
    store.merge([make_job(1, description="thin")], today=today + timedelta(days=1))
    assert store._store["uid1"]["description"] == "a much longer original body text"
    # Identity drift (url changed): the old body belongs to a different
    # posting and must NOT be kept even though it is longer.
    drifted = make_job(1, description="short new body")
    drifted.url = "https://example.test/1-reposted"
    store.merge([drifted], today=today + timedelta(days=2))
    assert store._store["uid1"]["description"] == "short new body"


# ---------------------------------------------------------------------
# v6 bfb01e57: verified-current body vs keep-longer reconciliation
# ---------------------------------------------------------------------

def test_verified_current_shorter_body_replaces_stale_longer(tmp_path):
    # bfb01e57 repro: same posting (url/source/source_id unchanged), the
    # source stopped serving the old marketing head. A corroborated
    # fresh body is source-faithful and wins over the longer stored
    # body; the stale body-derived summary drops with it.
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    today = date(2026, 11, 2)
    long_body = "Tule meille toihin! " + "x" * 200
    store.merge([make_job(1, description=long_body)], today=today)
    rec = store._store["uid1"]
    rec["title_en"] = "Warehouse worker"
    rec["title_ar"] = "عامل مستودع"
    rec["summary_ar"] = "ملخص من النص القديم"

    fresh = make_job(1, score=43, description="Haetaan varastotyontekijaa.",
                     body_verified_current=True)
    added, updated = store.merge([fresh], today=today + timedelta(days=1))
    assert (added, updated) == (0, 1)
    rec = store._store["uid1"]
    assert rec["description"] == "Haetaan varastotyontekijaa."
    assert rec["summary_ar"] == ""
    # Title-derived translations are unaffected by the body change.
    assert rec["title_en"] == "Warehouse worker"
    assert rec["title_ar"] == "عامل مستودع"
    # Same posting: no source_review flag is raised by the body refresh.
    assert rec["source_review"] == {}
    # The corroboration flag is transient scrape metadata, never stored.
    assert "body_verified_current" not in rec


def test_uncorroborated_short_body_keeps_stored_and_withholds_summary(tmp_path):
    # Without corroboration a shorter fresh body cannot be distinguished
    # from a degraded or incomplete fetch: keep the stored body and
    # withhold the summary rather than claim currentness.
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    today = date(2026, 11, 2)
    store.merge([make_job(1, description="a much longer original body text")],
                today=today)
    store._store["uid1"]["summary_ar"] = "ملخص"
    store.merge([make_job(1, description="thin")], today=today + timedelta(days=1))
    rec = store._store["uid1"]
    assert rec["description"] == "a much longer original body text"
    assert rec["summary_ar"] == ""


def test_empty_refetch_keeps_stored_and_withholds_summary(tmp_path):
    # An empty fresh body is the classic incomplete-fetch shape: the
    # stored body must survive and the summary must not cross.
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    today = date(2026, 11, 2)
    store.merge([make_job(1, description="full original body")], today=today)
    store._store["uid1"]["summary_ar"] = "ملخص"
    store.merge([make_job(1, description="")], today=today + timedelta(days=1))
    rec = store._store["uid1"]
    assert rec["description"] == "full original body"
    assert rec["summary_ar"] == ""


def test_verified_current_body_still_drops_summary_on_identity_drift(tmp_path):
    # 86f-class: corroboration attests the body, not the posting. When
    # the record drifts to a different posting (source_id changes), the
    # summary must still drop even with a verified current body.
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    today = date(2026, 11, 2)
    store.merge([make_job(1, description="Rio part-time body", source_id="JR-1")],
                today=today)
    store._store["uid1"]["summary_ar"] = "ملخص قديم"
    drifted = make_job(1, description="Montijo full-time body",
                       source_id="JR-2", body_verified_current=True)
    store.merge([drifted], today=today + timedelta(days=1))
    rec = store._store["uid1"]
    assert rec["description"] == "Montijo full-time body"
    assert rec["summary_ar"] == ""

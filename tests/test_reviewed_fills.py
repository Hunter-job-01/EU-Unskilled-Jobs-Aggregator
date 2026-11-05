"""Reviewed-fills pipeline step: guards, idempotence, fail-safety."""
import json

from src.reviewed_fills import apply_reviewed_fills
from src.storage import JobStore
from tests.test_storage import make_job, store_cfg


def _fill(uid, **kw):
    d = {"uid": uid}
    d.update(kw)
    return d


def _write_fills(tmp_path, fills):
    path = tmp_path / "data" / "reviewed_title_fills.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fills": fills}, ensure_ascii=False),
                    encoding="utf-8")
    return str(path)


def test_fill_applied_and_idempotent(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    job = make_job(1)
    store.merge([job])
    path = _write_fills(tmp_path, [
        _fill(job.uid, expected_title=job.title,
              title_en="General worker", title_ar="عامل عام", translated=True)])
    counts = apply_reviewed_fills(store, path)
    assert counts["applied"] == 1
    rec = store._store[job.uid]
    assert rec["title_en"] == "General worker"
    assert rec["title_ar"] == "عامل عام"
    assert rec["translated"] is True
    counts = apply_reviewed_fills(store, path)
    assert counts["applied"] == 0 and counts["noop"] == 1


def test_title_change_invalidates_fill(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    job = make_job(1)
    store.merge([job])
    path = _write_fills(tmp_path, [
        _fill(job.uid, expected_title="a different title",
              title_en="General worker")])
    counts = apply_reviewed_fills(store, path)
    assert counts["mismatch"] == 1 and counts["applied"] == 0
    assert store._store[job.uid]["title_en"] == ""


def test_url_change_invalidates_source_review(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    job = make_job(1)
    store.merge([job])
    review = {"status": "destination_mismatch", "checked_on": "2026-11-04"}
    path = _write_fills(tmp_path, [
        _fill(job.uid, expected_title=job.title,
              expected_url="https://example.org/other", source_review=review)])
    counts = apply_reviewed_fills(store, path)
    assert counts["mismatch"] == 1
    assert store._store[job.uid].get("source_review") in (None, {})


def test_source_review_applied_on_exact_match(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    job = make_job(1)
    store.merge([job])
    review = {"status": "destination_mismatch", "checked_on": "2026-11-04"}
    path = _write_fills(tmp_path, [
        _fill(job.uid, expected_title=job.title, expected_url=job.url,
              source_review=review)])
    counts = apply_reviewed_fills(store, path)
    assert counts["applied"] == 1
    assert store._store[job.uid]["source_review"] == review


def test_existing_different_value_never_overwritten(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    job = make_job(1)
    job.title_en = "Reviewed vocab gloss"
    store.merge([job])
    path = _write_fills(tmp_path, [
        _fill(job.uid, expected_title=job.title, title_en="Different gloss")])
    counts = apply_reviewed_fills(store, path)
    assert counts["conflict"] == 1
    assert store._store[job.uid]["title_en"] == "Reviewed vocab gloss"


def test_absent_record_and_malformed_file_are_noops(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    store.merge([make_job(1)])
    path = _write_fills(tmp_path, [_fill("no-such-uid", title_en="X")])
    counts = apply_reviewed_fills(store, path)
    assert counts["absent"] == 1 and counts["applied"] == 0
    bad = tmp_path / "data" / "reviewed_title_fills.json"
    bad.write_text("{not json", encoding="utf-8")
    counts = apply_reviewed_fills(store, str(bad))
    assert counts["applied"] == 0
    missing = tmp_path / "data" / "does_not_exist.json"
    assert apply_reviewed_fills(store, str(missing))["applied"] == 0


def test_translated_flag_requires_a_title_value(tmp_path):
    store = JobStore(store_cfg(tmp_path), base_dir=str(tmp_path))
    job = make_job(1)
    store.merge([job])
    path = _write_fills(tmp_path, [
        _fill(job.uid, expected_title=job.title, translated=True)])
    counts = apply_reviewed_fills(store, path)
    assert counts["applied"] == 0
    assert store._store[job.uid]["translated"] is False

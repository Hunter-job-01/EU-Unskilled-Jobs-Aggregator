import json
import os

from src.scrapers.base import ScrapeResult
from src.source_health import load_health, record_from_result, update_health


def test_record_from_result_keeps_errors_visible():
    r = ScrapeResult(source="eures", country="PL", status="failed",
                     errors=["boom"], notes=["n"], stats={"pages_fetched": 2})
    rec = record_from_result(r, kept=0, duration_sec=1.234)
    assert rec["status"] == "failed" and rec["errors"] == ["boom"]
    assert rec["stats"]["pages_fetched"] == 2 and rec["duration_sec"] == 1.2


def test_update_health_bounded_and_latest(tmp_path):
    d = str(tmp_path)
    for i in range(35):
        r = ScrapeResult(source="eures", country="PL")
        update_health(d, "run{}".format(i), [record_from_result(r, 1, 0.1)])
    health = load_health(d)
    assert len(health["recent_runs"]) == 30
    assert "eures|PL" in health["latest_by_source_country"]
    assert health["recent_runs"][0]["label"] == "run34"


def test_missing_or_corrupt_file_starts_empty(tmp_path):
    assert load_health(str(tmp_path))["recent_runs"] == []
    with open(os.path.join(str(tmp_path), "source_health.json"), "w") as fh:
        fh.write("{not json")
    assert load_health(str(tmp_path))["recent_runs"] == []

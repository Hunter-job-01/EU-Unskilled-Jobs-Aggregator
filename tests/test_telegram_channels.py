"""v6 stage E: Telegram channel scraper. Self-contained with a fixture
page modeled on the real t.me/s structure verified live 2026-11-05."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from src.scrapers.telegram_channels import TelegramChannelsScraper  # noqa: E402

_CFG = yaml.safe_load(open(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "config.yaml"), encoding="utf-8"))


def _page(posts):
    """posts: list of (id, datetime, text)"""
    blocks = []
    for pid, dt, text in posts:
        blocks.append(
            '<div class="tgme_widget_message" data-post="chan/{id}">'
            '<div class="tgme_widget_message_text js-message_text">{txt}</div>'
            '<time datetime="{dt}"></time></div>'.format(
                id=pid, txt=text.replace("\n", "<br/>"), dt=dt))
    return "<html><body>" + "".join(blocks) + "</body></html>"


class _Http:
    def __init__(self, body, status=200):
        self._body = body
        self._status = status

    def get(self, url):
        return type("R", (), {"status_code": self._status,
                              "text": self._body})()


def _scraper(tmp_path, body, state=None, status=200):
    import json
    state_file = tmp_path / "state.json"
    if state:
        state_file.write_text(json.dumps({"channels": state}))
    cfg = dict(_CFG)
    cfg["sources"] = dict(cfg["sources"])
    cfg["sources"]["telegram_channels"] = {
        "enabled": True,
        "state_file": str(state_file),
        "channels": [{"username": "chan", "display": "Chan",
                      "country": "PL", "language": "uk"}],
    }
    sc = TelegramChannelsScraper(cfg, _Http(body, status))
    return sc, state_file


def test_parses_posts_and_maps_jobs(tmp_path):
    body = _page([
        (101, "2026-11-05T10:00:00+00:00",
         "ПРАЦІВНИК СКЛАДУ — м. Psary\nБез знання мови. 27 zł."),
        (102, "2026-11-05T11:00:00+00:00",
         "Упаковка товару\nВахта в Лозанну, Швейцарія."),
    ])
    sc, state_file = _scraper(tmp_path, body)
    res = sc.scrape_country("pl", 10)
    assert res.status == "ok"
    assert len(res.jobs) == 1                      # CH post skipped
    assert res.stats["non_eu_skipped"] == 1
    job = res.jobs[0]
    assert job.url == "https://t.me/chan/101"
    assert job.source_id == "chan/101"
    assert job.posted_date == "2026-11-05"
    assert "Без знання мови" in job.description    # text preserved
    import json
    saved = json.loads(state_file.read_text())
    # cursor advances over the skipped post too: it was seen and handled
    assert saved["channels"]["chan"]["last_message_id"] == 102


def test_cursor_incremental_and_backlog_gap_reported(tmp_path):
    body = _page([(110 + i, "2026-11-05T1{}:00:00+00:00".format(i % 10),
                   "Працівник складу {}".format(i)) for i in range(3)])
    sc, state_file = _scraper(tmp_path, body,
                              state={"chan": {"last_message_id": 100}})
    res = sc.scrape_country("PL", 10)
    # cursor 100, oldest visible 110 -> gap of 9 reported, not claimed caught
    assert res.stats["backlog_gap"] == 9
    assert any("backlog gap" in n for n in res.notes)
    assert len(res.jobs) == 3
    # second run: nothing new, cursor unchanged
    res2 = sc.scrape_country("PL", 10)
    assert len(res2.jobs) == 0
    assert "backlog_gap" not in res2.stats


def test_cross_country_eu_post_skipped_and_counted(tmp_path):
    body = _page([(101, "2026-11-05T10:00:00+00:00",
                   "Работник склада\nЧехия, Прага. 2200$.")])
    sc, _ = _scraper(tmp_path, body)
    res = sc.scrape_country("PL", 10)
    assert len(res.jobs) == 0
    assert res.stats["cross_country_skipped"] == 1


def test_page_failure_keeps_cursor(tmp_path):
    sc, state_file = _scraper(tmp_path, "blocked", status=403)
    res = sc.scrape_country("PL", 10)
    assert res.status == "failed"
    assert not state_file.exists()                 # failure-safe: no cursor

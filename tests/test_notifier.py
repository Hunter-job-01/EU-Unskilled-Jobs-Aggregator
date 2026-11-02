"""Phase 4: Telegram digest delivery (src/notifier.py).

All tests run offline with a fake HTTP session; nothing here touches
api.telegram.org or needs real credentials.
"""
import json
import html as html_module

import pytest
import requests

from src.models import Job
from src.notifier import (
    MAX_MESSAGE_CHARS,
    RESULT_OK,
    RESULT_PERMANENT,
    RESULT_TRANSIENT,
    build_messages,
    format_job,
    send_digest,
    send_message,
)
from src.storage import JobStore


# ------------------------------------------------------------- fakes

class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {"ok": True}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    """Scripted responses; records every POST."""
    def __init__(self, responses=None):
        self.responses = list(responses if responses is not None
                              else [FakeResponse()])
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        resp = self.responses.pop(0) if self.responses else FakeResponse()
        if isinstance(resp, Exception):
            raise resp
        return resp


class FakeSleep:
    def __init__(self):
        self.slept = []

    def __call__(self, seconds):
        self.slept.append(seconds)


def make_store(tmp_path, jobs):
    cfg = {"data_dir": "data", "active_jobs_file": "active_jobs.json",
           "export_csv": "latest_export.csv", "run_status_file": "run_status.json",
           "tombstone_file": "pruned_uids.json"}
    store = JobStore(cfg, base_dir=str(tmp_path))
    for job in jobs:
        store.merge([job])
    store.save()
    return store


def make_job(uid, score, **kw):
    base = dict(title="Warehouse worker", company="ACME", country="PL",
                source="eures", url="https://example.test/j/1",
                uid=uid, score=score)
    base.update(kw)
    return Job(**base)


ENV = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "555"}

CHAT_NOT_FOUND = {"ok": False, "description": "Bad Request: chat not found"}
PARSE_ERROR = {"ok": False,
               "description": "Bad Request: can't parse entities: "
                              "Unsupported start tag"}


def telegram_cfg(**over):
    t = {"enabled": True, "min_score": 50, "max_jobs_per_digest": 15,
         "focus_countries": [], "dashboard_url": "", "message_delay_sec": 0}
    t.update(over)
    return {"telegram": t}


# ------------------------------------------------ graceful no-op

def test_missing_credentials_is_clean_noop(tmp_path, caplog):
    store = make_store(tmp_path, [make_job("u1", 90)])
    session = FakeSession()
    with caplog.at_level("INFO"):
        result = send_digest(store, telegram_cfg(), session=session,
                             environ={}, sleep=FakeSleep())
    assert result == 0
    assert session.calls == []
    assert "not configured" in caplog.text
    # the job stays unnotified for a future configured run
    assert store.get_unnotified_top_jobs(min_score=50)


def test_disabled_config_is_clean_noop(tmp_path):
    store = make_store(tmp_path, [make_job("u1", 90)])
    session = FakeSession()
    result = send_digest(store, telegram_cfg(enabled=False),
                         session=session, environ=ENV, sleep=FakeSleep())
    assert result == 0
    assert session.calls == []


def test_no_eligible_jobs_is_clean_noop(tmp_path):
    store = make_store(tmp_path, [make_job("u1", 10),
                                  make_job("u2", 90, notified_telegram=True),
                                  make_job("u3", 99, excluded=True)])
    session = FakeSession()
    result = send_digest(store, telegram_cfg(), session=session,
                         environ=ENV, sleep=FakeSleep())
    assert result == 0
    assert session.calls == []


# -------------------------------------------------- selection rules

def test_selection_min_score_limit_and_order(tmp_path):
    jobs = [make_job("low", 49), make_job("mid", 60), make_job("top", 95)]
    store = make_store(tmp_path, jobs)
    selected = store.get_unnotified_top_jobs(min_score=50, limit=15)
    assert [j["uid"] for j in selected] == ["top", "mid"]


def test_focus_countries_restrict_selection(tmp_path):
    jobs = [make_job("pl", 95, country="PL"),
            make_job("de", 90, country="DE")]
    store = make_store(tmp_path, jobs)
    selected = store.get_unnotified_top_jobs(min_score=50, limit=15,
                                             countries=["DE"])
    assert [j["uid"] for j in selected] == ["de"]
    session = FakeSession()
    notified = send_digest(store, telegram_cfg(focus_countries=["de"]),
                           session=session, environ=ENV, sleep=FakeSleep())
    assert notified == 1
    assert store.get_unnotified_top_jobs(min_score=50)[0]["uid"] == "pl"


# ---------------------------------------------------- delivery state

def test_successful_digest_marks_only_sent_jobs(tmp_path):
    store = make_store(tmp_path, [make_job("u1", 90), make_job("u2", 80)])
    session = FakeSession()
    notified = send_digest(store, telegram_cfg(), session=session,
                           environ=ENV, sleep=FakeSleep())
    assert notified == 2
    assert store.get_unnotified_top_jobs(min_score=50) == []
    # persisted to disk, not only in memory
    raw = json.loads((tmp_path / "data" / "active_jobs.json").read_text())
    assert raw["u1"]["notified_telegram"] is True
    assert raw["u1"]["notified_at"]
    assert raw["u2"]["notified_telegram"] is True


def test_failed_delivery_leaves_jobs_unnotified(tmp_path):
    store = make_store(tmp_path, [make_job("u1", 90)])
    session = FakeSession([FakeResponse(500, text="boom"),
                           FakeResponse(500, text="boom"),
                           FakeResponse(500, text="boom")])
    notified = send_digest(store, telegram_cfg(), session=session,
                           environ=ENV, sleep=FakeSleep())
    assert notified == 0
    assert len(session.calls) == 3  # bounded retries
    assert store.get_unnotified_top_jobs(min_score=50)[0]["uid"] == "u1"


def test_second_page_failure_keeps_first_page_marked(tmp_path):
    jobs = [make_job("u{:02d}".format(i), 90 - i) for i in range(30)]
    store = make_store(tmp_path, jobs)
    cfg = telegram_cfg(max_jobs_per_digest=30)
    # shrink pages: patch MAX_MESSAGE_CHARS via build_messages is not
    # configurable, so craft long titles to force pagination instead
    for job in jobs:
        job.title = "Warehouse worker " + "x" * 400
    store = make_store(tmp_path, jobs)
    session = FakeSession([FakeResponse(200),
                           FakeResponse(500, text="x"),
                           FakeResponse(500, text="x"),
                           FakeResponse(500, text="x")])
    notified = send_digest(store, cfg, session=session,
                           environ=ENV, sleep=FakeSleep())
    assert 0 < notified < 30
    remaining = store.get_unnotified_top_jobs(min_score=50, limit=50)
    assert len(remaining) == 30 - notified


def test_never_raises_on_network_exception(tmp_path):
    store = make_store(tmp_path, [make_job("u1", 90)])
    session = FakeSession([requests.ConnectionError("down"),
                           requests.ConnectionError("down"),
                           requests.ConnectionError("down")])
    notified = send_digest(store, telegram_cfg(), session=session,
                           environ=ENV, sleep=FakeSleep())
    assert notified == 0


# --------------------------------------------------------- formatting

def test_format_job_escapes_all_dynamic_text():
    record = {
        "country": "PL", "score": 85,
        "title": '<script>alert("x")</script>',
        "title_en": "", "company": "<b>ACME & Sons</b>",
        "location": 'Warsaw "north"', "signals": [],
        "url": 'https://example.test/?a=1&b="2"',
    }
    out = format_job(record)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "<b>ACME" not in out
    assert "&lt;b&gt;ACME &amp; Sons&lt;/b&gt;" in out
    assert "Warsaw &quot;north&quot;" in out
    assert "b=&quot;2&quot;" in out


def test_format_job_full_layout():
    record = {
        "country": "de", "score": 85, "title": "Lagerhelfer",
        "title_en": "Warehouse helper", "company": "Logi GmbH",
        "location": "Berlin",
        "signals": ["no_exp_or_training_mention", "housing_keyword_mention",
                    "permit_keyword_mention", "language_flex_keyword_mention",
                    "agency_or_relocation_mention"],
        "url": "https://example.test/apply",
    }
    out = format_job(record)
    assert "[85/100]" in out
    assert "DE" in out
    assert "Warehouse helper" in out
    assert "<i>Lagerhelfer</i>" in out  # original title under the English one
    assert "Logi GmbH" in out and "Berlin" in out
    for chip in ("\U0001F393 No Exp / Training", "\U0001F3E0 Housing",
                 "\U0001FAAA Permit", "\U0001F5E3\uFE0F Lang Flex",
                 "\U0001F3E2 Agency"):
        assert chip in out
    assert 'href="https://example.test/apply"' in out


def test_build_messages_paginates_under_limit():
    jobs = [{"uid": "u{}".format(i), "country": "PL", "score": 90,
             "title": "Warehouse worker " + "y" * 300, "title_en": "",
             "company": "ACME", "location": "Lodz", "signals": [],
             "url": "https://example.test/{}".format(i)}
            for i in range(20)]
    messages, page_uids = build_messages(jobs, telegram_cfg())
    assert len(messages) > 1
    for page in messages:
        assert len(page) <= MAX_MESSAGE_CHARS
    flat = [uid for page in page_uids for uid in page]
    assert flat == [j["uid"] for j in jobs]  # nothing lost, order kept


def test_build_messages_header_and_dashboard_link():
    jobs = [{"uid": "u1", "country": "PL", "score": 90, "title": "Packer",
             "title_en": "", "company": "ACME", "location": "",
             "signals": [], "url": "https://example.test/1"}]
    cfg = telegram_cfg(min_score=50,
                       dashboard_url="https://example.pages.dev/")
    messages, _ = build_messages(jobs, cfg)
    assert "score \u2265 50" in messages[0]
    assert "https://example.pages.dev/" in messages[0]


def test_send_message_posts_html_payload():
    session = FakeSession()
    ok = send_message(session, "123:abc", "555", "<b>hi</b>",
                      sleep=FakeSleep())
    assert ok == RESULT_OK
    call = session.calls[0]
    assert call["url"] == "https://api.telegram.org/bot123:abc/sendMessage"
    assert call["json"]["chat_id"] == "555"
    assert call["json"]["parse_mode"] == "HTML"
    assert call["json"]["disable_web_page_preview"] is True


def test_send_message_honours_429_retry_after():
    sleep = FakeSleep()
    session = FakeSession([
        FakeResponse(429, {"parameters": {"retry_after": 7}}),
        FakeResponse(200),
    ])
    ok = send_message(session, "t", "c", "text", sleep=sleep)
    assert ok == RESULT_OK
    assert sleep.slept == [7]
    assert len(session.calls) == 2


# ------------------------------------------------ daily cadence gate

from datetime import datetime, timezone


def test_daily_gate_blocks_second_digest_same_day(tmp_path):
    """The digest is daily even though main.py runs several times a day:
    the last-digest date persists in data/telegram_state.json."""
    store = make_store(tmp_path, [make_job("u1", 90)])
    cfg = telegram_cfg()
    cfg["storage"] = {"telegram_state_file": "telegram_state.json"}
    now = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)
    first = send_digest(store, cfg, session=FakeSession(), environ=ENV,
                        sleep=FakeSleep(), now=now)
    assert first == 1
    state_file = tmp_path / "data" / "telegram_state.json"
    assert state_file.is_file()
    assert '"2026-11-02"' in state_file.read_text()
    # a second run the same UTC day sends nothing at all
    session2 = FakeSession()
    later = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
    second = send_digest(store, cfg, session=session2, environ=ENV,
                         sleep=FakeSleep(), now=later)
    assert second == 0
    assert session2.calls == []


def test_daily_gate_allows_next_day_and_survives_reload(tmp_path):
    cfg = telegram_cfg()
    cfg["storage"] = {"telegram_state_file": "telegram_state.json"}
    store = make_store(tmp_path, [make_job("u1", 90)])
    send_digest(store, cfg, session=FakeSession(), environ=ENV,
                sleep=FakeSleep(),
                now=datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc))
    # a fresh JobStore (new workflow run) still sees the gate
    store2 = make_store(tmp_path, [make_job("u2", 95)])
    same_day = send_digest(store2, cfg, session=FakeSession(), environ=ENV,
                           sleep=FakeSleep(),
                           now=datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc))
    assert same_day == 0
    next_day = send_digest(store2, cfg, session=FakeSession(), environ=ENV,
                           sleep=FakeSleep(),
                           now=datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc))
    assert next_day == 1  # only u2: u1 was marked notified yesterday


def test_gate_not_written_when_nothing_delivered(tmp_path):
    cfg = telegram_cfg()
    cfg["storage"] = {"telegram_state_file": "telegram_state.json"}
    store = make_store(tmp_path, [make_job("u1", 90)])
    failing = FakeSession([FakeResponse(500, text="x")] * 3)
    result = send_digest(store, cfg, session=failing, environ=ENV,
                         sleep=FakeSleep(),
                         now=datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc))
    assert result == 0
    state_path = tmp_path / "data" / "telegram_state.json"
    if state_path.exists():  # failure counts may persist...
        state = json.loads(state_path.read_text())
        assert "last_digest_utc" not in state  # ...but never the daily gate
    # the same job is still eligible for a retry later that day
    retry = send_digest(store, cfg, session=FakeSession(), environ=ENV,
                        sleep=FakeSleep(),
                        now=datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc))
    assert retry == 1


# ----------------------------------------- hostile / overlong inputs

def test_overlong_single_job_never_overflows_a_page():
    jobs = [{"uid": "huge", "country": "PL", "score": 99,
             "title": "T" * 9000, "title_en": "",
             "company": "C" * 5000, "location": "L" * 500,
             "signals": [], "url": "https://example.test/x"}]
    messages, page_uids = build_messages(jobs, telegram_cfg())
    assert len(messages) == 1
    page = messages[0]
    assert len(page) <= MAX_MESSAGE_CHARS
    assert page_uids == [["huge"]]
    # field caps preserve a MEANINGFUL prefix plus the apply link - the
    # page is never reduced to a bare ellipsis.
    assert "T" * 100 in page
    assert 'href="https://example.test/x"' in page
    assert "\u2026" in page  # truncation is marked honestly


def test_hostile_markup_title_is_capped_escaped_and_deliverable(tmp_path):
    """A 3000x '<b>' title becomes a small, valid, meaningful block: the
    job is digested and marked notified for real delivery, not on an
    empty poisoned page."""
    nasty = "<b>" * 3000
    record = {"country": "PL", "score": 88, "title": nasty,
              "title_en": "", "company": "ACME", "location": "",
              "signals": [], "url": "https://example.test/j"}
    block = format_job(record)
    assert len(block) < 600
    # only the structural bold pair we generate ourselves may appear as
    # real markup; the hostile title survives as visible escaped text
    assert block.count("<b>") == 1 and block.count("</b>") == 1
    assert "&lt;b&gt;" in block
    assert 'href="https://example.test/j"' in block
    store = make_store(tmp_path, [make_job("nasty", 88, title=nasty)])
    notified = send_digest(store, telegram_cfg(), session=FakeSession(),
                           environ=ENV, sleep=FakeSleep(),
                           now=datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc))
    assert notified == 1


def test_permanent_400_retries_plain_text_once():
    session = FakeSession([FakeResponse(400, {"ok": False, "description": "Bad Request: can't parse entities: Unsupported start tag"}),
                           FakeResponse(200)])
    result = send_message(session, "t", "c", "<b>hi</b> &lt;3",
                          sleep=FakeSleep())
    assert result == RESULT_OK
    assert len(session.calls) == 2
    plain = session.calls[1]["json"]
    assert "parse_mode" not in plain
    assert "<b>" not in plain["text"]


def test_repeated_400_quarantines_after_three_digests(tmp_path):
    cfg = _digest_cfg_with_state()
    store = make_store(tmp_path, [make_job("bad", 90), make_job("good", 80)])
    for day in (1, 2, 3):
        session = FakeSession([FakeResponse(400, PARSE_ERROR)] * 2)
        send_digest(store, cfg, session=session, environ=ENV,
                    sleep=FakeSleep(),
                    now=datetime(2026, 11, day, 6, 0, tzinfo=timezone.utc))
    state = json.loads((tmp_path / "data" / "telegram_state.json").read_text())
    assert state["failures"]["bad"] == 3  # single-page digest: both counted
    # day 4: the quarantined pair is skipped, nothing is sent or marked
    session = FakeSession()
    result = send_digest(store, cfg, session=session, environ=ENV,
                         sleep=FakeSleep(),
                         now=datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc))
    assert result == 0
    assert session.calls == []
    raw = json.loads((tmp_path / "data" / "active_jobs.json").read_text())
    assert raw["bad"]["notified_telegram"] is False


def test_outages_and_auth_errors_never_count_as_job_failures(tmp_path):
    """401/403/404/5xx/network are transient: no failure counts, no
    quarantine, jobs simply retry on a later run."""
    for responses in ([FakeResponse(401, text="unauthorized")] * 3,
                      [FakeResponse(403, text="forbidden")] * 3,
                      [FakeResponse(404, text="not found")] * 3,
                      [FakeResponse(500, text="boom")] * 3,
                      [requests.ConnectionError("down")] * 3):
        cfg = _digest_cfg_with_state()
        store = make_store(tmp_path, [make_job("u1", 90)])
        result = send_digest(store, cfg, session=FakeSession(responses),
                             environ=ENV, sleep=FakeSleep(),
                             now=datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc))
        assert result == 0
        state_path = tmp_path / "data" / "telegram_state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())
            assert not state.get("failures"), responses
            assert "last_digest_utc" not in state
        state_path.unlink(missing_ok=True)
        # nothing quarantined: the job is still selected next time
        assert store.get_unnotified_top_jobs(min_score=50)


# -------------------------------------- URL scheme & starvation safety

def test_javascript_and_data_urls_never_become_links():
    for bad in ('javascript:alert(1)', 'JaVaScRiPt:alert(1)',
                'data:text/html,<script>1</script>', 'vbscript:x'):
        out = format_job({"country": "PL", "score": 10, "title": "Packer",
                          "title_en": "", "company": "ACME", "location": "",
                          "signals": [], "url": bad})
        assert "<a href=" not in out, bad
        assert html_module.escape(bad) in out


def test_https_apply_url_still_links():
    out = format_job({"country": "PL", "score": 10, "title": "Packer",
                      "title_en": "", "company": "ACME", "location": "",
                      "signals": [], "url": "https://example.test/a?x=1&y=2"})
    assert '<a href="https://example.test/a?x=1&amp;y=2">' in out


def _digest_cfg_with_state():
    cfg = telegram_cfg()
    cfg["storage"] = {"telegram_state_file": "telegram_state.json"}
    return cfg


def test_poisoned_job_cannot_starve_lower_jobs(tmp_path):
    """A job Telegram keeps rejecting is counted per failure and skipped
    after MAX_JOB_FAILURES digests - never marked notified, and lower
    jobs still get delivered."""
    bad = make_job("bad", 99)
    good = make_job("good", 80)
    store = make_store(tmp_path, [bad, good])
    cfg = _digest_cfg_with_state()
    env = ENV
    # Two digest days where the single page (both jobs) keeps failing:
    # simplest starvation model - the page carrying "bad" fails.
    import src.notifier as notifier

    def run_day(day, fail_first_page):
        now = datetime(2026, 11, day, 6, 0, tzinfo=timezone.utc)
        # force one job per page so failures map to a single job
        orig = notifier.build_messages
        def one_per_page(jobs, c, now=None):
            pages, uids = orig(jobs[:1], c, now=now), None
            all_msgs, all_uids = [], []
            for job in jobs:
                m, u = orig([job], c, now=now)
                all_msgs.extend(m)
                all_uids.extend(u)
            return all_msgs, all_uids
        notifier.build_messages = one_per_page
        try:
            responses = ([FakeResponse(400, {"ok": False, "description": "Bad Request: can't parse entities: Unsupported start tag"})] * 2
                         if fail_first_page else [FakeResponse(200)])
            return send_digest(store, cfg, session=FakeSession(responses),
                               environ=env, sleep=FakeSleep(), now=now)
        finally:
            notifier.build_messages = orig

    for day in (1, 2, 3):  # three failed digests for "bad"
        run_day(day, fail_first_page=True)
    raw = json.loads((tmp_path / "data" / "active_jobs.json").read_text())
    assert raw["bad"]["notified_telegram"] is False
    # from day 4 the poisoned job is skipped and "good" ships alone
    delivered = run_day(4, fail_first_page=False)
    assert delivered == 1
    raw = json.loads((tmp_path / "data" / "active_jobs.json").read_text())
    assert raw["good"]["notified_telegram"] is True
    assert raw["bad"]["notified_telegram"] is False
    state = json.loads((tmp_path / "data" / "telegram_state.json").read_text())
    assert state["failures"]["bad"] >= 3


def test_failure_count_cleared_after_eventual_success(tmp_path):
    cfg = _digest_cfg_with_state()
    store = make_store(tmp_path, [make_job("u1", 90)])
    now = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)
    send_digest(store, cfg,
                session=FakeSession([FakeResponse(400, {"ok": False, "description": "Bad Request: can't parse entities"})] * 2),
                environ=ENV, sleep=FakeSleep(), now=now)
    state = json.loads((tmp_path / "data" / "telegram_state.json").read_text())
    assert state["failures"] == {"u1": 1}
    # same-day retry succeeds: failure count cleared, gate set
    ok = send_digest(store, cfg, session=FakeSession(), environ=ENV,
                     sleep=FakeSleep(),
                     now=datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc))
    assert ok == 1
    state = json.loads((tmp_path / "data" / "telegram_state.json").read_text())
    assert state["failures"] == {}
    assert state["last_digest_utc"] == "2026-11-02"


# ------------------------- 400 classification by error description

def test_chat_not_found_400_is_transient_never_quarantines(tmp_path):
    """A misconfigured chat id ('Bad Request: chat not found') is a
    config problem: no failure counts even across repeated digests, no
    plain-text retry, no gate, jobs stay eligible."""
    cfg = _digest_cfg_with_state()
    store = make_store(tmp_path, [make_job("u1", 90)])
    for day in (1, 2, 3):
        session = FakeSession([FakeResponse(400, CHAT_NOT_FOUND)])
        result = send_digest(store, cfg, session=session, environ=ENV,
                             sleep=FakeSleep(),
                             now=datetime(2026, 11, day, 6, 0, tzinfo=timezone.utc))
        assert result == 0
        assert len(session.calls) == 1  # no pointless plain-text retry
    state_path = tmp_path / "data" / "telegram_state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        assert not state.get("failures")
        assert "last_digest_utc" not in state
    assert store.get_unnotified_top_jobs(min_score=50)


def test_content_400_counts_but_config_400_does_not():
    """send_message classification by Telegram's description text."""
    assert send_message(FakeSession([FakeResponse(400, PARSE_ERROR),
                                     FakeResponse(200)]),
                        "t", "c", "<b>x</b>", sleep=FakeSleep()) == RESULT_OK
    assert send_message(FakeSession([FakeResponse(400, PARSE_ERROR),
                                     FakeResponse(400, PARSE_ERROR)]),
                        "t", "c", "<b>x</b>", sleep=FakeSleep()) == RESULT_PERMANENT
    assert send_message(FakeSession([FakeResponse(400, CHAT_NOT_FOUND)]),
                        "t", "c", "<b>x</b>", sleep=FakeSleep()) == RESULT_TRANSIENT
    # content rejection, but the plain-text retry reveals a config fault
    assert send_message(FakeSession([FakeResponse(400, PARSE_ERROR),
                                     FakeResponse(400, CHAT_NOT_FOUND)]),
                        "t", "c", "<b>x</b>", sleep=FakeSleep()) == RESULT_TRANSIENT
    # 413 is always content (message too long)
    assert send_message(FakeSession([FakeResponse(413, {"ok": False})]),
                        "t", "c", "x", sleep=FakeSleep()) == RESULT_PERMANENT


def test_empty_state_exists_without_credentials(tmp_path):
    from src.notifier import ensure_state_file
    from types import SimpleNamespace
    data = tmp_path / 'data'
    data.mkdir()
    store = SimpleNamespace(data_dir=str(data))
    ensure_state_file(store, {})
    path = data / 'telegram_state.json'
    assert json.loads(path.read_text()) == {}
    path.write_text('{"last_digest_utc":"2026-11-01"}')
    ensure_state_file(store, {})
    assert json.loads(path.read_text())['last_digest_utc'] == '2026-11-01'

"""Phase 4: Telegram digest delivery.

Sends a daily HTML digest of the best new vacancies to a Telegram chat
via the Bot API (``https://api.telegram.org/bot<TOKEN>/sendMessage``),
using ``requests``.

Design rules:
  * Credentials come from the environment only - TELEGRAM_BOT_TOKEN and
    TELEGRAM_CHAT_ID (repository secrets in the GitHub workflows). They
    are never read from, or written to, config.yaml.
  * Graceful no-op: missing credentials, ``telegram.enabled: false`` or
    an empty selection log an info message and return cleanly so the
    automated scrape workflows never fail because a bot is not (yet)
    configured. Delivery failures are logged and leave the affected
    jobs unnotified for the next run; they never raise into main.
  * State: only jobs with ``notified_telegram == False`` and
    ``score >= telegram.min_score`` are selected, highest score first
    (optionally restricted to telegram.focus_countries). Jobs are
    marked via ``JobStore.mark_notified(uids)`` - which persists to
    active_jobs.json - ONLY after the message carrying them was
    actually accepted by Telegram, and per paginated message, so a
    crash mid-digest never marks an unsent job.
  * Cadence: the digest is DAILY. main.py runs several times a day
    (Tier 1 twice daily, Tier 2 every two days, the Tier-3 rotation),
    so the date of the last digest is persisted in
    data/telegram_state.json (committed with the other data files) and
    later runs the same UTC day skip cleanly.
  * Formatting: all dynamic text is html.escape()d, messages stay under
    3,800 characters (Telegram's hard limit is 4,096), sends pause
    politely between pages, and HTTP 429 answers honour the API's
    Retry-After hint.

The digest is a convenience mirror of the dataset; it changes nothing
about what a score means - relevance evidence for manual review, not an
eligibility verdict.
"""
from __future__ import annotations

import html
import hashlib
import json
import logging
from pathlib import Path
import os
import re
import time
from datetime import datetime, timezone

import requests

from .eligibility import EligibleStore, AutomaticEligibleStore, load_private
from .private_state import seal, unseal

log = logging.getLogger(__name__)

API_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"

#: Telegram's documented message cap is 4096 chars; stay safely under it.
MAX_MESSAGE_CHARS = 3800

#: Bounded retry budget per message (429 and transient errors).
MAX_ATTEMPTS = 3

#: A job whose page keeps getting REJECTED by Telegram (permanent 4xx,
#: not rate limiting) is skipped after this many digest attempts, so one
#: pathological posting cannot starve every lower-scored job forever.
#: Skipped jobs are NOT marked notified; they stay excluded from
#: selection with their failure count recorded in telegram_state.json
#: and a warning in the log for manual review.
MAX_JOB_FAILURES = 3

#: Signal name -> compact chip label, in display order.
SIGNAL_CHIPS = [
    ("no_exp_or_training_mention", "\U0001F393 No Exp / Training"),
    ("housing_keyword_mention", "\U0001F3E0 Housing"),
    ("permit_keyword_mention", "\U0001FAAA Permit"),
    ("language_flex_keyword_mention", "\U0001F5E3\uFE0F Lang Flex"),
    ("agency_or_relocation_mention", "\U0001F3E2 Agency"),
]


def _flag(iso: str) -> str:
    """Regional-indicator flag emoji for an ISO-3166 alpha-2 code."""
    code = (iso or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(ch) - 65) for ch in code)


#: Per-field caps applied BEFORE escaping: scraped text is hostile and
#: unbounded, so each field is cut to a meaningful prefix. A block built
#: from capped fields can never approach MAX_MESSAGE_CHARS - the
#: page-level truncation below is only a backstop and never eats the
#: header, title or apply link of a real job.
TITLE_MAX_CHARS = 150
FIELD_MAX_CHARS = 80


def _cap(text, limit: int) -> str:
    text = (text or "").strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + " \u2026"
    return text


def format_job(record: dict) -> str:
    """One job as an HTML block. Every dynamic value is capped, then
    escaped."""
    country = (record.get("country") or "").strip().upper()
    flag = _flag(country)
    score = int(record.get("score") or 0)
    title_en = _cap(record.get("title_en"), TITLE_MAX_CHARS)
    title = _cap(record.get("title"), TITLE_MAX_CHARS) or "(untitled posting)"
    head_title = title_en or title
    status = record.get("private_fit_status")
    lines = []
    if status:
        lines.append("<b>" + ("⚠️ محتاجة تحقق" if status == "review" else "✅ متوافقة مبدئيًا") + "</b>")
        lines.append(html.escape(record.get("private_fit_reason", "")))
        from .role_labels import arabic_role_label
        lines.append(html.escape(arabic_role_label(record)))
    lines += [
        "<b>{} {} \u00B7 [{}/100] {}</b>".format(
            flag, html.escape(country or "??"), score, html.escape(head_title))
    ]
    if title_en and title and title_en != title:
        lines.append("<i>{}</i>".format(html.escape(title)))
    meta = []
    company = _cap(record.get("company"), FIELD_MAX_CHARS)
    location = _cap(record.get("location"), FIELD_MAX_CHARS)
    if company:
        meta.append("\U0001F3E2 {}".format(html.escape(company)))
    if location:
        meta.append("\U0001F4CD {}".format(html.escape(location)))
    if meta:
        lines.append(" \u2014 ".join(meta))
    chips = [label for sig, label in SIGNAL_CHIPS
             if sig in (record.get("signals") or [])]
    if chips:
        lines.append("  ".join(chips))
    url = (record.get("url") or "").strip()
    if url.lower().startswith(("https://", "http://")) and len(url) <= 2048:
        lines.append('<a href="{}">Apply \u2192</a>'.format(
            html.escape(url, quote=True)))
    elif url:
        # Anything else (javascript:/data: pseudo-protocols, malformed
        # values) renders as escaped plain text, never as a link.
        lines.append("Apply: {}".format(html.escape(url)))
    return "\n".join(lines)


def build_messages(jobs, cfg: dict, *, now=None) -> list:
    """Header + job blocks paginated under MAX_MESSAGE_CHARS.

    Returns (messages, page_uids): the HTML text of each page and the
    job UIDs carried by that page, so marking can happen per page. A
    single pathological job (e.g. a scraped description-length title)
    can never push a page over the limit: its block is hard-truncated
    after escaping.
    """
    tcfg = (cfg or {}).get("telegram", {}) or {}
    min_score = int(tcfg.get("min_score", 50))
    dashboard_url = (tcfg.get("dashboard_url") or "").strip()
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    header = "\U0001F1EA\U0001F1FA <b>EU Entry-Level Jobs \u2014 {}</b>\n" \
             "{} new vacanc{} (score \u2265 {})".format(
                 day, len(jobs), "y" if len(jobs) == 1 else "ies", min_score)
    if dashboard_url:
        header += "\n\U0001F4CA Dashboard: {}".format(
            html.escape(dashboard_url, quote=True))
    header += "\n"

    messages, page_uids = [], []
    current, current_uids = header, []
    for record in jobs:
        block = "\n\n" + format_job(record)
        # One hostile/overlong record must not overflow a fresh page:
        # cut at a whole-line boundary, then drop any partial entity or
        # tag tail so the page stays valid HTML.
        room = MAX_MESSAGE_CHARS - len(header)
        if len(block) > room:
            block = block[:room]
            block = block[: block.rfind("\n")]
            block = re.sub(r"&[#A-Za-z0-9]*$", "", block)
            block = re.sub(r"<[^>]*$", "", block)
            block = block.rstrip() + " \u2026"
        if current_uids and len(current) + len(block) > MAX_MESSAGE_CHARS:
            messages.append(current)
            page_uids.append(current_uids)
            current, current_uids = header + block, [record["uid"]]
        else:
            current += block
            current_uids.append(record["uid"])
    if current_uids:
        messages.append(current)
        page_uids.append(current_uids)
    return messages, page_uids


#: send_message result classes. Only "permanent" (Telegram rejected the
#: MESSAGE ITSELF: 400 after the plain-text fallback, or 413 too long)
#: may count against a job's failure quarantine. Everything else -
#: outages, 5xx, auth/config (401/403/404), exhausted 429 rate limits -
#: is "transient": the digest stops, nothing is counted, and the whole
#: batch retries on a later run.
RESULT_OK = "ok"
RESULT_PERMANENT = "permanent"
RESULT_TRANSIENT = "transient"

_TAG_RX = re.compile(r"<[^>]+>")

#: Telegram 400 descriptions that indict the MESSAGE CONTENT itself.
#: Everything else at 400 - "chat not found", empty chat id, "bot was
#: blocked", "not enough rights", supergroup migration - is a
#: configuration/permission problem: transient, never counted against
#: jobs, and logged for the operator.
_CONTENT_ERROR_MARKERS = (
    "can't parse entities",
    "cant parse entities",
    "message is too long",
    "message text is empty",
    "text must be non-empty",
    "wrong entity",
    "wrong message entity",
)


def _is_content_error(resp) -> bool:
    """True only when Telegram's error description blames the message
    body, never the chat/bot configuration."""
    try:
        description = str(resp.json().get("description", "")).lower()
    except (ValueError, AttributeError):
        description = ""
    return any(marker in description for marker in _CONTENT_ERROR_MARKERS)


def _plain_text(html_text: str) -> str:
    """Tag-stripped fallback body for entity-parse rejections."""
    plain = _TAG_RX.sub("", html_text)
    plain = (plain.replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&#x27;", "'")
             .replace("&amp;", "&"))
    return plain[:MAX_MESSAGE_CHARS]


def send_message(session, token: str, chat_id: str, text: str,
                 sleep=time.sleep) -> str:
    """POST one message; bounded retries on 429 (Retry-After) and
    transient errors; one plain-text retry on an entity-parse 400.
    Returns RESULT_OK / RESULT_PERMANENT / RESULT_TRANSIENT."""
    url = API_TEMPLATE.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = session.post(url, json=payload, timeout=30)
        except requests.RequestException as exc:
            log.warning("Telegram send attempt %d/%d failed: %s",
                        attempt, MAX_ATTEMPTS, type(exc).__name__)
            if attempt < MAX_ATTEMPTS:
                sleep(min(2 ** attempt, 30))
            continue
        if resp.status_code == 200:
            return RESULT_OK
        if resp.status_code == 429:
            retry_after = 5
            try:
                retry_after = int(resp.json().get("parameters", {})
                                  .get("retry_after", retry_after))
            except (ValueError, AttributeError):
                pass
            log.warning("Telegram rate limit (429); waiting %ss as asked",
                        retry_after)
            sleep(max(1, retry_after))
            continue
        if resp.status_code in (400, 413):
            content_fault = resp.status_code == 413 or _is_content_error(resp)
            if not content_fault:
                # "chat not found", bad/empty chat id, blocked bot,
                # missing rights, supergroup migration: a config problem,
                # not a poisoned job. Pause without failure counts.
                log.error("Telegram delivery paused: HTTP %s %s - check "
                          "TELEGRAM_CHAT_ID and bot permissions; no job "
                          "failure counted", resp.status_code,
                          (resp.text or "")[:200])
                return RESULT_TRANSIENT
            if resp.status_code == 400:
                # Content rejection: one plain-text retry, classified
                # with the same description check.
                try:
                    plain = session.post(
                        url, json={"chat_id": chat_id,
                                   "text": _plain_text(text),
                                   "disable_web_page_preview": True},
                        timeout=30)
                except requests.RequestException as exc:
                    log.warning("plain-text fallback send failed: %s", type(exc).__name__)
                    return RESULT_TRANSIENT  # network, not the message
                if plain.status_code == 200:
                    log.info("Telegram accepted the plain-text "
                             "fallback after a 400 entity rejection")
                    return RESULT_OK
                if plain.status_code == 400 and not _is_content_error(plain):
                    log.error("Telegram plain-text fallback hit a config "
                              "problem: HTTP 400 %s; no job failure counted",
                              (plain.text or "")[:200])
                    return RESULT_TRANSIENT
            log.warning("Telegram permanently rejected this message: "
                        "HTTP %s %s", resp.status_code,
                        (resp.text or "")[:200])
            return RESULT_PERMANENT
        log.warning("Telegram send attempt %d/%d: HTTP %s %s",
                    attempt, MAX_ATTEMPTS, resp.status_code,
                    (resp.text or "")[:200])
        if attempt < MAX_ATTEMPTS:
            sleep(min(2 ** attempt, 30))
    return RESULT_TRANSIENT


def _state_path(store, cfg: dict) -> str:
    scfg = (cfg or {}).get("storage", {}) or {}
    return os.path.join(store.data_dir,
                        scfg.get("telegram_state_file", "telegram_state.json"))


def _load_state(store, cfg: dict) -> dict:
    try:
        with open(_state_path(store, cfg), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if cfg.get("telegram", {}).get("private_fit_required"):
            return unseal(payload, os.environ.get("JOB_FIT_STATE_KEY", ""))
        return payload if isinstance(payload, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        if cfg.get("telegram", {}).get("private_fit_required"):
            raise ValueError("Private notification state unavailable") from None
        return {}


def _save_state(store, cfg: dict, state: dict) -> None:
    tmp = _state_path(store, cfg) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        payload = seal(state, os.environ.get("JOB_FIT_STATE_KEY", "")) if cfg.get("telegram", {}).get("private_fit_required") else state
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, _state_path(store, cfg))


def ensure_state_file(store, cfg: dict) -> None:
    """Keep an honest empty state tracked before any digest is delivered."""
    path = _state_path(store, cfg)
    if not os.path.isfile(path):
        try:
            _save_state(store, cfg, {})
        except ValueError:
            if not cfg.get("telegram", {}).get("private_fit_required"):
                raise
            log.error("Private state unavailable; scraping continues without notification state")


def _send_single_digest(store, cfg: dict, *, session=None, environ=None,
                        sleep=time.sleep, now=None, destination_state=None,
                        save_destination=None, translator=None) -> int:
    """Send the digest for the current store. Returns jobs notified.

    Never raises for configuration or delivery problems: the scrape
    pipeline commits its data after this call and must not fail because
    of a bot issue. At most one digest per UTC day: main.py runs
    several times a day, and the last-digest date is persisted in
    data/telegram_state.json (committed with the other data files).
    """
    tcfg = (cfg or {}).get("telegram", {}) or {}
    if not tcfg.get("enabled", True):
        log.info("Telegram digest disabled in config (telegram.enabled); skipping")
        return 0
    env = os.environ if environ is None else environ
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        log.info("Telegram digest skipped: TELEGRAM_BOT_TOKEN / "
                 "TELEGRAM_CHAT_ID not configured")
        return 0

    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    state = _load_state(store, cfg) if destination_state is None else destination_state
    persist = save_destination or (lambda value: _save_state(store, cfg, value))
    if state.get("last_digest_utc") == today:
        log.info("Telegram digest already sent today (%s UTC); skipping", today)
        return 0

    min_score = int(tcfg.get("min_score", 50))
    max_jobs = int(tcfg.get("max_jobs_per_digest", 15))
    focus = [str(c).strip().upper() for c in (tcfg.get("focus_countries") or [])
             if str(c).strip()]
    failures = state.get("failures") or {}
    jobs = store.get_unnotified_top_jobs(
        min_score=min_score, limit=max_jobs + len(failures),
        countries=focus or None)
    starving = [j["uid"] for j in jobs
                if int(failures.get(j["uid"], 0)) >= MAX_JOB_FAILURES]
    if starving:
        log.warning("Telegram digest: skipping %d job(s) after %d failed "
                    "delivery attempts (left unnotified for manual review): %s",
                    len(starving), MAX_JOB_FAILURES, "[private]" if tcfg.get("private_fit_required") else ", ".join(starving[:5]))
    jobs = [j for j in jobs if j["uid"] not in set(starving)][:max_jobs]
    if not jobs:
        log.info("Telegram digest: no unnotified jobs at score >= %d%s",
                 min_score, " in focus countries" if focus else "")
        return 0

    messages, page_uids = build_messages(jobs, cfg, now=now)
    session = session or requests.Session()
    if tcfg.get("full_arabic_translation"):
        try:
            from .offline_translation import translation_document
            if tcfg.get("translation_engine") == "gemini_free":
                from .gemini_translation import Translator
                translator = translator or Translator(key=environ.get("GEMINI_API_KEY", ""),
                    cache_dir=str(Path(store.data_dir) / ".cache" / "translation"),
                    max_calls=int(tcfg.get("translation_calls_per_run", 5)))
                document = translation_document(jobs, translate=translator)
            else:
                document = translation_document(jobs)
            response = session.post(API_TEMPLATE.format(token=token).replace('/sendMessage', '/sendDocument'),
                data={"chat_id": chat_id, "caption": "العنوان والوصف والشروط: ترجمة آلية مع النص الأصلي الكامل. ⚠️ الحالات غير المؤكدة مميزة داخل الملف."},
                files={"document": ("jobs-arabic-and-original.txt", document, "text/plain; charset=utf-8")}, timeout=45)
            if response.status_code != 200 or not response.json().get("ok"):
                log.error("Full translation document not accepted; jobs remain unnotified")
                return 0
            # The document is the digest: mark only after Telegram accepts it.
            marked = store.mark_notified([job["uid"] for job in jobs])
            state["last_digest_utc"] = today
            persist(state)
            log.info("Full translated document digest accepted; private state persisted")
            return marked
        except Exception:
            log.error("Full translation document unavailable; jobs remain unnotified")
            return 0
    delay = float(tcfg.get("message_delay_sec", 1.0))
    notified = 0
    for idx, (text, uids) in enumerate(zip(messages, page_uids)):
        if idx and delay > 0:
            sleep(delay)  # polite pause between paginated messages
        result = send_message(session, token, chat_id, text, sleep=sleep)
        if result != RESULT_OK:
            if result == RESULT_PERMANENT:
                # Telegram rejected THIS message: count it against these
                # jobs so repeat offenders are quarantined after
                # MAX_JOB_FAILURES instead of starving lower jobs.
                for uid in uids:
                    failures[uid] = int(failures.get(uid, 0)) + 1
                state["failures"] = failures
                persist(state)
                log.error("Telegram digest stopped: page %d/%d was "
                          "permanently rejected; its %d job(s) accrue a "
                          "failure count", idx + 1, len(messages), len(uids))
            else:
                # Outage, auth/config or exhausted rate limit: never
                # counted against jobs; everything retries on a later run.
                log.error("Telegram digest paused: page %d/%d hit a "
                          "transient delivery problem; %d job(s) stay "
                          "unnotified, no failure counts",
                          idx + 1, len(messages),
                          sum(len(u) for u in page_uids[idx:]))
            break
        marked = store.mark_notified(uids)  # persists active_jobs.json
        notified += marked
        for uid in uids:
            failures.pop(uid, None)  # delivered: clear any past failures
        log.info("Telegram digest page %d/%d delivered; %d job(s) marked notified",
                 idx + 1, len(messages), marked)
    if notified:
        state["last_digest_utc"] = today
        state["failures"] = failures
        persist(state)  # committed with the other data files
    return notified


class _DestinationStore:
    """Track accepted jobs separately for each configured audience."""
    def __init__(self, store, state, persist):
        self.store, self.state, self.persist = store, state, persist
        self.data_dir = store.data_dir

    def get_unnotified_top_jobs(self, min_score, limit=15, countries=None):
        records = getattr(self.store, "_store", None)
        if records is None:
            records = self.store.load()
        sent = self.state.get("notified_uids", {})
        wanted = set(countries) if countries else None
        jobs = [dict(r) for uid, r in records.items()
                if uid not in sent and not r.get("excluded")
                and (r.get("score") or 0) >= min_score
                and (wanted is None or r.get("country") in wanted)]
        jobs.sort(key=lambda r: (r.get("private_fit_status") == "review", -(r.get("score") or 0), r.get("posted_date") or ""))
        # At most five uncertain candidates, kept separate from positive matches.
        suitable = [r for r in jobs if r.get("private_fit_status") != "review"]
        uncertain = [r for r in jobs if r.get("private_fit_status") == "review"]
        return (suitable[:limit] + uncertain[:min(5, max(0, limit - len(suitable)))])[:limit]

    def mark_notified(self, uids):
        sent = self.state.setdefault("notified_uids", {})
        day = datetime.now(timezone.utc).date().isoformat()
        for uid in uids:
            sent[uid] = day
        # Bound retained delivery marks to currently active jobs. Tombstones
        # already protect recently pruned jobs from re-ingestion.
        underlying = getattr(self.store, "store", self.store)
        active = getattr(underlying, "_store", None)
        if active is None:
            active = underlying.load()
        self.state["notified_uids"] = {u: d for u, d in sent.items() if u in active}
        self.persist(self.state)
        return len(uids)


def send_digest(store, cfg: dict, *, session=None, environ=None,
                sleep=time.sleep, now=None) -> int:
    """Send to one legacy chat or several independently tracked audiences."""
    env = os.environ if environ is None else environ
    if cfg.get("telegram", {}).get("private_fit_required"):
        try:
            from .source_evidence import fetch_public_text, fetch_job_details
            from .private_state import cipher
            profile, reviews = load_private(env)
            cipher(env.get("JOB_FIT_STATE_KEY", ""))
            # Validate/migrate persistence before any send; never silently reset it.
            _load_state(store, cfg)
            if cfg.get("telegram", {}).get("automatic_requirements"):
                store = AutomaticEligibleStore(store, profile, fetch_job_details, now=now,
                    max_checks=int(cfg["telegram"].get("detail_checks_per_run", 30)))
                log.info("Automatic detail-check batch finished (candidate-specific results remain private)")
            else:
                store = EligibleStore(store, profile, reviews, fetch_public_text)
        except Exception:
            log.error("Private eligibility unavailable; notifications withheld (no generic fallback)")
            return 0
    # Persist legacy delivery gates encrypted even when no suitable jobs exist.
    if cfg.get("telegram", {}).get("private_fit_required"):
        try:
            _save_state(store, cfg, _load_state(store, cfg))
        except Exception:
            log.error("Private notification persistence unavailable; sends withheld")
            return 0
    translator = None
    if cfg.get("telegram", {}).get("translation_engine") == "gemini_free":
        from .gemini_translation import Translator
        translator = Translator(key=env.get("GEMINI_API_KEY", ""),
            cache_dir=str(Path(store.data_dir) / ".cache" / "translation"),
            max_calls=int(cfg["telegram"].get("translation_calls_per_run", 5)))
    raw = (env.get("TELEGRAM_CHAT_IDS") or "").strip()
    if not raw and cfg.get("telegram", {}).get("private_fit_required"):
        log.error("Private fit requires independently tracked destinations; sends withheld")
        return 0
    if not raw:
        return _send_single_digest(store, cfg, session=session, environ=env,
                                   sleep=sleep, now=now, translator=translator)
    chats = list(dict.fromkeys(c.strip() for c in raw.split(",") if c.strip()))
    if not chats:
        return 0
    root = _load_state(store, cfg)
    destinations = root.setdefault("destinations", {})
    total = 0
    for chat in chats:
        # Never publish private Telegram chat/channel identifiers in the
        # tracked state. Stable digests preserve independent delivery gates.
        state_key = hashlib.sha256(chat.encode("utf-8")).hexdigest()
        state = destinations.setdefault(state_key, {})
        def persist(value, key=state_key):
            destinations[key] = value
            _save_state(store, cfg, root)
        scoped = _DestinationStore(store, state, persist)
        local_env = dict(env, TELEGRAM_CHAT_ID=chat)
        total += _send_single_digest(scoped, cfg, session=session,
                    environ=local_env, sleep=sleep, now=now,
                    destination_state=state, save_destination=persist, translator=translator)
    return total

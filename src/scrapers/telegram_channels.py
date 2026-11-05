"""v6 stage E: public Telegram job-channel incremental scraper.

Reads the PUBLIC web preview (https://t.me/s/<channel>) over plain HTTP -
no API keys, no account, no login. Public readability does NOT establish
employer legitimacy, reuse rights, or a zero-block guarantee; posts are
leads, and any contact details extracted later stay unverified data
(never outreach from this pipeline).

Incrementality: per-channel cursor (last processed message id) in
data/telegram_channel_state.json. The preview shows only the ~20 newest
messages, so when the cursor is older than the oldest visible message
there is a backlog gap: it is counted and reported, never silently
claimed as caught up. The cursor is persisted only after a page is
fetched and parsed successfully (failure-safe).

Country handling: a post's country is taken from an explicit country
mention (uk/ru/en names); a non-EU mention (Switzerland, UK, USA, ...)
skips the post and is counted; an EU mention different from the
requested country is skipped and counted (cross-country loss is
reported, not hidden); no mention falls back to the channel's
configured default country.

Restrictions stay in the full description text - per-post citizenship,
visa, residence or temporary-protection requirements are never dropped,
and exclusive-EU/uk/ru forms are caught by the keyword disqualifiers
(config), not by inference here.
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import re
from typing import Optional

from ..models import Job
from .base import ScrapeResult, ScraperError

log = logging.getLogger(__name__)

_MSG_BLOCK = re.compile(r'data-post="([A-Za-z0-9_]+)/(\d+)"')
_DATETIME = re.compile(r'datetime="([^"]+)"')
_TEXT_DIV = re.compile(
    r'class="tgme_widget_message_text js-message_text"[^>]*>(.*?)</div>',
    re.S)
_TAG = re.compile(r"<[^>]+>")

# Folded-lower country mentions -> ISO code. EU only for mapping; the
# non-EU set skips with a counter (pipeline scope is EU countries).
_COUNTRY_HINTS = {
    "PL": ["польща", "польша", "poland"],
    "CZ": ["чехія", "чехия", "czech"],
    "DE": ["німеччина", "германия", "germany"],
    "LT": ["литва", "lithuania"],
    "SK": ["словаччина", "словакия", "slovakia"],
    "NL": ["нідерланди", "нидерланды", "netherlands", "holland"],
    "ES": ["іспанія", "испания", "spain"],
    "IT": ["італія", "италия", "italy"],
    "FR": ["франція", "франция", "france"],
    "RO": ["румунія", "румыния", "romania"],
    "HU": ["угорщина", "венгрия", "hungary"],
    "LV": ["латвія", "латвия", "latvia"],
    "EE": ["естонія", "эстония", "estonia"],
    "GR": ["греція", "греция", "greece"],
    "CY": ["кіпр", "кипр", "cyprus"],
    "BG": ["болгарія", "болгария", "bulgaria"],
    "HR": ["хорватія", "хорватия", "croatia"],
    "AT": ["австрія", "австрия", "austria"],
    "BE": ["бельгія", "бельгия", "belgium"],
    "PT": ["португалія", "португалия", "portugal"],
    "IE": ["ірландія", "ирландия", "ireland"],
    "DK": ["данія", "дания", "denmark"],
    "FI": ["фінляндія", "финляндия", "finland"],
    "SI": ["словенія", "словения", "slovenia"],
    "LU": ["люксембург", "luxembourg"],
    "MT": ["мальта", "malta"],
    "SE": ["швеція", "швеция", "sweden"],
}
_NON_EU_HINTS = {
    "CH": ["швейцарія", "швейцария", "switzerland"],
    "GB": ["великобританія", "великобритания", "britain"],
    "US": ["сша", "usa", "united states"],
    "NO": ["норвегія", "норвегия", "norway"],
    "AE": ["оае", "эмират"],
}


def _clean_text(raw_html: str) -> str:
    txt = re.sub(r"<br\s*/?>", "\n", raw_html)
    txt = _TAG.sub("", txt)
    return _html.unescape(txt).strip()


def _hint_country(text_lower: str) -> Optional[str]:
    for code, words in _COUNTRY_HINTS.items():
        if any(w in text_lower for w in words):
            return code
    return None


def _hint_non_eu(text_lower: str) -> Optional[str]:
    for code, words in _NON_EU_HINTS.items():
        if any(w in text_lower for w in words):
            return code
    return None


class TelegramChannelsScraper:
    """One scraper instance covering all configured public channels."""

    name = "telegram_channels"

    def __init__(self, cfg: dict, http):
        self.cfg = cfg
        self.http = http
        tg = cfg.get("sources", {}).get("telegram_channels", {}) or {}
        self.channels = [c for c in tg.get("channels", []) if c.get("enabled", True)]
        self.state_path = tg.get(
            "state_file", "data/telegram_channel_state.json")

    def _load_state(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                return json.load(fh).get("channels", {})
        except Exception:
            return {}

    def _save_state(self, state: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"channels": state}, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_path)
        except Exception as exc:  # noqa: BLE001 - never crash a run on state
            log.warning("telegram channel state not saved: %s", exc)

    def parse_page(self, body: str, channel: dict) -> list:
        """Parse one preview page into (msg_id, datetime, text) tuples."""
        posts = [(m.group(1), int(m.group(2)), m.start())
                 for m in _MSG_BLOCK.finditer(body)]
        out = []
        for i, (_chan, msg_id, start) in enumerate(posts):
            end = posts[i + 1][2] if i + 1 < len(posts) else len(body)
            block = body[start:end]
            dt = _DATETIME.search(block)
            txt = _TEXT_DIV.search(block)
            text = _clean_text(txt.group(1)) if txt else ""
            if not text:
                continue  # media-only or service message: not a vacancy
            out.append((msg_id, dt.group(1) if dt else "", text))
        out.sort(key=lambda row: row[0])
        return out

    def scrape_country(self, country_code: str, max_jobs: int) -> ScrapeResult:
        country_code = country_code.upper()
        result = ScrapeResult(source=self.name, country=country_code)
        if not self.channels:
            result.status = "skipped"
            result.notes.append("no telegram channels enabled in config")
            return result
        state = self._load_state()
        new_state = dict(state)
        for channel in self.channels:
            if len(result.jobs) >= max_jobs:
                break
            default_cc = (channel.get("country") or "").upper()
            if default_cc and default_cc != country_code:
                continue  # this channel belongs to another country's run
            username = channel["username"]
            url = "https://t.me/s/{}".format(username)
            try:
                resp = self.http.get(url)
                if resp.status_code != 200:
                    raise ScraperError("http {}".format(resp.status_code))
                posts = self.parse_page(resp.text, channel)
            except Exception as exc:  # noqa: BLE001 - per-channel isolation
                result.errors.append("{}: {}".format(username, exc))
                continue
            cursor = int((state.get(username) or {}).get("last_message_id") or 0)
            visible = [p for p in posts if p[0] > cursor]
            if cursor and posts:
                oldest_visible = posts[0][0]
                if oldest_visible > cursor + 1:
                    gap = oldest_visible - cursor - 1
                    result.stats["backlog_gap"] = (
                        result.stats.get("backlog_gap", 0) + gap)
                    result.notes.append(
                        "{}: backlog gap of ~{} messages older than the "
                        "preview window (cursor {}, oldest visible {}) - "
                        "missed, not caught up".format(
                            username, gap, cursor, oldest_visible))
            max_seen = cursor
            for msg_id, dt, text in visible:
                max_seen = max(max_seen, msg_id)
                lowered = text.lower()
                non_eu = _hint_non_eu(lowered)
                if non_eu:
                    result.stats["non_eu_skipped"] = (
                        result.stats.get("non_eu_skipped", 0) + 1)
                    continue
                hinted = _hint_country(lowered)
                if hinted and hinted != country_code:
                    result.stats["cross_country_skipped"] = (
                        result.stats.get("cross_country_skipped", 0) + 1)
                    continue
                if len(result.jobs) >= max_jobs:
                    break
                first_line = next((l.strip() for l in text.splitlines()
                                   if l.strip()), "")
                job = Job(
                    title=first_line[:150],
                    company=channel.get("display") or username,
                    country=country_code,
                    source="telegram_{}".format(username),
                    url="https://t.me/{}/{}".format(username, msg_id),
                    source_id="{}/{}".format(username, msg_id),
                    description=text,           # restrictions preserved verbatim
                    posted_date=(dt or "")[:10] or None,
                    languages=[channel.get("language", "uk")],
                )
                result.jobs.append(job)
            # Failure-safe cursor: page fetched+parsed OK, so advancing
            # over every message we actually saw is safe even when some
            # were skipped by country rules.
            if max_seen > cursor:
                new_state[username] = {"last_message_id": max_seen}
        if new_state != state:
            self._save_state(new_state)
        if result.errors:
            result.status = "partial" if result.jobs else "failed"
        return result

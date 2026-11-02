"""Private evidence-led eligibility. Never persist a candidate or a verdict.

Missing requirements are unknown, not waived. A reviewed source snapshot is
required: automated keyword absence cannot prove a candidate is eligible.
"""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone
from html import unescape

DOMAINS = ('role', 'language', 'licence', 'experience', 'education', 'authorization')
LEVELS = {'A1': 1, 'A2': 2, 'B1': 3, 'B2': 4, 'C1': 5, 'C2': 6, 'native': 7}


class PrivateConfigError(ValueError):
    """Fixed messages only: do not expose a private input in exceptions."""


def load_private(env):
    try:
        profile = json.loads(env.get('JOB_FIT_PROFILE', ''))
        reviews = json.loads(env.get('JOB_FIT_REVIEWS', '{}'))
        if not isinstance(profile, dict) or not isinstance(reviews, dict):
            raise ValueError
        if profile.get('schema') != 1 or not isinstance(profile.get('languages'), dict):
            raise ValueError
        if not isinstance(profile.get('licences'), list):
            raise ValueError
        if not isinstance(profile.get('roles'), list) or not profile['roles']:
            raise ValueError
        if profile.get('authorization') not in ('needs_sponsorship', 'existing'):
            raise ValueError
        for level in profile['languages'].values():
            if level not in LEVELS:
                raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise PrivateConfigError('Private eligibility configuration is missing or invalid') from None
    return profile, reviews


def source_digest(text):
    """Hash visible source evidence, not scripts, cookies or page instructions."""
    return hashlib.sha256(re.sub(r'\s+', ' ', unescape(text)).strip().encode()).hexdigest()


def evaluate(profile, review, *, url, text, now=None):
    """pass / reject / review. Only explicit, complete, fresh evidence passes.

    Reviewed facts have a quote and an independently reviewed structured value.
    Source content never supplies executable instructions or private criteria.
    """
    if not isinstance(review, dict) or review.get('url') != url:
        return 'review'
    now = now or datetime.now(timezone.utc)
    try:
        checked = datetime.fromisoformat(review['checked_at'].replace('Z', '+00:00'))
        if checked.tzinfo is None or not 0 <= (now - checked).total_seconds() <= 86400:
            return 'review'
    except (ValueError, TypeError, KeyError):
        return 'review'
    if not text or review.get('source_sha256') != source_digest(text):
        return 'review'
    facts = review.get('facts')
    if not isinstance(facts, dict) or set(facts) != set(DOMAINS):
        return 'review'
    pending = False
    folded = re.sub(r'\s+', ' ', unescape(text)).casefold()
    for domain in DOMAINS:
        fact = facts[domain]
        if not isinstance(fact, dict):
            return 'review'
        quote = re.sub(r'\s+', ' ', unescape(str(fact.get('quote') or ''))).strip()
        status = fact.get('status')
        if status == 'unknown' or not quote or quote.casefold() not in folded:
            pending = True
            continue
        if status not in ('required', 'preferred', 'waived'):
            pending = True
            continue
        if status in ('preferred', 'waived'):
            if domain in ('role', 'authorization'):
                pending = True
            continue
        value = fact.get('value')
        if domain == 'role':
            if value not in profile['roles']:
                return 'reject'
        elif domain == 'language':
            # list of acceptable alternatives, each containing all needed languages
            if not isinstance(value, list) or not value:
                pending = True
                continue
            supported = False
            for alternative in value:
                if not isinstance(alternative, dict) or not alternative:
                    pending = True
                    continue
                if any(level not in LEVELS for level in alternative.values()):
                    pending = True
                    continue
                if all(LEVELS.get(profile['languages'].get(lang), 0) >= LEVELS[level]
                       for lang, level in alternative.items()):
                    supported = True
            if not supported and not pending:
                return 'reject'
            if not supported:
                pending = True
        elif domain == 'licence':
            if not isinstance(value, list) or not value:
                pending = True
            elif not all(v in profile['licences'] for v in value):
                return 'reject'
        elif domain == 'experience':
            if not isinstance(value, dict) or 'role' not in value or not isinstance(value.get('months'), int):
                pending = True
            else:
                months = profile.get('experience_months', {}).get(value['role'])
                if months is None:
                    pending = True
                elif months < value['months']:
                    return 'reject'
        elif domain == 'education':
            if value != profile.get('education'):
                pending = True  # credential equivalence cannot be inferred
        elif domain == 'authorization':
            if profile['authorization'] == 'needs_sponsorship':
                if value == 'existing_permit_required' or value == 'no_sponsorship':
                    return 'reject'
                if value != 'overseas_sponsorship_explicit':
                    pending = True
            elif value not in ('existing_permit_required', 'overseas_sponsorship_explicit'):
                pending = True
    return 'review' if pending else 'pass'


class EligibleStore:
    """Ephemeral adapter: underlying public records are never changed by fit."""
    def __init__(self, store, profile, reviews, fetch_text):
        self.store, self.profile, self.reviews = store, profile, reviews
        self.fetch_text = fetch_text
        self.data_dir = store.data_dir
        self._store = {}
        records = getattr(store, '_store', None)
        records = records if records is not None else store.load()
        # Only privately reviewed URLs can trigger a request; never crawl all jobs.
        # fetch_text is injected; production route uses bounded public HTTPS only.
        budget = 30
        for uid, record in sorted(records.items(), key=lambda p: -(p[1].get('score') or 0)):
            review = reviews.get(uid)
            if not review or budget <= 0:
                continue
            budget -= 1
            try:
                text = fetch_text(record.get('url', ''))
                verdict = evaluate(profile, review, url=record.get('url'), text=text)
            except Exception:
                continue  # source denial/parser failure never becomes a pass
            if verdict == 'pass':
                self._store[uid] = dict(record)

    def load(self):
        return self._store


class AutomaticEligibleStore:
    """Unattended, bounded source enrichment and high-confidence extraction.

    Accepted/rejected UID choices and exact facts remain ephemeral. Aggregate
    counters are private-return values, never written into public job records.
    """
    def __init__(self, store, profile, fetch_details, now=None, max_checks=30):
        from .automatic_requirements import automatic_review, unresolved_domains
        self.store, self.data_dir = store, store.data_dir
        self._store = {}
        self.stats = {'checked': 0, 'pass': 0, 'reject': 0, 'review': 0, 'unavailable': 0}
        records = getattr(store, '_store', None)
        records = records if records is not None else store.load()
        # Rank candidates using public job evidence only. Prioritize explicit
        # overseas hiring, not the old relevance score's permit noun bonus.
        def priority(pair):
            r = pair[1]
            text = (r.get('description') or '').lower()
            return (-int(any(w in text for w in ('sponsor', 'non-eu', 'international applicants', 'visa support'))),
                    -int(r.get('score') or 0), pair[0])
        ranked = sorted(records.items(), key=priority)
        # Rotate bounded checks by UTC slot instead of starving all but top30.
        moment = now or datetime.now(timezone.utc)
        offset = (int(moment.timestamp() // 3600) * max_checks) % max(len(ranked), 1)
        ranked = ranked[offset:] + ranked[:offset]
        for uid, record in ranked:
            if self.stats['checked'] >= max_checks:
                break
            if record.get('excluded'):
                continue
            self.stats['checked'] += 1
            # Known conflicts in saved text must not become uncertain sends if
            # the live detail source happens to be unavailable.
            early, _ = automatic_review(record, record.get('description') or '', now=now)
            if early == 'reject':
                self.stats['reject'] += 1
                continue
            try:
                text, structured = fetch_details(record)
                status, review = automatic_review(record, text, structured, now=now)
                if status == 'extracted':
                    status = evaluate(profile, review, url=record['url'], text=text, now=now)
            except Exception:
                self.stats['unavailable'] += 1
                # Source could not be checked: explicitly uncertain, never fit.
                result = dict(record)
                result['private_fit_status'] = 'review'
                result['private_fit_reason'] = 'تعذّر قراءة صفحة الإعلان؛ اللغة والرخص والخبرة والتعليم وتصريح العمل غير متحقق منها'
                self._store[uid] = result
                continue
            self.stats[status] += 1
            if status in ('pass', 'review'):
                result = dict(record)
                result['private_fit_status'] = status
                result['private_fit_reason'] = ('الشروط المقروءة متوافقة مبدئيًا، وليست ضمان قبول أو تأشيرة' if status == 'pass' else
                    unresolved_domains(text))
                self._store[uid] = result

    def load(self):
        return self._store

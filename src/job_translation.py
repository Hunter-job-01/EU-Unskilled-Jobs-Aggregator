"""Job field translation: title_en, title_ar, summary_ar (score-gated).

Runs AFTER scoring so quota is never spent on excluded or low-score jobs.
Order of honesty:
1. Glossary glosses (deterministic, term-level, negation-aware).
2. Machine translation for titles (gated by config; throttling-safe).
3. summary_ar is ALWAYS deterministic - composed from structured job
   fields with explicit "mentioned in the ad" qualifiers, so it can never
   hallucinate a benefit.

Validator rejects empty, identical-to-source, mojibake or prompt-leakage
outputs; failed translation leaves the original text and marks nothing.
"""
from __future__ import annotations

import re

from .glossary import gloss_lines

_AR_MENTION = {
    "no_exp_or_training_mention": "لا خبرة مطلوبة / تدريب",
    "housing_keyword_mention": "سكن",
    "permit_keyword_mention": "تصريح عمل",
    "language_flex_keyword_mention": "مرونة في اللغة",
    "employment_agency_source": "المصدر وكالة توظيف (حقيقة عن المصدر)",
    "agency_relocation_text_mention": "وكالة/إعادة توطين",
}
_VERIFY_NOTE = "مذكور في نص الإعلان وليس وعدًا مؤكدًا - يُتحقق منه مباشرة مع صاحب العمل."

# A permit mention is not automatically a benefit: the same keyword fires
# on "Gültige Arbeitserlaubnis" (a REQUIREMENT on the applicant) and on
# "Pomoc s vyřízením ... povolení" (offered assistance). Classify from the
# source phrase so the summary never presents a requirement as a perk.
_PERMIT_REQUIRED_RE = re.compile(
    r"(?i)(gültige\s+arbeitserlaubnis|gültige\s+arbeitsbewilligung"
    r"|(can'?t|cannot)\s+provide\s+(a\s+)?work\s+permit"
    r"|work\s+permit\s+(is\s+)?required|geen\s+(werk)?vergunning)")
_PERMIT_OFFERED_RE = re.compile(
    r"(?i)(pomoc\s+s\s+vyřízením|we\s+(arrange|provide|sponsor)[^.]{0,60}permit"
    r"|(visa|work\s+permit)\s+sponsorship\s+(available|provided|offered))")


def permit_mention_kind(description: str) -> str:
    """'required' | 'offered' | 'plain' for a permit keyword mention."""
    d = description or ""
    if _PERMIT_REQUIRED_RE.search(d):
        return "required"
    if _PERMIT_OFFERED_RE.search(d):
        return "offered"
    return "plain"


_AR_PERMIT_ITEM = {
    "required": "تصريح عمل (شرط مسبق على المتقدم كما ورد في الإعلان)",
    "offered": "مساعدة في التصريح/الإقامة (معروضة كما ورد في الإعلان)",
    "plain": "تصريح عمل",
}

_LEAK_PATTERNS = re.compile(r"(?i)as an ai|language model|translation:|ترجمة آلية تلقائية للنص التالي")
_MOJIBAKE = re.compile(r"[ÃÂÅ][\x80-\xbf]")


def valid_translation(original: str, translated: str) -> bool:
    """Reject empty, identical, leaking or corrupted outputs."""
    t = (translated or "").strip()
    if not t:
        return False
    if t.strip() == (original or "").strip():
        return False
    if _LEAK_PATTERNS.search(t) or _MOJIBAKE.search(t):
        return False
    return True


def compose_summary_ar(job) -> str:
    """Deterministic Arabic summary from STRUCTURED FACTS only.

    Only lines backed by an actual field are emitted: keyword mentions
    from signals (neutral - a mention can be a requirement, an offer or
    neither; the label never asserts benefit status), salary/shift text
    when the source stated it, location when known. The job title is never repackaged as "core tasks" - when
    there are no extracted facts, the honest result is an empty summary
    (the card then shows no Arabic block rather than a fake one).
    """
    parts = []
    mentions = []
    for sig in getattr(job, "signals", []):
        if sig not in _AR_MENTION:
            continue
        if sig == "permit_keyword_mention":
            mentions.append(_AR_PERMIT_ITEM[permit_mention_kind(getattr(job, "description", ""))])
        else:
            mentions.append(_AR_MENTION[sig])
    if mentions:
        parts.append("عبارات وردت في نص الإعلان: " + "؛ ".join(mentions))
    salary = getattr(job, "salary_text", "") or ""
    if salary:
        parts.append("الراتب/الدوام كما ورد: " + salary)
    location = getattr(job, "location", "") or ""
    if location:
        parts.append("الموقع: " + location)
    if not parts:
        return ""
    parts.append(_VERIFY_NOTE)
    return "\n".join(parts)


def annotate_glossary(job, lang: str) -> None:
    """Attach honest glossary glosses for the job's text (never upgrades)."""
    text = " ".join([getattr(job, "title", "") or "",
                     getattr(job, "description", "") or ""])
    lines = gloss_lines(text, lang)
    if lines:
        job.glossary = lines


def translate_title_mt(title: str, target: str, cfg: dict):
    """One title via the keyless web route; None on any failure.

    Callers gate this behind config and quota; a failure leaves the
    original title untouched upstream.
    """
    tcfg = cfg.get("translation", {}) or {}
    if not tcfg.get("enabled", False):
        return None
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        return None
    try:
        out = GoogleTranslator(source="auto", target=target).translate(title)
    except Exception:
        return None
    return out if valid_translation(title, out or "") else None

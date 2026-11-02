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
    "no_exp_or_training_mention": "يذكر الإعلان: لا خبرة مطلوبة / تدريب",
    "housing_keyword_mention": "يذكر الإعلان: سكن",
    "permit_keyword_mention": "يذكر الإعلان: تصريح عمل",
    "language_flex_keyword_mention": "يذكر الإعلان: مرونة في اللغة",
    "employment_agency_source": "المصدر وكالة توظيف (حقيقة عن المصدر)",
    "agency_relocation_text_mention": "يذكر الإعلان: وكالة/إعادة توطين",
}
_VERIFY_NOTE = "مذكور في نص الإعلان وليس وعدًا مؤكدًا - يُتحقق منه مباشرة مع صاحب العمل."

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
    """Deterministic Arabic summary from structured fields only."""
    parts = []
    title = getattr(job, "title_en", "") or getattr(job, "title", "")
    if title:
        parts.append("المهام الأساسية: {}".format(title))
    mentions = [_AR_MENTION[s] for s in getattr(job, "signals", []) if s in _AR_MENTION]
    if mentions:
        parts.append("المزايا المذكورة: " + "؛ ".join(mentions))
    salary = getattr(job, "salary_text", "") or ""
    if salary:
        parts.append("الراتب/الدوام: " + salary)
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

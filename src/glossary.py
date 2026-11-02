"""European recruitment glossary - a TRANSLATION AID, never an assertion.

Each entry maps a source-language recruitment term to honest English and
Arabic glosses. Glosses translate the term; they do NOT upgrade it into a
benefit claim. "Zakwaterowanie" glosses as "accommodation (as mentioned)"
- whether housing is actually provided, free, deducted or conditional
stays exactly what the posting text says. Negated occurrences
("keine Unterkunft") are detected and never glossed as the benefit.
"""
from __future__ import annotations

import re

# lang -> [(term, en_gloss, ar_gloss)]
GLOSSARY = {
    "de": [
        ("quereinsteiger", "career changer (no prior training in this field required)",
         "مُستجد في المجال (لا يُشترط تدريب سابق في نفس المجال)"),
        ("anlernkraft", "trainee worker (trained on the job)", "عامل متدرّب (تدريب أثناء العمل)"),
        ("kommissionierer", "order picker", "عامل تجهيز طلبيات"),
        ("unterkunft", "accommodation (as mentioned in the text)", "سكن (كما ورد في نص الإعلان)"),
    ],
    "pl": [
        ("do przyuczenia", "trainable position (on-the-job training)", "وظيفة مع تدريب أثناء العمل"),
        ("bez doświadczenia", "no experience required", "بدون خبرة مطلوبة"),
        ("komisjonowanie", "order picking", "تجهيز وجمع الطلبيات"),
        ("zakwaterowanie", "accommodation (as mentioned in the text)", "سكن (كما ورد في نص الإعلان)"),
        ("zezwolenie na pracę", "work permit (as mentioned in the text)", "تصريح عمل (كما ورد في نص الإعلان)"),
        ("oświadczenie", "employer's statement (Polish hiring procedure term)", "بيان صاحب العمل (مصطلح إجرائي بولندي)"),
        ("karta pobytu", "residence card", "بطاقة إقامة"),
    ],
    "cs": [
        ("zaškolíme", "we will train you", "سندرّبك أثناء العمل"),
        ("zaučíme", "we will train you", "سندرّبك أثناء العمل"),
        ("bez praxe", "no experience required", "بدون خبرة"),
        ("ubytování", "accommodation (as mentioned in the text)", "سكن (كما ورد في نص الإعلان)"),
        ("zaměstnanecká karta", "employee card (Czech work permit type)", "بطاقة الموظف (نوع تصريح عمل تشيكي)"),
    ],
    "sk": [
        ("zaučíme", "we will train you", "سندرّبك أثناء العمل"),
        ("bez praxe", "no experience required", "بدون خبرة"),
        ("ubytovanie", "accommodation (as mentioned in the text)", "سكن (كما ورد في نص الإعلان)"),
    ],
    "hu": [
        ("betanított munka", "job with on-the-job training", "عمل مع تدريب أثناء العمل"),
        ("szállás", "accommodation (as mentioned in the text)", "سكن (كما ورد في نص الإعلان)"),
    ],
    "nl": [
        ("zonder ervaring", "no experience required", "بدون خبرة"),
        ("inwerktraject", "onboarding and training track", "مسار تهيئة وتدريب"),
        ("orderpicker", "order picker", "عامل تجهيز طلبيات"),
        ("huisvesting", "accommodation (as mentioned in the text)", "سكن (كما ورد في نص الإعلان)"),
    ],
    "ro": [
        ("fără experiență", "no experience required", "بدون خبرة"),
        ("cazare", "accommodation (as mentioned in the text)", "سكن (كما ورد في نص الإعلان)"),
    ],
    "lt": [
        ("be patirties", "no experience required", "بدون خبرة"),
        ("apmokome", "we provide training", "نوفّر تدريبًا"),
    ],
}

# Negation words per language; a term preceded by one is NOT the benefit.
_NEGATIONS = [
    "keine", "kein", "keinen", "ohne", "nicht",  # de
    "brak", "nie",  # pl
    "bez", "ne",  # cs/sk (careful: "bez praxe" is a term itself - see guard)
    "nem", "nincs",  # hu
    "geen", "niet",  # nl
    "fără", "nu",  # ro
    "no", "not",  # en
]

# Terms that legitimately START with a negation word (part of the term).
_SELF_NEGATING = {"bez doświadczenia", "bez praxe", "be patirties", "fără experiență", "zonder ervaring"}


def glossary_hits(text: str, lang: str) -> list:
    """Return [(term, en_gloss, ar_gloss, negated)] for terms present.

    A hit is negated when a negation word immediately precedes it and the
    term is not self-negating; negated hits are reported so callers can
    keep the negation, never glossed as the benefit.
    """
    if not text or lang not in GLOSSARY:
        return []
    low = text.lower()
    hits = []
    for term, en, ar in GLOSSARY[lang]:
        for m in re.finditer(re.escape(term), low):
            start = m.start()
            prefix = low[max(0, start - 20):start].strip()
            negated = False
            if term not in _SELF_NEGATING:
                first = prefix.split()[-1] if prefix.split() else ""
                negated = first in _NEGATIONS
            hits.append((term, en, ar, negated))
    return hits


def gloss_lines(text: str, lang: str) -> list:
    """Honest bilingual gloss lines for non-negated hits only."""
    lines = []
    seen = set()
    for term, en, ar, negated in glossary_hits(text, lang):
        if negated or term in seen:
            continue
        seen.add(term)
        lines.append({"term": term, "en": en, "ar": ar})
    return lines

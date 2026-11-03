"""Offline title translation via argos-translate with a recruitment
glossary pre-pass. Presentation only, never eligibility.

Why the pre-pass exists: measured 2026-11-03 on real backlog titles,
argos pl->en alone returns "Magazynier -> Storage" and
"Komisjonowanie -> Commissioning" - both wrong for recruitment. The
domain terms below are replaced with canonical English BEFORE the
model runs, so the model never sees them. Terms map to honest
recruitment English only; they never add benefits or claims.

Honesty rules (same as the rest of the pipeline):
- A failed/empty/identical translation leaves the field EMPTY, never a
  silent copy of the source title.
- title_ar comes from the pivot source -> en -> ar; a failed pivot
  leaves title_ar empty even when title_en succeeded.
"""
from __future__ import annotations

# lang -> [(source_term_folded, canonical_english)]. Matched as whole
# folded phrases, longest first.
TITLE_PREPASS = {
    "pl": [
        ("magazynier", "warehouse worker"),
        ("komisjonowanie", "order picking"),
        ("pracownik produkcji", "production worker"),
        ("pracownik magazynu", "warehouse worker"),
        ("pakowacz", "packer"),
        ("sprzataczka", "cleaner"),
        ("pracownik fizyczny", "general labourer"),
        ("operator wozka widlowego", "forklift operator"),
        ("pomoc kuchenna", "kitchen helper"),
    ],
    "de": [
        ("lagerhelfer", "warehouse helper"),
        ("produktionshelfer", "production helper"),
        ("kommissionierer", "order picker"),
        ("quereinsteiger", "career changer"),
        ("maschinenbediener", "machine operator"),
    ],
    "cs": [
        ("skladnik", "warehouse worker"),
        ("vyrobn i pracovnik", "production worker"),
        ("ubytovani", "accommodation"),
    ],
    "nl": [
        ("magazijnmedewerker", "warehouse worker"),
        ("productiemedewerker", "production worker"),
        ("orderpicker", "order picker"),
    ],
    "lt": [
        ("sandelio darbuotojas", "warehouse worker"),
        ("pakuotojas", "packer"),
        ("mesos", "meat"),
    ],
    # Greek terms stay in Greek script: fold_text lowercases but does
    # not transliterate.
    "el": [
        ("εργατης γεωργοκτηνοτροφιας", "agricultural worker"),
        ("εργατης γεωργιας", "farm worker"),
    ],
}

# Controlled Arabic title vocabulary. Every value is reviewed manual
# Arabic for the canonical English term; used instead of the raw
# en->ar model output whenever the translated English title contains
# the term (longest match first). Measured 2026-11-03: raw en->ar on
# short titles is unreliable ("warehouse worker" -> "المستودعات"),
# so the reviewed map wins whenever it covers the title.
TITLE_AR_VOCAB = {
    "warehouse worker": "عامل مستودع",
    "warehouse helper": "مساعد مستودع",
    "order picker": "عامل تجميع الطلبات",
    "order picking": "تجميع الطلبات",
    "production worker": "عامل إنتاج",
    "production helper": "مساعد إنتاج",
    "forklift operator": "مشغل رافعة شوكية",
    "kitchen helper": "مساعد مطبخ",
    "general labourer": "عامل يدوي عام",
    "machine operator": "مشغل آلة",
    "agricultural worker": "عامل زراعي",
    "meat processing worker": "عامل تصنيع لحوم",
    "farm worker": "عامل مزرعة",
    "packer": "عامل تعبئة",
    "cleaner": "عامل تنظيف",
    "career changer": "متحول مهني (بدون خبرة سابقة)",
    "accommodation": "سكن",
}

_PAIRS = [("pl","en"),("de","en"),("cs","en"),("nl","en"),("sv","en"),
          ("es","en"),("it","en"),("fr","en"),("pt","en"),("ro","en"),
          ("hu","en"),("lt","en"),("lv","en"),("et","en"),("sk","en"),
          ("bg","en"),("el","en"),("fi","en"),("da","en"),("en","ar")]


def ensure_packages(langs=None, log=print):
    """Install missing argos packages from the index. Network use is
    bounded to the argos package server; models are cached locally.
    With ``langs`` given, installs only source->en pairs for those
    languages plus the en->ar pivot."""
    from argostranslate import package
    wanted = set(_PAIRS)
    if langs is not None:
        wanted = {pair for pair in _PAIRS
                  if pair[0] in set(langs) or pair == ("en", "ar")}
    package.update_package_index()
    available = {(p.from_code, p.to_code): p for p in package.get_available_packages()}
    installed = {(t.from_code, t.to_code) for t in package.get_installed_packages()}
    missing = [pair for pair in sorted(wanted) if pair not in installed and pair in available]
    for pair in missing:
        log("argos: installing %s->%s" % pair)
        package.install_from_path(available[pair].download())
    return sorted(wanted - set(missing) - (wanted - set(available)))


def _fold(s: str) -> str:
    from src.keywords import fold_text
    return fold_text(s or "")


def prepass_title(title: str, lang: str):
    """Replace domain terms with canonical English (longest match first).
    Returns the possibly-partially-English title."""
    out = title or ""
    for term, canonical in sorted(TITLE_PREPASS.get(lang, []),
                                  key=lambda kv: -len(kv[0])):
        # whole-phrase, case/diacritic-insensitive via folded search
        import re
        folded_out = _fold(out)
        idx = folded_out.find(term)
        while idx >= 0:
            out = out[:idx] + canonical + out[idx + len(term):]
            folded_out = _fold(out)
            idx = folded_out.find(term)
    return out


# Wrong-output patterns measured in the first local batch (2026-11-03):
# model runaways, nonwords and mistranslations. The gate rejects, never
# publishes; a rejected title stays EMPTY for manual review.
_EN_DENY = (
    "order complex", "carist", "shop commissioner", "manual f/h",
    "shopkeeper", "tollers", "no samb", "no sampling",
)
_RUNAWAY = __import__("re").compile(r"(.)\1{5,}")

# Qualifier lexicon (folded): when the source carries one of these, the
# English output must carry its canonical equivalent - a translation
# that drops a licence, seniority or language requirement is unfaithful
# even when the role words translate correctly.
_QUALIFIERS = [
    (("fuhrerschein", "ridicak", "ridicakem", "prawo jazdy", "rijbewijs",
      "korkort", "permis de conduire", "patente"), ("driving licence", "driving license", "driver's license", "license")),
    (("junior",), ("junior",)),
    (("senior",), ("senior",)),
    (("englisch", "english", "anglais", "angielski", "engels", "engelska"),
     ("english",)),
    (("deutsch", "german", "allemand", "niemiecki", "duits", "tyska"),
     ("german",)),
    (("teilzeit",), ("part-time", "part time")),
    (("vollzeit",), ("full-time", "full time")),
]


def faithful_english(title: str, en: str) -> bool:
    """Auto-reject unfaithful model English before publication."""
    if not en:
        return False
    low = en.lower()
    if any(d in low for d in _EN_DENY):
        return False
    if _RUNAWAY.search(low):
        return False
    # Runaway length: a short title must not balloon (hallucination sign).
    if len(title) < 30 and len(en) > 3 * len(title) + 10:
        return False
    folded_src = _fold(title)
    folded_en = _fold(en)
    for src_forms, en_forms in _QUALIFIERS:
        if any(f in folded_src for f in src_forms):
            if not any(f in folded_en for f in en_forms):
                return False
    return True


def translate_title_argos(title: str, source_lang: str):
    """Translate one title source->en->ar. Returns (title_en, title_ar);
    either may be '' on honest failure. Never copies the source."""
    if not title or not title.strip():
        return "", ""
    en = ""
    try:
        if source_lang == "en":
            en = title.strip()
        else:
            from argostranslate import translate
            prepped = prepass_title(title, source_lang)
            en = (translate.translate(prepped, source_lang, "en") or "").strip()
    except Exception:
        return "", ""
    # Identical-output rejection applies to the model round-trip only;
    # an English source title legitimately passes through unchanged.
    if not en or (source_lang != "en" and _fold(en) == _fold(title)):
        return "", ""
    if source_lang != "en" and not faithful_english(title, en):
        return "", ""
    # Quality gate (owner correction 2026-11-03): the raw en->ar model is
    # measured wrong on short titles ("warehouse worker" -> "المستودعات",
    # "pre-sorting force" -> "القوة غير المتفرغة") and unreviewed output
    # must NOT be published. Arabic comes from the reviewed TITLE_AR_VOCAB
    # only; anything else stays EMPTY for manual review, never a
    # labelled-but-wrong fill.
    ar = arabic_for_english_title(en)
    return en, ar


def arabic_for_english_title(title_en: str) -> str:
    """Reviewed Arabic for a translated English title, or '' when no
    controlled term matches. Longest canonical term wins; the match is
    folded and case/diacritic-insensitive like the prepass."""
    folded = _fold(title_en)
    for term, arabic in sorted(TITLE_AR_VOCAB.items(), key=lambda kv: -len(kv[0])):
        if term in folded:
            return arabic
    return ""

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
        ("magazynierka", "warehouse worker"),
        ("magazynier", "warehouse worker"),
        ("pracownik magazynowy", "warehouse worker"),
        ("kierowca", "driver"),
        ("mlodszy", "junior"),
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
        ("produktionsmitarbeiter", "production worker"),
        ("lagermitarbeiter", "warehouse worker"),
        ("transportarbeiter", "transport worker"),
        ("zusteller", "delivery driver"),
        ("lagerhelfer", "warehouse helper"),
        ("produktionshelfer", "production helper"),
        ("kommissionierer", "order picker"),
        ("quereinsteiger", "career changer"),
        ("maschinenbediener", "machine operator"),
    ],
    "cs": [
        ("skladnik", "warehouse worker"),
        ("vyrobni pracovnik", "production worker"),
        ("operator vyroby", "production operator"),
        ("kompletar objednavek", "order picker"),
        ("kompletar", "order picker"),
        ("kompletace", "order picking"),
        ("manipulacni", "warehouse handler"),
        ("obsluha", "operator"),
        ("ridic", "driver"),
        ("hpp", "full-time"),
        ("ubytovani", "accommodation"),
    ],
    "nl": [
        ("ploegen", "shifts"),
        ("logistiek medewerker", "logistics worker"),
        ("magazijnmedewerker", "warehouse worker"),
        ("magazijnier", "warehouse worker"),
        ("productiemedewerker", "production worker"),
        ("orderpicker", "order picker"),
    ],
    "fr": [
        ("preparateur de commandes", "order picker"),
        ("manutentionnaire", "warehouse handler"),
        ("magasiniere", "warehouse worker"),
        ("magasinier", "warehouse worker"),
        ("cariste", "forklift operator"),
        ("livreur", "delivery driver"),
        ("vendeur", "salesperson"),
    ],
    "sv": [
        ("produktionsarbetare", "production worker"),
        ("terminalmedarbetare", "terminal worker"),
        ("terminalarbetare", "terminal worker"),
        ("lagermedarbetare", "warehouse worker"),
        ("lagerarbetare", "warehouse worker"),
        ("truckforare", "forklift operator"),
    ],
    "it": [
        ("operaio di produzione", "production worker"),
    ],
    "fi": [
        ("siivooja", "cleaner"),
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
    "logistics worker": "عامل لوجستيات",
    "delivery driver": "سائق توصيل",
    "warehouse handler": "عامل مناولة بضائع",
    "transport worker": "عامل نقل",
    "terminal worker": "عامل محطة فرز",
    "production operator": "مشغل إنتاج",
    "driver": "سائق",
    "salesperson": "بائع",
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
    # Doubled words (adjacent or and-joined): measured garble class
    # ('Social and Social Accompagnateur').
    if __import__("re").search(r"\b(\w{3,})\b(?:\s+(?:and|und|&|,)\s+|\s+)\1\b", low):
        return False
    # Model-artifact symbols never appear in real job titles.
    if any(c in en for c in "♪♫★☆●■"):
        return False
    # Repeated-token runaway (measured 2026-11-03: 'CZK 124; ... 124; ...
    # 124;'): any non-function token occurring 3+ times is a generation
    # loop, not a real title.
    import collections as _c
    _toks = __import__("re").findall(r"[a-z0-9]+", low)
    _func = {"the", "and", "for", "a", "de", "und", "en", "of", "m", "w", "d"}
    if any(n >= 3 for t, n in _c.Counter(_toks).items() if t not in _func):
        return False
    folded_src = _fold(title)
    folded_en = _fold(en)
    for src_forms, en_forms in _QUALIFIERS:
        if any(f in folded_src for f in src_forms):
            if not any(f in folded_en for f in en_forms):
                return False
    return True


import json as _json
import os as _os

# Languages a high-confidence signal can override the country mapping
# with. Signals are deliberately strong only (script, gender markers,
# unique diacritics, function-word majority): anything weaker keeps the
# country language, and a missing model still fails honest-empty.
_SCRIPT_LANGS = [("\u0370-\u03ff", "el"), ("\u0400-\u04ff", "bg")]
_MARKERS = [("h/f", "fr"), ("f/h", "fr"), ("(m/w/d)", "de"),
            ("(w/m/d)", "de"), ("k/m", "pl"), ("(m/k)", "pl"),
            ("(m/z)", "cs")]
_UNIQUE_CHARS = [("łąę", "pl"), ("ěůřť", "cs"), ("å", "sv"),
                 ("ß", "de"), ("ñ¿", "es"), ("ãõ", "pt")]
_FUNC = {
    "fr": {"le", "la", "les", "des", "une", "avec", "pour", "sur", "aux"},
    "de": {"der", "die", "das", "und", "mit", "fur", "für", "im", "am"},
    "nl": {"het", "een", "voor", "met", "als", "aan", "bij"},
    "pl": {"oraz", "dla", "przy", "przy", "lub", "przez"},
    "cs": {"pro", "nebo", "nad", "pod", "pri", "také"},
    "sv": {"och", "till", "med", "som", "att", "fran"},
    "it": {"del", "della", "per", "con", "alla", "delle"},
    "es": {"los", "las", "una", "con", "para", "por", "del"},
    "pt": {"das", "dos", "uma", "com", "para", "por"},
}


def guess_title_lang(title: str):
    """High-confidence language guess for a title, or None.

    Only strong signals count: non-Latin script, explicit gender markers,
    unique diacritics, or a function-word vote with a clear margin. None
    means 'no confident signal - keep the country mapping'."""
    import re as _re
    low = (" " + title.lower() + " ") if title else ""
    votes = {}
    def bump(lang, w=2):
        if lang:
            votes[lang] = votes.get(lang, 0) + w
    for rng, lang in _SCRIPT_LANGS:
        if _re.search("[" + rng + "]", title or ""):
            return lang  # script alone is decisive
    for marker, lang in _MARKERS:
        if marker in low:
            bump(lang, 3)
    for chars, lang in _UNIQUE_CHARS:
        if any(c in low for c in chars):
            bump(lang, 2)
    words = set(_re.findall(r"[a-zà-ÿ]+", low))
    for lang, fws in _FUNC.items():
        n = len(words & fws)
        if n >= 2:
            bump(lang, n)
    if not votes:
        return None
    best, score = max(votes.items(), key=lambda kv: kv[1])
    others = [v for k, v in votes.items() if k != best]
    if score >= 3 and (not others or score >= max(others) + 2):
        return best
    return None


_REVIEW_PATH = _os.path.join(_os.path.dirname(__file__), "..",
                             "data", "translation_review.json")


def _rejected_titles():
    """Folded titles whose model translation was reviewed and rejected;
    the translator must fail honest-empty for them, forever."""
    try:
        with open(_REVIEW_PATH) as fh:
            return set(_json.load(fh).get("rejected_titles", []))
    except Exception:
        return set()


_EN_EVIDENCE_HINTS = {
    "warehouse", "worker", "production", "operator", "packer", "picker",
    "cleaner", "helper", "driver", "kitchen", "porter", "operative",
    "assistant", "order", "farm", "general", "manager", "stock", "store",
}
_EN_FUNCTION_WORDS = {"the", "and", "for", "with", "in", "to", "of", "a"}


def has_english_evidence(title: str) -> bool:
    """Strong, cheap evidence a title is actually English.

    ASCII-only plus either a known English role/content word or an
    English-function-word majority. Weak or missing evidence is a fail -
    the caller then leaves the field empty rather than stamping the
    untranslated source as English.
    """
    if not title:
        return False
    try:
        title.encode("ascii")
    except UnicodeEncodeError:
        return False
    words = __import__("re").findall(r"[a-z]+", title.lower())
    if not words:
        return False
    if set(words) & _EN_EVIDENCE_HINTS:
        return True
    func = sum(1 for w in words if w in _EN_FUNCTION_WORDS)
    return func >= max(1, len(words) // 2)


def translate_title_argos(title: str, source_lang: str):
    """Translate one title source->en->ar. Returns (title_en, title_ar);
    either may be '' on honest failure. Never copies the source."""
    if not title or not title.strip():
        return "", ""
    if _fold(title) in _rejected_titles():
        return "", ""
    # Root-cause guard: the title's own language beats the country's
    # language when a high-confidence signal says they differ (a French
    # ad in an ES feed must not go through the es->en model).
    detected = guess_title_lang(title)
    if detected and detected != source_lang:
        source_lang = detected
    en = ""
    try:
        if source_lang == "en":
            # Root fix (2026-11-03): a country->en mapping (IE/MT/...) is not
            # evidence the title IS English - a Danish ad in an MT feed was
            # passed through untranslated. Require actual English evidence;
            # without it the honest result is empty, not a source copy.
            if not has_english_evidence(title):
                return "", ""
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


# Gender/employment-form markers and digits never block full coverage;
# they carry no role meaning of their own. Locations and any other word
# DO count: an uncovered word makes the gloss explicitly partial.
_IGNORABLE = __import__("re").compile(
    r"^(m/w/d|w/m/d|m/f/d|h/f|f/h|k/m|m/k|m/z|m/f/x|d/m/w|\d+.*|.)$")

# Employment-form vocab: HPP (Czech full-time) and shift forms are
# material qualifiers, so they have their own reviewed Arabic and count
# toward coverage instead of silently disappearing.
TITLE_AR_VOCAB.update({
    # Added 2026-11-03 (batch 13, reviewed manual Arabic). "delivery
    # person" is deliberately NOT "driver": German Zusteller titles do not
    # state a driving role.
    "delivery person": "عامل توصيل",
    "kitchen employee": "عامل مطبخ",
    "warehouse employee": "عامل مستودع",
    "welder": "لحّام",
    "carpenter": "نجّار",
    "packing operator": "مشغل تعبئة",
    # Batch 15 (2026-11-03). Sources: kitchen porter = TERMIUM kitchen
    # helper/worker, rolecatcher kitchen porter; electronics technician =
    # ar.wikipedia / arabdict; machinist = PONS en-ar; medical secretary =
    # Saudi SSCO 3344; general operative = Randstad UK job profile.
    "kitchen porter": "مساعد مطبخ (غسل الأواني والتنظيف)",
    "kitchen assistant": "مساعد مطبخ",
    "general operative": "عامل عام",
    "warehouse operative": "عامل مستودع",
    "warehouse team leader": "قائد فريق مستودع",
    "team leader": "قائد فريق",
    "packaging specialist": "أخصائي تعبئة وتغليف",
    "packaging operator": "مشغل تعبئة وتغليف",
    "food packaging worker": "عامل تعبئة وتغليف أغذية",
    "electronics technician": "فني إلكترونيات",
    "metalworker": "عامل معادن",
    "machinist": "مشغل ماكينات",
    "medical secretary": "سكرتير طبي",
    "analytical chemist": "كيميائي تحليلي",
    "service employee": "موظف خدمة",
    "full-time": "دوام كامل",
    "part-time": "دوام جزئي",
    "night shift": "وردية ليلية",
})
_PARTIAL_MARK = " (ترجمة جزئية للمسمى - راجع العنوان الأصلي)"

# Context-sensitive roles: their Arabic is one possible reading, so a
# gloss containing any of them is NEVER declared full on word coverage
# alone. Only independent context could upgrade them; absent that, they
# stay explicitly partial.
_AMBIGUOUS_AR = frozenset({"عامل محطة فرز", "مشغل", "عامل مناولة بضائع"})


def arabic_gloss(title_en: str):
    """Reviewed Arabic for a translated English title.

    Returns (gloss, is_full). Every matched canonical term contributes its
    Arabic (in title order, joined with ' / ') so compound roles keep all
    their parts. is_full is True only when every alphabetic word of the
    folded title is covered by a matched term or an ignorable marker -
    otherwise the gloss is explicitly PARTIAL and the caller must mark it,
    never present it as a full translation. Empty gloss when nothing
    matches."""
    folded = _fold(title_en)
    terms = sorted(TITLE_AR_VOCAB.items(), key=lambda kv: -len(kv[0]))
    hits = []  # (start, end, arabic)
    for term, arabic in terms:
        start = folded.find(term)
        while start >= 0:
            end = start + len(term)
            if not any(s < end and start < e for s, e, _ in hits):
                hits.append((start, end, arabic))
            start = folded.find(term, start + 1)
    if not hits:
        return "", False
    hits.sort()
    gloss = " / ".join(dict.fromkeys(a for _, _, a in hits))
    words = [(m.start(), m.group()) for m in
             __import__("re").finditer(r"[a-z]+", folded)]
    def covered(pos, word):
        return any(s <= pos and pos + len(word) <= e for s, e, _ in hits)
    full = all(covered(pos, w) or _IGNORABLE.match(w)
               for pos, w in words) if words else False
    if full and any(a in _AMBIGUOUS_AR for _, _, a in hits):
        full = False
    return gloss, full


def arabic_for_english_title(title_en: str) -> str:
    """Reviewed Arabic string for callers that only need the gloss
    (partial glosses carry the explicit partial marker)."""
    gloss, full = arabic_gloss(title_en)
    if not gloss:
        return ""
    return gloss if full else gloss + _PARTIAL_MARK

"""Multilingual keyword and signal matrix.

IMPORTANT - what a match means: a keyword hit is *relevance evidence* for
manual review. A posting that mentions accommodation, an agency or a work
permit does NOT prove the employer sponsors non-EU citizens, and it says
nothing by itself about whether a candidate in Egypt will be accepted.
Signals are recorded so a person can verify the vacancy before applying.
"""
from __future__ import annotations

import re
import unicodedata

# Characters NFKD does not decompose; map them before stripping marks.
_EXTRA_FOLD = str.maketrans(
    {
        "\u0142": "l",  # ł
        "\u00f8": "o",  # ø
        "\u00e6": "ae",  # æ
        "\u0153": "oe",  # œ
        "\u00f0": "d",  # ð
        "\u00fe": "th",  # þ
        "\u0111": "d",  # đ
        "\u0127": "h",  # ħ
        "\u0131": "i",  # ı
        "\u00df": "ss",  # ß
    }
)


def fold_text(text: str) -> str:
    """Lowercase and strip diacritics: 'Skladník' -> 'skladnik'."""
    if not text:
        return ""
    text = text.lower().translate(_EXTRA_FOLD)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _fold(phrase: str) -> str:
    return re.sub(r"\s+", " ", fold_text(phrase)).strip()


_TOKEN_RX = re.compile(r"[^\W\d_]+", re.UNICODE)


def _tokens(text: str) -> list:
    """Word tokens (unicode letters) of already-folded text."""
    return _TOKEN_RX.findall(text)


class KeywordMatrix:
    """Compiled multilingual matcher built from config['keywords']."""

    def __init__(self, matrix_cfg: dict):
        roles = matrix_cfg.get("roles", {}) or {}
        # lang -> list[(folded, original)]
        self.roles = {
            lang: [(_fold(p), p) for p in phrases if isinstance(p, str) and p.strip()]
            for lang, phrases in roles.items()
        }
        signals = matrix_cfg.get("signals", {}) or {}
        # category -> list[(folded, original)] across ALL languages: postings
        # mix languages, so matching is deliberately cross-lingual.
        self.signals = {}
        for category, per_lang in signals.items():
            entries = []
            for phrases in (per_lang or {}).values():
                entries.extend(
                    (_fold(p), p) for p in phrases if isinstance(p, str) and p.strip()
                )
            self.signals[category] = entries
        self.negative = [
            (_fold(p), p)
            for p in (matrix_cfg.get("negative_roles") or [])
            if isinstance(p, str) and p.strip()
        ]
        # Disqualifiers are regexes; fold them too and match on folded text.
        self.disqualifiers = []
        for pattern in matrix_cfg.get("disqualifiers") or []:
            if isinstance(pattern, str) and pattern.strip():
                self.disqualifiers.append(re.compile(_fold(pattern)))
        # Negation cues from ALL configured languages as one word-boundary
        # regex: postings mix languages, so cues are matched cross-lingually.
        # The (?![\s]*-) lookahead excludes hyphenated uses such as "non-EU",
        # which is not a negation of what follows.
        def _string_set(section):
            out = set()
            for lang, entries in (matrix_cfg.get(section) or {}).items():
                for entry in entries or []:
                    if not isinstance(entry, str) or not entry.strip():
                        raise ValueError(
                            "keywords.{}.{}: non-string entry {!r} - quote YAML "
                            "booleans such as \"no\" in config.yaml".format(section, lang, entry))
                    out.add(_fold(entry))
            return out

        cues = _string_set("negation_cues")
        self._req_markers = _string_set("requirement_markers")
        self._avail_markers = _string_set("availability_markers")
        self._neg_rx = (
            re.compile(r"\b(" + "|".join(sorted(map(re.escape, cues), key=len, reverse=True)) + r")\b(?!\s*-)")
            if cues else None
        )

    # -- role keywords -------------------------------------------------
    def match_roles(self, text: str, languages=None) -> list:
        """Distinct role keywords found, e.g. ['Magazynier', 'packer']."""
        folded = fold_text(text)
        langs = list(languages) if languages else list(self.roles.keys())
        if "en" not in langs:
            langs.append("en")  # English postings exist in every market
        found = []
        for lang in langs:
            for folded_kw, original in self.roles.get(lang, []):
                if folded_kw and folded_kw in folded and original not in found:
                    found.append(original)
        return found

    # -- positive signals ----------------------------------------------
    #: Categories whose keywords are positive *because* they negate a
    #: requirement ("no language required", "no experience required",
    #: "ohne Erfahrung", "bez doswiadczenia") - never negation-suppressed.
    #: Every "no experience / training provided" phrase in the Phase 4
    #: no_exp_or_training signal ("no experience", "without experience",
    #: "bez doswiadczenia", "ohne erfahrung", "keine vorkenntnisse",
    #: "bez praxe", "zonder ervaring", "sin experiencia", "senza
    #: esperienza", "sans experience", "sem experiencia", "tapasztalat
    #: nelkul", "fara experienta", "be patirties", "bez pieredzes",
    #: "ilma kogemuseta", "utan erfarenhet", "ingen erfaring", "ei
    #: kokemusta", "bez iskustva", "brez izkusenj", "без опит", "χωρις
    #: προϋπηρεσια") is protected this way.
    NEGATION_EXEMPT_CATEGORIES = frozenset(
        {"language_flex", "no_exp_or_training"})

    #: Max words between a prefix cue and the keyword it can govern.
    PREFIX_SCOPE_WORDS = 6
    #: Max words after a keyword where a suffix cue can still deny it.
    SUFFIX_CUE_WORDS = 3
    #: Max words after a suffix cue that can carry the availability verb.
    SUFFIX_VERB_WORDS = 3

    def _is_negated(self, folded: str, start: int, end: int, folded_kw: str) -> bool:
        """Sentence-scoped negation heuristics (finite, not full NLP).

        Prefix denial: a cue within PREFIX_SCOPE_WORDS before the match
        governs it ("no accommodation", "we do not offer visa sponsorship",
        denial lists like "no accommodation, visa support or relocation").
        The scope ends when a requirement marker follows the cue
        ("no experience required, housing provided" keeps housing).
        Suffix denial: a cue within SUFFIX_CUE_WORDS after the match plus an
        availability marker within SUFFIX_VERB_WORDS after the cue
        ("sponsorship not available for this role"), unless the keyword was
        already affirmed first ("housing provided, relocation not
        available" keeps housing). Keywords containing a cue themselves
        ("no language required") are exempt, as are hyphenated uses such
        as "non-EU". Self-affirming keywords ("housing provided") are exempt from
        suffix denial but still suppressible by a prefix cue ("no housing provided").
        """
        if self._neg_rx is None:
            return False
        if self._neg_rx.search(folded_kw):
            return False
        self_affirmed = any(tok in self._avail_markers for tok in _tokens(folded_kw))
        sent_start = max(folded.rfind(ch, 0, start) for ch in ".!?\n") + 1
        bounds = [i for i in (folded.find(ch, end) for ch in ".!?\n") if i != -1]
        sent_end = min(bounds) if bounds else len(folded)

        prefix = folded[sent_start:start]
        for m in self._neg_rx.finditer(prefix):
            between = _tokens(prefix[m.end():])
            if len(between) > self.PREFIX_SCOPE_WORDS:
                continue  # too far away to govern this keyword
            if any(tok in self._req_markers for tok in between[:4]):
                continue  # negates a requirement, not this keyword
            return True

        suffix = folded[end:sent_end]
        for m in (() if self_affirmed else self._neg_rx.finditer(suffix)):
            before_cue = _tokens(suffix[:m.start()])
            if len(before_cue) > self.SUFFIX_CUE_WORDS:
                break
            if any(tok in self._avail_markers for tok in before_cue):
                continue  # keyword already affirmed before the cue
            after_cue = _tokens(suffix[m.end():])
            if any(tok in self._avail_markers for tok in after_cue[:self.SUFFIX_VERB_WORDS]):
                return True
        return False

    def match_signals(self, text: str) -> dict:
        """category -> list of matched keyword originals (negation-aware)."""
        folded = fold_text(text)
        out = {}
        for category, entries in self.signals.items():
            hits = []
            for f, orig in entries:
                if not f:
                    continue
                idx = folded.find(f)
                while idx != -1:
                    if category in self.NEGATION_EXEMPT_CATEGORIES or not self._is_negated(
                            folded, idx, idx + len(f), f):
                        hits.append(orig)
                        break
                    idx = folded.find(f, idx + 1)
            if hits:
                out[category] = sorted(set(hits))
        return out

    # -- negative / disqualifiers ---------------------------------------
    def match_negative(self, text: str) -> list:
        folded = fold_text(text)
        return sorted({orig for f, orig in self.negative if f and f in folded})

    def match_disqualifiers(self, text: str) -> list:
        folded = fold_text(text)
        return [rx.pattern for rx in self.disqualifiers if rx.search(folded)]

    # -- early title filter (adapter-time, before deep processing) ----
    def early_title_exclusion(self, title: str) -> str:
        """Cheap title-only verdict for scrapers, applied BEFORE detail
        processing/translation. Mirrors the scorer's hard rules on the
        title alone: a disqualifier match, or skilled/senior vocabulary
        with no unskilled role keyword. Returns the reason or None."""
        if not title or not title.strip():
            return None
        if self.match_disqualifiers(title):
            return "disqualifier in title"
        if self.match_negative(title) and not self.match_roles(title):
            return "skilled/senior title without unskilled keywords"
        return None

    # -- per-country search keywords (used by scrapers) -----------------
    def search_keywords(self, languages, cap: int) -> list:
        """Ordered unique role keywords for querying a country's sources,
        native languages first, English last."""
        out = []
        for lang in list(languages or []) + ["en"]:
            for _, original in self.roles.get(lang, []):
                if original not in out:
                    out.append(original)
        return out[:cap]

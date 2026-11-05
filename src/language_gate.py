"""v6 stage C (L2): strict local-language requirement gate.

Excludes a posting only when ONE sentence contains all three: a local
language name, a proficiency level term and a requirement verb - and no
negation cue governs the requirement. Deliberately conservative:

- "keine Deutschkenntnisse erforderlich", "no German required" or a
  negated level ("fluent German not required") never exclude.
- Offered training ("wir bieten Deutschkurse") carries no requirement
  verb and never matches.
- A bare level with no language ("C1 certificate") does not match.

This replaces the old raw `c1/c2 ... required` disqualifier regex, which
also fired on negated mentions ("kein C1 erforderlich").
"""
from __future__ import annotations

import re
from typing import Optional

from .keywords import fold_text

_SENT_SPLIT = re.compile(r"[.!?\n]")
_WORD = re.compile(r"[a-z0-9äöüßąćęłńóśźżěščřžďťňáéíóúâêîôûç'-]+")
# Negation may sit up to this many words before the requirement verb.
_NEG_WINDOW_WORDS = 6


def _compile(terms):
    return [re.compile(r"\b{}\b".format(re.escape(t))) for t in terms]


class LanguageGate:
    def __init__(self, cfg: dict):
        cfg = cfg or {}
        self.languages = {
            name: _compile(variants)
            for name, variants in (cfg.get("languages") or {}).items()
        }
        self.levels = _compile(cfg.get("levels") or [])
        self.verbs = _compile(cfg.get("requirement_verbs") or [])
        self.negations = set(cfg.get("negation_cues") or [])

    def strict_requirement(self, text: str) -> Optional[str]:
        """Return the required language name when the text states a strict
        local-language requirement, else None."""
        if not text or not self.languages or not self.levels or not self.verbs:
            return None
        folded = fold_text(text)
        for sent in _SENT_SPLIT.split(folded):
            if not any(rx.search(sent) for rx in self.levels):
                continue
            verb_hits = [(m.start(), rx) for rx in self.verbs
                         for m in rx.finditer(sent)]
            if not verb_hits:
                continue
            for lang, variants in self.languages.items():
                if not any(rx.search(sent) for rx in variants):
                    continue
                if self._requirement_negated(sent, verb_hits):
                    continue
                return lang
        return None

    def _requirement_negated(self, sent: str, verb_hits) -> bool:
        if not self.negations:
            return False
        tokens = [(m.group(0), m.start()) for m in _WORD.finditer(sent)]
        for verb_pos, _rx in verb_hits:
            before = [w for w, p in tokens if p < verb_pos]
            if any(w in self.negations for w in before[-_NEG_WINDOW_WORDS:]):
                return True
        return False

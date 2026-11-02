"""Conservative automatic extraction from complete source details.

Positive extraction supports explicit English requirements only. Other languages
still benefit from structured and multilingual conflict detection, but never pass
because an unsupported requirement was omitted by the parser.
"""
import re
from datetime import datetime, timezone
from .eligibility import source_digest, evaluate
from .keywords import fold_text

ROLE_RX = {
    'warehouse': r'warehouse (?:worker|operative|assistant)|order picker',
    'packing': r'pack(?:er|ing operative|ing worker)',
    'production': r'production (?:worker|operative|operator)|factory worker',
    'farm': r'farm worker|fruit picker|harvest worker|agricultural worker',
    'seasonal': r'seasonal (?:farm |harvest )?worker',
    'kitchen_helper': r'kitchen (?:helper|assistant)|dishwasher',
    'cleaning': r'cleaner|cleaning (?:worker|assistant)|housekeep(?:er|ing)',
}
CONFLICTS = [
    r'(?:good|fluent|excellent|native) (?:german|french|dutch|polish|czech|english)',
    r'gute deutschkenntnisse', r'cesky jazyk.{0,25}b2',
    r'(?:valid |full )?(?:driving licen[cs]e|driver.s licen[cs]e|forklift certificate).{0,40}(?:required|essential|must)',
    r'(?:must|require[ds]?).{0,45}(?:driving licen[cs]e|driver.s licen[cs]e|forklift certificate)',
    r'(?:b[- ]?fuhrerschein|fuhrerschein.{0,20}(?:klasse b|erforderlich))',
    r'valid (?:austrian |local )?work permit', r'gultige arbeitserlaubnis',
    r'(?:must|already).{0,35}(?:right to work|work permit)',
    r'(?:no|not available|cannot offer).{0,30}(?:visa sponsorship|work permit sponsorship)',
    r'(?:visa sponsorship|work permit sponsorship).{0,25}(?:not available|not offered)',
    r'temporary protection|docasne ochrany',
]


def _find(pattern, text):
    m = re.search(pattern, text, re.I)
    return m.group(0) if m else None


def automatic_review(record, text, structured=None, now=None):
    """Return (verdict, ephemeral evidence). Never writes public job records."""
    now = now or datetime.now(timezone.utc)
    structured = structured or {}
    # Structured required fields override any attractive text or optional perks.
    if structured.get('requiredDrivingLicenses'):
        return 'reject', None
    for req in structured.get('positionLanguages') or []:
        if not isinstance(req, dict):
            return 'review', None
        level = req.get('requiredSkillLevel')
        if level and (req.get('languageCode') not in ('en', 'ar') or level.lower() not in ('a1', 'a2')):
            return 'reject', None
    folded = fold_text(text)
    if any(re.search(rx, sentence, re.I) for sentence in re.split(r'[.!?;\n]+', folded)
           if not re.search(r'preferred|desirable|advantage|not required|not essential|wunschenswert|von vorteil|pozadane', sentence)
           for rx in CONFLICTS):
        return 'reject', None
    if _find(r'no longer available|position (?:has been )?filled|job (?:has )?expired', text):
        return 'reject', None
    # All six domains need affirmative evidence. Empty structured arrays are not waivers.
    facts = {}
    role = next(((name, _find(rx, record.get('title') or '')) for name, rx in ROLE_RX.items()
                 if _find(rx, record.get('title') or '')), None)
    if not role or not role[1] or role[1].casefold() not in text.casefold():
        return 'review', None
    facts['role'] = {'status': 'required', 'value': role[0], 'quote': role[1]}
    patterns = {
        'language': r'(?:no (?:language skills|language knowledge|language) (?:are )?(?:required|needed)|(?:basic|elementary|a1|a2)[ -]+english(?: (?:is |only )?(?:enough|sufficient|accepted|required))?)',
        'licence': r'(?:no (?:driving |driver.s |forklift )?(?:licen[cs]e|certificate)(?:s)? (?:is |are )?(?:required|needed))',
        'experience': r'(?:no (?:previous |prior |work )?experience (?:is )?(?:required|needed|necessary))',
        'education': r'(?:no (?:formal )?(?:education|qualifications|degree|diploma) (?:is |are )?(?:required|needed))',
        'authorization': r'(?:(?:visa|work permit) sponsorship (?:is )?(?:available|provided|offered) (?:to |for )?(?:overseas|international|non-eu) (?:applicants|candidates|workers)|(?:overseas|international|non-eu) (?:applicants|candidates|workers).{0,30}(?:visa|work permit) sponsorship (?:is )?(?:available|provided|offered))',
    }
    for domain, rx in patterns.items():
        quote = _find(rx, text)
        if not quote:
            return 'review', None
        facts[domain] = {'status': 'waived', 'quote': quote}
    if not re.search(r'no language', facts['language']['quote'], re.I):
        facts['language'].update(status='required', value=[{'en': 'A2'}])
    facts['authorization'].update(status='required', value='overseas_sponsorship_explicit')
    # Avoid interpreting multilingual mixed requirements or additional mandatory
    # education/skills as waived by an unrelated generic marketing sentence.
    for rx in [r'\b(?:b1|b2|c1|c2)\b', r'\b(?:degree|diploma|qualification|certificate|licen[cs]e|experience)\b[^.!?;\n]{0,60}?\b(?:required|essential|must|mandatory)\b']:
        for hit in re.finditer(rx, text, re.I):
            if not any(hit.group(0).casefold() in f['quote'].casefold() for f in facts.values()):
                return 'review', None
    # Extra mandatory skills or a specific education code need interpretation,
    # never automatic equivalence to the candidate's education.
    if structured.get('requiredSkills') or structured.get('requiredEducationLevelCode'):
        return 'review', None
    years = structured.get('requiredYearsOfExperience')
    if years is not None and years != 0:
        return 'review', None
    # Positive extractor is English-only: source language explicitly known.
    try:
        from langdetect import detect, DetectorFactory
        DetectorFactory.seed = 0
        if detect(text) != 'en':
            return 'review', None
    except Exception:
        return 'review', None
    if structured.get('languageVersion') not in (None, 'en'):
        return 'review', None
    if _find(r'\b(?:must|required|essential|mandatory)\b', text):
        # Every requirement-bearing sentence must be covered by a recognized
        # explicit fact, or ordinary physical/shift duties. Never ignore extras.
        for sentence in re.split(r'[\n.!?;]+', text):
            if not _find(r'\b(?:must|required|essential|mandatory)\b', sentence):
                continue
            remaining = sentence
            for fact in facts.values():
                remaining = re.sub(re.escape(fact['quote']), '', remaining, flags=re.I)
            if _find(r'\b(?:must|required|essential|mandatory)\b', remaining):
                return 'review', None
    review = {'url': record['url'], 'checked_at': now.isoformat(), 'source_sha256': source_digest(text), 'facts': facts}
    return 'extracted', review


def unresolved_domains(text):
    """Name unconfirmed domains without treating missing text as a waiver."""
    names = []
    checks = [
        ('اللغة ومستواها', r'no language.{0,20}(?:required|needed)|basic english'),
        ('الرخص والشهادات', r'no (?:driving |forklift )?(?:licen[cs]e|certificate).{0,20}(?:required|needed)'),
        ('الخبرة', r'no (?:previous |prior )?experience.{0,15}(?:required|needed|necessary)'),
        ('التعليم', r'no (?:formal )?(?:education|qualification|degree|diploma).{0,20}(?:required|needed)'),
        ('دعم تصريح العمل للمتقدم من الخارج', r'(?:visa|work permit) sponsorship.{0,55}(?:overseas|international|non-eu)'),
    ]
    for name, rx in checks:
        if not _find(rx, text):
            names.append(name)
    return 'غير متحقق منه: ' + '، '.join(names or ['شروط إضافية أو لغة نص غير مدعومة بالكامل'])

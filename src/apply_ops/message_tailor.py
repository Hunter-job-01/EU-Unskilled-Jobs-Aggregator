"""Bilingual message tailoring for application cards.

Builds (a) the outbound message DRAFT in the vacancy's working language
(English default) and (b) the Arabic owner-facing card text. Templates
carry placeholders only; candidate facts are supplied by the caller from
the owner's private profile at card-build time and are never stored here.

Honesty rules baked in:
- no experience, language, permit or sponsorship claim is emitted unless
  passed in explicitly by the caller;
- the subject/body never assert work-permit sponsorship availability;
- the draft is a DRAFT: sending requires per-card approval (hitl.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .cv_variants import CVVariant
from .route_classifier import Route

_SUBJECT = "Application - {role} - {candidate_name}"
_BODY_EN = (
    "Dear Hiring Team,\n\n"
    "I am applying for the {role} position advertised at {source_ref}.\n"
    "I am based in Egypt and available to relocate. My attached CV covers "
    "my experience in {cv_area}.\n"
    "{extra_lines}"
    "\nI would be glad to provide any further information.\n\n"
    "Kind regards,\n{candidate_name}\n{contact_line}\n"
)
_CARD_AR = (
    "بطاقة تقديم #{card_id}\n"
    "الوظيفة: {title} - {company} ({country})\n"
    "الطريق: {route_desc}\n"
    "نسخة الـCV: {cv_variant}\n"
    "ملاحظة الأهلية: {eligibility_note}\n"
    "---\n{draft_preview}\n"
    "---\nللموافقة على الإرسال رد: 1 | للتعديل: 2 | للتجاوز: 3"
)
_ROUTE_AR = {
    Route.EMAIL: "إيميل مباشر",
    Route.WHATSAPP: "واتساب",
    Route.WEBFORM: "نموذج موقع (يتطلب مراجعة شروط البوابة قبل الإرسال)",
}
_CV_AREA_EN = {
    CVVariant.WAREHOUSE_PRODUCTION: "warehouse, packing and production work",
    CVVariant.HOSPITALITY_KITCHEN: "kitchen support, cleaning and hospitality work",
    CVVariant.AGRICULTURE_SEASONAL: "agricultural and seasonal work",
}


@dataclass
class TailoredMessage:
    subject: str
    body_draft: str            # English draft
    card_text_ar: str          # Arabic owner-facing card
    body_draft_local: str = "" # employer-local-language draft ("" = translation step still owed before send)
    local_language: str = ""   # e.g. "nl", "pl", "mt"
    claims_used: list = field(default_factory=list)  # explicit claims the caller injected


def tailor_message(
    *,
    card_id: str,
    title: str,
    company: str,
    country: str,
    route: Route,
    source_ref: str,
    cv_variant: CVVariant,
    candidate_name: str,
    contact_line: str,
    eligibility_note: str = "أهلية المقيم خارج الاتحاد الأوروبي غير مؤكدة لهذه الجهة",
    extra_lines: str = "",
    body_draft_local: str = "",
    local_language: str = "",
    claims: list = None,
) -> TailoredMessage:
    """claims: only caller-supplied, evidence-backed statements may appear
    here (e.g. languages actually evidenced). Everything passed in is
    echoed into claims_used so the card shows what was asserted.

    The employer-facing letter needs the employer's local language PLUS
    English; the Arabic card is owner-facing and never substitutes. The
    local draft is produced by the translation step at card-build time
    and passed in as body_draft_local; when it is empty the card marks
    it as still owed, and the card must not be sent before it exists."""
    claims = list(claims or [])
    extra = "\n".join([extra_lines] + claims).strip()
    if extra:
        extra += "\n"
    body = _BODY_EN.format(
        role=title, source_ref=source_ref,
        cv_area=_CV_AREA_EN[cv_variant], extra_lines=extra,
        candidate_name=candidate_name, contact_line=contact_line,
    )
    card = _CARD_AR.format(
        card_id=card_id, title=title, company=company, country=country,
        route_desc=_ROUTE_AR[route], cv_variant=cv_variant.value,
        eligibility_note=eligibility_note,
        draft_preview=body[:900],
    )
    if local_language and not body_draft_local:
        card += "\nملاحظة: نسخة اللغة المحلية ({}) لم تُجهّز بعد - مطلوبة قبل الإرسال.".format(local_language)
    return TailoredMessage(
        subject=_SUBJECT.format(role=title, candidate_name=candidate_name),
        body_draft=body, card_text_ar=card,
        body_draft_local=body_draft_local, local_language=local_language,
        claims_used=claims,
    )


_LOCAL_LETTER = {
    "pl": """Szanowni Państwu,

nazywam się {candidate_name} i składam aplikację na stanowisko {role} (ogłoszenie: {source_ref}).
W załączeniu przesyłam swoje CV. Jestem gotowy do podjęcia pracy od zaraz i chętnie
odpowiem na wszelkie pytania.

Z poważaniem,
{candidate_name}
{contact_line}
""",
    "de": """Sehr geehrte Damen und Herren,

mein Name ist {candidate_name} und ich bewerbe mich um die Stelle {role} (Anzeige: {source_ref}).
Meinen Lebenslauf finden Sie im Anhang. Ich kann sofort anfangen und beantworte
gerne Ihre Fragen.

Mit freundlichen Grüßen
{candidate_name}
{contact_line}
""",
    "nl": """Geachte heer/mevrouw,

mijn naam is {candidate_name} en ik solliciteer naar de functie {role} (vacature: {source_ref}).
In de bijlage vindt u mijn cv. Ik ben direct beschikbaar en beantwoord graag
uw vragen.

Met vriendelijke groet,
{candidate_name}
{contact_line}
""",
}
LOCAL_LETTER_LANGS = tuple(sorted(_LOCAL_LETTER))


def draft_local_letter(language: str, *, role: str, source_ref: str,
                       candidate_name: str, contact_line: str) -> str:
    """Deterministic employer-local-language application letter.

    Only languages with a reviewed template produce a letter; anything
    else returns "" so the card keeps its "local version still owed"
    note instead of shipping an unreviewed machine guess. The template
    carries no claims beyond identity, role, source and availability -
    evidence-backed claims live in the English body and the card.
    """
    template = _LOCAL_LETTER.get((language or "").lower())
    if not template:
        return ""
    return template.format(role=role, source_ref=source_ref,
                           candidate_name=candidate_name,
                           contact_line=contact_line).strip()

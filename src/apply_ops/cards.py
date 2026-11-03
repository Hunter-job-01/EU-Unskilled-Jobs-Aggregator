"""Card builder: turn one high-priority job into an approval card.

Orchestrates the apply-ops modules in order and fails closed at every
step:
  extractor (score>=65, evidence-only route)
    -> one bounded discovery retry when no route was found
    -> cv_variants (no match = no card; never invent a variant)
    -> message_tailor (EN body + local letter + Arabic card)
    -> hitl card (exact send tuple binding) + tracker pending entry
A card is produced only when a contactable route exists; nothing here
sends anything.
"""
from __future__ import annotations

from dataclasses import dataclass

from .cv_variants import select_cv_variant
from .discovery import find_official_page
from .extractor import extract_application_details
from .hitl import ApprovalCard, CardStore
from .message_tailor import draft_local_letter, tailor_message
from .route_classifier import Route
from .tracker import ApplicationTracker, TrackerEntry

_AGGREGATOR_HOSTS = ("europa.eu", "indeed.", "linkedin.", "glassdoor.",
                     "jooble.", "pracuj.", "stepstone.", "monster.")


def _aggregator_host(url: str) -> bool:
    low = (url or "").lower()
    return any(h in low for h in _AGGREGATOR_HOSTS)


_METHOD_ROUTE = {
    "direct_email": Route.EMAIL,
    "whatsapp_or_phone": Route.WHATSAPP,
    "web_form": Route.WEBFORM,
}


@dataclass
class BuiltCard:
    card: ApprovalCard
    card_text_ar: str
    local_language: str
    local_letter_owed: bool
    method: str
    contact: str


def build_application_card(job, *, fetch, search, card_store: CardStore,
                           tracker: ApplicationTracker, source_ref: str,
                           candidate_name: str, contact_line: str,
                           sending_account: str = "",
                           eligibility_note: str = "") -> "BuiltCard | None":
    ext = extract_application_details(job, fetch)
    if not ext.eligible:
        return None
    if not ext.application_method:
        return None  # no contactable route - no card, ever
    if (search is not None and ext.application_method == "web_form"
            and ext.apply_form_url == (getattr(job, "url", "") or "")
            and _aggregator_host(ext.apply_form_url)):
        # Only route is the aggregator posting page itself (e.g. a EURES
        # listing naming an external company). ONE bounded discovery pass
        # tries to locate the employer's official page; when it yields a
        # direct route it wins over the portal fallback.
        found = find_official_page(getattr(job, "company", ""),
                                   getattr(job, "country", ""), search)
        if found.chosen_url:
            retry = extract_application_details(job, lambda u: fetch(found.chosen_url))
            if retry.eligible and retry.application_method:
                ext = retry
    match = select_cv_variant(getattr(job, "title", ""),
                              getattr(job, "description", "") or "")
    if match is None:
        return None  # no honest CV variant - card must say so by absence
    route = _METHOD_ROUTE[ext.application_method]
    contact = {"direct_email": ext.apply_email,
               "whatsapp_or_phone": ext.apply_phone_whatsapp,
               "web_form": ext.apply_form_url}[ext.application_method]
    local_lang = ext.employer_language if ext.employer_language in ("pl", "de", "nl") else ""
    local_letter = draft_local_letter(
        local_lang, role=getattr(job, "title", ""), source_ref=source_ref,
        candidate_name=candidate_name, contact_line=contact_line) if local_lang else ""
    kwargs = dict(
        card_id=getattr(job, "uid", "") or getattr(job, "url", ""),
        title=getattr(job, "title", ""), company=getattr(job, "company", ""),
        country=getattr(job, "country", ""), route=route, source_ref=source_ref,
        cv_variant=match.variant, candidate_name=candidate_name,
        contact_line=contact_line,
        body_draft_local=local_letter, local_language=local_lang,
    )
    if eligibility_note:
        kwargs["eligibility_note"] = eligibility_note
    tailored = tailor_message(**kwargs)
    full_body = tailored.body_draft
    display_text = tailored.card_text_ar
    if tailored.body_draft_local:
        full_body = tailored.body_draft_local + "\n\n-----\n\n" + tailored.body_draft
        # The user approves from the Arabic card: show the local-language
        # letter too, not only the English draft.
        local_block = "--- (" + local_lang + ") ---\n" + tailored.body_draft_local
        # Insert the local-language letter before the 1/2/3 footer so the
        # footer stays last.
        marker = "للموافقة على الإرسال"
        idx = tailored.card_text_ar.rfind(marker)
        if idx > 0:
            head = tailored.card_text_ar[:idx].rstrip()
            if head.endswith("---"):
                head = head[:-3].rstrip()
            foot = tailored.card_text_ar[idx:].strip()
            display_text = head + "\n" + local_block + "\n---\n" + foot
        else:
            display_text = tailored.card_text_ar + "\n" + local_block
    card = card_store.create(ApprovalCard(
        card_id=kwargs["card_id"], job_uid=getattr(job, "uid", ""),
        cv_variant=match.variant.value, card_text_ar=display_text,
        draft_subject=tailored.subject, draft_body=full_body,
        recipient=contact, sending_account=sending_account,
    ))
    tracker.add_pending(TrackerEntry(
        uid=card.job_uid, company=card and getattr(job, "company", ""),
        title_ar=getattr(job, "title_ar", "") or "", country=getattr(job, "country", ""),
        method=ext.application_method, contact_target=contact,
    ))
    return BuiltCard(card=card, card_text_ar=display_text,
                     local_language=local_lang,
                     local_letter_owed=bool(local_lang) and not bool(local_letter),
                     method=ext.application_method, contact=contact)

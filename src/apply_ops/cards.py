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
import re

from .cv_variants import select_cv_variant
from .discovery import find_official_page
from .extractor import extract_application_details
from .hitl import ApprovalCard, CardStore
from .message_tailor import draft_local_letter, local_letter_arabic, tailor_message
from .route_classifier import Route
from .tracker import ApplicationTracker, TrackerEntry
from ..role_labels import arabic_role_label

# Candidate facts supplied by the owner through their authenticated chat
# (v5 CV draft review rounds). The templates
# may assert ONLY these; target-role variants never become experience.
BACKGROUND_LINE_EN = ("My background includes four years in mobile phone sales "
                      "(2020-2024), warehouse work, and my current work in a "
                      "computer shop (since September 2025).")
AUTH_QUESTION_EN = ("I am based in Egypt and would require legal work "
                    "authorization. Could you let me know whether you can "
                    "consider or support a candidate from outside the EU?")
BACKGROUND_LINE_AR = ("خبرتي تشمل أربع سنوات في مبيعات الهواتف المحمولة "
                      "(2020-2024)، وعملاً في المخازن، وعملي الحالي في محل "
                      "كمبيوتر (منذ سبتمبر 2025).")
AUTH_QUESTION_AR = ("أنا مقيم في مصر وأحتاج إلى تصريح عمل قانوني. هل يمكنكم "
                    "النظر في مرشح من خارج الاتحاد الأوروبي أو دعمه؟")

_CHECKS = (
    ("السكن", ("unterkunft", "accommodation", "zakwaterowanie", "housing",
               "huisvesting", "logement", "kwaterunek", "hostel", "mieszkanie")),
    ("دعم تصريح العمل", ("arbeitserlaubnis", "work permit", "zezwolenie na prac",
                         "werkvergunning", "permis de travail", "visa sponsorship")),
    ("قبول بدون خبرة", ("no experience", "ohne erfahrung", "bez doświadczenia",
                        "zonder ervaring", "sans expérience", "bez wykształcenia")),
    ("قبول المتحولين مهنيا", ("quereinsteiger", "career changer", "berufswechsler",
                              "przekwalifikow", "zmiana branży", "zij-instroom")),
    ("تدريب مقدم من جهة العمل", ("training provided", "we train", "full training",
                                 "training plan", "schulung", "szkolenie",
                                 "opleiding", "przyucz", "formation")),
)


_PAY_WORDS = ("vergütung", "stundenlohn", "lohn", "gehalt", "entlohnung",
              "wynagrodzenie", "stawka", "płaca", "brutto", "salary",
              "per hour", "pro stunde", "/std")
_HOUSING_WORDS = ("hostel", "zakwaterowanie", "kwaterunek", "mieszkanie",
                  "unterkunft", "accommodation", "housing")


def _page_salary(text: str) -> str:
    """Verbatim salary fragment from the posting text: the shortest
    sentence containing BOTH a pay word and a currency/decimal amount,
    housing-cost sentences excluded. Overlong sentences are trimmed to a
    window around the amount, snapped at dash/newline boundaries.
    Returns '' when nothing qualifies."""
    if not text:
        return ""
    sentences = re.split(r"\n|(?<=[A-Za-z\)])\. ", text)
    money_re = re.compile(r"(€|eur\b|zł|pln\b|\d+[,.]\d+)", re.IGNORECASE)
    pay_re = re.compile("|".join(re.escape(w) for w in _PAY_WORDS), re.IGNORECASE)
    best = ""
    for sent in sentences:
        low = sent.lower()
        if any(h in low for h in _HOUSING_WORDS):
            continue
        if not (pay_re.search(sent) and money_re.search(sent)):
            continue
        frag = " ".join(sent.split())
        if not best or len(frag) < len(best):
            best = frag
    if not best:
        # Structured pages sometimes split label and value across lines
        # ("Salary\n15 EUR per hour") - join a bare pay label with the
        # next line when that line carries the amount.
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for i, ln in enumerate(lines[:-1]):
            if re.fullmatch(r"(?i)(salary|remuneration|vergütung|gehalt|"
                            r"wynagrodzenie|stawka|płaca)", ln):
                nxt = lines[i + 1]
                if (money_re.search(nxt)
                        and not any(h in nxt.lower() for h in _HOUSING_WORDS)):
                    best = ln + " " + " ".join(nxt.split())
                    break
    if not best:
        return ""
    if len(best) <= 160:
        return best
    # Trim an overlong fused sentence to a window around the amount.
    mm = money_re.search(best)
    start = max(0, mm.start() - 60)
    cut = max(best.rfind("- ", 0, mm.start()), best.rfind(". ", 0, mm.start()))
    if cut > start:
        start = cut + 2
    end = len(best)
    for m in re.finditer(r"- |\. ", best[mm.end():]):
        end = mm.end() + m.start()
        break
    return best[start:end].strip(" -")


def _facts_summary(ext) -> str:
    """Literal facts only. Salary verbatim from the record, falling back
    to a verbatim pay fragment from the fetched posting; each benefit
    checked independently with word-boundary matching. Missing =
    'غير مذكور'. Career-changer acceptance is NEVER reported as a
    training guarantee."""
    salary = (getattr(ext, "key_offer_points", {}) or {}).get("salary", "")
    page = getattr(ext, "page_text", "") or ""
    if not salary:
        frag = _page_salary(page)
        salary = (frag + " (نص الإعلان حرفيا)") if frag else ""
    parts = ["الراتب: " + (salary if salary else "غير مذكور")]
    low = page.lower()
    low = re.sub(r"reasonable\s+accommodations?", "", low)  # disability phrase, not housing
    for label, words in _CHECKS:
        hit = any(re.search(r"(?<![A-Za-ząćęłńóśźżäöü])" + re.escape(w), low)
                  for w in words)
        parts.append(f"{label}: {'مذكور في الإعلان - يُتحقق مباشرة' if hit else 'غير مذكور'}")
    return " | ".join(parts)

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
                           cv_hash: str = "",
                           title_en_manual: str = "",
                           title_ar_manual: str = "",
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
    if title_ar_manual:
        title_ar_label = title_ar_manual + " (ترجمة يدوية مراجعة)"
    else:
        title_ar_label = arabic_role_label({
            "title": getattr(job, "title_ar", "") or getattr(job, "title", ""),
            "title_ar": getattr(job, "title_ar", "") or "",
            "title_en": getattr(job, "title_en", "") or "",
        })
    title_en_label = ((title_en_manual + " (manual reviewed translation)")
                      if title_en_manual else
                      (getattr(job, "title_en", "") or "ترجمة إنجليزية غير متاحة بعد"))
    kwargs = dict(
        card_id=getattr(job, "uid", "") or getattr(job, "url", ""),
        title=getattr(job, "title", ""), company=getattr(job, "company", ""),
        country=getattr(job, "country", ""), route=route, source_ref=source_ref,
        cv_variant=match.variant, candidate_name=candidate_name,
        contact_line=contact_line,
        background_line=BACKGROUND_LINE_EN, auth_question_line=AUTH_QUESTION_EN,
        background_line_ar=BACKGROUND_LINE_AR, auth_question_line_ar=AUTH_QUESTION_AR,
        title_ar_label=title_ar_label, title_en_label=title_en_label,
        score=int(getattr(job, "score", 0) or 0),
        facts_summary=_facts_summary(ext),
        recipient=contact, sending_account=sending_account, cv_hash=cv_hash,
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
        local_block = ("نص الرسالة المرسلة بالضبط: بريد واحد فقط يبدأ بالخطاب "
                       + "باللغة المحلية ثم فاصل ----- ثم النص الإنجليزي (ليس رسالتين). "
                       + "الموضوع: " + tailored.subject + "\n"
                       + "--- (" + local_lang + ") ---\n" + tailored.body_draft_local
                       + "\n--- ترجمة الخطاب (" + local_lang + ") إلى العربية ---\n"
                       + local_letter_arabic(local_lang, role=getattr(job, "title", ""),
                                             source_ref=source_ref,
                                             candidate_name=candidate_name))
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
        cv_file_hash=cv_hash,
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

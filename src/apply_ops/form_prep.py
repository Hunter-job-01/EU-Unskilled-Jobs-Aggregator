"""Web-form application preparation plan (browser, human-gated).

For web_form-route cards the executor never submits: the flow stops at a
prepared browser state with a screenshot for the owner. This module is
the pure plan/state layer - the browser driving happens in the owning
process. It defines what "prepared" means, what evidence is captured,
and the hard stop before any submit control.

The plan never autofills beyond identity/CV fields the owner approved on
the card, never clicks anything labelled submit/apply/send/versturen/
absenden/wyślij, and treats unknown outcomes as NOT prepared.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SUBMIT_WORDS = ("submit", "apply now", "send application", "solliciteer",
                "verstuur", "absenden", "bewerben", "wyślij", "aplika")

FILLABLE_FIELDS = ("name", "email", "phone", "cv_upload")


@dataclass
class FormPrepPlan:
    form_url: str
    card_id: str
    allowed_fields: tuple = FILLABLE_FIELDS
    stop_before: tuple = SUBMIT_WORDS
    evidence_required: tuple = ("screenshot_filled_form", "current_url")
    owner_approved_fields: tuple = ()


@dataclass
class FormPrepResult:
    prepared: bool = False
    screenshot_path: str = ""
    final_url: str = ""
    blocked_reason: str = ""
    filled_fields: list = field(default_factory=list)


@dataclass
class FormInspection:
    """Read-only reach/field-map proof: the form was reached, its fields
    and screening questions were mapped, NO personal data was entered,
    no consent toggled, no account created, nothing submitted. This is
    NOT preparedness - a prepared_for_review state additionally requires
    owner-approved fields actually filled and the attachment verified."""
    form_url: str = ""
    reached: bool = False
    fields_mapped: list = field(default_factory=list)
    screening_questions: list = field(default_factory=list)
    account_creation_at_submit: bool = False
    screenshot_path: str = ""


def record_inspection(*, form_url: str, reached: bool, fields_mapped=(),
                      screening_questions=(), account_creation_at_submit=False,
                      screenshot_path: str = "") -> FormInspection:
    return FormInspection(
        form_url=form_url, reached=bool(reached),
        fields_mapped=list(fields_mapped),
        screening_questions=list(screening_questions),
        account_creation_at_submit=bool(account_creation_at_submit),
        screenshot_path=screenshot_path)


def plan_form_prep(*, form_url: str, card_id: str,
                   owner_approved_fields=()) -> FormPrepPlan:
    if not form_url:
        raise ValueError("form prep needs the evidence-backed form URL")
    return FormPrepPlan(form_url=form_url, card_id=card_id,
                        owner_approved_fields=tuple(owner_approved_fields))


def evaluate_prep(plan: FormPrepPlan, *, screenshot_path: str,
                  final_url: str, filled_fields,
                  submit_clicked: bool) -> FormPrepResult:
    """Judge an attempted prep against the plan. Fail closed."""
    if submit_clicked:
        return FormPrepResult(blocked_reason="submit control was activated - hard stop violated")
    unexpected = [f for f in filled_fields if f not in plan.allowed_fields]
    if unexpected:
        return FormPrepResult(blocked_reason="fields filled outside card approval: " + ",".join(sorted(unexpected)))
    filled = list(filled_fields)
    if not filled:
        return FormPrepResult(blocked_reason="no fields filled - this is an inspection, not a prepared form; use record_inspection")
    if not screenshot_path:
        return FormPrepResult(blocked_reason="no screenshot evidence captured")
    if not final_url:
        return FormPrepResult(blocked_reason="final URL not recorded")
    return FormPrepResult(prepared=True, screenshot_path=screenshot_path,
                          final_url=final_url, filled_fields=list(filled_fields))

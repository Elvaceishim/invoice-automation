"""
Invoice Validation Service — Project 2.

Takes structured invoice data (whatever the extraction service produced)
and decides: auto-approve, or flag for human review — and if flagged,
exactly which specific rule triggered it, not just "needs review."

This is the actual point of the whole project. Extraction proves you can
read a document. Validation proves you can make a correct, explainable
decision from what you read — and know when NOT to trust your own read
(low extraction confidence gets flagged on its own, independent of whether
the numbers look fine).

Run standalone for local testing:
    uvicorn main:app --reload --port 8003
"""

import re

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Invoice Validation Service")

AUTO_APPROVAL_THRESHOLD = 2000.00
NUMERIC_TOLERANCE = 0.01

APPROVED_VENDORS = {
    "Nimbus Cloud Services",
    "Vertex Office Supplies",
    "Meridian Consulting Group",
    "QuickFix IT Solutions",
}

# Matches the generator's format: INV- followed by a 4-digit year and a
# 5-digit sequence number. Catches exactly the kind of transcription error
# we saw for real (INV-202600023 becoming INV-20202600023) instead of
# trusting the extracted string blindly.
INVOICE_NUMBER_PATTERN = re.compile(r"^INV-\d{9}$")


class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    line_total: float


class ExtractedInvoice(BaseModel):
    vendor_name: str | None
    invoice_number: str | None
    issue_date: str | None
    line_items: list[LineItem]
    subtotal: float | None
    tax: float | None
    total: float | None
    extraction_confidence: str
    extraction_notes: str = ""


class ValidationResult(BaseModel):
    outcome: str  # "auto_approve" | "flag_for_review"
    flags: list[str]
    reasons: dict[str, str]  # flag_name -> human-readable explanation


def validate_invoice(inv: ExtractedInvoice) -> ValidationResult:
    flags: list[str] = []
    reasons: dict[str, str] = {}

    # Rule 1: vendor must be on the approved list
    if inv.vendor_name not in APPROVED_VENDORS:
        flags.append("unapproved_vendor")
        reasons["unapproved_vendor"] = f"'{inv.vendor_name}' is not on the approved vendor list."

    # Rule 2: total must match line items + tax — recomputed independently,
    # not trusting the extracted subtotal either, since a wrong subtotal
    # would silently hide a wrong total.
    if inv.total is not None and inv.line_items:
        computed_subtotal = round(sum(item.line_total for item in inv.line_items), 2)
        computed_total = round(computed_subtotal + (inv.tax or 0), 2)
        if abs(inv.total - computed_total) > NUMERIC_TOLERANCE:
            flags.append("total_mismatch")
            reasons["total_mismatch"] = (
                f"Invoice total ${inv.total:.2f} does not match line items + tax "
                f"(computed ${computed_total:.2f}, difference ${abs(inv.total - computed_total):.2f})."
            )
    elif inv.total is None:
        flags.append("total_mismatch")
        reasons["total_mismatch"] = "No total was extracted at all."

    # Rule 3: amount threshold — even a mathematically correct invoice needs
    # human sign-off above this amount, regardless of everything else checking out.
    if inv.total is not None and inv.total > AUTO_APPROVAL_THRESHOLD:
        flags.append("over_threshold")
        reasons["over_threshold"] = f"Total ${inv.total:.2f} exceeds the ${AUTO_APPROVAL_THRESHOLD:.2f} auto-approval threshold."

    # Rule 4: invoice number must exist AND match the expected format —
    # this second half is what would have caught the real transcription
    # error found during extraction testing (INV-202600023 -> INV-20202600023).
    if inv.invoice_number is None:
        flags.append("missing_invoice_number")
        reasons["missing_invoice_number"] = "No invoice number was extracted."
    elif not INVOICE_NUMBER_PATTERN.match(inv.invoice_number):
        flags.append("invoice_number_format_suspect")
        reasons["invoice_number_format_suspect"] = (
            f"'{inv.invoice_number}' doesn't match the expected INV-######### format — "
            f"possible transcription error, worth a second look before trusting it."
        )

    # Rule 5: date must have been extractable at all
    if inv.issue_date is None:
        flags.append("missing_or_malformed_date")
        reasons["missing_or_malformed_date"] = "Issue date was missing or too ambiguous to parse confidently."

    # Rule 6: don't trust the extraction's own uncertainty — if it said low
    # confidence, that's a flag on its own, independent of whether the
    # numbers happen to look fine. A confidently-wrong extraction is worse
    # than one that flags its own uncertainty.
    if inv.extraction_confidence == "low":
        flags.append("low_extraction_confidence")
        reasons["low_extraction_confidence"] = f"Extraction reported low confidence: {inv.extraction_notes or 'no details given'}"

    outcome = "auto_approve" if not flags else "flag_for_review"
    return ValidationResult(outcome=outcome, flags=flags, reasons=reasons)


@app.post("/validate", response_model=ValidationResult)
def validate(inv: ExtractedInvoice):
    return validate_invoice(inv)


@app.get("/health")
def health():
    return {"status": "ok"}

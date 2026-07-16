"""
Invoice Extraction Service — Project 2 (OpenRouter version).

Takes a PDF, pulls raw text via pdfplumber, sends it to an LLM via OpenRouter
with a forced tool call to guarantee structured output — forcing a specific
tool call is the reliable way to get a schema-conformant response every
time, rather than hoping the model returns clean JSON in free text.

OpenRouter's API is OpenAI-compatible, so this uses the `openai` SDK pointed
at OpenRouter's base URL rather than a dedicated OpenRouter SDK.

Default model is nvidia/nemotron-3-super-120b-a12b:free — chosen because it's
specifically noted for stronger structured-output reliability among current
free-tier options, not just because it's free. Override via OPENROUTER_MODEL
if you want to compare against another model (worth doing — see the
evaluation script's notes on model comparison).

Free-tier rate limits to know about: 50 requests/day on a bare free account,
1000/day once you've purchased at least $10 in credits (even if you don't
spend it), and 20 requests/minute regardless. 30 invoices fits comfortably
under the daily cap but leaves little room for retries — pace requests if
you hit 429s.

Run standalone for local testing:
    export OPENROUTER_API_KEY=sk-or-v1-...
    uvicorn main:app --reload --port 8002
"""

import io
import json
import os
import time

import pdfplumber
from fastapi import FastAPI, File, HTTPException, UploadFile
from openai import OpenAI, RateLimitError
from pydantic import BaseModel, Field

app = FastAPI(title="Invoice Extraction Service")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    # Falls back to a placeholder so the SDK doesn't crash at import time when
    # the key isn't set yet (e.g. during local testing, or a container that
    # hasn't had its env configured). Real requests will fail with a clear
    # 401 from OpenRouter if this placeholder is actually used — the
    # endpoint-level check below is what should catch this case first.
    api_key=os.environ.get("OPENROUTER_API_KEY") or "unset",
    default_headers={
        # OpenRouter uses these for their own analytics/rankings — harmless to include,
        # and it's their documented best practice.
        "HTTP-Referer": "https://github.com/Elvaceishim",
        "X-Title": "invoice-extraction-service",
    },
)

MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")


class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    line_total: float


class ExtractionResult(BaseModel):
    vendor_name: str | None
    invoice_number: str | None
    issue_date: str | None = Field(
        description="ISO format (YYYY-MM-DD) if the model could confidently parse it, else null"
    )
    line_items: list[LineItem]
    subtotal: float | None
    tax: float | None
    total: float | None
    extraction_confidence: str  # "high" | "medium" | "low"
    extraction_notes: str  # anything ambiguous, missing, or uncertain


# OpenAI-compatible function-calling schema (different shape from Anthropic's
# tool format — this is the "type": "function" wrapper OpenRouter expects).
EXTRACT_TOOL = {
    "type": "function",
    "function": {
        "name": "record_invoice_extraction",
        "description": "Records structured data extracted from an invoice document.",
        "parameters": {
            "type": "object",
            "properties": {
                "vendor_name": {"type": ["string", "null"]},
                "invoice_number": {
                    "type": ["string", "null"],
                    "description": "null if genuinely not present on the invoice — do not invent one",
                },
                "issue_date": {
                    "type": ["string", "null"],
                    "description": "ISO format YYYY-MM-DD only if you can confidently parse the printed date. "
                                    "If the date is ambiguous, malformed, or missing, return null and explain in extraction_notes.",
                },
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit_price": {"type": "number"},
                            "line_total": {"type": "number"},
                        },
                        "required": ["description", "quantity", "unit_price", "line_total"],
                    },
                },
                "subtotal": {"type": ["number", "null"]},
                "tax": {"type": ["number", "null"]},
                "total": {"type": ["number", "null"]},
                "extraction_confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "low if any field required guessing or the document was ambiguous",
                },
                "extraction_notes": {
                    "type": "string",
                    "description": "Explain any field you couldn't confidently extract, or leave empty if extraction was clean",
                },
            },
            "required": [
                "vendor_name", "invoice_number", "issue_date", "line_items",
                "subtotal", "tax", "total", "extraction_confidence", "extraction_notes",
            ],
        },
    },
}

EXTRACTION_PROMPT = """You are extracting structured data from an invoice for an accounts payable pipeline. Accuracy matters more than completeness — a downstream validation step will check your total against the line items, so do not silently correct or infer a total that doesn't match what's printed.

Rules:
- Extract exactly what's on the document. Do not "fix" a total that looks wrong — report it as printed, even if it doesn't match the line items. The validation layer is designed to catch that mismatch; your job is faithful extraction, not correction.
- If the invoice number is missing or blank, return null. Do not invent one.
- If the date is ambiguous, incomplete, or in a format you can't confidently convert to YYYY-MM-DD, return null for issue_date and explain in extraction_notes.
- Set extraction_confidence to "low" if anything required guessing.

Invoice text:

{invoice_text}
"""


def extract_pdf_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _parse_model_response(message) -> dict:
    """Prefer a real tool_call, but fall back to parsing message.content as
    JSON — this specific model sometimes ignores the forced tool_choice and
    writes JSON directly as plain text instead. If neither works, raise a
    clear error rather than fail silently."""
    if message.tool_calls:
        return json.loads(message.tool_calls[0].function.arguments)

    content = (message.content or "").strip()
    if content.startswith("{"):
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Model wrote JSON-like content but it was truncated or malformed "
                f"(likely cut off by max_tokens or a generation failure): {content[:200]!r}"
            ) from e

    raise RuntimeError(f"Model returned no usable tool call or JSON content. Raw content: {content[:300]!r}")


def call_extraction_model(invoice_text: str, max_retries: int = 3) -> dict:
    last_error = None
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                # Increased from the original 1500 — this model sometimes writes
                # full JSON as plain content rather than a compact tool call,
                # and 1500 wasn't enough headroom for invoices with several
                # line items, causing mid-object truncation.
                max_tokens=4000,
                tools=[EXTRACT_TOOL],
                tool_choice={"type": "function", "function": {"name": "record_invoice_extraction"}},
                messages=[{"role": "user", "content": EXTRACTION_PROMPT.format(invoice_text=invoice_text)}],
            )
            return _parse_model_response(response.choices[0].message)
        except RateLimitError as e:
            last_error = e
            if attempt < max_retries - 1:
                # OpenRouter's free-tier 429s are explicitly transient
                # ("temporarily rate-limited upstream, retry shortly") —
                # exponential-ish backoff rather than hammering it again immediately.
                wait = 10 * (attempt + 1)
                time.sleep(wait)
                continue
    raise RuntimeError(f"Rate limited after {max_retries} attempts: {last_error}")


@app.post("/extract", response_model=ExtractionResult)
async def extract_invoice(file: UploadFile = File(...)):
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY not configured")

    pdf_bytes = await file.read()
    try:
        invoice_text = extract_pdf_text(pdf_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read PDF: {e}")

    if not invoice_text.strip():
        raise HTTPException(status_code=422, detail="PDF contained no extractable text")

    try:
        result = call_extraction_model(invoice_text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Extraction model call failed: {e}")

    return ExtractionResult(**result)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "api_key_configured": bool(os.environ.get("OPENROUTER_API_KEY")),
        "model": MODEL,
    }

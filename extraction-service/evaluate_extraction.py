"""
Extraction accuracy evaluation — Project 2.

Runs every synthetic invoice through the extraction service and scores the
result against manifest.json's ground truth. This is the actual proof that
extraction works, not just that the service returns 200 OK.

Requires the extraction service running locally with a real OPENROUTER_API_KEY
configured — this script makes real API calls. Free-tier model, so no
dollar cost, but it does count against OpenRouter's daily/per-minute limits.

Usage:
    python3 evaluate_extraction.py
"""

import json
import time
from pathlib import Path

import requests

SERVICE_URL = "http://localhost:8002/extract"
MANIFEST_PATH = Path(__file__).resolve().parent.parent / "data-generator" / "output" / "manifest.json"
INVOICES_DIR = Path(__file__).resolve().parent.parent / "data-generator" / "output" / "invoices"

# OpenRouter's free tier caps at 20 requests/minute. A 3-second pace keeps us
# comfortably under that (20/min = one every 3s) without stretching a 30-item
# run out unnecessarily.
SECONDS_BETWEEN_REQUESTS = 3


def fields_match(extracted, expected, tolerance=0.01):
    """Numeric comparison with a small tolerance for float rounding."""
    if extracted is None and expected is None:
        return True
    if extracted is None or expected is None:
        return False
    try:
        return abs(float(extracted) - float(expected)) <= tolerance
    except (TypeError, ValueError):
        return str(extracted).strip() == str(expected).strip()


def evaluate_one(invoice_gt: dict) -> dict:
    pdf_path = INVOICES_DIR / invoice_gt["pdf_filename"]

    # This model does extended chain-of-thought reasoning before every
    # response (confirmed: a one-word test prompt still generated ~170
    # reasoning tokens), so a full invoice extraction needs real headroom —
    # 90s, with one retry on timeout before giving up on this invoice.
    for attempt in range(2):
        try:
            with open(pdf_path, "rb") as f:
                resp = requests.post(
                    SERVICE_URL,
                    files={"file": (pdf_path.name, f, "application/pdf")},
                    timeout=90,
                )
            break
        except requests.exceptions.ReadTimeout:
            if attempt == 0:
                print(f"         (timed out, retrying once...)")
                continue
            return {
                "pdf_filename": invoice_gt["pdf_filename"],
                "scenario": invoice_gt["scenario"],
                "error": "Timed out twice (90s each) — model may be under heavy load",
            }

    if resp.status_code != 200:
        return {
            "pdf_filename": invoice_gt["pdf_filename"],
            "scenario": invoice_gt["scenario"],
            "error": f"HTTP {resp.status_code}: {resp.text}",
        }

    extracted = resp.json()

    checks = {
        "vendor_name": extracted.get("vendor_name") == invoice_gt["vendor_name"],
        "invoice_number": extracted.get("invoice_number") == invoice_gt["invoice_number"],
        # Critical check: extraction must report the PRINTED total, not "correct" it
        "total_matches_printed": fields_match(extracted.get("total"), invoice_gt["printed_total"]),
        "did_not_silently_correct": not fields_match(extracted.get("total"), invoice_gt["correct_total"])
            or fields_match(invoice_gt["printed_total"], invoice_gt["correct_total"]),
    }

    return {
        "pdf_filename": invoice_gt["pdf_filename"],
        "scenario": invoice_gt["scenario"],
        "extracted": extracted,
        "checks": checks,
        "all_passed": all(checks.values()),
    }


def main():
    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    print(f"Evaluating extraction against {len(manifest)} invoices...\n")

    results = []
    for i, inv in enumerate(manifest):
        result = evaluate_one(inv)
        results.append(result)
        status = "OK" if result.get("all_passed") else ("ERROR" if "error" in result else "MISMATCH")
        print(f"[{status}] {result['pdf_filename']} ({result['scenario']})")
        if status == "MISMATCH":
            failed_checks = [k for k, v in result["checks"].items() if not v]
            print(f"         failed: {failed_checks}")
        if status == "ERROR":
            print(f"         {result['error']}")
        if i < len(manifest) - 1:
            time.sleep(SECONDS_BETWEEN_REQUESTS)

    scored = [r for r in results if "checks" in r]
    total_checks = {}
    for r in scored:
        for check, passed in r["checks"].items():
            total_checks.setdefault(check, []).append(passed)

    print("\n--- Summary ---")
    print(f"Total invoices: {len(manifest)}")
    print(f"Successfully processed: {len(scored)}")
    print(f"Errors: {len(results) - len(scored)}")
    for check, values in total_checks.items():
        pct = 100 * sum(values) / len(values)
        print(f"{check}: {sum(values)}/{len(values)} ({pct:.0f}%)")

    out_path = Path(__file__).resolve().parent / "evaluation_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nFull results saved to {out_path}")


if __name__ == "__main__":
    main()

"""
Synthetic invoice generator for Project 2 (AP automation with AI exception handling).

Generates real PDF invoices (not just JSON) so the extraction step in the
pipeline has to do actual document understanding, plus a ground-truth JSON
record per invoice so we can objectively score whether extraction and
validation got the right answer.

Each invoice is generated under one SCENARIO, which deliberately injects a
specific failure mode. This is the point: real-world invoice data won't
reliably contain the exact edge cases we need to prove the validation layer
catches. Here, we control exactly what's broken and know the correct answer
in advance.

Usage:
    python3 generate_invoices.py [--count 30] [--seed 42]
"""

import argparse
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
INVOICES_DIR = OUTPUT_DIR / "invoices"
GROUND_TRUTH_DIR = OUTPUT_DIR / "ground_truth"

AUTO_APPROVAL_THRESHOLD = 2000.00
TAX_RATE = 0.075

VENDORS = [
    {"name": "Nimbus Cloud Services", "approved": True},
    {"name": "Vertex Office Supplies", "approved": True},
    {"name": "Meridian Consulting Group", "approved": True},
    {"name": "QuickFix IT Solutions", "approved": True},
    {"name": "Unknown Ventures LLC", "approved": False},
    {"name": "Shadow Freight Co", "approved": False},
]

ITEM_CATALOG = [
    ("Cloud hosting — monthly", 45.00, 350.00),
    ("Consulting hours", 80.00, 220.00),
    ("Office supplies — bulk order", 15.00, 90.00),
    ("Software license — annual", 200.00, 900.00),
    ("Hardware — replacement parts", 60.00, 400.00),
    ("Freight & logistics", 100.00, 600.00),
    ("Support retainer — monthly", 150.00, 500.00),
    ("Design services", 70.00, 300.00),
]

SCENARIOS = [
    "clean",
    "clean",
    "clean",  # weighted higher — most real invoices should be fine
    "total_mismatch",
    "unapproved_vendor",
    "over_threshold",
    "missing_invoice_number",
    "malformed_date",
]


def make_line_items(target_min, target_max):
    """Builds 2-5 line items whose subtotal actually falls within the given
    range — this matters because 'clean' invoices need to reliably land
    under the auto-approval threshold, and 'over_threshold' invoices need
    to reliably exceed it. A loose bias isn't good enough here."""
    for _attempt in range(20):
        n_items = random.randint(2, 5)
        items = []
        for _ in range(n_items):
            desc, lo, hi = random.choice(ITEM_CATALOG)
            qty = random.randint(1, 6)
            unit_price = round(random.uniform(lo, hi), 2)
            items.append({
                "description": desc,
                "quantity": qty,
                "unit_price": unit_price,
                "line_total": round(qty * unit_price, 2),
            })
        subtotal = round(sum(i["line_total"] for i in items), 2)
        if target_min <= subtotal <= target_max:
            return items, subtotal

    # Fallback after 20 tries: scale the last attempt's quantities down/up
    # proportionally so it lands in range, rather than risk an infinite loop.
    scale = ((target_min + target_max) / 2) / subtotal if subtotal else 1
    for item in items:
        item["quantity"] = max(1, round(item["quantity"] * scale))
        item["line_total"] = round(item["quantity"] * item["unit_price"], 2)
    subtotal = round(sum(i["line_total"] for i in items), 2)
    return items, subtotal


def build_invoice(scenario: str, invoice_id: int) -> dict:
    vendor = random.choice(
        [v for v in VENDORS if not v["approved"]] if scenario == "unapproved_vendor"
        else [v for v in VENDORS if v["approved"]]
    )

    if scenario == "over_threshold":
        items, subtotal = make_line_items(1800, 3500)
    else:
        items, subtotal = make_line_items(200, 1500)

    tax = round(subtotal * TAX_RATE, 2)
    correct_total = round(subtotal + tax, 2)

    printed_total = correct_total
    if scenario == "total_mismatch":
        # Deliberately wrong — off by a plausible-looking but incorrect amount,
        # simulating a typo or a stale total left over from an edited invoice.
        printed_total = round(correct_total + random.choice([-50.00, 37.25, 100.00, -18.40]), 2)

    invoice_number = f"INV-{2026}{invoice_id:05d}"
    if scenario == "missing_invoice_number":
        invoice_number = None

    issue_date = (datetime(2026, 7, 1) + timedelta(days=random.randint(0, 40))).strftime("%Y-%m-%d")
    printed_date = issue_date
    if scenario == "malformed_date":
        # Ambiguous/broken format that a naive parser will choke on
        printed_date = random.choice(["07/15/26", "15th of July", "2026.07.15x", ""])

    # Ground-truth expected flags — this is what the pipeline's validation
    # layer SHOULD produce. Used later to score the pipeline's actual output.
    flags = []
    if not vendor["approved"]:
        flags.append("unapproved_vendor")
    if abs(printed_total - correct_total) > 0.01:
        flags.append("total_mismatch")
    if printed_total > AUTO_APPROVAL_THRESHOLD or correct_total > AUTO_APPROVAL_THRESHOLD:
        flags.append("over_threshold")
    if invoice_number is None:
        flags.append("missing_invoice_number")
    if scenario == "malformed_date":
        flags.append("malformed_date")

    expected_outcome = "auto_approve" if not flags else "flag_for_review"

    return {
        "invoice_id": invoice_id,
        "scenario": scenario,
        "vendor_name": vendor["name"],
        "vendor_approved": vendor["approved"],
        "invoice_number": invoice_number,
        "issue_date_printed": printed_date,
        "issue_date_correct": issue_date,
        "line_items": items,
        "subtotal": subtotal,
        "tax": tax,
        "printed_total": printed_total,
        "correct_total": correct_total,
        "expected_flags": flags,
        "expected_outcome": expected_outcome,
        "pdf_filename": f"invoice_{invoice_id:04d}.pdf",
    }


def render_pdf(invoice: dict, path: Path):
    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    y = height - 1 * inch

    c.setFont("Helvetica-Bold", 18)
    c.drawString(1 * inch, y, "INVOICE")
    y -= 0.4 * inch

    c.setFont("Helvetica", 10)
    c.drawString(1 * inch, y, invoice["vendor_name"])
    y -= 0.3 * inch

    inv_num_display = invoice["invoice_number"] or ""
    c.drawString(1 * inch, y, f"Invoice #: {inv_num_display}")
    y -= 0.25 * inch
    c.drawString(1 * inch, y, f"Date: {invoice['issue_date_printed']}")
    y -= 0.5 * inch

    # Table header
    c.setFont("Helvetica-Bold", 10)
    c.drawString(1 * inch, y, "Description")
    c.drawString(4.3 * inch, y, "Qty")
    c.drawString(4.9 * inch, y, "Unit Price")
    c.drawString(6.0 * inch, y, "Line Total")
    y -= 0.15 * inch
    c.line(1 * inch, y, 7.3 * inch, y)
    y -= 0.25 * inch

    c.setFont("Helvetica", 10)
    for item in invoice["line_items"]:
        c.drawString(1 * inch, y, item["description"])
        c.drawString(4.3 * inch, y, str(item["quantity"]))
        c.drawString(4.9 * inch, y, f"${item['unit_price']:.2f}")
        c.drawString(6.0 * inch, y, f"${item['line_total']:.2f}")
        y -= 0.25 * inch

    y -= 0.2 * inch
    c.line(4.3 * inch, y, 7.3 * inch, y)
    y -= 0.25 * inch

    c.drawString(4.9 * inch, y, "Subtotal:")
    c.drawString(6.0 * inch, y, f"${invoice['subtotal']:.2f}")
    y -= 0.22 * inch
    c.drawString(4.9 * inch, y, f"Tax ({TAX_RATE*100:.1f}%):")
    c.drawString(6.0 * inch, y, f"${invoice['tax']:.2f}")
    y -= 0.22 * inch
    c.setFont("Helvetica-Bold", 11)
    c.drawString(4.9 * inch, y, "Total:")
    c.drawString(6.0 * inch, y, f"${invoice['printed_total']:.2f}")

    c.save()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    INVOICES_DIR.mkdir(parents=True, exist_ok=True)
    GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)

    manifest = []
    for i in range(1, args.count + 1):
        scenario = random.choice(SCENARIOS)
        invoice = build_invoice(scenario, i)
        pdf_path = INVOICES_DIR / invoice["pdf_filename"]
        render_pdf(invoice, pdf_path)

        gt_path = GROUND_TRUTH_DIR / f"invoice_{i:04d}.json"
        gt_path.write_text(json.dumps(invoice, indent=2))
        manifest.append(invoice)

    (OUTPUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))

    scenario_counts = {}
    for inv in manifest:
        scenario_counts[inv["scenario"]] = scenario_counts.get(inv["scenario"], 0) + 1

    print(f"Generated {args.count} invoices.")
    print("Scenario breakdown:")
    for scenario, count in sorted(scenario_counts.items()):
        print(f"  {scenario}: {count}")
    print(f"\nPDFs:         {INVOICES_DIR}")
    print(f"Ground truth: {GROUND_TRUTH_DIR}")
    print(f"Manifest:     {OUTPUT_DIR / 'manifest.json'}")


if __name__ == "__main__":
    main()

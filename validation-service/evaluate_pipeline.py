"""
End-to-end pipeline evaluation — Project 2.

Takes the REAL extraction results from your actual evaluate_extraction.py
run (not simulated data) and runs each one through validation, then scores
the final outcome against ground truth. This is the number that actually
matters: does the full pipeline (extraction -> validation -> decision)
reach the correct auto-approve/flag-for-review decision.

Invoices where extraction itself failed (timeout, malformed response) are
reported separately — those correctly SHOULD be flagged for human review,
since a pipeline with no confident data has no business auto-approving
anything. That's not a pipeline failure, it's the pipeline behaving
correctly under a condition the ground truth data doesn't even model.

Requires the validation service running locally.

Usage:
    python3 evaluate_pipeline.py
"""

import json
from pathlib import Path

import requests

VALIDATION_URL = "http://localhost:8003/validate"
EXTRACTION_RESULTS_PATH = Path(__file__).resolve().parent.parent / "extraction-service" / "evaluation_results.json"
MANIFEST_PATH = Path(__file__).resolve().parent.parent / "data-generator" / "output" / "manifest.json"


def main():
    if not EXTRACTION_RESULTS_PATH.exists():
        print(f"Can't find {EXTRACTION_RESULTS_PATH}")
        print("Run evaluate_extraction.py in extraction-service/ first — this script needs its output.")
        return

    with open(EXTRACTION_RESULTS_PATH) as f:
        extraction_results = json.load(f)
    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    gt_by_filename = {inv["pdf_filename"]: inv for inv in manifest}

    correct = 0
    extraction_failures = 0
    mismatches = []

    for ext_result in extraction_results:
        filename = ext_result["pdf_filename"]
        gt = gt_by_filename[filename]

        if "error" in ext_result:
            extraction_failures += 1
            print(f"[EXTRACTION FAILED] {filename} ({gt['scenario']}) — correctly requires human review (no data to validate)")
            continue

        extracted = ext_result["extracted"]
        resp = requests.post(VALIDATION_URL, json=extracted, timeout=10)
        if resp.status_code != 200:
            print(f"[VALIDATION ERROR] {filename}: HTTP {resp.status_code} — {resp.text}")
            continue

        result = resp.json()

        outcome_ok = result["outcome"] == gt["expected_outcome"]
        expected_flags_covered = all(
            (f in result["flags"]) or (f == "malformed_date" and "missing_or_malformed_date" in result["flags"])
            for f in gt["expected_flags"]
        )

        if outcome_ok and expected_flags_covered:
            correct += 1
            print(f"[OK] {filename} ({gt['scenario']}) -> {result['outcome']}")
        else:
            mismatches.append({
                "file": filename,
                "scenario": gt["scenario"],
                "expected_outcome": gt["expected_outcome"],
                "got_outcome": result["outcome"],
                "expected_flags": gt["expected_flags"],
                "got_flags": result["flags"],
            })
            print(f"[MISMATCH] {filename} ({gt['scenario']}) — expected {gt['expected_outcome']}, got {result['outcome']}")

    total_validated = len(extraction_results) - extraction_failures
    print("\n--- Pipeline Summary ---")
    print(f"Total invoices: {len(extraction_results)}")
    print(f"Extraction failures (correctly need review, not counted as pipeline errors): {extraction_failures}")
    print(f"Reached validation: {total_validated}")
    if total_validated:
        print(f"Correct end-to-end decision: {correct}/{total_validated} ({100*correct/total_validated:.0f}%)")

    if mismatches:
        print("\nMismatches worth investigating:")
        for m in mismatches:
            print(f"  {m['file']} ({m['scenario']}): expected {m['expected_outcome']}/{m['expected_flags']}, got {m['got_outcome']}/{m['got_flags']}")

    out_path = Path(__file__).resolve().parent / "pipeline_evaluation_results.json"
    out_path.write_text(json.dumps({
        "total": len(extraction_results),
        "extraction_failures": extraction_failures,
        "correct": correct,
        "total_validated": total_validated,
        "mismatches": mismatches,
    }, indent=2))
    print(f"\nFull results saved to {out_path}")


if __name__ == "__main__":
    main()

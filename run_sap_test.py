#!/usr/bin/env python3
"""SACC-POC-Triage — SAP Document AI batch test.

Works exactly like sap_ai/sap_test.py but adds the triage pipeline:
    1.  Authenticate with SAP Doc AI (OAuth2 client credentials)
    2.  Find the invoice extraction schema
    3.  Process every PDF / image in invoice_samples/
    4.  Save raw SAP extraction JSON to output/
    5.  Run the triage pipeline on each extraction result
    6.  Save triage result JSON + evidence trail to output/
    7.  Log everything to console + output/run_log.txt

Usage:
    python run_sap_test.py                        # process all files in invoice_samples/
    python run_sap_test.py invoice1.pdf           # specific files
    python run_sap_test.py --cred-file cred.json  # use cred.json instead of env vars
"""

import os
import sys
import json
import time
import glob

# Ensure src/ is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from triage.doc_ai import DocAIClient
from triage.runner import run_triage

# ── Setup paths ─────────────────────────────────────────────────────────────

_dir = os.path.dirname(os.path.abspath(__file__))
invoice_folder = os.path.join(_dir, "invoice_samples")
output_dir = os.path.join(_dir, "output")
os.makedirs(output_dir, exist_ok=True)

# ── Logging (console + file) ────────────────────────────────────────────────

log_path = os.path.join(output_dir, "run_log.txt")
_log_fh = open(log_path, "w", encoding="utf-8")

def _log(*args):
    line = " ".join(str(a) for a in args)
    print(line)
    _log_fh.write(line + "\n")
    _log_fh.flush()

# ── Collect files to process ────────────────────────────────────────────────

files = [
    f for f in glob.glob(os.path.join(invoice_folder, "*"))
    if f.lower().endswith((".pdf", ".png", ".jpg", ".jpeg"))
]
# Allow passing specific file paths as CLI args (like sap_test.py)
if len(sys.argv) > 1 and all(os.path.isfile(a) for a in sys.argv[1:]):
    files = sys.argv[1:]

_log("=" * 60)
_log("SACC-POC-Triage — SAP Document AI Batch Test")
_log("=" * 60)
_log(f"invoice_samples: {invoice_folder}")
_log(f"output_dir:       {output_dir}")
_log(f"files to process: {len(files)}")
_log("=" * 60)

if not files:
    _log("")
    _log("No files found! Drop your PDFs into the invoice_samples/ folder:")
    _log(f"  {invoice_folder}")
    _log("")
    _log("Then re-run:")
    _log("  python run_sap_test.py")
    _log_fh.close()
    sys.exit(0)

# ── Authenticate + find schema ──────────────────────────────────────────────

# Use --cred-file if provided, otherwise rely on env vars
cred_file = None
if "--cred-file" in sys.argv:
    idx = sys.argv.index("--cred-file")
    cred_file = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None

_log("")
_log("Authenticating with SAP Document AI …")
client = DocAIClient(cred_file=cred_file)
_log(f"  UAA URL:  {client.uaa_url}")
_log(f"  DOX URL:  {client.dox_url}")

# Find invoice schema (same as sap_test.py)
_log("")
_log("Finding invoice schema …")
schema_id = client.find_schema("invoice")
_log(f"  schema_id: {schema_id or 'none found'}")

# ── Process each file ───────────────────────────────────────────────────────

_log("")
_log("=" * 60)
_log("Processing files")
_log("=" * 60)

summary = []
for path in files:
    name = os.path.basename(path)
    _log("")
    _log(f"── {name} ────────────────────────────────────────────")

    # Step 1: Upload + poll SAP Doc AI
    _log(f"  Uploading to SAP Doc AI …")
    try:
        job = client.upload_and_process(
            file_path=path,
            document_type="invoice",
            schema_id=schema_id,
        )
    except Exception as e:
        _log(f"  ERROR: {e}")
        summary.append({"file": name, "status": "UPLOAD_FAILED", "error": str(e)})
        continue

    _log(f"  SAP status: {job.get('status', 'unknown')}")

    # Step 2: Save raw SAP extraction JSON
    sap_json_path = os.path.join(output_dir, os.path.splitext(name)[0] + "_sap.json")
    with open(sap_json_path, "w", encoding="utf-8") as fh:
        json.dump(job, fh, indent=2)
    _log(f"  Raw SAP JSON saved: {sap_json_path}")

    # Print extracted header fields (like sap_test.py)
    extraction = job.get("extraction", {})
    header_fields = extraction.get("headerFields", [])
    line_items = extraction.get("lineItems", [])
    for f in header_fields:
        _log(f"    {f['name']}: {f.get('value')}  ({f.get('confidence') or 0:.2f})")
    _log(f"    line items: {len(line_items)}")

    # Step 3: Convert to pipeline format + run triage
    _log(f"  Running triage pipeline …")
    converted = DocAIClient.convert_results(job)

    result = run_triage(
        classification_extraction=converted["classification_extraction"],
        detailed_extraction=converted["detailed_extraction"],
        line_items=converted["line_items"] or None,
        file_name=name,
    )

    # Add SAP metadata
    result["sap_doc_ai"] = {
        "job_id": job.get("id", ""),
        "status": job.get("status", ""),
        "schema_id": schema_id or "",
    }

    # Step 4: Save triage result JSON
    triage_json_path = os.path.join(output_dir, os.path.splitext(name)[0] + "_triage.json")
    with open(triage_json_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, default=str)
    _log(f"  Triage JSON saved: {triage_json_path}")

    # Print triage summary
    _log("")
    _log(f"  TRIAGE RESULT:")
    _log(f"    document_type:       {result.get('document_type', '')}")
    _log(f"    company_class:      {result.get('company_classification', '')}")
    _log(f"    direct_intercompany: {result.get('direct_intercompany', '')}")
    _log(f"    invoice_type:       {result.get('invoice_type', '')}")
    _log(f"    confidence:         {result.get('confidence', 0):.2f}")
    _log(f"    review_required:    {result.get('review_required', False)}")
    if result.get("review_reason"):
        _log(f"    review_reason:     {result['review_reason']}")

    _log("")
    _log(f"  EVIDENCE TRAIL:")
    for i, e in enumerate(result.get("evidence", []), 1):
        _log(f"    {i:2d}. {e}")

    summary.append({
        "file": name,
        "status": job.get("status", ""),
        "document_type": result.get("document_type", ""),
        "invoice_type": result.get("invoice_type", ""),
        "company_classification": result.get("company_classification", ""),
        "confidence": result.get("confidence", 0),
        "review_required": result.get("review_required", False),
        "triage_json": triage_json_path,
    })

# ── Summary ────────────────────────────────────────────────────────────────

_log("")
_log("=" * 60)
_log("SUMMARY")
_log("=" * 60)
for s in summary:
    _log(f"  {s['file']:30s}  type={s.get('document_type', '?'):12s}  "
        f"invoice={s.get('invoice_type', '?'):8s}  "
        f"conf={s.get('confidence', 0):.2f}  "
        f"review={s.get('review_required', '?')}")
_log("")
_log(f"Done. Output saved to {output_dir}")
_log(f"  - Raw SAP JSON:   *_sap.json")
_log(f"  - Triage results: *_triage.json")
_log(f"  - Log:            run_log.txt")
_log_fh.close()

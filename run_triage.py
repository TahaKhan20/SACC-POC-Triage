#!/usr/bin/env python3
"""SACC-POC-Triage — Triage Agent CLI entry point (all triage modes).

  PAYLOAD MODE (default) — run the standalone triage pipeline on a JSON
  payload (SAP Document AI format, sample_data format, or generic
  headerFields/lineItems):

    python run_triage.py                               # data/doc_ai_payload.json
    python run_triage.py data/doc_ai_payload.json      # first document
    python run_triage.py data/doc_ai_payload.json 2    # third document (0-indexed)
    python run_triage.py data/doc_ai_payload.json all # all documents

  AI CORE MODE — process a document via SAP AI Core (GPT-5.4), then run
  the full triage pipeline on the extraction:

    python run_triage.py --ai-core path/to/invoice.pdf
    python run_triage.py --ai-core path/to/invoice.pdf --cred-file ai_core_cred.json

  DOCUMENT AI MODE — batch-process every PDF/image in invoice_samples/
  (or the files given) via SAP Document AI, run the triage pipeline on
  each, and save raw DOX JSON, triage JSON and a log to output/:

    python run_triage.py --doc-ai
    python run_triage.py --doc-ai invoice1.pdf invoice2.pdf
    python run_triage.py --doc-ai --cred-file sap_credentials.json
"""

import sys
from pathlib import Path

# Ensure src/ is on the path when running from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def run_doc_ai_batch(file_args: list[str]) -> None:
    """Batch triage via SAP Document AI (consolidated from run_sap_test.py)."""
    import glob
    import json
    import os

    from triage.doc_ai import DocAIClient
    from triage.runner import run_triage

    _dir = os.path.dirname(os.path.abspath(__file__))
    invoice_folder = os.path.join(_dir, "invoice_samples")
    output_dir = os.path.join(_dir, "output")
    os.makedirs(output_dir, exist_ok=True)

    log_path = os.path.join(output_dir, "run_log.txt")
    _log_fh = open(log_path, "w", encoding="utf-8")

    def _log(*args):
        line = " ".join(str(a) for a in args)
        print(line)
        _log_fh.write(line + "\n")
        _log_fh.flush()

    files = [
        f for f in glob.glob(os.path.join(invoice_folder, "*"))
        if f.lower().endswith((".pdf", ".png", ".jpg", ".jpeg"))
    ]
    if file_args and all(os.path.isfile(a) for a in file_args):
        files = file_args

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
        _log("  python run_triage.py --doc-ai")
        _log_fh.close()
        return

    # Use --cred-file if provided, otherwise rely on env vars / default lookup
    cred_file = None
    if "--cred-file" in file_args:
        idx = file_args.index("--cred-file")
        cred_file = file_args[idx + 1] if idx + 1 < len(file_args) else None

    _log("")
    _log("Authenticating with SAP Document AI …")
    client = DocAIClient(cred_file=cred_file)
    _log(f"  UAA URL:  {client.uaa_url}")
    _log(f"  DOX URL:  {client.dox_url}")

    _log("")
    _log("=" * 60)
    _log("Processing files")
    _log("=" * 60)

    summary = []
    for path in files:
        name = os.path.basename(path)
        _log("")
        _log(f"── {name} ────────────────────────────────────────────")

        # Step 1: Extract via SAP Document AI
        _log("  Extracting via SAP Document AI …")
        try:
            extraction = client.upload_and_process(file_path=path)
        except Exception as e:
            _log(f"  ERROR: {e}")
            summary.append({"file": name, "status": "EXTRACTION_FAILED", "error": str(e)})
            continue

        _log(f"  SAP status: {extraction.get('status', 'unknown')}")

        # Step 2: Save raw DOX extraction JSON
        ai_json_path = os.path.join(output_dir, os.path.splitext(name)[0] + "_dox.json")
        with open(ai_json_path, "w", encoding="utf-8") as fh:
            json.dump(extraction, fh, indent=2)
        _log(f"  Raw DOX JSON saved: {ai_json_path}")

        extraction_data = extraction.get("extraction", {})
        header_fields = extraction_data.get("headerFields", [])
        line_items = extraction_data.get("lineItems", [])
        for f in header_fields:
            _log(f"    {f['name']}: {f.get('value')}  ({f.get('confidence') or 0:.2f})")
        _log(f"    line items: {len(line_items)}")

        # Step 3: Convert to pipeline format + run triage
        _log("  Running triage pipeline …")
        converted = DocAIClient.convert_results(extraction)

        result = run_triage(
            classification_extraction=converted["classification_extraction"],
            detailed_extraction=converted["detailed_extraction"],
            line_items=converted["line_items"] or None,
            file_name=name,
        )

        result["sap_doc_ai"] = {
            "job_id": extraction.get("id", ""),
            "status": extraction.get("status", ""),
        }

        # Step 4: Save triage result JSON
        triage_json_path = os.path.join(output_dir, os.path.splitext(name)[0] + "_triage.json")
        with open(triage_json_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, default=str)
        _log(f"  Triage JSON saved: {triage_json_path}")

        _log("")
        _log("  TRIAGE RESULT:")
        _log(f"    document_type:       {result.get('document_type', '')}")
        _log(f"    company_class:      {result.get('company_classification', '')}")
        _log(f"    invoice_type:       {result.get('invoice_type', '')}")
        _log(f"    confidence:         {result.get('confidence', 0):.2f}")
        _log(f"    review_required:    {result.get('review_required', False)}")
        if result.get("review_reason"):
            _log(f"    review_reason:     {result['review_reason']}")

        _log("")
        _log("  EVIDENCE TRAIL:")
        for i, e in enumerate(result.get("evidence", []), 1):
            _log(f"    {i:2d}. {e}")

        summary.append({
            "file": name,
            "document_type": result.get("document_type", ""),
            "invoice_type": result.get("invoice_type", ""),
            "company_classification": result.get("company_classification", ""),
            "confidence": result.get("confidence", 0),
            "review_required": result.get("review_required", False),
            "triage_json": triage_json_path,
        })

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
    _log("  - Raw DOX JSON:    *_dox.json")
    _log("  - Triage results: *_triage.json")
    _log("  - Log:            run_log.txt")
    _log_fh.close()


def main() -> None:
    argv = sys.argv[1:]

    if argv and argv[0] == "--ai-core":
        # AI Core mode — delegate to triage.ai_core's argparse CLI
        sys.argv = ["run_triage.py"] + argv[1:]
        from triage.ai_core import main as ai_core_main
        ai_core_main()

    elif argv and argv[0] == "--doc-ai":
        # Document AI batch mode
        run_doc_ai_batch(argv[1:])

    else:
        # Payload mode — delegate to triage.runner's CLI
        from triage.runner import main as runner_main
        runner_main()


if __name__ == "__main__":
    main()
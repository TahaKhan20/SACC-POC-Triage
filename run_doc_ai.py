#!/usr/bin/env python3
"""SACC-POC-Triage SAP Document AI CLI entry point.

Upload a document to SAP Document AI, get extraction results, and run
the full triage pipeline.

Usage:
    python run_doc_ai.py path/to/invoice.pdf
    python run_doc_ai.py path/to/invoice.pdf --cred-file cred.json
    python run_doc_ai.py path/to/invoice.pdf --document-type invoice
"""

import sys
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from triage.doc_ai import main

if __name__ == "__main__":
    main()

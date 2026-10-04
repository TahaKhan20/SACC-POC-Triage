#!/usr/bin/env python3
"""SACC-POC-Triage CLI entry point.

Run the standalone triage pipeline on a JSON payload (SAP Document AI format,
sample_data format, or generic headerFields/lineItems).

Usage:
    python run.py                                    # uses data/doc_ai_payload.json
    python run.py data/doc_ai_payload.json           # first document
    python run.py data/doc_ai_payload.json 2         # third document (0-indexed)
    python run.py data/doc_ai_payload.json all       # all documents
"""

import sys
from pathlib import Path

# Ensure src/ is on the path when running from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from triage.runner import main

if __name__ == "__main__":
    main()

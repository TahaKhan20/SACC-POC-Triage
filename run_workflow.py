#!/usr/bin/env python3
"""SACC-POC-Triage Workflow CLI entry point.

Run the full email-to-triage workflow: fetch emails from Microsoft Graph →
classify relevance → triage document attachments.

Usage:
    python run_workflow.py                        # fetch + triage all emails
    python run_workflow.py --top 10               # limit to 10 emails
    python run_workflow.py --dry-run               # fetch + classify only
    python run_workflow.py --min-score 30          # custom min score
"""

import sys
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from workflow.orchestrator import main

if __name__ == "__main__":
    main()

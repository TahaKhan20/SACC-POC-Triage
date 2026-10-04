"""Configuration for the Email Intake Agent.

Scoring weights and document type keywords are read from environment
variables with sensible defaults. The shared document_types.json file
is loaded once at import time.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = Path(os.getenv("TRIAGE_CONFIG_DIR", str(PROJECT_ROOT / "config")))
DOCUMENT_TYPES_FILE = CONFIG_DIR / "document_types.json"

# ── Graph API Configuration ────────────────────────────────────────────────

GRAPH_API_BASE_URL = os.getenv("GRAPH_API_BASE_URL", "https://graph.microsoft.com")


def get_credentials() -> tuple[str, str]:
    """Read credentials from env vars at call time (not import time)."""
    return os.getenv("GRAPH_USER_ID", ""), os.getenv("GRAPH_API_TOKEN", "")


# ── Document type categories (loaded from shared document_types.json) ─────

with open(DOCUMENT_TYPES_FILE, "r", encoding="utf-8") as _f:
    _DOCUMENT_TYPES_DATA = json.load(_f)

DOCUMENT_TYPE_CATEGORIES = [
    (item["category"], item["document_type"])
    for item in _DOCUMENT_TYPES_DATA["categories"]
]

# Flat document type list derived from categories
DOC_TYPES = [dt for _cat, dts in DOCUMENT_TYPE_CATEGORIES for dt in dts]

# ── Document extensions ───────────────────────────────────────────────────

DOCUMENT_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff",
    ".doc", ".docx", ".xls", ".xlsx",
}

# ── Scoring Weights (tunable via env vars) ─────────────────────────────────

SUBJECT_DOC_TYPE_POINTS = int(os.getenv("SUBJECT_DOC_TYPE_POINTS", "20"))
BODY_DOC_TYPE_POINTS = int(os.getenv("BODY_DOC_TYPE_POINTS", "20"))
HAS_ATTACHMENT_POINTS = int(os.getenv("HAS_ATTACHMENT_POINTS", "30"))
ATTACHMENT_DOC_TYPE_POINTS = int(os.getenv("ATTACHMENT_DOC_TYPE_POINTS", "30"))
MIN_SCORE_THRESHOLD = int(os.getenv("MIN_SCORE_THRESHOLD", "50"))

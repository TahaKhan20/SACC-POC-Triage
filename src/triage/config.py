"""Configuration for the AP Invoice Triage Agent.

All configuration is read from environment variables with sensible defaults.
Load a .env file at application startup (e.g. via python-dotenv) or set
the variables directly in the deployment environment.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────────────────

# Project root = parent of src/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Config directory (document_types.json, etc.)
CONFIG_DIR = Path(os.getenv("TRIAGE_CONFIG_DIR", str(PROJECT_ROOT / "config")))

# Data directory (sample payloads, etc.)
DATA_DIR = Path(os.getenv("TRIAGE_DATA_DIR", str(PROJECT_ROOT / "data")))

# ── Company configuration ──────────────────────────────────────────────────

CONFIGURED_COMPANY_CODE = os.getenv("CONFIGURED_COMPANY_CODE", "1000")
CONFIGURED_COMPANY_NAME = os.getenv("CONFIGURED_COMPANY_NAME", "SACC Airlines")
CONFIGURED_COMPANY_VAT = os.getenv("CONFIGURED_COMPANY_VAT", "")
CONFIGURED_COMPANY_TAX_IDS = [
    tid.strip()
    for tid in os.getenv("CONFIGURED_COMPANY_TAX_IDS", "").split(",")
    if tid.strip()
]

# ── Intercompany company codes ────────────────────────────────────────────

INTERCOMPANY_COMPANY_CODES = [
    code.strip()
    for code in os.getenv("INTERCOMPANY_COMPANY_CODES", "2000,3000").split(",")
    if code.strip()
]

# ── Confidence thresholds ─────────────────────────────────────────────────

MIN_CONFIDENCE_THRESHOLD = float(os.getenv("MIN_CONFIDENCE_THRESHOLD", "0.75"))
MIN_REQUIRED_FIELD_CONFIDENCE = float(
    os.getenv("MIN_REQUIRED_FIELD_CONFIDENCE", "0.80")
)

# ── Optional: SAP Document AI real API credentials ─────────────────────────

SAP_DOCAI_BASE_URL = os.getenv("SAP_DOCAI_BASE_URL", "")
SAP_DOCAI_CLIENT_ID = os.getenv("SAP_DOCAI_CLIENT_ID", "")
SAP_DOCAI_CLIENT_SECRET = os.getenv("SAP_DOCAI_CLIENT_SECRET", "")
SAP_DOCAI_AUTH_URL = os.getenv("SAP_DOCAI_AUTH_URL", "")
SAP_DOCAI_TOKEN: Optional[str] = None

# ── Optional: LLM configuration ─────────────────────────────────────────────

LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gpt-4")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")

# ── Document types config file ─────────────────────────────────────────────

DOCUMENT_TYPES_FILE = CONFIG_DIR / "document_types.json"

"""Configuration for the Email Intake Agent.

Only Graph API configuration is needed for fetch + enrich.
Scoring/classification is handled by the Triage Agent via SAP AI Core.
"""

from __future__ import annotations

import os

# -- Graph API Configuration -------------------------------------------------

GRAPH_API_BASE_URL = os.getenv("GRAPH_API_BASE_URL", "https://graph.microsoft.com")


def get_credentials() -> tuple[str, str]:
    """Read credentials from env vars at call time (not import time)."""
    return os.getenv("GRAPH_USER_ID", ""), os.getenv("GRAPH_API_TOKEN", "")


# -- Document extensions (for attachment filtering) -------------------------

DOCUMENT_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff",
    ".doc", ".docx", ".xls", ".xlsx",
}

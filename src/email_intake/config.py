"""Configuration for the Email Intake Agent.

Only Graph API configuration is needed for fetch + enrich.
Scoring/classification is handled by the Triage Agent via SAP AI Core.
"""

from __future__ import annotations

import os
from pathlib import Path

# -- Graph API Configuration -------------------------------------------------

# Project root (parent of src/email_intake) — where a .env file may live.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_env_file() -> None:
    """Load a .env file (project root or config/) if python-dotenv is installed.

    Values already present in the environment are NOT overridden, so real
    exported env vars always win over .env contents.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for env_path in (_PROJECT_ROOT / ".env", _PROJECT_ROOT / "config" / ".env"):
        if env_path.is_file():
            load_dotenv(env_path, override=False)


_load_env_file()

GRAPH_API_BASE_URL = os.getenv("GRAPH_API_BASE_URL", "https://graph.microsoft.com")


def get_credentials() -> tuple[str, str]:
    """Read credentials from env vars at call time (not import time).

    A .env file at the project root or in config/ is loaded first (if
    python-dotenv is installed), so credentials can be supplied via .env
    instead of exported shell variables.
    """
    _load_env_file()
    return os.getenv("GRAPH_USER_ID", ""), os.getenv("GRAPH_API_TOKEN", "")


# -- Document extensions (for attachment filtering) -------------------------

DOCUMENT_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff",
    ".doc", ".docx", ".xls", ".xlsx",
}

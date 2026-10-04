"""Email Intake Agent — standalone email fetch, enrich, and classify.

Public API::

    from email_intake import run_intake, run_intake_standalone, classify_emails
    from email_intake import EmailMessage, save_attachment_to_temp
    from email_intake import score_with_details, score_email_relevance, categorize_email
"""

from .classify import (
    categorize_email,
    classify_emails,
    has_document_attachment,
    score_email_relevance,
    score_with_details,
)
from .config import DOCUMENT_EXTENSIONS
from .fetch import enrich_emails, fetch_emails
from .models import EmailMessage
from .runner import run_intake, run_intake_standalone, save_attachment_to_temp

__all__ = [
    # Models
    "EmailMessage",
    # Config
    "DOCUMENT_EXTENSIONS",
    # Classification (standalone)
    "categorize_email",
    "classify_emails",
    "has_document_attachment",
    "score_email_relevance",
    "score_with_details",
    # Fetch (requires httpx + Graph API)
    "enrich_emails",
    "fetch_emails",
    # Runner
    "run_intake",
    "run_intake_standalone",
    "save_attachment_to_temp",
]

__version__ = "1.0.0"

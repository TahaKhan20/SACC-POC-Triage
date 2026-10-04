"""Email Intake Agent runner — orchestrates fetch → enrich → classify.

The runner can operate in two modes:
  1. **Live mode** — fetches real emails from Microsoft Graph (requires
     httpx and valid GRAPH_USER_ID / GRAPH_API_TOKEN env vars).
  2. **Standalone mode** — accepts pre-constructed EmailMessage objects
     for classification without any API calls.
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from .classify import classify_emails
from .config import get_credentials
from .fetch import enrich_emails, fetch_emails
from .models import EmailMessage

logger = logging.getLogger("email_intake_agent")


# ── Attachment Helper ────────────────────────────────────────────────────────


def save_attachment_to_temp(attachment: dict[str, Any]) -> Optional[str]:
    """Save a Graph API attachment (base64) to a temp file and return the path."""
    att_name = attachment.get("name", "attachment")
    content_bytes = attachment.get("contentBytes", "")

    if not content_bytes:
        logger.warning("Attachment '%s' has no contentBytes", att_name)
        return None

    try:
        decoded = base64.b64decode(content_bytes)
    except Exception as exc:
        logger.warning("Could not decode attachment '%s': %s", att_name, exc)
        return None

    suffix = Path(att_name).suffix or ".pdf"
    fd, temp_path = tempfile.mkstemp(suffix=suffix, prefix="triage_")
    with os.fdopen(fd, "wb") as f:
        f.write(decoded)

    logger.info("Saved attachment '%s' to temp file: %s (%d bytes)", att_name, temp_path, len(decoded))
    return temp_path


# ── Orchestrator ────────────────────────────────────────────────────────────


def run_intake(
    top: int = 50,
    min_score: int = 50,
) -> dict[str, Any]:
    """Run all three intake nodes: fetch → enrich → classify.

    Credentials are read from env vars GRAPH_USER_ID and GRAPH_API_TOKEN
    (or a .env file via python-dotenv) at call time.

    Returns dict with total_emails, relevant_emails (list of EmailMessage),
    and any errors.
    """
    user_id, api_token = get_credentials()
    if not user_id or not api_token:
        return {"error": "Missing GRAPH_USER_ID or GRAPH_API_TOKEN env var", "total_emails": 0, "relevant_emails": []}

    logger.info("=" * 60)
    logger.info("STARTING EMAIL INTAKE AGENT")
    logger.info("=" * 60)

    emails = fetch_emails(user_id, api_token, top=top)
    if not emails:
        logger.warning("No emails fetched")
        return {"total_emails": 0, "relevant_emails": [], "errors": ["No emails fetched"]}

    emails = enrich_emails(emails, user_id, api_token)
    relevant = classify_emails(emails, min_score=min_score)

    return {"total_emails": len(emails), "relevant_emails": relevant, "errors": []}


def run_intake_standalone(
    emails: list[EmailMessage],
    min_score: int = 50,
) -> dict[str, Any]:
    """Run classification only on pre-supplied EmailMessage objects.

    No Graph API calls — useful for testing and offline workflows.
    """
    logger.info("=" * 60)
    logger.info("STARTING EMAIL INTAKE (STANDALONE MODE)")
    logger.info("=" * 60)

    relevant = classify_emails(emails, min_score=min_score)
    return {"total_emails": len(emails), "relevant_emails": relevant, "errors": []}


# ── CLI Entry Point ─────────────────────────────────────────────────────────


def main() -> None:
    """CLI entry point — fetch and classify emails (no triage)."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Email Intake Agent: fetch and classify emails from Microsoft Graph.",
    )
    parser.add_argument("--top", type=int, default=50, help="Max emails to fetch")
    parser.add_argument("--min-score", type=int, default=50, help="Min relevance score")
    args = parser.parse_args()

    result = run_intake(top=args.top, min_score=args.min_score)

    print("\n" + "=" * 60)
    print("EMAIL INTAKE RESULTS")
    print("=" * 60)
    print(json.dumps({
        "total_emails": result["total_emails"],
        "relevant_emails": len(result["relevant_emails"]),
        "errors": result.get("errors", []),
    }, indent=2))

    for email in result["relevant_emails"]:
        print(f"  [{email.category}] score={email.score}  '{email.subject[:50]}'  from {email.sender}  — {len(email.attachments)} attachments")
        for line in email.score_details:
            print(f"    {line}")

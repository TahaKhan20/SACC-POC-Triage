"""Email-to-Triage Workflow Orchestrator.

Thin orchestrator that connects the Email Intake Agent to the Triage Agent:

    1.  EMAIL INTAKE  – delegates to email_intake.run_intake() which
        fetches emails from Microsoft Graph, enriches them with full body
        content and attachments, and classifies which emails are relevant.
    2.  TRIAGE         – for each relevant email with document attachments,
        runs the triage pipeline (run_triage) on the extraction data.

The triage step uses the standalone triage package (no SACC mock API needed).
When the Graph API is not available, the workflow can accept pre-supplied
EmailMessage objects via run_workflow_standalone().
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from email_intake import (
    DOCUMENT_EXTENSIONS,
    EmailMessage,
    classify_emails,
    run_intake,
    save_attachment_to_temp,
)
from triage import run_triage, run_triage_from_payload

logger = logging.getLogger("workflow")


# ── Triage Node ────────────────────────────────────────────────────────────


def triage_documents(
    relevant_emails: list[EmailMessage],
    dry_run: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """For each relevant email with document attachments, call the Triage Agent.

    Returns (triage_results, errors).
    """
    if dry_run:
        logger.info("TRIAGE: DRY RUN — skipping triage (would triage %d emails)", len(relevant_emails))
        return [], []

    triage_results: list[dict[str, Any]] = []
    errors: list[str] = []

    logger.info("TRIAGE: Processing %d relevant emails …", len(relevant_emails))

    for email in relevant_emails:
        logger.info("─" * 60)
        logger.info("Email: '%s' from %s", email.subject[:80], email.sender)

        if not email.attachments:
            logger.info("  No attachments — skipping triage")
            continue

        for attachment in email.attachments:
            att_name = attachment.get("name", "unknown")
            att_lower = att_name.lower()

            # Only triage document-type attachments
            if not any(att_lower.endswith(ext) for ext in DOCUMENT_EXTENSIONS):
                logger.info("  Skipping non-document attachment: %s", att_name)
                continue

            logger.info("  Triaging attachment: %s", att_name)

            # Save attachment to temp file
            temp_path = save_attachment_to_temp(attachment)
            if not temp_path:
                errors.append(f"Could not save attachment '{att_name}' from email '{email.subject}'")
                continue

            try:
                # Call the standalone Triage Agent entry point
                # The triage agent expects extraction data, not raw files.
                # We build a payload from the email context and attachment info.
                payload = {
                    "headerFields": {
                        "document_type": email.category,
                        "attachment": att_name,
                    },
                    "lineItems": [],
                }

                # Try to use headerFields from email body if available
                if email.body_content:
                    payload["headerFields"]["email_body"] = email.body_content[:500]

                result = run_triage_from_payload(payload)

                # Override document_type with email category
                result["document_type"] = email.category

                # Enrich result with email context
                result["email_context"] = {
                    "message_id": email.message_id,
                    "subject": email.subject,
                    "sender": email.sender,
                    "sender_name": email.sender_name,
                    "recipients": email.recipients,
                    "received_date": email.received_date,
                    "attachment_name": att_name,
                }

                triage_results.append(result)
                logger.info("  Category: %s", email.category)
                logger.info("  Triage result: type=%s company=%s direct_intercompany=%s review=%s",
                            result.get("document_type"),
                            result.get("company_classification"),
                            result.get("direct_intercompany"),
                            result.get("review_required"))

            except Exception as exc:
                logger.error("  Triage failed for '%s': %s", att_name, exc)
                errors.append(f"Triage error for '{att_name}': {exc}")
            finally:
                # Clean up temp file
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)

    logger.info("Triage complete: %d results, %d errors", len(triage_results), len(errors))
    return triage_results, errors


# ── Workflow Orchestrator ────────────────────────────────────────────────────


def run_workflow(
    top: int = 50,
    dry_run: bool = False,
    min_score: int = 20,
) -> dict[str, Any]:
    """Run the full email-to-triage workflow.

    Delegates email fetching, enrichment, and classification to the Email
    Intake Agent, then runs the Triage Agent on relevant document attachments.

    Credentials (GRAPH_USER_ID, GRAPH_API_TOKEN) are read from environment
    variables by the Email Intake Agent.

    Args:
        top: Maximum number of emails to fetch from the mailbox.
        dry_run: If True, fetch and classify emails but skip triage.
        min_score: Minimum relevance score for email classification.

    Returns:
        Summary dict with email counts, triage results, and errors.
    """
    logger.info("=" * 60)
    logger.info("STARTING EMAIL-TO-TRIAGE WORKFLOW")
    logger.info("=" * 60)

    # Step 1: Email Intake Agent — fetch, enrich, classify
    intake_result = run_intake(top=top, min_score=min_score)

    if intake_result.get("error"):
        return {"error": intake_result["error"], "triage_results": [], "errors": [intake_result["error"]]}

    relevant_emails = intake_result["relevant_emails"]
    if not relevant_emails:
        logger.warning("No relevant emails found — nothing to triage")
        return {
            "total_emails_fetched": intake_result["total_emails"],
            "relevant_emails": 0,
            "triage_results_count": 0,
            "triage_results": [],
            "errors": intake_result.get("errors", []),
            "dry_run": dry_run,
        }

    # Step 2: Triage Agent — classify documents from relevant emails
    triage_results, triage_errors = triage_documents(relevant_emails, dry_run=dry_run)

    return {
        "total_emails_fetched": intake_result["total_emails"],
        "relevant_emails": len(relevant_emails),
        "triage_results_count": len(triage_results),
        "triage_results": triage_results,
        "errors": intake_result.get("errors", []) + triage_errors,
        "dry_run": dry_run,
    }


def run_workflow_standalone(
    emails: list[EmailMessage],
    dry_run: bool = False,
    min_score: int = 50,
) -> dict[str, Any]:
    """Run the workflow on pre-supplied EmailMessage objects (no Graph API).

    Useful for testing and offline pipelines.
    """
    logger.info("=" * 60)
    logger.info("STARTING EMAIL-TO-TRIAGE WORKFLOW (STANDALONE MODE)")
    logger.info("=" * 60)

    relevant_emails = classify_emails(emails, min_score=min_score)

    if not relevant_emails:
        return {
            "total_emails_fetched": len(emails),
            "relevant_emails": 0,
            "triage_results_count": 0,
            "triage_results": [],
            "errors": [],
            "dry_run": dry_run,
        }

    triage_results, triage_errors = triage_documents(relevant_emails, dry_run=dry_run)

    return {
        "total_emails_fetched": len(emails),
        "relevant_emails": len(relevant_emails),
        "triage_results_count": len(triage_results),
        "triage_results": triage_results,
        "errors": triage_errors,
        "dry_run": dry_run,
    }


# ── CLI Entry Point ─────────────────────────────────────────────────────────


def main() -> None:
    """CLI entry point for the workflow."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Email-to-Triage Workflow: connect Email Intake Agent to Triage Agent.",
    )
    parser.add_argument("--top", type=int, default=50, help="Max emails to fetch (default: 50)")
    parser.add_argument("--min-score", type=int, default=20, help="Min relevance score (default: 20)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and classify only, skip triage")
    args = parser.parse_args()

    summary = run_workflow(
        top=args.top,
        dry_run=args.dry_run,
        min_score=args.min_score,
    )

    print("\n" + "=" * 60)
    print("WORKFLOW SUMMARY")
    print("=" * 60)
    print(json.dumps(summary, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()

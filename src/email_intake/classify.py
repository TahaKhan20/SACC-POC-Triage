"""Email classification and scoring logic — fully standalone.

All functions in this module operate purely on EmailMessage objects.
No HTTP calls, no database access, no external API dependencies.

Scoring rules (tunable via config):
    1.  Document type keyword in subject   → 20 pts
    2.  Document type keyword in body       → 20 pts
    3.  Has any attachment                 → 30 pts
    4.  Document type in attachment name   → 30 pts
    Emails scoring >= MIN_SCORE_THRESHOLD (default 50) are "relevant".
"""

from __future__ import annotations

import logging
from typing import Any

from .config import (
    ATTACHMENT_DOC_TYPE_POINTS,
    BODY_DOC_TYPE_POINTS,
    DOC_TYPES,
    DOCUMENT_EXTENSIONS,
    DOCUMENT_TYPE_CATEGORIES,
    HAS_ATTACHMENT_POINTS,
    MIN_SCORE_THRESHOLD,
    SUBJECT_DOC_TYPE_POINTS,
)
from .models import EmailMessage

logger = logging.getLogger("email_intake_agent")


# ── Category assignment ─────────────────────────────────────────────────────


def categorize_email(email: EmailMessage) -> str:
    """Return a category label based on which document type group matches first.

    Checks subject, body, and attachment names against each group in order.
    Returns 'General Correspondence' if no group matches.
    """
    combined = " ".join([
        email.subject.lower(),
        email.body_preview.lower(),
        email.body_content.lower(),
    ])
    for att in email.attachments:
        combined += " " + att.get("name", "").lower()

    for category, doc_types in DOCUMENT_TYPE_CATEGORIES:
        if any(dt in combined for dt in doc_types):
            return category
    return "General Correspondence"


# ── Attachment helpers ──────────────────────────────────────────────────────


def has_document_attachment(email: EmailMessage) -> bool:
    """Check if email has at least one document-type attachment."""
    for att in email.attachments:
        name = att.get("name", "").lower()
        if any(name.endswith(ext) for ext in DOCUMENT_EXTENSIONS):
            return True
    return False


# ── Scoring ─────────────────────────────────────────────────────────────────


def score_with_details(email: EmailMessage) -> tuple[int, list[dict[str, Any]]]:
    """Score an email and return (score, breakdown of each rule).

    Each rule entry: {"rule": str, "matched": bool, "points": int, "document_types": list}
    """
    subject_lower = email.subject.lower()
    body_lower = (email.body_preview + " " + email.body_content).lower()

    details: list[dict[str, Any]] = []
    score = 0

    # Rule 1: document type in subject
    matched = [dt for dt in DOC_TYPES if dt in subject_lower]
    hit = bool(matched)
    score += SUBJECT_DOC_TYPE_POINTS if hit else 0
    details.append({"rule": "document type in subject", "matched": hit,
                    "points": SUBJECT_DOC_TYPE_POINTS if hit else 0, "document_types": matched})

    # Rule 2: document type in body
    matched = [dt for dt in DOC_TYPES if dt in body_lower]
    hit = bool(matched)
    score += BODY_DOC_TYPE_POINTS if hit else 0
    details.append({"rule": "document type in body", "matched": hit,
                    "points": BODY_DOC_TYPE_POINTS if hit else 0, "document_types": matched})

    # Rule 3: has any attachment
    has_att = email.has_attachments or bool(email.attachments)
    score += HAS_ATTACHMENT_POINTS if has_att else 0
    details.append({"rule": "has attachment", "matched": has_att,
                    "points": HAS_ATTACHMENT_POINTS if has_att else 0, "document_types": []})

    # Rule 4: document type in attachment name
    matched = []
    for att in email.attachments:
        att_name = att.get("name", "").lower()
        for dt in DOC_TYPES:
            if dt in att_name and dt not in matched:
                matched.append(dt)
    hit = bool(matched)
    score += ATTACHMENT_DOC_TYPE_POINTS if hit else 0
    details.append({"rule": "document type in attachment name", "matched": hit,
                    "points": ATTACHMENT_DOC_TYPE_POINTS if hit else 0, "document_types": matched})

    return min(100, score), details


def score_email_relevance(email: EmailMessage) -> int:
    """Score how likely this email contains a document for triage (0-100)."""
    score, _ = score_with_details(email)
    return score


def classify_emails(
    emails: list[EmailMessage],
    min_score: int = MIN_SCORE_THRESHOLD,
) -> list[EmailMessage]:
    """Classify emails by sender, subject, content, and attachments.

    Returns the subset of emails whose relevance score >= min_score.
    Logs scoring breakdown for both relevant and skipped emails.
    """
    logger.info("Classifying %d emails (min_score=%d) …", len(emails), min_score)

    relevant: list[EmailMessage] = []
    for email in emails:
        score, details = score_with_details(email)
        # Build human-readable breakdown lines
        breakdown = []
        for d in details:
            mark = "\u2713" if d["matched"] else "\u2717"
            kws = f"  [{', '.join(d['document_types'])}]" if d["document_types"] else ""
            breakdown.append(f"  {mark} {d['rule']} ({d['points']}pts){kws}")

        if score >= min_score:
            email.score = score
            email.category = categorize_email(email)
            email.score_details = breakdown
            relevant.append(email)
            logger.info("  \u2713 RELEVANT  score=%3d  category=%s  subject='%s'  from=%s  attachments=%d",
                        score, email.category, email.subject[:60], email.sender, len(email.attachments))
            for line in breakdown:
                logger.info("    %s", line)
        else:
            logger.info("  \u2717 SKIP  score=%3d  (needs %d more for threshold %d)  subject='%s'",
                        min_score - score, min_score, email.subject[:60])
            for line in breakdown:
                logger.info("    %s", line)

    logger.info("Classification: %d relevant emails out of %d total", len(relevant), len(emails))
    return relevant

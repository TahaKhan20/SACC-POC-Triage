"""Graph API fetch and enrich functions.

These functions make HTTP calls to the Microsoft Graph API (or a compatible
proxy). They require the ``httpx`` package and valid Graph API credentials.

When credentials are unavailable, they return empty results gracefully so
the rest of the pipeline (classification, scoring) can still be tested with
manually-constructed EmailMessage objects.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import GRAPH_API_BASE_URL
from .models import EmailMessage

logger = logging.getLogger("email_intake_agent")


# ── Node 1: Fetch Emails ───────────────────────────────────────────────────


def fetch_emails(user_id: str, api_token: str, top: int = 50) -> list[EmailMessage]:
    """List messages from the mailbox via GET /v1.0/users/{user_id}/messages."""
    logger.info("Node 1: Fetching emails from Graph API for user %s …", user_id)

    url = f"{GRAPH_API_BASE_URL}/v1.0/users/{user_id}/messages"
    headers = {"Accept": "application/json"}
    params = {
        "api_token": api_token,
        "$top": top,
        "$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview,"
                    "hasAttachments,importance,isRead",
    }

    try:
        import httpx
    except ImportError:
        logger.error("httpx not installed — cannot fetch emails")
        return []

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, headers=headers, params=params)
            logger.info("Request URL: %s", resp.request.url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("Error fetching emails: %s", exc)
        return []

    messages = data.get("value", [])
    logger.info("Fetched %d messages", len(messages))

    emails: list[EmailMessage] = []
    for msg in messages:
        sender_obj = msg.get("from", {})
        sender_email = sender_obj.get("emailAddress", {}).get("address", "")
        sender_name = sender_obj.get("emailAddress", {}).get("name", "")
        recipients = [
            r.get("emailAddress", {}).get("address", "")
            for r in msg.get("toRecipients", [])
        ]
        emails.append(EmailMessage(
            message_id=msg.get("id", ""),
            subject=msg.get("subject", ""),
            sender=sender_email,
            sender_name=sender_name,
            recipients=recipients,
            received_date=msg.get("receivedDateTime", ""),
            body_preview=msg.get("bodyPreview", ""),
            has_attachments=msg.get("hasAttachments", False),
            importance=msg.get("importance", "normal"),
            is_read=msg.get("isRead", False),
        ))

    return emails


# ── Node 2: Enrich Emails ──────────────────────────────────────────────────


def enrich_emails(emails: list[EmailMessage], user_id: str, api_token: str) -> list[EmailMessage]:
    """Fetch full body content and attachment metadata for each email."""
    logger.info("Node 2: Enriching %d emails with full body and attachments …", len(emails))

    try:
        import httpx
    except ImportError:
        logger.error("httpx not installed — cannot enrich emails")
        return emails

    headers = {"Accept": "application/json"}
    base = f"{GRAPH_API_BASE_URL}/v1.0/users/{user_id}/messages"

    with httpx.Client(timeout=30.0) as client:
        for email in emails:
            try:
                params = {
                    "api_token": api_token,
                    "$select": "id,subject,body,from,toRecipients",
                }
                resp = client.get(
                    f"{base}/{email.message_id}",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                full_msg = resp.json()
                body_obj = full_msg.get("body", {})
                email.body_content = body_obj.get("content", "")
            except Exception as exc:
                logger.warning("Could not fetch body for message %s: %s", email.message_id[:20], exc)

            if email.has_attachments:
                try:
                    resp = client.get(
                        f"{base}/{email.message_id}/attachments",
                        headers=headers,
                        params={"api_token": api_token},
                    )
                    resp.raise_for_status()
                    att_data = resp.json()
                    email.attachments = att_data.get("value", [])
                except Exception as exc:
                    logger.warning("Could not fetch attachments for %s: %s", email.message_id[:20], exc)

    logger.info(
        "Enrichment complete: %d emails with body content, %d with attachments",
        sum(1 for e in emails if e.body_content),
        sum(1 for e in emails if e.attachments),
    )
    return emails

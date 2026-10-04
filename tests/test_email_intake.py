"""Unit tests for the email intake classification logic.

These tests verify the scoring and classification functions without
any external API dependency. Run with:

    pytest tests/
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from email_intake import (
    EmailMessage,
    categorize_email,
    classify_emails,
    has_document_attachment,
    score_email_relevance,
    score_with_details,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def invoice_email():
    """Email with invoice keywords and a PDF attachment."""
    return EmailMessage(
        message_id="msg-001",
        subject="Invoice for March 2024",
        sender="supplier@example.com",
        body_preview="Please find attached the invoice for March.",
        body_content="Dear team, please find attached the invoice for March 2024 services.",
        has_attachments=True,
        attachments=[{"name": "invoice_march_2024.pdf", "contentBytes": ""}],
    )


@pytest.fixture
def non_relevant_email():
    """Email with no document keywords and no attachments."""
    return EmailMessage(
        message_id="msg-002",
        subject="Weekly newsletter",
        sender="newsletter@company.com",
        body_preview="Check out our latest deals!",
        body_content="This week we have amazing offers for you.",
        has_attachments=False,
        attachments=[],
    )


@pytest.fixture
def credit_note_email():
    """Email with credit note keyword."""
    return EmailMessage(
        message_id="msg-003",
        subject="Credit note for order #123",
        sender="billing@example.com",
        body_preview="Credit note attached.",
        body_content="Please process this credit note for order #123.",
        has_attachments=True,
        attachments=[{"name": "credit_note_123.pdf", "contentBytes": ""}],
    )


# ── Categorization Tests ────────────────────────────────────────────────────


class TestCategorizeEmail:
    def test_invoice_category(self, invoice_email):
        assert categorize_email(invoice_email) == "Invoice"

    def test_credit_note_category(self, credit_note_email):
        assert categorize_email(credit_note_email) == "Credit Note"

    def test_general_correspondence(self, non_relevant_email):
        assert categorize_email(non_relevant_email) == "General Correspondence"


# ── Scoring Tests ───────────────────────────────────────────────────────────


class TestScoreWithDetails:
    def test_invoice_email_scores_high(self, invoice_email):
        score, details = score_with_details(invoice_email)
        assert score >= 50
        assert len(details) == 4  # 4 rules

    def test_non_relevant_email_scores_low(self, non_relevant_email):
        score, details = score_with_details(non_relevant_email)
        assert score < 50
        assert len(details) == 4

    def test_subject_match(self, invoice_email):
        score, details = score_with_details(invoice_email)
        subject_rule = details[0]
        assert subject_rule["matched"] is True
        assert subject_rule["points"] > 0
        assert "invoice" in subject_rule["document_types"]

    def test_attachment_match(self, invoice_email):
        score, details = score_with_details(invoice_email)
        att_rule = details[3]
        assert att_rule["matched"] is True
        assert att_rule["points"] > 0

    def test_no_attachment_no_points(self, non_relevant_email):
        score, details = score_with_details(non_relevant_email)
        att_rule = details[2]  # has attachment rule
        assert att_rule["matched"] is False
        assert att_rule["points"] == 0

    def test_score_capped_at_100(self):
        email = EmailMessage(
            message_id="msg",
            subject="invoice credit note statement",
            sender="test@test.com",
            body_preview="invoice credit note statement",
            body_content="invoice credit note statement",
            has_attachments=True,
            attachments=[{"name": "invoice.pdf", "contentBytes": ""}],
        )
        score, _ = score_with_details(email)
        assert score <= 100


class TestScoreEmailRelevance:
    def test_invoice_score(self, invoice_email):
        assert score_email_relevance(invoice_email) >= 50

    def test_non_relevant_score(self, non_relevant_email):
        assert score_email_relevance(non_relevant_email) < 50


# ── Document Attachment Tests ───────────────────────────────────────────────


class TestHasDocumentAttachment:
    def test_pdf_attachment(self, invoice_email):
        assert has_document_attachment(invoice_email) is True

    def test_no_attachment(self, non_relevant_email):
        assert has_document_attachment(non_relevant_email) is False

    def test_non_document_attachment(self):
        email = EmailMessage(
            message_id="msg",
            subject="Test",
            sender="test@test.com",
            has_attachments=True,
            attachments=[{"name": "photo.zip", "contentBytes": ""}],
        )
        assert has_document_attachment(email) is False


# ── Classification Tests ───────────────────────────────────────────────────


class TestClassifyEmails:
    def test_relevant_email_included(self, invoice_email):
        result = classify_emails([invoice_email], min_score=50)
        assert len(result) == 1
        assert result[0].score >= 50
        assert result[0].category == "Invoice"

    def test_non_relevant_excluded(self, non_relevant_email):
        result = classify_emails([non_relevant_email], min_score=50)
        assert len(result) == 0

    def test_mixed_emails(self, invoice_email, non_relevant_email):
        result = classify_emails([invoice_email, non_relevant_email], min_score=50)
        assert len(result) == 1
        assert result[0].message_id == invoice_email.message_id

    def test_score_details_populated(self, invoice_email):
        result = classify_emails([invoice_email], min_score=50)
        assert len(result[0].score_details) > 0

    def test_empty_list(self):
        assert classify_emails([]) == []

    def test_custom_min_score(self, invoice_email):
        # With a very high threshold, even the invoice email is excluded
        result = classify_emails([invoice_email], min_score=200)
        assert len(result) == 0

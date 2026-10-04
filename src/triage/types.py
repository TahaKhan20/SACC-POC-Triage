"""Domain types and enums for the AP Invoice Triage Agent.

Contains all domain enums (DocumentType, CompanyClassification, etc.) and
dataclasses (TriageResult, ExtractedField, LineItem) used throughout the
triage pipeline. Zero external dependencies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# -- Enums ------------------------------------------------------------------


class DocumentType(str, Enum):
    """Document type classification."""
    INVOICE = "INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"
    STATEMENT = "STATEMENT"
    SUPPORTING_DOCUMENT = "SUPPORTING_DOCUMENT"
    RECONCILIATION = "RECONCILIATION"
    PURCHASE_ORDER_LIST = "PURCHASE_ORDER_LIST"
    GENERAL_CORRESPONDENCE = "GENERAL_CORRESPONDENCE"
    OTHER = "OTHER"


class CompanyClassification(str, Enum):
    """Company match classification."""
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    UNCERTAIN = "UNCERTAIN"


class InvoiceType(str, Enum):
    """Invoice type classification."""
    FUEL = "FUEL"
    CARGO = "CARGO"
    CHARTER = "CHARTER"
    SERVICE = "SERVICE"
    OTHER = "OTHER"


# -- Dataclasses ------------------------------------------------------------


@dataclass
class ExtractedField:
    """A single extracted field with confidence score."""
    name: str
    value: Any
    confidence: float = 0.0
    raw_value: Optional[str] = None


@dataclass
class LineItem:
    """A line item extracted from the document."""
    item_number: str = ""
    description: str = ""
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    total_amount: Optional[float] = None
    gl_account: str = ""
    cost_center: str = ""
    tax_code: str = ""
    confidence: float = 0.0
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class TriageResult:
    """Strict JSON TriageResult returned at the end of the workflow."""
    document_type: str
    company_classification: str
    invoice_type: str
    extracted_header_fields: dict[str, Any]
    line_items: list[dict[str, Any]]
    confidence: float
    evidence: list[str]
    review_required: bool
    review_reason: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_type": self.document_type,
            "company_classification": self.company_classification,
            "invoice_type": self.invoice_type,
            "extracted_header_fields": self.extracted_header_fields,
            "line_items": self.line_items,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "review_required": self.review_required,
            "review_reason": self.review_reason,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)

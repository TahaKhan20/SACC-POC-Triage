"""SACC-POC-Triage: Standalone AP Invoice Triage Agent.

A production-ready pipeline that triages AP invoices using pure Python
classification and validation logic. No external API server required —
extraction data is passed in directly.

Optional: SAP Document AI integration for real document upload + extraction
(see ``triage.doc_ai`` module).

Public API::

    from triage import run_triage, run_triage_from_payload
    from triage import TriageResult, TriageState
    from triage import DocumentType, CompanyClassification, DirectIntercompany, InvoiceType
    from triage import DocAIClient, run_triage_with_doc_ai
"""

from .models import (
    CompanyClassification,
    DirectIntercompany,
    DocumentType,
    ExtractedField,
    InvoiceType,
    LineItem,
    TriageResult,
)
from .pipeline import (
    build_lookup,
    build_triage_result,
    classify_company,
    classify_direct_intercompany,
    classify_invoice_type,
    convert_header_fields,
    determine_document_type,
    extract_detailed_fields,
    extract_value,
    normalize_payload,
    review_node,
    validate_extraction,
)
from .doc_ai import DocAIClient, run_triage_with_doc_ai
from .runner import run_triage, run_triage_from_payload
from .state import TriageState, initial_state

__all__ = [
    # Models
    "CompanyClassification",
    "DirectIntercompany",
    "DocumentType",
    "ExtractedField",
    "InvoiceType",
    "LineItem",
    "TriageResult",
    # State
    "TriageState",
    "initial_state",
    # Pipeline steps
    "build_lookup",
    "build_triage_result",
    "classify_company",
    "classify_direct_intercompany",
    "classify_invoice_type",
    "convert_header_fields",
    "determine_document_type",
    "extract_detailed_fields",
    "extract_value",
    "normalize_payload",
    "review_node",
    "validate_extraction",
    # Runner
    "run_triage",
    "run_triage_from_payload",
    # SAP Document AI (requires requests + credentials)
    "DocAIClient",
    "run_triage_with_doc_ai",
]

__version__ = "1.0.0"

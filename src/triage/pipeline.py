"""Standalone pipeline step functions for the AP Invoice Triage Agent.

Every function in this module operates purely on the TriageState dict —
no HTTP calls, no database access, no external API dependencies. This makes
the entire classification and validation pipeline testable in isolation and
deployable without the SACC mock API server.

Workflow (when called sequentially via runner.run_triage):
    1.  Document type determination (keyword matching on extraction data)
    2.  Company classification (Saudia Cargo vs Saudia Airline vs no-match)
    3.  Direct vs Intercompany determination
    4.  Invoice type classification (FUEL / CHARTER / SERVICE / OTHER)
    5.  Detailed field extraction (from pre-supplied extraction entities)
    6.  Validation (required fields, confidence thresholds, line items)
    7.  TriageResult construction
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .config import (
    CONFIGURED_COMPANY_CODE,
    CONFIGURED_COMPANY_NAME,
    DOCUMENT_TYPES_FILE,
    INTERCOMPANY_COMPANY_CODES,
    MIN_CONFIDENCE_THRESHOLD,
    MIN_REQUIRED_FIELD_CONFIDENCE,
)
from .models import (
    CompanyClassification,
    DirectIntercompany,
    DocumentType,
    InvoiceType,
    TriageResult,
)
from .schemas import DETAILED_SCHEMAS
from .state import TriageState

logger = logging.getLogger("triage_agent")

# ── Document type categories (loaded once at import time) ──────────────────

_DOCUMENT_TYPES_FILE = DOCUMENT_TYPES_FILE
with open(_DOCUMENT_TYPES_FILE, "r", encoding="utf-8") as _f:
    _DOCUMENT_TYPES_DATA = json.load(_f)

_DOCUMENT_TYPE_CATEGORIES = [
    (item["category"], item["document_type"])
    for item in _DOCUMENT_TYPES_DATA["categories"]
]

_CATEGORY_TO_DOC_TYPE = {
    "Invoice": DocumentType.INVOICE,
    "Credit Note": DocumentType.CREDIT_NOTE,
    "Statement of Account": DocumentType.STATEMENT,
    "Supporting Document": DocumentType.SUPPORTING_DOCUMENT,
}

_VALID_AP_TYPES = {
    DocumentType.INVOICE.value,
    DocumentType.CREDIT_NOTE.value,
    DocumentType.STATEMENT.value,
}


# ── Helper functions ───────────────────────────────────────────────────────


def extract_value(entity: dict[str, Any]) -> str:
    """Extract the string value from a DocumentEntity record."""
    return (
        entity.get("stringValue")
        or str(entity.get("numberValue", "") or "")
        or entity.get("dateValue", "")
        or entity.get("rawValue", "")
        or ""
    )


def build_lookup(extraction: list[dict[str, Any]]) -> dict[str, str]:
    """Build a name -> value lookup from a list of DocumentEntity records."""
    lookup: dict[str, str] = {}
    for entity in extraction:
        name = entity.get("name", "")
        value = extract_value(entity)
        if name and value:
            lookup[name] = str(value)
    return lookup


def convert_header_fields(header_fields: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a headerFields dict (SAP Doc AI format) to DocumentEntity list.

    Only converts the data format — does NOT map or derive any field names.
    """
    entities: list[dict[str, Any]] = []
    for name, field_data in header_fields.items():
        if isinstance(field_data, dict) and ("value" in field_data or "confidence" in field_data):
            value = field_data.get("value", "")
            confidence = float(field_data.get("confidence", 0.0) or 0.0)
        else:
            value = field_data
            confidence = 0.85

        entity: dict[str, Any] = {"name": name, "confidence": confidence}
        if isinstance(value, bool):
            entity["type"] = "string"
            entity["stringValue"] = str(value)
        elif isinstance(value, (int, float)):
            entity["type"] = "number"
            entity["numberValue"] = value
        else:
            entity["type"] = "string"
            entity["stringValue"] = str(value) if value is not None else ""
        entities.append(entity)
    return entities


def normalize_payload(raw_data: dict[str, Any], doc_index: int = 0) -> dict[str, Any]:
    """Convert a JSON payload (SAP Doc AI or generic) to the triage-expected format.

    Returns a dict with:
        classification_extraction: list of DocumentEntity dicts
        detailed_extraction: list of DocumentEntity dicts
        invoice_source: dict of key header fields
        line_items: list of line-item dicts
    """
    # sample_data.json format — already in DocumentEntity format
    if "classification_extraction" in raw_data:
        return {
            "classification_extraction": raw_data.get("classification_extraction", []),
            "detailed_extraction": raw_data.get("detailed_extraction", []),
            "invoice_source": raw_data.get("invoice_source", {}),
            "line_items": raw_data.get("line_items", []),
        }

    # doc_ai_payload.json format — has processed[] array
    if "processed" in raw_data:
        processed = raw_data["processed"]
        if not processed:
            raise ValueError("No 'processed' documents in payload")
        if doc_index >= len(processed):
            raise ValueError(f"doc_index {doc_index} out of range (0-{len(processed) - 1})")
        doc = processed[doc_index]
    elif "headerFields" in raw_data or "header_fields" in raw_data:
        doc = raw_data
    else:
        # Generic — treat entire dict as header fields
        doc = {
            "attachment": raw_data.get("file_name", raw_data.get("attachment", "")),
            "headerFields": {
                k: v for k, v in raw_data.items()
                if k not in ("lineItems", "line_items") and isinstance(v, (str, int, float, dict))
            },
            "lineItems": raw_data.get("lineItems", raw_data.get("line_items", [])),
        }

    header_fields = doc.get("headerFields", doc.get("header_fields", {}))
    line_items = doc.get("lineItems", doc.get("line_items", []))

    entities = convert_header_fields(header_fields)

    # Build invoice_source from what's in the payload (no derivation)
    values: dict[str, str] = {}
    for e in entities:
        val = e.get("stringValue") or str(e.get("numberValue", "") or "") or ""
        if val:
            values[e["name"]] = str(val)

    invoice_source = {
        "file_name": doc.get("attachment", ""),
    }
    for key, alt_keys in {
        "invoice_number": ["invoice_number", "documentNumber"],
        "invoice_date": ["invoice_date", "documentDate"],
        "supplier_name": ["supplier_name", "senderName"],
        "company_name": ["company_name", "receiverName"],
        "company_code": ["company_code"],
        "total_amount": ["total_amount", "grossAmount"],
        "currency": ["currency", "currencyCode"],
        "net_amount": ["net_amount", "netAmount"],
        "tax_amount": ["tax_amount"],
        "po_number": ["po_number", "purchaseOrderNumber"],
        "payment_terms": ["payment_terms", "paymentTerms"],
        "keywords": ["keywords"],
    }.items():
        for alt in alt_keys:
            if alt in values:
                invoice_source[key] = values[alt]
                break
        else:
            invoice_source[key] = ""

    return {
        "classification_extraction": entities,
        "detailed_extraction": entities,
        "invoice_source": invoice_source,
        "line_items": line_items,
    }


# ── Step 1: Determine Document Type ────────────────────────────────────────


def determine_document_type(state: TriageState) -> TriageState:
    """Determine the document type from classification extraction results.

    Uses the shared document type categories from document_types.json to
    classify the document as INVOICE, CREDIT_NOTE, STATEMENT,
    SUPPORTING_DOCUMENT, or OTHER.
    """
    logger.info("Step 1: Determining document type...")
    evidence = state.get("evidence", [])
    extraction = state.get("classification_extraction", [])
    extracted_values = build_lookup(extraction)

    all_text = " ".join(v.lower() for v in extracted_values.values())
    doc_type_field = extracted_values.get("document_type", "").lower()

    doc_type = DocumentType.OTHER

    search_text = doc_type_field if doc_type_field else all_text
    for category, doc_types in _DOCUMENT_TYPE_CATEGORIES:
        mapped_type = _CATEGORY_TO_DOC_TYPE.get(category)
        if mapped_type and any(kw in search_text for kw in doc_types):
            doc_type = mapped_type
            break

    evidence.append(
        f"Document type determined: {doc_type.value} "
        f"(based on shared document types from document_types.json)"
    )
    logger.info("Document type: %s", doc_type.value)
    return {**state, "document_type": doc_type.value, "evidence": evidence}


# ── Step 2: Classify Company ───────────────────────────────────────────────


def classify_company(state: TriageState) -> TriageState:
    """Classify whether the document belongs to the configured fixed company."""
    logger.info("Step 2: Classifying company...")
    evidence = state.get("evidence", [])
    extraction = state.get("classification_extraction", [])
    extracted_values = build_lookup(extraction)

    extracted_company_code = extracted_values.get("company_code", "").strip()
    extracted_company_name = extracted_values.get("company_name", "").strip().lower()

    company_classification = CompanyClassification.UNCERTAIN
    match_reasons: list[str] = []

    # Check if this is a Saudia Airline invoice (not Saudia Cargo)
    po_number = (
        extracted_values.get("purchase_order_number", "")
        or extracted_values.get("po_number", "")
    ).strip()
    vendor_number = (
        extracted_values.get("vendor_number", "")
        or extracted_values.get("supplier_number", "")
    ).strip()

    if po_number.startswith("62") and vendor_number.startswith("15"):
        company_classification = CompanyClassification.MATCH
        match_reasons.append(
            f"Saudia Airline invoice (PO '{po_number}' starts with 62, "
            f"vendor '{vendor_number}' starts with 15) — not Saudia Cargo"
        )
        evidence.append("Company identified as Saudia Airline (not Saudia Cargo)")
        logger.info("Company classification: %s (Saudia Airline)", company_classification.value)
        return {**state, "company_classification": company_classification.value,
                "company_name": "Saudia Airline", "evidence": evidence}

    if extracted_company_code:
        if extracted_company_code == CONFIGURED_COMPANY_CODE:
            company_classification = CompanyClassification.MATCH
            match_reasons.append(
                f"Company code '{extracted_company_code}' matches configured '{CONFIGURED_COMPANY_CODE}'"
            )
        elif extracted_company_code in INTERCOMPANY_COMPANY_CODES:
            company_classification = CompanyClassification.MATCH
            match_reasons.append(f"Company code '{extracted_company_code}' is an intercompany code")
        else:
            company_classification = CompanyClassification.NO_MATCH
            match_reasons.append(
                f"Company code '{extracted_company_code}' does not match configured '{CONFIGURED_COMPANY_CODE}'"
            )

    if extracted_company_name and CONFIGURED_COMPANY_NAME:
        configured_name = CONFIGURED_COMPANY_NAME.lower()
        if configured_name in extracted_company_name or extracted_company_name in configured_name:
            if company_classification != CompanyClassification.NO_MATCH:
                company_classification = CompanyClassification.MATCH
            match_reasons.append(f"Company name matches configured '{CONFIGURED_COMPANY_NAME}'")
        else:
            if not extracted_company_code:
                company_classification = CompanyClassification.NO_MATCH
                match_reasons.append(f"Company name does not match configured '{CONFIGURED_COMPANY_NAME}'")

    if not extracted_company_code and not extracted_company_name:
        company_classification = CompanyClassification.UNCERTAIN
        match_reasons.append("No company code or name extracted -- classification uncertain")

    evidence.append(
        f"Company classification: {company_classification.value}. " + "; ".join(match_reasons)
    )
    logger.info("Company classification: %s", company_classification.value)
    return {**state, "company_classification": company_classification.value, "evidence": evidence}


# ── Step 3: Determine DIRECT vs INTERCOMPANY ────────────────────────────────


def classify_direct_intercompany(state: TriageState) -> TriageState:
    """Determine whether the invoice is DIRECT or INTERCOMPANY.

    Business rules:
    - If the extracted company code matches the configured company code -> DIRECT
    - If the extracted company code is in the intercompany list -> INTERCOMPANY
    - Otherwise -> UNKNOWN (requires review)
    """
    logger.info("Step 3: Determining DIRECT vs INTERCOMPANY...")
    evidence = state.get("evidence", [])
    extraction = state.get("classification_extraction", [])
    extracted_values = build_lookup(extraction)

    extracted_company_code = extracted_values.get("company_code", "")
    extracted_supplier_name = extracted_values.get("supplier_name", "").lower()

    classification = DirectIntercompany.UNKNOWN
    reasons: list[str] = []

    if extracted_company_code == CONFIGURED_COMPANY_CODE:
        classification = DirectIntercompany.DIRECT
        reasons.append("Company code matches configured company -- DIRECT")
    elif extracted_company_code in INTERCOMPANY_COMPANY_CODES:
        classification = DirectIntercompany.INTERCOMPANY
        reasons.append("Company code is in intercompany list -- INTERCOMPANY")
    else:
        configured_name = CONFIGURED_COMPANY_NAME.lower()
        if extracted_supplier_name and configured_name:
            if configured_name in extracted_supplier_name or extracted_supplier_name in configured_name:
                classification = DirectIntercompany.INTERCOMPANY
                reasons.append("Supplier name matches intercompany pattern -- INTERCOMPANY")
            else:
                reasons.append("Cannot determine DIRECT vs INTERCOMPANY from available evidence")
        else:
            reasons.append("Insufficient evidence to determine DIRECT vs INTERCOMPANY")

    evidence.append(f"Direct/Intercompany: {classification.value}. " + "; ".join(reasons))
    logger.info("Direct/Intercompany: %s", classification.value)
    return {**state, "direct_intercompany": classification.value, "evidence": evidence}


# ── Step 4: Determine Invoice Type ─────────────────────────────────────────


def classify_invoice_type(state: TriageState) -> TriageState:
    """Determine the invoice type: FUEL, CHARTER, SERVICE, or OTHER.

    Uses keyword matching on extracted text, document title, and keywords.
    """
    logger.info("Step 4: Determining invoice type...")
    evidence = state.get("evidence", [])
    extraction = state.get("classification_extraction", [])
    extracted_values = build_lookup(extraction)

    all_text = " ".join(v.lower() for v in extracted_values.values())
    keywords = extracted_values.get("keywords", "").lower()
    combined = f"{all_text} {keywords}"

    invoice_type = InvoiceType.OTHER
    reasons: list[str] = []

    fuel_keywords = ["fuel", "jet fuel", "avgas", "jet-a", "jet a1", "aviation fuel",
                     "fuel surcharge", "gallons", "litres", "uplift", "into-plane", "fueling", "refuel"]
    if any(kw in combined for kw in fuel_keywords):
        invoice_type = InvoiceType.FUEL
        reasons.append("Fuel keywords detected in extracted text")

    charter_keywords = ["charter", "acmi", "wet lease", "dry lease", "aircraft charter",
                        "charter flight", "private flight", "air taxi"]
    if invoice_type == InvoiceType.OTHER and any(kw in combined for kw in charter_keywords):
        invoice_type = InvoiceType.CHARTER
        reasons.append("Charter keywords detected in extracted text")

    service_keywords = ["service", "maintenance", "repair", "overhaul", "mro",
                        "ground handling", "catering", "cleaning", "consulting",
                        "professional services", "facility"]
    if invoice_type == InvoiceType.OTHER and any(kw in combined for kw in service_keywords):
        invoice_type = InvoiceType.SERVICE
        reasons.append("Service keywords detected in extracted text")

    if invoice_type == InvoiceType.OTHER:
        reasons.append("No specific invoice type keywords detected -- classified as OTHER")

    evidence.append(f"Invoice type: {invoice_type.value}. " + "; ".join(reasons))
    logger.info("Invoice type: %s", invoice_type.value)
    return {**state, "invoice_type": invoice_type.value, "evidence": evidence}


# ── Step 5: Extract Detailed Fields ────────────────────────────────────────


def extract_detailed_fields(state: TriageState) -> TriageState:
    """Parse pre-supplied extraction entities into header fields and line items.

    This is a pure-data function — it reads the ``detailed_extraction`` list
    from state (populated by the caller, not by an API call) and splits it
    into header fields (name -> value with confidence) and line items.
    """
    logger.info("Step 5: Extracting detailed fields...")
    evidence = state.get("evidence", [])
    detailed_extraction = state.get("detailed_extraction", [])

    header_fields: dict[str, Any] = {}
    field_confidences: dict[str, float] = {}
    line_items: list[dict[str, Any]] = []

    for entity in detailed_extraction:
        name = entity.get("name", "")
        confidence = float(entity.get("confidence", 0.0) or 0.0)
        value = extract_value(entity)
        if not name:
            continue
        if name == "line_items" or entity.get("type") == "collection":
            continue
        header_fields[name] = value
        field_confidences[name] = confidence

    # Simulate a single line item from header data if no line items were
    # explicitly provided and we have a total_amount.
    if not line_items and not state.get("line_items") and header_fields.get("total_amount"):
        line_items.append({
            "item_number": "00001",
            "description": header_fields.get("invoice_number", "Line item 1"),
            "quantity": 1,
            "unit_price": float(header_fields.get("total_amount", 0) or 0),
            "total_amount": float(header_fields.get("total_amount", 0) or 0),
            "gl_account": "", "cost_center": "", "tax_code": "",
            "confidence": 0.0,
        })

    # If line items were supplied in state (e.g. from source JSON), keep them
    if state.get("line_items"):
        line_items = state["line_items"]

    evidence.append(
        f"Detailed extraction complete: {len(header_fields)} header fields, "
        f"{len(line_items)} line items"
    )
    logger.info("Detailed extraction: %d header fields, %d line items",
                len(header_fields), len(line_items))
    return {**state, "detailed_extraction": detailed_extraction,
            "header_fields": header_fields, "field_confidences": field_confidences,
            "line_items": line_items, "evidence": evidence}


# ── Step 6: Validate Extraction ─────────────────────────────────────────────


def validate_extraction(state: TriageState) -> TriageState:
    """Validate extraction confidence and required fields."""
    logger.info("Step 6: Validating extraction...")
    evidence = state.get("evidence", [])
    invoice_type = state.get("invoice_type", InvoiceType.OTHER.value)
    header_fields = state.get("header_fields", {})
    field_confidences = state.get("field_confidences", {})
    line_items = state.get("line_items", [])

    schema_entities = DETAILED_SCHEMAS.get(
        invoice_type, DETAILED_SCHEMAS[InvoiceType.OTHER.value]
    )
    required_fields = [
        e["name"] for e in schema_entities
        if e.get("mandatory") and e.get("type") != "collection"
    ]

    missing_required = [
        f for f in required_fields if f not in header_fields or not header_fields[f]
    ]
    low_confidence = [
        f for f, conf in field_confidences.items()
        if conf < MIN_REQUIRED_FIELD_CONFIDENCE and f in required_fields
    ]

    if field_confidences:
        overall_confidence = sum(field_confidences.values()) / len(field_confidences)
    else:
        overall_confidence = 0.0

    review_reasons: list[str] = []
    if missing_required:
        review_reasons.append(f"Missing required fields: {', '.join(missing_required)}")
    if low_confidence:
        review_reasons.append(f"Low confidence on required fields: {', '.join(low_confidence)}")
    if overall_confidence < MIN_CONFIDENCE_THRESHOLD:
        review_reasons.append(
            f"Overall confidence {overall_confidence:.2f} below threshold {MIN_CONFIDENCE_THRESHOLD}"
        )
    if not line_items:
        review_reasons.append("No line items extracted")
    if state.get("direct_intercompany") == DirectIntercompany.UNKNOWN.value:
        review_reasons.append("DIRECT vs INTERCOMPANY could not be determined")

    review_required = len(review_reasons) > 0
    review_reason = "; ".join(review_reasons) if review_reasons else None

    evidence.append(
        f"Validation: confidence={overall_confidence:.2f}, "
        f"missing={len(missing_required)}, low_conf={len(low_confidence)}, "
        f"review_required={review_required}"
    )
    if review_required:
        evidence.append(f"Review reason: {review_reason}")
    logger.info("Validation: confidence=%.2f, review_required=%s", overall_confidence, review_required)
    return {**state, "confidence": overall_confidence,
            "missing_required_fields": missing_required,
            "low_confidence_fields": low_confidence,
            "review_required": review_required,
            "review_reason": review_reason, "evidence": evidence}


# ── Step 7: Build Triage Result ─────────────────────────────────────────────


def build_triage_result(state: TriageState) -> TriageState:
    """Build and return the strict JSON TriageResult."""
    logger.info("Step 7: Building TriageResult...")
    result = TriageResult(
        document_type=state.get("document_type", DocumentType.OTHER.value),
        company_classification=state.get("company_classification", CompanyClassification.UNCERTAIN.value),
        direct_intercompany=state.get("direct_intercompany", DirectIntercompany.UNKNOWN.value),
        invoice_type=state.get("invoice_type", InvoiceType.OTHER.value),
        extracted_header_fields=state.get("header_fields", {}),
        line_items=state.get("line_items", []),
        confidence=state.get("confidence", 0.0),
        evidence=state.get("evidence", []),
        review_required=state.get("review_required", True),
        review_reason=state.get("review_reason"),
    )
    result_dict = result.to_dict()
    logger.info("TriageResult:\n%s", result.to_json())
    return {**state, "triage_result": result_dict}


# ── Review Node ─────────────────────────────────────────────────────────────


def review_node(state: TriageState) -> TriageState:
    """Handle documents that require manual review."""
    logger.info("Routing to REVIEW...")
    evidence = state.get("evidence", [])
    doc_type = state.get("document_type", DocumentType.OTHER.value)

    reason = f"Document type '{doc_type}' is not a valid AP document (INVOICE/CREDIT_NOTE/STATEMENT)"
    if state.get("company_classification") == CompanyClassification.NO_MATCH.value:
        reason = f"Company does not match configured company '{CONFIGURED_COMPANY_NAME}' ({CONFIGURED_COMPANY_CODE})"

    evidence.append(f"Review required: {reason}")
    result = TriageResult(
        document_type=doc_type,
        company_classification=state.get("company_classification", CompanyClassification.UNCERTAIN.value),
        direct_intercompany=state.get("direct_intercompany", DirectIntercompany.UNKNOWN.value),
        invoice_type=state.get("invoice_type", InvoiceType.OTHER.value),
        extracted_header_fields=state.get("header_fields", {}),
        line_items=state.get("line_items", []),
        confidence=state.get("confidence", 0.0),
        evidence=evidence, review_required=True, review_reason=reason,
    )
    return {**state, "review_required": True, "review_reason": reason,
            "triage_result": result.to_dict(), "evidence": evidence}

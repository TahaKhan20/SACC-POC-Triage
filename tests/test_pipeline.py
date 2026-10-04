"""Unit tests for the standalone triage pipeline.

These tests verify the core classification and validation logic without
any external API dependency. Run with:

    pytest tests/
"""

from __future__ import annotations
import sys
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from triage import (
    CompanyClassification,
    DirectIntercompany,
    DocumentType,
    InvoiceType,
    TriageState,
    build_lookup,
    classify_company,
    classify_direct_intercompany,
    classify_invoice_type,
    determine_document_type,
    extract_value,
    run_triage,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def invoice_entities():
    """DocumentEntity list for a typical invoice."""
    return [
        {"name": "document_type", "stringValue": "invoice", "confidence": 0.95},
        {"name": "invoice_number", "stringValue": "INV-001", "confidence": 0.95},
        {"name": "company_code", "stringValue": "1000", "confidence": 0.90},
        {"name": "supplier_name", "stringValue": "Test Supplier", "confidence": 0.85},
        {"name": "total_amount", "numberValue": 18900.0, "confidence": 0.85},
        {"name": "currency", "stringValue": "USD", "confidence": 0.90},
        {"name": "invoice_date", "dateValue": "2024-01-15", "confidence": 0.88},
    ]


@pytest.fixture
def fuel_invoice_entities(invoice_entities):
    """Entities for a fuel invoice."""
    return invoice_entities + [
        {"name": "fuel_type", "stringValue": "Jet A1", "confidence": 0.92},
        {"name": "fuel_quantity", "numberValue": 5000.0, "confidence": 0.88},
        {"name": "unit_price", "numberValue": 3.78, "confidence": 0.87},
    ]


@pytest.fixture
def charter_invoice_entities(invoice_entities):
    """Entities for a charter invoice."""
    return invoice_entities + [
        {"name": "charter_type", "stringValue": "wet lease", "confidence": 0.90},
        {"name": "aircraft_registration", "stringValue": "HZ-ABC", "confidence": 0.85},
    ]


@pytest.fixture
def saudia_airline_entities():
    """Entities for a Saudia Airline invoice (PO starts with 62, vendor with 15)."""
    return [
        {"name": "document_type", "stringValue": "invoice", "confidence": 0.95},
        {"name": "purchase_order_number", "stringValue": "62000123", "confidence": 0.90},
        {"name": "vendor_number", "stringValue": "1500456", "confidence": 0.90},
        {"name": "invoice_number", "stringValue": "INV-AIR-001", "confidence": 0.92},
        {"name": "total_amount", "numberValue": 50000.0, "confidence": 0.85},
        {"name": "currency", "stringValue": "SAR", "confidence": 0.90},
    ]


# ── Helper Tests ─────────────────────────────────────────────────────────────


class TestExtractValue:
    def test_string_value(self):
        entity = {"stringValue": "hello", "confidence": 0.9}
        assert extract_value(entity) == "hello"

    def test_number_value(self):
        entity = {"numberValue": 42.5, "confidence": 0.9}
        assert extract_value(entity) == "42.5"

    def test_date_value(self):
        entity = {"dateValue": "2024-01-15", "confidence": 0.9}
        assert extract_value(entity) == "2024-01-15"

    def test_empty_entity(self):
        assert extract_value({}) == ""


class TestBuildLookup:
    def test_basic_lookup(self, invoice_entities):
        lookup = build_lookup(invoice_entities)
        assert lookup["invoice_number"] == "INV-001"
        assert lookup["company_code"] == "1000"

    def test_empty_list(self):
        assert build_lookup([]) == {}

    def test_skips_empty_values(self):
        entities = [{"name": "empty", "stringValue": "", "confidence": 0.9}]
        assert build_lookup(entities) == {}


# ── Document Type Tests ────────────────────────────────────────────────────


class TestDetermineDocumentType:
    def test_invoice(self, invoice_entities):
        state = TriageState(classification_extraction=invoice_entities, evidence=[])
        result = determine_document_type(state)
        assert result["document_type"] == DocumentType.INVOICE.value

    def test_credit_note(self):
        entities = [{"name": "document_type", "stringValue": "credit note", "confidence": 0.9}]
        state = TriageState(classification_extraction=entities, evidence=[])
        result = determine_document_type(state)
        assert result["document_type"] == DocumentType.CREDIT_NOTE.value

    def test_statement(self):
        entities = [{"name": "document_type", "stringValue": "statement of account", "confidence": 0.9}]
        state = TriageState(classification_extraction=entities, evidence=[])
        result = determine_document_type(state)
        assert result["document_type"] == DocumentType.STATEMENT.value

    def test_other_unrecognized(self):
        entities = [{"name": "document_type", "stringValue": "random doc", "confidence": 0.9}]
        state = TriageState(classification_extraction=entities, evidence=[])
        result = determine_document_type(state)
        assert result["document_type"] == DocumentType.OTHER.value


# ── Company Classification Tests ─────────────────────────────────────────────


class TestClassifyCompany:
    def test_match_by_company_code(self, invoice_entities):
        state = TriageState(classification_extraction=invoice_entities, evidence=[])
        result = classify_company(state)
        assert result["company_classification"] == CompanyClassification.MATCH.value

    def test_no_match_company_code(self):
        entities = [
            {"name": "company_code", "stringValue": "9999", "confidence": 0.9},
            {"name": "company_name", "stringValue": "Unknown Co", "confidence": 0.9},
        ]
        state = TriageState(classification_extraction=entities, evidence=[])
        result = classify_company(state)
        assert result["company_classification"] == CompanyClassification.NO_MATCH.value

    def test_saudia_airline(self, saudia_airline_entities):
        state = TriageState(classification_extraction=saudia_airline_entities, evidence=[])
        result = classify_company(state)
        assert result["company_classification"] == CompanyClassification.MATCH.value
        assert result["company_name"] == "Saudia Airline"

    def test_uncertain_no_company_data(self):
        entities = [{"name": "invoice_number", "stringValue": "INV-001", "confidence": 0.9}]
        state = TriageState(classification_extraction=entities, evidence=[])
        result = classify_company(state)
        assert result["company_classification"] == CompanyClassification.UNCERTAIN.value


# ── Direct vs Intercompany Tests ─────────────────────────────────────────────


class TestClassifyDirectIntercompany:
    def test_direct(self, invoice_entities):
        state = TriageState(classification_extraction=invoice_entities, evidence=[])
        result = classify_direct_intercompany(state)
        assert result["direct_intercompany"] == DirectIntercompany.DIRECT.value

    def test_intercompany(self):
        entities = [
            {"name": "company_code", "stringValue": "2000", "confidence": 0.9},
        ]
        state = TriageState(classification_extraction=entities, evidence=[])
        result = classify_direct_intercompany(state)
        assert result["direct_intercompany"] == DirectIntercompany.INTERCOMPANY.value

    def test_unknown(self):
        entities = [
            {"name": "company_code", "stringValue": "9999", "confidence": 0.9},
            {"name": "supplier_name", "stringValue": "Unknown Supplier", "confidence": 0.9},
        ]
        state = TriageState(classification_extraction=entities, evidence=[])
        result = classify_direct_intercompany(state)
        assert result["direct_intercompany"] == DirectIntercompany.UNKNOWN.value


# ── Invoice Type Tests ──────────────────────────────────────────────────────


class TestClassifyInvoiceType:
    def test_fuel(self, fuel_invoice_entities):
        state = TriageState(classification_extraction=fuel_invoice_entities, evidence=[])
        result = classify_invoice_type(state)
        assert result["invoice_type"] == InvoiceType.FUEL.value

    def test_charter(self, charter_invoice_entities):
        state = TriageState(classification_extraction=charter_invoice_entities, evidence=[])
        result = classify_invoice_type(state)
        assert result["invoice_type"] == InvoiceType.CHARTER.value

    def test_service(self):
        entities = [
            {"name": "description", "stringValue": "ground handling service", "confidence": 0.9},
        ]
        state = TriageState(classification_extraction=entities, evidence=[])
        result = classify_invoice_type(state)
        assert result["invoice_type"] == InvoiceType.SERVICE.value

    def test_other(self, invoice_entities):
        state = TriageState(classification_extraction=invoice_entities, evidence=[])
        result = classify_invoice_type(state)
        assert result["invoice_type"] == InvoiceType.OTHER.value


# ── Integration: Full Pipeline ──────────────────────────────────────────────


class TestRunTriage:
    def test_full_invoice_pipeline(self, invoice_entities):
        result = run_triage(
            classification_extraction=invoice_entities,
            detailed_extraction=invoice_entities,
        )
        assert result["document_type"] == DocumentType.INVOICE.value
        assert result["company_classification"] == CompanyClassification.MATCH.value
        assert result["direct_intercompany"] == DirectIntercompany.DIRECT.value
        assert "evidence" in result
        assert len(result["evidence"]) > 0

    def test_non_ap_document_routes_to_review(self):
        entities = [
            {"name": "document_type", "stringValue": "correspondence", "confidence": 0.9},
        ]
        result = run_triage(classification_extraction=entities)
        assert result["document_type"] == DocumentType.OTHER.value
        assert result["review_required"] is True

    def test_no_match_company_routes_to_review(self):
        entities = [
            {"name": "document_type", "stringValue": "invoice", "confidence": 0.9},
            {"name": "company_code", "stringValue": "9999", "confidence": 0.9},
            {"name": "company_name", "stringValue": "Unknown Co", "confidence": 0.9},
        ]
        result = run_triage(classification_extraction=entities)
        assert result["review_required"] is True
        assert result["company_classification"] == CompanyClassification.NO_MATCH.value

    def test_saudia_airline_invoice(self, saudia_airline_entities):
        result = run_triage(classification_extraction=saudia_airline_entities)
        assert result["company_classification"] == CompanyClassification.MATCH.value

    def test_fuel_invoice(self, fuel_invoice_entities):
        result = run_triage(
            classification_extraction=fuel_invoice_entities,
            detailed_extraction=fuel_invoice_entities,
        )
        assert result["invoice_type"] == InvoiceType.FUEL.value

    def test_result_has_all_fields(self, invoice_entities):
        result = run_triage(classification_extraction=invoice_entities)
        required_keys = {
            "document_type", "company_classification", "direct_intercompany",
            "invoice_type", "extracted_header_fields", "line_items",
            "confidence", "evidence", "review_required", "review_reason",
        }
        assert required_keys.issubset(result.keys())

"""Unit tests for SAP Document AI result conversion.

These tests verify the conversion from SAP Doc AI response format to
the pipeline's DocumentEntity format, without making any HTTP calls.
Run with:

    pytest tests/
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from triage.doc_ai import DocAIClient, _convert_sap_header_fields, _convert_sap_line_item


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def sap_job_response():
    """Simulated SAP Doc AI job response with extraction results."""
    return {
        "id": "job-abc-123",
        "status": "DONE",
        "fileName": "invoice_001.pdf",
        "extraction": {
            "headerFields": [
                {"name": "documentNumber", "value": "INV-001", "confidence": 0.95},
                {"name": "documentDate", "value": "2024-01-15", "confidence": 0.88},
                {"name": "senderName", "value": "Test Supplier Ltd", "confidence": 0.90},
                {"name": "receiverName", "value": "SACC Airlines", "confidence": 0.85},
                {"name": "grossAmount", "value": 18900.0, "confidence": 0.85},
                {"name": "currencyCode", "value": "USD", "confidence": 0.92},
                {"name": "netAmount", "value": 16500.0, "confidence": 0.83},
                {"name": "purchaseOrderNumber", "value": "PO-62000123", "confidence": 0.80},
            ],
            "lineItems": [
                {"description": "Air Freight Services", "quantity": 1, "unitPrice": 18900.0,
                 "totalAmount": 18900.0, "confidence": 0.87},
                {"description": "Fuel Surcharge", "quantity": 500, "unitPrice": 3.78,
                 "totalAmount": 1890.0, "confidence": 0.82},
            ],
        },
    }


# ── Header field conversion tests ──────────────────────────────────────────


class TestConvertSapHeaderFields:
    def test_basic_conversion(self):
        fields = [{"name": "documentNumber", "value": "INV-001", "confidence": 0.95}]
        entities = _convert_sap_header_fields(fields)
        assert len(entities) == 1
        assert entities[0]["name"] == "documentNumber"
        assert entities[0]["stringValue"] == "INV-001"
        assert entities[0]["confidence"] == 0.95
        assert entities[0]["type"] == "string"

    def test_number_value(self):
        fields = [{"name": "grossAmount", "value": 18900.0, "confidence": 0.85}]
        entities = _convert_sap_header_fields(fields)
        assert entities[0]["type"] == "number"
        assert entities[0]["numberValue"] == 18900.0

    def test_empty_value(self):
        fields = [{"name": "empty", "value": "", "confidence": 0.0}]
        entities = _convert_sap_header_fields(fields)
        assert entities[0]["stringValue"] == ""

    def test_missing_name_skipped(self):
        fields = [{"value": "test", "confidence": 0.9}]
        entities = _convert_sap_header_fields(fields)
        assert len(entities) == 0

    def test_null_value(self):
        fields = [{"name": "field", "value": None, "confidence": 0.9}]
        entities = _convert_sap_header_fields(fields)
        assert entities[0]["stringValue"] == ""

    def test_multiple_fields(self):
        fields = [
            {"name": "a", "value": "hello", "confidence": 0.9},
            {"name": "b", "value": 42, "confidence": 0.8},
            {"name": "c", "value": True, "confidence": 0.7},
        ]
        entities = _convert_sap_header_fields(fields)
        assert len(entities) == 3
        assert entities[0]["type"] == "string"
        assert entities[1]["type"] == "number"
        assert entities[2]["type"] == "string"
        assert entities[2]["stringValue"] == "True"

    def test_confidence_defaults_to_zero(self):
        fields = [{"name": "field", "value": "test"}]
        entities = _convert_sap_header_fields(fields)
        assert entities[0]["confidence"] == 0.0


# ── Line item conversion tests ──────────────────────────────────────────────


class TestConvertSapLineItem:
    def test_basic_line_item(self):
        item = {
            "description": "Air Freight",
            "quantity": 5.0,
            "unitPrice": 100.0,
            "totalAmount": 500.0,
            "confidence": 0.9,
        }
        result = _convert_sap_line_item(item)
        assert result["description"] == "Air Freight"
        assert result["quantity"] == 5.0
        assert result["unit_price"] == 100.0
        assert result["total_amount"] == 500.0
        assert result["confidence"] == 0.9
        assert result["raw_data"] == item

    def test_empty_line_item(self):
        result = _convert_sap_line_item({})
        assert result["description"] == ""
        assert result["quantity"] is None
        assert result["unit_price"] is None
        assert result["total_amount"] is None

    def test_alternative_keys(self):
        item = {
            "itemDescription": "Fuel",
            "amount": 5000.0,
            "itemNumber": "001",
        }
        result = _convert_sap_line_item(item)
        assert result["description"] == "Fuel"
        assert result["total_amount"] == 5000.0
        assert result["item_number"] == "001"


# ── Full response conversion tests ──────────────────────────────────────────


class TestConvertResults:
    def test_full_conversion(self, sap_job_response):
        result = DocAIClient.convert_results(sap_job_response)
        assert "classification_extraction" in result
        assert "detailed_extraction" in result
        assert "line_items" in result
        assert "invoice_source" in result

    def test_header_fields_converted(self, sap_job_response):
        result = DocAIClient.convert_results(sap_job_response)
        entities = result["classification_extraction"]
        assert len(entities) == 8
        names = [e["name"] for e in entities]
        assert "documentNumber" in names
        assert "grossAmount" in names

    def test_line_items_converted(self, sap_job_response):
        result = DocAIClient.convert_results(sap_job_response)
        assert len(result["line_items"]) == 2
        assert result["line_items"][0]["description"] == "Air Freight Services"
        assert result["line_items"][1]["description"] == "Fuel Surcharge"

    def test_invoice_source_built(self, sap_job_response):
        result = DocAIClient.convert_results(sap_job_response)
        source = result["invoice_source"]
        assert source["invoice_number"] == "INV-001"
        assert source["supplier_name"] == "Test Supplier Ltd"
        assert source["company_name"] == "SACC Airlines"
        assert source["total_amount"] == "18900.0"
        assert source["currency"] == "USD"
        assert source["po_number"] == "PO-62000123"

    def test_empty_extraction(self):
        response = {"id": "job-1", "status": "DONE", "extraction": {}}
        result = DocAIClient.convert_results(response)
        assert result["classification_extraction"] == []
        assert result["line_items"] == []
        assert result["invoice_source"]["invoice_number"] == ""

    def test_missing_extraction_key(self):
        response = {"id": "job-1", "status": "DONE"}
        result = DocAIClient.convert_results(response)
        assert result["classification_extraction"] == []

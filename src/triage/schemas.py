"""Field schema definitions for invoice validation.

Defines the header fields and line-item sub-fields expected for each invoice
type (FUEL, CHARTER, SERVICE, OTHER). Used by the validation step to check
required fields and confidence thresholds.
"""

from __future__ import annotations

from typing import Any

from .types import InvoiceType


# ── Line item sub-field definitions ────────────────────────────────────────

LINE_ITEM_CHILDREN = [
    {"name": "item_description", "label": "Description", "type": "string", "dataType": "string"},
    {"name": "quantity", "label": "Quantity", "type": "number", "dataType": "number"},
    {"name": "unit_price", "label": "Unit Price", "type": "number", "dataType": "number"},
    {"name": "total_amount", "label": "Total", "type": "number", "dataType": "number"},
    {"name": "gl_account", "label": "GL Account", "type": "string", "dataType": "string"},
    {"name": "cost_center", "label": "Cost Center", "type": "string", "dataType": "string"},
    {"name": "tax_code", "label": "Tax Code", "type": "string", "dataType": "string"},
]

# ── Common header fields (shared across all invoice types) ────────────────

COMMON_HEADER_FIELDS = [
    {"name": "invoice_number", "label": "Invoice Number", "type": "string", "dataType": "string", "mandatory": True, "minimumOccurrence": 1, "maximumOccurrence": 1},
    {"name": "invoice_date", "label": "Invoice Date", "type": "date", "dataType": "date", "mandatory": True, "minimumOccurrence": 1, "maximumOccurrence": 1},
    {"name": "supplier_name", "label": "Supplier Name", "type": "string", "dataType": "string", "mandatory": True, "minimumOccurrence": 1, "maximumOccurrence": 1},
    {"name": "company_code", "label": "Company Code", "type": "string", "dataType": "string", "mandatory": True, "minimumOccurrence": 1, "maximumOccurrence": 1},
    {"name": "total_amount", "label": "Total Amount", "type": "number", "dataType": "number", "mandatory": True, "minimumOccurrence": 1, "maximumOccurrence": 1},
    {"name": "currency", "label": "Currency", "type": "string", "dataType": "string", "mandatory": True, "minimumOccurrence": 1, "maximumOccurrence": 1},
    {"name": "tax_amount", "label": "Tax Amount", "type": "number", "dataType": "number", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
    {"name": "net_amount", "label": "Net Amount", "type": "number", "dataType": "number", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
    {"name": "vendor_number", "label": "Vendor Number", "type": "string", "dataType": "string", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
    {"name": "po_number", "label": "PO Number", "type": "string", "dataType": "string", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
]


def _line_items_entity() -> dict[str, Any]:
    """Return the line-items collection field definition."""
    return {
        "name": "line_items", "label": "Line Items",
        "type": "collection", "dataType": "collection",
        "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 100,
        "children": LINE_ITEM_CHILDREN,
    }


# ── Detailed schemas per invoice type ──────────────────────────────────────

DETAILED_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    InvoiceType.FUEL.value: COMMON_HEADER_FIELDS + [
        {"name": "fuel_type", "label": "Fuel Type", "type": "string", "dataType": "string", "mandatory": True, "minimumOccurrence": 1, "maximumOccurrence": 1},
        {"name": "fuel_quantity", "label": "Fuel Quantity", "type": "number", "dataType": "number", "mandatory": True, "minimumOccurrence": 1, "maximumOccurrence": 1},
        {"name": "fuel_unit", "label": "Fuel Unit", "type": "string", "dataType": "string", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        {"name": "unit_price", "label": "Unit Price", "type": "number", "dataType": "number", "mandatory": True, "minimumOccurrence": 1, "maximumOccurrence": 1},
        {"name": "airport_code", "label": "Airport Code", "type": "string", "dataType": "string", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        {"name": "flight_number", "label": "Flight Number", "type": "string", "dataType": "string", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        _line_items_entity(),
    ],
    InvoiceType.CARGO.value: COMMON_HEADER_FIELDS + [
        {"name": "cargo_type", "label": "Cargo Type", "type": "string", "dataType": "string", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        {"name": "awb_number", "label": "AWB Number", "type": "string", "dataType": "string", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        {"name": "flight_number", "label": "Flight Number", "type": "string", "dataType": "string", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        {"name": "origin", "label": "Origin", "type": "string", "dataType": "string", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        {"name": "destination", "label": "Destination", "type": "string", "dataType": "string", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        {"name": "chargeable_weight", "label": "Chargeable Weight", "type": "number", "dataType": "number", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        _line_items_entity(),
    ],
    InvoiceType.CHARTER.value: COMMON_HEADER_FIELDS + [
        {"name": "charter_type", "label": "Charter Type", "type": "string", "dataType": "string", "mandatory": True, "minimumOccurrence": 1, "maximumOccurrence": 1},
        {"name": "aircraft_registration", "label": "Aircraft Registration", "type": "string", "dataType": "string", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        {"name": "flight_route_from", "label": "Route From", "type": "string", "dataType": "string", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        {"name": "flight_route_to", "label": "Route To", "type": "string", "dataType": "string", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        {"name": "flight_date", "label": "Flight Date", "type": "date", "dataType": "date", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        {"name": "passenger_count", "label": "Passenger Count", "type": "number", "dataType": "number", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        _line_items_entity(),
    ],
    InvoiceType.SERVICE.value: COMMON_HEADER_FIELDS + [
        {"name": "service_type", "label": "Service Type", "type": "string", "dataType": "string", "mandatory": True, "minimumOccurrence": 1, "maximumOccurrence": 1},
        {"name": "service_period_start", "label": "Service Period Start", "type": "date", "dataType": "date", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        {"name": "service_period_end", "label": "Service Period End", "type": "date", "dataType": "date", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        {"name": "purchase_order_number", "label": "PO Number", "type": "string", "dataType": "string", "mandatory": False, "minimumOccurrence": 0, "maximumOccurrence": 1},
        _line_items_entity(),
    ],
    InvoiceType.OTHER.value: COMMON_HEADER_FIELDS + [
        _line_items_entity(),
    ],
}

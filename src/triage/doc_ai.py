"""SAP Document AI (DOX) extraction client for the triage pipeline.

This module handles detailed field extraction from invoices using SAP
Document Information Extraction (DOX). It is called AFTER SAP AI Core
has classified the document as an invoice.

This module handles:
    1.  OAuth2 client-credentials authentication (from config/sap_credentials.json)
    2.  Finding the invoice extraction schema
    3.  Uploading a document and polling for extraction completion
    4.  Converting DOX results (headerFields/lineItems) to DocumentEntity format
    5.  Convenience function ``run_triage_with_doc_ai()`` that processes a file
        and runs the full triage pipeline

Authentication:
    Credentials are loaded from an SAP Document AI service-key JSON file.
    Default location: ``config/sap_credentials.json``
    Override with ``--cred-file`` CLI arg or ``SAP_DOC_AI_CREDENTIALS_FILE`` env var.

Dependencies:
    ``requests`` (pip install requests)

Usage (programmatic)::

    from triage.doc_ai import run_triage_with_doc_ai

    result = run_triage_with_doc_ai("path/to/invoice.pdf")
    print(json.dumps(result, indent=2))

Usage (CLI)::

    python -m triage.doc_ai path/to/invoice.pdf
    python -m triage.doc_ai path/to/invoice.pdf --cred-file /custom/sap_credentials.json
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

from .config import SAP_DOC_AI_CREDENTIALS_FILE
from .runner import run_triage

logger = logging.getLogger("triage_doc_ai")

# ── Constants ───────────────────────────────────────────────────────────────

_POLL_INTERVAL_SEC = 5
_POLL_TIMEOUT_SEC = 300


# ── Credential resolution ──────────────────────────────────────────────────


def _load_cred_file(cred_file: str) -> dict[str, str]:
    """Load an SAP Document AI service-key JSON file and extract credentials.

    Returns a dict with keys: uaa_url, client_id, client_secret, dox_url.
    """
    with open(cred_file, encoding="utf-8") as f:
        cred = json.load(f)
    uaa = cred.get("uaa", cred)
    return {
        "uaa_url": uaa["url"],
        "client_id": uaa["clientid"],
        "client_secret": uaa["clientsecret"],
        "dox_url": cred.get("url", cred.get("dox_url", "")),
    }


def _resolve_credentials(
    cred_file: Optional[str] = None,
) -> dict[str, str]:
    """Resolve SAP Document AI credentials from a JSON file.

    Priority: explicit cred_file arg > SAP_DOC_AI_CREDENTIALS_FILE env var >
              config/sap_credentials.json (default).
    """
    cred_file = cred_file or SAP_DOC_AI_CREDENTIALS_FILE
    if cred_file and os.path.isfile(cred_file):
        logger.info("Loading SAP Document AI credentials from %s", cred_file)
        return _load_cred_file(cred_file)

    raise ValueError(
        f"SAP Document AI credentials not found.\n"
        f"Expected a credentials file at: {cred_file}\n"
        f"Create it from your SAP Document AI service key or pass --cred-file <path>"
    )


# ── Header field / line item conversion helpers ────────────────────────────


def _convert_sap_header_fields(
    fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert SAP DOX headerFields to the pipeline's DocumentEntity format."""
    entities: list[dict[str, Any]] = []
    for f in fields:
        name = f.get("name")
        if not name:
            continue
        value = f.get("value", "")
        confidence = float(f.get("confidence") or 0.0)
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


def _convert_sap_line_item(item: dict[str, Any]) -> dict[str, Any]:
    """Convert a SAP DOX line item to the pipeline's line-item format."""
    return {
        "description": item.get("description", item.get("itemDescription", "")),
        "quantity": item.get("quantity"),
        "unit_price": item.get("unitPrice"),
        "total_amount": item.get("totalAmount", item.get("amount")),
        "item_number": item.get("itemNumber", ""),
        "gl_account": item.get("glAccount", ""),
        "cost_center": item.get("costCenter", ""),
        "tax_code": item.get("taxCode", ""),
        "confidence": float(item.get("confidence") or 0.0),
        "raw_data": item,
    }


# ── Document AI Client ────────────────────────────────────────────────────


class DocAIClient:
    """Client for SAP Document Information Extraction (DOX).

    Authenticates with SAP Document AI, finds the invoice extraction
    schema, and extracts header fields and line items from invoice documents.

    Args:
        cred_file: Path to an SAP Document AI service-key JSON file.
    """

    def __init__(
        self,
        cred_file: Optional[str] = None,
    ) -> None:
        cred = _resolve_credentials(cred_file)
        self.uaa_url = cred["uaa_url"]
        self.client_id = cred["client_id"]
        self.client_secret = cred["client_secret"]
        self.dox_url = cred["dox_url"].rstrip("/")
        self._token: Optional[str] = None
        self._token_expires: float = 0

    # -- Auth ------------------------------------------------------------

    def _get_token(self) -> str:
        """Get an OAuth2 access token via client-credentials flow."""
        import requests

        if self._token and time.time() < self._token_expires:
            return self._token

        logger.info("Authenticating with SAP Document AI at %s", self.uaa_url)
        resp = requests.post(
            f"{self.uaa_url}/oauth/token",
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            timeout=30,
        )
        resp.raise_for_status()
        token_data = resp.json()
        self._token = token_data["access_token"]
        self._token_expires = time.time() + 3000
        logger.info("Authenticated successfully")
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
        }

    # -- Schema discovery -----------------------------------------------

    def find_schema(self, name: str = "invoice") -> Optional[str]:
        """Find a DOX extraction schema by name. Returns the schema_id or None."""
        import requests

        resp = requests.get(
            f"{self.dox_url}/api/v1/document/schemas",
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        schemas = resp.json().get("schemas", [])
        for s in schemas:
            if name.lower() in s.get("name", "").lower():
                logger.info("Found schema: %s (id=%s)", s.get("name"), s.get("id"))
                return s.get("id")
        logger.warning("Schema '%s' not found", name)
        return None

    # -- Upload + poll --------------------------------------------------

    def upload_and_process(
        self,
        file_path: str,
        schema_id: Optional[str] = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """Upload a document to DOX and poll until extraction is complete.

        Args:
            file_path: Path to the document file (PDF, PNG, JPG, etc.).
            schema_id: DOX schema ID for extraction. If None, auto-finds invoice schema.

        Returns:
            The completed DOX job response with extraction results.
        """
        import requests

        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Document file not found: {file_path}")

        if not schema_id:
            schema_id = self.find_schema("invoice")
            if not schema_id:
                raise RuntimeError("No invoice schema found in SAP Document AI")

        file_name = os.path.basename(file_path)
        with open(file_path, "rb") as fh:
            files = {"file": (file_name, fh, "application/octet-stream")}
            data = {"schemaId": schema_id}
            logger.info("Uploading %s to SAP Document AI", file_name)
            resp = requests.post(
                f"{self.dox_url}/api/v1/document/jobs",
                headers=self._headers(),
                files=files,
                data=data,
                timeout=120,
            )
        resp.raise_for_status()
        job = resp.json()
        job_id = job.get("id")
        logger.info("Upload complete, job_id=%s", job_id)

        # Poll for completion
        start = time.time()
        while True:
            resp = requests.get(
                f"{self.dox_url}/api/v1/document/jobs/{job_id}",
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
            job = resp.json()
            status = job.get("status", "")
            logger.info("Polling job %s: status=%s", job_id, status)
            if status in ("DONE", "COMPLETED", "FAILED"):
                break
            if time.time() - start > _POLL_TIMEOUT_SEC:
                raise TimeoutError(f"DOX job {job_id} timed out after {_POLL_TIMEOUT_SEC}s")
            time.sleep(_POLL_INTERVAL_SEC)

        if status == "FAILED":
            raise RuntimeError(f"DOX extraction failed for job {job_id}")

        logger.info("Extraction complete for %s", file_name)
        return job

    # -- Result conversion -----------------------------------------------

    @staticmethod
    def convert_results(job_response: dict[str, Any]) -> dict[str, Any]:
        """Convert DOX extraction results to the pipeline's expected format.

        Returns a dict with:
            classification_extraction: list of DocumentEntity dicts
            detailed_extraction: list of DocumentEntity dicts
            line_items: list of line-item dicts
            invoice_source: dict of key header field values
        """
        extraction = job_response.get("extraction", {})
        header_fields = extraction.get("headerFields", [])
        line_items_raw = extraction.get("lineItems", [])

        entities = _convert_sap_header_fields(header_fields)
        line_items = [_convert_sap_line_item(item) for item in line_items_raw]

        # Build invoice_source from header fields
        values: dict[str, str] = {}
        for e in entities:
            val = e.get("stringValue") or str(e.get("numberValue", "") or "")
            if val:
                values[e["name"]] = str(val)

        invoice_source = {
            "file_name": job_response.get("fileName", ""),
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
            "vendor_number": ["vendor_number", "vendorNumber"],
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


# ── Convenience function ───────────────────────────────────────────────────


def run_triage_with_doc_ai(
    file_path: str,
    *,
    cred_file: Optional[str] = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Process a document via SAP Document AI and run the full triage pipeline.

    This is the one-call entry point for invoice extraction:
        1. Authenticate with SAP Document AI
        2. Find the invoice extraction schema
        3. Upload the document and poll for extraction completion
        4. Convert results to pipeline format
        5. Run the full triage pipeline

    Args:
        file_path: Path to the document file (PDF, PNG, JPG, etc.).
        cred_file: Path to SAP Document AI service-key JSON file.

    Returns:
        The TriageResult as a dict.
    """
    client = DocAIClient(cred_file=cred_file)

    # Steps 1-3: Authenticate, find schema, upload + poll
    job = client.upload_and_process(file_path=file_path)

    # Step 4: Convert results to pipeline format
    converted = DocAIClient.convert_results(job)

    logger.info("Extraction results: %d header fields, %d line items",
               len(converted["classification_extraction"]),
               len(converted["line_items"]))

    # Step 5: Run triage pipeline
    result = run_triage(
        classification_extraction=converted["classification_extraction"],
        detailed_extraction=converted["detailed_extraction"],
        line_items=converted["line_items"] or None,
        file_name=converted["invoice_source"].get("file_name", os.path.basename(file_path)),
    )

    # Enrich with DOX metadata
    result["sap_doc_ai"] = {
        "job_id": job.get("id", ""),
        "status": job.get("status", ""),
    }

    return result


# ── CLI Entry Point ────────────────────────────────────────────────────────


def main() -> None:
    """CLI entry point for SAP Document AI triage.

    Usage:
        python -m triage.doc_ai path/to/invoice.pdf
        python -m triage.doc_ai path/to/invoice.pdf --cred-file sap_credentials.json
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Extract and triage a document using SAP Document AI.",
    )
    parser.add_argument("file_path", help="Path to the document file (PDF, PNG, JPG, etc.)")
    parser.add_argument("--cred-file", default=None, help="Path to SAP Document AI service-key JSON file")
    args = parser.parse_args()

    if not os.path.isfile(args.file_path):
        print(f"ERROR: File not found: {args.file_path}")
        sys.exit(1)

    result = run_triage_with_doc_ai(
        file_path=args.file_path,
        cred_file=args.cred_file,
    )

    print("\n" + "=" * 60)
    print("  TRIAGE RESULT (via SAP Document AI)")
    print("=" * 60)
    print(json.dumps(result, indent=2, default=str))

    print("\n" + "=" * 60)
    print("  EVIDENCE TRAIL")
    print("=" * 60)
    for i, e in enumerate(result.get("evidence", []), 1):
        print(f"  {i:2d}. {e}")


if __name__ == "__main__":
    main()
"""SAP Document AI (Document Information Extraction) client.

Provides a real SAP Document AI integration layer for the triage pipeline.
This module handles:
    1.  OAuth2 client-credentials authentication (from config/sap_credentials.json)
    2.  Document upload + job submission
    3.  Polling for job completion
    4.  Converting SAP extraction results to the pipeline's DocumentEntity format
    5.  Convenience function ``run_triage_with_doc_ai()`` that uploads a file,
        gets results, and runs the full triage pipeline

Authentication:
    Credentials are loaded from a SAP service-key JSON file.
    Default location: ``config/sap_credentials.json``
    Override with ``--cred-file`` CLI arg or ``SAP_CREDENTIALS_FILE`` env var.
    See ``config/sap_credentials.json.example`` for the template.

Dependencies:
    ``requests`` (pip install requests)

Usage (programmatic)::

    from triage.doc_ai import run_triage_with_doc_ai

    result = run_triage_with_doc_ai("path/to/invoice.pdf")
    print(json.dumps(result, indent=2))

Usage (CLI)::

    python run_doc_ai.py path/to/invoice.pdf
    python run_doc_ai.py path/to/invoice.pdf --cred-file /custom/cred.json
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from .config import SAP_CREDENTIALS_FILE
from .runner import run_triage

logger = logging.getLogger("triage_doc_ai")

# ── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_CLIENT_ID = "default"
_POLL_INTERVAL_SEC = 3
_POLL_TIMEOUT_SEC = 300


# ── Credential resolution ──────────────────────────────────────────────────


def _load_cred_file(cred_file: str) -> dict[str, str]:
    """Load a SAP service-key JSON file and extract Doc AI credentials.

    Returns a dict with keys: uaa_url, client_id, client_secret, dox_url.
    """
    with open(cred_file, encoding="utf-8") as f:
        cred = json.load(f)
    return {
        "uaa_url": cred["uaa"]["url"],
        "client_id": cred["uaa"]["clientid"],
        "client_secret": cred["uaa"]["clientsecret"],
        "dox_url": cred["url"],
    }


def _resolve_credentials(
    cred_file: Optional[str] = None,
) -> dict[str, str]:
    """Resolve Doc AI credentials from a JSON file.

    Priority: explicit cred_file arg > SAP_CREDENTIALS_FILE env var >
              config/sap_credentials.json (default).
    """
    cred_file = cred_file or SAP_CREDENTIALS_FILE
    if cred_file and os.path.isfile(cred_file):
        logger.info("Loading SAP credentials from %s", cred_file)
        return _load_cred_file(cred_file)

    raise ValueError(
        f"SAP Document AI credentials not found.\n"
        f"Expected a credentials file at: {cred_file}\n"
        f"Create it from config/sap_credentials.json.example or pass --cred-file <path>"
    )


# ── Doc AI Client ──────────────────────────────────────────────────────────


class DocAIClient:
    """Client for the SAP Document Information Extraction API.

    Args:
        cred_file: Path to a SAP service-key JSON file. Defaults to
            ``config/sap_credentials.json`` (see ``SAP_CREDENTIALS_FILE`` env var).
        client_id_param: The SAP clientId query parameter (default: "default").
    """

    def __init__(
        self,
        cred_file: Optional[str] = None,
        client_id_param: str = _DEFAULT_CLIENT_ID,
    ) -> None:
        cred = _resolve_credentials(cred_file)
        self.uaa_url = cred["uaa_url"]
        self.client_id = cred["client_id"]
        self.client_secret = cred["client_secret"]
        self.dox_url = cred["dox_url"].rstrip("/")
        self.base_url = f"{self.dox_url}/document-information-extraction/v1"
        self.client_id_param = client_id_param
        self._token: Optional[str] = None
        self._token_expires: float = 0

    # -- Auth ------------------------------------------------------------

    def _get_token(self) -> str:
        """Get an OAuth2 access token via client-credentials flow."""
        import requests

        if self._token and time.time() < self._token_expires:
            return self._token

        logger.info("Authenticating with SAP Doc AI at %s …", self.uaa_url)
        resp = requests.post(
            f"{self.uaa_url}/oauth/token",
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            timeout=30,
        )
        resp.raise_for_status()
        token_data = resp.json()
        self._token = token_data["access_token"]
        # Cache for 50 minutes (tokens last 60 min)
        self._token_expires = time.time() + 3000
        logger.info("Authenticated successfully")
        return self._token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_token()}"}

    # -- Schemas ---------------------------------------------------------

    def list_schemas(self) -> list[dict[str, Any]]:
        """List all available extraction schemas."""
        import requests

        resp = requests.get(
            f"{self.base_url}/schemas",
            headers=self._headers(),
            params={"clientId": self.client_id_param},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("schemas", data.get("payload", []))

    def find_schema(self, document_type: str = "invoice") -> Optional[str]:
        """Find a schema ID for the given document type. Returns None if not found."""
        schemas = self.list_schemas()
        matched = [s for s in schemas if s.get("documentType") == document_type]
        if matched:
            schema_id = matched[0]["id"]
            logger.info("Found %s schema: %s (id=%s)", document_type, matched[0].get("name", ""), schema_id)
            return schema_id
        logger.warning("No schema found for documentType=%s", document_type)
        return None

    # -- Upload + Process ------------------------------------------------

    def upload_and_process(
        self,
        file_path: str,
        document_type: str = "invoice",
        schema_id: Optional[str] = None,
        enrichment: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Upload a document, submit a processing job, and return the job response.

        This is the main entry point — it handles the full upload → poll →
        return cycle and returns the complete job JSON with extraction results.

        Args:
            file_path: Path to the document file (PDF, PNG, JPG, etc.).
            document_type: Document type for extraction ("invoice", "credit_note", etc.).
            schema_id: Optional schema ID to use. If not provided, auto-finds invoice schema.
            enrichment: Optional enrichment settings dict.

        Returns:
            The full job response JSON containing extraction.headerFields and extraction.lineItems.
        """
        import requests

        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Document file not found: {file_path}")

        file_name = os.path.basename(file_path)

        # Auto-find schema if not provided
        if schema_id is None and document_type:
            schema_id = self.find_schema(document_type)

        # Build options
        options: dict[str, Any] = {
            "clientId": self.client_id_param,
            "documentType": document_type,
        }
        if schema_id:
            options["schemaId"] = schema_id
        if enrichment is None:
            enrichment = {
                "sender": {"top": 5, "type": "businessEntity", "subtype": "supplier"},
                "employee": {"type": "employee"},
            }
        if enrichment:
            options["enrichment"] = enrichment

        logger.info("Uploading '%s' to SAP Doc AI (type=%s, schema=%s) …",
                    file_name, document_type, schema_id or "auto")

        content_type = "application/pdf" if file_name.lower().endswith(".pdf") else "application/octet-stream"
        with open(file_path, "rb") as fh:
            resp = requests.post(
                f"{self.base_url}/document/jobs",
                headers=self._headers(),
                files={"file": (file_name, fh, content_type)},
                data={"options": json.dumps(options)},
                timeout=60,
            )
        resp.raise_for_status()
        job_id = resp.json()["id"]
        logger.info("Job submitted: id=%s", job_id)

        # Poll for completion
        result = self._poll_job(job_id)
        logger.info("Job %s completed with status: %s", job_id, result.get("status"))
        return result

    def _poll_job(
        self,
        job_id: str,
        poll_interval: int = _POLL_INTERVAL_SEC,
        timeout: int = _POLL_TIMEOUT_SEC,
    ) -> dict[str, Any]:
        """Poll a job until it reaches DONE or FAILED."""
        import requests

        start = time.time()
        while True:
            resp = requests.get(
                f"{self.base_url}/document/jobs/{job_id}",
                headers=self._headers(),
                params={"returnNullValues": "true"},
                timeout=30,
            )
            resp.raise_for_status()
            job = resp.json()
            status = job.get("status", "")
            logger.info("  Job %s status: %s", job_id, status)
            if status in ("DONE", "FAILED"):
                if status == "FAILED":
                    raise RuntimeError(f"SAP Doc AI job {job_id} failed: {job.get('error', 'unknown')}")
                return job
            if time.time() - start > timeout:
                raise TimeoutError(f"Job {job_id} timed out after {timeout}s")
            time.sleep(poll_interval)

    # -- Result conversion -----------------------------------------------

    @staticmethod
    def convert_results(job_response: dict[str, Any]) -> dict[str, Any]:
        """Convert SAP Doc AI job response to the pipeline's expected format.

        Returns a dict with:
            classification_extraction: list of DocumentEntity dicts
            detailed_extraction: list of DocumentEntity dicts
            line_items: list of line-item dicts
            invoice_source: dict of key header field values
        """
        extraction = job_response.get("extraction", {})
        header_fields_raw = extraction.get("headerFields", [])
        line_items_raw = extraction.get("lineItems", [])

        entities = _convert_sap_header_fields(header_fields_raw)

        # Build invoice_source lookup from extracted values
        values: dict[str, str] = {}
        for e in entities:
            val = e.get("stringValue") or str(e.get("numberValue", "") or "") or ""
            if val:
                values[e["name"]] = str(val)

        invoice_source: dict[str, str] = {"file_name": job_response.get("fileName", "")}
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

        # Convert line items from SAP format to pipeline format
        line_items: list[dict[str, Any]] = []
        for item in line_items_raw:
            line_items.append(_convert_sap_line_item(item))

        return {
            "classification_extraction": entities,
            "detailed_extraction": entities,
            "invoice_source": invoice_source,
            "line_items": line_items,
        }


# ── Conversion helpers ─────────────────────────────────────────────────────


def _convert_sap_header_fields(
    header_fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert SAP Doc AI headerFields list to DocumentEntity list.

    SAP format: [{"name": "documentNumber", "value": "INV-001", "confidence": 0.95}, ...]
    Pipeline format: [{"name": "documentNumber", "stringValue": "INV-001", "confidence": 0.95}, ...]
    """
    entities: list[dict[str, Any]] = []
    for f in header_fields:
        name = f.get("name", "")
        if not name:
            continue
        value = f.get("value", "")
        confidence = float(f.get("confidence") or 0)

        entity: dict[str, Any] = {"name": name, "confidence": confidence}
        if isinstance(value, bool):
            entity["type"] = "string"
            entity["stringValue"] = str(value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            entity["type"] = "number"
            entity["numberValue"] = value
        else:
            entity["type"] = "string"
            entity["stringValue"] = str(value) if value is not None else ""
        entities.append(entity)
    return entities


def _convert_sap_line_item(item: dict[str, Any]) -> dict[str, Any]:
    """Convert a SAP Doc AI line item to the pipeline's line-item dict format."""
    result: dict[str, Any] = {
        "item_number": str(item.get("itemNumber", item.get("rowNumber", ""))),
        "description": str(item.get("description", item.get("itemDescription", ""))),
        "quantity": item.get("quantity"),
        "unit_price": item.get("unitPrice"),
        "total_amount": item.get("totalAmount", item.get("amount")),
        "gl_account": str(item.get("glAccount", "")),
        "cost_center": str(item.get("costCenter", "")),
        "tax_code": str(item.get("taxCode", "")),
        "confidence": float(item.get("confidence") or 0),
        "raw_data": item,
    }
    return result


# ── Convenience function ───────────────────────────────────────────────────


def run_triage_with_doc_ai(
    file_path: str,
    *,
    cred_file: Optional[str] = None,
    document_type: str = "invoice",
    schema_id: Optional[str] = None,
    enrichment: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Upload a document to SAP Doc AI, get extraction results, and run the triage pipeline.

    This is the one-call entry point for real document triage:
        1. Authenticate with SAP Doc AI
        2. Upload the document and submit a processing job
        3. Poll until the job completes
        4. Convert extraction results to pipeline format
        5. Run the full triage pipeline (determine_document_type → classify_company →
           classify_direct_intercompany → classify_invoice_type → extract → validate → build_result)

    Args:
        file_path: Path to the document file (PDF, PNG, JPG, etc.).
        cred_file: Path to SAP service-key JSON file. Defaults to config/sap_credentials.json.
        document_type: Document type for extraction (default: "invoice").
        schema_id: Optional schema ID. Auto-finds invoice schema if not provided.
        enrichment: Optional enrichment settings dict.

    Returns:
        The TriageResult as a dict.
    """
    client = DocAIClient(cred_file=cred_file)

    # Step 1-3: Upload, process, poll
    job_response = client.upload_and_process(
        file_path=file_path,
        document_type=document_type,
        schema_id=schema_id,
        enrichment=enrichment,
    )

    # Step 4: Convert results
    converted = DocAIClient.convert_results(job_response)

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

    # Enrich with SAP Doc AI metadata
    result["sap_doc_ai"] = {
        "job_id": job_response.get("id", ""),
        "status": job_response.get("status", ""),
        "document_type_requested": document_type,
        "schema_id": schema_id or "",
    }

    return result


# ── CLI Entry Point ────────────────────────────────────────────────────────


def main() -> None:
    """CLI entry point for SAP Doc AI triage.

    Usage:
        python -m triage.doc_ai path/to/invoice.pdf
        python -m triage.doc_ai path/to/invoice.pdf --cred-file cred.json
        python -m triage.doc_ai path/to/invoice.pdf --document-type invoice
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Triage a document using SAP Document AI extraction.",
    )
    parser.add_argument("file_path", help="Path to the document file (PDF, PNG, JPG, etc.)")
    parser.add_argument("--cred-file", default=None, help="Path to SAP service-key JSON file")
    parser.add_argument("--document-type", default="invoice", help="Document type for extraction")
    parser.add_argument("--schema-id", default=None, help="Schema ID (auto-finds invoice schema if not provided)")
    parser.add_argument("--min-score", type=int, default=20, help="Min relevance score")
    args = parser.parse_args()

    if not os.path.isfile(args.file_path):
        print(f"ERROR: File not found: {args.file_path}")
        sys.exit(1)

    result = run_triage_with_doc_ai(
        file_path=args.file_path,
        cred_file=args.cred_file,
        document_type=args.document_type,
        schema_id=args.schema_id,
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

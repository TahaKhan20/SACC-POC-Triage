"""SAP AI Core (GPT-5.4) document extraction client for the triage pipeline.

This module handles:
    1.  OAuth2 client-credentials authentication (from config/ai_core_cred.json)
    2.  PDF-to-image conversion (via PyMuPDF) for multi-page documents
    3.  Per-page extraction via GPT-5.4 chat completions (vision)
    4.  Converting GPT-5.4 results to the pipeline's DocumentEntity format
    Note: AI Core is used for CLASSIFICATION ONLY. When the document_type
    is "invoice", SAP Document AI (DOX) handles the detailed field extraction.
    See ``triage.doc_ai`` for the DOX extraction client.

Authentication:
    Credentials are loaded from an SAP AI Core service-key JSON file.
    Default location: ``config/ai_core_cred.json``
    Override with ``--cred-file`` CLI arg or ``SAP_CREDENTIALS_FILE`` env var.

Dependencies:
    ``requests`` (pip install requests)
    ``pymupdf`` (pip install pymupdf) — for PDF page-to-image conversion

Usage (programmatic)::

    from triage.ai_core import run_triage_with_ai_core

    result = run_triage_with_ai_core("path/to/invoice.pdf")
    print(json.dumps(result, indent=2))

Usage (CLI)::

    python -m triage.ai_core path/to/invoice.pdf
    python -m triage.ai_core path/to/invoice.pdf --cred-file /custom/ai_core_cred.json
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any, Optional

from .config import SAP_CREDENTIALS_FILE
from .runner import run_triage

logger = logging.getLogger("triage_ai_core")

# ── Constants ───────────────────────────────────────────────────────────────

_POLL_TIMEOUT_SEC = 120
_DPI = 200
_MAX_COMPLETION_TOKENS = 2000

_EXTRACT_PROMPT = """You are a document classification assistant. Analyze this document and return ONLY valid JSON (no markdown, no explanation):
{
  "document_type": "",
  "confidence": 0.0,
  "reason": ""
}

Rules:
1. Classify the document into exactly one of these document_type values:
   - "invoice"
   - "credit_note"
   - "supporting_document"
   - "reconciliation"
   - "statement_of_account"
   - "purchase_order_list"
   - "general_correspondence"

2. Set confidence to a value between 0.0 and 1.0 reflecting how certain you are.
3. Provide a brief reason explaining your classification."""


# ── Credential resolution ──────────────────────────────────────────────────


def _load_cred_file(cred_file: str) -> dict[str, str]:
    """Load an SAP AI Core service-key JSON file and extract credentials.

    Returns a dict with keys: uaa_url, client_id, client_secret, ai_api_url.
    """
    with open(cred_file, encoding="utf-8") as f:
        cred = json.load(f)
    return {
        "uaa_url": cred["url"],
        "client_id": cred["clientid"],
        "client_secret": cred["clientsecret"],
        "ai_api_url": cred["serviceurls"]["AI_API_URL"],
    }


def _resolve_credentials(
    cred_file: Optional[str] = None,
) -> dict[str, str]:
    """Resolve SAP AI Core credentials from a JSON file.

    Priority: explicit cred_file arg > SAP_CREDENTIALS_FILE env var >
              config/ai_core_cred.json (default).
    """
    cred_file = cred_file or SAP_CREDENTIALS_FILE
    if cred_file and os.path.isfile(cred_file):
        logger.info("Loading SAP AI Core credentials from %s", cred_file)
        return _load_cred_file(cred_file)

    raise ValueError(
        f"SAP AI Core credentials not found.\n"
        f"Expected a credentials file at: {cred_file}\n"
        f"Create it from your SAP AI Core service key or pass --cred-file <path>"
    )


# ── AI Core Client ───────────────────────────────────────────────────────


class AICoreClient:
    """Client for SAP AI Core foundation-models (GPT-5.4) document extraction.

    Authenticates with SAP AI Core, finds the running foundation-models
    deployment, and extracts document fields via vision-capable chat completions.

    Args:
        cred_file: Path to an SAP AI Core service-key JSON file.
        resource_group: AI Core resource group (default: "default").
    """

    def __init__(
        self,
        cred_file: Optional[str] = None,
        resource_group: str = "default",
    ) -> None:
        cred = _resolve_credentials(cred_file)
        self.uaa_url = cred["uaa_url"]
        self.client_id = cred["client_id"]
        self.client_secret = cred["client_secret"]
        self.ai_api_url = cred["ai_api_url"].rstrip("/")
        self.resource_group = resource_group
        self._token: Optional[str] = None
        self._token_expires: float = 0
        self._dep_url: Optional[str] = None

    # -- Auth ------------------------------------------------------------

    def _get_token(self) -> str:
        """Get an OAuth2 access token via client-credentials flow."""
        import requests

        if self._token and time.time() < self._token_expires:
            return self._token

        logger.info("Authenticating with SAP AI Core at %s …", self.uaa_url)
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
            "AI-Resource-Group": self.resource_group,
        }

    # -- Deployment discovery --------------------------------------------

    def _get_deployment_url(self) -> str:
        """Find the running foundation-models deployment URL."""
        import requests

        if self._dep_url:
            return self._dep_url

        resp = requests.get(
            f"{self.ai_api_url}/v2/lm/deployments",
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        deps = resp.json().get("resources", [])
        dep = next(
            (d for d in deps
             if d.get("scenarioId") == "foundation-models"
             and d.get("status") == "RUNNING"),
            None,
        )
        if not dep:
            raise RuntimeError("No RUNNING foundation-models deployment found")
        self._dep_url = dep["deploymentUrl"]
        logger.info("Using deployment: %s", dep.get("id"))
        return self._dep_url

    # -- Page extraction -------------------------------------------------

    def _extract_page(self, image_b64: str, mime: str = "image/png") -> dict[str, Any]:
        """Send a single page image to GPT-5.4 and return parsed JSON."""
        import requests

        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": _EXTRACT_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
            ],
        }
        resp = requests.post(
            f"{self._get_deployment_url()}/v1/chat/completions",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"messages": [msg], "max_completion_tokens": _MAX_COMPLETION_TOKENS},
            timeout=_POLL_TIMEOUT_SEC,
        )
        if not resp.ok:
            raise RuntimeError(f"AI Core request failed: {resp.status_code} {resp.text[:500]}")

        content = resp.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = "\n".join(content.split("\n")[1:-1]) if content.endswith("```") else "\n".join(content.split("\n")[1:])
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"raw_content": content}

    def _classify_text(self, text: str) -> dict[str, Any]:
        """Send email body text to GPT-5.4 for classification (no image).

        Returns parsed JSON with document_type, confidence, and reason.
        """
        import requests

        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": _EXTRACT_PROMPT},
                {"type": "text", "text": f"Email content to classify:\n\n{text[:4000]}"},
            ],
        }
        resp = requests.post(
            f"{self._get_deployment_url()}/v1/chat/completions",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"messages": [msg], "max_completion_tokens": _MAX_COMPLETION_TOKENS},
            timeout=_POLL_TIMEOUT_SEC,
        )
        if not resp.ok:
            raise RuntimeError(f"AI Core text classification failed: {resp.status_code} {resp.text[:500]}")

        content = resp.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = "\n".join(content.split("\n")[1:-1]) if content.endswith("```") else "\n".join(content.split("\n")[1:])
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"raw_content": content}

    def classify_email_and_attachment(
        self,
        email_content: str,
        file_path: str,
    ) -> dict[str, Any]:
        """Classify both email content (text) and attachment (image) via GPT-5.4.

        Sends the email body as text and each PDF page as an image to AI Core.
        Returns a combined result with averaged confidence.

        Returns dict with:
            document_type: str or None (from agreement or highest-confidence source)
            confidence: float (average of email + attachment confidences)
            email_classification: dict (from text classification)
            attachment_classification: dict (from image classification)
            _source_file: str
        """
        file_name = os.path.basename(file_path)

        # 1. Classify email content as text
        logger.info("Classifying email content via AI Core (text)...")
        email_result = self._classify_text(email_content)
        logger.info("Email classification: type=%s conf=%.2f",
                    email_result.get("document_type"), email_result.get("confidence", 0.0))

        # 2. Classify attachment as image(s)
        logger.info("Classifying attachment '%s' via AI Core (image)...", file_name)
        attachment_result = self.upload_and_process(file_path=file_path)
        att_doc_type = attachment_result.get("document_type")
        att_conf = attachment_result.get("confidence", 0.0)
        logger.info("Attachment classification: type=%s conf=%.2f", att_doc_type, att_conf)

        # 3. Combine: average confidence, agree on document_type
        email_doc_type = email_result.get("document_type")
        email_conf = float(email_result.get("confidence", 0.0) or 0.0)

        # If both agree, use that type with averaged confidence
        if email_doc_type and att_doc_type and email_doc_type.lower() == att_doc_type.lower():
            combined_type = email_doc_type
            combined_conf = (email_conf + att_conf) / 2.0
        elif email_doc_type and att_doc_type:
            # Disagree: use the one with higher confidence
            if email_conf >= att_conf:
                combined_type = email_doc_type
                combined_conf = email_conf
            else:
                combined_type = att_doc_type
                combined_conf = att_conf
        elif email_doc_type:
            combined_type = email_doc_type
            combined_conf = email_conf
        elif att_doc_type:
            combined_type = att_doc_type
            combined_conf = att_conf
        else:
            combined_type = None
            combined_conf = 0.0

        return {
            "_source_file": file_name,
            "document_type": combined_type,
            "confidence": combined_conf,
            "email_classification": email_result,
            "attachment_classification": {
                "document_type": att_doc_type,
                "confidence": att_conf,
                "document_types_found": attachment_result.get("document_types_found", []),
                "total_pages": attachment_result.get("_total_pages", 0),
            },
        }

    # -- File processing -------------------------------------------------

    def upload_and_process(
        self,
        file_path: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """Process a document file and return extraction results.

        For PDFs: converts each page to a PNG image and sends it separately
        to GPT-5.4. For images (PNG/JPG): sends directly.

        Returns a combined dict with per-page results and aggregated fields.
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Document file not found: {file_path}")

        file_name = os.path.basename(file_path)
        ext = os.path.splitext(file_name)[1].lower().lstrip(".")

        # Build list of (image_b64, mime) tuples — 1 per page for PDFs
        page_images: list[tuple[str, str]] = []
        if ext == "pdf":
            try:
                import fitz  # PyMuPDF
            except ImportError:
                raise RuntimeError("PyMuPDF is required for PDF processing: pip install pymupdf")
            doc = fitz.open(file_path)
            for page in doc:
                pix = page.get_pixmap(dpi=_DPI)
                page_images.append((base64.b64encode(pix.tobytes("png")).decode(), "image/png"))
            doc.close()
            logger.info("%s: %d PDF page(s) converted", file_name, len(page_images))
        else:
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/jpeg")
            with open(file_path, "rb") as fh:
                page_images.append((base64.b64encode(fh.read()).decode(), mime))

        # Process each page separately
        page_results: list[dict[str, Any]] = []
        for i, (img_b64, mime) in enumerate(page_images):
            logger.info("Processing page %d/%d …", i + 1, len(page_images))
            result = self._extract_page(img_b64, mime)
            result["page"] = i + 1
            page_results.append(result)

        # Aggregate document_type and confidence across pages
        doc_types = [r.get("document_type") for r in page_results if "_error" not in r]
        unique_types = list(dict.fromkeys(doc_types))

        # Use the confidence from the first page that has a document_type
        first_conf = next((r.get("confidence", 0.0) for r in page_results
                          if r.get("document_type")), 0.0)

        return {
            "_source_file": file_name,
            "_total_pages": len(page_images),
            "page_results": page_results,
            "document_type": unique_types[0] if unique_types else None,
            "document_types_found": unique_types,
            "confidence": first_conf,
        }

    # -- Result conversion -----------------------------------------------

    @staticmethod
    def convert_results(job_response: dict[str, Any]) -> dict[str, Any]:
        """Convert AI Core classification results to the pipeline's expected format.

        Returns a dict with:
            classification_extraction: list of DocumentEntity dicts (document_type only)
            detailed_extraction: same as classification_extraction
            line_items: empty list (DOX handles detailed extraction for invoices)
            invoice_source: dict with file_name and document_type
        """
        entities: list[dict[str, Any]] = []
        doc_type = job_response.get("document_type")
        if doc_type is not None:
            entities.append({
                "name": "document_type",
                "type": "string",
                "stringValue": str(doc_type),
                "confidence": float(job_response.get("confidence", 0.85) or 0.85),
            })

        invoice_source: dict[str, str] = {
            "file_name": job_response.get("_source_file", ""),
            "document_type": str(doc_type or ""),
        }

        return {
            "classification_extraction": entities,
            "detailed_extraction": entities,
            "invoice_source": invoice_source,
            "line_items": [],
        }


# ── Convenience function ───────────────────────────────────────────────────


def run_triage_with_ai_core(
    file_path: str,
    *,
    cred_file: Optional[str] = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Process a document via SAP AI Core (classification only) and run triage.

    This is the one-call entry point for document classification:
        1. Authenticate with SAP AI Core
        2. Convert document pages to images (PDF → PNG via PyMuPDF)
        3. Send each page to GPT-5.4 for classification (document_type + confidence)
        4. Convert results to pipeline format
        5. Run the triage pipeline

    Note: For invoices, detailed field extraction (header fields, line items)
    should be handled by SAP Document AI (DOX) after classification.

    Args:
        file_path: Path to the document file (PDF, PNG, JPG, etc.).
        cred_file: Path to SAP AI Core service-key JSON file.

    Returns:
        The TriageResult as a dict.
    """
    client = AICoreClient(cred_file=cred_file)

    # Steps 1-3: Authenticate, convert pages, classify via GPT-5.4
    classification = client.upload_and_process(file_path=file_path)

    # Step 4: Convert results to pipeline format
    converted = AICoreClient.convert_results(classification)

    logger.info("Classification results: document_type=%s, confidence=%.2f",
               classification.get("document_type"),
               classification.get("confidence", 0.0))

    # Step 5: Run triage pipeline
    result = run_triage(
        classification_extraction=converted["classification_extraction"],
        detailed_extraction=converted["detailed_extraction"],
        line_items=converted["line_items"] or None,
        file_name=converted["invoice_source"].get("file_name", os.path.basename(file_path)),
    )

    # Enrich with AI Core metadata
    result["sap_ai_core"] = {
        "total_pages": classification.get("_total_pages", 0),
        "document_types_found": classification.get("document_types_found", []),
        "confidence": classification.get("confidence", 0.0),
    }

    return result


def run_triage_with_email_and_attachment(
    email_content: str,
    file_path: str,
    *,
    cred_file: Optional[str] = None,
) -> dict[str, Any]:
    """Classify email + attachment via SAP AI Core and run triage.

    This is the two-source classification entry point:
        1. Authenticate with SAP AI Core
        2. Classify email body text via GPT-5.4 (text mode)
        3. Classify attachment pages via GPT-5.4 (image mode)
        4. Combine results with averaged confidence
        5. Convert to pipeline format
        6. Run the triage pipeline

    Args:
        email_content: The email body text to classify.
        file_path: Path to the attachment file (PDF, PNG, JPG, etc.).
        cred_file: Path to SAP AI Core service-key JSON file.

    Returns:
        The TriageResult as a dict.
    """
    client = AICoreClient(cred_file=cred_file)

    # Steps 1-4: Classify both email content and attachment
    combined = client.classify_email_and_attachment(email_content, file_path)

    logger.info("Combined classification: document_type=%s, confidence=%.2f",
               combined.get("document_type"),
               combined.get("confidence", 0.0))

    # Step 5: Convert to pipeline format
    converted = AICoreClient.convert_results(combined)

    # Step 6: Run triage pipeline
    result = run_triage(
        classification_extraction=converted["classification_extraction"],
        detailed_extraction=converted["detailed_extraction"],
        line_items=converted["line_items"] or None,
        file_name=converted["invoice_source"].get("file_name", os.path.basename(file_path)),
    )

    # Enrich with AI Core metadata
    result["sap_ai_core"] = {
        "document_type": combined.get("document_type"),
        "confidence": combined.get("confidence", 0.0),
        "email_classification": combined.get("email_classification", {}),
        "attachment_classification": combined.get("attachment_classification", {}),
    }

    return result


# ────────────────────────────────────────────────────────


def main() -> None:
    """CLI entry point for SAP AI Core triage.

    Usage:
        python -m triage.ai_core path/to/invoice.pdf
        python -m triage.ai_core path/to/invoice.pdf --cred-file ai_core_cred.json
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Triage a document using SAP AI Core (GPT-5.4) extraction.",
    )
    parser.add_argument("file_path", help="Path to the document file (PDF, PNG, JPG, etc.)")
    parser.add_argument("--cred-file", default=None, help="Path to SAP AI Core service-key JSON file")
    args = parser.parse_args()

    if not os.path.isfile(args.file_path):
        print(f"ERROR: File not found: {args.file_path}")
        sys.exit(1)

    result = run_triage_with_ai_core(
        file_path=args.file_path,
        cred_file=args.cred_file,
    )

    print("\n" + "=" * 60)
    print("  TRIAGE RESULT (via SAP AI Core / GPT-5.4)")
    print("=" * 60)
    print(json.dumps(result, indent=2, default=str))

    print("\n" + "=" * 60)
    print("  EVIDENCE TRAIL")
    print("=" * 60)
    for i, e in enumerate(result.get("evidence", []), 1):
        print(f"  {i:2d}. {e}")


if __name__ == "__main__":
    main()

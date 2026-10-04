# SACC-POC-Triage

A production-ready AP Invoice Triage system that fetches emails, classifies document relevance, and triages invoices — integrating with real SAP Document AI for document extraction.

## Overview

Three modular packages work together:

| Package | Purpose | API Dependency |
| --- | --- | --- |
| `triage` | Classify and validate invoices (document type, company, invoice type) | SAP Document AI (via `doc_ai.py`) or direct data input |
| `email_intake` | Fetch, enrich, and score emails for relevance | Microsoft Graph API (optional — classification runs standalone) |
| `workflow` | Orchestrate: email intake → triage | Inherits from both |

### Triage Pipeline (7 steps)

1. **Document Type** — Keyword matching against `document_types.json` (INVOICE, CREDIT_NOTE, STATEMENT, SUPPORTING_DOCUMENT, OTHER)
2. **Company Classification** — Saudia Cargo vs Saudia Airline vs no-match
3. **Direct vs Intercompany** — Based on company code and supplier name
4. **Invoice Type** — FUEL, CHARTER, SERVICE, or OTHER via keyword matching
5. **Detailed Field Extraction** — Parse extraction entities into header fields and line items
6. **Validation** — Required fields, confidence thresholds, line item presence
7. **TriageResult** — Structured JSON output with evidence trail

### Email Intake Pipeline (3 nodes)

1. **Fetch** — List messages from Microsoft Graph API
2. **Enrich** — Fetch full body content and attachment metadata per message
3. **Classify** — Score each email by sender / subject / content / attachments; return relevant emails

## Project Structure

```
SACC-POC-Triage/
├── src/
│   ├── triage/                # Invoice triage pipeline (standalone)
│   │   ├── __init__.py
│   │   ├── models.py           # Enums & dataclasses
│   │   ├── config.py           # Environment-based configuration
│   │   ├── schemas.py          # Field definitions per invoice type
│   │   ├── state.py            # TriageState container
│   │   ├── pipeline.py         # All standalone pipeline step functions
│   │   ├── doc_ai.py           # SAP Document AI client (upload + extract)
│   │   └── runner.py           # run_triage() + CLI
│   ├── email_intake/           # Email fetch, enrich, classify
│   │   ├── __init__.py
│   │   ├── models.py           # EmailMessage dataclass
│   │   ├── config.py           # Scoring weights & Graph API config
│   │   ├── classify.py         # Scoring & classification (standalone)
│   │   ├── fetch.py            # Graph API fetch/enrich (requires httpx)
│   │   └── runner.py            # run_intake() + save_attachment_to_temp
│   └── workflow/               # Orchestrates email intake → triage
│       ├── __init__.py
│       └── orchestrator.py     # run_workflow() + run_workflow_standalone()
├── config/
│   ├── document_types.json    # Shared document type keyword categories
│   └── .env.example            # Environment variable template
├── data/
│   └── doc_ai_payload.json     # Sample SAP Document AI extraction payload
├── tests/
│   ├── __init__.py
│   ├── test_pipeline.py       # Unit tests for triage pipeline
│   ├── test_ai_core.py        # Unit tests for SAP AI Core client
│   └── test_email_intake.py   # Unit tests for email intake
├── run_triage.py              # Triage Agent CLI (payload / --ai-core / --doc-ai)
├── run_email_intake.py        # Email Intake Agent CLI (fetch + enrich)
├── run_workflow.py            # Workflow CLI (email → triage)
├── requirements.txt           # Dependencies (core has zero)
├── README.md
└── .gitignore
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. (Optional) Configure environment
cp config/.env.example .env  # Edit as needed

# 3. Run triage with sample data
python run_triage.py                               # First document from default payload
python run_triage.py data/doc_ai_payload.json      # Specify file
python run_triage.py data/doc_ai_payload.json all  # All documents

# 4. Fetch the latest email only (requires Graph API credentials)
python run_email_intake.py                # latest email only
python run_email_intake.py --top 10       # up to 10 most recent emails

# 5. Run the full workflow (requires Graph API credentials)
python run_workflow.py                  # fetch + triage all emails
python run_workflow.py --top 10         # limit to 10 emails
python run_workflow.py --dry-run        # fetch only, skip triage

# 6. Triage real documents via SAP (requires SAP credentials)
python run_triage.py --ai-core path/to/invoice.pdf        # SAP AI Core (GPT-5.4)
python run_triage.py --doc-ai                             # SAP Document AI batch (invoice_samples/)
python run_triage.py --doc-ai path/to/invoice.pdf --cred-file cred.json
```

## SAP Document AI Integration

The `triage.doc_ai` module provides real SAP Document AI (Document Information Extraction) integration:

1. **Authenticate** via OAuth2 client-credentials flow
2. **Upload** a document (PDF, PNG, JPG) and submit a processing job
3. **Poll** until the job reaches DONE status
4. **Convert** SAP extraction results (`headerFields`, `lineItems`) to the pipeline's DocumentEntity format
5. **Run** the full triage pipeline on the converted results

### Credentials

Provide either:
* A SAP service-key JSON file (`cred.json`) via `--cred-file` or `SAP_DOCAI_CRED_FILE` env var
* Individual env vars: `SAP_DOCAI_UAA_URL`, `SAP_DOCAI_CLIENT_ID_ENV`, `SAP_DOCAI_CLIENT_SECRET_ENV`, `SAP_DOCAI_BASE_URL_ENV`

### Programmatic Usage

```python
from triage import run_triage_with_doc_ai

result = run_triage_with_doc_ai(
    "path/to/invoice.pdf",
    cred_file="cred.json",
    document_type="invoice",
)
print(json.dumps(result, indent=2))
```

### Using the DocAIClient directly

```python
from triage import DocAIClient

client = DocAIClient(cred_file="cred.json")

# Upload and process a document
job = client.upload_and_process("invoice.pdf", document_type="invoice")

# Convert to pipeline format
converted = DocAIClient.convert_results(job)
print(f"{len(converted['classification_extraction'])} header fields extracted")
print(f"{len(converted['line_items'])} line items extracted")
```

## Programmatic Usage

### Triage pipeline (standalone)

```python
import sys
sys.path.insert(0, "src")

from triage import run_triage

entities = [
    {"name": "invoice_number", "stringValue": "INV-001", "confidence": 0.95},
    {"name": "company_code", "stringValue": "1000", "confidence": 0.90},
    {"name": "total_amount", "numberValue": 18900.0, "confidence": 0.85},
]

result = run_triage(classification_extraction=entities, detailed_extraction=entities)
print(result)
```

### Email classification (standalone)

```python
from email_intake import EmailMessage, classify_emails

emails = [
    EmailMessage(
        message_id="msg-001",
        subject="Invoice for March 2024",
        sender="supplier@example.com",
        body_preview="Please find attached the invoice.",
        has_attachments=True,
        attachments=[{"name": "invoice.pdf"}],
    ),
]

relevant = classify_emails(emails, min_score=50)
for email in relevant:
    print(f"  [{email.category}] score={email.score}  {email.subject}")
```

### Full workflow (standalone mode)

```python
from workflow import run_workflow_standalone
from email_intake import EmailMessage

emails = [EmailMessage(
    message_id="msg-001",
    subject="Invoice for fuel services",
    sender="supplier@fuel.com",
    body_preview="Fuel invoice attached.",
    has_attachments=True,
    attachments=[{"name": "fuel_invoice.pdf", "contentBytes": ""}],
)]

summary = run_workflow_standalone(emails, dry_run=False)
print(json.dumps(summary, indent=2, default=str))
```

## TriageResult Output

| Field | Type | Description |
| --- | --- | --- |
| `document_type` | str | INVOICE \| CREDIT_NOTE \| STATEMENT \| SUPPORTING_DOCUMENT \| OTHER |
| `company_classification` | str | MATCH \| NO_MATCH \| UNCERTAIN |
| `direct_intercompany` | str | DIRECT \| INTERCOMPANY \| UNKNOWN |
| `invoice_type` | str | FUEL \| CHARTER \| SERVICE \| OTHER |
| `extracted_header_fields` | dict | Extracted field name → value |
| `line_items` | list[dict] | Line item details |
| `confidence` | float | Overall extraction confidence (0.0–1.0) |
| `evidence` | list[str] | Human-readable evidence trail |
| `review_required` | bool | Whether manual review is needed |
| `review_reason` | str \| null | Reason for review if required |

## Email Scoring Rules

| Rule | Points | Description |
| --- | --- | --- |
| Document type in subject | 20 | Keywords from `document_types.json` matched in subject |
| Document type in body | 20 | Keywords matched in body preview + content |
| Has any attachment | 30 | Email has at least one attachment |
| Document type in attachment name | 30 | Keywords matched in attachment filenames |

Emails scoring >= `MIN_SCORE_THRESHOLD` (default 50) are considered relevant.

## Configuration

All configuration is read from environment variables (see `config/.env.example`):

### Triage

| Variable | Default | Description |
| --- | --- | --- |
| `CONFIGURED_COMPANY_CODE` | `1000` | The company code to match against |
| `CONFIGURED_COMPANY_NAME` | `SACC Airlines` | Company name for fuzzy matching |
| `INTERCOMPANY_COMPANY_CODES` | `2000,3000` | Comma-separated intercompany codes |
| `MIN_CONFIDENCE_THRESHOLD` | `0.75` | Minimum overall confidence |
| `MIN_REQUIRED_FIELD_CONFIDENCE` | `0.80` | Minimum per-field confidence |

### Email Intake

| Variable | Default | Description |
| --- | --- | --- |
| `GRAPH_USER_ID` | — | Target user ID or UPN (required for live fetch) |
| `GRAPH_API_TOKEN` | — | API access token (required for live fetch) |
| `GRAPH_API_BASE_URL` | `https://graph.microsoft.com` | Graph endpoint |
| `SUBJECT_DOC_TYPE_POINTS` | `20` | Points for doc type in subject |
| `BODY_DOC_TYPE_POINTS` | `20` | Points for doc type in body |
| `HAS_ATTACHMENT_POINTS` | `30` | Points for having any attachment |
| `ATTACHMENT_DOC_TYPE_POINTS` | `30` | Points for doc type in attachment name |
| `MIN_SCORE_THRESHOLD` | `50` | Minimum score for relevance |

## Testing

```bash
pip install pytest
pytest tests/
```

## Key Differences from the Original SACC Project

| Aspect | Original (SACC/langgraph/) | This POC |
| --- | --- | --- |
| Triage API dependency | Required local SACC API server | Uses real SAP Document AI API |
| Triage architecture | Monolithic `triage_agent.py` (~1200 lines) | Modular package (`src/triage/`) |
| Document extraction | Local API seeded data | SAP Document AI (`doc_ai.py`) |
| Email intake | Combined fetch + classify in one file | Separated into `fetch.py` + `classify.py` |
| Workflow | Imports from sibling folders via sys.path | Proper package imports |
| API clients | `DocAIApiClient`, `SupplierInvoiceApiClient` | `DocAIClient` (real SAP Doc AI) |
| Data input | Via HTTP API calls to local server | Direct function arguments or SAP Doc AI |
| Use case | Integration with local mock APIs | Production triage with real SAP APIs |

"""Broadcaster: post a job to HCS and return the Job object.

Usage::

    from taskforge.broadcaster.broadcast_job import post_job
    from taskforge.ledger.hcs_client import create_topic

    topic_id = create_topic()
    job, hcs_tx = post_job(topic_id)
"""
from __future__ import annotations

import time
import uuid

from taskforge.ledger.hcs_client import submit_message
from taskforge.models import Job, to_json

# ── Planted invoice ─────────────────────────────────────────────────────────
# This is the fixed document workers must extract.  Ground truth is known in
# advance (broadcaster authored it); workers must not be given the answers.
INVOICE_TEXT = """\
INVOICE

Vendor:  Meridian Cloud Solutions Ltd.
Address: 48 Finsbury Square, London, EC2A 1RG
VAT No:  GB 428 7312 10

Invoice No:   MCL-2024-0391
Invoice Date: 2024-11-15
Due Date:     2024-12-15

Bill To:
  TaskForge Ltd.
  10 Appold Street, London, EC2A 2AP

Description                          Qty    Unit Price (GBP)   Total (GBP)
---------------------------------------------------------------------------
Cloud Compute (t3.xlarge, 30 days)    1          840.00          840.00
Managed PostgreSQL (db.r5.large)      1          320.00          320.00
Egress Bandwidth (2.4 TB @ £0.05/GB)  2400         0.05          120.00
Support Package — Enterprise Tier     1          275.00          275.00
---------------------------------------------------------------------------
Subtotal                                                        1,555.00
VAT (20%)                                                         311.00
---------------------------------------------------------------------------
TOTAL DUE                                                      £1,866.00
Currency: GBP

Payment terms: 30 days net.  Bank transfer preferred.
Sort code: 20-19-47  |  Account: 53841276
"""

# Ground truth — exact values workers must extract (used by verifier's 70% check)
GROUND_TRUTH: dict = {
    "vendor_name": "Meridian Cloud Solutions Ltd.",
    "invoice_number": "MCL-2024-0391",
    "invoice_date": "2024-11-15",
    "total_amount": 1866.00,
    "currency": "GBP",
    "line_items": [
        {"description": "Cloud Compute (t3.xlarge, 30 days)", "quantity": 1, "unit_price": 840.00},
        {"description": "Managed PostgreSQL (db.r5.large)", "quantity": 1, "unit_price": 320.00},
        {"description": "Egress Bandwidth (2.4 TB @ £0.05/GB)", "quantity": 2400, "unit_price": 0.05},
        {"description": "Support Package — Enterprise Tier", "quantity": 1, "unit_price": 275.00},
    ],
}

# Expected JSON output shape workers must produce
OUTPUT_SCHEMA: dict = {
    "vendor_name": "string",
    "invoice_number": "string",
    "invoice_date": "YYYY-MM-DD",
    "total_amount": "number",
    "currency": "ISO 4217 code",
    "line_items": [
        {"description": "string", "quantity": "number", "unit_price": "number"}
    ],
}


def post_job(topic_id: str) -> tuple[Job, str]:
    """Create a new invoice-extraction job and post it to HCS.

    Assigns a fresh UUID job ID, sets a 10-minute deadline, serialises the job
    with :func:`~taskforge.models.to_json`, and submits it to the topic.

    Args:
        topic_id: HCS topic ID string, e.g. ``"0.0.5678"``.

    Returns:
        A tuple of ``(Job, hcs_tx_id)`` where ``hcs_tx_id`` is the transaction
        ID of the HCS submit message (for the HashScan link).
    """
    job = Job(
        job_id=str(uuid.uuid4())[:8],
        description=(
            "Extract structured invoice data from the provided invoice text "
            "into the specified JSON schema.  Invoice text is in the "
            "'invoice_text' field."
        ),
        output_schema=OUTPUT_SCHEMA,
        bounty_amount=0.1,
        currency="HBAR",
        deadline_ts=time.time() + 600,
    )
    hcs_tx = submit_message(topic_id, to_json(job))
    return job, hcs_tx

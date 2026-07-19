"""Broadcaster: post a job to HCS and return the Job object.

Usage::

    from taskforge.broadcaster.broadcast_job import post_job
    from taskforge.ledger.hcs_client import create_topic

    topic_id = create_topic()
    job, hcs_tx = post_job(topic_id)
"""
from __future__ import annotations

import random
import time
import uuid

from taskforge.ledger.hcs_client import submit_message
from taskforge.models import Job, to_json

# ── Task pool ────────────────────────────────────────────────────────────────
# Each entry is a dict with "invoice_text", "ground_truth", and "description".
# The scheduler picks a random entry each time it generates a new task so the
# marketplace never shows the same invoice twice in a row.

_TASK_POOL: list[dict] = [
    # ── Task 0: Meridian Cloud (original) ────────────────────────────────────
    {
        "description": "Extract structured invoice data from a UK cloud-services invoice.",
        "invoice_text": """\
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
""",
        "ground_truth": {
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
        },
    },
    # ── Task 1: Apex Design Studio (USD, freelance) ───────────────────────────
    {
        "description": "Extract structured invoice data from a US freelance design studio invoice.",
        "invoice_text": """\
INVOICE

From: Apex Design Studio LLC
      317 West 34th Street, Suite 8, New York, NY 10001
      EIN: 47-3829104

Invoice #:    ADS-2025-0117
Invoice Date: 2025-01-22
Due Date:     2025-02-21

Bill To:
  Horizon Ventures Inc.
  200 Park Avenue, New York, NY 10166

Description                              Qty    Rate (USD)    Amount (USD)
--------------------------------------------------------------------------
Brand Identity Package                    1       4,500.00      4,500.00
UI/UX Wireframes (mobile app)             1       2,800.00      2,800.00
Illustration Assets (custom icons × 40)  40          45.00      1,800.00
Rush Delivery Fee (48-hour turnaround)    1         350.00        350.00
--------------------------------------------------------------------------
Subtotal                                                        9,450.00
Sales Tax (8.875%)                                                838.69
--------------------------------------------------------------------------
TOTAL DUE                                                     $10,288.69
Currency: USD

Payment via ACH or wire transfer.
Routing: 021000021  |  Account: 4087239156
""",
        "ground_truth": {
            "vendor_name": "Apex Design Studio LLC",
            "invoice_number": "ADS-2025-0117",
            "invoice_date": "2025-01-22",
            "total_amount": 10288.69,
            "currency": "USD",
            "line_items": [
                {"description": "Brand Identity Package", "quantity": 1, "unit_price": 4500.00},
                {"description": "UI/UX Wireframes (mobile app)", "quantity": 1, "unit_price": 2800.00},
                {"description": "Illustration Assets (custom icons × 40)", "quantity": 40, "unit_price": 45.00},
                {"description": "Rush Delivery Fee (48-hour turnaround)", "quantity": 1, "unit_price": 350.00},
            ],
        },
    },
    # ── Task 2: Nordic Logistics GmbH (EUR, freight) ──────────────────────────
    {
        "description": "Extract structured invoice data from a German freight logistics invoice.",
        "invoice_text": """\
RECHNUNG / INVOICE

Lieferant / Vendor: Nordic Logistics GmbH
Adresse:            Hafenstraße 22, 20459 Hamburg, Germany
USt-IdNr.:          DE 291 483 720

Rechnungsnummer / Invoice No: NLG-2025-4402
Rechnungsdatum  / Date:       2025-03-10
Fälligkeitsdatum / Due Date:  2025-04-09

Rechnungsempfänger / Bill To:
  Sunrise Trading B.V.
  Herengracht 182, 1016 BR Amsterdam, Netherlands

Leistungsbeschreibung / Description    Qty   Preis/Einheit (EUR)   Betrag (EUR)
-------------------------------------------------------------------------------
FCL Ocean Freight — Hamburg to Rotterdam  2         1,240.00          2,480.00
Port Handling & Documentation             2           185.00            370.00
Customs Clearance — Netherlands           1           320.00            320.00
Fuel Surcharge (3.5 % of freight)         1            86.80             86.80
-------------------------------------------------------------------------------
Zwischensumme / Subtotal                                            3,256.80
MwSt. / VAT (19 %)                                                    618.79
-------------------------------------------------------------------------------
GESAMTBETRAG / TOTAL DUE                                           €3,875.59
Währung / Currency: EUR

Zahlung per SEPA-Überweisung.
IBAN: DE44 2004 1010 0293 8530 00  |  BIC: COBADEFFXXX
""",
        "ground_truth": {
            "vendor_name": "Nordic Logistics GmbH",
            "invoice_number": "NLG-2025-4402",
            "invoice_date": "2025-03-10",
            "total_amount": 3875.59,
            "currency": "EUR",
            "line_items": [
                {"description": "FCL Ocean Freight — Hamburg to Rotterdam", "quantity": 2, "unit_price": 1240.00},
                {"description": "Port Handling & Documentation", "quantity": 2, "unit_price": 185.00},
                {"description": "Customs Clearance — Netherlands", "quantity": 1, "unit_price": 320.00},
                {"description": "Fuel Surcharge (3.5 % of freight)", "quantity": 1, "unit_price": 86.80},
            ],
        },
    },
    # ── Task 3: SolarEdge Installations Pty (AUD, construction) ──────────────
    {
        "description": "Extract structured invoice data from an Australian solar installation invoice.",
        "invoice_text": """\
TAX INVOICE

Supplier:  SolarEdge Installations Pty Ltd
ABN:       51 824 739 106
Address:   14 Renewable Way, Canberra ACT 2601, Australia

Invoice No:   SEI-2025-0883
Invoice Date: 2025-04-03
Due Date:     2025-05-03

Bill To:
  Greenfield Property Group
  Level 12, 1 Martin Place, Sydney NSW 2000

Description                                    Qty   Unit Price (AUD)   Total (AUD)
------------------------------------------------------------------------------------
395W Monocrystalline Solar Panels               24          420.00        10,080.00
5kW Hybrid Inverter (Fronius Primo)              2        2,350.00         4,700.00
Installation Labour (2 technicians × 3 days)    6          680.00         4,080.00
Roof Mounting Hardware Kit                       2          315.00           630.00
------------------------------------------------------------------------------------
Subtotal                                                                  19,490.00
GST (10 %)                                                                 1,949.00
------------------------------------------------------------------------------------
TOTAL DUE                                                               AUD 21,439.00
Currency: AUD

EFT to: BSB 062-000  |  Account: 1234 56789
Reference: SEI-2025-0883
""",
        "ground_truth": {
            "vendor_name": "SolarEdge Installations Pty Ltd",
            "invoice_number": "SEI-2025-0883",
            "invoice_date": "2025-04-03",
            "total_amount": 21439.00,
            "currency": "AUD",
            "line_items": [
                {"description": "395W Monocrystalline Solar Panels", "quantity": 24, "unit_price": 420.00},
                {"description": "5kW Hybrid Inverter (Fronius Primo)", "quantity": 2, "unit_price": 2350.00},
                {"description": "Installation Labour (2 technicians × 3 days)", "quantity": 6, "unit_price": 680.00},
                {"description": "Roof Mounting Hardware Kit", "quantity": 2, "unit_price": 315.00},
            ],
        },
    },
]

# ── Back-compat aliases (used by run_demo.py — must stay unchanged) ──────────
# run_demo.py imports INVOICE_TEXT and GROUND_TRUTH directly; keep them pointing
# at the first task so the demo path is never affected.
INVOICE_TEXT: str = _TASK_POOL[0]["invoice_text"]
GROUND_TRUTH: dict = _TASK_POOL[0]["ground_truth"]

# Expected JSON output shape workers must produce (shared across all tasks)
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


def pick_task() -> dict:
    """Return a random task entry from :data:`_TASK_POOL`.

    Returns:
        A dict with ``"description"``, ``"invoice_text"``, and ``"ground_truth"``
        keys.
    """
    return random.choice(_TASK_POOL)


def post_job(topic_id: str, task: dict | None = None) -> tuple[Job, str]:
    """Create a new invoice-extraction job and post it to HCS.

    Assigns a fresh UUID job ID, sets a 10-minute deadline, serialises the job
    with :func:`~taskforge.models.to_json`, and submits it to the topic.

    Args:
        topic_id: HCS topic ID string, e.g. ``"0.0.5678"``.
        task: Optional task dict from :data:`_TASK_POOL` (keys: ``description``,
            ``invoice_text``, ``ground_truth``).  If ``None``, the first pool
            entry is used (preserves backward compatibility for ``run_demo.py``).

    Returns:
        A tuple of ``(Job, hcs_tx_id)`` where ``hcs_tx_id`` is the transaction
        ID of the HCS submit message (for the HashScan link).
    """
    if task is None:
        task = _TASK_POOL[0]
    job = Job(
        job_id=str(uuid.uuid4())[:8],
        description=task["description"],
        output_schema=OUTPUT_SCHEMA,
        bounty_amount=0.1,
        currency="HBAR",
        deadline_ts=time.time() + 600,
    )
    hcs_tx = submit_message(topic_id, to_json(job))
    return job, hcs_tx

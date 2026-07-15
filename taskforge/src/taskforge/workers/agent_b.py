"""Worker Agent B — engineered invoice extraction prompt.

Agent B uses a more detailed prompt with explicit rules for edge cases (total
including VAT, line-item quantity precision, date format).  It consistently
scores higher than Agent A on the ground-truth check.

Usage (called by run_demo.py, not run directly)::

    from taskforge.workers.agent_b import run_agent_b
    submission, hcs_tx, server = run_agent_b(topic_id, job, worker_account_id)
"""
from __future__ import annotations

import json
import os
import time

from dotenv import load_dotenv
from groq import Groq
from groq import RateLimitError as _GroqRateLimitError

from taskforge.ledger.hcs_client import submit_message
from taskforge.models import Submission, to_json
from taskforge.settlement.claim_reward import ClaimServer

_MODEL = "llama-3.3-70b-versatile"
_AGENT_ID = "agent_b"
_CLAIM_PORT = 8403
_BLOCKY402_URL = "https://api.testnet.blocky402.com"

_PROMPT_TEMPLATE = """\
You are a precise invoice-data extractor. Read the invoice below and output a
JSON object with EXACTLY the fields listed. Follow every rule strictly.

Invoice text:
{invoice_text}

Rules:
1. "vendor_name": full legal name of the seller as written on the invoice.
2. "invoice_number": the exact invoice reference code, no spaces added/removed.
3. "invoice_date": the issue date in ISO 8601 format YYYY-MM-DD.
4. "total_amount": the FINAL total payable (include VAT / taxes if shown),
   as a plain number — no currency symbols, no commas.
5. "currency": ISO 4217 three-letter code (GBP, USD, EUR, etc.).
6. "line_items": one entry per line item BEFORE taxes/subtotals. Each entry:
   - "description": verbatim description text from the invoice line.
   - "quantity": numeric quantity, as a number (not a string). If the unit
     is bandwidth (e.g. 2400 GB), use the numeric value.
   - "unit_price": price per unit as a number. If the line shows a per-GB
     rate (e.g. £0.05/GB), use that rate, not the line total.

Return ONLY the JSON object — no markdown fences, no explanation.
"""


_GROQ_MAX_RETRIES = 3
_GROQ_RETRY_DELAY = 6.0   # seconds; Groq free-tier resets in ~1 min, 6s is enough


def _groq_call(client: Groq, model: str, prompt: str) -> str:
    """Call the Groq chat API with automatic retry on rate-limit (429).

    Retries up to :data:`_GROQ_MAX_RETRIES` times with a fixed
    :data:`_GROQ_RETRY_DELAY`-second pause before each retry.

    Args:
        client: Authenticated :class:`~groq.Groq` client.
        model: Model name to use (e.g. ``"llama-3.3-70b-versatile"``).
        prompt: User-role prompt string.

    Returns:
        The model's response text, stripped of leading/trailing whitespace.

    Raises:
        groq.RateLimitError: If all retries are exhausted.
        groq.APIError: For non-rate-limit API failures.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _GROQ_MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            return (response.choices[0].message.content or "").strip()
        except _GroqRateLimitError as exc:
            last_exc = exc
            if attempt < _GROQ_MAX_RETRIES:
                time.sleep(_GROQ_RETRY_DELAY * attempt)
    raise last_exc  # type: ignore[misc]


def _extract_json(text: str) -> dict:
    """Slice the first JSON object out of a model response.

    Args:
        text: Raw model response string (may contain prose around the JSON).

    Returns:
        Parsed dict.

    Raises:
        ValueError: If no valid JSON object is found.
    """
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON object found in model response: {text[:200]}")
    return json.loads(text[start:end])


def run_agent_b(
    topic_id: str,
    job_id: str,
    invoice_text: str,
    worker_account_id: str,
    bounty_tinybars: int = 10_000_000,
) -> tuple[Submission, str, ClaimServer]:
    """Run Agent B's invoice extraction and start its claim endpoint.

    Calls the Groq API with a heavily engineered prompt, builds a
    :class:`~taskforge.models.Submission` (with the worker's Hedera account ID
    embedded for anti-spoofing), logs it to HCS, then starts the claim server.

    Args:
        topic_id: HCS topic to log the submission to.
        job_id: Job ID from the broadcaster's posted :class:`~taskforge.models.Job`.
        invoice_text: Raw invoice text to extract from.
        worker_account_id: Agent B's Hedera account ID (pre-registered in the
            submission for anti-spoofing per §4a).
        bounty_tinybars: Payment amount in tinybars (default 0.1 HBAR).

    Returns:
        A tuple of ``(Submission, hcs_tx_id, claim_server)`` where
        ``claim_server`` is already started and listening on port
        :data:`_CLAIM_PORT`.  The caller is responsible for calling
        ``claim_server.stop()`` when done.
    """
    load_dotenv()
    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    prompt = _PROMPT_TEMPLATE.format(invoice_text=invoice_text)
    raw = _groq_call(client, _MODEL, prompt)
    output_payload = _extract_json(raw)

    submission = Submission(
        job_id=job_id,
        agent_id=_AGENT_ID,
        output_payload={
            **output_payload,
            # Pre-register account ID before scoring — anti-spoofing §4a
            "_worker_account_id": worker_account_id,
        },
        submitted_ts=time.time(),
    )
    hcs_tx = submit_message(topic_id, to_json(submission))

    claim_server = ClaimServer(
        worker_account_id=worker_account_id,
        job_id=job_id,
        amount_tinybars=bounty_tinybars,
        deliverable={"agent_id": _AGENT_ID, "extraction": output_payload},
        facilitator_url=_BLOCKY402_URL,
        port=_CLAIM_PORT,
    )
    claim_server.start()

    return submission, hcs_tx, claim_server

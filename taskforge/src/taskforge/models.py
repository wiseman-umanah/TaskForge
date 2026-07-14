"""
TaskForge domain models.

Every dataclass in this module represents one auditable event in a TaskForge
run. Each instance **must** be serialised with :func:`to_json` and submitted to
HCS via :func:`~taskforge.ledger.hcs_client.submit_message` immediately after
creation — the HCS topic is the sole persistence layer; nothing is stored
anywhere else.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass
class Job:
    """A task posted by the broadcaster to the HCS topic.

    Attributes:
        job_id: Unique identifier for this job (e.g. a UUID or slug).
        description: Human-readable description of the task.
        output_schema: Expected JSON shape of a valid worker submission.
        bounty_amount: Reward in HBAR paid to the winning worker.
        currency: Currency denomination — always ``"HBAR"`` for this project.
        deadline_ts: UNIX timestamp after which submissions are no longer accepted.
    """

    job_id: str
    description: str
    output_schema: dict
    bounty_amount: float
    currency: str
    deadline_ts: float


@dataclass
class Submission:
    """A worker's response to a posted job.

    Attributes:
        job_id: The job this submission is responding to.
        agent_id: Identifier of the submitting agent — ``"agent_a"`` or ``"agent_b"``.
        output_payload: The extracted JSON data produced by the worker.
        submitted_ts: UNIX timestamp when the submission was created.
    """

    job_id: str
    agent_id: str
    output_payload: dict
    submitted_ts: float


@dataclass
class VerdictLog:
    """The verifier's scored assessment of a single submission.

    Attributes:
        job_id: The job being assessed.
        agent_id: The agent whose submission was scored.
        score: Normalised score in ``[0.0, 1.0]``.  Higher is better.
        passed: ``True`` if the submission passed the schema check and ground-truth
            threshold; ``False`` for automatic rejects.
        reason: Human-readable explanation of the score or rejection.
        ts: UNIX timestamp when the verdict was produced.
    """

    job_id: str
    agent_id: str
    score: float
    passed: bool
    reason: str
    ts: float


@dataclass
class PaymentRecord:
    """Receipt of a settled (or explicitly rejected) x402 payment.

    Attributes:
        job_id: The job for which payment was settled.
        winner_agent_id: Agent that received the bounty.
        tx_hash: Hedera transaction ID string (``"0.0.account@seconds.nanos"``)
            used to construct the HashScan link.
        amount: Amount paid in HBAR.
        hcs_message_id: Transaction ID of the HCS message that recorded this record.
    """

    job_id: str
    winner_agent_id: str
    tx_hash: str
    amount: float
    hcs_message_id: str


def to_json(obj: Job | Submission | VerdictLog | PaymentRecord) -> str:
    """Serialise a TaskForge dataclass to a JSON string for HCS submission.

    A ``"_type"`` key is injected into the payload so downstream consumers can
    identify the event type without inspecting message structure.

    Args:
        obj: Any TaskForge dataclass instance.

    Returns:
        A compact JSON string ready to pass to
        :func:`~taskforge.ledger.hcs_client.submit_message`.

    Example:
        >>> job = Job(job_id="j1", description="...", output_schema={},
        ...           bounty_amount=0.1, currency="HBAR", deadline_ts=0.0)
        >>> to_json(job)
        '{"job_id": "j1", ..., "_type": "Job"}'
    """
    payload = asdict(obj)
    payload["_type"] = type(obj).__name__
    return json.dumps(payload)

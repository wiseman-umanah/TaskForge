"""TaskForge Coordinator — FastAPI application.

This is the central server for TaskForge v2.  It exposes the open
marketplace API: agent registration (with x402 entry-fee gate), task
generation, submission intake, and a leaderboard.

Endpoints
---------
GET  /health                    — liveness + topic ID
POST /agents/register           — pay 0.01 HBAR entry fee, register agent
GET  /agents                    — list registered agents
DELETE /agents/{agent_id}       — deregister an agent
POST /tasks/generate            — generate and broadcast a new task
GET  /tasks                     — list open (un-settled) tasks
GET  /tasks/{job_id}            — single task + submissions received
POST /submit                    — agent posts answer to an open task
GET  /leaderboard               — win counts and avg scores per agent
GET  /audit/{job_id}            — replay HCS messages for a job

Run via ``taskforge.cli.run_server`` — do not start directly.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from taskforge.broadcaster.broadcast_job import GROUND_TRUTH, INVOICE_TEXT, post_job
from taskforge.coordinator.gate import EntryFeeGate, ENTRY_FEE_TINYBARS
from taskforge.coordinator.registry import AgentRegistry
from taskforge.coordinator.scheduler import Scheduler
from taskforge.ledger.hcs_client import poll_topic, submit_message
from taskforge.models import AgentRegistration, Job, Submission, to_json

load_dotenv()

# ── Application state ─────────────────────────────────────────────────────────

@dataclass
class CoordinatorState:
    """All mutable runtime state for the coordinator.

    Attributes:
        topic_id: HCS topic created at startup.
        jobs: Open jobs keyed by job_id.
        task_specs: Ground-truth specs keyed by job_id (not exposed via API).
        submissions: Pending submissions keyed by job_id.
        settled_jobs: Set of job_ids already scored and paid.
        registry: Registered agent registry.
        scores: Cumulative scores keyed by agent_id for leaderboard.
    """

    topic_id: str
    jobs: dict[str, Job] = field(default_factory=dict)
    task_specs: dict[str, dict] = field(default_factory=dict)
    submissions: dict[str, list[Submission]] = field(default_factory=dict)
    settled_jobs: set[str] = field(default_factory=set)
    registry: AgentRegistry = field(init=False)
    scores: dict[str, list[float]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def __post_init__(self) -> None:
        """Initialise the registry with the topic_id."""
        self.registry = AgentRegistry(topic_id=self.topic_id)


# Module-level singletons — populated by create_app()
_state: CoordinatorState | None = None
_gate: EntryFeeGate | None = None
_scheduler: Scheduler | None = None


# ── Pydantic request/response models ─────────────────────────────────────────

class RegisterRequest(BaseModel):
    """Body for ``POST /agents/register``."""

    agent_id: str
    account_id: str
    claim_url: str
    capabilities: list[str] = ["invoice_extraction"]


class SubmitRequest(BaseModel):
    """Body for ``POST /submit``."""

    job_id: str
    agent_id: str
    output_payload: dict[str, Any]


# ── Factory ───────────────────────────────────────────────────────────────────

def create_app(topic_id: str, operator_id: str, operator_key: str) -> FastAPI:
    """Create and configure the FastAPI application.

    Initialises shared state, the entry-fee gate, and the deadline scheduler.
    Replays HCS messages to rebuild agent registry on a restart.

    Args:
        topic_id: HCS topic created at startup (printed on boot).
        operator_id: Broadcaster Hedera account ID.
        operator_key: Broadcaster ECDSA private key hex.

    Returns:
        A fully configured :class:`~fastapi.FastAPI` instance.
    """
    global _state, _gate, _scheduler

    _state = CoordinatorState(topic_id=topic_id)
    _gate = EntryFeeGate(coordinator_account_id=operator_id)

    # Replay any existing HCS messages (restart recovery)
    try:
        messages = poll_topic(topic_id, since_ts=0.0)
        _state.registry.load_from_hcs(messages)
        print(f"  [coordinator] replayed {len(messages)} HCS messages on boot")
    except Exception as exc:  # noqa: BLE001
        print(f"  [coordinator] HCS replay skipped: {exc}")

    _scheduler = Scheduler(
        state=_state,
        topic_id=topic_id,
        operator_id=operator_id,
        operator_key=operator_key,
    )
    _scheduler.start()
    print("  [coordinator] scheduler started (poll every 10s)")

    app = FastAPI(
        title="TaskForge Coordinator",
        description=(
            "Competitive agent task marketplace with Hedera HCS audit trail "
            "and x402 micropayment settlement.  "
            "Agents pay a 0.01 HBAR entry fee to register, then compete for "
            "0.1 HBAR bounties."
        ),
        version="2.0.0",
    )

    # ── Routes ────────────────────────────────────────────────────────────────

    @app.get("/health")
    def health() -> dict:
        """Return coordinator liveness and current topic ID."""
        assert _state is not None
        return {
            "status": "ok",
            "topic_id": _state.topic_id,
            "hashscan": f"https://hashscan.io/testnet/topic/{_state.topic_id}",
            "registered_agents": len(_state.registry),
            "open_tasks": len(_state.jobs) - len(_state.settled_jobs),
        }

    # ── Agent registration (x402 gated) ──────────────────────────────────────

    @app.post("/agents/register", status_code=201)
    def register_agent(
        body: RegisterRequest,
        request: Request,
        entry_fee_tx: str = Depends(_gate.require_payment),
    ) -> dict:
        """Register an agent after paying the 0.01 HBAR entry fee.

        On first call (no ``PAYMENT-SIGNATURE`` header) returns **402** with
        the payment requirements.  After paying, retry with the
        ``PAYMENT-SIGNATURE`` header to complete registration.

        The entry fee is settled on Hedera testnet via blocky402.  The
        transaction ID is stored in the ``AgentRegistration`` HCS message as
        proof of legitimate participation.
        """
        assert _state is not None
        reg = AgentRegistration(
            agent_id=body.agent_id,
            account_id=body.account_id,
            claim_url=body.claim_url,
            entry_fee_tx=entry_fee_tx,
            registered_ts=time.time(),
            capabilities=body.capabilities,
        )
        hcs_tx = _state.registry.add(reg)
        print(
            f"  [register] ✓ {body.agent_id} registered "
            f"(fee tx: {entry_fee_tx})  HCS: {hcs_tx}"
        )
        return {
            "registered": True,
            "agent_id": body.agent_id,
            "entry_fee_tx": entry_fee_tx,
            "entry_fee_tinybars": ENTRY_FEE_TINYBARS,
            "hcs_tx": hcs_tx,
            "hashscan_fee": f"https://hashscan.io/testnet/transaction/{entry_fee_tx}",
            "hashscan_hcs": f"https://hashscan.io/testnet/transaction/{hcs_tx}",
        }

    @app.get("/agents")
    def list_agents() -> list[dict]:
        """List all registered agents and their win counts."""
        assert _state is not None
        wins = _state.registry.wins()
        return [
            {
                "agent_id": r.agent_id,
                "account_id": r.account_id,
                "claim_url": r.claim_url,
                "capabilities": r.capabilities,
                "registered_ts": r.registered_ts,
                "wins": wins.get(r.agent_id, 0),
                "entry_fee_tx": r.entry_fee_tx,
            }
            for r in _state.registry.list()
        ]

    @app.delete("/agents/{agent_id}", status_code=200)
    def deregister_agent(agent_id: str) -> dict:
        """Deregister an agent by ID."""
        assert _state is not None
        removed = _state.registry.remove(agent_id)
        if not removed:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        return {"deregistered": agent_id}

    # ── Tasks ─────────────────────────────────────────────────────────────────

    @app.post("/tasks/generate", status_code=201)
    def generate_task() -> dict:
        """Generate and broadcast a new invoice-extraction task.

        Uses the hardcoded invoice fixture from ``broadcast_job.py``.
        Dynamic LLM-generated tasks are a post-hackathon upgrade.
        """
        assert _state is not None
        job, hcs_tx = post_job(_state.topic_id)
        task_spec = {"ground_truth": GROUND_TRUTH, "invoice_text": INVOICE_TEXT}

        with _state._lock:
            _state.jobs[job.job_id] = job
            _state.task_specs[job.job_id] = task_spec
            _state.submissions[job.job_id] = []

        print(f"  [task] generated job {job.job_id}  HCS: {hcs_tx}")
        return {
            "job_id": job.job_id,
            "description": job.description,
            "bounty_hbar": job.bounty_amount,
            "deadline_ts": job.deadline_ts,
            "hcs_tx": hcs_tx,
            "hashscan": f"https://hashscan.io/testnet/transaction/{hcs_tx}",
        }

    @app.get("/tasks")
    def list_tasks() -> list[dict]:
        """List all open (unsettled) tasks."""
        assert _state is not None
        now = time.time()
        return [
            {
                "job_id": jid,
                "description": job.description,
                "bounty_hbar": job.bounty_amount,
                "deadline_ts": job.deadline_ts,
                "seconds_remaining": max(0.0, job.deadline_ts - now),
                "submissions_received": len(_state.submissions.get(jid, [])),
                "settled": jid in _state.settled_jobs,
            }
            for jid, job in _state.jobs.items()
        ]

    @app.get("/tasks/{job_id}")
    def get_task(job_id: str) -> dict:
        """Return a single task and its received submissions (without payloads)."""
        assert _state is not None
        job = _state.jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        subs = _state.submissions.get(job_id, [])
        return {
            "job_id": job.job_id,
            "description": job.description,
            "bounty_hbar": job.bounty_amount,
            "deadline_ts": job.deadline_ts,
            "settled": job_id in _state.settled_jobs,
            "submissions": [
                {"agent_id": s.agent_id, "submitted_ts": s.submitted_ts}
                for s in subs
            ],
        }

    # ── Submission intake ─────────────────────────────────────────────────────

    @app.post("/submit", status_code=202)
    def submit(body: SubmitRequest) -> dict:
        """Accept an agent's answer to an open task.

        Validates that:
        - The job exists and is not yet settled.
        - The agent is registered.
        - The agent hasn't already submitted for this job.

        The submission is logged to HCS immediately on receipt.
        Scoring happens when the deadline expires (see scheduler).
        """
        assert _state is not None
        job = _state.jobs.get(body.job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job '{body.job_id}' not found")
        if body.job_id in _state.settled_jobs:
            raise HTTPException(
                status_code=409, detail=f"Job '{body.job_id}' already settled"
            )
        if time.time() > job.deadline_ts:
            raise HTTPException(
                status_code=410, detail=f"Job '{body.job_id}' deadline has passed"
            )

        reg = _state.registry.get(body.agent_id)
        if not reg:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Agent '{body.agent_id}' is not registered.  "
                    "POST /agents/register (entry fee: 0.01 HBAR) to participate."
                ),
            )

        # Prevent double-submission
        existing = _state.submissions.get(body.job_id, [])
        if any(s.agent_id == body.agent_id for s in existing):
            raise HTTPException(
                status_code=409,
                detail=f"Agent '{body.agent_id}' already submitted for job '{body.job_id}'",
            )

        # Inject anti-spoofing account field
        payload = {**body.output_payload, "_worker_account_id": reg.account_id}
        sub = Submission(
            job_id=body.job_id,
            agent_id=body.agent_id,
            output_payload=payload,
            submitted_ts=time.time(),
        )
        hcs_tx = submit_message(_state.topic_id, to_json(sub))

        with _state._lock:
            _state.submissions.setdefault(body.job_id, []).append(sub)

        print(f"  [submit] {body.agent_id} → job {body.job_id}  HCS: {hcs_tx}")
        return {
            "accepted": True,
            "job_id": body.job_id,
            "agent_id": body.agent_id,
            "hcs_tx": hcs_tx,
        }

    # ── Leaderboard ───────────────────────────────────────────────────────────

    @app.get("/leaderboard")
    def leaderboard() -> list[dict]:
        """Return agents ranked by win count, then average score."""
        assert _state is not None
        wins = _state.registry.wins()
        rows = []
        for reg in _state.registry.list():
            score_history = _state.scores.get(reg.agent_id, [])
            avg_score = sum(score_history) / len(score_history) if score_history else 0.0
            rows.append(
                {
                    "rank": 0,
                    "agent_id": reg.agent_id,
                    "wins": wins.get(reg.agent_id, 0),
                    "avg_score": round(avg_score, 3),
                    "submissions": len(score_history),
                }
            )
        rows.sort(key=lambda r: (-r["wins"], -r["avg_score"]))
        for i, row in enumerate(rows, 1):
            row["rank"] = i
        return rows

    # ── Audit ─────────────────────────────────────────────────────────────────

    @app.get("/audit/{job_id}")
    def audit(job_id: str) -> dict:
        """Return the full HCS message chain for a job (replayed from Mirror Node)."""
        assert _state is not None
        try:
            messages = poll_topic(_state.topic_id, since_ts=0.0)
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Mirror Node unavailable: {exc}"
            ) from exc
        relevant = [m for m in messages if m.get("job_id") == job_id]
        return {
            "job_id": job_id,
            "topic_id": _state.topic_id,
            "message_count": len(relevant),
            "messages": relevant,
        }

    return app

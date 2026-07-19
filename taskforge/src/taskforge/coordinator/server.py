"""TaskForge Coordinator — FastAPI application.

This is the central server for TaskForge v2.  It exposes the open
marketplace API: agent registration (with x402 entry-fee gate), task
generation, submission intake, and a leaderboard.

HCS topic model
---------------
- One **platform topic** is created at startup.  Agent registrations are
  written there so the registry survives restarts via HCS replay.
- One **per-task topic** is created when each new job is generated.  Every
  event that belongs to that competition (enrollment, submission, verdict,
  payment) is written to that topic — not the platform topic.  This means
  anyone with a ``job_id`` can open a single HashScan URL and read the full
  story from start to finish with no filtering.

Endpoints
---------
GET  /health                      — liveness + platform topic ID
POST /agents/register             — pay 0.01 HBAR entry fee, register agent globally
GET  /agents                      — list registered agents
DELETE /agents/{agent_id}         — deregister an agent
POST /tasks/generate              — generate and broadcast a new task (creates new HCS topic)
GET  /tasks                       — list tasks with per-task topic links
GET  /tasks/{job_id}              — single task + submissions received + topic link
POST /tasks/{job_id}/enroll       — pay entry fee and enroll in a specific task
GET  /tasks/{job_id}/enrollments  — list enrolled agents for a task
POST /submit                      — agent posts answer to an open task
GET  /leaderboard                 — win counts and avg scores per agent
GET  /audit/{job_id}              — replay HCS messages for a job from its own topic
GET  /hcs                         — platform topic + all per-task topics with hashscan links

Run via ``taskforge.cli.run_server`` — do not start directly.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import select

from taskforge.broadcaster.broadcast_job import pick_task, post_job
from taskforge.coordinator.gate import EntryFeeGate, ENTRY_FEE_TINYBARS
from taskforge.coordinator.registry import AgentRegistry
from taskforge.coordinator.scheduler import Scheduler
from taskforge.db import (
    AgentRow, EnrollmentRow, PaymentRow, SubmissionRow, TaskRow, VerdictRow,
    get_session, init_db, using_persistent_db,
)
from taskforge.ledger.hcs_client import create_topic, poll_topic, submit_message
from taskforge.models import AgentRegistration, Job, Submission, TaskEnrollment, to_json

load_dotenv()

# ── Application state ─────────────────────────────────────────────────────────

@dataclass
class CoordinatorState:
    """All mutable runtime state for the coordinator.

    Attributes:
        topic_id: Platform-level HCS topic (agent registrations).
        job_topics: Per-task HCS topics keyed by job_id.
        jobs: Open jobs keyed by job_id.
        task_specs: Ground-truth specs keyed by job_id (not exposed via API).
        submissions: Pending submissions keyed by job_id.
        settled_jobs: Set of job_ids already scored and paid.
        enrollments: Per-task enrollments — dict[job_id, dict[agent_id, TaskEnrollment]].
        registry: Registered agent registry.
        scores: Cumulative scores keyed by agent_id for leaderboard.
    """

    topic_id: str                   # platform topic — agent registrations
    job_topics: dict[str, str] = field(default_factory=dict)   # job_id → task topic_id
    jobs: dict[str, Job] = field(default_factory=dict)
    task_specs: dict[str, dict] = field(default_factory=dict)
    submissions: dict[str, list[Submission]] = field(default_factory=dict)
    settled_jobs: set[str] = field(default_factory=set)
    enrollments: dict[str, dict[str, TaskEnrollment]] = field(default_factory=dict)
    registry: AgentRegistry = field(init=False)
    scores: dict[str, list[float]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def __post_init__(self) -> None:
        """Initialise the registry with the platform topic_id."""
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


class EnrollRequest(BaseModel):
    """Body for ``POST /tasks/{job_id}/enroll``.

    The agent identifies itself and provides the claim URL where the
    coordinator will send the bounty payment if this agent wins.
    """

    agent_id: str
    claim_url: str


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

    # Initialise DB (creates tables if needed; idempotent)
    init_db()
    if using_persistent_db():
        print("  [coordinator] persistent DB active")

    _state = CoordinatorState(topic_id=topic_id)
    _gate = EntryFeeGate(coordinator_account_id=operator_id)

    # ── Load persisted state from DB (crash recovery) ─────────────────────
    _load_state_from_db(_state)

    # Replay any existing HCS messages to rebuild registry (restart recovery)
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

    # ── CORS — allow browser requests from any origin (frontend on GitHub Pages,
    #    localhost dev server, or direct file:// open).  Restrict origins in
    #    production by replacing "*" with your GitHub Pages URL.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["PAYMENT-REQUIRED", "PAYMENT-RESPONSE"],
    )

    # ── CORS safety-net ───────────────────────────────────────────────────────
    # Starlette's CORSMiddleware does not inject headers on HTTPException
    # responses raised inside route handlers (the exception handler runs inside
    # the middleware's own scope but bypasses the header-injection path).  This
    # raw middleware runs *outside* CORSMiddleware and unconditionally stamps
    # Access-Control-Allow-Origin on every response, including 402/404/422/500.
    @app.middleware("http")
    async def _cors_catchall(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Expose-Headers"] = (
            "PAYMENT-REQUIRED, PAYMENT-RESPONSE"
        )
        return response

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

        # ── Duplicate checks (after payment so they can't be used to probe
        #    the registry for free — the fee is charged first) ──────────────
        if _state.registry.get(body.agent_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Agent ID '{body.agent_id}' is already registered. "
                    "Choose a different ID."
                ),
            )
        existing_acct = _state.registry.get_by_account(body.account_id)
        if existing_acct:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Hedera account '{body.account_id}' is already registered "
                    f"under agent ID '{existing_acct.agent_id}'. "
                    "One account per agent."
                ),
            )

        reg = AgentRegistration(
            agent_id=body.agent_id,
            account_id=body.account_id,
            claim_url=body.claim_url,
            entry_fee_tx=entry_fee_tx,
            registered_ts=time.time(),
            capabilities=body.capabilities,
        )
        hcs_tx = _state.registry.add(reg)
        if using_persistent_db():
            with get_session() as s:
                s.merge(AgentRow(
                    agent_id=reg.agent_id,
                    account_id=reg.account_id,
                    claim_url=reg.claim_url,
                    entry_fee_tx=reg.entry_fee_tx,
                    registered_ts=reg.registered_ts,
                    capabilities=",".join(reg.capabilities),
                ))
                s.commit()
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

        Creates a fresh HCS topic for this task, posts the Job to it, and
        registers both in shared state.  All subsequent events for this task
        (enrollments, submissions, verdicts, payment) go to that same topic.
        """
        assert _state is not None
        task = pick_task()
        task_topic_id = create_topic(memo="taskforge-task")
        job, hcs_tx = post_job(task_topic_id, task=task)
        task_spec = {"ground_truth": task["ground_truth"], "invoice_text": task["invoice_text"]}

        with _state._lock:
            _state.job_topics[job.job_id] = task_topic_id
            _state.jobs[job.job_id] = job
            _state.task_specs[job.job_id] = task_spec
            _state.submissions[job.job_id] = []

        if using_persistent_db():
            with get_session() as s:
                s.merge(TaskRow(
                    job_id=job.job_id,
                    topic_id=task_topic_id,
                    description=job.description,
                    bounty_hbar=job.bounty_amount,
                    deadline_ts=job.deadline_ts,
                    settled=False,
                ))
                s.commit()

        print(
            f"  [task] generated job {job.job_id}  "
            f"task-topic={task_topic_id}  HCS: {hcs_tx}"
        )
        return {
            "job_id": job.job_id,
            "description": job.description,
            "bounty_hbar": job.bounty_amount,
            "deadline_ts": job.deadline_ts,
            "topic_id": task_topic_id,
            "hcs_tx": hcs_tx,
            "hashscan_topic": f"https://hashscan.io/testnet/topic/{task_topic_id}",
            "hashscan_tx": f"https://hashscan.io/testnet/transaction/{hcs_tx}",
        }

    @app.get("/tasks")
    def list_tasks() -> list[dict]:
        """List all tasks with enrollment counts and per-task HCS topic links."""
        assert _state is not None
        now = time.time()
        return [
            {
                "job_id": jid,
                "description": job.description,
                "invoice_text": (_state.task_specs.get(jid) or {}).get("invoice_text", ""),
                "bounty_hbar": job.bounty_amount,
                "deadline_ts": job.deadline_ts,
                "seconds_remaining": max(0.0, job.deadline_ts - now),
                "submissions_received": len(_state.submissions.get(jid, [])),
                "enrolled_agents": len(_state.enrollments.get(jid, {})),
                "settled": jid in _state.settled_jobs,
                "topic_id": _state.job_topics.get(jid, ""),
                "hashscan_topic": (
                    f"https://hashscan.io/testnet/topic/{_state.job_topics[jid]}"
                    if jid in _state.job_topics else ""
                ),
            }
            for jid, job in _state.jobs.items()
        ]

    @app.get("/tasks/{job_id}")
    def get_task(job_id: str) -> dict:
        """Return a single task, its enrollments, submitted agents, and HCS topic link."""
        assert _state is not None
        job = _state.jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        subs = _state.submissions.get(job_id, [])
        enrolled = list(_state.enrollments.get(job_id, {}).keys())
        task_topic = _state.job_topics.get(job_id, "")
        spec = _state.task_specs.get(job_id) or {}
        return {
            "job_id": job.job_id,
            "description": job.description,
            "invoice_text": spec.get("invoice_text", ""),
            "bounty_hbar": job.bounty_amount,
            "deadline_ts": job.deadline_ts,
            "settled": job_id in _state.settled_jobs,
            "topic_id": task_topic,
            "hashscan_topic": (
                f"https://hashscan.io/testnet/topic/{task_topic}" if task_topic else ""
            ),
            "enrolled_agents": enrolled,
            "submissions": [
                {"agent_id": s.agent_id, "submitted_ts": s.submitted_ts}
                for s in subs
            ],
        }

    @app.post("/tasks/{job_id}/enroll", status_code=201)
    def enroll_in_task(
        job_id: str,
        body: EnrollRequest,
        request: Request,
        entry_fee_tx: str = Depends(_gate.require_payment),
    ) -> dict:
        """Enroll in a specific task after paying the entry fee.

        The agent must already be globally registered via ``POST /agents/register``.
        This call binds the agent to this task and registers the claim URL where
        the coordinator will send the bounty payment if this agent wins.

        On first call (no ``PAYMENT-SIGNATURE`` header) returns **402**.
        After paying, retry with the ``PAYMENT-SIGNATURE`` header.
        """
        assert _state is not None

        # Task must exist and be open
        job = _state.jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        if job_id in _state.settled_jobs:
            raise HTTPException(status_code=409, detail=f"Job '{job_id}' is already settled")
        if time.time() > job.deadline_ts:
            raise HTTPException(status_code=410, detail=f"Job '{job_id}' deadline has passed")

        # Agent must be globally registered
        reg = _state.registry.get(body.agent_id)
        if not reg:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Agent '{body.agent_id}' is not registered. "
                    "Call POST /agents/register first."
                ),
            )

        # Prevent double-enrollment
        if body.agent_id in _state.enrollments.get(job_id, {}):
            raise HTTPException(
                status_code=409,
                detail=f"Agent '{body.agent_id}' is already enrolled in job '{job_id}'",
            )

        enrollment = TaskEnrollment(
            job_id=job_id,
            agent_id=body.agent_id,
            account_id=reg.account_id,
            claim_url=body.claim_url,
            entry_fee_tx=entry_fee_tx,
            enrolled_ts=time.time(),
        )
        # Enrollment goes to the task's own topic
        task_topic = _state.job_topics.get(job_id, _state.topic_id)
        hcs_tx = submit_message(task_topic, to_json(enrollment))

        with _state._lock:
            _state.enrollments.setdefault(job_id, {})[body.agent_id] = enrollment

        if using_persistent_db():
            with get_session() as s:
                s.add(EnrollmentRow(
                    job_id=enrollment.job_id,
                    agent_id=enrollment.agent_id,
                    account_id=enrollment.account_id,
                    claim_url=enrollment.claim_url,
                    entry_fee_tx=enrollment.entry_fee_tx,
                    enrolled_ts=enrollment.enrolled_ts,
                ))
                s.commit()

        print(f"  [enroll] ✓ {body.agent_id} enrolled in {job_id}  HCS: {hcs_tx}")
        return {
            "enrolled": True,
            "job_id": job_id,
            "agent_id": body.agent_id,
            "account_id": reg.account_id,
            "claim_url": body.claim_url,
            "entry_fee_tx": entry_fee_tx,
            "hcs_tx": hcs_tx,
            "hashscan_fee": f"https://hashscan.io/testnet/transaction/{entry_fee_tx}",
            "hashscan_hcs": f"https://hashscan.io/testnet/transaction/{hcs_tx}",
        }

    @app.get("/tasks/{job_id}/enrollments")
    def list_enrollments(job_id: str) -> list[dict]:
        """List agents enrolled in a specific task."""
        assert _state is not None
        if job_id not in _state.jobs:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        return [
            {
                "agent_id": e.agent_id,
                "account_id": e.account_id,
                "claim_url": e.claim_url,
                "enrolled_ts": e.enrolled_ts,
            }
            for e in _state.enrollments.get(job_id, {}).values()
        ]

    # ── Submission intake ─────────────────────────────────────────────────────

    @app.post("/submit", status_code=202)
    def submit(body: SubmitRequest) -> dict:
        """Accept an agent's answer to an open task.

        Validates that:
        - The job exists and is not yet settled.
        - The agent is globally registered.
        - The agent is enrolled in this specific task.
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
                    f"Agent '{body.agent_id}' is not registered. "
                    "POST /agents/register first, then POST /tasks/{job_id}/enroll."
                ),
            )

        # Must be enrolled in this specific task
        enrollment = _state.enrollments.get(body.job_id, {}).get(body.agent_id)
        if not enrollment:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Agent '{body.agent_id}' is not enrolled in job '{body.job_id}'. "
                    f"POST /tasks/{body.job_id}/enroll first."
                ),
            )

        # Prevent double-submission
        existing = _state.submissions.get(body.job_id, [])
        if any(s.agent_id == body.agent_id for s in existing):
            raise HTTPException(
                status_code=409,
                detail=f"Agent '{body.agent_id}' already submitted for job '{body.job_id}'",
            )

        # Use enrollment's claim_url and account_id for anti-spoofing
        payload = {**body.output_payload, "_worker_account_id": enrollment.account_id}
        sub = Submission(
            job_id=body.job_id,
            agent_id=body.agent_id,
            output_payload=payload,
            submitted_ts=time.time(),
        )
        # Submission goes to the task's own topic
        task_topic = _state.job_topics.get(body.job_id, _state.topic_id)
        hcs_tx = submit_message(task_topic, to_json(sub))

        with _state._lock:
            _state.submissions.setdefault(body.job_id, []).append(sub)

        if using_persistent_db():
            with get_session() as s:
                s.add(SubmissionRow(
                    job_id=sub.job_id,
                    agent_id=sub.agent_id,
                    output_json=json.dumps(sub.output_payload),
                    submitted_ts=sub.submitted_ts,
                ))
                s.commit()

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
        """Return the full HCS message chain for a job from its dedicated topic."""
        assert _state is not None
        task_topic = _state.job_topics.get(job_id)
        if not task_topic:
            raise HTTPException(status_code=404, detail=f"No HCS topic found for job '{job_id}'")
        try:
            messages = poll_topic(task_topic, since_ts=0.0)
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Mirror Node unavailable: {exc}"
            ) from exc
        return {
            "job_id": job_id,
            "topic_id": task_topic,
            "hashscan_topic": f"https://hashscan.io/testnet/topic/{task_topic}",
            "message_count": len(messages),
            "messages": messages,
        }

    # ── HCS overview ──────────────────────────────────────────────────────────

    @app.get("/hcs")
    def hcs_overview() -> dict:
        """Return the platform topic and all per-task topics with HashScan links.

        Used by the Ledger UI page to surface the full on-chain audit trail.
        """
        assert _state is not None
        state = _state   # local alias so lambdas below are not confused by the module-level Optional
        task_entries = []
        for job_id, task_topic in state.job_topics.items():
            job = state.jobs.get(job_id)
            task_entries.append({
                "job_id": job_id,
                "topic_id": task_topic,
                "hashscan_topic": f"https://hashscan.io/testnet/topic/{task_topic}",
                "settled": job_id in state.settled_jobs,
                "bounty_hbar": job.bounty_amount if job else 0.1,
                "submissions_received": len(state.submissions.get(job_id, [])),
                "enrolled_agents": len(state.enrollments.get(job_id, {})),
            })
        # newest tasks first — close over the local alias so Pyright can narrow the type
        task_entries.sort(
            key=lambda e: list(state.job_topics.keys()).index(e["job_id"]),
            reverse=True,
        )
        return {
            "platform_topic": {
                "topic_id": state.topic_id,
                "hashscan_topic": f"https://hashscan.io/testnet/topic/{state.topic_id}",
                "label": "Platform (agent registrations)",
            },
            "task_topics": task_entries,
            "total_tasks": len(task_entries),
        }

    return app


# ── DB helpers ────────────────────────────────────────────────────────────────

def _load_state_from_db(state: CoordinatorState) -> None:
    """Reload persisted state into CoordinatorState on startup.

    Only runs when ``DATABASE_URL`` is set to a real DB (not in-memory).
    Loads agents, tasks, enrollments, and submissions so the coordinator
    can resume after a crash without losing all context.

    Args:
        state: The freshly-created :class:`CoordinatorState` to populate.
    """
    if not using_persistent_db():
        return

    with get_session() as s:
        # Agents
        for row in s.exec(select(AgentRow)).all():
            caps = [c.strip() for c in row.capabilities.split(",") if c.strip()]
            reg = AgentRegistration(
                agent_id=row.agent_id,
                account_id=row.account_id,
                claim_url=row.claim_url,
                entry_fee_tx=row.entry_fee_tx,
                registered_ts=row.registered_ts,
                capabilities=caps,
            )
            state.registry._agents[reg.agent_id] = reg

        # Tasks
        for row in s.exec(select(TaskRow)).all():
            job = Job(
                job_id=row.job_id,
                description=row.description,
                output_schema={},
                bounty_amount=row.bounty_hbar,
                currency="HBAR",
                deadline_ts=row.deadline_ts,
            )
            state.jobs[row.job_id] = job
            state.job_topics[row.job_id] = row.topic_id
            state.submissions.setdefault(row.job_id, [])
            state.task_specs.setdefault(row.job_id, {
                "ground_truth": GROUND_TRUTH,
                "invoice_text": INVOICE_TEXT,
            })
            if row.settled:
                state.settled_jobs.add(row.job_id)

        # Enrollments
        for row in s.exec(select(EnrollmentRow)).all():
            from taskforge.models import TaskEnrollment as _TE
            enr = _TE(
                job_id=row.job_id,
                agent_id=row.agent_id,
                account_id=row.account_id,
                claim_url=row.claim_url,
                entry_fee_tx=row.entry_fee_tx,
                enrolled_ts=row.enrolled_ts,
            )
            state.enrollments.setdefault(row.job_id, {})[row.agent_id] = enr

        # Submissions
        for row in s.exec(select(SubmissionRow)).all():
            sub = Submission(
                job_id=row.job_id,
                agent_id=row.agent_id,
                output_payload=json.loads(row.output_json),
                submitted_ts=row.submitted_ts,
            )
            state.submissions.setdefault(row.job_id, []).append(sub)

    print(
        f"  [coordinator] loaded from DB: "
        f"{len(state.registry._agents)} agents, "
        f"{len(state.jobs)} tasks, "
        f"{sum(len(v) for v in state.submissions.values())} submissions"
    )

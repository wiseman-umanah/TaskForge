"""Uvicorn-compatible application entry point for TaskForge v2.

Exposes a module-level ``app`` object so the server can be started with
the standard uvicorn CLI::

    # From inside the taskforge/ directory:
    uvicorn taskforge.coordinator.app:app --port 8400

Or via the uv module runner::

    uv run python -m taskforge.cli.run_server

``--reload`` note: hot-reload re-imports this module on file changes,
which would create a new HCS topic each time.  Use ``--reload`` only
when developing route handlers; omit it for real runs.
"""
from __future__ import annotations

import os
import time

from dotenv import load_dotenv
from fastapi import FastAPI

from taskforge.broadcaster.broadcast_job import GROUND_TRUTH, INVOICE_TEXT, post_job
from taskforge.coordinator.server import CoordinatorState, create_app
from taskforge.coordinator import server as _srv
from taskforge.ledger.hcs_client import create_topic
from taskforge.models import AgentRegistration
from taskforge.workers.agent_a import _CLAIM_PORT as _PORT_A, run_agent_a
from taskforge.workers.agent_b import _CLAIM_PORT as _PORT_B, run_agent_b

# ── ANSI colours ──────────────────────────────────────────────────────────────
_G = "\033[32m"
_B = "\033[1m"
_D = "\033[2m"
_X = "\033[0m"

_HASHSCAN_TOPIC = "https://hashscan.io/testnet/topic/{}"
_HASHSCAN_TX    = "https://hashscan.io/testnet/transaction/{}"
_BOUNTY_TINYBARS = 10_000_000   # 0.1 HBAR


def _bootstrap() -> FastAPI:
    """Run the full startup sequence and return the configured FastAPI app.

    Called exactly once at module import time.  Performs:

    1. Creates a fresh HCS topic.
    2. Builds the FastAPI coordinator app (gate + scheduler).
    3. Pre-registers the two built-in agents (no entry fee).
    4. Generates the first invoice-extraction task.

    Returns:
        The fully initialised :class:`~fastapi.FastAPI` application.
    """
    load_dotenv()

    operator_id: str = os.environ["OPERATOR_ID"]
    operator_key: str = os.environ["OPERATOR_KEY"]
    worker_a_id: str = os.environ["WORKER_A_ACCOUNT_ID"]
    worker_b_id: str = os.environ["WORKER_B_ACCOUNT_ID"]

    print(f"\n{_B}{'='*64}{_X}")
    print(f"{_B}  TaskForge v2 — Coordinator Server{_X}")
    print(f"{_B}{'='*64}{_X}\n")

    # ── Step 1: HCS topic ─────────────────────────────────────────────────────
    print(f"{_B}[1/4] Creating HCS topic{_X}")
    topic_id = create_topic(memo="taskforge-v2-server")
    print(f"  {_G}✓{_X} Topic ID : {topic_id}")
    print(f"  {_G}✓{_X} HashScan : {_HASHSCAN_TOPIC.format(topic_id)}")

    # ── Step 2: Build FastAPI app ─────────────────────────────────────────────
    print(f"\n{_B}[2/4] Initialising coordinator{_X}")
    fastapi_app = create_app(
        topic_id=topic_id,
        operator_id=operator_id,
        operator_key=operator_key,
    )
    state: CoordinatorState = _srv._state  # type: ignore[attr-defined]
    print(f"  {_G}✓{_X} FastAPI app ready")
    print(f"  {_G}✓{_X} Entry fee gate: 0.01 HBAR → {operator_id}")

    # ── Step 3: Pre-register built-in agents ─────────────────────────────────
    print(f"\n{_B}[3/4] Pre-registering built-in agents{_X}")
    _, _, _server_a = run_agent_a(
        topic_id=topic_id,
        job_id="__warmup__",
        invoice_text=INVOICE_TEXT,
        worker_account_id=worker_a_id,
        bounty_tinybars=_BOUNTY_TINYBARS,
    )
    _, _, _server_b = run_agent_b(
        topic_id=topic_id,
        job_id="__warmup__",
        invoice_text=INVOICE_TEXT,
        worker_account_id=worker_b_id,
        bounty_tinybars=_BOUNTY_TINYBARS,
    )
    for agent_id, account_id, port in [
        ("agent_a", worker_a_id, _PORT_A),
        ("agent_b", worker_b_id, _PORT_B),
    ]:
        reg = AgentRegistration(
            agent_id=agent_id,
            account_id=account_id,
            claim_url=f"http://127.0.0.1:{port}/claim",
            entry_fee_tx="",
            registered_ts=time.time(),
        )
        hcs_tx = state.registry.add(reg)
        print(f"  {_G}✓{_X} Registered {agent_id} (port {port})  HCS: {hcs_tx}")

    # ── Step 4: Generate first task ───────────────────────────────────────────
    print(f"\n{_B}[4/4] Generating first invoice-extraction task{_X}")
    job, job_hcs_tx = post_job(topic_id)
    state.jobs[job.job_id] = job
    state.task_specs[job.job_id] = {"ground_truth": GROUND_TRUTH, "invoice_text": INVOICE_TEXT}
    state.submissions[job.job_id] = []
    print(f"  {_G}✓{_X} Job ID   : {job.job_id}")
    print(f"  {_G}✓{_X} Bounty   : {job.bounty_amount} HBAR  (deadline in 10 min)")
    print(f"  {_G}✓{_X} HashScan : {_HASHSCAN_TX.format(job_hcs_tx)}")

    # ── Ready ─────────────────────────────────────────────────────────────────
    print(f"\n{_B}{'='*64}{_X}")
    print(f"{_B}  Coordinator ready{_X}")
    print(f"  {_G}API base   :{_X} http://0.0.0.0:8400")
    print(f"  {_G}Docs       :{_X} http://0.0.0.0:8400/docs")
    print(f"  {_G}HCS topic  :{_X} {_HASHSCAN_TOPIC.format(topic_id)}")
    print(f"\n  {_D}External agents register at:{_X}")
    print(f"  {_D}  POST http://localhost:8400/agents/register{_X}")
    print(f"  {_D}  (pays 0.01 HBAR entry fee, then POST /submit for tasks){_X}")
    print(f"{_B}{'='*64}{_X}\n")

    return fastapi_app


# Module-level app object — uvicorn finds this when you pass
# "taskforge.coordinator.app:app" on the command line.
app: FastAPI = _bootstrap()

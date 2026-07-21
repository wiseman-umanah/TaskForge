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

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

from taskforge.broadcaster.broadcast_job import pick_task, post_job
from taskforge.coordinator.server import CoordinatorState, create_app
from taskforge.coordinator import server as _srv
from taskforge.ledger.hcs_client import create_topic

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
    3. Generates the first invoice-extraction task.

    Returns:
        The fully initialised :class:`~fastapi.FastAPI` application.
    """
    load_dotenv()

    operator_id: str = os.environ["OPERATOR_ID"]
    operator_key: str = os.environ["OPERATOR_KEY"]

    print(f"\n{_B}{'='*64}{_X}")
    print(f"{_B}  TaskForge v2 — Coordinator Server{_X}")
    print(f"{_B}{'='*64}{_X}\n")

    # ── Step 1: Platform HCS topic ────────────────────────────────────────────
    print(f"{_B}[1/3] Creating platform HCS topic (agent registrations){_X}")
    platform_topic_id = create_topic(memo="taskforge-v2-platform")
    print(f"  {_G}✓{_X} Platform topic : {platform_topic_id}")
    print(f"  {_G}✓{_X} HashScan       : {_HASHSCAN_TOPIC.format(platform_topic_id)}")

    # ── Step 2: Build FastAPI app ─────────────────────────────────────────────
    print(f"\n{_B}[2/3] Initialising coordinator{_X}")
    fastapi_app = create_app(
        topic_id=platform_topic_id,
        operator_id=operator_id,
        operator_key=operator_key,
    )
    state: CoordinatorState = _srv._state  # type: ignore[attr-defined]
    print(f"  {_G}✓{_X} FastAPI app ready")
    print(f"  {_G}✓{_X} Entry fee gate: 0.01 HBAR → {operator_id}")

    # ── Step 3: Generate first task (gets its own HCS topic) ─────────────────
    print(f"\n{_B}[3/3] Generating first invoice-extraction task{_X}")
    from taskforge.ledger.hcs_client import create_topic as _ct
    first_task = pick_task()
    task_topic_id = _ct(memo="taskforge-task")
    job, job_hcs_tx = post_job(task_topic_id, task=first_task)
    state.job_topics[job.job_id] = task_topic_id
    state.jobs[job.job_id] = job
    state.task_specs[job.job_id] = {
        "ground_truth": first_task["ground_truth"],
        "invoice_text": first_task["invoice_text"],
    }
    state.submissions[job.job_id] = []
    print(f"  {_G}✓{_X} Job ID      : {job.job_id}")
    print(f"  {_G}✓{_X} Task topic  : {task_topic_id}")
    print(f"  {_G}✓{_X} Bounty      : {job.bounty_amount} HBAR  (deadline in 10 min)")
    print(f"  {_G}✓{_X} HashScan TX : {_HASHSCAN_TX.format(job_hcs_tx)}")
    print(f"  {_G}✓{_X} HashScan    : {_HASHSCAN_TOPIC.format(task_topic_id)}")

    # ── Ready ─────────────────────────────────────────────────────────────────
    print(f"\n{_B}{'='*64}{_X}")
    print(f"{_B}  Coordinator ready{_X}")
    print(f"  {_G}API base        :{_X} http://0.0.0.0:8400")
    print(f"  {_G}Docs            :{_X} http://0.0.0.0:8400/docs")
    print(f"  {_G}Platform topic  :{_X} {_HASHSCAN_TOPIC.format(platform_topic_id)}")
    print(f"  {_G}Task topic      :{_X} {_HASHSCAN_TOPIC.format(task_topic_id)}")
    print(f"\n  {_D}Register agents at http://localhost:5173/register{_X}")
    print(f"  {_D}  (pays 0.01 HBAR entry fee via x402){_X}")
    print(f"{_B}{'='*64}{_X}\n")

    return fastapi_app


# Module-level app object — uvicorn finds this when you pass
# "taskforge.coordinator.app:app" on the command line.
app: FastAPI = _bootstrap()


def main() -> None:
    """CLI entry point: run the coordinator under uvicorn.

    Invoked by the ``taskforge-server`` console script defined in
    ``pyproject.toml``.  The ``app`` object is already fully initialised at
    module-import time by :func:`_bootstrap`, so uvicorn simply needs to serve
    it.
    """
    port = int(os.environ.get("PORT", "8400"))
    uvicorn.run(app, host="0.0.0.0", port=port)

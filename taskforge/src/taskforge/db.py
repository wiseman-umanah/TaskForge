"""TaskForge database layer — opt-in persistence via DATABASE_URL.

**Default behaviour (no DATABASE_URL set):** all DB functions are no-ops.
The coordinator runs exactly as before — pure in-memory dict state.
No SQLite file is created, no tables are touched.

**With DATABASE_URL set:** coordinator state is persisted to the configured
database (SQLite file path or Postgres DSN).  Tables are created automatically
on startup (``init_db()``).  On restart the coordinator reloads agents, tasks,
enrollments and submissions from DB so nothing is lost across process restarts.

Examples::

    # File-based SQLite (persists across restarts)
    DATABASE_URL=sqlite:///./taskforge.db

    # Postgres
    DATABASE_URL=postgresql+psycopg2://user:pass@localhost/taskforge

When ``DATABASE_URL`` is absent the module still imports cleanly — SQLModel
is imported lazily only when a real URL is present so the import never fails.

Usage::

    from taskforge.db import get_session, init_db, using_persistent_db

    init_db()                       # idempotent; no-op if no DATABASE_URL
    if using_persistent_db():
        with get_session() as s:
            s.add(AgentRow(...))
            s.commit()
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, ClassVar, Generator

from dotenv import load_dotenv

load_dotenv()

# ── Feature flag ──────────────────────────────────────────────────────────────

_DATABASE_URL: str | None = os.getenv("DATABASE_URL") or None


def using_persistent_db() -> bool:
    """Return True only when DATABASE_URL is explicitly set in the environment.

    Returns:
        ``True`` when a real database URL is configured; ``False`` otherwise.
    """
    return _DATABASE_URL is not None


# ── Engine (created only when DATABASE_URL is set) ────────────────────────────

_engine = None

if _DATABASE_URL is not None:
    from sqlalchemy.pool import StaticPool
    from sqlmodel import create_engine as _create_engine

    _is_file_sqlite = _DATABASE_URL.startswith("sqlite") and ":memory:" not in _DATABASE_URL

    if _is_file_sqlite:
        _engine = _create_engine(
            _DATABASE_URL,
            connect_args={"check_same_thread": False},
        )
    else:
        # Postgres / MySQL / other
        _engine = _create_engine(_DATABASE_URL)


# ── Tables (defined regardless so imports don't fail, but table=True only
#    registers metadata — no DB connection is made at import time) ────────────

from sqlmodel import Field, Session, SQLModel  # noqa: E402  (always safe to import)


class AgentRow(SQLModel, table=True):
    """Persisted agent registration record.

    Attributes:
        agent_id: Primary key — unique agent identifier.
        account_id: Hedera account that receives bounties.
        claim_url: Agent's x402 claim endpoint base URL.
        entry_fee_tx: On-chain entry-fee settlement tx ID.
        registered_ts: UNIX timestamp of registration.
        capabilities: Comma-separated task-type list.
    """

    __tablename__: ClassVar[str] = "agents"  # type: ignore[assignment]

    agent_id:      str   = Field(primary_key=True)
    account_id:    str
    claim_url:     str
    entry_fee_tx:  str   = Field(default="")
    registered_ts: float = Field(default=0.0)
    capabilities:  str   = Field(default="invoice_extraction")  # comma-separated


class TaskRow(SQLModel, table=True):
    """Persisted task/job record.

    Attributes:
        job_id: Primary key — unique job identifier.
        topic_id: Per-task HCS topic ID.
        description: Task description shown to agents.
        bounty_hbar: Bounty in HBAR.
        deadline_ts: UNIX deadline timestamp.
        settled: True once the job has been scored and paid.
    """

    __tablename__: ClassVar[str] = "tasks"  # type: ignore[assignment]

    job_id:      str   = Field(primary_key=True)
    topic_id:    str   = Field(default="")
    description: str
    bounty_hbar: float = Field(default=0.1)
    deadline_ts: float
    settled:     bool  = Field(default=False)


class EnrollmentRow(SQLModel, table=True):
    """Persisted per-task agent enrollment.

    Attributes:
        id: Auto-generated surrogate PK.
        job_id: Task being enrolled in.
        agent_id: Enrolling agent.
        account_id: Hedera account for payment.
        claim_url: Per-task claim URL.
        entry_fee_tx: On-chain enrollment fee tx.
        enrolled_ts: UNIX timestamp.
    """

    __tablename__: ClassVar[str] = "enrollments"  # type: ignore[assignment]

    id:           int | None = Field(default=None, primary_key=True)
    job_id:       str
    agent_id:     str
    account_id:   str
    claim_url:    str
    entry_fee_tx: str   = Field(default="")
    enrolled_ts:  float = Field(default=0.0)


class SubmissionRow(SQLModel, table=True):
    """Persisted agent submission.

    Attributes:
        id: Auto-generated surrogate PK.
        job_id: Job the submission targets.
        agent_id: Submitting agent.
        output_json: JSON-serialised ``output_payload`` dict.
        submitted_ts: UNIX timestamp.
    """

    __tablename__: ClassVar[str] = "submissions"  # type: ignore[assignment]

    id:           int | None = Field(default=None, primary_key=True)
    job_id:       str
    agent_id:     str
    output_json:  str   = Field(default="{}")
    submitted_ts: float = Field(default=0.0)


class VerdictRow(SQLModel, table=True):
    """Persisted verifier verdict.

    Attributes:
        id: Auto-generated surrogate PK.
        job_id: Scored job.
        agent_id: Scored agent.
        score: Final score in [0, 1].
        passed: Whether the agent passed the threshold.
        reason: Human-readable scoring explanation.
        ts: UNIX timestamp.
    """

    __tablename__: ClassVar[str] = "verdicts"  # type: ignore[assignment]

    id:       int | None = Field(default=None, primary_key=True)
    job_id:   str
    agent_id: str
    score:    float = Field(default=0.0)
    passed:   bool  = Field(default=False)
    reason:   str   = Field(default="")
    ts:       float = Field(default=0.0)


class PaymentRow(SQLModel, table=True):
    """Persisted payment record.

    Attributes:
        id: Auto-generated surrogate PK.
        job_id: Paid job.
        winner_agent_id: Agent that received the bounty.
        tx_hash: Hedera tx ID of the settlement.
        amount_hbar: Amount in HBAR.
        recorded_ts: UNIX timestamp.
    """

    __tablename__: ClassVar[str] = "payments"  # type: ignore[assignment]

    id:              int | None = Field(default=None, primary_key=True)
    job_id:          str
    winner_agent_id: str
    tx_hash:         str   = Field(default="")
    amount_hbar:     float = Field(default=0.0)
    recorded_ts:     float = Field(default=0.0)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create all tables if DATABASE_URL is set. No-op otherwise.

    Safe to call on every startup — ``CREATE TABLE IF NOT EXISTS`` semantics.
    """
    if _engine is None:
        return
    SQLModel.metadata.create_all(_engine)
    print(f"  [db] tables ready  ({_DATABASE_URL})")


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Yield a database session.

    Raises:
        RuntimeError: If called when no DATABASE_URL is configured.
            Always guard with ``using_persistent_db()`` before calling.
    """
    if _engine is None:
        raise RuntimeError(
            "get_session() called but DATABASE_URL is not set. "
            "Guard DB writes with: if using_persistent_db(): ..."
        )
    with Session(_engine) as session:
        yield session

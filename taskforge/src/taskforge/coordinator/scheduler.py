"""Deadline watcher — scores and pays out when a job's deadline expires.

A single background :class:`threading.Thread` wakes every
:data:`_POLL_INTERVAL` seconds, scans all open jobs, and fires the
scoring + payment pipeline for any job whose ``deadline_ts`` has passed
and that still has at least one pending submission.

The coordinator creates one :class:`Scheduler` instance at startup and
calls :meth:`Scheduler.start`.  The scheduler runs as a daemon thread and
stops automatically when the process exits.

Usage::

    from taskforge.coordinator.scheduler import Scheduler
    sched = Scheduler(state=app_state, topic_id="0.0.5678")
    sched.start()
    sched.stop()   # optional graceful stop
"""
from __future__ import annotations

import time
import threading
import urllib.request
from typing import TYPE_CHECKING

from x402 import x402ClientSync
from x402.http.x402_http_client import x402HTTPClientSync
from x402.http.constants import PAYMENT_RESPONSE_HEADER
from x402.http.utils import decode_payment_response_header

from taskforge.hedera_x402 import ExactHederaSchemeClient
from taskforge.ledger.hcs_client import submit_message
from taskforge.models import PaymentRecord, to_json
from taskforge.verifier.extraction_verifier import ExtractionVerifier

if TYPE_CHECKING:
    from taskforge.coordinator.server import CoordinatorState

_POLL_INTERVAL = 10.0   # seconds between deadline checks
_HTTP_TIMEOUT = 20      # seconds for x402 HTTP round-trip
_BOUNTY_HBAR = 0.1


class Scheduler:
    """Background deadline watcher and payment dispatcher.

    Runs one daemon thread that polls :attr:`state` every
    :data:`_POLL_INTERVAL` seconds.  When a job's deadline has passed
    it scores all submissions, pays the winner via x402, and logs
    verdicts + payment to HCS.

    Attributes:
        state: Shared :class:`~taskforge.coordinator.server.CoordinatorState`.
        topic_id: HCS topic used for logging verdicts and payments.
        operator_id: Broadcaster Hedera account ID.
        operator_key: Broadcaster ECDSA private key hex.
    """

    def __init__(
        self,
        state: CoordinatorState,
        topic_id: str,
        operator_id: str,
        operator_key: str,
    ) -> None:
        """Create a :class:`Scheduler`.

        Args:
            state: Shared application state (jobs, submissions, registry).
            topic_id: HCS topic ID for event logging.
            operator_id: Broadcaster's Hedera account ID.
            operator_key: Broadcaster's ECDSA private key hex.
        """
        self.state = state
        self.topic_id = topic_id
        self.operator_id = operator_id
        self.operator_key = operator_key
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the scheduler daemon thread."""
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="taskforge-scheduler"
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the scheduler to stop and wait for it to exit."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=15)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        """Poll for expired jobs until stopped."""
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001
                print(f"  [scheduler] unhandled error: {exc}")
            self._stop_event.wait(timeout=_POLL_INTERVAL)

    def _tick(self) -> None:
        """Check all open jobs; fire scoring for any that have expired."""
        now = time.time()
        expired = [
            jid
            for jid, job in list(self.state.jobs.items())
            if job.deadline_ts <= now and jid not in self.state.settled_jobs
        ]
        for job_id in expired:
            self._settle_job(job_id)

    # ── Settlement ────────────────────────────────────────────────────────────

    def _settle_job(self, job_id: str) -> None:
        """Score all submissions for *job_id* and pay the winner.

        Args:
            job_id: Job to settle.
        """
        # Mark as settled immediately so concurrent ticks don't double-fire
        self.state.settled_jobs.add(job_id)

        job = self.state.jobs.get(job_id)
        task_spec = self.state.task_specs.get(job_id)
        submissions = self.state.submissions.get(job_id, [])

        if not job or not task_spec or not submissions:
            print(f"  [scheduler] job {job_id}: no submissions — skipping payment")
            return

        print(f"\n  [scheduler] settling job {job_id} ({len(submissions)} submissions)")

        verifier = ExtractionVerifier()
        verdicts = []
        for sub in submissions:
            verdict = verifier.verify(task_spec, sub)
            verdicts.append((verdict, sub))
            submit_message(self.topic_id, to_json(verdict))
            status = "✓" if verdict.passed else "✗"
            print(f"    {status} {sub.agent_id}: score={verdict.score:.3f}")

        # Sort by score descending; keep only passed
        passed = [(v, s) for v, s in verdicts if v.passed]
        passed.sort(key=lambda t: t[0].score, reverse=True)

        if not passed:
            print(f"    No passing submissions for job {job_id} — no payment")
            _log_no_winner(self.topic_id, job_id)
            return

        # Check for tie
        if len(passed) >= 2 and abs(passed[0][0].score - passed[1][0].score) < 0.001:
            print(
                f"    ⚡ Tie-break: {passed[0][0].agent_id} wins by listing order "
                f"(score={passed[0][0].score:.3f})"
            )

        winner_verdict, winner_sub = passed[0]
        winner_agent_id = winner_verdict.agent_id
        winner_reg = self.state.registry.get(winner_agent_id)

        if not winner_reg:
            print(f"    [scheduler] winner {winner_agent_id} not in registry — cannot pay")
            _log_no_winner(self.topic_id, job_id)
            return

        print(f"    Winner: {winner_agent_id} — initiating x402 payment")
        self._pay_winner(
            job_id=job_id,
            claim_url=winner_reg.claim_url,
            winner_agent_id=winner_agent_id,
            pre_logged_acct=winner_sub.output_payload.get("_worker_account_id", ""),
            expected_acct=winner_reg.account_id,
        )

        # Log rejections for losers
        for v, _s in passed[1:]:
            _log_rejection(self.topic_id, job_id, v.agent_id, v.score)
        for v, _s in verdicts:
            if v.agent_id not in {w[0].agent_id for w in passed}:
                _log_rejection(self.topic_id, job_id, v.agent_id, v.score)

        # Update win count
        self.state.registry.record_win(winner_agent_id)

    def _pay_winner(
        self,
        job_id: str,
        claim_url: str,
        winner_agent_id: str,
        pre_logged_acct: str,
        expected_acct: str,
    ) -> None:
        """Execute the full x402 round-trip to pay a winner.

        Probes the winner's claim endpoint, performs anti-spoofing check,
        signs the payment, retries, and logs the ``PaymentRecord`` to HCS.

        Args:
            job_id: The job being paid out.
            claim_url: Winner's x402 claim URL.
            winner_agent_id: Agent identifier for logging.
            pre_logged_acct: Account pre-registered in the submission payload.
            expected_acct: Account from the registry (registered at entry time).
        """
        from x402.http.utils import decode_payment_required_header
        from x402.http.constants import PAYMENT_REQUIRED_HEADER

        # ── Probe ──────────────────────────────────────────────────────────
        status, headers, body = _http_get(claim_url)
        if status != 402:
            print(f"    [pay] expected 402 from {winner_agent_id}, got {status} — aborting")
            return
        print(f"    [pay] received 402 from {winner_agent_id}")

        # ── Anti-spoofing ──────────────────────────────────────────────────
        pr_raw = headers.get(PAYMENT_REQUIRED_HEADER) or headers.get(
            PAYMENT_REQUIRED_HEADER.lower(), ""
        )
        if pr_raw:
            pr = decode_payment_required_header(pr_raw)
            challenged_acct = pr.accepts[0].pay_to if pr.accepts else ""
            if challenged_acct not in (pre_logged_acct, expected_acct):
                print(
                    f"    [pay] ANTI-SPOOFING: 402 pay_to={challenged_acct!r} "
                    f"not in registered accounts — aborting"
                )
                return
            print(f"    [pay] anti-spoofing OK ({challenged_acct})")

        # ── Sign + retry ───────────────────────────────────────────────────
        hedera_scheme = ExactHederaSchemeClient(
            operator_id=self.operator_id,
            operator_key_hex=self.operator_key,
        )
        x402_c = x402ClientSync()
        x402_c.register("hedera:testnet", hedera_scheme)
        http_c = x402HTTPClientSync(x402_c)

        payment_headers, _ = http_c.handle_402_response(
            headers={k: v for k, v in headers.items()},
            body=body,
        )

        status2, headers2, body2 = _http_get(claim_url, extra_headers=payment_headers)
        if status2 != 200:
            print(f"    [pay] expected 200 after payment, got {status2}: {body2[:120]!r}")
            return

        # ── Extract tx ID ──────────────────────────────────────────────────
        pr_resp_raw = headers2.get(PAYMENT_RESPONSE_HEADER) or headers2.get(
            PAYMENT_RESPONSE_HEADER.lower(), ""
        )
        tx_id = "pending"
        if pr_resp_raw:
            settle = decode_payment_response_header(pr_resp_raw)
            tx_id = settle.transaction or "pending"

        print(f"    [pay] ✓ settled! TX: {tx_id}")
        print(f"    [pay] HashScan: https://hashscan.io/testnet/transaction/{tx_id}")

        payment = PaymentRecord(
            job_id=job_id,
            winner_agent_id=winner_agent_id,
            tx_hash=tx_id,
            amount=_BOUNTY_HBAR,
            hcs_message_id="",
        )
        submit_message(self.topic_id, to_json(payment))


# ── Module-level helpers ───────────────────────────────────────────────────────

def _http_get(
    url: str,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Perform a GET and return (status, headers, body).

    Args:
        url: Target URL.
        extra_headers: Optional additional request headers.

    Returns:
        Tuple of ``(status_code, response_headers, body_bytes)``.
    """
    req = urllib.request.Request(url, method="GET")
    for name, value in (extra_headers or {}).items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read() or b""


def _log_no_winner(topic_id: str, job_id: str) -> None:
    """Log a PaymentRecord with winner='none' when no submissions pass.

    Args:
        topic_id: HCS topic.
        job_id: Job that expired without a winner.
    """
    submit_message(
        topic_id,
        to_json(
            PaymentRecord(
                job_id=job_id,
                winner_agent_id="none",
                tx_hash="expired",
                amount=0.0,
                hcs_message_id="",
            )
        ),
    )


def _log_rejection(
    topic_id: str, job_id: str, agent_id: str, score: float
) -> None:
    """Log a rejection PaymentRecord for a losing agent.

    Args:
        topic_id: HCS topic.
        job_id: Job ID.
        agent_id: Losing agent.
        score: Their score (for the record).
    """
    submit_message(
        topic_id,
        to_json(
            PaymentRecord(
                job_id=job_id,
                winner_agent_id="none",
                tx_hash=f"rejected:{agent_id}:score={score:.3f}",
                amount=0.0,
                hcs_message_id="",
            )
        ),
    )

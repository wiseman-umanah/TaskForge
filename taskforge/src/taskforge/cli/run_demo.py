"""TaskForge full demo — orchestrates the complete competitive agent flow.

Run from inside the ``taskforge/`` directory::

    uv run python -m taskforge.cli.run_demo

What it does (printed step-by-step):

1. Creates a fresh HCS topic (one per run) — prints HashScan link.
2. Posts the invoice-extraction job to HCS.
3. Runs Agent A (baseline prompt) — extracts invoice fields, logs to HCS.
4. Runs Agent B (engineered prompt) — extracts invoice fields, logs to HCS.
5. Scores both submissions with the ExtractionVerifier (schema → GT → LLM judge).
6. Logs both VerdictLogs to HCS.
7. Determines winner (higher score); anti-spoofing cross-check.
8. **Pay path**: broadcasts GET to winner's claim endpoint → receives 402 →
   pays via blocky402 → receives 200 → logs PaymentRecord to HCS → HashScan link.
9. **Reject path**: logs loser's rejection to HCS with reason and score.
10. Prints final summary.

Prerequisites: ``taskforge/.env`` with ``OPERATOR_ID``, ``OPERATOR_KEY``,
``WORKER_A_ACCOUNT_ID``, ``WORKER_B_ACCOUNT_ID``, ``GROQ_API_KEY``.
"""
from __future__ import annotations

import os
import time
import urllib.request

from dotenv import load_dotenv

from x402 import x402ClientSync
from x402.http.x402_http_client import x402HTTPClientSync
from x402.http.constants import PAYMENT_RESPONSE_HEADER
from x402.http.utils import decode_payment_response_header

from taskforge.broadcaster.broadcast_job import (
    GROUND_TRUTH,
    INVOICE_TEXT,
    post_job,
)
from taskforge.hedera_x402 import ExactHederaSchemeClient
from taskforge.ledger.hcs_client import create_topic, submit_message
from taskforge.models import PaymentRecord, VerdictLog, to_json
from taskforge.verifier.extraction_verifier import ExtractionVerifier
from taskforge.workers.agent_a import _CLAIM_PORT as PORT_A, run_agent_a
from taskforge.workers.agent_b import _CLAIM_PORT as PORT_B, run_agent_b

# ── ANSI colours ──────────────────────────────────────────────────────────────
_G = "\033[32m"   # green
_R = "\033[31m"   # red
_Y = "\033[33m"   # yellow
_D = "\033[2m"    # dim
_B = "\033[1m"    # bold
_X = "\033[0m"    # reset

HASHSCAN_TOPIC = "https://hashscan.io/testnet/topic/{}"
HASHSCAN_TX = "https://hashscan.io/testnet/transaction/{}"
BLOCKY402_URL = "https://api.testnet.blocky402.com"
BOUNTY_TINYBARS = 10_000_000   # 0.1 HBAR
BOUNTY_HBAR = 0.1


def _hdr(step: int, total: int, label: str) -> None:
    """Print a bold step header."""
    print(f"\n{_B}[STEP {step}/{total}] {label}{_X}")


def _ok(msg: str) -> None:
    print(f"  {_G}✓{_X} {msg}")


def _fail(msg: str) -> None:
    print(f"  {_R}✗{_X} {msg}")


def _dim(msg: str) -> None:
    print(f"  {_D}{msg}{_X}")


_HTTP_TIMEOUT = 20   # seconds — prevents silent hangs on network hiccups


def _do_request(
    url: str,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Perform a GET request; return (status, headers, body).

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


def main() -> None:
    """Run the full TaskForge demo end-to-end."""
    load_dotenv()

    operator_id: str = os.environ["OPERATOR_ID"]
    operator_key: str = os.environ["OPERATOR_KEY"]
    worker_a_id: str = os.environ["WORKER_A_ACCOUNT_ID"]
    worker_b_id: str = os.environ["WORKER_B_ACCOUNT_ID"]

    total_steps = 10
    print(f"\n{_B}{'='*64}{_X}")
    print(f"{_B}  TaskForge — Competitive Agent Task Marketplace Demo{_X}")
    print(f"{_B}{'='*64}{_X}")

    # ── Step 1: Create HCS topic ─────────────────────────────────────────────
    _hdr(1, total_steps, "Creating HCS topic (new topic per run)")
    topic_id = create_topic(memo="taskforge-demo")
    _ok(f"Topic ID : {topic_id}")
    _ok(f"HashScan : {HASHSCAN_TOPIC.format(topic_id)}")

    run_start_ts = time.time()

    # ── Step 2: Post job ─────────────────────────────────────────────────────
    _hdr(2, total_steps, "Broadcasting invoice-extraction job")
    job, job_hcs_tx = post_job(topic_id)
    _ok(f"Job ID   : {job.job_id}")
    _ok(f"HCS TX   : {HASHSCAN_TX.format(job_hcs_tx)}")
    _dim(f"Bounty   : {job.bounty_amount} HBAR  |  Deadline in 10 min")

    task_spec = {"ground_truth": GROUND_TRUTH, "invoice_text": INVOICE_TEXT}

    server_a = server_b = None
    try:
        # ── Step 3: Run Agent A ──────────────────────────────────────────────
        _hdr(3, total_steps, "Running Agent A (baseline prompt)")
        sub_a, hcs_a, server_a = run_agent_a(
            topic_id=topic_id,
            job_id=job.job_id,
            invoice_text=INVOICE_TEXT,
            worker_account_id=worker_a_id,
            bounty_tinybars=BOUNTY_TINYBARS,
        )
        _ok(f"Submission logged — HCS TX: {HASHSCAN_TX.format(hcs_a)}")
        _ok(f"Claim server live on port {PORT_A}")
        _dim(f"worker_account_id pre-registered: {worker_a_id}")
        _print_extraction("A", sub_a.output_payload)

        # ── Step 4: Run Agent B ──────────────────────────────────────────────
        _hdr(4, total_steps, "Running Agent B (engineered prompt)")
        sub_b, hcs_b, server_b = run_agent_b(
            topic_id=topic_id,
            job_id=job.job_id,
            invoice_text=INVOICE_TEXT,
            worker_account_id=worker_b_id,
            bounty_tinybars=BOUNTY_TINYBARS,
        )
        _ok(f"Submission logged — HCS TX: {HASHSCAN_TX.format(hcs_b)}")
        _ok(f"Claim server live on port {PORT_B}")
        _dim(f"worker_account_id pre-registered: {worker_b_id}")
        _print_extraction("B", sub_b.output_payload)

        # ── Step 5: Score both ───────────────────────────────────────────────
        _hdr(5, total_steps, "Scoring submissions (schema → ground-truth → LLM judge)")
        verifier = ExtractionVerifier()
        verdict_a = verifier.verify(task_spec, sub_a)
        verdict_b = verifier.verify(task_spec, sub_b)

        _ok(f"Agent A  score={verdict_a.score:.3f}  passed={verdict_a.passed}")
        _dim(f"  reason: {verdict_a.reason}")
        _ok(f"Agent B  score={verdict_b.score:.3f}  passed={verdict_b.passed}")
        _dim(f"  reason: {verdict_b.reason}")

        # ── Step 6: Log verdicts to HCS ──────────────────────────────────────
        _hdr(6, total_steps, "Logging verdicts to HCS")
        hcs_va = submit_message(topic_id, to_json(verdict_a))
        hcs_vb = submit_message(topic_id, to_json(verdict_b))
        _ok(f"Verdict A  HCS TX: {HASHSCAN_TX.format(hcs_va)}")
        _ok(f"Verdict B  HCS TX: {HASHSCAN_TX.format(hcs_vb)}")

        # ── Step 7: Determine winner & anti-spoofing check ───────────────────
        _hdr(7, total_steps, "Determining winner + anti-spoofing check")

        # Pick highest-scoring passed submission; tie-break by score then agent order
        candidates = [(verdict_a, sub_a, server_a, worker_a_id, PORT_A),
                      (verdict_b, sub_b, server_b, worker_b_id, PORT_B)]
        passed = [t for t in candidates if t[0].passed]
        passed.sort(key=lambda t: t[0].score, reverse=True)

        if not passed:
            print(f"\n{_R}Both agents failed schema/ground-truth checks.{_X}")
            print("No payment made.  Logging rejections to HCS.")
            _log_rejection(topic_id, verdict_a, verdict_b, job.job_id)
            return

        # Warn clearly on a tie so judges understand the tiebreak rule
        if len(passed) >= 2 and abs(passed[0][0].score - passed[1][0].score) < 0.001:
            print(
                f"  {_Y}⚡ Tie-break: both passed with equal score "
                f"({passed[0][0].score:.3f}).  "
                f"{passed[0][0].agent_id} wins by convention.{_X}"
            )

        winner_verdict, winner_sub, winner_server, winner_acct_id, winner_port = passed[0]
        loser_verdict = verdict_b if winner_verdict.agent_id == "agent_a" else verdict_a

        # Anti-spoofing: cross-check the 402 account against the pre-logged one
        pre_logged_acct = winner_sub.output_payload.get("_worker_account_id", "")
        _ok(f"Winner   : {_G}{winner_verdict.agent_id}{_X} (score={winner_verdict.score:.3f})")
        _dim(f"Pre-logged account: {pre_logged_acct}")

        # ── Step 8: Pay winner via x402 ──────────────────────────────────────
        _hdr(8, total_steps, f"Paying winner ({winner_verdict.agent_id}) via x402 on Hedera testnet")

        claim_url = f"http://127.0.0.1:{winner_port}/claim/{job.job_id}"
        _dim(f"Claim URL: {claim_url}")

        # Probe — expect 402
        status, headers, body = _do_request(claim_url)
        if status != 402:
            anomaly = f"Expected 402 from winner, got {status}"
            _fail(anomaly)
            _log_anomaly(topic_id, job.job_id, winner_verdict.agent_id, anomaly)
            return
        _ok("Received 402 Payment Required")

        # Extract account from 402 and cross-check
        from x402.http.utils import decode_payment_required_header
        pr_raw = headers.get("PAYMENT-REQUIRED") or headers.get("payment-required", "")
        if pr_raw:
            pr = decode_payment_required_header(pr_raw)
            challenged_acct = pr.accepts[0].pay_to if pr.accepts else ""
            if challenged_acct != pre_logged_acct:
                anomaly = (
                    f"ANTI-SPOOFING: 402 pay_to={challenged_acct!r} "
                    f"≠ pre-logged={pre_logged_acct!r}"
                )
                _fail(anomaly)
                _log_anomaly(topic_id, job.job_id, winner_verdict.agent_id, anomaly)
                return
            _ok(f"Anti-spoofing check passed (account matches: {challenged_acct})")

        # Build payment payload and retry
        hedera_scheme = ExactHederaSchemeClient(
            operator_id=operator_id,
            operator_key_hex=operator_key,
        )
        x402_client = x402ClientSync()
        x402_client.register("hedera:testnet", hedera_scheme)
        http_client = x402HTTPClientSync(x402_client)

        payment_headers, _ = http_client.handle_402_response(
            headers={k: v for k, v in headers.items()},
            body=body,
        )
        _ok("Payment payload signed — retrying with PAYMENT-SIGNATURE")

        status2, headers2, body2 = _do_request(claim_url, extra_headers=payment_headers)
        if status2 != 200:
            _fail(f"Expected 200 after payment, got {status2}: {body2.decode()[:200]}")
            return
        _ok(f"{_G}200 OK — payment settled!{_X}")

        # Extract tx ID from response header
        pr_resp_raw = (
            headers2.get(PAYMENT_RESPONSE_HEADER)
            or headers2.get(PAYMENT_RESPONSE_HEADER.lower(), "")
        )
        tx_id = "unknown"
        if pr_resp_raw:
            settle = decode_payment_response_header(pr_resp_raw)
            tx_id = settle.transaction or "pending"

        _ok(f"TX ID    : {tx_id}")
        _ok(f"{_G}HashScan : {HASHSCAN_TX.format(tx_id)}{_X}")

        # Log PaymentRecord to HCS
        payment_record = PaymentRecord(
            job_id=job.job_id,
            winner_agent_id=winner_verdict.agent_id,
            tx_hash=tx_id,
            amount=BOUNTY_HBAR,
            hcs_message_id="",
        )
        hcs_pay = submit_message(topic_id, to_json(payment_record))
        _ok(f"PaymentRecord HCS TX: {HASHSCAN_TX.format(hcs_pay)}")

        # ── Step 9: Reject loser ─────────────────────────────────────────────
        _hdr(9, total_steps, f"Rejecting loser ({loser_verdict.agent_id})")
        _log_rejection_single(topic_id, loser_verdict, job.job_id)

        # ── Step 10: Summary ─────────────────────────────────────────────────
        _hdr(10, total_steps, "Demo complete — full run summary")
        print(f"\n  {_B}HCS Topic    :{_X} {HASHSCAN_TOPIC.format(topic_id)}")
        print(f"  {_B}Job posted   :{_X} {HASHSCAN_TX.format(job_hcs_tx)}")
        print(f"  {_G}Winner       : {winner_verdict.agent_id}  score={winner_verdict.score:.3f}{_X}")
        print(f"  {_R}Loser        : {loser_verdict.agent_id}  score={loser_verdict.score:.3f}{_X}")
        print(f"  {_G}Payment TX   : {HASHSCAN_TX.format(tx_id)}{_X}")
        print(f"\n  {_D}Run duration: {time.time() - run_start_ts:.1f}s{_X}")

    finally:
        if server_a is not None:
            server_a.stop()
        if server_b is not None:
            server_b.stop()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_extraction(agent_label: str, payload: dict) -> None:
    """Print a compact, readable summary of an agent's extracted fields.

    Strips the internal ``_worker_account_id`` anti-spoofing field before
    printing so the terminal output stays clean for the demo video.

    Args:
        agent_label: Short label used in the header (e.g. ``"A"`` or ``"B"``).
        payload: ``output_payload`` dict from the agent's
            :class:`~taskforge.models.Submission`.
    """
    display = {k: v for k, v in payload.items() if k != "_worker_account_id"}
    # Format line_items compactly: one item per line
    items = display.pop("line_items", [])
    scalar_str = "  ".join(f"{k}={v!r}" for k, v in display.items())
    items_str = "; ".join(
        f"{it.get('description', '?')} qty={it.get('quantity')} @{it.get('unit_price')}"
        for it in items
    )
    print(f"  \033[2m[Agent {agent_label}] {scalar_str}\033[0m")
    print(f"  \033[2m          items: {items_str}\033[0m")


def _log_rejection(
    topic_id: str,
    verdict_a: VerdictLog,
    verdict_b: VerdictLog,
    job_id: str,
) -> None:
    """Log rejection verdicts for both agents when neither passed.

    Args:
        topic_id: HCS topic ID.
        verdict_a: Agent A's verdict.
        verdict_b: Agent B's verdict.
        job_id: Job ID for the payment record.
    """
    reject_record = PaymentRecord(
        job_id=job_id,
        winner_agent_id="none",
        tx_hash="none",
        amount=0.0,
        hcs_message_id="",
    )
    hcs_tx = submit_message(topic_id, to_json(reject_record))
    _fail(f"Both failed — no payment made.  HCS TX: {HASHSCAN_TX.format(hcs_tx)}")
    _dim(f"Agent A reason: {verdict_a.reason}")
    _dim(f"Agent B reason: {verdict_b.reason}")


def _log_rejection_single(
    topic_id: str,
    verdict: VerdictLog,
    job_id: str,
) -> None:
    """Log a single loser's rejection to HCS and print it.

    Args:
        topic_id: HCS topic ID.
        verdict: Loser's verdict.
        job_id: Job ID.
    """
    reject_record = PaymentRecord(
        job_id=job_id,
        winner_agent_id="none",
        tx_hash="none",
        amount=0.0,
        hcs_message_id="",
    )
    hcs_tx = submit_message(topic_id, to_json(reject_record))
    _fail(
        f"{_R}{verdict.agent_id} rejected{_X} — score={verdict.score:.3f}  "
        f"passed={verdict.passed}"
    )
    _dim(f"  reason: {verdict.reason}")
    _ok(f"Rejection HCS TX: {HASHSCAN_TX.format(hcs_tx)}")


def _log_anomaly(
    topic_id: str,
    job_id: str,
    agent_id: str,
    reason: str,
) -> None:
    """Log an anti-spoofing anomaly to HCS as a PaymentRecord with 'anomaly' tx_hash.

    Args:
        topic_id: HCS topic ID.
        job_id: Job ID.
        agent_id: Agent that triggered the anomaly.
        reason: Description of the anomaly.
    """
    anomaly_record = PaymentRecord(
        job_id=job_id,
        winner_agent_id=agent_id,
        tx_hash="ANOMALY",
        amount=0.0,
        hcs_message_id=reason,
    )
    hcs_tx = submit_message(topic_id, to_json(anomaly_record))
    _fail(f"Anomaly logged — HCS TX: {HASHSCAN_TX.format(hcs_tx)}")


if __name__ == "__main__":
    main()

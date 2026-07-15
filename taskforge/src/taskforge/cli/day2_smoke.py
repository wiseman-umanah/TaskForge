"""Day 2 smoke test — full x402 402 → sign → 200 round-trip on Hedera testnet.

Demonstrates the complete x402 Hedera payment flow end-to-end:

1. Worker's :class:`~taskforge.settlement.claim_reward.ClaimServer` starts in
   a background thread on ``localhost:8402``.
2. Broadcaster GETs ``/claim/<job_id>`` → receives a real ``402`` with a
   ``PAYMENT-REQUIRED`` header.
3. Broadcaster uses :class:`~taskforge.hedera_x402.ExactHederaSchemeClient`
   to build a partially-signed Hedera Transfer Transaction.
4. The x402.org facilitator (``https://x402.org/facilitator``) completes and
   submits the transaction to Hedera testnet.
5. Broadcaster retries with ``PAYMENT-SIGNATURE`` header → receives ``200``
   with the deliverable and a ``PAYMENT-RESPONSE`` header.
6. Broadcaster extracts the Hedera tx ID and prints a HashScan link.

Prerequisites
-------------
A populated ``taskforge/.env`` file with:

- ``OPERATOR_ID``  — broadcaster ECDSA testnet account (funded).
- ``OPERATOR_KEY`` — ECDSA private key (raw hex, no ``0x`` prefix).
- ``WORKER_A_ACCOUNT_ID`` — worker testnet account (receive-only).

Usage
-----
Run from inside the ``taskforge/`` directory::

    uv run python -m taskforge.cli.day2_smoke

Day 2 gate
----------
The HashScan link printed at the end must resolve and show an HBAR transfer
from the broadcaster to the worker.  Do not proceed to Day 3 until confirmed.
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

from taskforge.hedera_x402 import ExactHederaSchemeClient
from taskforge.settlement.claim_reward import ClaimServer

FACILITATOR_URL: str = "https://api.testnet.blocky402.com"
HASHSCAN_TX: str = "https://hashscan.io/testnet/transaction/{}"
CLAIM_PORT: int = 8402
BOUNTY_TINYBARS: int = 10_000_000   # 0.1 HBAR
JOB_ID: str = "day2-smoke"


def _do_request(
    url: str,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Perform a GET request and return status, headers, and body.

    Args:
        url: Target URL.
        extra_headers: Additional request headers.

    Returns:
        Tuple of (status_code, response_headers_dict, body_bytes).
    """
    req = urllib.request.Request(url, method="GET")
    for name, value in (extra_headers or {}).items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read() or b""


def main() -> None:
    """Run the Day 2 x402 smoke test.

    Starts the worker claim server, runs the full broadcaster payment flow,
    prints the HashScan transaction link, and stops the server.

    Raises:
        KeyError: If required env vars are missing.
        RuntimeError: If the payment flow does not return 200 after signing.
    """
    load_dotenv()

    operator_id: str = os.environ["OPERATOR_ID"]
    operator_key: str = os.environ["OPERATOR_KEY"]
    worker_a_id: str = os.environ["WORKER_A_ACCOUNT_ID"]

    print("=" * 60)
    print("TaskForge — Day 2 x402 Payment Smoke Test")
    print("=" * 60)

    # ── Step 1: start the worker's claim server ───────────────────────────────
    print(f"\n[1/5] Starting worker claim server on port {CLAIM_PORT}...")
    server = ClaimServer(
        worker_account_id=worker_a_id,
        job_id=JOB_ID,
        amount_tinybars=BOUNTY_TINYBARS,
        deliverable={"agent_id": "agent_a", "status": "smoke_test"},
        facilitator_url=FACILITATOR_URL,
        port=CLAIM_PORT,
    )
    server.start()
    time.sleep(0.3)   # give the server a moment to bind
    print(f"  Worker endpoint : http://127.0.0.1:{CLAIM_PORT}/claim/{JOB_ID}")

    claim_url: str = f"http://127.0.0.1:{CLAIM_PORT}/claim/{JOB_ID}"

    try:
        # ── Step 2: probe — expect 402 ────────────────────────────────────────
        print("\n[2/5] Probing claim endpoint (expecting 402)...")
        status, headers, body = _do_request(claim_url)
        print(f"  HTTP status : {status}")
        assert status == 402, f"Expected 402, got {status}: {body.decode()}"

        pr_header: str = headers.get("PAYMENT-REQUIRED") or headers.get("payment-required", "")
        assert pr_header, "No PAYMENT-REQUIRED header in 402 response"
        print("  PAYMENT-REQUIRED header received ✓")

        # ── Step 3: build payment payload (broadcaster signs) ─────────────────
        print("\n[3/5] Building Hedera payment payload...")
        hedera_scheme = ExactHederaSchemeClient(
            operator_id=operator_id,
            operator_key_hex=operator_key,
        )
        x402_client = x402ClientSync()
        x402_client.register("hedera:testnet", hedera_scheme)
        http_client = x402HTTPClientSync(x402_client)

        payment_headers, payment_payload = http_client.handle_402_response(
            headers={k: v for k, v in headers.items()},
            body=body,
        )
        print("  Payment payload built ✓")

        # ── Step 4: retry with payment signature ──────────────────────────────
        print("\n[4/5] Retrying with payment-signature...")
        status2, headers2, body2 = _do_request(claim_url, extra_headers=payment_headers)
        print(f"  HTTP status : {status2}")
        if status2 != 200:
            raise RuntimeError(
                f"Expected 200 after payment, got {status2}: {body2.decode()}"
            )
        print("  200 OK received ✓")

        # ── Step 5: extract tx ID and print HashScan link ─────────────────────
        print("\n[5/5] Extracting settlement details...")
        pr_resp: str = (
            headers2.get(PAYMENT_RESPONSE_HEADER)
            or headers2.get(PAYMENT_RESPONSE_HEADER.lower(), "")
        )
        if pr_resp:
            settle = decode_payment_response_header(pr_resp)
            tx_id: str = settle.transaction or "pending"
        else:
            tx_id = "pending-facilitator-settlement"

        print(f"  TX ID    : {tx_id}")
        print(f"  HashScan : {HASHSCAN_TX.format(tx_id)}")
        print(
            "\n✓ Day 2 gate: open the HashScan link above and confirm the HBAR"
            " transfer is visible."
        )
        print("  Once confirmed, Day 2 is done — do not proceed to Day 3 until you see it.")

    finally:
        server.stop()


if __name__ == "__main__":
    main()

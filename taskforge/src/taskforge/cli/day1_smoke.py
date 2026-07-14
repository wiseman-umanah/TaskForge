"""
Day 1 smoke test — HCS hello world.

Verifies that the broadcaster credentials are valid and that the
``hiero-sdk-python`` integration works end-to-end on Hedera testnet by:

1. Creating a fresh HCS topic.
2. Submitting a single ``{"event": "hello_taskforge"}`` message to it.
3. Printing clickable HashScan links for both the topic and the transaction.

Prerequisites
-------------
A populated ``taskforge/.env`` file (copy from ``.env.example``) with:

- ``OPERATOR_ID``  — broadcaster testnet account ID, e.g. ``"0.0.1234"``
- ``OPERATOR_KEY`` — funded ECDSA private key (raw hex, no ``0x`` prefix)

Usage
-----
Run from inside the ``taskforge/`` directory::

    uv run python -m taskforge.cli.day1_smoke

Day 1 gate
----------
Both printed HashScan links must resolve and show content on HashScan.
Do not proceed to Day 2 until this is confirmed.
"""
from __future__ import annotations

import json

from dotenv import load_dotenv

from taskforge.ledger.hcs_client import create_topic, submit_message

HASHSCAN_TOPIC: str = "https://hashscan.io/testnet/topic/{}"
HASHSCAN_TX: str = "https://hashscan.io/testnet/transaction/{}"


def main() -> None:
    """Run the Day 1 HCS smoke test and print HashScan verification links.

    Creates one HCS topic and submits one message.  Exits cleanly on success.
    Any Hedera precheck error (wrong key type, insufficient funds, etc.) will
    propagate as an unhandled exception so the failure reason is clearly visible.
    """
    load_dotenv()

    print("=" * 60)
    print("TaskForge — Day 1 HCS Smoke Test")
    print("=" * 60)

    # Step 1 — create a fresh topic for this run
    print("\n[1/2] Creating HCS topic...")
    topic_id: str = create_topic(memo="taskforge-day1-smoke")
    print(f"  Topic ID : {topic_id}")
    print(f"  HashScan : {HASHSCAN_TOPIC.format(topic_id)}")

    # Step 2 — submit a hello-world message to the new topic
    print("\n[2/2] Submitting message...")
    payload: str = json.dumps({"event": "hello_taskforge", "step": "day1"})
    tx_id: str = submit_message(topic_id, payload)
    print(f"  TX ID    : {tx_id}")
    print(f"  HashScan : {HASHSCAN_TX.format(tx_id)}")

    print("\n✓ Day 1 gate: open both HashScan links above and confirm the message is visible.")
    print("  Once confirmed, Day 1 is done — do not proceed to Day 2 until you see the message.")


if __name__ == "__main__":
    main()

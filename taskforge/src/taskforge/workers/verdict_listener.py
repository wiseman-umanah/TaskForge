"""Verdict listener — polls HCS for a VerdictLog for a specific worker.

Each worker calls :func:`wait_for_verdict` after submitting its result to find
out whether it won.  This blocks until either a :class:`~taskforge.models.VerdictLog`
for the given ``agent_id`` appears on the HCS topic, or the timeout expires.

Workers learn their outcome from HCS rather than from a separate notification
channel.  See TECHNICAL_REQUIREMENTS.md §4a.

Usage::

    from taskforge.workers.verdict_listener import wait_for_verdict
    passed = wait_for_verdict(topic_id, "agent_a", since_ts, timeout=120)
"""
from __future__ import annotations

import time

from taskforge.ledger.hcs_client import poll_topic

_POLL_INTERVAL = 5.0   # seconds between Mirror Node polls


def wait_for_verdict(
    topic_id: str,
    agent_id: str,
    since_ts: float,
    timeout: float = 120.0,
) -> bool | None:
    """Block until a :class:`~taskforge.models.VerdictLog` arrives for *agent_id*.

    Polls the Hedera Mirror Node every :data:`_POLL_INTERVAL` seconds looking
    for a message with ``_type == "VerdictLog"`` and ``agent_id`` matching the
    given value.

    Args:
        topic_id: HCS topic ID string to poll.
        agent_id: Agent identifier to wait for (``"agent_a"`` or ``"agent_b"``).
        since_ts: Only look at messages with ``consensus_timestamp > since_ts``.
            Typically the timestamp when the worker submitted its Submission.
        timeout: Maximum seconds to wait before giving up.

    Returns:
        ``True`` if the verdict has ``passed == True``; ``False`` if
        ``passed == False``; ``None`` if the timeout expires without finding a
        verdict.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        messages = poll_topic(topic_id, since_ts)
        for msg in messages:
            if msg.get("_type") == "VerdictLog" and msg.get("agent_id") == agent_id:
                return bool(msg.get("passed"))
        time.sleep(_POLL_INTERVAL)
    return None

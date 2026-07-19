"""Thin HCS wrapper for TaskForge.

Responsibilities (and nothing else):

- :func:`create_topic` — create a new HCS topic, return its ID string.
- :func:`submit_message` — submit a JSON payload to an existing topic, return
  the transaction ID string.
- :func:`poll_topic` — poll the Hedera Mirror Node REST API for messages
  posted after a given timestamp; returns parsed JSON payloads.

All business logic (serialisation, routing, error handling) belongs in callers.
This module stays small and stateless.

Implementation note — why we do not use ``Client.from_env()``
--------------------------------------------------------------
``Client.from_env()`` resolves the operator key via ``PrivateKey.from_string()``,
which defaults to **Ed25519** when the raw key is 32 bytes.  Hedera Portal
accounts are **ECDSA (secp256k1)** by default.  Using the wrong key type causes
an ``INVALID_SIGNATURE`` precheck error with no helpful message.  We therefore
build the client manually with ``PrivateKey.from_string_ecdsa()`` to remove the
ambiguity entirely.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv
from hiero_sdk_python import (
    AccountId,
    Client,
    Network,
    PrivateKey,
    TopicCreateTransaction,
    TopicId,
    TopicMessageSubmitTransaction,
)

_MIRROR_NODE_BASE = "https://testnet.mirrornode.hedera.com"


def _load_private_key(key_hex: str) -> PrivateKey:
    """Load a Hedera private key, auto-detecting ECDSA vs ED25519.

    Tries ECDSA (secp256k1) first — the default for Hedera Portal accounts.
    Falls back to ED25519 if ECDSA parsing fails.

    Args:
        key_hex: Raw hex private key string (with or without ``0x`` prefix).

    Returns:
        A :class:`~hiero_sdk_python.PrivateKey` ready for signing.

    Raises:
        ValueError: If the key cannot be parsed as either key type.
    """
    try:
        return PrivateKey.from_string_ecdsa(key_hex)
    except Exception:
        pass
    try:
        return PrivateKey.from_string_ed25519(key_hex)
    except Exception:
        pass
    try:
        # DER-encoded keys (prefix 302e0201 for ED25519, 3041020100... for ECDSA)
        # from_string() handles DER format automatically
        return PrivateKey.from_string(key_hex)
    except Exception:
        pass
    raise ValueError(
        f"Could not parse private key as ECDSA, ED25519, or DER-encoded. "
        f"Ensure the key is a valid hex string from your Hedera account. "
        f"Key prefix: {key_hex[:8]}…"
    )


def _build_client() -> Client:
    """Build a Hedera testnet :class:`~hiero_sdk_python.Client` for the broadcaster.

    Reads credentials from the environment (or a ``.env`` file in the working
    directory):

    - ``OPERATOR_ID``  — broadcaster account ID, e.g. ``"0.0.1234"``.
    - ``OPERATOR_KEY`` — Private key as raw hex.  Both ECDSA and ED25519 are
      accepted — the key type is detected automatically.

    Returns:
        A fully configured :class:`~hiero_sdk_python.Client` ready to sign and
        submit transactions on Hedera testnet.

    Raises:
        KeyError: If either ``OPERATOR_ID`` or ``OPERATOR_KEY`` is absent from
            the environment.
        ValueError: If the key cannot be parsed as either ECDSA or ED25519.
    """
    load_dotenv()
    operator_id = os.environ["OPERATOR_ID"]
    operator_key = os.environ["OPERATOR_KEY"]

    client = Client(Network("testnet"))
    client.set_operator(
        AccountId.from_string(operator_id),
        _load_private_key(operator_key),
    )
    return client


def create_topic(memo: str = "taskforge") -> str:
    """Create a new HCS topic and return its string ID.

    This should be called **once per demo run** at startup.  Creating a fresh
    topic per run keeps the HashScan audit trail clean and scoped to a single
    execution.

    Args:
        memo: Optional memo attached to the topic creation transaction.
            Visible on HashScan.  Defaults to ``"taskforge"``.

    Returns:
        The new topic ID in ``"shard.realm.num"`` format, e.g. ``"0.0.5678"``.

    Raises:
        KeyError: If broadcaster credentials are missing from the environment.
        hiero_sdk_python.exceptions.PrecheckError: If the transaction fails
            Hedera precheck (e.g. insufficient balance, wrong key type).
    """
    client = _build_client()
    receipt = (
        TopicCreateTransaction()
        .set_memo(memo)
        .execute(client)
    )
    return str(receipt.topic_id)


def submit_message(topic_id_str: str, message_json: str) -> str:
    """Submit a JSON payload to an existing HCS topic.

    Every TaskForge domain event (job posted, submission received, verdict,
    payment) is persisted exclusively as an HCS message via this function.

    Args:
        topic_id_str: The target topic ID in ``"shard.realm.num"`` format,
            e.g. ``"0.0.5678"``.  Obtain this from :func:`create_topic`.
        message_json: A JSON string produced by
            :func:`~taskforge.models.to_json`.

    Returns:
        The transaction ID in ``"account@seconds.nanos"`` format,
        e.g. ``"0.0.1234@1784047153.89261054"``.  Use this to construct a
        HashScan link:
        ``https://hashscan.io/testnet/transaction/<tx_id>``.

    Raises:
        KeyError: If broadcaster credentials are missing from the environment.
        hiero_sdk_python.exceptions.PrecheckError: If the transaction fails
            Hedera precheck.
    """
    client = _build_client()
    topic_id = TopicId.from_string(topic_id_str)
    receipt = (
        TopicMessageSubmitTransaction()
        .set_topic_id(topic_id)
        .set_message(message_json)
        .execute(client)
    )
    if receipt.transaction_id is None:
        raise RuntimeError("HCS submit_message: receipt returned no transaction_id")
    return receipt.transaction_id.to_string()


_MIRROR_NODE_TIMEOUT = 15   # seconds

def poll_topic(topic_id_str: str, since_ts: float = 0.0) -> list[dict]:
    """Fetch HCS messages from the Mirror Node posted after ``since_ts``.

    Polls the Hedera testnet Mirror Node REST API
    (``https://testnet.mirrornode.hedera.com``) and returns all messages on the
    given topic whose ``consensus_timestamp`` is greater than ``since_ts``.

    Each returned dict is the JSON-decoded content of one HCS message (as
    produced by :func:`~taskforge.models.to_json`), plus a ``"_consensus_ts"``
    key carrying the raw consensus timestamp string for ordering.

    Args:
        topic_id_str: Target topic ID in ``"shard.realm.num"`` format.
        since_ts: Only return messages with ``consensus_timestamp > since_ts``.
            Pass ``0.0`` to retrieve all messages on the topic.

    Returns:
        List of decoded message dicts, ordered by ``consensus_timestamp``
        ascending.  Empty list if no messages match, if the Mirror Node
        returns HTTP 429 (rate-limited), or if any transient network error
        occurs (connection refused, DNS failure, socket timeout).

    Raises:
        json.JSONDecodeError: If a message body is not valid JSON.
    """
    # Mirror Node uses "seconds.nanos" string format for gt filter
    since_str = f"{since_ts:.9f}"
    url = (
        f"{_MIRROR_NODE_BASE}/api/v1/topics/{topic_id_str}/messages"
        f"?limit=100&order=asc&timestamp=gt:{since_str}"
    )
    try:
        with urllib.request.urlopen(url, timeout=_MIRROR_NODE_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return []   # rate-limited — caller will retry on next poll cycle
        raise
    except urllib.error.URLError:
        # Transient network error (DNS, connection refused, socket timeout).
        # verdict_listener will retry on the next poll cycle.
        return []

    results: list[dict] = []
    for msg in data.get("messages", []):
        raw = base64.b64decode(msg["message"]).decode("utf-8")
        payload = json.loads(raw)
        payload["_consensus_ts"] = msg["consensus_timestamp"]
        results.append(payload)
    return results

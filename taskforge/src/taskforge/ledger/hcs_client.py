"""
Thin HCS wrapper for TaskForge.

Responsibilities (and nothing else):

- :func:`create_topic` — create a new HCS topic, return its ID string.
- :func:`submit_message` — submit a JSON payload to an existing topic, return
  the transaction ID string.

All business logic (serialisation, routing, error handling) belongs in callers.
This module stays small and stateless.

Implementation note — why we do not use ``Client.from_env()``
--------------------------------------------------------------
``Client.from_env()`` resolves the operator key via ``PrivateKey.from_string()``,
which defaults to **Ed25519** when the raw key is 32 bytes.  Hedera Portal
accounts are **ECDSA (secp256k1)** by default.  Using the wrong key type causes
a ``INVALID_SIGNATURE`` precheck error with no helpful message.  We therefore
build the client manually with ``PrivateKey.from_string_ecdsa()`` to remove the
ambiguity entirely.
"""
from __future__ import annotations

import os

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


def _build_client() -> Client:
    """Build a Hedera testnet :class:`~hiero_sdk_python.Client` for the broadcaster.

    Reads credentials from the environment (or a ``.env`` file in the working
    directory):

    - ``OPERATOR_ID``  — broadcaster account ID, e.g. ``"0.0.1234"``.
    - ``OPERATOR_KEY`` — ECDSA private key as a raw hex string (no ``0x`` prefix).

    Returns:
        A fully configured :class:`~hiero_sdk_python.Client` ready to sign and
        submit transactions on Hedera testnet.

    Raises:
        KeyError: If either ``OPERATOR_ID`` or ``OPERATOR_KEY`` is absent from
            the environment.
        ValueError: If either value cannot be parsed as a valid account ID or
            ECDSA private key.
    """
    load_dotenv()
    operator_id = os.environ["OPERATOR_ID"]
    operator_key = os.environ["OPERATOR_KEY"]

    client = Client(Network("testnet"))
    client.set_operator(
        AccountId.from_string(operator_id),
        PrivateKey.from_string_ecdsa(operator_key),
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

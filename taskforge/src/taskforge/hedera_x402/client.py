"""Hedera client-side scheme for the x402 exact payment scheme (V2).

Implements the :class:`~x402.interfaces.SchemeNetworkClient` protocol so it
can be registered with :class:`~x402.x402ClientSync` via
``client.register("hedera:testnet", ExactHederaSchemeClient(...))``.

The ``create_payment_payload`` method builds a partially-signed Hedera
:class:`~hiero_sdk_python.transaction.transfer_transaction.TransferTransaction`
— broadcaster deducts the bounty from its own account, credits the worker's
account — serialises it to bytes, and base64-encodes it for transport.
The blocky402 facilitator (``https://api.testnet.blocky402.com``,
``feePayer: 0.0.7162784``) completes signing and submits the transaction
to Hedera testnet.
"""
from __future__ import annotations

import base64
import os
from typing import Any

from dotenv import load_dotenv
from hiero_sdk_python import (
    AccountId,
    Client,
    Hbar,
    Network,
    PrivateKey,
    TransactionId,
    TransferTransaction,
)

from x402.schemas import PaymentRequirements

NETWORK = "hedera:testnet"
SCHEME = "exact"
HBAR_ASSET = "0.0.0"


def _load_private_key(key_hex: str) -> PrivateKey:
    """Load a Hedera private key, auto-detecting ECDSA vs ED25519.

    Tries ECDSA (secp256k1) first — the default for Hedera Portal accounts.
    Falls back to ED25519 if ECDSA parsing fails, so agents with either key
    type work without any extra configuration.

    Args:
        key_hex: Raw hex private key string (with or without ``0x`` prefix),
            or a DER-encoded hex string.

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


class ExactHederaSchemeClient:
    """Client-side Hedera exact scheme for x402 V2.

    Builds a partially-signed HBAR :class:`TransferTransaction` for the x402
    facilitator to complete and submit.

    Attributes:
        scheme: Always ``"exact"`` — matches the facilitator's advertised kind.
    """

    scheme: str = SCHEME

    def __init__(self, operator_id: str, operator_key_hex: str) -> None:
        """Create an ``ExactHederaSchemeClient``.

        Accepts both ECDSA (secp256k1) and ED25519 private keys — the key type
        is detected automatically.  Hedera Portal accounts are ECDSA by default;
        older or tool-created accounts may be ED25519.

        Args:
            operator_id: Broadcaster Hedera account ID, e.g. ``"0.0.1234"``.
            operator_key_hex: Private key as raw hex (with or without ``0x``
                prefix).  The key type is auto-detected — no extra config needed.
        """
        self._operator_id = AccountId.from_string(operator_id)
        self._operator_key = _load_private_key(operator_key_hex)

    def _build_client(self) -> Client:
        """Build and return a signed testnet :class:`~hiero_sdk_python.Client`.

        Returns:
            A configured testnet client with the broadcaster operator set.
        """
        client = Client(Network("testnet"))
        client.set_operator(self._operator_id, self._operator_key)
        return client

    def create_payment_payload(
        self,
        requirements: PaymentRequirements,
    ) -> dict[str, Any]:
        """Build a partially-signed Hedera transfer for the blocky402 facilitator.

        Mirrors the ``@x402/hedera`` TypeScript reference implementation:

        1. Sets ``TransactionId`` to a generated ID for the *fee payer* account
           (from ``requirements.extra["feePayer"]``) — blocky402 adds its
           signature for that account before submitting.
        2. Adds HBAR transfers: debit broadcaster, credit worker's ``pay_to``.
        3. Freezes, payer signs, serialises to bytes, base64-encodes.

        The returned inner payload ``{"transaction": "<base64>"}`` matches the
        shape the blocky402 facilitator's ``/settle`` endpoint expects.

        Args:
            requirements: Payment requirements from the 402 response.  Must
                have ``pay_to`` set to the worker's account ID, ``amount`` in
                tinybars, and ``extra["feePayer"]`` set to the fee-payer
                account ID (e.g. ``"0.0.7162784"`` for blocky402 testnet).

        Returns:
            Scheme-specific inner payload dict with a single key:
                - ``transaction``: base64-encoded signed ``Transaction`` bytes.

        Raises:
            ValueError: If ``requirements.asset`` is not ``"0.0.0"`` (HBAR).
            KeyError: If ``requirements.extra`` is missing ``feePayer``.
        """
        if requirements.asset != HBAR_ASSET:
            raise ValueError(
                f"ExactHederaSchemeClient only supports HBAR (asset '0.0.0'), "
                f"got '{requirements.asset}'"
            )

        fee_payer_id = AccountId.from_string(requirements.extra["feePayer"])
        amount_tinybars = int(requirements.amount)
        pay_to = AccountId.from_string(requirements.pay_to)

        client = self._build_client()

        tx = TransferTransaction()
        tx.add_hbar_transfer(self._operator_id, Hbar.from_tinybars(-amount_tinybars))
        tx.add_hbar_transfer(pay_to, Hbar.from_tinybars(amount_tinybars))
        # Use fee payer's account for the TransactionId — blocky402 expects this
        tx.set_transaction_id(TransactionId.generate(fee_payer_id))
        tx.freeze_with(client)
        tx.sign(self._operator_key)

        raw_bytes: bytes = tx.to_bytes()
        encoded = base64.b64encode(raw_bytes).decode("ascii")

        return {"transaction": encoded}


def client_from_env() -> ExactHederaSchemeClient:
    """Construct an :class:`ExactHederaSchemeClient` from environment variables.

    Reads ``OPERATOR_ID`` and ``OPERATOR_KEY`` — the same variables used by
    the HCS client — so no additional configuration is needed.

    Returns:
        A ready-to-use :class:`ExactHederaSchemeClient`.

    Raises:
        KeyError: If ``OPERATOR_ID`` or ``OPERATOR_KEY`` is not set.
    """
    load_dotenv()
    return ExactHederaSchemeClient(
        operator_id=os.environ["OPERATOR_ID"],
        operator_key_hex=os.environ["OPERATOR_KEY"],
    )

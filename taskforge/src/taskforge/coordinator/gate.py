"""Entry-fee gate — x402 seller side for agent registration.

The coordinator acts as an x402 *server* for ``POST /agents/register``.
Any agent that has not yet paid the entry fee receives a ``402`` response
with the coordinator's Hedera account and the fee amount encoded in the
``PAYMENT-REQUIRED`` header.  After the agent pays and retries with a
``PAYMENT-SIGNATURE`` header, this module settles the payment via blocky402
and returns the on-chain transaction ID.

This is the mirror image of ``claim_reward.py`` — the logic is identical,
the direction is reversed: here *the coordinator is the payee*, not the
payer.

Usage::

    from taskforge.coordinator.gate import EntryFeeGate
    gate = EntryFeeGate(coordinator_account_id="0.0.1234")
    tx_hash = gate.settle(request_headers)   # raises HTTP 402 if no sig
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import HTTPException, Request
from x402.http.constants import PAYMENT_REQUIRED_HEADER, PAYMENT_SIGNATURE_HEADER
from x402.http.facilitator_client import FacilitatorConfig, HTTPFacilitatorClientSync
from x402.http.utils import (
    decode_payment_signature_header,
    encode_payment_required_header,
)
from x402.schemas import PaymentRequired, PaymentRequirements

_NETWORK = "hedera:testnet"
_SCHEME = "exact"
_HBAR_ASSET = "0.0.0"
_MAX_TIMEOUT_SECONDS = 180
_FEE_PAYER = "0.0.7162784"
_BLOCKY402_URL = "https://api.testnet.blocky402.com"

#: Entry fee — 0.01 HBAR.  Cheap for legitimate agents; expensive to spam.
ENTRY_FEE_TINYBARS: int = 1_000_000


class EntryFeeGate:
    """x402 entry-fee gate for agent registration.

    Exposes a single method :meth:`require_payment` that is called as a
    FastAPI dependency on ``POST /agents/register``.  If no payment signature
    is present in the request it raises ``HTTP 402``.  If a signature is
    present it settles via blocky402 and returns the Hedera tx ID.

    Attributes:
        coordinator_account_id: Hedera account that receives the entry fee.
        fee_tinybars: Fee amount in tinybars (default 0.01 HBAR).
        facilitator_url: blocky402 facilitator base URL.
    """

    def __init__(
        self,
        coordinator_account_id: str,
        fee_tinybars: int = ENTRY_FEE_TINYBARS,
        facilitator_url: str = _BLOCKY402_URL,
    ) -> None:
        """Create an :class:`EntryFeeGate`.

        Args:
            coordinator_account_id: Hedera account ID of the coordinator
                (payee).  Typically ``OPERATOR_ID`` from the environment.
            fee_tinybars: Entry fee in tinybars.  Defaults to
                :data:`ENTRY_FEE_TINYBARS` (0.01 HBAR).
            facilitator_url: blocky402 facilitator URL for settlement.
        """
        self.coordinator_account_id = coordinator_account_id
        self.fee_tinybars = fee_tinybars
        self.facilitator_url = facilitator_url

        requirements = PaymentRequirements(
            scheme=_SCHEME,
            network=_NETWORK,
            asset=_HBAR_ASSET,
            amount=str(fee_tinybars),
            pay_to=coordinator_account_id,
            max_timeout_seconds=_MAX_TIMEOUT_SECONDS,
            extra={"assetTransferMethod": "hedera_transfer", "feePayer": _FEE_PAYER},
        )
        self._requirements = requirements
        self._encoded_pr = encode_payment_required_header(
            PaymentRequired(x402_version=2, accepts=[requirements])
        )

    def require_payment(self, request: Request) -> str:
        """FastAPI dependency: enforce the entry-fee x402 gate.

        Call this as a dependency on ``POST /agents/register``::

            @app.post("/agents/register")
            def register(tx: str = Depends(gate.require_payment), ...):
                ...

        If the request carries no ``PAYMENT-SIGNATURE`` header, raises
        ``HTTP 402`` with the ``PAYMENT-REQUIRED`` header populated.

        If a valid signature is present, settles via blocky402 and returns
        the on-chain Hedera transaction ID.

        Args:
            request: The incoming FastAPI :class:`~fastapi.Request`.

        Returns:
            Hedera transaction ID string of the settled entry-fee payment.

        Raises:
            HTTPException(402): When no payment signature is present.
            HTTPException(400): When the payment signature header is malformed.
            HTTPException(502): When the blocky402 facilitator call fails.
            HTTPException(402): When the facilitator rejects the payment.
        """
        sig_header = request.headers.get(PAYMENT_SIGNATURE_HEADER)
        if not sig_header:
            raise HTTPException(
                status_code=402,
                detail="Entry fee required to register",
                headers={PAYMENT_REQUIRED_HEADER: self._encoded_pr},
            )

        try:
            payload = decode_payment_signature_header(sig_header)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Malformed payment-signature: {exc}"
            ) from exc

        try:
            facilitator = HTTPFacilitatorClientSync(
                FacilitatorConfig(url=self.facilitator_url)
            )
            settle_resp = facilitator.settle(payload, self._requirements)
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Facilitator error: {exc}"
            ) from exc

        if not settle_resp.success:
            reason = settle_resp.error_message or settle_resp.error_reason or "unknown"
            raise HTTPException(
                status_code=402, detail=f"Entry fee settlement rejected: {reason}"
            )

        return settle_resp.transaction or "pending"


def gate_from_env() -> EntryFeeGate:
    """Construct an :class:`EntryFeeGate` from environment variables.

    Reads ``OPERATOR_ID`` as the coordinator's Hedera account.

    Returns:
        A ready-to-use :class:`EntryFeeGate`.

    Raises:
        KeyError: If ``OPERATOR_ID`` is not set.
    """
    load_dotenv()
    return EntryFeeGate(coordinator_account_id=os.environ["OPERATOR_ID"])

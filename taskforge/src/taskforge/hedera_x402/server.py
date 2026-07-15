"""Hedera server-side scheme for the x402 exact payment scheme (V2).

Implements the :class:`~x402.interfaces.SchemeNetworkServer` protocol so it
can be registered with :class:`~x402.x402ResourceServerSync` via
``server.register("hedera:testnet", ExactHederaSchemeServer())``.

The server scheme is used by the worker's claim endpoint (``claim_reward.py``)
to build the ``PaymentRequirements`` object that gets encoded into the 402
``PAYMENT-REQUIRED`` response header.
"""
from __future__ import annotations

from x402.schemas import AssetAmount, Network, PaymentRequirements, Price, SupportedKind

SCHEME = "exact"
HBAR_ASSET = "0.0.0"          # Native HBAR asset identifier
HBAR_DECIMALS = 8              # 1 HBAR = 10^8 tinybars
# Fee payer advertised by blocky402 /supported for hedera:testnet
FEE_PAYER = "0.0.7162784"


class ExactHederaSchemeServer:
    """Server-side Hedera exact scheme for x402 V2.

    Handles price parsing (HBAR / tinybars) and populates the
    ``PaymentRequirements.extra`` field with the Hedera-specific metadata
    the facilitator and client need.

    Attributes:
        scheme: Always ``"exact"`` — matches the facilitator's advertised kind.
    """

    scheme: str = SCHEME

    def parse_price(self, price: Price, network: Network) -> AssetAmount:
        """Convert a price into a Hedera HBAR :class:`~x402.schemas.AssetAmount`.

        Accepts two formats:

        - ``AssetAmount`` — returned as-is (must use asset ``"0.0.0"``).
        - ``float`` / ``int`` — treated as **HBAR** and converted to tinybars.

        Args:
            price: Either an :class:`~x402.schemas.AssetAmount` or a numeric
                HBAR value (e.g. ``0.1`` for 0.1 HBAR = 10,000,000 tinybars).
            network: The CAIP-2 network identifier (e.g. ``"hedera:testnet"``).

        Returns:
            :class:`~x402.schemas.AssetAmount` with ``asset="0.0.0"`` and
            ``amount`` in tinybars as a string.

        Raises:
            TypeError: If ``price`` is a string (dollar-string pricing is not
                supported for Hedera — pass a numeric HBAR amount instead).
        """
        if isinstance(price, AssetAmount):
            return price

        if isinstance(price, str):
            raise TypeError(
                "Dollar-string pricing is not supported for Hedera.  "
                "Pass a numeric HBAR amount (e.g. 0.1) or an explicit AssetAmount."
            )

        tinybars = int(float(price) * (10 ** HBAR_DECIMALS))
        return AssetAmount(amount=str(tinybars), asset=HBAR_ASSET)

    def enhance_payment_requirements(
        self,
        requirements: PaymentRequirements,
        supported_kind: SupportedKind,
        extension_keys: list[str],
    ) -> PaymentRequirements:
        """Populate Hedera-specific fields in the payment requirements.

        Sets ``asset`` to HBAR (``"0.0.0"``) if not already set, and ensures
        ``extra`` contains the ``assetTransferMethod`` field so the facilitator
        knows to use a Hedera Transfer Transaction.

        Args:
            requirements: Base payment requirements (partially filled).
            supported_kind: Supported kind object returned by the facilitator.
            extension_keys: Extension keys active for this request.

        Returns:
            The same ``requirements`` object with ``asset`` and ``extra``
            filled in.
        """
        if not requirements.asset:
            requirements.asset = HBAR_ASSET

        if requirements.extra is None:
            requirements.extra = {}

        requirements.extra.setdefault("assetTransferMethod", "hedera_transfer")
        requirements.extra.setdefault("feePayer", FEE_PAYER)

        return requirements

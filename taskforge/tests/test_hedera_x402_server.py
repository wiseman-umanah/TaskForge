"""Unit tests for taskforge.hedera_x402.server — ExactHederaSchemeServer."""
from __future__ import annotations

import pytest

from x402.schemas import AssetAmount, PaymentRequirements

from taskforge.hedera_x402.server import (
    HBAR_ASSET,
    HBAR_DECIMALS,
    ExactHederaSchemeServer,
    FEE_PAYER,
)

_NETWORK = "hedera:testnet"


def _server() -> ExactHederaSchemeServer:
    return ExactHederaSchemeServer()


# ── parse_price ───────────────────────────────────────────────────────────────

class TestParsePrice:
    def test_float_hbar_converts_to_tinybars(self) -> None:
        server = _server()
        result = server.parse_price(0.1, _NETWORK)
        assert result.asset == HBAR_ASSET
        assert result.amount == str(int(0.1 * 10 ** HBAR_DECIMALS))  # "10000000"

    def test_int_hbar_converts(self) -> None:
        server = _server()
        result = server.parse_price(1, _NETWORK)
        assert result.amount == str(10 ** HBAR_DECIMALS)  # "100000000"

    def test_asset_amount_passthrough(self) -> None:
        server = _server()
        amount = AssetAmount(amount="5000", asset=HBAR_ASSET)
        result = server.parse_price(amount, _NETWORK)
        assert result is amount  # same object returned

    def test_string_price_raises_type_error(self) -> None:
        server = _server()
        with pytest.raises(TypeError, match="Dollar-string"):
            server.parse_price("$1.00", _NETWORK)  # type: ignore[arg-type]

    def test_zero_price_gives_zero_tinybars(self) -> None:
        server = _server()
        result = server.parse_price(0.0, _NETWORK)
        assert result.amount == "0"


# ── enhance_payment_requirements ─────────────────────────────────────────────

class TestEnhancePaymentRequirements:
    def _base_requirements(self, asset: str = HBAR_ASSET, extra: dict | None = None) -> PaymentRequirements:
        return PaymentRequirements(
            scheme="exact",
            network=_NETWORK,
            asset=asset,
            amount="10000000",
            pay_to="0.0.1234",
            max_timeout_seconds=180,
            extra=extra if extra is not None else {},
        )

    def test_sets_asset_when_missing(self) -> None:
        server = _server()
        req = self._base_requirements(asset="")
        result = server.enhance_payment_requirements(req, None, [])  # type: ignore[arg-type]
        assert result.asset == HBAR_ASSET

    def test_does_not_overwrite_existing_asset(self) -> None:
        server = _server()
        req = self._base_requirements(asset="0.0.456858")  # some token
        result = server.enhance_payment_requirements(req, None, [])  # type: ignore[arg-type]
        assert result.asset == "0.0.456858"

    def test_adds_asset_transfer_method(self) -> None:
        server = _server()
        req = self._base_requirements(extra=None)
        result = server.enhance_payment_requirements(req, None, [])  # type: ignore[arg-type]
        assert result.extra is not None
        assert result.extra["assetTransferMethod"] == "hedera_transfer"

    def test_adds_fee_payer(self) -> None:
        server = _server()
        req = self._base_requirements(extra=None)
        result = server.enhance_payment_requirements(req, None, [])  # type: ignore[arg-type]
        assert result.extra["feePayer"] == FEE_PAYER

    def test_does_not_overwrite_existing_extra(self) -> None:
        server = _server()
        req = self._base_requirements(extra={"custom": "value"})
        result = server.enhance_payment_requirements(req, None, [])  # type: ignore[arg-type]
        assert result.extra["custom"] == "value"
        assert "assetTransferMethod" in result.extra
        assert "feePayer" in result.extra

    def test_returns_same_requirements_object(self) -> None:
        server = _server()
        req = self._base_requirements()
        result = server.enhance_payment_requirements(req, None, [])  # type: ignore[arg-type]
        assert result is req

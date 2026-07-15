"""Worker claim endpoint — the x402 seller side of the payment flow.

Every worker process runs this HTTP server.  The endpoint always returns
``402 Payment Required`` with the worker's Hedera account and the bounty
amount encoded in the ``PAYMENT-REQUIRED`` header.  Once the broadcaster
sends a valid ``PAYMENT-SIGNATURE`` header, the server calls the blocky402
facilitator to settle the payment on Hedera testnet and then returns
``200 OK`` with the submission deliverable.

This is the server/seller side of the x402 Hedera exact scheme.  The
broadcaster (payer/client) hits this endpoint, pays, and receives the
deliverable.

Usage::

    from taskforge.settlement.claim_reward import ClaimServer
    server = ClaimServer(
        worker_account_id="0.0.5678",
        job_id="job-001",
        amount_tinybars=10_000_000,   # 0.1 HBAR
        deliverable={"extraction": ...},
        facilitator_url="https://api.testnet.blocky402.com",
        port=8402,
    )
    server.start()   # non-blocking — runs in a daemon thread
    server.stop()
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from x402.http.constants import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PAYMENT_SIGNATURE_HEADER,
)
from x402.http.facilitator_client import FacilitatorConfig, HTTPFacilitatorClientSync
from x402.http.utils import (
    decode_payment_signature_header,
    encode_payment_required_header,
    encode_payment_response_header,
)
from x402.schemas import PaymentRequired, PaymentRequirements

_NETWORK = "hedera:testnet"
_SCHEME = "exact"
_HBAR_ASSET = "0.0.0"
_MAX_TIMEOUT_SECONDS = 180
# Fee payer advertised by blocky402 /supported for hedera:testnet
_FEE_PAYER = "0.0.7162784"

BLOCKY402_URL = "https://api.testnet.blocky402.com"


class ClaimServer:
    """A minimal x402 payment-gated HTTP server for a single job claim.

    The worker exposes one endpoint: ``GET /claim/<job_id>``.  Before the
    broadcaster pays, the server returns ``402`` with the Hedera payment
    requirements.  After receiving a valid ``PAYMENT-SIGNATURE`` header, it
    calls the blocky402 facilitator to settle the HBAR transfer on Hedera
    testnet, then returns ``200`` with the submission deliverable and a
    ``PAYMENT-RESPONSE`` header containing the settlement receipt.

    Attributes:
        worker_account_id: Hedera account ID that will receive the payment.
        job_id: The job being claimed.
        amount_tinybars: Bounty in tinybars (0.1 HBAR = 10,000,000).
        deliverable: Arbitrary JSON-serialisable object returned on 200.
        facilitator_url: URL of the x402 facilitator (blocky402 by default).
        port: TCP port to listen on.
    """

    def __init__(
        self,
        worker_account_id: str,
        job_id: str,
        amount_tinybars: int,
        deliverable: Any,
        facilitator_url: str = BLOCKY402_URL,
        port: int = 8402,
    ) -> None:
        """Create a :class:`ClaimServer`.

        Args:
            worker_account_id: Hedera account ID of the worker (payee).
            job_id: Job identifier — used in the URL path.
            amount_tinybars: Payment amount in tinybars.
            deliverable: JSON-serialisable submission payload returned on 200.
            facilitator_url: x402 facilitator base URL for settlement calls.
            port: Local TCP port the server binds to.
        """
        self.worker_account_id = worker_account_id
        self.job_id = job_id
        self.amount_tinybars = amount_tinybars
        self.deliverable = deliverable
        self.facilitator_url = facilitator_url
        self.port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        """Return a request handler class bound to this server's configuration.

        Returns:
            A :class:`BaseHTTPRequestHandler` subclass.
        """
        worker_account_id = self.worker_account_id
        job_id = self.job_id
        amount_tinybars = self.amount_tinybars
        deliverable = self.deliverable
        facilitator_url = self.facilitator_url

        payment_requirements = PaymentRequirements(
            scheme=_SCHEME,
            network=_NETWORK,
            asset=_HBAR_ASSET,
            amount=str(amount_tinybars),
            pay_to=worker_account_id,
            max_timeout_seconds=_MAX_TIMEOUT_SECONDS,
            extra={"assetTransferMethod": "hedera_transfer", "feePayer": _FEE_PAYER},
        )
        payment_required = PaymentRequired(
            x402_version=2,
            accepts=[payment_requirements],
        )
        encoded_pr = encode_payment_required_header(payment_required)
        target_path = f"/claim/{job_id}"

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ARG002
                """Suppress default access log — demo output is cleaner without it."""

            def do_GET(self) -> None:  # noqa: N802
                """Handle GET requests to the claim endpoint.

                Returns 404 for unknown paths, 402 without payment signature,
                or 200 with the deliverable once payment has settled on Hedera.
                """
                if self.path != target_path:
                    self._send(404, b"Not found")
                    return

                sig_header = self.headers.get(PAYMENT_SIGNATURE_HEADER)
                if not sig_header:
                    # No payment — issue the 402
                    self._send(
                        402,
                        b"Payment required",
                        {PAYMENT_REQUIRED_HEADER: encoded_pr},
                    )
                    return

                # Decode the payment payload from the PAYMENT-SIGNATURE header
                try:
                    payload = decode_payment_signature_header(sig_header)
                except Exception as exc:
                    self._send(400, f"Bad payment signature header: {exc}".encode())
                    return

                # Call blocky402 to settle the Hedera transfer on-chain
                try:
                    facilitator = HTTPFacilitatorClientSync(
                        FacilitatorConfig(url=facilitator_url)
                    )
                    settle_resp = facilitator.settle(payload, payment_requirements)
                except Exception as exc:
                    self._send(
                        502,
                        f"Facilitator settle failed: {exc}".encode(),
                    )
                    return

                if not settle_resp.success:
                    reason = settle_resp.error_message or settle_resp.error_reason or "unknown"
                    self._send(402, f"Settlement rejected: {reason}".encode())
                    return

                encoded_resp = encode_payment_response_header(settle_resp)
                body = json.dumps({"status": "paid", "deliverable": deliverable}).encode()
                self._send(200, body, {PAYMENT_RESPONSE_HEADER: encoded_resp})

            def _send(
                self,
                status: int,
                body: bytes,
                extra_headers: dict[str, str] | None = None,
            ) -> None:
                """Send an HTTP response.

                Args:
                    status: HTTP status code.
                    body: Response body bytes.
                    extra_headers: Additional headers to include.
                """
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for name, value in (extra_headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

        return _Handler

    def start(self) -> None:
        """Start the claim server in a background daemon thread.

        Returns immediately.  Call :meth:`stop` to shut it down.
        """
        handler = self._make_handler()
        self._server = HTTPServer(("127.0.0.1", self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Shut down the claim server gracefully."""
        if self._server:
            self._server.shutdown()
            self._server = None

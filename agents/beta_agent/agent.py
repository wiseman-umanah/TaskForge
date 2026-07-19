"""Beta Agent — autonomous TaskForge competitor.

Engineered invoice-extraction prompt with explicit field rules and stricter
JSON instructions. Consistently scores higher than Alpha on the ground-truth
check.

On startup the agent:
  1. Self-registers with the coordinator (pays 0.01 HBAR entry fee via x402).
     Skipped if already registered.
  2. Starts a single x402 MultiJobClaimServer on CLAIM_PORT that handles any
     /claim/<job_id> — no port-per-task needed.
  3. Polls GET /tasks every 30 s for open jobs.
  4. For each new task: enrolls (pays 0.01 HBAR per-task fee), extracts
     invoice fields, submits the answer.

Usage::

    cp .env.example .env   # fill in credentials
    pip install -r requirements.txt
    python agent.py        # runs forever — Ctrl+C to stop
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from dotenv import load_dotenv
from groq import Groq
from groq import RateLimitError as _GroqRateLimitError

from x402 import x402ClientSync
from x402.http.x402_http_client import x402HTTPClientSync
from x402.http.constants import PAYMENT_REQUIRED_HEADER, PAYMENT_RESPONSE_HEADER, PAYMENT_SIGNATURE_HEADER
from x402.http.facilitator_client import FacilitatorConfig, HTTPFacilitatorClientSync
from x402.http.utils import (
    decode_payment_signature_header,
    encode_payment_required_header,
    encode_payment_response_header,
)
from x402.schemas import PaymentRequired, PaymentRequirements

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "taskforge", "src"))
from taskforge.hedera_x402 import ExactHederaSchemeClient  # noqa: E402

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
COORDINATOR_URL: str = os.getenv("COORDINATOR_URL", "http://localhost:8400")
AGENT_ID:        str = os.environ["AGENT_ID"]
ACCOUNT_ID:      str = os.environ["HEDERA_ACCOUNT_ID"]
PRIVATE_KEY:     str = os.environ["HEDERA_PRIVATE_KEY"]
GROQ_API_KEY:    str = os.environ["GROQ_API_KEY"]
CLAIM_PORT:      int = int(os.getenv("CLAIM_PORT", "9403"))
CLAIM_BASE_URL:  str = os.getenv("CLAIM_BASE_URL", f"http://localhost:{CLAIM_PORT}")

_MODEL            = "llama-3.3-70b-versatile"
_POLL_INTERVAL    = 30
_HTTP_TIMEOUT     = 20
_GROQ_MAX_RETRIES = 3
_GROQ_RETRY_DELAY = 6.0
_BLOCKY402_URL    = "https://api.testnet.blocky402.com"
_FEE_PAYER        = "0.0.7162784"
# Bounty is read from the task's bounty_hbar field at runtime — not hardcoded.
# Fallback used only if the API field is missing (should never happen).
_BOUNTY_FALLBACK_TINYBARS = 10_000_000   # 0.1 HBAR

_G = "\033[32m"; _R = "\033[31m"; _Y = "\033[33m"
_B = "\033[1m";  _D = "\033[2m";  _X = "\033[0m"

# ── Engineered prompt ─────────────────────────────────────────────────────────
_PROMPT = """\
You are a precise invoice-data extractor. Read the invoice below and output a
JSON object with EXACTLY the fields listed. Follow every rule strictly.

Invoice text:
{invoice_text}

Rules:
1. "vendor_name": full legal name of the seller as written on the invoice.
2. "invoice_number": the exact invoice reference code, no spaces added/removed.
3. "invoice_date": the issue date in ISO 8601 format YYYY-MM-DD.
4. "total_amount": the FINAL total payable (include VAT / taxes if shown),
   as a plain number — no currency symbols, no commas.
5. "currency": ISO 4217 three-letter code (GBP, USD, EUR, etc.).
6. "line_items": one entry per line item BEFORE taxes/subtotals. Each entry:
   - "description": verbatim description text from the invoice line.
   - "quantity": numeric quantity, as a number (not a string). Bandwidth:
     use the raw numeric value in the base unit (e.g. 2400 for 2400 GB).
   - "unit_price": price per unit as a number (e.g. £0.05/GB → 0.05).

Return ONLY the JSON object — no markdown fences, no explanation.
"""

# ── Multi-job x402 claim server ───────────────────────────────────────────────

class MultiJobClaimServer:
    """One HTTP server on one port that handles GET /claim/<any_job_id>.

    The coordinator may call any job_id at any time after settlement.
    Each job_id is registered via :meth:`add_job` before the agent submits.
    """

    def __init__(self, account_id: str, port: int) -> None:
        self._account_id = account_id
        self._port       = port
        self._jobs: dict[str, dict] = {}   # job_id → {amount, deliverable}
        self._lock  = threading.Lock()
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def add_job(self, job_id: str, amount_tinybars: int, deliverable: Any) -> None:
        """Register a job so the server can respond to its claim path."""
        with self._lock:
            self._jobs[job_id] = {
                "amount":      amount_tinybars,
                "deliverable": deliverable,
            }

    def start(self) -> None:
        """Bind the port and start serving in a daemon thread."""
        account_id  = self._account_id
        jobs_ref    = self._jobs
        jobs_lock   = self._lock

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: ARG002
                pass  # suppress access log

            def do_GET(self) -> None:  # noqa: N802
                # Path must be /claim/<job_id>
                parts = self.path.strip("/").split("/")
                if len(parts) != 2 or parts[0] != "claim":
                    self._send(404, b"Not found")
                    return

                job_id = parts[1]
                with jobs_lock:
                    job_info = jobs_ref.get(job_id)

                if job_info is None:
                    self._send(404, b"Job not registered on this agent")
                    return

                amount      = job_info["amount"]
                deliverable = job_info["deliverable"]

                requirements = PaymentRequirements(
                    scheme="exact",
                    network="hedera:testnet",
                    asset="0.0.0",
                    amount=str(amount),
                    pay_to=account_id,
                    max_timeout_seconds=180,
                    extra={"assetTransferMethod": "hedera_transfer", "feePayer": _FEE_PAYER},
                )
                encoded_pr = encode_payment_required_header(
                    PaymentRequired(x402_version=2, accepts=[requirements])
                )

                sig_header = self.headers.get(PAYMENT_SIGNATURE_HEADER)
                if not sig_header:
                    self._send(402, b"Payment required", {PAYMENT_REQUIRED_HEADER: encoded_pr})
                    return

                try:
                    payload = decode_payment_signature_header(sig_header)
                except Exception as exc:
                    self._send(400, f"Bad signature: {exc}".encode())
                    return

                try:
                    facilitator = HTTPFacilitatorClientSync(
                        FacilitatorConfig(url=_BLOCKY402_URL)
                    )
                    settle_resp = facilitator.settle(payload, requirements)
                except Exception as exc:
                    self._send(502, f"Facilitator error: {exc}".encode())
                    return

                if not settle_resp.success:
                    reason = settle_resp.error_message or settle_resp.error_reason or "unknown"
                    self._send(402, f"Settlement rejected: {reason}".encode())
                    return

                encoded_resp = encode_payment_response_header(settle_resp)
                body = json.dumps({"status": "paid", "deliverable": deliverable}).encode()
                self._send(200, body, {PAYMENT_RESPONSE_HEADER: encoded_resp})

            def _send(self, status: int, body: bytes,
                      extra: dict[str, str] | None = None) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

        self._server = HTTPServer(("0.0.0.0", self._port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _api(
    path: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict, bytes]:
    url  = f"{COORDINATOR_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (extra_headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read() or b""


def _api_json(
    path: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict | list:
    status, _hdrs, raw = _api(path, method=method, body=body, extra_headers=extra_headers)
    parsed = json.loads(raw) if raw else {}
    if status >= 300:
        detail = (parsed.get("detail", raw.decode()[:200])
                  if isinstance(parsed, dict) else raw.decode()[:200])
        raise RuntimeError(f"HTTP {status}: {detail}")
    return parsed


# ── x402 payment helper ───────────────────────────────────────────────────────

def _x402_pay(path: str, body: dict) -> dict:
    """Probe path, pay the x402 fee, retry. Returns the 201 response dict."""
    status, hdrs, resp_body = _api(path, method="POST", body=body)
    if status == 201:
        return json.loads(resp_body)
    if status != 402:
        raise RuntimeError(f"Unexpected {status}: {resp_body.decode()[:200]}")

    pr_raw = hdrs.get(PAYMENT_REQUIRED_HEADER) or hdrs.get(PAYMENT_REQUIRED_HEADER.lower(), "")
    if not pr_raw:
        raise RuntimeError("No PAYMENT-REQUIRED header in 402 response")

    hedera_scheme = ExactHederaSchemeClient(
        operator_id=ACCOUNT_ID,
        operator_key_hex=PRIVATE_KEY,
    )
    x402_c = x402ClientSync()
    x402_c.register("hedera:testnet", hedera_scheme)
    http_c = x402HTTPClientSync(x402_c)

    payment_headers, _ = http_c.handle_402_response(
        headers={k: v for k, v in hdrs.items()},
        body=resp_body,
    )

    status2, _, body2 = _api(path, method="POST", body=body, extra_headers=payment_headers)
    if status2 != 201:
        raise RuntimeError(f"Payment rejected ({status2}): {body2.decode()[:200]}")
    return json.loads(body2)

# ── Groq helpers ──────────────────────────────────────────────────────────────

def _groq_call(client: Groq, prompt: str) -> str:
    last_exc: Exception | None = None
    for attempt in range(1, _GROQ_MAX_RETRIES + 1):
        try:
            r = client.chat.completions.create(
                model=_MODEL,
                messages=[{"role": "user", "content": prompt}],
            )
            return (r.choices[0].message.content or "").strip()
        except _GroqRateLimitError as exc:
            last_exc = exc
            if attempt < _GROQ_MAX_RETRIES:
                wait = int(_GROQ_RETRY_DELAY * attempt)
                print(f"  {_Y}⚠ Groq rate-limited — retrying in {wait}s…{_X}")
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def _extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON in response: {text[:200]}")
    return json.loads(text[start:end])

# ── Registration ──────────────────────────────────────────────────────────────

def _register() -> None:
    """Self-register globally (one-time, pays 0.01 HBAR entry fee)."""
    print(f"{_B}Registering '{AGENT_ID}'…{_X}")
    agents = _api_json("/agents")
    assert isinstance(agents, list)
    if any(a["agent_id"] == AGENT_ID for a in agents):
        print(f"  {_G}✓{_X} Already registered.")
        return

    result = _x402_pay("/agents/register", {
        "agent_id":     AGENT_ID,
        "account_id":   ACCOUNT_ID,
        "claim_url":    CLAIM_BASE_URL,
        "capabilities": ["invoice_extraction"],
    })
    print(f"  {_G}✓{_X} Registered!  Entry fee TX: {result.get('entry_fee_tx', 'n/a')}")


def _enroll(job_id: str) -> bool:
    """Enroll in a specific task (pays 0.01 HBAR per-task fee).

    Returns True on success, False if enrollment failed (already enrolled
    counts as success too).
    """
    # Already enrolled?
    enrollments = _api_json(f"/tasks/{job_id}/enrollments")
    assert isinstance(enrollments, list)
    if any(e["agent_id"] == AGENT_ID for e in enrollments):
        return True

    try:
        _x402_pay(f"/tasks/{job_id}/enroll", {
            "agent_id":  AGENT_ID,
            "claim_url": f"{CLAIM_BASE_URL}/claim/{job_id}",
        })
        print(f"  {_G}✓{_X} Enrolled in {job_id}")
        return True
    except RuntimeError as exc:
        print(f"  {_R}✗ Enroll failed for {job_id}: {exc}{_X}")
        return False

# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    groq = Groq(api_key=GROQ_API_KEY)

    print(f"\n{_B}{'='*60}{_X}")
    print(f"{_B}  Beta Agent  ·  {AGENT_ID}  ·  port {CLAIM_PORT}{_X}")
    print(f"{_B}{'='*60}{_X}\n")

    _register()

    # Start ONE claim server for ALL jobs — handles any /claim/<job_id>
    claim_server = MultiJobClaimServer(account_id=ACCOUNT_ID, port=CLAIM_PORT)
    claim_server.start()
    print(f"  {_G}✓{_X} Claim server started on port {CLAIM_PORT}\n")

    submitted: set[str] = set()

    print(f"{_B}Polling for tasks every {_POLL_INTERVAL}s — Ctrl+C to stop{_X}\n")

    try:
        while True:
            try:
                tasks = _api_json("/tasks")
                assert isinstance(tasks, list)
                open_tasks = [
                    t for t in tasks
                    if not t["settled"] and t["seconds_remaining"] > 10
                ]

                for task in open_tasks:
                    job_id = task["job_id"]
                    if job_id in submitted:
                        continue

                    # Derive bounty from the task response — no hardcoded value
                    bounty_tinybars = int(task.get("bounty_hbar", 0.1) * 100_000_000)
                    if bounty_tinybars <= 0:
                        bounty_tinybars = _BOUNTY_FALLBACK_TINYBARS

                    print(
                        f"  {_B}→ Task {job_id}{_X}"
                        f"  bounty={task['bounty_hbar']} HBAR ({bounty_tinybars} tinybars)"
                        f"  {_D}{int(task['seconds_remaining'])}s left{_X}"
                    )

                    # Extract invoice fields
                    try:
                        raw     = _groq_call(groq, _PROMPT.format(invoice_text=task.get("invoice_text") or task["description"]))
                        payload = _extract_json(raw)
                    except Exception as exc:
                        print(f"  {_R}✗ Extraction failed: {exc}{_X}")
                        continue

                    # Register this job on the shared claim server BEFORE enrolling.
                    # Bounty amount comes from the task — agent doesn't need to know it.
                    claim_server.add_job(
                        job_id=job_id,
                        amount_tinybars=bounty_tinybars,
                        deliverable={"agent_id": AGENT_ID, "extraction": payload},
                    )

                    # Enroll in the task (pays per-task entry fee)
                    if not _enroll(job_id):
                        continue

                    # Submit answer
                    try:
                        result = _api_json("/submit", method="POST", body={
                            "job_id":   job_id,
                            "agent_id": AGENT_ID,
                            "output_payload": {
                                **payload,
                                "_worker_account_id": ACCOUNT_ID,
                            },
                        })
                        submitted.add(job_id)
                        hcs_tx = result.get("hcs_tx", "n/a") if isinstance(result, dict) else "n/a"
                        print(f"  {_G}✓{_X} Submitted — HCS: {hcs_tx}")
                    except RuntimeError as exc:
                        print(f"  {_R}✗ Submit failed: {exc}{_X}")

            except Exception as exc:  # noqa: BLE001
                print(f"  {_Y}⚠ Poll error: {exc}{_X}")

            time.sleep(_POLL_INTERVAL)

    except KeyboardInterrupt:
        print(f"\n{_D}Stopping…{_X}")
    finally:
        claim_server.stop()


if __name__ == "__main__":
    main()

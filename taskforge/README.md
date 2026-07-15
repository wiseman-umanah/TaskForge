# TaskForge

**Competitive AI task marketplace with micropayments on Hedera testnet via x402.**

Two AI worker agents compete to extract invoice fields. A verifier scores them. The winner gets paid 0.1 HBAR via the x402 payment protocol on Hedera testnet. Every event is anchored to an HCS topic — an immutable, tamper-proof audit trail visible on HashScan.

---

## Demo

```bash
cd taskforge
cp .env.example .env   # fill in your testnet accounts + Groq API key
uv run python -m taskforge.cli.run_demo
```

Single command, ~30 seconds, no manual intervention. Output:

```
[STEP 1/10] Creating HCS topic (new topic per run)
  ✓ Topic ID : 0.0.9582131
  ✓ HashScan : https://hashscan.io/testnet/topic/0.0.9582131

[STEP 2/10] Broadcasting invoice-extraction job
...
[STEP 5/10] Scoring submissions (schema → ground-truth → LLM judge)
  ✓ Agent A  score=0.925  passed=True
  ✓ Agent B  score=1.000  passed=True

[STEP 8/10] Paying winner (agent_b) via x402 on Hedera testnet
  ✓ 200 OK — payment settled!
  ✓ HashScan : https://hashscan.io/testnet/transaction/...

[STEP 9/10] Rejecting loser (agent_a)
  ✗ agent_a rejected — score=0.925
```

---

## Architecture

```
Broadcaster (orchestrator)
    │
    ├─ [1] Creates HCS topic ──────────────────────────────► HCS (audit log)
    ├─ [2] Posts Job to HCS ───────────────────────────────► HCS
    │
    ├─ [3] Runs Agent A (baseline prompt) ─► Groq LLM ──► Submission A ──► HCS
    ├─ [4] Runs Agent B (engineered prompt) ► Groq LLM ──► Submission B ──► HCS
    │
    ├─ [5] Scores both (ExtractionVerifier)
    │       schema check → ground-truth 70% → LLM judge 30%
    ├─ [6] Logs VerdictLog A + B ─────────────────────────► HCS
    │
    ├─ [7] Anti-spoofing: cross-checks 402 account vs pre-logged Submission
    │
    ├─ [8] PAY PATH  ──────────────────────────────────────────────────────
    │       GET /claim/{job_id} → 402  ← winner's ClaimServer
    │       Build PAYMENT-SIGNATURE (TransferTransaction, signed by broadcaster)
    │       blocky402 facilitator adds fee-payer sig + submits to Hedera testnet
    │       GET /claim/{job_id} + PAYMENT-SIGNATURE → 200
    │       Logs PaymentRecord ───────────────────────────► HCS
    │
    └─ [9] REJECT PATH ────────────────────────────────────────────────────
            Logs rejection PaymentRecord ──────────────────► HCS
```

### x402 payment flow (Hedera-specific)

Workers are the **x402 sellers**. The broadcaster is the **x402 client/payer**.

| Party | Role | Holds key? |
|---|---|---|
| Broadcaster (`OPERATOR_ID`) | Payer — builds + signs TransferTransaction | Yes (ECDSA) |
| Worker A (`WORKER_A_ACCOUNT_ID`) | Seller — issues 402, receives HBAR | No (receive-only) |
| Worker B (`WORKER_B_ACCOUNT_ID`) | Seller — issues 402, receives HBAR | No (receive-only) |
| blocky402 (`0.0.7162784`) | Facilitator — adds fee-payer sig + submits tx | External |

The TransactionId is set to `TransactionId.generate(fee_payer_account_id)` — blocky402's account (`0.0.7162784`), not the broadcaster's — because blocky402 co-signs as fee payer.

### Escrow model

Simplified: broadcaster pays directly to winner after verification passes. No held funds / smart-contract escrow. This is an explicit scope decision noted here to distinguish intentional design from oversight.

---

## Prerequisites

- Python 3.12 + `uv` (`pip install uv`)
- Three funded Hedera testnet accounts: broadcaster, worker A, worker B
  - Create at [portal.hedera.com](https://portal.hedera.com) (ECDSA by default)
  - Fund via the [testnet faucet](https://portal.hedera.com/faucet)
- A free [Groq API key](https://console.groq.com)

```bash
# Install dependencies
uv sync

# Configure
cp .env.example .env
# Edit .env — fill in OPERATOR_ID, OPERATOR_KEY, WORKER_A_ACCOUNT_ID,
# WORKER_B_ACCOUNT_ID, GROQ_API_KEY
```

### Required `.env` variables

```
OPERATOR_ID=0.0.xxxx           # broadcaster account (funded, ECDSA)
OPERATOR_KEY=                  # ECDSA private key (hex, 0x prefix OK)
WORKER_A_ACCOUNT_ID=0.0.yyyy   # worker A receive-only account
WORKER_B_ACCOUNT_ID=0.0.zzzz   # worker B receive-only account
GROQ_API_KEY=                  # Groq API key
FACILITATOR_URL=https://api.testnet.blocky402.com
HEDERA_NETWORK=hedera:testnet
```

---

## Verifier scoring

Three-stage fail-fast pipeline:

1. **Schema check** — all required fields present with correct types? Fail → score 0.0.
2. **Ground-truth check (70%)** — compares `vendor_name`, `invoice_number`, `invoice_date`, `total_amount`, `currency` against planted answers. Per-field mismatch deductions.
3. **LLM judge on `line_items` (30%)** — string matching is too brittle for descriptions. Groq `llama-3.3-70b-versatile` scores line-item accuracy.

Higher total score wins. Tie → Agent A wins (deterministic sort order).

---

## File layout

```
taskforge/
├── src/taskforge/
│   ├── models.py                       # Job, Submission, VerdictLog, PaymentRecord
│   ├── hedera_x402/                    # Custom Hedera x402 scheme (client + server)
│   ├── ledger/hcs_client.py            # create_topic, submit_message, poll_topic
│   ├── settlement/claim_reward.py      # Worker HTTP server — issues 402, calls blocky402
│   ├── broadcaster/broadcast_job.py    # Post job to HCS; planted invoice + ground truth
│   ├── workers/agent_a.py              # Baseline extraction prompt
│   ├── workers/agent_b.py              # Engineered extraction prompt
│   ├── workers/verdict_listener.py     # Poll HCS for VerdictLog
│   ├── verifier/base.py                # Abstract Verifier interface
│   ├── verifier/extraction_verifier.py # Concrete verifier — schema+GT+LLM judge
│   └── cli/
│       ├── run_demo.py                 # Full demo orchestrator
│       ├── day1_smoke.py               # HCS smoke test (Day 1 gate)
│       └── day2_smoke.py               # x402 payment smoke test (Day 2 gate)
├── pyproject.toml
├── .env.example
└── README.md
```

---

## Individual smoke tests

```bash
# Day 1: HCS hello world
uv run python -m taskforge.cli.day1_smoke

# Day 2: x402 payment round-trip
uv run python -m taskforge.cli.day2_smoke
```

---

## Built for

[Micropayments with x402 on Hedera](https://hedera.com/) hackathon bounty — July 2026.

Submission uses:
- `hiero-sdk-python` for HCS + account transfers
- `x402` Python SDK for the 402 payment protocol
- `blocky402` (`https://api.testnet.blocky402.com`) as the Hedera x402 facilitator
- Groq `llama-3.3-70b-versatile` for worker agents and the LLM judge

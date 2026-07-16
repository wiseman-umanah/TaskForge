# TaskForge

A competitive AI task marketplace where two agents race to extract invoice fields,
a verifier scores their work, and the winner is paid in real HBAR on Hedera testnet
via the x402 micropayment protocol — all within a single 30-second command.

Every event (job posted, submissions, verdicts, payment, rejection) is written to a
Hedera Consensus Service (HCS) topic and visible on HashScan — a tamper-proof,
on-chain audit trail with no database required.

---

## What it demonstrates

| Capability | How |
|---|---|
| **Competitive agent evaluation** | Two LLM workers (different prompts) compete on the same task; a three-stage verifier picks the better one |
| **x402 micropayments on Hedera** | Real HBAR moves via a proper 402 HTTP challenge/response round-trip, not a direct transfer |
| **On-chain audit trail** | Every state transition logged to HCS; reproducible from the topic alone |
| **Anti-spoofing** | Winner's account ID is bound at submission time, cross-checked against the 402 challenge before any payment is made |

---

## How it works

```
Broadcaster (orchestrator)
    │
    ├─ [1] Creates a fresh HCS topic ─────────────────────────────► HCS
    ├─ [2] Posts invoice-extraction job ──────────────────────────► HCS
    │
    ├─ [3] Runs Agent A — baseline prompt ──► Groq LLM ──► Submission ──► HCS
    ├─ [4] Runs Agent B — engineered prompt ► Groq LLM ──► Submission ──► HCS
    │
    ├─ [5] Scores both (ExtractionVerifier)
    │         Stage 1  Schema check          fail-fast, score 0 if invalid
    │         Stage 2  Ground-truth (70 %)   5 scalar fields vs planted answers
    │         Stage 3  LLM judge (30 %)      Groq scores line-item accuracy
    │
    ├─ [6] Logs VerdictLog A + B ─────────────────────────────────► HCS
    │
    ├─ [7] Anti-spoofing check
    │         Broadcaster cross-checks the 402 pay_to account
    │         against the account pre-logged in the winner's Submission.
    │         Mismatch → anomaly logged to HCS, payment aborted.
    │
    ├─ [8] PAY PATH ─────────────────────────────────────────────────────
    │         GET /claim/{job_id}            → 402 Payment Required
    │         Build + sign TransferTransaction (payer = broadcaster)
    │         blocky402 facilitator adds fee-payer sig, submits to Hedera
    │         GET /claim/{job_id} + PAYMENT-SIGNATURE  → 200 OK
    │         Logs PaymentRecord ───────────────────────────────────► HCS
    │
    └─ [9] REJECT PATH ──────────────────────────────────────────────────
              Logs rejection record with score and reason ──────────► HCS
```

### The task

A planted invoice (Meridian Cloud Solutions Ltd., GBP 1,866.00) is embedded in the
broadcaster. Agents must extract it into a strict JSON schema. Agent A uses a
minimal prompt and intentionally gets the bandwidth line-item quantity wrong
(2.4 TB instead of 2400 GB). Agent B uses an engineered prompt and gets it right.
The LLM judge catches the discrepancy — Agent B wins.

### The payment protocol

Workers are the **x402 sellers**. Each runs an HTTP server that always returns
`402 Payment Required` until a valid `PAYMENT-SIGNATURE` header arrives.
The broadcaster is the **x402 buyer**: it receives the 402, builds a partially-signed
Hedera `TransferTransaction`, and sends it to the
[blocky402](https://api.testnet.blocky402.com) facilitator, which co-signs as
fee payer and submits it to Hedera testnet. Real HBAR transfers before the 200 is
returned.

| Party | Role | Key needed |
|---|---|---|
| Broadcaster (`OPERATOR_ID`) | Payer — signs the TransferTransaction | Yes (ECDSA) |
| Worker A (`WORKER_A_ACCOUNT_ID`) | Seller — issues 402, receives HBAR | No |
| Worker B (`WORKER_B_ACCOUNT_ID`) | Seller — issues 402, receives HBAR | No |
| blocky402 (`0.0.7162784`) | Facilitator — fee-payer co-signer | External |

> **Escrow model:** broadcaster pays directly to the winner after verification.
> No held funds or smart-contract escrow — an intentional scope decision, noted
> here to distinguish design from oversight.

---

## Quick start

**Requirements:** Python 3.12, [`uv`](https://docs.astral.sh/uv/), three funded
Hedera testnet accounts, a free [Groq API key](https://console.groq.com).

```bash
# 1. Clone and install
git clone <repo>
cd taskforge
uv sync

# 2. Configure
cp .env.example .env
# Fill in OPERATOR_ID, OPERATOR_KEY, WORKER_A_ACCOUNT_ID,
# WORKER_B_ACCOUNT_ID, and GROQ_API_KEY

# 3. Run
uv run python -m taskforge.cli.run_demo
```

Create your three testnet accounts at [portal.hedera.com](https://portal.hedera.com)
(ECDSA key type, the default) and fund them from the
[testnet faucet](https://portal.hedera.com/faucet).

### `.env` reference

```
OPERATOR_ID=0.0.xxxx           # broadcaster — the only account that needs a key
OPERATOR_KEY=                  # ECDSA private key (hex string, 0x prefix OK)
WORKER_A_ACCOUNT_ID=0.0.yyyy   # worker A — receive-only, no key required
WORKER_B_ACCOUNT_ID=0.0.zzzz   # worker B — receive-only, no key required
GROQ_API_KEY=                  # from console.groq.com, free tier is fine
FACILITATOR_URL=https://api.testnet.blocky402.com
HEDERA_NETWORK=hedera:testnet
```

---

## Terminal output

```
================================================================
  TaskForge — Competitive Agent Task Marketplace Demo
================================================================

[STEP 1/10] Creating HCS topic (new topic per run)
  ✓ Topic     0.0.9582817
  ✓ HashScan  https://hashscan.io/testnet/topic/0.0.9582817

[STEP 2/10] Broadcasting invoice-extraction job
  ✓ Job ID    740aeeb8
  ✓ HCS TX    https://hashscan.io/testnet/transaction/0.0.9554629@...

[STEP 3/10] Running Agent A (baseline prompt)
  ✓ Submission logged  — HCS TX: ...
  ✓ Claim server live on port 8402

[STEP 4/10] Running Agent B (engineered prompt)
  ✓ Submission logged  — HCS TX: ...
  ✓ Claim server live on port 8403

[STEP 5/10] Scoring submissions (schema → ground-truth → LLM judge)

  Agent A  ──────────────────────────────────────────────────
    vendor_name  : Meridian Cloud Solutions Ltd.
    invoice_date : 2024-11-15
    total_amount : 1866.0
    line_items   : 4 items  (bandwidth qty: 2.4)

  Agent B  ──────────────────────────────────────────────────
    vendor_name  : Meridian Cloud Solutions Ltd.
    invoice_date : 2024-11-15
    total_amount : 1866.0
    line_items   : 4 items  (bandwidth qty: 2400)

  ✓ Agent A  score=0.925  passed=True
      GT=1.00  LLM=0.75  total=0.92
  ✓ Agent B  score=1.000  passed=True
      GT=1.00  LLM=1.00  total=1.00

[STEP 7/10] Determining winner + anti-spoofing check
  ✓ Winner     agent_b  (score=1.000)
  ✓ Anti-spoofing passed (account 0.0.9323949 matches pre-logged)

[STEP 8/10] Paying winner (agent_b) via x402 on Hedera testnet
  ✓ 402 Payment Required received
  ✓ Payment payload signed
  ✓ 200 OK — payment settled
  ✓ TX  0.0.7162784@1784113329.090540170
  ✓ HashScan  https://hashscan.io/testnet/transaction/...

[STEP 9/10] Rejecting loser (agent_a)
  ✗ agent_a rejected  score=0.925  (line-item quantity mismatch)

[STEP 10/10] Run complete — 31.8s
```

---

## Running the tests

```bash
cd taskforge
uv run pytest tests/ -v
```

31 offline unit tests covering `models.to_json`, the `ExtractionVerifier` scoring
pipeline (schema check, ground-truth weighting, 70/30 split, threshold gating),
and the `ExactHederaSchemeServer` price parsing — all with the LLM judge mocked out.
No network access required.

---

## Project layout

```
taskforge/
├── src/taskforge/
│   ├── models.py                        Job, Submission, VerdictLog, PaymentRecord
│   ├── hedera_x402/
│   │   ├── client.py                    Builds signed TransferTransaction for blocky402
│   │   └── server.py                    Parses price, populates payment requirements
│   ├── ledger/
│   │   └── hcs_client.py                create_topic · submit_message · poll_topic
│   ├── settlement/
│   │   └── claim_reward.py              Worker HTTP server: 402 until paid, 200 after
│   ├── broadcaster/
│   │   └── broadcast_job.py             Planted invoice · ground truth · post_job()
│   ├── workers/
│   │   ├── agent_a.py                   Baseline extraction prompt (Groq)
│   │   ├── agent_b.py                   Engineered extraction prompt (Groq)
│   │   └── verdict_listener.py          Polls HCS for VerdictLog
│   ├── verifier/
│   │   ├── base.py                      Abstract Verifier interface
│   │   └── extraction_verifier.py       Schema → ground-truth 70% → LLM judge 30%
│   └── cli/
│       ├── run_demo.py                  Full 10-step orchestrator
│       ├── day1_smoke.py                HCS smoke test
│       └── day2_smoke.py                x402 payment smoke test
├── tests/
│   ├── test_models.py
│   ├── test_extraction_verifier.py
│   └── test_hedera_x402_server.py
├── pyproject.toml
├── .env.example
└── README.md
```

---

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Package manager | `uv` |
| Hedera SDK | `hiero-sdk-python` — HCS topic create/submit, TransferTransaction |
| Payment protocol | `x402` Python SDK — 402 challenge/response, payload encoding |
| Facilitator | [blocky402](https://api.testnet.blocky402.com) — hosted, fee-payer co-signer |
| LLM | Groq `llama-3.3-70b-versatile` — workers and LLM judge |
| Config | `python-dotenv` |

---

## Built for

The **"Micropayments with x402 on Hedera"** bounty, July 2026 hackathon.

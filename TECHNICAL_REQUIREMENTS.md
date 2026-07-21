# Technical Requirements & Architecture — TaskForge

**Companion to PRD.md**  
**Status: ✅ COMPLETE — submitted July 2026**  
**Language: Python 3.12**

---

## 1. Stack — As Shipped

| Layer | Choice | Status |
|---|---|---|
| Language | Python 3.12 | ✅ |
| Package manager | `uv` — `uv add <pkg>` only, never `pip install` | ✅ |
| Hedera ledger ops | `hiero-sdk-python` — HCS topic create/submit, `TransferTransaction` | ✅ |
| Agent orchestration | `hedera-agent-kit` skipped — plain `hiero-sdk-python` calls used throughout | ✅ resolved |
| x402 payment layer | `x402` Python SDK — client, server, facilitator client | ✅ |
| Facilitator | **blocky402** (`https://api.testnet.blocky402.com`) — AlgoVoi was tried first, blocky402 chosen; fee payer `0.0.7162784` on `hedera:testnet` | ✅ resolved |
| LLM | Groq `llama-3.3-70b-versatile` — workers + LLM judge | ✅ |
| Web framework | FastAPI + uvicorn (coordinator server / v2) | ✅ |
| Persistence | SQLModel + SQLAlchemy — **opt-in** via `DATABASE_URL`; no-op when unset | ✅ |
| Config | `python-dotenv` | ✅ |
| Frontend | React 18 + Vite, plain CSS | ✅ |

---

## 2. Components — As Shipped

```
taskforge/src/taskforge/
├── models.py                     Job · Submission · VerdictLog · PaymentRecord
│                                 AgentRegistration · TaskEnrollment · to_json()
├── db.py                         Opt-in SQLModel persistence (6 tables)
│                                 No-op when DATABASE_URL is unset
├── coordinator/
│   ├── app.py                    Bootstrap: platform HCS topic → FastAPI → first task
│   ├── server.py                 13 REST endpoints, CoordinatorState dataclass
│   ├── scheduler.py              Background deadline watcher: score → pay → auto-generate
│   ├── gate.py                   EntryFeeGate — x402 seller for registration/enrollment
│   └── registry.py               Thread-safe agent registry + win tracking
├── hedera_x402/
│   ├── client.py                 ExactHederaSchemeClient — builds signed TransferTx
│   └── server.py                 ExactHederaSchemeServer — parses price requirements
├── ledger/
│   └── hcs_client.py             create_topic() · submit_message() · poll_topic()
│                                 Mirror Node 429 and URLError both return [] (no raise)
├── settlement/
│   └── claim_reward.py           ClaimServer: 402 until paid, 200 after settlement
├── broadcaster/
│   └── broadcast_job.py          Task pool (4 invoices) · pick_task() · post_job()
├── workers/
│   ├── agent_a.py                Baseline extraction (run_demo.py)
│   ├── agent_b.py                Engineered extraction (run_demo.py)
│   └── verdict_listener.py       Polls HCS Mirror Node for VerdictLog
├── verifier/
│   ├── base.py                   Abstract Verifier interface
│   └── extraction_verifier.py    Schema → GT 70% → LLM judge 30%
└── cli/
    ├── run_demo.py               10-step demo orchestrator (FROZEN — do not modify)
    ├── run_server.py             Coordinator server entry point
    ├── day1_smoke.py             ✅ HCS smoke test
    └── day2_smoke.py             ✅ x402 end-to-end smoke test

agents/
├── alpha_agent/agent.py          Autonomous agent: register → enroll → extract → submit
└── beta_agent/agent.py           Same structure, engineered prompt, port 9403

taskforge-ui/src/
├── pages/Tasks.jsx               Task cards with Compete modal
├── pages/Agents.jsx              Registry + deregister modal
├── pages/Leaderboard.jsx         Win counts + avg scores
├── pages/Ledger.jsx              HCS audit trail (platform + per-task topics)
├── pages/Register.jsx            API reference + curl snippets
└── pages/AgentGuide.jsx          End-to-end agent integration guide
```

---

## 3. Data Models — As Shipped

```python
@dataclass
class Job:
    job_id: str
    description: str
    output_schema: dict
    bounty_amount: float
    currency: str          # "HBAR"
    deadline_ts: float

@dataclass
class Submission:
    job_id: str
    agent_id: str
    output_payload: dict   # includes _worker_account_id for anti-spoofing
    submitted_ts: float

@dataclass
class VerdictLog:
    job_id: str
    agent_id: str
    score: float
    passed: bool
    reason: str            # "GT=0.80 LLM=1.00 total=0.86 [⚠ LLM note if failed]"
    ts: float

@dataclass
class PaymentRecord:
    job_id: str
    winner_agent_id: str
    tx_hash: str           # Hedera tx ID or "ANOMALY" / "expired" / "none"
    amount: float
    hcs_message_id: str

@dataclass
class AgentRegistration:   # v2 addition
    agent_id: str
    account_id: str
    claim_url: str
    capabilities: list[str]
    entry_fee_tx: str
    registered_ts: float

@dataclass
class TaskEnrollment:      # v2 addition
    job_id: str
    agent_id: str
    account_id: str
    claim_url: str
    entry_fee_tx: str
    enrolled_ts: float
```

Every instance is serialised with `to_json()` and written to HCS immediately on creation.
HCS is the system of record — no database required.

---

## 4a. Payment Authorization — Anti-Spoofing ✅ Implemented

All three rules from the original spec are implemented:

1. **Account bound at submission time** — `_worker_account_id` is embedded in every
   `Submission.output_payload` before scoring runs. In v2 it is also stored in the
   agent's `TaskEnrollment` record on the per-task HCS topic.

2. **Fixed allowlist** — the scheduler's `_pay_winner()` checks `pre_logged_acct`
   (from submission payload) and `expected_acct` (from enrollment/registry). The 402
   `pay_to` must match one of these or payment is aborted and an anomaly is logged to HCS.

3. **402 challenge is a verification checkpoint** — the broadcaster/coordinator always
   cross-checks the account in the `PAYMENT-REQUIRED` header against the pre-logged
   value before calling `handle_402_response()`. Mismatch → `ANTI-SPOOFING` log to HCS,
   no payment issued.

---

## 4b. Hedera-Specific Integration — Resolved Decisions

| Topic | Decision |
|---|---|
| Key type | `PrivateKey.from_string_ecdsa()` always — Portal accounts are ECDSA. Fallback chain: ECDSA → ED25519 → `from_string()` (DER) |
| `Client.from_env()` | Not used — defaults Ed25519 on 32-byte keys → `INVALID_SIGNATURE` |
| `Hbar` amounts | `Hbar.from_tinybars(n)` — `Hbar.from_unit()` does not exist |
| `TransactionId` | `TransactionId.generate(AccountId.from_string("0.0.7162784"))` — fee payer's account, not broadcaster's |
| blocky402 payload | `{"transaction": "<base64>"}` — exactly one key; extra keys → `transaction_could_not_be_decoded` |
| `priceAtomic` | Tinybars — 0.1 HBAR = 10,000,000 tinybars |
| Signed payload TTL | 180 seconds — sign immediately before retry, not before probe |
| HCS topic ID | `str(receipt.topic_id)` → `"0.0.5678"` |
| HCS tx ID | `receipt.transaction_id.to_string()` → `"0.0.1234@seconds.nanos"` |
| Mirror Node 429 | `poll_topic()` returns `[]` — caller retries on next poll cycle |

---

## 4c. x402 Payment Flow — Sequence Diagram

### Registration / Enrollment (0.01 HBAR entry fee)

```
Agent                      Coordinator               blocky402          Hedera HCS
  |                             |                        |                   |
  |-- POST /agents/register --> |                        |                   |
  |                             |                        |                   |
  | <-- 402 PAYMENT-REQUIRED -- |                        |                   |
  |   (amount=0.01 HBAR,        |                        |                   |
  |    pay_to=coordinator acct) |                        |                   |
  |                             |                        |                   |
  | [sign TransferTx locally]   |                        |                   |
  |                             |                        |                   |
  |-- POST + PAYMENT-SIGNATURE->|                        |                   |
  |                             |-- settle(tx, req) ---> |                   |
  |                             |                        |-- submit tx ----> |
  |                             |                        | <-- tx_id ------  |
  | <-- 201 {registered, tx} -- |                        |                   |
  |                             |-- submit_message() ---------------------------> HCS
```

### Task Settlement — Pay Winner (0.1 HBAR bounty)

```
Coordinator/Scheduler       Agent ClaimServer          blocky402          Hedera HCS
  |                             |                        |                   |
  | [deadline expired]          |                        |                   |
  | [ExtractionVerifier scores] |                        |                   |
  | [winner determined]         |                        |                   |
  |                             |                        |                   |
  |-- GET /claim/{job_id} ----> |                        |                   |
  |                             |                        |                   |
  | <-- 402 PAYMENT-REQUIRED -- |                        |                   |
  |   (pay_to=agent Hedera acct)|                        |                   |
  |                             |                        |                   |
  | [anti-spoofing check]       |                        |                   |
  | [sign TransferTx]           |                        |                   |
  |                             |                        |                   |
  |-- GET + PAYMENT-SIGNATURE-> |                        |                   |
  |                             |-- settle(tx, req) ---> |                   |
  |                             |                        |-- submit tx ----> |
  |                             |                        | <-- tx_id ------  |
  | <-- 200 PAYMENT-RESPONSE -- |                        |                   |
  |   (transaction=tx_id)       |                        |                   |
  |                             |                        |                   |
  |-- submit_message(PaymentRecord) ----------------------------------------> HCS
```

**HTTP headers reference:**

| Direction | Header | Content |
|---|---|---|
| Server → Client | `PAYMENT-REQUIRED` | base64 JSON — `x402_version`, `accepts[].scheme/network/amount/pay_to` |
| Client → Server | `PAYMENT-SIGNATURE` | base64 JSON — signed `TransferTransaction` bytes |
| Server → Client | `PAYMENT-RESPONSE` | base64 JSON — `transaction` field = Hedera tx ID |

---

## 5. Day-by-Day Plan — Completed

| Day | Milestone | Outcome |
|---|---|---|
| 1 | Testnet accounts, `hiero-sdk-python` HCS hello-world | ✅ `day1_smoke.py` — topic created, message on HashScan |
| 2 | x402 + blocky402 wired up, one real payment settles | ✅ `day2_smoke.py` — 0.1 HBAR moved, tx on HashScan |
| 3 | Workers A + B built, different output quality confirmed | ✅ `agent_a.py`, `agent_b.py`, `verdict_listener.py` |
| 4 | Verifier built: schema → GT 70% → LLM judge 30% | ✅ `extraction_verifier.py`, 31 tests passing |
| 5 | `run_demo.py` end-to-end, both pay and reject paths | ✅ Full 10-step flow, clean unattended run |
| 6 | v2 coordinator server, autonomous agents, UI, polish, README, submit | ✅ FastAPI coordinator, alpha/beta agents, React UI, all docs updated |

---

## 6. Open Technical Decisions — All Resolved

| Decision | Resolution |
|---|---|
| Escrow model | Simplified direct payment post-verification. Stated explicitly in README as scope decision. |
| Facilitator | blocky402 (`https://api.testnet.blocky402.com`). AlgoVoi was attempted first; blocky402 confirmed working Day 2. |
| Task type | Locked — invoice field extraction. Four invoices in pool (GBP, USD, EUR, AUD). |
| Database | Opt-in SQLModel persistence — coordinator runs fully in-memory by default. |
| Multiple workers | v2 supports N external agents via REST API; demo still uses fixed A vs B for a clean video story. |

---

## 7. Risks — All De-Risked

| Risk | Resolution |
|---|---|
| `hiero-sdk-python` + `x402` interop | ✅ Working Day 2. Key insight: use `PrivateKey.from_string_ecdsa()`, `TransactionId` must use fee payer's account, inner payload is `{"transaction": "<base64>"}` only. |
| Testnet HBAR faucet | ✅ All accounts funded before Day 3. |
| AlgoVoi facilitator | ✅ Switched to blocky402 — one endpoint, confirmed live on `hedera:testnet`. |
| Rate limits (Groq) | ✅ `_groq_call()` has 3-retry back-off in all three call sites (agent_a, agent_b, verifier LLM judge). |
| Mirror Node 429 | ✅ `poll_topic()` returns `[]` on 429 and transient `URLError` — verdict listener retries on next cycle. |
| Port conflicts on re-run | ✅ `try/finally` in `run_demo.py` calls `server.stop()` on both claim servers. `MultiJobClaimServer` uses one port for all job IDs. |
| Agents scoring 0.000 | ✅ Fixed — `GET /tasks` now returns `invoice_text`; agents use `task["invoice_text"]` not `task["description"]`. |

---

## 8. Task Type — Invoice Extraction (Locked, Shipped)

**Task pool (4 invoices, randomly assigned per round):**

| Vendor | Currency | Challenge |
|---|---|---|
| Meridian Cloud Solutions Ltd. | GBP | Bandwidth: 2400 GB @ £0.05/GB (quantity trap for Agent A) |
| Apex Design Studio LLC | USD | 40-unit illustration assets, US sales tax |
| Nordic Logistics GmbH | EUR | Bilingual German/English headers, 19% MwSt |
| SolarEdge Installations Pty Ltd | AUD | Labour with composite qty description, 10% GST |

**Scoring formula:**

```
final_score = 0.7 × gt_score + 0.3 × llm_score
passed      = gt_score ≥ 0.5
```

- `gt_score` — 5 scalar fields vs planted ground truth (exact/near-exact match)
- `llm_score` — Groq judge rates `line_items` accuracy; 0.25 deducted per wrong item
- Pass threshold is on `gt_score` only — fluking a good LLM judge score does not win

**Why this task type:** deterministic ground truth (broadcaster planted the answers), no execution sandbox needed, LLM judge is confined to the harder-to-match line-items sub-field so the primary score is not just "another LLM's opinion."

---

## 9. HCS Topic Model — As Shipped

```
Platform topic  (one per server run, memo="taskforge-v2-platform")
    └── AgentRegistration messages

Per-task topic  (one per job, memo="taskforge-task")
    ├── Job
    ├── TaskEnrollment × N
    ├── Submission × N
    ├── VerdictLog × N
    └── PaymentRecord  (or rejection / anomaly)
```

HashScan URL formats:
- Topic: `https://hashscan.io/testnet/topic/<id>`
- Transaction: `https://hashscan.io/testnet/transaction/<tx_id>`

---

## 10. Success Criteria — All Met ✅

- [x] Real testnet transactions — every tx has a HashScan link printed in the demo output
- [x] Both "paid" and "rejected" paths demonstrated in `run_demo.py`
- [x] Full run completes unattended in a single terminal session (~35 seconds)
- [x] README explains architecture and how to reproduce
- [x] GitHub repo public
- [x] Demo video ≤ 5 minutes

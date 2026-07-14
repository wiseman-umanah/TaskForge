# Technical Requirements & Architecture — TaskForge

**Companion to PRD.md — feed both to LLM co-builder together**
**Language: Python 3.10+**

---

## 1. Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.10+ | Required by hiero-sdk-python |
| Hedera ledger ops | `hiero-sdk-python` (PyPI) | Direct HCS topic create/submit, account transfers. Official Hiero-maintained SDK. |
| Agent orchestration | `hedera-agent-kit` Python package (optional) | LangChain toolkit with `core_consensus_plugin`, `core_account_plugin`, `core_account_query_plugin`, `core_token_plugin`. Use only if it saves time — plain `hiero-sdk-python` calls are simpler and less likely to break under deadline pressure. Default to plain SDK calls; add the agent kit only if LLM-driven tool-calling is genuinely needed. |
| x402 payment layer | Official `x402` Python SDK (PyPI) | Client, server, facilitator, and framework integration in one package. |
| Facilitator | Hedera reference facilitator (from `matevszm/x402-hedera-example`, TypeScript) OR AlgoVoi multi-chain facilitator (confirmed native Hedera support, hosted, single endpoint) | **Try AlgoVoi first** — hosted means no facilitator infra to run yourself. Fall back to self-hosting the TS reference facilitator only if AlgoVoi doesn't fit. |
| LLM calls | Anthropic API (or provider of choice) | Used by both worker agents and the verifier |
| Config | `python-dotenv` | Testnet account IDs + private keys, never committed |

---

## 2. Components

```
taskforge/
├── broadcaster/
│   └── broadcast_job.py      # defines job, publishes to HCS, triggers settlement after verdict
├── workers/
│   ├── agent_a.py            # baseline prompt
│   └── agent_b.py            # constraint-engineered prompt
├── verifier/
│   ├── base.py               # abstract Verifier interface: verify(task_spec, submission) -> VerdictLog
│   └── extraction_verifier.py # concrete implementation for the locked task type (Option 2 by default)
├── settlement/
│   └── pay_winner.py         # x402 payment call via facilitator, Hedera exact scheme
├── ledger/
│   └── hcs_client.py         # thin wrapper: create_topic(), submit_message()
├── cli/
│   └── run_demo.py           # orchestrates full sequence, step-labeled terminal output
├── models.py                 # Job, Submission, VerdictLog, PaymentRecord dataclasses
├── .env.example
└── README.md
```

---

## 3. Data Models

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class Job:
    job_id: str
    description: str
    output_schema: dict          # expected submission JSON shape
    bounty_amount: float
    currency: str                 # "HBAR" (or testnet USDC-equivalent if used)
    deadline_ts: float

@dataclass
class Submission:
    job_id: str
    agent_id: str                 # "agent_a" | "agent_b"
    output_payload: dict
    submitted_ts: float

@dataclass
class VerdictLog:
    job_id: str
    agent_id: str
    score: float
    passed: bool
    reason: str
    ts: float

@dataclass
class PaymentRecord:
    job_id: str
    winner_agent_id: str
    tx_hash: str
    amount: float
    hcs_message_id: str
```

Every one of these gets serialized to JSON and submitted as an HCS message at the point it's created — the HCS topic *is* the system of record.

**On the pluggable verifier:** `verifier/base.py` defines one abstract method (`verify(task_spec, submission) -> VerdictLog`) so additional task types (summarization, code-gen) can be added after the hackathon without touching the broadcaster, settlement, or ledger layers. **Only one concrete verifier ships in the hackathon submission.** This is an architecture decision, not a feature commitment — do not build a second concrete verifier before the deadline; it was explicitly considered and rejected (see PRD §2a).

---

## 4. Hedera-Specific Integration Points

- **Accounts needed (all testnet):** broadcaster, worker A, worker B. Verifier can be off-chain/non-paying — it doesn't need its own account unless you want its scoring identity attested too (optional, cut if time-constrained).
- **HCS topic:** one topic per demo run is cleanest for a clear HashScan story. Create it fresh in `run_demo.py` at the start of each run, print the topic ID immediately.
- **x402 exact payment scheme (Hedera-specific):** client (broadcaster) constructs a payment transaction, signs it but leaves it partially signed, sends it to the facilitator, which completes signing (pays gas) and submits it. This is different from EVM-chain x402 flows — confirm you're following Hedera's documented exact scheme, not porting an EVM example directly.
- **HashScan:** every tx hash and topic ID printed to console during the demo run, formatted as clickable HashScan testnet links.

---

## 5. Day-by-Day Plan (6 days)

| Day | Milestone | Gate to pass before moving on |
|---|---|---|
| 1 | Testnet accounts created (broadcaster + 2 workers). `hiero-sdk-python` "hello world": create an HCS topic, submit one message, confirm it on HashScan. | Message visible on HashScan or stop and debug — nothing else proceeds until this works. |
| 2 | x402 Python SDK + chosen facilitator wired up. One real payment (broadcaster → single worker account) settles on Hedera testnet end-to-end. | Payment tx visible on HashScan. **This is the single biggest technical risk — do not proceed to agent logic until this is solid.** |
| 3 | Task type locked (§8 in PRD — recommend Option 2). Worker A and Worker B built, each producing a submission with a visibly different quality/approach. | Run both workers side by side, confirm output actually differs. |
| 4 | Verifier built: schema check + LLM-judge scoring (+ ground-truth check if Option 2). Scoring wired into HCS logging. | Verifier correctly scores a known-good and a known-bad submission differently. |
| 5 | Full sequence wired into `run_demo.py`. Test **both** the pay path and the reject path end-to-end, unattended. | One full clean run, no manual intervention, both paths demonstrated. |
| 6 | Terminal output polish (step labels, color for pass/fail), record demo video, write README, submit before July 19, 11:59 PM ET. | Video under 5 minutes, GitHub public, description matches what's on screen. |

If Day 2's gate isn't cleared by end of Day 2, treat that as the signal to simplify further (e.g., drop the facilitator abstraction and call Hedera SDK transfer directly for the payment leg, treating "x402-flavored" logic as a thin wrapper) rather than losing Days 3–4 to a broken payment layer.

---

## 6. Open Technical Decisions

1. **Escrow model.** Full smart-contract-style held funds vs. simplified "broadcaster pays directly to winner only after verification passes." Default to the simplified version — it's honest, it's fast to build, and it's explicitly called out as a scope decision in the README (not hidden).
2. **Facilitator choice.** AlgoVoi (hosted, multi-chain incl. Hedera) vs. self-hosted TypeScript reference facilitator ported/wrapped for Python use. Try AlgoVoi first.
3. **Task type.** Locked in PRD §8 as a recommendation (Option 2 — fixed-document extraction with ground truth), but confirm before Day 3 starts.

---

## 7. Risks to De-Risk Immediately (Day 1–2, before writing agent logic)

- Confirm `hiero-sdk-python` and the `x402` Python SDK actually interoperate cleanly for Hedera's exact payment scheme — this combination is new (Hedera's x402 support only shipped this year) and under-documented compared to the EVM-chain x402 flows most tutorials cover.
- Confirm testnet HBAR faucet access for all 3 accounts before building anything that depends on funded accounts.
- If the facilitator integration proves unexpectedly painful, have a fallback: settle payment via a direct `hiero-sdk-python` HBAR transfer, and treat the "x402" framing as sitting at the HTTP/API layer (402 response + retry-with-payment-header pattern) rather than depending on a fully wired third-party facilitator. This still satisfies the bounty's requirement to build "using x402 standard on Hedera rails" while giving you a working fallback if the facilitator layer is the thing that breaks under time pressure.

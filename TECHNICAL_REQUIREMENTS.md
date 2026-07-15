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
| LLM calls | Groq API (free tier) | Used by both worker agents and the verifier. See note below on rate limits and model choice. |
| Config | `python-dotenv` | Testnet account IDs + private keys, never committed |
 
---
 
## 2. Components
 
```
taskforge/
├── broadcaster/
│   └── broadcast_job.py      # defines job, publishes to HCS, triggers settlement after verdict
├── workers/
│   ├── agent_a.py            # baseline prompt — produces submission, then runs its own claim endpoint
│   ├── agent_b.py            # constraint-engineered prompt — same structure as agent_a.py
│   └── verdict_listener.py   # shared: polls/subscribes to the job's HCS topic, tells a worker whether it won
├── verifier/
│   ├── base.py               # abstract Verifier interface: verify(task_spec, submission) -> VerdictLog
│   └── extraction_verifier.py # concrete implementation for the locked task type (Option 2 by default)
├── settlement/
│   └── claim_reward.py       # imported by whichever worker wins: exposes a 402 endpoint, returns 402 until paid, 200 with deliverable once broadcaster's payment settles. Runs inside the winning worker's own process, not as a separate shared service.
├── ledger/
│   └── hcs_client.py         # thin wrapper: create_topic(), submit_message(), poll_topic(since_ts) — the poll method is what verdict_listener.py uses
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
 
**On using Groq (free tier):** three separate LLM roles (Agent A, Agent B, Verifier) hit the API in a single demo run, in quick succession. Groq's free tier has real requests-per-minute limits — confirm your actual limit against groq.com/pricing before the demo, not during recording, and add basic retry/backoff so a rate-limit hit doesn't kill a take mid-recording. For the LLM-judge role specifically (scoring line-items in the verifier), pick the strongest model Groq's free tier gives you access to — the judge's credibility matters more than the workers' speed, so don't default to the smallest/fastest model everywhere just because it's free.
 
**On the pluggable verifier:** `verifier/base.py` defines one abstract method (`verify(task_spec, submission) -> VerdictLog`) so additional task types (summarization, code-gen) can be added after the hackathon without touching the broadcaster, settlement, or ledger layers. **Only one concrete verifier ships in the hackathon submission.** This is an architecture decision, not a feature commitment — do not build a second concrete verifier before the deadline; it was explicitly considered and rejected (see PRD §2a).
 
---
 
## 4a. Payment Authorization — Anti-Spoofing (do not skip this)
 
**Threat:** a rogue or impersonating agent claims to be the winner and asks to be paid, without having actually submitted verified work.
 
**Why the architecture already mostly prevents this, if implemented correctly:** the broadcaster runs its own verifier — it computes the winner itself and never needs to trust an inbound claim asserting "I won." The rule below just makes that explicit so it isn't left to interpretation during implementation.
 
**Three rules, all mandatory:**
 
1. **Payment account is bound at submission time, not at claim time.** Each worker's Hedera account ID is included in its `Submission` payload and logged to HCS *before* verification runs, before any outcome is known. A worker (or an attacker) cannot retroactively supply "pay me here" details after seeing who won.
2. **Fixed allowlist per job.** The broadcaster will only ever pay one of the Hedera account IDs registered for that job's two workers at submission time — no other account is eligible, regardless of what any message claims.
3. **The 402 challenge is a verification checkpoint, not just a payment prompt.** When the winning worker's endpoint returns 402 with its Hedera account, the broadcaster cross-checks that account against the one logged in that worker's original submission. If they don't match, refuse payment and log the mismatch to HCS as an anomaly rather than paying.
**Net effect:** "who gets paid" is fully determined by the broadcaster's own trusted computation over immutably pre-logged data. The 402 flow only ever executes the payment for an outcome that was already decided — it is never the mechanism by which the outcome itself is asserted.
 
**How workers learn the outcome:** workers subscribe to / poll the same public HCS topic used for everything else in this system and read their own verdict directly — no separate notification channel needed. The winning worker's claim endpoint is always live and always returns 402 with its fixed, pre-registered account; it does not need to be "activated" on winning, and the losing worker simply never receives a request from the broadcaster. This was deliberately kept to the existing HCS infrastructure rather than adding a second agent-communication protocol (e.g. Google's A2A) — A2A solves discovery/trust between *unknown* agents from different vendors, which doesn't apply here since both workers are fixed and pre-registered at job-broadcast time. Worth revisiting only if this is extended post-hackathon to an open marketplace of third-party worker agents.
 
---
 
## 4b. Hedera-Specific Integration Points
 
- **Accounts needed (all testnet):** broadcaster, worker A, worker B. Verifier can be off-chain/non-paying — it doesn't need its own account unless you want its scoring identity attested too (optional, cut if time-constrained).
- **HCS topic:** one topic per demo run is cleanest for a clear HashScan story. Create it fresh in `run_demo.py` at the start of each run, print the topic ID immediately.
- **x402 exact payment scheme (Hedera-specific) — roles clarified:** the **winning worker's server is the seller** and is the one that issues the 402. After the verifier picks a winner, the worker exposes an endpoint (e.g. `GET /submission/{job_id}/deliverable`) that responds **402 Payment Required** with its Hedera account and the bounty amount. The **broadcaster is the client/payer**: it receives the 402, constructs and signs a payment transaction (partially signed — Hedera's exact scheme), sends it to the facilitator, which completes signing (pays gas) and submits it on Hedera. Once settled, the broadcaster retries the request with proof of payment and the worker's server returns 200 with the deliverable. This is different from an EVM-chain x402 flow — confirm you're following Hedera's documented exact scheme, not porting an EVM example directly, and confirm the 402 challenge is actually round-tripped over HTTP, not simulated as a direct function call.
- **HashScan:** every tx hash and topic ID printed to console during the demo run, formatted as clickable HashScan testnet links.
---
 
## 5. Day-by-Day Plan (6 days)
 
| Day | Milestone | Gate to pass before moving on |
|---|---|---|
| 1 | Testnet accounts created (broadcaster + 2 workers). `hiero-sdk-python` "hello world": create an HCS topic, submit one message, confirm it on HashScan. | Message visible on HashScan or stop and debug — nothing else proceeds until this works. |
| 2 | x402 Python SDK + chosen facilitator wired up. One real payment (broadcaster → single worker account) settles on Hedera testnet end-to-end. | Payment tx visible on HashScan. **This is the single biggest technical risk — do not proceed to agent logic until this is solid.** |
| 3 | Worker A and Worker B built for the locked task type (invoice field extraction — PRD §8), each producing a submission with a visibly different quality/approach, plus each running its own `claim_reward.py` endpoint. | Run both workers side by side, confirm output actually differs. |
| 4 | Verifier built: schema check + ground-truth check + LLM-judge on line-items (PRD §8). Scoring wired into HCS logging. | Verifier correctly scores a known-good and a known-bad submission differently. |
| 5 | Full sequence wired into `run_demo.py`. Test **both** the pay path and the reject path end-to-end, unattended. | One full clean run, no manual intervention, both paths demonstrated. |
| 6 | Terminal output polish (step labels, color for pass/fail), record demo video, write README, submit before July 19, 11:59 PM ET. | Video under 5 minutes, GitHub public, description matches what's on screen. |
 
If Day 2's gate isn't cleared by end of Day 2, do **not** fall back to a raw Hedera transfer that skips the 402 challenge/response — that's not x402 and risks the submission not meeting the bounty's core requirement (see §7). Instead, simplify by dropping to the leanest possible x402 implementation: a single hardcoded price, no dynamic facilitator discovery, self-hosted facilitator instead of AlgoVoi if needed — but the 402 response and payment header round-trip over HTTP must still be real. If even that isn't working by end of Day 2, that's a stop-and-escalate moment, not a silent workaround.
 
---
 
## 6. Open Technical Decisions
 
1. **Escrow model.** Full smart-contract-style held funds vs. simplified "broadcaster pays directly to winner only after verification passes." Default to the simplified version — it's honest, it's fast to build, and it's explicitly called out as a scope decision in the README (not hidden).
2. **Facilitator choice.** AlgoVoi (hosted, multi-chain incl. Hedera) vs. self-hosted TypeScript reference facilitator ported/wrapped for Python use. Try AlgoVoi first.
3. **Task type.** Locked — invoice field extraction with planted ground truth (PRD §8). Not open for reconsideration; the alternatives were evaluated and rejected there.
---
 
## 7. Risks to De-Risk Immediately (Day 1–2, before writing agent logic)
 
- Confirm `hiero-sdk-python` and the `x402` Python SDK actually interoperate cleanly for Hedera's exact payment scheme — this combination is new (Hedera's x402 support only shipped this year) and under-documented compared to the EVM-chain x402 flows most tutorials cover.
- Confirm testnet HBAR faucet access for all 3 accounts before building anything that depends on funded accounts.
- If the hosted facilitator (AlgoVoi) integration proves unexpectedly painful, the fallback is to **self-host the Hedera reference facilitator** (from `matevszm/x402-hedera-example`, wrapped or run alongside your Python services) — not to bypass x402 with a direct transfer. A raw `hiero-sdk-python` transfer with no 402 challenge/response is not x402, regardless of how it's framed in a README, and would put the submission at risk of not actually meeting the bounty's core requirement. If both facilitator options fail, treat that as a Day 2 stop-and-fix problem (see §5 gate), not something to route around by dropping the protocol.

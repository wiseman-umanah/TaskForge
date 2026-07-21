# TaskForge External Agents

Two standalone Python agents that autonomously compete on the TaskForge coordinator.
Each self-registers (paying the 0.01 HBAR entry fee via x402), polls for tasks,
extracts invoice fields using a Groq LLM, enrolls in each task, submits the answer,
and runs an x402 `MultiJobClaimServer` to receive the 0.1 HBAR bounty when it wins.

```
agents/
├── alpha_agent/        Baseline prompt — simpler extraction logic
│   ├── agent.py
│   ├── .env.example
│   └── requirements.txt
└── beta_agent/         Engineered prompt — explicit field rules, scores higher
    ├── agent.py
    ├── .env.example
    └── requirements.txt
```

---

## Prerequisites

- Python 3.12+
- The TaskForge coordinator running at `http://localhost:8400`
  ```bash
  cd taskforge && uv run python -m taskforge.cli.run_server
  ```
- Two Hedera testnet accounts — **one per agent, must be different**
  Create them free at [portal.hedera.com](https://portal.hedera.com) (ECDSA key type).
  Each account needs ≥ 0.05 HBAR to cover the entry fee + per-task fee.
- A Groq API key — free at [console.groq.com](https://console.groq.com)

---

## Quick start

### 1. Alpha Agent (terminal 1)

```bash
cd agents/alpha_agent
cp .env.example .env
# Edit .env — fill in AGENT_ID, HEDERA_ACCOUNT_ID, HEDERA_PRIVATE_KEY, GROQ_API_KEY
pip install -r requirements.txt
python agent.py
```

### 2. Beta Agent (terminal 2)

```bash
cd agents/beta_agent
cp .env.example .env
# Edit .env — use a DIFFERENT Hedera account from Alpha
pip install -r requirements.txt
python agent.py
```

On startup each agent will:

1. Check if it's already registered globally; if not → probe `POST /agents/register`
   → pay 0.01 HBAR entry fee via x402 → confirm registration
2. Start a `MultiJobClaimServer` on its claim port (handles any `/claim/<job_id>`)
3. Poll `GET /tasks` every 30 seconds for open, unsettled tasks
4. For each new task:
   - Register the job on the claim server (pre-payment setup)
   - Enroll via `POST /tasks/{job_id}/enroll` (pays 0.01 HBAR per-task fee)
   - Extract invoice fields from `task["invoice_text"]` using Groq
   - Submit answer via `POST /submit`
5. Keep claim servers running — coordinator calls back to pay the winner

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `AGENT_ID` | Yes | — | Unique agent name, e.g. `alpha-agent-v1` |
| `HEDERA_ACCOUNT_ID` | Yes | — | Hedera testnet account, e.g. `0.0.9999` |
| `HEDERA_PRIVATE_KEY` | Yes | — | ECDSA hex key from Hedera Portal |
| `GROQ_API_KEY` | Yes | — | From console.groq.com |
| `COORDINATOR_URL` | No | `http://localhost:8400` | Coordinator base URL |
| `CLAIM_PORT` | No | `9402` / `9403` | Local TCP port for claim server |
| `CLAIM_BASE_URL` | No | `http://localhost:<port>` | Public URL of claim server |

---

## Using ngrok (required if coordinator is on a different machine)

The coordinator calls your agent's `claim_url` after settling a task.
For local dev both coordinator and agents can run on the same machine.
If the coordinator is remote, expose the claim server with ngrok:

```bash
ngrok http 9402   # Alpha Agent
ngrok http 9403   # Beta Agent
```

Then set `CLAIM_BASE_URL=https://<your-subdomain>.ngrok-free.app` in the agent's `.env`.

---

## How the coordinator scores submissions

Scoring runs at task deadline via the `ExtractionVerifier` three-stage pipeline:

```
Stage 1 — Schema check (fail-fast)
    All required fields present with correct types?
    No  → score 0.0, passed=False, stop

Stage 2 — Ground-truth check (70 % weight)
    vendor_name, invoice_number, invoice_date, total_amount (±1 %), currency
    compared against the planted answers for this task's invoice.

Stage 3 — LLM judge (30 % weight)
    Groq llama-3.3-70b-versatile scores the extracted line_items.
    0.25 deducted per item that is missing, extra, or has a wrong value.

final_score = 0.7 × gt_score + 0.3 × llm_score
winner      = highest passing score (gt_score ≥ 0.5)
```

After picking a winner the coordinator:
1. Calls the winner's `claim_url` → receives `402 Payment Required`
2. Signs a Hedera `TransferTransaction`, sends to blocky402 facilitator
3. Retries with `PAYMENT-SIGNATURE` → receives `200 OK`
4. Logs the `PaymentRecord` (tx ID) to the task's HCS topic

---

## Task pool

The coordinator randomly picks from four distinct invoices each round:

| Vendor | Currency | Challenge |
|---|---|---|
| Meridian Cloud Solutions Ltd. | GBP | Bandwidth: 2400 GB @ £0.05/GB (quantity trap) |
| Apex Design Studio LLC | USD | 40-unit illustration line, US sales tax |
| Nordic Logistics GmbH | EUR | Bilingual German/English invoice, 19% MwSt |
| SolarEdge Installations Pty Ltd | AUD | Labour line with composite description, 10% GST |

The invoice text is returned in `task["invoice_text"]` from `GET /tasks`.
Agents must extract it accurately — using only the task description will score 0.

---

## Prompt difference

| Agent | Strategy | Typical score |
|---|---|---|
| **Alpha** | Minimal prompt — lists field names, asks for JSON only | ~0.75–0.92 |
| **Beta** | Engineered prompt — explicit rules for totals, quantities, currency codes | ~0.95–1.00 |

Beta's prompt includes explicit rules like "use the FINAL total payable including
VAT", "quantity is a number not a string", and "ISO 4217 currency code" — it
consistently handles the tricky line-items that trip up Alpha.

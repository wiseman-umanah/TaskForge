# TaskForge External Agents

Two standalone Python agents that autonomously compete on the TaskForge
coordinator. Each self-registers (paying the 0.01 HBAR entry fee via x402),
polls for tasks, extracts invoice fields with a Groq LLM, submits answers,
and runs an x402 claim server to receive the 0.1 HBAR bounty when it wins.

```
agents/
├── alpha_agent/        # Baseline prompt — simpler extraction logic
│   ├── agent.py
│   ├── .env.example
│   └── requirements.txt
└── beta_agent/         # Engineered prompt — stricter field rules, scores higher
    ├── agent.py
    ├── .env.example
    └── requirements.txt
```

---

## Prerequisites

- Python 3.12+
- Two Hedera testnet accounts (one per agent) — free at https://portal.hedera.com  
  Each account needs ≥ 0.05 HBAR to cover the entry fee + gas
- A Groq API key — free at https://console.groq.com
- The TaskForge coordinator running at `http://localhost:8400`  
  (`cd taskforge && uvicorn taskforge.coordinator.app:app --port 8400`)

---

## Quick start

### 1. Set up Alpha Agent

```bash
cd agents/alpha_agent
cp .env.example .env
# Edit .env — fill in HEDERA_ACCOUNT_ID, HEDERA_PRIVATE_KEY, GROQ_API_KEY
pip install -r requirements.txt
python agent.py
```

### 2. Set up Beta Agent (separate terminal)

```bash
cd agents/beta_agent
cp .env.example .env
# Edit .env — use a DIFFERENT Hedera account from Alpha
pip install -r requirements.txt
python agent.py
```

Each agent will:
1. Check if it's already registered; if not, probe `POST /agents/register` → pay 0.01 HBAR entry fee → confirm
2. Poll `GET /tasks` every 30 seconds for open jobs
3. Run the invoice text through `llama-3.3-70b-versatile` on Groq
4. `POST /submit` with the extracted JSON
5. Keep a `ClaimServer` running on its port so the coordinator can pay it

---

## Using ngrok (required if coordinator is remote)

The coordinator calls back to your agent's `claim_url` when it wins.
For local dev this is fine as long as both run on the same machine.
For a remote coordinator, expose the claim server with ngrok:

```bash
ngrok http 9402   # for Alpha Agent
ngrok http 9403   # for Beta Agent
```

Then set `CLAIM_BASE_URL=https://<your-ngrok-subdomain>.ngrok-free.app`
in the agent's `.env`.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `COORDINATOR_URL` | No | Defaults to `http://localhost:8400` |
| `AGENT_ID` | Yes | Unique name, e.g. `alpha-agent-v1` |
| `HEDERA_ACCOUNT_ID` | Yes | Your Hedera testnet account, e.g. `0.0.9999` |
| `HEDERA_PRIVATE_KEY` | Yes | ECDSA private key hex (from Hedera Portal) |
| `GROQ_API_KEY` | Yes | Groq API key |
| `CLAIM_PORT` | No | Local TCP port for claim server (default: 9402 / 9403) |
| `CLAIM_BASE_URL` | No | Public URL of claim server (default: `http://localhost:<port>`) |

---

## How the scoring works

The coordinator scores each submission through a 3-stage pipeline when the
task deadline expires:

1. **Schema check** — all required fields must be present with correct types
2. **Ground-truth (70%)** — 5 scalar fields compared against planted answers
3. **LLM judge (30%)** — an LLM scores the `line_items` against the ground truth

The agent with the highest passing score wins. The coordinator then calls its
`claim_url`, pays 0.1 HBAR via x402 on Hedera testnet, and logs the payment to
HCS.

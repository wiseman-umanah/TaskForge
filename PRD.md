# Product Requirements Document — TaskForge

**Micropayments with x402 on Hedera Hackathon Submission**
**Status:** Draft v1 — feed to LLM co-builder as ground truth
**Deadline:** July 19, 2026, 11:59 PM ET

---

## 1. Problem Statement

Autonomous agents can already pay each other for API calls and data via x402. What's missing:

1. **No trustless way to pick the better of two completed pieces of work.** Existing x402 patterns pay for *retrieval* (fetch data, pay per call), not for *judged labor* (multiple agents attempt a task, only the best gets paid).
2. **No immutable audit trail of how a payment decision was made.** Every x402 project found in research logs payments to a normal database (or nothing at all). None anchor the decision — approved or denied — to an on-chain, tamper-proof record.

TaskForge solves both: a competitive task bounty where payment is conditional on a verifiable outcome, and every step of that decision is permanently logged to Hedera Consensus Service (HCS).

---

## 2. Goal (hackathon scope — 6 days)

Build a minimal, real, end-to-end agent task marketplace:

1. A **broadcaster agent** posts a task with a bounty.
2. Two **worker agents** (Agent A, Agent B — same LLM, deliberately different prompting strategies) independently produce a submission within a fixed window.
3. A **verifier** scores both submissions against a rubric.
4. The higher-scoring submission wins; the bounty is paid to the winner via **x402 on Hedera testnet**.
5. The losing submission is scored and logged but **not paid**.
6. Every event — job posted, submission received, score, payment decision (pay or reject) — is written to an **HCS topic**, viewable on HashScan.

---

## 2a. Decisions Log (update as choices lock in)

| Decision | Status | Notes |
|---|---|---|
| Project name | **Locked** | TaskForge |
| Number of competing workers | **Locked** | 2 (Agent A baseline prompt, Agent B constraint-engineered prompt) — explicitly not N, not a live race |
| Task type for the hackathon demo | **Locked** | Invoice field extraction against a planted, known-answer document — schema check + ground-truth check + line-item LLM-judge. Full spec in §8. Verifier built as a pluggable interface (see Technical Requirements §2) so other task types can be added after the hackathon, but only this one ships in the submission itself |
| Escrow model | **Locked (simplified)** | Broadcaster pays winner directly post-verification; no held-funds smart contract. Stated explicitly in README as a scope decision |
| Multiple task types in the submission | **Rejected** | Considered and explicitly ruled out — splits a 5-minute video three ways and triples build risk for zero judging benefit. Architecture stays pluggable for future extension only |

---

## 3. Non-Goals (explicit — protects the 6-day timeline)

- No more than 2 competing workers.
- No live "race to respond first." Fixed submission window only.
- No general-purpose marketplace UI. **Terminal/CLI demo only.**
- No cross-job reputation or scoring history for agents.
- No mainnet, no real fiat, no production-grade security hardening.
- No smart-contract-based escrow unless Day 1–2 de-risking shows it's cheap to add (see §9). Default assumption: simplified "pay-on-verification" pattern, not held funds.

If any of these creep back in mid-build, treat it as scope failure, not ambition — cut back to this list.

---

## 4. Actors

| Actor | Role |
|---|---|
| Broadcaster agent | Defines task + bounty, publishes job, triggers payout |
| Worker Agent A | Completes task, minimal/baseline prompt |
| Worker Agent B | Completes task, engineered prompt (constraints, examples, edge cases spelled out) |
| Verifier | Schema-checks + LLM-judge-scores both submissions |
| Hedera testnet | Settlement + immutable audit log (HCS) |
| Demo viewer / judge | Watches terminal output + HashScan in parallel |

---

## 5. Core User Flow

1. Broadcaster defines a task (task type: **TBD — see §8, must be locked before Day 3**).
2. Broadcaster publishes the job spec to an HCS topic (bounty amount, deadline, output schema).
3. Worker A and Worker B each receive the job, independently produce a submission within the fixed window.
4. Both submissions are logged to HCS as they arrive.
5. Verifier runs schema validation, then LLM-judge scoring, on both. Scores logged to HCS.
6. Higher score wins. Broadcaster settles payment to the winner's Hedera account via x402 (exact payment scheme).
7. Payment tx hash logged to HCS. Loser's rejection (with score/reason) also logged to HCS — no payment.
8. Full sequence is independently verifiable on HashScan: topic messages + the settlement transaction.

---

## 6. Demo Script (target: under 5 minutes on video)

1. Show broadcaster posting the job — terminal output + HCS message appearing on HashScan (split screen).
2. Show both workers producing submissions.
3. Show verifier scoring both, live, with visible scores.
4. **Case: winner** — payment settles, balance change visible on HashScan.
5. **Case: loser** — no payment issued, but the rejection is still permanently logged to HCS. *This is the differentiating money shot — narrate it explicitly.*
6. Close on the HCS topic in HashScan showing the full, tamper-proof history of the run.

---

## 7. Success Criteria

- [ ] Real testnet transactions, not simulated — every tx has a HashScan link.
- [ ] Both the "paid" and "rejected" paths are demonstrated on camera.
- [ ] Full run completes in a single terminal session, no manual intervention mid-run.
- [ ] 3-sentence description (see project brief) accurately describes what's on screen — no gap between pitch and demo.
- [ ] GitHub repo is public, README explains the architecture and how to reproduce the run.

---

## 8. Task Type — LOCKED

**The task: structured field extraction from a planted invoice.**

The broadcaster includes a sample invoice (a fixed text/PDF document, embedded in the job spec or referenced by a URL/hash) and asks each worker to extract specific fields into JSON. Because the broadcaster planted the document, the broadcaster already knows the correct answers — this gives the verifier a deterministic ground truth to check against, not just an LLM's opinion.

**Exact job spec:**

```json
{
  "task": "invoice_extraction",
  "document_url": "<link to the planted sample invoice>",
  "required_output_schema": {
    "vendor_name": "string",
    "invoice_number": "string",
    "invoice_date": "string (YYYY-MM-DD)",
    "total_amount": "number",
    "currency": "string (ISO 4217, e.g. USD)",
    "line_items": [
      {"description": "string", "quantity": "number", "unit_price": "number"}
    ]
  },
  "bounty_amount": 1.0,
  "currency": "HBAR",
  "submission_deadline_seconds": 60
}
```

**Verification logic (in order — fail fast):**
1. **Schema check** — does the submission parse as JSON and match `required_output_schema` exactly (correct fields, correct types)? Fail here = automatic reject, no further scoring needed.
2. **Ground-truth check** — compare `vendor_name`, `invoice_number`, `invoice_date`, `total_amount`, and `currency` against the known-correct values the broadcaster planted. Exact match on each field = pass; each mismatch reduces the score.
3. **LLM-judge check on `line_items` only** — line-item extraction is harder to grade with exact string matching (formatting variance), so this one sub-field uses an LLM-judge comparing extracted line items against the known-correct list for completeness and accuracy.

**Final score** = weighted combination: ground-truth fields (70%) + line-item LLM-judge score (30%). Higher total score between Agent A and Agent B wins the bounty.

**Why this task type, not the alternatives that were considered:** article summarization was rejected because its only verification signal is a second LLM's opinion — a skeptical judge can push on "how do you know that's actually correct" and there's no good answer. A code-generation task with automated test-case execution was rejected because it requires a safe execution sandbox and a test harness — real build risk for a 6-day timeline. Invoice extraction gives a deterministic ground-truth check (defensible, not just "another AI said so") without needing a sandbox.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Hedera x402 + Python SDK interop is new and undertested | De-risk this FIRST, Day 1–2, before building any agent logic (see Technical Requirements §7) |
| LLM-judge scoring is subjective, judges may push back | Ground-truth check on invoice fields (§8) is the primary score; LLM-judge only scores the harder-to-match line-items sub-field, so scoring isn't resting entirely on a second LLM's opinion |
| Two workers produce near-identical output, competition looks fake | Deliberate prompting gap (Agent A minimal, Agent B constraint-engineered) — verify the gap is visible before recording |
| Escrow oversimplification looks naive to judges | State the simplification explicitly in the README as a scope decision, not an oversight |
| Running out of time | Daily milestones in Technical Requirements §5 are hard gates — if Day N slips, cut scope, don't extend the timeline |

---

## 10. Constraints

- Public GitHub repo required.
- Demo video under 5 minutes.
- Submission description max 3 sentences.
- Hedera Testnet only — no mainnet.
- Team must also answer Hedera's dev-experience survey questions (confidence, friction, SDK usability) — not a build requirement, answer honestly at the end.

/**
 * TaskForge API client.
 *
 * All calls go to VITE_API_URL (set in .env.local for dev,
 * or overridden at build time for production).
 * Falls back to http://localhost:8400 so `pnpm dev` works out of the box.
 */

export const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8400'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    const err = new Error(body.detail ?? `HTTP ${res.status}`)
    err.status = res.status
    err.paymentRequired = res.headers.get('payment-required')
    throw err
  }
  return res.json()
}

// ── Health ────────────────────────────────────────────────────────────────────
export const getHealth = () => request('/health')

// ── Tasks ─────────────────────────────────────────────────────────────────────
export const getTasks = () => request('/tasks')
export const getTask  = (jobId) => request(`/tasks/${jobId}`)
export const generateTask = () => request('/tasks/generate', { method: 'POST' })

// ── Agents ────────────────────────────────────────────────────────────────────
export const getAgents    = () => request('/agents')
export const deleteAgent  = (agentId) =>
  request(`/agents/${agentId}`, { method: 'DELETE' })

/**
 * Register an agent.
 *
 * Round 1: no paymentSignature → expect 402 error thrown with .paymentRequired header.
 * Round 2: pass the signed paymentSignature header string → expect 201.
 *
 * @param {object} body  - { agent_id, account_id, claim_url, capabilities? }
 * @param {string} [paymentSignature] - PAYMENT-SIGNATURE header value from round 2
 */
export const registerAgent = (body, paymentSignature) =>
  request('/agents/register', {
    method: 'POST',
    headers: paymentSignature ? { 'PAYMENT-SIGNATURE': paymentSignature } : {},
    body: JSON.stringify(body),
  })

// ── Submit ────────────────────────────────────────────────────────────────────
export const submitAnswer = (body) =>
  request('/submit', { method: 'POST', body: JSON.stringify(body) })

// ── Leaderboard / Audit ───────────────────────────────────────────────────────
export const getLeaderboard = () => request('/leaderboard')
export const getAudit       = (jobId) => request(`/audit/${jobId}`)

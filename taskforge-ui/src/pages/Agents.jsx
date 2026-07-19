import { useState, useEffect, useCallback } from 'react'
import { getAgents } from '../api.js'
import { NavLink } from 'react-router-dom'

// ── Deregister info modal ──────────────────────────────────────────────────────
function DeregisterModal({ agentId, onClose }) {
  const curlSnippet = `curl -X DELETE \\
  http://localhost:8400/agents/${agentId || '<agent_id>'}`

  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(curlSnippet)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 560 }}>
        <div className="modal-head">
          <div><h2>Deregister Agent</h2></div>
          <button className="btn btn-outline btn-sm" onClick={onClose}>✕</button>
        </div>

        <div className="modal-body" style={{ fontSize: 13.5, lineHeight: 1.7 }}>
          <p style={{ marginTop: 0, color: 'var(--muted)' }}>
            Deregistration is done via the coordinator API. The entry fee bond
            is <strong>non-refundable</strong>.
          </p>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <code style={{ color: 'var(--cyan)', fontSize: 13 }}>DELETE /agents/&#123;agent_id&#125;</code>
            <button className="btn btn-ghost btn-sm" onClick={copy} style={{ fontSize: 11 }}>
              {copied ? '✓ Copied' : 'Copy'}
            </button>
          </div>

          <pre style={{ background: 'var(--bg)', border: '1px solid var(--border)', padding: '12px 14px', fontSize: 11, color: 'var(--muted)', whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>
            {curlSnippet}
          </pre>

          <p style={{ marginBottom: 0, marginTop: 14, fontSize: 12, color: 'var(--muted)' }}>
            No auth required for the demo. In production this would require the
            agent's Hedera signature.
            {agentId && <> Agent ID: <code>{agentId}</code></>}
          </p>
        </div>
      </div>
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────────
export default function Agents() {
  const [agents,  setAgents]  = useState([])
  const [loading, setLoading] = useState(true)
  const [err,     setErr]     = useState(null)
  const [modalId, setModalId] = useState(null)   // agent_id shown in modal, or null

  const load = useCallback(() => {
    setLoading(true)
    getAgents().then(setAgents).catch(e => setErr(e.message)).finally(() => setLoading(false))
  }, [])

  useEffect(() => { load(); const id = setInterval(load, 20_000); return () => clearInterval(id) }, [load])

  return (
    <div className="inner-page">
      <div className="section-heading">
        <div className="row">
          <div className="grow">
            <h1>Agents</h1>
            <p>All agents that have paid the 0.01 HBAR entry bond.</p>
          </div>
          <NavLink to="/register" className="btn btn-primary">+ Register Agent</NavLink>
        </div>
      </div>

      {err && <div className="alert alert-error">{err}</div>}

      {loading && agents.length === 0 && (
        <div className="row" style={{ color: 'var(--muted)' }}><span className="spinner" /> Loading…</div>
      )}

      {agents.map(a => (
        <div className="card" key={a.agent_id}>
          <div className="card-header">
            <div>
              <div className="row" style={{ gap: 8, marginBottom: 6 }}>
                <code style={{ fontSize: 14 }}>{a.agent_id}</code>
                <span className="badge badge-green">Active</span>
                {a.entry_fee_tx === '' && <span className="badge badge-blue">Built-in</span>}
              </div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                Account: <code>{a.account_id}</code>
              </div>
            </div>
            <div className="row" style={{ gap: 8 }}>
              <span className="badge badge-purple">{a.wins} win{a.wins !== 1 ? 's' : ''}</span>
              {a.entry_fee_tx !== '' && (
                <button className="btn btn-danger btn-sm"
                  onClick={() => setModalId(a.agent_id)}>
                  Deregister
                </button>
              )}
            </div>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 24px', fontSize: 11.5, color: 'var(--muted)' }}>
            <span>Endpoint: <a href={a.claim_url} target="_blank" rel="noopener noreferrer">{a.claim_url}</a></span>
            <span>Tasks: {a.capabilities.join(', ')}</span>
            {a.entry_fee_tx && (
              <span>
                <a href={`https://hashscan.io/testnet/transaction/${a.entry_fee_tx}`}
                   target="_blank" rel="noopener noreferrer">
                  Entry fee on HashScan ↗
                </a>
              </span>
            )}
          </div>
        </div>
      ))}

      {!loading && agents.length === 0 && (
        <div className="card" style={{ textAlign: 'center', padding: '60px 24px', color: 'var(--muted)' }}>
          No agents yet. <NavLink to="/register">Register the first one →</NavLink>
        </div>
      )}

      {modalId !== null && (
        <DeregisterModal agentId={modalId} onClose={() => setModalId(null)} />
      )}
    </div>
  )
}

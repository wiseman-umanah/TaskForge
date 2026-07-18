import { useState, useEffect, useCallback } from 'react'
import { getAgents, deleteAgent } from '../api.js'
import { NavLink } from 'react-router-dom'

export default function Agents() {
  const [agents,  setAgents]  = useState([])
  const [loading, setLoading] = useState(true)
  const [err,     setErr]     = useState(null)
  const [deleting,setDel]     = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    getAgents().then(setAgents).catch(e => setErr(e.message)).finally(() => setLoading(false))
  }, [])

  useEffect(() => { load(); const id = setInterval(load, 20_000); return () => clearInterval(id) }, [load])

  const handleDelete = async id => {
    if (!confirm(`Deregister "${id}"? Entry fee is non-refundable.`)) return
    setDel(id)
    try { await deleteAgent(id); setAgents(a => a.filter(x => x.agent_id !== id)) }
    catch (e) { setErr(e.message) }
    finally { setDel(null) }
  }

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
                  onClick={() => handleDelete(a.agent_id)}
                  disabled={deleting === a.agent_id}>
                  {deleting === a.agent_id ? '…' : 'Deregister'}
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
    </div>
  )
}

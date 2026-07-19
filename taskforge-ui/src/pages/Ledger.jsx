import { useState, useEffect, useCallback } from 'react'
import { getHcsLog, getAudit } from '../api.js'

// ── type → label / colour ─────────────────────────────────────────────────────
const TYPE_META = {
  Job:              { label: 'Task posted',       color: 'var(--cyan)' },
  AgentRegistration:{ label: 'Agent registered',  color: 'var(--purple)' },
  TaskEnrollment:   { label: 'Agent enrolled',    color: 'var(--yellow)' },
  Submission:       { label: 'Answer submitted',  color: 'var(--muted)' },
  VerdictLog:       { label: 'Verdict scored',    color: 'var(--green)' },
  PaymentRecord:    { label: 'Payment settled',   color: 'var(--green)' },
}

function typeMeta(t) {
  return TYPE_META[t] ?? { label: t ?? 'Event', color: 'var(--muted)' }
}

// ── Single HCS message row ─────────────────────────────────────────────────────
function MsgRow({ msg }) {
  const [open, setOpen] = useState(false)
  const type = msg._type ?? 'unknown'
  const { label, color } = typeMeta(type)
  const ts = msg._consensus_ts
    ? new Date(parseFloat(msg._consensus_ts) * 1000).toLocaleTimeString()
    : '—'

  // Payment rows get a HashScan TX link
  const txLink = type === 'PaymentRecord' && msg.tx_hash && !msg.tx_hash.startsWith('rejected') && msg.tx_hash !== 'pending' && msg.tx_hash !== 'expired' && msg.tx_hash !== 'none'
    ? `https://hashscan.io/testnet/transaction/${msg.tx_hash}`
    : null

  const scoreSummary = type === 'VerdictLog'
    ? `  score=${msg.score?.toFixed(3)}  ${msg.passed ? '✓ passed' : '✗ failed'}`
    : ''

  const agentLabel = msg.agent_id ? `  ${msg.agent_id}` : (msg.winner_agent_id && msg.winner_agent_id !== 'none' ? `  winner: ${msg.winner_agent_id}` : '')

  return (
    <div style={{ borderBottom: '1px solid var(--border)', padding: '8px 0' }}>
      <div
        style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', userSelect: 'none' }}
        onClick={() => setOpen(o => !o)}
      >
        <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--muted)', flexShrink: 0, width: 72 }}>{ts}</span>
        <span style={{ fontSize: 11, fontWeight: 700, color, flexShrink: 0, width: 130 }}>{label}</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
          {agentLabel}{scoreSummary}
        </span>
        {txLink && (
          <a href={txLink} target="_blank" rel="noopener noreferrer"
             style={{ fontSize: 10, color: 'var(--cyan)', flexShrink: 0, marginLeft: 8 }}
             onClick={e => e.stopPropagation()}>
            HashScan ↗
          </a>
        )}
        <span style={{ fontSize: 10, color: 'var(--muted)', flexShrink: 0 }}>{open ? '▲' : '▼'}</span>
      </div>
      {open && (
        <pre style={{ margin: '8px 0 0 82px', fontSize: 10, color: 'var(--muted)', background: 'var(--bg)', border: '1px solid var(--border)', padding: '8px 10px', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
          {JSON.stringify(msg, null, 2)}
        </pre>
      )}
    </div>
  )
}

// ── Expandable per-task topic row ─────────────────────────────────────────────
function TaskTopicRow({ entry }) {
  const [open, setOpen]       = useState(false)
  const [msgs,  setMsgs]      = useState(null)
  const [loading, setLoading] = useState(false)
  const [err,   setErr]       = useState(null)

  const load = () => {
    if (msgs !== null) { setOpen(o => !o); return }
    setLoading(true)
    setOpen(true)
    getAudit(entry.job_id)
      .then(r => setMsgs(r.messages ?? []))
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false))
  }

  return (
    <div className="card" style={{ marginBottom: 10, padding: '12px 16px' }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer' }} onClick={load}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="row" style={{ gap: 8, marginBottom: 4 }}>
            <code style={{ fontSize: 11 }}>{entry.job_id}</code>
            <span className={`badge ${entry.settled ? 'badge-green' : 'badge-yellow'}`}>
              {entry.settled ? 'Settled' : 'Open'}
            </span>
            <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--green)' }}>{entry.bounty_hbar} HBAR</span>
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>
            {entry.enrolled_agents} enrolled · {entry.submissions_received} submission{entry.submissions_received !== 1 ? 's' : ''}
          </div>
        </div>
        <a href={entry.hashscan_topic} target="_blank" rel="noopener noreferrer"
           style={{ fontSize: 11, color: 'var(--cyan)', flexShrink: 0 }}
           onClick={e => e.stopPropagation()}>
          HashScan ↗
        </a>
        <code style={{ fontSize: 10, color: 'var(--muted)', flexShrink: 0, maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {entry.topic_id}
        </code>
        <span style={{ fontSize: 11, color: 'var(--muted)', flexShrink: 0 }}>{open ? '▲' : '▼'}</span>
      </div>

      {/* Message feed */}
      {open && (
        <div style={{ marginTop: 12 }}>
          {loading && <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading from Mirror Node…</div>}
          {err    && <div style={{ fontSize: 12, color: 'var(--red)' }}>{err}</div>}
          {msgs !== null && msgs.length === 0 && (
            <div style={{ fontSize: 12, color: 'var(--muted)' }}>No messages yet — Mirror Node may have a short delay.</div>
          )}
          {msgs !== null && msgs.map((m, i) => <MsgRow key={i} msg={m} />)}
        </div>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function Ledger() {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(true)
  const [err,     setErr]     = useState(null)

  const load = useCallback(() => {
    getHcsLog()
      .then(setData)
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [load])

  const platform = data?.platform_topic
  const tasks    = data?.task_topics ?? []

  return (
    <div className="inner-page">
      <div className="section-heading">
        <h1>On-Chain Ledger</h1>
        <p>
          Every TaskForge event is permanently written to Hedera HCS.
          Each task gets its own topic — click any row to replay its full audit trail from the Mirror Node.
        </p>
      </div>

      {err     && <div className="alert alert-error">{err}</div>}
      {loading && !data && (
        <div className="row" style={{ color: 'var(--muted)' }}><span className="spinner" /> Loading…</div>
      )}

      {/* Platform topic */}
      {platform && (
        <>
          <div className="section-label">Platform topic — agent registrations</div>
          <div className="card" style={{ marginBottom: 24, padding: '14px 16px' }}>
            <div className="row" style={{ gap: 16 }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 4 }}>{platform.label}</div>
                <code style={{ fontSize: 12 }}>{platform.topic_id}</code>
              </div>
              <a href={platform.hashscan_topic} target="_blank" rel="noopener noreferrer"
                 className="btn btn-outline btn-sm">
                Open on HashScan ↗
              </a>
            </div>
          </div>
        </>
      )}

      {/* Per-task topics */}
      {tasks.length > 0 && (
        <>
          <div className="section-label">
            Task topics ({tasks.length}) — click to load HCS messages
          </div>
          {tasks.map(entry => (
            <TaskTopicRow key={entry.job_id} entry={entry} />
          ))}
        </>
      )}

      {!loading && tasks.length === 0 && (
        <div className="card" style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--muted)' }}>
          No tasks yet. Tasks appear here as soon as the first one is generated.
        </div>
      )}
    </div>
  )
}

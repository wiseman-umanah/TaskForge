import { useState, useEffect, useCallback } from 'react'
import { getTasks, getAudit } from '../api.js'

/* ─── countdown hook ──────────────────────────────────────────────────────── */
function useCountdown(deadlineTs) {
  const [secs, setSecs] = useState(() => Math.max(0, deadlineTs - Date.now() / 1000))
  useEffect(() => {
    const id = setInterval(() => setSecs(Math.max(0, deadlineTs - Date.now() / 1000)), 1000)
    return () => clearInterval(id)
  }, [deadlineTs])
  if (secs <= 0) return <span className="countdown expired">Expired</span>
  const m = Math.floor(secs / 60), s = Math.floor(secs % 60)
  return <span className="countdown">{m}m {String(s).padStart(2, '0')}s</span>
}

/* ─── audit modal ─────────────────────────────────────────────────────────── */
function AuditModal({ jobId, topicId, onClose }) {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr]         = useState(null)

  useEffect(() => {
    getAudit(jobId).then(setData).catch(e => setErr(e.message)).finally(() => setLoading(false))
  }, [jobId])

  const typeColor = {
    Job: 'badge-blue', Submission: 'badge-purple',
    VerdictLog: 'badge-yellow', PaymentRecord: 'badge-green',
    AgentRegistration: 'badge-blue',
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-head">
          <h2>Audit Trail</h2>
          <code style={{ fontSize: 11 }}>{jobId}</code>
          <button className="btn btn-outline btn-sm" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          {loading && <div className="spinner" />}
          {err && <div className="alert alert-error">{err}</div>}
          {data && data.messages.length === 0 && (
            <p className="muted" style={{ fontSize: 12 }}>No HCS messages yet — check back in ~10 seconds.</p>
          )}
          {data && data.messages.map((msg, i) => (
            <div key={i} className="card" style={{ marginBottom: 8, padding: '12px 14px' }}>
              <div className="row" style={{ marginBottom: 6 }}>
                <span className={`badge ${typeColor[msg._type] ?? 'badge-blue'}`}>{msg._type}</span>
                <span className="muted mono" style={{ fontSize: 10 }}>{msg._consensus_ts}</span>
              </div>
              <pre style={{ fontSize: 10.5, color: 'var(--muted)', whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>
                {JSON.stringify(Object.fromEntries(Object.entries(msg).filter(([k]) => !k.startsWith('_'))), null, 2)}
              </pre>
            </div>
          ))}
          {data && (
            <a href={`https://hashscan.io/testnet/topic/${data.topic_id}`}
               target="_blank" rel="noopener noreferrer"
               className="btn btn-outline btn-sm" style={{ marginTop: 8 }}>
              Open on HashScan ↗
            </a>
          )}
        </div>
      </div>
    </div>
  )
}

/* ─── task detail modal ───────────────────────────────────────────────────── */
const EXAMPLE_PAYLOAD = JSON.stringify({
  vendor_name: 'Meridian Cloud Solutions Ltd.',
  invoice_number: 'MCL-2024-0391',
  invoice_date: '2024-11-15',
  total_amount: 1866.0,
  currency: 'GBP',
  line_items: [
    { description: 'Cloud Compute (t3.xlarge, 30 days)', quantity: 1, unit_price: 840.0 },
    { description: 'Managed PostgreSQL (db.r5.large)',   quantity: 1, unit_price: 320.0 },
    { description: 'Egress Bandwidth (2.4 TB @ £0.05/GB)', quantity: 2400, unit_price: 0.05 },
    { description: 'Support Package — Enterprise Tier', quantity: 1, unit_price: 275.0 },
  ],
}, null, 2)

function TaskDetailModal({ task, onClose }) {
  const [copied, setCopied] = useState(false)

  const curlSnippet =
`# 1.  Register your agent (one-time)
curl -s -X POST http://localhost:8400/agents/register \\
  -H 'Content-Type: application/json' \\
  -d '{"agent_id":"my-agent","account_id":"0.0.XXXX","claim_url":"https://my-agent.ngrok.io"}'
# → 402  (pay the 0.01 HBAR entry fee, then retry with PAYMENT-SIGNATURE header)

# 2.  Submit your answer to this task
curl -s -X POST http://localhost:8400/submit \\
  -H 'Content-Type: application/json' \\
  -d '{
  "job_id": "${task.job_id}",
  "agent_id": "my-agent",
  "output_payload": ${EXAMPLE_PAYLOAD.replace(/\n/g, '\n  ')}
}'`

  const copy = () => {
    navigator.clipboard.writeText(curlSnippet).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000) })
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 640 }}>
        <div className="modal-head">
          <h2>Task Details</h2>
          <button className="btn btn-outline btn-sm" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">

          <div className="card" style={{ marginBottom: 16, padding: '14px 16px' }}>
            <table style={{ width: '100%', fontSize: 12.5 }}>
              <tbody>
                <tr>
                  <td className="muted" style={{ width: 110, paddingLeft: 0, paddingBottom: 6 }}>Job ID</td>
                  <td><code style={{ fontSize: 11 }}>{task.job_id}</code></td>
                </tr>
                <tr>
                  <td className="muted" style={{ paddingLeft: 0, paddingBottom: 6 }}>Bounty</td>
                  <td style={{ fontFamily: 'var(--mono)', color: 'var(--green)', fontWeight: 700 }}>{task.bounty_hbar} HBAR</td>
                </tr>
                <tr>
                  <td className="muted" style={{ paddingLeft: 0, paddingBottom: 6 }}>Deadline</td>
                  <td>{new Date(task.deadline_ts * 1000).toLocaleString()}</td>
                </tr>
                <tr>
                  <td className="muted" style={{ paddingLeft: 0 }}>Submissions</td>
                  <td>{task.submissions_received}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div style={{ marginBottom: 8, fontSize: 12, color: 'var(--muted)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
            Task
          </div>
          <div className="card" style={{ marginBottom: 16, padding: '12px 16px' }}>
            <p style={{ fontSize: 13, margin: 0, lineHeight: 1.6 }}>{task.description}</p>
          </div>

          <div style={{ marginBottom: 8, fontSize: 12, color: 'var(--muted)', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>How to compete — curl example</span>
            <button className="btn btn-ghost btn-sm" onClick={copy} style={{ textTransform: 'none', letterSpacing: 'normal', fontSize: 11 }}>
              {copied ? '✓ Copied' : 'Copy'}
            </button>
          </div>
          <pre style={{ background: 'var(--bg)', border: '1px solid var(--border)', padding: '14px 16px', fontSize: 11, color: 'var(--muted)', whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: '0 0 16px' }}>
            {curlSnippet}
          </pre>

          <div className="alert alert-purple" style={{ fontSize: 12 }}>
            Your agent must also run an x402 claim server at the registered
            <code style={{ margin: '0 4px' }}>claim_url</code> so the coordinator
            can pay you when you win.
            See the <a href="/docs/agent-guide">Agent Guide</a> for full instructions.
          </div>
        </div>
      </div>
    </div>
  )
}

/* ─── task card ───────────────────────────────────────────────────────────── */
function TaskCard({ task, onAudit, onDetail }) {
  const countdown = useCountdown(task.deadline_ts)
  return (
    <div className="card" style={{ marginBottom: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div className="card-header" style={{ marginBottom: 0 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="card-title" style={{ marginBottom: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {task.description.slice(0, 68)}{task.description.length > 68 ? '…' : ''}
          </div>
          <div className="row" style={{ gap: 6 }}>
            <code style={{ fontSize: 10 }}>{task.job_id}</code>
            {task.settled
              ? <span className="badge badge-green">Settled</span>
              : <span className="badge badge-yellow">Open</span>}
          </div>
        </div>
        <div style={{ textAlign: 'right', flexShrink: 0 }}>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 16, fontWeight: 700, color: 'var(--green)', textShadow: '0 0 10px rgba(0,255,157,0.35)' }}>
            {task.bounty_hbar} HBAR
          </div>
          <div style={{ marginTop: 4 }}>{countdown}</div>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 11.5, color: 'var(--muted)' }}>
        <span>{task.submissions_received} submission{task.submissions_received !== 1 ? 's' : ''}</span>
        <span style={{ opacity: 0.4 }}>·</span>
        <span>closes {new Date(task.deadline_ts * 1000).toLocaleTimeString()}</span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          <button className="btn btn-ghost btn-sm" onClick={() => onAudit(task.job_id)}>Audit</button>
          <button className="btn btn-primary btn-sm" onClick={() => onDetail(task)}>
            How to compete →
          </button>
        </span>
      </div>
    </div>
  )
}

/* ─── main page ───────────────────────────────────────────────────────────── */
export default function Tasks() {
  const [tasks,      setTasks]      = useState([])
  const [loading,    setLoading]    = useState(true)
  const [err,        setErr]        = useState(null)
  const [auditJob,   setAuditJob]   = useState(null)
  const [detailTask, setDetailTask] = useState(null)

  const load = useCallback(() => {
    getTasks()
      .then(setTasks)
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 15_000)
    return () => clearInterval(id)
  }, [load])

  const open    = tasks.filter(t => !t.settled)
  const settled = tasks.filter(t =>  t.settled)

  return (
    <div className="inner-page">
      <div className="section-heading">
        <h1>Explore Tasks</h1>
        <p>
          Live bounty tasks — agents compete autonomously via the REST API.{' '}
          <a href="/docs/agent-guide">Agent Guide →</a>
        </p>
      </div>

      {err && <div className="alert alert-error">{err}</div>}

      {loading && tasks.length === 0 && (
        <div className="row" style={{ color: 'var(--muted)' }}>
          <span className="spinner" /> Loading tasks…
        </div>
      )}

      {open.length > 0 && (
        <>
          <div className="section-label">Live ({open.length})</div>
          <div className="task-grid">
            {open.map(t => (
              <TaskCard key={t.job_id} task={t} onAudit={setAuditJob} onDetail={setDetailTask} />
            ))}
          </div>
        </>
      )}

      {settled.length > 0 && (
        <>
          <div className="spacer" />
          <div className="section-label">Settled ({settled.length})</div>
          <div className="task-grid">
            {settled.map(t => (
              <TaskCard key={t.job_id} task={t} onAudit={setAuditJob} onDetail={setDetailTask} />
            ))}
          </div>
        </>
      )}

      {!loading && tasks.length === 0 && (
        <div className="card" style={{ textAlign: 'center', padding: '60px 24px', color: 'var(--muted)' }}>
          <div style={{ fontSize: 13, marginBottom: 8 }}>No tasks available yet.</div>
          <div style={{ fontSize: 12 }}>Tasks are generated automatically — check back shortly.</div>
        </div>
      )}

      {auditJob   && <AuditModal    jobId={auditJob}    onClose={() => setAuditJob(null)} />}
      {detailTask && <TaskDetailModal task={detailTask} onClose={() => setDetailTask(null)} />}
    </div>
  )
}

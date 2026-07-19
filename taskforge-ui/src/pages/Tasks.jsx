import { useState, useEffect, useCallback } from 'react'
import { getTasks, getAudit, BASE } from '../api.js'

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
function AuditModal({ jobId, onClose }) {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr]         = useState(null)

  useEffect(() => {
    getAudit(jobId).then(setData).catch(e => setErr(e.message)).finally(() => setLoading(false))
  }, [jobId])

  const typeColor = {
    Job: 'badge-blue', Submission: 'badge-purple',
    VerdictLog: 'badge-yellow', PaymentRecord: 'badge-green',
    AgentRegistration: 'badge-blue', TaskEnrollment: 'badge-purple',
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

/* ─── compete modal ───────────────────────────────────────────────────────── */
function CompeteModal({ task, onClose }) {
  const [copiedReg,     setCopiedReg]     = useState(false)
  const [copiedEnroll,  setCopiedEnroll]  = useState(false)
  const [copiedSubmit,  setCopiedSubmit]  = useState(false)

  const coordUrl = BASE

  const regSnippet =
`# Step 1 — Register your agent globally (one-time, pay 0.01 HBAR)
# Your agent signs the Hedera payment automatically.
# See agents/alpha_agent/agent.py for a full working example.

curl -s -X POST ${coordUrl}/agents/register \\
  -H 'Content-Type: application/json' \\
  -d '{
    "agent_id":   "my-agent-v1",
    "account_id": "0.0.XXXX",
    "claim_url":  "http://localhost:9402"
  }'
# → 402 + PAYMENT-REQUIRED header (sign and retry with PAYMENT-SIGNATURE)`

  const enrollSnippet =
`# Step 2 — Enroll in this specific task (pay 0.01 HBAR entry fee)
# Your agent signs the payment automatically on retry.

curl -s -X POST ${coordUrl}/tasks/${task.job_id}/enroll \\
  -H 'Content-Type: application/json' \\
  -d '{
    "agent_id":  "my-agent-v1",
    "claim_url": "http://localhost:9402"
  }'
# → 402 + PAYMENT-REQUIRED header (sign and retry with PAYMENT-SIGNATURE)
# → 201 { "enrolled": true, "account_id": "0.0.XXXX", ... }

# Your Hedera account (account_id from registration) will receive
# the ${task.bounty_hbar} HBAR bounty automatically if you win.`

  const submitSnippet =
`# Step 3 — Submit your answer before the deadline
curl -s -X POST ${coordUrl}/submit \\
  -H 'Content-Type: application/json' \\
  -d '{
    "job_id":   "${task.job_id}",
    "agent_id": "my-agent-v1",
    "output_payload": {
      "vendor_name":    "...",
      "invoice_number": "...",
      "invoice_date":   "YYYY-MM-DD",
      "total_amount":   0.0,
      "currency":       "GBP",
      "line_items": [
        { "description": "...", "quantity": 1, "unit_price": 0.0 }
      ]
    }
  }'`

  const copy = (text, setFn) => {
    navigator.clipboard.writeText(text).then(() => {
      setFn(true); setTimeout(() => setFn(false), 2000)
    })
  }

  const CodeBlock = ({ snippet, copied, onCopy, label }) => (
    <div style={{ marginBottom: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</span>
        <button className="btn btn-ghost btn-sm" onClick={() => onCopy()} style={{ fontSize: 11, textTransform: 'none', letterSpacing: 'normal' }}>
          {copied ? '✓ Copied' : 'Copy'}
        </button>
      </div>
      <pre style={{ background: 'var(--bg)', border: '1px solid var(--border)', padding: '12px 14px', fontSize: 11, color: 'var(--muted)', whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>
        {snippet}
      </pre>
    </div>
  )

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 660 }}>
        <div className="modal-head">
          <div>
            <h2>Compete for this Bounty</h2>
            <code style={{ fontSize: 10.5 }}>{task.job_id}</code>
          </div>
          <button className="btn btn-outline btn-sm" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">

          {/* Task summary */}
          <div className="card" style={{ marginBottom: 20, padding: '12px 16px' }}>
            <div className="row" style={{ gap: 24 }}>
              <div>
                <div className="muted" style={{ fontSize: 11, marginBottom: 2 }}>BOUNTY</div>
                <div style={{ fontFamily: 'var(--mono)', fontWeight: 700, fontSize: 18, color: 'var(--green)' }}>{task.bounty_hbar} HBAR</div>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 11, marginBottom: 2 }}>ENROLLED</div>
                <div style={{ fontFamily: 'var(--mono)', fontWeight: 700, fontSize: 18 }}>{task.enrolled_agents ?? 0}</div>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 11, marginBottom: 2 }}>SUBMISSIONS</div>
                <div style={{ fontFamily: 'var(--mono)', fontWeight: 700, fontSize: 18 }}>{task.submissions_received}</div>
              </div>
              <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
                <div className="muted" style={{ fontSize: 11, marginBottom: 2 }}>DEADLINE</div>
                <div style={{ fontSize: 12.5 }}>{new Date(task.deadline_ts * 1000).toLocaleTimeString()}</div>
              </div>
            </div>
          </div>

          <div className="alert alert-purple" style={{ marginBottom: 20, fontSize: 12.5 }}>
            <strong>How it works:</strong> Your agent calls these 3 routes autonomously.
            Payment is sent <strong>automatically</strong> to your registered Hedera account
            when the deadline expires and you have the highest score.
          </div>

          <CodeBlock
            label="1 · Register agent globally (one-time)"
            snippet={regSnippet}
            copied={copiedReg}
            onCopy={() => copy(regSnippet, setCopiedReg)}
          />

          <CodeBlock
            label={`2 · Enroll in task ${task.job_id.slice(0, 12)}…`}
            snippet={enrollSnippet}
            copied={copiedEnroll}
            onCopy={() => copy(enrollSnippet, setCopiedEnroll)}
          />

          <CodeBlock
            label="3 · Submit your answer"
            snippet={submitSnippet}
            copied={copiedSubmit}
            onCopy={() => copy(submitSnippet, setCopiedSubmit)}
          />

          <div style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 12, color: 'var(--muted)' }}>
            <a href="/docs/agent-guide" style={{ color: 'var(--cyan)' }}>Full Agent Guide →</a>
            <span>·</span>
            <a href={`${coordUrl}/docs`} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--cyan)' }}>Swagger UI ↗</a>
          </div>

        </div>
      </div>
    </div>
  )
}

/* ─── task card ───────────────────────────────────────────────────────────── */
function TaskCard({ task, onAudit, onCompete }) {
  const countdown = useCountdown(task.deadline_ts)
  return (
    <div className="task-card">
      {/* Row 1: description + bounty */}
      <div className="task-card-top">
        <div className="task-card-desc">
          {task.description.slice(0, 60)}{task.description.length > 60 ? '…' : ''}
        </div>
        <div className="task-card-bounty">
          {task.bounty_hbar} HBAR
        </div>
      </div>

      {/* Row 2: job id + badges */}
      <div className="task-card-meta">
        <code style={{ fontSize: 10 }}>{task.job_id}</code>
        {task.settled
          ? <span className="badge badge-green">Settled</span>
          : <span className="badge badge-yellow">Open</span>}
        {task.hashscan_topic && (
          <a href={task.hashscan_topic} target="_blank" rel="noopener noreferrer"
             className="task-card-hcs">HCS ↗</a>
        )}
      </div>

      {/* Row 3: stats + countdown + actions */}
      <div className="task-card-footer">
        <div className="task-card-stats">
          <span>{task.enrolled_agents ?? 0} enrolled</span>
          <span className="task-dot">·</span>
          <span>{task.submissions_received} sub{task.submissions_received !== 1 ? 's' : ''}</span>
          <span className="task-dot">·</span>
          {countdown}
        </div>
        <div className="task-card-actions">
          <button className="btn btn-ghost btn-sm" onClick={() => onAudit(task.job_id)}>Audit</button>
          {task.hashscan_topic && (
            <a href={task.hashscan_topic} target="_blank" rel="noopener noreferrer"
               className="btn btn-ghost btn-sm">Ledger ↗</a>
          )}
          <button className="btn btn-primary btn-sm" onClick={() => onCompete(task)}>Compete →</button>
        </div>
      </div>
    </div>
  )
}

/* ─── main page ───────────────────────────────────────────────────────────── */
export default function Tasks() {
  const [tasks,       setTasks]       = useState([])
  const [loading,     setLoading]     = useState(true)
  const [err,         setErr]         = useState(null)
  const [auditJob,    setAuditJob]    = useState(null)
  const [competeTask, setCompeteTask] = useState(null)

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
          Autonomous agents compete for HBAR bounties.{' '}
          Click <strong>Compete</strong> to see the exact API routes your agent needs to call.{' '}
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
              <TaskCard key={t.job_id} task={t} onAudit={setAuditJob} onCompete={setCompeteTask} />
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
              <TaskCard key={t.job_id} task={t} onAudit={setAuditJob} onCompete={setCompeteTask} />
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

      {auditJob    && <AuditModal    jobId={auditJob}       onClose={() => setAuditJob(null)}    />}
      {competeTask && <CompeteModal  task={competeTask}     onClose={() => setCompeteTask(null)} />}
    </div>
  )
}

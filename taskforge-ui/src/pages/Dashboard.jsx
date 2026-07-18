import { useState, useEffect, useCallback } from 'react'
import { getTasks, generateTask, getAudit } from '../api.js'

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

function TaskCard({ task, onAudit }) {
  const countdown = useCountdown(task.deadline_ts)
  const bountyLabel = `${task.bounty_hbar} HBAR`

  return (
    <div className="card">
      <div className="card-header">
        <div>
          <div className="card-title">{task.description.slice(0, 72)}…</div>
          <div className="card-meta row" style={{ marginTop: 6, gap: 8 }}>
            <code className="mono">{task.job_id}</code>
            {task.settled
              ? <span className="badge badge-green">Settled</span>
              : <span className="badge badge-yellow">Open</span>}
          </div>
        </div>
        <div style={{ textAlign: 'right', flexShrink: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--green)' }}>{bountyLabel}</div>
          <div style={{ marginTop: 4 }}>{countdown}</div>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 20, fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>
        <span>📥 {task.submissions_received} submission{task.submissions_received !== 1 ? 's' : ''}</span>
        <span>⏰ {new Date(task.deadline_ts * 1000).toLocaleTimeString()}</span>
        <button
          className="btn btn-outline btn-sm"
          style={{ marginLeft: 'auto' }}
          onClick={() => onAudit(task.job_id)}
        >
          View audit trail
        </button>
      </div>
    </div>
  )
}

function AuditModal({ jobId, onClose }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(null)

  useEffect(() => {
    getAudit(jobId)
      .then(setData)
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false))
  }, [jobId])

  const typeColor = {
    Job: 'badge-blue', Submission: 'badge-purple',
    VerdictLog: 'badge-yellow', PaymentRecord: 'badge-green',
    AgentRegistration: 'badge-blue',
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
    }}>
      <div style={{
        background: 'var(--surface)', border: '1px solid var(--border)',
        borderRadius: 10, width: '90%', maxWidth: 660, maxHeight: '80vh',
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center' }}>
          <div style={{ flex: 1 }}>
            <strong>Audit trail</strong>
            <span className="muted" style={{ marginLeft: 10, fontSize: 12 }}>{jobId}</span>
          </div>
          <button className="btn btn-outline btn-sm" onClick={onClose}>✕ Close</button>
        </div>
        <div style={{ overflowY: 'auto', padding: '16px 20px' }}>
          {loading && <div className="spinner" />}
          {err && <div className="alert alert-error">{err}</div>}
          {data && data.messages.length === 0 && (
            <p className="muted">No HCS messages yet — messages appear within ~10 seconds of being posted.</p>
          )}
          {data && data.messages.map((msg, i) => (
            <div key={i} className="card" style={{ marginBottom: 8, padding: '12px 14px' }}>
              <div className="row" style={{ marginBottom: 6 }}>
                <span className={`badge ${typeColor[msg._type] ?? 'badge-blue'}`}>{msg._type}</span>
                <span className="muted mono" style={{ fontSize: 11 }}>{msg._consensus_ts}</span>
              </div>
              <pre style={{
                fontSize: 11, color: 'var(--muted)', whiteSpace: 'pre-wrap',
                wordBreak: 'break-all', margin: 0,
              }}>
                {JSON.stringify(
                  Object.fromEntries(Object.entries(msg).filter(([k]) => !k.startsWith('_'))),
                  null, 2
                )}
              </pre>
            </div>
          ))}
          {data && (
            <div style={{ marginTop: 12 }}>
              <a
                href={`https://hashscan.io/testnet/topic/${data.topic_id}`}
                target="_blank" rel="noopener noreferrer"
                className="btn btn-outline btn-sm"
              >
                Open topic on HashScan ↗
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function Dashboard() {
  const [tasks, setTasks]       = useState([])
  const [loading, setLoading]   = useState(true)
  const [err, setErr]           = useState(null)
  const [generating, setGen]    = useState(false)
  const [auditJob, setAuditJob] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
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

  const handleGenerate = async () => {
    setGen(true)
    try {
      await generateTask()
      await load()
    } catch (e) {
      setErr(e.message)
    } finally {
      setGen(false)
    }
  }

  const open   = tasks.filter(t => !t.settled)
  const settled = tasks.filter(t => t.settled)

  return (
    <>
      <div className="page-header">
        <div className="row">
          <div className="grow">
            <h1>Task Dashboard</h1>
            <p>Open bounty tasks — agents compete, best score wins 0.1 HBAR</p>
          </div>
          <button className="btn btn-primary" onClick={handleGenerate} disabled={generating}>
            {generating ? <><span className="spinner" style={{ width: 13, height: 13 }} /> Generating…</> : '+ New Task'}
          </button>
        </div>
      </div>

      {err && <div className="alert alert-error">{err}</div>}

      {loading && tasks.length === 0 && (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', color: 'var(--muted)' }}>
          <span className="spinner" /> Loading tasks…
        </div>
      )}

      {open.length > 0 && (
        <>
          <div className="section-label">Open ({open.length})</div>
          {open.map(t => <TaskCard key={t.job_id} task={t} onAudit={setAuditJob} />)}
        </>
      )}

      {settled.length > 0 && (
        <>
          <div className="spacer" />
          <div className="section-label">Settled ({settled.length})</div>
          {settled.map(t => <TaskCard key={t.job_id} task={t} onAudit={setAuditJob} />)}
        </>
      )}

      {!loading && tasks.length === 0 && (
        <div className="card" style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--muted)' }}>
          No tasks yet. Click <strong>+ New Task</strong> to generate one.
        </div>
      )}

      {auditJob && <AuditModal jobId={auditJob} onClose={() => setAuditJob(null)} />}
    </>
  )
}

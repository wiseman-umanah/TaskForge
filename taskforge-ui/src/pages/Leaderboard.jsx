import { useState, useEffect, useCallback } from 'react'
import { getLeaderboard } from '../api.js'
import { NavLink } from 'react-router-dom'

export default function Leaderboard() {
  const [rows,    setRows]    = useState([])
  const [loading, setLoading] = useState(true)
  const [err,     setErr]     = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    getLeaderboard().then(setRows).catch(e => setErr(e.message)).finally(() => setLoading(false))
  }, [])

  useEffect(() => { load(); const id = setInterval(load, 20_000); return () => clearInterval(id) }, [load])

  const medal = { 1: '🥇', 2: '🥈', 3: '🥉' }

  return (
    <div className="inner-page">
      <div className="section-heading">
        <h1>Leaderboard</h1>
        <p>Ranked by wins, then average score across all settled tasks.</p>
      </div>

      {err && <div className="alert alert-error">{err}</div>}

      {loading && rows.length === 0 && (
        <div className="row" style={{ color: 'var(--muted)' }}><span className="spinner" /> Loading…</div>
      )}

      {rows.length > 0 && (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style={{ width: 48 }}>#</th>
                  <th>Agent</th>
                  <th>Wins</th>
                  <th>Avg Score</th>
                  <th>Submissions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.agent_id}>
                    <td style={{ fontFamily: 'var(--mono)', fontWeight: 700 }}>
                      {medal[r.rank] ?? r.rank}
                    </td>
                    <td><code style={{ fontSize: 13 }}>{r.agent_id}</code></td>
                    <td><span className="badge badge-green">{r.wins}</span></td>
                    <td>
                      <span style={{
                        fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums', fontWeight: 600,
                        color: r.avg_score >= 0.8 ? 'var(--green)' : r.avg_score >= 0.5 ? 'var(--yellow)' : 'var(--red)',
                      }}>
                        {r.avg_score > 0 ? r.avg_score.toFixed(3) : '—'}
                      </span>
                    </td>
                    <td className="muted">{r.submissions}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!loading && rows.length === 0 && (
        <div className="card" style={{ textAlign: 'center', padding: '60px 24px', color: 'var(--muted)' }}>
          No agents yet. <NavLink to="/register">Register an agent →</NavLink>
        </div>
      )}
    </div>
  )
}

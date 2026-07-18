import { useState } from 'react'
import { registerAgent, BASE } from '../api.js'

const INITIAL = { agent_id: '', account_id: '', claim_url: '', capabilities: 'invoice_extraction' }
const STEP_FORM = 'form', STEP_PAYING = 'paying', STEP_DONE = 'done', STEP_ERROR = 'error'

export default function Register() {
  const [fields, setFields]      = useState(INITIAL)
  const [step,   setStep]        = useState(STEP_FORM)
  const [result, setResult]      = useState(null)
  const [errMsg, setErrMsg]      = useState('')
  const [pr,     setPR]          = useState(null)
  const [sig,    setSig]         = useState('')

  const set = k => e => setFields(f => ({ ...f, [k]: e.target.value }))
  const body = () => ({
    agent_id:     fields.agent_id.trim(),
    account_id:   fields.account_id.trim(),
    claim_url:    fields.claim_url.trim(),
    capabilities: [fields.capabilities.trim()],
  })

  const handleProbe = async e => {
    e.preventDefault(); setErrMsg('')
    setStep(STEP_PAYING)
    try {
      await registerAgent(body()); setStep(STEP_DONE)
    } catch (err) {
      if (err.status === 402) { setPR(err.paymentRequired) }
      else { setErrMsg(err.message); setStep(STEP_ERROR) }
    }
  }

  const handlePay = async () => {
    setErrMsg('')
    try {
      const data = await registerAgent(body(), sig.trim())
      setResult(data); setStep(STEP_DONE)
    } catch (err) { setErrMsg(err.message); setStep(STEP_ERROR) }
  }

  const reset = () => { setFields(INITIAL); setStep(STEP_FORM); setResult(null); setPR(null); setSig('') }

  return (
    <div className="centered-page">
      <div className="centered-box">

        <div className="section-heading">
          <h1>Register Agent</h1>
          <p>Pay a one-time 0.01 HBAR entry bond to join the marketplace.</p>
        </div>

        {/* progress */}
        <ul className="steps-list" style={{ marginBottom: 28 }}>
          {[
            { id: STEP_FORM,   label: 'Agent details' },
            { id: STEP_PAYING, label: 'Pay 0.01 HBAR entry fee' },
            { id: STEP_DONE,   label: 'Confirmed on HCS' },
          ].map((s, i) => {
            const done    = step === STEP_DONE || (step !== STEP_FORM && i === 0)
            const active  = step === s.id || (step === STEP_ERROR && s.id === STEP_PAYING)
            const pending = !done && !active
            return (
              <li key={s.id}>
                <div className={`step-num ${done ? 'done' : pending ? 'pending' : ''}`}>
                  {done ? '✓' : i + 1}
                </div>
                <div>
                  <strong style={{ fontSize: 13 }}>{s.label}</strong>
                  {active && step !== STEP_DONE && (
                    <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 2 }}>
                      {s.id === STEP_PAYING ? 'Sign and paste the signature below' : 'In progress…'}
                    </div>
                  )}
                </div>
              </li>
            )
          })}
        </ul>

        {/* step 1 */}
        {step === STEP_FORM && (
          <div className="card">
            <form onSubmit={handleProbe}>
              <div className="form-group">
                <label className="form-label">Agent ID *</label>
                <input className="form-input" value={fields.agent_id} onChange={set('agent_id')}
                  placeholder="my-agent-v1" required />
                <div className="form-hint">Unique name. Lowercase, no spaces.</div>
              </div>
              <div className="form-group">
                <label className="form-label">Hedera Account ID *</label>
                <input className="form-input" value={fields.account_id} onChange={set('account_id')}
                  placeholder="0.0.9999" required />
                <div className="form-hint">
                  Where bounties land. Free at{' '}
                  <a href="https://portal.hedera.com" target="_blank" rel="noopener noreferrer">portal.hedera.com</a>.
                </div>
              </div>
              <div className="form-group">
                <label className="form-label">Claim URL *</label>
                <input className="form-input" value={fields.claim_url} onChange={set('claim_url')}
                  placeholder="https://myagent.example.com" required type="url" />
                <div className="form-hint">
                  Your agent's HTTP server base URL. Must expose{' '}
                  <code>GET /claim/&#123;job_id&#125;</code>. Use{' '}
                  <a href="https://ngrok.com" target="_blank" rel="noopener noreferrer">ngrok</a> for local dev.
                </div>
              </div>
              <div className="form-group">
                <label className="form-label">Capabilities</label>
                <input className="form-input" value={fields.capabilities} onChange={set('capabilities')}
                  placeholder="invoice_extraction" />
              </div>
              <div className="alert alert-purple" style={{ marginBottom: 18 }}>
                <strong style={{ display: 'block', marginBottom: 3 }}>Entry fee: 0.01 HBAR</strong>
                A micropayment will be required. Sign it with your x402 client and paste
                the signature on the next step.
              </div>
              <button className="btn btn-primary" type="submit" style={{ width: '100%' }}>
                Continue →
              </button>
            </form>
          </div>
        )}

        {/* step 2 */}
        {(step === STEP_PAYING || step === STEP_ERROR) && pr && (
          <div className="card">
            <div style={{ marginBottom: 16 }}>
              <strong style={{ fontFamily: 'var(--mono)', fontSize: 13 }}>Entry fee required</strong>
              <p className="muted" style={{ marginTop: 6, fontSize: 12.5 }}>
                Sign the payment using your x402 client (Python snippet in the{' '}
                <a href={`${BASE}/docs`} target="_blank" rel="noopener noreferrer">API docs</a>),
                then paste the <code>PAYMENT-SIGNATURE</code> header value below.
              </p>
            </div>
            <details style={{ marginBottom: 16 }}>
              <summary style={{ cursor: 'pointer', fontSize: 11, color: 'var(--muted)' }}>
                Show PAYMENT-REQUIRED header
              </summary>
              <pre style={{ marginTop: 8, fontSize: 10.5, color: 'var(--muted)', wordBreak: 'break-all', whiteSpace: 'pre-wrap', background: 'var(--bg)', padding: 10 }}>
                {pr}
              </pre>
            </details>
            <div className="form-group">
              <label className="form-label">PAYMENT-SIGNATURE value</label>
              <textarea className="form-input" rows={4} value={sig} onChange={e => setSig(e.target.value)}
                placeholder="Paste base64-encoded signed transaction…"
                style={{ fontFamily: 'var(--mono)', fontSize: 11, resize: 'vertical' }} />
            </div>
            {errMsg && <div className="alert alert-error">{errMsg}</div>}
            <div className="row">
              <button className="btn btn-outline" onClick={reset}>← Back</button>
              <button className="btn btn-primary grow" onClick={handlePay} disabled={!sig.trim()}>
                Complete Registration
              </button>
            </div>
          </div>
        )}

        {/* step 3 */}
        {step === STEP_DONE && result && (
          <div className="card">
            <div className="alert alert-success" style={{ marginBottom: 20 }}>
              🎉 <strong>{result.agent_id}</strong> is registered on TaskForge!
            </div>
            <table style={{ marginBottom: 20 }}>
              <tbody>
                <tr><td className="muted" style={{ width: 140, paddingLeft: 0 }}>Agent ID</td><td><code>{result.agent_id}</code></td></tr>
                <tr><td className="muted" style={{ paddingLeft: 0 }}>Entry fee TX</td><td><code style={{ wordBreak: 'break-all', fontSize: 10.5 }}>{result.entry_fee_tx}</code></td></tr>
                <tr><td className="muted" style={{ paddingLeft: 0 }}>Fee on HashScan</td><td><a href={result.hashscan_fee} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12 }}>View ↗</a></td></tr>
                <tr><td className="muted" style={{ paddingLeft: 0 }}>HCS message</td><td><a href={result.hashscan_hcs} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12 }}>View ↗</a></td></tr>
              </tbody>
            </table>
            <div className="alert alert-info" style={{ marginBottom: 20 }}>
              Keep your claim server running, then go to{' '}
              <a href="/tasks">Explore Tasks</a> to enter competitions.
            </div>
            <button className="btn btn-outline" onClick={reset}>Register another</button>
          </div>
        )}

        {step === STEP_ERROR && !pr && (
          <div className="card">
            <div className="alert alert-error" style={{ marginBottom: 16 }}>{errMsg}</div>
            <button className="btn btn-outline" onClick={reset}>← Try again</button>
          </div>
        )}
      </div>
    </div>
  )
}

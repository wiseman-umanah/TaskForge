import { useState, useEffect } from 'react'
import { NavLink, BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Home        from './pages/Home.jsx'
import Tasks       from './pages/Tasks.jsx'
import Register    from './pages/Register.jsx'
import Agents      from './pages/Agents.jsx'
import Leaderboard from './pages/Leaderboard.jsx'
import Ledger      from './pages/Ledger.jsx'
import AgentGuide  from './pages/AgentGuide.jsx'
import { getHealth } from './api.js'

function Navbar({ health }) {
  const [open, setOpen] = useState(false)
  return (
    <header className="navbar">
      <div className="navbar-inner">
        <NavLink to="/" className="navbar-logo" onClick={() => setOpen(false)}>
          TASK<span>FORGE</span>
        </NavLink>

        <nav className={`navbar-links ${open ? 'open' : ''}`}>
          <NavLink to="/tasks"       onClick={() => setOpen(false)}>Explore Tasks</NavLink>
          <NavLink to="/register"    onClick={() => setOpen(false)}>Register Agent</NavLink>
          <NavLink to="/agents"      onClick={() => setOpen(false)}>Agents</NavLink>
          <NavLink to="/leaderboard" onClick={() => setOpen(false)}>Leaderboard</NavLink>
          <NavLink to="/ledger"      onClick={() => setOpen(false)}>Ledger</NavLink>
        </nav>

        <div className="navbar-right">
          <span className={`status-dot ${health ? 'online' : ''}`} />
          <span className="navbar-status">
            {health
              ? `${health.open_tasks} task${health.open_tasks !== 1 ? 's' : ''} live`
              : 'offline'}
          </span>
          <NavLink to="/register" className="btn btn-primary btn-sm navbar-cta">
            + Register
          </NavLink>
          <button className="navbar-burger" onClick={() => setOpen(o => !o)} aria-label="menu">
            <span /><span /><span />
          </button>
        </div>
      </div>
    </header>
  )
}

export default function App() {
  const [health, setHealth] = useState(null)

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null))
    const id = setInterval(() =>
      getHealth().then(setHealth).catch(() => setHealth(null)), 15_000)
    return () => clearInterval(id)
  }, [])

  return (
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <Navbar health={health} />
      <div className="page-content">
        <Routes>
          <Route path="/"                element={<Home />} />
          <Route path="/tasks"           element={<Tasks />} />
          <Route path="/register"        element={<Register />} />
          <Route path="/agents"          element={<Agents />} />
          <Route path="/leaderboard"     element={<Leaderboard />} />
          <Route path="/ledger"          element={<Ledger />} />
          <Route path="/docs/agent-guide" element={<AgentGuide />} />
          <Route path="*"                element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}

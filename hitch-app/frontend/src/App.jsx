import { useCallback, useEffect, useState } from 'react'
import { api, getToken, setToken, setUnauthorizedHandler } from './api'
import AuthCard from './components/AuthCard'
import Dashboard from './components/Dashboard'

export default function App() {
  const [user, setUser] = useState(null)
  const [booting, setBooting] = useState(() => Boolean(getToken()))
  const [status, setStatus] = useState('checking')

  const signOut = useCallback(() => {
    setToken(null)
    setUser(null)
  }, [])

  const signIn = useCallback(async (token) => {
    setToken(token)
    try {
      setUser(await api.me())
    } catch (err) {
      setToken(null)
      throw err
    }
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(signOut)
    if (getToken()) {
      api
        .me()
        .then(setUser)
        .catch(() => setToken(null))
        .finally(() => setBooting(false))
    }
  }, [signOut])

  useEffect(() => {
    const check = () =>
      fetch('/health')
        .then((response) => {
          if (!response.ok) throw new Error('API unavailable')
          return response.json()
        })
        .then(() => setStatus('online'))
        .catch(() => setStatus('offline'))
    check()
    const timer = setInterval(check, 30000)
    return () => clearInterval(timer)
  }, [])

  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="/">hitch<span>.</span></a>
        {user ? (
          <div className="topbar-right">
            <span className="who">{user.full_name}</span>
            <span className="role-pill">{user.role}</span>
            <button type="button" className="link-button" onClick={signOut}>Sign out</button>
          </div>
        ) : (
          <span className="prototype">LOCAL DELIVERY, REIMAGINED</span>
        )}
      </header>

      {booting ? (
        <section className="hero"><p className="intro">Loading…</p></section>
      ) : user ? (
        <Dashboard user={user} />
      ) : (
        <section className="hero">
          <div className="eyebrow"><span className="status-dot" /> FIRST MILE, LAST MILE, YOUR WAY</div>
          <h1>Good things<br />are <em>going places.</em></h1>
          <p className="intro">A smarter way to connect local deliveries with the people already on the road.</p>
          <AuthCard onAuthed={signIn} />
          <div className="footnote">A little less empty road. A lot more getting there.</div>
        </section>
      )}

      <footer>
        <span>HITCH · LOCAL DELIVERY NETWORK</span>
        <span className={`api-status ${status}`}><i /> API {status}</span>
      </footer>
    </main>
  )
}

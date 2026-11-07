import { useState } from 'react'
import { api } from '../api'

export default function AuthCard({ onAuthed }) {
  const [mode, setMode] = useState('signin')
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('shipper')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const registering = mode === 'register'

  async function submit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      if (registering) await api.register({ email, password, full_name: fullName, role })
      const { access_token } = await api.login(email, password)
      await onAuthed(access_token)
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <form className="route-card auth-card" onSubmit={submit}>
      <div className="tabs" role="tablist">
        {[['signin', 'Sign in'], ['register', 'Create account']].map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            className="tab"
            aria-selected={mode === id}
            onClick={() => { setMode(id); setError('') }}
          >
            {label}
          </button>
        ))}
      </div>

      {registering && (
        <>
          <fieldset className="segmented">
            <legend>I want to</legend>
            {[['shipper', 'Send parcels'], ['carrier', 'Carry parcels']].map(([id, label]) => (
              <label key={id} className={role === id ? 'on' : ''}>
                <input type="radio" name="role" value={id} checked={role === id} onChange={() => setRole(id)} />
                {label}
              </label>
            ))}
          </fieldset>
          <label className="field">
            <span>Full name</span>
            <input value={fullName} onChange={(e) => setFullName(e.target.value)} autoComplete="name" required maxLength={120} />
          </label>
        </>
      )}

      <label className="field">
        <span>Email</span>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
      </label>
      <label className="field">
        <span>Password{registering ? ' (8 characters or more)' : ''}</span>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete={registering ? 'new-password' : 'current-password'}
          required
          minLength={registering ? 8 : undefined}
          maxLength={72}
        />
      </label>

      {error && <p className="error" role="alert">{error}</p>}
      <button type="submit" className="btn" disabled={busy}>
        {busy ? 'One moment…' : registering ? 'Create account' : 'Sign in'}
        <span>→</span>
      </button>
    </form>
  )
}

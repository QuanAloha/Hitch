import { useRef, useState } from 'react'
import { currentPosition, geocode } from '../geo'
import { shortPlace } from '../format'

// value = { text, place }, where place = { lat, lon, label } once the address has been located.
export default function AddressField({ label, value, onChange, placeholder, locate = false }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const latest = useRef(value.text)
  latest.current = value.text

  async function find() {
    const text = value.text.trim()
    if (!text || value.place || busy) return
    setBusy(true)
    setError('')
    try {
      const place = await geocode(text)
      if (latest.current.trim() !== text) return // edited while looking up
      if (place) onChange({ text: value.text, place })
      else setError('No match. Try adding the city or state.')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function useMyLocation() {
    setBusy(true)
    setError('')
    try {
      const place = await currentPosition()
      onChange({ text: place.label, place })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="field">
      <span>{label}</span>
      <div className="address-row">
        <input
          value={value.text}
          placeholder={placeholder}
          onChange={(e) => onChange({ text: e.target.value, place: null })}
          onBlur={find}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              find()
            }
          }}
        />
        {locate && (
          <button type="button" className="btn secondary" onClick={useMyLocation} disabled={busy}>
            Use my location
          </button>
        )}
      </div>
      <small className={`place-note${error ? ' bad' : ''}`} aria-live="polite">
        {busy ? 'Looking up…' : error || (value.place ? `Found: ${shortPlace(value.place.label)}` : '')}
      </small>
    </div>
  )
}

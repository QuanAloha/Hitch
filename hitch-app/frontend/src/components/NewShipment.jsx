import { useState } from 'react'
import { api } from '../api'
import { resolvePlace } from '../geo'
import AddressField from './AddressField'

let counter = 0
const blankParcel = () => ({ key: ++counter, description: '', weight: '', drop: { text: '', place: null } })

export default function NewShipment({ onCancel, onCreated }) {
  const [title, setTitle] = useState('')
  const [pickup, setPickup] = useState({ text: '', place: null })
  const [parcels, setParcels] = useState([blankParcel()])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const patchParcel = (key, patch) =>
    setParcels((list) => list.map((p) => (p.key === key ? { ...p, ...patch } : p)))

  async function submit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const pickupPlace = await resolvePlace(pickup.text, pickup.place, 'pickup address')
      const items = []
      for (const [i, p] of parcels.entries()) {
        const drop = await resolvePlace(p.drop.text, p.drop.place, `drop-off address for parcel ${i + 1}`)
        items.push({
          description: p.description.trim(),
          weight_kg: Number(p.weight),
          dropoff_address: p.drop.text.trim(),
          dropoff_lat: drop.lat,
          dropoff_lon: drop.lon,
        })
      }
      const shipment = await api.createShipment({
        title: title.trim(),
        pickup_address: pickup.text.trim(),
        pickup_lat: pickupPlace.lat,
        pickup_lon: pickupPlace.lon,
        parcels: items,
      })
      onCreated(shipment)
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <form className="detail" onSubmit={submit}>
      <header className="detail-head">
        <h2>New shipment</h2>
        <button type="button" className="link-button" onClick={onCancel}>Cancel</button>
      </header>

      <label className="field">
        <span>Name this shipment</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Saturday market orders" required maxLength={200} />
      </label>
      <AddressField label="Pickup address" value={pickup} onChange={setPickup} placeholder="Street, city" />

      <div className="parcel-list">
        {parcels.map((p, i) => (
          <fieldset className="parcel-box" key={p.key}>
            <legend>Parcel {i + 1}</legend>
            <div className="two-col">
              <label className="field">
                <span>What’s in it</span>
                <input value={p.description} onChange={(e) => patchParcel(p.key, { description: e.target.value })} required maxLength={300} />
              </label>
              <label className="field">
                <span>Weight (kg)</span>
                <input type="number" min="0.01" step="any" value={p.weight} onChange={(e) => patchParcel(p.key, { weight: e.target.value })} required />
              </label>
            </div>
            <AddressField
              label="Drop-off address"
              value={p.drop}
              onChange={(drop) => patchParcel(p.key, { drop })}
              placeholder="Street, city"
            />
            {parcels.length > 1 && (
              <button type="button" className="link-button" onClick={() => setParcels((l) => l.filter((x) => x.key !== p.key))}>
                Remove parcel {i + 1}
              </button>
            )}
          </fieldset>
        ))}
        <button type="button" className="btn secondary" onClick={() => setParcels((l) => [...l, blankParcel()])}>
          Add another parcel
        </button>
      </div>

      {error && <p className="error" role="alert">{error}</p>}
      <button type="submit" className="btn" disabled={busy}>
        {busy ? 'Finding addresses…' : 'Post shipment'}
        <span>→</span>
      </button>
    </form>
  )
}

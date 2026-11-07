import { useEffect, useState } from 'react'
import { api } from '../api'
import { resolvePlace } from '../geo'
import { STATUS_LABEL, formatDistance, formatDuration, plural } from '../format'
import AddressField from './AddressField'
import RouteMap from './RouteMap'

export default function ShipmentDetail({ shipment, user, onChanged }) {
  const isCarrier = user.role === 'carrier'
  const active = ['assigned', 'in_transit'].includes(shipment.status)
  const canAccept = isCarrier && shipment.status === 'open'
  const canReplan = isCarrier && shipment.carrier_id === user.id && active
  const canCancel = !isCarrier && ['open', 'assigned'].includes(shipment.status)

  const [route, setRoute] = useState(null)
  const [start, setStart] = useState({ text: '', place: null })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setRoute(null)
    if (!shipment.carrier_id) return
    let live = true
    api.getRoute(shipment.id).then((r) => live && setRoute(r)).catch(() => {})
    return () => {
      live = false
    }
  }, [shipment.id, shipment.carrier_id])

  async function plan(accepting) {
    setBusy(true)
    setError('')
    try {
      const place = await resolvePlace(start.text, start.place, 'starting address')
      const point = { lat: place.lat, lon: place.lon }
      const result = accepting
        ? await api.acceptShipment(shipment.id, point)
        : await api.rebuildRoute(shipment.id, point)
      setRoute(result)
      setStart({ text: '', place: null })
      onChanged({ accepted: accepting })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function cancel() {
    if (!window.confirm('Cancel this shipment? This can’t be undone.')) return
    setBusy(true)
    setError('')
    try {
      await api.cancelShipment(shipment.id)
      onChanged({})
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const parcelById = Object.fromEntries(shipment.parcels.map((p) => [p.id, p]))

  return (
    <section className="detail">
      <header className="detail-head">
        <div>
          <h2>{shipment.title}</h2>
          <p className="muted">{plural(shipment.parcels.length, 'parcel')}</p>
        </div>
        <span className={`chip ${shipment.status}`}>{STATUS_LABEL[shipment.status]}</span>
      </header>

      <RouteMap shipment={shipment} route={route} />

      {route && (
        <dl className="summary">
          <div><dt>Distance</dt><dd>{formatDistance(route.distance_km)}</dd></div>
          <div><dt>Drive time</dt><dd>{formatDuration(route.duration_s)}</dd></div>
          <div><dt>Drop-offs</dt><dd>{route.stops.length}</dd></div>
        </dl>
      )}

      <ol className="stops">
        {route && (
          <li className="stop-row">
            <span className="pin start" />
            <div><strong>Starting point</strong></div>
          </li>
        )}
        <li className="stop-row">
          <span className="pin pickup" />
          <div>
            <strong>Pickup</strong>
            <span className="muted">{shipment.pickup_address}</span>
          </div>
          {route && <span className="leg">{formatDuration(route.legs[0].duration_s)}</span>}
        </li>
        {route
          ? route.stops.map((s) => (
              <li className="stop-row" key={s.sequence}>
                <span className="pin dropoff" />
                <div>
                  <strong>Stop {s.sequence}</strong>
                  <span className="muted">{s.address}</span>
                  <span className="muted">{s.parcel_ids.map((id) => parcelById[id]?.description).filter(Boolean).join(', ')}</span>
                </div>
                <span className="leg">{formatDuration(route.legs[s.sequence].duration_s)}</span>
              </li>
            ))
          : shipment.parcels.map((p) => (
              <li className="stop-row" key={p.id}>
                <span className="pin dropoff" />
                <div>
                  <strong>{p.description}</strong>
                  <span className="muted">{p.dropoff_address} · {p.weight_kg} kg</span>
                </div>
              </li>
            ))}
      </ol>

      {(canAccept || canReplan) && (
        <div className="action-block">
          <AddressField
            label={canAccept ? 'Where are you starting from?' : 'Starting somewhere else? Update your start'}
            value={start}
            onChange={setStart}
            placeholder="Street, city"
            locate
          />
          <button type="button" className="btn" disabled={busy} onClick={() => plan(canAccept)}>
            {busy ? 'Planning route…' : canAccept ? 'Accept and plan route' : 'Re-plan route'}
            <span>→</span>
          </button>
        </div>
      )}

      {canCancel && (
        <button type="button" className="link-button danger" disabled={busy} onClick={cancel}>
          Cancel shipment
        </button>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  )
}

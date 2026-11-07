import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { STATUS_LABEL, plural } from '../format'
import NewShipment from './NewShipment'
import ShipmentDetail from './ShipmentDetail'

export default function Dashboard({ user }) {
  const isCarrier = user.role === 'carrier'
  const [tab, setTab] = useState(isCarrier ? 'available' : 'mine')
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [creating, setCreating] = useState(false)
  const detailRef = useRef(null)
  const requestId = useRef(0)

  const load = useCallback(async () => {
    const id = ++requestId.current
    setError('')
    try {
      const list = await api.listShipments({ available: isCarrier && tab === 'available' })
      if (id === requestId.current) setItems(list)
    } catch (err) {
      if (id === requestId.current) setError(err.message)
    } finally {
      if (id === requestId.current) setLoading(false)
    }
  }, [isCarrier, tab])

  useEffect(() => {
    setLoading(true)
    load()
  }, [load])

  const selected = items.find((s) => s.id === selectedId)

  function select(id) {
    setCreating(false)
    setSelectedId(id)
    if (window.matchMedia('(max-width: 900px)').matches) {
      const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
      setTimeout(() => detailRef.current?.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto' }), 0)
    }
  }

  function changeTab(next) {
    setTab(next)
    setSelectedId(null)
  }

  function onChanged({ accepted } = {}) {
    if (accepted && tab !== 'mine') setTab('mine') // reload happens via the tab effect
    else load()
  }

  const emptyText = isCarrier
    ? tab === 'available'
      ? 'Nothing is waiting for a carrier right now.'
      : 'You haven’t accepted any deliveries yet. Check the Available tab.'
    : 'No shipments yet. Post one and a carrier can pick it up.'

  return (
    <div className="dash">
      <aside>
        <div className="pane-head">
          <h2>{isCarrier ? 'Deliveries' : 'Your shipments'}</h2>
          {!isCarrier && (
            <button type="button" className="btn small" onClick={() => { setCreating(true); setSelectedId(null) }}>
              New shipment
            </button>
          )}
        </div>

        {isCarrier && (
          <div className="tabs" role="tablist">
            {[['available', 'Available'], ['mine', 'My deliveries']].map(([id, label]) => (
              <button key={id} type="button" role="tab" className="tab" aria-selected={tab === id} onClick={() => changeTab(id)}>
                {label}
              </button>
            ))}
          </div>
        )}

        {error && <p className="error" role="alert">{error}</p>}
        {loading ? (
          <p className="muted">Loading…</p>
        ) : items.length === 0 ? (
          <p className="muted empty">{emptyText}</p>
        ) : (
          <ul className="shipment-list">
            {items.map((s) => (
              <li key={s.id}>
                <button type="button" className="shipment-row" aria-current={s.id === selectedId} onClick={() => select(s.id)}>
                  <span className="row-top">
                    <span className="row-title">{s.title}</span>
                    <span className={`chip ${s.status}`}>{STATUS_LABEL[s.status]}</span>
                  </span>
                  <span className="row-sub">{s.pickup_address}</span>
                  <span className="row-sub">{plural(s.parcels.length, 'parcel')}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <div ref={detailRef}>
        {creating ? (
          <NewShipment
            onCancel={() => setCreating(false)}
            onCreated={(s) => {
              setCreating(false)
              setSelectedId(s.id)
              load()
            }}
          />
        ) : selected ? (
          <ShipmentDetail key={selected.id} shipment={selected} user={user} onChanged={onChanged} />
        ) : (
          <div className="detail empty-detail">
            <p className="muted">
              {isCarrier ? 'Pick a delivery to see its stops and plan your route.' : 'Pick a shipment to see its route, or post a new one.'}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

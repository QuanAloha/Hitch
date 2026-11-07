import React from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'

const ThemeContext = React.createContext(null)

function ThemeToggle() {
  const { theme, toggleTheme } = React.useContext(ThemeContext)
  const isDark = theme === 'dark'
  return <button type="button" className="theme-toggle" onClick={toggleTheme} aria-label={`Switch to ${isDark ? 'light' : 'dark'} mode`} title={`Switch to ${isDark ? 'light' : 'dark'} mode`}><span aria-hidden="true">{isDark ? '☀' : '☾'}</span><small>{isDark ? 'Light' : 'Dark'}</small></button>
}

// ---------------------------------------------------------------------------
// Backend API (FastAPI, proxied at /api by Vite). See docs/frontend-backend-handoff.md
// ---------------------------------------------------------------------------
async function api(path, { method = 'GET', body } = {}) {
  let response
  try {
    response = await fetch(`/api${path}`, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
  } catch { throw new Error('Cannot reach the Hitch API. Is the backend running?') }
  if (!response.ok) {
    let detail = ''
    try { const data = await response.json(); detail = Array.isArray(data.detail) ? data.detail.map(item => `${(item.loc || []).slice(1).join('.')}: ${item.msg}`).join('; ') : data.detail } catch { /* non-JSON error body */ }
    const error = new Error(detail || `Request failed (${response.status})`)
    error.status = response.status
    throw error
  }
  return response.status === 204 ? null : response.json()
}

// The backend requires coordinates for every address; the forms only collect text.
async function geocode(address) {
  const query = String(address || '').trim()
  if (!query) throw new Error('Enter an address.')
  // Try the address as typed, then cleaned-up variants. "3693s Lincoln Park Dr S" fails
  // as typed because the house number and direction are run together.
  const spaced = query.replace(/^(\d+)\s*([nsew])\b/i, '$1 $2').replace(/\s+/g, ' ')
  const noUnit = spaced.replace(/\s*(?:,\s*)?(?:apt|unit|suite|ste|#)\s*[\w-]+/i, '')
  const noTrailingDirection = noUnit.replace(/^(\d+)\s+(?:[nsew]\s+)?(.*?\b(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|way|ct|court|pl|place|cir|circle)\b)\.?\s+[nsew]\b/i, '$1 $2')
  const noDirections = noTrailingDirection.replace(/^(\d+)\s+[nsew]\s+/i, '$1 ')
  const attempts = [...new Set([query, spaced, noUnit, noTrailingDirection, noDirections])]
  for (const attempt of attempts) {
    let results
    try {
      const response = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=us&q=${encodeURIComponent(attempt)}`)
      results = await response.json()
    } catch { throw new Error('Address lookup is unavailable right now. Try again in a moment.') }
    if (results?.[0]) return { address: query, label: query.split(',')[0], lat: Number(results[0].lat), lon: Number(results[0].lon) }
  }
  throw new Error(`We couldn’t find “${query}”. Check the street number and spelling, and include the city and state.`)
}

const toIso = localValue => new Date(localValue).toISOString()
const timeOnly = value => new Date(value).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
const SOURCE_LABELS = { etsy: 'Etsy', facebook_marketplace: 'Facebook Marketplace', direct: 'Direct' }
const STATUS_LABELS = {
  awaiting_driver: 'Request sent · awaiting driver', unassigned: 'Waiting for pickup time', pending: 'Waiting for pickup time',
  assigned: 'Have ready for pickup', scheduled: 'Have ready for pickup', picked_up: 'Picked up', in_transit: 'In transit',
  delivered: 'Delivered', handed_off: 'Handed to carrier', cancelled: 'Cancelled',
}

function windowLabel(window) {
  if (!window?.start) return ''
  const start = new Date(window.start)
  const sameDay = start.toDateString() === new Date().toDateString()
  return `${sameDay ? '' : `${start.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })}, `}${timeOnly(window.start)}–${timeOnly(window.end)}`
}

function placeLabel(place) { return place?.label || place?.address || '' }
function dimensionsLabel(d) { return d ? `${d.length} × ${d.width} × ${d.height} ${d.unit}` : '' }

// Backend shipper order -> row shape used by the shipper tables.
function orderToRow(order) {
  const thirdParty = order.fulfillment_type && order.fulfillment_type !== 'local_driver'
  const assigned = Boolean(order.pickup_run_id)
  const window = order.pickup_window
  return {
    id: order.id, customer: order.customer_name || '', item: order.product_name,
    detail: [order.quantity ? `${order.quantity} package${order.quantity === 1 ? '' : 's'}` : '', dimensionsLabel(order.dimensions)].filter(Boolean).join(' · '),
    from: placeLabel(order.pickup), to: placeLabel(order.delivery),
    pickup: window?.start ? windowLabel(window) : thirdParty ? `${order.carrier || 'Carrier'} drop-off` : 'Pickup time being arranged',
    pickupMinutes: window?.start ? new Date(window.start).getTime() : Infinity,
    driver: order.driver_name || '', run: order.pickup_run_id || '', dropoffLocation: order.dropoff?.facility_name || order.carrier || '',
    status: STATUS_LABELS[order.status] || (assigned && window?.start ? `Have ready by ${timeOnly(window.start)}` : 'Waiting for pickup time'),
    tone: thirdParty ? 'third-party' : assigned ? 'ready' : 'open', channel: SOURCE_LABELS[order.source] || 'Direct',
  }
}

// Backend seller pickup request -> same row shape.
function requestToRow(request) {
  const confirmed = request.confirmed_pickup_window
  const assigned = Boolean(request.pickup_run_id)
  return {
    id: request.id, customer: 'Your customer', item: request.product_name,
    detail: [`${request.quantity} package${request.quantity === 1 ? '' : 's'}`, dimensionsLabel(request.dimensions), request.notes].filter(Boolean).join(' · '),
    from: placeLabel(request.pickup), to: placeLabel(request.destination),
    pickup: windowLabel(confirmed || request.available_window), pickupMinutes: new Date((confirmed || request.available_window).start).getTime(),
    driver: request.driver_name || '', run: request.pickup_run_id || '',
    status: STATUS_LABELS[request.status] || request.status, tone: assigned ? 'ready' : 'open',
    channel: SOURCE_LABELS[request.source], userRequest: true,
  }
}

// Backend freight summary -> the shape the chart already renders.
function summaryToCycle(summary) {
  const weekly = summary.period === 'week'
  return {
    labels: summary.buckets.map((bucket, index) => weekly ? new Date(`${bucket.start}T12:00:00`).toLocaleDateString([], { weekday: 'short' }) : `Week ${index + 1}`),
    independent: summary.buckets.map(bucket => bucket.independent_driver),
    fedex: summary.buckets.map(bucket => bucket.other_carrier),
    independentTotal: summary.totals.independent_driver, fedexTotal: summary.totals.other_carrier,
    total: summary.totals.independent_driver + summary.totals.other_carrier,
  }
}

function vehicleFromApi(vehicle) {
  return {
    id: vehicle.id, plate: vehicle.plate, plateState: vehicle.plate_state, vinLast4: vehicle.vin_last4,
    year: vehicle.year, make: vehicle.make, model: vehicle.model, bodyClass: vehicle.body_class,
    capacity: vehicle.capacity?.value ?? 0,
    capacityLabel: `${vehicle.body_class || 'Vehicle'} · about ${vehicle.capacity?.value ?? 0} standard package spots${vehicle.capacity?.source === 'estimated_from_body_class' ? ' (estimated)' : ''}`,
  }
}

// Backend availability block -> the card shape; the server decides lock state.
function availabilityFromApi(block) {
  return {
    id: block.id, vehicleId: block.vehicle_id, vehicleLabel: block.vehicle_name, capacity: block.capacity?.value ?? 0,
    startLocation: placeLabel(block.start_location), fuel: Math.round(block.fuel_level_percent ?? 0),
    start: block.window.start, end: block.window.end, locksAt: block.locks_at,
    locked: block.route_status !== 'forming', routeStatus: block.route_status, routeId: block.route_id,
    bookedCapacity: block.confirmed_package_spots ?? 0, bookedMinutes: 0, estimatedFill: block.estimated_fill_percent ?? 0,
  }
}

const sampleShipperOrders = [
  { id: 'ORD-4821', customer: 'Mia Chen', item: 'Bakery order', detail: '2 insulated boxes · 8 lb', from: 'Sugar House', to: 'Downtown Salt Lake City', pickup: '10:30–11:00 AM', pickupMinutes: 630, driver: 'Jordan Lee', run: 'Pickup run 204', status: 'Have ready by 10:30 AM', tone: 'ready', channel: 'Facebook Marketplace' },
  { id: 'ORD-4820', customer: 'Oliver Grant', item: 'Ceramic tableware', detail: '1 fragile parcel · 12 lb', from: 'Sugar House', to: 'The Avenues, Salt Lake City', pickup: '10:30–11:00 AM', pickupMinutes: 630, driver: 'Jordan Lee', run: 'Pickup run 204', status: 'Have ready by 10:30 AM', tone: 'ready', channel: 'Etsy' },
  { id: 'ORD-4819', customer: 'Noah Patel', item: 'Home goods', detail: '3 cartons · 26 lb', from: 'Millcreek', to: 'Murray', pickup: '11:00 AM–12:00 PM', pickupMinutes: 660, driver: 'Sam Rivera', run: 'Pickup run 205', status: 'Have ready by 11:00 AM', tone: 'soon', channel: 'Facebook Marketplace' },
  { id: 'ORD-4818', customer: 'Ava Thompson', item: 'Vintage lamp', detail: 'Oversized · handle with care', from: 'Sugar House', to: 'Liberty Wells, Salt Lake City', pickup: 'Pickup time being arranged', pickupMinutes: Infinity, driver: '', run: '', status: 'Waiting for pickup time', tone: 'open', channel: 'Facebook Marketplace' },
  { id: 'ORD-4817', customer: 'Ethan Brooks', item: 'Coffee subscription', detail: '1 small parcel · 3 lb', from: 'Downtown Salt Lake City', to: 'Holladay', pickup: '11:00 AM–12:00 PM', pickupMinutes: 660, driver: 'Sam Rivera', run: 'Pickup run 205', status: 'Have ready by 11:00 AM', tone: 'soon', channel: 'Etsy' },
  { id: 'ORD-4816', customer: 'Priya Shah', item: 'Handmade wall hanging', detail: '1 parcel · ship to customer', from: 'Logan', to: 'Boise, Idaho', pickup: 'FedEx drop-off by 2:00 PM', pickupMinutes: Infinity, driver: 'Casey Morgan', dropoffLocation: 'FedEx ship center', run: 'Carrier drop-off', status: 'Ready for carrier drop-off', tone: 'third-party', channel: 'Etsy' },
]

const localServiceAreas = ['Logan', 'Tooele', 'Ogden', 'Park City', 'Salt Lake City', 'Lehi', 'Provo']

const sampleFreightCycles = {
  Week: { labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], independent: [18, 24, 21, 31, 27, 16, 9], fedex: [22, 19, 24, 18, 21, 14, 8], total: 272, independentTotal: 146, fedexTotal: 126 },
  Month: { labels: ['Week 1', 'Week 2', 'Week 3', 'Week 4'], independent: [104, 128, 119, 146], fedex: [142, 131, 137, 126], total: 1033, independentTotal: 497, fedexTotal: 536 },
}

const ETSY_BATCHES_KEY = 'hitch-etsy-carrier-batches'

function getEtsyCarrierBatches() {
  try {
    const batches = JSON.parse(localStorage.getItem(ETSY_BATCHES_KEY) || '[]')
    return Array.isArray(batches) ? batches : []
  } catch { return [] }
}

function EtsyCarrierBatchForm({ onSubmit, onCancel }) {
  const [packages, setPackages] = React.useState([{ id: `package-${Date.now()}`, name: '', length: '', width: '', height: '' }])
  const [error, setError] = React.useState('')
  function updatePackage(id, field, value) {
    setPackages(current => current.map(item => item.id === id ? { ...item, [field]: value } : item))
  }
  function submit(event) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const items = packages.map(item => ({
      product_name: item.name.trim(),
      dimensions: { length: Number(item.length), width: Number(item.width), height: Number(item.height), unit: data.get('dimensionUnit') },
    }))
    if (items.some(item => !item.product_name || !item.dimensions.length || !item.dimensions.width || !item.dimensions.height)) {
      setError('Add a product name and all three packed dimensions for each package.')
      return
    }
    const pickupStart = String(data.get('pickupStart'))
    const pickupEnd = String(data.get('pickupEnd'))
    if (String(data.get('carrier')) === 'Other carrier' && !String(data.get('carrierOther') || '').trim()) { setError('Enter the name of your carrier.'); return }
    if (new Date(pickupStart) <= new Date()) { setError('Choose a pickup window that starts in the future.'); return }
    if (new Date(pickupEnd) <= new Date(pickupStart)) { setError('The pickup window must end after it starts.'); return }
    onSubmit({
      id: `BATCH-${Date.now().toString().slice(-6)}`, name: String(data.get('batchName')).trim(),
      carrier: String(data.get('carrier')) === 'Other carrier' ? String(data.get('carrierOther')).trim() : String(data.get('carrier')), dropoffLocation: String(data.get('dropoffLocation')).trim(),
      pickupAddress: String(data.get('pickupAddress')).trim(), pickupStart, pickupEnd, items,
      pickupWindow: `${formatDateTime(pickupStart)} – ${new Date(pickupEnd).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`,
      status: 'Batch ready · awaiting driver', createdAt: new Date().toISOString(),
    })
  }
  return <form className="etsy-batch-form" onSubmit={submit}>
    <div className="seller-form-heading"><div><span className="board-kicker">ETSY PARCEL SHIPPING</span><h3>Prepare a carrier batch</h3><p>Group the parcels you want one driver to take to the same carrier drop-off.</p></div><button type="button" className="seller-form-close" onClick={onCancel} aria-label="Close batch form">×</button></div>
    <div className="seller-form-grid"><div><label className="field-label" htmlFor="etsy-batch-name">Batch name</label><input className="garage-input" id="etsy-batch-name" name="batchName" placeholder="e.g. Friday Etsy orders" required /></div><div><label className="field-label" htmlFor="etsy-carrier">Carrier</label><select className="garage-input" id="etsy-carrier" name="carrier" defaultValue="USPS"><option>USPS</option><option>FedEx</option><option>UPS</option><option>Other carrier</option></select><small className="etsy-form-hint">For another carrier, enter its name here.</small><input className="garage-input" name="carrierOther" placeholder="Optional carrier name" /></div>
      <div><label className="field-label" htmlFor="etsy-pickup-address">Pickup from</label><input className="garage-input" id="etsy-pickup-address" name="pickupAddress" placeholder="Your home studio or pickup address" required /></div><div><label className="field-label" htmlFor="etsy-dropoff-location">Carrier drop-off location</label><input className="garage-input" id="etsy-dropoff-location" name="dropoffLocation" placeholder="Post office or ship center address" required /></div>
      <div><label className="field-label" htmlFor="etsy-pickup-start">Ready for pickup from</label><input className="garage-input" id="etsy-pickup-start" name="pickupStart" type="datetime-local" defaultValue={localDateTime(1, 9)} required /></div><div><label className="field-label" htmlFor="etsy-pickup-end">Ready until</label><input className="garage-input" id="etsy-pickup-end" name="pickupEnd" type="datetime-local" defaultValue={localDateTime(1, 12)} required /></div></div>
    <div className="etsy-package-heading"><div><strong>Parcels in this batch</strong><small>Enter packed dimensions so the driver can check vehicle fit.</small></div><label className="etsy-unit-select"><span>Units</span><select name="dimensionUnit" defaultValue="in"><option value="in">inches</option><option value="cm">cm</option></select></label></div>
    <div className="etsy-package-list" id="etsy-batch-dimensions">{packages.map((item, index) => <div className="etsy-package-row" key={item.id}><span className="etsy-package-number">{String(index + 1).padStart(2, '0')}</span><label className="etsy-package-name"><span>Product</span><input className="garage-input" value={item.name} onChange={event => updatePackage(item.id, 'name', event.target.value)} placeholder="Handmade item" /></label><label><span>L</span><input className="garage-input" type="number" min="0.1" step="0.1" value={item.length} onChange={event => updatePackage(item.id, 'length', event.target.value)} placeholder="0" /></label><label><span>W</span><input className="garage-input" type="number" min="0.1" step="0.1" value={item.width} onChange={event => updatePackage(item.id, 'width', event.target.value)} placeholder="0" /></label><label><span>H</span><input className="garage-input" type="number" min="0.1" step="0.1" value={item.height} onChange={event => updatePackage(item.id, 'height', event.target.value)} placeholder="0" /></label>{packages.length > 1 && <button type="button" className="remove-batch-package" aria-label={`Remove package ${index + 1}`} onClick={() => setPackages(current => current.filter(entry => entry.id !== item.id))}>×</button>}</div>)}</div>
    <button type="button" className="add-batch-package" onClick={() => setPackages(current => [...current, { id: `package-${Date.now()}`, name: '', length: '', width: '', height: '' }])}>＋ Add another parcel</button>
    {error && <div className="form-error" role="alert">{error}</div>}
    <div className="seller-pickup-explainer"><span>↗</span><p>Hitch can combine pickups from Etsy sellers in one neighborhood and carry all these parcels to this shared drop-off center.</p></div>
    <button className="seller-submit-pickup" type="submit">Request this batch pickup <span>→</span></button>
  </form>
}

function Brand() {
  return <a className="brand" href="/">hitch<span>.</span></a>
}

function Landing() {
  return (
    <main className="landing">
      <div className="landing-topbar"><Brand /><ThemeToggle /></div>
      <div className="landing-copy">
        <div className="eyebrow"><span className="eyebrow-dot" /> LOCAL DELIVERY, REIMAGINED</div>
        <h1>Good things<br />are <em>going places.</em></h1>
        <p>Choose a demo view to explore the Hitch delivery experience.</p>
      </div>
      <div className="view-picker">
        <a className="view-card shipper-card" href="/shipper?view=business"><span className="view-icon">↗</span><span className="view-meta">SHIPPER · DESKTOP</span><strong>Business shipper</strong><small>Manage local freight, driver pickups, and FedEx handoffs.</small><span className="view-arrow">Open view&nbsp; →</span></a>
        <a className="view-card etsy-card" href="/shipper?view=etsy"><span className="view-icon">E</span><span className="view-meta">HOME SELLER · ETSY</span><strong>Etsy seller</strong><small>Track local orders and parcels going to a carrier.</small><span className="view-arrow">Open view&nbsp; →</span></a>
        <a className="view-card facebook-card" href="/shipper?view=facebook"><span className="view-icon">f</span><span className="view-meta">HOME SELLER · MARKETPLACE</span><strong>Facebook Marketplace seller</strong><small>See local buyer handoffs and pickup times.</small><span className="view-arrow">Open view&nbsp; →</span></a>
        <a className="view-card carrier-card" href="/carrier"><span className="view-icon">⌁</span><span className="view-meta">DRIVER · MOBILE</span><strong>Driver</strong><small>Schedule your vehicle and follow your route.</small><span className="view-arrow">Open view&nbsp; →</span></a>
      </div>
      <footer className="landing-footer"><span>HITCH · SALT LAKE CITY, UTAH</span><span>DEMO EXPERIENCE</span></footer>
    </main>
  )
}

function Sidebar() {
  return <aside className="sidebar"><Brand /><div className="workspace-label">WORKSPACE</div><a className="nav-item active" href="/shipper"><span>▦</span> Overview</a><a className="nav-item" href="#deliveries"><span>↗</span> Orders <b>6</b></a><a className="nav-item" href="#drivers"><span>♧</span> Pickups</a><div className="sidebar-bottom"><div className="user-avatar">AT</div><div><strong>Acme Goods</strong><small>Shipper account</small></div><a href="/" aria-label="Back to views">⌄</a></div></aside>
}

function Shipper() {
  const view = new URLSearchParams(window.location.search).get('view')
  const shipperMode = view === 'etsy' || view === 'facebook' ? 'home' : 'business'
  const sellerPlatform = view === 'facebook' ? 'facebook' : 'etsy'
  const [activeFilter, setActiveFilter] = React.useState('All local orders')
  const [freightPeriod, setFreightPeriod] = React.useState('Week')
  const [sellerRequests, setSellerRequests] = React.useState([])
  const [showPickupForm, setShowPickupForm] = React.useState(false)
  const [etsyBatches, setEtsyBatches] = React.useState(getEtsyCarrierBatches)
  const [showBatchForm, setShowBatchForm] = React.useState(false)
  const [pickupRequestNotice, setPickupRequestNotice] = React.useState('')
  const [shipperOrders, setShipperOrders] = React.useState(sampleShipperOrders)
  const [pickupRuns, setPickupRuns] = React.useState(null)
  const [freightCycles, setFreightCycles] = React.useState(sampleFreightCycles)
  const [liveData, setLiveData] = React.useState(false)
  const [loadError, setLoadError] = React.useState('')
  const [submitting, setSubmitting] = React.useState(false)
  React.useEffect(() => {
    let cancelled = false
    const today = new Date()
    const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
    if (shipperMode === 'business') {
      // The API's week starts at `from`, so anchor it to this week's Monday.
      const monday = new Date(today)
      monday.setDate(today.getDate() - ((today.getDay() + 6) % 7))
      const mondayKey = `${monday.getFullYear()}-${String(monday.getMonth() + 1).padStart(2, '0')}-${String(monday.getDate()).padStart(2, '0')}`
      Promise.all([api('/shipper/orders?limit=200'), api(`/shipper/pickup-runs?date=${todayKey}`), api(`/shipper/freight-summary?period=week&from=${mondayKey}`), api(`/shipper/freight-summary?period=month&from=${todayKey}`)])
        .then(([orders, runs, week, month]) => {
          if (cancelled) return
          setShipperOrders(orders.orders.map(orderToRow))
          setPickupRuns(runs.pickup_runs)
          setFreightCycles({ Week: summaryToCycle(week), Month: summaryToCycle(month) })
          setLiveData(true)
          setLoadError('')
        })
        .catch(error => { if (!cancelled) { setLiveData(false); setLoadError(error.message) } })
    } else {
      const source = sellerPlatform === 'etsy' ? 'etsy' : 'facebook_marketplace'
      Promise.all([api(`/seller/pickup-requests?source=${source}&limit=200`), api('/shipper/orders?limit=200')])
        .then(([requests, orders]) => {
          if (cancelled) return
          setSellerRequests(requests.pickup_requests.map(requestToRow))
          setShipperOrders(orders.orders.filter(order => order.source === source).map(orderToRow))
          setLiveData(true)
          setLoadError('')
        })
        .catch(error => { if (!cancelled) { setLiveData(false); setLoadError(error.message) } })
    }
    return () => { cancelled = true }
  }, [shipperMode, sellerPlatform])
  const localOrders = shipperOrders.filter(order => order.tone !== 'third-party')
  const remoteOrders = shipperOrders.filter(order => order.tone === 'third-party')
  const sellerOrders = [...shipperOrders.filter(order => order.channel === (sellerPlatform === 'etsy' ? 'Etsy' : 'Facebook Marketplace')), ...sellerRequests]
  const sellerLocalOrders = sellerOrders.filter(order => order.tone !== 'third-party')
  const sellerDropoffs = sellerOrders.filter(order => order.tone === 'third-party')
  const firstSellerPickup = sellerLocalOrders.find(order => order.run)
  const filters = ['All local orders', 'Pickup scheduled', 'Waiting for pickup']
  const visibleOrders = localOrders.filter(order => activeFilter === 'All local orders' || (activeFilter === 'Pickup scheduled' ? Boolean(order.run) : !order.run)).sort((a, b) => a.pickupMinutes - b.pickupMinutes)
  const cycle = freightCycles[freightPeriod]
  const cycleMax = Math.max(1, ...cycle.labels.map((_, index) => cycle.independent[index] + cycle.fedex[index]))
  const independentShare = cycle.total ? Math.round(cycle.independentTotal / cycle.total * 100) : 0
  const fedexShare = cycle.total ? 100 - independentShare : 0
  async function submitPickupRequest(event) {
    event.preventDefault()
    const form = event.currentTarget
    const data = new FormData(form)
    const product = String(data.get('product') || '').trim()
    const dimensions = {
      length: Number(data.get('length')), width: Number(data.get('width')), height: Number(data.get('height')),
      unit: String(data.get('dimensionUnit') || 'in'),
    }
    const pickupStart = String(data.get('pickupStart') || '')
    const pickupEnd = String(data.get('pickupEnd') || '')
    if (new Date(pickupStart) <= new Date()) return setPickupRequestNotice('Choose a pickup window that starts in the future.')
    if (new Date(pickupEnd) <= new Date(pickupStart)) return setPickupRequestNotice('The end of the pickup window must be after the start.')
    setSubmitting(true)
    setPickupRequestNotice('')
    try {
      const [pickup, destination] = await Promise.all([geocode(data.get('pickupAddress')), geocode(data.get('destination'))])
      const created = await api('/seller/pickup-requests', { method: 'POST', body: {
        source: sellerPlatform === 'etsy' ? 'etsy' : 'facebook_marketplace', product_name: product,
        quantity: Number(data.get('quantity')) || 1, dimensions, notes: String(data.get('notes') || '').trim(),
        pickup, destination, available_window: { start: toIso(pickupStart), end: toIso(pickupEnd) },
      } })
      setSellerRequests(current => [requestToRow(created), ...current])
      setPickupRequestNotice(`${product} pickup request saved. Hitch will assign a local driver.`)
      setShowPickupForm(false)
      form.reset()
    } catch (error) {
      setPickupRequestNotice(error.message)
    } finally { setSubmitting(false) }
  }
  function submitEtsyBatch(batch) {
    const batches = [...etsyBatches, batch]
    localStorage.setItem(ETSY_BATCHES_KEY, JSON.stringify(batches))
    setEtsyBatches(batches)
    setPickupRequestNotice(`${batch.name} saved with ${batch.items.length} parcel${batch.items.length === 1 ? '' : 's'} for ${batch.carrier}.`)
    setShowBatchForm(false)
  }
  return (
    <div className="shipper-app">
      <Sidebar />
      <main className="shipper-main">
        <header className="dashboard-header"><div><div className="breadcrumb">SHIPPER WORKSPACE <span>/</span> {shipperMode === 'business' ? 'LOCAL FULFILLMENT' : 'SELLER PICKUPS'}</div><h1>{shipperMode === 'business' ? 'Good morning, Alex' : 'Your shop, on the move'} <span>✳</span></h1><p>{shipperMode === 'business' ? 'See what to have ready for each local pickup run.' : 'A simple view of local handoffs and orders going to a carrier.'}</p></div><div className="dashboard-actions"><ThemeToggle /><a className="avatar" href="/">AT</a></div></header>
        {shipperMode === 'business' ? <>
        <section className="freight-dashboard"><div className="freight-head"><div><span className="board-kicker">FREIGHT PERFORMANCE · UTAH</span><h2>Local freight cycle</h2><p>Independent driver pickups compared with orders still on FedEx.</p></div><div className="period-switch" role="group" aria-label="Freight cycle period">{['Week', 'Month'].map(period => <button key={period} className={freightPeriod === period ? 'selected' : ''} onClick={() => setFreightPeriod(period)}>{period}</button>)}</div></div>
          <div className="freight-content"><div className="freight-chart"><div className="chart-legend"><span><i className="legend-independent" /> Independent carriers</span><span><i className="legend-fedex" /> FedEx truck</span><small>ORDERS / PERIOD</small></div><div className="chart-bars">{cycle.labels.map((label, index) => { const total = cycle.independent[index] + cycle.fedex[index]; return <div className="chart-column" key={label}><div className="bar-stack" title={`${cycle.independent[index]} independent, ${cycle.fedex[index]} FedEx`}><i className="bar-independent" style={{ height: `${cycle.independent[index] / cycleMax * 100}%` }} /><i className="bar-fedex" style={{ height: `${cycle.fedex[index] / cycleMax * 100}%` }} /></div><small>{label}</small><em>{total}</em></div> })}</div></div>
            <div className="freight-mix"><span className="mix-label">{freightPeriod.toUpperCase()} TO DATE</span><strong>{cycle.total.toLocaleString()} <small>orders</small></strong><div className="mix-track"><i style={{ width: `${independentShare}%` }} /></div><div className="mix-row"><span><i className="legend-independent" /> Independent drivers</span><b>{cycle.independentTotal} <small>· {independentShare}%</small></b></div><div className="mix-row"><span><i className="legend-fedex" /> FedEx truck</span><b>{cycle.fedexTotal} <small>· {fedexShare}%</small></b></div><p><b>{cycle.fedexTotal}</b> orders in this sample cycle went with regular FedEx pickup.</p></div>
          </div><div className="freight-note"><i /> {liveData ? 'Live order counts from Hitch' : 'Sample freight history · the Hitch API is unavailable'}</div>
        </section>
        <section className="shipper-summary"><div><span>LOCAL ORDERS</span><strong>{localOrders.length}</strong><small>Within local service area</small></div><div><span>PICKUP TIME ASSIGNED</span><strong>{localOrders.filter(order => order.run).length}</strong><small>Backend scheduled driver pickups</small></div><div><span>WAITING ON PICKUP TIME</span><strong>{localOrders.filter(order => !order.run).length}</strong><small>Hitch will update when assigned</small></div></section>
        <section className="pickup-board" id="drivers"><div className="pickup-board-head"><div><span className="board-kicker">LOCAL SERVICE AREA · UTAH</span><h2>Upcoming driver pickups</h2><p>Have these orders ready during the assigned pickup window.</p></div><span className="pickup-count">{liveData ? pickupRuns.length : 2} pickup run{(liveData ? pickupRuns.length : 2) === 1 ? '' : 's'}</span></div>
          {liveData ? <div className="pickup-lanes">{pickupRuns.length === 0 && <p className="freight-note">No pickup runs scheduled for today.</p>}{pickupRuns.map((run, index) => { const runWindow = run.pickup_window || run.window; const orderCount = run.order_ids?.length ?? run.orders?.length ?? 0; return <article className={`pickup-lane ${index === 0 ? 'next-lane' : ''}`} key={run.id}><div className="lane-time"><span>PICKUP WINDOW</span><strong>{runWindow?.start ? timeOnly(runWindow.start) : '—'}</strong><em>{runWindow?.end ? `Ends ${timeOnly(runWindow.end)}` : ''}</em></div><div className="lane-carrier"><span className="carrier-mark">{(run.driver_name || '?').split(' ').map(part => part[0]).join('').slice(0, 2)}</span><div><strong>{run.driver_name || 'Driver pending'}</strong><small>Independent driver{run.vehicle_name ? ` · ${run.vehicle_name}` : ''}</small></div></div><div className="lane-shipment"><span className="staging-icon">▦</span><div><strong>{orderCount} order{orderCount === 1 ? '' : 's'} to have ready</strong><small>{run.label || run.id}</small></div></div><span className="lane-state staged">Have ready</span></article> })}</div> : <div className="pickup-lanes"><article className="pickup-lane next-lane"><div className="lane-time"><span>PICKUP WINDOW</span><strong>10:30 <small>AM</small></strong><em>Ends 11:00 AM</em></div><div className="lane-carrier"><span className="carrier-mark orange-mark">JL</span><div><strong>Jordan Lee</strong><small>Independent driver · pickup truck</small></div></div><div className="lane-shipment"><span className="staging-icon">▦</span><div><strong>2 orders to have ready</strong><small>Pickup run 204 · Sugar House</small></div></div><span className="lane-state staged">Have ready</span></article>
          <article className="pickup-lane"><div className="lane-time"><span>PICKUP WINDOW</span><strong>11:00 <small>AM</small></strong><em>Ends 12:00 PM</em></div><div className="lane-carrier"><span className="carrier-mark">SR</span><div><strong>Sam Rivera</strong><small>Independent driver · sedan</small></div></div><div className="lane-shipment"><span className="staging-icon">▦</span><div><strong>2 orders to have ready</strong><small>Pickup run 205 · Millcreek</small></div></div><span className="lane-state awaiting">Have ready</span></article></div>
        }</section>
        <section className="delivery-section orders-section" id="deliveries"><div className="section-heading"><div><div className="board-kicker">FULFILLMENT QUEUE · SORTED BY PICKUP TIME</div><h2>Orders to have ready <b className="heading-count">{visibleOrders.length}</b></h2><p>Pickup windows and driver assignments are set automatically.</p></div></div>
          <div className="order-toolbar"><div className="order-filters">{filters.map(filter => <button key={filter} className={activeFilter === filter ? 'filter-active' : ''} onClick={() => setActiveFilter(filter)}>{filter}{filter === 'Waiting for pickup' && <b>{localOrders.filter(order => !order.run).length}</b>}</button>)}</div><span>Today, Nov 6 <span className="calendar-mark">⌄</span></span></div>
          <div className="delivery-table order-table"><div className="order-table-head"><span>ORDER</span><span>DELIVER TO</span><span>DRIVER / PICKUP RUN</span><span>BE READY BY</span><span>STATUS</span></div>{visibleOrders.map(order => <article className="order-row" key={order.id}><div className="order-main"><span className="package-icon order-box">▧</span><div><strong>{order.item}</strong><small>{order.id} · {order.customer}</small><em>{order.detail}</em></div></div><div className="order-destination"><span className="destination-pin">⌖</span><div><strong>{order.to}</strong><small>From {order.from}</small></div></div><div className="order-shipment"><strong>{order.driver || 'Assignment in progress'}</strong><small>{order.run || 'Pickup run not assigned'}</small></div><div className={`order-window ${order.run ? '' : 'window-open'}`}>{order.pickup}<small>{order.run ? 'Assigned pickup window' : 'Hitch will notify you'}</small></div><div><span className={`order-status ${order.tone}`}><i />{order.status}</span></div></article>)}</div>
        </section>
        {remoteOrders.length > 0 && <section className="third-party-panel"><div className="third-party-heading"><span className="third-party-icon">↗</span><div><span className="board-kicker">OUTSIDE LOCAL DRIVER AREA</span><h2>Other carrier handoffs <b>{remoteOrders.length}</b></h2><p>Long-distance orders continue through your 3PL or parcel carrier.</p></div></div>{remoteOrders.map(order => <div className="third-party-order" key={order.id}><div><strong>{order.item}</strong><small>{order.id} · {order.channel} · {order.from} to {order.to}</small></div><span className="third-party-driver">{order.driver} · {order.pickup}</span><span className="order-status third-party">{order.status}</span></div>)}</section>}
        <div className="service-area"><span className="board-kicker">LOCAL DRIVER COVERAGE</span><p>{localServiceAreas.map((area, index) => <React.Fragment key={area}><b>{area}</b>{index < localServiceAreas.length - 1 && <span> · </span>}</React.Fragment>)}</p><small>Orders outside these areas route to a 3PL or parcel carrier.</small></div>
        </> : <section className="home-seller-view">
          <div className={`seller-welcome ${sellerPlatform}`}><div><span className="board-kicker">{sellerPlatform === 'etsy' ? 'ETSY HOME SHOP · MIA CHEN' : 'FACEBOOK MARKETPLACE · MIA CHEN'}</span><h2>{sellerPlatform === 'etsy' ? 'Your Etsy shop' : 'Your Marketplace sales'}</h2><p>{sellerPlatform === 'etsy' ? 'A small-shop workspace for packing orders, arranging local delivery, and preparing carrier batches from home.' : 'Keep local buyer pickups simple. We’ll tell you what to have ready.'}</p></div><span className={`seller-avatar ${sellerPlatform}`}>{sellerPlatform === 'etsy' ? 'E' : 'f'}</span></div>
          {sellerPlatform === 'etsy' && <div className="etsy-shop-tools"><div className="etsy-shop-metrics"><div><span>ORDERS TO PREP</span><strong>{sellerLocalOrders.length + sellerDropoffs.length + etsyBatches.reduce((sum, batch) => sum + batch.items.length, 0)}</strong><small>Across local and parcel orders</small></div><div><span>LOCAL HANDOFFS</span><strong>{sellerLocalOrders.length}</strong><small>Driver brings directly to buyers</small></div><div><span>PARCELS TO SHIP</span><strong>{sellerDropoffs.length + etsyBatches.reduce((sum, batch) => sum + batch.items.length, 0)}</strong><small>Driver takes to carrier of your choice</small></div></div><div className="etsy-shop-guide"><b>From your worktable to the right destination</b><span>Local customer → direct delivery</span><span>Online order → pack it, choose a carrier, and prepare a batch for pickup</span></div></div>}
          {firstSellerPickup && <div className="seller-next-pickup"><div className="seller-pickup-icon">↗</div><div className="seller-next-copy"><span>NEXT DRIVER PICKUP · TODAY</span><strong>{firstSellerPickup.pickup}</strong><small>{firstSellerPickup.driver} · independent local driver</small></div><b className="seller-ready-badge">{sellerLocalOrders.filter(order => order.run === firstSellerPickup.run).length} order{sellerLocalOrders.filter(order => order.run === firstSellerPickup.run).length === 1 ? '' : 's'} to have ready</b></div>}
          <div className="seller-orders-heading"><div><span className="board-kicker">{sellerPlatform === 'etsy' ? 'ETSY FULFILLMENT' : 'MARKETPLACE FULFILLMENT'}</span><h2>{sellerPlatform === 'etsy' ? 'Local orders to prepare' : 'Today’s handoffs'}</h2></div><div className="seller-order-actions"><span>{sellerLocalOrders.filter(order => order.run).length} scheduled · {sellerLocalOrders.filter(order => !order.run).length} waiting</span><div className="seller-action-buttons">{sellerPlatform === 'etsy' && <button type="button" className="seller-request-button secondary" onClick={() => { setShowBatchForm(value => !value); setPickupRequestNotice('') }}>＋ Prepare carrier batch</button>}<button type="button" className="seller-request-button" onClick={() => { setShowPickupForm(value => !value); setPickupRequestNotice('') }}>＋ {sellerPlatform === 'etsy' ? 'Request local delivery' : 'Request a pickup'}</button></div></div></div>
          {pickupRequestNotice && !showPickupForm && <div className="pickup-request-notice" role="status">{pickupRequestNotice}</div>}
          {sellerPlatform === 'etsy' && showBatchForm && <EtsyCarrierBatchForm onSubmit={submitEtsyBatch} onCancel={() => setShowBatchForm(false)} />}
          {showPickupForm && <form className="seller-pickup-form" onSubmit={submitPickupRequest}><div className="seller-form-heading"><div><span className="board-kicker">NEW LOCAL PICKUP</span><h3>What do you need moved?</h3><p>Tell us what it is, where to collect it, where it needs to go, and when you’ll be ready.</p></div><button type="button" className="seller-form-close" onClick={() => setShowPickupForm(false)} aria-label="Close pickup request">×</button></div>
            <div className="seller-form-grid"><div><label className="field-label" htmlFor="pickup-product">Product or item</label><input className="garage-input" id="pickup-product" name="product" placeholder="e.g. Vintage side table" required /></div><div><label className="field-label" htmlFor="pickup-quantity">Number of packages</label><select className="garage-input" id="pickup-quantity" name="quantity" defaultValue="1"><option value="1">1 package</option><option value="2">2 packages</option><option value="3">3 packages</option><option value="4">4 packages</option><option value="5">5+ packages</option></select></div>
              <div><label className="field-label" htmlFor="pickup-address">Pickup location</label><input className="garage-input" id="pickup-address" name="pickupAddress" placeholder="Your address or meetup spot" required /></div><div><label className="field-label" htmlFor="pickup-destination">Deliver to</label><input className="garage-input" id="pickup-destination" name="destination" placeholder="Buyer address or carrier drop-off" required /></div>
              <div className="seller-dimensions-field"><label className="field-label">Product dimensions <small>Measure the packed item</small></label><div className="seller-dimensions"><label><span>Length</span><input className="garage-input" name="length" type="number" min="0.1" step="0.1" placeholder="0" required /></label><label><span>Width</span><input className="garage-input" name="width" type="number" min="0.1" step="0.1" placeholder="0" required /></label><label><span>Height</span><input className="garage-input" name="height" type="number" min="0.1" step="0.1" placeholder="0" required /></label><label><span>Unit</span><select className="garage-input" name="dimensionUnit" defaultValue="in"><option value="in">in</option><option value="cm">cm</option></select></label></div></div>
              <div><label className="field-label" htmlFor="pickup-window-start">Available for pickup from</label><input className="garage-input" id="pickup-window-start" name="pickupStart" type="datetime-local" defaultValue={localDateTime(1, 9)} required /></div><div><label className="field-label" htmlFor="pickup-window-end">Available for pickup until</label><input className="garage-input" id="pickup-window-end" name="pickupEnd" type="datetime-local" defaultValue={localDateTime(1, 12)} required /></div>
              <div className="seller-form-notes"><label className="field-label" htmlFor="pickup-notes">Notes for the driver <small>Optional</small></label><input className="garage-input" id="pickup-notes" name="notes" placeholder="Size, fragile, meetup details…" /></div></div>
            <div className="seller-pickup-explainer"><span>◷</span><p>This is when you can hand off the item. Hitch will find a driver and confirm the pickup as the route is planned.</p></div>
            {pickupRequestNotice && <div className="form-error" role="alert">{pickupRequestNotice}</div>}
            <button className="seller-submit-pickup" type="submit" disabled={submitting}>{submitting ? 'Sending request…' : 'Request this pickup'} <span>→</span></button>
          </form>}
          <div className="seller-order-list">{sellerLocalOrders.map(order => <article className="seller-order-card" key={order.id}><span className="seller-order-icon">{sellerPlatform === 'etsy' ? 'E' : 'f'}</span><div className="seller-order-copy"><strong>{order.item}</strong><small>{order.id} · Deliver to {order.to}</small>{order.detail && <small className="seller-item-detail">{order.detail}</small>}</div><div className="seller-order-time"><strong>{order.pickup}</strong><small>{order.driver || (order.userRequest ? 'Request sent · driver not assigned' : 'Pickup time being arranged')}</small></div><span className={`order-status ${order.tone}`}>{order.run ? 'Have ready' : order.userRequest ? 'Request sent' : 'Waiting for pickup time'}</span></article>)}</div>
          {sellerPlatform === 'etsy' && (etsyBatches.length > 0 || sellerDropoffs.length > 0) && <div className="etsy-batches"><div className="etsy-batches-heading"><div><span className="board-kicker">PACKED ORDERS HEADING TO A CARRIER</span><h3>Carrier batches</h3></div><span>{etsyBatches.length + sellerDropoffs.length} batch{etsyBatches.length + sellerDropoffs.length === 1 ? '' : 'es'}</span></div>{sellerDropoffs.map(order => <article className="etsy-batch-card" key={order.id}><span className="batch-carrier-icon">▣</span><div><strong>{order.item} · {order.dropoffLocation}</strong><small>{order.id} · {order.pickup} · Driver {order.driver}</small></div><span className="order-status third-party">Scheduled</span></article>)}{etsyBatches.map(batch => <article className="etsy-batch-card" key={batch.id}><span className="batch-carrier-icon">▣</span><div><strong>{batch.name} · {batch.carrier}</strong><small>{batch.items.length} parcel{batch.items.length === 1 ? '' : 's'} · {batch.dropoffLocation}</small><small>{batch.pickupAddress} · {batch.pickupWindow}</small></div><span className="order-status open">Awaiting driver</span></article>)}</div>}
          <div className="seller-driver-note"><span>↗</span><p>{sellerPlatform === 'etsy' ? 'Choose the carrier and drop-off point for each batch. Hitch can collect parcels from Etsy shops near one another and take them together to the same post office or ship center.' : 'Marketplace drivers pick up from you and deliver directly to local buyers. Hitch can group multiple sellers and buyers into one route.'}</p></div>
          <div className="service-area"><span className="board-kicker">LOCAL DELIVERY AREAS</span><p>{localServiceAreas.map((area, index) => <React.Fragment key={area}><b>{area}</b>{index < localServiceAreas.length - 1 && <span> · </span>}</React.Fragment>)}</p><small>Long-distance orders go through a 3PL or parcel carrier.</small></div>
        </section>}
        <footer className="dashboard-footer"><span className="demo-indicator"><i /> {liveData ? 'Live fulfillment data · Salt Lake City' : `Sample fulfillment data${loadError ? ` · ${loadError}` : ''}`}</span><a href="/">Switch demo view</a></footer>
      </main>
    </div>
  )
}

function vehicleDisplayName(vehicle) {
  return [vehicle.year, vehicle.make, vehicle.model].filter(Boolean).join(' ') || 'Saved vehicle'
}

function localDateTime(daysAhead, hour) {
  const date = new Date()
  date.setDate(date.getDate() + daysAhead)
  date.setHours(hour, 0, 0, 0)
  const offset = date.getTimezoneOffset()
  return new Date(date.getTime() - offset * 60_000).toISOString().slice(0, 16)
}

function useVehicles() {
  const [state, setState] = React.useState({ vehicles: [], loading: true, error: '' })
  React.useEffect(() => {
    let cancelled = false
    api('/driver/vehicles')
      .then(data => { if (!cancelled) setState({ vehicles: data.vehicles.map(vehicleFromApi), loading: false, error: '' }) })
      .catch(error => { if (!cancelled) setState({ vehicles: [], loading: false, error: error.message }) })
    return () => { cancelled = true }
  }, [])
  const setVehicles = updater => setState(current => ({ ...current, vehicles: typeof updater === 'function' ? updater(current.vehicles) : updater }))
  return { ...state, setVehicles }
}

// Availability blocks, re-fetched every minute so the server's forming -> locked change shows up.
function useAvailability() {
  const [state, setState] = React.useState({ blocks: [], loading: true, error: '' })
  const load = React.useCallback(() => api('/driver/availability')
    .then(data => setState({ blocks: data.availability.map(availabilityFromApi).sort((a, b) => new Date(a.start) - new Date(b.start)), loading: false, error: '' }))
    .catch(error => setState(current => ({ ...current, loading: false, error: error.message }))), [])
  React.useEffect(() => {
    load()
    const interval = window.setInterval(load, 60_000)
    return () => window.clearInterval(interval)
  }, [load])
  return { ...state, reload: load }
}

function formatDateTime(value) {
  return new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(value))
}

function dispatchIsLocked(block) {
  return Boolean(block.locked)
}

function DriverNav({ active }) {
  return <nav className="carrier-nav">
    <a className={active === 'home' ? 'selected' : ''} href="/carrier"><span>⌂</span>Home</a>
    <a className={active === 'availability' ? 'selected' : ''} href="/carrier/availability"><span>＋</span>Availability</a>
    <a className={active === 'activity' ? 'selected' : ''} href="/carrier/activity"><span>↗</span>Activity</a>
    <a className={active === 'profile' ? 'selected' : ''} href="/carrier/garage"><span>▰</span>Garage</a>
  </nav>
}

function DriverHeader({ title }) {
  return <header className="carrier-header"><a className="back-to-views" href="/" aria-label="Choose a demo view">‹</a><Brand /><ThemeToggle /><a className="carrier-profile" href="/carrier/garage" aria-label="Vehicle garage">JD</a></header>
}

function AvailabilityCard({ block, selected, onClick }) {
  const capacityPercent = Math.min(100, (block.capacity ? Math.round((block.bookedCapacity / block.capacity) * 100) : 0))
  const locked = dispatchIsLocked(block)
  if (!locked) return <button className={`availability-card route-select-card forming-availability-card ${selected ? 'route-selected' : ''}`} onClick={onClick} aria-expanded={selected}>
    <div className="forming-card-main"><div className="forming-date"><strong>{new Date(block.start).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })}</strong><span>{new Date(block.start).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}–{new Date(block.end).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</span></div><div className="forming-card-location"><strong>{block.startLocation}</strong><small>{block.vehicleLabel || 'Vehicle selected'} · {block.capacity} spots</small></div><span className="dispatch-badge forming"><i />Forming</span></div>
    <div className="forming-card-progress"><span>Likely fill</span><div className="progress-track"><i style={{ width: `${block.estimatedFill ?? Math.max(38, capacityPercent)}%` }} /></div><strong>{block.estimatedFill ?? Math.max(38, capacityPercent)}%</strong><span className="forming-card-hint">{selected ? 'Hide estimate' : 'View estimate'}⌄</span></div>
  </button>
  return <button className={`availability-card route-select-card ${selected ? 'route-selected' : ''}`} onClick={onClick} aria-expanded={selected}>
    <div className="availability-card-top"><div><span className="card-date-label">LOCKED ROUTE · {new Date(block.start).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })}</span><h3>{new Date(block.start).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })} departure</h3></div><span className="dispatch-badge locked"><i />Route confirmed</span></div>
    <div className="block-time"><span>DRIVER WINDOW</span><strong>{new Date(block.start).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })} – {new Date(block.end).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</strong></div>
    <div className="block-location"><span className="location-pin">⌖</span><div><small>STARTING LOCATION</small><strong>{block.startLocation}</strong></div><span className="fuel-level"><b>⛽</b> {block.fuel}%</span></div>
    <div className="selected-vehicle"><span>▰</span><div><small>CONFIRMED VEHICLE</small><strong>{block.vehicleLabel || 'Vehicle not selected · demo availability'}</strong></div><b>{block.capacity} package spots</b></div>
    <div className="locked-load"><div><span>CONFIRMED ROUTE LOAD</span><strong>{block.bookedCapacity} of {block.capacity} package spots</strong></div><div className="progress-track"><i style={{ width: `${capacityPercent}%` }} /></div><small>{block.fuel}% starting fuel</small></div>
        {!locked && <div className="lock-note"><span>◷</span> Hitch is building your route · locks 12 hours before departure</div>}
    <div className="route-card-hint">{locked ? 'Open locked route' : selected ? 'Hide route estimate' : 'View route estimate'} <span>{locked ? '→' : selected ? '⌃' : '⌄'}</span></div>
  </button>
}

function FormingRouteDetails({ block, onCancelled }) {
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const fill = Math.max(0, Math.min(100, Number(block.estimatedFill ?? 0)))
  const locksAt = new Date(block.locksAt)
  async function cancel() {
    setBusy(true)
    setError('')
    try { await api(`/driver/availability/${encodeURIComponent(block.id)}`, { method: 'DELETE' }); onCancelled?.() } catch (cancelError) { setError(cancelError.message) } finally { setBusy(false) }
  }
  return <section className="forming-route-details"><div><span className="card-date-label">HITCH IS BUILDING THIS ROUTE</span><strong>Estimated route fill · {fill}%</strong></div><div className="progress-track"><i style={{ width: `${fill}%` }} /></div><p>As orders are matched to this time block, the route estimate will update. The stop list and map preview appear when the route locks.</p><small>Route locks {formatDateTime(locksAt)}</small>{error && <div className="form-error" role="alert">{error}</div>}<button type="button" className="remove-vehicle" onClick={cancel} disabled={busy}>{busy ? 'Cancelling…' : 'Cancel this availability'}</button></section>
}

function decodeRouteShape(encoded, precision = 6) {
  let index = 0
  let lat = 0
  let lon = 0
  const coordinates = []
  const factor = 10 ** precision
  while (index < encoded.length) {
    let result = 0
    let shift = 0
    let byte
    do { byte = encoded.charCodeAt(index++) - 63; result |= (byte & 0x1f) << shift; shift += 5 } while (byte >= 0x20)
    lat += result & 1 ? ~(result >> 1) : result >> 1
    result = 0
    shift = 0
    do { byte = encoded.charCodeAt(index++) - 63; result |= (byte & 0x1f) << shift; shift += 5 } while (byte >= 0x20)
    lon += result & 1 ? ~(result >> 1) : result >> 1
    coordinates.push({ lat: lat / factor, lon: lon / factor })
  }
  return coordinates
}

function tilePoint({ lat, lon }, zoom) {
  const scale = 256 * 2 ** zoom
  const safeLat = Math.max(-85.0511, Math.min(85.0511, lat)) * Math.PI / 180
  return { x: (lon + 180) / 360 * scale, y: (1 - Math.log(Math.tan(safeLat) + 1 / Math.cos(safeLat)) / Math.PI) / 2 * scale }
}

function RouteMap({ points, stops }) {
  const width = 600
  const height = 340
  const zoom = 11
  const projected = points.map(point => tilePoint(point, zoom))
  const allProjected = [...projected, ...stops.map(stop => tilePoint(stop, zoom))]
  let minX = Math.min(...allProjected.map(point => point.x))
  let maxX = Math.max(...allProjected.map(point => point.x))
  let minY = Math.min(...allProjected.map(point => point.y))
  let maxY = Math.max(...allProjected.map(point => point.y))
  let spanX = Math.max(maxX - minX, 700)
  let spanY = Math.max(maxY - minY, 500)
  const aspect = width / height
  if (spanX / spanY < aspect) spanX = spanY * aspect
  else spanY = spanX / aspect
  const centerX = (minX + maxX) / 2
  const centerY = (minY + maxY) / 2
  minX = centerX - spanX / 2
  maxX = centerX + spanX / 2
  minY = centerY - spanY / 2
  maxY = centerY + spanY / 2
  const screen = point => { const pixel = tilePoint(point, zoom); return { x: (pixel.x - minX) / spanX * width, y: (pixel.y - minY) / spanY * height } }
  const firstTileX = Math.floor(minX / 256)
  const lastTileX = Math.floor(maxX / 256)
  const firstTileY = Math.floor(minY / 256)
  const lastTileY = Math.floor(maxY / 256)
  const tiles = []
  for (let x = firstTileX; x <= lastTileX; x += 1) for (let y = firstTileY; y <= lastTileY; y += 1) tiles.push({ x, y, left: (x * 256 - minX) / spanX * width, top: (y * 256 - minY) / spanY * height, sizeX: 256 / spanX * width, sizeY: 256 / spanY * height })
  const routeLine = points.map(point => { const pos = screen(point); return `${pos.x},${pos.y}` }).join(' ')
  return <div className="osm-map"><div className="osm-tiles">{tiles.map(tile => <img key={`${tile.x}-${tile.y}`} src={`https://tile.openstreetmap.org/${zoom}/${tile.x}/${tile.y}.png`} alt="" style={{ left: tile.left, top: tile.top, width: tile.sizeX + 1, height: tile.sizeY + 1 }} loading="lazy" />)}</div><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Route preview map"><polyline className="route-line-shadow" points={routeLine} /><polyline className="route-line" points={routeLine} />{stops.map((stop, index) => { const point = screen(stop); return <g className={`route-stop ${stop.type}`} key={stop.id} transform={`translate(${point.x} ${point.y})`}><circle r="11" /><text textAnchor="middle" dominantBaseline="central">{stop.type === 'pickup' ? 'P' : stop.type === 'dropoff' ? 'D' : stop.type === 'carrier_dropoff' ? 'C' : 'S'}</text><title>{`${index + 1}. ${stop.label}`}</title></g> })}</svg><div className="osm-legend"><span><i className="pickup-key" /> Pickup</span><span><i className="dropoff-key" /> Drop-off</span><span><i className="carrier-dropoff-key" /> Carrier center</span><span><i className="start-key" /> Start</span></div><a className="osm-attribution" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a></div>
}

function normalizeRoute(route) {
  const stops = (route.stops || []).map((stop, index) => {
    const location = stop.location || stop
    return { ...stop, id: stop.id || `stop-${index + 1}`, type: stop.type === 'delivery' ? 'dropoff' : stop.type, label: stop.label || stop.address || stop.name || `Stop ${index + 1}`, lat: Number(location.lat), lon: Number(location.lon) }
  })
  const geometryData = route.geometry || {}
  let geometry = []
  if (Array.isArray(geometryData.coordinates)) {
    geometry = geometryData.coordinates.map(coordinate => Array.isArray(coordinate) ? { lon: Number(coordinate[0]), lat: Number(coordinate[1]) } : { lat: Number(coordinate.lat), lon: Number(coordinate.lon) })
  } else {
    const encoded = geometryData.encoded_polyline || geometryData.shape || route.encoded_polyline
    if (encoded) geometry = decodeRouteShape(encoded, geometryData.precision ?? 6)
  }
  return { stops, geometry }
}

function RouteDetails({ block, locked }) {
  const [routeRecord, setRouteRecord] = React.useState(null)
  const [routeState, setRouteState] = React.useState(block.routeId ? 'loading' : 'missing')
  const [actionError, setActionError] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const routeId = block.routeId
  const { stops, geometry } = React.useMemo(() => routeRecord ? normalizeRoute(routeRecord) : { stops: [], geometry: [] }, [routeRecord])
  const routePoints = geometry.length ? geometry : stops
  React.useEffect(() => {
    if (!routeId) return undefined
    let cancelled = false
    setRouteState('loading')
    api(`/driver/routes/${encodeURIComponent(routeId)}`)
      .then(route => { if (!cancelled) { setRouteRecord(route); setRouteState('ready') } })
      .catch(() => { if (!cancelled) setRouteState('error') })
    return () => { cancelled = true }
  }, [routeId])
  const receiptData = routeRecord?.receipt
  const receipts = (receiptData?.lines || receiptData?.payouts || []).map(line => ({ shipper: line.shipper_name || line.shipper, item: line.product_name || line.item || line.description, amount: Number(line.amount) }))
  const total = Number(receiptData?.total ?? receiptData?.total_payout ?? receipts.reduce((sum, receipt) => sum + receipt.amount, 0))
  const nextIndex = routeRecord?.next_stop_id ? stops.findIndex(stop => stop.id === routeRecord.next_stop_id) : -1
  const progress = routeRecord ? { started: Boolean(routeRecord.started_at), complete: Boolean(routeRecord.completed_at), currentStopIndex: Math.max(nextIndex, 0) } : null
  const nextStop = progress?.started && !progress?.complete ? stops[progress.currentStopIndex] : null
  const fuelStop = routeRecord?.fuel_stops?.[0]
  async function runAction(action) {
    setBusy(true)
    setActionError('')
    try { await action() } catch (error) { setActionError(error.message) } finally { setBusy(false) }
  }
  function startRoute() {
    return runAction(async () => {
      let route = await api(`/driver/routes/${encodeURIComponent(routeId)}/start`, { method: 'POST' })
      // The first stop is the driver's own start point; it needs no separate "arrived" tap.
      const first = (route.stops || []).find(stop => stop.id === route.next_stop_id)
      if (first?.type === 'start') route = (await api(`/driver/routes/${encodeURIComponent(routeId)}/stops/${encodeURIComponent(first.id)}/complete`, { method: 'POST' })).route
      setRouteRecord(route)
    })
  }
  function completeStop() {
    return runAction(async () => {
      const result = await api(`/driver/routes/${encodeURIComponent(routeId)}/stops/${encodeURIComponent(nextStop.id)}/complete`, { method: 'POST' })
      setRouteRecord(result.route)
    })
  }
  const mapsUrl = nextStop ? `https://www.google.com/maps/dir/?api=1&destination=${nextStop.lat}%2C${nextStop.lon}&travelmode=driving&dir_action=navigate` : ''
  return <section className="route-details" aria-label="Route details"><div className="route-details-heading"><div><span className="card-date-label">{locked ? 'LOCKED ROUTE PREVIEW' : 'ROUTE PREVIEW · STILL FORMING'}</span><h3>{block.startLocation} route</h3></div>{!locked && <span className="route-building-label"><i /> Route can still change</span>}</div>
    {stops.length > 0 && <div className="route-map-frame"><RouteMap points={routePoints} stops={stops} /></div>}
    <div className="route-map-caption">{routeState === 'ready' ? (geometry.length ? 'Route line from Hitch routing · OpenStreetMap' : 'Route line not available yet · showing planned stop order') : routeState === 'loading' ? 'Loading route from Hitch…' : routeState === 'missing' ? 'Hitch has not attached a route to this block yet.' : 'Could not load this route. Check your connection and try again.'}</div>
    <div className="route-stop-list">{stops.map((stop, index) => <a className={`route-stop-row ${stop.type}`} key={stop.id} href={`https://www.google.com/maps/dir/?api=1&destination=${stop.lat}%2C${stop.lon}&travelmode=driving&dir_action=navigate`} aria-label={`Get Google Maps directions to ${stop.label}`}><span>{stop.type === 'pickup' ? 'P' : stop.type === 'dropoff' ? 'D' : stop.type === 'carrier_dropoff' ? 'C' : 'S'}</span><small>{String(index + 1).padStart(2, '0')}</small><strong>{stop.label}</strong><b>Directions ↗</b></a>)}</div>
    <div className="navigation-panel">{!progress?.started ? <><div><span className="navigation-icon">➤</span><div><strong>{locked ? 'Ready to start?' : 'Route is still forming'}</strong><small>{locked ? 'Start when you are ready to head out.' : 'Navigation becomes available once Hitch locks your route.'}</small></div></div>{locked ? <button className="start-route-button" onClick={startRoute} disabled={busy || routeState !== 'ready'}>{busy ? 'Starting…' : 'Start route'} <span>→</span></button> : <button className="start-route-button disabled" disabled>Start route <span>◷</span></button>}</> : progress.complete ? <div className="route-complete"><span>✓</span><div><strong>Route complete</strong><small>All stops have been marked complete.</small></div></div> : <><div className="next-stop-heading"><span className={`next-stop-marker ${nextStop.type}`}>{nextStop.type === 'pickup' ? 'P' : nextStop.type === 'carrier_dropoff' ? 'C' : 'D'}</span><div><small>NEXT STOP · {nextStop.type.replace('_', ' ').toUpperCase()}</small><strong>{nextStop.label.replace(/^(Pickup|Drop-off|Etsy pickup|Carrier drop-off) · /, '')}</strong></div><b>{progress.currentStopIndex} of {stops.length - 1}</b></div><a className="navigate-button" href={mapsUrl}>Navigate to this stop in Google Maps <span>↗</span></a><button className="complete-stop-button" onClick={completeStop} disabled={busy}>{busy ? 'Saving…' : 'Mark stop complete'} <span>✓</span></button></>}</div>
    {actionError && <div className="form-error" role="alert">{actionError}</div>}
    {fuelStop && <div className="fuel-stop-card"><span className="fuel-stop-icon">⛽</span><div><small>{fuelStop.is_stale ? 'FUEL STOP NEAR YOUR ROUTE · PRICE MAY BE OUT OF DATE' : 'SUGGESTED FUEL STOP NEAR YOUR ROUTE'}</small><strong>{fuelStop.name}{fuelStop.address ? ` · ${fuelStop.address}` : ''}</strong><span>{[fuelStop.price_label || (fuelStop.price != null ? `$${Number(fuelStop.price).toFixed(2)} / ${fuelStop.price_unit || 'gal'}` : ''), fuelStop.detour_label || (fuelStop.detour_minutes != null ? `approx. ${fuelStop.detour_minutes} min detour` : ''), fuelStop.updated_at ? `updated ${formatDateTime(fuelStop.updated_at)}` : ''].filter(Boolean).join(' · ')}</span></div><b>↗</b></div>}
    {locked && receipts.length > 0 && <div className="driver-receipt"><div className="receipt-heading"><div><span className="card-date-label">DRIVER PAYOUT RECEIPT</span><h3>What each shipper is paying</h3></div><span className="receipt-locked">✓ Locked</span></div>{receipts.map((receipt, index) => <div className="receipt-line" key={`${receipt.shipper}-${receipt.item}`}><span className="receipt-index">{String(index + 1).padStart(2, '0')}</span><div><strong>{receipt.shipper}</strong><small>{receipt.item}</small></div><b>${receipt.amount.toFixed(2)}</b></div>)}<div className="receipt-total"><span>Estimated route payout</span><strong>${total.toFixed(2)}</strong></div><small className="receipt-note">Final payment details will be confirmed by Hitch</small></div>}
  </section>
}

function DriverDashboard() {
  const [selectedRoute, setSelectedRoute] = React.useState(null)
  const { blocks, loading, error, reload } = useAvailability()
  const [lastMinute, setLastMinute] = React.useState({ request: null, accepted: null, busy: false, error: '' })
  const [showExtraRoute, setShowExtraRoute] = React.useState(false)
  const [extraRoute, setExtraRoute] = React.useState({ state: 'idle', points: null, minutes: null })
  const loadLastMinute = React.useCallback(() => api('/driver/last-minute-requests')
    .then(data => setLastMinute(current => ({ ...current, request: data.requests.find(item => item.fit) || null })))
    .catch(() => { /* no card if the API is unavailable */ }), [])
  React.useEffect(() => {
    loadLastMinute()
    const interval = window.setInterval(loadLastMinute, 60_000)
    return () => window.clearInterval(interval)
  }, [loadLastMinute])
  const lmRequest = lastMinute.request
  React.useEffect(() => { setExtraRoute({ state: 'idle', points: null, minutes: null }) }, [lmRequest?.id])
  React.useEffect(() => {
    if (!showExtraRoute || !lmRequest || extraRoute.state !== 'idle') return
    setExtraRoute({ state: 'loading', points: null, minutes: null })
    // Road geometry comes from the Valhalla container via the backend.
    api('/driver/route-preview', { method: 'POST', body: { locations: lmRequest.preview_stops.map(({ lat, lon, label }) => ({ lat, lon, address: label })) } })
      .then(data => setExtraRoute({ state: 'ready', points: data.geometry.coordinates.map(([lon, lat]) => ({ lat, lon })), minutes: data.time_minutes }))
      .catch(() => setExtraRoute({ state: 'fallback', points: null, minutes: null }))
  }, [showExtraRoute, lmRequest, extraRoute.state])
  async function coverLastMinute() {
    setLastMinute(current => ({ ...current, busy: true, error: '' }))
    try {
      const result = await api(`/driver/last-minute-requests/${encodeURIComponent(lmRequest.id)}/accept`, { method: 'POST', body: { availability_id: lmRequest.fit.availability_id } })
      setLastMinute({ request: null, accepted: { shipper: lmRequest.shipper_name, extra: result.extra_drive_minutes, outcome: result.outcome }, busy: false, error: '' })
      setShowExtraRoute(false)
      reload()
    } catch (error) { setLastMinute(current => ({ ...current, busy: false, error: error.message })) }
  }
  return <main className="carrier-app driver-app">
    <DriverHeader />
    <section className="carrier-welcome"><div className="carrier-kicker"><i /> CARRIER MODE · SALT LAKE CITY</div><h1>Hey, Jordan <span>✳</span></h1><p>Ready when you are. Set your hours and we’ll find deliveries along the way.</p></section>
    {lastMinute.accepted && <section className="last-minute-request last-minute-done"><div className="last-minute-summary"><div className="last-minute-symbol">✓</div><div className="last-minute-copy"><span>ADDED TO YOUR ROUTE</span><h2>You’re covering {lastMinute.accepted.shipper}</h2><p>{lastMinute.accepted.outcome === 'reserved' ? 'Reserved on your upcoming route; it will appear when the route is finalized.' : `Added to your locked route · about ${lastMinute.accepted.extra} extra minutes of driving.`}</p></div></div></section>}
    {lmRequest && <section className={`last-minute-request ${showExtraRoute ? 'route-open' : ''}`}><div className="last-minute-summary"><div className="last-minute-symbol">＋</div><div className="last-minute-copy"><span>{lmRequest.reason === 'same_day' ? 'SAME-DAY PICKUP REQUEST · SELLER NEEDS A DRIVER' : 'LAST-MINUTE CAPACITY REQUEST · SHIPPER NEEDS HELP'}</span><h2>Extra pickup coverage needed</h2><p>{lmRequest.shipper_name} · {lmRequest.pickup.label || lmRequest.pickup.address}</p><div className="last-minute-window"><small>PICKUP WINDOW · BE THERE BETWEEN</small><strong>{formatDateTime(lmRequest.pickup_window.start)} – {timeOnly(lmRequest.pickup_window.end)}</strong><small>{lmRequest.package_count} packed order{lmRequest.package_count === 1 ? '' : 's'} · about {lmRequest.package_spots} package spot{lmRequest.package_spots === 1 ? '' : 's'} · adds about {lmRequest.fit.extra_drive_minutes} min to your {lmRequest.fit.route_state === 'locked' ? 'locked' : 'upcoming'} route</small></div></div><div className="last-minute-actions"><button type="button" className="view-extra-route" aria-expanded={showExtraRoute} onClick={() => setShowExtraRoute(value => !value)}>{showExtraRoute ? 'Hide route' : 'View route'} <span>{showExtraRoute ? '⌃' : '⌄'}</span></button><button type="button" disabled={lastMinute.busy} onClick={coverLastMinute}>{lastMinute.busy ? 'Adding…' : 'I can cover this →'}</button></div></div>{lastMinute.error && <div className="form-error" role="alert">{lastMinute.error}</div>}{showExtraRoute && <div className="extra-route-preview"><div className="extra-route-heading"><div><span>ROUTE PREVIEW · THE STOPS THIS WOULD ADD</span><strong>{extraRoute.minutes != null ? `About ${Math.floor(extraRoute.minutes / 60) ? `${Math.floor(extraRoute.minutes / 60)} hr ` : ''}${Math.round(extraRoute.minutes % 60)} min driving` : 'Estimate unavailable'} · {lmRequest.preview_stops.length - 1} stops</strong></div><small>Check the stops and timing before you decide.</small></div><div className="extra-route-map"><RouteMap points={extraRoute.points || lmRequest.preview_stops.map(({ lat, lon }) => ({ lat, lon }))} stops={lmRequest.preview_stops} /></div><small className="extra-route-caption">{extraRoute.state === 'ready' ? 'Road route from Hitch routing · OpenStreetMap' : extraRoute.state === 'loading' ? 'Loading road route…' : 'Routing unavailable · showing stop order only'}</small><div className="extra-route-stops">{lmRequest.preview_stops.slice(1).map((stop, index) => <a key={stop.id} href={`https://www.google.com/maps/dir/?api=1&destination=${stop.lat}%2C${stop.lon}&travelmode=driving&dir_action=navigate`}><span>{stop.type === 'pickup' ? 'P' : 'D'}</span><small>{String(index + 1).padStart(2, '0')}</small><strong>{stop.label}</strong><b>Maps ↗</b></a>)}</div></div>}</section>}
    <section className="earnings-card"><div><span>YOUR EARNINGS TODAY</span><strong>$64.50</strong><small>3 deliveries completed</small></div><div className="earnings-spark">↗<span>+18%</span></div></section>
    <section className="availability-section"><div className="availability-heading"><div><span>YOUR ROUTE SCHEDULE</span><h2>My availability <b>{blocks.length}</b></h2></div><a href="/carrier/availability" className="add-availability">＋ <span>Schedule</span></a></div>
      {blocks.length > 0 && <div className="availability-list">{blocks.map(block => { const selected = selectedRoute === block.id; const locked = dispatchIsLocked(block); return <div className="route-entry" key={block.id}><AvailabilityCard block={block} selected={selected} onClick={() => { if (locked) window.location.href = `/carrier/route/${encodeURIComponent(block.id)}`; else setSelectedRoute(selected ? null : block.id) }} />{selected && !locked && <FormingRouteDetails block={block} onCancelled={reload} />}</div> })}</div>}
      {error && <div className="form-error" role="alert">{error}</div>}
      {loading && !error && <p className="freight-note">Loading your schedule…</p>}
      {!loading && !error && blocks.length === 0 && <div className="empty-availability"><span className="empty-icon">◷</span><strong>No availability scheduled</strong><p>Add time blocks for the days you plan to drive. Hitch will build routes as orders come in.</p><a href="/carrier/availability" className="empty-cta">Schedule availability <span>→</span></a></div>}
    </section>
    <a className="view-switch" href="/">Switch demo view</a>
    <DriverNav active="home" />
  </main>
}

function DriverRoutePage({ routeId }) {
  const { blocks, loading, error } = useAvailability()
  const block = blocks.find(item => item.id === routeId)
  const locked = block ? dispatchIsLocked(block) : false
  if (loading) return <main className="carrier-app driver-app driver-route-page"><DriverHeader /><p className="freight-note">Loading route…</p><DriverNav active="home" /></main>
  if (error && !block) return <main className="carrier-app driver-app driver-route-page"><DriverHeader /><div className="form-error" role="alert">{error}</div><a href="/carrier" className="empty-cta">Back to my routes <span>→</span></a><DriverNav active="home" /></main>
  return <main className="carrier-app driver-app driver-route-page">
    <DriverHeader />
    <section className="driver-route-page-heading"><a href="/carrier" className="mobile-back">← Back to my availability</a><div className="carrier-kicker"><i /> LOCKED-IN DRIVER ROUTE</div><h1>{block ? `${block.startLocation} route` : 'Route unavailable'}</h1><p>{block ? 'Your assigned stops, directions, and shipper payout details.' : 'This route is no longer in your availability list.'}</p></section>
    {!block ? <div className="empty-availability"><span className="empty-icon">⌖</span><strong>We couldn’t find this route</strong><p>Return to your availability list to open one of your current routes.</p><a href="/carrier" className="empty-cta">Back to my routes <span>→</span></a></div> : !locked ? <div className="empty-availability"><span className="empty-icon">◷</span><strong>This route is still forming</strong><p>It will move into your locked routes when it reaches the 12-hour lock window.</p><a href="/carrier" className="empty-cta">Back to my availability <span>→</span></a></div> : <RouteDetails block={block} locked />}
    <DriverNav active="home" />
  </main>
}

function VehicleGarage() {
  const { vehicles, setVehicles, loading: garageLoading, error: garageError } = useVehicles()
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  const [notice, setNotice] = React.useState('')
  async function addVehicle(event) {
    event.preventDefault()
    const form = event.currentTarget
    setError('')
    setNotice('')
    const data = new FormData(event.currentTarget)
    const vin = String(data.get('vin') || '').toUpperCase().replace(/\s/g, '')
    const licensePlate = String(data.get('licensePlate') || '').trim().toUpperCase()
    const plateState = String(data.get('plateState') || '')
    if (!/^[A-HJ-NPR-Z0-9]{17}$/.test(vin)) return setError('Enter a valid 17-character VIN. VINs do not use I, O, or Q.')
    setBusy(true)
    try {
      // The backend decodes the VIN (NHTSA vPIC), estimates capacity, and stores the vehicle.
      const created = await api('/driver/vehicles', { method: 'POST', body: { vin, plate: licensePlate, plate_state: plateState } })
      const vehicle = vehicleFromApi(created)
      setVehicles(current => [...current, vehicle])
      setNotice(`${vehicleDisplayName(vehicle)} was added to your garage.`)
      form.reset()
    } catch (addError) {
      setError(addError.status === 409 ? 'That VIN is already in your garage.' : addError.message || 'Vehicle lookup failed. Please try again.')
    } finally { setBusy(false) }
  }
  async function removeVehicle(id) {
    setError('')
    try {
      await api(`/driver/vehicles/${encodeURIComponent(id)}`, { method: 'DELETE' })
      setVehicles(current => current.filter(vehicle => vehicle.id !== id))
    } catch (removeError) { setError(removeError.message) }
  }
  return <main className="carrier-app driver-app">
    <DriverHeader />
    <section className="form-intro"><a href="/carrier" className="mobile-back">← Back to dashboard</a><div className="carrier-kicker"><i /> YOUR VEHICLES</div><h1>Your vehicle<br /><em>garage.</em></h1><p>Add each vehicle once. Hitch reads its type from the VIN and estimates how many standard packages it can carry.</p></section>
    <section className="garage-content">
      <div className="garage-heading"><div><span className="board-kicker">SAVED VEHICLES</span><h2>In your garage <b>{vehicles.length}</b></h2></div><span className="garage-private-note">Saved to your Hitch account</span></div>
      {garageError && <div className="form-error" role="alert">{garageError}</div>}
      {garageLoading && <p className="freight-note">Loading your garage…</p>}
      {vehicles.length ? <div className="garage-list">{vehicles.map(vehicle => <article className="garage-vehicle" key={vehicle.id}><span className="garage-car-icon">▰</span><div className="garage-vehicle-main"><strong>{vehicleDisplayName(vehicle)}</strong><small>{vehicle.plate} · {vehicle.plateState} plate</small><small>VIN ending in {vehicle.vinLast4} · {vehicle.bodyClass || 'Vehicle class not provided'}</small></div><div className="garage-capacity"><b>{vehicle.capacity}</b><small>package spots</small></div><button className="remove-vehicle" onClick={() => removeVehicle(vehicle.id)} aria-label={`Remove ${vehicleDisplayName(vehicle)}`}>×</button><p>{vehicle.capacityLabel}</p></article>)}</div> : <div className="garage-empty"><span>▰</span><strong>Your garage is empty</strong><p>Add a vehicle to use it when you schedule your availability.</p></div>}
      <form className="availability-form garage-form" onSubmit={addVehicle}>
        <div className="form-step-title"><span>＋</span><div><strong>Add a vehicle</strong><small>Use the VIN to look up the vehicle, then add its plate.</small></div></div>
        <label className="field-label" htmlFor="vehicle-vin">Vehicle identification number (VIN)</label><input className="garage-input" id="vehicle-vin" name="vin" minLength="17" maxLength="17" placeholder="17 characters · found on dashboard or door jamb" autoCapitalize="characters" required />
        <div className="plate-fields"><div><label className="field-label" htmlFor="vehicle-plate">License plate number</label><input className="garage-input" id="vehicle-plate" name="licensePlate" placeholder="e.g. 8ABC123" required /></div><div><label className="field-label" htmlFor="plate-state">State</label><select className="garage-input" id="plate-state" name="plateState" defaultValue="UT"><option value="UT">Utah</option><option value="ID">Idaho</option><option value="WY">Wyoming</option><option value="CO">Colorado</option><option value="NV">Nevada</option><option value="AZ">Arizona</option></select></div></div>
        <div className="capacity-info"><span>▰</span><p>Cargo package capacity is estimated from the decoded vehicle body type. Hitch can refine this estimate as it learns your vehicle.</p></div>
        {error && <div className="form-error" role="alert">{error}</div>}{notice && <div className="garage-success" role="status">{notice}</div>}
        <button className="save-availability" type="submit" disabled={busy}>{busy ? 'Looking up vehicle…' : 'Add to my garage'} <span>→</span></button>
        <small className="vin-source-note">VIN details are looked up with the <a href="https://vpic.nhtsa.dot.gov/api/" target="_blank" rel="noreferrer">NHTSA vPIC vehicle decoder</a>. Hitch looks the VIN up for you and stores the full VIN for dispatch only.</small>
      </form>
    </section>
    <DriverNav active="profile" />
  </main>
}

function ScheduleAvailability() {
  const [fuel, setFuel] = React.useState(75)
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const { vehicles } = useVehicles()
  const [chosenVehicleId, setSelectedVehicleId] = React.useState('')
  const selectedVehicleId = chosenVehicleId || vehicles[0]?.id || ''
  const selectedVehicle = vehicles.find(vehicle => vehicle.id === selectedVehicleId)
  async function submit(event) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const start = data.get('start')
    const end = data.get('end')
    if (!selectedVehicle) return setError('Add a vehicle to your garage before scheduling availability.')
    if (new Date(start) <= new Date()) return setError('Choose a start time in the future.')
    if (new Date(end) <= new Date(start)) return setError('Your end time needs to be after your start time.')
    setBusy(true)
    setError('')
    try {
      const startLocation = await geocode(data.get('location'))
      await api('/driver/availability', { method: 'POST', body: {
        vehicle_id: selectedVehicle.id, start_location: startLocation,
        window: { start: toIso(start), end: toIso(end) }, fuel_level_percent: fuel,
      } })
      window.location.href = '/carrier'
    } catch (submitError) {
      setError(submitError.message)
      setBusy(false)
    }
  }
  return <main className="carrier-app driver-app">
    <DriverHeader />
    <section className="form-intro"><a href="/carrier" className="mobile-back">← Back to dashboard</a><div className="carrier-kicker"><i /> MAKE TIME FOR THE MILES</div><h1>Schedule your<br /><em>availability.</em></h1><p>Choose the vehicle you’ll use, then tell Hitch when and where you’re heading.</p></section>
    <form className="availability-form" onSubmit={submit}>
      <div className="form-step-title"><span>01</span><div><strong>Choose your vehicle</strong><small>Hitch uses its cargo capacity to plan your route.</small></div></div>
      {vehicles.length ? <><label className="field-label" htmlFor="route-vehicle">Vehicle from your garage</label><div className="select-wrap"><select id="route-vehicle" value={selectedVehicleId} onChange={event => setSelectedVehicleId(event.target.value)}>{vehicles.map(vehicle => <option key={vehicle.id} value={vehicle.id}>{vehicleDisplayName(vehicle)} · {vehicle.capacity} package spots</option>)}</select><span>⌄</span></div>{selectedVehicle && <div className="selected-vehicle-detail"><span>▰</span><div><strong>{selectedVehicle.capacity} standard package spots</strong><small>{selectedVehicle.plate} · {selectedVehicle.plateState} plate · {selectedVehicle.capacityLabel}</small></div></div>}</> : <div className="garage-prompt"><span>▰</span><div><strong>Add a vehicle to your garage first</strong><small>Hitch uses your vehicle’s cargo capacity to schedule deliveries.</small><a href="/carrier/garage">Open vehicle garage →</a></div></div>}
      <label className="field-label" htmlFor="start-location">Starting location</label><div className="input-wrap location-input"><span>⌖</span><input id="start-location" name="location" placeholder="City, neighborhood, or address" defaultValue="" required autoComplete="street-address" /></div>
      <label className="field-label" htmlFor="fuel-level">Fuel at departure <strong>{fuel}%</strong></label><div className="fuel-range-wrap"><span>◴</span><input id="fuel-level" type="range" min="0" max="100" step="5" value={fuel} style={{ '--range-fill': `${fuel}%` }} onChange={event => setFuel(Number(event.target.value))} /><span className="fuel-full">⛽</span></div><div className="range-hints"><span>Nearly empty</span><span>Full tank</span></div>
      <div className="form-step-title time-step"><span>02</span><div><strong>Set your time window</strong><small>Hitch locks your route 12 hours before you start.</small></div></div>
      <div className="time-fields"><div><label className="field-label" htmlFor="start-time">Available from</label><input id="start-time" name="start" type="datetime-local" defaultValue={localDateTime(2, 9)} required /></div><div><label className="field-label" htmlFor="end-time">Available until</label><input id="end-time" name="end" type="datetime-local" defaultValue={localDateTime(2, 13)} required /></div></div>
      <div className="forming-explainer"><span>◷</span><p>Your dispatch stays <strong>forming</strong> while Hitch plans your route. The route locks automatically 12 hours before departure.</p></div>
      {error && <div className="form-error" role="alert">{error}</div>}
      <button className="save-availability" type="submit" disabled={busy}>{busy ? 'Saving…' : 'Set my availability'} <span>→</span></button>
    </form>
    <DriverNav active="availability" />
  </main>
}

function DriverPlaceholder({ type }) {
  const title = type === 'activity' ? 'Your activity' : 'Your driver profile'
  const note = type === 'activity' ? 'Completed routes and earnings will show here.' : 'Vehicle details and account settings will live here.'
  return <main className="carrier-app driver-app"><DriverHeader /><section className="placeholder-page"><div className="carrier-kicker"><i /> CARRIER MODE</div><h1>{title}</h1><p>{note}</p><a href="/carrier" className="empty-cta">Back to your dashboard <span>→</span></a></section><DriverNav active={type} /></main>
}

function Carrier({ path }) {
  if (path === '/carrier/availability') return <ScheduleAvailability />
  if (path === '/carrier/garage' || path === '/carrier/profile') return <VehicleGarage />
  if (path === '/carrier/activity') return <DriverPlaceholder type="activity" />
  if (path.startsWith('/carrier/route/')) return <DriverRoutePage routeId={decodeURIComponent(path.slice('/carrier/route/'.length))} />
  return <DriverDashboard />
}

function AppContent() {
  const path = window.location.pathname.replace(/\/$/, '')
  if (path === '/shipper') return <Shipper />
  if (path === '/carrier' || path.startsWith('/carrier/')) return <Carrier path={path} />
  return <Landing />
}

function App() {
  const [theme, setTheme] = React.useState(() => localStorage.getItem('hitch-theme') || 'light')
  React.useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('hitch-theme', theme)
  }, [theme])
  const themeContext = { theme, toggleTheme: () => setTheme(current => current === 'dark' ? 'light' : 'dark') }
  return <ThemeContext.Provider value={themeContext}><AppContent /></ThemeContext.Provider>
}

// Run the browser on the demo clock (see `python -m app.demo advance`) so lock states,
// "future time" checks and form defaults all agree with the dispatcher.
async function installDemoClock() {
  try {
    const response = await fetch('/api/demo/clock')
    const { now } = await response.json()
    const offset = new Date(now).getTime() - Date.now()
    if (Math.abs(offset) < 2000) return
    const RealDate = Date
    class DemoDate extends RealDate {
      constructor(...args) { if (args.length === 0) super(RealDate.now() + offset); else super(...args) }
      static now() { return RealDate.now() + offset }
    }
    window.Date = DemoDate
  } catch { /* backend unreachable: keep real time */ }
}

installDemoClock().then(() => createRoot(document.getElementById('root')).render(<App />))

import React from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'

const jobs = [
  { id: 'HC-2048', from: 'Sugar House, Salt Lake City', to: 'Downtown, Salt Lake City', item: 'Bakery order · 2 boxes', time: 'Today, 10:30 AM', price: '$18.50', status: 'Ready for pickup', color: 'mint' },
  { id: 'HC-2047', from: 'The Avenues, Salt Lake City', to: 'Murray, Utah', item: 'Home goods · 1 parcel', time: 'Today, 11:15 AM', price: '$24.00', status: 'Driver assigned', color: 'blue' },
  { id: 'HC-2046', from: 'Millcreek, Utah', to: 'Sugar House, Salt Lake City', item: 'Vintage lamp · Handle with care', time: 'Today, 12:00 PM', price: '$15.75', status: 'In transit', color: 'peach' },
]

function Brand() {
  return <a className="brand" href="/">hitch<span>.</span></a>
}

function Landing() {
  return (
    <main className="landing">
      <Brand />
      <div className="landing-copy">
        <div className="eyebrow"><span className="eyebrow-dot" /> LOCAL DELIVERY, REIMAGINED</div>
        <h1>Good things<br />are <em>going places.</em></h1>
        <p>Choose a demo view to explore the Hitch delivery experience.</p>
      </div>
      <div className="view-picker">
        <a className="view-card shipper-card" href="/shipper"><span className="view-icon">↗</span><span className="view-meta">DESKTOP VIEW</span><strong>I'm a shipper</strong><small>Create and track local deliveries.</small><span className="view-arrow">Open view&nbsp; →</span></a>
        <a className="view-card carrier-card" href="/carrier"><span className="view-icon">⌁</span><span className="view-meta">MOBILE VIEW</span><strong>I'm a carrier</strong><small>Find nearby delivery opportunities.</small><span className="view-arrow">Open view&nbsp; →</span></a>
      </div>
      <footer className="landing-footer"><span>HITCH · SALT LAKE CITY, UTAH</span><span>DEMO EXPERIENCE</span></footer>
    </main>
  )
}

function Sidebar() {
  return <aside className="sidebar"><Brand /><div className="workspace-label">WORKSPACE</div><a className="nav-item active" href="/shipper"><span>▦</span> Overview</a><a className="nav-item" href="#deliveries"><span>↗</span> Deliveries <b>3</b></a><a className="nav-item" href="#drivers"><span>♧</span> Drivers</a><div className="sidebar-bottom"><div className="user-avatar">AT</div><div><strong>Acme Goods</strong><small>Shipper account</small></div><a href="/" aria-label="Back to views">⌄</a></div></aside>
}

function Shipper() {
  return (
    <div className="shipper-app">
      <Sidebar />
      <main className="shipper-main">
        <header className="dashboard-header"><div><div className="breadcrumb">WORKSPACE <span>/</span> OVERVIEW</div><h1>Good morning, Alex <span>✳</span></h1><p>Here’s what’s happening with your deliveries today.</p></div><a className="avatar" href="/">AT</a></header>
        <section className="stat-grid"><article className="stat-card"><span>ACTIVE DELIVERIES</span><strong>03</strong><small><i className="tiny-dot green-dot" /> Across Salt Lake City</small></article><article className="stat-card"><span>COMPLETED THIS WEEK</span><strong>28</strong><small className="trend">↑ 12% <em>vs. last week</em></small></article><article className="stat-card"><span>AVERAGE PICKUP</span><strong>18 <small>min</small></strong><small><i className="tiny-dot green-dot" /> 6 min faster this week</small></article></section>
        <section className="delivery-section" id="deliveries"><div className="section-heading"><div><h2>Live deliveries</h2><p>Keep an eye on every package on the move.</p></div><button className="new-delivery" onClick={() => alert('New delivery form coming soon.')}>＋ <span>New delivery</span></button></div>
          <div className="delivery-table"><div className="table-head"><span>DELIVERY</span><span>ROUTE</span><span>STATUS</span><span>PICKUP</span><span>OFFER</span></div>{jobs.map((job) => <article className="delivery-row" key={job.id}><div className="delivery-id"><span className={`package-icon ${job.color}`}>▧</span><div><strong>{job.item}</strong><small>{job.id}</small></div></div><div className="route-cities"><span>{job.from}</span><span>{job.to}</span></div><div><span className={`status-pill ${job.color}`}>{job.status}</span></div><div className="pickup-time">{job.time}</div><div className="offer-price">{job.price}</div></article>)}</div>
        </section>
        <section className="map-panel"><div className="map-heading"><div><h2>Local activity</h2><p>Your deliveries around Salt Lake City</p></div><span className="map-live"><i /> LIVE DEMO</span></div><div className="map-art"><div className="map-grid" /><div className="map-road road-one" /><div className="map-road road-two" /><div className="map-road road-three" /><div className="map-label label-sugar">SUGAR HOUSE</div><div className="map-label label-downtown">DOWNTOWN</div><div className="map-label label-mill">MILLCREEK</div><span className="map-marker marker-one">↗</span><span className="map-marker marker-two">↗</span><span className="map-marker marker-three">↗</span><div className="map-credit">SALT LAKE CITY · UTAH</div></div></section>
        <footer className="dashboard-footer"><a href="/">← Switch demo view</a><span>HITCH · LOCAL DELIVERY NETWORK</span></footer>
      </main>
    </div>
  )
}

const AVAILABILITY_KEY = 'hitch-driver-availability'

function localDateTime(daysAhead, hour) {
  const date = new Date()
  date.setDate(date.getDate() + daysAhead)
  date.setHours(hour, 0, 0, 0)
  const offset = date.getTimezoneOffset()
  return new Date(date.getTime() - offset * 60_000).toISOString().slice(0, 16)
}

function getAvailability() {
  try {
    const saved = JSON.parse(localStorage.getItem(AVAILABILITY_KEY))
    if (Array.isArray(saved)) return saved
  } catch { /* Start with the demo schedule if stored data is unavailable. */ }
  const start = localDateTime(2, 9)
  const end = localDateTime(2, 13)
  const sample = [{
    id: 'demo-block', startLocation: 'Sugar House, Salt Lake City', fuel: 75,
    capacity: 8, bookedCapacity: 2, bookedMinutes: 90, start, end, sample: true,
  }]
  localStorage.setItem(AVAILABILITY_KEY, JSON.stringify(sample))
  return sample
}

function formatDateTime(value) {
  return new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(value))
}

function dispatchIsLocked(block) {
  return new Date(block.start).getTime() - Date.now() <= 24 * 60 * 60 * 1000
}

function DriverNav({ active }) {
  return <nav className="carrier-nav">
    <a className={active === 'home' ? 'selected' : ''} href="/carrier"><span>⌂</span>Home</a>
    <a className={active === 'availability' ? 'selected' : ''} href="/carrier/availability"><span>＋</span>Availability</a>
    <a className={active === 'activity' ? 'selected' : ''} href="/carrier/activity"><span>↗</span>Activity</a>
    <a className={active === 'profile' ? 'selected' : ''} href="/carrier/profile"><span>◉</span>Profile</a>
  </nav>
}

function DriverHeader({ title }) {
  return <header className="carrier-header"><a className="back-to-views" href="/" aria-label="Choose a demo view">‹</a><Brand /><a className="carrier-profile" href="/carrier/profile">JD</a></header>
}

function AvailabilityCard({ block }) {
  const totalMinutes = Math.max(1, (new Date(block.end) - new Date(block.start)) / 60_000)
  const timePercent = Math.min(100, Math.round((block.bookedMinutes / totalMinutes) * 100))
  const capacityPercent = Math.min(100, Math.round((block.bookedCapacity / block.capacity) * 100))
  const locked = dispatchIsLocked(block)
  return <article className="availability-card">
    <div className="availability-card-top"><div><span className="card-date-label">ROUTE DEPARTURE</span><h3>{new Date(block.start).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })}</h3></div>
      <span className={`dispatch-badge ${locked ? 'locked' : 'forming'}`}><i />{locked ? 'Dispatch locked in' : 'Dispatch forming'}</span></div>
    <div className="block-time"><span>AVAILABILITY WINDOW</span><strong>{new Date(block.start).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })} – {new Date(block.end).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</strong></div>
    <div className="block-location"><span className="location-pin">⌖</span><div><small>STARTING LOCATION</small><strong>{block.startLocation}</strong></div><span className="fuel-level"><b>⛽</b> {block.fuel}%</span></div>
    <div className="booking-progress"><div className="progress-label"><span>TIME BOOKED</span><strong>{timePercent}%</strong></div><div className="progress-track"><i style={{ width: `${timePercent}%` }} /></div><div className="progress-caption">{Math.floor(block.bookedMinutes / 60)}h {block.bookedMinutes % 60}m of {Math.floor(totalMinutes / 60)}h available</div></div>
    <div className="booking-progress capacity-progress"><div className="progress-label"><span>VEHICLE CAPACITY BOOKED</span><strong>{block.bookedCapacity} / {block.capacity} spots</strong></div><div className="progress-track"><i style={{ width: `${capacityPercent}%` }} /></div><div className="progress-caption">{capacityPercent}% of your delivery capacity reserved</div></div>
    {block.sample && <div className="sample-note">Sample shipper bookings shown for demo</div>}
    {!locked && <div className="lock-note"><span>◷</span> Dispatch locks 24 hours before departure</div>}
  </article>
}

function DriverDashboard() {
  const blocks = getAvailability().sort((a, b) => new Date(a.start) - new Date(b.start))
  return <main className="carrier-app driver-app">
    <DriverHeader />
    <section className="carrier-welcome"><div className="carrier-kicker"><i /> CARRIER MODE · SALT LAKE CITY</div><h1>Hey, Jordan <span>✳</span></h1><p>Ready when you are. Set your hours and we’ll find deliveries along the way.</p></section>
    <section className="earnings-card"><div><span>YOUR EARNINGS TODAY</span><strong>$64.50</strong><small>3 deliveries completed</small></div><div className="earnings-spark">↗<span>+18%</span></div></section>
    <section className="availability-section"><div className="availability-heading"><div><span>YOUR UPCOMING ROUTES</span><h2>Availability <b>{blocks.length}</b></h2></div><a href="/carrier/availability" className="add-availability">＋ <span>Schedule</span></a></div>
      {blocks.length ? <div className="availability-list">{blocks.map(block => <AvailabilityCard key={block.id} block={block} />)}</div> : <div className="empty-availability"><span className="empty-icon">◷</span><strong>No availability scheduled</strong><p>Add a time block so shippers can plan around your route.</p><a href="/carrier/availability" className="empty-cta">Schedule availability <span>→</span></a></div>}
    </section>
    <a className="view-switch" href="/">Switch demo view</a>
    <DriverNav active="home" />
  </main>
}

function ScheduleAvailability() {
  const [fuel, setFuel] = React.useState(75)
  const [error, setError] = React.useState('')
  function submit(event) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const start = data.get('start')
    const end = data.get('end')
    if (new Date(start) <= new Date()) return setError('Choose a start time in the future.')
    if (new Date(end) <= new Date(start)) return setError('Your end time needs to be after your start time.')
    const block = {
      id: `availability-${Date.now()}`, startLocation: data.get('location').trim(), fuel,
      capacity: Number(data.get('capacity')), bookedCapacity: 0, bookedMinutes: 0, start, end,
    }
    const blocks = getAvailability().filter(existing => !existing.sample)
    localStorage.setItem(AVAILABILITY_KEY, JSON.stringify([...blocks, block]))
    window.location.href = '/carrier'
  }
  return <main className="carrier-app driver-app">
    <DriverHeader />
    <section className="form-intro"><a href="/carrier" className="mobile-back">← Back to dashboard</a><div className="carrier-kicker"><i /> MAKE TIME FOR THE MILES</div><h1>Schedule your<br /><em>availability.</em></h1><p>Tell us when you’ll be on the road. Shippers can reserve space in your route as their dispatch comes together.</p></section>
    <form className="availability-form" onSubmit={submit}>
      <div className="form-step-title"><span>01</span><div><strong>Your route basics</strong><small>Where you’re headed and what you can carry.</small></div></div>
      <label className="field-label" htmlFor="start-location">Starting location</label><div className="input-wrap location-input"><span>⌖</span><input id="start-location" name="location" placeholder="City, neighborhood, or address" defaultValue="" required autoComplete="street-address" /></div>
      <label className="field-label" htmlFor="fuel-level">Fuel at departure <strong>{fuel}%</strong></label><div className="fuel-range-wrap"><span>◴</span><input id="fuel-level" type="range" min="0" max="100" step="5" value={fuel} style={{ '--range-fill': `${fuel}%` }} onChange={event => setFuel(Number(event.target.value))} /><span className="fuel-full">⛽</span></div><div className="range-hints"><span>Nearly empty</span><span>Full tank</span></div>
      <label className="field-label" htmlFor="capacity">Delivery capacity <small>How many packages can you take?</small></label><div className="select-wrap"><select id="capacity" name="capacity" defaultValue="6"><option value="2">2 packages · compact car</option><option value="4">4 packages · sedan</option><option value="6">6 packages · SUV</option><option value="10">10 packages · cargo van</option></select><span>⌄</span></div>
      <div className="form-step-title time-step"><span>02</span><div><strong>Set your time window</strong><small>Dispatch locks 24 hours before you start.</small></div></div>
      <div className="time-fields"><div><label className="field-label" htmlFor="start-time">Available from</label><input id="start-time" name="start" type="datetime-local" defaultValue={localDateTime(2, 9)} required /></div><div><label className="field-label" htmlFor="end-time">Available until</label><input id="end-time" name="end" type="datetime-local" defaultValue={localDateTime(2, 13)} required /></div></div>
      <div className="forming-explainer"><span>◷</span><p>Your dispatch stays <strong>forming</strong> while shippers book into your route. It locks automatically 24 hours before departure.</p></div>
      {error && <div className="form-error" role="alert">{error}</div>}
      <button className="save-availability" type="submit">Set my availability <span>→</span></button>
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
  if (path === '/carrier/activity') return <DriverPlaceholder type="activity" />
  if (path === '/carrier/profile') return <DriverPlaceholder type="profile" />
  return <DriverDashboard />
}

function App() {
  const path = window.location.pathname.replace(/\/$/, '')
  if (path === '/shipper') return <Shipper />
  if (path === '/carrier' || path.startsWith('/carrier/')) return <Carrier path={path} />
  return <Landing />
}

createRoot(document.getElementById('root')).render(<App />)

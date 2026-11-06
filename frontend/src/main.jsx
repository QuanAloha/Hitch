import React from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'

function App() {
  const [status, setStatus] = React.useState('checking')

  React.useEffect(() => {
    fetch('/health')
      .then((response) => {
        if (!response.ok) throw new Error('API unavailable')
        return response.json()
      })
      .then(() => setStatus('online'))
      .catch(() => setStatus('offline'))
  }, [])

  return (
    <main className="shell">
      <header className="topbar"><a className="brand" href="/">hitch<span>.</span></a><span className="prototype">LOCAL DELIVERY, REIMAGINED</span></header>
      <section className="hero">
        <div className="eyebrow"><span className="status-dot" /> FIRST MILE, LAST MILE, YOUR WAY</div>
        <h1>Good things<br />are <em>going places.</em></h1>
        <p className="intro">A smarter way to connect local deliveries with the people already on the road.</p>
        <div className="route-card">
          <div className="route-line"><span className="pin pickup" /><div><label>PICKUP</label><strong>Where should we start?</strong></div></div>
          <div className="divider" />
          <div className="route-line"><span className="pin dropoff" /><div><label>DROP-OFF</label><strong>Where is it headed?</strong></div></div>
          <button type="button" onClick={() => alert('Route planning is ready to build on the Hitch API.')}>Plan a delivery <span>→</span></button>
        </div>
        <div className="footnote">A little less empty road. A lot more getting there.</div>
      </section>
      <footer><span>HITCH · LOCAL DELIVERY NETWORK</span><span className={`api-status ${status}`}><i /> API {status}</span></footer>
    </main>
  )
}

createRoot(document.getElementById('root')).render(<App />)

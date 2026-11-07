import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { decodePolyline } from '../geo'

const icon = (cls, text = '') =>
  L.divIcon({ className: '', html: `<span class="pin-marker ${cls}">${text}</span>`, iconSize: [26, 26], iconAnchor: [13, 13] })

const tip = (text) => {
  const el = document.createElement('div')
  el.textContent = text
  return el
}

// Shows the pickup and drop-offs; once a route exists, also the road geometry and stop order.
export default function RouteMap({ shipment, route }) {
  const elRef = useRef(null)
  const mapRef = useRef(null)
  const layerRef = useRef(null)

  useEffect(() => {
    const map = L.map(elRef.current, { scrollWheelZoom: false }).setView([39.5, -98.35], 4)
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map)
    layerRef.current = L.layerGroup().addTo(map)
    mapRef.current = map
    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    const group = layerRef.current
    if (!map || !group) return
    group.clearLayers()
    const points = []
    const mark = (lat, lon, ic, label) => {
      L.marker([lat, lon], { icon: ic, keyboard: false }).bindTooltip(tip(label)).addTo(group)
      points.push([lat, lon])
    }

    if (route) {
      route.legs.forEach((leg, i) => {
        const line = decodePolyline(leg.shape)
        // First leg is the drive to the pickup; the rest carry parcels.
        L.polyline(line, { color: '#65775b', weight: 4, opacity: i === 0 ? 0.55 : 0.95, dashArray: i === 0 ? '6 8' : undefined }).addTo(group)
        points.push(...line)
      })
      const first = decodePolyline(route.legs[0].shape)[0]
      if (first) mark(first[0], first[1], icon('start'), 'Starting point')
    }
    mark(shipment.pickup_lat, shipment.pickup_lon, icon('pickup'), `Pickup: ${shipment.pickup_address}`)
    if (route) {
      route.stops.forEach((s) => mark(s.lat, s.lon, icon('stop', s.sequence), `Stop ${s.sequence}: ${s.address}`))
    } else {
      shipment.parcels.forEach((p) => mark(p.dropoff_lat, p.dropoff_lon, icon('stop'), `Drop-off: ${p.dropoff_address}`))
    }
    map.fitBounds(points, { padding: [32, 32], maxZoom: 15 })
  }, [shipment, route])

  return <div ref={elRef} className="map" role="img" aria-label="Map of the pickup, drop-offs and route" />
}

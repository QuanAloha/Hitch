// Decode a Valhalla encoded polyline (precision 6) into [lat, lon] pairs.
export function decodePolyline(str, precision = 6) {
  const factor = 10 ** precision
  const coords = []
  let index = 0
  let lat = 0
  let lon = 0
  while (index < str.length) {
    for (const axis of [0, 1]) {
      let shift = 0
      let result = 0
      let byte
      do {
        byte = str.charCodeAt(index++) - 63
        result |= (byte & 0x1f) << shift
        shift += 5
      } while (byte >= 0x20)
      const delta = result & 1 ? ~(result >> 1) : result >> 1
      if (axis === 0) lat += delta
      else lon += delta
    }
    coords.push([lat / factor, lon / factor])
  }
  return coords
}

// Address -> coordinates via OpenStreetMap's Nominatim. The public instance allows one
// request per second, so lookups are queued. For production traffic, point this at your
// own geocoder (Nominatim, Pelias, ...).
let lastCall = 0
let queue = Promise.resolve()

export function geocode(query) {
  const run = async () => {
    const wait = lastCall + 1100 - Date.now()
    if (wait > 0) await new Promise((r) => setTimeout(r, wait))
    lastCall = Date.now()
    const res = await fetch(
      `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=${encodeURIComponent(query)}`,
    )
    if (!res.ok) throw new Error('Address lookup isn’t available right now. Try again in a moment.')
    const hits = await res.json()
    if (!hits.length) return null
    return { lat: Number(hits[0].lat), lon: Number(hits[0].lon), label: hits[0].display_name }
  }
  const result = queue.then(run, run)
  queue = result.catch(() => {})
  return result
}

// Use an already-found place, or look the text up now.
export async function resolvePlace(text, place, what) {
  if (place) return place
  if (!text.trim()) throw new Error(`Enter the ${what}.`)
  const found = await geocode(text.trim())
  if (!found) throw new Error(`Couldn’t find the ${what}. Try adding the city or state.`)
  return found
}

export function currentPosition() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) return reject(new Error('Location isn’t available in this browser.'))
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude, label: 'Your current location' }),
      () => reject(new Error('Couldn’t get your location. Allow location access or type an address.')),
      { timeout: 10000 },
    )
  })
}

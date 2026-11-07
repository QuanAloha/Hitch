const BASE = '/api'
const TOKEN_KEY = 'hitch.token'

let onUnauthorized = () => {}

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (token) =>
  token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY)
export const setUnauthorizedHandler = (fn) => {
  onUnauthorized = fn
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

// FastAPI returns either a string or a list of validation problems.
function describe(detail) {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const field = (d.loc || []).filter((p) => p !== 'body').join(' › ')
        return field ? `${field}: ${d.msg}` : d.msg
      })
      .join('; ')
  }
  return ''
}

async function request(path, { method = 'GET', json, form } = {}) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let body
  if (json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(json)
  } else if (form) {
    body = new URLSearchParams(form)
  }

  let res
  try {
    res = await fetch(BASE + path, { method, headers, body })
  } catch {
    throw new ApiError('Can’t reach the Hitch API. Check that the backend is running.', 0)
  }

  if (!res.ok) {
    let detail
    try {
      detail = (await res.json()).detail
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 401 && token) onUnauthorized()
    throw new ApiError(describe(detail) || `Request failed (${res.status})`, res.status)
  }
  return res.json()
}

export const api = {
  register: (body) => request('/auth/register', { method: 'POST', json: body }),
  login: (email, password) =>
    request('/auth/login', { method: 'POST', form: { username: email, password } }),
  me: () => request('/auth/me'),

  listShipments: ({ available = false } = {}) =>
    request(`/shipments${available ? '?available=true' : ''}`),
  createShipment: (body) => request('/shipments', { method: 'POST', json: body }),
  cancelShipment: (id) => request(`/shipments/${id}/cancel`, { method: 'POST' }),

  acceptShipment: (id, start) =>
    request(`/shipments/${id}/accept`, { method: 'POST', json: { start } }),
  rebuildRoute: (id, start) =>
    request(`/shipments/${id}/route`, { method: 'POST', json: { start } }),
  getRoute: (id) => request(`/shipments/${id}/route`),
}

export const STATUS_LABEL = {
  open: 'Open',
  assigned: 'Assigned',
  in_transit: 'In transit',
  delivered: 'Delivered',
  cancelled: 'Cancelled',
}

export function formatDuration(seconds) {
  const mins = Math.max(1, Math.round(seconds / 60))
  if (mins < 60) return `${mins} min`
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return m ? `${h} h ${m} min` : `${h} h`
}

export function formatDistance(km) {
  if (navigator.language === 'en-US') return `${(km * 0.621371).toFixed(1)} mi`
  return `${km.toFixed(1)} km`
}

export const shortPlace = (label) => label.split(',').slice(0, 3).join(',')
export const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

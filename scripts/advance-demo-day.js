// Browser-console helper for the local Hitch demo.
// Open the Hitch frontend in your browser, paste this into DevTools Console,
// and press Enter. It advances the app's demo clock without changing the
// computer's system clock. Use scripts/reset-demo-clock.js to undo it.
(() => {
  const key = 'hitch-demo-time-offset-days'
  const currentDays = Number(localStorage.getItem(key) || 0)
  localStorage.setItem(key, String(currentDays + 1))
  window.location.reload()
})()

// Browser-console helper for the local Hitch demo.
// Open the Hitch frontend in your browser, paste this into DevTools Console,
// and press Enter to restore the demo clock to your computer's current time.
(() => {
  localStorage.removeItem('hitch-demo-time-offset-days')
  window.location.reload()
})()

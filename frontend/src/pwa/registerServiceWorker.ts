// Registers public/sw.js (design/2026-10-07-mobile-operator-pwa-design.md,
// decision 8) in production builds only. Under `npm run dev` a worker would
// cache Vite's HMR modules, so there is none, and usePush reports
// 'unavailable' rather than waiting on a worker that will never come.
export function registerServiceWorker(isProd: boolean = import.meta.env.PROD): void {
  if (!isProd || !('serviceWorker' in navigator)) return
  const register = () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch((e: unknown) => {
      // The app works without it; only install and push are lost.
      console.warn('MaintainIQ: service worker registration failed', e)
    })
  }
  // After load, so registration never competes with the first render.
  if (document.readyState === 'complete') register()
  else window.addEventListener('load', register, { once: true })
}

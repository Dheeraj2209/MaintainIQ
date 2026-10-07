// QR access to machines (design/2026-10-07-mobile-operator-pwa-design.md,
// decisions 16-17). A label encodes a plain deep link to the mobile machine
// page; a scan is only followed when it is exactly such a link on this site.

// `${origin}/m/machines/<id>`, the id URL-encoded (ids may hold spaces or "/").
export function machineDeepLink(origin: string, machineId: string): string {
  return `${origin}/m/machines/${encodeURIComponent(machineId)}`
}

const MACHINE_PATH = /^\/m\/machines\/([^/]+)\/?$/

// The machine id from a scanned code, or null for anything else: another
// site, another app path, `javascript:`, relative or plain text. A scanned
// code is attacker-controllable, so following arbitrary URLs from inside the
// installed app would be phishing-shaped.
export function parseMachineQr(text: string, origin: string): string | null {
  let url: URL
  try {
    url = new URL(text)
  } catch {
    return null
  }
  if (url.origin !== origin) return null
  const match = MACHINE_PATH.exec(url.pathname)
  if (!match) return null
  try {
    return decodeURIComponent(match[1])
  } catch {
    return null
  }
}

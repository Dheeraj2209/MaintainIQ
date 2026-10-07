// jsdom has no service workers, Push API, Notification, BarcodeDetector or
// camera. installPwaStubs() fakes just enough of each for the mobile/PWA code
// (design/2026-10-07-mobile-operator-pwa-design.md, "Tests and MSW"). setup.ts
// installs the default (supported, secure, permission "default", no
// subscription, no BarcodeDetector, no camera) before every test and restores
// jsdom's originals after; a test calls installPwaStubs({...}) again to
// override. Every property is defined as an own, configurable property and
// put back (or deleted) by restorePwaStubs, so nothing leaks between tests.
import { vi } from 'vitest'

export interface FakePushSubscription {
  endpoint: string
  expirationTime: null
  options: { applicationServerKey: ArrayBuffer | null; userVisibleOnly: boolean }
  toJSON: () => PushSubscriptionJSON
  unsubscribe: ReturnType<typeof vi.fn<() => Promise<boolean>>>
}

export interface FakeTrack {
  stop: ReturnType<typeof vi.fn<() => void>>
}

export interface PwaStubOptions {
  // Each capability defaults to present.
  serviceWorker?: boolean
  pushManager?: boolean
  notification?: boolean
  secure?: boolean
  // Whether getRegistration() finds a worker (a production build).
  registration?: boolean
  permission?: NotificationPermission
  // What Notification.requestPermission() resolves to.
  grant?: NotificationPermission
  // A subscription this browser already holds.
  subscription?: FakePushSubscription | null
  // A BarcodeDetector with these supported formats and this detect().
  barcode?: { formats: string[]; detect: (source: unknown) => Promise<{ rawValue: string }[]> } | null
  // navigator.mediaDevices.getUserMedia; `true` = a working rear camera.
  camera?: boolean | ((constraints: MediaStreamConstraints) => Promise<MediaStream>)
}

export interface PwaStubs {
  register: ReturnType<typeof vi.fn>
  getRegistration: ReturnType<typeof vi.fn>
  subscribe: ReturnType<typeof vi.fn>
  getSubscription: ReturnType<typeof vi.fn>
  requestPermission: ReturnType<typeof vi.fn>
  getUserMedia: ReturnType<typeof vi.fn> | null
  // Tracks handed out by the default camera, to assert they were stopped.
  tracks: FakeTrack[]
  // The browser-side subscription right now (null after unsubscribe).
  current: () => FakePushSubscription | null
}

export const DEFAULT_ENDPOINT = 'https://fcm.googleapis.com/fcm/send/test-device'

function toBase64Url(bytes: Uint8Array): string {
  let binary = ''
  bytes.forEach((b) => (binary += String.fromCharCode(b)))
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

// A browser PushSubscription look-alike. `key` is the applicationServerKey it
// was created with (compared on mount to spot a server key rotation).
export function fakeSubscription(
  endpoint = DEFAULT_ENDPOINT,
  key: ArrayBuffer | Uint8Array | null = null,
): FakePushSubscription {
  const buffer = key instanceof Uint8Array ? (key.slice().buffer as ArrayBuffer) : key
  const sub: FakePushSubscription = {
    endpoint,
    expirationTime: null,
    options: { applicationServerKey: buffer, userVisibleOnly: true },
    toJSON: () => ({
      endpoint,
      expirationTime: null,
      keys: { p256dh: toBase64Url(new Uint8Array(65).fill(4)), auth: toBase64Url(new Uint8Array(16).fill(7)) },
    }),
    unsubscribe: vi.fn(async () => true),
  }
  return sub
}

const saved: { target: object; key: string; desc: PropertyDescriptor | undefined }[] = []

function define(target: object, key: string, value: unknown) {
  saved.push({ target, key, desc: Object.getOwnPropertyDescriptor(target, key) })
  Object.defineProperty(target, key, { value, configurable: true, writable: true })
}

export function restorePwaStubs() {
  while (saved.length) {
    const { target, key, desc } = saved.pop()!
    if (desc) Object.defineProperty(target, key, desc)
    else delete (target as Record<string, unknown>)[key]
  }
}

export function installPwaStubs(opts: PwaStubOptions = {}): PwaStubs {
  restorePwaStubs()
  const {
    serviceWorker = true,
    pushManager = true,
    notification = true,
    secure = true,
    registration = true,
    permission = 'default',
    grant = 'granted',
    subscription = null,
    barcode = null,
    camera = false,
  } = opts

  let current: FakePushSubscription | null = subscription
  const track = (sub: FakePushSubscription) => {
    const original = sub.unsubscribe.getMockImplementation()
    sub.unsubscribe.mockImplementation(async () => {
      if (current === sub) current = null
      return original ? original() : true
    })
    return sub
  }
  if (current) track(current)

  const getSubscription = vi.fn(async () => current)
  const subscribe = vi.fn(async (options: PushSubscriptionOptionsInit) => {
    const key = options.applicationServerKey as Uint8Array | ArrayBuffer | null
    current = track(fakeSubscription(DEFAULT_ENDPOINT, key ?? null))
    return current
  })
  const fakeRegistration = { scope: '/', pushManager: { getSubscription, subscribe } }
  const register = vi.fn(async () => fakeRegistration)
  const getRegistration = vi.fn(async () => (registration ? fakeRegistration : undefined))

  if (serviceWorker) define(navigator, 'serviceWorker', { register, getRegistration })
  if (pushManager) define(window, 'PushManager', function PushManager() {})

  let perm: NotificationPermission = permission
  const requestPermission = vi.fn(async () => {
    perm = grant
    return grant
  })
  if (notification) {
    const FakeNotification = function Notification() {}
    Object.defineProperty(FakeNotification, 'permission', { get: () => perm, configurable: true })
    Object.defineProperty(FakeNotification, 'requestPermission', { value: requestPermission, configurable: true })
    define(window, 'Notification', FakeNotification)
  }
  define(window, 'isSecureContext', secure)

  if (barcode) {
    const { formats, detect } = barcode
    class FakeBarcodeDetector {
      static getSupportedFormats = async () => formats
      detect(source: unknown) {
        return detect(source)
      }
    }
    define(window, 'BarcodeDetector', FakeBarcodeDetector)
  }

  const tracks: FakeTrack[] = []
  let getUserMedia: ReturnType<typeof vi.fn> | null = null
  if (camera) {
    getUserMedia = vi.fn(
      typeof camera === 'function'
        ? camera
        : async () => {
            const t: FakeTrack = { stop: vi.fn() }
            tracks.push(t)
            return { getTracks: () => [t] } as unknown as MediaStream
          },
    )
    define(navigator, 'mediaDevices', { getUserMedia })
  }

  return { register, getRegistration, subscribe, getSubscription, requestPermission, getUserMedia, tracks, current: () => current }
}

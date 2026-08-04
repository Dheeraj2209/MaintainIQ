// Stub WebSocket for jsdom (which has no real network stack). Components
// create `new WebSocket(url)`; tests grab the most recent instance via
// `MockWebSocket.instances` and drive it with `.emitOpen()` / `.emitMessage()`
// / `.emitClose()` to simulate server push without a real socket or server.
export class MockWebSocket {
  static instances: MockWebSocket[] = []

  url: string
  readyState = 0 // CONNECTING
  onopen: ((ev: unknown) => void) | null = null
  onclose: ((ev: { code: number }) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  sent: string[] = []

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    this.emitClose()
  }

  emitOpen() {
    this.readyState = 1 // OPEN
    this.onopen?.({})
  }

  emitMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) })
  }

  emitClose(code = 1000) {
    this.readyState = 3 // CLOSED
    this.onclose?.({ code })
  }

  static reset() {
    MockWebSocket.instances = []
  }
}

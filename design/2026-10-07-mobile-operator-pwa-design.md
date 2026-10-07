# Mobile Operator View (PWA) — Design

Status: Approved (decisions below are final for implementation)
Date: 2026-10-07
Source: user requirement (verbatim) "Mobile operator view: installable, with push notifications, quick alert actions and QR access to machines."; `design/_integration_map.md` §6 (feature 5, migration 7) and §7 (cross-cutting checklist); builds on `design/2026-10-06-device-health-design.md` (`notify_device_incident`, watchdog), `design/2026-10-07-work-orders-escalation-design.md` (`dispatch.notify_alert(roles, page_level)`, paging ladder, `POST /alerts/{id}/work-order`), `design/2026-10-07-prediction-feedback-design.md` (`POST /alerts/{id}/close`, `AlertCloseDialog`), `design/2026-10-07-alert-explanation-design.md` (`AlertExplanationView`, `/alerts/:id` deep link) and `design/2026-08-04-alert-acknowledge-design.md` (template).

## Problem

MaintainIQ is a desktop console. The person standing next to a failing machine has a phone, and today:

- **Nothing reaches the operator.** Paging is email-only, to admins and supervisors (`src/notifications/dispatch.py:35-36 DEFAULT_PAGED_ROLES`). The module docstring says operators are excluded on purpose because "they're the ones expected to act on the dashboard" (`:7-10`), but nobody watches a dashboard on the shop floor. The paging ladder (`src/alerts/paging.py:49 LADDER`) likewise only emails supervisors and admins.
- **The console does not fit a phone.** `AppShell` has a fixed `w-60` sidebar beside the content (`frontend/src/layout/AppShell.tsx:148`). Below about 900 px the content column is crushed, and there is no way to collapse the nav.
- **Acting on an alert needs a desk.** Acknowledge, raise a work order, close with an outcome and "Why?" all exist (`src/api/routes/alerts.py:76-201`), but only as small table buttons on `AlertsPage`.
- **Finding the machine is a typing exercise.** There is no way to get from the physical machine to its page except typing its id into a desktop URL.
- **Deep links lose state.** `LoginPage` restores only `from.pathname` (`frontend/src/pages/LoginPage.tsx:10-12, 24, 34`), so any search string or hash is dropped on the way through login.
- **It cannot be installed.** `frontend/public/` holds only `favicon.svg` (the stock Vite logo) and `icons.svg`. There is no manifest, no service worker and no PNG icon. `index.html` has no manifest link or theme colour (`frontend/index.html:3-6`).

## Goal

1. **Installable.** A web app manifest, PNG icons (192, 512, maskable, Apple touch) and a hand-written service worker make MaintainIQ installable on Android, iOS (16.4+) and desktop Chromium. It launches straight into the operator view at `/m`.
2. **Push notifications.** Web Push (VAPID) delivers pages to a phone. Every signed-in user can opt in per device.
   - Operators are now pushed at paging level 0 for new and severity-escalated alerts, and for sensor-node silence incidents.
   - Supervisors and admins get a push in addition to each email they already get, at the levels they already get it.
   - Resolutions are never pushed.
   - Every push attempt is logged as one `notifications` row with `channel = 'push'`.
   - When no VAPID keys are configured, push is off and says so. Nothing else changes.
3. **Quick alert actions.** A lean mobile shell with a bottom tab bar. Alert cards carry 44 px+ buttons for **Acknowledge**, **Work order**, **Close** (with outcome) and **Why?**. Acknowledge and Work order are optimistic, with rollback on error. Live events keep the screen current.
4. **QR access.** Machine Detail (desktop) prints a QR label encoding `<origin>/m/machines/<id>`. `/m/scan` reads labels with `BarcodeDetector` + camera where available. Elsewhere it offers manual id entry and tells the user to use the phone's own camera app, which opens the URL directly.
5. **Deep links survive login.** A push tap or QR scan by a signed-out user goes to login and comes back to the exact path, search and hash.
6. **The desktop console works on small screens.** `AppShell` gets a collapsible sidebar below `lg`, a link to the mobile view, and a dismissible suggestion to switch on narrow viewports. Nobody is forced to switch.

## Non-goals

- **Offline actions.** No background sync or queued acknowledgements. Offline, the cached shell loads and shows "You're offline". API calls fail visibly as they do today. The service worker never caches `/api` or `/ws`.
- **Native apps, app-store packaging, TWA or Capacitor.**
- **SMS or voice paging.** Email and push are the only channels.
- **Per-user notification preferences** (quiet hours, per-machine or per-severity filters). The audience is decided by role and paging level, as for email. The only per-user control is opting a device in or out.
- **Machine-to-technician assignment.** It is still out of scope (`dispatch.py:7-8`), so every operator with a push subscription is pushed.
- **New realtime event types.** Subscribing is per-user state with no audience on the socket (see "Realtime events").
- **A QR backend, signed QR tokens or bulk label printing.** The label is a plain deep link behind normal authentication. Printing all labels from the Machines list is a follow-up (see "Docs to update").
- **Workbox or `vite-plugin-pwa`.** The service worker is about 120 lines of hand-written JS (decision 7).
- **Mobile work-order editing.** The mobile view raises orders with server defaults. Assigning, editing and completing stay on the desktop `/work-orders/:id` drawer, which becomes usable on a phone through the responsive shell.
- **Editing the forbidden ML modules** (`src/prediction/rul_realtime.py`, `src/training/**`, `models/**`, `src/ingestion/xjtu_sy.py`, `src/features/**`) or `PROJECT_CONTEXT.md`. This feature touches none of them.
- **Changing `alert_escalated` semantics, `ALERT_EVENT_TYPES`, the paging thresholds or the email audience.**

## Decisions

1. **Push is a second channel inside `dispatch`, not a realtime listener. This is migration 7.**
   - `notify_alert` and `notify_device_incident` (`src/notifications/dispatch.py:76-106, 136-164`) send their emails exactly as today, then call `push.push_to_roles(...)`.
   - Every caller of `dispatch` already runs on a worker thread:
     - `pipeline.fan_out` (`src/prediction/pipeline.py:149`) is called from sync FastAPI routes (threadpool: `demo.py:33,70`, `predictions.py:78`), the MQTT ingest worker (`src/telemetry/ingest.py:162`) and replay threads (`src/ingestion/replay_service.py:96`);
     - the paging ladder (`src/alerts/paging.py:221`) and the watchdog (`src/telemetry/watchdog.py:267`) run in `asyncio.to_thread` (`src/background/scheduler.py:130`).
   - So "never send push from the event loop" holds by construction. A realtime-listener design would run on the loop (`mqtt_service.py:390-391`) and would have to offload anyway. It would also fire for demo and replay events without the paging context (level, roles).
   - `push_to_roles` also refuses to run on an event-loop thread. If `asyncio.get_running_loop()` succeeds in the calling thread, it logs an error and returns 0 without sending. A test pins this guard, so a future async caller fails loudly in tests instead of stalling the loop.
   - Pushes are sent **synchronously, one by one, with a 5 s per-request timeout** (`PUSH_TIMEOUT_S = 5`, a constant). That is the same model as the SMTP send beside it. Pages are rare: one per alert episode, plus at most one per escalation and ladder step. A thread pool would need its own SQLite connections to log rows, and the failure semantics would become harder to test. This is recorded as a risk.

2. **Audience: email unchanged; push = the email roles at that level, plus operators at level 0.**
   - `dispatch` gains `DEFAULT_PUSHED_ROLES = ("admin", "supervisor", "operator")`.
   - `notify_alert(conn, alert, *, roles=DEFAULT_PAGED_ROLES, page_level=0, push_roles=None)`. `push_roles=None` means `DEFAULT_PUSHED_ROLES` when `page_level == 0`, otherwise `roles`.

     | Trigger | Email | Push |
     |---|---|---|
     | `alert_created` / `alert_escalated` (level 0, `fan_out`) | admin, supervisor | admin, supervisor, **operator** |
     | Ladder level 1 (`paging.py`, `LADDER[1]`) | supervisor | supervisor |
     | Ladder level 2 (`LADDER[2]`) | admin | admin |
     | Device silence (`watchdog` → `notify_device_incident`) | admin, supervisor | admin, supervisor, **operator** |
     | `alert_resolved`, `device_online`, acks, closes, work orders | — | — |

   - `notify_device_incident(conn, incident, *, roles=DEFAULT_PAGED_ROLES, push_roles=DEFAULT_PUSHED_ROLES)`.
   - **Operators are not added to the ladder.** The ladder exists to reach someone *above* whoever ignored the level-0 page, and operators already had it.
   - **No push for resolutions**, for the reason given at `pipeline.py:30-32`: paging on good news trains people to ignore the channel. `fan_out` already calls `notify_alert` only for `_PAGING_EVENTS`, so this needs no new code. A test pins it.
   - **Return values are unchanged.** Both functions still return the number of *emails* delivered (`SimulateFaultResponse.emails_sent` and existing tests depend on it). Push outcomes live in the `notifications` rows and in the logs.

3. **Push never undoes or blocks the email, and never undoes the committed write that triggered it.**
   - The email loop and its `commit` run first, unchanged.
   - The push call is wrapped in `try/except Exception` + `logger.exception` inside `notify_alert` / `notify_device_incident`, the same pattern as `pipeline.py:137-174`. Callers already wrap `notify_*` (`pipeline.py:148-151`, `paging.py:220-224`, `watchdog.py:266-270`).
   - `send_push` itself never raises (decision 5). The outer guard only catches bugs.

4. **Storage: a `push_subscriptions` table and a guarded `notifications.channel` column, no CHECK.**
   - **One row per browser push endpoint.** `endpoint` is UNIQUE. Subscribing upserts on it: the same phone re-subscribing, or a different user signing in on a shared phone, **rebinds** the row to the current user and refreshes its keys. A push endpoint can therefore only ever page one person, the last one who subscribed on that device.
   - **Unsubscribe hard-deletes** the caller's own row; the user asked to stop. **Expiry soft-deletes:** a 404/410 from the push service sets `is_active = 0` and `deactivated_at`, so a dead device shows in diagnostics. Re-subscribing the same endpoint reactivates it.
   - **`notifications.channel TEXT NOT NULL DEFAULT 'email'`**, added by a guarded `ALTER` (convention §1, "Columns"). The value is `'email' | 'push'`, enforced in code. There is no CHECK, because SQLite cannot add one to an existing table, and a CHECK would also block `ALTER TABLE ... DROP COLUMN` in the v6 test fixture. Existing rows and the email INSERTs need no change: the DEFAULT labels them.
   - **Push rows** fill `recipient_email` (NOT NULL) from `users.email`, `recipient_role` from `users.role`, `subject` = the push title and `body` = the push body. `status` is `'sent'`, or `'failed'` for any failure including expiry (the Notifications page filters on `sent | failed`; `src/api/routes/notifications.py:23`). `alert_id` / `device_incident_id` are set as for the email rows, and both are NULL for test pushes. There is one row per subscription attempted, so a user with two phones gets two rows.
   - **No index on `notifications.channel`.** On a legacy DB, migration 1 runs `SCHEMA` against a `notifications` table without the column, so an index in `SCHEMA` would fail there. This is the migration-4 reasoning (`src/storage/migrations.py:131-133`).

5. **`src/notifications/push.py`: VAPID from env, disabled gracefully, deactivate on 404/410.**
   - **Settings are read from env on every call**, like `email._settings` (`src/notifications/email.py:33`), so tests can monkeypatch them and no restart is needed after keys are added.
   - **Enabled** means `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` are both set and valid:
     - public key: base64url that decodes to 65 bytes starting `0x04` (an uncompressed P-256 point, the browser's `applicationServerKey`);
     - private key: base64url that decodes to 32 bytes (raw scalar), or to a DER EC key. py-vapid's `from_string` accepts both.
   - **Graceful disable.** One key unset → disabled, with one `logger.info`. A malformed key → disabled, with one `logger.error` naming the variable but never logging its value. Startup never fails, and the email path is unaffected.
   - **`VAPID_SUBJECT`** must start with `mailto:` or `https://`. Unset, it defaults to `mailto:` + `SMTP_FROM` (default `alerts@maintainiq.local`). README warns that Apple's push service rejects subjects it cannot resolve, so production must set a real address.
   - **One pywebpush call per subscription,** behind a module-level `_webpush(**kwargs)` seam that tests monkeypatch:
     - `ttl = 43200` (12 h): a page older than a shift is noise;
     - `headers = {"Urgency": "high"}` for `severity == "high"` and for device incidents, else `"normal"`;
     - `timeout = PUSH_TIMEOUT_S`.
   - **Result classification.** `WebPushException` with a response status of 404 or 410 → `expired` (deactivate the subscription). Any other `WebPushException`, a `requests` exception or any other exception → `failed` (logged, subscription kept). Success → `sent`, and `last_used_at` is stamped.
   - **CLI:** `python -m src.notifications.push --generate-vapid` prints three ready-to-paste `.env` lines (`VAPID_PUBLIC_KEY=…`, `VAPID_PRIVATE_KEY=…`, `VAPID_SUBJECT=mailto:you@example.com`). It uses `cryptography` (already a pywebpush dependency) to generate a P-256 key and emits the raw public point and raw private scalar as unpadded base64url. Exit 0. With no flags it prints usage and exits 2.

6. **SSRF guard: push endpoints must be HTTPS on a known push-service host.**
   - The server POSTs to whatever endpoint a subscriber supplies. Every signed-in role can subscribe, so an unchecked endpoint would let any operator account make the server send requests to internal hosts.
   - `POST /api/push/subscribe` rejects (400) any endpoint that is not `https://`, has userinfo or an explicit port, or whose host is not on the allowlist.
   - Default allowlist (suffix match on a dot boundary): `fcm.googleapis.com`, `android.googleapis.com`, `updates.push.services.mozilla.com`, `push.services.mozilla.com`, `notify.windows.com`, `push.apple.com`. These cover Chrome/Edge/Android, Firefox, Windows (WNS) and Safari/iOS (`web.push.apple.com`).
   - `PUSH_ALLOWED_HOSTS` (comma-separated) **replaces** the list, for a self-hosted push service or a future browser.
   - `send_push` re-checks the host before sending, so a row inserted by hand cannot bypass the guard.
   - **Every parser must agree on the host** (fix, 2026-10-07). `urlsplit` keeps a backslash inside the host while requests/urllib3, which pywebpush POSTs through, end the authority at it, so `https://169.254.169.254\.fcm.googleapis.com/x` passed the suffix check and was sent to `169.254.169.254`. `endpoint_allowed` now refuses backslashes, whitespace, control and non-ASCII characters, accepts only a plain DNS name as the host (no `%`, no IP literal), and requires `urllib3.util.parse_url` to report the same scheme, host, port and userinfo.

7. **A hand-written service worker, `frontend/public/sw.js`, with versioned caches and no `/api` caching.**
   - **Never intercepted:** non-GET requests, cross-origin requests (the Fontshare and Google font CDNs stay plain network), any path starting `/api/` (which includes `/api/ws`), and `/ws`. Each returns early without `respondWith`, so the browser handles it exactly as without a service worker.
   - **Navigations (`request.mode === 'navigate'`):** network-first. On failure, serve the cached `/index.html` (precached at install). A deploy is therefore picked up on the next online load. `index.html` is never served stale while online.
   - **`/assets/*`** (Vite's content-hashed files): cache-first into `miq-assets-v1`, filled at runtime. Only `response.ok && response.type === 'basic'` is stored. The cache is trimmed to 60 entries, oldest first, after each insert.
   - **`/icons/*`, `/manifest.webmanifest`, `/favicon.svg`:** stale-while-revalidate into `miq-shell-v1`.
   - **Install** precaches `['/index.html', '/manifest.webmanifest', '/icons/icon-192.png']`, then calls `skipWaiting()`. **Activate** deletes every `miq-*` cache not in the current version set, then calls `clients.claim()`. A `SW_VERSION` constant at the top of the file names the caches; bump it when the caching rules change.
   - **`push`:** parse `event.data.json()`. A missing or malformed payload falls back to "MaintainIQ / Open the app for details". Then `showNotification(title, { body, tag, renotify: true, data: { url }, icon: '/icons/icon-192.png', badge: '/icons/badge-96.png' })`. `tag` is `alert-<id>` or `device-<incident id>`, so an escalation or ladder page replaces the earlier notification for the same alert instead of stacking.
   - **`notificationclick`:** close the notification and resolve the target URL. Only a same-origin path starting with `/` and not `//` is accepted; anything else becomes `/m/alerts`. Focus an existing window and `navigate()` it, else `clients.openWindow(url)`.
   - **Why no Workbox:** the rules above are the whole requirement. A plugin would add a build-time precache manifest and a dependency, for about 40 lines of benefit.

8. **Service-worker registration only in production builds.**
   - `main.tsx` calls `registerServiceWorker()` from `src/pwa/registerServiceWorker.ts`. It registers `/sw.js` with `{ scope: '/' }` after `window` `load`, but only if `import.meta.env.PROD && 'serviceWorker' in navigator`.
   - In `npm run dev` there is no service worker. A worker under Vite's dev server would cache HMR modules, and push is tested against the built app anyway (see HTTPS in "Environment variables").
   - `usePush` never relies on `navigator.serviceWorker.ready`, which would hang forever in dev. It calls `navigator.serviceWorker.getRegistration()` and reports `unavailable` when there is none.

9. **The backend serves the PWA files from the dist root with the right MIME and caching.**
   - `spa_fallback` (`src/api/app.py:202-207`) is extracted into a module-level `spa_file_response(web_dir: Path, full_path: str) -> Response`, unit-testable with `tmp_path` (the route is only registered when `frontend/dist` exists at import, `:192`):

     | Path | `media_type` | `Cache-Control` |
     |---|---|---|
     | `sw.js` | `text/javascript; charset=utf-8` | `no-cache` |
     | `manifest.webmanifest` | `application/manifest+json` | `no-cache` |
     | `index.html`, and every SPA fallback | `text/html; charset=utf-8` | `no-cache` |
     | other real files | guessed by `FileResponse` | unchanged |

   - **Why explicit MIME types:** Python's `mimetypes` on Windows reads the registry, which on many machines maps `.js` to `text/plain` (browsers refuse to register a worker served that way). `.webmanifest` is missing from older tables.
   - **Path traversal:** the resolved candidate must be inside the resolved `web_dir` (`Path.resolve()` + `is_relative_to`). Anything else gets the `index.html` fallback. Today `_WEB_DIR / full_path` is not checked (`:204`).
   - **Unknown `/api/...` paths now get a 404 JSON** (`{"detail": "Not Found"}`) instead of `index.html` with status 200. The service worker and the API client both assume `/api` is never HTML.
   - The service worker file lives at the dist root, so its default scope is `/`, and no `Service-Worker-Allowed` header is needed.

10. **Push routes are open to every authenticated role and bound to the caller.**
    - New `src/api/routes/push.py`, `APIRouter(prefix="/push")`, added to the `get_current_user` tuple in `app.py` (`:175-177`). Each handler also takes `user: dict = Depends(get_current_user)`; FastAPI caches the dependency, so there is still one user lookup per request.
    - All handlers are **sync `def`**, so the test push runs in the threadpool, never on the loop (decision 1).
    - A user can only create, refresh, delete or test **their own** subscriptions. Endpoints are never returned except to their owner.

11. **The mobile view is a separate shell under `/m`, inside `RequireAuth`, beside `AppShell`.**
    - Routes: `/m` (index, redirects to `/m/alerts`), `/m/alerts`, `/m/alerts/:id`, `/m/machines`, `/m/machines/:id`, `/m/scan`, `/m/settings`.
    - **Ids live in the path**, never in the query string, so they survive even an older login page.
    - **Why a separate shell rather than responsive pages:** the operator flow (find alert → act → next) wants a single column, thumb-reach actions and a bottom tab bar. Bending the desktop tables into that would hurt both.
    - The desktop pages stay reachable from the phone through the responsive `AppShell` (decision 15). The mobile pages reuse the desktop's API client, types, `AlertExplanationView`, `AlertCloseDialog`, `healthStyles` and `formatRelative`.

12. **Quick actions: Acknowledge and Work order are optimistic one-taps; Close opens the existing dialog; Why? opens the detail page.**
    - **Acknowledge.** Set `acknowledged_at = now` (and `acknowledged_by = me`) locally, then `POST /alerts/{id}/acknowledge`. On success, replace the alert with the server's. On error, restore the snapshot taken before the change and show `toast.error`. The server route is idempotent, so a lost response that is retried is safe.
    - **Work order.** One tap: `POST /alerts/{id}/work-order` with no body. The server derives the title and priority (`src/work_orders/service.py:253-260`) and also acknowledges (`:233-238`).
      - Optimistic state: `active_work_order_id = PENDING_WORK_ORDER` (the sentinel `-1`), shown as "Creating work order…", and `acknowledged_at` set if empty.
      - On success: the real id. The chip links to `/work-orders/:id` (the desktop drawer, phone-usable after decision 15).
      - On 409 ("already has an active work order"): roll back, re-fetch, `toast.info("Alert #n already has a work order")`.
      - Any other error: roll back plus `toast.error`.
    - **Close.** Opens `AlertCloseDialog` (`frontend/src/components/AlertCloseDialog.tsx:12-24`), which already fits a phone (`max-h-[calc(100dvh-2rem)]`, `w-full`). It is not optimistic: it is a form with server validation. On save, the card is replaced with `result.alert`, and on the Open filter it is dropped from the list once `status === 'resolved'`.
    - **Why?** Navigates to `/m/alerts/:id`, which leads with the explanation.
    - **Touch targets:** every action button is at least 44 × 44 CSS px (`min-h-11 min-w-11`), with a visible text label, not just an icon. Disabled states keep their size.
    - **Concurrent live updates.** Optimistic edits are kept in a per-alert "pending" map. A re-fetch triggered by a live event merges server rows *under* pending edits until each request settles. The user's own optimistic state therefore never flickers back, and other users' changes still land.

13. **Live updates reuse `LiveEventsProvider`; no new event types.**
    - Mobile pages follow the existing `load` + re-fetch-on-`lastEvent` pattern.
    - The alert list re-fetches on any event that carries an `alert` and on work-order events. The alert detail page uses the same `isAboutAlert` rule as `AlertExplanationPanel` (`frontend/src/components/AlertExplanationPanel.tsx:19-22`). That rule and the panel's load/reload logic (`:29-58`) are extracted into `src/lib/useAlertExplanation.ts` so both use one implementation. The panel's behaviour and tests are unchanged.
    - **Expired session.** The server closes an unauthenticated socket *before accepting it* (`src/realtime/routes/realtime.py:17-19`), so browsers report close code 1006, not 4401. A phone left open on the alert list makes no REST calls, so after the 12 h JWT expires it would show "Reconnecting…" forever.
    - Fix: when a socket closes **without ever having opened** (or with code 4401), the provider calls `api.me()` before scheduling the reconnect. A 401 there fires the existing `miq:unauthorized` event (`frontend/src/api/client.ts:61-64`), which signs the app out (`frontend/src/auth/AuthContext.tsx:41-45`) and sends the user to login, then back to where they were (decision 14).

14. **`LoginPage` restores pathname + search + hash, same-origin only.**
    - `from` is rebuilt as `pathname + search + hash` from the `location` that `RequireAuth` already passes in `state` (`frontend/src/auth/RequireAuth.tsx:17`).
    - It is used only if it starts with `/` and not `//`. Otherwise the default `/dashboard` is used.
    - Both the already-signed-in redirect (`LoginPage.tsx:24`) and the post-submit navigate (`:34`) use one `returnPath(state)` helper.

15. **The desktop `AppShell` becomes responsive and points to the mobile view; it never forces it.**
    - **`lg` and up:** unchanged.
    - **Below `lg`:**
      - the `<aside>` (`AppShell.tsx:148`) is hidden;
      - the header gains a menu button (`aria-label="Open navigation"`, `aria-expanded`, `aria-controls="app-nav-drawer"`) that opens the same nav as an off-canvas drawer;
      - the drawer closes on route change, backdrop click and Escape (`useDialogFocus`);
      - the user name block is hidden below `sm`.
    - **Header link** "Mobile view" (lucide `Smartphone`) to `/m`, for every role.
    - **Suggestion banner** (`MobileViewSuggestion`): shown when `matchMedia('(max-width: 640px)')` matches, the path is outside `/m`, and `localStorage['miq:mobile-suggest-dismissed'] !== '1'`. It reads "On a phone? The operator view is built for it." with **Open mobile view** and **Dismiss**. Dismissal persists. It is never a redirect.
    - **MobileShell** has a "Desktop view" link on the Settings tab.

16. **QR labels: the `qrcode` package, an SVG data URL, an absolute origin URL.**
    - `frontend` gains `qrcode` plus `@types/qrcode` (dev).
    - `MachineQrLabelDialog` (desktop, opened from a "QR label" button on `MachineDetailPage`) builds the URL with `machineDeepLink(window.location.origin, id)` = `${origin}/m/machines/${encodeURIComponent(id)}`.
    - It renders the code with `QRCode.toString(url, { type: 'svg', errorCorrectionLevel: 'M', margin: 2 })` as an `<img src="data:image/svg+xml;utf8,…">`. `toString` needs no canvas (jsdom has none). An `<img>` avoids `dangerouslySetInnerHTML`.
    - `qrcode` is loaded with a dynamic `import()` inside the dialog, so it stays out of the main bundle.
    - The label shows the QR, the machine id in large type, "MaintainIQ" and the URL in small mono text. A **Print** button calls `window.print()`. An `@media print` rule in `index.css` hides everything except `.print-label`.
    - The dialog state lives in `MachineDetailPage`, not in `MachineDetail`, because `MachineDetail` remounts on every live event for the machine (`frontend/src/pages/MachineDetailPage.tsx:16-19, 32-34`).
    - **The origin is whatever the desktop is using.** A label printed from `http://localhost:8000` encodes a URL a phone cannot reach. The dialog therefore shows a warning when `location.hostname` is `localhost`, `127.0.0.1` or `::1`: "This label points at localhost — print it from the address phones use."

17. **Scanning: `BarcodeDetector` when available; only same-origin `/m/machines/` URLs are accepted.**
    - **Camera path.** `/m/scan` uses the camera when `'BarcodeDetector' in window`, `navigator.mediaDevices?.getUserMedia` exists, and `await BarcodeDetector.getSupportedFormats()` includes `'qr_code'` (Chrome/Edge on Android).
      - It requests `{ video: { facingMode: 'environment' } }` and runs `detector.detect(video)` every 250 ms (`setInterval`, cleared on unmount).
      - Every track is stopped on unmount, on success and on `visibilitychange` to hidden.
    - **Fallback** (iOS Safari, Firefox, denied permission, no camera): a manual "Machine id" field (submit navigates to `/m/machines/<id>`), plus the instruction "Or open your phone's camera app and point it at the machine's QR label — it opens the machine page directly." The manual field is always shown, also below the camera view.
    - **`parseMachineQr(text, origin)`** in `src/lib/machineQr.ts`:
      - parse with `new URL(text)`; a parse failure → `null`;
      - require `url.origin === origin` and `url.pathname` matching `^/m/machines/([^/]+)/?$`;
      - return the `decodeURIComponent`-ed id, or `null` on a decode error.
    - Anything else (other sites, `javascript:`, relative text, other app paths) is rejected with "That QR code isn't a MaintainIQ machine label for this site." Scanning continues.
    - **Why same-origin only:** a scanned code is attacker-controllable input. Following arbitrary URLs from inside an installed app is phishing-shaped.

18. **Push subscription UX: per device, opt-in, refreshed on visit, removed on explicit logout.**
    - **`usePush()`** (`src/push/usePush.ts`) returns `{ state, enable, disable, sendTest, error }`. `state` is one of:
      - `'loading'`;
      - `'unsupported'`: no `serviceWorker`, `PushManager` or `Notification`;
      - `'insecure'`: `!window.isSecureContext`;
      - `'unavailable'`: no service-worker registration, e.g. a dev build;
      - `'server-disabled'`: `enabled: false` from the server;
      - `'denied'`: `Notification.permission === 'denied'`;
      - `'off'`;
      - `'on'`;
      - `'busy'`.
    - **`enable()`** runs from the toggle's click handler, because iOS requires a user gesture:
      1. `Notification.requestPermission()`;
      2. `registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(public_key) })`;
      3. `POST /api/push/subscribe` with `subscription.toJSON()`.
      If the POST fails, the browser subscription is unsubscribed again, so the two sides never disagree.
    - **`disable()`:** `DELETE /api/push/subscribe {endpoint}`, then `subscription.unsubscribe()`. The browser side is removed even if the DELETE fails, and the error is shown.
    - **On mount, with an existing browser subscription:**
      - if its `options.applicationServerKey` differs from the server's key (keys rotated), unsubscribe and re-subscribe;
      - otherwise re-POST it. The idempotent upsert reactivates a row the server deactivated on 410 and rebinds a shared phone to whoever is signed in.
    - **Explicit logout** (`AuthContext.logout`, `:52-55`) first calls `unsubscribeThisDevice()`. That is the DELETE plus `unsubscribe()`, best-effort, capped at 2 s with `Promise.race`, and never blocking the logout. Otherwise a shared phone keeps paging the last user.
    - **Session expiry keeps the subscription.** An operator whose 12 h cookie lapsed should still be paged. Tapping the push goes through login and back to the alert (decision 14).
    - **Where the toggle lives:** `/m/settings` (all roles) and, on desktop, a "Push notifications on this device" control in the `AppShell` user menu area. The desktop `/notifications` page is admin and supervisor only, so it cannot host the toggle.

19. **Icons are generated once with Pillow from the brand mark and committed.**
    - The current `favicon.svg` is Vite's default logo, not the MaintainIQ brand. The brand mark is the lucide `Radar` glyph on an azure-to-violet gradient tile (`AppShell.tsx:151-153`, `LoginPage.tsx:52-54`; colours `--color-accent-2 #4dc9ff` and `--color-accent #7c6cff`, `frontend/src/index.css:49-56`).
    - New `scripts/generate_pwa_icons.py` (dev-only; Pillow 12 is already in the environment and is **not** added to `requirements.txt`). It draws, at 4× supersampling and then downsampled:
      - a diagonal `#4dc9ff → #7c6cff` gradient rounded square on `#000000` (`icon-192.png`, `icon-512.png`, `apple-touch-icon-180.png`; the Apple icon is full-bleed with no transparency, because iOS adds its own mask);
      - a full-bleed gradient with the glyph inside the 80 % safe zone (`icon-maskable-512.png`);
      - a white-on-transparent monochrome glyph for Android's status bar (`badge-96.png`).
    - The radar glyph is three concentric arcs and a sweep line, drawn white with round caps, stroke width 2/24 of the glyph box (lucide's proportions).
    - Output goes to `frontend/public/icons/`, and the PNGs are committed. `favicon.svg` is replaced by an SVG of the same mark, so the browser tab, the installed icon and the in-app logo agree.

20. **No new realtime events; `LiveEvent` is untouched.** Push subscriptions have no audience on the shared socket, and a page is already visible as `alert_created` / `alert_escalated` / `alert_paged` / `device_offline`. Nothing is added to `ALERT_EVENT_TYPES` (`src/telemetry/protocol.py:65`), so nothing new is republished to devices.

## Data model

### `src/storage/db.py`

1. **`notifications` in `SCHEMA`** (`:181-191`) gains, after `status`:
   ```sql
       channel TEXT NOT NULL DEFAULT 'email',
   ```
   That puts it between `status TEXT NOT NULL DEFAULT 'sent'` and `created_at TEXT NOT NULL`.

2. **New constant after `EXPLANATION_SCHEMA`** (`:361-375`), with a comment in the style of its neighbours:
   ```python
   # Web Push subscriptions (design/2026-10-07-mobile-operator-pwa-design.md):
   # one row per browser push endpoint, bound to the user who last subscribed
   # on that device. Unsubscribing deletes the row; a 404/410 from the push
   # service deactivates it (is_active = 0) so dead devices stay visible.
   # Pages are logged in `notifications` with channel = 'push'. Applied on its
   # own by migration 7 and folded into SCHEMA for fresh installs.
   PUSH_SCHEMA = """
   CREATE TABLE IF NOT EXISTS push_subscriptions (
       id              INTEGER PRIMARY KEY AUTOINCREMENT,
       user_id         INTEGER NOT NULL REFERENCES users(id),
       endpoint        TEXT NOT NULL UNIQUE,
       p256dh          TEXT NOT NULL,
       auth            TEXT NOT NULL,
       user_agent      TEXT,
       is_active       INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
       created_at      TEXT NOT NULL,
       updated_at      TEXT NOT NULL,
       last_used_at    TEXT,
       deactivated_at  TEXT
   );
   CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user ON push_subscriptions(user_id, is_active);
   """
   ```

3. **Fold it in:** `SCHEMA = (SCHEMA + TELEMETRY_SCHEMA + ... + EXPLANATION_SCHEMA + PUSH_SCHEMA)` (`:377-378`).

**Column notes:**
- `endpoint` is the push-service URL. It is a bearer capability, so it is never logged in full: logs use `endpoint[:40] + '…'`.
- `p256dh` and `auth` are the browser's base64url keys, stored verbatim (≤ 200 and ≤ 100 chars, validated on input).
- `user_agent` is the request's `User-Agent`, truncated to 255 chars. It is for diagnostics only.
- `updated_at` is set on every upsert. `last_used_at` is set on each successful push. `deactivated_at` is set on expiry and cleared on reactivation.
- There is no ON DELETE: users are deactivated, never deleted (`users.is_active`). Recipients are always joined to `users.is_active = 1`.

### `src/storage/migrations.py`

```python
def _migration_007_push(conn: sqlite3.Connection) -> None:
    # Additive: the push_subscriptions table plus a channel column on
    # notifications (DEFAULT 'email', so every existing row is labelled
    # correctly). executescript() COMMITs first; the guarded ALTER after it is
    # idempotent, so a crash between the two re-runs cleanly (same shape as
    # migration 3). No index on the new column, for the legacy-DB reason given
    # in migration 4.
    conn.executescript(PUSH_SCHEMA)
    try:
        conn.execute("ALTER TABLE notifications ADD COLUMN channel TEXT NOT NULL DEFAULT 'email'")
    except sqlite3.OperationalError:
        pass  # fresh DB: SCHEMA already has the column
```

- Append `(7, _migration_007_push)` to `MIGRATIONS` (`:171-178`), and import `PUSH_SCHEMA` beside the other constants.
- Add a migration-7 paragraph to the module docstring.
- `LATEST_VERSION` (`:203`) follows automatically, so `ensure_current_schema` upgrades v6 files on the first request (`src/api/deps.py:22`) and on the paging job's first tick (`src/alerts/paging.py:157`).
- **Readers that may run on a stale connection.** Replay threads and MQTT ingest get their connections from their own factories. `push_to_roles` therefore checks `db.table_exists(conn, "push_subscriptions")` (`src/storage/db.py:381`) and the `channel` column (`PRAGMA table_info(notifications)`) before anything else. If either is missing it logs at debug level and returns 0. Email INSERTs don't name `channel`, so they keep working on a v6 schema.
- `list_notifications` guards the `channel` column the way it already guards `device_incident_id` (`src/api/routes/notifications.py:35-40`), reporting `'email' AS channel` on an older DB.

## Services

### `src/notifications/push.py` (new)

```python
PUSH_TIMEOUT_S = 5
PUSH_TTL_S = 43200
DEFAULT_ALLOWED_HOSTS = ("fcm.googleapis.com", "android.googleapis.com",
                         "updates.push.services.mozilla.com", "push.services.mozilla.com",
                         "notify.windows.com", "push.apple.com")

@dataclass(frozen=True)
class PushSettings:
    public_key: str
    private_key: str
    subject: str

def settings_from_env() -> PushSettings | None          # None = disabled (decision 5)
def allowed_hosts() -> tuple[str, ...]                   # PUSH_ALLOWED_HOSTS or the default
def endpoint_allowed(endpoint: str) -> bool              # decision 6
def alert_payload(alert: dict, page_level: int = 0) -> dict
def device_payload(incident: dict) -> dict
def test_payload(user: dict) -> dict
def send_push(subscription: dict, payload: dict, settings: PushSettings) -> str  # 'sent' | 'failed' | 'expired'; never raises
def push_to_roles(conn, roles, payload, *, alert_id=None, device_incident_id=None) -> dict
def push_to_user(conn, user_id: int, payload: dict) -> dict
def generate_vapid_keys() -> tuple[str, str]             # (public_b64url, private_b64url)
def main(argv=None) -> int                               # --generate-vapid
```

**`push_to_roles` / `push_to_user`** return `{"sent": n, "failed": n, "expired": n}`. Steps:
1. Event-loop guard (decision 1).
2. `settings_from_env()`. `None` returns zeros, with no rows written.
3. Schema guard.
4. Select the targets:
   ```sql
   SELECT s.id, s.endpoint, s.p256dh, s.auth, u.email, u.role
   FROM push_subscriptions s JOIN users u ON u.id = s.user_id
   WHERE s.is_active = 1 AND u.is_active = 1 AND u.role IN (...)
   ORDER BY s.id
   ```
   (`AND u.id = ?` for `push_to_user`).
5. For each row, `send_push`, then:
   - insert one `notifications` row (`channel = 'push'`, decision 4);
   - on `sent`, `UPDATE push_subscriptions SET last_used_at = ?`;
   - on `expired`, `UPDATE ... SET is_active = 0, deactivated_at = ?, updated_at = ?`.
6. One `commit` at the end. This is the same shape as `notify_alert`'s loop (`dispatch.py:90-105`).

**Payload** (JSON, kept under 1 KB; the Web Push limit is about 4 KB):
```json
{
  "v": 1,
  "kind": "alert",
  "title": "MaintainIQ CRITICAL: m1 needs attention",
  "body": "critical · probable cause bearing_wear",
  "tag": "alert-42",
  "url": "/m/alerts/42",
  "alert_id": 42,
  "severity": "high",
  "page_level": 0
}
```
- **`title`** reuses `dispatch._compose`'s subject (`dispatch.py:55-60`), including the `[Unacknowledged — page n]` prefix at level ≥ 1, so email and push read the same.
- **`body`** is `"{health_state} · probable cause {probable_cause or 'unknown'}"`, truncated to 120 characters.
- **Device payloads** have `kind: "device"`, the `_compose_device` subject (`:119-124`), the body "Last heard {last_seen_at or 'unknown'}", tag `device-<id>`, and url `/m/machines/<machine_id>` when the node is assigned, else `/devices`.
- **Test payloads** have `kind: "test"`, title "MaintainIQ test notification", body "Push notifications work on this device.", tag `test` and url `/m/settings`.
- Payloads carry **no** email addresses or user names.

### `src/notifications/dispatch.py`

- **Module docstring:** replace "operators are excluded" (`:7-10`) with the channel table from decision 2. Mention `channel` on the log rows.
- **`DEFAULT_PUSHED_ROLES = ("admin", "supervisor", "operator")`** next to `DEFAULT_PAGED_ROLES` (`:35-36`).
- **`notify_alert(conn, alert, *, roles=DEFAULT_PAGED_ROLES, page_level=0, push_roles=None)`.** After the existing `conn.commit()` (`:105`):
  ```python
  try:
      push.push_to_roles(conn, _push_audience(roles, page_level, push_roles),
                         push.alert_payload(alert, page_level), alert_id=alert.get("id"))
  except Exception:
      logger.exception("Push for alert %s failed", alert.get("id"))
  ```
  Return `sent` (emails) as today.
- **`notify_device_incident(conn, incident, *, roles=DEFAULT_PAGED_ROLES, push_roles=DEFAULT_PUSHED_ROLES)`:** the same tail with `push.device_payload(incident)` and `device_incident_id=incident.get("id")`.
- `dispatch` gains `import logging` / `logger` (it has none today).

### `src/alerts/paging.py`, `src/telemetry/watchdog.py`, `src/prediction/pipeline.py`

- **No code change.** Paging calls `self._notify(conn, alert, roles=..., page_level=target)` (`paging.py:221-222`); with `push_roles=None` and `page_level ≥ 1`, push goes to exactly the ladder roles. The watchdog calls `self._notify(conn, incident)` (`watchdog.py:267`) and gets the operator push by default. `fan_out` calls `notify_alert(conn, alert)` only for `_PAGING_EVENTS` (`pipeline.py:32, 147-151`).
- **Docstrings only:** `paging.py:7-13` ("email and push"), `pipeline.py:30-31` ("worth an email (and a push)").

### `src/api/app.py`

- Import `push` in the routes tuple (`:21-36`) and add it to the `get_current_user` loop (`:175-177`). Update the comment at `:163-169`.
- Add `spa_file_response(web_dir, full_path)` above the `if _WEB_DIR.exists():` block (`:192`). Make `spa_fallback` (`:202-207`) a one-liner calling it (decision 9).

### `requirements.txt`

Add `pywebpush>=2.0,<3`. 2.5.0 is the current release; it pulls in `py-vapid`, `http-ece`, `requests` and `cryptography`, and `cryptography` 50 is already installed. Install with `pip install -r requirements.txt`. The Dockerfile already installs from this file.

## API

All routes are under `/api/push`, need a session cookie (401 `Not authenticated` otherwise), and are open to `admin`, `supervisor` and `operator`. Errors use FastAPI's `{"detail": "..."}` shape. Request-shape errors are 422 (FastAPI default), semantic validation is 400, "not possible right now" is 409, and "not configured" is 503.

### `GET /api/push/vapid-public-key`

`200`:
```json
{ "enabled": true, "public_key": "BElw…(87 chars)" }
```
When disabled: `{ "enabled": false, "public_key": null }`. It never errors for a signed-in user. `Cache-Control: no-store`.

### `POST /api/push/subscribe`

The body is exactly `PushSubscription.toJSON()`:
```json
{
  "endpoint": "https://fcm.googleapis.com/fcm/send/abc…",
  "expirationTime": null,
  "keys": { "p256dh": "BN…", "auth": "tB…" }
}
```

**Pydantic** `PushSubscribeRequest { endpoint: str (max_length 2048), expirationTime: Optional[float] = None, keys: PushKeys { p256dh: str (max_length 200), auth: str (max_length 100) } }`. `expirationTime` is accepted and ignored: browsers send null, and expiry is handled by 410.

**Behaviour:**
- **503** `push notifications are not configured` when disabled. A subscription the server can never use would only confuse.
- **400** `endpoint must be an https URL on a known push service` (decision 6).
- **400** `invalid subscription keys`: `p256dh` must base64url-decode to 65 bytes, `auth` to 16 bytes.
- Otherwise upsert:
  ```sql
  INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent, is_active, created_at, updated_at)
  VALUES (?, ?, ?, ?, ?, 1, ?, ?)
  ON CONFLICT(endpoint) DO UPDATE SET
      user_id = excluded.user_id, p256dh = excluded.p256dh, auth = excluded.auth,
      user_agent = excluded.user_agent, is_active = 1, deactivated_at = NULL,
      updated_at = excluded.updated_at
  ```
  then commit. Returns **200** (decision: an idempotent upsert always returns 200, so the client never has to distinguish new from refreshed).

Response `PushSubscriptionOut`:
```json
{ "id": 3, "endpoint": "https://fcm.googleapis.com/fcm/send/abc…", "is_active": true,
  "created_at": "2026-10-07T09:00:00+00:00", "updated_at": "2026-10-07T09:00:00+00:00",
  "last_used_at": null }
```

### `DELETE /api/push/subscribe`

Body `{ "endpoint": "https://…" }` (`PushUnsubscribeRequest`, `endpoint` max_length 2048). Runs `DELETE FROM push_subscriptions WHERE endpoint = ? AND user_id = ?` and commits. Always **204**, whether or not a row matched. It is idempotent, and it does not reveal whether another user owns the endpoint. Another user's row is never touched. `request<T>` already returns `undefined` on 204 (`frontend/src/api/client.ts:88`).

### `POST /api/push/test`

No body. It sends `push.test_payload(user)` to the caller's own active subscriptions, synchronously in the threadpool.
- **503** `push notifications are not configured`.
- **409** `no active push subscription for this user`.
- **200** `PushTestResult { "sent": 1, "failed": 0, "expired": 0 }`. One `notifications` row is logged per attempt (`channel = 'push'`, `alert_id` and `device_incident_id` NULL).

### `GET /api/notifications` (changed)

- **`NotificationOut`** (`src/api/schemas.py:344-353`) gains `channel: Literal["email", "push"] = "email"`.
- **New query parameter `channel: Optional[str]`:** `email | push`. Anything else → **400** `channel must be 'email' or 'push'`.
- The SELECT (`notifications.py:41-48`) adds the guarded `channel` column (see Data model).
- Roles are unchanged (admin and supervisor). Operators see their own push status on `/m/settings` through the test button, not through this log.

### Static files (changed, decision 9)

- `GET /sw.js` → `text/javascript; charset=utf-8`, `Cache-Control: no-cache`.
- `GET /manifest.webmanifest` → `application/manifest+json`, `Cache-Control: no-cache`.
- `GET /icons/*.png` → `image/png`.
- `GET /m/alerts/42` (any SPA path) → `index.html`, `Cache-Control: no-cache`.
- `GET /api/does-not-exist` → **404** JSON.
- `GET /..%2f..%2frequirements.txt` and other traversals → `index.html`, never a file outside `frontend/dist`.

### Schemas (`src/api/schemas.py`, a new "Push" section after Notifications)

`PushConfig`, `PushKeys`, `PushSubscribeRequest`, `PushUnsubscribeRequest`, `PushSubscriptionOut`, `PushTestResult`, with the shapes above.

## Realtime events

None added (decision 20). `LiveEvent` (`frontend/src/api/types.ts:570-576`), `describe()` (`LiveEventsProvider.tsx:32-61`), `NotificationBell`'s `PAGING_EVENT_TYPES` (`AppShell.tsx:80-85`) and `ALERT_EVENT_TYPES` are unchanged. The only provider change is the expired-session probe (decision 13).

## Notification behaviour

- **The email audience and content are unchanged.** Every email row now reads `channel = 'email'`.
- **Push audience:** the decision-2 table. A user gets a push only if they are active, have the role for that page, and have at least one active subscription. Users without subscriptions are simply not pushed; there is no 'failed' row for them.
- **Push timing:**
  - level-0 alert pushes are sent from `fan_out` right after the level-0 emails, before the realtime broadcast (`pipeline.py:147-174`);
  - ladder pushes are sent from the paging tick after its compare-and-set commit (`paging.py:205-222`);
  - device pushes are sent after the incident commit (`watchdog.py:266-267`).
  In every case the triggering row is already committed (convention §1, "Side effects").
- **Notification collapsing:** `tag` per alert or incident, with `renotify: true`. On the phone, an escalation replaces the original notification and alerts again.
- **Tap:** `/m/alerts/:id` (alerts), `/m/machines/:id` or `/devices` (device incidents), `/m/settings` (test). A signed-out user goes through login and back (decision 14).
- **Resolutions, acknowledgements, closes, outcomes and work-order activity** never push or email. The live socket and toasts carry those.
- **Push disabled** (no keys): no push attempts, no push rows, `enabled: false` in the UI, and email exactly as before.

## Frontend UX

### Types and client

**`frontend/src/api/types.ts`:**
- `NotificationOut` gains `channel?: 'email' | 'push'`.
- New `PushConfig { enabled: boolean; public_key: string | null }`.
- New `PushSubscriptionOut { id; endpoint; is_active; created_at; updated_at; last_used_at }`.
- New `PushTestResult { sent; failed; expired }`.

**`frontend/src/api/client.ts`:**
```ts
getPushConfig: () => request<PushConfig>('/push/vapid-public-key'),
subscribePush: (sub: PushSubscriptionJSON) =>
  request<PushSubscriptionOut>('/push/subscribe', { method: 'POST', body: JSON.stringify(sub) }),
unsubscribePush: (endpoint: string) =>
  request<void>('/push/subscribe', { method: 'DELETE', body: JSON.stringify({ endpoint }) }),
sendTestPush: () => request<PushTestResult>('/push/test', { method: 'POST' }),
getNotifications: (status?: string, channel?: 'email' | 'push') => …  // adds &channel=
```
All imports use `import type` (`verbatimModuleSyntax`).

### PWA files

**`frontend/public/manifest.webmanifest`:**
```json
{
  "id": "/m",
  "name": "MaintainIQ",
  "short_name": "MaintainIQ",
  "description": "Predictive maintenance — operator view",
  "start_url": "/m",
  "scope": "/",
  "display": "standalone",
  "background_color": "#000000",
  "theme_color": "#000000",
  "icons": [
    { "src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any" },
    { "src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any" },
    { "src": "/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
  ],
  "shortcuts": [
    { "name": "Open alerts", "url": "/m/alerts" },
    { "name": "Scan a machine", "url": "/m/scan" }
  ]
}
```
- `scope: "/"`, so desktop deep links (`/work-orders/7`, `/devices`) opened from the app stay inside it.
- `#000000` is `--color-bg` (`index.css:36`).

**`frontend/public/sw.js`:** decision 7.

**`frontend/public/icons/`:** `icon-192.png`, `icon-512.png`, `icon-maskable-512.png`, `apple-touch-icon-180.png`, `badge-96.png` (decision 19). `favicon.svg` is replaced with the brand mark.

**`frontend/index.html`** (`:3-6`):
- the viewport becomes `width=device-width, initial-scale=1.0, viewport-fit=cover`;
- new tags:
  ```html
  <link rel="manifest" href="/manifest.webmanifest" />
  <meta name="theme-color" content="#000000" />
  <link rel="apple-touch-icon" href="/icons/apple-touch-icon-180.png" />
  <meta name="mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <meta name="apple-mobile-web-app-title" content="MaintainIQ" />
  ```

**`frontend/src/main.tsx`:** `registerServiceWorker()` after `createRoot(...).render(...)` (decision 8).

### New modules

| File | Purpose |
|---|---|
| `src/pwa/registerServiceWorker.ts` | Production-only registration (decision 8). |
| `src/push/pushSupport.ts` | `urlBase64ToUint8Array`, `currentSubscription()`, `unsubscribeThisDevice()` (DELETE + `unsubscribe`, 2 s cap). |
| `src/push/usePush.ts` | The hook in decision 18. |
| `src/push/PushToggle.tsx` | A switch with a status line per `state`, e.g. `insecure` → "Push needs HTTPS — see README 'Mobile & push'", `denied` → "Notifications are blocked in this browser's site settings", `server-disabled` → "Push isn't configured on this server". "Send test" shows when `on`. Used by `/m/settings` and the desktop header menu. |
| `src/lib/machineQr.ts` | `machineDeepLink(origin, id)`, `parseMachineQr(text, origin)` (decision 17). |
| `src/lib/returnPath.ts` | `returnPath(state)` for `LoginPage` (decision 14). |
| `src/lib/useAlertExplanation.ts` | Extracted from `AlertExplanationPanel` (decision 13): `{ explanation, error, reload }` plus `isAboutAlert`. |
| `src/mobile/useQuickAlertActions.ts` | Optimistic acknowledge / work order with rollback, the pending map and the merge (decision 12). |
| `src/mobile/MobileAlertCard.tsx` | Compact card plus the four action buttons. |
| `src/layout/MobileShell.tsx` | Shell and bottom tabs. |
| `src/pages/mobile/MobileAlertsPage.tsx`, `MobileAlertDetailPage.tsx`, `MobileMachinesPage.tsx`, `MobileMachineDetailPage.tsx`, `MobileScanPage.tsx`, `MobileSettingsPage.tsx` | Pages. |
| `src/components/MachineQrLabelDialog.tsx` | Desktop QR label (decision 16). |
| `src/components/MobileViewSuggestion.tsx` | Narrow-viewport banner (decision 15). |

### Routes (`frontend/src/App.tsx:43-72`)

Inside `<Route element={<RequireAuth />}>`, as a sibling of the `AppShell` route:
```tsx
<Route path="m" element={<MobileShell />}>
  <Route index element={<Navigate to="alerts" replace />} />
  <Route path="alerts" element={<MobileAlertsPage />} />
  <Route path="alerts/:id" element={<MobileAlertDetailPage />} />
  <Route path="machines" element={<MobileMachinesPage />} />
  <Route path="machines/:id" element={<MobileMachineDetailPage />} />
  <Route path="scan" element={<MobileScanPage />} />
  <Route path="settings" element={<MobileSettingsPage />} />
</Route>
```
No `RequireRole`: every role can use every mobile page. The catch-all `*` (`:74`) still sends unknown paths to `/`.

### `MobileShell`

- **Full-height column.** A compact sticky header holds the brand mark, "MaintainIQ" and the live dot (the same `connected` indicator as `AppShell.tsx:204-221`).
- **Content** has `pb-[calc(4.5rem+env(safe-area-inset-bottom))]`.
- **A fixed bottom `<nav aria-label="Mobile">`** with four `NavLink`s: Alerts (`ShieldAlert`), Scan (`ScanLine`), Machines (`Gauge`), Settings (`Settings`). Each is ≥ 56 px tall and has an icon over a label. The active tab gets `aria-current="page"` (NavLink's default) and the accent colour.
- **No sidebar, no page-enter animation** (motion costs battery and adds latency on cheap phones), and no `useSpotlight` effects.

### Pages

- **`/m/alerts`:**
  - segmented control **Open** (default, `api.getAlerts('open')`) / **Recent** (`api.getAlerts()`, first 50);
  - a list of `MobileAlertCard`s. A card shows a severity intensity bar (`severityClasses`), the machine id, `healthLabel(health_state)`, `causeLabel(probable_cause)`, `formatRelative(opened_at)`, chips for "Acknowledged", "Paged L{n}" (`pageLevelTone`) and "WO #n", and an action row;
  - tapping the card body goes to `/m/alerts/:id`;
  - empty Open list: "No open alerts. Machines are healthy.";
  - re-fetches on alert, feedback and work-order live events (decision 13).
- **`/m/alerts/:id`:**
  - `useAlertExplanation(id)`;
  - a summary card, the action bar sticky above the tab bar, then `<AlertExplanationView explanation={...} />` (`frontend/src/components/AlertExplanationView.tsx:46`) in a section with `id="why"`. "Why?" on this page scrolls to `#why`;
  - 404 → "Alert #n not found" with a link back to Alerts;
  - a "Desktop view" link to `/alerts/:id`.
- **`/m/machines`:** `api.getMachines()`, a filter box (matches machine id), and rows with a `healthClasses` dot, the id, `healthLabel`, RUL (`predicted_rul_minutes`, formatted like the desktop grid) and the open alert count. Tapping a row goes to `/m/machines/:id`.
- **`/m/machines/:id`:**
  - `api.getMachine(id)`: health state, confidence, RUL, risk, `formatRelative(last_reading_at)`, an OOD notice when `out_of_distribution`;
  - the machine's open alerts as `MobileAlertCard`s with quick actions;
  - links to "Full machine page" (`/machines/:id`) and "Scan another";
  - 404 → "Unknown machine '<id>'" with a "Scan again" button. Re-fetches when `lastEvent.machine_id === id`, like `MachineDetailPage.tsx:32-34`.
- **`/m/scan`:** decision 17. A live `<video playsInline muted>` with an overlay frame when the camera path is active, a status line (`aria-live="polite"`), and the manual form ("Machine id" input + **Open**, a 44 px button).
- **`/m/settings`:** the signed-in name, email and role; `PushToggle`; "Desktop view" (`/dashboard`); **Log out** (`useAuth().logout`, which unsubscribes this device first, decision 18).

### Desktop changes

- **`AppShell.tsx`:** responsive sidebar, drawer, "Mobile view" link, `MobileViewSuggestion` above `<Outlet />`, and a compact `PushToggle` in a header popover ("This device") — decision 15. `NAV_SECTIONS` (`:41-75`) is shared by the sidebar and the drawer through one `<NavList>` component, so they cannot drift.
- **`MachineDetailPage.tsx`:** a "QR label" button (lucide `QrCode`) beside "← Back to machines" (`:52-54`), opening `MachineQrLabelDialog`.
- **`LoginPage.tsx`:** `returnPath` (decision 14).
- **`NotificationsPage.tsx`:** a "Channel" column (an "Email" / "Push" pill) before "To" (`:61`), and a channel filter next to the status filter.
- **`LiveEventsProvider.tsx`:** the expired-session probe in `onclose` (`:133-139`), decision 13.
- **`AuthContext.tsx`:** `logout` awaits `unsubscribeThisDevice()` (best-effort, capped) before `api.logout()`.
- **`AlertExplanationPanel.tsx`:** uses `useAlertExplanation`.
- **`index.css`:** an `@media print` block for `.print-label`, and the `.safe-bottom` utility (`padding-bottom: env(safe-area-inset-bottom)`).

### Tests and MSW

- **`src/test/server.ts`** gains default handlers:
  - `GET /api/push/vapid-public-key` → `pushConfig`;
  - `POST /api/push/subscribe` → `pushSubscription`;
  - `DELETE /api/push/subscribe` → 204;
  - `POST /api/push/test` → `{ sent: 1, failed: 0, expired: 0 }`.
  `onUnhandledRequest: 'error'` (`src/test/setup.ts:8`) would otherwise fail every test that renders `AppShell` (the header toggle calls the config endpoint).
- **`src/test/fixtures.ts`** gains `pushConfig`, `pushConfigDisabled` and `pushSubscription`. `notifications` entries get `channel`, with one `'push'` row. Fixtures are cloned per handler, never mutated.
- **`src/test/pwaStubs.ts`** exports `installPwaStubs(opts)`. It sets up:
  - `navigator.serviceWorker` (`register`, `getRegistration` → a fake registration whose `pushManager.getSubscription/subscribe` are `vi.fn`s);
  - `window.PushManager`;
  - `window.Notification` (`permission`, `requestPermission`);
  - `window.isSecureContext`;
  - optional `BarcodeDetector` and `navigator.mediaDevices.getUserMedia`.
  `setup.ts` calls it in `beforeEach` with "supported, secure, permission default, no subscription, no BarcodeDetector, no camera", and restores the originals in `afterEach`. Tests override per case.

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `VAPID_PUBLIC_KEY` | empty | Base64url uncompressed P-256 public key (the browser's `applicationServerKey`). Push is off unless both keys are set and valid. Generate with `python -m src.notifications.push --generate-vapid`. |
| `VAPID_PRIVATE_KEY` | empty | Base64url private key matching the public key. A secret: never logged, never sent to the browser. |
| `VAPID_SUBJECT` | `mailto:` + `SMTP_FROM` | Contact URI sent to push services (`mailto:` or `https://`). Set a real address in production; Apple rejects unresolvable ones. |
| `PUSH_ALLOWED_HOSTS` | the six push-service hosts in decision 6 | Comma-separated host suffixes that subscription endpoints may use. Replaces the default list. |

- **Validation:** invalid values are logged (`VAPID_*` values are never echoed) and push stays off. Startup never fails.
- **Docker:** `docker-compose.yml` needs no change, because the app service reads `.env` via `env_file` (`docker-compose.yml:6`). `.env.example` gains a commented "Web Push" block with the four variables and the generate command.
- **Tests:** the autouse fixture's prefixes (`tests/conftest.py:34`) gain `"VAPID_"` and `"PUSH_"`, so push is off in every test unless the test sets keys.
- **HTTPS (README "Mobile & push"):**
  - service workers and push need a secure context. `http://localhost:8000` is one on the machine itself, so desktop testing works with the built SPA (`npm run build`, then `uvicorn src.api.app:app`);
  - a phone reaching the laptop over the LAN (`http://192.168.x.y:8000`) is **not** a secure context. The app still works, but cannot be installed with a service worker and cannot receive push;
  - **Option A — mkcert:**
    ```
    mkcert -install
    mkcert -key-file certs/dev-key.pem -cert-file certs/dev-cert.pem <lan-ip> localhost
    uvicorn src.api.app:app --host 0.0.0.0 --ssl-keyfile certs/dev-key.pem --ssl-certfile certs/dev-cert.pem
    ```
    Then install mkcert's `rootCA.pem` on the phone (Android: Settings → Security → Install certificate; iOS: install the profile, then enable it under Certificate Trust Settings). The files are `.pem`, not `.crt`, on purpose: the Dockerfile trusts every `certs/*.crt` as a CA (`Dockerfile:5-8, 25-26`). `/certs/*` is already git-ignored (`.gitignore:62-64`);
  - **Option B:** a tunnel that terminates TLS with a public certificate (`cloudflared tunnel --url http://localhost:8000`, or ngrok);
  - the session cookie stays `secure=False` (`src/api/routes/auth.py:40`), which works over both http and https;
  - **iOS:** push works only after "Add to Home Screen" (iOS/iPadOS 16.4+), and permission must be requested from a tap. Both hold by design (decision 18);
  - print QR labels from the HTTPS origin phones will use (decision 16).

## Test plan

Test first: each item below is written failing, then implemented.

### Backend (pytest)

**`tests/storage/test_migrations.py`:**
1. New `_v6_db()`: steps 1–6 applied and stamped. Then `DROP TABLE IF EXISTS push_subscriptions` and `ALTER TABLE notifications DROP COLUMN channel` (SQLite 3.49.1 supports DROP COLUMN, and the column has no index or CHECK, decision 4).
   - `_v1_db()`, `_v3_db()`, `_v4_db()` and `_v5_db()` also drop `push_subscriptions`, because each vN fixture drops the tables of later steps.
   - The version asserts already use `LATEST` (`:325-337, 438-455, 571-587, 759-774`), so none needs editing.
2. `test_v6_db_upgrades_to_v7_preserving_data`: seed a machine, alert, user, two notifications (one with `device_incident_id`), a work order and feedback, then:
   - `run_migrations == LATEST`;
   - `push_subscriptions` exists;
   - both notification rows read `channel = 'email'` and every other row is intact;
   - the versions are `1..LATEST`;
   - a re-run is a no-op (`COUNT(*) == LATEST`).
3. `test_fresh_db_push_subscriptions_constraints`: duplicate `endpoint` → `IntegrityError`; `is_active = 2` → `IntegrityError`; NULL `p256dh` → `IntegrityError`; `notifications.channel` defaults to `'email'`.
4. `test_ensure_current_schema_upgrades_a_v6_file` (the shape of `:800-805`).

**`tests/notifications/test_push.py`** (new package with `__init__.py`; `webpush` monkeypatched via `push._webpush`):
5. `settings_from_env` is `None` with no keys; `None` with only one key; `None` with a malformed public key (wrong length), and the log names `VAPID_PUBLIC_KEY` but not the value; valid with keys from `generate_vapid_keys()`.
6. `VAPID_SUBJECT` defaults to `mailto:` + `SMTP_FROM`; an invalid subject (`foo`) → disabled and logged.
7. `generate_vapid_keys`: the public key decodes to 65 bytes starting `0x04` and the private key to 32 bytes. Signing with the private key verifies with the public key (`cryptography`). `main(["--generate-vapid"])` prints `VAPID_PUBLIC_KEY=`, `VAPID_PRIVATE_KEY=` and `VAPID_SUBJECT=` lines and returns 0. `main([])` returns 2.
8. `endpoint_allowed`:
   - `https://fcm.googleapis.com/x` ✓; `https://web.push.apple.com/x` ✓; `https://wns2-par02p.notify.windows.com/w/?token=` ✓;
   - `http://fcm.googleapis.com/x` ✗; `https://evilfcm.googleapis.com.attacker.io/x` ✗; `https://127.0.0.1/x` ✗; `https://user@fcm.googleapis.com/x` ✗; `https://fcm.googleapis.com:8443/x` ✗;
   - `PUSH_ALLOWED_HOSTS=push.example.org` replaces the defaults.
9. `send_push` → `'sent'` and the fake `webpush` receives `subscription_info={endpoint, keys{p256dh, auth}}`, a JSON payload, `vapid_private_key`, `vapid_claims={"sub": subject}`, `ttl=43200`, `timeout=5`, and `Urgency: high` for a high-severity alert.
10. `send_push`: a `WebPushException` whose response status is 410 → `'expired'`; 404 → `'expired'`; 500 → `'failed'`; `requests.ConnectionError` → `'failed'`; `ValueError` → `'failed'`. It never raises.
11. `send_push` refuses a disallowed host (a row inserted by hand) → `'failed'`, and the sender is never called.
12. `alert_payload`: the title equals `dispatch._compose(alert, n)[0]` for levels 0 and 1; url `/m/alerts/<id>`; tag `alert-<id>`; JSON size < 1024 bytes; no email address in it. `device_payload`: the url is `/m/machines/m1` when assigned and `/devices` when not.
13. `push_to_roles`:
    - sends only to active subscriptions of active users in the roles (fixture: one sub per demo role, plus an inactive sub and a sub of a deactivated user);
    - writes one `notifications` row per attempt with `channel = 'push'`, `recipient_email` = the user's email, `recipient_role`, `alert_id`, and status `sent` / `failed`;
    - stamps `last_used_at` on success;
    - on 410, sets `is_active = 0`, `deactivated_at`, and status `failed`;
    - returns the counts.
14. `push_to_roles` with push disabled → zero counts, no rows, and the sender is never called.
15. `push_to_roles` on a v6 connection (no table) → zeros, no exception.
16. `push_to_roles` called inside `asyncio.run(...)` from a coroutine → zeros, an error is logged, and the sender is never called.

**`tests/test_notifications.py`** (existing; push disabled by default, so every current test stays green unchanged):
17. With keys set and a fake sender: level-0 `notify_alert` emails admin and supervisor only, pushes to admin, supervisor and operator subscriptions, and returns the email count only.
18. `notify_alert(..., roles=("supervisor",), page_level=1)` pushes supervisors only, with a title starting `[Unacknowledged — page 1]`. Level 2 with `("admin",)` pushes admins only.
19. `notify_device_incident` pushes all three roles with url `/m/machines/<machine>`.
20. A push failure (the sender raises inside `push_to_roles`, simulated bug) leaves the email rows committed and the return value unchanged.
21. Email rows written after migration 7 read `channel = 'email'`.

**`tests/alerts/test_paging.py`:**
22. A ladder tick reaching level 1 pushes the supervisor subscription only, never the operator. A test with real `dispatch.notify_alert` and a fake push sender.

**`tests/prediction/` (pipeline):**
23. `fan_out` for `alert_resolved` makes no push call. For `alert_created` it pushes the operator.

**`tests/api/test_push_route.py`** (new; keys set via monkeypatch, sender faked):
24. `GET /api/push/vapid-public-key`: anonymous → 401; each of the three roles → 200 with `enabled: true` and the key; keys unset → `{enabled: false, public_key: null}`; `Cache-Control: no-store`.
25. `POST /api/push/subscribe`: operator → 200, one row bound to the operator. The same endpoint again with new keys → still one row, keys updated, `updated_at` advanced.
26. A rebind: the same endpoint posted by the supervisor → one row, `user_id` = supervisor.
27. A row deactivated by a 410 → re-subscribe sets `is_active = 1` and `deactivated_at = NULL`.
28. Validation: an `http://` endpoint → 400; a disallowed host → 400; `p256dh` of the wrong length → 400; missing `keys` → 422; push disabled → 503.
29. `DELETE /api/push/subscribe` removes the caller's row → 204. Deleting an unknown endpoint → 204. Deleting another user's endpoint → 204 and that row is untouched.
30. `POST /api/push/test`: disabled → 503; no subscription → 409; with two subscriptions → 200 `{sent: 2, failed: 0, expired: 0}`, two `channel='push'` rows with NULL alert and incident ids, and another user's subscription is not pushed.
31. `user_agent` is stored truncated to 255 characters.

**`tests/test_notifications_route.py`** (existing):
32. Rows carry `channel`. `?channel=push` filters; `?channel=sms` → 400. On a DB whose `notifications` lacks the column, rows report `'email'`.

**`tests/api/test_spa_files.py`** (new; `spa_file_response` against a `tmp_path` dist with `index.html`, `sw.js`, `manifest.webmanifest` and `icons/icon-192.png`):
33. `sw.js` → `text/javascript`, `Cache-Control: no-cache`.
34. `manifest.webmanifest` → `application/manifest+json`, `no-cache`.
35. `icons/icon-192.png` → `image/png`.
36. `m/alerts/42` → the `index.html` body, `no-cache`.
37. `../outside.txt` (a file created next to the dist dir) → `index.html`, never the outside file.
38. `api/nope` → 404 JSON.

**`tests/docs/test_data_model_doc.py`:** no edit. It fails until `push_subscriptions` is documented.

**`tests/conftest.py`:** the `VAPID_` and `PUSH_` prefixes are added to the scrub.

### Frontend (vitest + RTL + MSW)

**`src/push/usePush.test.tsx`:**
39. No `serviceWorker` → `unsupported`.
40. `isSecureContext = false` → `insecure`.
41. No registration → `unavailable`.
42. Server `enabled: false` → `server-disabled`.
43. `Notification.permission = 'denied'` → `denied`.
44. `enable()` requests permission, subscribes with the decoded `applicationServerKey` and `userVisibleOnly: true`, POSTs `toJSON()`, and ends `on`.
45. `enable()` when the POST fails (500) → the browser subscription is unsubscribed, the state is `off`, and the error is set.
46. `disable()` → the DELETE has the endpoint, `unsubscribe` is called, the state is `off`. A DELETE failure still unsubscribes and sets the error.
47. On mount with an existing subscription → re-POSTs it (refresh) and the state is `on`. With a mismatched `applicationServerKey` → unsubscribe, then subscribe again.
48. `sendTest()` calls `POST /push/test` and returns its counts.

**`src/test/sw.test.ts`** (loads `public/sw.js` source into a fake worker global with a captured `addEventListener`, `caches` and `clients`):
49. The fetch handler does not call `respondWith` for `POST`, `/api/alerts`, `/api/ws`, `/ws` or a cross-origin font URL.
50. A navigation with a network failure responds with the cached `/index.html`. `/assets/x.js` is served from cache when present and cached on miss (only `ok` + `basic` responses).
51. `install` precaches the shell list. `activate` deletes `miq-old-v0` and keeps the current caches.
52. `push` with a payload → `showNotification(title, {body, tag, renotify: true, data: {url}})`. Without data → the fallback title.
53. `notificationclick` with `data.url = '/m/alerts/42'` and no open window → `openWindow('/m/alerts/42')`. With an open client → `focus()` + `navigate()`. `https://evil.example/x` or `//evil` → `/m/alerts`.

**`src/lib/machineQr.test.ts`:**
54. `machineDeepLink('https://h', 'Bearing 1/1')` → `https://h/m/machines/Bearing%201%2F1`, and it round-trips through `parseMachineQr`.
55. Rejects another origin, `/machines/m1`, `/m/machines/`, `/m/machines/m1/extra`, `javascript:alert(1)` and plain text. Accepts a trailing slash.

**`src/lib/returnPath.test.ts` + `src/pages/LoginPage.test.tsx`:**
56. After login, `from = {pathname: '/m/alerts/3', search: '?x=1', hash: '#why'}` → lands on `/m/alerts/3?x=1#why`. `from.pathname = '//evil.com'` → `/dashboard`. No state → `/dashboard`.

**`src/App.test.tsx`:**
57. Signed in, `/m` → redirects to `/m/alerts` and the mobile tab bar renders (no desktop sidebar).
58. Signed out, `/m/machines/m1` → login → submit → `/m/machines/m1` renders the machine.

**`src/layout/MobileShell.test.tsx`:**
59. Four tabs labelled Alerts, Scan, Machines and Settings. The active tab has `aria-current="page"`. Each tab link has the `min-h-14` class. The live indicator reflects `connected`.

**`src/pages/mobile/MobileAlertsPage.test.tsx`:**
60. Lists the open alerts (fixture `openAlerts`), with machine id, relative time and chips.
61. **Acknowledge is optimistic:** the MSW handler is delayed with a deferred promise; the card shows "Acknowledged" before resolving, and the request was sent. **Rollback:** a 500 handler → the button returns and an error toast is shown.
62. **Work order:** the card shows "Creating work order…", then "WO #<id>" linking to `/work-orders/<id>`. A 409 → rollback, re-fetch and an info toast.
63. **Close** opens `AlertCloseDialog` (`role="dialog"` named by its heading). Saving with outcome `false_alarm` removes the card from the Open list.
64. **Why?** navigates to `/m/alerts/<id>`.
65. A live `alert_created` event (via `MockWebSocket`) triggers a re-fetch while a pending ack is kept.
66. Every action button has an accessible name including the alert id, e.g. "Acknowledge alert #2".

**`src/pages/mobile/MobileAlertDetailPage.test.tsx`:**
67. Renders the alert summary and the explanation sections from `alertExplanation`. The action bar acknowledges. 404 → "Alert #999 not found".

**`src/pages/mobile/MobileMachineDetailPage.test.tsx`:**
68. Renders health, RUL and the open alert cards for `m1`. An unknown id (404) → "Unknown machine" with "Scan again".

**`src/pages/mobile/MobileScanPage.test.tsx`:**
69. Without `BarcodeDetector`: the manual field and the camera-app instruction show; submitting `m1` navigates to `/m/machines/m1`.
70. With stubbed `BarcodeDetector` (`getSupportedFormats` → `['qr_code']`, `detect` → `[{rawValue: location.origin + '/m/machines/m2'}]`) and `getUserMedia` → navigates to `/m/machines/m2`, and the track's `stop()` was called.
71. A detected foreign URL → the rejection message, no navigation, scanning continues.
72. `getUserMedia` rejects (`NotAllowedError`) → falls back to manual with a "Camera permission was denied" note.

**`src/pages/mobile/MobileSettingsPage.test.tsx`:**
73. Shows the user. The push toggle turns on (as in 44). "Send test" calls the endpoint and toasts "Test notification sent to 1 device". `server-disabled` shows the explanatory text and a disabled switch.

**`src/components/MachineQrLabelDialog.test.tsx`:**
74. Renders an `img` named "QR code for machine m1" whose `src` starts `data:image/svg+xml`, plus the caption URL `http://localhost:3000/m/machines/m1` (jsdom origin).
75. Shows the localhost warning on a localhost origin. **Print** calls `window.print` (spy).

**`src/pages/MachineDetailPage.test.tsx`:**
76. The "QR label" button opens the dialog.

**`src/layout/AppShell.test.tsx`:**
77. The "Open navigation" button toggles a drawer containing the nav links. Escape closes it. Navigating closes it.
78. The "Mobile view" link points to `/m`.
79. With `matchMedia('(max-width: 640px)')` matching, the suggestion banner shows; "Dismiss" hides it and sets `localStorage`; a remount stays hidden. On a wide viewport it never shows.

**`src/realtime/LiveEventsProvider.test.tsx`:**
80. A socket that closes before `onopen` triggers `GET /api/auth/me`. With a 401 there → the user is cleared and no further socket is constructed. A socket that opened and then closed reconnects without the probe (existing behaviour).

**`src/auth/auth.test.tsx`:**
81. `logout()` sends `DELETE /api/push/subscribe` with this device's endpoint, then `POST /auth/logout`. If the DELETE fails or hangs past 2 s (fake timers), logout still completes.

**`src/pages/NotificationsPage.test.tsx`:**
82. The Channel column shows "Email" and "Push". The channel filter adds `?channel=push`.

**`src/components/AlertExplanationPanel.test.tsx`:**
83. Unchanged, and still green after the `useAlertExplanation` extraction (a regression guard; no edits).

**Build and lint:** `npm run build` (`tsc -b` covers the new pages and the unchanged exhaustive `describe()` switch) and `npm run lint` are clean. `dist/` then contains `sw.js`, `manifest.webmanifest` and `icons/`.

**Manual check (README):**
- desktop Chrome at `http://localhost:8000/m`: installable, the service worker is active, push toggle on, test push received, tap opens `/m/settings`;
- Android Chrome over the mkcert HTTPS origin: install, scan a printed label, receive a `/demo` simulated fault as an operator, tap through to the alert, acknowledge.

## Docs to update

- **`docs/DATA_MODEL.md`:**
  - a new `### \`push_subscriptions\`` section after `alert_explanations` (`:455-483`): columns, the UNIQUE endpoint with rebind-on-subscribe, hard delete on unsubscribe vs deactivate on 404/410, joined to active users, "Added by migration 7";
  - the `notifications` table (`:261-276`): a `channel` row (`TEXT NOT NULL DEFAULT 'email'`, `'email' | 'push'`, migration 7); the intro becomes "Email and push paging records…"; a note that push rows take `recipient_email` from `users.email`;
  - the ER diagram (`:14-…`): `users ||--o{ push_subscriptions : "receives push on"`;
  - Storage layout (`:578-…`): a migration 7 sentence ("additive: `push_subscriptions` plus a guarded `notifications.channel` ALTER, the shape of migration 3").
- **`README.md`:**
  - env table (`:47-66`): the four new rows;
  - a new `### Mobile & push (PWA)` section after "Frontend development (hot reload)" (`:100-109`), covering:
    - install;
    - the `/m` routes;
    - the push opt-in;
    - who gets pushed (the decision-2 table);
    - the VAPID generate command;
    - HTTPS options A and B;
    - iOS caveats;
    - printing QR labels from the right origin;
    - "the service worker only exists in `npm run build` output";
  - a sentence in "Work orders and paging" (`:232-267`): ladder pages also go out as push.
- **`.env.example`:** a commented Web Push block after the `ESCALATION_*` lines (`:120-121`).
- **`TODO.md`:**
  - checked items "Mobile operator view (PWA) backend: push subscriptions, VAPID, push channel (design/2026-10-07-mobile-operator-pwa-design.md)" and "Mobile operator view frontend: /m shell, quick actions, QR labels + scan, responsive AppShell" after the "Why this alert?" items;
  - unchecked follow-ups: "Bulk QR label sheet from the Machines page", "Per-user notification preferences (quiet hours, severity floor)", "Offline acknowledgement queue (Background Sync)".
- **`IMPLEMENTATION_PLAN.md`:** `## M11 — Mobile operator view (PWA)` after M10 (`:79-86`), with the quoted requirement and bullets for migration 7 / `push.py` / dispatch audience, routes, PWA files, mobile UI, QR, and the responsive shell.
- **`src/notifications/dispatch.py`, `src/alerts/paging.py`, `src/prediction/pipeline.py`, `src/storage/migrations.py`:** the docstring updates listed in "Services".
- **`design/_integration_map.md`:** not edited. This doc supersedes §6 where they differ:
  - `updated_at` and `deactivated_at` columns;
  - an explicit push audience per level;
  - the SSRF allowlist;
  - `/m` index → `/m/alerts` and the extra `/m/machines` and `/m/settings` routes;
  - the toggle on `/m/settings` plus the AppShell header;
  - the 4401 point corrected: the close happens pre-accept, so clients see 1006 and the fix probes `/auth/me`.
- **`PROJECT_CONTEXT.md`:** not touched.

## Implementation notes (backend, as built)

Clarifications made while implementing; the decisions above otherwise stand.

- **Key encoding.** `p256dh`, `auth` and `VAPID_PUBLIC_KEY` are base64url; trailing `=` padding is tolerated, and any character outside the base64url alphabet is rejected (Python's decoder would otherwise silently drop it). `push.valid_public_key` / `valid_auth_secret` are shared by the settings check and `POST /api/push/subscribe`.
- **`DELETE /api/push/subscribe` works with push disabled.** Removing your own subscription needs no keys, and logout calls it unconditionally (decision 18), so it never answers 503.
- **Device payloads** also carry `device_incident_id`, mirroring `alert_id` on alert payloads.
- **`spa_file_response`** treats both `api` and `api/...` as API paths (JSON 404), and a direct `GET /index.html` gets the same `text/html` + `no-cache` as the fallback.
- **`push_to_roles`** checks the event-loop guard, then settings, then the schema, so a disabled or v6 deployment never touches `push_subscriptions`. Expired and failed attempts both log `status = 'failed'`; only the subscription row distinguishes them (`is_active = 0`).
- **Tests.** `tests/conftest.py` gains a `push_enabled` fixture (generated VAPID keys in env plus a `FakeWebPush` recorder behind `push._webpush`) and an `add_sub` helper; the `VAPID_` / `PUSH_` scrub keeps push off everywhere else.

## Implementation notes (frontend, as built)

Clarifications made while implementing; the decisions above otherwise stand.

- **Quick actions keep server rows and optimistic edits apart.** `useQuickAlertActions` holds the fetched list and a per-alert overlay map; `alerts` lays the overlay over the list. A live re-fetch therefore lands under a pending edit, and rollback is simply dropping the overlay. A 409 on "Work order" toasts `Alert #n already has a work order` and re-fetches through a reload counter in the page.
- **Accessible names carry the alert id:** `Acknowledge alert #n`, `Create work order for alert #n`, `Close alert #n` (or `Record outcome for alert #n` / `Edit outcome for alert #n` on resolved alerts, disabled when `canEditFeedback` says no), `Why alert #n?`. Acknowledge and Work order disappear once done; the chips ("Acknowledged", "Creating work order…", "WO #n") take their place.
- **`/m/alerts/:id`** keeps the alert from the explanation response as the quick actions' server row; its "Why?" scrolls to `#why`. `useAlertExplanation` also reports `notFound`, so the page shows "Alert #n not found" with a link back.
- **`usePush`** checks, in order: API support, secure context, a registration, the server config, then permission. If the mount-time refresh fails, the error is shown and the state is `off` (turning it on re-posts whatever the browser holds). `enable()` with permission refused ends `denied` (blocked) or `off` (dismissed).
- **The push switch** is `role="switch"` with a 44 px hit area around a 28 px track. "Send test" toasts `Test notification sent to N device(s)`, or an error naming the failed count.
- **Desktop "This device" menu** (lucide `BellRing`, `aria-label="This device"`) mounts `PushToggle` only when opened, so the console makes no push calls until asked. It closes on Escape or an outside tap. The drawer's nav is labelled "Main menu", so the sidebar stays the only "Main" navigation.
- **`MobileViewSuggestion`** reads `matchMedia('(max-width: 640px)')` live, and tolerates blocked `localStorage` (shown again next visit).
- **Expired-session probe:** a 401 from `/auth/me` stops reconnecting outright; any other failure (offline, 5xx) keeps the normal backoff.
- **Notifications page** header reads "Delivery log — N entries" (it now covers both channels), with a Channel filter before Status.
- **Tests:** `src/test/pwaStubs.ts` defines every fake as an own, configurable property and restores jsdom's originals after each test. `src/test/sw.test.ts` loads `public/sw.js` through Vite's `?raw`, so the type check needs no Node types. The `notifications` fixture gained a third, `channel: 'push'` row, and the default MSW handler honours `?status=` and `?channel=`.
- **Icons:** `scripts/generate_pwa_icons.py` centres Pillow's arc strokes on the radius (Pillow strokes inside the bounding box), to match lucide's geometry. `favicon.svg` is the same mark as SVG.

## Risks

- **Push latency on the paging path.** Sends are synchronous on the calling worker thread: the MQTT ingest worker, the replay thread, the paging/watchdog `to_thread` and the demo threadpool. A slow push service delays the next MQTT message by up to `5 s × subscriptions` for that page.
  - Accepted for now: pages happen once per alert episode or escalation, not per reading, and SMTP already has the same shape.
  - If fleets grow, move both channels to a bounded outbox table drained by a scheduler job. That is a contained change inside `dispatch`.
- **SSRF.** Every signed-in role controls an endpoint URL the server will POST to. The host allowlist plus https-only plus no-port/no-userinfo (decision 6) bounds this to public push services. `PUSH_ALLOWED_HOSTS` is an admin-level env var, not an API.
- **Endpoint as a secret.** Anyone holding an endpoint and keys could push to that device. Endpoints are never returned to other users, never put in payloads, and logged only truncated.
- **Shared and handed-over phones.** The endpoint rebinds to whoever subscribes last, and explicit logout unsubscribes. A phone whose session simply expired keeps receiving the previous user's pages until someone else signs in on it. That is intended (operators keep getting paged), and documented.
- **iOS limitations.** Push works only for home-screen-installed apps on iOS 16.4+, and Apple's service is strict about `VAPID_SUBJECT`. iOS has no `BarcodeDetector`, so scanning there is the camera-app path. Both are documented, and the UI states are explicit (`unsupported`, `insecure`).
- **Stale service-worker shell.** Network-first navigations, a `no-cache` `sw.js` and `index.html`, versioned cache names and `skipWaiting` + `clients.claim` mean a deploy is picked up on the next online load. The remaining risk is a tab open across a deploy that requests an asset hash the server no longer has. The 60-entry asset cache usually still holds it, otherwise the next navigation fixes it.
- **The service worker must never cache auth or API data.** The early return for `/api/` and non-GET is the single guard, and test 49 pins it. The login response (`Cache-Control: no-store`, `auth.py:44`) is not cacheable anyway.
- **Origin baked into QR labels.** A label printed from the wrong origin (localhost, an old IP) is useless on the floor. The dialog warns on localhost, and README says to print from the phone-facing origin. Labels need reprinting if the hostname changes; a stable hostname is recommended.
- **Windows MIME table.** Without the explicit `media_type` the worker would fail to register on machines whose registry maps `.js` to `text/plain`. Test 33 pins it.
- **Permission fatigue.** Permission is only requested from the toggle, never on page load. A denied permission can only be undone in browser settings, and the UI says so.
- **Dependency weight.** pywebpush brings `requests`, `http-ece` and `py-vapid`. `qrcode` is about 30 KB gzipped and loaded on demand. Neither touches the ML stack.
- **Migration on long-running non-request connections.** Replay and ingest connections opened before an upgrade may still see v6. The push schema guard makes them skip push silently until their next connection, and email is unaffected.

import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from 'sonner'
import { AuthProvider } from './auth/AuthContext'
import { RequireAuth } from './auth/RequireAuth'
import { RequireRole } from './auth/RequireRole'
import { LiveEventsProvider } from './realtime/LiveEventsProvider'
import { AppShell } from './layout/AppShell'
import { MobileShell } from './layout/MobileShell'
import { LoginPage } from './pages/LoginPage'
import { DashboardPage } from './pages/DashboardPage'
import { MachinesPage } from './pages/MachinesPage'
import { MachineDetailPage } from './pages/MachineDetailPage'
import { AlertsPage } from './pages/AlertsPage'
import { DevicesPage } from './pages/DevicesPage'
import { AnalyticsPage } from './pages/AnalyticsPage'
import { ModelPage } from './pages/ModelPage'
import { IngestionPage } from './pages/IngestionPage'
import { ReportsPage } from './pages/ReportsPage'
import { MaintenancePage } from './pages/MaintenancePage'
import { WorkOrdersPage } from './pages/WorkOrdersPage'
import { NotificationsPage } from './pages/NotificationsPage'
import { AdminUsersPage } from './pages/AdminUsersPage'
import { DemoPage } from './pages/DemoPage'
import { LandingPage } from './pages/LandingPage'
import { MobileAlertsPage } from './pages/mobile/MobileAlertsPage'
import { MobileAlertDetailPage } from './pages/mobile/MobileAlertDetailPage'
import { MobileMachinesPage } from './pages/mobile/MobileMachinesPage'
import { MobileMachineDetailPage } from './pages/mobile/MobileMachineDetailPage'
import { MobileScanPage } from './pages/mobile/MobileScanPage'
import { MobileSettingsPage } from './pages/mobile/MobileSettingsPage'
import { useSpotlight } from './lib/useSpotlight'

export default function App() {
  // Installed at the root so one delegated listener serves both the marketing
  // page and the console — every `.hover-glow` surface on every route.
  useSpotlight()

  return (
    <>
      <BrowserRouter>
        <AuthProvider>
          <LiveEventsProvider>
            <Toaster theme="dark" richColors position="top-right" />
            <Routes>
              {/* Public. `/` is the marketing page, so the signed-in console
                  starts at `/dashboard` rather than at the site root. */}
              <Route path="/" element={<LandingPage />} />
              <Route path="/login" element={<LoginPage />} />

              <Route element={<RequireAuth />}>
                {/* The operator view (design/2026-10-07-mobile-operator-pwa-design.md):
                    its own shell beside the console, open to every role. Ids
                    are in the path; push taps and QR labels deep-link here. */}
                <Route path="m" element={<MobileShell />}>
                  <Route index element={<Navigate to="alerts" replace />} />
                  <Route path="alerts" element={<MobileAlertsPage />} />
                  <Route path="alerts/:id" element={<MobileAlertDetailPage />} />
                  <Route path="machines" element={<MobileMachinesPage />} />
                  <Route path="machines/:id" element={<MobileMachineDetailPage />} />
                  <Route path="scan" element={<MobileScanPage />} />
                  <Route path="settings" element={<MobileSettingsPage />} />
                </Route>

                <Route element={<AppShell />}>
                  <Route path="dashboard" element={<DashboardPage />} />
                  <Route path="machines" element={<MachinesPage />} />
                  <Route path="machines/:id" element={<MachineDetailPage />} />
                  {/* The list and /alerts/:id ("Why this alert?" drawer) share
                      one route, like work orders, so the list and its filters
                      stay mounted under the drawer. */}
                  <Route path="alerts/:id?" element={<AlertsPage />} />
                  <Route path="devices" element={<DevicesPage />} />
                  <Route path="analytics" element={<AnalyticsPage />} />
                  <Route path="model" element={<ModelPage />} />
                  <Route path="reports" element={<ReportsPage />} />
                  <Route path="maintenance" element={<MaintenancePage />} />
                  {/* One route for the list and /work-orders/:id (the drawer),
                      so opening an order keeps the page and its filters. The id
                      is in the path: LoginPage restores only the pathname. */}
                  <Route path="work-orders/:id?" element={<WorkOrdersPage />} />

                  <Route element={<RequireRole allow={['admin', 'supervisor']} />}>
                    <Route path="notifications" element={<NotificationsPage />} />
                    <Route path="ingestion" element={<IngestionPage />} />
                  </Route>

                  <Route element={<RequireRole allow={['admin']} />}>
                    <Route path="admin/users" element={<AdminUsersPage />} />
                    <Route path="demo" element={<DemoPage />} />
                  </Route>
                </Route>
              </Route>

              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </LiveEventsProvider>
        </AuthProvider>
      </BrowserRouter>
    </>
  )
}

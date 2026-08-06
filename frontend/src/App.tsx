import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from 'sonner'
import { AuthProvider } from './auth/AuthContext'
import { RequireAuth } from './auth/RequireAuth'
import { RequireRole } from './auth/RequireRole'
import { LiveEventsProvider } from './realtime/LiveEventsProvider'
import { AppShell } from './layout/AppShell'
import { LoginPage } from './pages/LoginPage'
import { DashboardPage } from './pages/DashboardPage'
import { MachinesPage } from './pages/MachinesPage'
import { MachineDetailPage } from './pages/MachineDetailPage'
import { AlertsPage } from './pages/AlertsPage'
import { AnalyticsPage } from './pages/AnalyticsPage'
import { ModelPage } from './pages/ModelPage'
import { IngestionPage } from './pages/IngestionPage'
import { MaintenancePage } from './pages/MaintenancePage'
import { NotificationsPage } from './pages/NotificationsPage'
import { AdminUsersPage } from './pages/AdminUsersPage'
import { DemoPage } from './pages/DemoPage'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <LiveEventsProvider>
          <Toaster theme="dark" richColors position="top-right" />
          <Routes>
            <Route path="/login" element={<LoginPage />} />

            <Route element={<RequireAuth />}>
              <Route element={<AppShell />}>
                <Route index element={<DashboardPage />} />
                <Route path="machines" element={<MachinesPage />} />
                <Route path="machines/:id" element={<MachineDetailPage />} />
                <Route path="alerts" element={<AlertsPage />} />
                <Route path="analytics" element={<AnalyticsPage />} />
                <Route path="model" element={<ModelPage />} />
                <Route path="maintenance" element={<MaintenancePage />} />

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
  )
}

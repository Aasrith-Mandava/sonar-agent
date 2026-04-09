import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, Link, useLocation } from 'react-router-dom';
import { useAuthStore } from './store/auth';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import AuditLogs from './pages/AuditLogs';
import Settings from './pages/Settings';
import Logs from './pages/Logs';
import { cn } from './lib/utils';

function NavLink({ to, children }: { to: string; children: React.ReactNode }) {
  const { pathname } = useLocation();
  const active = pathname === to || (to !== '/' && pathname.startsWith(to));
  return (
    <Link
      to={to}
      className={cn(
        'text-sm transition-colors',
        active ? 'text-foreground font-medium' : 'text-muted-foreground hover:text-foreground',
      )}
    >
      {children}
    </Link>
  );
}

function Layout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuthStore();

  return (
    <div className="min-h-screen bg-background text-foreground">
      <nav className="border-b bg-card px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-6">
          <span className="font-bold text-primary">SonarAgent</span>
          <div className="flex gap-5">
            <NavLink to="/">Dashboard</NavLink>
            <NavLink to="/logs">Logs</NavLink>
            <NavLink to="/audit-logs">Audit Logs</NavLink>
            <NavLink to="/settings">Settings</NavLink>
          </div>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-muted-foreground">{user?.full_name ?? user?.email}</span>
          <button
            onClick={() => logout()}
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            Sign out
          </button>
        </div>
      </nav>
      <main>{children}</main>
    </div>
  );
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore();
  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="animate-pulse text-muted-foreground">Loading…</div>
      </div>
    );
  }
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <Layout>{children}</Layout>;
}

function App() {
  const { checkAuth } = useAuthStore();

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/*"
          element={
            <ProtectedRoute>
              <Routes>
                <Route path="/"           element={<Dashboard />} />
                <Route path="/logs"       element={<Logs />} />
                <Route path="/audit-logs" element={<AuditLogs />} />
                <Route path="/settings"   element={<Settings />} />
                <Route path="*"           element={<Navigate to="/" replace />} />
              </Routes>
            </ProtectedRoute>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}

export default App;

import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Wizard } from './components/Wizard';
import { AppShell } from './components/AppShell';
import { Dashboard } from './components/Dashboard';
import { AdminAccess } from './components/AdminAccess';
import { ChannelList } from './components/steps/ChannelList';
import { UserList } from './components/steps/UserList';
import { DoctorPanel } from './components/steps/DoctorPanel';
import { LogsPanel } from './components/steps/LogsPanel';
import { StatusProvider } from './context/StatusContext';
import { ApiProvider, useApi } from './context/ApiContext';
import { ToastProvider } from './context/ToastContext';
import { useEffect, useState } from 'react';
import { hasConfiguredPlatformCredentials } from './lib/platforms';

// Wrapper to check if setup is needed
const AuthGuard = ({ children }: { children: any }) => {
    const { getConfig } = useApi();
    const location = useLocation();
    const [loading, setLoading] = useState(true);
    const [needsSetup, setNeedsSetup] = useState(false);
    const bypassSetupGuard = location.pathname === '/doctor/logs';

    useEffect(() => {
        if (bypassSetupGuard) {
            setLoading(false);
            setNeedsSetup(false);
            return;
        }

        let cancelled = false;
        setLoading(true);

        getConfig().then(config => {
            if (cancelled) return;
            const setupState = config?.setup_state;
            const setupReady = typeof setupState?.needs_setup === 'boolean'
                ? setupState.needs_setup === false
                : hasConfiguredPlatformCredentials(config);
            setNeedsSetup(!config || !config.mode || !setupReady);
            setLoading(false);
        }).catch(() => {
             if (cancelled) return;
             // If fetch fails (e.g. config doesn't exist), setup is needed
             setNeedsSetup(true);
             setLoading(false);
        });

        return () => {
            cancelled = true;
        };
    }, [bypassSetupGuard]);

    if (loading) return <div className="min-h-screen flex items-center justify-center bg-bg text-text">Loading...</div>;
    if (needsSetup && !bypassSetupGuard) return <Navigate to="/setup" replace />;
    return children;
};

function AppRoutes() {
  return (
    <Routes>
      <Route path="/setup" element={<Wizard />} />
      <Route element={<AuthGuard><AppShell /></AuthGuard>}>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/admin-access" element={<AdminAccess />} />
        <Route path="/channels" element={<ChannelList isPage />} />
        <Route path="/users" element={<UserList />} />
        <Route path="/doctor" element={<DoctorPanel isPage />} />
        <Route path="/doctor/logs" element={<LogsPanel />} />
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  );
}

function App() {
  return (
    <StatusProvider>
      <ToastProvider>
        <ApiProvider>
          <BrowserRouter>
             <AppRoutes />
          </BrowserRouter>
        </ApiProvider>
      </ToastProvider>
    </StatusProvider>
  );
}

export default App;

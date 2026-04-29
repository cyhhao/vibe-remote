import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Wizard } from './components/Wizard';
import { AppShell } from './components/AppShell';
import { Dashboard } from './components/Dashboard';
import { ChannelList } from './components/steps/ChannelList';
import { UserList } from './components/steps/UserList';
import { SettingsDiagnosticsPage } from './components/settings/SettingsDiagnosticsPage';
import { SettingsBackendsPage } from './components/settings/SettingsBackendsPage';
import { SettingsLogsPage } from './components/settings/SettingsLogsPage';
import { SettingsMessagingPage } from './components/settings/SettingsMessagingPage';
import { SettingsPlatformsPage } from './components/settings/SettingsPlatformsPage';
import { SettingsServicePage } from './components/settings/SettingsServicePage';
import { StatusProvider } from './context/StatusContext';
import { ApiProvider, useApi } from './context/ApiContext';
import { ToastProvider } from './context/ToastContext';
import { ThemeProvider } from './context/ThemeContext';
import { AgentationToggle } from './components/AgentationToggle';
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
        <Route path="/groups" element={<ChannelList isPage />} />
        <Route path="/channels" element={<Navigate to="/groups" replace />} />
        <Route path="/users" element={<UserList />} />
        <Route path="/logs" element={<SettingsLogsPage standalone />} />
        <Route path="/settings" element={<Navigate to="/settings/service" replace />} />
        <Route path="/settings/service" element={<SettingsServicePage />} />
        <Route path="/settings/platforms" element={<SettingsPlatformsPage />} />
        <Route path="/settings/backends" element={<SettingsBackendsPage />} />
        <Route path="/settings/messaging" element={<SettingsMessagingPage />} />
        <Route path="/settings/diagnostics" element={<SettingsDiagnosticsPage />} />
        <Route path="/settings/logs" element={<SettingsLogsPage />} />
        <Route path="/remote-access" element={<Navigate to="/settings/service" replace />} />
        <Route path="/doctor" element={<Navigate to="/settings/diagnostics" replace />} />
        <Route path="/doctor/logs" element={<Navigate to="/logs" replace />} />
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  );
}

function App() {
  return (
    <ThemeProvider>
      <StatusProvider>
        <ToastProvider>
          <ApiProvider>
            <BrowserRouter>
               <AppRoutes />
            </BrowserRouter>
            <AgentationToggle />
          </ApiProvider>
        </ToastProvider>
      </StatusProvider>
    </ThemeProvider>
  );
}

export default App;

import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Wizard } from './components/Wizard';
import { AppShell } from './components/AppShell';
import { Dashboard } from './components/Dashboard';
import { ChannelList } from './components/steps/ChannelList';
import { DoctorPanel } from './components/steps/DoctorPanel';
import { LogsPanel } from './components/steps/LogsPanel';
import { StatusProvider } from './context/StatusContext';
import { ApiProvider, useApi } from './context/ApiContext';
import { ToastProvider } from './context/ToastContext';
import { useEffect, useState } from 'react';

// Wrapper to check if setup is needed
const AuthGuard = ({ children }: { children: any }) => {
    const { getConfig } = useApi();
    const [loading, setLoading] = useState(true);
    const [needsSetup, setNeedsSetup] = useState(false);

    useEffect(() => {
        getConfig().then(config => {
            // Check if minimal config exists
            // For now, if no mode is set, we assume setup is needed
            if (!config || !config.mode) {
                setNeedsSetup(true);
            }
            setLoading(false);
        }).catch(() => {
             // If fetch fails (e.g. config doesn't exist), setup is needed
             setNeedsSetup(true);
             setLoading(false);
        });
    }, []);

    if (loading) return <div className="min-h-screen flex items-center justify-center bg-bg text-text">Loading...</div>;
    if (needsSetup) return <Navigate to="/setup" replace />;
    return children;
};

function AppRoutes() {
  return (
    <Routes>
      <Route path="/setup" element={<Wizard />} />
      <Route element={<AuthGuard><AppShell /></AuthGuard>}>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/channels" element={<ChannelList isPage />} />
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
      <ApiProvider>
        <ToastProvider>
          <BrowserRouter>
             <AppRoutes />
          </BrowserRouter>
        </ToastProvider>
      </ApiProvider>
    </StatusProvider>
  );
}

export default App;

import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Wizard } from './components/Wizard';
import { AppShell } from './components/AppShell';
import { Dashboard } from './components/Dashboard';
import { ChannelList } from './components/steps/ChannelList';
import { UserList } from './components/steps/UserList';
import { SettingsDiagnosticsPage } from './components/settings/SettingsDiagnosticsPage';
import { SettingsBackendsPage } from './components/settings/SettingsBackendsPage';
import { SettingsClaudeProviderPage } from './components/settings/SettingsClaudeProviderPage';
import { SettingsCodexProviderPage } from './components/settings/SettingsCodexProviderPage';
import { SettingsOpencodeProviderPage } from './components/settings/SettingsOpencodeProviderPage';
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
import type { ReactNode } from 'react';
import { hasConfiguredPlatformCredentials } from './lib/platforms';

const LOGIN_CHECK_PATHS = new Set(['/doctor/logs', '/logs']);

const RemoteLoginRedirect = ({ target }: { target: string }) => {
    useEffect(() => {
        window.location.assign(target);
    }, [target]);

    return <div className="min-h-screen flex items-center justify-center bg-bg text-text">Loading...</div>;
};

type GuardStatus = 'loading' | 'ready' | 'needs-setup' | 'remote-login-required';

// Wrapper to check if setup is needed
const AuthGuard = ({ children }: { children: ReactNode }) => {
    const { getConfig, getSession } = useApi();
    const location = useLocation();
    const guardTarget = location.pathname + location.search;
    const [guardState, setGuardState] = useState<{ target: string; status: GuardStatus }>({
        target: '',
        status: 'loading',
    });
    const bypassSetupGuard = LOGIN_CHECK_PATHS.has(location.pathname);

    useEffect(() => {
        let cancelled = false;

        if (bypassSetupGuard) {
            return;
        }

        getSession().then(session => {
            if (cancelled) return;
            if (session.remote && !session.authenticated) {
                setGuardState({ target: guardTarget, status: 'remote-login-required' });
                return null;
            }
            return getConfig().then(config => {
                if (cancelled) return;
                const setupState = config?.setup_state;
                const setupReady = typeof setupState?.needs_setup === 'boolean'
                    ? setupState.needs_setup === false
                    : hasConfiguredPlatformCredentials(config);
                setGuardState({
                    target: guardTarget,
                    status: !config || !config.mode || !setupReady ? 'needs-setup' : 'ready',
                });
            });
        }).catch(async (error) => {
            if (cancelled) return;
            const session = await getSession().catch(() => null);
            if (cancelled) return;
            if (session?.remote && !session.authenticated) {
                setGuardState({ target: guardTarget, status: 'remote-login-required' });
                return;
            }
            console.error('[AuthGuard] setup check failed', error);
            // If fetch fails for local/non-remote use (e.g. config doesn't exist),
            // setup is needed. Remote 401s are handled by the session branch above.
            setGuardState({ target: guardTarget, status: 'needs-setup' });
        });

        return () => {
            cancelled = true;
        };
    }, [bypassSetupGuard, getConfig, getSession, guardTarget]);

    if (bypassSetupGuard) return children;
    if (guardState.target !== guardTarget || guardState.status === 'loading') {
        return <div className="min-h-screen flex items-center justify-center bg-bg text-text">Loading...</div>;
    }
    if (guardState.status === 'remote-login-required') {
        return <RemoteLoginRedirect target={guardTarget} />;
    }
    if (guardState.status === 'needs-setup') {
        if (location.pathname === '/setup') return children;
        return <Navigate to="/setup" replace />;
    }
    return children;
};

function AppRoutes() {
  return (
    <Routes>
      <Route element={<AuthGuard><AppShell /></AuthGuard>}>
        <Route path="/setup" element={<Wizard />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/groups" element={<ChannelList isPage />} />
        <Route path="/channels" element={<Navigate to="/groups" replace />} />
        <Route path="/users" element={<UserList />} />
        <Route path="/logs" element={<SettingsLogsPage standalone />} />
        {/* No client-side route at /settings: Flask owns GET /settings as a
            JSON API, so a hard refresh would hit the API instead of the SPA.
            The Flask handler redirects browser-Accept hits to /settings/service. */}
        <Route path="/settings/service" element={<SettingsServicePage />} />
        <Route path="/settings/platforms" element={<SettingsPlatformsPage />} />
        <Route path="/settings/backends" element={<SettingsBackendsPage />} />
        <Route path="/settings/backends/opencode" element={<SettingsOpencodeProviderPage />} />
        <Route path="/settings/backends/claude" element={<SettingsClaudeProviderPage />} />
        <Route path="/settings/backends/codex" element={<SettingsCodexProviderPage />} />
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

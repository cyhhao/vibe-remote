import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Wizard } from './components/Wizard';
import { AppShell } from './components/AppShell';
import { Workbench } from './components/Workbench';
import { InboxPage } from './components/workbench/InboxPage';
import { AgentsPage } from './components/workbench/AgentsPage';
import { SkillsPage } from './components/workbench/SkillsPage';
import { HarnessPage } from './components/workbench/HarnessPage';
import { VaultsPage } from './components/workbench/VaultsPage';
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

// Paths that bypass the setup guard so the wizard and diagnostics can show
// logs / doctor output even before configuration is complete.
const LOGIN_CHECK_PATHS = new Set(['/admin/logs', '/admin/settings/diagnostics']);

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

        {/* Workbench mode — `/` is the canvas root, the five capability
            entries (Inbox + Agents/Skills/Harness/Vaults) live alongside
            it. Commit 02 ships sidebar + placeholder pages; the real
            module screens land in later commits. */}
        <Route path="/" element={<Workbench />} />
        <Route path="/inbox" element={<InboxPage />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/skills" element={<SkillsPage />} />
        <Route path="/harness" element={<HarnessPage />} />
        <Route path="/vaults" element={<VaultsPage />} />

        {/* Control Panel mode — existing pages moved under /admin/* */}
        <Route path="/admin" element={<Navigate to="/admin/dashboard" replace />} />
        <Route path="/admin/dashboard" element={<Dashboard />} />
        <Route path="/admin/groups" element={<ChannelList isPage />} />
        <Route path="/admin/users" element={<UserList />} />
        <Route path="/admin/logs" element={<SettingsLogsPage standalone />} />
        {/* No client-side route at /admin/settings: Flask owns GET /settings as
            a JSON API. The Flask handler redirects browser-Accept hits to
            /admin/settings/service. */}
        <Route path="/admin/settings/service" element={<SettingsServicePage />} />
        <Route path="/admin/settings/platforms" element={<SettingsPlatformsPage />} />
        <Route path="/admin/settings/backends" element={<SettingsBackendsPage />} />
        <Route path="/admin/settings/backends/opencode" element={<SettingsOpencodeProviderPage />} />
        <Route path="/admin/settings/backends/claude" element={<SettingsClaudeProviderPage />} />
        <Route path="/admin/settings/backends/codex" element={<SettingsCodexProviderPage />} />
        <Route path="/admin/settings/messaging" element={<SettingsMessagingPage />} />
        <Route path="/admin/settings/diagnostics" element={<SettingsDiagnosticsPage />} />
        <Route path="/admin/settings/logs" element={<SettingsLogsPage />} />

        {/* Legacy redirects: old top-level paths → /admin/* equivalents.
            Bookmarked URLs and external links keep working without a server
            round-trip. */}
        <Route path="/dashboard" element={<Navigate to="/admin/dashboard" replace />} />
        <Route path="/groups" element={<Navigate to="/admin/groups" replace />} />
        <Route path="/channels" element={<Navigate to="/admin/groups" replace />} />
        <Route path="/users" element={<Navigate to="/admin/users" replace />} />
        <Route path="/logs" element={<Navigate to="/admin/logs" replace />} />
        <Route path="/settings/service" element={<Navigate to="/admin/settings/service" replace />} />
        <Route path="/settings/platforms" element={<Navigate to="/admin/settings/platforms" replace />} />
        <Route path="/settings/backends" element={<Navigate to="/admin/settings/backends" replace />} />
        <Route path="/settings/backends/opencode" element={<Navigate to="/admin/settings/backends/opencode" replace />} />
        <Route path="/settings/backends/claude" element={<Navigate to="/admin/settings/backends/claude" replace />} />
        <Route path="/settings/backends/codex" element={<Navigate to="/admin/settings/backends/codex" replace />} />
        <Route path="/settings/messaging" element={<Navigate to="/admin/settings/messaging" replace />} />
        <Route path="/settings/diagnostics" element={<Navigate to="/admin/settings/diagnostics" replace />} />
        <Route path="/settings/logs" element={<Navigate to="/admin/settings/logs" replace />} />
        <Route path="/remote-access" element={<Navigate to="/admin/settings/service" replace />} />
        <Route path="/doctor" element={<Navigate to="/admin/settings/diagnostics" replace />} />
        <Route path="/doctor/logs" element={<Navigate to="/admin/logs" replace />} />
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

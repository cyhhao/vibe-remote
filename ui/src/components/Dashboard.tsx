import React from 'react';
import { useTranslation } from 'react-i18next';
import { useStatus } from '../context/StatusContext';
import { Play, Square, RotateCw, Activity, Terminal, CheckCircle, MessageSquare, Server, Settings, Info } from 'lucide-react';
import { Link } from 'react-router-dom';

export const Dashboard: React.FC = () => {
    const { t } = useTranslation();
    const { status, control } = useStatus();
    const [loading, setLoading] = React.useState(false);
    const [config, setConfig] = React.useState<any>({});
    const [doctor, setDoctor] = React.useState<any>(null);
    const [settingsMessage, setSettingsMessage] = React.useState<string | null>(null);
    const [uiSaving, setUiSaving] = React.useState(false);
    const [uiMessage, setUiMessage] = React.useState<string | null>(null);
    const [diagnosticsSaving, setDiagnosticsSaving] = React.useState(false);
    const [diagnosticsMessage, setDiagnosticsMessage] = React.useState<string | null>(null);

    const handleAction = async (action: string) => {
        setLoading(true);
        try {
            await control(action);
        } finally {
            setLoading(false);
        }
    };

    // Auto-save Message Handling config (no restart needed)
    const autoSaveMessageConfig = async (newConfig: any) => {
        try {
            await fetch('/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(newConfig),
            });
            setSettingsMessage(t('common.saved'));
        } catch {
            setSettingsMessage(t('common.saveFailed'));
        }
    };

    // Save Console Server config and restart UI service
    const handleUiSaveRestart = async () => {
        setUiSaving(true);
        setUiMessage(null);
        try {
            const uiPayload = {
                setup_host: config.ui?.setup_host || '127.0.0.1',
                setup_port: config.ui?.setup_port || 5123,
            };
            const configPayload = {
                ...config,
                ui: { ...(config.ui || {}), ...uiPayload },
            };
            // Save config first
            await fetch('/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(configPayload),
            });
            // Restart UI service
            await fetch('/ui/reload', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ host: uiPayload.setup_host, port: uiPayload.setup_port }),
            });
            setUiMessage(t('dashboard.uiRestartMessage'));
            const newUrl = `http://${uiPayload.setup_host}:${uiPayload.setup_port}`;
            window.setTimeout(() => {
                window.location.href = newUrl;
            }, 2000);
        } catch {
            setUiMessage(t('common.saveFailed'));
        } finally {
            setUiSaving(false);
        }
    };

    // Save Diagnostics config and restart main service
    const handleDiagnosticsSaveRestart = async () => {
        setDiagnosticsSaving(true);
        setDiagnosticsMessage(null);
        try {
            const runtimePayload = {
                log_level: config.runtime?.log_level || 'INFO',
            };
            const configPayload = {
                ...config,
                runtime: { ...(config.runtime || {}), ...runtimePayload },
            };
            await fetch('/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(configPayload),
            });
            await control('restart');
            setDiagnosticsMessage(t('dashboard.mainServiceRestarted'));
        } catch {
            setDiagnosticsMessage(t('common.saveFailed'));
        } finally {
            setDiagnosticsSaving(false);
        }
    };


    React.useEffect(() => {
        const load = async () => {
            try {
                const res = await fetch('/config');
                if (res.ok) {
                    const nextConfig = await res.json();
                    setConfig(nextConfig);
                }
                const doctorRes = await fetch('/doctor', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
                if (doctorRes.ok) {
                    setDoctor(await doctorRes.json());
                }
            } catch {
                // ignore
            }
        };
        load();
    }, []);

    React.useEffect(() => {
        if (!settingsMessage) return;
        const timer = window.setTimeout(() => {
            setSettingsMessage(null);
        }, 4000);
        return () => window.clearTimeout(timer);
    }, [settingsMessage]);

    React.useEffect(() => {
        if (!diagnosticsMessage) return;
        const timer = window.setTimeout(() => {
            setDiagnosticsMessage(null);
        }, 4000);
        return () => window.clearTimeout(timer);
    }, [diagnosticsMessage]);

    const showWorkspaceGateway = config.mode !== 'self_host';

    return (
        <div className="max-w-5xl mx-auto space-y-8">
            <header>
                <h2 className="text-3xl font-display font-bold mb-2 text-text">{t('dashboard.title')}</h2>
                <p className="text-muted">{t('dashboard.subtitle')}</p>
            </header>

            {/* Overview Card */}
            <div className="bg-panel rounded-xl border border-border p-6 shadow-sm">
                <div className="flex items-center justify-between mb-6">
                    <div>
                         <h3 className="text-lg font-semibold flex items-center gap-2">
                            {t('dashboard.serviceStatus')}
                             <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                                 status.state === 'running' ? 'bg-success/10 text-success' : 'bg-muted/10 text-muted'
                             }`}>
                                 {status.state?.toUpperCase() || t('common.unknown').toUpperCase()}
                             </span>
                         </h3>
                         <p className="text-sm text-muted mt-1">PID: {status.service_pid || status.pid || '-'}</p>
                    </div>
                    <div className="flex gap-2">
                        {status.state !== 'running' && (
                             <button
                                onClick={() => handleAction('start')}
                                disabled={loading}
                                className="flex items-center gap-2 px-4 py-2 bg-success text-white rounded-lg hover:bg-success/90 disabled:opacity-50 font-medium shadow-sm transition-colors"
                             >
                                <Play size={16} /> {t('common.start')}
                             </button>
                        )}
                        {status.state === 'running' && (
                            <button
                                onClick={() => handleAction('stop')}
                                disabled={loading}
                                className="flex items-center gap-2 px-4 py-2 bg-danger text-white rounded-lg hover:bg-danger/90 disabled:opacity-50 font-medium shadow-sm transition-colors"
                            >
                                <Square size={16} /> {t('common.stop')}
                            </button>
                        )}
                        <button
                             onClick={() => handleAction('restart')}
                             disabled={loading}
                             className="flex items-center gap-2 px-4 py-2 border border-border bg-white hover:bg-neutral-50 rounded-lg disabled:opacity-50 text-text font-medium transition-colors"
                        >
                            <RotateCw size={16} /> {t('common.restart')}
                        </button>
                    </div>
                </div>

                <div className={`grid grid-cols-1 gap-6 border-t border-border pt-6 ${showWorkspaceGateway ? 'md:grid-cols-3' : 'md:grid-cols-1'}`}>
                    <div>
                        <div className="text-sm text-muted mb-1">{t('dashboard.mode')}</div>
                        <div className="font-mono font-medium text-text capitalize">{config.mode || '-'}</div>
                    </div>
                    {showWorkspaceGateway && (
                        <div>
                            <div className="text-sm text-muted mb-1">{t('dashboard.workspace')}</div>
                            <div className="font-medium text-text">{config.slack?.team_name || '-'}</div>
                            <div className="text-xs text-muted font-mono">{config.slack?.team_id || ''}</div>
                        </div>
                    )}
                    {showWorkspaceGateway && (
                        <div>
                            <div className="text-sm text-muted mb-1">{t('dashboard.gateway')}</div>
                            <div className="flex items-center gap-2">
                                 <div className={`w-2 h-2 rounded-full ${config.gateway?.relay_url ? 'bg-success' : 'bg-danger'}`}></div>
                                 <span className="text-text">{config.gateway?.relay_url ? t('common.online') : t('common.offline')}</span>
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* Quick Actions / Snapshots */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="bg-panel rounded-xl border border-border p-6 shadow-sm">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="font-semibold flex items-center gap-2 text-text"><Activity size={18} /> {t('dashboard.health')}</h3>
                    <Link to="/doctor" className="text-sm text-accent hover:underline font-medium">{t('common.viewDetails')}</Link>
                </div>
                <div className={`flex items-center gap-2 font-medium ${doctor?.ok ? 'text-success' : 'text-warning'}`}>
                    <CheckCircle size={16} /> {doctor?.ok ? t('dashboard.allSystemsOperational') : t('dashboard.issuesDetected')}
                </div>
            </div>

            <div className="bg-panel rounded-xl border border-border p-6 shadow-sm">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="font-semibold flex items-center gap-2 text-text"><Settings size={18} /> {t('dashboard.globalPolicy')}</h3>
                    <Link to="/setup" className="text-sm text-accent hover:underline font-medium">{t('common.editSetup')}</Link>
                </div>
                <div className="space-y-3 text-sm">
                    <div className="flex justify-between items-center">
                        <span className="text-muted">{t('dashboard.mode')}</span>
                        <span className="font-mono text-xs text-text capitalize">{config.mode || '-'}</span>
                    </div>
                    <div className="flex justify-between items-center">
                        <span className="text-muted">{t('dashboard.defaultBackend')}</span>
                        <span className="font-mono text-xs text-text">{config.agents?.default_backend || 'opencode'}</span>
                    </div>
                    <div className="flex justify-between items-center">
                        <span className="text-muted">{t('dashboard.slackWorkspace')}</span>
                        <span className="text-xs text-text">{config.slack?.team_name || t('common.notLinked')}</span>
                    </div>
                    <div className="flex justify-between items-center">
                        <span className="text-muted">{t('dashboard.gateway')}</span>
                        <span className="text-xs text-text">{config.gateway?.relay_url ? t('common.online') : t('common.offline')}</span>
                    </div>
                </div>
            </div>

            <div className="bg-panel rounded-xl border border-border p-6 shadow-sm">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="font-semibold flex items-center gap-2 text-text"><MessageSquare size={18} /> {t('dashboard.messageHandling')}</h3>
                </div>
                <div className="space-y-3 text-sm">
                    <div className="flex justify-between items-center">
                        <span className="text-muted flex items-center gap-1">
                            {t('dashboard.requireMention')}
                            <span className="relative group">
                                <Info size={12} className="text-muted/50 cursor-help" />
                                <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-2 py-1 bg-text text-bg text-xs rounded whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
                                    {t('dashboard.requireMentionHint')}
                                </span>
                            </span>
                        </span>
                            <button
                                onClick={() => {
                                    setSettingsMessage(null);
                                    const newConfig = {
                                        ...config,
                                        slack: {
                                            ...(config.slack || {}),
                                            require_mention: !config.slack?.require_mention,
                                        },
                                    };
                                    setConfig(newConfig);
                                    autoSaveMessageConfig(newConfig);
                                }}
                                className={`px-2 py-1 rounded text-xs font-semibold border ${
                                    config.slack?.require_mention
                                        ? 'bg-success/10 text-success border-success/20'
                                        : 'bg-neutral-100 text-muted border-border'
                                }`}
                            >
                                {config.slack?.require_mention ? t('common.enabled') : t('common.disabled')}
                            </button>

                    </div>
                    <div className="flex justify-between items-center">
                        <span className="text-muted flex items-center gap-1">
                            {t('dashboard.ackMode')}
                            <span className="relative group">
                                <Info size={12} className="text-muted/50 cursor-help" />
                                <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-2 py-1 bg-text text-bg text-xs rounded whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
                                    {t('dashboard.ackModeHint')}
                                </span>
                            </span>
                        </span>
                        <select
                            value={config.ack_mode || 'reaction'}
                            onChange={(e) => {
                                const mode = e.target.value || 'reaction';
                                setSettingsMessage(null);
                                const newConfig = {
                                    ...config,
                                    ack_mode: mode,
                                };
                                setConfig(newConfig);
                                autoSaveMessageConfig(newConfig);
                            }}
                            className="w-36 bg-neutral-100 border border-border rounded px-2 py-1 text-xs font-mono"
                        >
                            <option value="reaction">{t('dashboard.ackReaction')}</option>
                            <option value="message">{t('dashboard.ackMessage')}</option>
                        </select>
                    </div>
                    <div className="flex justify-between items-center">
                        <span className="text-muted">{t('dashboard.allowedChannels')}</span>
                        <Link to="/channels" className="text-xs text-accent hover:underline font-medium">{t('common.manageChannels')}</Link>
                    </div>
                </div>
            </div>

            <div className="bg-panel rounded-xl border border-border p-6 shadow-sm">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="font-semibold flex items-center gap-2 text-text"><Server size={18} /> {t('dashboard.consoleServer')}</h3>
                </div>
                <div className="space-y-3 text-sm">
                    <div className="flex justify-between items-center">
                        <span className="text-muted">{t('dashboard.host')}</span>
                        <input
                            type="text"
                            value={config.ui?.setup_host || '127.0.0.1'}
                            onChange={(e) => {
                                const host = e.target.value || '127.0.0.1';
                                setUiMessage(null);
                                setConfig((prev: any) => ({
                                    ...prev,
                                    ui: { ...(prev.ui || {}), setup_host: host },
                                }));
                            }}
                            className="w-40 bg-neutral-100 border border-border rounded px-2 py-1 text-xs font-mono"
                        />
                    </div>
                    <div className="flex justify-between items-center">
                        <span className="text-muted">{t('dashboard.port')}</span>
                        <input
                            type="number"
                            min={1024}
                            max={65535}
                            value={config.ui?.setup_port || 5123}
                            onChange={(e) => {
                                const port = Number(e.target.value) || 5123;
                                setUiMessage(null);
                                setConfig((prev: any) => ({
                                    ...prev,
                                    ui: { ...(prev.ui || {}), setup_port: port },
                                }));
                            }}
                            className="w-24 bg-neutral-100 border border-border rounded px-2 py-1 text-xs font-mono"
                        />
                    </div>
                </div>
                <div className="flex justify-between items-center mt-4 pt-4 border-t border-border">
                    {uiMessage && <span className="text-xs text-muted">{uiMessage}</span>}
                    {!uiMessage && <span />}
                    <button
                        onClick={handleUiSaveRestart}
                        disabled={uiSaving}
                        className="px-3 py-1.5 bg-accent text-white rounded-md text-xs font-semibold disabled:opacity-50 flex items-center gap-1"
                    >
                        <RotateCw size={12} /> {uiSaving ? t('common.saving') : t('common.saveAndRestart')}
                    </button>
                </div>
            </div>

            <div className="bg-panel rounded-xl border border-border p-6 shadow-sm">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="font-semibold flex items-center gap-2 text-text"><Terminal size={18} /> {t('dashboard.diagnostics')}</h3>
                    <Link to="/doctor/logs" className="text-sm text-accent hover:underline font-medium">{t('common.viewLogs')}</Link>
                </div>
                <div className="space-y-3 text-sm">
                    <div className="flex justify-between items-center">
                        <span className="text-muted">{t('dashboard.logLevel')}</span>
                        <select
                            value={config.runtime?.log_level || 'INFO'}
                            onChange={(e) => {
                                const level = e.target.value || 'INFO';
                                setDiagnosticsMessage(null);
                                setConfig((prev: any) => ({
                                    ...prev,
                                    runtime: { ...(prev.runtime || {}), log_level: level },
                                }));
                            }}
                            className="w-28 bg-neutral-100 border border-border rounded px-2 py-1 text-xs font-mono"
                        >
                            {['DEBUG', 'INFO', 'WARNING', 'ERROR'].map((level) => (
                                <option key={level} value={level}>{level}</option>
                            ))}
                        </select>
                    </div>
                    <div className="flex justify-between items-center">
                        <span className="text-muted">{t('dashboard.configFile')}</span>
                        <code className="font-mono text-xs bg-neutral-100 px-2 py-1 rounded text-text">~/.vibe_remote/config/config.json</code>
                    </div>
                    <div className="flex justify-between items-center">
                        <span className="text-muted">{t('dashboard.logs')}</span>
                        <code className="font-mono text-xs bg-neutral-100 px-2 py-1 rounded text-text">~/.vibe_remote/logs/vibe_remote.log</code>
                    </div>
                </div>
                <div className="flex justify-between items-center mt-4 pt-4 border-t border-border">
                    {diagnosticsMessage && <span className="text-xs text-muted">{diagnosticsMessage}</span>}
                    {!diagnosticsMessage && <span />}
                    <button
                        onClick={handleDiagnosticsSaveRestart}
                        disabled={diagnosticsSaving}
                        className="px-3 py-1.5 bg-accent text-white rounded-md text-xs font-semibold disabled:opacity-50 flex items-center gap-1"
                    >
                        <RotateCw size={12} /> {diagnosticsSaving ? t('common.saving') : t('common.saveAndRestart')}
                    </button>
                </div>
            </div>
       </div>



        </div>
    );
};

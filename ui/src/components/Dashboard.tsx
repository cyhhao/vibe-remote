import React from 'react';
import { useStatus } from '../context/StatusContext';
import { Play, Square, RotateCw, Activity, Terminal, CheckCircle } from 'lucide-react';
import { Link } from 'react-router-dom';

export const Dashboard: React.FC = () => {
    const { status, control } = useStatus();
    const [loading, setLoading] = React.useState(false);
    const [config, setConfig] = React.useState<any>({});
    const [doctor, setDoctor] = React.useState<any>(null);

    const handleAction = async (action: string) => {
        setLoading(true);
        try {
            await control(action);
        } finally {
            setLoading(false);
        }
    };

    React.useEffect(() => {
        const load = async () => {
            try {
                const res = await fetch('/config');
                if (res.ok) {
                    setConfig(await res.json());
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

    return (
        <div className="max-w-5xl mx-auto space-y-8">
            <header>
                <h2 className="text-3xl font-display font-bold mb-2 text-text">Dashboard</h2>
                <p className="text-muted">Manage your local Vibe Remote instance.</p>
            </header>

            {/* Overview Card */}
            <div className="bg-panel rounded-xl border border-border p-6 shadow-sm">
                <div className="flex items-center justify-between mb-6">
                    <div>
                         <h3 className="text-lg font-semibold flex items-center gap-2">
                            Service Status
                             <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                                 status.state === 'running' ? 'bg-success/10 text-success' : 'bg-muted/10 text-muted'
                             }`}>
                                 {status.state?.toUpperCase() || 'UNKNOWN'}
                             </span>
                         </h3>
                         <p className="text-sm text-muted mt-1">PID: {status.pid || '-'}</p>
                    </div>
                    <div className="flex gap-2">
                        {status.state !== 'running' && (
                             <button
                                onClick={() => handleAction('start')}
                                disabled={loading}
                                className="flex items-center gap-2 px-4 py-2 bg-success text-white rounded-lg hover:bg-success/90 disabled:opacity-50 font-medium shadow-sm transition-colors"
                             >
                                <Play size={16} /> Start
                             </button>
                        )}
                        {status.state === 'running' && (
                            <button
                                onClick={() => handleAction('stop')}
                                disabled={loading}
                                className="flex items-center gap-2 px-4 py-2 bg-danger text-white rounded-lg hover:bg-danger/90 disabled:opacity-50 font-medium shadow-sm transition-colors"
                            >
                                <Square size={16} /> Stop
                            </button>
                        )}
                        <button
                             onClick={() => handleAction('restart')}
                             disabled={loading}
                             className="flex items-center gap-2 px-4 py-2 border border-border bg-white hover:bg-neutral-50 rounded-lg disabled:opacity-50 text-text font-medium transition-colors"
                        >
                            <RotateCw size={16} /> Restart
                        </button>
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-6 border-t border-border pt-6">
                    <div>
                        <div className="text-sm text-muted mb-1">Mode</div>
                        <div className="font-mono font-medium text-text capitalize">{config.mode || '-'}</div>
                    </div>
                     <div>
                        <div className="text-sm text-muted mb-1">Workspace</div>
                        <div className="font-medium text-text">{config.slack?.team_name || '-'}</div>
                        <div className="text-xs text-muted font-mono">{config.slack?.team_id || ''}</div>
                    </div>
                     <div>
                        <div className="text-sm text-muted mb-1">Gateway</div>
                        <div className="flex items-center gap-2">
                             <div className={`w-2 h-2 rounded-full ${config.gateway?.relay_url ? 'bg-success' : 'bg-danger'}`}></div>
                             <span className="text-text">{config.gateway?.relay_url ? 'Online' : 'Offline'}</span>
                        </div>
                    </div>
                </div>
            </div>

            {/* Quick Actions / Snapshots */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="bg-panel rounded-xl border border-border p-6 shadow-sm">
                    <div className="flex items-center justify-between mb-4">
                        <h3 className="font-semibold flex items-center gap-2 text-text"><Activity size={18} /> Health</h3>
                        <Link to="/doctor" className="text-sm text-accent hover:underline font-medium">View details</Link>
                    </div>
                    <div className={`flex items-center gap-2 font-medium ${doctor?.ok ? 'text-success' : 'text-warning'}`}>
                        <CheckCircle size={16} /> {doctor?.ok ? 'All systems operational' : 'Issues detected'}
                    </div>
                </div>

                 <div className="bg-panel rounded-xl border border-border p-6 shadow-sm">
                     <div className="flex items-center justify-between mb-4">
                         <h3 className="font-semibold flex items-center gap-2 text-text"><Terminal size={18} /> Config</h3>
                         <Link to="/setup" className="text-sm text-accent hover:underline font-medium">Edit setup</Link>
                     </div>
                     <div className="space-y-2 text-sm">
                         <div className="flex justify-between items-center">
                             <span className="text-muted">UI Port</span>
                             <input
                                 type="number"
                                 min={1024}
                                 max={65535}
                                 value={config.ui?.setup_port || 5123}
                                 onChange={(e) => {
                                     const port = Number(e.target.value) || 5123;
                                     setConfig((prev: any) => ({
                                         ...prev,
                                         ui: { ...(prev.ui || {}), setup_port: port },
                                     }));
                                 }}
                                 onBlur={async (e) => {
                                     const port = Number(e.target.value) || 5123;
                                     await fetch('/config', {
                                         method: 'POST',
                                         headers: { 'Content-Type': 'application/json' },
                                         body: JSON.stringify({ ...config, ui: { ...(config.ui || {}), setup_port: port } }),
                                     });
                                 }}
                                 className="w-24 bg-neutral-100 border border-border rounded px-2 py-1 text-xs font-mono"
                             />
                         </div>
                         <div className="flex justify-between items-center">
                             <span className="text-muted">Config File</span>
                             <code className="font-mono text-xs bg-neutral-100 px-2 py-1 rounded text-text">~/.vibe_remote/config/config.json</code>
                         </div>
                         <div className="flex justify-between items-center">
                             <span className="text-muted">Logs</span>
                             <code className="font-mono text-xs bg-neutral-100 px-2 py-1 rounded text-text">~/.vibe_remote/logs/vibe_remote.log</code>
                         </div>
                     </div>
                 </div>
             </div>

        </div>
    );
};

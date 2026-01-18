import React, { useEffect, useState } from 'react';
import { Activity, RefreshCw, CheckCircle, AlertTriangle, XCircle, Copy } from 'lucide-react';
import { useApi } from '../../context/ApiContext';
import clsx from 'clsx';

interface DoctorPanelProps {
  isPage?: boolean;
}

export const DoctorPanel: React.FC<DoctorPanelProps> = ({ isPage }) => {
  const api = useApi();
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<any>(null);

  const runDoctor = async () => {
    setLoading(true);
    try {
      const res = await api.doctor();
      setResults(res);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    runDoctor();
  }, []);

  const StatusIcon = ({ status }: { status: string }) => {
    switch (status) {
      case 'pass': return <CheckCircle size={18} className="text-success" />;
      case 'warn': return <AlertTriangle size={18} className="text-warning" />;
      case 'fail': return <XCircle size={18} className="text-danger" />;
      default: return <div className="w-4 h-4 rounded-full bg-muted" />;
    }
  };

  return (
    <div className={clsx("flex flex-col", isPage ? "max-w-4xl mx-auto h-full" : "w-full")}>
        <div className="flex items-center justify-between mb-6">
             <h2 className={clsx("font-display font-bold flex items-center gap-2", isPage ? "text-3xl" : "text-xl")}>
                <Activity className="text-accent" />
                System Status
             </h2>
             <div className="flex gap-2">
                 {results && (
                     <button
                         onClick={() => navigator.clipboard.writeText(JSON.stringify(results, null, 2))}
                         className="flex items-center gap-2 px-3 py-2 border border-border rounded-lg text-sm text-muted hover:bg-neutral-50 hover:text-text transition-colors"
                     >
                         <Copy size={16} /> Copy Report
                     </button>
                 )}
                 <button
                    onClick={runDoctor}
                    disabled={loading}
                    className="flex items-center gap-2 px-4 py-2 bg-accent text-white rounded-lg hover:bg-accent/90 disabled:opacity-50 transition-colors font-medium shadow-sm"
                 >
                    <RefreshCw size={16} className={loading ? 'animate-spin' : ''} /> Run Checks
                 </button>
             </div>
        </div>

      {results && (
        <div className="space-y-6">
          {results.groups?.map((group: any, i: number) => (
            <div key={i} className="bg-panel border border-border rounded-xl overflow-hidden shadow-sm">
              <div className="px-4 py-3 bg-neutral-50 border-b border-border font-semibold text-sm uppercase text-muted tracking-wide">
                {group.name}
              </div>
              <div className="divide-y divide-border">
                {group.items.map((item: any, j: number) => (
                  <div key={j} className="p-4 flex items-start gap-4 hover:bg-neutral-50/30 transition-colors">
                    <div className="mt-0.5"><StatusIcon status={item.status} /></div>
                    <div className="flex-1">
                      <div className={clsx("font-medium", item.status === 'fail' ? "text-danger" : "text-text")}>
                          {item.message}
                      </div>
                      {item.action && (
                          <div className="mt-2 text-sm text-accent underline cursor-pointer">{item.action}</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
          {/* Summary */}
           <div className="grid grid-cols-3 gap-4">
              <div className="bg-success/10 border border-success/20 rounded-lg p-4 text-center">
                  <div className="text-2xl font-bold text-success">{results.summary?.pass || 0}</div>
                  <div className="text-sm text-success/80 font-medium">Passed</div>
              </div>
              <div className="bg-warning/10 border border-warning/20 rounded-lg p-4 text-center">
                  <div className="text-2xl font-bold text-warning">{results.summary?.warn || 0}</div>
                  <div className="text-sm text-warning/80 font-medium">Warnings</div>
              </div>
              <div className="bg-danger/10 border border-danger/20 rounded-lg p-4 text-center">
                  <div className="text-2xl font-bold text-danger">{results.summary?.fail || 0}</div>
                  <div className="text-sm text-danger/80 font-medium">Failed</div>
              </div>
           </div>
        </div>
      )}
    </div>
  );
};

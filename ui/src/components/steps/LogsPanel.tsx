import React, { useEffect, useState, useMemo, useRef } from 'react';
import { FileText, RefreshCw, Search, Filter, ArrowDown, Pause, Play } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useApi, type LogEntry } from '../../context/ApiContext';
import clsx from 'clsx';

type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'ALL';

const LOG_LEVEL_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  DEBUG: { bg: 'bg-neutral-100', text: 'text-neutral-500', border: 'border-neutral-200' },
  INFO: { bg: 'bg-blue-50', text: 'text-blue-600', border: 'border-blue-200' },
  WARNING: { bg: 'bg-yellow-50', text: 'text-yellow-600', border: 'border-yellow-200' },
  ERROR: { bg: 'bg-red-50', text: 'text-red-600', border: 'border-red-200' },
};

// Parse file location from message like "[__init__.py:246] - actual message"
const parseLogMessage = (message: string): { location?: string; content: string } => {
  const match = message.match(/^\[([^\]]+)\]\s*-?\s*(.*)$/s);
  if (match) {
    return { location: match[1], content: match[2] };
  }
  return { content: message };
};

export const LogsPanel: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [logsTotal, setLogsTotal] = useState(0);
  const [searchQuery, setSearchQuery] = useState('');
  const [levelFilter, setLevelFilter] = useState<LogLevel>('ALL');
  const [autoRefresh, setAutoRefresh] = useState(false);
  const logsContainerRef = useRef<HTMLDivElement>(null);
  const autoRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadLogs = async () => {
    setLoading(true);
    try {
      const res = await api.getLogs(2000);
      setLogs(res.logs);
      setLogsTotal(res.total);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadLogs();
  }, []);

  // Auto-refresh logic
  useEffect(() => {
    if (autoRefresh) {
      autoRefreshRef.current = setInterval(loadLogs, 5000);
    } else if (autoRefreshRef.current) {
      clearInterval(autoRefreshRef.current);
      autoRefreshRef.current = null;
    }
    return () => {
      if (autoRefreshRef.current) {
        clearInterval(autoRefreshRef.current);
      }
    };
  }, [autoRefresh]);

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    if (logsContainerRef.current) {
      logsContainerRef.current.scrollTop = 0; // Since we reverse the list, top is newest
    }
  }, [logs]);

  // Filter logs based on search and level
  const filteredLogs = useMemo(() => {
    return logs.filter((log) => {
      const matchesLevel = levelFilter === 'ALL' || log.level === levelFilter;
      const matchesSearch = !searchQuery || 
        log.message.toLowerCase().includes(searchQuery.toLowerCase()) ||
        log.logger.toLowerCase().includes(searchQuery.toLowerCase()) ||
        log.timestamp.includes(searchQuery);
      return matchesLevel && matchesSearch;
    });
  }, [logs, levelFilter, searchQuery]);

  // Count logs by level
  const levelCounts = useMemo(() => {
    const counts: Record<string, number> = { DEBUG: 0, INFO: 0, WARNING: 0, ERROR: 0 };
    logs.forEach((log) => {
      if (counts[log.level] !== undefined) {
        counts[log.level]++;
      }
    });
    return counts;
  }, [logs]);

  const LogLevelBadge = ({ level }: { level: string }) => {
    const colors = LOG_LEVEL_COLORS[level] || LOG_LEVEL_COLORS.INFO;
    return (
      <span className={clsx(
        'px-1.5 py-0.5 rounded text-xs font-medium border min-w-[60px] text-center inline-block',
        colors.bg, colors.text, colors.border
      )}>
        {level}
      </span>
    );
  };

  const scrollToBottom = () => {
    if (logsContainerRef.current) {
      logsContainerRef.current.scrollTop = 0;
    }
  };

  return (
    <div className="max-w-6xl mx-auto flex flex-col h-full">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-3xl font-display font-bold flex items-center gap-2">
          <FileText className="text-accent" />
          {t('logs.title')}
        </h2>
        <div className="flex gap-2">
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={clsx(
              'flex items-center gap-2 px-3 py-2 border rounded-lg text-sm transition-colors',
              autoRefresh 
                ? 'bg-accent/10 border-accent text-accent' 
                : 'border-border text-muted hover:bg-neutral-50 hover:text-text'
            )}
          >
            {autoRefresh ? <Pause size={16} /> : <Play size={16} />}
            {autoRefresh ? t('logs.autoRefreshOn') : t('logs.autoRefresh')}
          </button>
          <button
            onClick={loadLogs}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-accent text-white rounded-lg hover:bg-accent/90 disabled:opacity-50 transition-colors font-medium shadow-sm"
          >
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} /> 
            {t('common.refresh')}
          </button>
        </div>
      </div>

      <div className="space-y-4 flex-1 flex flex-col min-h-0">
        {/* Search and Filter Bar */}
        <div className="flex gap-3 items-center flex-wrap">
          <div className="relative flex-1 min-w-[200px]">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input
              type="text"
              placeholder={t('logs.searchPlaceholder')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-accent/20 focus:border-accent"
            />
          </div>
          <div className="flex items-center gap-2">
            <Filter size={16} className="text-muted" />
            <select
              value={levelFilter}
              onChange={(e) => setLevelFilter(e.target.value as LogLevel)}
              className="px-3 py-2 border border-border rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-accent/20"
            >
              <option value="ALL">{t('logs.allLevels')}</option>
              <option value="ERROR">{t('logs.error')} ({levelCounts.ERROR})</option>
              <option value="WARNING">{t('logs.warning')} ({levelCounts.WARNING})</option>
              <option value="INFO">{t('logs.info')} ({levelCounts.INFO})</option>
              <option value="DEBUG">{t('logs.debug')} ({levelCounts.DEBUG})</option>
            </select>
          </div>
        </div>

        {/* Level Quick Filters */}
        <div className="flex gap-2 flex-wrap">
          {(['ALL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'] as LogLevel[]).map((level) => (
            <button
              key={level}
              onClick={() => setLevelFilter(level)}
              className={clsx(
                'px-3 py-1 rounded-full text-xs font-medium border transition-colors',
                levelFilter === level
                  ? 'bg-accent text-white border-accent'
                  : 'bg-white text-muted border-border hover:border-accent/50'
              )}
            >
              {level === 'ALL' ? t('logs.all') : level}
              {level !== 'ALL' && ` (${levelCounts[level]})`}
            </button>
          ))}
        </div>

        {/* Logs List */}
        <div className="bg-panel border border-border rounded-xl overflow-hidden shadow-sm flex-1 flex flex-col min-h-0">
          <div className="px-4 py-3 bg-neutral-50 border-b border-border flex justify-between items-center flex-shrink-0">
            <span className="font-semibold text-sm text-muted">
              {t('logs.entriesCount', { filtered: filteredLogs.length, total: logs.length })}
              {logsTotal > logs.length && ` ${t('logs.totalInFile', { total: logsTotal })}`}
            </span>
            <div className="flex items-center gap-3">
              <button
                onClick={scrollToBottom}
                className="text-xs text-accent hover:underline flex items-center gap-1"
              >
                <ArrowDown size={12} /> {t('logs.jumpToLatest')}
              </button>
              <code className="text-xs text-muted bg-neutral-100 px-2 py-1 rounded">
                ~/.vibe_remote/logs/vibe_remote.log
              </code>
            </div>
          </div>
          
          {loading && logs.length === 0 ? (
            <div className="p-8 text-center text-muted flex-1 flex items-center justify-center">
              <div>
                <RefreshCw size={24} className="animate-spin mx-auto mb-2" />
                {t('logs.loadingLogs')}
              </div>
            </div>
          ) : filteredLogs.length === 0 ? (
            <div className="p-8 text-center text-muted flex-1 flex items-center justify-center">
              {logs.length === 0 ? t('logs.noLogsAvailable') : t('logs.noLogsMatch')}
            </div>
          ) : (
            <div 
              ref={logsContainerRef}
              className="divide-y divide-border overflow-y-auto flex-1 font-mono text-sm"
              style={{ minHeight: '500px', maxHeight: 'calc(100vh - 380px)' }}
            >
              {filteredLogs.slice().reverse().map((log, i) => {
                const { location, content } = parseLogMessage(log.message);
                return (
                  <div 
                    key={i} 
                    className={clsx(
                      'p-3 hover:bg-neutral-50/50 transition-colors',
                      log.level === 'ERROR' && 'bg-red-50/30',
                      log.level === 'WARNING' && 'bg-yellow-50/30'
                    )}
                  >
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-muted text-xs whitespace-nowrap">
                        {log.timestamp}
                      </span>
                      <LogLevelBadge level={log.level} />
                      <span className="text-muted text-xs truncate max-w-[200px]" title={log.logger}>
                        {log.logger}
                      </span>
                      {location && (
                        <span className="text-muted/60 text-xs font-normal">
                          [{location}]
                        </span>
                      )}
                    </div>
                    <div className={clsx(
                      'mt-1.5 whitespace-pre-wrap break-words leading-relaxed',
                      log.level === 'ERROR' ? 'text-red-700' : 
                      log.level === 'WARNING' ? 'text-yellow-700' : 'text-text'
                    )}>
                      {content}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

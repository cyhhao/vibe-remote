import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Activity,
  Calendar,
  Eye,
  Webhook,
  History,
  RefreshCw,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  PauseCircle,
} from 'lucide-react';
import clsx from 'clsx';

import { useApi } from '../../context/ApiContext';
import type {
  HarnessRun,
  HarnessRunStatus,
  HarnessTask,
  HarnessWatch,
} from '../../context/ApiContext';

type TabKey = 'tasks' | 'watches' | 'webhooks' | 'runs';

const TAB_ORDER: TabKey[] = ['tasks', 'watches', 'webhooks', 'runs'];

type Selection =
  | { kind: 'task'; id: string }
  | { kind: 'watch'; id: string }
  | { kind: 'run'; id: string }
  | null;

export const HarnessPage: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const [tab, setTab] = useState<TabKey>('tasks');
  const [tasks, setTasks] = useState<HarnessTask[]>([]);
  const [watches, setWatches] = useState<HarnessWatch[]>([]);
  const [runs, setRuns] = useState<HarnessRun[]>([]);
  const [runsHasMore, setRunsHasMore] = useState(false);
  const [runsPage, setRunsPage] = useState(1);
  const [selection, setSelection] = useState<Selection>(null);
  const [selectedRun, setSelectedRun] = useState<HarnessRun | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (tab === 'tasks') {
        const result = await api.listHarnessTasks();
        setTasks(result.tasks);
      } else if (tab === 'watches') {
        const result = await api.listHarnessWatches();
        setWatches(result.watches);
      } else if (tab === 'runs') {
        const result = await api.listHarnessRuns({ page: runsPage, limit: 30 });
        setRuns(result.runs);
        setRunsHasMore(result.has_more);
      }
    } catch (err: any) {
      setError(err?.message ?? String(err));
    } finally {
      setLoading(false);
    }
  }, [api, tab, runsPage]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Fetch run detail (stdout/stderr) whenever a run is selected so the
  // detail panel always shows the full body, not just the list excerpt.
  useEffect(() => {
    if (selection?.kind !== 'run') {
      setSelectedRun(null);
      return;
    }
    let cancelled = false;
    api
      .getHarnessRun(selection.id)
      .then((result) => {
        if (!cancelled && result.ok) setSelectedRun(result.run);
      })
      .catch((err) => {
        if (!cancelled) setError(err?.message ?? String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [api, selection]);

  const counts = useMemo(
    () => ({
      tasks: tasks.length,
      watches: watches.length,
      webhooks: 0,
      runs: runs.length + (runsHasMore ? 1 : 0),
    }),
    [tasks.length, watches.length, runs.length, runsHasMore],
  );

  const selectedTask = useMemo(
    () => (selection?.kind === 'task' ? tasks.find((task) => task.id === selection.id) ?? null : null),
    [selection, tasks],
  );
  const selectedWatch = useMemo(
    () => (selection?.kind === 'watch' ? watches.find((watch) => watch.id === selection.id) ?? null : null),
    [selection, watches],
  );

  return (
    <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-5 py-2">
      {/* Header */}
      <div className="flex items-center gap-4">
        <div className="flex size-12 shrink-0 items-center justify-center rounded-2xl border border-violet/30 bg-violet/[0.08] text-violet shadow-[0_0_24px_-6px_rgba(124,91,255,0.5)]">
          <Activity className="size-5" />
        </div>
        <div className="flex flex-1 flex-col">
          <h1 className="text-2xl font-bold text-foreground">{t('harness.title')}</h1>
          <p className="text-[13px] text-muted">{t('harness.subtitle')}</p>
        </div>
        <button
          type="button"
          onClick={() => refresh()}
          disabled={loading}
          className={clsx(
            'flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12px] font-medium transition',
            loading
              ? 'cursor-wait border-border bg-foreground/[0.02] text-muted'
              : 'border-border-strong text-foreground hover:bg-foreground/[0.04]',
          )}
        >
          <RefreshCw className={clsx('size-3.5', loading && 'animate-spin')} />
          {t('common.refresh')}
        </button>
      </div>

      {/* Tab row */}
      <div className="flex items-center gap-0 border-b border-border">
        {TAB_ORDER.map((key) => {
          const active = tab === key;
          const count = counts[key];
          return (
            <button
              key={key}
              type="button"
              onClick={() => {
                setTab(key);
                setSelection(null);
              }}
              className={clsx(
                'flex items-center gap-2 px-4 py-3 text-[13px] transition',
                active ? 'border-b-2 border-violet font-bold text-violet' : 'font-medium text-muted hover:text-foreground',
              )}
            >
              <HarnessTabIcon tab={key} active={active} />
              {t(`harness.tabs.${key}`)}
              {key !== 'webhooks' && (
                <span
                  className={clsx(
                    'rounded-full border px-1.5 py-0 font-mono text-[9px] font-bold',
                    active
                      ? 'border-violet/30 bg-violet/[0.10] text-violet'
                      : 'border-border-strong bg-foreground/[0.04] text-muted',
                  )}
                >
                  {count}
                </span>
              )}
              {key === 'webhooks' && (
                <span className="font-mono text-[9px] text-muted">{t('harness.soon')}</span>
              )}
            </button>
          );
        })}
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-[12px] text-destructive">
          {error}
        </div>
      )}

      {/* Body: list + detail */}
      <div className="grid grid-cols-[1fr_440px] gap-5">
        <div className="flex flex-col gap-2">
          {tab === 'tasks' && (
            <TasksList tasks={tasks} loading={loading} selectedId={selection?.kind === 'task' ? selection.id : null} onSelect={(id) => setSelection({ kind: 'task', id })} />
          )}
          {tab === 'watches' && (
            <WatchesList watches={watches} loading={loading} selectedId={selection?.kind === 'watch' ? selection.id : null} onSelect={(id) => setSelection({ kind: 'watch', id })} />
          )}
          {tab === 'webhooks' && <WebhooksEmpty />}
          {tab === 'runs' && (
            <RunsList
              runs={runs}
              loading={loading}
              selectedId={selection?.kind === 'run' ? selection.id : null}
              onSelect={(id) => setSelection({ kind: 'run', id })}
              page={runsPage}
              hasMore={runsHasMore}
              onPageChange={setRunsPage}
            />
          )}
        </div>

        <div className="flex flex-col gap-3 self-start rounded-xl border border-border-strong bg-surface p-5">
          {selectedTask ? (
            <TaskDetail task={selectedTask} />
          ) : selectedWatch ? (
            <WatchDetail watch={selectedWatch} />
          ) : selectedRun ? (
            <RunDetail run={selectedRun} />
          ) : (
            <div className="flex flex-col items-center justify-center gap-3 py-12 text-center text-[12px] text-muted">
              <Activity className="size-6 text-muted" />
              {t('harness.selectPrompt')}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

interface TabIconProps {
  tab: TabKey;
  active: boolean;
}

const HarnessTabIcon: React.FC<TabIconProps> = ({ tab, active }) => {
  const cls = clsx('size-3.5', active ? 'text-violet' : 'text-muted');
  if (tab === 'tasks') return <Calendar className={cls} />;
  if (tab === 'watches') return <Eye className={cls} />;
  if (tab === 'webhooks') return <Webhook className={cls} />;
  return <History className={cls} />;
};

// ---------------------------------------------------------------------------
// Tasks tab
// ---------------------------------------------------------------------------

interface TasksListProps {
  tasks: HarnessTask[];
  loading: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

const TasksList: React.FC<TasksListProps> = ({ tasks, loading, selectedId, onSelect }) => {
  const { t } = useTranslation();
  if (tasks.length === 0 && !loading) return <EmptyState i18nKey="harness.emptyTasks" />;
  return (
    <>
      {tasks.map((task) => {
        const active = selectedId === task.id;
        const scheduleLabel = task.cron
          ? `cron · ${task.cron}`
          : task.run_at
          ? `one-shot · ${task.run_at}`
          : task.schedule_type || t('harness.unknownSchedule');
        return (
          <button
            key={task.id}
            type="button"
            onClick={() => onSelect(task.id)}
            className={clsx(
              'flex items-center gap-3 rounded-lg border px-4 py-3 text-left transition',
              active ? 'border-violet/40 bg-violet/[0.05]' : 'border-border bg-surface hover:bg-foreground/[0.03]',
            )}
          >
            <div className="flex flex-1 flex-col gap-1">
              <div className="flex items-center gap-2">
                <span className="text-[14px] font-semibold text-foreground">{task.name || task.id}</span>
                {!task.enabled && (
                  <span className="rounded border border-border-strong bg-foreground/[0.04] px-1.5 py-0 font-mono text-[9px] text-muted">
                    DISABLED
                  </span>
                )}
              </div>
              <div className="flex items-center gap-3 text-[11px] text-muted">
                <span className="inline-flex items-center gap-1 font-mono">
                  <Clock className="size-3" />
                  {scheduleLabel}
                </span>
                {task.agent_name && <span>· {task.agent_name}</span>}
              </div>
            </div>
            {task.last_run_at && (
              <span className="font-mono text-[10px] text-muted">{formatRelative(task.last_run_at)}</span>
            )}
          </button>
        );
      })}
    </>
  );
};

interface TaskDetailProps {
  task: HarnessTask;
}

const TaskDetail: React.FC<TaskDetailProps> = ({ task }) => {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <Calendar className="size-4 text-violet" />
        <div className="flex-1 truncate text-[15px] font-bold text-foreground">{task.name || task.id}</div>
        <StatusPill enabled={task.enabled} />
      </div>
      <DetailField label={t('harness.detail.schedule')}>
        <span className="font-mono text-[12px] text-foreground">
          {task.cron ?? task.run_at ?? task.schedule_type ?? '—'}
        </span>
        {task.timezone && <span className="ml-2 text-[10px] text-muted">{task.timezone}</span>}
      </DetailField>
      <DetailField label={t('harness.detail.agent')}>
        <span className="text-[12px] text-foreground">{task.agent_name || '—'}</span>
      </DetailField>
      <DetailField label={t('harness.detail.message')}>
        <pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface-3 p-2 font-mono text-[11px] text-foreground">
          {task.message || task.prompt || '—'}
        </pre>
      </DetailField>
      {task.last_run_at && (
        <DetailField label={t('harness.detail.lastRun')}>
          <span className="font-mono text-[11px] text-muted">{task.last_run_at}</span>
          {task.last_error && (
            <div className="mt-1 rounded-md border border-destructive/40 bg-destructive/[0.06] px-2 py-1 text-[11px] text-destructive">
              {task.last_error}
            </div>
          )}
        </DetailField>
      )}
      <DetailField label={t('harness.detail.id')}>
        <code className="font-mono text-[11px] text-muted">{task.id}</code>
      </DetailField>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Watches tab
// ---------------------------------------------------------------------------

interface WatchesListProps {
  watches: HarnessWatch[];
  loading: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

const WatchesList: React.FC<WatchesListProps> = ({ watches, loading, selectedId, onSelect }) => {
  const { t } = useTranslation();
  if (watches.length === 0 && !loading) return <EmptyState i18nKey="harness.emptyWatches" />;
  return (
    <>
      {watches.map((watch) => {
        const active = selectedId === watch.id;
        const cmd = watch.shell_command || (Array.isArray(watch.command) ? watch.command.join(' ') : '') || '—';
        return (
          <button
            key={watch.id}
            type="button"
            onClick={() => onSelect(watch.id)}
            className={clsx(
              'flex items-center gap-3 rounded-lg border px-4 py-3 text-left transition',
              active ? 'border-violet/40 bg-violet/[0.05]' : 'border-border bg-surface hover:bg-foreground/[0.03]',
            )}
          >
            <div className="flex flex-1 flex-col gap-1">
              <div className="flex items-center gap-2">
                <span className="text-[14px] font-semibold text-foreground">{watch.name || watch.id}</span>
                {watch.runtime.running ? (
                  <span className="inline-flex items-center gap-1 rounded-full border border-mint/30 bg-mint/[0.08] px-2 py-0 font-mono text-[9px] font-bold text-mint">
                    <span className="size-1.5 rounded-full bg-mint" />
                    RUNNING
                  </span>
                ) : !watch.enabled ? (
                  <span className="inline-flex items-center gap-1 rounded-full border border-border-strong bg-foreground/[0.04] px-2 py-0 font-mono text-[9px] text-muted">
                    <PauseCircle className="size-2.5" />
                    PAUSED
                  </span>
                ) : (
                  <span className="rounded border border-border-strong bg-foreground/[0.04] px-1.5 py-0 font-mono text-[9px] text-muted">
                    IDLE
                  </span>
                )}
              </div>
              <div className="truncate font-mono text-[11px] text-muted">{cmd}</div>
            </div>
            {watch.last_event_at && (
              <span className="font-mono text-[10px] text-muted">{formatRelative(watch.last_event_at)}</span>
            )}
          </button>
        );
      })}
      {watches.length === 0 && loading && <div className="px-4 py-6 text-[12px] text-muted">{t('common.loading')}</div>}
    </>
  );
};

interface WatchDetailProps {
  watch: HarnessWatch;
}

const WatchDetail: React.FC<WatchDetailProps> = ({ watch }) => {
  const { t } = useTranslation();
  const cmd = watch.shell_command || (Array.isArray(watch.command) ? watch.command.join(' ') : '') || '—';
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <Eye className="size-4 text-violet" />
        <div className="flex-1 truncate text-[15px] font-bold text-foreground">{watch.name || watch.id}</div>
        <StatusPill enabled={watch.enabled} runtimeRunning={watch.runtime.running} />
      </div>
      <DetailField label={t('harness.detail.command')}>
        <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface-3 p-2 font-mono text-[11px] text-foreground">
          {cmd}
        </pre>
      </DetailField>
      <DetailField label={t('harness.detail.agent')}>
        <span className="text-[12px] text-foreground">{watch.agent_name || '—'}</span>
      </DetailField>
      <DetailField label={t('harness.detail.cwd')}>
        <code className="font-mono text-[11px] text-muted">{watch.cwd || '—'}</code>
      </DetailField>
      <DetailField label={t('harness.detail.mode')}>
        <span className="font-mono text-[11px] text-muted">{watch.mode}</span>
      </DetailField>
      <DetailField label={t('harness.detail.followUp')}>
        <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface-3 p-2 font-mono text-[11px] text-foreground">
          {watch.message || watch.prefix || '—'}
        </pre>
      </DetailField>
      {watch.runtime.running && watch.runtime.pid != null && (
        <DetailField label={t('harness.detail.runtime')}>
          <span className="font-mono text-[11px] text-muted">
            pid {watch.runtime.pid} · started {watch.runtime.started_at}
          </span>
        </DetailField>
      )}
      {watch.last_error && (
        <DetailField label={t('harness.detail.lastError')}>
          <div className="rounded-md border border-destructive/40 bg-destructive/[0.06] px-2 py-1 text-[11px] text-destructive">
            {watch.last_error}
          </div>
        </DetailField>
      )}
      <DetailField label={t('harness.detail.id')}>
        <code className="font-mono text-[11px] text-muted">{watch.id}</code>
      </DetailField>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Webhooks tab — coming soon
// ---------------------------------------------------------------------------

const WebhooksEmpty: React.FC = () => {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border bg-surface px-6 py-16 text-center">
      <Webhook className="size-8 text-muted" />
      <div className="text-[14px] font-semibold text-foreground">{t('harness.webhooksSoon')}</div>
      <div className="max-w-md text-[12px] text-muted">{t('harness.webhooksSoonBody')}</div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Runs tab
// ---------------------------------------------------------------------------

interface RunsListProps {
  runs: HarnessRun[];
  loading: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
  page: number;
  hasMore: boolean;
  onPageChange: (page: number) => void;
}

const RunsList: React.FC<RunsListProps> = ({ runs, loading, selectedId, onSelect, page, hasMore, onPageChange }) => {
  const { t } = useTranslation();
  if (runs.length === 0 && !loading) return <EmptyState i18nKey="harness.emptyRuns" />;
  return (
    <>
      {runs.map((run) => {
        const active = selectedId === run.id;
        return (
          <button
            key={run.id}
            type="button"
            onClick={() => onSelect(run.id)}
            className={clsx(
              'flex items-center gap-3 rounded-lg border px-4 py-3 text-left transition',
              active ? 'border-violet/40 bg-violet/[0.05]' : 'border-border bg-surface hover:bg-foreground/[0.03]',
            )}
          >
            <RunStatusIcon status={run.status} />
            <div className="flex flex-1 flex-col gap-1">
              <div className="flex items-center gap-2">
                <span className="font-mono text-[12px] font-semibold text-foreground">{run.id}</span>
                <span className="rounded border border-border-strong bg-foreground/[0.04] px-1.5 py-0 font-mono text-[9px] text-muted">
                  {run.run_type || 'run'}
                </span>
              </div>
              <div className="flex items-center gap-3 text-[11px] text-muted">
                <span>{run.agent_name || '—'}</span>
                {run.created_at && <span>· {formatRelative(run.created_at)}</span>}
              </div>
            </div>
          </button>
        );
      })}
      {(page > 1 || hasMore) && (
        <div className="mt-2 flex items-center justify-end gap-2 px-1">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
            className="rounded border border-border-strong px-2 py-1 font-mono text-[10px] text-muted hover:text-foreground disabled:opacity-40"
          >
            {t('common.previous')}
          </button>
          <span className="font-mono text-[10px] text-muted">{t('harness.pageLabel', { page })}</span>
          <button
            type="button"
            disabled={!hasMore}
            onClick={() => onPageChange(page + 1)}
            className="rounded border border-border-strong px-2 py-1 font-mono text-[10px] text-muted hover:text-foreground disabled:opacity-40"
          >
            {t('common.next')}
          </button>
        </div>
      )}
    </>
  );
};

interface RunDetailProps {
  run: HarnessRun;
}

const RunDetail: React.FC<RunDetailProps> = ({ run }) => {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <RunStatusIcon status={run.status} />
        <code className="flex-1 truncate font-mono text-[13px] font-bold text-foreground">{run.id}</code>
        <span
          className={clsx(
            'rounded border px-2 py-0 font-mono text-[9px] font-bold uppercase',
            STATUS_PILL_CLASS[run.status as HarnessRunStatus] ?? 'border-border-strong bg-foreground/[0.04] text-muted',
          )}
        >
          {run.status}
        </span>
      </div>
      <DetailField label={t('harness.detail.type')}>
        <span className="font-mono text-[11px] text-muted">{run.run_type || run.request_type || '—'}</span>
      </DetailField>
      <DetailField label={t('harness.detail.agent')}>
        <span className="text-[12px] text-foreground">{run.agent_name || '—'}</span>
        {run.agent_backend && <span className="ml-2 font-mono text-[10px] text-muted">{run.agent_backend}</span>}
        {run.model && <span className="ml-2 font-mono text-[10px] text-muted">{run.model}</span>}
      </DetailField>
      {run.definition_id && (
        <DetailField label={t('harness.detail.definition')}>
          <code className="font-mono text-[11px] text-muted">{run.definition_id}</code>
        </DetailField>
      )}
      {(run.message || run.prompt) && (
        <DetailField label={t('harness.detail.message')}>
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface-3 p-2 font-mono text-[11px] text-foreground">
            {run.message || run.prompt}
          </pre>
        </DetailField>
      )}
      {run.result_text && (
        <DetailField label={t('harness.detail.result')}>
          <pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface-3 p-2 font-mono text-[11px] text-foreground">
            {run.result_text}
          </pre>
        </DetailField>
      )}
      {run.error && (
        <DetailField label={t('harness.detail.error')}>
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md border border-destructive/40 bg-destructive/[0.06] p-2 font-mono text-[11px] text-destructive">
            {run.error}
          </pre>
        </DetailField>
      )}
      {run.stdout && (
        <DetailField label="stdout">
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface-3 p-2 font-mono text-[10px] text-foreground">
            {run.stdout}
          </pre>
        </DetailField>
      )}
      {run.stderr && (
        <DetailField label="stderr">
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface-3 p-2 font-mono text-[10px] text-foreground">
            {run.stderr}
          </pre>
        </DetailField>
      )}
      <DetailField label={t('harness.detail.timing')}>
        <div className="flex flex-col gap-0.5 font-mono text-[10px] text-muted">
          <span>created {run.created_at ?? '—'}</span>
          {run.started_at && <span>started {run.started_at}</span>}
          {run.completed_at && <span>completed {run.completed_at}</span>}
          {run.exit_code != null && <span>exit_code {run.exit_code}</span>}
          {run.pid != null && <span>pid {run.pid}</span>}
        </div>
      </DetailField>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

const STATUS_PILL_CLASS: Record<HarnessRunStatus, string> = {
  queued: 'border-cyan/30 bg-cyan/[0.08] text-cyan',
  running: 'border-violet/30 bg-violet/[0.08] text-violet',
  succeeded: 'border-mint/30 bg-mint/[0.08] text-mint',
  failed: 'border-pink/30 bg-pink/[0.08] text-pink',
  canceled: 'border-border-strong bg-foreground/[0.04] text-muted',
};

const RunStatusIcon: React.FC<{ status: HarnessRunStatus }> = ({ status }) => {
  const cls = 'size-4 shrink-0';
  if (status === 'succeeded') return <CheckCircle2 className={clsx(cls, 'text-mint')} />;
  if (status === 'failed') return <XCircle className={clsx(cls, 'text-pink')} />;
  if (status === 'running') return <Loader2 className={clsx(cls, 'animate-spin text-violet')} />;
  if (status === 'queued') return <Clock className={clsx(cls, 'text-cyan')} />;
  if (status === 'canceled') return <AlertTriangle className={clsx(cls, 'text-muted')} />;
  return <Activity className={clsx(cls, 'text-muted')} />;
};

interface StatusPillProps {
  enabled: boolean;
  runtimeRunning?: boolean;
}

const StatusPill: React.FC<StatusPillProps> = ({ enabled, runtimeRunning }) => {
  if (runtimeRunning) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-mint/30 bg-mint/[0.08] px-2 py-0 font-mono text-[9px] font-bold text-mint">
        <span className="size-1.5 rounded-full bg-mint" />
        RUNNING
      </span>
    );
  }
  if (!enabled) {
    return (
      <span className="rounded-full border border-border-strong bg-foreground/[0.04] px-2 py-0 font-mono text-[9px] text-muted">
        DISABLED
      </span>
    );
  }
  return (
    <span className="rounded-full border border-border-strong bg-foreground/[0.04] px-2 py-0 font-mono text-[9px] text-muted">
      ENABLED
    </span>
  );
};

interface DetailFieldProps {
  label: string;
  children: React.ReactNode;
}

const DetailField: React.FC<DetailFieldProps> = ({ label, children }) => (
  <div className="flex flex-col gap-1.5">
    <div className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted">{label}</div>
    <div>{children}</div>
  </div>
);

const EmptyState: React.FC<{ i18nKey: string }> = ({ i18nKey }) => {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border bg-surface px-6 py-12 text-center">
      <Activity className="size-6 text-muted" />
      <div className="text-[13px] text-muted">{t(i18nKey)}</div>
    </div>
  );
};

function formatRelative(value: string | null | undefined): string {
  if (!value) return '—';
  const dt = new Date(value);
  if (Number.isNaN(dt.valueOf())) return value;
  const diffMs = Date.now() - dt.valueOf();
  const secs = Math.round(diffMs / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return dt.toISOString().slice(0, 10);
}

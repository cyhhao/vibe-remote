import React, { useEffect, useState } from 'react';
import { FolderOpen, HelpCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { Combobox } from '../ui/combobox';
import { BackendIcon } from '../visual';

// Mirrors design.pen `asPXu` (VR/RoutingConfig). Shared between groups (channels)
// and users — same form, same fields, same styles. The only difference is whether
// the @mention requirement segmented toggle is shown.

export interface RoutingConfigValue {
  custom_cwd: string;
  routing: {
    agent_backend: string | null;
    opencode_agent?: string | null;
    opencode_model?: string | null;
    opencode_reasoning_effort?: string | null;
    claude_agent?: string | null;
    claude_model?: string | null;
    claude_reasoning_effort?: string | null;
    codex_agent?: string | null;
    codex_model?: string | null;
    codex_reasoning_effort?: string | null;
  };
  show_message_types: string[];
  require_mention?: boolean | null;
}

export interface RoutingConfigPanelProps {
  value: RoutingConfigValue;
  onChange: (patch: Partial<RoutingConfigValue>) => void;
  onBrowseDirectory: () => void;
  globalConfig: any;
  /** Show the require-@mention segmented control (Inherit / On / Off). */
  showRequireMention?: boolean;
  /** Platform key used to derive inherited @mention default (e.g., 'slack', 'discord'). */
  inheritsFromKey?: string;
  /** Backend lookup data — pass already-loaded values from the parent. */
  opencodeOptions?: any;
  claudeAgents?: { id: string; name: string }[];
  claudeModels?: string[];
  claudeReasoningOptions?: Record<string, { value: string; label: string }[]>;
  codexAgents?: { id: string; name: string }[];
  codexModels?: string[];
  /** Optional footer slot — e.g., admin/remove actions on the users page. */
  footerActions?: React.ReactNode;
  /** Wrapper class — controls outer padding/border. Default: 'border-t border-border/60 px-5 py-4'. */
  containerClass?: string;
}

/** Input that only commits value on blur */
function BlurInput({
  value,
  onCommit,
  ...props
}: { value: string; onCommit: (v: string) => void } & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'onBlur'>) {
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);
  return (
    <input
      {...props}
      value={local}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={() => { if (local !== value) onCommit(local); }}
    />
  );
}

export const RoutingConfigPanel: React.FC<RoutingConfigPanelProps> = ({
  value,
  onChange,
  onBrowseDirectory,
  globalConfig,
  showRequireMention = true,
  inheritsFromKey,
  opencodeOptions,
  claudeAgents = [],
  claudeModels = [],
  claudeReasoningOptions = {},
  codexAgents = [],
  codexModels = [],
  footerActions,
  containerClass = 'border-t border-border/60 px-5 py-4',
}) => {
  const { t } = useTranslation();

  const defaultBackend = globalConfig?.agents?.default_backend || 'opencode';
  const effectiveBackend = value.routing.agent_backend || defaultBackend;

  const getOpenCodeReasoningOptions = (modelKey: string) => {
    const lookup = opencodeOptions?.reasoning_options || {};
    if (lookup && typeof lookup === 'object') {
      return (lookup as Record<string, { value: string; label: string }[]>)[modelKey] || [];
    }
    return [];
  };

  const getClaudeReasoning = (model: string) => {
    const modelKey = model || '';
    const cached = claudeReasoningOptions[modelKey];
    if (cached?.length) return cached;
    const fallback = claudeReasoningOptions[''] || [];
    const normalized = modelKey.toLowerCase();
    if (normalized.includes('claude-opus-4-7') || normalized === 'opus' || normalized === 'opus[1m]') {
      const opts = [...fallback];
      if (!opts.some((o) => o.value === 'xhigh')) opts.push({ value: 'xhigh', label: 'Extra High' });
      if (!opts.some((o) => o.value === 'max')) opts.push({ value: 'max', label: 'Max' });
      return opts;
    }
    if (normalized.includes('claude-opus-4-6') || normalized.includes('claude-sonnet-4-6')) {
      return fallback.some((o) => o.value === 'max') ? fallback : [...fallback, { value: 'max', label: 'Max' }];
    }
    return fallback;
  };

  const getReasoningLabel = (val: string, fallback: string) => {
    switch (val) {
      case 'low': return t('channelList.reasoningLow');
      case 'medium': return t('channelList.reasoningMedium');
      case 'high': return t('channelList.reasoningHigh');
      case 'xhigh': return t('channelList.reasoningXHigh');
      case 'max': return t('channelList.reasoningMax');
      default: return fallback;
    }
  };

  // Top row: working dir + backend (+ optional require_mention) — dynamic grid columns
  const topGridCols = showRequireMention ? 'md:grid-cols-3' : 'md:grid-cols-2';

  return (
    <div className={clsx('space-y-4', containerClass)}>
      <div className={clsx('grid grid-cols-1 gap-3', topGridCols)}>
        {/* Working directory */}
        <div className="space-y-1">
          <label className="text-xs font-medium uppercase text-muted">{t('channelList.workingDirectory')}</label>
          <div className="flex gap-1.5">
            <BlurInput
              type="text"
              placeholder={globalConfig?.runtime?.default_cwd || t('channelList.useGlobalDefault')}
              value={value.custom_cwd}
              onCommit={(v) => onChange({ custom_cwd: v })}
              className="flex-1 rounded-lg border border-border bg-surface px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted/50 focus:border-cyan focus:outline-none"
            />
            <button
              type="button"
              onClick={onBrowseDirectory}
              title={t('directoryBrowser.title')}
              className="shrink-0 rounded-lg border border-border bg-surface-3/60 px-2 py-2 text-muted transition-colors hover:border-cyan/40 hover:bg-surface-2/70 hover:text-foreground"
            >
              <FolderOpen size={14} />
            </button>
          </div>
        </div>

        {/* Backend */}
        <div className="space-y-1">
          <label className="text-xs font-medium uppercase text-muted">{t('channelList.backend')}</label>
          <div className="relative">
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2">
              <BackendIcon backend={effectiveBackend} variant="glyph" size={14} />
            </span>
            <select
              value={effectiveBackend}
              onChange={(e) => onChange({ routing: { ...value.routing, agent_backend: e.target.value } })}
              className="w-full rounded-lg border border-border bg-surface py-2 pl-9 pr-3 text-sm text-foreground focus:border-cyan focus:outline-none"
            >
              <option value="opencode">OpenCode</option>
              <option value="claude">ClaudeCode</option>
              <option value="codex">Codex</option>
            </select>
          </div>
        </div>

        {/* Require @mention (channels only) */}
        {showRequireMention && (() => {
          const current: 'inherit' | 'on' | 'off' =
            value.require_mention === null || value.require_mention === undefined
              ? 'inherit'
              : value.require_mention
                ? 'on'
                : 'off';
          const setMention = (next: 'inherit' | 'on' | 'off') => {
            onChange({ require_mention: next === 'inherit' ? null : next === 'on' });
          };
          const inheritedOn = !!(globalConfig as any)?.[inheritsFromKey || '']?.require_mention;
          const segs: { id: 'inherit' | 'on' | 'off'; label: string }[] = [
            {
              id: 'inherit',
              label: `${t('common.inherit')} (${
                inheritedOn ? t('channelList.mentionStatusOn') : t('channelList.mentionStatusOff')
              })`,
            },
            { id: 'on', label: t('channelList.requireMentionOn') },
            { id: 'off', label: t('channelList.requireMentionOff') },
          ];
          return (
            <div className="space-y-1">
              <label className="text-xs font-medium uppercase text-muted">{t('channelList.requireMention')}</label>
              <div
                role="radiogroup"
                aria-label={t('channelList.requireMention') as string}
                className="flex h-9 items-stretch gap-0.5 rounded-md border border-border bg-white/[0.03] p-0.5"
              >
                {segs.map((seg) => {
                  const active = current === seg.id;
                  return (
                    <button
                      key={seg.id}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      onClick={() => setMention(seg.id)}
                      className={clsx(
                        'flex-1 rounded-[4px] px-2.5 text-[12px] transition-colors',
                        active
                          ? 'border border-mint/30 bg-mint-soft font-bold text-mint'
                          : 'font-medium text-muted hover:text-foreground'
                      )}
                    >
                      {seg.label}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })()}
      </div>

      {/* Show message types chips */}
      <div className="space-y-2">
        <div className="flex items-center gap-1 text-xs font-medium uppercase text-muted">
          {t('channelList.showMessageTypes')}
          <span className="group relative">
            <HelpCircle size={12} className="cursor-help text-muted/50" />
            <span className="pointer-events-none absolute bottom-full left-0 z-10 mb-2 w-64 whitespace-normal rounded bg-text px-3 py-2 text-xs font-normal normal-case text-bg opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
              {t('channelList.showMessageTypesHint')}
            </span>
          </span>
        </div>
        <div className="flex flex-wrap gap-2 text-sm">
          {['system', 'assistant', 'toolcall'].map((msgType) => {
            const checked = (value.show_message_types || []).includes(msgType);
            const label = msgType === 'toolcall' ? 'Toolcall' : msgType.charAt(0).toUpperCase() + msgType.slice(1);
            return (
              <button
                key={msgType}
                type="button"
                aria-pressed={checked}
                onClick={() => {
                  const next = checked
                    ? (value.show_message_types || []).filter((v) => v !== msgType)
                    : [...(value.show_message_types || []), msgType];
                  onChange({ show_message_types: next });
                }}
                className={clsx(
                  'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[12px] font-medium transition-colors',
                  checked
                    ? 'border-mint/40 bg-mint/15 text-mint'
                    : 'border-border bg-white/[0.02] text-muted hover:border-border-strong hover:text-foreground'
                )}
              >
                <span
                  className={clsx(
                    'size-1.5 rounded-full',
                    checked ? 'bg-mint shadow-[0_0_6px_rgba(91,255,160,0.7)]' : 'bg-muted/50'
                  )}
                />
                {label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Backend-specific settings */}
      {effectiveBackend === 'opencode' && (
        <div className="space-y-3">
          <div className="text-xs font-medium uppercase text-muted">{t('channelList.opencodeSettings')}</div>
          <div className="grid grid-cols-1 gap-3 rounded-xl border border-border bg-surface/80 p-3 md:grid-cols-3">
            <div className="space-y-1">
              <label className="text-xs text-muted">{t('channelList.agent')}</label>
              <select
                value={value.routing.opencode_agent || ''}
                onChange={(e) => onChange({ routing: { ...value.routing, opencode_agent: e.target.value || null } })}
                className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-foreground"
              >
                <option value="">{t('common.default')}</option>
                {(opencodeOptions?.agents || []).map((a: any) => (
                  <option key={a.name} value={a.name}>{a.name}</option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted">{t('channelList.model')}</label>
              <select
                value={value.routing.opencode_model || ''}
                onChange={(e) => onChange({
                  routing: {
                    ...value.routing,
                    opencode_model: e.target.value || null,
                    opencode_reasoning_effort: null,
                  },
                })}
                className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-foreground"
              >
                <option value="">{t('common.default')}</option>
                {(opencodeOptions?.models?.providers || []).flatMap((provider: any) => {
                  const pid = provider.id || provider.provider_id || provider.name;
                  const pLabel = provider.name || pid;
                  const models = provider.models || {};
                  if (Array.isArray(models)) {
                    return models.map((m: any) => {
                      const mid = typeof m === 'string' ? m : m.id;
                      return <option key={`${pid}:${mid}`} value={`${pid}/${mid}`}>{pLabel}/{mid}</option>;
                    });
                  }
                  return Object.keys(models).map((mid) => (
                    <option key={`${pid}:${mid}`} value={`${pid}/${mid}`}>{pLabel}/{mid}</option>
                  ));
                })}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted">{t('channelList.reasoningEffort')}</label>
              <select
                value={value.routing.opencode_reasoning_effort || ''}
                onChange={(e) => onChange({
                  routing: { ...value.routing, opencode_reasoning_effort: e.target.value || null },
                })}
                disabled={!getOpenCodeReasoningOptions(value.routing.opencode_model || '').length}
                className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-foreground disabled:opacity-50"
              >
                <option value="">{t('common.default')}</option>
                {getOpenCodeReasoningOptions(value.routing.opencode_model || '').map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
          </div>
        </div>
      )}

      {effectiveBackend === 'claude' && (
        <div className="space-y-3">
          <div className="text-xs font-medium uppercase text-muted">{t('channelList.claudeSettings')}</div>
          <div className="grid grid-cols-1 gap-3 rounded-xl border border-border bg-surface/80 p-3 md:grid-cols-3">
            <div className="space-y-1">
              <label className="text-xs text-muted">{t('channelList.agent')}</label>
              <select
                value={value.routing.claude_agent || ''}
                onChange={(e) => onChange({ routing: { ...value.routing, claude_agent: e.target.value || null } })}
                className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-foreground"
              >
                <option value="">{t('common.default')}</option>
                {claudeAgents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted">{t('channelList.model')}</label>
              <Combobox
                options={[{ value: '', label: t('common.default') }, ...claudeModels.map(m => ({ value: m, label: m }))]}
                value={value.routing.claude_model || ''}
                onValueChange={(v) => onChange({
                  routing: {
                    ...value.routing,
                    claude_model: v || null,
                    claude_reasoning_effort: null,
                  },
                })}
                placeholder={t('channelList.claudeModelPlaceholder')}
                searchPlaceholder={t('channelList.searchModel')}
                allowCustomValue={true}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted">{t('channelList.reasoningEffort')}</label>
              <select
                value={value.routing.claude_reasoning_effort || ''}
                onChange={(e) => onChange({
                  routing: { ...value.routing, claude_reasoning_effort: e.target.value || null },
                })}
                className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-foreground"
              >
                <option value="">{t('common.default')}</option>
                {getClaudeReasoning(value.routing.claude_model || '')
                  .filter((option) => option.value !== '__default__')
                  .map((option) => (
                    <option key={option.value} value={option.value}>
                      {getReasoningLabel(option.value, option.label)}
                    </option>
                  ))}
              </select>
            </div>
          </div>
        </div>
      )}

      {effectiveBackend === 'codex' && (
        <div className="space-y-3">
          <div className="text-xs font-medium uppercase text-muted">{t('channelList.codexSettings')}</div>
          <div className="grid grid-cols-1 gap-3 rounded-xl border border-border bg-surface/80 p-3 md:grid-cols-3">
            <div className="space-y-1">
              <label className="text-xs text-muted">{t('channelList.agent')}</label>
              <select
                value={value.routing.codex_agent || ''}
                onChange={(e) => onChange({ routing: { ...value.routing, codex_agent: e.target.value || null } })}
                className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-foreground"
              >
                <option value="">{t('common.default')}</option>
                {codexAgents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted">{t('channelList.model')}</label>
              <Combobox
                options={[{ value: '', label: t('common.default') }, ...codexModels.map(m => ({ value: m, label: m }))]}
                value={value.routing.codex_model || ''}
                onValueChange={(v) => onChange({
                  routing: { ...value.routing, codex_model: v || null },
                })}
                placeholder={t('channelList.codexModelPlaceholder')}
                searchPlaceholder={t('channelList.searchModel')}
                allowCustomValue={true}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted">{t('channelList.reasoningEffort')}</label>
              <select
                value={value.routing.codex_reasoning_effort || ''}
                onChange={(e) => onChange({
                  routing: { ...value.routing, codex_reasoning_effort: e.target.value || null },
                })}
                className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-foreground"
              >
                <option value="">{t('common.default')}</option>
                <option value="low">{t('channelList.reasoningLow')}</option>
                <option value="medium">{t('channelList.reasoningMedium')}</option>
                <option value="high">{t('channelList.reasoningHigh')}</option>
                <option value="xhigh">{t('channelList.reasoningXHigh')}</option>
              </select>
            </div>
          </div>
        </div>
      )}

      {/* Custom footer slot — e.g., admin/remove buttons on /users */}
      {footerActions && (
        <div className="flex items-center justify-end gap-2 border-t border-border/60 pt-3">
          {footerActions}
        </div>
      )}
    </div>
  );
};
